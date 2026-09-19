"""ローカル Web UI のジョブ制御を検証する（サーバーは起動しない）."""

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from mp3dl import web
from mp3dl.downloader import DownloadError, Result, Track
from mp3dl.web import Job, State, _resolve_folder


@pytest.fixture
def fake_backend(monkeypatch):
    """yt-dlp と ffmpeg を使わずにジョブを動かすための差し替え."""
    calls = {"downloaded": []}

    monkeypatch.setattr(web, "ensure_ffmpeg", lambda location: None)

    def fake_fetch(url, cookies_from_browser=None):
        return [
            Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa"),
            Track("bbbbbbbbbbb", "Song B", "https://youtu.be/bbbbbbbbbbb"),
        ]

    def fake_download(tracks, *, on_result=None, **kwargs):
        results = []
        for track in tracks:
            calls["downloaded"].append(track.title)
            result = Result(track, "downloaded")
            results.append(result)
            if on_result:
                on_result(result)
        return results

    monkeypatch.setattr(web, "fetch_tracks", fake_fetch)
    monkeypatch.setattr(web, "download_tracks", fake_download)
    return calls


def test_job_downloads_everything(tmp_path, fake_backend):
    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    state = job.to_json()
    assert state["status"] == "done"
    assert state["finished"] is True
    assert state["counts"] == {"total": 2, "done": 2, "skipped": 0, "failed": 0}
    assert fake_backend["downloaded"] == ["Song A", "Song B"]


def test_job_skips_existing_mp3(tmp_path, fake_backend):
    (tmp_path / "Song A.mp3").write_bytes(b"")

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    state = job.to_json()
    assert state["counts"] == {"total": 2, "done": 1, "skipped": 1, "failed": 0}
    assert state["items"][0]["status"] == "skipped"
    assert state["items"][0]["detail"]
    assert fake_backend["downloaded"] == ["Song B"]  # 既存曲は渡されない


def test_skip_existing_can_be_turned_off(tmp_path, fake_backend):
    (tmp_path / "Song A.mp3").write_bytes(b"")

    job = Job(url="https://example.com/list", folder=tmp_path, skip_existing=False)
    job.run()

    assert fake_backend["downloaded"] == ["Song A", "Song B"]


def test_dry_run_does_not_download(tmp_path, fake_backend):
    job = Job(url="https://example.com/list", folder=tmp_path, dry_run=True)
    job.run()

    assert job.to_json()["status"] == "done"
    assert fake_backend["downloaded"] == []


def test_all_skipped_finishes_without_calling_downloader(tmp_path, fake_backend):
    (tmp_path / "Song A.mp3").write_bytes(b"")
    (tmp_path / "Song B.mp3").write_bytes(b"")

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    assert "すべてダウンロード済み" in job.to_json()["message"]
    assert fake_backend["downloaded"] == []


def test_playlist_error_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ensure_ffmpeg", lambda location: None)

    def boom(url, cookies_from_browser=None):
        raise DownloadError("再生リストを取得できませんでした")

    monkeypatch.setattr(web, "fetch_tracks", boom)

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    state = job.to_json()
    assert state["status"] == "error"
    assert "再生リストを取得できませんでした" in state["error"]


def test_missing_ffmpeg_is_reported(tmp_path, monkeypatch):
    def boom(location):
        raise DownloadError("ffmpeg が見つかりません。")

    monkeypatch.setattr(web, "ensure_ffmpeg", boom)

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    assert job.to_json()["status"] == "error"
    assert "ffmpeg" in job.to_json()["error"]


def test_cancel_stops_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ensure_ffmpeg", lambda location: None)
    monkeypatch.setattr(
        web,
        "fetch_tracks",
        lambda url, cookies_from_browser=None: [
            Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa")
        ],
    )

    seen = {}

    def fake_download(tracks, *, should_stop=None, on_result=None, **kwargs):
        seen["stopped"] = should_stop()
        return []

    monkeypatch.setattr(web, "download_tracks", fake_download)

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.cancel()
    job.run()

    assert seen["stopped"] is True
    assert job.to_json()["status"] == "cancelled"


def test_state_rejects_a_second_job_while_running(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ensure_ffmpeg", lambda location: None)
    release = threading.Event()

    def slow_fetch(url, cookies_from_browser=None):
        release.wait(timeout=5)
        return [Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa")]

    monkeypatch.setattr(web, "fetch_tracks", slow_fetch)
    monkeypatch.setattr(web, "download_tracks", lambda tracks, **kwargs: [])

    state = State()
    state.start(Job(url="https://example.com/list", folder=tmp_path))

    with pytest.raises(RuntimeError):
        state.start(Job(url="https://example.com/other", folder=tmp_path))

    release.set()
    for _ in range(50):
        if state.job.to_json()["finished"]:
            break
        time.sleep(0.05)

    # 終わった後なら次のジョブを開始できる
    state.start(Job(url="https://example.com/other", folder=tmp_path))


def test_resolve_folder_expands_home_and_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _resolve_folder("~/Music/x") == (tmp_path / "Music" / "x").resolve()
    assert _resolve_folder("   ") == web.default_folder().resolve()
