@echo off
REM KAP aciklamalarini cek + Gemini ile yorumla (haber faktoru) + skorlari yenile.
REM Kurulum yapilmis + AI anahtari Ayarlar'dan (istege bagli). Activation gerektirmez.
cd /d "%~dp0backend"
".venv\Scripts\python.exe" scripts\poll_kap.py
".venv\Scripts\python.exe" scripts\run_scoring.py
pause
