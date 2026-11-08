@echo off
setlocal
if not exist "%POB_PYTHON%" (
  set "POB_PYTHON=python"
)
"%POB_PYTHON%" "%~dp0discover_env.py" --write --fix %*
exit /b %errorlevel%
