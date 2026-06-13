
@echo off
:: dashboard_widget.py と同じフォルダに置いて実行してください

set SCRIPT_DIR=%~dp0
set SCRIPT_PATH=%SCRIPT_DIR%dashboard_widget.py
set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set VBS_PATH=%STARTUP_DIR%\dashboard_widget.vbs

:: VBScriptを作成（コンソール非表示で起動するため）
echo Set WshShell = CreateObject("WScript.Shell") > "%VBS_PATH%"
echo WshShell.Run "pythonw ""%SCRIPT_PATH%""", 0, False >> "%VBS_PATH%"

echo.
echo ✅ 自動起動の登録が完了しました！
echo    次回PC起動時からウィジェットが自動で表示されます。
echo.
echo 登録先: %VBS_PATH%
echo.
pause