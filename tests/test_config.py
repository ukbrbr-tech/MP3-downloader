"""設定の保存・読み込みのテスト."""

from __future__ import annotations

from mp3dl.config import DEFAULT_QUALITY, Settings


def test_defaults():
    settings = Settings()
    assert settings.quality == DEFAULT_QUALITY == "320"
    assert settings.mode == "new"
    assert settings.embed_thumbnail is True


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(last_url="https://example.com/list", dark_mode=True)
    settings.remember_playlist("https://example.com/list")
    settings.save(path)

    loaded = Settings.load(path)
    assert loaded.last_url == "https://example.com/list"
    assert loaded.dark_mode is True
    assert loaded.playlists == ["https://example.com/list"]


def test_missing_file_gives_defaults(tmp_path):
    assert Settings.load(tmp_path / "nope.json").quality == DEFAULT_QUALITY


def test_broken_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    assert Settings.load(path).mode == "new"


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"quality": "192", "bogus": 1}', encoding="utf-8")
    loaded = Settings.load(path)
    assert loaded.quality == "192"
    assert not hasattr(loaded, "bogus")


def test_remember_playlist_moves_to_front_without_duplicates():
    settings = Settings()
    settings.remember_playlist("a")
    settings.remember_playlist("b")
    settings.remember_playlist("a")
    assert settings.playlists == ["a", "b"]


def test_remember_playlist_limit():
    settings = Settings()
    for i in range(30):
        settings.remember_playlist(f"url{i}")
    assert len(settings.playlists) == 20


def test_output_root_falls_back_to_music(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert Settings().output_root().name in ("Music", "ミュージック")
