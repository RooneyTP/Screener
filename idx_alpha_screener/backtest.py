"""
backtest.py — Backtest Scoring Engine v2 untuk IDX Alpha Screener
==================================================================
Menggunakan compute_all_indicators + compute_total_score + classify
dari data.py & scoring.py — BUKAN RSI+EMA20 manual.

Metode:
  - Rolling windows: tiap hari, hitung sinyal pakai data HARI INI
    (indikator sudah di-shift(1) oleh data.py → tidak ada look-ahead)
  - Ukur forward return H+1, H+3, H+5 SETELAH sinyal muncul
  - Hitung win rate PER SINYAL (STRONG_BUY, BUY, WEAK_BUY, HOLD, SELL)
  - Hitung expected return per sinyal (avg_return * win_rate)

Usage:
  python backtest.py                         # test 5 teratas dari CSV
  python backtest.py --all                   # semua dari CSV
  python backtest.py --ticker BBCA BBRI      # spesifik
  python backtest.py --single BBCA           # satu saham, detail
"""

import sys
import os
import csv
import time
import argparse
import logging
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import numpy as np

# ── Pastikan bisa import module lokal ──
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import data
import scoring as sc
import regime as rg

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backtest")

# ════════════════════════════════════════════════════════════════
#  Load fundamental data di startup (dari CSV hasil scan)
# ════════════════════════════════════════════════════════════════
_data_dir = os.path.dirname(os.path.abspath(__file__))
_funda_csv = os.path.join(_data_dir, "screener_v2_result.csv")
if os.path.exists(_funda_csv) and hasattr(sc, 'load_fundamentals'):
    sc.load_fundamentals(_funda_csv)
    logger.info(f"Fundamental loaded: {len(sc.FUNDAMENTALS)} tickers")

# ── Trading fee per round-trip ──
TRADING_FEE_PCT = 0.4  # 0.4% default
NO_FEE_MODE = False    # --no-fee flag

# ════════════════════════════════════════════════════════════════
#  EXIT STRATEGY SIMULATION
# ════════════════════════════════════════════════════════════════
def _simulate_exit_returns(df: pd.DataFrame, entry_idx: int,
                           entry_price: float, signal: str,
                           fee_pct: float = 0.4,
                           max_hold_days: int = 15,
                           flat_exit_days: int = 7,
                           flat_exit_threshold_pct: float = 2.0,
                           hard_stop_pct: float = -15.0) -> dict:
    """
    Simulasi return dengan exit strategy aktual.
    Bandingkan dengan H+5 standar.

    Returns dict:
    {exit_method: "HARD_STOP"|"FLAT_EXIT"|"MAX_HOLD"|"STANDARD",
     exit_day: int, exit_price: float, return_pct: float}
    """
    if signal not in ("STRONG_BUY", "BUY", "WEAK_BUY"):
        return {"exit_method": "NONE", "exit_day": 5, "exit_price": 0, "return_pct": 0.0}

    max_bars = min(max_hold_days, len(df) - entry_idx - 1)

    for day in range(1, max_bars + 1):
        bar_idx = entry_idx + day
        if bar_idx >= len(df):
            break
        current_price = float(df.iloc[bar_idx]["close"])
        if pd.isna(current_price) or current_price <= 0:
            continue

        ret_pct = (current_price - entry_price) / entry_price * 100
        ret_after_fee = ret_pct - fee_pct

        # 1. Hard Stop Check (cek setiap hari, termasuk hari 1)
        if ret_pct <= hard_stop_pct:
            return {
                "exit_method": "HARD_STOP",
                "exit_day": day,
                "exit_price": current_price,
                "return_pct": round(ret_after_fee, 2),
            }

        # 2. Flat Exit Check (setelah flat_exit_days)
        if day >= flat_exit_days:
            if abs(ret_pct) < flat_exit_threshold_pct:
                return {
                    "exit_method": "FLAT_EXIT",
                    "exit_day": day,
                    "exit_price": current_price,
                    "return_pct": round(ret_after_fee, 2),
                }

    # 3. Max Hold reached
    if max_bars >= max_hold_days:
        bar_idx = entry_idx + max_hold_days
        if bar_idx < len(df):
            current_price = float(df.iloc[bar_idx]["close"])
            ret_pct = (current_price - entry_price) / entry_price * 100
            ret_after_fee = ret_pct - fee_pct
            return {
                "exit_method": "MAX_HOLD",
                "exit_day": max_hold_days,
                "exit_price": current_price,
                "return_pct": round(ret_after_fee, 2),
            }

    # 4. Standard H+5 (tidak kena exit manapun)
    exit_idx = min(entry_idx + 5, len(df) - 1)
    exit_price = float(df.iloc[exit_idx]["close"])
    ret_pct = (exit_price - entry_price) / entry_price * 100
    ret_after_fee = ret_pct - fee_pct
    return {
        "exit_method": "STANDARD",
        "exit_day": 5,
        "exit_price": exit_price,
        "return_pct": round(ret_after_fee, 2),
    }


