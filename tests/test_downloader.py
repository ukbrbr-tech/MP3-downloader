import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from mp3dl.downloader import DownloadError, Track, _build_options, _walk_entries, ensure_ffmpeg, plan
from mp3dl.library import Library


def test_walk_entries_flattens_nested_playlists():
    info = {
        "entries": [
            {"id": "aaaaaaaaaaa", "title": "A", "url": "https://youtu.be/aaaaaaaaaaa"},
            None,  # 削除済み動画
            {
                "_type": "playlist",
                "entries": [{"id": "bbbbbbbbbbb", "title": "B", "url": "https://youtu.be/bbbbbbbbbbb"}],
            },
        ]
    }
    tracks = list(_walk_entries(info))
    assert [t.title for t in tracks] == ["A", "B"]


def test_walk_entries_handles_single_video():
    info = {"id": "aaaaaaaaaaa", "title": "Solo"}
    tracks = list(_walk_entries(info))
    assert len(tracks) == 1
    assert tracks[0].url == "https://www.youtube.com/watch?v=aaaaaaaaaaa"


def test_plan_marks_existing_files_as_skipped(tmp_path):
    (tmp_path / "Already Here.mp3").write_bytes(b"")
    library = Library.scan(tmp_path)
    tracks = [
        Track("aaaaaaaaaaa", "Already Here", "https://youtu.be/aaaaaaaaaaa"),
        Track("bbbbbbbbbbb", "Brand New", "https://youtu.be/bbbbbbbbbbb"),
    ]

    result = plan(tracks, library)

    assert result[0][1] is not None
    assert result[1][1] is None


def test_build_options_sets_mp3_postprocessor(tmp_path):
    opts = _build_options(
        dest=tmp_path,
        filename_template="%(title)s.%(ext)s",
        quality="320",
        ffmpeg_location=None,
        cookies_from_browser=None,
        embed_thumbnail=False,
        verbose=False,
    )
    extract = opts["postprocessors"][0]
    assert extract["preferredcodec"] == "mp3"
    assert extract["preferredquality"] == "320"
    assert opts["outtmpl"].endswith("%(title)s.%(ext)s")


def test_ensure_ffmpeg_rejects_missing_path(tmp_path):
    with pytest.raises(DownloadError):
        ensure_ffmpeg(str(tmp_path / "nope" / "ffmpeg"))
