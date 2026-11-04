@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- find Python (python, then py -3) -------------------------------------
set "PY=python"
where python >nul 2>nul
if not errorlevel 1 goto :found
set "PY=py -3"
where py >nul 2>nul
if not errorlevel 1 goto :found

echo.
echo  Python not found on PATH.
echo.
echo  1) Install Python 3.10+ from https://www.python.org/downloads/
echo  2) IMPORTANT: tick "Add python.exe to PATH" during setup
echo  3) Close this window, reopen it, and run start_server.bat again
echo.
echo  If Python is already installed, open Settings ^> Apps ^> Advanced app settings
echo  ^> App execution aliases and turn off "python.exe" / "python3.exe".
echo.
pause
exit /b 1

:found
echo Using Python:
%PY% --version
echo.

rem --- install dependencies with -m pip (no separate pip command needed) ----
%PY% -m pip --version >nul 2>nul
if errorlevel 1 (
  echo pip module not found, trying ensurepip...
  %PY% -m ensurepip --upgrade
)

%PY% -c "import flask, paramiko, winrm, cryptography, smb" 2>nul
if not errorlevel 1 goto :start

echo Installing required packages...
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo  Package installation failed.
  echo  Check your internet connection, then try again.
  echo.
  pause
  exit /b 1
)

:start
echo.
echo Starting Remote Control server...
echo.
%PY% run_server.py
echo.
echo Server stopped.
pause
