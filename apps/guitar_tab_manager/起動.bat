@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================
echo    Guitar TAB Manager
echo ===================================
echo.

:: Python確認
python --version >nul 2>&1
if errorlevel 1 (
    echo Python が見つかりません。Python 3.10 以上をインストールしてください。
    pause
    exit /b 1
)

:: 依存ライブラリのインストール（初回のみ）
python -c "import anthropic" >nul 2>&1
if errorlevel 1 (
    echo [初回セットアップ] anthropic をインストール中...
    pip install anthropic -q
)

python -c "import reportlab" >nul 2>&1
if errorlevel 1 (
    echo [初回セットアップ] reportlab をインストール中...
    pip install reportlab -q
)

:: 起動
python guitar_tab_manager.py
if errorlevel 1 (
    echo.
    echo エラーが発生しました。
    pause
)
