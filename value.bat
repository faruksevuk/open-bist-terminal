@echo off
REM PE/PB (value faktoru) doldur - RESUME-SAFE. fast_info kirilgan; olursa tekrar calistir, devam eder.
REM Kurulum bir kez yapilmis olmali (start.bat/start.sh). Activation gerektirmez.
cd /d "%~dp0backend"
".venv\Scripts\python.exe" scripts\populate_value.py
pause
