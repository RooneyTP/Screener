# scalp/backtest.py — Intraday Event-Driven Backtest Engine (SKILL.md §③)
# ======================================================================
# Replays 1-minute OHLCV bars to simulate scalp strategy performance.
#
# Key features over swing backtest:
#   - 1-minute bar resolution with realistic OHLC exit checking
#   - Multi-leg exit model: breakeven stop → trailing stop
#   - Time-of-day partitioned metrics (morning breakout vs afternoon momentum)
#   - Per-session Sharpe, win rate by time band
#   - Transaction costs per leg (buy fee + sell fee + slippage)
#
# Data source: histori_ihsg.db (populated by scalp/producer.py)

from __future__ import annotations

import logging
import sqlite3
import os as _os
import sys
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_sys_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _sys_root not in sys.path:
    sys.path.insert(0, _sys_root)

from scalp.config import ScalpConfig
from scalp.signals import (
    IntradayFeatures,
    SignalResult,
    compute_intraday_features,
    detect_morning_breakout,
    detect_afternoon_momentum,
    get_session,
)

logger = logging.getLogger(__name__)


# ── Data Types ──────────────────────────────────────────────────────

@dataclass
class TradeRecord:
    """A single completed trade."""
    ticker: str = ""
    date: str = ""
    session: str = ""            # morning / afternoon
    entry_time: str = ""
    exit_time: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    shares: int = 0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: str = ""        # TAKE_PROFIT / CUT_LOSS / EOD_CLOSE
    holding_minutes: float = 0.0
    signal: str = ""
    strategy: str = ""


@dataclass
class BacktestResult:
    """Complete backtest output with full metrics."""
    total_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    expectancy_r: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    avg_holding_minutes: float = 0.0

    # Time-band breakdown
    morning_trades: int = 0
    morning_win_rate: float = 0.0
    morning_pnl: float = 0.0
    afternoon_trades: int = 0
    afternoon_win_rate: float = 0.0
    afternoon_pnl: float = 0.0

    # Curves
    equity_curve: list[float] = field(default_factory=list)
    drawdown_curve: list[float] = field(default_factory=list)

    # Raw data
    trade_log: list[TradeRecord] = field(default_factory=list)
    error: str = ""


# ── Constants ───────────────────────────────────────────────────────

RISK_FREE_RATE = 0.05
TRADING_DAYS_YEAR = 252


# ── Backtest Engine ─────────────────────────────────────────────────

class IntradayBacktest:
    """Event-driven replay of 1-minute bars for scalp strategy.

    Simulates:
      - ORB morning breakout (09:05-09:30)
      - Afternoon momentum (09:30-15:45)
      - Trailing stop: breakeven after 0.8% → trail 0.5% after 1.5%
      - Realistic OHLC exit checking (high≥TP, low≤SL)
      - Transaction costs per leg
    """

    def __init__(self, config: ScalpConfig | None = None):
        self.config = config or ScalpConfig()
        self.trades: list[TradeRecord] = []

    def run(
        self,
        db_path: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> BacktestResult:
        """Run full backtest on histori_ihsg.db data.

        Args:
            db_path: Path to histori_ihsg.db (default from config)
            start_date: 'YYYY-MM-DD' filter start
            end_date: 'YYYY-MM-DD' filter end
        """
        if db_path is None:
            db_path = _os.path.join(_sys_root, self.config.histori_db_name)

        if not _os.path.exists(db_path):
            return BacktestResult(error=f"Database not found: {db_path}")

        conn = sqlite3.connect(db_path)

        # Get unique trading dates
        date_clause = ""
        params = []
        if start_date:
            date_clause += " AND DATE(waktu) >= ?"
            params.append(start_date)
        if end_date:
            date_clause += " AND DATE(waktu) <= ?"
            params.append(end_date)

        dates_df = pd.read_sql(
            f"SELECT DISTINCT DATE(waktu) as dt FROM histori_ihsg WHERE 1=1 {date_clause} ORDER BY dt",
            conn, params=params,
        )
        if dates_df.empty:
            conn.close()
            return BacktestResult(error="No data in database")

        dates = dates_df["dt"].tolist()
        logger.info("Backtest: %d trading days from %s", len(dates), db_path)

        # ── Replay each day ─────────────────────────────────────────
        for d in dates:
            self._replay_day(conn, d)

        conn.close()

        # ── Compute metrics ─────────────────────────────────────────
        return self._compute_metrics()

    def _replay_day(self, conn: sqlite3.Connection, day: str) -> None:
        """Replay a single trading day."""
        # Get all tickers that traded on this day
        tickers_df = pd.read_sql(
            "SELECT DISTINCT ticker FROM histori_ihsg WHERE DATE(waktu) = ?",
            conn, params=[day],
        )
        tickers = tickers_df["ticker"].tolist()

        for ticker in tickers:
            df = pd.read_sql(
                "SELECT open, high, low, harga as close, volume, waktu "
                "FROM histori_ihsg WHERE ticker = ? AND DATE(waktu) = ? "
                "ORDER BY waktu ASC",
                conn, params=[ticker, day],
            )
            if len(df) < 10:
                continue

            df = df.reset_index(drop=True)

            # Split into morning (first 25 bars ≈ 09:05-09:30) and afternoon
            morning_cutoff = min(25, len(df))
            morning_df = df.iloc[:morning_cutoff]
            afternoon_df = df.iloc[morning_cutoff:] if len(df) > morning_cutoff else pd.DataFrame()

            # ── Check morning breakout ─────────────────────────
            if len(morning_df) >= self.config.morning_min_bars:
                self._check_strategy(ticker, day, morning_df, "morning")

            # ── Check afternoon momentum ───────────────────────
            if len(afternoon_df) >= self.config.afternoon_min_bars:
                self._check_strategy(ticker, day, afternoon_df, "afternoon")

    def _check_strategy(
        self,
        ticker: str,
        day: str,
        df: pd.DataFrame,
        session: str,
    ) -> None:
        """Check if a strategy triggers and simulate exit."""
        open_p = df["open"].astype(float)
        high_p = df["high"].astype(float)
        low_p = df["low"].astype(float)
        close_p = df["close"].astype(float)
        vol_p = df["volume"].astype(float)

        # Compute features
        feat = compute_intraday_features(open_p, high_p, low_p, close_p, vol_p, self.config)

        # Route to strategy
        if session == "morning":
            result = detect_morning_breakout(feat, self.config)
        else:
            result = detect_afternoon_momentum(feat, self.config)

        if result.signal == "HINDARI":
            return

        # ── Simulate exit ───────────────────────────────────────
        entry_idx = len(df) - 1
        entry_price = result.entry_price
        sl = result.stop_loss
        tp = result.take_profit
        shares = 100  # minimum 1 lot

        # Walk forward through remaining bars
        remaining = df.iloc[entry_idx + 1:] if entry_idx + 1 < len(df) else pd.DataFrame()
        exit_price, exit_idx, exit_reason = self._simulate_exit(
            entry_price, sl, tp, remaining, entry_idx,
        )

        # Apply costs
        gross_entry = entry_price * shares * (1 + self.config.buy_fee_pct)
        gross_exit = exit_price * shares * (1 - self.config.sell_fee_pct - self.config.slippage_pct)
        pnl = gross_exit - gross_entry
        pnl_pct = (pnl / gross_entry * 100) if gross_entry > 0 else 0

        holding_bars = max(1, exit_idx - entry_idx)
        holding_min = holding_bars  # 1 bar = 1 minute

        entry_time_str = str(df.iloc[entry_idx]["waktu"]) if "waktu" in df.columns else day
        exit_time_str = str(df.iloc[exit_idx]["waktu"]) if exit_idx < len(df) and "waktu" in df.columns else day

        self.trades.append(TradeRecord(
            ticker=ticker,
            date=day,
            session=session,
            entry_time=entry_time_str,
            exit_time=exit_time_str,
            entry_price=entry_price,
            exit_price=exit_price,
            shares=shares,
            stop_loss=sl,
            take_profit=tp,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            holding_minutes=holding_min,
            signal=result.signal,
            strategy=result.strategy,
        ))

    def _simulate_exit(
        self,
        entry: float,
        sl: float,
        tp: float,
        bars: pd.DataFrame,
        entry_idx: int,
    ) -> tuple[float, int, str]:
        """Simulate realistic exit using OHLC bars with trailing stop.

        Returns (exit_price, bar_index, exit_reason).
        """
        if bars.empty:
            last_close = entry  # No more bars — exit at entry (EOD)
            return last_close, entry_idx, "EOD_CLOSE"

        highest_since_entry = entry
        current_sl = sl

        for i, (_, bar) in enumerate(bars.iterrows()):
            bar_high = float(bar["high"])
            bar_low = float(bar["low"])
            bar_close = float(bar["close"])

            # Update highest price seen
            if bar_high > highest_since_entry:
                highest_since_entry = bar_high

            # Trailing stop: breakeven
            profit_from_entry = (bar_close - entry) / entry
            if profit_from_entry >= self.config.breakeven_trigger_pct and current_sl < entry:
                current_sl = entry

            # Trailing stop: trail after activation
            if profit_from_entry >= self.config.trailing_activation_pct:
                trail_sl = highest_since_entry * (1 - self.config.trailing_distance_pct)
                current_sl = max(current_sl, trail_sl)

            # Check TP hit (high >= TP)
            if bar_high >= tp:
                return tp, entry_idx + i + 1, "TAKE_PROFIT"

            # Check SL hit (low <= SL)
            if bar_low <= current_sl:
                return current_sl, entry_idx + i + 1, "CUT_LOSS"

        # End of day — close position
        last_close = float(bars.iloc[-1]["close"])
        return last_close, entry_idx + len(bars), "EOD_CLOSE"

    def _compute_metrics(self) -> BacktestResult:
        """Compute comprehensive performance metrics."""
        if not self.trades:
            return BacktestResult(error="No trades generated")

        n = len(self.trades)
        returns = np.array([t.pnl_pct / 100 for t in self.trades])
        pnls = np.array([t.pnl for t in self.trades])
        wins = returns[returns > 0]
        losses = returns[returns < 0]

        win_rate = len(wins) / n
        total_wins = wins.sum() if len(wins) > 0 else 0.0
        total_losses = abs(losses.sum()) if len(losses) > 0 else 1e-9
        profit_factor = abs(total_wins / total_losses) if total_losses > 0 else float("inf")

        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
        expectancy = float(np.mean(returns))

        std_ret = float(np.std(returns, ddof=1)) if n > 1 else 1e-9
        if std_ret < 1e-12:
            std_ret = 1e-9
        rf_daily = RISK_FREE_RATE / TRADING_DAYS_YEAR
        sharpe = (expectancy - rf_daily) / std_ret * np.sqrt(TRADING_DAYS_YEAR)

        downside = returns[returns < 0]
        downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 1e-9
        sortino = (expectancy - rf_daily) / max(downside_std, 1e-9) * np.sqrt(TRADING_DAYS_YEAR)

        # Drawdown
        cumulative = np.cumprod(1 + returns)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = (peak - cumulative) / peak
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

        avg_holding = np.mean([t.holding_minutes for t in self.trades])

        # Time-band breakdown
        morning_trades = [t for t in self.trades if t.session == "morning"]
        afternoon_trades = [t for t in self.trades if t.session == "afternoon"]

        morning_wr = (
            len([t for t in morning_trades if t.pnl > 0]) / max(1, len(morning_trades)) * 100
        )
        afternoon_wr = (
            len([t for t in afternoon_trades if t.pnl > 0]) / max(1, len(afternoon_trades)) * 100
        )
        morning_pnl = sum(t.pnl for t in morning_trades)
        afternoon_pnl = sum(t.pnl for t in afternoon_trades)

        return BacktestResult(
            total_trades=n,
            win_rate_pct=round(win_rate * 100, 1),
            profit_factor=round(profit_factor, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown_pct=round(max_dd * 100, 1),
            expectancy_r=round(expectancy, 4),
            avg_win_pct=round(avg_win * 100, 3),
            avg_loss_pct=round(avg_loss * 100, 3),
            avg_holding_minutes=round(avg_holding, 1),
            morning_trades=len(morning_trades),
            morning_win_rate=round(morning_wr, 1),
            morning_pnl=round(morning_pnl, 0),
            afternoon_trades=len(afternoon_trades),
            afternoon_win_rate=round(afternoon_wr, 1),
            afternoon_pnl=round(afternoon_pnl, 0),
            equity_curve=list(cumulative),
            drawdown_curve=list(drawdowns * 100),
            trade_log=self.trades,
        )


# ── Convenience Functions ──────────────────────────────────────────

def run_intraday_backtest(
    db_path: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    config: ScalpConfig | None = None,
) -> BacktestResult:
    """Run intraday backtest and return result."""
    bt = IntradayBacktest(config)
    return bt.run(db_path, start_date, end_date)


def print_tearsheet(result: BacktestResult) -> None:
    """Print formatted backtest tearsheet to console."""
    if result.error:
        print(f"Backtest Error: {result.error}")
        return

    print("\n" + "=" * 60)
    print("  INTRADAY SCALPING BACKTEST — TEARSHEET")
    print("=" * 60)
    print(f"  Total Trades:        {result.total_trades}")
    print(f"  Win Rate:            {result.win_rate_pct:.1f}%")
    print(f"  Profit Factor:       {result.profit_factor:.2f}")
    print(f"  Sharpe Ratio:        {result.sharpe_ratio:.2f}")
    print(f"  Sortino Ratio:       {result.sortino_ratio:.2f}")
    print(f"  Max Drawdown:        {result.max_drawdown_pct:.1f}%")
    print(f"  Expectancy (R):      {result.expectancy_r:.4f}")
    print(f"  Avg Win:             {result.avg_win_pct:.3f}%")
    print(f"  Avg Loss:            {result.avg_loss_pct:.3f}%")
    print(f"  Avg Holding:         {result.avg_holding_minutes:.1f} min")
    print("-" * 60)
    print(f"  Morning Trades:      {result.morning_trades} (WR: {result.morning_win_rate:.1f}%)")
    print(f"  Morning PnL:         Rp {result.morning_pnl:,.0f}")
    print(f"  Afternoon Trades:    {result.afternoon_trades} (WR: {result.afternoon_win_rate:.1f}%)")
    print(f"  Afternoon PnL:       Rp {result.afternoon_pnl:,.0f}")
    print("=" * 60 + "\n")


# ── CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Intraday Scalping Backtest")
    parser.add_argument("--db", default=None, help="Path to histori_ihsg.db")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--detail", action="store_true", help="Show trade log")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    result = run_intraday_backtest(
        db_path=args.db,
        start_date=args.start,
        end_date=args.end,
    )
    print_tearsheet(result)

    if args.detail and result.trade_log:
        print("Trade Log:")
        for t in result.trade_log[:50]:
            emoji = "🟢" if t.pnl > 0 else "🔴"
            print(f"  {emoji} {t.date} {t.ticker:6s} {t.strategy:20s} "
                  f"PnL=Rp{t.pnl:+,.0f} ({t.pnl_pct:+.2f}%) {t.holding_minutes:.0f}min {t.exit_reason}")
