@echo off
setlocal
set "ROOT=%~dp0"
if not exist "%ROOT%.venv\Scripts\python.exe" (
  echo Run first: py -3.12 scripts\install.py
  pause
  exit /b 1
)
"%ROOT%.venv\Scripts\python.exe" -m trace_llm --open-browser %*
if errorlevel 1 pause
