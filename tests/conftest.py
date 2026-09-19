"""テスト用の共通部品（ネットワークを使わない偽 yt-dlp など）."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# どこから実行しても mp3dl を import できるようにする
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg が必要")


def make_tone(path: Path, seconds: float = 1.0) -> Path:
    """テスト用の短い mp3 を ffmpeg で作る."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG, "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-codec:a", "libmp3lame", "-b:a", "128k", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def make_jpeg(path: Path, size: int = 1400) -> Path:
    """テスト用の大きめ JPEG を作る（縮小処理の確認に使う）."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (size, size), (200, 30, 30)).save(path, "JPEG")
    return path


class FakeYDL:
    """yt-dlp の代わりに、その場で mp3 とサムネイルを作る偽オブジェクト.

    `videos` は video_id -> info の辞書。info に "fail" があるとその回数だけ失敗する。
    """

    def __init__(self, opts: dict, videos: dict, calls: list | None = None):
        self.opts = opts
        self.videos = videos
        self.calls = calls if calls is not None else []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url: str, download: bool = True):
        video_id = url.rsplit("=", 1)[-1]
        self.calls.append(video_id)
        info = dict(self.videos[video_id])

        remaining = info.pop("fail_times", 0)
        if remaining:
            self.videos[video_id]["fail_times"] = remaining - 1
            raise RuntimeError(info.get("fail_message", "Unable to download webpage"))

        if not download:
            return info

        template = self.opts["outtmpl"]
        base = Path(str(template).replace("%(id)s.%(ext)s", video_id))
        make_tone(base.with_suffix(".mp3"))
        if self.opts.get("writethumbnail") and info.get("thumbnail"):
            make_jpeg(base.with_suffix(".jpg"))

        for hook in self.opts.get("progress_hooks", []):
            hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
            hook({"status": "finished"})
        info["filepath"] = str(base.with_suffix(".mp3"))
        return info


def fake_factory(videos: dict, calls: list | None = None):
    """FakeYDL を yt-dlp の代わりに渡せる形にする."""

    def factory(opts: dict):
        return FakeYDL(opts, videos, calls)

    return factory


def video_info(video_id: str, title: str, **extra) -> dict:
    info = {
        "id": video_id,
        "title": title,
        "uploader": "Test Channel",
        "upload_date": "20240501",
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
    }
    info.update(extra)
    return info


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """設定ファイルを毎回テスト用の場所に向ける."""
    from mp3dl import config

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path / "config")
    return tmp_path / "config" / config.SETTINGS_FILENAME
