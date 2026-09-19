"""コマンドラインの動きを確認する（ネットワークは使わない）."""

from __future__ import annotations

import pytest

from mp3dl import cli
from mp3dl.job import Plan
from mp3dl.pipeline import DOWNLOADED, Result, Summary
from mp3dl.playlist import Playlist, PlaylistError, Track


@pytest.fixture(autouse=True)
def no_settings_writes(monkeypatch):
    monkeypatch.setattr(cli, "save_settings", lambda *a, **k: None)


def sample_plan(tmp_path, done_ids=()):
    tracks = [
        Track(f"video{i:07d}", f"Song {i}", f"https://www.youtube.com/watch?v=video{i:07d}", index=i)
        for i in range(1, 3)
    ]
    return Plan(
        playlist=Playlist(title="My Playlist", url="u", tracks=tracks),
        destination=tmp_path / "My Playlist",
        new_tracks=[t for t in tracks if t.video_id not in done_ids],
        done_tracks=[t for t in tracks if t.video_id in done_ids],
    )


def test_help_runs():
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_check_deps_returns_status(capsys):
    code = cli.main(["--check-deps"])
    out = capsys.readouterr().out
    assert "Python" in out and "FFmpeg" in out
    assert code in (0, 1)


def test_check_mode_lists_new_videos(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "make_plan", lambda url, root, **kw: sample_plan(tmp_path))
    called = {"run": False}
    monkeypatch.setattr(cli, "run_plan", lambda *a, **k: called.__setitem__("run", True))

    code = cli.main(["https://example.com/list", "-o", str(tmp_path), "--mode", "check"])
    out = capsys.readouterr().out

    assert code == 0
    assert called["run"] is False
    assert "新規: 2 件" in out
    assert "Song 1" in out


def test_new_mode_downloads_only_new(tmp_path, monkeypatch, capsys):
    plan = sample_plan(tmp_path, done_ids={"video0000001"})
    monkeypatch.setattr(cli, "make_plan", lambda url, root, **kw: plan)

    seen = {}

    def fake_run_plan(plan, *, mode="new", on_result=None, **kwargs):
        seen["targets"] = [t.video_id for t in plan.tracks_for_mode(mode)]
        summary = Summary()
        for track in plan.tracks_for_mode(mode):
            result = Result(track, DOWNLOADED)
            summary.add(result)
            if on_result:
                on_result(result)
        return summary

    monkeypatch.setattr(cli, "run_plan", fake_run_plan)

    code = cli.main(["https://example.com/list", "-o", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert seen["targets"] == ["video0000002"]
    assert "成功 1" in out


def test_nothing_new_exits_cleanly(tmp_path, monkeypatch, capsys):
    plan = sample_plan(tmp_path, done_ids={"video0000001", "video0000002"})
    monkeypatch.setattr(cli, "make_plan", lambda url, root, **kw: plan)

    code = cli.main(["https://example.com/list", "-o", str(tmp_path)])

    assert code == 0
    assert "新しい動画はありません" in capsys.readouterr().out


def test_playlist_error_returns_error_code(tmp_path, monkeypatch, capsys):
    def boom(url, root, **kw):
        raise PlaylistError("読み込めませんでした")

    monkeypatch.setattr(cli, "make_plan", boom)

    code = cli.main(["https://example.com/list", "-o", str(tmp_path)])

    assert code == 1
    assert "読み込めませんでした" in capsys.readouterr().err


def test_reset_archive_clears_history(tmp_path, monkeypatch, capsys):
    from mp3dl.archive import Archive

    plan = sample_plan(tmp_path)
    plan.destination.mkdir(parents=True, exist_ok=True)
    Archive(plan.destination).record("video0000001")
    monkeypatch.setattr(cli, "make_plan", lambda url, root, **kw: plan)
    monkeypatch.setattr(cli, "run_plan", lambda *a, **k: Summary())

    cli.main(["https://example.com/list", "-o", str(tmp_path), "--reset-archive", "--mode", "check"])

    assert len(Archive(plan.destination)) == 0
    assert "履歴をリセット" in capsys.readouterr().out
