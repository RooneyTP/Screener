"""
scoring.py — Shared Scoring Engine & Portfolio Optimization
=============================================================
Merged from scoring_engine.py + scoring-related functions/constants from screener.py.
Single source of truth for scoring weights, sector data, and portfolio math.
"""

import os
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("scoring")

# ── Calibrated win probability map (Task 20) ──
CALIBRATION_MAP = {90: 0.72, 80: 0.65, 70: 0.55, 60: 0.48, 50: 0.42, 40: 0.35, 30: 0.28}


# ═══════════════════════════════════════════════════════════════════════════
# SCORING WEIGHTS & CONSTANTS (from screener.py)
# ═══════════════════════════════════════════════════════════════════════════

AI_AKTIF = True

# ==========================================
# ⚙️ STRATEGY ENGINE CONFIG (BALANCED WEIGHTS v10)
# ==========================================
# REVAMP: Tech 35% -> Fund 25% -> RS 20% -> Sentiment 20%
BOBOT_SKOR = {
    # TIER 1: TECHNICAL INDICATORS (35% weight)
    "EMA_Aligned": 2.0,
    "RSI_Good_Entry": 1.5,
    "MACD_Bullish": 1.5,
    "Volume_Confirm": 2.0,
    "ADX_Strong": 1.5,
    "VCP_Pattern": 1.0,
    "Vol_Anomaly": 1.5,

    # TIER 2: FUNDAMENTAL ANALYSIS (25% weight)
    "PER_Cheap": 2.0,
    "PER_Fair": 1.0,
    "PBV_Strong": 1.5,
    "PBV_Mahal": -1.0,
    "Earnings_Quality": 1.5,

    # TIER 3: RELATIVE STRENGTH (20% weight)
    "RS_Outperform": 1.5,
    "Sector_Leadership": 1.5,
    "Alpha_Leader": 1.5,
    "RS_Top_Decile": 1.0,

    # TIER 4: SENTIMENT & MARKET PSYCHOLOGY (20% weight)
    "News_BULLISH": 1.5,
    "News_BEARISH": -1.5,
    "Sentiment_Strong": 1.0,
    "Foreign_Buy": 1.5,
    "Foreign_Sell": -1.5,

    # MICRO PATTERNS & CONFIRMATION (5% weight)
    "Pullback_EMA21": 1.0,
    "Wyckoff_Absorb": 1.0,
    "Sector_Cold": -1.0,
    "Overbought": -1.5,

    # RISK PENALTIES (Critical Safety Filters)
    "EPS_Minus": -2.0,
    "Delisting_Risk": -3.0,
    "Broksum_ACCUM": 1.5,
    "Broksum_DIST": -1.5,
}


