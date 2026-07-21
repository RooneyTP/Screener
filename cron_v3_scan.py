#!/usr/bin/env python3
"""
Cron Wrapper — IDX Alpha Screener v3 (21:00 WIB)
===================================================
Dipanggil oleh cron Hermes setiap 21:00 WIB.

Pipeline:
  1. python main.py --top 75 --no-ihsg --telegram
     → scan 75+ saham liquid IHSG, skor dengan engine v3
     → kirim rekomendasi ke Telegram
  2. Cetak ringkasan untuk deliver log

Perubahan dari v2:
  - Scoring v3 (threshold >=62, swing gate, ADX filter)
  - Exit Strategy (hard_stop -15%, max_hold 15d, flat_exit 7d/2%, earnings blackout)
  - Portfolio Heat (max 5 posisi, max 2/sektor, 40% exposure)
  - Cache TTL 20 hari (data stale tetap HIT)
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

print(f"{'=' * 60}")
print(f"  IDX ALPHA SCREENER v3 — Cron Scan {now}")
print(f"  Engine: Scoring v3 + Swing Gate + Portfolio Heat + Exit Strategy")
print(f"{'=' * 60}")
sys.stdout.flush()

# ── 1. Run v3 screener ──
print(f"\n[{now}] Scan 75+ saham liquid IHSG (cache TTL=20d)...")
sys.stdout.flush()

try:
    result = subprocess.run(
        [sys.executable, "main.py", "--top", "125", "--no-ihsg", "--telegram"],
        cwd=SCREENER_DIR,
        capture_output=True,
        text=True,
        timeout=600,  # 10 menit untuk 75+ saham
    )

    if result.stdout:
        # Ambil hanya bagian ringkasan (20 baris terakhir)
        lines = result.stdout.strip().split("\n")
        summary_lines = [l for l in lines if any(k in l for k in 
                        ["REKOMENDASI", "Ticker", "BUY", "HOLD", "SELL",
                         "Saham discan", "Portfolio", "🔥", "🟢", "⚫", "=",
                         "Selesai"])]
        if summary_lines:
            print("\n".join(summary_lines[-30:]))
        else:
            print(result.stdout[-2000:])
        sys.stdout.flush()

    if result.stderr:
        # Filter stderr — tampilkan hanya warnings (bukan rate-limit spam)
        err_lines = [l for l in result.stderr.split("\n") if l.strip() and 
                     "Rate limited" not in l and "Too Many Requests" not in l]
        if err_lines:
            print(f"\n[stderr] {'; '.join(err_lines[-5:])}")
            sys.stdout.flush()

    if result.returncode != 0:
        print(f"\n❌ Screener gagal (exit code {result.returncode})")
        sys.exit(1)

except subprocess.TimeoutExpired:
    print(f"\n❌ Screener timeout — main.py tidak selesai dalam 10 menit")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Screener gagal: {e}")
    sys.exit(1)

# ── 2. Simpan CSV dengan timestamp ──
csv_src = os.path.join(SCREENER_DIR, "screener_v2_result.csv")
if os.path.exists(csv_src):
    csv_dst = os.path.join(OUTPUT_DIR, f"screener_v3_{today}.csv")
    root_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screener_v3_result.csv")
    with open(csv_src) as f:
        data = f.read()
    with open(csv_dst, "w") as f:
        f.write(data)
    with open(root_csv, "w") as f:
        f.write(data)
    print(f"\n✅ CSV disimpan ke {csv_dst}")
else:
    print(f"\n⚠️  CSV tidak ditemukan — scan mungkin 0 hasil")

finish = datetime.now().strftime("%Y-%m-%d %H:%M:%S WIB")
print(f"\n{'=' * 60}")
print(f"  Selesai: {finish}")
print(f"{'=' * 60}")
