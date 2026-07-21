"""
Dashboard web per monitorare il sistema e approvare candidati.

Avvia con:
    uvicorn dashboard.api:app --host 0.0.0.0 --port 8000

Apri http://localhost:8000
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import Config
from db.database import init_db, get_session_factory
from db.models import Strategy, Backtest, Candidate, CycleLog, UserIdea
from agents.idea_evaluator import IdeaEvaluator, IdeaFileReader
from fastapi import UploadFile, File, Form
import tempfile


app = FastAPI(title="Quant Agent Dashboard")

config = Config()
engine = init_db(config.get("database.url"))
SessionFactory = get_session_factory(engine)

idea_evaluator = IdeaEvaluator(
    api_key=config.get("claude.api_key"),
    model=config.get("claude.model", "claude-sonnet-4-5"),
)


@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!DOCTYPE html>
<html>
<head>
<title>Quant Agent Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, system-ui, sans-serif; }
body { background: #050811; color: #e2e8f0; padding: 24px; }
h1 { color: #00d4ff; margin-bottom: 8px; font-size: 24px; }
.subtitle { color: #64748b; margin-bottom: 24px; font-size: 13px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-bottom: 24px; }
.stat { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 8px; padding: 16px; }
.stat-label { font-size: 11px; color: #64748b; text-transform: uppercase; letter-spacing: 1px; }
.stat-value { font-size: 24px; color: #00d4ff; font-weight: 600; margin-top: 6px; }
.section { background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
.section h2 { color: #94a3b8; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; padding: 10px; color: #64748b; font-weight: 500; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }
td { padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.04); color: #cbd5e1; }
.verdict-APPROVE { color: #34d399; }
.verdict-REVIEW { color: #fbbf24; }
.verdict-REJECT { color: #f87171; }
.btn { padding: 6px 12px; border-radius: 6px; border: 1px solid; background: transparent; cursor: pointer; font-size: 12px; margin-right: 4px; }
.btn-approve { color: #34d399; border-color: #34d39966; }
.btn-reject { color: #f87171; border-color: #f8717166; }
.btn:hover { background: rgba(255,255,255,0.05); }
.empty { color: #475569; text-align: center; padding: 40px; font-size: 13px; }
pre { background: #000814; padding: 12px; border-radius: 6px; font-size: 11px; overflow-x: auto; color: #94a3b8; }
.live-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #34d399; margin-right: 8px; animation: pulse 2s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
</style>
</head>
<body>
<h1>◈ Quant Agent Dashboard</h1>
<div class="subtitle"><span class="live-dot"></span>Sistema operativo · auto-refresh ogni 30s · <a href="/ideas" style="color:#00d4ff; text-decoration:none;">💡 Submit Idea →</a></div>

<div id="stats" class="grid"></div>

<div class="section">
<h2>🎯 Candidati in attesa di approvazione</h2>
<div id="candidates"></div>
</div>

<div class="section">
<h2>📊 Backtest recenti</h2>
<div id="backtests"></div>
</div>

<div class="section">
<h2>🔄 Ultimi cicli</h2>
<div id="cycles"></div>
</div>

<script>
async function load() {
  const stats = await fetch('/api/stats').then(r => r.json());
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="stat-label">Strategie totali</div><div class="stat-value">${stats.total_strategies}</div></div>
    <div class="stat"><div class="stat-label">Backtest eseguiti</div><div class="stat-value">${stats.total_backtests}</div></div>
    <div class="stat"><div class="stat-label">Candidati attivi</div><div class="stat-value">${stats.pending_candidates}</div></div>
    <div class="stat"><div class="stat-label">Cicli completati</div><div class="stat-value">${stats.total_cycles}</div></div>
  `;
  
  const cands = await fetch('/api/candidates').then(r => r.json());
  document.getElementById('candidates').innerHTML = cands.length === 0
    ? '<div class="empty">Nessun candidato in attesa. Il sistema sta cercando…</div>'
    : `<table><thead><tr>
        <th>EA</th><th>Profilo</th><th>Symbol</th><th>Score</th><th>PF</th><th>Sharpe</th><th>Max DD</th><th>WF</th><th>Verdetto</th><th>Azioni</th>
      </tr></thead><tbody>${cands.map(c => `
        <tr>
          <td><strong>${c.ea_name}</strong></td>
          <td>${c.profile}</td>
          <td>${c.symbol}</td>
          <td><strong>${c.score.toFixed(0)}</strong></td>
          <td>${c.profit_factor.toFixed(2)}</td>
          <td>${c.sharpe.toFixed(2)}</td>
          <td>${c.max_dd.toFixed(2)}%</td>
          <td>${c.wf_score != null ? c.wf_score.toFixed(2) : '—'}</td>
          <td class="verdict-${c.verdict}">${c.verdict}</td>
          <td>
            <button class="btn btn-approve" onclick="approve(${c.id})">Approva</button>
            <button class="btn btn-reject" onclick="reject(${c.id})">Rifiuta</button>
          </td>
        </tr>`).join('')}</tbody></table>`;
  
  const bts = await fetch('/api/backtests?limit=10').then(r => r.json());
  document.getElementById('backtests').innerHTML = bts.length === 0 ? '<div class="empty">Nessun backtest ancora.</div>' :
    `<table><thead><tr><th>Strategia</th><th>Symbol</th><th>PF</th><th>Sharpe</th><th>Max DD</th><th>Trades</th><th>Pass</th></tr></thead><tbody>
    ${bts.map(b => `<tr>
      <td>${b.strategy_name}</td><td>${b.symbol}</td>
      <td>${b.profit_factor.toFixed(2)}</td><td>${b.sharpe.toFixed(2)}</td>
      <td>${b.max_dd.toFixed(2)}%</td><td>${b.trades}</td>
      <td>${b.passes ? '✅' : '❌'}</td>
    </tr>`).join('')}</tbody></table>`;
  
  const cycles = await fetch('/api/cycles?limit=5').then(r => r.json());
  document.getElementById('cycles').innerHTML = `<table><thead><tr>
    <th>#</th><th>Inizio</th><th>Stato</th><th>Generate</th><th>Backtest</th><th>Candidati</th>
  </tr></thead><tbody>${cycles.map(c => `<tr>
    <td>#${c.id}</td><td>${new Date(c.started_at).toLocaleString('it-IT')}</td>
    <td>${c.status}</td><td>${c.generated}</td><td>${c.backtested}</td>
    <td><strong>${c.candidates}</strong></td>
  </tr>`).join('')}</tbody></table>`;
}

async function approve(id) {
  await fetch(`/api/candidates/${id}/approve`, { method: 'POST' });
  load();
}

async function reject(id) {
  await fetch(`/api/candidates/${id}/reject`, { method: 'POST' });
  load();
}

load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


@app.get("/api/stats")
def stats():
    s = SessionFactory()
    try:
        return {
            "total_strategies": s.query(Strategy).count(),
            "total_backtests": s.query(Backtest).count(),
            "pending_candidates": s.query(Candidate).filter(Candidate.status == "pending").count(),
            "total_cycles": s.query(CycleLog).count(),
        }
    finally:
        s.close()


@app.get("/api/candidates")
def candidates():
    s = SessionFactory()
    try:
        results = []
        cands = s.query(Candidate).filter(Candidate.status == "pending").all()
        for c in cands:
            bt = s.get(Backtest, c.backtest_id)
            if not bt:
                continue
            strat = s.get(Strategy, bt.strategy_id)
            results.append({
                "id": c.id,
                "ea_name": Path(strat.mql5_path).stem if strat.mql5_path else strat.name,
                "profile": strat.profile,
                "symbol": strat.symbol,
                "score": c.overall_score,
                "verdict": c.recommendation,
                "profit_factor": bt.profit_factor,
                "sharpe": bt.sharpe_ratio,
                "max_dd": bt.max_drawdown_pct,
                "wf_score": bt.walk_forward_score,
            })
        return results
    finally:
        s.close()


@app.get("/api/backtests")
def backtests(limit: int = 10):
    s = SessionFactory()
    try:
        bts = s.query(Backtest).order_by(Backtest.created_at.desc()).limit(limit).all()
        return [{
            "id": bt.id,
            "strategy_name": s.get(Strategy, bt.strategy_id).name if bt.strategy_id else "?",
            "symbol": s.get(Strategy, bt.strategy_id).symbol if bt.strategy_id else "?",
            "profit_factor": bt.profit_factor,
            "sharpe": bt.sharpe_ratio,
            "max_dd": bt.max_drawdown_pct,
            "trades": bt.total_trades,
            "passes": bt.passes_prop_rules,
        } for bt in bts]
    finally:
        s.close()


@app.get("/api/cycles")
def cycles(limit: int = 10):
    s = SessionFactory()
    try:
        cs = s.query(CycleLog).order_by(CycleLog.id.desc()).limit(limit).all()
        return [{
            "id": c.id,
            "started_at": c.started_at.isoformat() if c.started_at else None,
            "status": c.status,
            "generated": c.strategies_generated,
            "backtested": c.backtests_run,
            "candidates": c.candidates_found,
        } for c in cs]
    finally:
        s.close()


@app.post("/api/candidates/{cand_id}/approve")
def approve(cand_id: int):
    s = SessionFactory()
    try:
        c = s.get(Candidate, cand_id)
        if not c:
            raise HTTPException(404)
        c.status = "approved"
        c.approved_at = datetime.now(timezone.utc)
        s.commit()
        return {"ok": True}
    finally:
        s.close()


@app.post("/api/candidates/{cand_id}/reject")
def reject(cand_id: int):
    s = SessionFactory()
    try:
        c = s.get(Candidate, cand_id)
        if not c:
            raise HTTPException(404)
        c.status = "rejected"
        s.commit()
        return {"ok": True}
    finally:
        s.close()


# ============ IDEAS ENDPOINTS ============

@app.post("/api/ideas/submit")
async def submit_idea(
    title: str = Form(""),
    text: str = Form(""),
    notes: str = Form(""),
    file: Optional[UploadFile] = File(None),
    url: str = Form(""),
):
    """Sottometti una nuova idea (testo, file o URL) per valutazione."""
    content = ""
    source_type = "text"
    source_path = ""
    
    # Determina la fonte
    if file and file.filename:
        # File upload
        suffix = Path(file.filename).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            tmp_path = Path(tmp.name)
        try:
            content = IdeaFileReader.read(tmp_path)
            source_type = "file"
            source_path = file.filename
        finally:
            tmp_path.unlink(missing_ok=True)
    elif url:
        content = IdeaFileReader.read_url(url)
        source_type = "url"
        source_path = url
    elif text:
        content = text
        source_type = "text"
    else:
        raise HTTPException(400, "Fornisci text, file o url")
    
    if len(content.strip()) < 50:
        raise HTTPException(400, "Contenuto troppo breve (min 50 caratteri)")
    
    # Evaluate
    prop_firm = config.get("prop.target_firm", "ftmo")
    prop_phase = config.get("prop.phase", "challenge")
    
    evaluation = idea_evaluator.evaluate(
        content=content,
        source=source_path or "text_input",
        prop_firm=prop_firm,
        prop_phase=prop_phase,
    )
    
    # Salva
    s = SessionFactory()
    try:
        idea = UserIdea(
            source_type=source_type,
            source_path=source_path,
            original_content=content[:5000],
            user_title=title or evaluation.idea_extracted[:200],
            user_notes=notes,
            idea_extracted=evaluation.idea_extracted,
            tradability_score=evaluation.tradability_score,
            completeness_score=evaluation.completeness_score,
            structured_strategy=evaluation.structured_strategy,
            missing_elements=evaluation.missing_elements,
            assumptions_made=evaluation.assumptions_made,
            critical_review=evaluation.critical_review,
            verdict=evaluation.verdict,
            proceed_to_codegen=evaluation.proceed_to_codegen,
            reviewer_recommendations=evaluation.reviewer_recommendations,
        )
        s.add(idea)
        s.commit()
        s.refresh(idea)
        
        return {
            "id": idea.id,
            "verdict": evaluation.verdict,
            "tradability_score": evaluation.tradability_score,
            "completeness_score": evaluation.completeness_score,
            "idea_extracted": evaluation.idea_extracted,
            "critical_review": evaluation.critical_review,
            "proceed_to_codegen": evaluation.proceed_to_codegen,
            "missing_elements": evaluation.missing_elements,
            "recommendations": evaluation.reviewer_recommendations,
        }
    finally:
        s.close()


@app.get("/api/ideas")
def list_ideas(limit: int = 20):
    s = SessionFactory()
    try:
        ideas = s.query(UserIdea).order_by(UserIdea.created_at.desc()).limit(limit).all()
        return [{
            "id": i.id,
            "title": i.user_title,
            "source_type": i.source_type,
            "verdict": i.verdict,
            "tradability_score": i.tradability_score,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        } for i in ideas]
    finally:
        s.close()


@app.get("/api/ideas/{idea_id}")
def get_idea(idea_id: int):
    s = SessionFactory()
    try:
        i = s.get(UserIdea, idea_id)
        if not i:
            raise HTTPException(404)
        return {
            "id": i.id,
            "title": i.user_title,
            "notes": i.user_notes,
            "source_type": i.source_type,
            "source_path": i.source_path,
            "original_content": i.original_content,
            "idea_extracted": i.idea_extracted,
            "tradability_score": i.tradability_score,
            "completeness_score": i.completeness_score,
            "structured_strategy": i.structured_strategy,
            "missing_elements": i.missing_elements,
            "assumptions_made": i.assumptions_made,
            "critical_review": i.critical_review,
            "verdict": i.verdict,
            "proceed_to_codegen": i.proceed_to_codegen,
            "recommendations": i.reviewer_recommendations,
            "status": i.status,
            "created_at": i.created_at.isoformat() if i.created_at else None,
        }
    finally:
        s.close()


@app.post("/api/ideas/{idea_id}/approve_for_pipeline")
def approve_idea_for_pipeline(idea_id: int):
    """L'utente approva di mandare l'idea alla pipeline (codegen → backtest)."""
    s = SessionFactory()
    try:
        idea = s.get(UserIdea, idea_id)
        if not idea:
            raise HTTPException(404)
        if not idea.structured_strategy:
            raise HTTPException(400, "Idea non strutturabile")
        
        idea.status = "approved_for_dev"
        idea.user_decided_at = datetime.now(timezone.utc)
        s.commit()
        
        # TODO: trigger orchestrator a processare questa idea nel prossimo ciclo
        # Per ora la marchiamo, l'orchestrator picka le ideas approvate
        
        return {"ok": True, "message": "Idea aggiunta alla pipeline. Sarà processata al prossimo ciclo."}
    finally:
        s.close()


@app.post("/api/ideas/{idea_id}/reject")
def reject_idea(idea_id: int):
    s = SessionFactory()
    try:
        idea = s.get(UserIdea, idea_id)
        if not idea:
            raise HTTPException(404)
        idea.status = "rejected"
        idea.user_decided_at = datetime.now(timezone.utc)
        s.commit()
        return {"ok": True}
    finally:
        s.close()


# Pagina dedicata ideas
@app.get("/ideas", response_class=HTMLResponse)
def ideas_page():
    return IDEAS_HTML


# ============ HTML PAGES ============

IDEAS_HTML = """
<!DOCTYPE html>
<html>
<head>
<title>Submit Idea — Quant Agent</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, system-ui, sans-serif; }
body { background: #050811; color: #e2e8f0; padding: 24px; min-height: 100vh; }
.container { max-width: 900px; margin: 0 auto; }
h1 { color: #00d4ff; margin-bottom: 8px; font-size: 24px; }
.subtitle { color: #64748b; margin-bottom: 24px; font-size: 13px; }
.tab-bar { display: flex; gap: 4px; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.06); }
.tab { padding: 10px 16px; cursor: pointer; color: #64748b; font-size: 13px; border-bottom: 2px solid transparent; }
.tab.active { color: #00d4ff; border-bottom-color: #00d4ff; }
.input-section { background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
label { display: block; color: #94a3b8; font-size: 12px; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 1px; }
input[type="text"], input[type="url"], textarea, input[type="file"] {
  width: 100%; background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.08);
  border-radius: 6px; padding: 10px 12px; color: #e2e8f0; font-size: 13px; font-family: inherit;
  margin-bottom: 14px;
}
textarea { resize: vertical; min-height: 200px; line-height: 1.6; }
input:focus, textarea:focus { outline: none; border-color: #00d4ff66; }
.btn { padding: 12px 24px; border-radius: 8px; border: 1px solid #00d4ff66; background: rgba(0,212,255,0.1);
  color: #00d4ff; cursor: pointer; font-size: 13px; font-weight: 600; }
.btn:hover { background: rgba(0,212,255,0.18); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-secondary { color: #94a3b8; border-color: rgba(255,255,255,0.15); background: transparent; }
.section-input { display: none; }
.section-input.active { display: block; }
.result { margin-top: 24px; padding: 20px; border-radius: 10px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); }
.verdict { font-size: 18px; font-weight: 700; padding: 8px 14px; border-radius: 6px; display: inline-block; margin-bottom: 16px; }
.verdict-PROMETTENTE { background: rgba(52,211,153,0.15); color: #34d399; border: 1px solid #34d39966; }
.verdict-INTERESSANTE_CON_RISERVE { background: rgba(251,191,36,0.15); color: #fbbf24; border: 1px solid #fbbf2466; }
.verdict-RISCHIOSA { background: rgba(248,113,113,0.15); color: #f87171; border: 1px solid #f8717166; }
.verdict-DA_SCARTARE { background: rgba(100,116,139,0.15); color: #94a3b8; border: 1px solid #94a3b866; }
.scores { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin: 16px 0; }
.score-card { background: rgba(255,255,255,0.03); padding: 12px; border-radius: 6px; }
.score-label { color: #64748b; font-size: 11px; text-transform: uppercase; }
.score-value { color: #00d4ff; font-size: 22px; font-weight: 700; margin-top: 4px; }
.review-text { white-space: pre-wrap; line-height: 1.7; font-size: 13px; color: #cbd5e1; padding: 16px; background: rgba(0,0,0,0.2); border-radius: 8px; margin: 16px 0; }
.actions { display: flex; gap: 10px; margin-top: 16px; }
.spinner { display: inline-block; width: 14px; height: 14px; border: 2px solid #00d4ff33; border-top-color: #00d4ff; border-radius: 50%; animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.history { margin-top: 32px; }
.history-item { background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.05); border-radius: 8px; padding: 14px; margin-bottom: 10px; cursor: pointer; }
.history-item:hover { background: rgba(255,255,255,0.04); }
.nav-back { color: #00d4ff; text-decoration: none; font-size: 13px; margin-bottom: 16px; display: inline-block; }
.nav-back:hover { text-decoration: underline; }
.tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 10px; background: rgba(255,255,255,0.05); color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-right: 6px; }
</style>
</head>
<body>
<div class="container">
  <a class="nav-back" href="/">← Dashboard</a>
  <h1>💡 Submit Trading Idea</h1>
  <div class="subtitle">L'agente analizzerà la tua idea, farà devil's advocate, e ti dirà se vale la pena svilupparla in EA.</div>

  <div class="tab-bar">
    <div class="tab active" onclick="showTab('text', this)">📝 Testo</div>
    <div class="tab" onclick="showTab('file', this)">📎 File</div>
    <div class="tab" onclick="showTab('url', this)">🔗 URL</div>
  </div>

  <form id="ideaForm" enctype="multipart/form-data">
    <div class="input-section">
      <label>Titolo (opzionale)</label>
      <input type="text" name="title" placeholder="Es: Breakout Asia su EURUSD durante volatilità bassa">

      <div id="section-text" class="section-input active">
        <label>La tua idea / ipotesi</label>
        <textarea name="text" placeholder="Descrivi la tua idea in modo discorsivo. Anche solo appunti grezzi vanno bene — l'agente estrarrà la struttura."></textarea>
      </div>

      <div id="section-file" class="section-input">
        <label>Carica file (.txt, .md, .pdf, .docx, .png, .jpg)</label>
        <input type="file" name="file" accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg">
      </div>

      <div id="section-url" class="section-input">
        <label>URL articolo / paper</label>
        <input type="url" name="url" placeholder="https://...">
      </div>

      <label>Note aggiuntive (contesto, perché ti interessa)</label>
      <textarea name="notes" style="min-height:80px" placeholder="Es: l'ho letto su un paper, ma voglio sapere se regge sul forex moderno..."></textarea>

      <button type="submit" class="btn" id="submitBtn">🧠 Analizza Idea</button>
    </div>
  </form>

  <div id="result"></div>

  <div class="history">
    <h2 style="font-size:14px; color:#94a3b8; text-transform:uppercase; letter-spacing:1px; margin-bottom:12px;">Idee precedenti</h2>
    <div id="history-list"></div>
  </div>
</div>

<script>
let activeTab = 'text';

function showTab(tab, el) {
  activeTab = tab;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  document.querySelectorAll('.section-input').forEach(s => s.classList.remove('active'));
  document.getElementById('section-' + tab).classList.add('active');
}

document.getElementById('ideaForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Analizzando...';
  
  const fd = new FormData(e.target);
  
  // Pulisci campi non attivi per evitare confusione backend
  if (activeTab !== 'text') fd.delete('text');
  if (activeTab !== 'file') fd.delete('file');
  if (activeTab !== 'url') fd.delete('url');
  
  try {
    const r = await fetch('/api/ideas/submit', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await r.text());
    const data = await r.json();
    showResult(data);
    loadHistory();
  } catch (err) {
    document.getElementById('result').innerHTML = `<div class="result"><div style="color:#f87171">Errore: ${err.message}</div></div>`;
  }
  
  btn.disabled = false;
  btn.innerHTML = '🧠 Analizza Idea';
});

function showResult(data) {
  const verdict = data.verdict || 'INTERESSANTE_CON_RISERVE';
  const html = `
    <div class="result">
      <div class="verdict verdict-${verdict}">${verdict.replace(/_/g, ' ')}</div>
      
      <div style="color:#cbd5e1; font-size:14px; line-height:1.6; margin-bottom:16px;">
        <strong style="color:#00d4ff;">Idea estratta:</strong><br>
        ${data.idea_extracted || '(nessuna)'}
      </div>
      
      <div class="scores">
        <div class="score-card">
          <div class="score-label">Tradabilità</div>
          <div class="score-value">${data.tradability_score}/100</div>
        </div>
        <div class="score-card">
          <div class="score-label">Completezza</div>
          <div class="score-value">${data.completeness_score}/100</div>
        </div>
      </div>
      
      ${data.missing_elements && data.missing_elements.length ? `
        <div style="margin-bottom:14px;">
          <strong style="color:#fbbf24; font-size:12px;">⚠️ Elementi mancanti:</strong>
          <ul style="margin:6px 0 0 20px; color:#cbd5e1; font-size:13px;">
            ${data.missing_elements.map(e => `<li>${e}</li>`).join('')}
          </ul>
        </div>` : ''}
      
      <div class="review-text">${data.critical_review || '(nessuna review)'}</div>
      
      ${data.proceed_to_codegen ? `
        <div class="actions">
          <button class="btn" onclick="approveForPipeline(${data.id})">
            ✅ Manda alla Pipeline (genera EA + backtest)
          </button>
          <button class="btn btn-secondary" onclick="rejectIdea(${data.id})">
            Archivia
          </button>
        </div>` : `
        <div style="color:#94a3b8; font-size:12px; margin-top:12px;">
          Idea archiviata. Modifica/integra e ri-sottometti se vuoi un nuovo tentativo.
        </div>`}
    </div>
  `;
  document.getElementById('result').innerHTML = html;
  document.getElementById('result').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function approveForPipeline(id) {
  if (!confirm('Mandare alla pipeline? L\\'idea sarà sviluppata in EA e backtestata.')) return;
  const r = await fetch(`/api/ideas/${id}/approve_for_pipeline`, { method: 'POST' });
  const data = await r.json();
  alert(data.message || 'OK');
  loadHistory();
}

async function rejectIdea(id) {
  await fetch(`/api/ideas/${id}/reject`, { method: 'POST' });
  document.getElementById('result').innerHTML = '';
  loadHistory();
}

async function loadHistory() {
  const r = await fetch('/api/ideas?limit=10');
  const items = await r.json();
  const html = items.length === 0
    ? '<div style="color:#475569; font-size:13px; padding:20px; text-align:center;">Nessuna idea ancora sottomessa.</div>'
    : items.map(i => `
        <div class="history-item" onclick="loadIdea(${i.id})">
          <div style="display:flex; justify-content:space-between; align-items:center;">
            <div>
              <span class="tag">${i.source_type}</span>
              <span class="tag verdict-${i.verdict}" style="background:none; color:inherit; border:1px solid;">${i.verdict || '?'}</span>
              <strong style="color:#cbd5e1;">${i.title || 'Senza titolo'}</strong>
            </div>
            <div style="color:#64748b; font-size:11px;">
              Score: ${i.tradability_score} · ${new Date(i.created_at).toLocaleString('it-IT')}
            </div>
          </div>
        </div>
      `).join('');
  document.getElementById('history-list').innerHTML = html;
}

async function loadIdea(id) {
  const r = await fetch(`/api/ideas/${id}`);
  const data = await r.json();
  showResult(data);
}

loadHistory();
</script>
</body>
</html>
"""
