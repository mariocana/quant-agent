"""Telegram notifier — manda alert quando il sistema trova candidati EA."""
import httpx
from loguru import logger


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
    
    def send(self, message: str, parse_mode: str = "Markdown") -> bool:
        if not self.enabled:
            return False
        try:
            response = httpx.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": parse_mode,
                },
                timeout=10,
            )
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False
    
    def notify_candidate(
        self,
        ea_name: str,
        profile_name: str,
        symbol: str,
        score: float,
        verdict: str,
        backtest,
        wf_consistency: float = None,
        dashboard_url: str = "http://localhost:8000",
    ):
        emoji = {"APPROVE": "✅", "REVIEW": "🟡", "REJECT": "❌"}.get(verdict, "❓")
        
        wf_text = f"\n*Walk-forward:* {wf_consistency:.2f}" if wf_consistency else ""
        
        message = f"""
{emoji} *EA Candidato Pronto*

*Nome:* `{ea_name}`
*Profilo:* {profile_name}
*Simbolo:* {symbol}
*Verdetto:* *{verdict}*
*Score:* {score}/100

📊 *Metriche Backtest:*
• Profit factor: `{backtest.profit_factor:.2f}`
• Sharpe: `{backtest.sharpe_ratio:.2f}`
• Max DD: `{backtest.max_drawdown_pct:.2f}%`
• Trades: `{backtest.total_trades}` (WR `{backtest.win_rate*100:.1f}%`)
• Max consec losses: `{backtest.max_consecutive_losses}`{wf_text}

🔗 [Apri dashboard]({dashboard_url}/candidates/{ea_name})

_Approva o rifiuta dal dashboard prima del deploy._
"""
        return self.send(message)
    
    def notify_cycle_summary(
        self,
        cycle_n: int,
        generated: int,
        compiled: int,
        backtested: int,
        candidates: int,
        duration_min: float,
    ):
        message = f"""
🔄 *Ciclo #{cycle_n} completato*

⏱  Durata: `{duration_min:.1f} min`

📊 *Pipeline:*
• Strategie generate: `{generated}`
• Compilate OK: `{compiled}`
• Backtest eseguiti: `{backtested}`
• 🎯 *Candidati trovati: `{candidates}`*

_Prossimo ciclo schedulato._
"""
        return self.send(message)
    
    def notify_error(self, error: str, component: str = "system"):
        message = f"""
🚨 *Errore in {component}*

```
{error[:500]}
```
"""
        return self.send(message)