# ─── Daftar Saham per Sektor ─────────────────────────────────────────────────
WATCHLIST_SEKTOR = {
    "Perbankan": [
        "BBCA.JK", "BBRI.JK", "BMRI.JK", "BBNI.JK", "BRIS.JK", "BBTN.JK",
        "BNGA.JK", "BDMN.JK", "NISP.JK", "BTPS.JK", "ARTO.JK", "CFIN.JK",
        "BBYB.JK", "BVIC.JK", "BJTM.JK", "BJBR.JK", "PNBN.JK", "BSIM.JK"
    ],
    "Konglomerat & Investasi": [
        "ASII.JK", "SRTG.JK", "BMTR.JK", "BHIT.JK", "MLPL.JK", "SMMA.JK",
        "ABMM.JK", "UNTR.JK", "TPIA.JK", "LPKR.JK", "MPPA.JK", "BNLI.JK",
        "SCMA.JK", "VIVA.JK", "ADMG.JK"
    ],
    "Teknologi & Telco": [
        "TLKM.JK", "ISAT.JK", "EXCL.JK", "GOTO.JK", "BUKA.JK",
        "BELI.JK", "WIFI.JK", "EMTK.JK", "MLPT.JK", "MTDL.JK", "DMMX.JK",
        "KREN.JK", "AXIO.JK", "GLVA.JK"
    ],
    "Energi & Tambang": [
        "ADRO.JK", "ITMG.JK", "PTBA.JK", "INDY.JK", "HRUM.JK", "BUMI.JK",
        "BRMS.JK", "DEWA.JK", "ENRG.JK", "MEDC.JK", "PGAS.JK", "AKRA.JK",
        "ANTM.JK", "INCO.JK", "TINS.JK", "CUAN.JK", "MBMA.JK", "NCKL.JK",
        "KKGI.JK", "DOID.JK", "ADMR.JK", "RMKE.JK", "TOBA.JK"
    ],
    "Infrastruktur & Konstruksi": [
        "JSMR.JK", "PTPP.JK", "ADHI.JK", "WIKA.JK", "WSKT.JK", "WEGE.JK",
        "PPRE.JK", "TOTL.JK", "ACST.JK", "JKON.JK", "META.JK", "CMNP.JK",
        "LEAD.JK", "RIGS.JK", "TPMA.JK", "SMDR.JK", "BIRD.JK"
    ],
    "Consumer & Retail": [
        "UNVR.JK", "ICBP.JK", "INDF.JK", "MYOR.JK", "GOOD.JK", "ROTI.JK",
        "CAMP.JK", "CLEO.JK", "ADES.JK", "STTP.JK", "SIDO.JK", "KAEF.JK",
        "PEHA.JK", "AMRT.JK", "MIDI.JK", "MAPI.JK", "MAPA.JK", "ACES.JK",
        "ERAA.JK", "RALS.JK", "LPPF.JK", "MPPA.JK", "HOKI.JK", "CPIN.JK", "JPFA.JK", "ENZO.JK"
    ],
    "Properti & Real Estate": [
        "BSDE.JK", "CTRA.JK", "SMRA.JK", "PWON.JK", "ASRI.JK", "DMAS.JK",
        "DUTI.JK", "DILD.JK", "PPRO.JK", "BKSL.JK", "GWSA.JK", "MKPI.JK",
        "LPCK.JK", "KIJA.JK", "SSIA.JK"
    ],
    "Kesehatan": [
        "KLBF.JK", "MIKA.JK", "HEAL.JK", "SILO.JK", "PRDA.JK", "DGNS.JK",
        "BMHS.JK", "IRRA.JK", "TSPC.JK", "SAME.JK"
    ],
    "Industri Dasar & Logam": [
        "SMGR.JK", "INTP.JK", "SMBR.JK", "SMCB.JK", "KRAS.JK", "ISSP.JK",
        "BAJA.JK", "NIKL.JK", "ALKA.JK", "BRNA.JK", "TOTO.JK"
    ],
    "Transportasi & Logistik": [
        "ASSA.JK", "BIRD.JK", "GIAA.JK", "TMAS.JK", "SMDR.JK", "NELY.JK",
        "HAIS.JK", "PANI.JK", "BPTR.JK"
    ],
    "Agrikultur": [
        "AALI.JK", "LSIP.JK", "SIMP.JK", "BWPT.JK", "TAPG.JK", "DSNG.JK",
        "TBLA.JK", "SSMS.JK", "ANJT.JK"
    ],
    "Pantauan Khusus (High Volatility)": [
        "ALII.JK", "PMUI.JK", "AREA.JK", "STRK.JK", "WIDI.JK", "AWAN.JK",
        "HUMI.JK", "GTRA.JK", "MENN.JK"
    ],
}

SEMUA_TICKER = [t for sektor in WATCHLIST_SEKTOR.values() for t in sektor]
PETA_SEKTOR = {t: s for s, tlist in WATCHLIST_SEKTOR.items() for t in tlist}

# OPTIMIZATION: Initialize SEKTOR_MOMENTUM as empty dict
SEKTOR_MOMENTUM = {}
_SEKTOR_MOMENTUM_LOADED = False


# ═══════════════════════════════════════════════════════════════════════════
# SCORING ENGINE (from scoring_engine.py)
# ═══════════════════════════════════════════════════════════════════════════

def _normalize_score(score, typical_max=65.0):
    """Normalize component score to 0-100 range based on typical max achievable."""
    return min(100, max(0, (score / typical_max) * 100))


def get_adaptive_weights(adx_val):
    """Task 15: Adaptive regime-based scoring weights."""
    if adx_val > 25:
        return 0.65, 0.05, 0.20, 0.10   # trending
    elif adx_val > 20:
        return 0.50, 0.20, 0.20, 0.10   # transition
    else:
        return 0.35, 0.30, 0.25, 0.10   # ranging


