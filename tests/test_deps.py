"""依存関係チェックのテスト."""

from __future__ import annotations

from mp3dl import deps
from mp3dl.deps import Dependency, check_dependencies, find_ffmpeg, format_report, missing_required


def test_check_reports_every_component():
    names = {dep.name for dep in check_dependencies()}
    assert {"Python", "yt-dlp", "FFmpeg"} <= names


def test_missing_required_lists_only_required():
    found = missing_required(
        [
            Dependency("a", ok=False, required=True),
            Dependency("b", ok=False, required=False),
            Dependency("c", ok=True, required=True),
        ]
    )
    assert [dep.name for dep in found] == ["a"]


def test_report_includes_hint_for_missing():
    text = format_report([Dependency("FFmpeg", ok=False, required=True, hint="入れてください")])
    assert "入れてください" in text


def test_find_ffmpeg_accepts_explicit_file(tmp_path):
    fake = tmp_path / "ffmpeg"
    fake.write_text("", encoding="utf-8")
    assert find_ffmpeg(str(fake)) == str(fake)


def test_find_ffmpeg_accepts_explicit_folder(tmp_path):
    (tmp_path / "ffmpeg").write_text("", encoding="utf-8")
    assert find_ffmpeg(str(tmp_path)) == str(tmp_path / "ffmpeg")


def test_find_ffmpeg_returns_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(deps.shutil, "which", lambda name: None)
    monkeypatch.setattr(deps, "__file__", str(tmp_path / "pkg" / "deps.py"))
    monkeypatch.setitem(__import__("sys").modules, "imageio_ffmpeg", None)
    assert find_ffmpeg(str(tmp_path / "missing")) is None
