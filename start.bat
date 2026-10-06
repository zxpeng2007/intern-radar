@echo off
rem Windows one-click start: build the page (first run takes about a minute), then serve it.
rem To keep it running in the background and refreshing every 3 hours, run instead:
rem     powershell -NoProfile -ExecutionPolicy Bypass -File setup\windows_tasks.ps1
cd /d "%~dp0"
where python >nul 2>nul || (echo Python was not found. Install it from https://python.org and tick "Add to PATH". & pause & exit /b 1)
python autorefresh.py
echo.
echo Opening http://localhost:8765 - leave this window open to keep the site up.
start "" http://localhost:8765
python serve.py
pause