def compute_confidence(tech_score, fund_score, rs_score, sent_score,
                       adx_val, ihsg_change, ihsg_trend,
                       weekly_bullish, monthly_bullish,
                       pct_above_ema50=50.0):
    """
    Compute confidence & skor with v11 adaptive weights + score normalization.

    Returns:
        (confidence, skor, c_thresh_buy)
    """
    # Normalize component scores to 0-100 range
    n_tech = _normalize_score(tech_score, 65)
    n_fund = _normalize_score(fund_score, 50)
    n_rs = _normalize_score(rs_score, 50)
    n_sent = _normalize_score(sent_score, 30)

    w_tech, w_fund, w_rs, w_sent = get_adaptive_weights(adx_val)
    confidence = (n_tech * w_tech + max(0, n_fund) * w_fund +
                  n_rs * w_rs + max(0, n_sent) * w_sent)
    confidence = min(100, max(0, confidence))

    # IHSG penalty (softer -- only penalize when IHSG is actually dropping)
    if isinstance(ihsg_change, (int, float)) and ihsg_change < -1.0:
        confidence -= 8
    elif isinstance(ihsg_change, (int, float)) and ihsg_change < -0.3:
        confidence -= 3

    # Multi-timeframe: bonus for confluence, mild penalty for divergence
    if weekly_bullish and monthly_bullish:
        confidence += 5
    elif not weekly_bullish and not monthly_bullish:
        confidence -= 3
    elif not weekly_bullish:
        confidence -= 1

    confidence = min(100, max(5, confidence))
    skor = round(confidence / 100.0 * 15, 1)

    # Task 16: Dynamic breadth threshold
    breadth_tightness = 1.0 + max(0, (50 - pct_above_ema50)) / 100
    c_thresh_buy = min(90, int((55 if ihsg_trend == "UP" else 65) * breadth_tightness))

    return confidence, skor, c_thresh_buy


def get_calibrated_win_prob(confidence):
    """Task 20: Calibrated win probability from bucket map."""
    cal_key = min(CALIBRATION_MAP.keys(), key=lambda k: abs(k - confidence))
    ai_win_prob = round(CALIBRATION_MAP[cal_key] * 100, 0)
    ai_verdict = ("ULTRA BUY" if ai_win_prob >= 70 else
                  "BUY" if ai_win_prob >= 50 else
                  "WEAK" if ai_win_prob >= 35 else "HINDARI")
    return ai_win_prob, ai_verdict


def get_signal(confidence, skor, c_thresh_buy, rrr, weekly_bullish, ihsg_trend):
    """Determine signal from confidence + thresholds."""
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
# ADDITIONAL FUNCTIONS (from screener.py)
# ═══════════════════════════════════════════════════════════════════════════

def hitung_kelly_sizing(ai_win_prob_percent: float, harga_saham: float, modal_trading: float = 10000000.0) -> str:
    """
    Menghitung porsi beli menggunakan Half-Kelly Criterion.
    Asumsi Risk/Reward Ratio (b) adalah 2.0
    """
    p = (ai_win_prob_percent) / 100.0
    q = 1.0 - p
    b = 2.0

    kelly_fraction = p - (q / b)

    if kelly_fraction <= 0:
        return "0 Lot (Risiko Terlalu Tinggi)"

    safe_kelly = kelly_fraction / 2.0
    safe_kelly = min(safe_kelly, 0.25)

    alokasi_dana = modal_trading * safe_kelly
    jumlah_lot = int((alokasi_dana / harga_saham) / 100)

    return f"{safe_kelly*100:.1f}% Modal (Beli ±{max(1, jumlah_lot)} Lot)"


# ─── ENSEMBLE PREDICTOR (v5.0) ────────────────────────────────────────────────
_ensemble_cache = None
_ensemble_threshold = 0.50


def _predict_ensemble(fitur_14: list) -> float:
    """
    Prediksi menggunakan ensemble model (XGBoost+RF+HGB) dari latih_ai.py v5.0.
    Returns win probability 0-100, or -1 if ensemble not available.
    """
    global _ensemble_cache, _ensemble_threshold
    try:
        if _ensemble_cache is None:
            import joblib as _jl
            if not os.path.exists("ensemble_model.pkl"):
                return -1.0
            bundle = _jl.load("ensemble_model.pkl")
            _ensemble_cache = bundle["ensemble"]
            _ensemble_threshold = bundle.get("threshold", 0.50)
            logger.info("[ENSEMBLE] Loaded v%s (threshold=%.3f)",
                       bundle.get("version", "?"), _ensemble_threshold)

        clean = np.nan_to_num(np.array(fitur_14, dtype=float).reshape(1, -1),
                              nan=0.0, posinf=0.0, neginf=0.0)
        proba = _ensemble_cache.predict_proba(clean)[0]
        win_prob = proba[1] * 100 if len(proba) > 1 else proba[0] * 100
        return round(float(win_prob), 2)
    except Exception as e:
        logger.debug("[ENSEMBLE] %s", e)
        return -1.0


