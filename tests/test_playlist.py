"""再生リストの読み取りのテスト（yt-dlp は差し替える）."""

from __future__ import annotations

import pytest

from mp3dl.playlist import Playlist, PlaylistError, fetch_playlist


class StubYDL:
    """extract_info が決まった値を返す偽 yt-dlp."""

    info: dict | None = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        if isinstance(self.info, Exception):
            raise self.info
        return self.info


def stub(info):
    return type("Bound", (StubYDL,), {"info": info})


PLAYLIST_INFO = {
    "title": "My Playlist",
    "webpage_url": "https://www.youtube.com/playlist?list=PL1",
    "uploader": "Owner",
    "entries": [
        {"id": "aaaaaaaaaaa", "title": "Song A", "url": "https://youtu.be/aaaaaaaaaaa"},
        None,  # 非公開・削除済み
        {"id": "bbbbbbbbbbb", "title": "Song B", "uploader": "Ch B"},
    ],
}


def test_fetch_playlist_indexes_tracks():
    playlist = fetch_playlist("https://example.com/list", ydl_factory=stub(PLAYLIST_INFO))

    assert playlist.title == "My Playlist"
    assert [t.index for t in playlist.tracks] == [1, 2]
    assert [t.video_id for t in playlist.tracks] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert playlist.tracks[1].url.endswith("watch?v=bbbbbbbbbbb")
    assert playlist.tracks[1].uploader == "Ch B"


def test_nested_playlists_are_flattened():
    info = {
        "title": "Outer",
        "entries": [
            {"_type": "playlist", "entries": [{"id": "ccccccccccc", "title": "C"}]},
            {"id": "ddddddddddd", "title": "D"},
        ],
    }
    playlist = fetch_playlist("u", ydl_factory=stub(info))
    assert [t.video_id for t in playlist.tracks] == ["ccccccccccc", "ddddddddddd"]


def test_single_video():
    info = {"id": "eeeeeeeeeee", "title": "Only One", "webpage_url": "https://youtu.be/e"}
    playlist = fetch_playlist("u", ydl_factory=stub(info))
    assert len(playlist.tracks) == 1
    assert playlist.tracks[0].index == 1


def test_empty_url_is_rejected():
    with pytest.raises(PlaylistError):
        fetch_playlist("   ")


def test_missing_info_raises():
    with pytest.raises(PlaylistError):
        fetch_playlist("u", ydl_factory=stub(None))


def test_ytdlp_error_is_wrapped():
    factory = stub(RuntimeError("ERROR: Unable to download webpage; please report this issue"))
    with pytest.raises(PlaylistError) as excinfo:
        fetch_playlist("u", ydl_factory=factory)
    assert "please report" not in str(excinfo.value)


def test_destination_uses_playlist_name(tmp_path):
    playlist = Playlist(title='Mix: best / 2024?', url="u")
    dest = playlist.destination(tmp_path)
    assert dest.parent == tmp_path
    assert not any(ch in dest.name for ch in '\\/:*?"<>|')


def test_destination_without_subfolder(tmp_path):
    playlist = Playlist(title="X", url="u")
    assert playlist.destination(tmp_path, subfolder=False) == tmp_path
