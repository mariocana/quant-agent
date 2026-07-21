# Quant Agent — Design Document v3

**Data:** 7 Maggio 2026
**Stato:** in review — fase concettuale
**Autore:** Quant Agent Design Session

---

## 1. Contesto

Nelle iterazioni precedenti abbiamo costruito un sistema che tentava di:
- Generare Expert Advisor MQL5 da zero (fallimento: alta rate di compile errors)
- Backtestare via MT5 Strategy Tester (fallimento: MT5 già aperto ignora /config)
- Implementare un backtester Python custom (compromesso: numeri divergenti dalla realtà)

**Insight decisivo dell'utente**: già esistono `algo_framework` e `datasea` — tool maturi
che l'utente ha sviluppato per sé stesso. Sono la fonte di verità per backtest, walk-forward,
Monte Carlo, e gestione dati.

**Nuovo ruolo dell'agent**: non reinventare la ruota, ma **orchestrare** questi tool 24/7
con intelligenza — come un quantitative researcher senior che assegna esperimenti a
strumenti che già funzionano.

---

## 2. Vincoli operativi

- Il PC dell'utente ha 3 conda env separati: `propagent`, `algo_framework`, `datasea`
- I tool vengono lanciati via CLI, nessuna importazione diretta
- Dati condivisi in `C:\datasea_data\` (Delta Lake)
- Strategie generate vanno in `strategies/ai_generated/` con prefisso `AI_`
- Nessun limite hardware per ingestioni
- L'utente deve rimanere in controllo del proprio repo `algo_framework`

---

## 3. Architettura logica

### 3.1 I 5 layer del sistema

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 5: HUMAN INTERFACE                                   │
│  Dashboard web                                              │
│  Approve/reject candidati, sottomettere idee                │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  LAYER 4: ORCHESTRATION (propagent env)                     │
│  Loop h24, cycle scheduler, database, memory                │
│  Prende decisioni su cosa esplorare next                    │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  LAYER 3: AI COGNITIVE AGENTS (Claude API)                  │
│  - MarketIntelligence: interpreta stato mercato             │
│  - StrategyResearcher: propone esperimenti                  │
│  - StrategyAuthor: scrive nuove strategie da template       │
│  - ResultAnalyzer: valuta output backtest                   │
│  - IdeaEvaluator: analizza idee utente                      │
│  - PropValidator: verifica compliance FTMO/FundedNext       │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  LAYER 2: TOOL ADAPTERS (propagent env, subprocess)         │
│  - DataseaClient: query + ingest via CLI                    │
│  - AlgoFrameworkClient: registry + backtest via CLI         │
│  - EnvBridge: gestione conda run tra env                    │
└─────────────────────────────────────────────────────────────┘
                              ▲
┌─────────────────────────────────────────────────────────────┐
│  LAYER 1: USER'S EXISTING TOOLS                             │
│  algo_framework (env: algo_framework) — backtest engine     │
│  datasea (env: datasea) — data lake                         │
│  C:\datasea_data\ (Delta Lake, condiviso)                   │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 Distinzione fondamentale

Il sistema è **due cose diverse insieme**:

**A. Un data engineer autonomo** — mantiene aggiornato il datasea, sa cosa c'è,
sa cosa serve, ingesta cosa manca.

**B. Un quantitative researcher** — esplora lo spazio delle strategie, propone
esperimenti, interpreta risultati, decide cosa promuovere a candidate.

Entrambi condividono lo stesso substrate (Claude + algo_framework + datasea) ma
hanno cadenze e priorità diverse. È importante non confonderli nel codice.

---

## 4. Componenti dettagliati

### 4.1 EnvBridge (Layer 2)

**Responsabilità:** eseguire comandi Python in un env conda diverso da `propagent`,
catturare stdout/stderr, gestire timeout, propagare errori strutturati.

**Metodo pubblico principale:**
```
run(env_name, cwd, args, timeout, env_vars) → CommandResult
```

**Perché serve un layer astratto:**
- `conda run -n algo_framework python ...` ha syntax specifiche
- Windows richiede `shell=True` per attivare conda
- Gestione encoding output (spesso cp1252 su Windows italiano)
- Streaming di output lunghi (ingest datasea può durare ore)

**CommandResult contiene:** returncode, stdout, stderr, duration, artifacts_path

---

### 4.2 DataseaClient (Layer 2)

**Responsabilità:** parlare con datasea via CLI per query + ingest.

**Operazioni pubbliche:**

1. **list_available(symbol, timeframe) → DataInventory**
   Che simboli/TF/date range abbiamo già in `C:\datasea_data\`
   
2. **ingest_mt5(symbol, timeframe, since_date) → IngestJob**
   Lancia `python examples\bronze\mt5_download_history.py` per popolare dati mancanti
   
3. **ingest_binance(symbol, timeframe, since_date) → IngestJob**
   Idem per Binance
   
4. **get_data_path(symbol, timeframe) → Path | None**
   Ritorna il path CSV/parquet per un simbolo, se disponibile
   
5. **get_health() → dict**
   Stato datasea: totale simboli, date range coperto, dischi

**Nota importante:** questi metodi non fanno API magic. Wrappano CLI. Se datasea
cambia i suoi CLI, dobbiamo aggiornare DataseaClient (ma non tutto il resto).

---

### 4.3 AlgoFrameworkClient (Layer 2)

**Responsabilità:** parlare con algo_framework via CLI per backtest + registry.

**Operazioni pubbliche:**

1. **list_strategies() → list[StrategyInfo]**
   Discovery: esegue `python -c "from core.registry import StrategyRegistry; ..."`
   e ritorna nomi + descrizione + parametri esposti di ogni strategia registrata
   
2. **run_backtest(strategy_name, symbol, timeframe, params, prop_rules) → BacktestReport**
   Lancia `python pipeline.py --strategy X --csv ... --tf ... --export ...`
   Parsa i report esportati
   
3. **run_walk_forward(strategy_name, ...) → WalkForwardReport**
   Se il pipeline.py fa già walk-forward integrato, questo è alias di run_backtest
   Altrimenti chiama command diverso
   
4. **run_monte_carlo(strategy_name, ...) → MonteCarloReport**
   Idem, dipende da come è strutturato pipeline.py
   
5. **register_strategy(strategy_file_path) → None**
   Copia il file .py in `strategies/ai_generated/`, verifica che il registry
   lo carichi correttamente

**Da definire con l'utente:**
- Formato esatto dell'export di pipeline.py (JSON? HTML? CSV?)
- Se pipeline.py fa già walk-forward + monte carlo insieme o serve command separato
- Quali parametri di CLI accetta (leggeremo `--help` la prima volta)

---

### 4.4 StrategyAuthor (Layer 3, Claude-powered)

**Responsabilità:** dato un brief testuale (ipotesi trading, tipo di regime, TF target),
scrivere un file Python conforme a `strategies/_template.py`.

**Input:**
- Testo dell'ipotesi ("mean reversion su range asiatico con RSI e Bollinger")
- Il template `_template.py` reale letto dal repo (context per Claude)
- Esempi di strategie esistenti (BB_RSI_SCALP, BB_RSI_AGGRO) come reference

**Output:**
- File `.py` completo, sintatticamente valido
- Sintassi verificata via `ast.parse()` prima di scriverlo su disco
- Nome file convenzione: `AI_<TYPE>_<SHORTNAME>_v<NUM>.py`

**Regole di sicurezza:**
- Non può cancellare o modificare strategie esistenti (solo aggiungere)
- Non può fare import di moduli non standard
- File salvato in `strategies/ai_generated/` — mai in `strategies/` root
- Prima di registrarla, syntax check + dry-run compilazione

---

### 4.5 MarketIntelligence (Layer 3, evolves MarketScanner)

**Responsabilità:** interpretare stato mercato usando dati datasea (non più MT5 diretto).

**Cambia da prima:**
- Query datasea per candele recenti (30 giorni)
- Calcola regime, ATR, volatilità come prima
- Aggiunge cross-asset insights (correlazioni tra simboli)
- Ritorna un "market briefing" testuale che gli altri agent usano come context

---

### 4.6 StrategyResearcher (Layer 3, evolves l'esistente)

**Responsabilità:** decidere quale esperimento lanciare next.

**Il vero valore aggiunto qui:**
- Riceve: market briefing + storico esperimenti recenti + strategie disponibili nel registry
- Decide: "voglio testare BB_RSI_AGGRO su XAUUSD H1 con RSI period 21 invece di 14"
- Oppure: "nessuna strategia esistente è adatta al regime attuale, chiedi allo StrategyAuthor di crearne una nuova"

**Output non è più codice**, è un **experiment plan**:
```json
{
  "mode": "existing" | "author_new",
  "strategy_name": "BB_RSI_AGGRO",  // se mode=existing
  "author_brief": "...",             // se mode=author_new
  "symbol": "XAUUSD",
  "timeframe": "H1",
  "params_override": {"rsi_period": 21, "bb_dev": 2.5},
  "rationale": "Perché questa combinazione ha senso ora"
}
```

---

### 4.7 ResultAnalyzer, IdeaEvaluator, PropValidator (Layer 3)

Restano concettualmente identici a quelli attuali. Cambiano solo:
- Input: leggono report format algo_framework invece di formato MT5
- ResultAnalyzer: aggiunge campo "monte_carlo_confidence" nell'output

---

## 5. Flusso di un ciclo completo

```
[ORCHESTRATOR TICK — ogni N ore]

