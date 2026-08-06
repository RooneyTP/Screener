"""
v7_scan.py — V7 Dual Mode Scanner (Invezgo ONLY)
Data 100% dari Invezgo. Output ke Telegram via cron + formatted.
"""
import sys, os, warnings, yaml, traceback
warnings.filterwarnings('ignore')
ROOT = r'C:\Hermes_Workspace\Screener\idx_alpha_screener'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))  # parent for utils/
import pandas as pd, logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("v7_scan")

# File logging — jejak error walau cron jalan headless
try:
    _log_path = os.path.join(ROOT, "data", "screener.log")
    os.makedirs(os.path.dirname(_log_path), exist_ok=True)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception:
    pass

from data import compute_all_indicators, align_to_market, fetch_ihsg_cached
from regime import detect_market_regime
from scoring import compute_total_score
from data_invezgo import InvezgoProvider
import v7 as v7_engine
from v7_exit import compute_exit, position_sizing

# ── V7 addon modules ──
from market_sentiment import predict_market_sentiment, compute_ihsg_key_levels
from entry_timing import recommend_entry
from telegram_formatter import format_message
from signal_manager import CooldownTracker
from position_tracker import PositionTracker, format_position_alerts
from perf_tracker import dedup_and_log_batch, weekly_stats

# ── Group mapping untuk label konglomerat ──
GROUP_NAMES = {
    "BRPT": "Barito", "DSSA": "Barito", "BUMI": "Barito", "ENRG": "Barito",
    "BNBR": "Bakrie", "VBID": "Bakrie", "ELTY": "Bakrie",
    "INDF": "Salim", "ICBP": "Salim", "KLBF": "Salim", "HMSP": "Salim", "BISI": "Salim",
    "ASII": "Astra", "UNTR": "Astra", "AKRA": "Astra", "CPIN": "Astra", "ISAT": "Astra",
}


def group_of(ticker: str) -> str:
    """Label grup konglomerat untuk ticker."""
    return GROUP_NAMES.get(ticker.upper(), "")


def aggregate_sector_flow(signals: list) -> str:
    """
    Agregasi broker flow per grup (proxy sektor) dari daftar sinyal.
    Sederhana: hitung jumlah sinyal akumulasi vs distribusi per grup.
    """
    from collections import Counter
    groups = Counter()
    for s in signals:
        g = group_of(s.get("tkr", ""))
        if not g:
            continue
        bf = s.get("bf", "")
        if "akumulasi" in bf:
            groups[g] += 1
        elif "distribusi" in bf:
            groups[g] -= 1
    if not groups:
        return ""
    parts = [f"{g}: {'🔵' if v > 0 else '🔴' if v < 0 else '⚪'}{abs(v)}" for g, v in groups.most_common()]
    return "🏭 Grup: " + " | ".join(parts[:5])


