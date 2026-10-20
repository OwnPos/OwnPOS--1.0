@echo off
rem Builds OwnPOS into ONE password-protected installer file:  dist\installer\OwnPOS-Setup-x.y.z.exe
rem Steps: licence key check, tests (needs a PostgreSQL server), program folder (PyInstaller), self-test of the
rem built program, installer (Inno Setup 6.4+, asks for the installer password).
rem Needs once: the .venv (install_windows.bat makes it), Inno Setup 6.4+ from jrsoftware.org, and the licence
rem key made with  .venv\Scripts\python.exe tools\licence_tool.py init
rem Tests: set OWNPOS_TEST_PG first, e.g.  set OWNPOS_TEST_PG=host=localhost port=5432 user=postgres password=...
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call install_windows.bat
set PY=.venv\Scripts\python.exe
"%PY%" -m pip install -r requirements-dev.txt || (pause & exit /b 1)
echo.
echo  1/5  Licence key...
"%PY%" -c "import sys; from ownpos.core.licence_key import PUBLIC_KEY_B64 as k; sys.exit(0 if k else 1)"
if errorlevel 1 (
  echo  The licence key is not set, so this build would have licensing switched OFF.
  echo  Run once:  %PY% tools\licence_tool.py init   then build again.
  pause & exit /b 1
)
echo  2/5  Tests...
if "%OWNPOS_TEST_PG%"=="" (
  echo  OWNPOS_TEST_PG is not set: the tests need a PostgreSQL server and are SKIPPED on this PC.
  echo  Press Ctrl+C to stop, or any key to build without running them here.
  pause
) else (
  set QT_QPA_PLATFORM=offscreen
  "%PY%" -m pytest -q tests_pg || (echo  Tests failed: nothing was built. & pause & exit /b 1)
  set QT_QPA_PLATFORM=
)
echo  3/5  Building the program folder...
"%PY%" -m PyInstaller --noconfirm --clean ownpos.spec || (echo  Build failed. & pause & exit /b 1)
echo  4/5  Self-test of the built program...
set OWNPOS_DATA=%TEMP%\ownpos-selftest
if not "%OWNPOS_TEST_PG%"=="" set OWNPOS_SELFTEST_PG=%OWNPOS_TEST_PG%
"dist\OwnPOS\OwnPOS.exe" --self-test "%TEMP%\ownpos-selftest.txt"
if errorlevel 1 (type "%TEMP%\ownpos-selftest.txt" & echo  Self-test failed: no installer made. & pause & exit /b 1)
set OWNPOS_DATA=
set OWNPOS_SELFTEST_PG=
findstr /b "SELFTEST" "%TEMP%\ownpos-selftest.txt"
"%PY%" tools\write_version.py > "%TEMP%\ownpos_ver.txt" || (echo  Could not read the version. & pause & exit /b 1)
set /p VER=<"%TEMP%\ownpos_ver.txt"
echo  5/5  Making the installer...
powershell -NoProfile -ExecutionPolicy Bypass -File tools\make_installer.ps1
if errorlevel 1 (echo  No installer made. & pause & exit /b 1)
echo.
echo  DONE: dist\installer\OwnPOS-Setup-%VER%.exe  (asks for the installer password)
pause
