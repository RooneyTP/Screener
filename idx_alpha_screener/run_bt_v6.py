"""Run backtest with all tickers from CSV (fresh data, not stale)."""
import csv, subprocess, sys, os

with open('screener_v2_result.csv') as f:
    tickers = [r['ticker'].strip() for r in csv.DictReader(f)]

cmd = [
    sys.executable, 'backtest.py', '--ticker'
] + tickers

print(f"Running backtest on {len(tickers)} tickers...")
result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
print(f"Exit code: {result.returncode}")
