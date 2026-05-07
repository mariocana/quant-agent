"""Walk-Forward Analyzer — testa la robustezza out-of-sample della strategia.

Idea: divide il periodo storico in N segmenti, su ognuno fa
in-sample (ottimizza) + out-of-sample (testa). Una strategia robusta
mantiene performance simili dentro/fuori sample.
"""
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Callable
from loguru import logger


@dataclass
class WalkForwardResult:
    n_splits: int
    in_sample_avg_pf: float
    out_sample_avg_pf: float
    consistency_score: float       # 0-1, quanto out-sample assomiglia a in-sample
    splits_passed: int             # Quanti split hanno mantenuto pf > 1.3 OOS
    individual_results: list[dict]


class WalkForwardAnalyzer:
    def __init__(self, n_splits: int = 5, train_ratio: float = 0.7):
        self.n_splits = n_splits
        self.train_ratio = train_ratio
    
    def analyze(
        self,
        backtest_func: Callable,        # Funzione che esegue 1 backtest
        ea_name: str,
        symbol: str,
        timeframe: str,
        date_from: datetime,
        date_to: datetime,
        deposit: float,
    ) -> WalkForwardResult:
        """
        Esegue walk-forward analysis su N segmenti rolling.
        
        Per ogni split:
        1. Train period: backtest "in-sample" (qui useremmo ottimizzazione MT5)
        2. Test period: backtest "out-of-sample" con stessi parametri
        3. Confronta profit factor in/out
        """
        total_days = (date_to - date_from).days
        split_days = total_days // self.n_splits
        train_days = int(split_days * self.train_ratio)
        test_days = split_days - train_days
        
        logger.info(
            f"📊 Walk-forward: {self.n_splits} splits, "
            f"{train_days}d train + {test_days}d test each"
        )
        
        results = []
        for i in range(self.n_splits):
            split_start = date_from + timedelta(days=i * split_days)
            train_end = split_start + timedelta(days=train_days)
            test_end = train_end + timedelta(days=test_days)
            
            try:
                # In-sample
                in_sample = backtest_func(
                    ea_name=ea_name,
                    symbol=symbol,
                    timeframe=timeframe,
                    date_from=split_start,
                    date_to=train_end,
                    deposit=deposit,
                )
                
                # Out-of-sample
                out_sample = backtest_func(
                    ea_name=ea_name,
                    symbol=symbol,
                    timeframe=timeframe,
                    date_from=train_end,
                    date_to=test_end,
                    deposit=deposit,
                )
                
                results.append({
                    "split": i + 1,
                    "in_sample_period": (split_start.isoformat(), train_end.isoformat()),
                    "out_sample_period": (train_end.isoformat(), test_end.isoformat()),
                    "in_sample_pf": in_sample.profit_factor,
                    "out_sample_pf": out_sample.profit_factor,
                    "in_sample_dd": in_sample.max_drawdown_pct,
                    "out_sample_dd": out_sample.max_drawdown_pct,
                    "out_sample_trades": out_sample.total_trades,
                })
                logger.info(
                    f"  Split {i+1}: IS pf={in_sample.profit_factor:.2f}, "
                    f"OOS pf={out_sample.profit_factor:.2f}"
                )
            except Exception as e:
                logger.error(f"  Split {i+1} failed: {e}")
                results.append({"split": i + 1, "error": str(e)})
        
        # Calcola metriche aggregate
        valid = [r for r in results if "error" not in r]
        if not valid:
            return WalkForwardResult(
                n_splits=self.n_splits,
                in_sample_avg_pf=0,
                out_sample_avg_pf=0,
                consistency_score=0,
                splits_passed=0,
                individual_results=results,
            )
        
        is_avg = sum(r["in_sample_pf"] for r in valid) / len(valid)
        oos_avg = sum(r["out_sample_pf"] for r in valid) / len(valid)
        
        # Consistency: oos_pf / is_pf, clipped 0-1
        consistency = min(oos_avg / is_avg, 1.0) if is_avg > 0 else 0
        
        passed = sum(1 for r in valid if r["out_sample_pf"] > 1.3)
        
        return WalkForwardResult(
            n_splits=self.n_splits,
            in_sample_avg_pf=round(is_avg, 3),
            out_sample_avg_pf=round(oos_avg, 3),
            consistency_score=round(consistency, 3),
            splits_passed=passed,
            individual_results=results,
        )
