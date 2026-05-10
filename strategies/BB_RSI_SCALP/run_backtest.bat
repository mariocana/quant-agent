@echo off
REM ═══════════════════════════════════════════════════
REM  Avvia Backtest BB+RSI Scalping
REM ═══════════════════════════════════════════════════
call conda activate prop_bot
echo.
echo Avvio backtest BB_RSI_SCALP...
echo.
python backtester.py --strategy BB_RSI_SCALP --export
echo.
pause
