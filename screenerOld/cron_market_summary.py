#!/usr/bin/env python3
"""
Cron Wrapper — Daily Market Summary (agent mode with news research)
====================================================================
Dipanggil oleh cron Hermes setiap 21:00 WIB.
CRON_AGENT_MODE=true → reporter hanya generate report (TIDAK kirim Telegram).

Pipeline:
  1. daily_screening_runner.py  → candidates_{date}.json
  2. daily_research_reporter_v2.py → report file + stdout (CRON_AGENT_MODE)
  3. stdout injected sebagai context ke agent (dengan agent-reach skill)
  4. Agent: web_search berita menarik → compose final message → deliver Telegram
"""

import sys
import subprocess
from pathlib import Path
from datetime import datetime

SCREENER_DIR = Path("C:/Hermes_Workspace/Screener")
OUTPUT_DIR = Path("C:/Hermes_Workspace/output")
LOGS_DIR = Path("C:/Hermes_Workspace/logs")

today = datetime.now().strftime("%Y-%m-%d")
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")

print(f"📅 MARKET SUMMARY — {today}")
print(f"⏰ {now}")
print("─" * 40)
print()

errors = []

# ── Agent mode env: sub-agen tidak perlu kirim Telegram langsung ──
CRON_ENV = os.environ.copy()
CRON_ENV["CRON_AGENT_MODE"] = "true"

# ── Step 0: Hapus candidates lama (biar fresh) ──
candidates_file = OUTPUT_DIR / f"candidates_{today}.json"
if candidates_file.exists():
    candidates_file.unlink()
    print("🧹 Old candidates file removed — will generate fresh")

# ── Step 1: Run screening ──
print("📥 Step 1: Screening...")
screener_script = SCREENER_DIR / "daily_screening_runner.py"
if not screener_script.exists():
    msg = "❌ daily_screening_runner.py not found"
    print(msg)
    errors.append(msg)
else:
    result = subprocess.run(
        [sys.executable, str(screener_script)],
        capture_output=True, text=True, timeout=600,
        cwd=str(SCREENER_DIR), env=CRON_ENV
    )
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            if any(kw in line for kw in ["✓", "✅", "❌", "ERROR", "WARNING", "INFO", "candidates"]):
                print(f"   {line.strip()}")
    if result.returncode != 0:
        msg = f"Screening exit code {result.returncode}"
        print(f"   ⚠️  {msg}")
        errors.append(msg)
    else:
        print("   ✅ Screening OK")

# ── Step 2: Check candidates ──
if candidates_file.exists():
    import json
    with open(candidates_file) as f:
        data = json.load(f)
    cands = data.get("candidates", data if isinstance(data, list) else [])
    print(f"   📊 Candidates found: {len(cands)}")
else:
    print("   ⚠️  No candidates file — reporter will use empty list")

# ── Step 3: Run reporter & send Telegram ──
print()
print("📤 Step 2: Research + Report + Telegram...")
reporter_script = SCREENER_DIR / "daily_research_reporter_v2.py"
if not reporter_script.exists():
    msg = "❌ daily_research_reporter_v2.py not found"
    print(msg)
    errors.append(msg)
else:
    result = subprocess.run(
        [sys.executable, str(reporter_script)],
        capture_output=True, text=True, timeout=300,
        cwd=str(SCREENER_DIR), env=CRON_ENV
    )
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            print(f"   {line.strip()}")
    if result.returncode != 0:
        errors.append(f"Reporter exit code {result.returncode}")

# ── Final status ──
print()
print("─" * 40)
if errors:
    print(f"⚠️  SELESAI dengan {len(errors)} error(s):")
    for e in errors:
        print(f"   • {e}")
    sys.exit(1)
else:
    print("✅ SELESAI — Data siap untuk agent news research!")
    sys.exit(0)
