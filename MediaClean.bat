@echo off
title MediaClean
cd /d "%~dp0"

:: Prefer local virtual environment when available
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" main.py
    goto :end
)

:: Try python, then python3, then py launcher
where python >nul 2>&1
if %errorlevel%==0 (
    python main.py
    goto :end
)

where python3 >nul 2>&1
if %errorlevel%==0 (
    python3 main.py
    goto :end
)

where py >nul 2>&1
if %errorlevel%==0 (
    py main.py
    goto :end
)

echo.
echo ERROR: No se encontro Python en el sistema.
echo Instala Python desde https://www.python.org/downloads/
echo Asegurate de marcar "Add Python to PATH" durante la instalacion.
echo.
pause

:end
