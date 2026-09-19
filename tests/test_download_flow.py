"""yt-dlp を差し替えて、スキップ判定を含むダウンロードの流れを検証する."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mp3dl import downloader
from mp3dl.downloader import Track, download_tracks
from mp3dl.library import Library


class FakeYoutubeDL:
    """呼ばれた URL を記録し、mp3 ができたことにするダミー."""

    calls: list[str] = []
    fail_urls: set[str] = set()
    titles: dict[str, str] = {}

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def download(self, urls):
        for url in urls:
            FakeYoutubeDL.calls.append(url)
            if url in FakeYoutubeDL.fail_urls:
                raise RuntimeError("boom")
            dest = Path(self.opts["outtmpl"]).parent
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{FakeYoutubeDL.titles[url]}.mp3").write_bytes(b"")


def setup_function(_):
    FakeYoutubeDL.calls = []
    FakeYoutubeDL.fail_urls = set()
    FakeYoutubeDL.titles = {}


def _patch(monkeypatch):
    monkeypatch.setattr(downloader, "_import_ytdlp", lambda: FakeYoutubeDL)


def test_second_run_skips_everything(tmp_path, monkeypatch):
    _patch(monkeypatch)
    tracks = [
        Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa"),
        Track("bbbbbbbbbbb", "Song B", "https://youtu.be/bbbbbbbbbbb"),
    ]
    FakeYoutubeDL.titles = {t.url: t.title for t in tracks}

    first = download_tracks(tracks, dest=tmp_path, library=Library.scan(tmp_path))
    assert [r.status for r in first] == ["downloaded", "downloaded"]
    assert sorted(p.name for p in tmp_path.glob("*.mp3")) == ["Song A.mp3", "Song B.mp3"]

    FakeYoutubeDL.calls = []
    second = download_tracks(tracks, dest=tmp_path, library=Library.scan(tmp_path))
    assert [r.status for r in second] == ["skipped", "skipped"]
    assert FakeYoutubeDL.calls == []


def test_manually_placed_mp3_is_not_redownloaded(tmp_path, monkeypatch):
    _patch(monkeypatch)
    (tmp_path / "song a.mp3").write_bytes(b"")  # 履歴ファイル無しで手動配置
    track = Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa")
    FakeYoutubeDL.titles = {track.url: track.title}

    results = download_tracks([track], dest=tmp_path, library=Library.scan(tmp_path))

    assert results[0].status == "skipped"
    assert FakeYoutubeDL.calls == []


def test_failure_does_not_stop_other_tracks_or_get_archived(tmp_path, monkeypatch):
    _patch(monkeypatch)
    bad = Track("aaaaaaaaaaa", "Bad", "https://youtu.be/aaaaaaaaaaa")
    good = Track("bbbbbbbbbbb", "Good", "https://youtu.be/bbbbbbbbbbb")
    FakeYoutubeDL.titles = {bad.url: bad.title, good.url: good.title}
    FakeYoutubeDL.fail_urls = {bad.url}

    results = download_tracks([bad, good], dest=tmp_path, library=Library.scan(tmp_path))

    assert [r.status for r in results] == ["failed", "downloaded"]
    # 失敗した曲は履歴に残らないので、次回また試される
    assert Library.scan(tmp_path).reason_to_skip("aaaaaaaaaaa", "Bad") is None


def test_duplicate_track_in_playlist_downloaded_once(tmp_path, monkeypatch):
    _patch(monkeypatch)
    track = Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa")
    FakeYoutubeDL.titles = {track.url: track.title}

    results = download_tracks([track, track], dest=tmp_path, library=Library.scan(tmp_path))

    assert [r.status for r in results] == ["downloaded", "skipped"]
    assert FakeYoutubeDL.calls == [track.url]


def test_parallel_jobs_download_all(tmp_path, monkeypatch):
    _patch(monkeypatch)
    tracks = [Track(f"{i:011d}", f"Song {i}", f"https://youtu.be/{i:011d}") for i in range(6)]
    FakeYoutubeDL.titles = {t.url: t.title for t in tracks}

    results = download_tracks(tracks, dest=tmp_path, library=Library.scan(tmp_path), jobs=3)

    assert [r.status for r in results] == ["downloaded"] * 6
    assert len(list(tmp_path.glob("*.mp3"))) == 6
