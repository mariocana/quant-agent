"""DataseaClient — inventory of the datasea gold data lake.

datasea has no query CLI (it's a library), so we run scan_gold.py (which mirrors
workbench.py::scan_gold) with the workbench python and parse its JSON. This tells
the agent what symbols / timeframes / date ranges actually exist before it asks
algo_framework to backtest on them.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from adapters import env_bridge
from adapters.env_bridge import ToolError

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("adapters.datasea_client")

_SCAN_SCRIPT = str(Path(__file__).parent / "scan_gold.py")
_INV_PREFIX = "GOLD_INVENTORY:"

# datasea stores '5m'; strategies/CLIs use 'M5'. Accept either when filtering.
_TF_TO_DATASEA = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m",
                  "H1": "1h", "H4": "4h", "D1": "1d"}


def to_datasea_tf(tf: str) -> str:
    """'M5' -> '5m'; already-datasea styles pass through."""
    return _TF_TO_DATASEA.get(tf, tf)


class DataseaClient:
    def __init__(
        self,
        data_root: str,
        python_exec: str = "python",
        conda_env: Optional[str] = None,
        timeout_s: float = 300,
    ):
        self.data_root = str(data_root)
        self.python_exec = python_exec
        self.conda_env = conda_env or None
        self.timeout = timeout_s

    def list_available(self, symbol: Optional[str] = None,
                       timeframe: Optional[str] = None) -> list[dict]:
        """Inventory of the gold lake, optionally filtered by symbol/timeframe.
        Each item: {table, timeframe, symbol, bars, start, end, spread}."""
        res = env_bridge.run(
            [_SCAN_SCRIPT, self.data_root],
            cwd=Path(_SCAN_SCRIPT).parent, timeout=self.timeout,
            python_exec=self.python_exec, conda_env=self.conda_env,
        )
        if not res.ok:
            raise ToolError(f"datasea scan failed:\n{res.stderr[-2000:]}")
        rows = _parse_inventory(res.stdout)
        if rows is None:
            raise ToolError(f"could not parse gold inventory from:\n{res.stdout[-2000:]}")
        return _filter(rows, symbol, timeframe)

    def has(self, symbol: str, timeframe: str) -> bool:
        """True if that symbol+timeframe exists in gold (timeframe in either style)."""
        return bool(self.list_available(symbol=symbol, timeframe=timeframe))

    def health(self) -> dict:
        """Summary: symbol/timeframe counts and total bars across the lake."""
        rows = self.list_available()
        return {
            "data_root": self.data_root,
            "tables": sorted({r["table"] for r in rows}),
            "symbols": sorted({r["symbol"] for r in rows}),
            "timeframes": sorted({r["timeframe"] for r in rows}),
            "entries": len(rows),
            "total_bars": sum(r.get("bars", 0) for r in rows),
        }


def _parse_inventory(stdout: str) -> Optional[list[dict]]:
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith(_INV_PREFIX):
            return json.loads(s[len(_INV_PREFIX):])
    return None


def _filter(rows: list[dict], symbol: Optional[str],
            timeframe: Optional[str]) -> list[dict]:
    out = rows
    if symbol:
        out = [r for r in out if r["symbol"] == symbol]
    if timeframe:
        tf = to_datasea_tf(timeframe)
        out = [r for r in out if r["timeframe"] == tf]
    return out
