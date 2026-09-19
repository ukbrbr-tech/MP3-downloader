"""tkinter で作ったシンプルな GUI.

画面の更新は必ずメインスレッドで行い、重い処理は別スレッドで走らせて
キュー越しに結果を受け取る（tkinter はスレッドセーフではないため）。
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .archive import Archive
from .config import Settings, save_settings
from .deps import check_dependencies, format_report, missing_required
from .job import DEFAULT_MODE, MODES, Plan, make_plan, run_plan
from .pipeline import CANCELLED, DOWNLOADED, FAILED, Progress, Result, Summary
from .playlist import PlaylistError, Track

WINDOW_TITLE = f"mp3dl - YouTube 再生リスト mp3 保存ツール v{__version__}"

#: 画面の配色（ライト / ダーク）
THEMES = {
    "light": {
        "bg": "#f4f4f4",
        "fg": "#1a1a1a",
        "field": "#ffffff",
        "field_fg": "#1a1a1a",
        "select": "#cde3ff",
        "log_bg": "#ffffff",
        "log_fg": "#202020",
    },
    "dark": {
        "bg": "#232629",
        "fg": "#e8e8e8",
        "field": "#2e3236",
        "field_fg": "#e8e8e8",
        "select": "#3d5a80",
        "log_bg": "#1c1f22",
        "log_fg": "#dcdcdc",
    },
}

CHECKED = "☑"
UNCHECKED = "☐"


@dataclass
class Message:
    """ワーカースレッドから GUI へ渡す 1 件の通知."""

    kind: str  # log | plan | progress | result | done | error | counts
    payload: object = None


class App(ttk.Frame):
    """メインウィンドウ."""

    def __init__(self, master: tk.Tk, settings: Settings | None = None):
        super().__init__(master, padding=10)
        self.master: tk.Tk = master
        self.settings = settings or Settings.load()
        self.queue: "queue.Queue[Message]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.plan: Plan | None = None
        self.checked: set[str] = set()
        self.counts = {"ok": 0, "skip": 0, "fail": 0}
        self.total_targets = 0
        self.done_targets = 0

        self.master.title(WINDOW_TITLE)
        self.master.minsize(820, 640)
        self.grid(row=0, column=0, sticky="nsew")
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)

        self._build_variables()
        self._build_widgets()
        self.apply_theme()
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)
        self._pump_id: str | None = None
        self._closing = False
        self._pump_id = self.after(100, self._pump_queue)
        self.after(200, self._startup_check)

    # ------------------------------------------------------------ 画面構築

    def _build_variables(self) -> None:
        s = self.settings
        self.var_url = tk.StringVar(value=s.last_url)
        self.var_folder = tk.StringVar(value=str(s.output_root()))
        self.var_mode = tk.StringVar(value=s.mode if s.mode in MODES else DEFAULT_MODE)
        self.var_status = tk.StringVar(value="待機中")
        self.var_current = tk.StringVar(value="—")
        self.var_counts = tk.StringVar(value="成功 0 / スキップ 0 / 失敗 0")
        self.var_overall = tk.DoubleVar(value=0.0)
        self.var_track = tk.DoubleVar(value=0.0)
        self.var_dark = tk.BooleanVar(value=s.dark_mode)
        self.var_notify = tk.BooleanVar(value=s.notify_on_complete)
        self.var_thumb = tk.BooleanVar(value=s.embed_thumbnail)
        self.var_quality = tk.StringVar(value=s.quality)

    def _build_widgets(self) -> None:
        self.columnconfigure(0, weight=1)
        row = 0

        # --- URL -------------------------------------------------------
        url_frame = ttk.LabelFrame(self, text="1. YouTube 再生リスト URL", padding=8)
        url_frame.grid(row=row, column=0, sticky="ew", pady=(0, 6))
        url_frame.columnconfigure(0, weight=1)
        self.url_box = ttk.Combobox(
            url_frame, textvariable=self.var_url, values=self.settings.playlists
        )
        self.url_box.grid(row=0, column=0, sticky="ew")
        ttk.Button(url_frame, text="登録", width=8, command=self.remember_url).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Button(
            url_frame, text="登録を全て更新", width=14, command=self.start_all_playlists
        ).grid(row=0, column=2, padx=(6, 0))
        row += 1

        # --- 保存先 -----------------------------------------------------
        folder_frame = ttk.LabelFrame(self, text="2. 保存先フォルダ", padding=8)
        folder_frame.grid(row=row, column=0, sticky="ew", pady=6)
        folder_frame.columnconfigure(0, weight=1)
        ttk.Entry(folder_frame, textvariable=self.var_folder).grid(row=0, column=0, sticky="ew")
        ttk.Button(folder_frame, text="参照...", width=8, command=self.choose_folder).grid(
            row=0, column=1, padx=(6, 0)
        )
        ttk.Button(
            folder_frame, text="フォルダを開く", width=14, command=self.open_folder
        ).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(
            folder_frame,
            text="実際の保存先は「このフォルダ / 再生リスト名」になります。",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        row += 1

        # --- モードと操作 -----------------------------------------------
        mode_frame = ttk.LabelFrame(self, text="3. モードと実行", padding=8)
        mode_frame.grid(row=row, column=0, sticky="ew", pady=6)
        for index, (key, label) in enumerate(MODES.items()):
            ttk.Radiobutton(
                mode_frame,
                text=label,
                value=key,
                variable=self.var_mode,
                command=self._on_mode_change,
            ).grid(row=0, column=index, sticky="w", padx=(0, 12))

        buttons = ttk.Frame(mode_frame)
        buttons.grid(row=0, column=len(MODES), sticky="e", padx=(12, 0))
        mode_frame.columnconfigure(len(MODES), weight=1)
        self.btn_check = ttk.Button(buttons, text="確認", width=10, command=self.start_check)
        self.btn_check.grid(row=0, column=0, padx=(0, 6))
        self.btn_start = ttk.Button(buttons, text="開始", width=10, command=self.start_download)
        self.btn_start.grid(row=0, column=1, padx=(0, 6))
        self.btn_stop = ttk.Button(
            buttons, text="中止", width=10, command=self.stop, state="disabled"
        )
        self.btn_stop.grid(row=0, column=2)

        extras = ttk.Frame(mode_frame)
        extras.grid(row=1, column=0, columnspan=len(MODES) + 1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(
            extras, text="サムネイルを埋め込む", variable=self.var_thumb,
            command=self._on_option_change,
        ).grid(row=0, column=0, padx=(0, 12))
        ttk.Label(extras, text="音質(kbps):").grid(row=0, column=1)
        quality = ttk.Combobox(
            extras, textvariable=self.var_quality, width=6,
            values=("320", "256", "192", "128"), state="readonly",
        )
        quality.grid(row=0, column=2, padx=(4, 12))
        quality.bind("<<ComboboxSelected>>", lambda _e: self._on_option_change())
        ttk.Checkbutton(
            extras, text="完了時に通知", variable=self.var_notify,
            command=self._on_option_change,
        ).grid(row=0, column=3, padx=(0, 12))
        ttk.Checkbutton(
            extras, text="ダークモード", variable=self.var_dark, command=self.apply_theme
        ).grid(row=0, column=4, padx=(0, 12))
        ttk.Button(extras, text="履歴をリセット", command=self.reset_archive).grid(
            row=0, column=5
        )
        row += 1

        # --- 動画一覧 ---------------------------------------------------
        list_frame = ttk.LabelFrame(self, text="4. 動画一覧", padding=8)
        list_frame.grid(row=row, column=0, sticky="nsew", pady=6)
        self.rowconfigure(row, weight=3)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        columns = ("check", "no", "title", "state")
        self.tree = ttk.Treeview(
            list_frame, columns=columns, show="headings", selectmode="browse", height=8
        )
        self.tree.heading("check", text="")
        self.tree.heading("no", text="#")
        self.tree.heading("title", text="タイトル")
        self.tree.heading("state", text="状態")
        self.tree.column("check", width=34, anchor="center", stretch=False)
        self.tree.column("no", width=44, anchor="center", stretch=False)
        self.tree.column("title", width=460, anchor="w")
        self.tree.column("state", width=150, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)

        self.select_bar = ttk.Frame(list_frame)
        self.select_bar.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Button(self.select_bar, text="全て選択", command=lambda: self._check_all(True)).grid(
            row=0, column=0, padx=(0, 6)
        )
        ttk.Button(self.select_bar, text="全て解除", command=lambda: self._check_all(False)).grid(
            row=0, column=1, padx=(0, 6)
        )
        ttk.Button(self.select_bar, text="新規だけ選択", command=self._check_new_only).grid(
            row=0, column=2
        )
        row += 1

        # --- 進捗 -------------------------------------------------------
        progress_frame = ttk.LabelFrame(self, text="5. 進捗", padding=8)
        progress_frame.grid(row=row, column=0, sticky="ew", pady=6)
        progress_frame.columnconfigure(1, weight=1)
        ttk.Label(progress_frame, text="全体:").grid(row=0, column=0, sticky="w")
        ttk.Progressbar(
            progress_frame, variable=self.var_overall, maximum=100.0
        ).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Label(progress_frame, textvariable=self.var_counts, width=30).grid(row=0, column=2)
        ttk.Label(progress_frame, text="現在:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Progressbar(
            progress_frame, variable=self.var_track, maximum=100.0
        ).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Label(progress_frame, textvariable=self.var_status, width=30).grid(
            row=1, column=2, pady=(6, 0)
        )
        self.label_current = ttk.Label(
            progress_frame, textvariable=self.var_current, wraplength=700
        )
        self.label_current.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        row += 1

        # --- ログ -------------------------------------------------------
        log_frame = ttk.LabelFrame(self, text="6. ログ", padding=8)
        log_frame.grid(row=row, column=0, sticky="nsew", pady=(6, 0))
        self.rowconfigure(row, weight=2)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self._on_mode_change()

    # -------------------------------------------------------------- テーマ

    def apply_theme(self) -> None:
        """ライト / ダークの配色を適用する."""
        theme = THEMES["dark" if self.var_dark.get() else "light"]
        style = ttk.Style(self.master)
        try:
            style.theme_use("clam")
        except tk.TclError:  # pragma: no cover - 環境依存
            pass
        self.master.configure(bg=theme["bg"])
        style.configure(".", background=theme["bg"], foreground=theme["fg"])
        style.configure("TLabelframe", background=theme["bg"], foreground=theme["fg"])
        style.configure("TLabelframe.Label", background=theme["bg"], foreground=theme["fg"])
        style.configure("TButton", background=theme["field"], foreground=theme["fg"])
        style.configure(
            "TEntry", fieldbackground=theme["field"], foreground=theme["field_fg"]
        )
        style.configure(
            "TCombobox", fieldbackground=theme["field"], foreground=theme["field_fg"]
        )
        # 読み取り専用のコンボボックス（音質）は状態ごとに色を指定しないと読めなくなる
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", theme["field"]), ("disabled", theme["bg"])],
            foreground=[("readonly", theme["field_fg"]), ("disabled", theme["fg"])],
            selectbackground=[("readonly", theme["field"])],
            selectforeground=[("readonly", theme["field_fg"])],
        )
        style.configure(
            "Treeview.Heading", background=theme["bg"], foreground=theme["fg"]
        )
        style.configure(
            "Treeview",
            background=theme["field"],
            fieldbackground=theme["field"],
            foreground=theme["field_fg"],
        )
        style.map("Treeview", background=[("selected", theme["select"])])
        self.log_text.configure(
            bg=theme["log_bg"], fg=theme["log_fg"], insertbackground=theme["log_fg"]
        )
        self.settings.dark_mode = self.var_dark.get()
        self._save_settings()

    # -------------------------------------------------------------- ログ

    def log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ----------------------------------------------------- 起動時チェック

    def _startup_check(self) -> None:
        deps = check_dependencies(self.settings.ffmpeg_location or None)
        self.log("依存関係の確認:")
        for line in format_report(deps).splitlines():
            self.log("  " + line)
        missing = missing_required(deps)
        if missing:
            names = "、".join(dep.name for dep in missing)
            hints = "\n\n".join(f"{dep.name}: {dep.hint}" for dep in missing if dep.hint)
            self.var_status.set("依存関係が不足")
            messagebox.showwarning(
                "必要なソフトが不足しています",
                f"次のものが見つかりませんでした: {names}\n\n{hints}",
            )
        else:
            self.log("必要なものは揃っています。URL を入れて「確認」または「開始」を押してください。")

    # ------------------------------------------------------------ 入力処理

    def choose_folder(self) -> None:
        current = self.var_folder.get().strip()
        initial = current if current and Path(current).is_dir() else str(Path.home())
        chosen = filedialog.askdirectory(initialdir=initial, title="保存先フォルダを選択")
        if chosen:
            self.var_folder.set(chosen)
            self.settings.last_output_dir = chosen
            self._save_settings()

    def open_folder(self) -> None:
        """保存先（再生リストのフォルダがあればそちら）をエクスプローラーで開く."""
        target = Path(self.var_folder.get().strip() or ".").expanduser()
        if self.plan is not None and self.plan.destination.is_dir():
            target = self.plan.destination
        if not target.is_dir():
            messagebox.showinfo("フォルダがありません", f"まだ作成されていません:\n{target}")
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(target)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except OSError as exc:
            messagebox.showerror("開けませんでした", str(exc))

    def remember_url(self) -> None:
        url = self.var_url.get().strip()
        if not url:
            return
        self.settings.remember_playlist(url)
        self.url_box.configure(values=self.settings.playlists)
        self._save_settings()
        self.log(f"再生リストを登録しました: {url}")

    def reset_archive(self) -> None:
        """保存先フォルダの履歴を消す（次回は全件が新規扱いになる）."""
        target = self.plan.destination if self.plan else None
        if target is None:
            messagebox.showinfo(
                "先に確認してください",
                "「確認」を押して再生リストを読み込むと、対象フォルダの履歴を消せます。",
            )
            return
        if not messagebox.askyesno(
            "履歴をリセット",
            f"{target}\n\nの処理済み履歴を消します。次回は全動画が新規扱いになります。\nよろしいですか？",
        ):
            return
        Archive(target).reset()
        self.log(f"履歴をリセットしました: {target}")
        self.start_check()

    def _on_mode_change(self) -> None:
        mode = self.var_mode.get()
        self.settings.mode = mode
        state = "normal" if mode == "select" else "disabled"
        for child in self.select_bar.winfo_children():
            child.configure(state=state)
        self._save_settings()

    def _on_option_change(self) -> None:
        self.settings.embed_thumbnail = self.var_thumb.get()
        self.settings.notify_on_complete = self.var_notify.get()
        self.settings.quality = self.var_quality.get()
        self._save_settings()

    def _save_settings(self) -> None:
        self.settings.last_url = self.var_url.get().strip()
        folder = self.var_folder.get().strip()
        if folder:
            self.settings.last_output_dir = folder
        save_settings(self.settings)

    # ------------------------------------------------------------ 一覧表示

    def _track_key(self, track: Track) -> str:
        return track.video_id or track.url

    def _fill_tree(self, plan: Plan) -> None:
        self.tree.delete(*self.tree.get_children())
        done_ids = {self._track_key(t) for t in plan.done_tracks}
        self.checked = {self._track_key(t) for t in plan.new_tracks}
        for track in plan.playlist.tracks:
            key = self._track_key(track)
            state = "処理済み" if key in done_ids else "新規"
            self.tree.insert(
                "",
                "end",
                iid=key,
                values=(
                    CHECKED if key in self.checked else UNCHECKED,
                    track.index,
                    track.title,
                    state,
                ),
            )

    def _set_row(self, key: str, state: str) -> None:
        if self.tree.exists(key):
            values = list(self.tree.item(key, "values"))
            values[3] = state
            self.tree.item(key, values=values)
            self.tree.see(key)

    def _toggle(self, key: str) -> None:
        if not self.tree.exists(key):
            return
        values = list(self.tree.item(key, "values"))
        if key in self.checked:
            self.checked.discard(key)
            values[0] = UNCHECKED
        else:
            self.checked.add(key)
            values[0] = CHECKED
        self.tree.item(key, values=values)

    def _on_tree_click(self, event: tk.Event) -> None:
        if self.var_mode.get() != "select":
            return
        if self.tree.identify("region", event.x, event.y) != "cell":
            return
        if self.tree.identify_column(event.x) != "#1":
            return
        key = self.tree.identify_row(event.y)
        if key:
            self._toggle(key)

    def _on_tree_space(self, _event: tk.Event) -> None:
        if self.var_mode.get() != "select":
            return
        for key in self.tree.selection():
            self._toggle(key)

    def _check_all(self, value: bool) -> None:
        for key in self.tree.get_children():
            if (key in self.checked) != value:
                self._toggle(key)

    def _check_new_only(self) -> None:
        if not self.plan:
            return
        new_keys = {self._track_key(t) for t in self.plan.new_tracks}
        for key in self.tree.get_children():
            want = key in new_keys
            if (key in self.checked) != want:
                self._toggle(key)

    # ------------------------------------------------------------ 実行

    def _busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        self.btn_check.configure(state=state)
        self.btn_start.configure(state=state)
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def _start_worker(self, target: Callable[[], None]) -> bool:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("実行中です", "前の処理が終わるまでお待ちください。")
            return False
        self.stop_event.clear()
        self._busy(True)
        self.worker = threading.Thread(target=self._guard(target), daemon=True)
        self.worker.start()
        return True

    def _guard(self, target: Callable[[], None]) -> Callable[[], None]:
        """ワーカーで例外が出ても GUI を落とさない."""

        def wrapped() -> None:
            try:
                target()
            except PlaylistError as exc:
                self.queue.put(Message("error", str(exc)))
            except Exception as exc:  # 想定外でも画面に出す
                self.queue.put(
                    Message("error", f"想定外のエラー: {exc}\n{traceback.format_exc(limit=3)}")
                )
            finally:
                self.queue.put(Message("done", None))

        return wrapped

    def _inputs(self) -> tuple[str, Path] | None:
        url = self.var_url.get().strip()
        folder = self.var_folder.get().strip()
        if not url:
            messagebox.showwarning("URL が空です", "YouTube 再生リストの URL を入力してください。")
            return None
        if not folder:
            messagebox.showwarning("保存先が空です", "保存先フォルダを選んでください。")
            return None
        self._save_settings()
        return url, Path(folder).expanduser()

    def start_check(self) -> None:
        """再生リストを読み込み、新規／処理済みを表示するだけ."""
        inputs = self._inputs()
        if not inputs:
            return
        url, folder = inputs
        self._reset_progress()
        self.var_status.set("確認中...")
        self.log(f"再生リストを確認しています: {url}")

        def work() -> None:
            plan = make_plan(url, folder, settings=self.settings)
            self.queue.put(Message("plan", plan))

        self._start_worker(work)

    def start_download(self) -> None:
        """モードに応じてダウンロードする."""
        inputs = self._inputs()
        if not inputs:
            return
        url, folder = inputs
        mode = self.var_mode.get()
        selected = sorted(self.checked)
        reuse = self.plan if (self.plan and self.plan.playlist.url and mode == "select") else None

        if mode == "select" and not selected:
            messagebox.showinfo(
                "選択がありません",
                "「確認」を押して一覧を出し、処理する動画にチェックを入れてください。",
            )
            return

        self._reset_progress()
        self.var_status.set("準備中...")

        def work() -> None:
            plan = reuse or make_plan(url, folder, settings=self.settings)
            self.queue.put(Message("plan", plan))
            targets = plan.tracks_for_mode(mode, selected)
            if mode == "check":
                self.queue.put(Message("log", "確認のみ: ダウンロードは行いません。"))
                return
            if not targets:
                self.queue.put(Message("log", "新しい動画はありません。すべて処理済みです。"))
                return
            self.queue.put(Message("counts", len(targets)))
            self.queue.put(
                Message("log", f"{len(targets)} 件を処理します（モード: {MODES[mode]}）")
            )
            summary = run_plan(
                plan,
                mode=mode,
                settings=self.settings,
                selected_ids=selected,
                on_result=lambda r: self.queue.put(Message("result", r)),
                on_progress=lambda p: self.queue.put(Message("progress", p)),
                on_log=lambda m: self.queue.put(Message("log", m)),
                should_stop=self.stop_event.is_set,
            )
            self.queue.put(Message("summary", summary))

        self._start_worker(work)

    def start_all_playlists(self) -> None:
        """登録済みの再生リストをまとめて更新する（新規のみ）."""
        urls = list(self.settings.playlists)
        current = self.var_url.get().strip()
        if current and current not in urls:
            urls.insert(0, current)
        if not urls:
            messagebox.showinfo(
                "登録がありません", "URL を入力して「登録」を押すと、一括更新できます。"
            )
            return
        folder = Path(self.var_folder.get().strip() or str(self.settings.output_root()))
        if not messagebox.askyesno(
            "一括更新", f"{len(urls)} 件の再生リストを新規のみで更新します。よろしいですか？"
        ):
            return

        self._reset_progress()
        self.var_status.set("一括更新中...")

        def work() -> None:
            for number, url in enumerate(urls, start=1):
                if self.stop_event.is_set():
                    self.queue.put(Message("log", "中止しました。"))
                    break
                self.queue.put(Message("log", f"[{number}/{len(urls)}] {url}"))
                try:
                    plan = make_plan(url, folder, settings=self.settings)
                except PlaylistError as exc:
                    self.queue.put(Message("log", f"  読み込み失敗: {exc}"))
                    continue
                self.queue.put(Message("plan", plan))
                targets = plan.new_tracks
                if not targets:
                    self.queue.put(Message("log", "  新規なし"))
                    continue
                self.queue.put(Message("counts", len(targets)))
                run_plan(
                    plan,
                    mode="new",
                    settings=self.settings,
                    on_result=lambda r: self.queue.put(Message("result", r)),
                    on_progress=lambda p: self.queue.put(Message("progress", p)),
                    on_log=lambda m: self.queue.put(Message("log", m)),
                    should_stop=self.stop_event.is_set,
                )
            self.queue.put(Message("log", "一括更新が終わりました。"))

        self._start_worker(work)

    def stop(self) -> None:
        self.stop_event.set()
        self.var_status.set("中止しています...")
        self.log("中止を要求しました。現在の曲が終わり次第止まります。")

    # ------------------------------------------------------------ 進捗反映

    def _reset_progress(self) -> None:
        self.counts = {"ok": 0, "skip": 0, "fail": 0}
        self.total_targets = 0
        self.done_targets = 0
        self.var_overall.set(0.0)
        self.var_track.set(0.0)
        self.var_current.set("—")
        self._update_counts()

    def _update_counts(self) -> None:
        self.var_counts.set(
            f"成功 {self.counts['ok']} / スキップ {self.counts['skip']} / 失敗 {self.counts['fail']}"
        )

    def _pump_queue(self) -> None:
        """ワーカーからの通知を GUI に反映する（メインスレッド）."""
        try:
            while True:
                message = self.queue.get_nowait()
                self._handle(message)
        except queue.Empty:
            pass
        finally:
            if not self._closing:
                self._pump_id = self.after(100, self._pump_queue)

    def _handle(self, message: Message) -> None:
        kind = message.kind
        if kind == "log":
            self.log(str(message.payload))
        elif kind == "plan":
            self._handle_plan(message.payload)  # type: ignore[arg-type]
        elif kind == "counts":
            self.total_targets = int(message.payload or 0)
            self.done_targets = 0
        elif kind == "progress":
            self._handle_progress(message.payload)  # type: ignore[arg-type]
        elif kind == "result":
            self._handle_result(message.payload)  # type: ignore[arg-type]
        elif kind == "summary":
            self._handle_summary(message.payload)  # type: ignore[arg-type]
        elif kind == "error":
            self.var_status.set("エラー")
            self.log("エラー: " + str(message.payload))
            messagebox.showerror("エラー", str(message.payload))
        elif kind == "done":
            self._busy(False)
            if self.var_status.get() in ("確認中...", "準備中...", "中止しています...", "一括更新中..."):
                self.var_status.set("待機中")

    def _handle_plan(self, plan: Plan) -> None:
        self.plan = plan
        self._fill_tree(plan)
        self.log(plan.summary_text())
        if plan.new_tracks:
            self.log("新規の動画:")
            for track in plan.new_tracks:
                self.log(f"  {track.index:02d}. {track.title}")
        else:
            self.log("新規の動画はありません。")
        self.var_status.set("確認完了")

    def _handle_progress(self, progress: Progress) -> None:
        stage = {
            "downloading": "ダウンロード中",
            "converting": "mp3 に変換中",
            "tagging": "タグ書き込み中",
            "finished": "完了",
        }.get(progress.stage, progress.stage)
        self.var_status.set(stage)
        self.var_track.set(progress.percent)
        self.var_current.set(f"{progress.track.index:02d}. {progress.track.title}")
        if self.total_targets:
            base = self.done_targets / self.total_targets * 100
            step = progress.percent / self.total_targets
            self.var_overall.set(min(base + step, 100.0))

    def _handle_result(self, result: Result) -> None:
        key = self._track_key(result.track)
        if result.status == DOWNLOADED:
            self.counts["ok"] += 1
            self._set_row(key, "保存しました")
            note = f"（{result.detail}）" if result.detail else ""
            self.log(f"✓ {result.track.title}{note}")
        elif result.status == FAILED:
            self.counts["fail"] += 1
            self._set_row(key, "失敗")
            self.log(f"✗ {result.track.title} : {result.detail}")
        elif result.status == CANCELLED:
            self._set_row(key, "中止")
        else:
            self.counts["skip"] += 1
            self._set_row(key, "スキップ")
            self.log(f"- {result.track.title} : {result.detail}")
        self.done_targets += 1
        self.var_track.set(0.0)
        if self.total_targets:
            self.var_overall.set(min(self.done_targets / self.total_targets * 100, 100.0))
        self._update_counts()

    def _handle_summary(self, summary: Summary) -> None:
        self.var_status.set("完了")
        self.var_overall.set(100.0)
        text = (
            f"完了: 成功 {summary.downloaded} / スキップ {summary.skipped} / "
            f"失敗 {summary.failed}"
        )
        self.log(text)
        if summary.failures:
            self.log("失敗した動画（次回もう一度試せます）:")
            for result in summary.failures:
                self.log(f"  ✗ {result.track.title} : {result.detail}")
        if self.var_notify.get():
            try:
                self.master.bell()
            except tk.TclError:  # pragma: no cover - 環境依存
                pass
            messagebox.showinfo("処理が終わりました", text)

    # ------------------------------------------------------------ 終了処理

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("実行中です", "処理を中止して終了しますか？"):
                return
            self.stop_event.set()
        self._closing = True
        if self._pump_id is not None:
            try:
                self.after_cancel(self._pump_id)
            except tk.TclError:  # pragma: no cover - 既に破棄済み
                pass
            self._pump_id = None
        self._save_settings()
        self.master.destroy()


def run(settings: Settings | None = None) -> int:
    """GUI を起動する."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # pragma: no cover - 画面が無い環境
        print(f"GUI を起動できませんでした: {exc}", file=sys.stderr)
        return 1
    App(root, settings=settings)
    root.mainloop()
    return 0
