"""アプリ全体の既定値と、利用者ごとの設定ファイル.

ハードコードを避けるため、変更しそうな値はすべてこのモジュールに集める。
設定は JSON 1 ファイル（Windows では %APPDATA%\\mp3dl\\settings.json）に保存する。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------- 既定値

#: mp3 のビットレート（kbps）。仕様上の基本は 320。
DEFAULT_QUALITY = "320"

#: アルバムアートの最大の一辺（px）。これを超えるサムネイルは縮小する。
DEFAULT_COVER_MAX_PX = 800

#: アルバムアートの JPEG 品質
DEFAULT_COVER_JPEG_QUALITY = 88

#: 一時的な通信エラーに対する再試行回数（1 曲あたり）
DEFAULT_RETRIES = 3

#: 再試行の待ち時間（秒）。指数的に伸ばすための基準値。
DEFAULT_RETRY_BACKOFF = 3.0

#: 同時ダウンロード数の既定値。安定性を優先して 1。
DEFAULT_JOBS = 1

#: ファイル名の最大長（拡張子を除く。Windows の MAX_PATH に余裕を持たせる）
MAX_FILENAME_STEM = 120

#: 処理済み動画 ID を記録するファイル（yt-dlp の download-archive と同じ形式）
ARCHIVE_FILENAME = "download_archive.txt"

#: 処理済み動画の詳細（タイトルや日時）を残す補助ファイル
PROCESSED_FILENAME = "processed.json"

#: 保存先フォルダに書き出すログ
LOG_FILENAME = "app.log"

#: 旧バージョンが使っていた履歴ファイル（見つかったら取り込む）
LEGACY_ARCHIVE_FILENAMES = (".downloaded.txt",)

#: 設定ファイル名
SETTINGS_FILENAME = "settings.json"

APP_NAME = "mp3dl"


# ----------------------------------------------------------------- パス関連


def config_dir() -> Path:
    """設定ファイルを置くフォルダ."""
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = Path(base) if base else Path.home() / ".config"
    return root / APP_NAME


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILENAME


def default_music_root() -> Path:
    """保存先の初期値（<ホーム>/Music）."""
    home = Path.home()
    for name in ("Music", "ミュージック"):
        candidate = home / name
        if candidate.is_dir():
            return candidate
    return home / "Music"


# ------------------------------------------------------------------- 設定本体


@dataclass
class Settings:
    """GUI の入力内容を次回起動時に復元するための設定."""

    last_url: str = ""
    last_output_dir: str = ""
    mode: str = "new"  # new | all | check | select
    quality: str = DEFAULT_QUALITY
    embed_thumbnail: bool = True
    cover_max_px: int = DEFAULT_COVER_MAX_PX
    retries: int = DEFAULT_RETRIES
    jobs: int = DEFAULT_JOBS
    dark_mode: bool = False
    notify_on_complete: bool = True
    make_playlist_subfolder: bool = True
    cookies_from_browser: str = ""
    ffmpeg_location: str = ""
    #: 登録した再生リスト URL（複数の一括更新に使う）
    playlists: list[str] = field(default_factory=list)

    # -------------------------------------------------------------- 入出力

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        """設定を読み込む。壊れていても既定値で起動できるようにする."""
        path = Path(path) if path else settings_path()
        settings = cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings
        if not isinstance(raw, dict):
            return settings
        known = {f for f in asdict(settings)}
        for key, value in raw.items():
            if key not in known:
                continue
            current = getattr(settings, key)
            if isinstance(current, bool) and isinstance(value, bool):
                setattr(settings, key, value)
            elif isinstance(current, int) and not isinstance(current, bool):
                try:
                    setattr(settings, key, int(value))
                except (TypeError, ValueError):
                    pass
            elif isinstance(current, str) and isinstance(value, str):
                setattr(settings, key, value)
            elif isinstance(current, list) and isinstance(value, list):
                setattr(settings, key, [str(v) for v in value if isinstance(v, str)])
        return settings

    def save(self, path: Path | None = None) -> None:
        """設定を保存する。失敗してもアプリは止めない."""
        path = Path(path) if path else settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(path)
        except OSError:
            pass

    # ------------------------------------------------------------ 補助処理

    def remember_playlist(self, url: str, limit: int = 20) -> None:
        """登録済み再生リストの先頭に URL を入れる（重複は除く）."""
        url = url.strip()
        if not url:
            return
        self.playlists = [url] + [p for p in self.playlists if p != url]
        del self.playlists[limit:]

    def output_root(self) -> Path:
        """保存先のルートフォルダ."""
        if self.last_output_dir.strip():
            return Path(self.last_output_dir).expanduser()
        return default_music_root()


_save_lock = threading.Lock()


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """複数スレッドから呼ばれても壊れないように保存する."""
    with _save_lock:
        settings.save(path)
