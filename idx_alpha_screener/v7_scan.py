"""
v7_scan.py — V7 Dual Mode Scanner (Invezgo ONLY)
Data 100% dari Invezgo. Output ke Telegram via cron + formatted.
"""
import sys, os, warnings, yaml, traceback, math
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
from perf_tracker import dedup_and_log_batch

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


def _update_logged_sizing(logged_signals: list, ticker: str, mode: str,
                          lots: int, cost: int) -> None:
    """Sinkronkan lot/cost hasil guard ke daftar sinyal yang akan di-log ke perf CSV.

    logged_signals punya satu baris per (ticker, mode) — sama dengan sinyal di
    swing/intra, jadi update in-place supaya CSV konsisten dengan rekomendasi.
    """
    for lg in logged_signals:
        if lg.get("ticker") == ticker and lg.get("mode") == mode:
            lg["lots"] = lots
            lg["cost"] = cost
            return


def enforce_group_concentration_guard(swing: list, intra: list,
                                      logged_signals: list,
                                      capital: float,
                                      max_pct: float = 40.0) -> list:
    """C2 — Guard konsentrasi grup konglomerat (grup = proxy sektor).

    Dipanggil SETELAH sinyal swing+intraday terkumpul dan sizing dihitung.
    Total alokasi (cost) per grup konglomerat dihitung dari SEMUA sinyal
    (swing + intraday). Jika total sebuah grup melebihi max_pct% modal
    (default 40% — config portfolio.max_sector_exposure_pct), lot sinyal
    grup itu diturunkan secara proporsional (minimal 1 lot) dan cost
    diperbarui di signal dict + logged_signals agar pesan Telegram dan
    perf CSV konsisten.

    Pendekatan: TURUNKAN LOT (bukan sekadar menandai) karena position_sizing
    hanya membatasi 15%/posisi — tanpa guard agregat, 3 sinyal grup sama
    (mis. BRPT/BUMI/DSSA Barito) bisa menumpuk >40% modal di grup yang
    korelasinya tinggi. Penurunan proporsional aman: (1) hanya aktif saat
    >max_pct, (2) floor 1 lot, (3) tidak mengubah ticker/harga → dedup &
    cooldown tidak terpengaruh, (4) saat konsentrasi normal fungsi ini
    no-op total (perilaku lama TIDAK berubah).

    Return daftar baris peringatan (kosong jika semua grup normal).
    """
    warnings = []
    if capital <= 0 or not (swing or intra):
        return warnings
    limit = capital * max_pct / 100.0
    all_signals = [("swing", s) for s in swing] + [("intraday", s) for s in intra]

    # ── Total alokasi per grup ──
    group_cost = {}
    for _mode, s in all_signals:
        g = group_of(s.get("tkr", ""))
        if not g:
            continue
        cost = float((s.get("sizing") or {}).get("cost", 0) or 0)
        group_cost[g] = group_cost.get(g, 0.0) + cost

    for g, total in sorted(group_cost.items()):
        pct = total / capital * 100.0
        if pct <= max_pct:
            continue  # normal — no-op
        factor = limit / total  # < 1 → skala proporsional
        details = []
        for mode, s in all_signals:
            if group_of(s.get("tkr", "")) != g:
                continue
            sz = s.get("sizing") or {}
            old_lots = int(sz.get("lots", 0) or 0)
            if old_lots <= 0:
                continue
            new_lots = max(1, int(old_lots * factor))
            if new_lots >= old_lots:
                continue
            price = float(s.get("price", 0) or 0)
            if price <= 0:
                continue
            new_cost = int(new_lots * price * 100)
            sz["lots"] = new_lots
            sz["cost"] = new_cost
            sz["pct_modal"] = round(new_cost / capital * 100, 1)
            # L3: risk_amount ikut di-scale proporsional (5% dari cost baru) —
            # sebelumnya tidak diupdate → risiko di ringkasan Telegram overstate
            sz["risk_amount"] = int(new_cost * 0.05)
            _update_logged_sizing(logged_signals, s.get("tkr", ""), mode,
                                  new_lots, new_cost)
            # L1: label mode di detail — ticker yang muncul di swing & intraday
            # sebelumnya tampil dobel tanpa pembeda (mis. "BRPT 15→13 lot")
            details.append(f"{s.get('tkr')}({mode}) {old_lots}→{new_lots} lot")
        new_total = sum(
            float((s.get("sizing") or {}).get("cost", 0) or 0)
            for _m, s in all_signals if group_of(s.get("tkr", "")) == g
        )
        new_pct = new_total / capital * 100.0
        if details:
            warnings.append(
                f"⚠️ KONSENTRASI: Grup {g} {pct:.0f}% > {max_pct:.0f}% — "
                f"lot dikurangi ({', '.join(details)}) → {new_pct:.0f}%"
            )
        else:
            warnings.append(
                f"⚠️ KONSENTRASI: Grup {g} {pct:.0f}% > {max_pct:.0f}% — "
                f"hati-hati (lot sudah minimal, tidak bisa dikurangi)"
            )
    return warnings


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
    # M3: enabled dibaca dari config.yaml (v7.enabled), bukan hardcode True
    v7_engine.enabled = bool(CONFIG.get("v7", {}).get("enabled", True))
    # F1: override threshold/bobot V7 dari config.yaml (kosong = default hardcode engine)
    v7_engine.configure(CONFIG.get("v7", {}))

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
    # H4: IHSG gagal/kosong → JANGAN diam-diam jadi 0 sinyal (align_to_market
    # mengisi idx_close NaN → dropna() membuang SEMUA baris). Scan tetap lanjut
    # tanpa align market; kolom idx diisi 0.0 (scoring punya fallback .get).
    # N5: IHSG non-kosong tapi <21 baris juga masuk jalur ini — pct_change(20)
    # (data.py:463) butuh 21 baris; <21 → idx_ret_20d NaN → dropna() → 0 sinyal.
    if df_ihsg is None or df_ihsg.empty or len(df_ihsg) < 21:
        logger.warning(
            "IHSG kosong/tidak tersedia — scan LANJUT tanpa align market "
            "(idx_close=0.0, sinyal tetap diproses; scoring pakai fallback)")
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
                logger.warning("Cooldown: %s", tkr)  # M1: level warning (debug tidak tampil di level WARNING)
                continue

            df = ip.get_historical(tkr, period="1y")
            if df.empty or len(df) < 60:
                logger.warning("Skip %s: data harga kosong/<60 baris", tkr)  # M1
                continue
            df = compute_all_indicators(df)
            if df_ihsg is not None and not df_ihsg.empty:
                df = align_to_market(df, df_ihsg=df_ihsg).dropna()
            else:
                # H4: IHSG tidak tersedia → SKIP align (hindari idx_close NaN →
                # dropna() → 0 sinyal diam-diam). Kolom idx diisi netral 0.0.
                df["idx_close"] = 0.0
                df["idx_ret_20d"] = 0.0
                df["idx_volatility"] = 0.0
            if len(df) < 30:
                logger.warning("Skip %s: baris tersisa <30 setelah align", tkr)  # M1
                continue
            row = df.iloc[-1]
            if pd.isna(row.get("rsi")):
                logger.warning("Skip %s: RSI NaN di baris terakhir", tkr)  # M1
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
            earn = v7r["factors"].get("earnings_detail", "")
            vol_ratio = float(row.get("vol_ratio", 1) or 1)
            # N4: vol_ratio NaN lolos guard lama (`nan or 1` → nan truthy) →
            # `vol_ratio >= 1.0` selalu False → sinyal intraday hilang diam-diam.
            # NaN/inf → default 1.0 + jejak debug.
            if not math.isfinite(vol_ratio):
                logger.debug("vol_ratio tidak valid (%s) → default 1.0", vol_ratio)
                vol_ratio = 1.0
            weekly = row.get("weekly_trend", "NO_DATA")
            brokers_raw = v7r["factors"].get("brokers", "")

            swing_score = v7r["score"]
            if "akumulasi" in bf and v7r["score"] >= 48:
                swing_score += 5
            swing_score = min(100, swing_score)  # L6: bonus +5 tidak boleh >100

            # ── Swing filter (independent) ──
            swing_ok = False
            if swing_score >= 50 or ("akumulasi" in bf and v7r["score"] >= 48):
                if not ("distribusi" in bf and v7r["score"] < 55):
                    nn = ("netral" in bf) or ("net_buy" in bf and swing_score < 52)
                    if not (swing_score < 55 and nn):
                        swing_ok = True

            # ── Intraday filter (independent of swing) ──
            intra_ok = v7r["score"] >= 48 and vol_ratio >= 1.0

            # L12: recommend_entry dipanggil SEKALI per ticker (sebelumnya 2x
            # untuk ticker yang lolos swing+intraday — hasilnya identik).
            entry_rec = None
            if swing_ok or intra_ok:
                entry_rec = recommend_entry(tkr, price, atr, row, v7r, sentiment)

            if swing_ok:
                ex = compute_exit(price, atr, regime, "swing", weekly)
                sz = position_sizing(CAPITAL, price, swing_score, atr_pct)
                swing.append({
                    "tkr": tkr, "score": swing_score, "price": price,
                    "exit": ex, "sizing": sz,
                    "bf": bf, "ff": ff, "earn": earn, "weekly": weekly, "brokers": brokers_raw,
                    "entry_rec": entry_rec, "group": group_of(tkr),
                    "rsi": float(row.get("rsi", 0) or 0),
                    "vol_ratio": vol_ratio,
                })
                logged_signals.append({
                    "ticker": tkr, "mode": "swing", "score": swing_score,
                    "signal": v7r["signal"], "entry_price": price,
                    "sl": ex["stop_loss"], "tp": ex["take_profit"],
                    "lots": sz.get("lots", 0), "cost": sz.get("cost", 0),
                    "regime": regime,
                })

            if intra_ok:
                ex2 = compute_exit(price, atr, regime, "intraday", weekly)
                sz2 = position_sizing(CAPITAL, price, v7r["score"], atr_pct)
                intra.append({
                    "tkr": tkr, "score": v7r["score"], "price": price,
                    "exit": ex2, "sizing": sz2, "bf": bf, "ff": ff, "earn": earn, "vol": vol_ratio,
                    "entry_rec": entry_rec, "group": group_of(tkr),
                })
                logged_signals.append({
                    "ticker": tkr, "mode": "intraday", "score": v7r["score"],
                    "signal": v7r["signal"], "entry_price": price,
                    "sl": ex2["stop_loss"], "tp": ex2["take_profit"],
                    "lots": sz2.get("lots", 0), "cost": sz2.get("cost", 0),
                    "regime": regime,
                })

            # Record cooldown if ANY signal passed
            if swing_ok or intra_ok:
                cooldown.record(tkr, v7r["signal"], {"score": swing_score})

        except Exception as e:
            logger.warning("Skip %s: %s", tkr, e)  # M1: level warning (debug tidak tampil di level WARNING)
            continue

    swing.sort(key=lambda x: x["score"], reverse=True)
    intra.sort(key=lambda x: x["score"], reverse=True)

    # ── C2: Guard konsentrasi grup konglomerat (grup = proxy sektor) ──
    # position_sizing membatasi 15% per posisi, tapi tanpa guard agregat,
    # 3 sinyal grup sama (mis. BRPT/BUMI/DSSA Barito) bisa menumpuk >40%
    # modal di satu grup berkorelasi tinggi. Batas dari config
    # portfolio.max_sector_exposure_pct (default 40%). No-op saat normal.
    portfolio_cfg = CONFIG.get("portfolio", {})
    if portfolio_cfg.get("enabled", True):
        max_group_pct = float(portfolio_cfg.get("max_sector_exposure_pct", 40.0))
        concentration_warnings = enforce_group_concentration_guard(
            swing, intra, logged_signals, CAPITAL, max_pct=max_group_pct)
        for w in concentration_warnings:
            logger.warning("C2: %s", w)
    else:
        concentration_warnings = []

    # ── Log performa sinyal ke CSV (dedup persistén: ±1% harga & <14 hari) ──
    # N7: path dibaca dari config.yaml perf_tracker.csv_path (dulu hardcode
    # perf_tracker_v7.csv walau config menyediakan path). Fallback ke nama
    # lama kalau config kosong — perilaku tidak berubah untuk config kosong.
    pt_path = (CONFIG.get("perf_tracker") or {}).get("csv_path", "") or ""
    if pt_path:
        perf_csv = pt_path if os.path.isabs(pt_path) else os.path.join(ROOT, pt_path)
    else:
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
    # M2: extra_parts (alert posisi, sektor) diintegrasikan SEBELUM truncate
    # 3500 di format_message — di-append SETELAH truncate (lama) membuat
    # output >4096 karakter → pesan di-drop Telegram.
    extra_parts = []
    if position_alerts:
        extra_parts.append(format_position_alerts(position_alerts))
    if sector_line:
        extra_parts.append(sector_line)
    output_message = format_message(swing, intra, sentiment, CAPITAL,
                                    narratives=narratives,
                                    concentration_warnings=concentration_warnings,
                                    extra_parts=extra_parts)

    print(output_message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("FATAL: %s\n%s", e, traceback.format_exc())
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
