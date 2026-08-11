"""
backtest_v4.py — V4 Backtest Engine for IDX Watchlist
=====================================================
Simulasi sinyal V4 pada watchlist saham konglomerat.
Menggunakan data historis dari InvezgoProvider.
"""
import sys
import os
import logging
from datetime import datetime
from collections import defaultdict

import numpy as np
import pandas as pd

# Add project root to path
PROJECT_ROOT = r"C:\Hermes_Workspace\Screener"
sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
logger = logging.getLogger("backtest_v4")

from idx_alpha_screener.data_invezgo import InvezgoProvider
from idx_alpha_screener.data import compute_all_indicators, align_to_market, fetch_ihsg_cached
from idx_alpha_screener.scoring import compute_total_score, THRESHOLDS
from idx_alpha_screener.regime import detect_market_regime

# ── Watchlist (dari config.yaml, digabung semua grup) ──
WATCHLIST = sorted(set([
    # user picks
    "ALII", "BRPT", "BUMI", "TPIA", "CUAN", "MPPA",
    # barito
    "DSSA", "ENRG",
    # bakrie
    "BNBR", "VBID", "ELTY",
    # salim
    "INDF", "ICBP", "KLBF", "HMSP", "BISI",
    # astra
    "ASII", "UNTR", "AKRA", "CPIN", "ISAT",
]))

SIGNAL_LEVELS = {0: "STRONG_BUY", 1: "BUY", 2: "WEAK_BUY"}


def get_signal_level(score: float, regime: str) -> str:
    """Get signal level from score based on regime thresholds."""
    th = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])
    if score >= th[0]:
        return "STRONG_BUY"
    elif score >= th[1]:
        return "BUY"
    elif score >= th[2]:
        return "WEAK_BUY"
    return "HOLD"


