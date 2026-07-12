@echo off
REM Open BIST Terminal - backend/frontend'i durdur (veri bist.db'de korunur).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
pause
