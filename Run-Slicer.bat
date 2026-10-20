@echo off
title CNC Milling Slicer — Subtractive Studio
echo Starting CNC Milling Slicer Backend...
cd /d "%~dp0"
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" server\app.py
pause
