"""yt-dlp を使った再生リストの取得."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

WATCH_URL = "https://www.youtube.com/watch?v={id}"


class PlaylistError(RuntimeError):
    """再生リストを取得できなかったときに送出する."""


@dataclass
class Track:
    """再生リスト内の 1 曲（取得前のざっくりした情報）."""

    video_id: str | None
    title: str
    url: str
    index: int = 0  #: 再生リスト上の順番（1 始まり）
    uploader: str = ""
    duration: float | None = None

    @property
    def short_id(self) -> str:
        return self.video_id or "?"


@dataclass
class Playlist:
    """再生リストそのもの."""

    title: str
    url: str
    tracks: list[Track] = field(default_factory=list)
    uploader: str = ""

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def safe_folder_name(self) -> str:
        """保存先のサブフォルダ名（Windows で安全な名前）."""
        from .naming import sanitize_component

        return sanitize_component(self.title or "playlist")

    def destination(self, root: Path, *, subfolder: bool = True) -> Path:
        """`Music/<再生リスト名>/` を返す."""
        root = Path(root).expanduser()
        return root / self.safe_folder_name if subfolder else root


def _import_ytdlp():
    try:
        from yt_dlp import YoutubeDL  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 環境依存
        raise PlaylistError(
            "yt-dlp がインストールされていません。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from exc
    return YoutubeDL


def flat_options(cookies_from_browser: str | None = None) -> dict:
    """一覧だけを素早く取るための yt-dlp オプション."""
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "noprogress": True,
        "retries": 5,
        "socket_timeout": 30,
    }
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def fetch_playlist(
    url: str,
    *,
    cookies_from_browser: str | None = None,
    ydl_factory=None,
) -> Playlist:
    """再生リスト（または単一動画）の URL から一覧を取得する."""
    url = (url or "").strip()
    if not url:
        raise PlaylistError("URL を入力してください。")

    factory = ydl_factory or _import_ytdlp()
    opts = flat_options(cookies_from_browser)

    try:
        with factory(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except PlaylistError:
        raise
    except Exception as exc:
        raise PlaylistError(f"再生リストを読み込めませんでした: {_clean(exc)}") from exc

    if not info:
        raise PlaylistError(
            "再生リストの情報を取得できませんでした。"
            "URL が正しいか、限定公開ではないか確認してください。"
        )

    tracks: list[Track] = []
    for track in _walk_entries(info):
        track.index = len(tracks) + 1
        tracks.append(track)

    title = info.get("title") or info.get("id") or "playlist"
    if info.get("entries") is None:
        # 単一動画のときは「再生リスト名」がそのまま曲名になってしまうので分かりやすくする
        title = info.get("playlist_title") or title

    return Playlist(
        title=str(title),
        url=info.get("webpage_url") or url,
        tracks=tracks,
        uploader=str(info.get("uploader") or info.get("channel") or ""),
    )


def fetch_tracks(url: str, *, cookies_from_browser: str | None = None) -> list[Track]:
    """互換用: 曲の一覧だけを返す."""
    return fetch_playlist(url, cookies_from_browser=cookies_from_browser).tracks


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
        url = WATCH_URL.format(id=video_id)
    if not url:
        return None
    title = entry.get("title") or video_id or url
    duration = entry.get("duration")
    return Track(
        video_id=str(video_id) if video_id else None,
        title=str(title),
        url=str(url),
        uploader=str(entry.get("uploader") or entry.get("channel") or ""),
        duration=float(duration) if isinstance(duration, (int, float)) else None,
    )


def _clean(exc: Exception) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    message = message.replace("ERROR: ", "")
    for marker in ("; please report this issue", "Confirm you are on the latest version"):
        head, sep, _ = message.partition(marker)
        if sep:
            message = head.strip().rstrip(";")
    return message.splitlines()[0] if message else exc.__class__.__name__
