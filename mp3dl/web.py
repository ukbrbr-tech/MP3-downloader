"""ブラウザから使うためのローカル Web UI.

`python -m mp3dl --web` で 127.0.0.1 にだけ待ち受けるサーバーを立て、
ブラウザで開いた画面から URL と保存先を指定してダウンロードする。

このサーバーは自分の PC のフォルダにファイルを書き込むため、外部に公開しない。
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import threading
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .deps import find_ffmpeg
from .config import DEFAULT_QUALITY, Settings, default_music_root
from .job import Plan, make_plan, run_plan
from .pipeline import CANCELLED, DOWNLOADED, FAILED, DownloadError, Progress, Result
from .playlist import PlaylistError, Track

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY_BYTES = 64 * 1024

#: DNS リバインディング対策として、この Host でのアクセスだけ受け付ける
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def default_folder() -> Path:
    """保存先の初期値（<ホーム>/Music）."""
    return default_music_root()


@dataclass
class Item:
    """画面に表示する 1 曲分の状態."""

    title: str
    video_id: str | None
    url: str
    status: str = "pending"  # pending | downloading | done | skipped | failed | cancelled
    detail: str = ""
    percent: float = 0.0

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "videoId": self.video_id,
            "url": self.url,
            "status": self.status,
            "detail": self.detail,
            "percent": round(self.percent, 1),
        }


@dataclass
class Job:
    """1 回分のダウンロード処理."""

    url: str
    folder: Path
    quality: str = DEFAULT_QUALITY
    jobs: int = 1
    dry_run: bool = False
    skip_existing: bool = True
    cookies_from_browser: str | None = None

    status: str = "analyzing"  # analyzing | running | done | error | cancelled
    message: str = "再生リストを読み込んでいます..."
    error: str = ""
    items: list[Item] = field(default_factory=list)
    destination: Path | None = None
    _by_key: dict[str, Item] = field(default_factory=dict, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def mode(self) -> str:
        """画面のチェックボックスから、処理モードを決める."""
        if self.dry_run:
            return "check"
        return "new" if self.skip_existing else "all"

    def settings(self) -> Settings:
        base = Settings.load()
        base.quality = self.quality
        base.cookies_from_browser = self.cookies_from_browser or ""
        return base

    # ------------------------------------------------------------------ 実行

    def run(self) -> None:
        settings = self.settings()
        try:
            plan = make_plan(self.url, self.folder, settings=settings)
        except (PlaylistError, DownloadError) as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # 想定外でも画面にはきちんと出す
            self._fail(f"再生リストを読み込めませんでした: {exc}")
            return

        self._prepare(plan)

        if self.dry_run:
            self._finish("確認のみ（ダウンロードはしていません）")
            return

        targets = plan.tracks_for_mode(self.mode)
        if not targets:
            self._finish("すべて処理済みです。")
            return

        with self._lock:
            self.status = "running"
            self.message = f"{len(targets)} 曲をダウンロードしています..."

        try:
            run_plan(
                plan,
                mode=self.mode,
                settings=settings,
                on_result=self._on_result,
                on_progress=self._on_progress,
                should_stop=self._stop.is_set,
            )
        except DownloadError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:
            self._fail(f"ダウンロード中にエラーが発生しました: {exc}")
            return

        if self._stop.is_set():
            self._finish("中止しました。", status="cancelled")
        else:
            self._finish("完了しました。")

    def cancel(self) -> None:
        self._stop.set()
        with self._lock:
            if self.status in ("analyzing", "running"):
                self.message = "中止しています..."

    # ------------------------------------------------------------ 内部処理

    @staticmethod
    def _key(track: Track) -> str:
        return track.video_id or track.url

    def _prepare(self, plan: Plan) -> None:
        self.destination = plan.destination
        done_keys = {self._key(t) for t in plan.done_tracks}
        with self._lock:
            for track in plan.playlist.tracks:
                item = Item(title=track.title, video_id=track.video_id, url=track.url)
                if self.skip_existing and self._key(track) in done_keys:
                    item.status = "skipped"
                    item.detail = "処理済み (Video ID 一致)"
                self.items.append(item)
                self._by_key[self._key(track)] = item

    def _on_progress(self, progress: Progress) -> None:
        item = self._by_key.get(self._key(progress.track))
        if not item:
            return
        stage_text = {
            "converting": "mp3 に変換しています...",
            "tagging": "タグを書き込んでいます...",
        }.get(progress.stage, "")
        with self._lock:
            item.percent = progress.percent
            if progress.stage != "finished":
                item.status = "downloading"
                item.detail = stage_text

    def _on_result(self, result: Result) -> None:
        item = self._by_key.get(self._key(result.track))
        if not item:
            return
        mapping = {DOWNLOADED: "done", "skipped": "skipped", FAILED: "failed", CANCELLED: "cancelled"}
        with self._lock:
            item.status = mapping.get(result.status, result.status)
            item.detail = result.detail
            item.percent = 100.0 if item.status == "done" else item.percent

    def _fail(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = message
            self.message = "エラーが発生しました。"

    def _finish(self, message: str, status: str = "done") -> None:
        with self._lock:
            self.status = status
            self.message = message

    # -------------------------------------------------------------- 状態出力

    def to_json(self) -> dict:
        with self._lock:
            items = [item.to_json() for item in self.items]
            counts = {
                "total": len(items),
                "done": sum(1 for i in items if i["status"] == "done"),
                "skipped": sum(1 for i in items if i["status"] == "skipped"),
                "failed": sum(1 for i in items if i["status"] == "failed"),
            }
            return {
                "status": self.status,
                "message": self.message,
                "error": self.error,
                "folder": str(self.destination or self.folder),
                "dryRun": self.dry_run,
                "finished": self.status in ("done", "error", "cancelled"),
                "counts": counts,
                "items": items,
            }


class State:
    """同時に 1 件だけ実行するジョブの入れ物."""

    def __init__(self) -> None:
        self.job: Job | None = None
        self.lock = threading.Lock()

    def start(self, job: Job) -> None:
        with self.lock:
            if self.job and self.job.status in ("analyzing", "running"):
                raise RuntimeError("すでにダウンロード中です。")
            self.job = job
        threading.Thread(target=job.run, daemon=True).start()


def _resolve_folder(raw: str) -> Path:
    folder = Path(raw).expanduser() if raw.strip() else default_folder()
    return folder.resolve()


class Handler(BaseHTTPRequestHandler):
    server_version = "mp3dl"
    state: State  # ThreadingHTTPServer 側で差し込む

    # ------------------------------------------------------------- 共通処理

    def log_message(self, fmt: str, *args) -> None:  # 既定のアクセスログを抑制
        pass

    def _host_allowed(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    def _send_json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        # JSON 以外の Content-Type を弾くことで、他サイトからの POST を防ぐ
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if ctype != "application/json":
            raise ValueError("Content-Type は application/json にしてください。")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY_BYTES:
            raise ValueError("リクエストが大きすぎます。")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("不正なリクエストです。")
        return data

    # ----------------------------------------------------------------- GET

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._send_json({"error": "このアドレスからは利用できません。"}, 403)
            return

        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self._serve_static("index.html")
        elif path == "/api/config":
            self._send_json(
                {
                    "defaultFolder": str(default_folder()),
                    "hasFfmpeg": find_ffmpeg() is not None,
                    "defaultQuality": DEFAULT_QUALITY,
                }
            )
        elif path == "/api/state":
            job = self.state.job
            self._send_json(job.to_json() if job else {"status": "idle", "items": []})
        else:
            self._send_json({"error": "not found"}, 404)

    def _serve_static(self, name: str) -> None:
        target = (STATIC_DIR / name).resolve()
        if not target.is_file() or STATIC_DIR.resolve() not in target.parents:
            self._send_json({"error": "not found"}, 404)
            return
        body = target.read_bytes()
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------------------------------------------------------------- POST

    def do_POST(self) -> None:
        if not self._host_allowed():
            self._send_json({"error": "このアドレスからは利用できません。"}, 403)
            return

        path = self.path.split("?")[0]
        try:
            data = self._read_json()
        except ValueError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except json.JSONDecodeError:
            self._send_json({"error": "JSON を解析できませんでした。"}, 400)
            return

        if path == "/api/start":
            self._start(data)
        elif path == "/api/cancel":
            if self.state.job:
                self.state.job.cancel()
            self._send_json({"ok": True})
        elif path == "/api/open-folder":
            self._open_folder(data.get("folder", ""))
        else:
            self._send_json({"error": "not found"}, 404)

    def _start(self, data: dict) -> None:
        url = str(data.get("url", "")).strip()
        if not url:
            self._send_json({"error": "URL を入力してください。"}, 400)
            return
        if not url.startswith(("http://", "https://")):
            self._send_json({"error": "http:// または https:// で始まる URL を入力してください。"}, 400)
            return

        try:
            folder = _resolve_folder(str(data.get("folder", "")))
            jobs = max(1, min(8, int(data.get("jobs", 1) or 1)))
        except (OSError, ValueError) as exc:
            self._send_json({"error": f"保存先を解釈できませんでした: {exc}"}, 400)
            return

        job = Job(
            url=url,
            folder=folder,
            quality=str(data.get("quality") or DEFAULT_QUALITY),
            jobs=jobs,
            dry_run=bool(data.get("dryRun")),
            skip_existing=bool(data.get("skipExisting", True)),
        )
        try:
            self.state.start(job)
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 409)
            return
        self._send_json({"ok": True, "folder": str(folder)})

    def _open_folder(self, raw: str) -> None:
        try:
            folder = _resolve_folder(raw)
        except OSError as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        if not folder.is_dir():
            self._send_json({"error": "フォルダがまだ存在しません。"}, 400)
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            elif sys.platform.startswith("win"):
                subprocess.Popen(["explorer", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as exc:
            self._send_json({"error": f"フォルダを開けませんでした: {exc}"}, 400)
            return
        self._send_json({"ok": True})


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    """ローカル Web UI を起動する."""
    handler = type("BoundHandler", (Handler,), {"state": State()})
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"エラー: ポート {port} を使えません（{exc}）。--port で別の番号を指定してください。")
        return 1

    url = f"http://{host}:{port}/"
    print(f"mp3dl の画面を開きました: {url}")
    print("終了するには Ctrl+C を押してください。")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します。")
    finally:
        httpd.server_close()
    return 0
