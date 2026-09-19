"""処理済み動画の履歴（YouTube Video ID ベース）.

重複判定はファイル名やタイトルではなく **必ず Video ID** で行う。
履歴は保存先フォルダ直下の `download_archive.txt` に yt-dlp の
download-archive と同じ形式（`youtube <video id>`）で追記する。
補助的に `processed.json` へタイトルや処理日時も残すが、
スキップ判定に使うのは download_archive.txt のほうだけ。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from .config import ARCHIVE_FILENAME, LEGACY_ARCHIVE_FILENAMES, PROCESSED_FILENAME

#: yt-dlp のアーカイブ行 "youtube dQw4w9WgXcQ"
_ARCHIVE_LINE = re.compile(r"^\s*(?:(?P<extractor>\S+)\s+)?(?P<id>[0-9A-Za-z_-]{6,})\s*$")

#: 履歴に書き出すときの extractor 名（yt-dlp と揃える）
ARCHIVE_EXTRACTOR = "youtube"


class Archive:
    """1 つの保存先フォルダに対する処理済み Video ID の集合."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.archive_path = self.directory / ARCHIVE_FILENAME
        self.processed_path = self.directory / PROCESSED_FILENAME
        self._ids: set[str] = set()
        self._lock = threading.Lock()
        self.load()

    # ------------------------------------------------------------- 読み込み

    def load(self) -> None:
        """履歴ファイルを読み込む（旧形式のファイルがあれば取り込む）."""
        ids: set[str] = set()
        for name in (ARCHIVE_FILENAME, *LEGACY_ARCHIVE_FILENAMES):
            ids |= self._read_ids(self.directory / name)
        # processed.json は補助情報だが、archive が消えていても復元できるようにする
        ids |= self._read_processed_ids()
        with self._lock:
            self._ids = ids

    @staticmethod
    def _read_ids(path: Path) -> set[str]:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return set()
        found: set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = _ARCHIVE_LINE.match(line)
            if match:
                found.add(match.group("id"))
        return found

    def _read_processed_ids(self) -> set[str]:
        return {
            str(record["video_id"])
            for record in self._read_processed_records()
            if record.get("video_id")
        }

    def _read_processed_records(self) -> list[dict]:
        try:
            raw = json.loads(self.processed_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return []
        if isinstance(raw, dict):
            raw = raw.get("videos") or []
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    # --------------------------------------------------------------- 問い合わせ

    def __contains__(self, video_id: object) -> bool:
        return self.has(video_id if isinstance(video_id, str) else "")

    def has(self, video_id: str | None) -> bool:
        """この Video ID が処理済みかどうか."""
        if not video_id:
            return False
        with self._lock:
            return video_id in self._ids

    @property
    def video_ids(self) -> set[str]:
        with self._lock:
            return set(self._ids)

    def __len__(self) -> int:
        with self._lock:
            return len(self._ids)

    # ------------------------------------------------------------------ 記録

    def record(
        self,
        video_id: str | None,
        *,
        title: str | None = None,
        filename: str | None = None,
        url: str | None = None,
    ) -> None:
        """**正常終了した動画だけ** を処理済みとして記録する."""
        if not video_id:
            return
        with self._lock:
            if video_id in self._ids:
                return
            self._ids.add(video_id)
        self._append_archive(video_id)
        self._append_processed(video_id, title=title, filename=filename, url=url)

    def _append_archive(self, video_id: str) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.archive_path.open("a", encoding="utf-8") as handle:
                handle.write(f"{ARCHIVE_EXTRACTOR} {video_id}\n")
                handle.flush()
        except OSError:
            pass

    def _append_processed(
        self,
        video_id: str,
        *,
        title: str | None,
        filename: str | None,
        url: str | None,
    ) -> None:
        record = {
            "video_id": video_id,
            "title": title or "",
            "filename": filename or "",
            "url": url or f"https://www.youtube.com/watch?v={video_id}",
            "processed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        try:
            records = self._read_processed_records()
            records.append(record)
            self.directory.mkdir(parents=True, exist_ok=True)
            tmp = self.processed_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.processed_path)
        except OSError:
            pass

    # ------------------------------------------------------------------ 消去

    def reset(self) -> None:
        """履歴をすべて消す（次回は再生リスト全体が新規扱いになる）."""
        with self._lock:
            self._ids.clear()
        for path in (
            self.archive_path,
            self.processed_path,
            *(self.directory / name for name in LEGACY_ARCHIVE_FILENAMES),
        ):
            try:
                path.unlink()
            except OSError:
                pass
