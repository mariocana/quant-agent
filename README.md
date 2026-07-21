# Quant Research Agent

Agente quant **autonomo** che fa ricerca di strategie di trading orchestrando due
tool maturi dell'utente: **algo_framework** (backtest, walk-forward, Monte Carlo) e
**datasea** (data lake gold, Delta Lake). Non reinventa il backtester e non scrive
più codice MQL5: *AI as researcher, not as coder*.

Stato: **funzionante end-to-end** — il ciclo autonomo propone esperimenti, scrive
le proprie strategie con Claude, le backtesta/valida sui dati reali e mette in
dashboard solo i candidati che passano il gate.

---

## Cosa fa (il ciclo)

```
StrategyResearcher  ── propone ExperimentPlan (grounded) ─┐
   │  (o UserIdea approvata dalla dashboard)              │
   ▼                                                      ▼
ResearchRunner ─(author_brief? → StrategyAuthor scrive AI_*.py)─▶ backtest
   │                                                      │
   │                             (se il backtest merita)  ▼
   │                                                  robustness (WF + Monte Carlo)
   ▼
ResultEvaluator  ── gate deterministico, robustness OBBLIGATORIA ──▶ APPROVE / REVIEW / REJECT
   ▼
ResearchLoop  ── history persistente + on_outcome ──▶ DB ──▶ Dashboard (approva/rifiuta)
```

Ogni ciclo l'orchestrator: (1) processa le idee utente approvate, (2) fa proporre
al researcher N esperimenti, (3) li esegue e giudica, (4) persiste gli esiti nel DB.

---

## Architettura

**Layer 2 — adapter (`adapters/`)**: parlano coi tool via subprocess, nessun import diretto.
- `env_bridge` — runner subprocess (UTF-8/timeout), parsing `JSON_EXPORT:`, `is_setup_error`
- `algo_framework_client` — `list_strategies` / `get_strategy_info` / `run_backtest` / `run_robustness` (leggono il JSON del contratto `--export-json`)
- `datasea_client` + `scan_gold` — inventory della gold (simboli/TF/date/spread)

**Layer 3 — agenti cognitivi (`agents/`)**:
- `researcher` — propone `ExperimentPlan` **ancorati** (strategia AI_*, simbolo/TF ∈ inventory, params ⊆ config, WF dimensionato sullo span); può proporre strategie nuove (`author_new`)
- `strategy_author` — da un brief Claude scrive una strategia conforme al `_template.py`, validata con `ast.parse` + safety AST + **dry-import** reale, salvata in `strategies/ai_generated/AI_*.py`
- `research_runner` — esegue un piano: (author →) backtest → (se merita) robustness → esito
- `result_evaluator` — gate deterministico su `validation_criteria` + `robustness_gate` (robustness obbligatoria per APPROVE)
- `research_loop` — il ciclo con storico persistente (`experiment_results/history.jsonl`)
- `idea_evaluator` — valuta le idee sottomesse dalla dashboard (devil's advocate)

**Orchestrator / interfaccia**:
- `orchestrator.py` — costruisce tutto e fa girare il loop (`--once` o schedulato)
- `dashboard/api.py` — FastAPI: candidati con Approva/Rifiuta, backtest, cicli, submit idee (`/ideas`)
- `db/` — SQLAlchemy (`models`, `database`) + `mapping` (ExperimentOutcome → righe DB)

**Tool a valle (`research_cli.py`, `collaudo_db.py`, `smoke_test_adapters.py`)**: giro
singolo, ispezione DB, smoke test degli adapter.

---

## Ambiente

Tre repo, **un solo conda env `workbench`** (`algo_framework/workbench-environment.yml`):
datasea è pip-installato, algo_framework gira dalla sua cartella. Prod = Windows.

Prerequisito: nel repo **algo_framework** deve essere attivo il contratto
machine-readable (`--export-json`, `--params`; branch/merge dei "contratti"),
altrimenti gli adapter falliscono.

---

## Setup

```bash
conda activate workbench
cd prop-agent-system
pip install -r requirements.txt          # deps dell'agente (leggere: no pandas/numpy/MT5)
cp config.example.yaml config.yaml       # poi compila i valori
```

`config.yaml` — sezioni principali:
- `claude.api_key` — usata da researcher, author, narrativa
- `tools` — `algo_framework_dir`, `datasea_data_root`, `datasea_table`, `python_exec`, `conda_env`
- `orchestrator` — `cycle_hours`, `max_experiments_per_cycle`, `only_ai_strategies`
- `validation_criteria` + `robustness_gate` — le soglie del gate
- `prop` — contesto prop firm per l'idea evaluator

---

## Uso

```bash
# Un ciclo di prova
python orchestrator.py --once

# Loop schedulato h24
python orchestrator.py

# Dashboard (candidati, backtest, cicli, idee)
uvicorn dashboard.api:app --host 0.0.0.0 --port 8000     # http://localhost:8000

# Ispezione DB senza dashboard
python collaudo_db.py

# Un singolo esperimento su una strategia specifica (console, salva JSON)
python research_cli.py --strategy NAME --symbol SYM --tf TF \
    --algo-dir ... --datasea ... --table ...

# Smoke test degli adapter sui dati veri
python smoke_test_adapters.py --algo-dir ... --datasea ... --table ...
```

---

## Garanzie

- 🔒 **Non tocca le tue strategie.** Con `only_ai_strategies: true` (default) l'agente
  lavora SOLO sulle strategie che genera lui (`AI_*`); se non ce ne sono, le crea.
  Scrive esclusivamente in `strategies/ai_generated/AI_*.py` (versionate, mai
  overwrite/delete). Backtestare è read-only (params a runtime).
- 🛡️ **Robustness obbligatoria** per un APPROVE — difesa contro l'over-ottimizzazione.
- 🧪 **Sicurezza dell'author**: `ast.parse` + whitelist import (no os/subprocess/eval/
  open) + dry-import reale prima che una strategia generata possa girare.
- ♻️ **Impara**: storico persistente; evita le combo (strategia/simbolo) che danno ERROR.

---

## Stato & TODO

Fatto e collaudato live: adapter, researcher, author (Claude scrive strategie
valide), runner, evaluator, loop, orchestrator, persistenza DB, dashboard.

Da fare / migliorabile:
- [ ] flusso `/ideas` end-to-end e run schedulato h24 (check live)
- [ ] **data engineer datasea**: ingestione dei dati mancanti (oggi l'agente solo *legge* la gold) — l'unica metà del design ancora assente
- [ ] retention/archive delle strategie AI che non producono mai APPROVE
- [ ] notifiche candidati (hook `on_candidate` esiste, non collegato)
- [ ] timeframe-awareness; iniettare simbolo/TF target nel brief dell'author

Lato dati (utente): allineare strategie ↔ InstrumentSpec in `algo_framework/instruments.py`.
