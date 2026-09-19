@echo off
rem mp3dl の GUI を起動します。ダブルクリックで実行してください。
setlocal
cd /d "%~dp0"

rem 仮想環境があればそれを使う
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "run_gui.pyw"
    goto :eof
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "run_gui.pyw"
    goto :eof
)

echo Python が見つかりませんでした。
echo https://www.python.org/downloads/windows/ からインストールしてください。
pause