1. MarketIntelligence.brief()
   ↓
   Legge datasea → produce testo con stato mercati
   
2. Data health check
   ↓
   DataseaClient.list_available() per simboli in watchlist
   ↓
   Se mancano dati per simboli/date rilevanti:
       DataseaClient.ingest_mt5(...)  (queue background, non blocca)
   
3. StrategyResearcher.propose(brief, history, registry)
   ↓
   Ritorna experiment_plan
   
4. Branch in base a plan.mode:
   
   4a. mode = "existing":
       AlgoFrameworkClient.run_backtest(
           strategy=plan.strategy_name,
           symbol=plan.symbol,
           timeframe=plan.timeframe,
           params=plan.params_override
       )
       
   4b. mode = "author_new":
       new_file = StrategyAuthor.write(plan.author_brief)
       AlgoFrameworkClient.register_strategy(new_file)
       AlgoFrameworkClient.run_backtest(...)
   
5. ResultAnalyzer.evaluate(report, prop_rules)
   ↓
   Verdict: APPROVE | REVIEW | REJECT
   + Confidence score
   + Note qualitative
   
6. PropValidator.check_compliance(report, prop_rules)
   ↓
   Bool + list violazioni potenziali
   
7. Se APPROVE + compliant:
       Salva Candidate in DB
       Dashboard mostra pending review
