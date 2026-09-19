"""Windows で安全なファイル名づくりのテスト."""

from __future__ import annotations

import pytest

from mp3dl.naming import sanitize_component, track_filename, unique_path


@pytest.mark.parametrize("bad", list('\\/:*?"<>|'))
def test_forbidden_characters_are_replaced(bad):
    name = track_filename(1, f"a{bad}b")
    assert bad not in name
    assert name.endswith(".mp3")


def test_filename_format():
    assert track_filename(1, "Song A") == "01 - Song A.mp3"
    assert track_filename(12, "Song B") == "12 - Song B.mp3"
    assert track_filename(103, "Song C") == "103 - Song C.mp3"
    assert track_filename(None, "Song D") == "Song D.mp3"


def test_trailing_dots_and_spaces_removed():
    assert track_filename(1, "name...  ") == "01 - name.mp3"


def test_reserved_names():
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("nul.txt") == "_nul.txt"


def test_empty_title():
    assert track_filename(1, "   ") == "01 - untitled.mp3"


def test_long_title_is_truncated():
    name = track_filename(1, "あ" * 400)
    assert len(Path_stem(name)) <= 120


def Path_stem(name: str) -> str:
    return name[: -len(".mp3")]


def test_control_characters_removed():
    assert "\n" not in track_filename(1, "line1\nline2")


def test_unique_path_does_not_overwrite(tmp_path):
    first = tmp_path / "01 - Song.mp3"
    first.write_bytes(b"x")
    second = unique_path(first)
    assert second.name == "01 - Song (2).mp3"

    second.write_bytes(b"y")
    third = unique_path(first)
    assert third.name == "01 - Song (3).mp3"
    assert first.read_bytes() == b"x"


def test_unique_path_free_name(tmp_path):
    target = tmp_path / "free.mp3"
    assert unique_path(target) == target