# ── Sector momentum ──────────────────────────────────────────────────────
def _download_batch(tickers_batch: list, period: str = "5d") -> pd.DataFrame:
    """Download batch kecil dengan timeout via concurrent.futures agar tidak hang."""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    def _do_download():
        return yf.download(tickers_batch, period=period, progress=False, threads=False)

    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_do_download)
        try:
            return future.result(timeout=30)
        except (FuturesTimeout, Exception):
            return pd.DataFrame()


def compute_sector_momentum():
    """Lazy-load sector momentum (called once at start of jalankan_screener)"""
    global SEKTOR_MOMENTUM, _SEKTOR_MOMENTUM_LOADED

    if _SEKTOR_MOMENTUM_LOADED:
        return

    logger.info("Memindai Arus Uang (Rotasi Sektor)...")

    try:
        BATCH_SIZE = 25
        data_sektor_all: dict[str, pd.Series] = {}

        for sektor, daftar_saham in WATCHLIST_SEKTOR.items():
            if sektor == "Pantauan Khusus (High Volatility)":
                continue
            tickers_full = [t if t.endswith(".JK") else f"{t}.JK" for t in daftar_saham]

            for i in range(0, len(tickers_full), BATCH_SIZE):
                batch = tickers_full[i: i + BATCH_SIZE]
                try:
                    raw = _download_batch(batch)
                    if raw.empty:
                        continue

                    if isinstance(raw.columns, pd.MultiIndex):
                        close_df = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
                    else:
                        close_df = raw[["Close"]].rename(columns={"Close": batch[0]}) if "Close" in raw.columns else pd.DataFrame()

                    for tkr in batch:
                        if tkr in close_df.columns:
                            series = close_df[tkr].dropna()
                            if len(series) >= 2:
                                data_sektor_all[tkr] = series
                except Exception:
                    continue

        for sektor, daftar_saham in WATCHLIST_SEKTOR.items():
            if sektor == "Pantauan Khusus (High Volatility)":
                continue
            ret_sektor = []
            for tkr in daftar_saham:
                full_t = tkr if tkr.endswith(".JK") else f"{tkr}.JK"
                series = data_sektor_all.get(full_t)
                if series is not None and len(series) >= 2:
                    try:
                        pct_change = (series.iloc[-1] - series.iloc[0]) / series.iloc[0] * 100
                        ret_sektor.append(float(pct_change))
                    except Exception:
                        pass
            SEKTOR_MOMENTUM[sektor] = sum(ret_sektor) / len(ret_sektor) if ret_sektor else 0.0

        logger.info("Rotasi sektor selesai (%d sektor dipindai)", len(SEKTOR_MOMENTUM))
        _SEKTOR_MOMENTUM_LOADED = True
    except Exception as e:
        logger.warning("Rotasi sektor gagal: %s -- lanjut tanpa data momentum sektor.", e)
        SEKTOR_MOMENTUM = {}
        _SEKTOR_MOMENTUM_LOADED = True


# ── Backtesting ──────────────────────────────────────────────────────────
def backtest_signals(df: pd.DataFrame, lookback_periods: int = 252) -> dict:
    """
    v4.0: Real event-driven backtest using backtest.py (replaces heuristic formula).
    Falls back to conservative estimate if backtest module is unavailable.
    """
    if df.empty:
        return {}

    # Try real backtest first
    try:
        from backtest import backtest as real_backtest, walk_forward_optimize
    except ImportError:
        real_backtest = None
        walk_forward_optimize = None

    if real_backtest is not None:
        try:
            win_rate, sharpe = real_backtest(df, pd.DataFrame())
            return {
                "total_signals": len(df[df["Sinyal"].isin(["BUY", "STRONG_BUY", "ULTRA_BUY"])]),
                "win_rate": round(win_rate, 4),
                "sharpe_ratio": round(sharpe, 2),
                "method": "event-driven (backtest.py)",
            }
        except Exception as e:
            logger.warning("[backtest] Real backtest failed: %s -- using conservative fallback", e)

    buy_signals = df[df["Sinyal"].isin(["BUY", "STRONG_BUY", "ULTRA_BUY"])]
    total = len(buy_signals)
    if total == 0:
        return {"total_signals": 0, "win_rate": 0.0, "sharpe_ratio": 0.0, "method": "fallback"}

    return {
        "total_signals": total,
        "win_rate": 0.0,
        "sharpe_ratio": 0.0,
        "method": "fallback (run backtest.py with real price data)",
    }


