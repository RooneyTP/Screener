#!/usr/bin/env python3
"""
Master Daily Market Analysis Runner
Chains: Screening → Research → Report → Telegram

Usage:
  python run_daily_analysis.py              # Full run
  python run_daily_analysis.py --mock      # Use mock screener (testing)
  python run_daily_analysis.py --dry-run   # Skip Telegram, print only
"""

import sys
import subprocess
import configparser
from pathlib import Path
from datetime import datetime

SCREENER_DIR = Path("C:\\Hermes_Workspace\\Screener")
CONFIG_FILE = SCREENER_DIR / "config.ini"

def load_config():
    """Load config.ini, return section dicts + flags"""
    config = configparser.ConfigParser()
    config.read(str(CONFIG_FILE))
    
    screening = dict(config["screening"])
    research = dict(config["research"])
    telegram = dict(config["telegram"])
    logging = dict(config["logging"])
    
    return screening, research, telegram, logging


def detect_screener(screening_cfg, use_mock=False):
    """
    Auto-detect available screener script.
    Priority: manual path > screener_mock.py (if --mock) > screener.py > screener_mock.py
    """
    
    script_setting = screening_cfg.get("screener_script", "auto")
    
    if use_mock:
        mock_path = SCREENER_DIR / "screener_mock.py"
        if mock_path.exists():
            return mock_path
        print("ERROR: screener_mock.py not found even with --mock flag")
        return None
    
    if script_setting.lower() not in ["auto", ""]:
        custom_path = SCREENER_DIR / script_setting
        if custom_path.exists():
            return custom_path
        print(f"WARNING: Configured script '{script_setting}' not found, falling back")
    
    # Auto-detect: prefer real screener, fallback to mock
    real_path = SCREENER_DIR / "screener.py"
    mock_path = SCREENER_DIR / "screener_mock.py"
    
    if real_path.exists():
        return real_path
    elif mock_path.exists():
        print("NOTE: No screener.py found, using screener_mock.py for testing")
        return mock_path
    else:
        print("ERROR: No screener script found (tried screener.py, screener_mock.py)")
        return None


def run_step(script_path, description):
    """Run a step and handle errors"""
    script_name = script_path.name
    
    print(f"\n{'='*60}")
    print(f"STEP: {description}")
    print(f"Script: {script_name}")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print('='*60 + "\n")
    
    if not script_path.exists():
        print(f"ERROR: Script not found: {script_path}")
        return False
    
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min per step
            cwd=str(SCREENER_DIR)
        )
        
        # Print output
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr, file=sys.stderr)
        
        if result.returncode != 0:
            print(f"\n❌ {description} FAILED (exit code {result.returncode})")
            return False
        
        print(f"\n✅ {description} COMPLETED")
        return True
        
    except subprocess.TimeoutExpired:
        print(f"\n❌ {description} TIMEOUT (>10 minutes)")
        return False
    except Exception as e:
        print(f"\n❌ {description} ERROR: {e}")
        return False


def main():
    # Parse flags
    use_mock = "--mock" in sys.argv
    dry_run = "--dry-run" in sys.argv
    
    # Load config
    screening_cfg, research_cfg, telegram_cfg, logging_cfg = load_config()
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          DAILY MARKET ANALYSIS — AUTOMATED RUN               ║
║          Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}                        ║
║          Mode: {'MOCK' if use_mock else 'REAL'} {'DRY-RUN' if dry_run else 'LIVE'}        ║
╚══════════════════════════════════════════════════════════════╝
""")
    
    # Detect screener
    screener_script = detect_screener(screening_cfg, use_mock=use_mock)
    if screener_script is None:
        print("❌ Cannot continue without a screener script")
        print("   Create screener.py or run with --mock for testing")
        return 1
    
    # Step 1: Run screening
    print(f"Using screener: {screener_script.name}")
    if not run_step(screener_script, "Stock Screening"):
        print("\n⚠️  Screening failed, attempting to continue with research...")
    
    # Step 1.5: Run bridge (CSV → JSON) if using real screener
    if not use_mock and screener_script.name == "screener.py":
        bridge_script = SCREENER_DIR / "daily_screening_runner.py"
        if bridge_script.exists():
            print("\n🔄 Converting CSV to JSON format...")
            if not run_step(bridge_script, "CSV → JSON Bridge"):
                print("\n❌ Bridge failed - cannot continue")
                return 1
        else:
            print("\n⚠️ WARNING: Bridge script not found, skipping CSV conversion")
    
    # Step 2: Run research & report (wajib pakai v2)
    reporter_script = SCREENER_DIR / "daily_research_reporter_v2.py"
    
    if not reporter_script.exists():
        print("❌ ERROR: daily_research_reporter_v2.py not found!")
        return 1
    
    print("✓ Using v2 reporter (IHSG-based sentiment)")
    
    if not run_step(reporter_script, "News Research & Report"):
        print("\n❌ Research step failed - check logs")
        return 1
    
    print(f"""
╔══════════════════════════════════════════════════════════════╗
║          ALL STEPS COMPLETED                                 ║
║          Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}                       ║
╚══════════════════════════════════════════════════════════════╝
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
