@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_pob_cn.ps1" %*
exit /b %errorlevel%
