"""StrategyResearcher — decides the next experiment(s) to run.

New-architecture version: instead of authoring strategy CODE, it picks WHICH
existing strategy to run, on WHICH symbol/timeframe, with WHICH param overrides,
and WHY. The output is a list of ExperimentPlan objects that ResearchRunner can
execute directly.

Grounding is the point: every proposal is validated against reality —
  - strategy must exist in the registry,
  - (symbol, timeframe) must exist in the datasea gold inventory (table inferred),
  - param keys must belong to the strategy's default_config,
so the researcher can't propose an experiment that would ERROR or fail-hard.

The LLM call is injectable (complete_fn) so the grounding/parse logic is testable
without an API key. (Supersedes the legacy code-authoring researcher, removed in
Phase 3. Authoring brand-new strategies is StrategyAuthor's job, still to come.)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from agents.research_runner import ExperimentPlan, span_months, fit_wf

try:
    from loguru import logger
except ModuleNotFoundError:  # pragma: no cover
    import logging
    logger = logging.getLogger("agents.researcher")


@dataclass
class ResearchContext:
    strategies: list[str]                        # registry names
    inventory: list[dict]                        # datasea gold rows
    default_configs: dict[str, dict] = field(default_factory=dict)  # strategy -> default_config
    history: list[dict] = field(default_factory=list)               # recent experiment summaries
    strategy_symbols: dict[str, list] = field(default_factory=dict) # strategy -> declared symbols()

    @classmethod
    def build(cls, algo, sea, history=None, with_configs=True,
              only_ai=True) -> "ResearchContext":
        """Gather the live context from the adapters. only_ai restricts the
        existing-strategy pool to AI-generated ones (name prefix AI_), so the
        agent never experiments on the user's own strategies."""
        from adapters.env_bridge import ToolError
        strategies = algo.list_strategies()
        if only_ai:
            strategies = [s for s in strategies if s.upper().startswith("AI_")]
        inventory = [r for r in sea.list_available()
                     if not str(r.get("symbol", "")).startswith("(error")]
        configs: dict[str, dict] = {}
        symbols: dict[str, list] = {}
        if with_configs:
            for s in strategies:
                try:
                    info = algo.get_strategy_info(s)
                    configs[s] = info.get("default_config", {})
                    symbols[s] = info.get("symbols", [])
                except ToolError:
                    configs[s], symbols[s] = {}, []
        return cls(strategies, inventory, configs, history or [], symbols)


SYSTEM_PROMPT = """You are a senior quantitative researcher at a prop firm. Your
job is NOT to write code: it is to decide the NEXT experiment to run, choosing
among tools that already exist.

You receive: the available strategies (with their tunable config keys), the data
actually present (symbol + timeframe + span), and the recent history with its
verdicts. You must propose RUNNABLE and SENSIBLE experiments.

Hard rules:
- Use ONLY strategies from the provided list.
- Use ONLY (symbol, timeframe) pairs present in the inventory.
- An EXISTING strategy must be used ONLY on a symbol it DECLARES (see symbols=...).
  Do not pair a strategy with a symbol outside its own.
- In params use ONLY keys present in THAT strategy's config.
- Learn from history: do not re-test identical combos already REJECTed; on
  REVIEW/promising combos try parameter variations; explore symbols/TFs not yet
  covered.
- Prefer experiments with a clear thesis (regime, timeframe, parameter).

The "available strategies" are ONLY those the agent generated (AI_*). If the list
is empty, you MUST propose type (B) experiments to create new ones.

Reply ONLY with a JSON array (no surrounding text). Two element types:

A) Experiment on an EXISTING strategy:
{
  "strategy": "EXACT_NAME_FROM_LIST",
  "symbol": "EXACT_SYMBOL",
  "timeframe": "EXACT_TF",
  "params": {"key": value},       // optional; {} to use defaults
  "rationale": "1-2 sentences"
}

B) NEW strategy to write (only if no existing one suits the regime):
{
  "strategy": "short_type",        // type hint, e.g. "mean_reversion"
  "symbol": "EXACT_SYMBOL",        // must exist in the inventory
  "timeframe": "EXACT_TF",
  "author_brief": "description of the hypothesis/logic to implement (2-4 sentences)",
  "rationale": "why a new strategy is needed here"
}
Prefer (A). Use (B) sparingly."""


