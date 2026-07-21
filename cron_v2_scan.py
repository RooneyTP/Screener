#!/usr/bin/env python3
"""
Cron Wrapper — IDX Alpha Screener v2 Scan (21:00 WIB)
======================================================
Dipanggil oleh cron Hermes setiap 21:00 WIB.
Pipeline:
  1. python main.py --parallel --telegram → scan saham IHSG + kirim ke Telegram
  2. stdout → cron agent untuk compose ringkasan tambahan (jika perlu)

Output: CSV tersimpan + Telegram terkirim otomatis.
"""

import sys
import os
import subprocess
from datetime import datetime

SCREENER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "idx_alpha_screener")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

today = datetime.now().strftime("%Y-%m-%d")
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")

print(f"IDX Alpha Screener v2 — Cron Scan {now}")
print(f"Scan saham IHSG dengan parallel fetch + swing gate + threshold >=55")
print("=" * 60)

# ── 1. Run v2 screener ──
print(f"[{now}] Starting scan via main.py --parallel --telegram --no-ihsg --top 10")
sys.stdout.flush()

try:
    result = subprocess.run(
        [sys.executable, "main.py", "--parallel", "--telegram", "--no-ihsg", "--top", "10"],
        cwd=SCREENER_DIR,
        capture_output=True,
        text=True,
        timeout=300,  # 5 menit untuk ~200 saham parallel
    )

    if result.stdout:
        print(result.stdout)
        sys.stdout.flush()
    if result.stderr:
        print(f"[stderr]\n{result.stderr}")
        sys.stdout.flush()

    if result.returncode != 0:
        print(f"\n❌ Screener gagal (exit code {result.returncode})")
        sys.exit(1)

except subprocess.TimeoutExpired:
    print(f"\n❌ Screener timeout — main.py tidak selesai dalam 5 menit")
    print("   Penyebab: yfinance mungkin lambat atau rate-limited.")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Screener gagal: {e}")
    sys.exit(1)

# ── 2. Copy hasil CSV ke output/ dengan timestamp ──
csv_src = os.path.join(SCREENER_DIR, "screener_v2_result.csv")
if os.path.exists(csv_src):
    csv_dst = os.path.join(OUTPUT_DIR, f"screener_v2_result_{today}.csv")
    # Also copy to root Screener dir for bot to find
    root_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screener_v2_result.csv")
    with open(csv_src) as f:
        data = f.read()
    with open(csv_dst, "w") as f:
        f.write(data)
    with open(root_csv, "w") as f:
        f.write(data)
    print(f"✅ CSV disimpan ke {csv_dst}")
else:
    print("⚠️  CSV tidak ditemukan — scan mungkin 0 hasil")

print(f"\nSelesai: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}")
print("=" * 60)
