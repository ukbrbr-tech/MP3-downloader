# mp3dl

YouTube の再生リストから、**まだ一度も処理していない動画だけ**を mp3 として保存する個人用ツールです。
同じ再生リストを後日もう一度処理すると、以前処理した動画はスキップし、新しく追加された動画だけを処理します。

> 自分がダウンロードする権利を持つ動画、ダウンロードが許可されているコンテンツにのみ使用してください。

## できること

- YouTube 再生リストの URL を貼るだけで、新規動画だけを mp3 化
- 重複判定は **YouTube Video ID** 基準（タイトル変更・同名動画でも誤判定しない）
- mp3 は既定で **320kbps**
- サムネイルを **アルバムアート**として埋め込み（他の PC やスマホでも表示される JPEG）
- ID3 タグ（曲名 / チャンネル名 / 再生リスト名 / トラック番号 / 公開日 / 元動画 URL）
- `01 - 曲名.mp3` 形式のファイル名（Windows で使えない文字は自動で置換）
- 既存ファイルを上書きしない（`01 - 曲名 (2).mp3` として保存）
- 4 つのモード：**新規のみ / 全件 / 確認のみ / 選択**
- シンプルな GUI（進捗バー、成功・スキップ・失敗の件数、ログ表示）
- 1 曲失敗しても処理は止まらず、失敗した動画は次回また試せる

## 必要なソフト

| 名前 | 用途 | 導入方法 |
| --- | --- | --- |
| Python 3.9 以上 | 本体 | https://www.python.org/downloads/windows/ （インストール時に「Add python.exe to PATH」にチェック） |
| FFmpeg | mp3 変換 | `winget install Gyan.FFmpeg` |
| yt-dlp | 動画の取得 | `pip install -r requirements.txt` |
| mutagen | ID3 タグ・アルバムアート | 同上 |
| pillow | サムネイルの縮小・JPEG 変換 | 同上 |
| tkinter | GUI | Windows 版 Python に同梱（Linux は `sudo apt install python3-tk`） |

## インストール

### Windows（かんたん）

1. Python をインストールする（「Add python.exe to PATH」を必ずチェック）
2. このフォルダを好きな場所に置く
3. **`setup_windows.bat` をダブルクリック**（ライブラリの導入と依存関係の確認をまとめて行います）
4. FFmpeg が無いと表示されたら、PowerShell で `winget install Gyan.FFmpeg` を実行

### コマンドで入れる場合

```bash
python -m pip install -r requirements.txt
python -m mp3dl --check-deps      # 足りないものを確認
```

FFmpeg を PATH に入れたくない場合は、`ffmpeg.exe` をこのフォルダの `ffmpeg\bin\` に置けば自動で見つけます。

## 起動方法

| 方法 | コマンド |
| --- | --- |
| GUI（推奨） | `start_gui.bat` をダブルクリック、または `python -m mp3dl` |
| GUI（起動しないとき） | `debug_gui.bat` をダブルクリック（エラー内容が画面に残ります） |
| コマンドライン | `python -m mp3dl "<再生リストURL>" -o "C:\Users\you\Music"` |
| ブラウザ画面 | `python -m mp3dl --web` |
| 依存関係の確認 | `python -m mp3dl --check-deps` |

## 基本操作（GUI）

1. アプリを起動する
2. YouTube 再生リストの URL を貼る
3. 保存先フォルダを選ぶ（初回のみ。次回からは記憶されます）
4. モードは **「新規のみ」** のまま
5. **「開始」** を押す

これだけです。先に中身を見たいときは「確認」を押すと、総数・処理済み・新規件数と新規動画のタイトルが表示されます。

### モード

| モード | 動作 |
| --- | --- |
| **新規のみ**（既定） | 履歴に無い動画だけを処理する |
| 全件 | 再生リスト全体を処理する（既存ファイルは上書きせず別名で保存） |
| 確認のみ | 総数・処理済み・新規件数と新規動画のタイトルを表示するだけ |
| 選択 | 「確認」で一覧を出し、チェックを入れた動画だけを処理する |

「選択」モードでは、一覧の左端のチェック欄をクリック（またはキーボードのスペース）で切り替えられます。

### その他の機能

- **登録**：よく使う再生リストを登録しておけます
- **登録を全て更新**：登録済みの再生リストをまとめて「新規のみ」で更新します
- **フォルダを開く**：保存先をエクスプローラーで開きます
- **ダークモード** / **完了時に通知** / **音質(kbps)** / **サムネイルを埋め込む**
- 最後に使った URL・保存先・モードは自動で記憶されます

## 保存構造

```
Music/
└─ My Playlist/
   ├─ 01 - Song A.mp3
   ├─ 02 - Song B.mp3
   ├─ 03 - Song C.mp3
   ├─ download_archive.txt   ← 処理済み Video ID（これが新規判定の基準）
   ├─ processed.json         ← 処理済みの記録（タイトル・日時など）
   └─ app.log                ← 実行ログ
