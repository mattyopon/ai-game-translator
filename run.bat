@echo off
REM ========================================================
REM  AI Game Translator — Quick Launch
REM  ゲーム翻訳AIツール 簡単起動
REM ========================================================
REM  Double-click to start. Browser opens automatically.
REM  ダブルクリックで起動。ブラウザが自動で開きます。
REM ========================================================

echo Starting AI Game Translator...
echo.

REM Install dependencies if needed
pip install flask openpyxl pyyaml tqdm anthropic --quiet 2>nul

python app.py

if errorlevel 1 (
    echo.
    echo [ERROR] Failed to start. Make sure Python 3.10+ is installed.
    pause
)
