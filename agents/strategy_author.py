"""StrategyAuthor — write a NEW algo_framework strategy from a brief.

When the researcher (or an approved user idea) needs a strategy that doesn't
exist yet, this asks Claude to write a Python file conforming to
algo_framework's strategies/_template.py, then validates it hard before it can
ever be imported or backtested:

  1. ast.parse           — must be syntactically valid Python
  2. safety AST scan     — only whitelisted imports; no os/subprocess/eval/open/…
                           (the dry-import EXECUTES module-level code, so this
                            gate runs BEFORE it)
  3. dry-import          — load it in the workbench env and confirm it's a
                           BaseStrategy subclass that instantiates and exposes a
                           name (returns the registered name)

Only then is it written to <algo>/strategies/ai_generated/AI_<TYPE>_<NAME>_vN.py.
Existing strategies are never modified; names are versioned to avoid clobbering.

The Claude call is injectable (complete_fn) so validation/naming logic is testable
without an API key or the framework.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

from adapters import env_bridge
from adapters.env_bridge import ToolError

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("agents.strategy_author")

# Only these top-level modules may be imported by a generated strategy.
ALLOWED_IMPORTS = {"numpy", "pandas", "math", "typing", "dataclasses", "core", "__future__"}
# Names that must never appear as calls / references.
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "breakpoint", "memoryview",
}
FORBIDDEN_ATTRS = {"__globals__", "__builtins__", "__subclasses__", "__bases__",
                   "__mro__", "__dict__", "__class__", "__code__"}

_NAME_NAME = "AUTHOR_NAME:"


class AuthorError(RuntimeError):
    """The authored strategy failed generation, validation, or dry-import."""


@dataclass
class AuthoredStrategy:
    name: str            # registered strategy name (from dry-import), or class hint
    path: str            # file written under strategies/ai_generated/
    strategy_type: str
    code: str


class StrategyAuthor:
    def __init__(self, algo_dir: str, api_key: Optional[str] = None,
                 model: str = "claude-sonnet-4-6",
                 python_exec: str = "python", conda_env: Optional[str] = None,
                 complete_fn: Optional[Callable[[str, str], str]] = None):
        self.algo_dir = Path(algo_dir)
        self.api_key = api_key
        self.model = model
        self.python_exec = python_exec
        self.conda_env = conda_env or None
        self._complete_fn = complete_fn

    # ── public ────────────────────────────────────────────────────────
    def author(self, brief: Union[str, dict], strategy_type: str = "custom",
               dry_import: bool = True) -> AuthoredStrategy:
        reference = self._read_reference()
        code = _extract_code(self._complete(SYSTEM_PROMPT, self._user_prompt(brief, reference)))

        self.validate_syntax(code)          # raises AuthorError
        self.check_safety(code)             # raises AuthorError
        code = code.rstrip() + "\n"         # normalise: end with exactly one newline

        name = None
        if dry_import:
            name = self._dry_import(code)   # raises AuthorError on failure

        path = self._target_path(strategy_type, name or _class_name(code) or "STRATEGY")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        logger.info(f"✍️  authored {path.name} (name={name})")
        return AuthoredStrategy(name=name or _class_name(code) or path.stem,
                                path=str(path), strategy_type=strategy_type, code=code)

    # ── validation ────────────────────────────────────────────────────
    @staticmethod
    def validate_syntax(code: str) -> None:
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise AuthorError(f"syntax error: {e}") from e

    @staticmethod
    def check_safety(code: str) -> None:
        """Reject non-whitelisted imports and dangerous names/attrs (the code is
        about to be imported, i.e. executed)."""
        tree = ast.parse(code)
        problems: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                        problems.append(f"forbidden import: {a.name}")
            elif isinstance(node, ast.ImportFrom):
                root = (node.module or "").split(".")[0]
                if root and root not in ALLOWED_IMPORTS:
                    problems.append(f"forbidden import from: {node.module}")
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                problems.append(f"forbidden name: {node.id}")
            elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_ATTRS:
                problems.append(f"forbidden attribute: {node.attr}")
        if problems:
            raise AuthorError("safety check failed: " + "; ".join(sorted(set(problems))))

    # ── dry-import in the workbench env ───────────────────────────────
    def _dry_import(self, code: str) -> str:
        """Write a staging file, load it, confirm it's a BaseStrategy, return name.
        The staging file starts with '_' so the registry never auto-discovers it."""
        staging = self.algo_dir / "strategies" / "ai_generated" / "_staging_probe.py"
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_text(code, encoding="utf-8")
        try:
            probe = (
                "import sys, importlib.util, inspect; sys.path.insert(0, '.'); "
                "from core.base_strategy import BaseStrategy; "
                "spec = importlib.util.spec_from_file_location('ai_probe', %r); "
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
                "cls = next(c for _, c in inspect.getmembers(m, inspect.isclass) "
                "if issubclass(c, BaseStrategy) and c is not BaseStrategy "
                "and c.__module__ == 'ai_probe'); "
                "print('%s' + cls().name)" % (str(staging), _NAME_NAME)
            )
            res = env_bridge.run(["-c", probe], cwd=self.algo_dir, timeout=120,
                                 python_exec=self.python_exec, conda_env=self.conda_env)
            if not res.ok:
                raise AuthorError("dry-import failed:\n" + res.stderr[-1500:])
            for line in res.stdout.splitlines():
                if line.strip().startswith(_NAME_NAME):
                    return line.strip()[len(_NAME_NAME):]
            raise AuthorError("dry-import produced no strategy name:\n" + res.stdout[-1500:])
        finally:
            staging.unlink(missing_ok=True)

    # ── reference material for the prompt ─────────────────────────────
    def _read_reference(self) -> str:
        parts = []
        for rel in ("strategies/_template.py", "strategies/bb_rsi_aggro.py", "core/indicators.py"):
            p = self.algo_dir / rel
            if p.is_file():
                parts.append(f"# ===== {rel} =====\n{p.read_text(encoding='utf-8')}")
        if not parts:
            raise AuthorError(f"no reference files found under {self.algo_dir} "
                              "(need strategies/_template.py)")
        return "\n\n".join(parts)

    def _target_path(self, strategy_type: str, name_hint: str) -> Path:
        stype = _slug(strategy_type) or "CUSTOM"
        short = _slug(name_hint) or "STRATEGY"
        d = self.algo_dir / "strategies" / "ai_generated"
        v = 1
        while (d / f"AI_{stype}_{short}_v{v}.py").exists():
            v += 1
        return d / f"AI_{stype}_{short}_v{v}.py"

    def _user_prompt(self, brief: Union[str, dict], reference: str) -> str:
        import json
        brief_text = json.dumps(brief, ensure_ascii=False, indent=2) if isinstance(brief, dict) else str(brief)
        return (
            f"REFERENCE (template + example + available indicators):\n\n{reference}\n\n"
            f"BRIEF OF THE STRATEGY TO WRITE:\n{brief_text}\n\n"
            "Write the complete .py file. ONLY code, no text, no fences."
        )

    def _complete(self, system: str, user: str) -> str:
        if self._complete_fn:
            return self._complete_fn(system, user)
        from agents.api_client import make_client, call_with_retry
        client = make_client(self.api_key, timeout_seconds=180)
        return call_with_retry(client, model=self.model, max_tokens=4000,
                               system=system, messages=[{"role": "user", "content": user}])


SYSTEM_PROMPT = """You are a quant developer. Write a COMPLETE Python file for the
algo_framework framework, conforming EXACTLY to the provided template.

