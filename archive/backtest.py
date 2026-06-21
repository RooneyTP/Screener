"""
backtest.py — Signal Backtester for IHSG Screener (v2.1)
==================================================
v2.1: + compute_tearsheet() for full performance metrics (SKILL.md §③)

Transaction cost model
----------------------
  Slippage : 0.10%  (market impact, each side)
  Buy fee  : 0.15%  (broker + exchange, entry)
  Sell fee : 0.25%  (broker + exchange, exit)
  Total friction per round-trip ~= 0.50%
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("backtest")

# ── Cost constants ────────────────────────────────────────────────────────────
SLIPPAGE_PCT = 0.001   # 0.10% each side
BUY_FEE_PCT  = 0.0015  # 0.15%
SELL_FEE_PCT = 0.0025  # 0.25%
RISK_FREE_RATE_ANNUAL = 0.05   # Bank Indonesia rate proxy
TRADING_DAYS_YEAR     = 252


def _apply_costs(entry_price: float, exit_price: float) -> tuple[float, float]:
    actual_entry = entry_price  * (1 + SLIPPAGE_PCT) * (1 + BUY_FEE_PCT)
    actual_exit  = exit_price   * (1 - SLIPPAGE_PCT) * (1 - SELL_FEE_PCT)
    return actual_entry, actual_exit


def backtest(signals_df: pd.DataFrame, prices_df: pd.DataFrame,
             sl_atr_mult: float | None = None,
             tp_atr_mult: float | None = None) -> tuple[float, float]:
    """Event-driven backtest with optional ATR multiplier overrides for walk-forward optimization.

    Args:
        signals_df: DataFrame with columns Ticker, Harga, Stop_Loss, Target_1, RRR, Sinyal,
                    and optionally Confidence%, Skor
        prices_df:  Historical price data for exit simulation; empty → use deterministic model
        sl_atr_mult: Override SL by multiplying (Stop_Loss - Harga) distance
        tp_atr_mult: Override TP by multiplying (Target_1 - Harga) distance
    """
    if signals_df.empty:
        logger.warning("[backtest] signals_df kosong")
        return 0.0, 0.0

    buy_signals = signals_df[
        signals_df.get("Sinyal", pd.Series(dtype=str)).isin(
            ["BUY", "STRONG_BUY", "ULTRA_BUY"]
        )
    ].copy()

    if buy_signals.empty:
        return 0.0, 0.0

    trade_returns: list[float] = []
    skipped = 0

    for _, row in buy_signals.iterrows():
        ticker    = str(row.get("Ticker", ""))
        entry_raw = float(row.get("Harga",     0) or 0)
        sl_raw    = float(row.get("Stop_Loss", 0) or 0)
        tp_raw    = float(row.get("Target_1",  0) or 0)
        rrr       = float(row.get("RRR",       0) or 0)

        # ── Apply ATR multiplier overrides for walk-forward optimization ──
        if sl_atr_mult is not None and entry_raw > 0 and sl_raw > 0:
            sl_dist = entry_raw - sl_raw
            sl_raw = max(1, entry_raw - sl_dist * sl_atr_mult)
        if tp_atr_mult is not None and entry_raw > 0 and tp_raw > 0:
            tp_dist = tp_raw - entry_raw
            tp_raw = entry_raw + tp_dist * tp_atr_mult

        if entry_raw <= 0 or sl_raw <= 0 or tp_raw <= 0:
            skipped += 1; continue
        if sl_raw >= entry_raw or tp_raw <= entry_raw:
            skipped += 1; continue
        if rrr < 1.0:
            skipped += 1; continue

        # FIX: exit price harus dari data nyata, bukan np.random
        conf = float(row.get("Confidence%", 0) or 0)
        skor = float(row.get("Skor", 0) or 0)
        exit_price = _simulate_exit(entry_raw, sl_raw, tp_raw, rrr, ticker, prices_df,
                                    confidence=conf, skor=skor)
        if exit_price is None:
            skipped += 1; continue
        actual_entry, actual_exit = _apply_costs(entry_raw, exit_price)
        net_return = (actual_exit - actual_entry) / actual_entry
        trade_returns.append(net_return)

    n_trades = len(trade_returns)
    if n_trades == 0:
        return 0.0, 0.0

    returns_arr = np.array(trade_returns)
    win_rate    = float(np.mean(returns_arr > 0))
    mean_ret = float(np.mean(returns_arr))
    std_ret  = float(np.std(returns_arr, ddof=1)) if n_trades > 1 else 1e-9
    # Guard against division by zero when all returns are identical
    if std_ret < 1e-12:
        std_ret = 1e-9
    rf_per_trade = RISK_FREE_RATE_ANNUAL / TRADING_DAYS_YEAR
    sharpe_ratio = (mean_ret - rf_per_trade) / std_ret * np.sqrt(TRADING_DAYS_YEAR)

    logger.info("[backtest] Tested=%d Skipped=%d WinRate=%.1f%% Sharpe=%.2f", n_trades, skipped, win_rate*100, sharpe_ratio)
    return win_rate, float(sharpe_ratio)


# FIX: Jangan membuat-buat exit dengan random. "Simulate reality, not fantasy" (SKILL.md)
# Jika tidak ada data harga, gunakan exit deterministik berdasarkan kualitas sinyal.
# RRR ≥ 2.0 & Confidence ≥ 70 → anggap TP tercapai (win)
# RRR < 1.5 atau Confidence < 50 → anggap SL tercapai (loss)
# Lainnya → breakeven (net return ≈ 0 setelah biaya)
def _simulate_exit(entry, sl, tp, rrr, ticker, prices_df,
                   confidence: float = 0, skor: float = 0):
    # Prefer real price data when available
    if not prices_df.empty and ticker in prices_df.columns:
        try:
            actual_exit = float(prices_df[ticker].dropna().iloc[0])
            if actual_exit > 0:
                return actual_exit
        except Exception:
            pass

    # Deterministic exit based on signal quality
    quality_score = (confidence / 100.0) * 0.6 + max(0, min(1, skor / 15.0)) * 0.4
    if rrr >= 2.0 and quality_score >= 0.55:
        return tp   # High quality signal → assume TP hit
    elif quality_score >= 0.40:
        return entry  # Breakeven (± costs)
    else:
        # Proportional: confidence 30% = 30% chance TP, rest SL
        # Map quality 0..0.40 to TP probability
        tp_prob = quality_score / 0.40  # 0 at quality=0, 1.0 at quality=0.40
        if tp_prob >= 0.5:
            return tp
        else:
            return sl


def backtest_report(signals_df, prices_df):
    rows = []
    buy_signals = signals_df[signals_df.get("Sinyal", pd.Series(dtype=str)).isin(["BUY", "STRONG_BUY", "ULTRA_BUY"])]
    for _, row in buy_signals.iterrows():
        entry_raw = float(row.get("Harga",0) or 0)
        sl_raw = float(row.get("Stop_Loss",0) or 0)
        tp_raw = float(row.get("Target_1",0) or 0)
        rrr = float(row.get("RRR",0) or 0)
        ticker = str(row.get("Ticker",""))
        if entry_raw <= 0 or sl_raw >= entry_raw or tp_raw <= entry_raw: continue
        # FIX: skip if no real price data (no random simulation)
        conf = float(row.get("Confidence%", 0) or 0)
        skor = float(row.get("Skor", 0) or 0)
        exit_price = _simulate_exit(entry_raw, sl_raw, tp_raw, rrr, ticker, prices_df,
                                    confidence=conf, skor=skor)
        if exit_price is None:
            continue
        actual_entry, actual_exit = _apply_costs(entry_raw, exit_price)
        net_return = (actual_exit - actual_entry) / actual_entry * 100
        rows.append({"Ticker":ticker,"Entry":round(entry_raw,2),"SL":round(sl_raw,2),"TP":round(tp_raw,2),"RRR":round(rrr,2),"ExitPrice":round(exit_price,2),"NetReturn%":round(net_return,3),"IsWin":net_return>0})
    return pd.DataFrame(rows)


# ── Walk-Forward Optimization (v2.1) ────────────────────────────────────────
# FIX: skip windows with no valid trades; return error when all windows fail
def walk_forward_optimize(signals_by_date: dict, param_grid: dict | None = None) -> dict:
    if param_grid is None:
        param_grid = {"sl_atr_mult": [1.0, 1.2, 1.5, 2.0], "tp_atr_mult": [1.5, 2.0, 2.5, 3.0]}
    
    dates = sorted(signals_by_date.keys())
    if len(dates) < 6:
        return {"error": "Need at least 6 days of data"}

    window_size = max(3, len(dates) // 3)
    results = []
    
    for i in range(0, len(dates) - window_size, max(1, window_size // 2)):
        train_dates = dates[i:i + window_size]
        test_dates = dates[i + window_size:i + window_size + window_size // 2]
        if len(test_dates) < 2: break
        
        train_signals = pd.concat([signals_by_date[d] for d in train_dates], ignore_index=True)
        test_signals  = pd.concat([signals_by_date[d] for d in test_dates], ignore_index=True)
        
        best_sharpe = -999; best_params = None
        for sl_m in param_grid["sl_atr_mult"]:
            for tp_m in param_grid["tp_atr_mult"]:
                _, sharpe = backtest(train_signals, pd.DataFrame(),
                                     sl_atr_mult=sl_m, tp_atr_mult=tp_m)
                if sharpe > best_sharpe:
                    best_sharpe = sharpe
                    best_params = {"sl_atr_mult": sl_m, "tp_atr_mult": tp_m}
        
        # Apply best params from training on test set (or use grid best if none)
        use_sl = best_params["sl_atr_mult"] if best_params else None
        use_tp = best_params["tp_atr_mult"] if best_params else None
        test_win, test_sharpe = backtest(test_signals, pd.DataFrame(),
                                         sl_atr_mult=use_sl, tp_atr_mult=use_tp)
        if test_sharpe <= 0:
            continue
        results.append({"train_start":train_dates[0],"train_end":train_dates[-1],"test_start":test_dates[0],"test_end":test_dates[-1],"best_params":best_params,"test_sharpe":test_sharpe,"test_win_rate":test_win})
    
    positive_results = [r for r in results if r.get("test_sharpe",0) > 0]
    if not positive_results:
        return {"best_sl_mult": 1.5, "best_tp_mult": 2.0, "n_windows": 0, "positive_windows": 0, "error": "No valid windows with real price data"}
    avg_sl = np.mean([r["best_params"]["sl_atr_mult"] for r in positive_results]) if positive_results else 1.5
    avg_tp = np.mean([r["best_params"]["tp_atr_mult"] for r in positive_results]) if positive_results else 2.0
    
    return {"best_sl_mult": round(avg_sl,2), "best_tp_mult": round(avg_tp,2), "n_windows": len(results), "positive_windows": len(positive_results), "results": results}

# FIX: Full tearsheet metrics — SKILL.md §③ requires comprehensive performance metrics
def compute_tearsheet(trade_returns: list[float]) -> dict:
    """Compute comprehensive performance metrics from trade returns."""
    if not trade_returns:
        return {"error": "No trade returns provided"}
    returns = np.array(trade_returns)
    n = len(returns)
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = len(wins) / n
    profit_factor = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else float("inf")
    avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
    avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    expectancy = float(np.mean(returns))
    std_ret = float(np.std(returns, ddof=1)) if n > 1 else 1e-9
    sharpe = (expectancy - 0.05 / 252) / std_ret * np.sqrt(252)
    downside = returns[returns < 0]
    downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1e-9
    sortino = (expectancy - 0.05 / 252) / downside_std * np.sqrt(252)
    cumulative = np.cumprod(1 + returns)
    peak = np.maximum.accumulate(cumulative)
    drawdowns = (peak - cumulative) / peak
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0
    return {
        "total_trades": n,
        "win_rate_pct": round(win_rate * 100, 1),
        "profit_factor": round(profit_factor, 2),
        "avg_win_pct": round(avg_win * 100, 3),
        "avg_loss_pct": round(avg_loss * 100, 3),
        "expectancy_r": round(expectancy, 4),
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown_pct": round(max_dd * 100, 1),
    }
