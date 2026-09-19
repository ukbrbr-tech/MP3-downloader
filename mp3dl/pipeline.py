"""1 曲ずつ mp3 に変換して保存する中心の処理.

流れ:
  1. 履歴（Video ID）で処理するかどうかを決める
  2. yt-dlp で音声を取得し、ffmpeg で mp3 320kbps に変換
  3. `01 - 曲名.mp3` にリネーム（既存ファイルは上書きしない）
  4. mutagen で ID3 タグとアルバムアートを書き込む
  5. 成功したものだけ履歴へ記録する
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .archive import Archive
from .config import (
    DEFAULT_COVER_MAX_PX,
    DEFAULT_QUALITY,
    DEFAULT_RETRIES,
    DEFAULT_RETRY_BACKOFF,
)
from .naming import track_filename, unique_path
from .playlist import Playlist, Track, WATCH_URL
from .tagging import (
    TaggingError,
    TrackTags,
    apply_tags,
    find_thumbnail,
    read_cover_bytes,
    remove_thumbnail_files,
)

#: 一時ダウンロード用のフォルダ名（保存先の中に作る）
TEMP_DIRNAME = ".mp3dl-tmp"

#: 再試行しても無駄な（恒久的な）エラーの手がかり
_PERMANENT_MARKERS = (
    "private video",
    "video unavailable",
    "removed by the uploader",
    "account associated with this video has been terminated",
    "members-only",
    "sign in to confirm your age",
    "this video is not available",
    "copyright",
    "has been removed",
)

# 処理結果のステータス
DOWNLOADED = "downloaded"
SKIPPED = "skipped"
FAILED = "failed"
CANCELLED = "cancelled"


class DownloadError(RuntimeError):
    """処理全体を続行できないときに送出する."""


class DownloadCancelled(Exception):
    """利用者が中止したときに yt-dlp の処理を打ち切るために使う."""


@dataclass
class Result:
    """1 曲の処理結果."""

    track: Track
    status: str
    detail: str = ""
    path: Path | None = None

    @property
    def ok(self) -> bool:
        return self.status != FAILED


@dataclass
class Progress:
    """1 曲のダウンロード進捗（0〜100%）."""

    track: Track
    percent: float
    stage: str  # "downloading" | "converting" | "tagging" | "finished"


@dataclass
class Options:
    """ダウンロードの設定（GUI / CLI の双方から渡す）."""

    quality: str = DEFAULT_QUALITY
    embed_thumbnail: bool = True
    cover_max_px: int = DEFAULT_COVER_MAX_PX
    retries: int = DEFAULT_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    ffmpeg_location: str | None = None
    cookies_from_browser: str | None = None
    verbose: bool = False
    #: 既に同名ファイルがある場合でも上書きせず別名で保存する
    avoid_overwrite: bool = True


@dataclass
class Summary:
    """処理全体の集計."""

    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    results: list[Result] = field(default_factory=list)

    def add(self, result: Result) -> None:
        self.results.append(result)
        if result.status == DOWNLOADED:
            self.downloaded += 1
        elif result.status == SKIPPED:
            self.skipped += 1
        elif result.status == FAILED:
            self.failed += 1
        elif result.status == CANCELLED:
            self.cancelled = True

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status == FAILED]


def ensure_ffmpeg(ffmpeg_location: str | None = None) -> str:
    """mp3 変換に必要な ffmpeg を探す。無ければ分かりやすい例外を出す."""
    from .deps import ffmpeg_install_hint, find_ffmpeg

    found = find_ffmpeg(ffmpeg_location)
    if not found:
        raise DownloadError("FFmpeg が見つかりません。\n" + ffmpeg_install_hint())
    return found


def select_new(tracks: Sequence[Track], archive: Archive) -> list[Track]:
    """履歴に無い（＝新規の）曲だけを返す."""
    return [track for track in tracks if not archive.has(track.video_id)]


def split_by_archive(
    tracks: Sequence[Track], archive: Archive
) -> tuple[list[Track], list[Track]]:
    """(新規, 処理済み) に分ける."""
    new: list[Track] = []
    done: list[Track] = []
    for track in tracks:
        (done if archive.has(track.video_id) else new).append(track)
    return new, done


# --------------------------------------------------------------- yt-dlp 設定


def _import_ytdlp():
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 環境依存
        raise DownloadError(
            "yt-dlp がインストールされていません。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from exc
    return YoutubeDL


def build_ydl_options(temp_dir: Path, options: Options) -> dict:
    """1 曲分の yt-dlp オプションを組み立てる."""
    opts: dict = {
        "format": "bestaudio/best",
        # 一時フォルダには ID だけの名前で置き、あとで正式名にリネームする
        "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(options.quality),
            }
        ],
        "writethumbnail": bool(options.embed_thumbnail),
        "quiet": not options.verbose,
        "no_warnings": not options.verbose,
        "noprogress": True,
        "ignoreerrors": False,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "consoletitle": False,
        "noplaylist": True,
        "overwrites": True,
    }
    if options.ffmpeg_location:
        opts["ffmpeg_location"] = options.ffmpeg_location
    if options.cookies_from_browser:
        opts["cookiesfrombrowser"] = (options.cookies_from_browser,)
    return opts


def _clean_error(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    message = message.replace("ERROR: ", "")
    for marker in ("; please report this issue", "Confirm you are on the latest version"):
        head, sep, _ = message.partition(marker)
        if sep:
            message = head.strip().rstrip(";")
    return message.splitlines()[0] if message else exc.__class__.__name__


def is_permanent_error(message: str) -> bool:
    """再試行しても直らないエラーかどうか."""
    lowered = message.lower()
    return any(marker in lowered for marker in _PERMANENT_MARKERS)


# ------------------------------------------------------------- 1 曲分の処理


class TrackDownloader:
    """1 曲を mp3 として保存する処理をまとめたもの."""

    def __init__(
        self,
        *,
        dest: Path,
        archive: Archive,
        playlist: Playlist | None = None,
        options: Options | None = None,
        on_progress: Callable[[Progress], None] | None = None,
        on_log: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        ydl_factory=None,
    ):
        self.dest = Path(dest)
        self.archive = archive
        self.playlist = playlist
        self.options = options or Options()
        self.on_progress = on_progress
        self.on_log = on_log
        self.should_stop = should_stop
        self._ydl_factory = ydl_factory

    # ------------------------------------------------------------ 補助

    def _log(self, message: str) -> None:
        if self.on_log:
            self.on_log(message)

    def _progress(self, track: Track, percent: float, stage: str) -> None:
        if self.on_progress:
            self.on_progress(Progress(track, percent, stage))

    def _stopped(self) -> bool:
        return bool(self.should_stop and self.should_stop())

    @property
    def temp_dir(self) -> Path:
        return self.dest / TEMP_DIRNAME

    # ------------------------------------------------------------ 本体

    def run(self, track: Track) -> Result:
        """1 曲を処理する。失敗しても例外は投げず Result で返す."""
        if self._stopped():
            return Result(track, CANCELLED, "中止しました")

        self.dest.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        attempts = max(1, int(self.options.retries))
        last_error = ""

        for attempt in range(1, attempts + 1):
            if self._stopped():
                return Result(track, CANCELLED, "中止しました")
            try:
                return self._attempt(track)
            except DownloadCancelled:
                return Result(track, CANCELLED, "中止しました")
            except Exception as exc:  # yt-dlp は多様な例外を投げる
                last_error = _clean_error(exc)
                if is_permanent_error(last_error) or attempt >= attempts:
                    break
                wait = self.options.retry_backoff * attempt
                self._log(
                    f"  再試行 {attempt}/{attempts - 1}: {track.title} "
                    f"（{last_error}） {wait:.0f} 秒待機"
                )
                if self._sleep(wait):
                    return Result(track, CANCELLED, "中止しました")
            finally:
                self._cleanup_temp(track)

        return Result(track, FAILED, last_error or "不明なエラー")

    def _sleep(self, seconds: float) -> bool:
        """待機する。途中で中止されたら True を返す."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stopped():
                return True
            time.sleep(0.2)
        return False

    def _attempt(self, track: Track) -> Result:
        opts = build_ydl_options(self.temp_dir, self.options)
        opts["progress_hooks"] = [self._make_hook(track)]
        opts["postprocessor_hooks"] = [self._make_pp_hook(track)]

        factory = self._ydl_factory or _import_ytdlp()
        with factory(opts) as ydl:
            info = ydl.extract_info(track.url, download=True)
        if isinstance(info, dict) and info.get("entries"):
            entries = [e for e in info["entries"] if e]
            info = entries[0] if entries else info

        produced = self._find_produced_mp3(track, info if isinstance(info, dict) else {})
        if produced is None:
            raise DownloadError("mp3 ファイルが作成されませんでした（FFmpeg を確認してください）。")

        final_path = self._move_into_place(track, info if isinstance(info, dict) else {}, produced)

        self._progress(track, 97.0, "tagging")
        tag_note = self._tag(track, info if isinstance(info, dict) else {}, final_path, produced)

        self.archive.record(
            track.video_id,
            title=track.title,
            filename=final_path.name,
            url=track.url,
        )
        self._progress(track, 100.0, "finished")
        return Result(track, DOWNLOADED, tag_note, final_path)

    # ---------------------------------------------------- ファイルの後始末

    def _find_produced_mp3(self, track: Track, info: dict) -> Path | None:
        """一時フォルダにできた mp3 を探す."""
        candidates: list[Path] = []
        video_id = info.get("id") or track.video_id
        if video_id:
            candidates.append(self.temp_dir / f"{video_id}.mp3")
        # yt-dlp は実際に書き出したファイルを requested_downloads に入れる
        for entry in info.get("requested_downloads") or []:
            if isinstance(entry, dict):
                for key in ("filepath", "_filename", "filename"):
                    if entry.get(key):
                        candidates.append(Path(entry[key]).with_suffix(".mp3"))
        for key in ("filepath", "_filename", "filename"):
            value = info.get(key)
            if value:
                candidates.append(Path(value).with_suffix(".mp3"))
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        # 最後の手段として一時フォルダを走査する
        found = sorted(self.temp_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        return found[-1] if found else None

    def _move_into_place(self, track: Track, info: dict, produced: Path) -> Path:
        """`01 - 曲名.mp3` にリネームして保存先へ移す."""
        title = str(info.get("title") or track.title)
        index = track.index or None
        name = track_filename(index, title)
        target = self.dest / name
        if self.options.avoid_overwrite:
            target = unique_path(target)
            if target.name != name:
                self._log(f"  同名ファイルがあるため {target.name} として保存します")
        elif target.exists():
            try:
                target.unlink()
            except OSError:
                target = unique_path(target)

        shutil.move(str(produced), str(target))
        return target

    def _tag(self, track: Track, info: dict, final_path: Path, produced: Path) -> str:
        """ID3 タグとアルバムアートを書き込む。失敗しても mp3 自体は残す."""
        cover = None
        if self.options.embed_thumbnail:
            cover = read_cover_bytes(find_thumbnail(produced))
            # 埋め込みに使うのはメモリ上のデータなので、画像ファイルはここで消す
            remove_thumbnail_files(produced)
            if cover is None:
                cover = self._download_thumbnail(info)

        tags = TrackTags(
            title=str(info.get("title") or track.title),
            artist=str(
                info.get("artist")
                or info.get("uploader")
                or info.get("channel")
                or track.uploader
            ),
            album=str(self.playlist.title if self.playlist else info.get("album") or ""),
            track_number=track.index or None,
            track_total=len(self.playlist.tracks) if self.playlist else None,
            year=_release_year(info),
            comment=track.url or WATCH_URL.format(id=track.video_id or ""),
            url=track.url,
        )

        try:
            apply_tags(final_path, tags, cover=cover, cover_max_px=self.options.cover_max_px)
        except TaggingError as exc:
            self._log(f"  注意: タグを書き込めませんでした（{exc}）")
            return "タグ書き込みに失敗（mp3 は保存済み）"
        return ""

    def _download_thumbnail(self, info: dict) -> bytes | None:
        """yt-dlp がサムネイルを保存しなかった場合に直接取得する."""
        url = info.get("thumbnail")
        if not url and info.get("thumbnails"):
            thumbs = [t for t in info["thumbnails"] if isinstance(t, dict) and t.get("url")]
            if thumbs:
                url = thumbs[-1]["url"]
        if not url:
            return None
        try:
            import urllib.request

            with urllib.request.urlopen(url, timeout=20) as response:
                return response.read()
        except Exception:
            return None

    def _cleanup_temp(self, track: Track) -> None:
        """一時ファイル（未完了の部品やサムネイル）を片付ける."""
        video_id = track.video_id
        try:
            if not self.temp_dir.is_dir():
                return
            for path in list(self.temp_dir.iterdir()):
                if video_id and not path.name.startswith(video_id):
                    continue
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass
            if not any(self.temp_dir.iterdir()):
                self.temp_dir.rmdir()
        except OSError:
            pass

    # ---------------------------------------------------------- 進捗フック

    def _make_hook(self, track: Track) -> Callable[[dict], None]:
        def hook(status: dict) -> None:
            if self._stopped():
                raise DownloadCancelled
            state = status.get("status")
            if state == "downloading":
                total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
                done = status.get("downloaded_bytes") or 0
                percent = (done / total * 100) if total else 0.0
                # 変換にも時間がかかるので、取得完了で 85% とする
                self._progress(track, min(percent, 100.0) * 0.85, "downloading")
            elif state == "finished":
                self._progress(track, 85.0, "converting")

        return hook

    def _make_pp_hook(self, track: Track) -> Callable[[dict], None]:
        def hook(status: dict) -> None:
            if self._stopped():
                raise DownloadCancelled
            if status.get("status") == "started":
                self._progress(track, 92.0, "converting")

        return hook


def _release_year(info: dict) -> str:
    """公開日（YYYYMMDD）を ID3 の日付表記にする."""
    for key in ("release_date", "upload_date"):
        value = info.get(key)
        if value and isinstance(value, str) and len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    year = info.get("release_year")
    if year:
        return str(year)
    return ""


# ----------------------------------------------------------- まとめて処理


def download_tracks(
    tracks: Iterable[Track],
    *,
    dest: Path,
    archive: Archive,
    playlist: Playlist | None = None,
    options: Options | None = None,
    on_result: Callable[[Result], None] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    ydl_factory=None,
) -> Summary:
    """曲を 1 つずつ処理する。1 曲失敗しても残りは続ける."""
    worker = TrackDownloader(
        dest=dest,
        archive=archive,
        playlist=playlist,
        options=options,
        on_progress=on_progress,
        on_log=on_log,
        should_stop=should_stop,
        ydl_factory=ydl_factory,
    )

    summary = Summary()
    for track in tracks:
        if should_stop and should_stop():
            result = Result(track, CANCELLED, "中止しました")
        else:
            result = worker.run(track)
        summary.add(result)
        if on_result:
            on_result(result)
        if result.status == CANCELLED:
            break
    return summary


__all__ = [
    "CANCELLED",
    "DOWNLOADED",
    "DownloadCancelled",
    "DownloadError",
    "FAILED",
    "Options",
    "Progress",
    "Result",
    "SKIPPED",
    "Summary",
    "TrackDownloader",
    "download_tracks",
    "ensure_ffmpeg",
    "select_new",
    "split_by_archive",
]
