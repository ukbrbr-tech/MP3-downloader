"""ダウンロード〜タグ付けまでの通し確認（ネットワークは使わない）."""

from __future__ import annotations

from conftest import fake_factory, requires_ffmpeg, video_info
from mp3dl.archive import Archive
from mp3dl.pipeline import Options, download_tracks
from mp3dl.playlist import Playlist, Track

pytestmark = requires_ffmpeg


def make_tracks(count: int = 2) -> list[Track]:
    return [
        Track(
            video_id=f"video{i:07d}",
            title=f"Song {i}",
            url=f"https://www.youtube.com/watch?v=video{i:07d}",
            index=i,
        )
        for i in range(1, count + 1)
    ]


def make_videos(tracks: list[Track], **extra) -> dict:
    return {t.video_id: video_info(t.video_id, t.title, **extra) for t in tracks}


def run(tracks, videos, dest, archive=None, playlist=None, options=None, calls=None):
    return download_tracks(
        tracks,
        dest=dest,
        archive=archive or Archive(dest),
        playlist=playlist or Playlist(title="My Playlist", url="p", tracks=list(tracks)),
        options=options or Options(retries=1),
        ydl_factory=fake_factory(videos, calls),
    )


def test_downloads_and_names_files(tmp_path):
    tracks = make_tracks(2)
    summary = run(tracks, make_videos(tracks), tmp_path)

    assert summary.downloaded == 2
    assert summary.failed == 0
    assert (tmp_path / "01 - Song 1.mp3").is_file()
    assert (tmp_path / "02 - Song 2.mp3").is_file()


def test_records_only_successful_videos(tmp_path):
    tracks = make_tracks(2)
    videos = make_videos(tracks)
    videos["video0000002"]["fail_times"] = 5
    videos["video0000002"]["fail_message"] = "Private video"

    summary = run(tracks, videos, tmp_path, options=Options(retries=2))

    assert summary.downloaded == 1
    assert summary.failed == 1
    archive = Archive(tmp_path)
    assert archive.has("video0000001")
    assert not archive.has("video0000002")  # 失敗したものは履歴に残さない


def test_second_run_skips_processed(tmp_path):
    """同じ再生リストをもう一度処理すると、新規だけが対象になる."""
    from mp3dl.pipeline import select_new

    tracks = make_tracks(2)
    run(tracks, make_videos(tracks), tmp_path)

    later = make_tracks(3)  # 3 曲目が追加された
    archive = Archive(tmp_path)
    new = select_new(later, archive)
    assert [t.video_id for t in new] == ["video0000003"]

    summary = run(new, make_videos(later), tmp_path, archive=archive)
    assert summary.downloaded == 1
    assert (tmp_path / "03 - Song 3.mp3").is_file()


def test_existing_file_is_not_overwritten(tmp_path):
    tracks = make_tracks(1)
    existing = tmp_path / "01 - Song 1.mp3"
    existing.write_bytes(b"do not touch")

    summary = run(tracks, make_videos(tracks), tmp_path)

    assert summary.downloaded == 1
    assert existing.read_bytes() == b"do not touch"
    assert (tmp_path / "01 - Song 1 (2).mp3").is_file()


def test_retry_then_success(tmp_path):
    tracks = make_tracks(1)
    videos = make_videos(tracks)
    videos["video0000001"]["fail_times"] = 1
    videos["video0000001"]["fail_message"] = "Unable to download webpage: timed out"

    calls: list[str] = []
    summary = run(
        tracks, videos, tmp_path,
        options=Options(retries=3, retry_backoff=0.0),
        calls=calls,
    )

    assert summary.downloaded == 1
    assert calls == ["video0000001", "video0000001"]  # 1 回失敗して 1 回成功


def test_permanent_error_is_not_retried(tmp_path):
    tracks = make_tracks(1)
    videos = make_videos(tracks)
    videos["video0000001"]["fail_times"] = 99
    videos["video0000001"]["fail_message"] = "ERROR: Private video"

    calls: list[str] = []
    summary = run(
        tracks, videos, tmp_path,
        options=Options(retries=3, retry_backoff=0.0),
        calls=calls,
    )

    assert summary.failed == 1
    assert calls == ["video0000001"]  # 再試行していない


def test_one_failure_does_not_stop_the_rest(tmp_path):
    tracks = make_tracks(3)
    videos = make_videos(tracks)
    videos["video0000002"]["fail_times"] = 99
    videos["video0000002"]["fail_message"] = "Video unavailable"

    summary = run(tracks, videos, tmp_path, options=Options(retries=1))

    assert summary.downloaded == 2
    assert summary.failed == 1
    assert (tmp_path / "03 - Song 3.mp3").is_file()


