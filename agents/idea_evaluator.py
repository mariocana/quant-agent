"""
Idea Evaluator Agent — analizza idee/ipotesi fornite dall'utente
(testo libero, documenti, articoli) e decide se vale la pena svilupparle in EA.

Funziona come "secondo parere critico" + extractor di strategie tradabili.
"""
import json
import re
from pathlib import Path
from anthropic import Anthropic
from loguru import logger
from typing import Optional
from dataclasses import dataclass, asdict


SYSTEM_PROMPT_EXTRACTOR = """Sei un analista quantitativo che estrae idee tradabili da testo discorsivo.

L'utente ti darà del testo (note, appunti, articoli, post, paper) che CONTIENE un'idea o un'osservazione di mercato.

Il tuo compito è:
1. Identificare l'idea CORE (anche se sepolta tra altre informazioni)
2. Strutturarla nel formato JSON standard di una strategia
3. Se mancano dettagli, ipotizza valori sensati e SEGNALA cosa hai dovuto presumere

Ritorna SOLO un JSON con questa struttura:

{
  "idea_extracted": "Riassunto in 1-2 frasi dell'idea core",
  "tradability_score": 0-100,        // Quanto è concretamente implementabile
  "completeness_score": 0-100,       // Quanto è completa l'idea (mancano dettagli?)
  "missing_elements": ["entry rules", "stop loss method", ...],  // Cosa manca
  "assumptions_made": ["Ho assunto SL = 1.5x ATR perché non specificato", ...],
  
  "structured_strategy": {
    "name": "Nome breve",
    "strategy_type": "trend_following|breakout|mean_reversion|momentum|swing|ict_smc|other",
    "hypothesis": "Descrizione strutturata",
    "entry_logic": {
      "long_conditions": [...],
      "short_conditions": [...]
    },
    "exit_logic": {
      "stop_loss": "...",
      "take_profit": "...",
      "trailing": "..."
    },
    "indicators": [{"name": "...", "period": ...}],
    "parameters": {...},
    "expected_behavior": "..."
  }
}

Se il testo NON contiene un'idea tradabile (è generico, filosofico, off-topic), ritorna:
{
  "idea_extracted": "Nessuna idea tradabile identificata",
  "tradability_score": 0,
  "reason": "Spiegazione del perché"
}
"""

SYSTEM_PROMPT_REVIEWER = """Sei un risk officer e quantitative researcher esperto di prop firm trading.

L'utente ti propone un'idea di strategia. Il tuo compito è fare DEVIL'S ADVOCATE rigoroso.

Output strutturato OBBLIGATORIO:

═══ ANALISI IDEA ═══

VERDETTO COMPLESSIVO: [PROMETTENTE | INTERESSANTE_CON_RISERVE | RISCHIOSA | DA_SCARTARE]

✅ PUNTI DI FORZA:
- ...
- ...

⚠️ DEBOLEZZE LOGICHE:
- ...
- ...

🚨 BIAS COGNITIVI RISCHIOSI:
- (cerca: hindsight bias, survivorship bias, overfitting, look-ahead bias, recency bias)
- ...

⚖️ COMPLIANCE PROP FIRM:
- L'idea viola/rischia di violare regole? (martingale, hedging cross-account, news rules)
- Quanti trade/giorno genera tipicamente? (rischio FTMO 2000 req/day)
- Drawdown atteso vs limite prop?

📊 BACKTESTABILITÀ:
- Quali dati storici servono?
- Quanti anni minimo per validità statistica?
- Ci sono costi nascosti? (spread variabili, swap, slippage)

🎯 RACCOMANDAZIONI CONCRETE:
- Se PROMETTENTE: come strutturarla in MQL5, parametri da ottimizzare
- Se DA_SCARTARE: spiega chiaramente perché, senza essere brutale

📈 PROBABILITÀ DI SUCCESSO STIMATA:
- Su prop FTMO/FundedNext: XX%
- Razionale: ...

Sii diretto, critico ma costruttivo. NON dire mai "potrebbe funzionare" senza specificare condizioni precise.
Se rilevi red flag gravi, mettili in evidenza con 🚨.
"""


