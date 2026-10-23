@echo off
title CNSlicer — Subtractive Studio
cd /d "%~dp0"
if exist "dist\CNSlicer\CNSlicer.exe" (
    start "" "dist\CNSlicer\CNSlicer.exe"
) else (
    ".venv\Scripts\python.exe" desktop_app.py
)
exit
