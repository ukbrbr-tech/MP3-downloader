@echo off
rem 必要な Python ライブラリをまとめて入れ、依存関係を確認します。
setlocal
cd /d "%~dp0"

echo === Python ライブラリをインストールします ===
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo インストールに失敗しました。Python が入っているか確認してください。
    pause
    exit /b 1
)

echo.
echo === 依存関係を確認します ===
python -m mp3dl --check-deps

echo.
echo FFmpeg が見つからない場合は、次のコマンドで導入できます:
echo     winget install Gyan.FFmpeg
echo.
pause
