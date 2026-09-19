"""Windows で安全なファイル名を作るためのユーティリティ."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .config import MAX_FILENAME_STEM

#: Windows のファイル名に使えない文字
_FORBIDDEN = '\\/:*?"<>|'

#: 全角の同じ記号へ置き換えると読みやすいものはそのまま置換する
_REPLACEMENTS = {
    "\\": "￥",
    "/": "／",
    ":": "：",
    "*": "＊",
    "?": "？",
    '"': "”",
    "<": "＜",
    ">": "＞",
    "|": "｜",
}

#: Windows の予約名（拡張子を付けても使えない）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_WHITESPACE = re.compile(r"\s+")


def sanitize_component(name: str, *, max_length: int = MAX_FILENAME_STEM) -> str:
    """ファイル名・フォルダ名として安全な 1 要素を作る.

    - Windows で使えない文字（\\ / : * ? " < > |）を全角へ置換
    - 制御文字を削除
    - 末尾の空白とピリオドを削除（Windows が受け付けないため）
    - 予約名（CON など）を避ける
    - 長すぎる名前を切り詰める
    """
    text = unicodedata.normalize("NFC", name or "")
    # 制御文字を削除
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    text = "".join(_REPLACEMENTS.get(ch, ch) for ch in text)
    text = _WHITESPACE.sub(" ", text).strip()
    # Windows は末尾のピリオド・空白を落としてしまうので先に取り除く
    text = text.rstrip(" .")
    if len(text) > max_length:
        text = text[:max_length].rstrip(" .")
    if not text:
        return "untitled"
    if text.split(".")[0].upper() in _RESERVED:
        text = f"_{text}"
    return text


def track_filename(index: int | None, title: str, extension: str = "mp3") -> str:
    """`01 - 曲名.mp3` 形式のファイル名を作る."""
    safe_title = sanitize_component(title)
    if index is None:
        stem = safe_title
    else:
        stem = f"{index:02d} - {safe_title}"
        # 連番を足した分だけ長くなるので、必要なら再度切り詰める
        if len(stem) > MAX_FILENAME_STEM:
            keep = MAX_FILENAME_STEM - len(f"{index:02d} - ")
            stem = f"{index:02d} - {sanitize_component(title, max_length=max(keep, 1))}"
    return f"{stem}.{extension}"


def unique_path(path: Path) -> Path:
    """既存ファイルを上書きしないように、空いているパスを返す.

    `01 - Song.mp3` が既にあれば `01 - Song (2).mp3` を返す。
    """
    path = Path(path)
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    for counter in range(2, 1000):
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
    # ここまで来ることはまずないが、最後の手段として時刻を付ける
    import time

    return parent / f"{stem} ({int(time.time())}){suffix}"
