#!/usr/bin/env python3
"""Cron Wrapper — V7 Dual Mode Scanner (21:00 WIB)
Telegram delivery is handled inside v7_scan.py via send_telegram_sync."""
import sys, os, subprocess
SCREENER_DIR = r"C:\Hermes_Workspace\Screener\idx_alpha_screener"
# Gunakan Hermes venv python yang punya invezgo-sdk
VENV_PYTHON = r"C:\Users\yanli\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
PYTHON = VENV_PYTHON if os.path.exists(VENV_PYTHON) else sys.executable
print(f"{'='*50}\n  V7 DUAL MODE SCAN\n  [cron_v3_scan.py] ...")
sys.stdout.flush()
try:
    result = subprocess.run([PYTHON, "v7_scan.py"], cwd=SCREENER_DIR, capture_output=True, text=True, timeout=600)
    if result.stdout: print(result.stdout)
    if result.stderr:
        err = [l for l in result.stderr.split('\n') if 'ERROR' in l or 'Traceback' in l]
        if err: print("\n".join(err[:3]))
    print(f"\n{'='*50}\n  {'✅ Selesai' if result.returncode==0 else '❌ Gagal'} (exit {result.returncode})\n{'='*50}")
except Exception as e:
    print(f"\n❌ Error: {e}")
