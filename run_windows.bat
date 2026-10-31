@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  call install_windows.bat
  if errorlevel 1 exit /b 1
)
if not exist data mkdir data
set HOST=127.0.0.1
set PORT=8001
echo.
echo Starting SAN CHUNG KHOAN - FULL WEB EDITION...
echo Open http://127.0.0.1:8001 in Chrome or Edge.
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8001
