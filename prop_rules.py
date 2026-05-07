"""Definizioni delle regole prop firm — fonte di verità per validazione."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PropRules:
    name: str
    max_daily_dd_pct: float
    max_total_dd_pct: float
    profit_target_pct: Optional[float]
    min_trading_days: int
    news_block_minutes: int
    hedging_allowed: bool
    max_requests_per_day: Optional[int] = None
    strategy_consistency_required: bool = False
    notes: list[str] = field(default_factory=list)


PROP_FIRMS = {
    "ftmo": {
        "free_trial": PropRules(
            name="FTMO Free Trial",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=5.0,         # Free Trial = target ridotto rispetto a Challenge
            min_trading_days=4,
            news_block_minutes=2,
            hedging_allowed=False,
            max_requests_per_day=2000,
            notes=[
                "Versione gratuita per testare la piattaforma",
                "Target ridotto al 5% (vs 10% Challenge)",
                "Stesse regole di drawdown della Challenge",
                "Nessun reward, solo testing strategia",
            ],
        ),
        "challenge": PropRules(
            name="FTMO Challenge",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=10.0,
            min_trading_days=4,
            news_block_minutes=2,
            hedging_allowed=False,
            max_requests_per_day=2000,
            notes=[
                "Vietato hedging cross-account o posizioni opposte su correlati",
                "Max 2000 server requests/giorno per evitare flag hyperactivity",
                "News restriction 2 min prima/dopo high-impact in challenge",
            ],
        ),
        "verification": PropRules(
            name="FTMO Verification",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=5.0,
            min_trading_days=4,
            news_block_minutes=2,
            hedging_allowed=False,
            max_requests_per_day=2000,
        ),
        "funded": PropRules(
            name="FTMO Funded",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=None,
            min_trading_days=0,
            news_block_minutes=2,
            hedging_allowed=False,
            max_requests_per_day=2000,
            notes=[
                "Posizioni vanno chiuse prima del weekend (no swing)",
                "News rule: 2 min restriction su strumenti targeted",
            ],
        ),
    },
    "fundednext": {
        "phase1": PropRules(
            name="FundedNext Stellar 2-Step Phase 1",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=8.0,
            min_trading_days=5,
            news_block_minutes=5,
            hedging_allowed=True,
            strategy_consistency_required=True,
            notes=[
                "Stessa strategia obbligatoria challenge → funded",
                "Trade entro 5 min news high-impact = solo 40% profit conta",
                "EA permessi solo MT4/MT5 (no cTrader, no Match-Trader)",
            ],
        ),
        "phase2": PropRules(
            name="FundedNext Stellar 2-Step Phase 2",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=5.0,
            min_trading_days=5,
            news_block_minutes=5,
            hedging_allowed=True,
            strategy_consistency_required=True,
        ),
        "stellar1step": PropRules(
            name="FundedNext Stellar 1-Step",
            max_daily_dd_pct=3.0,
            max_total_dd_pct=6.0,
            profit_target_pct=10.0,
            min_trading_days=2,
            news_block_minutes=5,
            hedging_allowed=True,
            strategy_consistency_required=True,
        ),
        "funded": PropRules(
            name="FundedNext Funded",
            max_daily_dd_pct=5.0,
            max_total_dd_pct=10.0,
            profit_target_pct=None,
            min_trading_days=0,
            news_block_minutes=5,
            hedging_allowed=True,
            strategy_consistency_required=True,
        ),
    },
}


def get_rules(firm: str, phase: str) -> PropRules:
    """Ritorna le regole per una specifica prop+phase."""
    if firm not in PROP_FIRMS:
        raise ValueError(f"Unknown prop firm: {firm}")
    if phase not in PROP_FIRMS[firm]:
        raise ValueError(f"Unknown phase {phase} for {firm}")
    return PROP_FIRMS[firm][phase]
