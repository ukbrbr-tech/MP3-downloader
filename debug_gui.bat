@echo off
rem うまく起動しないときに、エラー内容を確認するためのファイルです。
rem このウィンドウは閉じずに残るので、表示された内容を確認できます。
chcp 65001 >nul 2>nul
setlocal
cd /d "%~dp0"

echo ===== 実行に使う Python =====
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (py -3 -c "import sys" >nul 2>nul && set "PY=py -3")
if not defined PY (python -c "import sys" >nul 2>nul && set "PY=python")

if not defined PY (
    echo Python が見つかりませんでした。
    echo https://www.python.org/downloads/windows/ からインストールしてください。
    echo インストール時に「Add python.exe to PATH」にチェックを入れてください。
    echo.
    pause
    exit /b 1
)

%PY% -c "import sys; print(sys.version); print(sys.executable)"

echo.
echo ===== 依存関係の確認 =====
%PY% -m mp3dl --check-deps

echo.
echo ===== GUI を起動します（エラーはこの下に表示されます） =====
%PY% "%~dp0run_gui.pyw"

echo.
echo ===== 終了しました（終了コード %errorlevel%） =====
echo 上の内容をそのままコピーすると、原因の特定に役立ちます。
pause
