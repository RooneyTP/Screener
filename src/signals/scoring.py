# src/signals/scoring.py — Signal Scoring Engine (extracted from screener.py)
# FIX: Refactored from the 700-line god function analisis_saham()

from typing import Optional
import pandas as pd
import numpy as np


class ScoringEngine:
    """Encapsulates all scoring logic for swing trading signals."""

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

    def score_fundamental(self, per_val: float, pbv_val: float, earnings_growth: float) -> tuple[float, list[str]]:
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
        ihsg_trend: str,
        weekly_bullish: bool,
        monthly_bullish: bool,
    ) -> float:
        w = self.weights
        confidence = (
            tech_score * 0.60 +
            max(0, fund_score) * 0.10 +
            rs_score * 0.20 +
            max(0, sent_score) * 0.10
        )

        if ihsg_trend == "DOWN":
            confidence -= 6
        if weekly_bullish and monthly_bullish:
            confidence += 5
        elif not weekly_bullish and not monthly_bullish:
            confidence -= 8
        elif not weekly_bullish:
            confidence -= 4

        return min(100, max(0, confidence))

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
        if confidence >= 50 and skor >= 4.0:
            return "BUY", "C"
        if confidence >= 30 and skor >= 2.0:
            return "PANTAU", "D"
        if skor >= -15.0:
            return "TUNGGU", "E"
        return "HINDARI", "F"
