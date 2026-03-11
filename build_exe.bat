@echo off
REM ========================================================
REM  AI Game Translator — Windows EXE Builder
REM  ゲーム翻訳AIツール EXEビルドスクリプト
REM ========================================================
REM
REM  Usage: Double-click this file or run from Command Prompt
REM  Requirements: Python 3.10+ installed and in PATH
REM
REM  Output: dist\AIGameTranslator\AIGameTranslator.exe
REM ========================================================

echo.
echo ========================================
echo  AI Game Translator - EXE Builder
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

REM Install dependencies
echo [1/3] Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

REM Build with PyInstaller
echo [2/3] Building EXE with PyInstaller...
pyinstaller ^
    --name "AIGameTranslator" ^
    --onedir ^
    --windowed ^
    --noconfirm ^
    --clean ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --add-data "config.yaml;." ^
    --add-data "data;data" ^
    --hidden-import "anthropic" ^
    --hidden-import "flask" ^
    --hidden-import "openpyxl" ^
    --hidden-import "yaml" ^
    --hidden-import "tqdm" ^
    --hidden-import "translator" ^
    --hidden-import "memory" ^
    --hidden-import "glossary" ^
    --hidden-import "excel_io" ^
    --icon "NONE" ^
    app.py

if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

REM Copy data files to ensure they're accessible
echo [3/3] Copying data files...
if not exist "dist\AIGameTranslator\data" mkdir "dist\AIGameTranslator\data"
copy /Y "data\glossary.json" "dist\AIGameTranslator\data\" >nul
copy /Y "data\style_guide.md" "dist\AIGameTranslator\data\" >nul
copy /Y "config.yaml" "dist\AIGameTranslator\" >nul

REM Create empty translation memory if not exists
if not exist "dist\AIGameTranslator\data\translation_memory.json" (
    echo {"version":1,"entries":[]} > "dist\AIGameTranslator\data\translation_memory.json"
)

echo.
echo ========================================
echo  BUILD COMPLETE!
echo ========================================
echo.
echo  EXE: dist\AIGameTranslator\AIGameTranslator.exe
echo.
echo  Double-click the EXE to start.
echo  Browser will open automatically.
echo ========================================
echo.
pause
