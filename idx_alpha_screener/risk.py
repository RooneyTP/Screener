"""risk.py — Risk Management for IDX Alpha Screener v3
Dynamic SL/TP berbasis ATR + Volatility Regime + Adaptive Trailing
"""

import numpy as np
import pandas as pd
import logging

logger = logging.getLogger("risk")


def position_size(capital: float, price: float, atr: float,
                  max_risk_pct: float = 0.02) -> int:
    """
    Hitung jumlah saham berdasarkan ATR-based risk.
    v3: ATR/price minimal 0.5% (sebelumnya 0.3%).
    """
    if price <= 0 or atr <= 0 or pd.isna(atr) or pd.isna(price):
        logger.debug("position_size: price=%s atr=%s — skip", price, atr)
        return 0

    # Minimal 1% pergerakan rata-rata harian (lebih ketat)
    if atr / price < 0.01:
        logger.debug("position_size: ATR/price ratio terlalu kecil (%s/%s) — skip",
                    round(atr, 2), price)
        return 0

    # Batasi posisi maksimal 10% dari modal di satu saham
    max_nominal = capital * 0.10
    max_shares_by_capital = int(max_nominal / price) if price > 0 else 0

    risk_per_share = atr * 2.0
    if risk_per_share <= 0:
        return 0

    position_risk = capital * max_risk_pct
    raw_size = int(position_risk / risk_per_share)
    lot_size = max(0, (raw_size // 100) * 100)

    if max_shares_by_capital > 0:
        lot_size = min(lot_size, (max_shares_by_capital // 100) * 100)

    if lot_size < 100:
        logger.debug("position_size: %d saham < 1 lot — skip", lot_size)
        return 0

    return lot_size


def calculate_stop_loss(price: float, atr: float,
                        multiplier: float = 2.0,
                        volatility_regime: str = "NORMAL") -> float:
    """
    ATR-based stop loss v3.
    
    v3: Adaptive multiplier based on volatility regime.
    - HIGH_VOL: 3x ATR (wider to avoid whipsaw)
    - NORMAL: 2x ATR
    - LOW: 1.5x ATR (tighter)
    """
    if pd.isna(atr) or atr <= 0 or price <= 0:
        return 0.0
    
    # Adaptive multiplier
    if volatility_regime == "HIGH":
        mult = max(multiplier, 3.0)  # wider stop for high vol
    elif volatility_regime == "LOW":
        mult = min(multiplier, 1.5)  # tighter stop for low vol
    else:
        mult = multiplier
    
    sl = price - (atr * mult)
    return max(sl, price * 0.80)  # max drawdown 20%


def calculate_take_profit(price: float, atr: float,
                          multiplier: float = 3.0,
                          volatility_regime: str = "NORMAL") -> float:
    """
    ATR-based take profit v3.
    
    v3: Adaptive multiplier based on volatility regime.
    - HIGH_VOL: 2x ATR (take profits faster)
    - NORMAL: 3x ATR
    - LOW: 5x ATR (wait for bigger moves)
    """
    if pd.isna(atr) or atr <= 0 or price <= 0:
        return 0.0
    
    if volatility_regime == "HIGH":
        mult = min(multiplier, 2.0)
    elif volatility_regime == "LOW":
        mult = max(multiplier, 5.0)
    else:
        mult = multiplier
    
    return price + (atr * mult)


def calculate_trailing_stop(entry_price: float, current_price: float,
                             atr: float,
                             activated_pct: float = 3.0,
                             trail_atr: float = 2.0) -> float:
    """
    TRAILING STOP — P3 FEATURE
    
    Logic:
    1. Trail hanya aktif setelah harga naik > activated_pct dari entry
    2. Setelah aktif, trailing = highest_price - (atr * trail_atr)
    3. Tidak pernah turun (hanya naik)
    
    Parameters
    ----------
    entry_price : float
        Harga entry.
    current_price : float
        Harga saat ini (latest close).
    atr : float
        ATR value.
    activated_pct : float
        Persen kenaikan untuk aktivasi trailing (default 3%).
    trail_atr : float
        Multiplier ATR untuk trailing distance (default 2x).
    
    Returns
    -------
    float
        Harga stop loss (trailing). 0 jika belum aktif.
    """
    if pd.isna(atr) or atr <= 0 or entry_price <= 0 or current_price <= 0:
        return 0.0
    
    gain_pct = (current_price - entry_price) / entry_price * 100
    
    # Trail hanya aktif setelah gain tertentu
    if gain_pct < activated_pct:
        return 0.0  # not yet activated
    
    # Hitung trailing dari harga tertinggi sejak entry
    # (current_price digunakan sebagai proxy highest)
    trail_distance = atr * trail_atr
    trailing_sl = current_price - trail_distance
    
    return max(trailing_sl, entry_price * 0.95)  # floor: 5% below entry


def calculate_dynamic_tp(price, atr, volatility_regime="NORMAL", trend_strength=50):
    """
    Dynamic TP berdasarkan regime volatilitas dan trend strength.
    
    HIGH_VOL: TP = 1.5x ATR (ambil profit cepat, risiko reversal tinggi)
    NORMAL, trend kuat (>60): TP = 4x ATR
    NORMAL, trend lemah: TP = 2.5x ATR
    LOW_VOL: TP = 5x ATR (sabar, tunggu pergerakan besar)
    """
    if pd.isna(atr) or atr <= 0 or price <= 0:
        return 0.0

    if volatility_regime == "HIGH":
        mult = 1.5
    elif volatility_regime == "LOW":
        mult = 5.0
    else:  # NORMAL
        if trend_strength >= 60:
            mult = 4.0
        elif trend_strength >= 35:
            mult = 3.0
        else:
            mult = 2.5

    return round(price + (atr * mult), 0)


def kelly_fraction(win_prob: float, avg_win: float,
                   avg_loss: float) -> float:
    """
    Kelly Criterion sederhana.
    f* = (p × b - q) / b
    """
    if win_prob <= 0 or avg_loss <= 0:
        return 0.0

    b = avg_win / avg_loss
    q = 1.0 - win_prob
    kelly = (win_prob * b - q) / b
    half_kelly = max(0, kelly / 2.0)
    return min(half_kelly, 0.25)


def max_position_value(capital: float, open_positions: int = 0,
                       max_total_risk: float = 0.06) -> float:
    """Batasi total exposure portofolio. Maks 6% modal dalam risiko total."""
    available_risk = capital * max_total_risk
    used_risk = capital * 0.02 * open_positions
    remaining = max(0, available_risk - used_risk)
    return remaining * 50


def compute_volatility_regime(atr: float, price: float,
                               atr_hist: list = None) -> str:
    """
    Tentukan regime volatilitas dari ATR.
    
    Parameters
    ----------
    atr : float
        ATR saat ini.
    price : float
        Harga saat ini.
    atr_hist : list, optional
        History ATR untuk perbandingan.
        Jika ada, bandingkan ATR saat ini vs median history.
    
    Returns
    -------
    str: "HIGH", "NORMAL", atau "LOW"
    """
    if pd.isna(atr) or price <= 0:
        return "NORMAL"
    
    atr_pct = (atr / price) * 100
    
    if atr_hist and len(atr_hist) > 10:
        median_atr = np.median(atr_hist)
        if atr > median_atr * 1.5:
            return "HIGH"
        elif atr < median_atr * 0.5:
            return "LOW"
        else:
            return "NORMAL"
    
    # Fallback: threshold absolute
    if atr_pct > 4.0:
        return "HIGH"
    elif atr_pct < 0.5:
        return "LOW"
    else:
        return "NORMAL"


# ═══════════════════════════════════════════════════════════════
# EXIT STRATEGY — Kapan KELUAR dari posisi
# ═══════════════════════════════════════════════════════════════
# Ini yang paling penting: sinyal BUY tanpa exit plan = potensi rugi.

def evaluate_exit(entry_price: float, current_price: float, atr: float,
                  entry_date, current_date,
                  max_hold_days: int = 15,
                  flat_exit_days: int = 7,
                  flat_exit_threshold_pct: float = 2.0,
                  hard_stop_pct: float = -15.0,
                  volatility_regime: str = "NORMAL") -> dict:
    """
    Evaluasi apakah posisi harus di-exit.
    Return dict dengan keys: exit (bool), reason (str), detail (str).

    Empat kondisi exit:

    1. HARD STOP (-15% dari entry)
       Proteksi dari gap open pagi hari.
       Harga turun > hard_stop_pct dari entry → exit wajib.

    2. MAX HOLD (15 hari)
       Paksa exit setelah N hari — uang nggak boleh terikat terlalu lama.
       Kalau untung kecil tapi nggak maju2 → force sell.

    3. FLAT EXIT (7 hari tanpa pergerakan >2%)
       Uang diam nggak menghasilkan → opportunity loss.
       Harga nggak bergerak > flat_exit_threshold_pct dari entry.

    4. TRAILING STOP (sudah ada di calculate_trailing_stop)
       Harga naik lalu turun → trailing stop aktif.
       Tidak di-duplikasi di sini — panggil calculate_trailing_stop() dulu.

    Returns
    -------
    dict dengan keys:
        exit: bool — true jika harus exit
        reason: str — kode alasan (HARD_STOP / MAX_HOLD / FLAT_EXIT / NONE)
        detail: str — penjelasan untuk user
        sl_price: float — harga stop loss rekomendasi
    """
    if any(pd.isna(x) or x <= 0 for x in [entry_price, current_price, atr]):
        return {"exit": False, "reason": "NONE", "detail": "Data tidak valid", "sl_price": 0.0}

    ret_pct = (current_price - entry_price) / entry_price * 100

    # ── 1. Hard Stop Check ──────────────────────────────────────────
    # Proteksi dari gap open: kalau harga turun > hard_stop_pct, exit wajib
    if ret_pct <= hard_stop_pct:
        return {
            "exit": True,
            "reason": "HARD_STOP",
            "detail": f"Hard stop terpicu: return {ret_pct:+.1f}% ≤ {hard_stop_pct:.0f}%",
            "sl_price": entry_price * (1 + hard_stop_pct / 100),
        }

    # ── 2. Max Hold Check ──────────────────────────────────────────
    # Force exit setelah N hari — hindari modal terikat terlalu lama
    if entry_date and current_date and max_hold_days > 0:
        try:
            hold_days = (current_date - entry_date).days
            if hold_days >= max_hold_days:
                return {
                    "exit": True,
                    "reason": "MAX_HOLD",
                    "detail": f"Hold {hold_days} hari ≥ maks {max_hold_days} hari (return {ret_pct:+.1f}%)",
                    "sl_price": current_price,  # exit di harga pasar
                }
        except (TypeError, AttributeError):
            pass  # date comparison failed, skip check

    # ── 3. Flat Exit Check ──────────────────────────────────────────
    # Harga stagnan > N hari → opportunity loss, lebih baik pindah
    if flat_exit_days > 0 and flat_exit_threshold_pct > 0:
        if abs(ret_pct) < flat_exit_threshold_pct:
            if entry_date and current_date:
                try:
                    hold_days = (current_date - entry_date).days
                    if hold_days >= flat_exit_days:
                        return {
                            "exit": True,
                            "reason": "FLAT_EXIT",
                            "detail": (f"Flat exit: return {ret_pct:+.1f}% < "
                                       f"{flat_exit_threshold_pct}% selama {hold_days} hari"),
                            "sl_price": current_price,
                        }
                except (TypeError, AttributeError):
                    pass

    return {"exit": False, "reason": "NONE", "detail": "Hold", "sl_price": 0.0}


def evaluate_exits_batch(positions: list, current_prices: dict,
                         atr_values: dict, current_date,
                         config: dict = None) -> list:
    """
    Batch evaluasi exit untuk semua posisi aktif.
    Parameters
    ----------
    positions : list of dict
        [{ticker, sector, entry_price, shares, entry_date}, ...]
    current_prices : dict
        {ticker: current_price}
    atr_values : dict
        {ticker: atr}
    current_date : date
    config : dict
        Dari config.yaml section 'exit_strategy'.
    Returns
    -------
    list of dict dengan keys: ticker, exit, reason, detail, sl_price
    """
    cfg = config or {}
    max_hold = cfg.get("max_hold_days", 15)
    flat_days = cfg.get("flat_exit_days", 7)
    flat_thr = cfg.get("flat_exit_threshold_pct", 2.0)
    hard_stop = cfg.get("hard_stop_pct", -15.0)

    results = []
    for pos in positions:
        tkr = pos["ticker"]
        curr = current_prices.get(tkr, pos["entry_price"])
        atr = atr_values.get(tkr, curr * 0.02)
        result = evaluate_exit(
            entry_price=pos["entry_price"],
            current_price=curr,
            atr=atr,
            entry_date=pos["entry_date"],
            current_date=current_date,
            max_hold_days=max_hold,
            flat_exit_days=flat_days,
            flat_exit_threshold_pct=flat_thr,
            hard_stop_pct=hard_stop,
        )
        result["ticker"] = tkr
        result["current_price"] = curr
        result["shares"] = pos["shares"]
        results.append(result)
    return results


# ═══════════════════════════════════════════════════════════════
#  V4/V5 — Dynamic Position Sizing & Aggressive Trailing Stop
# ═══════════════════════════════════════════════════════════════
# Optimalisasi asimetri risiko: posisi lebih besar saat conviction
# tinggi & volatilitas rendah. Trailing stop gantikan TP fixed.


def dynamic_position_size(capital: float, price: float, atr: float,
                           conviction_score: float = 50.0,
                           max_risk_pct: float = 0.02,
                           base_allocation: float = 0.10) -> int:
    """
    Dynamic Position Sizing — bobot tidak equal weight.
    
    Logic:
      1. Hitung base position (risk-based seperti position_size biasa)
      2. Kalikan dengan conviction multiplier:
         - Score >= 70:  2.0x (full conviction)
         - Score >= 62:  1.5x (strong conviction)  
         - Score >= 55:  1.0x (moderate)
         - Score >= 48:  0.5x (low conviction)
         - Score <  48:  0.0x (skip)
      3. Kalikan dengan vol multiplier:
         - ATR/price < 1.5%:  1.3x (low vol → lebih agresif)
         - ATR/price 1.5-3%:   1.0x (normal)
         - ATR/price > 3%:     0.7x (high vol → lebih hati-hati)
      4. Batasi maksimal 15% dari modal di satu saham
    
    Returns
    -------
    int : jumlah saham dalam lot (kelipatan 100)
    """
    if price <= 0 or atr <= 0 or pd.isna(atr) or pd.isna(price):
        return 0
    
    # Minimal volatilitas
    atr_pct = atr / price
    if atr_pct < 0.005:
        return 0  # terlalu sepi
    
    # ── Base position (risk-based) ──
    risk_per_share = atr * 2.0
    position_risk = capital * max_risk_pct
    base_shares = int(position_risk / risk_per_share) if risk_per_share > 0 else 0
    
    # ── Conviction multiplier ──
    if conviction_score >= 70:
        conv_mult = 2.0
    elif conviction_score >= 62:
        conv_mult = 1.5
    elif conviction_score >= 55:
        conv_mult = 1.0
    elif conviction_score >= 48:
        conv_mult = 0.5
    else:
        return 0  # skip low conviction
    
    # ── Volatility multiplier ──
    if atr_pct < 0.015:
        vol_mult = 1.3
    elif atr_pct < 0.03:
        vol_mult = 1.0
    else:
        vol_mult = 0.7
    
    # ── Final calculation ──
    final_mult = conv_mult * vol_mult
    final_shares = int(base_shares * final_mult)
    
    # Batasi maks 15% modal di satu saham
    max_nominal = capital * 0.15
    max_shares = int(max_nominal / price) if price > 0 else 0
    
    final_shares = min(final_shares, max_shares)
    lot_size = max(0, (final_shares // 100) * 100)
    
    if lot_size < 100:
        return 0
    
    logger.debug(
        "dynamic_size: score=%.1f conv=%.1f vol=%.1f base=%d final=%d lot=%d",
        conviction_score, conv_mult, vol_mult, base_shares, final_shares, lot_size
    )
    
    return lot_size


def aggressive_trailing_stop(entry_price: float, highest_price: float,
                              current_price: float, atr: float,
                              mode: str = "atr",
                              trail_atr: float = 2.5,
                              donchian_lower: float = 0.0) -> float:
    """
    Aggressive Trailing Stop — gantikan take profit fixed.
    
    Tidak ada batas profit maksimal. Biarkan sistem mengejar
    fat-tail distribution dengan trailing stop yang naik terus.
    
    Dua mode:
      1. "atr" (default) — trailing berbasis ATR
         Stop = highest_price - (atr * trail_atr)
         Trail_atr = 2.5 (sedikit lebih longgar dari biasanya)
      
      2. "donchian" — trailing berbasis Donchian Channel lower
         Stop = donchian_lower (lower band 5-hari)
         Lebih ketat, cocok untuk trending market
    
    Parameters
    ----------
    entry_price : float
    highest_price : float — harga tertinggi sejak entry
    current_price : float — harga saat ini
    atr : float
    mode : str — "atr" atau "donchian"
    trail_atr : float — multiplier ATR untuk jarak trailing
    donchian_lower : float — lower band Donchian 5-hari
    
    Returns
    -------
    float : harga stop loss saat ini. 0 jika tidak aktif.
    """
    if any(pd.isna(x) or x <= 0 for x in [entry_price, current_price]):
        return 0.0
    
    gain_pct = (current_price - entry_price) / entry_price * 100
    
    # Trail hanya aktif setelah harga naik minimal (agar cut loss dulu
    # pake hard stop, bukan trailing)
    if gain_pct < 2.0:
        return 0.0  # not yet activated
    
    if mode == "donchian" and donchian_lower > 0:
        trailing_sl = donchian_lower
    else:
        # ATR mode
        if pd.isna(atr) or atr <= 0:
            return 0.0
        trailing_sl = highest_price - (atr * trail_atr)
    
    # Floor: tidak pernah lebih rendah dari entry - 5%
    floor = entry_price * 0.95
    trailing_sl = max(trailing_sl, floor)
    
    # Tidak pernah lebih rendah dari trailing sebelumnya
    # (ini di-handle oleh caller yang track highest_price)
    
    return round(trailing_sl, 0)


def estimate_exit_price(entry_price: float, current_price: float,
                         highest_price: float, atr: float,
                         hold_days: int,
                         conviction_score: float = 50.0,
                         config: dict = None) -> dict:
    """
    Estimasi harga exit berdasarkan trailing stop saat ini.
    
    Untuk report: kasih tau user di harga berapa posisi akan
    ke-trailing stop kalau harga balik sekarang.
    
    Returns
    -------
    dict: {exit_price, exit_type, return_pct, hold_days}
    """
    cfg = config or {}
    mode = cfg.get("trailing_mode", "atr")
    trail_atr = cfg.get("trail_atr", 2.5)
    
    trail_sl = aggressive_trailing_stop(
        entry_price, highest_price, current_price, atr,
        mode=mode, trail_atr=trail_atr
    )
    
    if trail_sl > 0:
        ret = (trail_sl - entry_price) / entry_price * 100
        return {
            "exit_price": trail_sl,
            "exit_type": "TRAILING",
            "return_pct": round(ret, 1),
            "hold_days": hold_days,
        }
    else:
        # Fallback: hard stop
        hard_stop = entry_price * 0.85
        ret = (hard_stop - entry_price) / entry_price * 100
        return {
            "exit_price": hard_stop,
            "exit_type": "HARD_STOP",
            "return_pct": round(ret, 1),
            "hold_days": hold_days,
        }
