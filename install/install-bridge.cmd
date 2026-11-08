@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_pob_bridge.ps1" %*
exit /b %errorlevel%
