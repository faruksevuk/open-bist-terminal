@echo off
REM Open BIST Terminal - skorlari yeniden hesapla + scores tablosuna yaz (dashboard'u tazeler).
REM Kurulum bir kez yapilmis olmali (start.bat/start.sh). Activation gerektirmez.
"%~dp0backend\.venv\Scripts\python.exe" "%~dp0backend\scripts\run_scoring.py"
pause
