"""
Orchestrator — il loop principale che fa girare tutto il sistema h24.

Esegui con:
    python orchestrator.py

Configurazione: edita config.yaml prima di lanciare.
"""
import sys
import time
import signal
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger
from apscheduler.schedulers.blocking import BlockingScheduler

from config import Config, load_profile
from db.database import init_db, get_session_factory
from db.models import Strategy, Backtest, Candidate, CycleLog, UserIdea
from agents.strategy_researcher import StrategyResearcher
from agents.spec_generator import SpecGenerator
from agents.mql5_codegen import MQL5CodeGenerator
from agents.backtest_runner import BacktestRunner
from agents.walk_forward import WalkForwardAnalyzer
from agents.prop_validator import PropValidator
from agents.result_analyzer import ResultAnalyzer
from agents.market_scanner import MarketScanner, DEFAULT_WATCHLIST
from notifications.telegram_bot import TelegramNotifier


# Setup logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level:8}</level> | {message}")
logger.add("logs/orchestrator_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days", level="DEBUG")


class Orchestrator:
    def __init__(self, config_path: str = "config.yaml"):
        logger.info("🚀 Initializing Prop Agent Orchestrator")
        self.config = Config(config_path)
        
        # Database
        self.engine = init_db(self.config.get("database.url"))
        self.SessionFactory = get_session_factory(self.engine)
        
        # Agents
        api_key = self.config.get("claude.api_key")
        model = self.config.get("claude.model", "claude-sonnet-4-6")
        
        self.scanner = MarketScanner(
            mt5_path=self.config.get("mt5.path"),
            mt5_login=self.config.get("mt5.login"),
            mt5_password=self.config.get("mt5.password"),
            mt5_server=self.config.get("mt5.server"),
        )
        self.researcher = StrategyResearcher(api_key, model)
        self.spec_gen = SpecGenerator(api_key, model)
        self.codegen = MQL5CodeGenerator(api_key, model)
        self.backtester = BacktestRunner(
            mt5_path=self.config.get("mt5.path"),
            mt5_login=self.config.get("mt5.login"),
            mt5_password=self.config.get("mt5.password"),
            mt5_server=self.config.get("mt5.server"),
        )
        self.walk_forward = WalkForwardAnalyzer(
            n_splits=self.config.get("backtest.walk_forward_splits", 5),
        )
        self.analyzer = ResultAnalyzer(api_key, model)
        
        # Notifier
        self.notifier = TelegramNotifier(
            bot_token=self.config.get("telegram.bot_token", ""),
            chat_id=self.config.get("telegram.chat_id", ""),
            enabled=self.config.get("telegram.enabled", False),
        )
        
        self.cycle_count = 0
        logger.success("✅ Orchestrator ready")
    
    def run_cycle(self):
        """Un ciclo completo della pipeline."""
        self.cycle_count += 1
        cycle_start = datetime.utcnow()
        logger.info(f"\n{'='*60}\n🔄 CYCLE #{self.cycle_count} START\n{'='*60}")
        
        session = self.SessionFactory()
        cycle_log = CycleLog(cycle_number=self.cycle_count, status="running")
        session.add(cycle_log)
        session.commit()
        
        try:
            generated = compiled_count = backtested = candidates_found = 0
            
            profiles_active = self.config.get("orchestrator.profiles_active", ["aggressive"])
            max_per_cycle = self.config.get("orchestrator.max_strategies_per_cycle", 3)
            
            prop_firm = self.config.get("prop.target_firm", "ftmo")
            prop_phase = self.config.get("prop.phase", "challenge")
            account_size = self.config.get("prop.account_size", 10000)
            
            # === MARKET SCAN: scansiona tutti i simboli per regime detection ===
            # La watchlist è configurabile, default include majors + indices + metals
            watchlist_config = self.config.get("orchestrator.watchlist", None)
            timeframes_to_scan = self.config.get("orchestrator.scan_timeframes", ["M15", "H1", "H4", "D1"])
            
            if watchlist_config and isinstance(watchlist_config, dict):
                watchlist = watchlist_config
            else:
                watchlist = DEFAULT_WATCHLIST
            
            logger.info(f"📡 Scanning markets ({len(watchlist)} symbols × {len(timeframes_to_scan)} TFs)...")
            scan_results = self.scanner.scan_all(
                watchlist=watchlist,
                timeframes=timeframes_to_scan,
            )
            
            if not scan_results:
                logger.warning("⚠️  Market scan returned no results — check MT5 connection")
                market_summary = "MARKET DATA UNAVAILABLE — proceed with default assumptions"
            else:
                market_summary = self.scanner.get_market_summary(scan_results)
                logger.info(f"   Best opportunity: {max(scan_results.values(), key=lambda x: x.overall_score).symbol}")
            
            # Storico recenti per evitare duplicati
            recent_strategies = session.query(Strategy).order_by(
                Strategy.created_at.desc()
            ).limit(20).all()
            recent_dicts = [
                {"name": s.name, "strategy_type": s.strategy_type, "symbol": s.symbol, "timeframe": s.timeframe}
                for s in recent_strategies
            ]
            
            # === PROCESSA IDEE UTENTE APPROVATE (priorità) ===
            approved_ideas = session.query(UserIdea).filter(
                UserIdea.status == "approved_for_dev"
            ).all()
            
            for idea in approved_ideas:
                logger.info(f"💡 Processing user idea: {idea.user_title}")
                try:
                    profile_name = profiles_active[0]  # default profile
                    profile = load_profile(profile_name)
                    symbol = symbols[0]
                    timeframe = timeframes[0]
                    
                    strategy_dict = idea.structured_strategy
                    if not strategy_dict:
                        idea.status = "rejected"
                        session.commit()
                        continue
                    
                    # Salva come strategia in pipeline
                    strategy_db = Strategy(
                        profile=profile_name,
                        source="user_idea",
                        user_idea_id=idea.id,
                        name=f"USER_{strategy_dict.get('name', idea.user_title)[:50]}",
                        hypothesis=strategy_dict.get("hypothesis", ""),
                        strategy_type=strategy_dict.get("strategy_type", "unknown"),
                        symbol=symbol,
                        timeframe=timeframe,
                        parameters=strategy_dict.get("parameters", {}),
                    )
                    session.add(strategy_db)
                    session.flush()
                    
                    # Pipeline: codegen → compile → backtest → validate
                    code, mq5_path = self.codegen.generate(
                        strategy=strategy_dict, profile=profile,
                        prop_firm=prop_firm, prop_phase=prop_phase, symbol=symbol,
                        output_dir=Path("strategies_archive") / "user_ideas",
                    )
                    strategy_db.mql5_code = code
                    strategy_db.mql5_path = str(mq5_path)
                    
                    success, errors = self.backtester.compile_ea(mq5_path)
                    strategy_db.compiled = success
                    if not success:
                        idea.status = "rejected"
                        session.commit()
                        continue
                    
                    # Aggiorna stato idea
                    idea.status = "in_pipeline"
                    idea.linked_strategy_id = strategy_db.id
                    session.commit()
                    
                    logger.success(f"   ✅ User idea {idea.id} entered pipeline as strategy {strategy_db.id}")
                
                except Exception as e:
                    logger.exception(f"Error processing user idea {idea.id}: {e}")
                    continue
            
            # === PIPELINE per ogni profilo attivo ===
            for profile_name in profiles_active:
                profile = load_profile(profile_name)
                
                for i in range(max_per_cycle):
                    try:
                        # === STEP 1: Spec/Custom decision via SpecGenerator ===
                        spec = self.spec_gen.generate(
                            profile=profile,
                            prop_firm=prop_firm,
                            prop_phase=prop_phase,
                            market_summary=market_summary,
                            previous_strategies=recent_dicts,
                        )
                        generated += 1
                        
                        mode = spec.get("mode", "spec")
                        symbol = spec.get("selected_symbol", "EURUSD")
                        timeframe = spec.get("selected_timeframe", "H1")
                        
                        # Aggiorna recent_dicts per anti-duplicate intra-ciclo
                        recent_dicts.append({
                            "name": spec.get("name", "?"),
                            "strategy_type": spec.get("strategy_type", "?"),
                            "symbol": symbol,
                            "timeframe": timeframe,
                        })
                        
                        # Salva in DB
                        strategy_db = Strategy(
                            profile=profile_name,
                            name=spec.get("name", "Unnamed"),
                            hypothesis=spec.get("hypothesis", ""),
                            strategy_type=spec.get("strategy_type", "unknown"),
                            symbol=symbol,
                            timeframe=timeframe,
                            parameters=spec.get("framework_params") or spec.get("parameters", {}),
                        )
                        session.add(strategy_db)
                        session.flush()
                        
                        # === BIFORCAZIONE in base al mode ===
                        if mode == "spec":
                            # ──── MODE SPEC: usa framework + .set file ────
                            logger.info(f"   📋 SPEC mode → generating .set file (no compile needed)")
                            
                            ea_name = f"PA_{profile_name}_{spec['name']}_{symbol}".replace(" ", "_")[:50]
                            ea_name = "".join(c for c in ea_name if c.isalnum() or c == "_")
                            set_path = Path("strategies_archive") / profile_name / f"{ea_name}.set"
                            
                            self.spec_gen.build_set_file(spec, set_path)
                            strategy_db.mql5_path = str(set_path)
                            strategy_db.compiled = True   # framework è già compilato
                            session.commit()
                            
                            # mq5_path per il backtest punta al framework, ma con .set
                            mq5_path = Path("templates") / "PropAgentFramework.mq5"
                            compiled_count += 1
                        
                        else:
                            # ──── MODE CUSTOM: codegen tradizionale ────
                            logger.info(f"   🛠️  CUSTOM mode → generating full MQL5 (custom logic required)")
                            
                            # Per il codegen serve il dict in formato vecchio
                            strategy_for_codegen = {
                                "name": spec.get("name"),
                                "hypothesis": spec.get("hypothesis"),
                                "entry_logic": spec.get("entry_logic", {}),
                                "exit_logic": spec.get("exit_logic", {}),
                                "indicators": spec.get("indicators", []),
                                "parameters": spec.get("parameters", {}),
                            }
                            
                            code, mq5_path = self.codegen.generate(
                                strategy=strategy_for_codegen,
                                profile=profile,
                                prop_firm=prop_firm,
                                prop_phase=prop_phase,
                                symbol=symbol,
                                output_dir=Path("strategies_archive") / profile_name,
                            )
                            strategy_db.mql5_code = code
                            strategy_db.mql5_path = str(mq5_path)
                            
                            # Compile con auto-fix retry
                            success, errors = self.backtester.compile_ea(mq5_path)
                            
                            if not success:
                                logger.warning(f"⚠️  Compile failed, attempting auto-fix...")
                                try:
                                    fixed_code = self.codegen.fix_compile_errors(
                                        original_code=code,
                                        compile_errors=errors,
                                        mq5_path=mq5_path,
                                    )
                                    code = fixed_code
                                    strategy_db.mql5_code = fixed_code
                                    success, errors = self.backtester.compile_ea(mq5_path)
                                    if success:
                                        logger.success(f"✅ Auto-fix succeeded")
                                except Exception as e:
                                    logger.error(f"   Auto-fix exception: {e}")
                            
                            strategy_db.compiled = success
                            strategy_db.compile_errors = errors if not success else None
                            session.commit()
                            
                            if not success:
                                logger.warning(f"⏭  Skipping {spec['name']} (compile failed even after auto-fix)")
                                continue
                            compiled_count += 1
                        
                        # === STEP 4: Backtest ===
                        years = self.config.get("backtest.history_years", 3)
                        date_to = datetime.utcnow()
                        date_from = date_to - timedelta(days=365 * years)
                        
                        # In SPEC mode, l'EA è il framework e i parametri sono nel .set
                        # In CUSTOM mode, l'EA è il file .mq5 generato e .set è None
                        if mode == "spec":
                            bt_ea_name = "QuantAgentFramework"
                            bt_set_file = Path(strategy_db.mql5_path)
                        else:
                            bt_ea_name = mq5_path.stem
                            bt_set_file = None
                        
                        bt_result = self.backtester.run_backtest(
                            ea_name=bt_ea_name,
                            symbol=symbol,
                            timeframe=timeframe,
                            date_from=date_from,
                            date_to=date_to,
                            deposit=account_size,
                            set_file=bt_set_file,
                        )
                        backtested += 1
                        
                        # Salva backtest
                        bt_db = Backtest(
                            strategy_id=strategy_db.id,
                            date_from=date_from,
                            date_to=date_to,
                            initial_deposit=bt_result.initial_deposit,
                            final_balance=bt_result.final_balance,
                            net_profit=bt_result.net_profit,
                            profit_factor=bt_result.profit_factor,
                            sharpe_ratio=bt_result.sharpe_ratio,
                            max_drawdown_money=bt_result.max_drawdown_money,
                            max_drawdown_pct=bt_result.max_drawdown_pct,
                            total_trades=bt_result.total_trades,
                            winning_trades=bt_result.winning_trades,
                            losing_trades=bt_result.losing_trades,
                            win_rate=bt_result.win_rate,
                            avg_win=bt_result.avg_win,
                            avg_loss=bt_result.avg_loss,
                            largest_win=bt_result.largest_win,
                            largest_loss=bt_result.largest_loss,
                            max_consecutive_wins=bt_result.max_consecutive_wins,
                            max_consecutive_losses=bt_result.max_consecutive_losses,
                            report_html_path=bt_result.report_xml_path,
                        )
                        session.add(bt_db)
                        session.flush()
                        
                        # === STEP 5: Quick validation (skip walk-forward se fallisce subito) ===
                        validator = PropValidator(prop_firm, prop_phase, account_size)
                        quick_validation = validator.validate(
                            backtest=bt_result,
                            profile_thresholds=profile.get("validation_thresholds", {}),
                        )
                        
                        if not quick_validation.passes:
                            logger.warning(
                                f"⏭  {strategy_dict['name']} fails quick validation "
                                f"({len(quick_validation.violations)} violations)"
                            )
                            bt_db.passes_prop_rules = False
                            bt_db.prop_violations = quick_validation.violations
                            session.commit()
                            continue
                        
                        # === STEP 6: Walk-forward (solo per quelli promettenti) ===
                        logger.info(f"🔬 Running walk-forward for {strategy_dict['name']}")
                        wf_result = self.walk_forward.analyze(
                            backtest_func=self.backtester.run_backtest,
                            ea_name=mq5_path.stem,
                            symbol=symbol,
                            timeframe=timeframe,
                            date_from=date_from,
                            date_to=date_to,
                            deposit=account_size,
                        )
                        bt_db.walk_forward_score = wf_result.consistency_score
                        bt_db.walk_forward_results = {
                            "in_sample_avg_pf": wf_result.in_sample_avg_pf,
                            "out_sample_avg_pf": wf_result.out_sample_avg_pf,
                            "consistency_score": wf_result.consistency_score,
                            "splits_passed": wf_result.splits_passed,
                            "individual": wf_result.individual_results,
                        }
                        
                        # === STEP 7: Final validation con WF ===
                        final_validation = validator.validate(
                            backtest=bt_result,
                            profile_thresholds=profile.get("validation_thresholds", {}),
                            wf_consistency_score=wf_result.consistency_score,
                        )
                        bt_db.passes_prop_rules = final_validation.passes
                        bt_db.prop_violations = final_validation.violations
                        bt_db.estimated_pass_days = final_validation.estimated_pass_days
                        session.commit()
                        
                        if not final_validation.passes:
                            logger.warning(f"⏭  {strategy_dict['name']} fails after WF")
                            continue
                        
                        # === STEP 8: AI final analysis ===
                        analysis, verdict = self.analyzer.analyze(
                            strategy=strategy_dict,
                            backtest_result=bt_result,
                            validation_report=final_validation,
                            wf_result=wf_result,
                        )
                        
                        if verdict == "REJECT":
                            logger.warning(f"⏭  {strategy_dict['name']} rejected by analyzer")
                            continue
                        
                        # === STEP 9: Save as candidate ===
                        candidate = Candidate(
                            backtest_id=bt_db.id,
                            overall_score=final_validation.score,
                            ai_analysis=analysis,
                            recommendation=verdict,
                            notified_at=datetime.utcnow(),
                        )
                        session.add(candidate)
                        session.commit()
                        candidates_found += 1
                        
                        logger.success(
                            f"🎯 CANDIDATE: {strategy_dict['name']} | "
                            f"Score {final_validation.score}/100 | {verdict}"
                        )
                        
                        # === STEP 10: Notify ===
                        self.notifier.notify_candidate(
                            ea_name=mq5_path.stem,
                            profile_name=profile["name"],
                            symbol=symbol,
                            score=final_validation.score,
                            verdict=verdict,
                            backtest=bt_result,
                            wf_consistency=wf_result.consistency_score,
                        )
                    
                    except Exception as e:
                        logger.exception(f"Error in pipeline iteration: {e}")
                        continue
            
            # Cycle complete
            cycle_log.completed_at = datetime.utcnow()
            cycle_log.status = "completed"
            cycle_log.strategies_generated = generated
            cycle_log.strategies_compiled = compiled_count
            cycle_log.backtests_run = backtested
            cycle_log.candidates_found = candidates_found
            session.commit()
            
            duration = (datetime.utcnow() - cycle_start).total_seconds() / 60
            logger.info(
                f"\n{'='*60}\n"
                f"✅ CYCLE #{self.cycle_count} DONE in {duration:.1f}min\n"
                f"   Generated: {generated} | Compiled: {compiled_count} | "
                f"Backtested: {backtested} | Candidates: {candidates_found}\n"
                f"{'='*60}\n"
            )
            
            self.notifier.notify_cycle_summary(
                self.cycle_count, generated, compiled_count, backtested,
                candidates_found, duration,
            )
        
        except Exception as e:
            logger.exception(f"Cycle failed: {e}")
            cycle_log.status = "failed"
            cycle_log.error_message = str(e)
            session.commit()
            self.notifier.notify_error(str(e), "orchestrator")
        finally:
            session.close()
    
    def start(self, run_now: bool = True):
        """Avvia lo scheduler in modalità blocking.
        
        Args:
            run_now: se True, esegue subito il primo ciclo prima di schedulare i successivi.
        """
        cycle_hours = self.config.get("orchestrator.cycle_hours", 4)
        
        # Usa datetime.now() (locale) invece di utcnow() per evitare problemi timezone
        scheduler = BlockingScheduler()
        scheduler.add_job(
            self.run_cycle,
            "interval",
            hours=cycle_hours,
            next_run_time=datetime.now() + timedelta(seconds=5 if run_now else cycle_hours * 3600),
            id="main_cycle",
            misfire_grace_time=300,  # tollera fino a 5 min di ritardo
        )
        
        logger.info(f"⏰ Scheduler started: cycle every {cycle_hours}h")
        if run_now:
            logger.info(f"   First cycle starts in 5 seconds")
        logger.info("   Press Ctrl+C to stop")
        
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("👋 Shutdown requested")
            scheduler.shutdown()


def main():
    config_path = "config.yaml"
    run_now = True
    
    # Parsing argomenti semplice
    args = sys.argv[1:]
    if "--no-immediate" in args:
        run_now = False
        args.remove("--no-immediate")
    if "--once" in args:
        # Modalità: esegui un ciclo e basta (utile per test)
        args.remove("--once")
        if args:
            config_path = args[0]
        orchestrator = Orchestrator(config_path)
        orchestrator.run_cycle()
        return
    
    if args:
        config_path = args[0]
    
    orchestrator = Orchestrator(config_path)
    orchestrator.start(run_now=run_now)


if __name__ == "__main__":
    main()