@dataclass
class IdeaEvaluation:
    """Risultato della valutazione di un'idea utente."""
    original_text: str
    source: str                          # "text" | "file:path" | "url"
    idea_extracted: str
    tradability_score: int               # 0-100
    completeness_score: int              # 0-100
    structured_strategy: Optional[dict]  # None se non tradabile
    missing_elements: list[str]
    assumptions_made: list[str]
    
    critical_review: str                 # Output completo del reviewer
    verdict: str                         # PROMETTENTE | INTERESSANTE_CON_RISERVE | RISCHIOSA | DA_SCARTARE
    
    proceed_to_codegen: bool             # Se True, può andare alla pipeline
    reviewer_recommendations: list[str]


class IdeaEvaluator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
    
    def evaluate(
        self,
        content: str,
        source: str = "text",
        prop_firm: str = "ftmo",
        prop_phase: str = "challenge",
    ) -> IdeaEvaluation:
        """
        Workflow completo:
        1. Estrai struttura dall'idea
        2. Critical review
        3. Decisione finale
        """
        logger.info(f"💡 Evaluating idea from source: {source}")
        
        # === STEP 1: Estrazione struttura ===
        extraction = self._extract_structure(content)
        
        if extraction.get("tradability_score", 0) < 30:
            # Idea non tradabile, salta review
            logger.warning(f"⏭  Idea non tradabile (score {extraction.get('tradability_score', 0)})")
            return IdeaEvaluation(
                original_text=content[:500],
                source=source,
                idea_extracted=extraction.get("idea_extracted", "?"),
                tradability_score=extraction.get("tradability_score", 0),
                completeness_score=0,
                structured_strategy=None,
                missing_elements=[],
                assumptions_made=[],
                critical_review=extraction.get("reason", "Idea non strutturabile"),
                verdict="DA_SCARTARE",
                proceed_to_codegen=False,
                reviewer_recommendations=[],
            )
        
        # === STEP 2: Critical review ===
        review = self._critical_review(
            extraction["structured_strategy"],
            prop_firm,
            prop_phase,
            extraction.get("assumptions_made", []),
        )
        
        # === STEP 3: Estrai verdetto e raccomandazioni ===
        verdict = self._extract_verdict(review)
        recommendations = self._extract_recommendations(review)
        proceed = verdict in ["PROMETTENTE", "INTERESSANTE_CON_RISERVE"]
        
        result = IdeaEvaluation(
            original_text=content[:500],
            source=source,
            idea_extracted=extraction.get("idea_extracted", ""),
            tradability_score=extraction.get("tradability_score", 0),
            completeness_score=extraction.get("completeness_score", 0),
            structured_strategy=extraction.get("structured_strategy"),
            missing_elements=extraction.get("missing_elements", []),
            assumptions_made=extraction.get("assumptions_made", []),
            critical_review=review,
            verdict=verdict,
            proceed_to_codegen=proceed,
            reviewer_recommendations=recommendations,
        )
        
        logger.info(f"   Verdict: {verdict} | Proceed: {proceed}")
        return result
    
    def _extract_structure(self, content: str) -> dict:
        """Step 1: estrai una strategia strutturata dal testo."""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT_EXTRACTOR,
            messages=[{
                "role": "user",
                "content": f"Analizza questo testo ed estrai l'idea tradabile:\n\n---\n{content}\n---"
            }],
        )
        
        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
        
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse extraction JSON: {e}")
            return {"tradability_score": 0, "reason": "Errore parsing"}
    
    def _critical_review(
        self,
        strategy: dict,
        prop_firm: str,
        prop_phase: str,
        assumptions: list[str],
    ) -> str:
        """Step 2: critical review da risk officer."""
        from prop_rules import get_rules
        rules = get_rules(prop_firm, prop_phase)
        
        assumptions_text = ""
        if assumptions:
            assumptions_text = f"\n\nNOTA: durante l'estrazione sono state fatte queste assunzioni:\n" + "\n".join(f"- {a}" for a in assumptions)
        
        user_msg = f"""Analizza criticamente questa idea di strategia:

NOME: {strategy.get('name', '?')}
TIPO: {strategy.get('strategy_type', '?')}
IPOTESI: {strategy.get('hypothesis', '?')}

ENTRY LOGIC:
{json.dumps(strategy.get('entry_logic', {}), indent=2, ensure_ascii=False)}

EXIT LOGIC:
{json.dumps(strategy.get('exit_logic', {}), indent=2, ensure_ascii=False)}

INDICATORI: {strategy.get('indicators', [])}
COMPORTAMENTO ATTESO: {strategy.get('expected_behavior', '?')}

CONTESTO PROP TARGET: {rules.name}
- Max daily DD: {rules.max_daily_dd_pct}%
- Max total DD: {rules.max_total_dd_pct}%
- Profit target: {rules.profit_target_pct}%
- News restriction: {rules.news_block_minutes} min
{assumptions_text}

Fai la tua analisi critica completa secondo lo schema definito."""
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2500,
            system=SYSTEM_PROMPT_REVIEWER,
            messages=[{"role": "user", "content": user_msg}],
        )
        
        return response.content[0].text
    
    def _extract_verdict(self, review: str) -> str:
        """Estrae il verdetto dalla review."""
        for v in ["PROMETTENTE", "INTERESSANTE_CON_RISERVE", "RISCHIOSA", "DA_SCARTARE"]:
            if v in review:
                return v
        return "INTERESSANTE_CON_RISERVE"  # default cauto
    
    def _extract_recommendations(self, review: str) -> list[str]:
        """Estrae i bullet point delle raccomandazioni."""
        recs = []
        in_rec_section = False
        for line in review.split("\n"):
            line = line.strip()
            if "RACCOMANDAZIONI" in line.upper():
                in_rec_section = True
                continue
            if in_rec_section:
                if line.startswith(("📈", "═", "PROBABILITÀ")):
                    break
                if line.startswith(("- ", "• ", "* ")):
                    recs.append(line[2:].strip())
        return recs


