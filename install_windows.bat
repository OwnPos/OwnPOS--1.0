@echo off
setlocal
title OwnPOS - setup for developers
cd /d "%~dp0"
echo.
echo  OwnPOS - one-time setup of the source code (for building and testing)
echo  ======================================================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo  Python is not installed.
    echo  1. Download Python 3.12 or 3.13 from https://www.python.org/downloads/windows/
    echo  2. During install, tick "Add python.exe to PATH".
    echo  3. Run this setup again.
    pause
    exit /b 1
  )
  set PY=python
)
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo  Python 3.11 or newer is needed. Please install Python 3.12 or 3.13 and run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo  Creating the program environment...
  %PY% -m venv .venv || (echo  Could not create the environment. & pause & exit /b 1)
)
echo  Installing components (needs internet, a few minutes the first time)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt || (echo  Install failed. Check the internet connection and try again. & pause & exit /b 1)
echo.
echo  Done. run_ownpos.bat starts this folder's code with its own settings folder (OwnPOS-Dev).
echo  Point it at a TEST database, never the store's real one. For the store, build the installer (build_exe.bat).
echo.
pause
