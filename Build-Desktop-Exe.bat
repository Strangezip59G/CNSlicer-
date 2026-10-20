@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Virtual environment not found. Run the desktop setup first.
  pause
  exit /b 1
)

"%PYTHON%" -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
  echo Installing the Windows executable packager into the project environment...
  "%PYTHON%" -m pip install pyinstaller
  if errorlevel 1 (
    echo Could not install PyInstaller. Check your internet connection and try again.
    pause
    exit /b 1
  )
)

"%PYTHON%" -m PyInstaller --noconfirm --clean --distpath "%~dp0dist" --workpath "%~dp0build" "%~dp0Anime to Desmos Studio.spec"
if errorlevel 1 (
  echo Build failed. Review the messages above.
  pause
  exit /b 1
)

if not exist "%~dp0dist\Anime to Desmos Studio\api keys.txt" type nul > "%~dp0dist\Anime to Desmos Studio\api keys.txt"
echo.
echo Desktop app created at:
echo %~dp0dist\Anime to Desmos Studio\Anime to Desmos Studio.exe
echo Enter your Desmos key in the api keys.txt file beside the EXE.
pause
