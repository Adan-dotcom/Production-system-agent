@echo off
REM ─────────────────────────────────────────────────────────────────
REM  run_scale_agent.bat
REM  Agente local de báscula — Biotécnica MVP
REM  Haz doble clic para iniciar. Deja esta ventana abierta.
REM ─────────────────────────────────────────────────────────────────

REM Ir a la carpeta del .bat (funciona desde cualquier ubicación)
cd /d "%~dp0"

echo [scale] Directorio: %~dp0

REM Crear entorno virtual si no existe
if not exist "venv\Scripts\python.exe" (
    echo [scale] Creando entorno virtual...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: No se pudo crear el venv. Instala Python 3.9+ y asegurate de que este en el PATH.
        pause
        exit /b 1
    )
)

REM Activar venv
call venv\Scripts\activate.bat

REM Instalar/actualizar dependencias
echo [scale] Instalando dependencias...
pip install -q -r requirements.txt
if errorlevel 1 (
    echo ERROR: Fallo al instalar dependencias.
    pause
    exit /b 1
)

echo.
echo =========================================================
echo  AGENTE DE BASCULA BIOTECNICA
echo  URL local: http://127.0.0.1:8787
echo  Endpoints:
echo    GET  /health  — estado del agente
echo    GET  /ports   — puertos COM disponibles
echo    GET  /weight  — lectura actual
echo    POST /config  — cambiar puerto/baudrate
echo =========================================================
echo.

REM Iniciar agente
python -m uvicorn scale_agent:app --host 127.0.0.1 --port 8787

REM Si termina inesperadamente, no cerrar la ventana
echo.
echo [scale] El agente se detuvo. Presiona cualquier tecla para cerrar.
pause
