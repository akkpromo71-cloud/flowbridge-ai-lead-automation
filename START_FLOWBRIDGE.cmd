@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dev.ps1" -OpenBrowser
set "flowbridge_exit=%errorlevel%"
if not "%flowbridge_exit%"=="0" echo FLOWBRIDGE START FAILED. See the error above; unrelated processes were not stopped.
echo.
echo Press any key to close this window.
pause >nul
exit /b %flowbridge_exit%
