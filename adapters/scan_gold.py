"""Standalone gold-inventory scanner — run with the workbench python.

Mirrors algo_framework/workbench.py::scan_gold. It needs polars + deltalake
(present in the workbench env), so it runs as a subprocess in that env rather
than importing those into the orchestrator env.

Usage:
    python scan_gold.py <DATA_ROOT>
Prints one line:
    GOLD_INVENTORY:[{"table","timeframe","symbol","bars","start","end","spread"}, ...]
"""
import json
import sys
from pathlib import Path

INVENTORY_PREFIX = "GOLD_INVENTORY:"


def scan(data_root: str) -> list[dict]:
    import polars as pl
    from deltalake import DeltaTable

    gold = Path(data_root) / "gold"
    out: list[dict] = []
    if not gold.is_dir():
        return out

    for table_dir in sorted(gold.iterdir()):
        if not table_dir.is_dir():
            continue
        for tf_dir in sorted(table_dir.iterdir()):
            if not tf_dir.is_dir():
                continue
            path = str(tf_dir)
            if not DeltaTable.is_deltatable(path):
                continue
            try:
                dt = DeltaTable(path)
                available = {f.name for f in dt.schema().fields}
                cols = [c for c in ("symbol", "timestamp", "spread") if c in available]
                if "symbol" not in cols or "timestamp" not in cols:
                    continue
                df = pl.from_arrow(dt.to_pyarrow_table(columns=cols))
                if df.is_empty():
                    continue
                has_spread = "spread" in df.columns
                for sym in df["symbol"].unique().sort().to_list():
                    sub = df.filter(pl.col("symbol") == sym)
                    out.append({
                        "table": table_dir.name,
                        "timeframe": tf_dir.name,
                        "symbol": sym,
                        "bars": sub.height,
                        "start": str(sub["timestamp"].min())[:10],
                        "end": str(sub["timestamp"].max())[:10],
                        "spread": round(float(sub["spread"].mean()), 3) if has_spread else None,
                    })
            except Exception as e:  # keep going; report the broken table
                out.append({
                    "table": table_dir.name, "timeframe": tf_dir.name,
                    "symbol": f"(error: {e})", "bars": 0,
                    "start": "-", "end": "-", "spread": None,
                })
    return out


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print(INVENTORY_PREFIX + json.dumps(scan(root)))
