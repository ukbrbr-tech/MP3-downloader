import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mp3dl import cli
from mp3dl.downloader import Track


def test_dry_run_lists_new_and_skipped(tmp_path, monkeypatch, capsys):
    (tmp_path / "Song A.mp3").write_bytes(b"")
    monkeypatch.setattr(
        cli,
        "fetch_tracks",
        lambda url, cookies_from_browser=None: [
            Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa"),
            Track("bbbbbbbbbbb", "Song B", "https://youtu.be/bbbbbbbbbbb"),
        ],
    )

    exit_code = cli.main(["https://example.com/playlist", "-o", str(tmp_path), "--dry-run"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "2 曲中 1 曲が新規、1 曲はスキップします。" in out
    assert "[スキップ] Song A" in out
    assert "[予定] Song B" in out
    assert not list(tmp_path.glob("Song B*"))


def test_no_skip_flag_ignores_existing_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "Song A.mp3").write_bytes(b"")
    monkeypatch.setattr(
        cli,
        "fetch_tracks",
        lambda url, cookies_from_browser=None: [
            Track("aaaaaaaaaaa", "Song A", "https://youtu.be/aaaaaaaaaaa")
        ],
    )

    cli.main(["https://example.com/playlist", "-o", str(tmp_path), "--dry-run", "--no-skip"])

    assert "[予定] Song A" in capsys.readouterr().out


def test_empty_playlist_reports_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "fetch_tracks", lambda url, cookies_from_browser=None: [])

    exit_code = cli.main(["https://example.com/playlist", "-o", str(tmp_path), "--dry-run"])

    assert exit_code == 1
    assert "見つかりませんでした" in capsys.readouterr().out
