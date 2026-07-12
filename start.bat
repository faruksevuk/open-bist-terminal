@echo off
REM Open BIST Terminal - cift tikla calistir.
REM PowerShell calistirma-politikasini bu cagri icin bypass eder (sistem ayari degismez).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause
