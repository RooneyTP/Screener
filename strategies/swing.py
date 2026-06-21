"""
strategies/swing.py — Swing Trading Strategy
=============================================
Consolidated from src/signals/* + core/scoring.py (ADX adaptive weights).

Sources:
    - src/signals/scoring.py      → ScoringEngine (scoring, signal determination)
    - src/signals/swing_strategy.py → market_regime_swing, SL/TP computation, MTF alignment
    - src/signals/ai_coordinator.py → predict_swing, ai_verdict
    - src/execution/sizer.py      → ATR-based, fixed-fractional, half-kelly position sizing
    - src/execution/slippage.py   → ATR-based slippage model
    - core/scoring.py             → get_adaptive_weights (ADX regime-based weights)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

from ta.trend import EMAIndicator

# ── ADX adaptive weights from core/scoring.py ──────────────────────────────
from core.scoring import get_adaptive_weights

# ── Position sizing from src/execution/sizer.py ────────────────────────────
from src.execution.sizer import (
    fixed_fractional as position_size_fixed,
    atr_based as position_size_atr,
    half_kelly as position_size_kelly,
)

# ── ATR-based slippage from src/execution/slippage.py ─────────────────────
from src.execution.slippage import atr_slippage, spread_estimate

logger = logging.getLogger("swing_strategy")


# ═══════════════════════════════════════════════════════════════════════════
# REGIME CLASSIFICATION (from src/signals/swing_strategy.py)
# ═══════════════════════════════════════════════════════════════════════════

def market_regime_swing(close: pd.Series, atr: pd.Series) -> str:
    """Classify market regime for swing trading.

    Args:
        close: Price series.
        atr: ATR series.

    Returns:
        "HIGH_VOLATILITY", "TRENDING", or "RANGING".
    """
    avg_atr = atr.tail(20).mean()
    current_atr = atr.iloc[-1]
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]

    if current_atr > avg_atr * 1.5:
        return "HIGH_VOLATILITY"
    elif abs(sma20 - sma50) / sma50 > 0.05:
        return "TRENDING"
    return "RANGING"


# ═══════════════════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME BULLISH CHECK (from src/signals/swing_strategy.py)
# ═══════════════════════════════════════════════════════════════════════════

def compute_multi_timeframe_bullish(
    close_daily: pd.Series,
    data_weekly: pd.DataFrame,
    data_monthly: pd.DataFrame,
) -> tuple[bool, bool]:
    """Check weekly and monthly bullish alignment (price > EMA)."""
    weekly_bullish = monthly_bullish = False

    try:
        if not data_weekly.empty and len(data_weekly) >= 20:
            close_w = data_weekly["Close"].squeeze()
            ema20_w = EMAIndicator(close=close_w, window=20).ema_indicator()
            weekly_bullish = float(close_w.iloc[-1]) > float(ema20_w.iloc[-1])
    except Exception:
        pass

    try:
        if not data_monthly.empty and len(data_monthly) >= 12:
            close_m = data_monthly["Close"].squeeze()
            ema12_m = EMAIndicator(close=close_m, window=12).ema_indicator()
            monthly_bullish = float(close_m.iloc[-1]) > float(ema12_m.iloc[-1])
    except Exception:
        pass

    return weekly_bullish, monthly_bullish


# ═══════════════════════════════════════════════════════════════════════════
# ATR-BASED SL / TP (from src/signals/swing_strategy.py)
# ═══════════════════════════════════════════════════════════════════════════

def compute_swing_entry_sl_tp(
    price: float,
    atr_v: float,
    regime: str,
    weekly_bullish: bool,
    monthly_bullish: bool,
) -> dict:
    """ATR-based Stop Loss and Take Profit levels for swing trades."""
    risk_factor = 1.5 if regime == "HIGH_VOLATILITY" else 1.2
    base_sl = price - (risk_factor * atr_v)

    if weekly_bullish and monthly_bullish:
        base_sl = price - (risk_factor * atr_v * 1.3)

    stop_loss = max(base_sl, price * 0.92)
    target_1 = price + (2.0 * atr_v)
    target_2 = price + (3.5 * atr_v)
    target_3 = price + (5.0 * atr_v)

    risk_pct = round(((price - stop_loss) / price) * 100, 1)
    reward_pct = round(((target_1 - price) / price) * 100, 1)
    rrr = round(reward_pct / risk_pct, 2) if risk_pct != 0 else 0

    return {
        "stop_loss": stop_loss,
        "target_1": target_1,
        "target_2": target_2,
        "target_3": target_3,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
        "rrr": rrr,
    }


# ═══════════════════════════════════════════════════════════════════════════
# AI PREDICTION (from src/signals/ai_coordinator.py)
# ═══════════════════════════════════════════════════════════════════════════

def predict_swing(features_14: list[float]) -> tuple[float, str]:
    """Predict win probability for swing trading.

    Tries ensemble model first, then ai_model fallback, then heuristic.
    Returns (win_probability_0_100, method_used).
    """
    # 1. Try ensemble model (latih_ai.py output)
    try:
        import joblib
        import os
        if os.path.exists("ensemble_model.pkl"):
            bundle = joblib.load("ensemble_model.pkl")
            ensemble = bundle["ensemble"]
            clean = np.nan_to_num(
                np.array(features_14, dtype=float).reshape(1, -1),
                nan=0.0, posinf=0.0, neginf=0.0,
            )
            proba = ensemble.predict_proba(clean)[0]
            win_prob = proba[1] * 100 if len(proba) > 1 else proba[0] * 100
            return round(float(win_prob), 2), "ensemble"
    except Exception as e:
        logger.debug("[AI] Ensemble failed: %s", e)

    # 2. Fallback to ai_model.py MarketAI
    try:
        from ai_model import get_ai_model
        ai = get_ai_model(model_type="swing")
        features_10 = [
            features_14[2], features_14[3], 50.0,
            features_14[7], features_14[8], features_14[9],
            features_14[10], features_14[11], features_14[13], 0.0,
        ]
        win_prob = ai.predict_win_probability(features_10)
        if win_prob > 0:
            return round(float(win_prob), 2), "ai_model_v4"
    except Exception as e:
        logger.debug("[AI] ai_model fallback failed: %s", e)

    # 3. Heuristic last resort
    rsi = features_14[2] if len(features_14) > 2 else 50
    adx = features_14[3] if len(features_14) > 3 else 20
    mm_conf = features_14[8] if len(features_14) > 8 else 50
    prob = 40.0
    if 40 <= rsi <= 65:
        prob += 10
    if adx > 25:
        prob += 10
    if mm_conf >= 70:
        prob += 10
    return round(min(85.0, prob), 2), "heuristic"


def ai_verdict(win_prob: float) -> str:
    """Convert win probability to trading verdict string."""
    if win_prob >= 60:
        return "ULTRA BUY"
    elif win_prob >= 50:
        return "BUY"
    return "WEAK"


# ═══════════════════════════════════════════════════════════════════════════
# SCORING ENGINE (from src/signals/scoring.py, with ADX adaptive weights)
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_score(score: float, typical_max: float = 65.0) -> float:
    """Normalize component score to 0–100 range."""
    return min(100, max(0, (score / typical_max) * 100))


class ScoringEngine:
    """Scoring logic for swing trading signals, with ADX-adaptive weights."""

    def __init__(self, weights: dict | None = None):
        self.weights = weights or {
            "technical": 0.35,
            "fundamental": 0.25,
            "relative_strength": 0.20,
            "sentiment": 0.20,
        }

    def score_technical(
        self,
        price: float,
        ema21_val: float,
        ema50_val: float,
        hma_val: float,
        rsi_v: float,
        macd_line: float,
        macd_signal: float,
        macd_hist: float,
        macd_hist_prev: float,
        vol_v: float,
        vol_sma_v: float,
        adx_v: float,
        bb_mid_v: float,
        support: float,
        obv_v: float,
        obv_ma: float,
        pattern: str,
        breakout_strength: bool,
    ) -> tuple[float, list[str]]:
        """Calculate technical score component (0–100). Returns (score, confirmations)."""
        score = 0
        confirmations = []

        if price > ema21_val > ema50_val and price > hma_val:
            score += 20
            confirmations.append("EMA+HMA")
        elif price > ema21_val > ema50_val:
            score += 15
            confirmations.append("EMA")

        if 30 <= rsi_v <= 50:
            score += 15
            confirmations.append("RSI_Entry")
        if macd_hist > 0 and macd_hist > macd_hist_prev:
            score += 15
            confirmations.append("MACD_Bullish")
        if vol_v > vol_sma_v * 1.5:
            score += 15
            confirmations.append("Volume_Surge")
        if adx_v > 35:
            score += 20
            confirmations.append("ADX_Strong")
        if pattern in ["BREAKOUT", "REVERSAL"]:
            score += 15
            confirmations.append(f"Pattern_{pattern}")
        if breakout_strength:
            score += 10
            confirmations.append("Breakout")
        if price > bb_mid_v and price > support:
            score += 10
            confirmations.append("Above_BB_Mid")

        return min(100, score), confirmations

    def score_fundamental(
        self, per_val: float, pbv_val: float, earnings_growth: float,
    ) -> tuple[float, list[str]]:
        score = 0
        confirmations = []
        if 0 < per_val <= 12:
            score += 25
            confirmations.append("PER_Cheap")
        elif 12 < per_val <= 18:
            score += 15
            confirmations.append("PER_Fair")
        if 0 < pbv_val <= 1.0:
            score += 25
            confirmations.append("PBV_Strong")
        elif pbv_val > 5.0:
            score -= 20
            confirmations.append("PBV_Expensive")
        if earnings_growth > 0:
            score += 15
            confirmations.append("Earnings_Growth")
        return min(100, max(-30, score)), confirmations

    def score_relative_strength(
        self,
        stock_ret_20: float,
        ihsg_ret_20: float,
        sector_momentum: float,
    ) -> tuple[float, list[str]]:
        score = 0
        confirmations = []
        if stock_ret_20 > ihsg_ret_20 + 5:
            score += 30
            confirmations.append("RS_Outperform")
        if stock_ret_20 > ihsg_ret_20:
            score += 20
            confirmations.append("RS_Beat_IHSG")
        if sector_momentum > 1.5:
            score += 25
            confirmations.append("Sector_Leading")
        return min(100, score), confirmations

    def score_sentiment(
        self,
        sentiment_score: float,
        news_count: int,
        foreign_status: str,
        mm_activity: str,
    ) -> tuple[float, list[str]]:
        score = 0
        confirmations = []
        if sentiment_score > 0.25 and news_count >= 2:
            score += 30
            confirmations.append("Sentiment_Strong_Bull")
        elif sentiment_score > 0.1 and news_count >= 2:
            score += 15
            confirmations.append("Sentiment_Bull")
        elif sentiment_score < -0.2 and news_count >= 2:
            score -= 30
            confirmations.append("Sentiment_Bear")
        if foreign_status == "ACCUMULATION":
            score += 20
            confirmations.append("Foreign_Buy")
        elif foreign_status == "DISTRIBUTION":
            score -= 25
            confirmations.append("Foreign_Sell")
        if mm_activity == "ACCUMULATION":
            score += 15
            confirmations.append("MM_Accum")
        return min(100, max(-50, score)), confirmations

    def compute_confidence(
        self,
        tech_score: float,
        fund_score: float,
        rs_score: float,
        sent_score: float,
        adx_val: float,
        ihsg_change: float,
        ihsg_trend: str,
        weekly_bullish: bool,
        monthly_bullish: bool,
        pct_above_ema50: float = 50.0,
    ) -> tuple[float, float, int]:
        """Compute confidence using ADX-adaptive weights from core/scoring.

        Returns:
            (confidence_0_100, skor_0_15, c_thresh_buy).
        """
        # Normalize component scores
        n_tech = _normalize_score(tech_score, 65)
        n_fund = _normalize_score(fund_score, 50)
        n_rs = _normalize_score(rs_score, 50)
        n_sent = _normalize_score(sent_score, 30)

        # ADX-adaptive weights (from core/scoring.py)
        w_tech, w_fund, w_rs, w_sent = get_adaptive_weights(adx_val)

        confidence = (
            n_tech * w_tech
            + max(0, n_fund) * w_fund
            + n_rs * w_rs
            + max(0, n_sent) * w_sent
        )
        confidence = min(100, max(0, confidence))

        # IHSG penalty (softer — only penalize when IHSG is actually dropping)
        if isinstance(ihsg_change, (int, float)) and ihsg_change < -1.0:
            confidence -= 8
        elif isinstance(ihsg_change, (int, float)) and ihsg_change < -0.3:
            confidence -= 3

        # Multi-timeframe alignment
        if weekly_bullish and monthly_bullish:
            confidence += 5
        elif not weekly_bullish and not monthly_bullish:
            confidence -= 3
        elif not weekly_bullish:
            confidence -= 1

        confidence = min(100, max(5, confidence))
        skor = round(confidence / 100.0 * 15, 1)

        # Dynamic breadth threshold
        breadth_tightness = 1.0 + max(0, (50 - pct_above_ema50)) / 100
        c_thresh_buy = min(90, int((55 if ihsg_trend == "UP" else 65) * breadth_tightness))

        return confidence, skor, c_thresh_buy

    def determine_signal(
        self,
        confidence: float,
        skor: float,
        rrr: float,
        weekly_bullish: bool,
        ihsg_trend: str,
        per_val: float,
        pbv_val: float,
    ) -> tuple[str, str]:
        """Determine signal grade from confidence, skor, and thresholds."""
        threshold_strong = 75 if ihsg_trend == "UP" else 80
        threshold_buy = 55 if ihsg_trend == "UP" else 65

        has_critical_risk = per_val < -50 or pbv_val > 50
        if has_critical_risk:
            return "HINDARI", "RISK"

        if confidence >= 85 and skor >= 10 and rrr >= 1.8 and weekly_bullish and ihsg_trend == "UP":
            return "ULTRA_BUY", "A+"
        if confidence >= 80 and skor >= 9.5 and rrr >= 1.6 and weekly_bullish:
            return "ULTRA_BUY", "A"
        if confidence >= threshold_strong and skor >= 8.0:
            return "STRONG_BUY", "B+"
        if confidence >= 70 and skor >= 7.0:
            return "STRONG_BUY", "B"
        if confidence >= threshold_buy and skor >= 4.0:
            return "BUY", "C"
        if confidence >= 30 and skor >= 2.0:
            return "PANTAU", "D"
        if skor >= -15.0:
            return "TUNGGU", "E"
        return "HINDARI", "F"

    def determine_signal_v11(
        self,
        confidence: float,
        skor: float,
        c_thresh_buy: int,
        rrr: float,
        weekly_bullish: bool,
        ihsg_trend: str,
    ) -> tuple[str, str]:
        """v11 signal determination (from core/scoring.py get_signal)."""
        c_thresh_strong = 75 if ihsg_trend == "UP" else 80
        signal = "HINDARI"
        signal_strength = "F"

        if confidence >= 85 and skor >= 10 and rrr >= 1.8 and weekly_bullish and ihsg_trend == "UP":
            signal, signal_strength = "ULTRA_BUY", "A+"
        elif confidence >= 80 and skor >= 9.5 and rrr >= 1.6 and weekly_bullish:
            signal, signal_strength = "ULTRA_BUY", "A"
        elif confidence >= c_thresh_strong and skor >= 8.0:
            signal, signal_strength = "STRONG_BUY", "B+"
        elif confidence >= 70 and skor >= 7.0:
            signal, signal_strength = "STRONG_BUY", "B"
        elif confidence >= c_thresh_buy and skor >= 4.0:
            signal, signal_strength = "BUY", "C"
        elif confidence >= 30 and skor >= 2.0:
            signal, signal_strength = "PANTAU", "D"
        elif skor >= -15.0:
            signal, signal_strength = "TUNGGU", "E"

        return signal, signal_strength


# ═══════════════════════════════════════════════════════════════════════════
# SWINGSTRATEGY CLASS — Main interface
# ═══════════════════════════════════════════════════════════════════════════

class SwingStrategy:
    """Swing trading strategy combining scoring, AI predictions, and position sizing.

    Usage:
        strategy = SwingStrategy(account_equity=100_000_000)
        result = strategy.analyze("BBCA")
        # result contains signal, entry/sl/tp, confidence, position size, etc.
    """

    def __init__(
        self,
        account_equity: float = 100_000_000.0,
        risk_per_trade: float = 0.01,
        sizing_model: str = "atr",  # "atr" | "fixed" | "kelly"
        use_ai: bool = True,
    ):
        """
        Args:
            account_equity: Total trading capital in IDR.
            risk_per_trade: Fraction of capital risked per trade (default 1%).
            sizing_model: Position sizing method — "atr", "fixed", or "kelly".
            use_ai: Whether to attempt AI-powered predictions.
        """
        self.account_equity = account_equity
        self.risk_per_trade = risk_per_trade
        self.sizing_model = sizing_model
        self.use_ai = use_ai
        self.scoring = ScoringEngine()
        logger.info(
            "SwingStrategy initialized (equity=%.0f, risk=%.2f%%, sizing=%s, ai=%s)",
            account_equity, risk_per_trade * 100, sizing_model, use_ai,
        )

    def get_signal(self, df: pd.DataFrame) -> dict:
        """Compute trading signal from a DataFrame with OHLCV + indicators.

        Args:
            df: DataFrame with columns:
                Open, High, Low, Close, Volume,
                EMA_21, EMA_50, HMA, RSI_14,
                MACD_line, MACD_signal, MACD_hist, MACD_hist_prev,
                VOLUME_SMA, ADX_14, BB_Mid,
                Support, OBV, OBV_MA,
                PER, PBV, Earnings_Growth,
                stock_return_20d, ihsg_return_20d, sector_momentum,
                Sentiment_Score, News_Count,
                Foreign_Status, MM_Activity,
                Pattern, Breakout_Strength.

        Returns:
            dict with keys: signal, strength, confidence, skor, rrr,
                stop_loss, targets, position_size, method, confirmations, etc.
        """
        row = df.iloc[-1]

        # ── Market regime ──
        close = df["Close"]
        atr = df["ATR_14"] if "ATR_14" in df.columns else _estimate_atr(df)
        regime = market_regime_swing(close, atr)

        # ── Multi-timeframe alignment ──
        weekly_bullish = bool(row.get("Weekly_Bullish", False))
        monthly_bullish = bool(row.get("Monthly_Bullish", False))

        # ── Scoring components ──
        tech_score, tech_confirmations = self.scoring.score_technical(
            price=float(row["Close"]),
            ema21_val=float(row["EMA_21"]),
            ema50_val=float(row["EMA_50"]),
            hma_val=float(row.get("HMA", row["EMA_21"])),
            rsi_v=float(row.get("RSI_14", 50)),
            macd_line=float(row.get("MACD_line", 0)),
            macd_signal=float(row.get("MACD_signal", 0)),
            macd_hist=float(row.get("MACD_hist", 0)),
            macd_hist_prev=float(row.get("MACD_hist_prev", 0)),
            vol_v=float(row.get("Volume", 0)),
            vol_sma_v=float(row.get("VOLUME_SMA", 1)),
            adx_v=float(row.get("ADX_14", 20)),
            bb_mid_v=float(row.get("BB_Mid", row["Close"])),
            support=float(row.get("Support", row["Close"] * 0.95)),
            obv_v=float(row.get("OBV", 0)),
            obv_ma=float(row.get("OBV_MA", 0)),
            pattern=str(row.get("Pattern", "")),
            breakout_strength=bool(row.get("Breakout_Strength", False)),
        )

        fund_score, fund_confirmations = self.scoring.score_fundamental(
            per_val=float(row.get("PER", 0)),
            pbv_val=float(row.get("PBV", 0)),
            earnings_growth=float(row.get("Earnings_Growth", 0)),
        )

        rs_score, rs_confirmations = self.scoring.score_relative_strength(
            stock_ret_20=float(row.get("stock_return_20d", 0)),
            ihsg_ret_20=float(row.get("ihsg_return_20d", 0)),
            sector_momentum=float(row.get("sector_momentum", 1.0)),
        )

        sent_score, sent_confirmations = self.scoring.score_sentiment(
            sentiment_score=float(row.get("Sentiment_Score", 0)),
            news_count=int(row.get("News_Count", 0)),
            foreign_status=str(row.get("Foreign_Status", "")),
            mm_activity=str(row.get("MM_Activity", "")),
        )

        # ── Confidence with ADX-adaptive weights ──
        adx_val = float(row.get("ADX_14", 20))
        confidence, skor, c_thresh_buy = self.scoring.compute_confidence(
            tech_score=tech_score,
            fund_score=fund_score,
            rs_score=rs_score,
            sent_score=sent_score,
            adx_val=adx_val,
            ihsg_change=float(row.get("ihsg_change", 0)),
            ihsg_trend=str(row.get("ihsg_trend", "SIDE")),
            weekly_bullish=weekly_bullish,
            monthly_bullish=monthly_bullish,
            pct_above_ema50=float(row.get("pct_above_ema50", 50.0)),
        )

        # ── Entry levels (SL/TP) ──
        atr_v = float(atr.iloc[-1]) if hasattr(atr, "iloc") else float(atr)
        price = float(row["Close"])
        levels = compute_swing_entry_sl_tp(price, atr_v, regime, weekly_bullish, monthly_bullish)

        # ── Signal ──
        signal, strength = self.scoring.determine_signal_v11(
            confidence=confidence,
            skor=skor,
            c_thresh_buy=c_thresh_buy,
            rrr=levels["rrr"],
            weekly_bullish=weekly_bullish,
            ihsg_trend=str(row.get("ihsg_trend", "SIDE")),
        )

        # ── AI prediction ──
        ai_prob = 50.0
        ai_method = "none"
        verdict = "WEAK"
        if self.use_ai:
            features = _build_features_14(row)
            ai_prob, ai_method = predict_swing(features)
            verdict = ai_verdict(ai_prob)

        # ── Position sizing ──
        shares = 0
        if self.sizing_model == "atr":
            shares = position_size_atr(
                account_equity=self.account_equity,
                risk_pct=self.risk_per_trade,
                atr_value=atr_v,
                atr_multiplier=2.0,
                entry=price,
            )
        elif self.sizing_model == "fixed":
            shares = position_size_fixed(
                account_equity=self.account_equity,
                risk_pct=self.risk_per_trade,
                entry=price,
                stop_loss=levels["stop_loss"],
            )
        elif self.sizing_model == "kelly":
            win_rate = ai_prob / 100.0
            loss_rate = 1.0 - win_rate
            wl_ratio = levels["rrr"] if levels["rrr"] > 0 else 1.5
            shares, _ = position_size_kelly(
                account_equity=self.account_equity,
                win_rate=win_rate,
                loss_rate=loss_rate,
                win_loss_ratio=wl_ratio,
                entry=price,
            )

        # ── ATR-based slippage estimate ──
        slippage = atr_slippage(atr, price) if hasattr(atr, "iloc") else 0.001

        # ── Compile result ──
        confirmations = (
            tech_confirmations + fund_confirmations
            + rs_confirmations + sent_confirmations
        )

        return {
            "ticker": str(row.get("Ticker", "")),
            "signal": signal,
            "strength": strength,
            "confidence": round(confidence, 1),
            "skor": skor,
            "regime": regime,
            "price": price,
            "stop_loss": levels["stop_loss"],
            "target_1": levels["target_1"],
            "target_2": levels["target_2"],
            "target_3": levels["target_3"],
            "rrr": levels["rrr"],
            "risk_pct": levels["risk_pct"],
            "reward_pct": levels["reward_pct"],
            "position_size_shares": shares,
            "position_size_nominal": round(shares * price, 0),
            "slippage_est_pct": slippage * 100,
            "ai_win_prob": ai_prob,
            "ai_method": ai_method,
            "ai_verdict": verdict,
            "confirmations": confirmations,
            "sizing_model": self.sizing_model,
        }

    def analyze(self, ticker: str) -> dict:
        """Analyze a single ticker (placeholder — expects pre-loaded data).

        Real usage requires fetching OHLCV, computing indicators, then calling get_signal().
        This method provides a stub that returns a skeleton result.
        """
        return {
            "ticker": ticker,
            "status": "requires OHLCV data — call get_signal(df) with a populated DataFrame",
            "note": (
                "Use analyze() only when you have pre-fetched data. "
                "For live analysis, build a DataFrame with required columns and pass to get_signal()."
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _estimate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Simple ATR estimation if ATR column is missing."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


def _build_features_14(row: pd.Series) -> list[float]:
    """Build 14-feature vector for AI prediction from data row."""
    return [
        float(row.get("Close", 0)),
        float(row.get("Volume", 0)),
        float(row.get("RSI_14", 50)),
        float(row.get("ADX_14", 20)),
        float(row.get("MACD_hist", 0)),
        float(row.get("BB_%B", 0.5)),
        float(row.get("OBV", 0)),
        float(row.get("PER", 0)),
        float(row.get("PBV", 0)),
        float(row.get("stock_return_20d", 0)),
        float(row.get("ihsg_return_20d", 0)),
        float(row.get("Sentiment_Score", 0)),
        float(row.get("sector_momentum", 1)),
        float(row.get("Earnings_Growth", 0)),
    ]