# ============ FILE READERS ============

class IdeaFileReader:
    """Legge contenuto da diversi tipi di file."""
    
    @staticmethod
    def read(file_path: Path) -> str:
        """Auto-detect tipo di file e legge il contenuto."""
        ext = file_path.suffix.lower()
        
        if ext in [".txt", ".md"]:
            return file_path.read_text(encoding="utf-8", errors="ignore")
        
        elif ext == ".pdf":
            return IdeaFileReader._read_pdf(file_path)
        
        elif ext == ".docx":
            return IdeaFileReader._read_docx(file_path)
        
        elif ext in [".png", ".jpg", ".jpeg"]:
            return IdeaFileReader._read_image_ocr(file_path)
        
        else:
            raise ValueError(f"Tipo file non supportato: {ext}")
    
    @staticmethod
    def _read_pdf(path: Path) -> str:
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            text = "\n".join(page.extract_text() for page in reader.pages)
            return text
        except ImportError:
            raise RuntimeError("pip install pypdf")
    
    @staticmethod
    def _read_docx(path: Path) -> str:
        try:
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            raise RuntimeError("pip install python-docx")
    
    @staticmethod
    def _read_image_ocr(path: Path) -> str:
        try:
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.open(path), lang="ita+eng")
        except ImportError:
            raise RuntimeError("pip install pytesseract pillow + installa tesseract-ocr")
    
    @staticmethod
    def read_url(url: str) -> str:
        """Scarica e estrae testo da URL."""
        try:
            import httpx
            from bs4 import BeautifulSoup
            response = httpx.get(url, follow_redirects=True, timeout=30)
            soup = BeautifulSoup(response.text, "html.parser")
            # Rimuovi script/style
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except ImportError:
            raise RuntimeError("pip install httpx beautifulsoup4")
