@echo off
REM Open BIST Terminal - GELISTIRICI modu (hot-reload: next dev + uvicorn --reload).
REM Son kullanici icin start.bat (production) kullan.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" -Dev
pause
