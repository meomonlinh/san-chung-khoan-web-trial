@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  call install_windows.bat
  if errorlevel 1 exit /b 1
)
if not exist data mkdir data
.venv\Scripts\python.exe bootstrap_owner.py
pause