```

保存先は「選んだフォルダ ＋ 再生リスト名」です。再生リストごとに履歴が分かれます。

## 「新規のみ」モードの仕組み

1. 再生リストを読み込み、各動画の **YouTube Video ID** を取り出す
2. 保存先フォルダの `download_archive.txt` を読む
   （`youtube dQw4w9WgXcQ` という形式。yt-dlp の download archive と同じ）
3. ID がファイルに**無い動画だけ**を処理する
4. mp3 の作成・タグ付けまで**正常に終わった動画だけ**を履歴へ追記する

判定に使うのは Video ID だけです。ファイル名やタイトルは一切見ません。そのため、

- 動画のタイトルが変わっても、二重ダウンロードになりません
- 同じ曲名の別動画が追加された場合は、きちんと新規として処理されます
- 失敗した動画は履歴に残らないので、次回もう一度試されます

例：月曜に 100 動画の再生リストを処理し、金曜に 103 動画になっていた場合、
金曜に同じ URL を入れると**新しい 3 動画だけ**が処理されます。

## 履歴をリセットする方法

もう一度すべてダウンロードし直したいときは、次のいずれかを行ってください。

| 方法 | 手順 |
| --- | --- |
| GUI | 「確認」を押した後に **「履歴をリセット」** ボタン |
| コマンド | `python -m mp3dl "<URL>" -o "<保存先>" --reset-archive` |
| 手動 | 保存先フォルダの `download_archive.txt` と `processed.json` を削除 |

一部の動画だけやり直したい場合は、`download_archive.txt` から該当する行（Video ID）を削除してください。

## よくあるエラーと対処方法

| 症状 | 原因と対処 |
| --- | --- |
| **`start_gui.bat` が一瞬で閉じる / 何も起きない** | **`debug_gui.bat` をダブルクリックしてください。**エラー内容が画面に残り、`mp3dl-error.log` にも記録されます。よくある原因は下の 3 つです |
| ↳ tkinter が入っていない | 「設定」→「アプリ」→ Python →「変更(Modify)」→「tcl/tk and IDLE」にチェックして再インストール |
| ↳ Microsoft Store 版 Python のダミーが反応している | 「設定」→「アプリ」→「アプリ実行エイリアス」で python.exe / python3.exe をオフにし、公式版 Python を入れる |
| ↳ ライブラリが未導入 | `setup_windows.bat` を実行する |
| `FFmpeg が見つかりません` | FFmpeg 未導入。`winget install Gyan.FFmpeg` を実行するか、`ffmpeg.exe` をこのフォルダの `ffmpeg\bin\` に置く |
| `yt-dlp がインストールされていません` | `python -m pip install -r requirements.txt` を実行 |
| GUI が起動しない（`No module named tkinter`） | Windows は Python を「Modify」→ tcl/tk を有効にして再インストール。Linux は `sudo apt install python3-tk` |
| `Video unavailable` / `Private video` | 動画が非公開・削除済み。再試行しても直らないため、その動画だけ失敗として記録されます |
| `Sign in to confirm your age` | 年齢制限付き動画。`--cookies-from-browser chrome` を付けて実行（GUI の場合は設定ファイルの `cookies_from_browser`） |
| `HTTP Error 403` / 途中で止まる | 一時的な通信エラー。自動で 3 回まで再試行します。時間をおいて「開始」を押し直すと、続きから処理されます |
| 曲名が文字化けしたファイル名になる | Windows で使えない文字が全角に置換されています（仕様） |
| アルバムアートが表示されない | pillow が未導入だと webp サムネイルを変換できません。`pip install pillow` を実行 |
| ダウンロードが極端に遅い・止まる | yt-dlp が古い可能性があります。`python -m pip install -U yt-dlp` で更新してください |

うまくいかないときは、保存先フォルダの `app.log` に詳しい記録が残っています。

## コマンドライン

```bash
python -m mp3dl "<URL>" -o "<保存先>"                # 新規のみ（既定）
python -m mp3dl "<URL>" -o "<保存先>" --mode check   # 確認のみ
python -m mp3dl "<URL>" -o "<保存先>" --mode all     # 全件
python -m mp3dl "<URL>" -o "<保存先>" -q 256         # 音質を変える
python -m mp3dl "<URL>" --no-subfolder               # 再生リスト名のフォルダを作らない
python -m mp3dl --check-deps                         # 依存関係の確認
```

設定は `%APPDATA%\mp3dl\settings.json`（Windows）に保存されます。

## 開発者向け

```
mp3dl/
├─ config.py     設定と既定値（ハードコードを避けるための置き場）
├─ naming.py     Windows で安全なファイル名づくり
├─ archive.py    処理済み Video ID の履歴
├─ playlist.py   再生リストの取得（yt-dlp）
├─ pipeline.py   1 曲の取得・変換・タグ付け
├─ tagging.py    ID3 タグとアルバムアート（mutagen / pillow）
├─ job.py        モード（新規のみ / 全件 / 確認のみ / 選択）の制御
├─ deps.py       依存関係チェック
├─ gui.py        tkinter の GUI
├─ cli.py        コマンドライン
└─ web.py        ブラウザ画面（おまけ）
```

テストの実行：

```bash
python -m pip install pytest
python -m pytest
```

テストはネットワークを使いません（yt-dlp を差し替えて、ffmpeg で作った短い mp3 を使います）。
