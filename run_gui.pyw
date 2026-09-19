"""ダブルクリックで GUI を起動するためのファイル（コンソールを出さない）.

Windows では拡張子 .pyw なので、黒いコマンドプロンプトが出ません。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mp3dl.gui import run

if __name__ == "__main__":
    raise SystemExit(run())
