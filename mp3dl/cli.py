"""コマンドラインインターフェース（GUI を使わない場合や自動実行向け）."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .archive import Archive
from .config import DEFAULT_QUALITY, Settings, save_settings
from .deps import check_dependencies, format_report, missing_required
from .job import DEFAULT_MODE, make_plan, run_plan
from .pipeline import DOWNLOADED, FAILED, DownloadError, Result
from .playlist import PlaylistError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mp3dl",
        description="YouTube の再生リストを mp3 に変換して保存します。"
        "処理済みの動画（Video ID）は自動でスキップします。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n"
        "  mp3dl                                              GUI を起動する\n"
        "  mp3dl 'https://www.youtube.com/playlist?list=PLxx' -o ~/Music\n"
        "  mp3dl URL -o ~/Music --mode check                  新規動画の確認だけ\n"
        "  mp3dl --check-deps                                 必要なソフトの確認\n",
    )
    parser.add_argument("url", nargs="?", help="YouTube の再生リスト（または動画）の URL")
    parser.add_argument(
        "-o",
        "--output",
        default="",
        help="保存先のルートフォルダ（既定: 設定または <ホーム>/Music）。"
        "この下に再生リスト名のフォルダを作ります。",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["new", "all", "check"],
        default=DEFAULT_MODE,
        help="new: 新規のみ（既定） / all: 全件 / check: 確認のみ",
    )
    parser.add_argument(
        "-q", "--quality", default=DEFAULT_QUALITY,
        help=f"mp3 のビットレート kbps（既定: {DEFAULT_QUALITY}）",
    )
    parser.add_argument(
        "--no-thumbnail", action="store_true", help="サムネイルを埋め込まない"
    )
    parser.add_argument(
        "--no-subfolder",
        action="store_true",
        help="再生リスト名のサブフォルダを作らず、指定フォルダ直下に保存する",
    )
    parser.add_argument("--ffmpeg-location", default="", help="ffmpeg の実行ファイルまたはフォルダ")
    parser.add_argument(
        "--cookies-from-browser",
        default="",
        help="限定公開・年齢制限付き動画向けにブラウザの Cookie を使う（例: chrome, firefox）",
    )
    parser.add_argument(
        "--reset-archive",
        action="store_true",
        help="この再生リストの処理済み履歴を消してから実行する",
    )
    parser.add_argument("--check-deps", action="store_true", help="必要なソフトの確認だけ行う")
    parser.add_argument("--gui", action="store_true", help="GUI を起動する")

    web = parser.add_argument_group("ブラウザ画面 (--web)")
    web.add_argument("--web", action="store_true", help="ブラウザで操作する画面を起動する")
    web.add_argument("--port", type=int, default=8765, help="--web で使うポート番号（既定: 8765）")
    web.add_argument("--no-browser", action="store_true", help="--web でブラウザを自動で開かない")

    parser.add_argument("-v", "--verbose", action="store_true", help="yt-dlp の詳細ログを表示する")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _print_result(result: Result) -> None:
    icons = {DOWNLOADED: "✓", "skipped": "-", FAILED: "✗"}
    labels = {DOWNLOADED: "保存", "skipped": "スキップ", FAILED: "失敗"}
    icon = icons.get(result.status, "?")
    label = labels.get(result.status, result.status)
    suffix = f" ({result.detail})" if result.detail else ""
    print(f"  {icon} [{label}] {result.track.title}{suffix}", flush=True)


def _check_deps() -> int:
    deps = check_dependencies()
    print(format_report(deps))
    missing = missing_required(deps)
    if missing:
        print("\n不足しているものがあります。上の案内に従って導入してください。", file=sys.stderr)
        return 1
    print("\n必要なものは揃っています。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check_deps:
        return _check_deps()

    if args.web:
        from .web import serve

        return serve(port=args.port, open_browser=not args.no_browser)

    settings = Settings.load()
    if args.gui or not args.url:
        if not args.url and not args.gui and not sys.stdin.isatty():
            parser.error("URL を指定してください。")
        try:
            from .gui import run as run_gui
        except ImportError as exc:
            print(
                f"GUI を起動できません（tkinter がありません: {exc}）。\n"
                "URL を指定してコマンドラインから実行することもできます。",
                file=sys.stderr,
            )
            return 1
        return run_gui(settings)

    # --------------------------------------------------- コマンドラインで実行
    settings.quality = args.quality
    settings.embed_thumbnail = not args.no_thumbnail
    settings.make_playlist_subfolder = not args.no_subfolder
    settings.ffmpeg_location = args.ffmpeg_location
    settings.cookies_from_browser = args.cookies_from_browser
    if args.output:
        settings.last_output_dir = str(Path(args.output).expanduser())
    settings.last_url = args.url
    settings.mode = args.mode
    save_settings(settings)

    root = settings.output_root()
    print(f"保存先のルート: {root}")
    print("再生リストを読み込んでいます...")

    try:
        plan = make_plan(args.url, root, settings=settings)
    except PlaylistError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    if args.reset_archive:
        Archive(plan.destination).reset()
        print("履歴をリセットしました。すべての動画が新規扱いになります。")
        plan = make_plan(args.url, root, settings=settings)

    print(plan.summary_text())
    for track in plan.new_tracks:
        print(f"  + [新規] {track.index:02d}. {track.title}")

    if args.mode == "check":
        return 0

    targets = plan.tracks_for_mode(args.mode)
    if not targets:
        print("新しい動画はありません。すべて処理済みです。")
        return 0

    try:
        summary = run_plan(
            plan,
            mode=args.mode,
            settings=settings,
            on_result=_print_result,
            on_log=lambda message: print(message, flush=True),
        )
    except DownloadError as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n中断しました。次回は続きから処理できます。", file=sys.stderr)
        return 130

    print(
        f"\n完了: 成功 {summary.downloaded} / スキップ {summary.skipped} / "
        f"失敗 {summary.failed}"
    )
    if summary.failures:
        print("失敗した動画（次回もう一度試せます）:", file=sys.stderr)
        for result in summary.failures:
            print(f"  ✗ {result.track.title}: {result.detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