```

---

## 6. Cosa scompare dal codebase attuale

Elimineremo (dopo aver validato la nuova architettura):

- `agents/python_backtester.py` — algo_framework fa questo
- `agents/backtest_runner.py` — MT5 Strategy Tester non più necessario
- `agents/mql5_codegen.py` — niente più codegen MQL5
- `agents/spec_generator.py` — niente più framework MQL5 parametrico
- `agents/walk_forward.py` — algo_framework ce l'ha
- `templates/PropAgentFramework.mq5` — non più necessario
- `templates/ea_skeleton.mq5` — non più necessario

Riduzione codebase stimata: ~2500 righe eliminate, ~1500 aggiunte. Net -1000 righe più chiare.

---

## 7. Rischi tecnici e mitigazioni

### R1: conda run tra env è lento su Windows (secondi di overhead)
**Mitigazione:** cachiamo lista strategie del registry (non ricalcoliamo ogni ciclo)

### R2: pipeline.py può avere durata variabile (secondi vs ore)
**Mitigazione:** timeout configurabile per strategia, kill process se supera. Priorità
bassa se durata backtest > tempo ciclo — meglio 1 backtest completo che 5 mezzi.

### R3: Path Windows con spazi rompono spesso i comandi shell
**Mitigazione:** EnvBridge quota sempre i path, usa `pathlib.Path` non stringhe

### R4: StrategyAuthor genera codice Python non valido
**Mitigazione:** `ast.parse()` obbligatorio prima di scrivere, seguito da un dry-import
in subprocess. Se fallisce, retry con errore in context.

### R5: L'agent riempie strategies/ai_generated/ di spazzatura nel tempo
**Mitigazione:** politica di retention. Strategie che dopo N tentativi non producono
mai un backtest APPROVE vengono spostate in `strategies/ai_generated/archive/`.

### R6: Datasea ingestion può conflittare con backtest se scrivono/leggono stessi file
**Mitigazione:** file lock su directory dati per simbolo. Ingest in background solo se
nessun backtest sta usando quel simbolo.

### R7: I due conda env potrebbero avere dipendenze incompatibili tra loro
**Mitigazione:** non è un problema perché sono isolati. L'unico contatto è via
file system (Delta Lake in `C:\datasea_data\`).

---

## 8. Decisioni ancora aperte

Cose che dobbiamo decidere insieme prima di scrivere codice:

- [ ] Formato esatto output di `pipeline.py --export` (parsing di quello)
- [ ] Come chiamare le funzioni del registry di algo_framework da CLI
  (probabilmente serve creare un piccolo script helper in algo_framework repo,
  o meglio: leggere il file `core/registry.py` direttamente per introspection)
- [ ] Politica di scheduling ingest datasea (background continuous vs on-demand)
- [ ] Se manteniamo il MarketScanner MT5 come backup o lo rimuoviamo del tutto
- [ ] Struttura del brief testuale che passa tra i vari agent
- [ ] Come gestiamo il caso "algo_framework crash a metà" (recovery, resume)
- [ ] **Chat conversazionale con l'agente** (feature nuova richiesta): oggi il canale
  idee è un *form* (`/ideas`) one-shot. Vogliamo un thread di dialogo dove l'utente
  discute l'ipotesi con l'agente PRIMA di mandarla in pipeline (raffina, chiede
  chiarimenti, risponde alle obiezioni del devil's advocate). Vedi §11.

---

## 9. Come procederemo

**Sequenza consigliata di implementazione** (una volta approvato questo design):

**Sprint 1 — Fondazione (2 ore lavoro):**
1. Scrivere `EnvBridge` con test unitario che dimostra che riesce a lanciare
   un `python -c "print('hello')"` in ognuno dei 3 env
2. Sondaggio esplorativo di `algo_framework` e `datasea` dai loro `--help`
   (l'agent lancerà i comandi e leggeremo l'output insieme)

**Sprint 2 — Adapter (3 ore):**
3. `DataseaClient` con metodi essenziali (list, ingest)
4. `AlgoFrameworkClient` con metodi essenziali (list_strategies, run_backtest)
5. Test integrazione: lanciamo BB_RSI_AGGRO su un simbolo esistente end-to-end
   dall'agent

**Sprint 3 — Cognitive (3 ore):**
6. Aggiorniamo `StrategyResearcher` per produrre experiment_plan invece di codice
7. `StrategyAuthor` (nuovo)
8. Aggiorniamo `ResultAnalyzer` per parsare il nuovo formato report

**Sprint 4 — Cleanup (1 ora):**
9. Rimuoviamo i moduli non più necessari
10. Aggiorniamo dashboard per il nuovo flusso

**Sprint 5 — Live test (variabile):**
11. Lanciamo `orchestrator --once` con il nuovo sistema
12. Iteriamo su prompt e edge cases

---

## 10. Costi

Cambiamento nel consumo Claude API:

- Prima (spec/custom mode): ~$0.30 per strategia (di cui 80% sprecato in codegen)
- Dopo: ~$0.15 per strategia
  - Researcher: ~$0.03 (produce experiment_plan)
  - StrategyAuthor: ~$0.10 (solo se serve nuova strategia)
  - ResultAnalyzer: ~$0.02

Risparmio: ~50%. In più, meno chiamate wasted su compile errors.

---

## Conclusione

Il design è: **l'agent smette di scrivere codice di produzione e diventa un
orchestrator + researcher**. La qualità del backtest, walk-forward e Monte Carlo
è affidata al tool maturo dell'utente (algo_framework). L'agent lavora dove
Claude è forte (interpretazione, brief, decision-making) e delega dove Python
è forte (backtest deterministico su dati reali).

Questo è il pattern che i quant fund seri usano: **AI as researcher, not as coder.**

---

## 11. Note v4 — aggiornamento post-review del codice reale

*Aggiunto dopo aver ispezionato algo_framework, datasea e il codice attuale. Rettifica
alcune assunzioni della v3 che il codice reale ha già superato.*

### 11.1 Cosa è cambiato rispetto alla v3

- **Un solo conda env, non tre.** Esiste già `algo_framework/workbench-environment.yml`
  (`name: workbench`) che unifica datasea + algo_framework. datasea è pip-installato
  come package (`pip install -e . --no-deps`), algo_framework gira dalla sua cartella.
  → L'`EnvBridge` cross-env della v3 è **overkill**: serve solo lanciare subprocess con
  `cwd=algo_framework` e `PYTHONIOENCODING=utf-8`. Rischio R7 e R1 decadono.
- **Il backtester legge datasea gold direttamente.** `backtester.py --datasea <ROOT>
  --datasea-table <t> --tf <tf>` legge Delta gold con spread reale e traduce i TF
  (`5m`↔`M5`). → Il "ponte delta→CSV" non serve.
- **`workbench.py` è già il prototipo manuale dell'agente.** Ha `scan_gold()` (inventory
  dati = `DataseaClient.list_available`), `list_strategies()` (registry discovery) e
  `/api/run` che orchestra `backtester.py`/`robustness.py` via
  `asyncio.create_subprocess_exec` con flag corretti, streaming e fix encoding Windows
  (R3 già mitigato lì). → Gli adapter Layer 2 vanno **copiati da workbench.py**, non
  reinventati.

### 11.2 Contratti da aggiungere (branch separati su algo_framework — bloccanti)

- **`--export-json`**: oggi backtester/robustness stampano il dict `metrics` a console e
  `--export` salva solo trades/equity CSV. L'agente ha bisogno del **summary in JSON**
  (Sharpe, PF, max DD, WF-consistency, MC-prop-pass). Il dict esiste già → banale.
- **`--params`**: override parametri da CLI per gli sweep del `StrategyResearcher`.
  `StrategyRegistry.get(config=dict)` accetta già il dict → manca solo il flag CLI.

### 11.3 Feature nuova: chat conversazionale con l'agente

Oggi `/ideas` è un form one-shot (sottometti → critica → approva). Vogliamo in più un
**thread di dialogo**: l'utente discute l'ipotesi con l'agente, l'agente fa domande e
devil's advocate in modo interattivo, e solo a valle si genera lo `structured_strategy`
che entra in pipeline. Da progettare:
- endpoint chat sulla dashboard (storia messaggi per sessione idea);
- l'agente mantiene il context dell'idea in sviluppo;
- "commit" esplicito → produce lo stesso `structured_strategy` che oggi esce dal form.
*L'esperienza a form resta come scorciatoia; la chat è il canale ricco.*

### 11.4 Guardrail prioritario

Il rischio più concreto è R5 (l'agente riempie `strategies/ai_generated/` di roba
sovra-ottimizzata). La `robustness.py` (WF + Monte Carlo) va resa **gate obbligatorio**
prima di promuovere a candidate, non un report opzionale.
