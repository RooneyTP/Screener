"""
v7_scan.py — V7 Dual Mode Scanner (Invezgo ONLY)
Data 100% dari Invezgo. Output ke Telegram via cron + formatted.
"""
import sys, os, warnings, yaml
warnings.filterwarnings('ignore')
ROOT = r'C:\Hermes_Workspace\Screener\idx_alpha_screener'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(ROOT))  # parent for utils/
import pandas as pd, logging
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("v7_scan")

from data import compute_all_indicators, align_to_market, fetch_ihsg_cached
from regime import detect_market_regime
from scoring import compute_total_score
from data_invezgo import InvezgoProvider
import v7 as v7_engine
from v7_exit import compute_exit, position_sizing

# ── V7 addon modules ──
from market_sentiment import predict_market_sentiment
from entry_timing import recommend_entry
from telegram_formatter import format_message

# ── Signal manager (cooldown only) ──
from signal_manager import CooldownTracker


def main():
    # Config
    with open(os.path.join(ROOT, "config.yaml")) as f:
        CONFIG = yaml.safe_load(f)

    WATCHLIST = list(dict.fromkeys([
        t for g in CONFIG.get("watchlist", {}).values() if isinstance(g, list) for t in g
    ]))
    CAPITAL = 20_000_000
    v7_engine.enabled = True

    # ── Init providers & data (with crash protection) ──
    try:
        ip = InvezgoProvider()
    except Exception as e:
        logger.error("Gagal init Invezgo: %s", e)
        print(f"❌ Gagal init Invezgo: {e}")
        return

    try:
        df_ihsg = fetch_ihsg_cached(period="2y")
    except Exception as e:
        logger.warning("Gagal ambil IHSG: %s", e)
        df_ihsg = pd.DataFrame()

    # ── Market regime — compute ONCE from IHSG ──
    if df_ihsg is not None and not df_ihsg.empty and len(df_ihsg) >= 50:
        df_ihsg_regime = compute_all_indicators(df_ihsg.copy())
        regime, trend_score, adx_val = detect_market_regime(df_ihsg_regime)
    else:
        regime = "RANGING"
    logger.info("Market regime: %s", regime)

    # ── Cooldown tracker ──
    cd_cfg = CONFIG.get("cooldown", {})
    cooldown = CooldownTracker(
        db_path=os.path.join(ROOT, cd_cfg.get("db_path", "data/signal_cooldown.json")),
        cooldown_days=cd_cfg.get("days", 1),
    )

    # ── Market sentiment — dihitung sekali, dipakai entry_rec ──
    sentiment = predict_market_sentiment(df_ihsg, ip)

    swing = []
    intra = []

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

            if v7r["signal"] not in ("STRONG_BUY", "BUY", "WEAK_BUY"):
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
                    "entry_rec": entry_rec,
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
                    "entry_rec": entry_rec2,
                })

            # Record cooldown if ANY signal passed
            if swing_ok or intra_ok:
                cooldown.record(tkr, v7r["signal"], {"score": swing_score})

        except Exception as e:
            logger.debug("Skip %s: %s", tkr, e)
            continue

    swing.sort(key=lambda x: x["score"], reverse=True)
    intra.sort(key=lambda x: x["score"], reverse=True)

    # ── Format & print ──
    output_message = format_message(swing, intra, sentiment, CAPITAL)
    print(output_message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("FATAL: %s", e)
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
