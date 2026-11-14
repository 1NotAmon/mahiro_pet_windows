@echo off
rem ============================================================
rem  Desktop Pet / debug launcher (keeps console, shows logs)
rem  Use this when something goes wrong and you need the output.
rem ============================================================
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py --info
    echo ------------------------------------------------------------
    ".venv\Scripts\python.exe" main.py
    goto :end
)
if exist ".venv\Scripts\pythonw.exe" (
    python main.py
    goto :end
)

echo [!] .venv not found. Please install dependencies first:
echo.
echo     powershell -ExecutionPolicy Bypass -File setup.ps1
echo.

:end
pause