Strict requirements:
- A class that extends BaseStrategy.
- Implement: name (property, UNIQUE, UPPER_SNAKE, with prefix 'AI_'),
  default_config (property), symbols(), timeframe(), generate_signal(symbol)
  (live), generate_signals_batch(df, htf_df) which ADDS the columns
  'signal' ('BUY'/'SELL'/None), 'sl', 'tp', 'reason'.
- Stop loss ALWAYS defined (hard stop). No martingale, grid, HFT scalping.
- USE ONLY these imports: numpy, pandas, `from core import indicators as ind`,
  `from core.base_strategy import BaseStrategy, Signal`, typing, dataclasses, math.
  FORBIDDEN: os, sys, subprocess, socket, requests, open/file I/O, eval, exec.

Output: ONLY the Python code, no explanations and no ``` fences."""


# ── helpers (pure, testable) ──────────────────────────────────────────
def _extract_code(text: str) -> str:
    text = (text or "").strip()
    if "```" in text:
        m = re.search(r"```(?:python)?\s*(.*?)```", text, re.DOTALL)
        if m:
            return m.group(1).strip()
    return text


def _class_name(code: str) -> Optional[str]:
    m = re.search(r"^\s*class\s+([A-Za-z_]\w*)", code, re.MULTILINE)
    return m.group(1) if m else None


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(s)).strip("_").upper()
    # drop a leading AI_ so we don't double it in the filename
    return re.sub(r"^AI_", "", s)
