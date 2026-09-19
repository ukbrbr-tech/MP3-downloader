"""起動時の依存関係チェック（FFmpeg / yt-dlp / Python ライブラリ）."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: 必須の Python ライブラリ（モジュール名 -> pip でのパッケージ名）
REQUIRED_MODULES = {"yt_dlp": "yt-dlp"}

#: 無くても動くが、あると機能が増えるライブラリ
OPTIONAL_MODULES = {
    "mutagen": ("mutagen", "ID3 タグとアルバムアートの埋め込み"),
    "PIL": ("pillow", "アルバムアートの縮小・JPEG 変換"),
}

#: Windows で FFmpeg が入りがちな場所
_WINDOWS_HINTS = (
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin",
)


@dataclass
class Dependency:
    """1 つの依存関係の状態."""

    name: str
    ok: bool
    required: bool
    detail: str = ""
    hint: str = ""

    def describe(self) -> str:
        mark = "OK" if self.ok else ("不足" if self.required else "任意/未導入")
        line = f"[{mark}] {self.name}"
        if self.detail:
            line += f" : {self.detail}"
        if not self.ok and self.hint:
            line += f"\n      → {self.hint}"
        return line


def _exe_names(stem: str) -> tuple[str, ...]:
    return (f"{stem}.exe", stem) if os.name == "nt" else (stem, f"{stem}.exe")


def find_ffmpeg(explicit: str | None = None) -> str | None:
    """ffmpeg の実行ファイルのパスを探す（見つからなければ None）.

    探す順番:
      1. 明示指定（設定・コマンドライン）
      2. アプリと同じフォルダの `ffmpeg/bin` や `bin`（持ち運び用）
      3. PATH
      4. Windows のよくある場所
      5. imageio-ffmpeg（入っていれば）
    """
    candidates: list[Path] = []

    if explicit:
        path = Path(explicit).expanduser()
        if path.is_dir():
            candidates.extend(path / name for name in _exe_names("ffmpeg"))
        else:
            candidates.append(path)

    here = Path(__file__).resolve().parent.parent
    for folder in (here / "ffmpeg" / "bin", here / "bin", here):
        candidates.extend(folder / name for name in _exe_names("ffmpeg"))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    found = shutil.which("ffmpeg")
    if found:
        return found

    if os.name == "nt":
        for folder in _WINDOWS_HINTS:
            for name in _exe_names("ffmpeg"):
                candidate = Path(folder) / name
                if candidate.is_file():
                    return str(candidate)

    try:  # pragma: no cover - 環境依存
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_version(path: str) -> str:
    """ffmpeg のバージョン行を 1 行返す（取得できなければ空文字）."""
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first = (proc.stdout or proc.stderr or "").splitlines()
    return first[0].strip() if first else ""


def ffmpeg_install_hint() -> str:
    if os.name == "nt":
        return (
            "PowerShell で `winget install Gyan.FFmpeg` を実行するか、"
            "https://www.gyan.dev/ffmpeg/builds/ から入手して "
            "ffmpeg.exe を PATH かこのアプリのフォルダの ffmpeg\\bin に置いてください。"
        )
    if sys.platform == "darwin":
        return "`brew install ffmpeg` を実行してください。"
    return "`sudo apt install ffmpeg`（Debian/Ubuntu 系）などで導入してください。"


def check_dependencies(ffmpeg_location: str | None = None) -> list[Dependency]:
    """必要なものが揃っているか調べて一覧で返す."""
    results: list[Dependency] = []

    results.append(
        Dependency(
            name="Python",
            ok=sys.version_info >= (3, 9),
            required=True,
            detail=sys.version.split()[0],
            hint="Python 3.9 以上が必要です。",
        )
    )

    for module, package in REQUIRED_MODULES.items():
        results.append(_check_module(module, package, required=True))

    for module, (package, purpose) in OPTIONAL_MODULES.items():
        dep = _check_module(module, package, required=False)
        if not dep.ok:
            dep.detail = f"未インストール（{purpose}に必要）"
        results.append(dep)

    results.append(_check_tkinter())

    ffmpeg = find_ffmpeg(ffmpeg_location)
    results.append(
        Dependency(
            name="FFmpeg",
            ok=bool(ffmpeg),
            required=True,
            detail=(ffmpeg_version(ffmpeg) or ffmpeg) if ffmpeg else "見つかりません",
            hint=ffmpeg_install_hint(),
        )
    )

    return results


def _check_module(module: str, package: str, *, required: bool) -> Dependency:
    try:
        mod = importlib.import_module(module)
    except Exception:
        return Dependency(
            name=package,
            ok=False,
            required=required,
            detail="未インストール",
            hint=f"`pip install {package}` を実行してください。",
        )
    return Dependency(name=package, ok=True, required=required, detail=_module_version(mod))


def _module_version(mod) -> str:
    """ライブラリごとにばらばらなバージョン表記を 1 つの文字列にまとめる."""
    version = getattr(mod, "__version__", None) or getattr(mod, "version_string", None)
    if version is None:
        raw = getattr(mod, "version", None)
        if isinstance(raw, str):
            version = raw
        elif isinstance(raw, tuple):
            version = ".".join(str(part) for part in raw)
        elif raw is not None:  # yt_dlp.version のようなモジュール
            version = getattr(raw, "__version__", None)
    return str(version) if version else "導入済み"


def _check_tkinter() -> Dependency:
    try:
        import tkinter  # noqa: F401  (入るかどうかだけ確かめる)
    except Exception:
        return Dependency(
            name="tkinter (GUI)",
            ok=False,
            required=False,
            detail="未インストール（GUI を使わない場合は不要）",
            hint=(
                "Windows の公式 Python には同梱されています。"
                "Linux では `sudo apt install python3-tk` を実行してください。"
            ),
        )
    return Dependency(name="tkinter (GUI)", ok=True, required=False, detail="利用可能")


def missing_required(deps: list[Dependency]) -> list[Dependency]:
    return [dep for dep in deps if dep.required and not dep.ok]


def format_report(deps: list[Dependency]) -> str:
    return "\n".join(dep.describe() for dep in deps)
