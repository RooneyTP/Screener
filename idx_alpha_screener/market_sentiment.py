"""
market_sentiment.py — Prediksi "Besok Merah?" untuk IHSG
==========================================================
Analisis IHSG pakai data dari fetch_ihsg_cached() + Invezgo broker flow.
Digunakan oleh v7_scan.py untuk memberi konteks market di output Telegram.
"""
import numpy as np
import pandas as pd
import logging
from datetime import datetime

logger = logging.getLogger("market_sentiment")


def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute IHSG technical indicators for sentiment analysis (no shift — we want current)."""
    if df.empty or len(df) < 50:
        return df

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # EMA12/50
    df["ema12"] = close.ewm(span=12, adjust=False).mean()
    df["ema50"] = close.ewm(span=50, adjust=False).mean()

    # RSI 14 — Wilder smoothing (alpha=1/14), konsisten dgn data.py
    # compute_all_indicators (R4: dulu rolling-mean Cutler → nilai beda dgn
    # indikator per-saham). Tanpa shift — sentiment mau nilai SAAT INI.
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta.where(delta < 0, 0.0))
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # Edge case Wilder: avg_loss == 0 (naik semua) → RSI 100, bukan NaN
    rsi = rsi.where(avg_loss != 0, 100.0)
    rsi = rsi.where(avg_gain != 0, 0.0)
    df["rsi"] = rsi

    # ADX 14 — Wilder smoothing penuh (alpha=1/14), konsisten dgn data.py
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    # Wilder SMMA untuk ATR (alpha=1/14), bukan SMA14
    atr_ = tr.ewm(alpha=1/14, adjust=False).mean()
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move
    # Smoothing +DM/-DM pakai Wilder (alpha=1/14), bukan ewm span=14
    plus_di = 100 * (plus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(alpha=1/14, adjust=False).mean() / atr_.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    # DX dirata-rata dengan Wilder smoothing, bukan SMA14
    df["adx"] = dx.ewm(alpha=1/14, adjust=False).mean()

    # Volume 20-day avg
    df["vol_avg20"] = volume.rolling(20).mean()
    df["vol_ratio"] = volume / df["vol_avg20"].replace(0, np.nan)

    # Return 3-day
    df["ret_3d"] = close.pct_change(3)

    # Consecutive red/green days
    df["change"] = close.diff()
    df["is_green"] = (df["change"] > 0).astype(int)
    df["is_red"] = (df["change"] <= 0).astype(int)

    return df


def _count_consecutive(series, val: int) -> int:
    """Count consecutive occurrences of val at the end of series."""
    count = 0
    for v in series[::-1]:
        if v == val:
            count += 1
        else:
            break
    return count


def compute_ihsg_key_levels(df_ihsg: pd.DataFrame) -> dict:
    """
    Hitung level kunci IHSG: support & resistance dari swing high/low terbaru.
    Pakai Donchian 20/50 hari sebagai proxy level psikologis.

    Returns dict: {support: float, resistance: float, current: float, trend: str}
    """
    empty = {"support": 0.0, "resistance": 0.0, "current": 0.0, "trend": "N/A"}
    if df_ihsg is None or df_ihsg.empty or len(df_ihsg) < 30:
        return empty
    try:
        close = df_ihsg["close"]
        high = df_ihsg["high"]
        low = df_ihsg["low"]
        current = float(close.iloc[-1])

        # Support/resistance dari swing terakhir 20 hari (jangan include hari ini penuh)
        recent_high = float(high.iloc[-20:-1].max()) if len(high) >= 21 else float(high.max())
        recent_low = float(low.iloc[-20:-1].min()) if len(low) >= 21 else float(low.min())

        # Trend dari EMA12 vs EMA50 (recompute cepat)
        ema12 = close.ewm(span=12, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        trend = "UP" if ema12 > ema50 else "DOWN"

        return {
            "support": round(recent_low, 0),
            "resistance": round(recent_high, 0),
            "current": round(current, 0),
            "trend": trend,
        }
    except Exception as e:
        logger.debug("Key levels error: %s", e)
        return empty


def predict_market_sentiment(df_ihsg: pd.DataFrame, invezgo_provider=None) -> dict:
    """
    Prediksi arah IHSG besok.

    Parameters
    ----------
    df_ihsg : pd.DataFrame
        IHSG historical data from fetch_ihsg_cached().
        Expected columns: open, high, low, close, volume
    invezgo_provider : InvezgoProvider, optional
        TIDAK DIPAKAI — endpoint broker Invezgo (get_summary_stock) menolak
        kode indeks (422), jadi foreign flow IHSG tidak tersedia.

    Returns
    -------
    dict with keys:
        sentiment : "GREEN" | "YELLOW" | "RED"
        label     : "Aman" | "Waspada" | "Bahaya" | "Netral"
        reason    : str — 1-2 kalimat deskripsi
        details   : list[str] — faktor-faktor yang dianalisis
    """
    # L2: guard None konsisten dengan compute_ihsg_key_levels (L86) — dulu
    # df_ihsg=None → AttributeError di .empty (crash scan).
    if df_ihsg is None or df_ihsg.empty or len(df_ihsg) < 50:
        return {
            "sentiment": "YELLOW",
            "label": "Netral",
            "reason": "⚪ NETRAL: Data IHSG tidak mencukupi",
            "details": ["Data IHSG tidak lengkap untuk analisis"],
        }

    df = _compute_indicators(df_ihsg.copy())
    row = df.iloc[-1]
    details = []

    close = float(row.get("close", np.nan))
    ema12 = row.get("ema12", np.nan)
    ema50 = row.get("ema50", np.nan)

    # ── 1. EMA12/50 trend ──
    trend_up = not pd.isna(ema12) and not pd.isna(ema50) and ema12 > ema50
    trend_down = not pd.isna(ema12) and not pd.isna(ema50) and ema12 < ema50
    if trend_up:
        details.append("IHSG EMA12>EMA50 (uptrend)")
    elif trend_down:
        details.append("IHSG EMA12<EMA50 (downtrend)")
    else:
        details.append("IHSG EMA12≈EMA50 (sideway)")

    # ── 2. ADX strength ──
    adx = row.get("adx", np.nan)
    adx_strong = not pd.isna(adx) and adx > 25
    adx_weak = not pd.isna(adx) and adx < 20
    if adx_strong:
        details.append(f"ADX {adx:.0f} (trend kuat)")
    elif adx_weak:
        details.append(f"ADX {adx:.0f} (ranging)")
    else:
        details.append(f"ADX {adx:.0f}")

    # ── 3. RSI ──
    rsi = row.get("rsi", np.nan)
    rsi_weak = not pd.isna(rsi) and rsi < 40
    rsi_strong = not pd.isna(rsi) and rsi > 60
    if rsi_weak:
        details.append(f"RSI {rsi:.0f} (lemah)")
    elif rsi_strong:
        details.append(f"RSI {rsi:.0f} (kuat)")
    else:
        details.append(f"RSI {rsi:.0f} (netral)")

    # ── 4. Consecutive days ──
    last_5 = df.tail(5)
    red_count = _count_consecutive(last_5["is_red"].values, 1)
    green_count = _count_consecutive(last_5["is_green"].values, 1)
    if red_count >= 3:
        details.append(f"{red_count} hari merah berturut-turut")
    elif green_count >= 3:
        details.append(f"{green_count} hari hijau berturut-turut")
    else:
        details.append(f"{red_count} merah, {green_count} hijau terakhir")

    # ── 5. Volume vs 20d avg ──
    vol_ratio = row.get("vol_ratio", np.nan)
    if not pd.isna(vol_ratio):
        if vol_ratio < 0.7 and red_count >= 2:
            details.append(f"Volume turun ({vol_ratio:.1f}x) — exhaustion")
        elif vol_ratio > 1.5 and green_count >= 2:
            details.append(f"Volume naik ({vol_ratio:.1f}x) — konfirmasi")
        else:
            details.append(f"Volume {vol_ratio:.1f}x avg")

    # ── 6. Foreign flow pada IHSG ──
    # Jujur: endpoint broker Invezgo (get_summary_stock) hanya menerima kode
    # saham (maks 7 karakter). Kode indeks IHSG ("COMPOSITE") ditolak API —
    # diverifikasi langsung ke API Invezgo: 422 "Stock code must be at most
    # 7 characters" (2026-08-07). Jadi data asing IHSG tidak tersedia dari
    # sumber ini; foreign_net dibiarkan 0 (tidak memicu kondisi RED/GREEN).
    foreign_net = 0
    details.append("Data asing IHSG tidak tersedia")

    # ── 7. 3-day return ──
    ret_3d = row.get("ret_3d", np.nan)
    if not pd.isna(ret_3d):
        if ret_3d < -0.02:
            details.append(f"3d return {ret_3d*100:.1f}% (negatif)")
        elif ret_3d > 0.02:
            details.append(f"3d return {ret_3d*100:.1f}% (positif)")
        else:
            details.append(f"3d return {ret_3d*100:.1f}% (flat)")

    # ── CLASSIFICATION ──
    # RED: (A) turun 3+ hari + ADX>20 + RSI<45 + foreign jual
    #      (B) downtrend + ADX kuat + 3d return negatif + volume naik (distribusi)
    red_cond_a = (
        red_count >= 3
        and not pd.isna(adx) and adx > 20
        and not pd.isna(rsi) and rsi < 45
        and foreign_net < -50_000_000_000
    )
    red_cond_b = (
        trend_down
        and adx_strong
        and not pd.isna(ret_3d) and ret_3d < -0.01
        and not pd.isna(vol_ratio) and vol_ratio > 1.2
    )
    red_cond = red_cond_a or red_cond_b

    # GREEN: uptrend + ADX>20 + volume naik
    green_cond = (
        trend_up
        and not pd.isna(adx) and adx > 20
        and not pd.isna(vol_ratio) and vol_ratio > 1.0
    )

    # YELLOW: mixed / 1-2 merah / ADX rendah / RSI mid
    yellow_cond = (
        (1 <= red_count <= 2)
        or adx_weak
        or (not pd.isna(rsi) and 40 <= rsi <= 60)
        or (trend_down and not adx_strong)
    )

    if red_cond:
        sentiment, label = "RED", "Bahaya"
        reason = "🔴 BESOK MERAH: IHSG rawan lanjut turun"
    elif green_cond:
        sentiment, label = "GREEN", "Aman"
        reason = "🟢 AMAN: IHSG bullish"
    elif yellow_cond:
        sentiment, label = "YELLOW", "Waspada"
        reason = "🟡 WASPADA: IHSG rawan koreksi"
    else:
        sentiment, label = "YELLOW", "Netral"
        reason = "⚪ NETRAL: IHSG sideway"

    return {
        "sentiment": sentiment,
        "label": label,
        "reason": reason,
        "details": details,
    }
