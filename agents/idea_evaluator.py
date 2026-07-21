"""
Idea Evaluator Agent — analyzes ideas/hypotheses provided by the user
(free text, documents, articles) and decides whether they're worth developing
into a strategy.

Acts as a critical "second opinion" + extractor of tradable strategies.
"""
import json
import re
from pathlib import Path
from loguru import logger
from typing import Optional
from dataclasses import dataclass, asdict

from agents.api_client import make_client, call_with_retry


SYSTEM_PROMPT_EXTRACTOR = """You are a quantitative analyst who extracts tradable ideas from free-form text.

The user will give you text (notes, jottings, articles, posts, papers) that CONTAINS a market idea or observation.

Your task:
1. Identify the CORE idea (even if buried among other information)
2. Structure it into the standard strategy JSON format
3. If details are missing, assume sensible values and FLAG what you had to assume

Return ONLY a JSON with this structure:

{
  "idea_extracted": "1-2 sentence summary of the core idea",
  "tradability_score": 0-100,        // How concretely implementable it is
  "completeness_score": 0-100,       // How complete the idea is (missing details?)
  "missing_elements": ["entry rules", "stop loss method", ...],  // What's missing
  "assumptions_made": ["Assumed SL = 1.5x ATR since unspecified", ...],

  "structured_strategy": {
    "name": "Short name",
    "strategy_type": "trend_following|breakout|mean_reversion|momentum|swing|ict_smc|other",
    "hypothesis": "Structured description",
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

If the text does NOT contain a tradable idea (generic, philosophical, off-topic), return:
{
  "idea_extracted": "No tradable idea identified",
  "tradability_score": 0,
  "reason": "Explanation of why"
}
"""

SYSTEM_PROMPT_REVIEWER = """You are a risk officer and quantitative researcher experienced in prop firm trading.

The user proposes a strategy idea. Your task is to play rigorous DEVIL'S ADVOCATE.

MANDATORY structured output:

═══ IDEA ANALYSIS ═══

OVERALL VERDICT: [PROMISING | INTERESTING_WITH_RESERVATIONS | RISKY | DISCARD]

✅ STRENGTHS:
- ...
- ...

⚠️ LOGICAL WEAKNESSES:
- ...
- ...

🚨 RISKY COGNITIVE BIASES:
- (look for: hindsight bias, survivorship bias, overfitting, look-ahead bias, recency bias)
- ...

⚖️ PROP FIRM COMPLIANCE:
- Does the idea violate/risk violating rules? (martingale, cross-account hedging, news rules)
- How many trades/day does it typically generate? (FTMO 2000 req/day risk)
- Expected drawdown vs prop limit?

📊 BACKTESTABILITY:
- What historical data is needed?
- Minimum years for statistical validity?
- Any hidden costs? (variable spreads, swap, slippage)

🎯 CONCRETE RECOMMENDATIONS:
- If PROMISING: how to structure it, parameters to optimize
- If DISCARD: explain clearly why, without being brutal

📈 ESTIMATED PROBABILITY OF SUCCESS:
- On prop FTMO/FundedNext: XX%
- Rationale: ...

Be direct, critical but constructive. NEVER say "it might work" without specifying precise conditions.
If you spot serious red flags, highlight them with 🚨.
"""


@dataclass
class IdeaEvaluation:
    """Result of evaluating a user idea."""
    original_text: str
    source: str                          # "text" | "file:path" | "url"
    idea_extracted: str
    tradability_score: int               # 0-100
    completeness_score: int              # 0-100
    structured_strategy: Optional[dict]  # None if not tradable
    missing_elements: list[str]
    assumptions_made: list[str]

    critical_review: str                 # Full reviewer output
    verdict: str                         # PROMISING | INTERESTING_WITH_RESERVATIONS | RISKY | DISCARD

    proceed_to_codegen: bool             # If True, may go to the pipeline
    reviewer_recommendations: list[str]


