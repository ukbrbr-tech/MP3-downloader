"""起動スクリプト（run_gui.pyw）が失敗を必ず知らせることを確認する.

pythonw.exe で起動すると標準出力・標準エラーがどこにも出ないため、
「一瞬で閉じる」状態にならないことがこのファイルの目的。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / "run_gui.pyw"


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    """run_gui.pyw を（__main__ として実行せずに）読み込む."""
    spec = importlib.util.spec_from_loader(
        "run_gui_module", loader=None, origin=str(LAUNCHER)
    )
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(LAUNCHER)
    exec(compile(LAUNCHER.read_text(encoding="utf-8"), str(LAUNCHER), "exec"), module.__dict__)

    shown: list[str] = []
    monkeypatch.setattr(module, "_show", shown.append)
    monkeypatch.setattr(module, "HERE", tmp_path)
    monkeypatch.setattr(module, "_log_path", lambda: tmp_path / module.LOG_NAME)
    module.shown = shown
    return module


def test_launcher_file_exists():
    assert LAUNCHER.is_file()


def test_failure_is_shown_and_logged(launcher, tmp_path):
    code = launcher._fail("起動できません", "詳しい原因\nTraceback...")

    assert code == 1
    assert launcher.shown, "利用者に何も表示されないまま終わってはいけない"
    assert "起動できません" in launcher.shown[0]
    assert "debug_gui.bat" in launcher.shown[0]  # 次にやることを案内する

    log = (tmp_path / launcher.LOG_NAME).read_text(encoding="utf-8")
    assert "起動できません" in log
    assert "Traceback..." in log


def test_log_failure_still_shows_message(launcher, monkeypatch):
    """ログを書けない場所でも、画面表示だけは必ず行う."""
    monkeypatch.setattr(launcher, "_write_log", lambda text: None)

    launcher._fail("書き込めません", "詳細")

    assert launcher.shown and "書き込めません" in launcher.shown[0]


def test_old_python_is_reported(launcher, monkeypatch):
    monkeypatch.setattr(launcher.sys, "version_info", (3, 8, 0))

    assert launcher.main() == 1
    assert "Python 3.9 以上" in launcher.shown[0]


def test_missing_tkinter_is_reported(launcher, monkeypatch):
    """tkinter が無い環境でも、黙って終了せず原因を伝える."""
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("No module named 'tkinter'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert launcher.main() == 1
    assert "tkinter" in launcher.shown[0]
