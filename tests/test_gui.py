"""GUI の起動と基本操作を、画面を出さずに確認する.

tkinter か表示環境が無い場合は自動で skip する。
"""

from __future__ import annotations

import pytest

tk = pytest.importorskip("tkinter")

from mp3dl.config import Settings  # noqa: E402
from mp3dl.job import Plan  # noqa: E402
from mp3dl.pipeline import DOWNLOADED, FAILED, Result, Summary  # noqa: E402
from mp3dl.playlist import Playlist, PlaylistError, Track  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    from mp3dl import gui

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("画面が使えない環境")
    root.withdraw()

    # 起動時のダイアログとファイル保存を抑止する
    monkeypatch.setattr(gui, "save_settings", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)

    settings = Settings(last_output_dir=str(tmp_path))
    instance = gui.App(root, settings=settings)
    instance.var_url.set("https://www.youtube.com/playlist?list=PL1")
    yield instance
    root.destroy()


def make_plan(tmp_path, done_ids=()):
    tracks = [
        Track(f"video{i:07d}", f"Song {i}", f"https://www.youtube.com/watch?v=video{i:07d}", index=i)
        for i in range(1, 4)
    ]
    playlist = Playlist(title="My Playlist", url="u", tracks=tracks)
    return Plan(
        playlist=playlist,
        destination=tmp_path / "My Playlist",
        new_tracks=[t for t in tracks if t.video_id not in done_ids],
        done_tracks=[t for t in tracks if t.video_id in done_ids],
    )


def drain(app, timeout: float = 10.0) -> None:
    """ワーカーの結果がキュー経由で画面に届くまで回す.

    ワーカーが終わり、キューが空になり、さらに「実行中」表示が解除される
    （= done メッセージまで処理された）ことを待つ。負荷が高い環境でも
    取りこぼさないように、時間で打ち切る形にしている。
    """
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        worker_done = not (app.worker and app.worker.is_alive())
        if worker_done and app.queue.empty() and str(app.btn_start["state"]) == "normal":
            break
        time.sleep(0.02)
    app.update()


def test_window_builds(app):
    assert app.btn_start["text"] == "開始"
    assert app.var_mode.get() == "new"
    assert app.var_counts.get() == "成功 0 / スキップ 0 / 失敗 0"


def test_check_lists_new_videos(app, tmp_path, monkeypatch):
    from mp3dl import gui

    plan = make_plan(tmp_path, done_ids={"video0000001"})
    monkeypatch.setattr(gui, "make_plan", lambda url, folder, **kw: plan)

    app.start_check()
    drain(app)

    rows = app.tree.get_children()
    assert len(rows) == 3
    states = [app.tree.item(row, "values")[3] for row in rows]
    assert states == ["処理済み", "新規", "新規"]
    assert "新規: 2 件" in app.log_text.get("1.0", "end")


def test_download_updates_counters(app, tmp_path, monkeypatch):
    from mp3dl import gui

    plan = make_plan(tmp_path)
    monkeypatch.setattr(gui, "make_plan", lambda url, folder, **kw: plan)

    def fake_run_plan(plan, *, mode="new", on_result=None, **kwargs):
        summary = Summary()
        for index, track in enumerate(plan.tracks_for_mode(mode)):
            result = Result(track, FAILED if index == 2 else DOWNLOADED, "失敗理由" if index == 2 else "")
            summary.add(result)
            if on_result:
                on_result(result)
        return summary

    monkeypatch.setattr(gui, "run_plan", fake_run_plan)

    app.start_download()
    drain(app)

    assert app.counts == {"ok": 2, "skip": 0, "fail": 1}
    assert app.var_counts.get() == "成功 2 / スキップ 0 / 失敗 1"
    assert app.var_overall.get() == 100.0


def test_check_mode_does_not_download(app, tmp_path, monkeypatch):
    from mp3dl import gui

    monkeypatch.setattr(gui, "make_plan", lambda url, folder, **kw: make_plan(tmp_path))
    called = {"run": False}

    def fake_run_plan(*a, **k):
        called["run"] = True
        return Summary()

    monkeypatch.setattr(gui, "run_plan", fake_run_plan)
    app.var_mode.set("check")

    app.start_download()
    drain(app)

    assert called["run"] is False
    assert "ダウンロードは行いません" in app.log_text.get("1.0", "end")


def test_select_mode_passes_checked_ids(app, tmp_path, monkeypatch):
    from mp3dl import gui

    plan = make_plan(tmp_path)
    monkeypatch.setattr(gui, "make_plan", lambda url, folder, **kw: plan)
    app.start_check()
    drain(app)

    app.var_mode.set("select")
    app._on_mode_change()
    app._check_all(False)
    app._toggle("video0000002")
    assert app.checked == {"video0000002"}

    seen = {}

    def fake_run_plan(plan, *, mode="new", selected_ids=None, on_result=None, **kwargs):
        seen["mode"] = mode
        seen["ids"] = list(selected_ids or [])
        seen["targets"] = [t.video_id for t in plan.tracks_for_mode(mode, selected_ids)]
        return Summary()

    monkeypatch.setattr(gui, "run_plan", fake_run_plan)
    app.start_download()
    drain(app)

    assert seen["mode"] == "select"
    assert seen["targets"] == ["video0000002"]


def test_error_is_shown_without_crashing(app, tmp_path, monkeypatch):
    from mp3dl import gui

    def boom(url, folder, **kw):
        raise PlaylistError("読み込めませんでした")

    monkeypatch.setattr(gui, "make_plan", boom)
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, msg: errors.append(msg))

    app.start_check()
    drain(app)

    assert errors and "読み込めませんでした" in errors[0]
    assert app.var_status.get() == "エラー"
    assert str(app.btn_start["state"]) == "normal"  # 操作が戻っている


def test_dark_mode_toggle(app):
    app.var_dark.set(True)
    app.apply_theme()
    assert str(app.log_text["bg"]) == gui_theme()["log_bg"]


def gui_theme():
    from mp3dl.gui import THEMES

    return THEMES["dark"]


def test_empty_url_is_rejected(app, monkeypatch):
    from mp3dl import gui

    warnings = []
    monkeypatch.setattr(gui.messagebox, "showwarning", lambda t, m: warnings.append(m))
    app.var_url.set("")

    app.start_check()

    assert warnings
    assert app.worker is None
