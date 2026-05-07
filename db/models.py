"""SQLAlchemy models for the prop agent system."""
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, JSON, Boolean, Text, ForeignKey
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Strategy(Base):
    """Una strategia generata dal Research Agent."""
    __tablename__ = "strategies"
    
    id = Column(Integer, primary_key=True)
    profile = Column(String(20), nullable=False)         # aggressive | conservative | switchable
    source = Column(String(20), default="auto")          # auto | user_idea
    user_idea_id = Column(Integer, nullable=True)        # se viene da idea utente
    name = Column(String(100), nullable=False)
    hypothesis = Column(Text, nullable=False)            # Descrizione testuale generata da Claude
    strategy_type = Column(String(50))                   # trend_following | breakout | etc.
    symbol = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    parameters = Column(JSON)                            # Dizionario parametri strategia
    
    mql5_code = Column(Text)                             # Codice EA generato
    mql5_path = Column(String(500))                      # Path al file .mq5
    compiled = Column(Boolean, default=False)
    compile_errors = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    backtests = relationship("Backtest", back_populates="strategy")


class Backtest(Base):
    """Risultato di un backtest su MT5 Strategy Tester."""
    __tablename__ = "backtests"
    
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    
    # Periodo testato
    date_from = Column(DateTime, nullable=False)
    date_to = Column(DateTime, nullable=False)
    
    # Metriche core
    initial_deposit = Column(Float)
    final_balance = Column(Float)
    net_profit = Column(Float)
    profit_factor = Column(Float)
    sharpe_ratio = Column(Float)
    sortino_ratio = Column(Float)
    
    # Drawdown
    max_drawdown_money = Column(Float)
    max_drawdown_pct = Column(Float)
    max_daily_drawdown_pct = Column(Float)
    
    # Trade stats
    total_trades = Column(Integer)
    winning_trades = Column(Integer)
    losing_trades = Column(Integer)
    win_rate = Column(Float)
    avg_win = Column(Float)
    avg_loss = Column(Float)
    largest_win = Column(Float)
    largest_loss = Column(Float)
    max_consecutive_wins = Column(Integer)
    max_consecutive_losses = Column(Integer)
    
    # Compliance prop
    passes_prop_rules = Column(Boolean, default=False)
    prop_violations = Column(JSON)                       # Lista delle violazioni
    estimated_pass_days = Column(Integer)                # Giorni stimati per superare challenge
    
    # Walk-forward
    walk_forward_score = Column(Float)                   # 0-1, robustezza out-of-sample
    walk_forward_results = Column(JSON)
    
    # Report files
    report_html_path = Column(String(500))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    
    strategy = relationship("Strategy", back_populates="backtests")


class Candidate(Base):
    """Un EA che ha passato tutti i test e attende approvazione umana."""
    __tablename__ = "candidates"
    
    id = Column(Integer, primary_key=True)
    backtest_id = Column(Integer, ForeignKey("backtests.id"), nullable=False)
    
    overall_score = Column(Float)                        # Punteggio composito
    ai_analysis = Column(Text)                           # Analisi finale di Claude
    recommendation = Column(String(20))                  # APPROVE | REVIEW | REJECT
    
    # Workflow umano
    notified_at = Column(DateTime)
    reviewed_at = Column(DateTime)
    approved_at = Column(DateTime)
    deployed_at = Column(DateTime)
    status = Column(String(20), default="pending")       # pending | approved | rejected | deployed
    
    user_notes = Column(Text)
    
    created_at = Column(DateTime, default=datetime.utcnow)


class CycleLog(Base):
    """Log di ogni ciclo dell'orchestratore."""
    __tablename__ = "cycle_logs"
    
    id = Column(Integer, primary_key=True)
    cycle_number = Column(Integer)
    started_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    status = Column(String(20))                          # running | completed | failed
    
    strategies_generated = Column(Integer, default=0)
    strategies_compiled = Column(Integer, default=0)
    backtests_run = Column(Integer, default=0)
    candidates_found = Column(Integer, default=0)
    
    error_message = Column(Text)
    metadata_json = Column(JSON)


class UserIdea(Base):
    """Idee/ipotesi sottomesse dall'utente per valutazione."""
    __tablename__ = "user_ideas"
    
    id = Column(Integer, primary_key=True)
    
    # Input
    source_type = Column(String(20))                     # text | file | url
    source_path = Column(String(500))                    # path del file o URL
    original_content = Column(Text)                      # contenuto raw
    user_title = Column(String(200))                     # titolo dato dall'utente
    user_notes = Column(Text)                            # note aggiuntive
    
    # Output evaluation
    idea_extracted = Column(Text)                        # riassunto core idea
    tradability_score = Column(Integer)                  # 0-100
    completeness_score = Column(Integer)                 # 0-100
    structured_strategy = Column(JSON)                   # strategia strutturata
    missing_elements = Column(JSON)                      # cosa mancava
    assumptions_made = Column(JSON)                      # assunzioni fatte
    
    critical_review = Column(Text)                       # review completa Claude
    verdict = Column(String(40))                         # PROMETTENTE | RISCHIOSA | etc.
    proceed_to_codegen = Column(Boolean, default=False)
    reviewer_recommendations = Column(JSON)
    
    # Workflow
    status = Column(String(20), default="evaluated")     # evaluated | approved_for_dev | in_pipeline | rejected
    linked_strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    user_decided_at = Column(DateTime)
