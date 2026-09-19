"""保存先フォルダの既存 mp3 を調べ、ダウンロード済みかどうかを判定する."""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

#: ダウンロード済み動画 ID を記録するファイル名（保存先フォルダ直下）
ARCHIVE_FILENAME = ".downloaded.txt"

#: "曲名 [dQw4w9WgXcQ].mp3" のようなファイル名から動画 ID を取り出す
_ID_IN_NAME = re.compile(r"[\[(]([0-9A-Za-z_-]{11})[\])]")

#: yt-dlp がファイル名に使えない文字を置き換えた跡（#, ？ など）も吸収するため、
#: 英数字以外をすべて落としたものを比較キーにする
_NON_ALNUM = re.compile(r"[^0-9a-z぀-ヿ一-鿿]+")


def normalize_title(title: str) -> str:
    """タイトル比較用のキーを作る.

    全角・半角、大文字・小文字、記号や空白の違いを無視して比較できるようにする。
    """
    folded = unicodedata.normalize("NFKC", title).casefold()
    # yt-dlp がファイル名に使えない文字（#, ?, / など）を置き換えた跡も吸収するため、
    # 記号と空白をすべて落としたものを比較キーにする
    return "".join(ch for ch in folded if ch.isalnum())


def video_id_from_filename(name: str) -> str | None:
    """ファイル名に埋め込まれた YouTube 動画 ID を返す（無ければ None）."""
    match = _ID_IN_NAME.search(name)
    return match.group(1) if match else None


@dataclass
class Library:
    """保存先フォルダの状態（既存 mp3 とダウンロード履歴）."""

    directory: Path
    video_ids: set[str] = field(default_factory=set)
    title_keys: set[str] = field(default_factory=set)
    archive_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def scan(cls, directory: Path, *, use_archive: bool = True) -> "Library":
        """フォルダ内の mp3 と履歴ファイルを読み込む."""
        directory = Path(directory)
        archive_path = directory / ARCHIVE_FILENAME if use_archive else None

        lib = cls(directory=directory, archive_path=archive_path)

        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix.lower() == ".mp3":
                    lib.title_keys.add(normalize_title(path.stem))
                    video_id = video_id_from_filename(path.stem)
                    if video_id:
                        lib.video_ids.add(video_id)

        if archive_path and archive_path.is_file():
            for line in archive_path.read_text(encoding="utf-8").splitlines():
                entry = line.strip()
                if entry and not entry.startswith("#"):
                    lib.video_ids.add(entry)

        return lib

    def reason_to_skip(self, video_id: str | None, title: str | None) -> str | None:
        """スキップすべきなら理由を、ダウンロードすべきなら None を返す."""
        with self._lock:
            if video_id and video_id in self.video_ids:
                return "ダウンロード済み (ID 一致)"
            if title and normalize_title(title) and normalize_title(title) in self.title_keys:
                return "同名の mp3 が存在"
        return None

    def record(self, video_id: str | None, title: str | None) -> None:
        """ダウンロード成功を記録する（同一実行中の重複ダウンロードも防ぐ）."""
        with self._lock:
            if video_id:
                self.video_ids.add(video_id)
            if title:
                key = normalize_title(title)
                if key:
                    self.title_keys.add(key)
            if video_id and self.archive_path is not None:
                self.archive_path.parent.mkdir(parents=True, exist_ok=True)
                with self.archive_path.open("a", encoding="utf-8") as handle:
                    handle.write(f"{video_id}\n")
