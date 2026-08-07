"""
entry_timing.py — Entry timing & price recommendation per stock
================================================================
Memberi rekomendasi entry untuk setiap stock yang dapat sinyal BUY di V7.
Digunakan oleh v7_scan.py untuk menambah entry_recommendation di tiap sinyal.
"""
import numpy as np
import pandas as pd
import logging
from typing import Optional, Dict

logger = logging.getLogger("entry_timing")


def recommend_entry(
    ticker: str,
    price: float,
    atr: float,
    row: pd.Series,
    v7_result: dict,
    market_sentiment: Optional[dict] = None,
) -> dict:
    """
    Rekomendasi entry timing & price untuk satu stock.

    Parameters
    ----------
    ticker : str — kode saham
    price : float — harga close terakhir
    atr : float — Average True Range
    row : pd.Series — baris terakhir dari dataframe (indikator teknikal)
    v7_result : dict — output dari v7_engine.compute()
    market_sentiment : dict, optional — dari predict_market_sentiment()

    Returns
    -------
    dict dengan keys:
        method      : str — metode entry
        price_range : str — rentang harga rekomendasi
        condition   : str — kondisi tambahan
        skip_reason : str, optional — alasan skip jika tidak direkomendasikan
    """
    if (price is None or atr is None
            or pd.isna(price) or pd.isna(atr)
            or price <= 0 or atr <= 0):
        return {
            "method": "Tidak bisa entry",
            "price_range": "-",
            "condition": "Data harga/ATR tidak valid",
        }

    signal = v7_result.get("signal", "HOLD")
    factors = v7_result.get("factors", {})
    broker_detail = factors.get("broker_detail", "netral")
    score = v7_result.get("score", 0)

    # Data dari row
    rsi = float(row.get("rsi", 50) or 50)
    pct_vwap = float(row.get("pct_vs_vwap", 0) or 0)
    vol_ratio = float(row.get("vol_ratio", 1) or 1)
    weekly = str(row.get("weekly_trend", "NO_DATA"))
    vwap = float(row.get("vwap", price) or price)
    dc_lower = float(row.get("dc_lower", price * 0.95) or price * 0.95)
    ema50 = float(row.get("ema50", price) or price)

    sentiment = (market_sentiment or {}).get("sentiment", "YELLOW")

    # ── Rule 1: Market sentiment RED → semua HOLD CASH kecuali STRONG_BUY ──
    if sentiment == "RED":
        if signal == "WEAK_BUY":
            return {
                "method": "⛔ HOLD CASH",
                "price_range": "-",
                "condition": "Market merah, WEAK_BUY skip",
                "skip_reason": "Market sentiment RED — WEAK_BUY ditunda",
            }
        elif signal == "BUY":
            return {
                "method": "🟡 Limit order di support",
                "price_range": f"Rp{int(dc_lower):,} - Rp{int(price):,}",
                "condition": "Jika IHSG hijau pagi & harga gap up <2%",
            }
        # STRONG_BUY tetap bisa entry di RED, tapi hati-hati
        return {
            "method": "Limit order diskon",
            "price_range": f"Rp{int(price - 0.5*atr):,} - Rp{int(price):,}",
            "condition": "Market merah — entry minimalis, ketat SL",
        }

    # ── Rule 2: Broker akumulasi masif → Open Entry ──
    if "akumulasi_masif" in broker_detail:
        return {
            "method": "Open Entry / Market Order",
            "price_range": f"Rp{int(price):,} - Rp{int(price * 1.01):,}",
            "condition": "Jika broker akumulasi lanjut di pre-open",
        }

    # ── Rule 3: RSI oversold → tunggu konfirmasi reversal ──
    if rsi < 40:
        return {
            "method": "Tunggu konfirmasi reversal",
            "price_range": f"Rp{int(price):,}",
            "condition": "Entry jika RSI naik >45 (konfirmasi reversal) & candle hijau",
        }

    # ── Rule 4: Screaming volume → entry Open + konfirmasi ──
    if vol_ratio > 2.0:
        return {
            "method": "Entry di Open",
            "price_range": f"Rp{int(price):,} - Rp{int(price * 1.015):,}",
            "condition": "Konfirmasi volume lanjut di 30 menit pertama",
        }

    # ── Rule 5: Price extended >5% dari VWAP → jangan entry ──
    if pct_vwap > 5:
        return {
            "method": "Jangan entry — tunggu pullback",
            "price_range": f"Rp{int(vwap):,} - Rp{int(price):,}",
            "condition": f"Harga {pct_vwap:.1f}% di atas VWAP, tunggu koreksi ke VWAP",
            "skip_reason": f"Harga extended {pct_vwap:.1f}% dari VWAP",
        }

    # ── Rule 6: Weekly BEARISH → hanya di support donchian ──
    if weekly == "BEARISH":
        return {
            "method": "GTC @ support Donchian",
            "price_range": f"Rp{int(dc_lower):,}",
            "condition": f"Hanya entry jika harga di support {int(dc_lower):,}",
        }

    # ── Rule 7: Broker netral & VWAP dekat (0-2%) → Limit di VWAP ──
    if "netral" in broker_detail and 0 <= pct_vwap <= 2:
        vwap_low = int(vwap * 0.995)
        vwap_high = int(vwap * 1.005)
        return {
            "method": f"Limit order di VWAP",
            "price_range": f"Rp{vwap_low:,} - Rp{vwap_high:,}",
            "condition": "Jika IHSG hijau pagi",
        }

    # ── Rule 8: Broker akumulasi (bukan masif) → Limit di harga pasar ──
    if "akumulasi" in broker_detail:
        return {
            "method": "Limit di harga pasaran",
            "price_range": f"Rp{int(price - 0.3*atr):,} - Rp{int(price):,}",
            "condition": "Jika broker akumulasi lanjut",
        }

    # ── Rule 9: Broker distribusi → entry diskon besar ──
    if "distribusi" in broker_detail:
        return {
            "method": "Limit diskon dalam",
            "price_range": f"Rp{int(price - atr):,} - Rp{int(price - 0.5*atr):,}",
            "condition": "Jika distribusi mereda & volume turun",
        }

    # ── DEFAULT ──
    low_price = int(price - 0.5 * atr)
    high_price = int(price)
    if low_price <= 0:
        low_price = int(price * 0.98)

    return {
        "method": "Limit order",
        "price_range": f"Rp{low_price:,} - Rp{high_price:,}",
        "condition": f"Jika IHSG hijau pagi",
    }