class IdeaEvaluator:
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6"):
        self.client = make_client(api_key, timeout_seconds=120)
        self.model = model

    def evaluate(
        self,
        content: str,
        source: str = "text",
        prop_firm: str = "ftmo",
        prop_phase: str = "challenge",
    ) -> IdeaEvaluation:
        """
        Full workflow:
        1. Extract structure from the idea
        2. Critical review
        3. Final decision
        """
        logger.info(f"💡 Evaluating idea from source: {source}")

        # === STEP 1: Structure extraction ===
        extraction = self._extract_structure(content)

        if extraction.get("tradability_score", 0) < 30:
            # Not tradable, skip review
            logger.warning(f"⏭  Idea not tradable (score {extraction.get('tradability_score', 0)})")
            return IdeaEvaluation(
                original_text=content[:500],
                source=source,
                idea_extracted=extraction.get("idea_extracted", "?"),
                tradability_score=extraction.get("tradability_score", 0),
                completeness_score=0,
                structured_strategy=None,
                missing_elements=[],
                assumptions_made=[],
                critical_review=extraction.get("reason", "Idea cannot be structured"),
                verdict="DISCARD",
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

        # === STEP 3: Extract verdict and recommendations ===
        verdict = self._extract_verdict(review)
        recommendations = self._extract_recommendations(review)
        proceed = verdict in ["PROMISING", "INTERESTING_WITH_RESERVATIONS"]

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
        """Step 1: extract a structured strategy from the text."""
        text = call_with_retry(
            self.client,
            model=self.model,
            max_tokens=2048,
            system=SYSTEM_PROMPT_EXTRACTOR,
            messages=[{
                "role": "user",
                "content": f"Analyze this text and extract the tradable idea:\n\n---\n{content}\n---"
            }],
        ).strip()

        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        try:
            return json.loads(text.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse extraction JSON: {e}")
            return {"tradability_score": 0, "reason": "Parse error"}

    def _critical_review(
        self,
        strategy: dict,
        prop_firm: str,
        prop_phase: str,
        assumptions: list[str],
    ) -> str:
        """Step 2: critical review by a risk officer."""
        from prop_rules import get_rules
        rules = get_rules(prop_firm, prop_phase)

        assumptions_text = ""
        if assumptions:
            assumptions_text = "\n\nNOTE: these assumptions were made during extraction:\n" + "\n".join(f"- {a}" for a in assumptions)

        user_msg = f"""Critically analyze this strategy idea:

NAME: {strategy.get('name', '?')}
TYPE: {strategy.get('strategy_type', '?')}
HYPOTHESIS: {strategy.get('hypothesis', '?')}

ENTRY LOGIC:
{json.dumps(strategy.get('entry_logic', {}), indent=2, ensure_ascii=False)}

EXIT LOGIC:
{json.dumps(strategy.get('exit_logic', {}), indent=2, ensure_ascii=False)}

INDICATORS: {strategy.get('indicators', [])}
EXPECTED BEHAVIOR: {strategy.get('expected_behavior', '?')}

TARGET PROP CONTEXT: {rules.name}
- Max daily DD: {rules.max_daily_dd_pct}%
- Max total DD: {rules.max_total_dd_pct}%
- Profit target: {rules.profit_target_pct}%
- News restriction: {rules.news_block_minutes} min
{assumptions_text}

Do your full critical analysis following the defined schema."""

        return call_with_retry(
            self.client,
            model=self.model,
            max_tokens=2500,
            system=SYSTEM_PROMPT_REVIEWER,
            messages=[{"role": "user", "content": user_msg}],
        )

    def _extract_verdict(self, review: str) -> str:
        """Extract the verdict from the review."""
        for v in ["PROMISING", "INTERESTING_WITH_RESERVATIONS", "RISKY", "DISCARD"]:
            if v in review:
                return v
        return "INTERESTING_WITH_RESERVATIONS"  # cautious default

    def _extract_recommendations(self, review: str) -> list[str]:
        """Extract the recommendation bullet points."""
        recs = []
        in_rec_section = False
        for line in review.split("\n"):
            line = line.strip()
            if "RECOMMENDATIONS" in line.upper():
                in_rec_section = True
                continue
            if in_rec_section:
                if line.startswith(("📈", "═", "PROBABILITY")):
                    break
                if line.startswith(("- ", "• ", "* ")):
                    recs.append(line[2:].strip())
        return recs


# ============ FILE READERS ============

class IdeaFileReader:
    """Reads content from various file types."""

    @staticmethod
    def read(file_path: Path) -> str:
        """Auto-detect the file type and read the content."""
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
            raise ValueError(f"Unsupported file type: {ext}")

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
            return pytesseract.image_to_string(Image.open(path), lang="eng")
        except ImportError:
            raise RuntimeError("pip install pytesseract pillow + install tesseract-ocr")

    @staticmethod
    def read_url(url: str) -> str:
        """Download and extract text from a URL."""
        try:
            import httpx
            from bs4 import BeautifulSoup
            response = httpx.get(url, follow_redirects=True, timeout=30)
            soup = BeautifulSoup(response.text, "html.parser")
            # Strip script/style
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except ImportError:
            raise RuntimeError("pip install httpx beautifulsoup4")
