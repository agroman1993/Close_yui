@echo off
REM Arranca Close Yui guardando su salida en salida.log.
REM Se usa python.exe (no pythonw) porque asi la redireccion funciona;
REM la ventana la oculta arrancar.vbs, que es quien lanza este fichero.
cd /d "%~dp0"
echo. >> salida.log
echo ===== arranque %DATE% %TIME% ===== >> salida.log
python main.py >> salida.log 2>&1
