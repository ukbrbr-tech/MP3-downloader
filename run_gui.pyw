"""ダブルクリックで GUI を起動するためのファイル.

Windows では拡張子 .pyw（pythonw.exe）で起動するとコンソールが出ない代わりに、
エラーメッセージもどこにも表示されずに「一瞬で閉じる」ように見えてしまう。
そのため、ここで起動時の失敗を必ず捕まえて、

  * 画面にメッセージを出す（tkinter が壊れていても Windows の標準ダイアログを使う）
  * このファイルと同じ場所の error.log に詳しい内容を書く

ようにしている。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOG_NAME = "mp3dl-error.log"
TITLE = "mp3dl を起動できませんでした"


def _log_path() -> Path:
    """書き込めるログの置き場所を返す（アプリのフォルダ → 一時フォルダ）."""
    for folder in (HERE, Path.home(), Path(__import__("tempfile").gettempdir())):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / LOG_NAME
            with probe.open("a", encoding="utf-8"):
                pass
            return probe
        except OSError:
            continue
    return HERE / LOG_NAME


def _write_log(text: str) -> Path | None:
    path = _log_path()
    try:
        from datetime import datetime

        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            handle.write(text)
            handle.write("\n")
        return path
    except OSError:
        return None


def _show(message: str) -> None:
    """ダイアログでメッセージを出す（使える手段を順に試す）."""
    try:  # Windows なら tkinter が無くても必ず出せる
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, TITLE, 0x10)
        return
    except Exception:
        pass
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(TITLE, message)
        root.destroy()
        return
    except Exception:
        pass
    print(f"{TITLE}\n{message}", file=sys.stderr)


def _fail(summary: str, detail: str) -> int:
    log = _write_log(f"{summary}\n\n{detail}")
    message = summary
    if log:
        message += f"\n\n詳しい内容:\n{log}"
    message += "\n\nこの画面が出ずに一瞬で閉じる場合は、debug_gui.bat を実行してください。"
    _show(message)
    return 1


def main() -> int:
    if sys.version_info < (3, 9):
        return _fail(
            f"Python 3.9 以上が必要です（今の Python は {sys.version.split()[0]}）。",
            f"実行ファイル: {sys.executable}",
        )

    sys.path.insert(0, str(HERE))

    try:
        import tkinter  # noqa: F401  (GUI に必要。先に確かめて原因を分かりやすくする)
    except Exception as exc:
        return _fail(
            "GUI に必要な tkinter が使えません。\n\n"
            "Windows: 「アプリと機能」から Python を選び「変更(Modify)」→\n"
            "         「tcl/tk and IDLE」にチェックを入れて再インストールしてください。\n"
            "Linux:   sudo apt install python3-tk",
            f"{exc}\n実行ファイル: {sys.executable}\n{traceback.format_exc()}",
        )

    try:
        from mp3dl.gui import run
    except Exception as exc:
        return _fail(
            f"アプリの読み込みに失敗しました: {exc}\n\n"
            "setup_windows.bat を実行して、必要なライブラリを入れ直してください。",
            f"実行ファイル: {sys.executable}\nフォルダ: {HERE}\n{traceback.format_exc()}",
        )

    try:
        return run()
    except Exception as exc:
        return _fail(
            f"起動中にエラーが発生しました: {exc}",
            f"実行ファイル: {sys.executable}\n{traceback.format_exc()}",
        )


if __name__ == "__main__":
    raise SystemExit(main())
