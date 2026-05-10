@echo off
REM ═══════════════════════════════════════════════════
REM  Confronto tutte le strategie
REM ═══════════════════════════════════════════════════
call conda activate prop_bot
echo.
echo Avvio confronto completo (4 strategie)...
echo.
python backtester.py --export
echo.
pause