# ════════════════════════════════════════════════════════════════
#  REGIME DETECTION from a single row (no look-ahead)
# ════════════════════════════════════════════════════════════════
def _detect_regime_from_row(row: pd.Series) -> str:
    """
    Deteksi regime dari satu baris data yang sudah di-shift(1).
    Logika identik dengan detect_market_regime() di regime.py,
    tapi hanya pakai data yang tersedia di row (tidak perlu full df).
    """
    ema12 = row.get("ema12", np.nan)
    ema50 = row.get("ema50", np.nan)
    price = row.get("close", np.nan)
    adx = row.get("adx", np.nan)

    if pd.isna(ema12) or pd.isna(ema50) or pd.isna(price) or pd.isna(adx):
        return "RANGING"
    if ema50 == 0:
        return "RANGING"

    ema_diff_pct = (ema12 - ema50) / ema50 * 100
    pct_vs_ema50 = (price - ema50) / ema50 * 100

    if adx > 30 and ema_diff_pct > 1.0 and price > ema50:
        return "BULL"
    elif adx > 30 and ema_diff_pct < -1.0 and price < ema50:
        return "BEAR"
    elif adx > 30:
        return "HIGH_VOLATILITY"
    else:
        return "RANGING"


# ════════════════════════════════════════════════════════════════
#  CORE: backtest_scoring(ticker)
# ════════════════════════════════════════════════════════════════
def backtest_scoring(ticker: str) -> dict:
    """
    Backtest scoring engine v2 untuk satu ticker.

    Pipeline:
      1. fetch_prices / fetch_with_cache
      2. compute_all_indicators (semua kolom di-shift(1) — no look-ahead)
      3. Untuk setiap baris (setelah warm-up):
           a. Deteksi regime dari row
           b. compute_total_score + classify
           c. Forward return H+1, H+3, H+5
      4. Hitung win rate, avg return, expected return per sinyal

    Returns dict dengan keys:
      ticker, total_days, valid_days,
      signals: {signal: {count, wr_h1/3/5, avg_ret_h1/3/5, exp_ret_h1/3/5}}
      error (jika ada)
    """
    try:
        tkr_full = ticker if ticker.endswith(".JK") else f"{ticker}.JK"

        # ── 1. Fetch data ──
        df = data.fetch_with_cache(tkr_full, period="1y")
        if df.empty or len(df) < 120:
            return {"ticker": ticker.replace(".JK", ""), "error": "data tidak cukup (< 120 baris)"}

        # ── 2. Compute all indicators (sudah di-shift(1)) ──
        df = data.compute_all_indicators(df)
        df = data.align_to_market(df)

        # ── 3. Rolling window ──
        #    Warm-up: butuh ~60 baris untuk indikator stabil
        #    Akhir: kurangi 5 baris karena tidak bisa ukur H+5
        warmup = 60
        results = []  # list of dict per baris sinyal

        for i in range(warmup, len(df) - 5):
            row = df.iloc[i]

            # Skip jika indikator inti NaN (belum siap)
            if pd.isna(row.get("rsi")) or pd.isna(row.get("adx")):
                continue

            # ── a. Deteksi regime dari baris ini ──
            regime = _detect_regime_from_row(row)

            # ── a½. Embed ticker untuk fundamental lookup ──
            row["ticker"] = ticker

            # ── b. Hitung total score & sinyal ──
            score = sc.compute_total_score(row, regime)
            signal = sc.classify(score, regime)

            # ── c. Forward returns ──
            entry_price = float(row["close"])
            if pd.isna(entry_price) or entry_price <= 0:
                continue

            fwd = {}
            for h in [1, 3, 5]:
                exit_idx = min(i + h, len(df) - 1)
                exit_price = float(df.iloc[exit_idx]["close"])
                ret = (exit_price - entry_price) / entry_price * 100
                if not NO_FEE_MODE:
                    ret -= TRADING_FEE_PCT
                fwd[h] = ret

            results.append({
                "date": df.index[i],
                "signal": signal,
                "score": score,
                "regime": regime,
                "entry_price": entry_price,
                "ret_h1": fwd[1],
                "ret_h3": fwd[3],
                "ret_h5": fwd[5],
                "exit_ret_h5": _simulate_exit_returns(
                    df, i, entry_price,
                    signal, TRADING_FEE_PCT if not NO_FEE_MODE else 0),
            })

        if not results:
            return {"ticker": ticker.replace(".JK", ""), "error": "tidak ada sinyal valid"}

        # ── 4. Hitung statistik per sinyal ──
        signal_order = ["STRONG_BUY", "BUY", "WEAK_BUY", "HOLD", "SELL"]
        signals_out = {}

        for sig in signal_order:
            entries = [r for r in results if r["signal"] == sig]
            if not entries:
                continue

            sig_data = {"count": len(entries), "regimes": {}}

            # Hitung per hold period
            for h in [1, 3, 5]:
                returns = [r[f"ret_h{h}"] for r in entries]
                if not returns:
                    continue

                n_win = sum(1 for ret in returns if ret > 0)
                n_loss = sum(1 for ret in returns if ret < 0)
                win_rate = n_win / len(returns) * 100 if returns else 0.0
                avg_ret = float(np.mean(returns))
                exp_ret = avg_ret  # expected return = average return (win rate sudah tercermin di avg)

                sig_data[f"count_h{h}"] = len(returns)
                sig_data[f"win_h{h}"] = n_win
                sig_data[f"loss_h{h}"] = n_loss
                sig_data[f"wr_h{h}"] = round(win_rate, 1)
                sig_data[f"avg_return_h{h}"] = round(avg_ret, 2)
                sig_data[f"exp_return_h{h}"] = round(exp_ret, 2)

            # --- Exit Strategy Statistics ---
            exit_entries = [r.get("exit_ret_h5") for r in entries if r.get("exit_ret_h5", {}).get("exit_method") != "NONE"]
            if exit_entries:
                exit_returns = [e["return_pct"] for e in exit_entries]
                exit_methods = {}
                for e in exit_entries:
                    method = e["exit_method"]
                    exit_methods[method] = exit_methods.get(method, 0) + 1

                n_win_exit = sum(1 for ret in exit_returns if ret > 0)
                wr_exit = n_win_exit / len(exit_returns) * 100 if exit_returns else 0
                avg_exit_ret = float(np.mean(exit_returns)) if exit_returns else 0

                sig_data["exit_wr_h5"] = round(wr_exit, 1)
                sig_data["exit_avg_return_h5"] = round(avg_exit_ret, 2)
                sig_data["exit_methods"] = exit_methods

            # Rata-rata score untuk sinyal ini
            scores = [r["score"] for r in entries]
            sig_data["avg_score"] = round(float(np.mean(scores)), 1)

            # Distribusi regime
            regime_counts = {}
            for r in entries:
                reg = r["regime"]
                regime_counts[reg] = regime_counts.get(reg, 0) + 1
            sig_data["regimes"] = regime_counts

            signals_out[sig] = sig_data

        return {
            "ticker": ticker.replace(".JK", ""),
            "total_days": len(df),
            "valid_signals": len(results),
            "signals": signals_out,
            "error": None,
        }

    except Exception as e:
        logger.exception("Error backtest %s: %s", ticker, e)
        return {"ticker": ticker.replace(".JK", ""), "error": str(e)}


# ════════════════════════════════════════════════════════════════
#  CSV / OUTPUT HELPERS
# ════════════════════════════════════════════════════════════════
def _flatten_result(res: dict) -> list:
    """
    Ubah nested dict hasil backtest ke list of dict untuk CSV.
    """
    rows = []
    ticker = res["ticker"]
    signals = res.get("signals", {})
    for sig, data_sig in signals.items():
        row = {
            "ticker": ticker,
            "signal": sig,
            "count": data_sig.get("count", 0),
            "avg_score": data_sig.get("avg_score", 0),
        }
        for h in [1, 3, 5]:
            row[f"wr_h{h}"] = data_sig.get(f"wr_h{h}", 0)
            row[f"avg_return_h{h}"] = data_sig.get(f"avg_return_h{h}", 0)
            row[f"exp_return_h{h}"] = data_sig.get(f"exp_return_h{h}", 0)
            row[f"win_h{h}"] = data_sig.get(f"win_h{h}", 0)
            row[f"loss_h{h}"] = data_sig.get(f"loss_h{h}", 0)
        rows.append(row)
    return rows


def _summarize_results(all_results: list, detail: bool = False) -> pd.DataFrame:
    """
    Gabungkan semua hasil ke DataFrame summary.
    detail=True → sertakan per sinyal baris.
    """
    flat_rows = []
    for res in all_results:
        if res.get("error"):
            continue
        flat_rows.extend(_flatten_result(res))
    return pd.DataFrame(flat_rows)


# ════════════════════════════════════════════════════════════════
#  SINGLE-TICKER DETAILED OUTPUT
# ════════════════════════════════════════════════════════════════
def print_single_detail(ticker: str, res: dict):
    """Cetak detail backtest untuk satu saham."""
    if res.get("error"):
        print(f"\n  ❌ {ticker}: {res['error']}")
        return

    signals = res.get("signals", {})
    print(f"\n  {'='*60}")
    print(f"  📊 BACKTEST DETAIL — {ticker}")
    print(f"  Total data: {res['total_days']} hari | Valid sinyal: {res['valid_signals']}")
    print(f"  {'='*60}")

    for sig in ["STRONG_BUY", "BUY", "WEAK_BUY", "HOLD", "SELL"]:
        sd = signals.get(sig)
        if not sd:
            continue
        print(f"\n  ── {sig} ({sd['count']}x, avg_score={sd['avg_score']}) ──")
        print(f"  {'Holding':>8} {'Win Rate':>10} {'Avg Return':>12} {'Exp Return':>12}  {'Win':>5} {'Loss':>5}")
        print(f"  {'─'*54}")
        for h in [1, 3, 5]:
            wr = sd.get(f"wr_h{h}", 0)
            ar = sd.get(f"avg_return_h{h}", 0)
            er = sd.get(f"exp_return_h{h}", 0)
            win = sd.get(f"win_h{h}", 0)
            loss = sd.get(f"loss_h{h}", 0)
            print(f"  H+{h:<5} {wr:>8.1f}% {ar:>+10.2f}% {er:>+10.2f}%  {win:>5} {loss:>5}")
        if sd.get("regimes"):
            print(f"  Regime: {sd['regimes']}")
        # Exit strategy stats (--simulate-exits)
        exit_wr = sd.get("exit_wr_h5")
        exit_ar = sd.get("exit_avg_return_h5")
        exit_methods = sd.get("exit_methods")
        if exit_wr is not None and exit_ar is not None:
            print(f"  ── Exit Simulation ──")
            print(f"  Exit WR H+5: {exit_wr:>5.1f}% | Exit AvgRet: {exit_ar:>+7.2f}%")
            if exit_methods:
                total_exits = sum(exit_methods.values())
                method_pct = {k: f"{v/total_exits*100:.0f}%" for k, v in exit_methods.items()}
                print(f"  Exit methods: {method_pct}")

    print(f"\n  {'='*60}\n")


# ════════════════════════════════════════════════════════════════
#  SUMMARY OUTPUT
# ════════════════════════════════════════════════════════════════
def print_summary(all_results: list):
    """Cetak summary backtest untuk banyak saham."""
    valid = [r for r in all_results if not r.get("error") and r.get("signals")]
    errors = [r for r in all_results if r.get("error")]

    print(f"\n{'='*75}")
    print(f"  📈 SUMMARY BACKTEST SCORING ENGINE v2")
    print(f"  {datetime.now().strftime('%d %B %Y %H:%M')}")
    print(f"  Saham dianalisis: {len(all_results)} | Valid: {len(valid)} | Error: {len(errors)}")
    print(f"{'='*75}")

    if not valid:
        print("  ❌ Tidak ada hasil valid untuk ditampilkan.")
        return

    # ── Per sinyal, agregasi ──
    signal_order = ["STRONG_BUY", "BUY", "WEAK_BUY", "HOLD", "SELL"]
    print(f"\n  {'Sinyal':<15} {'N':>6} {'WR H+1':>8} {'WR H+3':>8} {'WR H+5':>8}"
          f" {'AvgRet H+5':>11} {'ExpRet H+5':>11}")
    print(f"  {'─'*67}")

    total_signals = 0
    for sig in signal_order:
        # Agregasi semua ticker untuk sinyal ini
        counts = []
        wr_h1, wr_h3, wr_h5 = [], [], []
        ar_h1, ar_h3, ar_h5 = [], [], []

        for r in valid:
            sd = r["signals"].get(sig)
            if sd:
                counts.append(sd["count"])
                wr_h1.append(sd.get("wr_h1", 0))
                wr_h3.append(sd.get("wr_h3", 0))
                wr_h5.append(sd.get("wr_h5", 0))
                ar_h1.append(sd.get("avg_return_h1", 0))
                ar_h3.append(sd.get("avg_return_h3", 0))
                ar_h5.append(sd.get("avg_return_h5", 0))

        if not counts:
            continue

        n_total = sum(counts)
        total_signals += n_total

        # Weighted average berdasarkan jumlah sinyal per ticker
        weight = [c / n_total for c in counts]

        def weighted_avg(vals, w):
            return sum(v * w[i] for i, v in enumerate(vals)) if vals else 0.0

        w_wr_h1 = weighted_avg(wr_h1, weight)
        w_wr_h3 = weighted_avg(wr_h3, weight)
        w_wr_h5 = weighted_avg(wr_h5, weight)
        w_ar_h5 = weighted_avg(ar_h5, weight)

        print(f"  {sig:<15} {n_total:>6} {w_wr_h1:>7.1f}% {w_wr_h3:>7.1f}% {w_wr_h5:>7.1f}%"
              f" {w_ar_h5:>+9.2f}%  —")

    print(f"  {'─'*67}")
    print(f"  TOTAL: {total_signals} sinyal dari {len(valid)} saham")

    # ── Top performers ──
    print(f"\n  {'🏆 TOP 10 — Expected Return H+5 Per Ticker':^73}")
    print(f"  {'─'*73}")
    top_rows = []
    for r in valid:
        for sig, sd in r["signals"].items():
            if sig in ("STRONG_BUY", "BUY"):
                er = sd.get("exp_return_h5", 0)
                wr = sd.get("wr_h5", 0)
                cnt = sd.get("count", 0)
                if cnt >= 3:  # minimal 3 sinyal biar statistik bermakna
                    top_rows.append((r["ticker"], sig, er, wr, cnt))

    top_rows.sort(key=lambda x: x[2], reverse=True)
    print(f"  {'Ticker':<7} {'Sinyal':<12} {'Exp Ret H+5':>12} {'WR H+5':>8} {'Sinyal':>7}")
    print(f"  {'─'*46}")
    for tkr, sig, er, wr, cnt in top_rows[:10]:
        print(f"  {tkr:<7} {sig:<12} {er:>+10.2f}% {wr:>7.1f}% {cnt:>6}x")

    print(f"\n{'='*75}\n")


# ════════════════════════════════════════════════════════════════
#  MAIN
# ════════════════════════════════════════════════════════════════
def get_tickers_from_csv(n: int = 999) -> list:
    """Ambil ticker dari hasil scan terakhir, prioritaskan BUY signals."""
    data_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(data_dir, "screener_v2_result.csv")
    if not os.path.exists(path):
        return ["BBCA", "BBRI", "ADRO", "UNVR", "ASII"]

    with open(path) as f:
        rows = list(csv.DictReader(f))

    def sort_key(r):
        prio = {"STRONG_BUY": 0, "BUY": 1, "WEAK_BUY": 2, "HOLD": 3, "SELL": 4}
        return prio.get(r.get("signal", ""), 5), -float(r.get("score", 0))

    rows.sort(key=sort_key)
    return [r["ticker"] for r in rows[:n]]


