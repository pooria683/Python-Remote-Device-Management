@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -c "import flask, paramiko, winrm, cryptography, smb" 2>nul || python -m pip install -r requirements.txt
echo Starting Remote Control server...
echo.
python run_server.py
echo.
echo Server stopped.
pause
