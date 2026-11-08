@echo off
setlocal

rem Hard rule: discover the local environment and start Chinese PoB first.
call "%~dp0start-cn.cmd" >nul
if errorlevel 1 (
  echo {"ok":false,"error":"Chinese PoB is not ready. Run start-cn.cmd for details, or close English Path of Building and retry."}
  exit /b 2
)

set "DATA_DIR=%POB_AGENT_DATA%"
if not defined DATA_DIR if defined LOCALAPPDATA set "DATA_DIR=%LOCALAPPDATA%\pob-bd-builder"
if not defined DATA_DIR set "DATA_DIR=%USERPROFILE%\.pob-bd-builder"
if exist "%DATA_DIR%\runtime.env" call "%DATA_DIR%\runtime.env"
if defined POB_PYTHON (
  set "PY=%POB_PYTHON%"
) else (
  set "PY=python"
)

"%PY%" "%~dp0run_pob.py" %*
exit /b %errorlevel%