def run_backtest():
    """Main backtest function."""
    ip = InvezgoProvider()

    # Fetch IHSG once (cached)
    logger.info("Fetching IHSG data...")
    df_ihsg = fetch_ihsg_cached(period="2y")
    if df_ihsg.empty:
        logger.error("Gagal fetch IHSG. Abort.")
        return

    logger.info("IHSG data: %d rows, %s to %s",
                len(df_ihsg), df_ihsg.index[0].date(), df_ihsg.index[-1].date())

    # Store all signals
    all_signals = []

    for ticker in WATCHLIST:
        logger.info("Processing %s...", ticker)

        # Fetch historical data (~2 years to get enough for indicators + 20d forward)
        df = ip.get_historical(ticker, period="2y")
        if df.empty or len(df) < 80:
            logger.warning("  Data tidak cukup untuk %s (rows=%d), skip", ticker, len(df))
            continue

        logger.info("  %s: %d rows, %s to %s",
                    ticker, len(df), df.index[0].date(), df.index[-1].date())

        # ── Compute all indicators ONCE ──
        # compute_all_indicators uses .shift(1) everywhere, so row t's indicators
        # are based on data up to t-1 → no look-ahead for scoring.
        df = compute_all_indicators(df)
        df = align_to_market(df, df_ihsg=df_ihsg)

        # Drop rows with incomplete indicators (first ~60 rows)
        df = df.dropna(subset=["rsi", "adx", "macd", "ema12", "ema50"]).copy()

        if len(df) < 80:
            logger.warning("  %s: terlalu sedikit data setelah dropna (%d), skip",
                           ticker, len(df))
            continue

        # ── Walk forward day by day ──
        # Start at index 60 (indicators stabil), end at len-20 (need 20d forward)
        for t in range(60, len(df) - 20):
            row = df.iloc[t]

            # Detect regime using ONLY data up to day t
            subset = df.iloc[:t + 1]
            regime, trend_score, adx_val = detect_market_regime(subset)

            # Compute total score (V4 scoring)
            score = compute_total_score(row, regime)

            # Check if score meets WEAK_BUY threshold
            th = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])
            if score < th[2]:
                continue  # Not a signal

            signal_level = get_signal_level(score, regime)
            entry_price = float(row["close"])
            entry_date = row.name

            # Future returns at 5, 10, 15, 20 days
            future_prices = {}
            for hold in [5, 10, 15, 20]:
                if t + hold < len(df):
                    future_price = float(df.iloc[t + hold]["close"])
                    ret = (future_price - entry_price) / entry_price
                    future_prices[hold] = {
                        "price": future_price,
                        "return": ret,
                        "win": ret > 0,
                    }

            all_signals.append({
                "ticker": ticker,
                "date": entry_date,
                "entry_price": entry_price,
                "score": score,
                "signal_level": signal_level,
                "regime": regime,
                "trend_score": trend_score,
                "adx": adx_val,
                "future": future_prices,
            })

    # ── Compute & print stats ──
    if not all_signals:
        print("\n⚠  TIDAK ADA SINYAL DITEMUKAN untuk semua ticker.")
        return

    today_str = datetime.now().strftime('%Y-%m')
    print(f"\n{'=' * 62}")
    print(f"  BACKTEST V4 — WATCHLIST KONGLOMERAT")
    print(f"  Period: ~2025-01 to {today_str}")
    print(f"  Total signals: {len(all_signals)}")
    print(f"{'=' * 62}")

    # ── Overall by hold period ──
    print(f"\n{'─' * 62}")
    print(f"  HOLD  |  Signals  |    WR    |  Avg Return")
    print(f"{'─' * 62}")

    for hold in [5, 10, 15, 20]:
        sigs = [s for s in all_signals if hold in s["future"]]
        if not sigs:
            continue
        wins = sum(1 for s in sigs if s["future"][hold]["win"])
        returns = [s["future"][hold]["return"] for s in sigs]
        wr = wins / len(sigs) * 100
        avg_ret = float(np.mean(returns)) * 100
        print(f"  {hold:<5} |  {len(sigs):<8} |  {wr:>5.1f}%   |  {avg_ret:+6.2f}%")

    # ── By Signal Level (hold 10d) ──
    hold_display = 10
    sigs_10d = [s for s in all_signals if hold_display in s["future"]]

    print(f"\n{'─' * 62}")
    print(f"  By Signal Level (hold {hold_display}d):")
    print(f"  {'LEVEL':<14} |  {'Signals':<8} |  {'WR':<7} |  {'Avg Ret':<8}")
    print(f"{'─' * 62}")

    for level in ["STRONG_BUY", "BUY", "WEAK_BUY"]:
        sigs_lv = [s for s in sigs_10d if s["signal_level"] == level]
        if not sigs_lv:
            continue
        wins = sum(1 for s in sigs_lv if s["future"][hold_display]["win"])
        returns = [s["future"][hold_display]["return"] for s in sigs_lv]
        wr = wins / len(sigs_lv) * 100
        avg_ret = float(np.mean(returns)) * 100
        print(f"  {level:<14} |  {len(sigs_lv):<8} |  {wr:>5.1f}%  |  {avg_ret:+6.2f}%")

    # ── By Regime (hold 10d) ──
    print(f"\n{'─' * 62}")
    print(f"  By Regime (hold {hold_display}d):")
    print(f"  {'REGIME':<14} |  {'Signals':<8} |  {'WR':<7} |  {'Avg Ret':<8}")
    print(f"{'─' * 62}")

    for regime in ["BULL", "BEAR", "RANGING", "HIGH_VOLATILITY"]:
        sigs_rg = [s for s in sigs_10d if s["regime"] == regime]
        if not sigs_rg:
            continue
        wins = sum(1 for s in sigs_rg if s["future"][hold_display]["win"])
        returns = [s["future"][hold_display]["return"] for s in sigs_rg]
        wr = wins / len(sigs_rg) * 100
        avg_ret = float(np.mean(returns)) * 100
        print(f"  {regime:<14} |  {len(sigs_rg):<8} |  {wr:>5.1f}%  |  {avg_ret:+6.2f}%")

    # ── Best / Worst trades (hold 10d) ──
    if sigs_10d:
        best = max(sigs_10d, key=lambda s: s["future"][hold_display]["return"])
        worst = min(sigs_10d, key=lambda s: s["future"][hold_display]["return"])
        print(f"\n{'─' * 62}")
        print(f"  Best trade:  {best['ticker']}  +{best['future'][hold_display]['return'] * 100:.1f}%  ({best['date'].date()})")
        print(f"  Worst trade: {worst['ticker']}  {worst['future'][hold_display]['return'] * 100:.1f}%  ({worst['date'].date()})")

    # ── By Ticker (hold 10d) ──
    print(f"\n{'─' * 62}")
    print(f"  By Ticker (hold {hold_display}d):")
    print(f"  {'TICKER':<8} |  {'Signals':<8} |  {'WR':<7} |  {'Avg Ret':<8}")
    print(f"{'─' * 62}")

    ticker_stats = defaultdict(lambda: {"signals": 0, "wins": 0, "returns": []})
    for s in sigs_10d:
        t = s["ticker"]
        ticker_stats[t]["signals"] += 1
        if s["future"][hold_display]["win"]:
            ticker_stats[t]["wins"] += 1
        ticker_stats[t]["returns"].append(s["future"][hold_display]["return"])

    for ticker in sorted(ticker_stats.keys()):
        st = ticker_stats[ticker]
        wr = st["wins"] / st["signals"] * 100
        avg_ret = float(np.mean(st["returns"])) * 100
        print(f"  {ticker:<8} |  {st['signals']:<8} |  {wr:>5.1f}%  |  {avg_ret:+6.2f}%")

    # ── Overall Summary ──
    total_10d = len(sigs_10d)
    wins_10d = sum(1 for s in sigs_10d if s["future"][hold_display]["win"])
    returns_10d = [s["future"][hold_display]["return"] for s in sigs_10d]
    wr_10d = wins_10d / total_10d * 100
    avg_ret_10d = float(np.mean(returns_10d)) * 100
    med_ret_10d = float(np.median(returns_10d)) * 100
    std_ret_10d = float(np.std(returns_10d)) * 100

    print(f"\n{'=' * 62}")
    print(f"  SUMMARY (hold {hold_display}d)")
    print(f"{'=' * 62}")
    print(f"  Total signals:    {total_10d}")
    print(f"  Win Rate:         {wr_10d:.1f}%")
    print(f"  Avg Return:       {avg_ret_10d:+.2f}%")
    print(f"  Median Return:    {med_ret_10d:+.2f}%")
    print(f"  Std Dev Return:   {std_ret_10d:.2f}%")
    print(f"  Best Trade:       {best['ticker']} +{best['future'][hold_display]['return'] * 100:.1f}% ({best['date'].date()})" if sigs_10d else "")
    print(f"  Worst Trade:      {worst['ticker']} {worst['future'][hold_display]['return'] * 100:.1f}% ({worst['date'].date()})" if sigs_10d else "")
    print(f"{'=' * 62}\n")


if __name__ == "__main__":
    run_backtest()
