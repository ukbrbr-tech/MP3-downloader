"""モード（新規のみ / 全件 / 確認のみ / 選択）の動きを確認する."""

from __future__ import annotations

import pytest

from conftest import fake_factory, requires_ffmpeg, video_info
from mp3dl import job as job_module
from mp3dl.archive import Archive
from mp3dl.config import Settings
from mp3dl.job import MODES, Plan, make_plan, run_plan
from mp3dl.playlist import Playlist, PlaylistError, Track


def build_plan(tmp_path, done_ids=()) -> Plan:
    tracks = [
        Track(f"video{i:07d}", f"Song {i}", f"https://www.youtube.com/watch?v=video{i:07d}", index=i)
        for i in range(1, 4)
    ]
    playlist = Playlist(title="My Playlist", url="https://example.com/list", tracks=tracks)
    return Plan(
        playlist=playlist,
        destination=tmp_path / "My Playlist",
        new_tracks=[t for t in tracks if t.video_id not in done_ids],
        done_tracks=[t for t in tracks if t.video_id in done_ids],
    )


def test_modes_are_available():
    assert list(MODES) == ["new", "all", "check", "select"]


def test_mode_new_only_returns_new_tracks(tmp_path):
    plan = build_plan(tmp_path, done_ids={"video0000001"})
    assert [t.video_id for t in plan.tracks_for_mode("new")] == [
        "video0000002",
        "video0000003",
    ]


def test_mode_all_returns_everything(tmp_path):
    plan = build_plan(tmp_path, done_ids={"video0000001"})
    assert len(plan.tracks_for_mode("all")) == 3


def test_mode_select_uses_given_ids(tmp_path):
    plan = build_plan(tmp_path)
    chosen = plan.tracks_for_mode("select", ["video0000003"])
    assert [t.video_id for t in chosen] == ["video0000003"]


def test_summary_text_contains_counts(tmp_path):
    text = build_plan(tmp_path, done_ids={"video0000001"}).summary_text()
    assert "動画総数: 3 件" in text
    assert "処理済み: 1 件" in text
    assert "新規: 2 件" in text


def test_make_plan_uses_playlist_subfolder(tmp_path, monkeypatch):
    playlist = Playlist(
        title="My Playlist",
        url="u",
        tracks=[Track("aaaaaaaaaaa", "A", "u", index=1)],
    )
    monkeypatch.setattr(job_module, "fetch_playlist", lambda url, **kw: playlist)

    plan = make_plan("https://example.com/list", tmp_path, settings=Settings())
    assert plan.destination == tmp_path / "My Playlist"


def test_make_plan_marks_processed_videos(tmp_path, monkeypatch):
    tracks = [
        Track("aaaaaaaaaaa", "A", "u1", index=1),
        Track("bbbbbbbbbbb", "B", "u2", index=2),
    ]
    playlist = Playlist(title="P", url="u", tracks=tracks)
    monkeypatch.setattr(job_module, "fetch_playlist", lambda url, **kw: playlist)

    Archive(tmp_path / "P").record("aaaaaaaaaaa")

    plan = make_plan("https://example.com/list", tmp_path, settings=Settings())
    assert [t.video_id for t in plan.new_tracks] == ["bbbbbbbbbbb"]
    assert [t.video_id for t in plan.done_tracks] == ["aaaaaaaaaaa"]


def test_make_plan_rejects_empty_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr(
        job_module, "fetch_playlist", lambda url, **kw: Playlist(title="P", url="u")
    )
    with pytest.raises(PlaylistError):
        make_plan("https://example.com/list", tmp_path, settings=Settings())


@requires_ffmpeg
def test_run_plan_writes_log_and_archive(tmp_path, monkeypatch):
    plan = build_plan(tmp_path)
    videos = {
        t.video_id: video_info(t.video_id, t.title) for t in plan.playlist.tracks
    }
    monkeypatch.setattr(
        job_module,
        "download_tracks",
        _recording_download(videos),
    )

    summary = run_plan(plan, mode="new", settings=Settings())

    assert summary.downloaded == 3
    assert (plan.destination / "app.log").is_file()
    assert len(Archive(plan.destination)) == 3


def _recording_download(videos):
    """本物の download_tracks に偽 yt-dlp を渡す."""
    from mp3dl.pipeline import download_tracks as real

    def wrapper(tracks, **kwargs):
        kwargs["ydl_factory"] = fake_factory(videos)
        return real(tracks, **kwargs)

    return wrapper


@requires_ffmpeg
def test_second_run_downloads_only_newly_added(tmp_path, monkeypatch):
    """月曜に 2 曲、金曜に 3 曲になった場合、金曜は 1 曲だけ処理する."""
    monday = [
        Track(f"video{i:07d}", f"Song {i}", f"https://www.youtube.com/watch?v=video{i:07d}", index=i)
        for i in range(1, 3)
    ]
    friday = monday + [
        Track("video0000003", "Song 3", "https://www.youtube.com/watch?v=video0000003", index=3)
    ]
    videos = {t.video_id: video_info(t.video_id, t.title) for t in friday}
    monkeypatch.setattr(job_module, "download_tracks", _recording_download(videos))

    def set_playlist(tracks):
        monkeypatch.setattr(
            job_module,
            "fetch_playlist",
            lambda url, **kw: Playlist(title="Weekly", url="u", tracks=list(tracks)),
        )

    settings = Settings()
    set_playlist(monday)
    plan = make_plan("u", tmp_path, settings=settings)
    assert run_plan(plan, mode="new", settings=settings).downloaded == 2

    set_playlist(friday)
    plan2 = make_plan("u", tmp_path, settings=settings)
    assert [t.video_id for t in plan2.new_tracks] == ["video0000003"]
    summary = run_plan(plan2, mode="new", settings=settings)
    assert summary.downloaded == 1
    assert (plan2.destination / "03 - Song 3.mp3").is_file()
