@echo off
cd /d %~dp0
if not exist .venv\Scripts\python.exe (
  py -3 -m venv .venv
  if errorlevel 1 (
    echo Khong tao duoc virtual environment. Hay cai Python 3.11 trở lên va bat dau lai.
    pause
    exit /b 1
  )
)
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Cai thu vien that bai. Kiem tra ket noi Internet va thu lai.
  pause
  exit /b 1
)
echo Cai dat hoan tat.
pause
