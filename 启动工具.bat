@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python is not installed or is not available in PATH.
  echo Please install Python 3.10 or newer, then try again.
  pause
  exit /b 1
)
python -c "import PIL" >nul 2>&1
if errorlevel 1 (
  echo Installing the required image component...
  python -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 (
    echo Installation failed. Please check the network connection.
    pause
    exit /b 1
  )
)
start "" pythonw "%~dp0eink_cover_maker.py"
