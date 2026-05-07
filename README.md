# 🤖 Prop Agent System

Sistema autonomo h24 per generare, testare e validare Expert Advisor MQL5 per prop firm CFD (FTMO, FundedNext).

## Cosa fa

Il sistema gira in loop continuo e:
1. **Genera** ipotesi di strategie via Claude AI (autonomo)
2. **Accetta** anche TUE idee (testo/PDF/DOCX/URL/immagini OCR) e le valuta criticamente
3. **Scrive** codice MQL5 compilabile
4. **Compila** e fa backtest su MT5 Strategy Tester
5. **Valida** se passerebbe le regole prop
6. **Walk-forward** test per evitare overfitting
7. **Notifica** via Telegram quando trova un EA pronto
8. **Archivia** tutto in database

## Submit Trading Ideas

Vai su `http://localhost:8000/ideas` per sottomettere le tue idee al sistema:
- **Testo libero**: incolla note/appunti, anche grezzi
- **File**: carica .txt, .md, .pdf, .docx, immagini (OCR)
- **URL**: link a articoli/paper

L'agente farà:
- Estrazione strategia strutturata
- Critical review (devil's advocate)
- Compliance check prop firm
- Verdetto: PROMETTENTE / INTERESSANTE_CON_RISERVE / RISCHIOSA / DA_SCARTARE

Se approvi un'idea, va nella pipeline e diventa un candidato EA.

## Tre profili EA

- **Aggressive**: passaggio rapido challenge (~8 giorni, risk 1.5%)
- **Conservative**: gestione funded long-term (risk 0.5%)
- **Switchable**: adattivo (aggressivo fino al 50% target, poi conservativo)

## Setup rapido

### 1. Requirements
- Windows VPS con MT5 installato
- Python 3.11+
- Account Claude API ([console.anthropic.com](https://console.anthropic.com))
- Bot Telegram (opzionale ma raccomandato)
- Account MT5 demo (qualsiasi broker)

### 2. Installazione

**Opzione A — Miniconda (consigliato)**

```bash
# Installa Miniconda da https://docs.conda.io/en/latest/miniconda.html
# Poi:
git clone <repo>
cd prop_agent_system
conda env create -f environment.yml
conda activate propagent
```

**Opzione B — Python venv (alternativa)**

```bash
git clone <repo>
cd prop_agent_system
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

⚠️ Su Windows con `venv` potresti dover installare anche **Microsoft Visual C++ Build Tools** se alcune dipendenze devono compilarsi. Con conda no.

### 3. Configurazione

Copia `config.example.yaml` → `config.yaml` e compila:

```yaml
claude:
  api_key: "sk-ant-..."
  model: "claude-sonnet-4-5"

mt5:
  login: 12345678
  password: "your_demo_password"
  server: "MetaQuotes-Demo"
  path: "C:\\Program Files\\MetaTrader 5\\terminal64.exe"

telegram:
  bot_token: "..."
  chat_id: "..."

orchestrator:
  cycle_hours: 4              # Genera nuove strategie ogni 4 ore
  max_strategies_per_cycle: 5
  profiles_active: ["aggressive", "conservative"]
  
prop_target: "ftmo"           # ftmo | fundednext
```

### 4. Avvia

```bash
python orchestrator.py
```

Il sistema partirà e ti notificherà su Telegram quando avrà candidati EA pronti.

### 5. Dashboard (opzionale)

```bash
python dashboard/api.py
```

Apri `http://localhost:8000` per monitorare in tempo reale.

## Workflow umano

Il tuo intervento è ridotto al minimo:

1. Setup iniziale (1 ora)
2. Ricevi notifica Telegram: *"EA candidato pronto: Aggressive_EURUSD_v47, profit factor 2.3, max DD 6.8%"*
3. Apri dashboard, rivedi backtest e walk-forward
4. Approvi → il sistema deploya l'EA in MT5
5. Lanci la challenge

## Costi mensili stimati

- VPS Contabo Windows: $11
- Claude API: $30-100 (dipende da cycle_hours)
- **Totale**: $40-110/mese

## Avvertenze

⚠️ Sistema in beta. Sempre fare paper trading prima di soldi veri.
⚠️ Le regole prop cambiano. Verifica sempre la doc ufficiale.
⚠️ Nessuna garanzia di profitto. Il backtest non predice il futuro.

---

Made with Claude Code | Maintained by [posillipo]
