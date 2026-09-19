"""コマンドラインインターフェース."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .downloader import (
    DEFAULT_FILENAME_TEMPLATE,
    DEFAULT_QUALITY,
    DownloadError,
    Result,
    download_tracks,
    ensure_ffmpeg,
    fetch_tracks,
    plan,
)
from .library import Library


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mp3dl",
        description="YouTube の再生リストを mp3 に変換して指定フォルダに保存します。"
        "フォルダに既にある曲は自動でスキップします。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n"
        "  mp3dl 'https://www.youtube.com/playlist?list=PLxxxx' -o ~/Music/MyList\n"
        "  mp3dl URL -o ./out --dry-run\n",
    )
    parser.add_argument("url", help="YouTube の再生リスト（または動画）の URL")
    parser.add_argument(
        "-o",
        "--output",
        default=".",
        help="mp3 の保存先フォルダ（既定: カレントディレクトリ）",
    )
    parser.add_argument(
        "-q",
        "--quality",
        default=DEFAULT_QUALITY,
        help=f"mp3 のビットレート kbps（既定: {DEFAULT_QUALITY}）",
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=int,
        default=1,
        help="同時ダウンロード数（既定: 1）",
    )
    parser.add_argument(
        "--filename-template",
        default=DEFAULT_FILENAME_TEMPLATE,
        help="yt-dlp 形式のファイル名テンプレート"
        f"（既定: {DEFAULT_FILENAME_TEMPLATE.replace('%', '%%')}。"
        "例: '%%(title)s [%%(id)s].%%(ext)s'）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="ダウンロードせず、対象とスキップの一覧だけ表示する",
    )
    parser.add_argument(
        "--no-archive",
        action="store_true",
        help="ダウンロード履歴ファイル(.downloaded.txt)を使わず、既存 mp3 だけで判定する",
    )
    parser.add_argument(
        "--no-skip",
        action="store_true",
        help="既存ファイルのスキップを無効にして、すべて取得し直す",
    )
    parser.add_argument(
        "--embed-thumbnail",
        action="store_true",
        help="サムネイルをアルバムアートとして埋め込む（要 mutagen）",
    )
    parser.add_argument("--ffmpeg-location", help="ffmpeg の実行ファイルまたはフォルダのパス")
    parser.add_argument(
        "--cookies-from-browser",
        help="限定公開・年齢制限付き動画向けにブラウザの Cookie を使う（例: chrome, firefox）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="yt-dlp の詳細ログを表示する")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _print_result(result: Result) -> None:
    icons = {"downloaded": "✓", "skipped": "-", "failed": "✗"}
    labels = {"downloaded": "保存", "skipped": "スキップ", "failed": "失敗"}
    icon = icons.get(result.status, "?")
    label = labels.get(result.status, result.status)
    suffix = f" ({result.detail})" if result.detail else ""
    print(f"  {icon} [{label}] {result.track.title}{suffix}", flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    dest = Path(args.output).expanduser().resolve()
    if args.jobs < 1:
        print("エラー: --jobs は 1 以上を指定してください。", file=sys.stderr)
        return 2

    try:
        if not args.dry_run:
            ensure_ffmpeg(args.ffmpeg_location)

        print(f"保存先: {dest}")
        print("再生リストを読み込んでいます...")
        tracks = fetch_tracks(args.url, cookies_from_browser=args.cookies_from_browser)
    except DownloadError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    if not tracks:
        print("ダウンロードできる動画が見つかりませんでした。")
        return 1

    library = Library.scan(dest, use_archive=not args.no_archive)
    if args.no_skip:
        library.video_ids.clear()
        library.title_keys.clear()

    entries = plan(tracks, library)
    pending = [track for track, reason in entries if reason is None]
    skipped = [(track, reason) for track, reason in entries if reason is not None]

    print(f"{len(tracks)} 曲中 {len(pending)} 曲が新規、{len(skipped)} 曲はスキップします。")

    for track, reason in skipped:
        print(f"  - [スキップ] {track.title} ({reason})")

    if args.dry_run:
        for track in pending:
            print(f"  + [予定] {track.title}")
        return 0

    if not pending:
        print("すべてダウンロード済みです。")
        return 0

    try:
        results = download_tracks(
            pending,
            dest=dest,
            library=library,
            filename_template=args.filename_template,
            quality=args.quality,
            ffmpeg_location=args.ffmpeg_location,
            cookies_from_browser=args.cookies_from_browser,
            embed_thumbnail=args.embed_thumbnail,
            jobs=args.jobs,
            verbose=args.verbose,
            on_result=_print_result,
        )
    except DownloadError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました。次回は続きからダウンロードできます。", file=sys.stderr)
        return 130

    downloaded = sum(1 for r in results if r.status == "downloaded")
    failed = [r for r in results if r.status == "failed"]

    print(f"\n完了: {downloaded} 曲を保存、{len(skipped)} 曲をスキップ、{len(failed)} 曲が失敗。")
    if failed:
        print("失敗した曲:", file=sys.stderr)
        for result in failed:
            print(f"  ✗ {result.track.title}: {result.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
