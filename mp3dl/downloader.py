"""yt-dlp を使った再生リストの取得と mp3 変換."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

from .library import Library

DEFAULT_FILENAME_TEMPLATE = "%(title)s.%(ext)s"
DEFAULT_QUALITY = "192"


class DownloadError(RuntimeError):
    """ダウンロード処理を続行できないときに送出する."""


class DownloadCancelled(Exception):
    """利用者が中止したときに yt-dlp の処理を打ち切るために使う."""


@dataclass
class Track:
    """再生リスト内の 1 曲."""

    video_id: str | None
    title: str
    url: str


@dataclass
class Result:
    """1 曲の処理結果."""

    track: Track
    status: str  # "downloaded" | "skipped" | "failed" | "cancelled"
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status != "failed"


@dataclass
class Progress:
    """1 曲のダウンロード進捗（0〜100%）."""

    track: Track
    percent: float
    stage: str  # "downloading" | "converting" | "finished"


def _import_ytdlp():
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 環境依存
        raise DownloadError(
            "yt-dlp がインストールされていません。`pip install -r requirements.txt` を実行してください。"
        ) from exc
    return YoutubeDL


def ensure_ffmpeg(ffmpeg_location: str | None = None) -> None:
    """mp3 変換に必要な ffmpeg があるか確認する."""
    if ffmpeg_location:
        candidate = Path(ffmpeg_location)
        if candidate.is_dir():
            found = any((candidate / name).exists() for name in ("ffmpeg", "ffmpeg.exe"))
        else:
            found = candidate.exists()
        if not found:
            raise DownloadError(f"指定された ffmpeg が見つかりません: {ffmpeg_location}")
        return

    if shutil.which("ffmpeg") is None:
        raise DownloadError(
            "ffmpeg が見つかりません。mp3 への変換に必要です。\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: winget install Gyan.FFmpeg"
        )


def fetch_tracks(url: str, *, cookies_from_browser: str | None = None) -> list[Track]:
    """再生リスト（または単一動画）の URL から曲の一覧を取り出す."""
    YoutubeDL = _import_ytdlp()

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise DownloadError(f"再生リストの情報を取得できませんでした: {url}")

    return list(_walk_entries(info))


def _walk_entries(info: dict) -> Iterator[Track]:
    """再生リストが入れ子になっていても平坦に辿る."""
    entries = info.get("entries")
    if entries is None:
        track = _to_track(info)
        if track:
            yield track
        return

    for entry in entries:
        if not entry:  # 非公開・削除済み動画は None になる
            continue
        if entry.get("entries") is not None or entry.get("_type") == "playlist":
            yield from _walk_entries(entry)
            continue
        track = _to_track(entry)
        if track:
            yield track


def _to_track(entry: dict) -> Track | None:
    video_id = entry.get("id")
    url = entry.get("webpage_url") or entry.get("url")
    if not url and video_id:
        url = f"https://www.youtube.com/watch?v={video_id}"
    if not url:
        return None
    title = entry.get("title") or video_id or url
    return Track(video_id=video_id, title=title, url=url)


def plan(tracks: Iterable[Track], library: Library) -> list[tuple[Track, str | None]]:
    """各曲について「スキップ理由」または None（要ダウンロード）を組にして返す."""
    return [(track, library.reason_to_skip(track.video_id, track.title)) for track in tracks]


def _build_options(
    *,
    dest: Path,
    filename_template: str,
    quality: str,
    ffmpeg_location: str | None,
    cookies_from_browser: str | None,
    embed_thumbnail: bool,
    verbose: bool,
) -> dict:
    postprocessors: list[dict] = [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": quality,
        },
        {"key": "FFmpegMetadata", "add_metadata": True},
    ]
    if embed_thumbnail:
        postprocessors.append({"key": "EmbedThumbnail"})

    opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": str(dest / filename_template),
        "postprocessors": postprocessors,
        "writethumbnail": embed_thumbnail,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "noprogress": not verbose,
        "ignoreerrors": False,
        "retries": 5,
        "fragment_retries": 5,
        "consoletitle": False,
    }
    if ffmpeg_location:
        opts["ffmpeg_location"] = ffmpeg_location
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def _clean_error(exc: Exception) -> str:
    """yt-dlp の例外を 1 行の読めるメッセージにする."""
    message = str(exc).strip() or exc.__class__.__name__
    message = message.replace("ERROR: ", "")
    # "; please report this issue ..." 以降は利用者には不要
    for marker in ("; please report this issue", "Confirm you are on the latest version"):
        head, sep, _ = message.partition(marker)
        if sep:
            message = head.strip().rstrip(";")
    return message.splitlines()[0] if message else exc.__class__.__name__


def _make_hook(
    track: Track,
    on_progress: Callable[[Progress], None] | None,
    should_stop: Callable[[], bool] | None,
) -> Callable[[dict], None]:
    """yt-dlp のダウンロード進捗フック."""

    def hook(status: dict) -> None:
        if should_stop and should_stop():
            raise DownloadCancelled
        if not on_progress:
            return
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
            done = status.get("downloaded_bytes") or 0
            percent = (done / total * 100) if total else 0.0
            # 変換にも時間がかかるので、取得完了で 90% とする
            on_progress(Progress(track, min(percent, 100.0) * 0.9, "downloading"))
        elif status.get("status") == "finished":
            on_progress(Progress(track, 90.0, "converting"))

    return hook


def _make_pp_hook(
    track: Track, on_progress: Callable[[Progress], None] | None
) -> Callable[[dict], None]:
    """mp3 変換など後処理の進捗フック."""

    def hook(status: dict) -> None:
        if on_progress and status.get("status") == "started":
            on_progress(Progress(track, 95.0, "converting"))

    return hook


def download_tracks(
    tracks: Iterable[Track],
    *,
    dest: Path,
    library: Library,
    filename_template: str = DEFAULT_FILENAME_TEMPLATE,
    quality: str = DEFAULT_QUALITY,
    ffmpeg_location: str | None = None,
    cookies_from_browser: str | None = None,
    embed_thumbnail: bool = False,
    jobs: int = 1,
    verbose: bool = False,
    on_result: Callable[[Result], None] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> list[Result]:
    """曲を 1 つずつ mp3 に変換して保存する.

    既に存在するものはスキップし、失敗しても残りの曲は処理を続ける。
    """
    YoutubeDL = _import_ytdlp()
    dest.mkdir(parents=True, exist_ok=True)

    opts = _build_options(
        dest=dest,
        filename_template=filename_template,
        quality=quality,
        ffmpeg_location=ffmpeg_location,
        cookies_from_browser=cookies_from_browser,
        embed_thumbnail=embed_thumbnail,
        verbose=verbose,
    )

    def handle(track: Track) -> Result:
        if should_stop and should_stop():
            return Result(track, "cancelled", "中止しました")

        # 並列実行中に同じ曲が別スレッドで完了しているかもしれないので直前に再確認する
        reason = library.reason_to_skip(track.video_id, track.title)
        if reason:
            return Result(track, "skipped", reason)

        track_opts = dict(opts)
        if on_progress or should_stop:
            track_opts["progress_hooks"] = [_make_hook(track, on_progress, should_stop)]
            track_opts["postprocessor_hooks"] = [_make_pp_hook(track, on_progress)]

        try:
            with YoutubeDL(track_opts) as ydl:
                ydl.download([track.url])
        except DownloadCancelled:
            return Result(track, "cancelled", "中止しました")
        except Exception as exc:  # yt-dlp は多様な例外を投げる
            return Result(track, "failed", _clean_error(exc))

        library.record(track.video_id, track.title)
        if on_progress:
            on_progress(Progress(track, 100.0, "finished"))
        return Result(track, "downloaded")

    tracks = list(tracks)
    results: list[Result] = []

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for result in pool.map(handle, tracks):
                results.append(result)
                if on_result:
                    on_result(result)
    else:
        for track in tracks:
            result = handle(track)
            results.append(result)
            if on_result:
                on_result(result)

    return results
