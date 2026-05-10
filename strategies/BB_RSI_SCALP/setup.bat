@echo off
REM ═══════════════════════════════════════════════════
REM  Prop Bot — Setup Miniconda (Windows)
REM ═══════════════════════════════════════════════════
REM  Esegui questo script da Anaconda Prompt o
REM  Miniconda Prompt.
REM ═══════════════════════════════════════════════════

echo.
echo ========================================
echo   Prop Bot - Setup Miniconda
echo ========================================
echo.

REM Controlla se conda e' disponibile
where conda >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERRORE] Conda non trovato.
    echo Installa Miniconda da: https://docs.conda.io/en/latest/miniconda.html
    echo Poi riesegui questo script da Anaconda Prompt.
    pause
    exit /b 1
)

REM Rimuovi ambiente esistente se presente
echo [1/4] Rimozione ambiente precedente (se esiste)...
conda env remove -n prop_bot -y 2>nul

REM Crea l'ambiente dal file yml
echo [2/4] Creazione ambiente conda "prop_bot"...
conda env create -f environment.yml

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERRORE] Creazione ambiente fallita.
    echo Prova manualmente:
    echo   conda create -n prop_bot python=3.11 -y
    echo   conda activate prop_bot
    echo   pip install MetaTrader5 numpy pandas requests schedule pytz
    pause
    exit /b 1
)

REM Attiva l'ambiente
echo [3/4] Attivazione ambiente...
call conda activate prop_bot

REM Verifica installazione
echo [4/4] Verifica dipendenze...
python -c "import MetaTrader5; import pandas; import numpy; print('Tutte le dipendenze OK')"

if %ERRORLEVEL% NEQ 0 (
    echo [WARN] Alcune dipendenze mancanti, installazione manuale...
    pip install MetaTrader5>=5.0.45 numpy pandas requests schedule pytz
)

echo.
echo ========================================
echo   Setup completato!
echo ========================================
echo.
echo   Per attivare l'ambiente:
echo     conda activate prop_bot
echo.
echo   Per il backtest:
echo     python backtester.py --strategy BB_RSI_SCALP
echo.
echo   Per il bot live:
echo     python bot.py
echo.
pause
