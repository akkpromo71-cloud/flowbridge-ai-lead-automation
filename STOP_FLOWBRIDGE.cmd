@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop-dev.ps1" -IncludeInfrastructure
set "flowbridge_exit=%errorlevel%"
if not "%flowbridge_exit%"=="0" echo FLOWBRIDGE STOP FAILED. See the ownership error above.
echo.
echo Press any key to close this window.
pause >nul
exit /b %flowbridge_exit%
