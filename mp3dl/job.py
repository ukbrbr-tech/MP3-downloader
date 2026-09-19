"""モード（新規のみ / 全件 / 確認のみ / 選択）に応じた一連の処理.

GUI と CLI のどちらからも同じ手順を使えるように、ここにまとめている。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

from .archive import Archive
from .config import LOG_FILENAME, Settings
from .pipeline import (
    CANCELLED,
    Options,
    Progress,
    Result,
    Summary,
    download_tracks,
    ensure_ffmpeg,
    split_by_archive,
)
from .playlist import Playlist, PlaylistError, Track, fetch_playlist

#: 利用できるモード（キー -> 画面に出す名前）
MODES = {
    "new": "新規のみ",
    "all": "全件",
    "check": "確認のみ",
    "select": "選択",
}

DEFAULT_MODE = "new"


@dataclass
class Plan:
    """再生リストを確認した結果."""

    playlist: Playlist
    destination: Path
    new_tracks: list[Track] = field(default_factory=list)
    done_tracks: list[Track] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.playlist.tracks)

    def summary_text(self) -> str:
        return (
            f"再生リスト: {self.playlist.title}\n"
            f"保存先: {self.destination}\n"
            f"動画総数: {self.total} 件 / 処理済み: {len(self.done_tracks)} 件 / "
            f"新規: {len(self.new_tracks)} 件"
        )

    def tracks_for_mode(self, mode: str, selected_ids: Sequence[str] | None = None) -> list[Track]:
        """モードに応じて実際に処理する曲を決める."""
        if mode == "all":
            return list(self.playlist.tracks)
        if mode == "select":
            chosen = set(selected_ids or [])
            return [t for t in self.playlist.tracks if (t.video_id or t.url) in chosen]
        # "new" と "check" は新規のみ
        return list(self.new_tracks)


def make_plan(
    url: str,
    output_root: Path,
    *,
    settings: Settings | None = None,
    cookies_from_browser: str | None = None,
) -> Plan:
    """再生リストを取得して、新規／処理済みに分ける."""
    settings = settings or Settings()
    playlist = fetch_playlist(url, cookies_from_browser=cookies_from_browser or None)
    if not playlist.tracks:
        raise PlaylistError(
            "処理できる動画が見つかりませんでした。"
            "URL が再生リストのものか確認してください。"
        )
    destination = playlist.destination(
        output_root, subfolder=settings.make_playlist_subfolder
    )
    archive = Archive(destination)
    new_tracks, done_tracks = split_by_archive(playlist.tracks, archive)
    return Plan(
        playlist=playlist,
        destination=destination,
        new_tracks=new_tracks,
        done_tracks=done_tracks,
    )


def options_from_settings(settings: Settings, ffmpeg_location: str | None = None) -> Options:
    """設定から pipeline 用のオプションを作る."""
    return Options(
        quality=settings.quality,
        embed_thumbnail=settings.embed_thumbnail,
        cover_max_px=settings.cover_max_px,
        retries=settings.retries,
        ffmpeg_location=ffmpeg_location or (settings.ffmpeg_location or None),
        cookies_from_browser=settings.cookies_from_browser or None,
    )


def setup_logger(destination: Path) -> logging.Logger:
    """保存先フォルダに app.log を書くロガーを用意する."""
    logger = logging.getLogger(f"mp3dl.{destination}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = destination / LOG_FILENAME
    for handler in logger.handlers:
        if getattr(handler, "baseFilename", None) == str(log_path.resolve()):
            return logger
    try:
        destination.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    except OSError:
        pass
    return logger


def run_plan(
    plan: Plan,
    *,
    mode: str = DEFAULT_MODE,
    settings: Settings | None = None,
    selected_ids: Sequence[str] | None = None,
    on_result: Callable[[Result], None] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Summary:
    """確認済みの Plan に沿ってダウンロードする."""
    settings = settings or Settings()
    targets = plan.tracks_for_mode(mode, selected_ids)

    ffmpeg = ensure_ffmpeg(settings.ffmpeg_location or None)
    options = options_from_settings(settings, ffmpeg)

    plan.destination.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(plan.destination)
    logger.info("開始: %s (モード=%s, 対象=%d 件)", plan.playlist.title, mode, len(targets))

    archive = Archive(plan.destination)

    def log(message: str) -> None:
        logger.info(message.strip())
        if on_log:
            on_log(message)

    def handle_result(result: Result) -> None:
        if result.status == "downloaded":
            logger.info("保存: %s -> %s", result.track.title, result.path)
        elif result.status == "failed":
            logger.error("失敗: %s (%s)", result.track.title, result.detail)
        elif result.status == CANCELLED:
            logger.warning("中止: %s", result.track.title)
        if on_result:
            on_result(result)

    summary = download_tracks(
        targets,
        dest=plan.destination,
        archive=archive,
        playlist=plan.playlist,
        options=options,
        on_result=handle_result,
        on_progress=on_progress,
        on_log=log,
        should_stop=should_stop,
    )

    logger.info(
        "終了: 成功 %d / スキップ %d / 失敗 %d",
        summary.downloaded,
        summary.skipped,
        summary.failed,
    )
    return summary
