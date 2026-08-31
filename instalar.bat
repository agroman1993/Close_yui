@echo off
REM Instalador de Close Yui para Windows.
REM Lanza el script de PowerShell con permiso de ejecucion solo para este
REM fichero (no cambia tu politica global).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
echo.
pause
