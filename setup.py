"""
Setup script — verifica installazione e prepara il sistema.

Esegui:
    python setup.py
"""
import sys
from pathlib import Path
import shutil


def check_python_version():
    if sys.version_info < (3, 11):
        print("❌ Python 3.11+ richiesto. Versione attuale:", sys.version)
        return False
    print(f"✅ Python {sys.version.split()[0]}")
    return True


def check_config():
    if not Path("config.yaml").exists():
        if Path("config.example.yaml").exists():
            shutil.copy("config.example.yaml", "config.yaml")
            print("📝 config.yaml creato da example. EDITA prima di lanciare!")
            return False
        print("❌ config.example.yaml non trovato")
        return False
    print("✅ config.yaml presente")
    return True


def check_mt5():
    try:
        import MetaTrader5 as mt5
        print(f"✅ MetaTrader5 module v{mt5.__version__}")
        return True
    except ImportError:
        print("⚠️  MetaTrader5 non installato (richiesto solo su Windows)")
        return False


def check_anthropic():
    try:
        import anthropic
        print(f"✅ Anthropic SDK v{anthropic.__version__}")
        return True
    except ImportError:
        print("❌ pip install anthropic")
        return False


def init_db():
    from config import Config
    from db.database import init_db as db_init
    config = Config()
    db_init(config.get("database.url"))
    print("✅ Database inizializzato")


def make_dirs():
    for d in ["logs", "strategies_archive", "db"]:
        Path(d).mkdir(exist_ok=True)
    print("✅ Directory create")


def main():
    print("\n🤖 PROP AGENT SYSTEM — Setup\n")
    
    checks = [
        check_python_version(),
        check_anthropic(),
        check_config(),
    ]
    check_mt5()
    
    if all(checks):
        make_dirs()
        try:
            init_db()
        except Exception as e:
            print(f"⚠️  DB init fallito: {e}")
        
        print("\n✅ SETUP COMPLETO\n")
        print("Prossimi step:")
        print("1. Edita config.yaml con le tue credenziali")
        print("2. Lancia il sistema: python orchestrator.py")
        print("3. (opzionale) Apri dashboard: uvicorn dashboard.api:app\n")
    else:
        print("\n⚠️  Risolvi gli errori sopra prima di procedere\n")


if __name__ == "__main__":
    main()
