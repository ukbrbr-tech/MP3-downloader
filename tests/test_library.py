import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mp3dl.library import ARCHIVE_FILENAME, Library, normalize_title, video_id_from_filename


def test_normalize_title_ignores_case_width_and_symbols():
    assert normalize_title("Ｈｅｌｌｏ World!") == normalize_title("hello - world")
    assert normalize_title("夜に駆ける / YOASOBI") == normalize_title("夜に駆ける - yoasobi")
    assert normalize_title("") == ""


def test_video_id_from_filename():
    assert video_id_from_filename("My Song [dQw4w9WgXcQ]") == "dQw4w9WgXcQ"
    assert video_id_from_filename("My Song (dQw4w9WgXcQ)") == "dQw4w9WgXcQ"
    assert video_id_from_filename("My Song") is None


def test_scan_finds_existing_mp3_by_title_and_id(tmp_path):
    (tmp_path / "My Song.mp3").write_bytes(b"")
    (tmp_path / "Other [dQw4w9WgXcQ].mp3").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")

    lib = Library.scan(tmp_path)

    assert lib.reason_to_skip(None, "my song!") is not None
    assert lib.reason_to_skip("dQw4w9WgXcQ", "まったく別の題名") is not None
    assert lib.reason_to_skip("abcdefghijk", "新しい曲") is None
    assert lib.reason_to_skip(None, "notes") is None


def test_scan_is_recursive(tmp_path):
    sub = tmp_path / "album"
    sub.mkdir()
    (sub / "Deep Track.mp3").write_bytes(b"")

    assert Library.scan(tmp_path).reason_to_skip(None, "Deep Track") is not None


def test_scan_handles_missing_directory(tmp_path):
    lib = Library.scan(tmp_path / "not-created-yet")
    assert lib.reason_to_skip("abcdefghijk", "曲") is None


def test_record_appends_to_archive_and_blocks_next_run(tmp_path):
    lib = Library.scan(tmp_path)
    lib.record("abcdefghijk", "New Song")

    assert lib.reason_to_skip("abcdefghijk", "別題名") is not None
    assert (tmp_path / ARCHIVE_FILENAME).read_text(encoding="utf-8") == "abcdefghijk\n"

    reloaded = Library.scan(tmp_path)
    assert reloaded.reason_to_skip("abcdefghijk", "別題名") is not None


def test_archive_can_be_disabled(tmp_path):
    lib = Library.scan(tmp_path, use_archive=False)
    lib.record("abcdefghijk", "New Song")

    assert not (tmp_path / ARCHIVE_FILENAME).exists()
    assert Library.scan(tmp_path, use_archive=False).reason_to_skip("abcdefghijk", "別題名") is None


def test_archive_ignores_comments_and_blanks(tmp_path):
    (tmp_path / ARCHIVE_FILENAME).write_text("# comment\n\nabcdefghijk\n", encoding="utf-8")
    lib = Library.scan(tmp_path)
    assert lib.reason_to_skip("abcdefghijk", None) is not None
    assert lib.reason_to_skip("zzzzzzzzzzz", None) is None
