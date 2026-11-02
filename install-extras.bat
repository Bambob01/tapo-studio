@echo off
setlocal
set "NoDefaultCurrentDirectoryInExePath=1"
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run start.bat once first.
  pause
  exit /b 1
)
echo Installing Smart Music + Screen Sync support...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: -q -r requirements-extras.txt
if errorlevel 1 (
  echo Install failed.
) else (
  echo Done. Restart Tapo Studio to use them.
)
if /i not "%~1"=="nopause" pause
