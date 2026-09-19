"""mp3 への ID3 タグ書き込みとアルバムアートの埋め込み.

Title / Artist / Album / Track / Year / Comment（元動画 URL）と、
サムネイルを APIC（Front cover）として埋め込む。
別の PC やスマートフォンに mp3 を移してもカバーが表示されるように、
画像は JPEG（必要なら縮小）に統一して埋め込む。
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_COVER_JPEG_QUALITY, DEFAULT_COVER_MAX_PX

#: サムネイルとして扱う拡張子（yt-dlp が保存しうるもの）
THUMBNAIL_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")


class TaggingError(RuntimeError):
    """タグ付けに失敗したことを表す（曲自体は保存済みのことが多い）."""


@dataclass
class TrackTags:
    """1 曲分の ID3 タグ."""

    title: str = ""
    artist: str = ""
    album: str = ""
    track_number: int | None = None
    track_total: int | None = None
    year: str = ""  # "2024" または "2024-05-01"
    comment: str = ""  # 元動画 URL
    url: str = ""


def _import_mutagen():
    try:
        from mutagen.id3 import (  # type: ignore[import-not-found]
            APIC,
            COMM,
            ID3,
            TALB,
            TDRC,
            TIT2,
            TPE1,
            TRCK,
            WOAR,
        )
        from mutagen.mp3 import MP3  # type: ignore[import-not-found]
    except ImportError as exc:
        raise TaggingError(
            "mutagen がインストールされていないため ID3 タグを書き込めません。"
            "`pip install mutagen` を実行してください。"
        ) from exc
    return {
        "APIC": APIC, "COMM": COMM, "ID3": ID3, "TALB": TALB, "TDRC": TDRC,
        "TIT2": TIT2, "TPE1": TPE1, "TRCK": TRCK, "WOAR": WOAR, "MP3": MP3,
    }


# ------------------------------------------------------------------ 画像処理


def prepare_cover(
    data: bytes,
    *,
    max_px: int = DEFAULT_COVER_MAX_PX,
    jpeg_quality: int = DEFAULT_COVER_JPEG_QUALITY,
) -> tuple[bytes, str]:
    """カバーアート用の画像データと MIME タイプを返す.

    Pillow があれば JPEG へ変換し、大きすぎる画像は縮小する。
    Pillow が無い場合は、そのまま使える形式（JPEG/PNG）ならそれを使う。
    """
    try:
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        return _cover_without_pillow(data)

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            if max_px and max(image.size) > max_px:
                image.thumbnail((max_px, max_px), Image.LANCZOS)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception:
        # 画像が壊れていてもタグ付け全体は止めない
        return _cover_without_pillow(data)


def _cover_without_pillow(data: bytes) -> tuple[bytes, str]:
    if data.startswith(b"\xff\xd8\xff"):
        return data, "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image/png"
    # webp などは多くのプレイヤーが表示できないので埋め込まない
    raise TaggingError(
        "サムネイルの形式を変換できません（pillow を入れると対応できます）。"
    )


def find_thumbnail(mp3_path: Path) -> Path | None:
    """mp3 と同じ場所に yt-dlp が保存したサムネイル画像を探す."""
    mp3_path = Path(mp3_path)
    for suffix in THUMBNAIL_SUFFIXES:
        candidate = mp3_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def remove_thumbnail_files(mp3_path: Path) -> None:
    """埋め込みが終わったサムネイル画像ファイルを消す."""
    for suffix in THUMBNAIL_SUFFIXES:
        candidate = Path(mp3_path).with_suffix(suffix)
        try:
            if candidate.is_file():
                candidate.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------- タグ付け


def apply_tags(
    mp3_path: Path,
    tags: TrackTags,
    *,
    cover: bytes | None = None,
    cover_max_px: int = DEFAULT_COVER_MAX_PX,
) -> None:
    """mp3 に ID3v2.3 タグ（＋カバーアート）を書き込む.

    ID3v2.3 で保存するのは、Windows のエクスプローラーや
    古いプレイヤーとの相性が良いため。
    """
    m = _import_mutagen()
    path = Path(mp3_path)

    try:
        audio = m["MP3"](path)
    except Exception as exc:
        raise TaggingError(f"mp3 を開けませんでした: {exc}") from exc

    if audio.tags is None:
        audio.add_tags()
    id3 = audio.tags

    def put(frame) -> None:
        id3.setall(frame.FrameID, [frame])

    if tags.title:
        put(m["TIT2"](encoding=3, text=[tags.title]))
    if tags.artist:
        put(m["TPE1"](encoding=3, text=[tags.artist]))
    if tags.album:
        put(m["TALB"](encoding=3, text=[tags.album]))
    if tags.track_number:
        value = str(tags.track_number)
        if tags.track_total:
            value = f"{value}/{tags.track_total}"
        put(m["TRCK"](encoding=3, text=[value]))
    if tags.year:
        put(m["TDRC"](encoding=3, text=[tags.year]))
    if tags.comment:
        id3.delall("COMM")
        id3.add(m["COMM"](encoding=3, lang="eng", desc="", text=[tags.comment]))
    if tags.url:
        id3.delall("WOAR")
        id3.add(m["WOAR"](url=tags.url))

    if cover:
        try:
            data, mime = prepare_cover(cover, max_px=cover_max_px)
        except TaggingError:
            data, mime = b"", ""
        if data:
            id3.delall("APIC")
            id3.add(
                m["APIC"](
                    encoding=3,
                    mime=mime,
                    type=3,  # 3 = Front cover
                    desc="Cover",
                    data=data,
                )
            )

    try:
        # v2.3 で書き、互換のため v1 タグも残す
        audio.save(v2_version=3, v1=2)
    except Exception as exc:
        raise TaggingError(f"ID3 タグを保存できませんでした: {exc}") from exc


def read_cover_bytes(path: Path | None) -> bytes | None:
    """サムネイルファイルを読み込む（無ければ None）."""
    if not path:
        return None
    try:
        return Path(path).read_bytes()
    except OSError:
        return None
