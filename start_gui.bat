@echo off
rem mp3dl の GUI を起動します。ダブルクリックで実行してください。
chcp 65001 >nul 2>nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PY="
set "PYW="

rem --- 1) 仮想環境があればそれを使う ---------------------------------------
if exist ".venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
    set "PYW=%~dp0.venv\Scripts\pythonw.exe"
    goto :found
)

rem --- 2) py ランチャー（公式 Python の標準。いちばん確実） ------------------
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PY=py -3"
    set "PYW=pyw -3"
    goto :found
)

rem --- 3) PATH の python ----------------------------------------------------
rem Microsoft Store のダミー（実行しても何も起きない）を除くため、
rem 実際に動くかどうかをここで確かめる。
python -c "import sys" >nul 2>nul
if not errorlevel 1 (
    set "PY=python"
    set "PYW=pythonw"
    goto :found
)

echo.
echo Python が見つかりませんでした。
echo.
echo   https://www.python.org/downloads/windows/ からインストールしてください。
echo   インストール時に「Add python.exe to PATH」に必ずチェックを入れてください。
echo.
echo ※「Microsoft Store が開くだけ」の場合は、Windows の設定 →
echo    「アプリ実行エイリアス」で python.exe / python3.exe をオフにしてください。
echo.
pause
exit /b 1

:found
rem --- 4) 起動前に tkinter を確認する（無いと一瞬で閉じる原因になる） --------
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
    echo.
    echo GUI に必要な tkinter が使えません。
    echo.
    echo   「設定」→「アプリ」→ Python を選び「変更(Modify)」→
    echo   「tcl/tk and IDLE」にチェックを入れて再インストールしてください。
    echo.
    pause
    exit /b 1
)

rem --- 5) 起動 --------------------------------------------------------------
rem pythonw が使えない場合に備えて、失敗したら python で起動し直す。
if defined PYW (
    start "" %PYW% "%~dp0run_gui.pyw"
    if not errorlevel 1 goto :eof
)
start "" %PY% "%~dp0run_gui.pyw"
