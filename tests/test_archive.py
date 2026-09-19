"""履歴（Video ID）まわりのテスト. これが新規判定の要."""

from __future__ import annotations

from mp3dl.archive import Archive
from mp3dl.playlist import Track
from mp3dl.pipeline import select_new, split_by_archive


def test_record_and_reload(tmp_path):
    archive = Archive(tmp_path)
    assert len(archive) == 0

    archive.record("dQw4w9WgXcQ", title="Song A", filename="01 - Song A.mp3")
    assert archive.has("dQw4w9WgXcQ")

    reloaded = Archive(tmp_path)
    assert reloaded.has("dQw4w9WgXcQ")
    assert not reloaded.has("otherVideoId")


def test_archive_file_is_ytdlp_compatible(tmp_path):
    Archive(tmp_path).record("abcdefghijk")
    text = (tmp_path / "download_archive.txt").read_text(encoding="utf-8")
    assert text.strip() == "youtube abcdefghijk"


def test_duplicate_record_written_once(tmp_path):
    archive = Archive(tmp_path)
    archive.record("abcdefghijk", title="A")
    archive.record("abcdefghijk", title="A (再取得)")
    lines = (tmp_path / "download_archive.txt").read_text(encoding="utf-8").split()
    assert lines.count("abcdefghijk") == 1


def test_legacy_archive_is_imported(tmp_path):
    (tmp_path / ".downloaded.txt").write_text("oldVideoId1\n", encoding="utf-8")
    assert Archive(tmp_path).has("oldVideoId1")


def test_title_change_does_not_break_judgement(tmp_path):
    """タイトルが変わっても Video ID が同じならスキップされる."""
    archive = Archive(tmp_path)
    archive.record("sameId12345", title="むかしのタイトル")

    renamed = Track(video_id="sameId12345", title="あたらしいタイトル", url="u", index=1)
    assert select_new([renamed], archive) == []


def test_same_title_different_id_is_new(tmp_path):
    """同名の別動画は新規として処理される."""
    archive = Archive(tmp_path)
    archive.record("firstId1234", title="同じ曲名")

    other = Track(video_id="secondId123", title="同じ曲名", url="u", index=1)
    assert select_new([other], archive) == [other]


def test_split_by_archive(tmp_path):
    archive = Archive(tmp_path)
    archive.record("known123456")
    tracks = [
        Track(video_id="known123456", title="A", url="a", index=1),
        Track(video_id="fresh123456", title="B", url="b", index=2),
    ]
    new, done = split_by_archive(tracks, archive)
    assert [t.video_id for t in new] == ["fresh123456"]
    assert [t.video_id for t in done] == ["known123456"]


def test_reset(tmp_path):
    archive = Archive(tmp_path)
    archive.record("abcdefghijk")
    archive.reset()
    assert len(archive) == 0
    assert not (tmp_path / "download_archive.txt").exists()
    assert not Archive(tmp_path).has("abcdefghijk")


def test_broken_processed_json_is_ignored(tmp_path):
    (tmp_path / "processed.json").write_text("{ broken", encoding="utf-8")
    (tmp_path / "download_archive.txt").write_text("youtube abcdefghijk\n", encoding="utf-8")
    assert Archive(tmp_path).has("abcdefghijk")
