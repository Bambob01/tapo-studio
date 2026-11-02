@echo off
setlocal EnableExtensions
rem Never run programs that happen to sit in this folder in place of real ones.
set "NoDefaultCurrentDirectoryInExePath=1"
set "SYS=%SystemRoot%\System32"
cd /d "%~dp0"
title Tapo Studio

rem ---- warn if other Windows accounts could modify the app ------------------
"%SYS%\icacls.exe" "%~dp0." 2>nul | "%SYS%\findstr.exe" /i /r /c:"Users:.*(M)" /c:"Users:.*(F)" /c:"Users:.*(W)" /c:"Everyone:.*(M)" /c:"Everyone:.*(F)" >nul
if not errorlevel 1 (
  echo.
  echo  WARNING: other accounts on this PC can change files in this folder.
  echo  For safety, move the Tapo Studio folder into your own user folder,
  echo  e.g. %USERPROFILE%\Documents\TapoStudio, and run it from there.
  echo.
)

rem ---- find Python 3.11 or newer --------------------------------------------
set "PY="
for %%V in (3.13 3.12 3.14 3.11) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
  )
)
if not defined PY (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo.
  echo  Tapo Studio needs Python 3.11 or newer.
  echo  Install it from https://www.python.org/downloads/windows/
  echo  ^(tick "Add python.exe to PATH" during setup^), then run start.bat again.
  echo.
  pause
  exit /b 1
)

rem ---- private virtual environment inside this folder ------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Setting up Tapo Studio for the first time - this takes a minute...
  %PY% -m venv .venv || goto :fail
)

rem Exact versions, prebuilt packages only (no third-party build scripts run).
rem Reinstall only when requirements.txt changes.
"%SYS%\fc.exe" /b requirements.txt ".venv\installed.txt" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --only-binary=:all: -q -r requirements.txt || goto :fail
  copy /y requirements.txt ".venv\installed.txt" >nul
)

if not exist ".venv\extras-prompted" (
  echo.
  echo  Optional: install Smart Music and Screen Sync support now? ^(about 20 MB^)
  "%SYS%\choice.exe" /c YN /m "  Install extras"
  if not errorlevel 2 call "%~dp0install-extras.bat" nopause
  type nul > ".venv\extras-prompted"
)

".venv\Scripts\python.exe" -E -s -m tapo_studio %*
if errorlevel 1 pause
exit /b 0

:fail
echo.
echo  Setup failed. Check your internet connection and try again.
pause
exit /b 1
