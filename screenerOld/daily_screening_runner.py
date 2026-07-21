#!/usr/bin/env python3
"""
Daily Screening Runner - Bridge between screener.py and daily analysis system

This script:
1. Detects if CSV already exists (from previous pipeline step)
2. If not, runs screener.py to generate CSV
3. Parses CSV, extracts BUY signals
4. Converts to JSON format expected by daily_research_reporter_v2.py
5. Saves as candidates_{date}.json
"""

import os
import sys
import json
import subprocess
import pandas as pd
from datetime import datetime
from pathlib import Path

# Paths
WORKSPACE = Path("C:\\Hermes_Workspace")
SCREENER_DIR = WORKSPACE / "Screener"
OUTPUT_DIR = WORKSPACE / "output"
LOGS_DIR = WORKSPACE / "logs"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)


def run_screener():
    """Run the real screener.py"""
    screener_path = SCREENER_DIR / "screener.py"
    if not screener_path.exists():
        print(f"❌ ERROR: screener.py not found at {screener_path}")
        return False

    print("🔍 Running IHSG Screener v10.0...")
    print("   (This may take 2-5 minutes depending on market data)")

    try:
        result = subprocess.run(
            [sys.executable, str(screener_path)],
            cwd=str(SCREENER_DIR),
            capture_output=True, text=True,
            timeout=600
        )
        if result.stdout:
            print("\n" + "=" * 60)
            print("SCREENER OUTPUT (last 2000 chars):")
            print("=" * 60)
            print(result.stdout[-2000:])
            print("=" * 60 + "\n")
        if result.returncode != 0:
            print(f"⚠️ Screener exit code: {result.returncode}")
            if result.stderr:
                print(f"STDERR: {result.stderr}")
        print("✓ Screener completed")
        return True
    except subprocess.TimeoutExpired:
        print("❌ ERROR: Screener timeout (>10 min)")
        return False
    except Exception as e:
        print(f"❌ ERROR running screener: {e}")
        return False


def parse_csv_to_candidates(csv_path):
    """Parse CSV and extract BUY signal candidates"""
    if not csv_path.exists():
        print(f"❌ ERROR: CSV not found: {csv_path}")
        return None

    print(f"📊 Parsing CSV: {csv_path}")
    try:
        df = pd.read_csv(csv_path)
        print(f"   Total rows: {len(df)}")

        buy_signals = ["ULTRA_BUY", "STRONG_BUY", "BUY"]
        df_buy = df[df["Sinyal"].isin(buy_signals)].copy()
        print(f"   BUY signals: {len(df_buy)}")

        if df_buy.empty:
            print("   ⚠️ No BUY signals today")
            return []

        df_buy = df_buy.sort_values("Confidence%", ascending=False)

        signal_map = {
            "ULTRA_BUY": "breakout",
            "STRONG_BUY": "momentum",
            "BUY": "mean-reversion"
        }

        candidates = []
        for _, row in df_buy.iterrows():
            ticker = str(row.get("Ticker", "")).replace(".JK", "")
            candidate = {
                "ticker": ticker,
                "name": row.get("Ticker", ticker),
                "price": int(row.get("Harga", 0)),
                "score": float(row.get("Skor", 0)),
                "signal": signal_map.get(row.get("Sinyal"), "unknown"),
                "signal_raw": row.get("Sinyal", ""),
                "confidence": int(row.get("Confidence%", 0)),
                "sector": row.get("Sektor", "Unknown"),
                "strength": row.get("Strength", ""),
                "rsi": float(row.get("RSI", 0)) if pd.notna(row.get("RSI")) else None,
                "adx": float(row.get("ADX", 0)) if pd.notna(row.get("ADX")) else None,
                "volume_spike": row.get("Volume_Spike", ""),
                "mm_activity": row.get("MM_Activity", ""),
                "mm_confidence": int(row.get("MM_Confidence", 0)) if pd.notna(row.get("MM_Confidence")) else 0,
                "stop_loss": int(row.get("Stop_Loss", 0)) if pd.notna(row.get("Stop_Loss")) else None,
                "target": int(row.get("Target_1", 0)) if pd.notna(row.get("Target_1")) else None,
                "rrr": float(row.get("RRR", 0)) if pd.notna(row.get("RRR")) else None,
                "ai_win_prob": int(row.get("AI_Win_Prob%", 0)) if pd.notna(row.get("AI_Win_Prob%")) else 0,
                "ai_verdict": row.get("AI_Verdict", ""),
            }
            candidates.append(candidate)

        print(f"   ✓ Extracted {len(candidates)} candidates")
        for i, c in enumerate(candidates[:3], 1):
            print(f"   {i}. {c['ticker']:<8} | Score: {c['score']:.1f} | Conf: {c['confidence']}% | {c['signal_raw']}")
        return candidates
    except Exception as e:
        print(f"❌ ERROR reading CSV: {e}")
        return None


def save_candidates_json(candidates, date_str):
    """Save candidates to JSON"""
    output_file = OUTPUT_DIR / f"candidates_{date_str}.json"
    output = {
        "timestamp": datetime.now().isoformat(),
        "screening_date": date_str,
        "market": "IHSG",
        "screener_version": "v10.0",
        "source": "screener.py",
        "total_candidates": len(candidates),
        "candidates": candidates
    }
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    print(f"✓ Saved: {output_file}")
    return output_file


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    today_compact = datetime.now().strftime("%Y%m%d")

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           DAILY SCREENING RUNNER - {today}         ║
╚══════════════════════════════════════════════════════════════╝
""")

    # Cek CSV sudah ada atau belum
    csv_path = SCREENER_DIR / f"screener_ihsg_{today_compact}.csv"
    if csv_path.exists():
        print(f"📁 CSV exists: {csv_path.name}")
        print("   Skipping screener (already run in previous step)")
    else:
        if not run_screener():
            print("\n❌ FAILED: Could not run screener")
            return 1
        # Cari CSV kembali
        csv_path = SCREENER_DIR / f"screener_ihsg_{today_compact}.csv"
        if not csv_path.exists():
            csv_path = Path(f"screener_ihsg_{today_compact}.csv")
            if not csv_path.exists():
                print(f"❌ CSV not found after screener run")
                return 1

    # Parse CSV
    candidates = parse_csv_to_candidates(csv_path)
    if candidates is None:
        print("\n❌ FAILED: Could not parse CSV")
        return 1

    # Save JSON
    save_candidates_json(candidates, today)

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║  SCREENING COMPLETED                                         ║
║  Candidates: {len(candidates):<2}                                             ║
║  Sent to:  output/candidates_{today}.json            ║
╚══════════════════════════════════════════════════════════════╝
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