def test_id3_tags_and_cover(tmp_path):
    from mutagen.id3 import ID3

    tracks = make_tracks(1)
    playlist = Playlist(title="My Playlist", url="p", tracks=tracks)
    run(tracks, make_videos(tracks), tmp_path, playlist=playlist)

    tags = ID3(tmp_path / "01 - Song 1.mp3")
    assert tags["TIT2"].text[0] == "Song 1"
    assert tags["TPE1"].text[0] == "Test Channel"
    assert tags["TALB"].text[0] == "My Playlist"
    assert str(tags["TRCK"].text[0]).startswith("1")
    assert str(tags["TDRC"].text[0]).startswith("2024")
    assert "youtube.com/watch?v=video0000001" in tags.getall("COMM")[0].text[0]

    covers = tags.getall("APIC")
    assert covers and covers[0].mime == "image/jpeg"
    assert covers[0].type == 3
    assert len(covers[0].data) > 0


def test_cover_is_resized(tmp_path):
    import io

    from mutagen.id3 import ID3
    from PIL import Image

    tracks = make_tracks(1)
    run(tracks, make_videos(tracks), tmp_path, options=Options(retries=1, cover_max_px=200))

    cover = ID3(tmp_path / "01 - Song 1.mp3").getall("APIC")[0]
    with Image.open(io.BytesIO(cover.data)) as image:
        assert max(image.size) <= 200


def test_thumbnail_files_are_cleaned_up(tmp_path):
    tracks = make_tracks(1)
    run(tracks, make_videos(tracks), tmp_path)

    leftovers = list(tmp_path.rglob("*.jpg")) + list(tmp_path.rglob("*.webp"))
    assert leftovers == []
    assert not (tmp_path / ".mp3dl-tmp").exists()


def test_unsafe_title_is_sanitized(tmp_path):
    track = Track(
        video_id="video0000009",
        title='bad: name / with * chars?',
        url="https://www.youtube.com/watch?v=video0000009",
        index=1,
    )
    videos = {track.video_id: video_info(track.video_id, track.title)}
    summary = run([track], videos, tmp_path)

    assert summary.downloaded == 1
    saved = list(tmp_path.glob("*.mp3"))[0]
    assert not any(ch in saved.name for ch in '\\/:*?"<>|')


def test_cancellation_stops_processing(tmp_path):
    tracks = make_tracks(3)
    stop = {"value": False}

    def should_stop() -> bool:
        return stop["value"]

    def on_result(result):
        stop["value"] = True  # 1 曲目が終わったら中止する

    summary = download_tracks(
        tracks,
        dest=tmp_path,
        archive=Archive(tmp_path),
        playlist=Playlist(title="P", url="p", tracks=tracks),
        options=Options(retries=1),
        on_result=on_result,
        should_stop=should_stop,
        ydl_factory=fake_factory(make_videos(tracks)),
    )

    assert summary.downloaded == 1
    assert summary.cancelled
    assert not (tmp_path / "02 - Song 2.mp3").exists()


def test_options_request_320kbps_mp3(tmp_path):
    from mp3dl.pipeline import build_ydl_options

    opts = build_ydl_options(tmp_path, Options())
    extractor = opts["postprocessors"][0]
    assert extractor["key"] == "FFmpegExtractAudio"
    assert extractor["preferredcodec"] == "mp3"
    assert extractor["preferredquality"] == "320"
    assert opts["writethumbnail"] is True
    assert opts["noplaylist"] is True


def test_options_pass_through_ffmpeg_and_cookies(tmp_path):
    from mp3dl.pipeline import build_ydl_options

    opts = build_ydl_options(
        tmp_path,
        Options(quality="192", embed_thumbnail=False,
                ffmpeg_location="/usr/bin/ffmpeg", cookies_from_browser="chrome"),
    )
    assert opts["postprocessors"][0]["preferredquality"] == "192"
    assert opts["writethumbnail"] is False
    assert opts["ffmpeg_location"] == "/usr/bin/ffmpeg"
    assert opts["cookiesfrombrowser"] == ("chrome",)


def test_finds_file_from_requested_downloads(tmp_path):
    """yt-dlp が返す requested_downloads からも mp3 を見つけられる."""
    from mp3dl.archive import Archive
    from mp3dl.pipeline import TrackDownloader

    worker = TrackDownloader(dest=tmp_path, archive=Archive(tmp_path))
    worker.temp_dir.mkdir(parents=True, exist_ok=True)
    produced = worker.temp_dir / "someplace.mp3"
    produced.write_bytes(b"x")

    track = Track("zzzzzzzzzzz", "Z", "u", index=1)
    info = {"requested_downloads": [{"filepath": str(produced.with_suffix(".webm"))}]}
    assert worker._find_produced_mp3(track, info) == produced


def test_permanent_error_detection():
    from mp3dl.pipeline import is_permanent_error

    assert is_permanent_error("ERROR: Private video")
    assert is_permanent_error("Video unavailable")
    assert not is_permanent_error("Unable to download webpage: timed out")
    assert not is_permanent_error("HTTP Error 403: Forbidden")