class StrategyResearcher:
    def __init__(self, api_key: Optional[str] = None,
                 model: str = "claude-sonnet-4-6",
                 complete_fn: Optional[Callable[[str, str], str]] = None):
        self.api_key = api_key
        self.model = model
        self._complete_fn = complete_fn  # injectable for tests

    # ── public ────────────────────────────────────────────────────────
    def propose(self, context: ResearchContext, n: int = 1) -> list[ExperimentPlan]:
        user = self._user_prompt(context, n)
        text = self._complete(SYSTEM_PROMPT, user)
        raw = _extract_json_array(text)

        plans: list[ExperimentPlan] = []
        seen: set = set()
        for d in raw:
            p = self._ground(d, context)
            if not p:
                continue
            key = (p.strategy, p.symbol, p.timeframe,
                   json.dumps(p.params or {}, sort_keys=True),
                   json.dumps(p.author_brief, sort_keys=True, default=str) if p.author_brief else "")
            if key in seen:
                continue
            seen.add(key)
            plans.append(p)
            if len(plans) >= n:
                break
        logger.info(f"🔬 Researcher proposed {len(plans)}/{n} grounded plan(s)")
        return plans

    # ── grounding ─────────────────────────────────────────────────────
    def _ground(self, d: dict, ctx: ResearchContext) -> Optional[ExperimentPlan]:
        if not isinstance(d, dict):
            return None

        symbol, tf = d.get("symbol"), d.get("timeframe")
        row = next((r for r in ctx.inventory
                    if r.get("symbol") == symbol and r.get("timeframe") == tf), None)
        if not row:
            logger.warning(f"   dropped: no gold data for {symbol}/{tf}")
            return None

        # size the walk-forward to the data span so robustness actually runs
        wf_train, wf_test, wf_step = fit_wf(span_months(row))

        # author_new: a brief for a brand-new strategy. No registry check — the
        # strategy doesn't exist yet; 'strategy' is just a type hint. Still needs
        # real data to be tested on (grounded above).
        brief = d.get("author_brief")
        if brief:
            return ExperimentPlan(
                strategy=str(d.get("strategy") or "custom"), symbol=symbol, timeframe=tf,
                table=row.get("table"), params=None,
                rationale=str(d.get("rationale", "")).strip(), author_brief=brief,
                wf_train=wf_train, wf_test=wf_test, wf_step=wf_step,
            )

        want = str(d.get("strategy", "")).upper()
        strat = next((s for s in ctx.strategies if s.upper() == want), None)
        if not strat:
            logger.warning(f"   dropped: unknown strategy {d.get('strategy')!r}")
            return None

        # symbol-awareness: an existing strategy runs only on symbols it declares.
        # Avoids nonsensical pairings (e.g. a NASDAQ strategy on BTCUSD) that crash
        # or produce garbage. Only enforced when we know the declared symbols.
        declared = ctx.strategy_symbols.get(strat)
        if declared and symbol not in declared:
            logger.warning(f"   dropped: {symbol} not in {strat} symbols {declared}")
            return None

        params = d.get("params") or None
        if params and isinstance(params, dict):
            cfg = ctx.default_configs.get(strat)
            if cfg:  # keep only valid keys so we never trip the tool's fail-hard
                params = {k: v for k, v in params.items() if k in cfg} or None
        else:
            params = None

        return ExperimentPlan(
            strategy=strat, symbol=symbol, timeframe=tf, table=row.get("table"),
            params=params, rationale=str(d.get("rationale", "")).strip(),
            wf_train=wf_train, wf_test=wf_test, wf_step=wf_step,
        )

    # ── prompt + completion ───────────────────────────────────────────
    def _user_prompt(self, ctx: ResearchContext, n: int) -> str:
        strat_lines = []
        for s in ctx.strategies:
            keys = list((ctx.default_configs.get(s) or {}).keys())
            syms = ctx.strategy_symbols.get(s) or []
            bits = []
            if syms:
                bits.append(f"symbols={syms}")
            if keys:
                bits.append(f"params={keys}")
            strat_lines.append(f"  - {s}" + (f": {', '.join(bits)}" if bits else ""))
        if not strat_lines:
            strat_lines = ["  (no AI strategy yet — propose author_new to create some)"]
        inv_lines = [
            f"  - {r['symbol']} {r['timeframe']} (table={r['table']}, "
            f"{r.get('bars','?')} bars, {r.get('start','?')}→{r.get('end','?')})"
            for r in ctx.inventory
        ]
        hist_lines = [
            f"  - {h.get('strategy')}/{h.get('symbol')}/{h.get('timeframe')} "
            f"params={h.get('params') or {}} -> {h.get('verdict')} (score {h.get('score')})"
            for h in (ctx.history or [])[-15:]
        ] or ["  (none)"]

        # combos (strategy/symbol/tf) that ERRORed recently — broken strategy OR
        # a symbol without data/spec. Avoid the COMBO, not the whole strategy, so
        # a strategy stays usable on other symbols.
        errored = sorted({f"{h.get('strategy')}/{h.get('symbol')}/{h.get('timeframe')}"
                          for h in (ctx.history or [])[-25:]
                          if h.get("verdict") == "ERROR" and h.get("strategy")})
        avoid = (f"\n⚠️ COMBOS TO AVOID (recent ERROR — broken strategy or symbol "
                 f"without data/spec): {errored}\n" if errored else "")

        return (
            f"AVAILABLE STRATEGIES:\n" + "\n".join(strat_lines) + "\n\n"
            f"AVAILABLE DATA (gold inventory):\n" + "\n".join(inv_lines) + "\n"
            + avoid + "\n"
            f"RECENT HISTORY:\n" + "\n".join(hist_lines) + "\n\n"
            f"Propose {n} experiment(s). JSON array, nothing else."
        )

    def _complete(self, system: str, user: str) -> str:
        if self._complete_fn:
            return self._complete_fn(system, user)
        from agents.api_client import make_client, call_with_retry
        client = make_client(self.api_key, timeout_seconds=120)
        return call_with_retry(client, model=self.model, max_tokens=2000,
                               system=system, messages=[{"role": "user", "content": user}])


# ── robust JSON extraction (LLMs wrap arrays in prose / fences) ────────
def _extract_json_array(text: str) -> list[dict]:
    text = (text or "").strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = next((i for i, ln in enumerate(lines[1:], 1)
                    if ln.strip().startswith("```")), len(lines))
        text = "\n".join(lines[1:end]).strip()

    i, j = text.find("["), text.rfind("]")
    if i != -1 and j > i:
        chunk = text[i:j + 1]
    else:  # maybe a single object
        oi, oj = text.find("{"), text.rfind("}")
        chunk = text[oi:oj + 1] if (oi != -1 and oj > oi) else text

    for candidate in (chunk, _fix_json(chunk)):
        try:
            data = json.loads(candidate)
            return [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
        except (json.JSONDecodeError, TypeError):
            continue
    logger.error("could not parse researcher JSON")
    return []


def _fix_json(text: str) -> str:
    fixed = re.sub(r'(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1 "\2":', text)  # unquoted keys
    fixed = re.sub(r',(\s*[\}\]])', r'\1', fixed)                                 # trailing commas
    return fixed
