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

    # RSI 14
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # ADX 14
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_ = tr.rolling(14).mean()
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = ((up_move > down_move) & (up_move > 0)).astype(float) * up_move
    minus_dm = ((down_move > up_move) & (down_move > 0)).astype(float) * down_move
    plus_di = 100 * (plus_dm.ewm(span=14, adjust=False).mean() / atr_.replace(0, np.nan))
    minus_di = 100 * (minus_dm.ewm(span=14, adjust=False).mean() / atr_.replace(0, np.nan))
    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    df["adx"] = dx.rolling(14).mean()

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


def predict_market_sentiment(df_ihsg: pd.DataFrame, invezgo_provider=None) -> dict:
    """
    Prediksi arah IHSG besok.

    Parameters
    ----------
    df_ihsg : pd.DataFrame
        IHSG historical data from fetch_ihsg_cached().
        Expected columns: open, high, low, close, volume
    invezgo_provider : InvezgoProvider, optional
        Untuk ambil data foreign flow IHSG via get_broker_summary()

    Returns
    -------
    dict with keys:
        sentiment : "GREEN" | "YELLOW" | "RED"
        label     : "Aman" | "Waspada" | "Bahaya" | "Netral"
        reason    : str — 1-2 kalimat deskripsi
        details   : list[str] — faktor-faktor yang dianalisis
    """
    if df_ihsg.empty or len(df_ihsg) < 50:
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

    # ── 6. Foreign flow on IHSG ──
    foreign_net = 0
    if invezgo_provider is not None:
        try:
            summary = invezgo_provider.get_broker_summary("IHSG", days=3)
            if summary and isinstance(summary, list) and len(summary) > 0:
                foreign_codes = ["AG", "RG", "DB", "GS", "ML", "CS", "UBS"]
                for item in summary:
                    code = item.get("code", "")
                    if code in foreign_codes:
                        buy = int(item.get("buy_value", 0))
                        sell = int(item.get("sell_value", 0))
                        foreign_net += (buy - sell)

                if foreign_net > 50_000_000_000:
                    details.append(f"Asing beli Rp{foreign_net/1e9:.0f}B")
                elif foreign_net < -50_000_000_000:
                    details.append(f"Asing jual Rp{abs(foreign_net)/1e9:.0f}B")
                else:
                    details.append(f"Asing netral ({foreign_net/1e9:+.1f}B)")
            else:
                details.append("Data asing IHSG N/A")
        except Exception as e:
            logger.debug("Gagal ambil foreign flow IHSG: %s", e)
            details.append("Foreign flow IHSG error")
    else:
        details.append("Foreign flow IHSG N/A")

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
    #      (B) downtrend + ADX kuat + 3d return negatif + volume turun
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
