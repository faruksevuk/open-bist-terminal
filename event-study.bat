@echo off
REM Setup event study (PIT dogrulama) — her setup'i kendi verimizde bir kez dogrular.
REM setup_evidence config'e yazilir. Kurulum yapilmis olmali. Birkac dakika surebilir.
"%~dp0backend\.venv\Scripts\python.exe" "%~dp0backend\scripts\run_event_study.py" %*
pause