def main():
    # Config
    with open(os.path.join(ROOT, "config.yaml"), encoding="utf-8", errors="replace") as f:
        CONFIG = yaml.safe_load(f)

    WATCHLIST = list(dict.fromkeys([
        t for g in CONFIG.get("watchlist", {}).values() if isinstance(g, list) for t in g
    ]))
    # Buang ticker yang di-disable (WR rendah dari backtest)
    disabled = set(CONFIG.get("watchlist", {}).get("disabled", []))
    WATCHLIST = [t for t in WATCHLIST if t not in disabled]
    if disabled:
        logger.info("Watchlist disabled (WR rendah): %s", ", ".join(sorted(disabled)))
    CAPITAL = 20_000_000
    v7_engine.enabled = True

    # ── Init providers & data (with crash protection) ──
    try:
        ip = InvezgoProvider()
    except Exception as e:
        logger.error("Gagal init Invezgo: %s", e)
        print(f"❌ Gagal init Invezgo: {e}")
        sys.exit(1)

    try:
        df_ihsg = fetch_ihsg_cached(period="2y")
    except Exception as e:
        logger.warning("Gagal ambil IHSG: %s", e)
        df_ihsg = pd.DataFrame()

    # ── Market regime — compute ONCE from IHSG ──
    if df_ihsg is not None and not df_ihsg.empty and len(df_ihsg) >= 50:
        df_ihsg_regime = compute_all_indicators(df_ihsg.copy())
        regime, _, _ = detect_market_regime(df_ihsg_regime)
    else:
        regime = "RANGING"
    logger.info("Market regime: %s", regime)

    # ── Cooldown tracker + cleanup ──
    cd_cfg = CONFIG.get("cooldown", {})
    cooldown = CooldownTracker(
        db_path=os.path.join(ROOT, cd_cfg.get("db_path", "data/signal_cooldown.json")),
        cooldown_days=cd_cfg.get("days", 1) if cd_cfg.get("enabled", True) else 0,
    )
    cooldown.clean_old()

    # ── Market sentiment + key levels — sekali, dipakai entry_rec ──
    sentiment = predict_market_sentiment(df_ihsg, ip)
    key_levels = compute_ihsg_key_levels(df_ihsg)
    sentiment["key_levels"] = key_levels

    # ── Position tracker — cek posisi terbuka ──
    position_tracker = PositionTracker()
    swing = []
    intra = []
    logged_signals = []

    # Cek posisi terbuka dulu (harga dari Invezgo)
    def _get_price(tkr):
        try:
            df = ip.get_historical(tkr, period="5d")
            if df is not None and not df.empty:
                return float(df.iloc[-1]["close"])
        except Exception:
            pass
        return 0.0

    position_alerts = position_tracker.check_positions(_get_price)

    # ── Market mode filter: di BEAR/HIGH_VOL, hanya sinyal terkuat ──
    # Backtest: BEAR WR 33%, HIGH_VOL 35.6%, RANGING 38.9%, BULL 51.3%
    mode_cfg = CONFIG.get("market_mode", {})
    mode_enabled = mode_cfg.get("enabled", True)
    if mode_enabled:
        if regime in ("BEAR", "HIGH_VOLATILITY"):
            allowed_signals = {"STRONG_BUY"}
            logger.info("Market mode: %s — hanya STRONG_BUY diizinkan", regime)
        elif regime == "RANGING":
            allowed_signals = {"STRONG_BUY", "BUY"}
            logger.info("Market mode: RANGING — STRONG_BUY/BUY diizinkan")
        else:
            allowed_signals = {"STRONG_BUY", "BUY", "WEAK_BUY"}
    else:
        allowed_signals = {"STRONG_BUY", "BUY", "WEAK_BUY"}

    for tkr in WATCHLIST:
        try:
            # Cooldown check
            if cooldown.is_on_cooldown(tkr):
                logger.debug("Cooldown: %s", tkr)
                continue

            df = ip.get_historical(tkr, period="1y")
            if df.empty or len(df) < 60:
                continue
            df = compute_all_indicators(df)
            df = align_to_market(df, df_ihsg=df_ihsg).dropna()
            if len(df) < 30:
                continue
            row = df.iloc[-1]
            if pd.isna(row.get("rsi")):
                continue

            v4s = compute_total_score(row, regime)
            v7r = v7_engine.compute(tkr, v4s, regime)

            if v7r["signal"] not in allowed_signals:
                continue

            price = float(row["close"])
            atr = float(row.get("atr", 0) or 0)
            atr_pct = (atr / price * 100) if price > 0 else 0
            bf = v7r["factors"].get("broker_detail", "")
            ff = v7r["factors"].get("foreign_detail", "")
            vol_ratio = float(row.get("vol_ratio", 1) or 1)
            weekly = row.get("weekly_trend", "NO_DATA")
            brokers_raw = v7r["factors"].get("brokers", "")

            swing_score = v7r["score"]
            if "akumulasi" in bf and v7r["score"] >= 48:
                swing_score += 5

            # ── Swing filter (independent) ──
            swing_ok = False
            if swing_score >= 50 or ("akumulasi" in bf and v7r["score"] >= 48):
                if not ("distribusi" in bf and v7r["score"] < 55):
                    nn = ("netral" in bf) or ("net_buy" in bf and swing_score < 52)
                    if not (swing_score < 55 and nn):
                        swing_ok = True

            if swing_ok:
                entry_rec = recommend_entry(tkr, price, atr, row, v7r, sentiment)
                ex = compute_exit(price, atr, regime, "swing", weekly)
                sz = position_sizing(CAPITAL, price, swing_score, atr_pct)
                swing.append({
                    "tkr": tkr, "score": swing_score, "price": price,
                    "exit": ex, "sizing": sz,
                    "bf": bf, "ff": ff, "weekly": weekly, "brokers": brokers_raw,
                    "entry_rec": entry_rec, "group": group_of(tkr),
                    "rsi": float(row.get("rsi", 0) or 0),
                    "vol_ratio": vol_ratio,
                })
                logged_signals.append({
                    "ticker": tkr, "mode": "swing", "score": swing_score,
                    "signal": v7r["signal"], "entry_price": price,
                    "sl": ex["stop_loss"], "tp": ex["take_profit"],
                    "lots": sz.get("lots", 0), "cost": sz.get("cost", 0),
                })

            # ── Intraday filter (independent of swing) ──
            intra_ok = v7r["score"] >= 48 and vol_ratio >= 1.0
            if intra_ok:
                ex2 = compute_exit(price, atr, regime, "intraday", weekly)
                sz2 = position_sizing(CAPITAL, price, v7r["score"], atr_pct)
                entry_rec2 = recommend_entry(tkr, price, atr, row, v7r, sentiment)
                intra.append({
                    "tkr": tkr, "score": v7r["score"], "price": price,
                    "exit": ex2, "sizing": sz2, "bf": bf, "ff": ff, "vol": vol_ratio,
                    "entry_rec": entry_rec2, "group": group_of(tkr),
                })
                logged_signals.append({
                    "ticker": tkr, "mode": "intraday", "score": v7r["score"],
                    "signal": v7r["signal"], "entry_price": price,
                    "sl": ex2["stop_loss"], "tp": ex2["take_profit"],
                    "lots": sz2.get("lots", 0), "cost": sz2.get("cost", 0),
                })

            # Record cooldown if ANY signal passed
            if swing_ok or intra_ok:
                cooldown.record(tkr, v7r["signal"], {"score": swing_score})

        except Exception as e:
            logger.debug("Skip %s: %s", tkr, e)
            continue

    swing.sort(key=lambda x: x["score"], reverse=True)
    intra.sort(key=lambda x: x["score"], reverse=True)

    # ── Log performa sinyal ke CSV (dedup persistén: ±1% harga & <14 hari) ──
    perf_csv = os.path.join(ROOT, "data", "perf_tracker_v7.csv")
    dedup_results = dedup_and_log_batch(perf_csv, logged_signals)
    logged = sum(1 for r in dedup_results if r["logged"])
    if logged:
        logger.info("Perf tracker: %d sinyal tercatat", logged)

    # Tandai sinyal lanjutan (duplikat <14 hari) untuk label di Telegram
    cont_map = {
        (r["ticker"], r["mode"]): r
        for r in dedup_results if r["logged"] and not r["fresh"]
    }
    for s in swing:
        r = cont_map.get((s["tkr"], "swing"))
        if r:
            s["continuation"] = r["ref_date"]
    for s in intra:
        r = cont_map.get((s["tkr"], "intraday"))
        if r:
            s["continuation"] = r["ref_date"]

    # ── Sector rotation summary ──
    sector_line = aggregate_sector_flow(swing)

    # ── AI narrative top 3 sinyal swing (E1) — OPSIONAL, tidak pernah memblokir scan ──
    narratives = {}
    try:
        from ai_narrative import generate_narratives
        narratives = generate_narratives(swing[:3], sentiment)
    except Exception as e:
        logger.warning("AI narrative gagal (scan tetap jalan): %s", e)
        narratives = {}

    # ── Format & print ──
    output_message = format_message(swing, intra, sentiment, CAPITAL, narratives=narratives)

    # Tambah posisi alerts + sector line ke output
    extra_parts = []
    if position_alerts:
        extra_parts.append(format_position_alerts(position_alerts))
    if sector_line:
        extra_parts.append(sector_line)
    if extra_parts:
        output_message = output_message + "\n\n" + "\n\n".join(extra_parts)

    print(output_message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("FATAL: %s\n%s", e, traceback.format_exc())
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
