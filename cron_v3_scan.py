#!/usr/bin/env python3
"""
Cron Wrapper — IDX Alpha Screener v4 (21:00 WIB)
===================================================
Dipanggil oleh cron Hermes setiap 21:00 WIB.

Pipeline:
  1. python main.py --top 125 --no-ihsg --telegram --v4
  2. Cetak ringkasan
"""
import sys, os, subprocess
from datetime import datetime

SCREENER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "idx_alpha_screener")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

now = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")
today = datetime.now().strftime("%Y-%m-%d")
print(f"{'='*60}\n  IDX ALPHA SCREENER v4 — {now}\n{'='*60}")
sys.stdout.flush()

try:
    result = subprocess.run(
        [sys.executable, "main.py", "--top", "125", "--no-ihsg", "--telegram", "--v4"],
        cwd=SCREENER_DIR, capture_output=True, text=True, timeout=600
    )
    if result.stdout:
        lines = result.stdout.strip().split("\n")
        summary = [l for l in lines if any(k in l for k in ["REKOMENDASI","Ticker","BUY","HOLD","SELL","Saham discan","🟢","=","Selesai"])]
        print("\n".join(summary[-30:]) if summary else result.stdout[-2000:])
    sys.stdout.flush()
    if result.returncode != 0:
        print(f"❌ Gagal (exit {result.returncode})"); sys.exit(1)
except subprocess.TimeoutExpired:
    print("❌ Timeout"); sys.exit(1)
except Exception as e:
    print(f"❌ {e}"); sys.exit(1)

csv_src = os.path.join(SCREENER_DIR, "screener_v2_result.csv")
if os.path.exists(csv_src):
    with open(csv_src) as f: data = f.read()
    with open(os.path.join(OUTPUT_DIR, f"screener_v4_{today}.csv"), "w") as f: f.write(data)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "screener_v4_result.csv"), "w") as f: f.write(data)
    print(f"✅ CSV saved")

print(f"\n{'='*60}\n  Selesai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}\n{'='*60}")
