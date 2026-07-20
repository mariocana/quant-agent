"""Layer 2 — Tool adapters.

Thin wrappers over the user's mature tools (algo_framework, datasea). The agent
does NOT import those tools directly: it runs their CLIs as subprocesses and
consumes the machine-readable JSON they export (the `--export-json` contract).

Modules:
  env_bridge            — subprocess runner + JSON_EXPORT parsing (shared plumbing)
  algo_framework_client — list_strategies / run_backtest / run_robustness
  datasea_client        — gold data inventory
"""
from adapters.env_bridge import CommandResult, ToolError, run, read_json_result
from adapters.algo_framework_client import AlgoFrameworkClient
from adapters.datasea_client import DataseaClient

__all__ = [
    "CommandResult", "ToolError", "run", "read_json_result",
    "AlgoFrameworkClient", "DataseaClient",
]
