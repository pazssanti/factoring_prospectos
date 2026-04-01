@echo off
:: ============================================================
::  run_morning.bat
::  Pipeline diario de prospeccion factoring Los Lagos
::
::  Ejecuta en orden:
::    1. Ingesta incremental (API + OCDS nuevos)
::    2. Transform + Score + Reporte
::    3. Prediccion de ganadores en licitaciones abiertas
::    4. Alertas OC + adjudicadas + predicciones
::
::  Configurado para correr en Administrador de Tareas Windows
::  Horario recomendado: 06:30 AM de lunes a viernes
:: ============================================================

set PYTHON=C:\Users\DELL\AppData\Local\Python\pythoncore-3.14-64\python.exe
set PROYECTO=C:\Users\DELL\factoring_prospectos
set LOG=%PROYECTO%\logs\pipeline_%DATE:~-4,4%%DATE:~-7,2%%DATE:~0,2%.txt
set PYTHONIOENCODING=utf-8

:: Crear carpeta logs si no existe
if not exist "%PROYECTO%\logs" mkdir "%PROYECTO%\logs"

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo INICIO PIPELINE: %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"

cd /d "%PROYECTO%"

:: PASO 1: Ingesta incremental (solo API + OCDS nuevos, salta CSV/SII estaticos)
echo [%TIME%] PASO 1/4 - Ingesta incremental API+OCDS... >> "%LOG%"
"%PYTHON%" -u run_pipeline.py --modo incremental >> "%LOG%" 2>&1
echo [%TIME%] Pipeline incremental completado >> "%LOG%"
goto paso3

:: PASO 2 ya lo corre --modo incremental internamente (transform+score+report)
:: Esta seccion queda como respaldo manual si se necesita correr por pasos
:paso2manual
echo [%TIME%] PASO 2/4 - Transform + Score + Excel (manual)... >> "%LOG%"
"%PYTHON%" -u run_pipeline.py --paso transform >> "%LOG%" 2>&1
"%PYTHON%" -u run_pipeline.py --paso score >> "%LOG%" 2>&1
"%PYTHON%" -u run_pipeline.py --paso report >> "%LOG%" 2>&1
echo [%TIME%] Pipeline principal completado >> "%LOG%"

:paso3

:: PASO 3: Prediccion de ganadores en licitaciones abiertas
echo [%TIME%] PASO 3/4 - Prediccion licitaciones activas... >> "%LOG%"
"%PYTHON%" -u ingesta\enriquecer_licitaciones_activas.py >> "%LOG%" 2>&1
echo [%TIME%] Predicciones completadas >> "%LOG%"

:: PASO 4: Alertas (OC + adjudicadas + predicciones)
echo [%TIME%] PASO 4/4 - Generando alertas... >> "%LOG%"
"%PYTHON%" -u run_pipeline.py --paso alertas >> "%LOG%" 2>&1
echo [%TIME%] Alertas generadas >> "%LOG%"

echo. >> "%LOG%"
echo FIN PIPELINE: %DATE% %TIME% >> "%LOG%"
echo ============================================================ >> "%LOG%"

:: Abrir la carpeta output para ver los resultados (opcional)
:: explorer "%PROYECTO%\output"
