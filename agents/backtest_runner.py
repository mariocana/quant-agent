"""Backtest Runner — esegue MT5 Strategy Tester via Python API e parsa i risultati.

Nota: la libreria MetaTrader5 di Python non espone direttamente lo Strategy Tester.
Per eseguire backtest automatizzati usiamo il terminal command-line con file .ini di configurazione.
"""
import subprocess
import time
import re
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Optional
from loguru import logger

try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 module not installed — backtest runner in stub mode")


@dataclass
class BacktestResult:
    """Metriche estratte dal report di Strategy Tester."""
    initial_deposit: float
    final_balance: float
    net_profit: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_money: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    report_html_path: Optional[str] = None
    report_xml_path: Optional[str] = None


class BacktestRunner:
    def __init__(self, mt5_path: str, mt5_login: int, mt5_password: str, mt5_server: str):
        self.mt5_path = Path(mt5_path)
        self.mt5_login = mt5_login
        self.mt5_password = mt5_password
        self.mt5_server = mt5_server
        self._initialized = False
    
    def _ensure_mt5(self):
        if not MT5_AVAILABLE:
            raise RuntimeError("MetaTrader5 Python package not installed")
        if not self._initialized:
            if not mt5.initialize(
                path=str(self.mt5_path),
                login=self.mt5_login,
                password=self.mt5_password,
                server=self.mt5_server,
            ):
                raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
            self._initialized = True
    
    def compile_ea(self, mq5_path: Path) -> tuple[bool, str]:
        """Compila un .mq5 usando metaeditor64.exe. Ritorna (success, errors_text)."""
        metaeditor = self.mt5_path.parent / "metaeditor64.exe"
        if not metaeditor.exists():
            return False, f"metaeditor64.exe not found at {metaeditor}"
        
        log_file = mq5_path.with_suffix(".log")
        cmd = [
            str(metaeditor),
            f"/compile:{mq5_path}",
            f"/log:{log_file}",
        ]
        
        logger.info(f"🔨 Compiling {mq5_path.name}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        
        # MetaEditor returns 0 on success even with warnings; check log
        if log_file.exists():
            # Prova diverse encoding (utf-16 più comune, ma alcune versioni usano utf-8)
            log_content = ""
            for enc in ("utf-16", "utf-16-le", "utf-8", "cp1252"):
                try:
                    log_content = log_file.read_text(encoding=enc, errors="ignore")
                    if log_content and "error" in log_content.lower():
                        break
                except Exception:
                    continue
            
            if "0 error(s)" in log_content or "Result: 0 errors" in log_content:
                logger.success(f"✅ Compiled: {mq5_path.with_suffix('.ex5').name}")
                return True, log_content
            else:
                # Estrai e logga le righe di errore per diagnosi
                error_lines = [
                    line.strip() for line in log_content.split("\n")
                    if "error" in line.lower() or ": '" in line
                ][:15]  # max 15 righe
                
                logger.error(f"❌ Compile errors in {mq5_path.name}:")
                for line in error_lines:
                    if line:
                        logger.error(f"   {line}")
                
                return False, log_content
        
        # Fallback: nessun log file scritto
        logger.error(f"❌ Compile failed: no log file produced")
        return result.returncode == 0, result.stdout + result.stderr
    
    def run_backtest(
        self,
        ea_name: str,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
        deposit: float,
        leverage: int = 100,
        params: Optional[dict] = None,
        set_file: Optional[Path] = None,           # NUOVO: percorso a .set per modalità SPEC
    ) -> BacktestResult:
        """
        Esegue un backtest e ritorna i risultati parsati.
        
        Se set_file è fornito, viene applicato all'EA (modalità SPEC con framework).
        """
        if not MT5_AVAILABLE:
            logger.warning("MT5 not available — returning stub result")
            return self._stub_result(deposit)
        
        # Genera tester.ini
        ini_content = self._build_tester_ini(
            ea_name=ea_name,
            symbol=symbol,
            timeframe=timeframe,
            date_from=date_from,
            date_to=date_to,
            deposit=deposit,
            leverage=leverage,
            params=params or {},
            set_file=set_file,
        )
        
        ini_path = self.mt5_path.parent / "config" / f"tester_{ea_name}.ini"
        ini_path.parent.mkdir(parents=True, exist_ok=True)
        ini_path.write_text(ini_content, encoding="utf-16")
        
        report_path = self.mt5_path.parent / "MQL5" / "Reports" / f"{ea_name}_report.xml"
        
        # Lancia MT5 in modalità tester
        cmd = [str(self.mt5_path), f"/config:{ini_path}", "/portable"]
        logger.info(f"🚀 Launching backtest: {ea_name} on {symbol} {timeframe}")
        
        process = subprocess.Popen(cmd)
        
        # Attendi report (max 30 minuti)
        timeout = 1800
        start = time.time()
        while not report_path.exists() and (time.time() - start) < timeout:
            time.sleep(5)
        
        process.terminate()
        
        if not report_path.exists():
            raise TimeoutError(f"Backtest timeout for {ea_name}")
        
        return self._parse_report(report_path)
    
    def _build_tester_ini(self, **kwargs) -> str:
        """Costruisce il contenuto del file .ini per il tester MT5."""
        set_file_line = ""
        if kwargs.get("set_file"):
            set_path = Path(kwargs["set_file"]).resolve()
            set_file_line = f"\nExpertParameters={set_path}"
        
        return f"""[Tester]
Expert={kwargs['ea_name']}{set_file_line}
Symbol={kwargs['symbol']}
Period={kwargs['timeframe']}
Optimization=0
Model=1
FromDate={kwargs['date_from'].strftime('%Y.%m.%d')}
ToDate={kwargs['date_to'].strftime('%Y.%m.%d')}
ForwardMode=0
Deposit={kwargs['deposit']}
Currency=USD
ProfitInPips=0
Leverage={kwargs['leverage']}
ExecutionMode=0
ShutdownTerminal=1
Report={kwargs['ea_name']}_report
ReplaceReport=1
"""
    
    def _parse_report(self, report_path: Path) -> BacktestResult:
        """Parsa il report XML di MT5 ed estrae le metriche."""
        # MT5 può generare report in HTML o XML; qui assumiamo XML
        # (Implementazione semplificata — in produzione usare lxml)
        content = report_path.read_text(encoding="utf-16", errors="ignore")
        
        def extract(pattern: str, default: float = 0.0) -> float:
            match = re.search(pattern, content)
            if match:
                try:
                    return float(match.group(1).replace(" ", "").replace(",", ""))
                except ValueError:
                    return default
            return default
        
        return BacktestResult(
            initial_deposit=extract(r"Initial Deposit[:\s]+([0-9.,]+)"),
            final_balance=extract(r"Total Net Profit[:\s]+([0-9.,]+)") + extract(r"Initial Deposit[:\s]+([0-9.,]+)"),
            net_profit=extract(r"Total Net Profit[:\s]+(-?[0-9.,]+)"),
            profit_factor=extract(r"Profit Factor[:\s]+([0-9.,]+)"),
            sharpe_ratio=extract(r"Sharpe Ratio[:\s]+([0-9.,]+)"),
            max_drawdown_money=extract(r"Maximal Drawdown[:\s]+([0-9.,]+)"),
            max_drawdown_pct=extract(r"Maximal Drawdown[:\s]+[0-9.,]+\s*\(([0-9.]+)%\)"),
            total_trades=int(extract(r"Total Trades[:\s]+([0-9]+)")),
            winning_trades=int(extract(r"Profit Trades[:\s]+([0-9]+)")),
            losing_trades=int(extract(r"Loss Trades[:\s]+([0-9]+)")),
            win_rate=extract(r"Profit Trades[:\s]+[0-9]+\s*\(([0-9.]+)%\)") / 100,
            avg_win=extract(r"Average profit trade[:\s]+([0-9.,]+)"),
            avg_loss=extract(r"Average loss trade[:\s]+(-?[0-9.,]+)"),
            largest_win=extract(r"Largest profit trade[:\s]+([0-9.,]+)"),
            largest_loss=extract(r"Largest loss trade[:\s]+(-?[0-9.,]+)"),
            max_consecutive_wins=int(extract(r"Maximum consecutive wins[^0-9]+([0-9]+)")),
            max_consecutive_losses=int(extract(r"Maximum consecutive losses[^0-9]+([0-9]+)")),
            report_xml_path=str(report_path),
        )
    
    def _stub_result(self, deposit: float) -> BacktestResult:
        """Risultato fittizio per testing senza MT5."""
        return BacktestResult(
            initial_deposit=deposit,
            final_balance=deposit * 1.1,
            net_profit=deposit * 0.1,
            profit_factor=1.8,
            sharpe_ratio=1.4,
            max_drawdown_money=deposit * 0.05,
            max_drawdown_pct=5.0,
            total_trades=85,
            winning_trades=48,
            losing_trades=37,
            win_rate=0.56,
            avg_win=120.0,
            avg_loss=-65.0,
            largest_win=450.0,
            largest_loss=-180.0,
            max_consecutive_wins=8,
            max_consecutive_losses=4,
        )
