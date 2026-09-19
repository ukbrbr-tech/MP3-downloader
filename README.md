# mp3dl — YouTube 再生リスト → mp3 ダウンローダー

YouTube の再生リストの URL を渡すと、各動画を **mp3 に変換して指定したフォルダへ保存**します。
保存先フォルダに **既にある曲は自動でスキップ**するので、同じコマンドを何度実行しても
追加された曲だけが差分ダウンロードされます。

## 必要なもの

- Python 3.9 以上
- [ffmpeg](https://ffmpeg.org/)（mp3 変換に使用）
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt install ffmpeg`
  - Windows: `winget install Gyan.FFmpeg`

## インストール

```bash
git clone https://github.com/ukbrbr-tech/MP3-downloader.git
cd MP3-downloader
pip install -r requirements.txt      # yt-dlp
# もしくは mp3dl コマンドとして入れる場合:
pip install .
```

## 使い方

```bash
# 再生リストを ~/Music/MyList に mp3 で保存
python -m mp3dl "https://www.youtube.com/playlist?list=PLxxxxxxxx" -o ~/Music/MyList

# pip install . した場合は mp3dl コマンドで
mp3dl "https://www.youtube.com/playlist?list=PLxxxxxxxx" -o ~/Music/MyList

# 何がダウンロードされるか確認するだけ（実際には落とさない）
mp3dl "URL" -o ~/Music/MyList --dry-run

# 音質を 320kbps にして 3 曲ずつ並列ダウンロード
mp3dl "URL" -o ~/Music/MyList -q 320 -j 3
```

実行例:

```
保存先: /home/me/Music/MyList
再生リストを読み込んでいます...
20 曲中 3 曲が新規、17 曲はスキップします。
  - [スキップ] 夜に駆ける (同名の mp3 が存在)
  - [スキップ] Lemon (ダウンロード済み (ID 一致))
  ...
  ✓ [保存] 新しい曲 1
  ✓ [保存] 新しい曲 2
  ✗ [失敗] 限定公開の曲 (Private video)

完了: 2 曲を保存、17 曲をスキップ、1 曲が失敗。
```

再生リストの URL のほか、単一の動画 URL もそのまま渡せます。

## 既存ファイルをスキップする仕組み

ダウンロード前に保存先フォルダを調べ、次のいずれかに当てはまる曲を飛ばします。

1. **ダウンロード履歴に動画 ID がある** — 保存先フォルダの `.downloaded.txt` に、
   成功した動画の ID を 1 行ずつ記録します。曲名を後から変えてもスキップされます。
2. **ファイル名に動画 ID が含まれている** — `曲名 [dQw4w9WgXcQ].mp3` のような
   ファイル（`--filename-template '%(title)s [%(id)s].%(ext)s'` で作られる形）を認識します。
3. **同じ曲名の mp3 が既にある** — 大文字小文字・全角半角・記号や空白の違いは無視して
   比較するので、履歴ファイルが無くても、手動で入れた mp3 や他のツールで落とした
   mp3 は再ダウンロードされません。サブフォルダの中も対象です。

失敗した曲は履歴に記録されないため、次回の実行で自動的に再挑戦されます。
途中で `Ctrl+C` を押して中断した場合も、次回は続きから再開できます。

## オプション

| オプション | 説明 |
| --- | --- |
| `-o`, `--output` | mp3 の保存先フォルダ（既定: カレントディレクトリ） |
| `-q`, `--quality` | mp3 のビットレート kbps（既定: `192`） |
| `-j`, `--jobs` | 同時ダウンロード数（既定: `1`） |
| `--filename-template` | yt-dlp 形式のファイル名テンプレート（既定: `%(title)s.%(ext)s`） |
| `--dry-run` | ダウンロードせず、対象とスキップの一覧だけ表示 |
| `--no-skip` | スキップを無効にしてすべて取得し直す |
| `--no-archive` | `.downloaded.txt` を使わず、既存 mp3 だけで判定する |
| `--embed-thumbnail` | サムネイルをアルバムアートとして埋め込む（`pip install mutagen` が必要） |
| `--ffmpeg-location` | ffmpeg のパスを明示する |
| `--cookies-from-browser` | 限定公開・年齢制限付き動画向けにブラウザの Cookie を使う（例: `chrome`） |
| `-v`, `--verbose` | yt-dlp の詳細ログを表示 |

終了コードは、すべて成功で `0`、1 曲でも失敗すると `1`、中断すると `130` です。
cron や タスクスケジューラで定期実行すれば、再生リストの新曲だけが自動で溜まっていきます。

## 開発

```bash
pip install pytest
python -m pytest tests -q
```

テストはネットワークに接続しません（yt-dlp をダミーに差し替えて検証しています）。

## 注意

ダウンロードは、著作権者が許諾しているコンテンツや自分がアップロードした動画など、
各サービスの利用規約と各国の法律の範囲内で行ってください。