# ── Portfolio Optimization ──────────────────────────────────────────────
def build_covariance_matrix(candidates: pd.DataFrame) -> np.ndarray:
    from core.scraper import fetch_price_data

    returns_list = []
    tickers = []

    for ticker in candidates["Ticker"].tolist():
        ticker_full = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
        data = fetch_price_data(ticker_full, period="1y", interval="1d")
        if data.empty or "Close" not in data.columns:
            continue

        close = data["Close"].squeeze()
        pct_returns = close.pct_change().dropna()
        if len(pct_returns) >= 20:
            returns_list.append(pct_returns.iloc[-60:])
            tickers.append(ticker)

    if len(returns_list) < len(candidates) or len(returns_list) < 2:
        return np.eye(len(candidates)) * 0.2

    returns_df = pd.concat(returns_list, axis=1, join="inner")
    returns_df.columns = tickers[: returns_df.shape[1]]
    cov_matrix = returns_df.cov().fillna(0).values

    if cov_matrix.shape[0] != len(candidates):
        cov_matrix = np.eye(len(candidates)) * 0.2

    return cov_matrix


def optimize_portfolio(df: pd.DataFrame, risk_free_rate: float = 0.05) -> dict:
    try:
        from scipy.optimize import minimize
        OPTIMIZATION_AVAILABLE = True
    except ImportError:
        OPTIMIZATION_AVAILABLE = False

    if not OPTIMIZATION_AVAILABLE or df.empty:
        return {"error": "Optimization not available or no data"}

    candidates = df[(df["MM_Activity"] == "ACCUMULATION") & (df["MM_Confidence"] >= 75)].head(10)
    if len(candidates) < 3:
        return {"error": "Not enough high-confidence accumulation signals"}

    expected_returns = []
    for _, row in candidates.iterrows():
        exp_return = (row["RRR"] / 100) * (row["MM_Confidence"] / 100)
        expected_returns.append(exp_return)

    expected_returns = np.array(expected_returns)
    n_assets = len(candidates)
    cov_matrix = build_covariance_matrix(candidates)

    if cov_matrix.shape != (n_assets, n_assets):
        cov_matrix = np.eye(n_assets) * 0.2

    constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
    bounds = tuple((0, 1) for _ in range(n_assets))

    def neg_sharpe_ratio(weights):
        portfolio_return = np.dot(weights, expected_returns)
        portfolio_volatility = np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))
        if portfolio_volatility == 0:
            return 0.0
        sharpe = (portfolio_return - risk_free_rate) / portfolio_volatility
        return -sharpe

    initial_weights = np.ones(n_assets) / n_assets
    result = minimize(neg_sharpe_ratio, initial_weights, method='SLSQP', bounds=bounds, constraints=constraints)

    if result.success:
        optimal_weights = result.x
        tickers = candidates["Ticker"].tolist()
        return {
            "optimal_weights": dict(zip(tickers, optimal_weights)),
            "expected_portfolio_return": np.dot(optimal_weights, expected_returns),
            "portfolio_volatility": np.sqrt(np.dot(optimal_weights.T, np.dot(cov_matrix, optimal_weights))),
            "sharpe_ratio": -result.fun
        }
    else:
        return {"error": "Optimization failed"}


# ── Position Sizing ─────────────────────────────────────────────────────
def position_size_calc(account_equity: float, risk_pct: float, entry: float, stop_loss: float) -> dict[str, float]:
    risk_amount = account_equity * (risk_pct / 100)
    points_at_risk = abs(entry - stop_loss)

    if points_at_risk <= 0 or entry <= 0:
        return {"shares": 0, "position_size": 0, "risk_amount": 0, "risk_per_share": 0}

    shares = int(risk_amount / points_at_risk)
    position_value = shares * entry

    return {
        "shares": shares,
        "position_size": int(position_value),
        "risk_amount": int(risk_amount),
        "risk_per_share": float(points_at_risk),
    }
