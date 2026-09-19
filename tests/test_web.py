"""ローカル Web UI のジョブ制御を検証する（サーバーは起動しない）."""

from __future__ import annotations

import threading
import time

import pytest

from mp3dl import web
from mp3dl.job import Plan
from mp3dl.pipeline import DOWNLOADED, Result, Summary
from mp3dl.playlist import Playlist, PlaylistError, Track
from mp3dl.web import Job, State, _resolve_folder


def make_plan_object(tmp_path, done_ids=()):
    tracks = [
        Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa", index=1),
        Track("bbbbbbbbbbb", "Song B", "https://youtu.be/bbbbbbbbbbb", index=2),
    ]
    playlist = Playlist(title="List", url="https://example.com/list", tracks=tracks)
    return Plan(
        playlist=playlist,
        destination=tmp_path / "List",
        new_tracks=[t for t in tracks if t.video_id not in done_ids],
        done_tracks=[t for t in tracks if t.video_id in done_ids],
    )


@pytest.fixture
def fake_backend(monkeypatch, tmp_path):
    """yt-dlp と ffmpeg を使わずにジョブを動かすための差し替え."""
    calls = {"downloaded": [], "plan": None}

    def fake_make_plan(url, folder, **kwargs):
        plan = calls["plan"] or make_plan_object(tmp_path)
        return plan

    def fake_run_plan(plan, *, mode="new", on_result=None, **kwargs):
        summary = Summary()
        for track in plan.tracks_for_mode(mode):
            calls["downloaded"].append(track.title)
            result = Result(track, DOWNLOADED)
            summary.add(result)
            if on_result:
                on_result(result)
        return summary

    monkeypatch.setattr(web, "make_plan", fake_make_plan)
    monkeypatch.setattr(web, "run_plan", fake_run_plan)
    return calls


def test_job_downloads_everything(tmp_path, fake_backend):
    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    state = job.to_json()
    assert state["status"] == "done"
    assert state["finished"] is True
    assert state["counts"] == {"total": 2, "done": 2, "skipped": 0, "failed": 0}
    assert fake_backend["downloaded"] == ["Song A", "Song B"]


def test_job_skips_processed_video(tmp_path, fake_backend):
    fake_backend["plan"] = make_plan_object(tmp_path, done_ids={"aaaaaaaaaaa"})

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    state = job.to_json()
    assert state["counts"] == {"total": 2, "done": 1, "skipped": 1, "failed": 0}
    assert state["items"][0]["status"] == "skipped"
    assert state["items"][0]["detail"]
    assert fake_backend["downloaded"] == ["Song B"]  # 処理済みは渡されない


def test_skip_existing_can_be_turned_off(tmp_path, fake_backend):
    fake_backend["plan"] = make_plan_object(tmp_path, done_ids={"aaaaaaaaaaa"})

    job = Job(url="https://example.com/list", folder=tmp_path, skip_existing=False)
    job.run()

    assert job.mode == "all"
    assert fake_backend["downloaded"] == ["Song A", "Song B"]


def test_dry_run_does_not_download(tmp_path, fake_backend):
    job = Job(url="https://example.com/list", folder=tmp_path, dry_run=True)
    job.run()

    assert job.mode == "check"
    assert job.to_json()["status"] == "done"
    assert fake_backend["downloaded"] == []


def test_all_processed_finishes_without_downloading(tmp_path, fake_backend):
    fake_backend["plan"] = make_plan_object(
        tmp_path, done_ids={"aaaaaaaaaaa", "bbbbbbbbbbb"}
    )

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    assert "すべて処理済み" in job.to_json()["message"]
    assert fake_backend["downloaded"] == []


def test_playlist_error_is_reported(tmp_path, monkeypatch):
    def boom(url, folder, **kwargs):
        raise PlaylistError("再生リストを取得できませんでした")

    monkeypatch.setattr(web, "make_plan", boom)

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    state = job.to_json()
    assert state["status"] == "error"
    assert "再生リストを取得できませんでした" in state["error"]


def test_missing_ffmpeg_is_reported(tmp_path, monkeypatch, fake_backend):
    from mp3dl.pipeline import DownloadError

    def boom(plan, **kwargs):
        raise DownloadError("FFmpeg が見つかりません。")

    monkeypatch.setattr(web, "run_plan", boom)

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.run()

    assert job.to_json()["status"] == "error"
    assert "FFmpeg" in job.to_json()["error"]


def test_cancel_stops_the_run(tmp_path, monkeypatch, fake_backend):
    seen = {}

    def fake_run_plan(plan, *, should_stop=None, **kwargs):
        seen["stopped"] = should_stop()
        return Summary()

    monkeypatch.setattr(web, "run_plan", fake_run_plan)

    job = Job(url="https://example.com/list", folder=tmp_path)
    job.cancel()
    job.run()

    assert seen["stopped"] is True
    assert job.to_json()["status"] == "cancelled"


def test_state_rejects_a_second_job_while_running(tmp_path, monkeypatch, fake_backend):
    release = threading.Event()
    original = web.make_plan

    def slow_make_plan(url, folder, **kwargs):
        release.wait(timeout=5)
        return original(url, folder, **kwargs)

    monkeypatch.setattr(web, "make_plan", slow_make_plan)

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