def main():
    parser = argparse.ArgumentParser(
        description="Backtest IDX Alpha Screener v2 — Scoring Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Contoh:\n"
            "  python backtest.py                         # top 5 dari CSV\n"
            "  python backtest.py --all                   # semua dari CSV\n"
            "  python backtest.py --ticker BBCA BBRI      # spesifik\n"
            "  python backtest.py --single BBCA           # detail satu saham\n"
            "  python backtest.py --output hasil.csv      # simpan ke file kustom\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--ticker", nargs="+", help="Saham spesifik (tanpa .JK)")
    group.add_argument("--all", action="store_true", help="Semua ticker yang BUY di CSV")
    group.add_argument("--single", type=str, help="Detail untuk satu saham")
    parser.add_argument("--top", type=int, default=5, help="Top N dari CSV (default: 5)")
    parser.add_argument("--output", type=str, default=None,
                        help="File output CSV (default: backtest_results_{date}.csv)")
    parser.add_argument("--no-save", action="store_true",
                        help="Jangan simpan CSV, hanya tampilkan summary")
    parser.add_argument("--no-fee", action="store_true", default=False,
                        help="Skip trading fee (lihat raw edge sebelum fee)")
    parser.add_argument("--walk-forward", type=int, nargs="?", const=12, default=0,
                        help="Walk-forward validation: train N months, test next N/2 months (default: 12)")
    parser.add_argument("--simulate-exits", action="store_true", default=False,
                        help="Simulasikan exit strategy (max_hold 15 hari, flat_exit 7 hari, hard_stop -15%)")

    args = parser.parse_args()

    # ── Set global flags ──
    global NO_FEE_MODE
    NO_FEE_MODE = args.no_fee
    SIMULATE_EXITS = args.simulate_exits

    # ── Tentukan daftar ticker ──
    if args.single:
        tickers = [args.single.upper()]
    elif args.ticker:
        tickers = [t.upper() for t in args.ticker]
    elif args.all:
        tickers = get_tickers_from_csv(999)
    else:
        tickers = get_tickers_from_csv(args.top)

    logger.info("Backtest scoring engine v2 — %d saham", len(tickers))
    if NO_FEE_MODE:
        logger.info("Fee: OFF")
    else:
        logger.info("Fee: ON (%.1f%% per trade)", TRADING_FEE_PCT)
    logger.info("=" * 55)

    # ── Run backtest ──
    all_results = []
    for i, tkr in enumerate(tickers, 1):
        res = backtest_scoring(tkr)
        all_results.append(res)

        t = res["ticker"]
        if res.get("error"):
            logger.info("  [%2d/%d] %-6s — ❌ %s", i, len(tickers), t, res["error"])
        else:
            n = res["valid_signals"]
            # Ambil WR rata-rata dari sinyal BUY
            buy_data = res["signals"].get("STRONG_BUY") or res["signals"].get("BUY") or {}
            wr_h5 = buy_data.get("wr_h5", 0)
            ar_h5 = buy_data.get("avg_return_h5", 0)
            exit_ar = buy_data.get("exit_avg_return_h5", None)
            exit_wr = buy_data.get("exit_wr_h5", None)
            if exit_ar is not None and exit_wr is not None:
                logger.info("  [%2d/%d] %-6s — %3d sinyal | H+5: WR=%5.1f%% Ret=%+6.2f%% | Exit: WR=%5.1f%% Ret=%+6.2f%%",
                            i, len(tickers), t, n, wr_h5, ar_h5, exit_wr, exit_ar)
            else:
                logger.info("  [%2d/%d] %-6s — %3d sinyal | WR H+5=%5.1f%% | AvgRet=%+6.2f%%",
                            i, len(tickers), t, n, wr_h5, ar_h5)

        if i < len(tickers):
            time.sleep(0.3)

    # ── Walk-Forward Validation ──
    if args.walk_forward > 0 and all_results:
        try:
            from sklearn.model_selection import TimeSeriesSplit
            from datetime import timedelta
            
            """
            Walk-Forward Validation 
            Train on first N months, test on next N/2 months.
            Measures how well the scoring engine performs on unseen data.
            """
            logger.info("━" * 55)
            logger.info("Walk-Forward Validation: train %d months, test %d months",
                       args.walk_forward, args.walk_forward // 2)
            
            # Simple walk-forward: split per ticker and measure consistency
            stable_count = 0
            deg_count = 0
            
            for res in all_results:
                if res.get("error"):
                    continue
                ticker = res["ticker"]
                tkr_full = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
                
                df = data.fetch_with_cache(tkr_full, period="2y")
                if df.empty or len(df) < 180:
                    continue
                
                df = data.compute_all_indicators(df)
                df = data.align_to_market(df)
                
                total_len = len(df)
                test_start = total_len - args.walk_forward // 2 * 21  # ~21 trading days/month
                train_end = test_start - 1
                
                if train_end < 60 or test_start >= total_len:
                    continue
                
                # Train period signals
                train_results = []
                for i_pt in range(60, train_end - 5):
                    row = df.iloc[i_pt]
                    if pd.isna(row.get("rsi")):
                        continue
                    regime = _detect_regime_from_row(row)
                    row["ticker"] = ticker
                    score = sc.compute_total_score(row, regime)
                    signal = sc.classify(score, regime)
                    entry_price = float(row["close"])
                    if pd.isna(entry_price) or entry_price <= 0:
                        continue
                    exit_price = float(df.iloc[min(i_pt + 5, len(df) - 1)]["close"])
                    ret = (exit_price - entry_price) / entry_price * 100
                    if not NO_FEE_MODE:
                        ret -= TRADING_FEE_PCT
                    train_results.append({"signal": signal, "ret": ret, "score": score})
                
                # Test period signals
                test_results = []
                for i_pt in range(test_start, len(df) - 5):
                    row = df.iloc[i_pt]
                    if pd.isna(row.get("rsi")):
                        continue
                    regime = _detect_regime_from_row(row)
                    row["ticker"] = ticker
                    score = sc.compute_total_score(row, regime)
                    signal = sc.classify(score, regime)
                    entry_price = float(row["close"])
                    if pd.isna(entry_price) or entry_price <= 0:
                        continue
                    exit_price = float(df.iloc[min(i_pt + 5, len(df) - 1)]["close"])
                    ret = (exit_price - entry_price) / entry_price * 100
                    if not NO_FEE_MODE:
                        ret -= TRADING_FEE_PCT
                    test_results.append({"signal": signal, "ret": ret, "score": score})
                
                # Compare STRONG_BUY/BUY WR
                for sig_to_check in ("STRONG_BUY", "BUY"):
                    train_sigs = [r for r in train_results if r["signal"] == sig_to_check]
                    test_sigs = [r for r in test_results if r["signal"] == sig_to_check]
                    
                    if len(train_sigs) >= 3 and len(test_sigs) >= 2:
                        train_wr = sum(1 for r in train_sigs if r["ret"] > 0) / len(train_sigs) * 100
                        test_wr = sum(1 for r in test_sigs if r["ret"] > 0) / len(test_sigs) * 100
                        
                        if test_wr >= train_wr - 5:  # within 5% degradation
                            stable_count += 1
                        else:
                            deg_count += 1
                            
                        logger.info("  WF %s %s: train WR=%.0f%% (%dx) → test WR=%.0f%% (%dx) %s",
                                   ticker, sig_to_check, train_wr, len(train_sigs),
                                   test_wr, len(test_sigs),
                                   "✅" if test_wr >= train_wr - 5 else "⚠️ DEGRADE")
            
            if stable_count + deg_count > 0:
                stability_pct = stable_count / (stable_count + deg_count) * 100
                logger.info("Walk-Forward Stability: %.0f%% (%d stable, %d degraded)",
                           stability_pct, stable_count, deg_count)
                print(f"\n  📊 Walk-Forward Stability: {stability_pct:.0f}% ({stable_count} stable, {deg_count} degraded)")
        except ImportError:
            logger.warning("Walk-Forward: scikit-learn tidak terinstall. Skip.")
        except Exception as e:
            logger.warning("Walk-Forward error: %s", e)

    # ── Detail untuk single ticker ──
    if args.single and all_results:
        print_single_detail(args.single.upper(), all_results[0])

    # ── Summary ──
    print_summary(all_results)

    # ── Simpan CSV ──
    if not args.no_save:
        df_summary = _summarize_results(all_results)
        if not df_summary.empty:
            # Sort: STRONG_BUY first, then by exp_return_h5 descending
            signal_priority = {"STRONG_BUY": 0, "BUY": 1, "WEAK_BUY": 2, "HOLD": 3, "SELL": 4}
            df_summary["_prio"] = df_summary["signal"].map(
                lambda x: signal_priority.get(x, 9)
            )
            df_summary = df_summary.sort_values(["_prio", "ticker"]).drop(columns="_prio")

            # Simpan dengan timestamp
            date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = args.output or f"backtest_results_{date_str}.csv"
            df_summary.to_csv(out_path, index=False)
            logger.info("Hasil disimpan ke %s", os.path.abspath(out_path))

    logger.info("Selesai.")


if __name__ == "__main__":
    main()
