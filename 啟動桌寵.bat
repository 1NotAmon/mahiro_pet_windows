@echo off
rem ============================================================
rem  Desktop Pet / launcher (no console window)
rem  Run setup.ps1 first if .venv does not exist yet.
rem ============================================================
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" main.py
    exit /b 0
)
if exist ".venv\Scripts\python.exe" (
    start "" ".venv\Scripts\python.exe" main.py
    exit /b 0
)

echo [!] .venv not found. Please install dependencies first:
echo.
echo     powershell -ExecutionPolicy Bypass -File setup.ps1
echo.
pause
