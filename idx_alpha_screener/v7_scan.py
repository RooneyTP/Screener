"""
v7_scan.py — V7 Dual Mode Scanner (Invezgo ONLY)
Data 100% dari Invezgo. Output ke Telegram via cron + formatted.
"""
import sys, os, warnings, yaml, traceback, math
from datetime import datetime, timedelta
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
from scoring import compute_total_score, quality_gate  # IDE3: quality_gate = gate kualitas swing
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

# ── Group mapping konglomerat — SINGLE SOURCE: config.yaml section 'groups' ──
# (sebelumnya hardcode GROUP_NAMES di sini + duplikat di factor_analysis.py
# & weekly_report.py = 4 sumber drift; sekarang semua baca dari config.yaml
# via groups_config.py — fallback {} kalau config gagal, label kosong)
# R4: def group_of lokal DIHAPUS (dulu men-shadow import & crash utk
# ticker None — AttributeError 'NoneType'. group_of dari groups_config
# punya guard None + case-insensitive + param groups opsional.)
from groups_config import group_of


def _signal_from_score(score: float, regime: str, weekly: str) -> str:
    """Label sinyal dari score FINAL — konsisten dengan v7.compute().

    R3: kolom score & signal di perf CSV harus berasal dari perhitungan yang
    SAMA. N10 (P2): bonus akumulasi +5 DIHAPUS — score = skor final langsung
    dari v7.compute (sudah termasuk weekly penalty/bonus), sehingga label
    dihitung dari skor yang sama persis dengan kolom score. Threshold dibaca
    dari v7_engine.THRESHOLDS (sudah di-override config.yaml via
    v7_engine.configure) + cap weekly BEARISH → STRONG_BUY menjadi BUY —
    persis logika v7.compute().
    """
    th = v7_engine.THRESHOLDS.get(regime, v7_engine.THRESHOLDS["RANGING"])
    if score >= th[0]:
        sig = "STRONG_BUY"
    elif score >= th[1]:
        sig = "BUY"
    elif score >= th[2]:
        sig = "WEAK_BUY"
    elif score >= th[3]:
        sig = "HOLD"
    else:
        sig = "SELL"
    if str(weekly).strip().upper() == "BEARISH" and sig == "STRONG_BUY":
        sig = "BUY"
    return sig


def _swing_gate(score: float, bf: str, regime: str) -> bool:
    """Gate sinyal swing (N10 P2 — BULL-only untuk cabang akumulasi 48-49).

    - Skor >= 50 → lolos (WEAK_BUY ke atas sesuai threshold regime).
    - Cabang 'akumulasi & skor >= 48' (mengizinkan WEAK_BUY 48-49) HANYA
      aktif saat regime BULL. Di regime lain syarat skor = threshold
      market_mode yang sudah ada (>=50/60) — WEAK_BUY 48-49 tidak lolos
      (di RANGING/HIGH_VOL/BEAR bahkan sinyal WEAK_BUY sudah difilter
      allowed_signals lebih awal).
    - Distribusi dengan skor < 55 ditolak; netral/net_buy skor < 55 ditolak.
    """
    akum_gate = regime == "BULL" and "akumulasi" in bf and score >= 48
    if not (score >= 50 or akum_gate):
        return False
    if "distribusi" in bf and score < 55:
        return False
    nn = ("netral" in bf) or ("net_buy" in bf and score < 52)
    if score < 55 and nn:
        return False
    return True


# ── IDE3: GATE KUALITAS SWING — volume confirmation + quality_gate ──
# Threshold skor (SB65/BUY55 di THRESHOLDS) TIDAK diubah — gate bekerja DI
# ATAS label sinyal sebagai DOWNGRADE bertingkat; kolom score tetap skor v7
# asli, hanya label sinyal yang bisa turun (SB→BUY→WEAK_BUY→HOLD).
SWING_MIN_VOL_RATIO = 1.0       # minimal utk sinyal swing (di bawah → HOLD/skip)
SWING_SB_VOL_RATIO = 1.2        # STRONG_BUY butuh >= 1.2 (di bawah → BUY, BUKAN veto)
SWING_SB_VOL_RATIO_BULL = 1.0   # regime BULL: SB cukup >= 1.0 (tren kuat, sensitivitas dijaga)


def gate_swing_signal(swing_ok: bool, signal: str, vol_ratio, regime: str,
                      row, allowed_signals=("STRONG_BUY", "BUY", "WEAK_BUY")) -> dict:
    """IDE3 — gate kualitas sinyal swing (downgrade bertingkat, BUKAN veto).

    1. Volume confirmation: vol_ratio < 1.0 (atau NaN/0 — dianggap GAGAL
       gate) → sinyal di-downgrade HOLD (skip). STRONG_BUY butuh minimal
       1.2; di bawah → downgrade BUY (bukan veto). Di regime BULL, SB cukup
       vol_ratio minimal 1.0.
    2. quality_gate (scoring.py, signature quality_gate(row, signal)):
       falling knife / low liquidity / no trend / false breakout →
       downgrade bertingkat SB→BUY→WEAK_BUY→HOLD. Row v7_scan punya semua
       kolom yang dibaca quality_gate (rsi/vol_ratio/ret_20d/atr/close/adx
       dari compute_all_indicators) — dipanggil langsung, tanpa adaptor.

    Return {"ok": bool, "signal": str, "gate_vol": str, "gate_quality": str}
    — gate_vol/gate_quality = alasan gate ("pass" kalau lolos; dipakai utk
    kolom CSV gate_vol/gate_quality + log).
    """
    out = {"ok": bool(swing_ok), "signal": signal,
           "gate_vol": "pass", "gate_quality": "pass"}
    if not swing_ok:
        return out
    try:
        vr = float(vol_ratio)
    except (TypeError, ValueError):
        vr = float("nan")
    if not math.isfinite(vr) or vr <= 0:
        vr = 0.0  # NaN/0/negatif → anggap GAGAL gate volume
    if vr < SWING_MIN_VOL_RATIO:
        out.update({"ok": False, "signal": "HOLD",
                    "gate_vol": f"fail_vol<{SWING_MIN_VOL_RATIO}"})
        return out
    if signal == "STRONG_BUY" and vr < SWING_SB_VOL_RATIO and regime != "BULL":
        out.update({"signal": "BUY",
                    "gate_vol": f"downgrade_sb_vol<{SWING_SB_VOL_RATIO}"})
    try:
        q_sig = quality_gate(row, out["signal"])
    except Exception as e:
        logger.debug("quality_gate gagal %s: %s", signal, e)
        q_sig = out["signal"]
    if q_sig != out["signal"]:
        out["gate_quality"] = f"downgrade_{out['signal']}->{q_sig}"
        out["signal"] = q_sig
    if out["signal"] not in allowed_signals:
        out["ok"] = False
    return out


def _update_logged_sizing(logged_signals: list, ticker: str, mode: str,
                          lots: int, cost: int, risk_amount=None) -> None:
    """Sinkronkan lot/cost hasil guard ke daftar sinyal yang akan di-log ke perf CSV.

    logged_signals punya satu baris per (ticker, mode) — sama dengan sinyal di
    swing/intra, jadi update in-place supaya CSV konsisten dengan rekomendasi.
    """
    for lg in logged_signals:
        if lg.get("ticker") == ticker and lg.get("mode") == mode:
            lg["lots"] = lots
            lg["cost"] = cost
            if risk_amount is not None:
                lg["risk_amount"] = risk_amount
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
            old_cost = float(sz.get("cost", 0) or 0)
            old_risk = float(sz.get("risk_amount", 0) or 0)
            new_cost = int(new_lots * price * 100)
            sz["lots"] = new_lots
            sz["cost"] = new_cost
            sz["pct_modal"] = round(new_cost / capital * 100, 1)
            # L3 + IDE6: risk_amount di-scale PROPORSIONAL terhadap cost (fraksi
            # risiko asli dipertahankan) — risk_amount sekarang risiko SEJATI
            # (entry−SL)/entry×cost dari position_sizing, bukan 5% flat; kalau
            # key risk_amount tidak ada (pemanggil lama) fallback 5% cost.
            frac = (old_risk / old_cost) if old_cost > 0 else 0.05
            # N8: clamp fraksi — hanya pakai fraksi proporsional WAJAR
            # (0 < frac <= 0.5 = risiko jujur 0-50% dari cost). Nilai lama/
            # overstate (mis. 999_999_999 dari pemanggil lama) atau 0/negatif
            # → fallback 5% cost (konsisten dengan _sig_risk_amount).
            if not (0 < frac <= 0.5):
                frac = 0.05
            sz["risk_amount"] = int(new_cost * frac)
            _update_logged_sizing(logged_signals, s.get("tkr", ""), mode,
                                  new_lots, new_cost,
                                  risk_amount=sz.get("risk_amount"))
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


def _sig_risk_amount(s: dict) -> float:
    """Risk_amount sebuah sinyal; fallback 5% cost kalau key tidak ada (pemanggil lama)."""
    sz = s.get("sizing") or {}
    r = sz.get("risk_amount")
    if r is None:
        return float(sz.get("cost", 0) or 0) * 0.05
    return float(r or 0)


def enforce_total_risk_guard(swing: list, intra: list, logged_signals: list,
                             capital: float, max_risk_pct: float = 3.0) -> list:
    """IDE6 — Guard TOTAL RISK: Σ risk_amount semua sinyal ≤ max_risk_pct% modal.

    Dipanggil SETELAH enforce_group_concentration_guard (C2). risk_amount tiap
    sinyal sekarang risiko SEJATI (entry−SL)/entry×cost dari position_sizing.

    Aturan agregasi:
      - Semua sinyal swing + intraday dihitung.
      - Ticker yang muncul di KEDUA mode dihitung SEKALI — pakai mode dengan
        risk_amount LEBIH BESAR (mode lain adalah alternatif, bukan tambahan).
    Jika total > max_risk_pct% CAPITAL (default 3% = Rp600rb @ modal 20jt):
      lot sinyal ber-risk tertinggi diturunkan BERTAHAP (1 lot per langkah,
      fraksi risiko per rupiah dipertahankan, minimal 1 lot) sampai total ≤
      limit; sizing + logged_signals disinkronkan (konsisten dengan C2).
    No-op total saat total ≤ limit (perilaku normal TIDAK berubah).

    Return daftar baris peringatan (kosong kalau total risk normal).
    """
    warnings = []
    if capital <= 0 or not (swing or intra):
        return warnings
    limit = capital * max_risk_pct / 100.0
    all_signals = [("swing", s) for s in swing] + [("intraday", s) for s in intra]

    # ── Agregasi dengan dedup ticker (2 mode → sekali, pakai risk lebih besar) ──
    best = {}
    for mode, s in all_signals:
        tkr = s.get("tkr", "")
        if not tkr:
            continue
        r = _sig_risk_amount(s)
        if tkr not in best or r > best[tkr][2]:
            best[tkr] = (mode, s, r)
    total = sum(r for _, _, r in best.values())
    if total <= limit:
        return warnings  # normal — no-op

    # ── Kurangi lot sinyal ber-risk tertinggi bertahap (pola C2) ──
    order = sorted(best.values(), key=lambda x: x[2], reverse=True)
    details = []
    for mode, s, _r in order:
        if total <= limit:
            break
        sz = s.get("sizing") or {}
        price = float(s.get("price", 0) or 0)
        if price <= 0:
            continue
        lots = int(sz.get("lots", 0) or 0)
        if lots <= 1:
            continue
        old_cost = float(sz.get("cost", 0) or 0)
        old_risk = float(sz.get("risk_amount", 0) or 0)
        frac = (old_risk / old_cost) if old_cost > 0 else 0.05
        # N8: clamp fraksi — sama dengan C2 guard: hanya fraksi wajar
        # (0 < frac <= 0.5) yang dipertahankan; nilai overstate/lama → 5%.
        if not (0 < frac <= 0.5):
            frac = 0.05
        orig_lots = lots
        while lots > 1 and total > limit:
            lots -= 1
            new_cost = int(lots * price * 100)
            new_risk = int(new_cost * frac)
            total = total - old_risk + new_risk
            old_risk = new_risk
        if lots != orig_lots:
            new_cost = int(lots * price * 100)
            sz["lots"] = lots
            sz["cost"] = new_cost
            sz["pct_modal"] = round(new_cost / capital * 100, 1)
            sz["risk_amount"] = int(new_cost * frac)
            _update_logged_sizing(logged_signals, s.get("tkr", ""), mode,
                                  lots, new_cost,
                                  risk_amount=sz.get("risk_amount"))
            details.append(f"{s.get('tkr')}({mode}) {orig_lots}→{lots} lot")
    pct = total / capital * 100.0
    if details:
        warnings.append(
            f"⚠️ TOTAL RISK: {pct:.1f}% > {max_risk_pct:.0f}% — lot dikurangi "
            f"({', '.join(details)})"
        )
    else:
        warnings.append(
            f"⚠️ TOTAL RISK: {pct:.1f}% > {max_risk_pct:.0f}% — "
            f"hati-hati (lot sudah minimal, tidak bisa dikurangi)"
        )
    return warnings


# ── IDE5: CA (corporate action) calendar blackout ──
# Event yang memicu blackout (dari SDK Invezgo get_calendar): DIVIDEND, RIGHT,
# SPLIT, RUPS (RUPS_RESULT / RUPS_SCHEDULE). PUBLIC_EXPOSE tidak memblokir.
CA_BLACKOUT_TYPES = ("DIVIDEND", "RIGHT", "SPLIT", "RUPS")

# ── N10 (P2): syarat volume intraday — minimal 1.2x rata-rata (sebelumnya
# 1.0). Vol 1.0-1.1x = aktivitas normal, bukan lonjakan → bukan setup intraday.
INTRADAY_MIN_VOL_RATIO = 1.2


def _ca_calendar_check(ip, tkr: str, days_ahead: int = 7):
    """Cek calendar corporate action utk blackout sinyal (IDE5).

    Return (blocked, label):
      blocked = (TYPE, 'dd/mm') kalau ada event DIVIDEND/RIGHT/SPLIT/RUPS dalam
                H+days_ahead ke depan → sinyal harus di-skip.
      label   = 'TYPE dd/mm' event terdekat (apa pun tipenya) utk kolom event
                perf CSV, atau '' kalau tidak ada event.
    TIDAK pernah raise — calendar gagal / provider tanpa method = no-op
    (scan tetap jalan, perilaku lama).
    """
    try:
        events = ip.get_corporate_calendar(tkr) or []
    except Exception as e:
        logger.debug("CA calendar gagal %s: %s", tkr, e)
        return None, ""
    if not events:
        return None, ""
    today = datetime.now().date()
    horizon = today + timedelta(days=int(days_ahead))
    upcoming = []
    for ev in events:
        d = ev.get("date")
        if not d:
            continue
        try:
            ed = datetime.strptime(str(d), "%Y-%m-%d").date()
        except ValueError:
            continue
        if ed < today:
            continue
        upcoming.append((ed, str(ev.get("type", "") or "").upper()))
    if not upcoming:
        return None, ""
    upcoming.sort()
    nearest_date, nearest_type = upcoming[0]  # tuple = (tanggal, tipe event)
    label = f"{nearest_type} {nearest_date.strftime('%d/%m')}"
    for ed, etype in upcoming:
        if ed <= horizon and any(k in etype for k in CA_BLACKOUT_TYPES):
            return (etype, ed.strftime("%d/%m")), label
    return None, label


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


def flow_spike_warnings(swing: list, intra: list, max_n: int = 3) -> list:
    """L2-A — warning FLOW SPIKE utk section MANAJEMEN RISIKO (maks max_n).

    Ticker dengan flag flow_spike True (factor_broker_trend mendeteksi net buy
    MENDADAK vs baseline 20d — insight bandarmologi user: bandar bergerak diam,
    net buy besar yang tiba-tiba = jebakan distribusi besok) → peringatan
    waspada distribusi. Cap 3 ticker TOP SKOR saja (hindari spam). Ticker yang
    muncul di swing & intraday dihitung sekali (skor tertinggi).
    """
    best = {}
    for _s in list(swing) + list(intra):
        if not _s.get("flow_spike"):
            continue
        t = _s.get("tkr", "")
        if not t:
            continue
        sc = float(_s.get("score", 0) or 0)
        if t not in best or sc > best[t]:
            best[t] = sc
    out = []
    for _t in sorted(best, key=best.get, reverse=True)[:int(max_n)]:
        out.append(f"⚠️ FLOW SPIKE: {_t} net buy mendadak — waspada distribusi (jebakan bandar)")
    return out


def conflict_warnings(swing: list, intra: list, max_n: int = 3) -> list:
    """IDE1 — warning CONFLICT FLOW utk section MANAJEMEN RISIKO (maks max_n).

    Ticker dengan flag conflict_snapshot_vs_trend True (compute() V7: snapshot
    broker 3 hari akumulasi > 65 TAPI trend 20 hari distribusi < 35 →
    kontribusi broker_flow di-cap netral 50) → peringatan waspada distribusi.
    Pola sama dengan flow_spike_warnings: cap 3 ticker TOP SKOR, ticker yang
    muncul di swing & intraday dihitung sekali (skor tertinggi).
    """
    best = {}
    for _s in list(swing) + list(intra):
        if not _s.get("conflict_snapshot_vs_trend"):
            continue
        t = _s.get("tkr", "")
        if not t:
            continue
        sc = float(_s.get("score", 0) or 0)
        if t not in best or sc > best[t]:
            best[t] = sc
    out = []
    for _t in sorted(best, key=best.get, reverse=True)[:int(max_n)]:
        out.append(f"⚠️ CONFLICT FLOW: {_t} snapshot akumulasi vs trend distribusi 20d — "
                   f"kontribusi broker flow di-cap netral (waspada jebakan distribusi)")
    return out


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
    # N10 (P3): jejak audit per-ticker — tiap ticker disabled di-log INFO
    # (sebelumnya hanya daftar agregat; audit butuh jejak skip yang jelas).
    for _d in sorted(disabled):
        logger.info("Watchlist disable: %s", _d)
    CAPITAL = 20_000_000
    # IDE5: earnings_blackout_days dari config.yaml exit_strategy (default 7) —
    # jendela blackout corporate action (DIVIDEND/RIGHT/SPLIT/RUPS) ke depan.
    earnings_blackout_days = int(CONFIG.get("exit_strategy", {}).get("earnings_blackout_days", 7) or 7)
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
        # N10 (P2): cooldown 2 hari (config.yaml di-handle paralel; kalau
        # config.days berubah jadi 2, kode otomatis ikut — fallback di sini
        # juga 2). Efek: streak harian (BUMI 8 hari) terpotong jadi maks
        # ~2-3 kemunculan per minggu.
        cooldown_days=cd_cfg.get("days", 2) if cd_cfg.get("enabled", True) else 0,
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
    skip_reasons = []  # IDE5: alasan skip CA blackout (tampil di pesan)

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
            # Cooldown check — R3 per MODE: skip cepat hanya kalau KEDUA mode
            # sedang cooldown; gate per-mode dilakukan setelah sinyal dihitung
            # (record swing tidak boleh memblokir intraday, dan sebaliknya).
            if cooldown.is_on_cooldown(tkr, "swing") and cooldown.is_on_cooldown(tkr, "intraday"):
                logger.warning("Cooldown: %s (swing & intraday)", tkr)  # M1: level warning (debug tidak tampil di level WARNING)
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

            weekly = row.get("weekly_trend", "NO_DATA")
            v4s = compute_total_score(row, regime)
            # V7 akurasi: weekly trend masuk scoring (BEARISH -12 + cap
            # STRONG_BUY→BUY, BULLISH +5) — post-adjustment di v7.compute()
            v7r = v7_engine.compute(tkr, v4s, regime, weekly_trend=weekly)

            if v7r["signal"] not in allowed_signals:
                # N10 (P3): jejak audit — sebelumnya skip ini SILENT (audit
                # noise tidak bisa melacak kenapa ticker tidak muncul).
                logger.info("Skip %s: signal %s tidak diizinkan di regime %s",
                            tkr, v7r["signal"], regime)
                continue

            price = float(row["close"])
            atr = float(row.get("atr", 0) or 0)
            atr_pct = (atr / price * 100) if price > 0 else 0
            bf = v7r["factors"].get("broker_detail", "")
            ff = v7r["factors"].get("foreign_detail", "")
            earn = v7r["factors"].get("earnings_detail", "")
            vol_ratio = float(row.get("vol_ratio", 1) or 1)
            # N4: vol_ratio NaN lolos guard lama (`nan or 1` → nan truthy) →
            # `vol_ratio >= ambang` selalu False → sinyal intraday hilang diam-diam.
            # NaN/inf → default 1.0 + jejak debug.
            if not math.isfinite(vol_ratio):
                logger.debug("vol_ratio tidak valid (%s) → default 1.0", vol_ratio)
                vol_ratio = 1.0
            brokers_raw = v7r["factors"].get("brokers", "")

            # N10 (P2): bonus akumulasi +5 DIHAPUS — 0 sinyal bergantung
            # padanya dan menginflasi label/skor/cooldown/perf CSV. Score =
            # skor final langsung dari v7.compute (sudah termasuk weekly
            # penalty/bonus).
            swing_score = v7r["score"]

            # ── Swing filter (independent) ──
            # N10 (P2): cabang akumulasi 48-49 hanya aktif di regime BULL
            # (lihat _swing_gate) — di regime lain syarat skor = threshold
            # market_mode (>=50/60) yang sudah ada.
            swing_ok = _swing_gate(swing_score, bf, regime)

            # ── IDE3: gate kualitas SWING (volume confirmation + quality_gate) ──
            # Downgrade bertingkat di ATAS label sinyal (threshold skor
            # SB65/BUY55 TIDAK diubah): vol_ratio < 1.0 (atau NaN/0) → HOLD/
            # skip; STRONG_BUY tanpa vol >= 1.2 (>= 1.0 di BULL) → BUY;
            # quality_gate (falling knife/low liquidity/no trend/false
            # breakout) → SB→BUY→WEAK_BUY→HOLD. Hasil dicatat di log +
            # dict sinyal (gate_vol/gate_quality).
            swing_signal = _signal_from_score(swing_score, regime, weekly)
            gate_vol, gate_quality = "pass", "pass"
            if swing_ok:
                _g = gate_swing_signal(swing_ok, swing_signal,
                                       row.get("vol_ratio"), regime, row,
                                       allowed_signals)
                swing_ok, swing_signal = _g["ok"], _g["signal"]
                gate_vol, gate_quality = _g["gate_vol"], _g["gate_quality"]
                if gate_vol != "pass" or gate_quality != "pass":
                    logger.warning(
                        "Gate kualitas %s: gate_vol=%s gate_quality=%s → %s",
                        tkr, gate_vol, gate_quality, swing_signal)

            # ── Intraday filter (independent of swing) ──
            # N10 (P2): vol_ratio minimal 1.2x (sebelumnya 1.0) — vol
            # 1.0-1.1x bukan lonjakan volume.
            intra_ok = v7r["score"] >= 48 and vol_ratio >= INTRADAY_MIN_VOL_RATIO

            # R3: cooldown per MODE — record swing tidak memblokir intraday
            # (dan sebaliknya). Dulu 1 slot ticker-level → record intraday
            # menimpa swing & swing memblokir intraday.
            if swing_ok and cooldown.is_on_cooldown(tkr, "swing"):
                logger.warning("Cooldown: %s (swing)", tkr)
                swing_ok = False
            if intra_ok and cooldown.is_on_cooldown(tkr, "intraday"):
                logger.warning("Cooldown: %s (intraday)", tkr)
                intra_ok = False

            # ── IDE5: CA calendar blackout — corporate action
            # (DIVIDEND/RIGHT/SPLIT/RUPS) dalam H+earnings_blackout_days ke
            # depan → SKIP sinyal (swing & intraday) + catat alasan. Calendar
            # gagal/kosong = no-op (scan tetap jalan). ca_label dipakai kolom
            # 'event' di perf CSV (IDE1) — event terdekat walau di luar jendela.
            ca_block, ca_label = None, ""
            if swing_ok or intra_ok:
                try:
                    ca_block, ca_label = _ca_calendar_check(ip, tkr, earnings_blackout_days)
                except Exception as e:
                    logger.debug("CA calendar check gagal %s: %s", tkr, e)
                    ca_block, ca_label = None, ""
                if ca_block:
                    skip_reasons.append(
                        f"⚠️ CA BLACKOUT: {tkr} {ca_block[0]} {ca_block[1]} — skip (H+{earnings_blackout_days})")
                    logger.warning("CA blackout: %s (%s %s)", tkr, ca_block[0], ca_block[1])
                    swing_ok = False
                    intra_ok = False

            # N10 (P3): jejak audit — alasan skip yang sebelumnya SILENT
            # (tidak lolos gate swing/intraday sama sekali). IDE3: alasan
            # gate kualitas (gate_vol/gate_quality) ikut tercatat.
            if not swing_ok and not intra_ok:
                logger.info(
                    "Skip %s: tidak lolos gate swing/intraday (score=%.1f, "
                    "vol_ratio=%.2f, bf=%s, gate_vol=%s, gate_quality=%s)",
                    tkr, v7r["score"], vol_ratio, bf or "-",
                    gate_vol, gate_quality)

            # L12: recommend_entry dipanggil SEKALI per ticker (sebelumnya 2x
            # untuk ticker yang lolos swing+intraday — hasilnya identik).
            entry_rec = None
            if swing_ok or intra_ok:
                entry_rec = recommend_entry(tkr, price, atr, row, v7r, sentiment)

            if swing_ok:
                ex = compute_exit(price, atr, regime, "swing", weekly)
                # IDE6: sl FINAL dari compute_exit → risk_amount risiko sejati
                sz = position_sizing(CAPITAL, price, swing_score, atr_pct, sl=ex["stop_loss"])
                swing.append({
                    "tkr": tkr, "score": swing_score, "price": price,
                    "exit": ex, "sizing": sz,
                    "bf": bf, "ff": ff, "earn": earn, "weekly": weekly, "brokers": brokers_raw,
                    "entry_rec": entry_rec, "group": group_of(tkr),
                    "flow_spike": bool(v7r["factors"].get("flow_spike", False)),
                    "conflict_snapshot_vs_trend": bool(v7r["factors"].get("conflict_snapshot_vs_trend", False)),  # IDE1
                    "rsi": float(row.get("rsi", 0) or 0),
                    "vol_ratio": vol_ratio,
                    "gate_vol": gate_vol, "gate_quality": gate_quality,  # IDE3
                })
                logged_signals.append({
                    "ticker": tkr, "mode": "swing", "score": swing_score,
                    # R3: signal dihitung dari score FINAL (N10: bonus +5
                    # sudah dihapus — score = skor v7.compute langsung).
                    # IDE3: label FINAL setelah gate kualitas (downgrade
                    # SB→BUY→WEAK_BUY→HOLD kalau vol/gate kualitas gagal).
                    "signal": swing_signal,
                    "entry_price": price,
                    "sl": ex["stop_loss"], "tp": ex["take_profit"],
                    "lots": sz.get("lots", 0), "cost": sz.get("cost", 0),
                    # N10 (P3): risk_amount risiko sejati di perf CSV.
                    "risk_amount": int(sz.get("risk_amount", 0) or 0),
                    "regime": regime,
                    # IDE1 (faktor DNA): nilai faktor 0-100 + atr_pct/vol_ratio + event CA
                    "broker_flow": round(float(v7r["factors"].get("broker_flow", 0) or 0), 1),
                    "broker_trend": round(float(v7r["factors"].get("broker_trend", 0) or 0), 1),
                    # IDE4/5 (DNA lengkap): flag flow spike + rincian broker trend
                    "flow_spike": bool(v7r["factors"].get("flow_spike", False)),
                    "broker_trend_detail": v7r["factors"].get("broker_trend_detail", "unknown"),
                    "foreign_flow": round(float(v7r["factors"].get("foreign_flow", 0) or 0), 1),
                    "fundamental": round(float(v7r["factors"].get("fundamental", 0) or 0), 1),
                    "earnings_momentum": round(float(v7r["factors"].get("earnings_momentum", 0) or 0), 1),
                    "weekly_trend": weekly,
                    "atr_pct": round(atr_pct, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "event": ca_label,
                    "gate_vol": gate_vol, "gate_quality": gate_quality,  # IDE3
                })

            if intra_ok:
                ex2 = compute_exit(price, atr, regime, "intraday", weekly)
                # IDE6: sl FINAL dari compute_exit → risk_amount risiko sejati
                sz2 = position_sizing(CAPITAL, price, v7r["score"], atr_pct, sl=ex2["stop_loss"])
                intra.append({
                    "tkr": tkr, "score": v7r["score"], "price": price,
                    "exit": ex2, "sizing": sz2, "bf": bf, "ff": ff, "earn": earn, "vol": vol_ratio,
                    "entry_rec": entry_rec, "group": group_of(tkr),
                    "flow_spike": bool(v7r["factors"].get("flow_spike", False)),
                    "conflict_snapshot_vs_trend": bool(v7r["factors"].get("conflict_snapshot_vs_trend", False)),  # IDE1
                })
                logged_signals.append({
                    "ticker": tkr, "mode": "intraday", "score": v7r["score"],
                    # R3: konsisten — label dari score yang sama dengan kolom score.
                    "signal": _signal_from_score(v7r["score"], regime, weekly),
                    "entry_price": price,
                    "sl": ex2["stop_loss"], "tp": ex2["take_profit"],
                    "lots": sz2.get("lots", 0), "cost": sz2.get("cost", 0),
                    # N10 (P3): risk_amount risiko sejati di perf CSV.
                    "risk_amount": int(sz2.get("risk_amount", 0) or 0),
                    "regime": regime,
                    # IDE1 (faktor DNA): nilai faktor 0-100 + atr_pct/vol_ratio + event CA
                    "broker_flow": round(float(v7r["factors"].get("broker_flow", 0) or 0), 1),
                    "broker_trend": round(float(v7r["factors"].get("broker_trend", 0) or 0), 1),
                    # IDE4/5 (DNA lengkap): flag flow spike + rincian broker trend
                    "flow_spike": bool(v7r["factors"].get("flow_spike", False)),
                    "broker_trend_detail": v7r["factors"].get("broker_trend_detail", "unknown"),
                    "foreign_flow": round(float(v7r["factors"].get("foreign_flow", 0) or 0), 1),
                    "fundamental": round(float(v7r["factors"].get("fundamental", 0) or 0), 1),
                    "earnings_momentum": round(float(v7r["factors"].get("earnings_momentum", 0) or 0), 1),
                    "weekly_trend": weekly,
                    "atr_pct": round(atr_pct, 2),
                    "vol_ratio": round(vol_ratio, 2),
                    "event": ca_label,
                })

            # Record cooldown per MODE yang lolos (R3) — key (ticker, mode),
            # label sinyal dari score final mode tsb (IDE3: swing pakai label
            # SETELAH gate kualitas — cooldown konsisten dengan sinyal yang
            # benar-benar dikeluarkan).
            if swing_ok:
                cooldown.record(tkr, swing_signal,
                                {"score": swing_score}, mode="swing")
            if intra_ok:
                cooldown.record(tkr, _signal_from_score(v7r["score"], regime, weekly),
                                {"score": v7r["score"]}, mode="intraday")

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

    # ── IDE6: Guard TOTAL RISK — Σ risk_amount (dedup ticker 2 mode, pakai
    # mode dgn risk lebih besar) ≤ max_total_risk_pct% CAPITAL (default 3% =
    # Rp600rb @ modal 20jt). risk_amount sekarang risiko SEJATI (entry−SL)/entry
    # × cost dari position_sizing. Dijalankan SETELAH C2 (lot sudah dikecilkan
    # untuk konsentrasi grup); kalau masih lewat → lot ber-risk tertinggi
    # diturunkan bertahap. Peringatan digabung ke concentration_warnings supaya
    # tampil di pesan Telegram lewat jalur yang sama.
    if portfolio_cfg.get("enabled", True):
        max_risk_pct = float(portfolio_cfg.get("max_total_risk_pct", 3.0))
        risk_warnings = enforce_total_risk_guard(
            swing, intra, logged_signals, CAPITAL, max_risk_pct=max_risk_pct)
        concentration_warnings.extend(risk_warnings)
        for w in risk_warnings:
            logger.warning("IDE6: %s", w)

    # ── L2-A: FLOW SPIKE — net buy mendadak = jebakan distribusi (bandarmologi) ──
    # Insight user: bandar bergerak DIAM; net buy besar yang tiba-tiba muncul
    # sering persiapan distribusi besok. Warning di MANAJEMEN RISIKO, maks 3
    # ticker TOP SKOR (hindari spam).
    flow_warnings = flow_spike_warnings(swing, intra, max_n=3)
    concentration_warnings.extend(flow_warnings)
    for w in flow_warnings:
        logger.warning("L2-A: %s", w)

    # ── IDE1: CONFLICT FLOW — snapshot akumulasi vs trend distribusi 20d ──
    # Snapshot 3 hari bertentangan dengan trend 20 hari (kap kontribusi
    # broker_flow di compute()) → warning di MANAJEMEN RISIKO, maks 3 ticker
    # TOP SKOR (pola sama dengan flow_spike_warnings).
    conflict_flow_warnings = conflict_warnings(swing, intra, max_n=3)
    concentration_warnings.extend(conflict_flow_warnings)
    for w in conflict_flow_warnings:
        logger.warning("IDE1: %s", w)

    # ── L2-B: IHSG LATE-SESSION SURGE — loncat kodok penutupan (bandarmologi) ──
    # Insight user: IHSG meloncat 15.30-16.00 = bandar ajak ritel beli →
    # waspada distribusi besok. Flag dari predict_market_sentiment (data
    # intraday 5 menit get_index_intraday).
    if sentiment.get("late_surge"):
        _lbl = sentiment.get("late_surge_label", "")
        _w = f"⚠️ IHSG LONCAT: {_lbl} — waspada distribusi besok"
        concentration_warnings.append(_w)
        logger.warning("L2-B: %s", _w)

    # ── Log performa sinyal ke CSV (dedup persistén: ±1% harga & <14 hari) ──
    # N7-FIX: path SELALU data/perf_tracker_v7.csv (hardcode, sama seperti
    # sebelum regresi N7). Key config 'perf_tracker.csv_path' TIDAK dibaca —
    # itu key LEGACY untuk main.py lama dan pernah menyesatkan path ke
    # data/perf_tracker.csv (file terpisah yang tidak dipakai perf_tracker /
    # factor_analysis / weekly_report). Section perf_tracker di config.yaml
    # sudah dikomentari dengan catatan legacy (lihat config.yaml). JANGAN
    # menghidupkan kembali pembacaan key ini.
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
    # IDE5: alasan skip CA blackout ditampilkan di section MANAJEMEN RISIKO
    # lewat param skip_reasons (dipetakan jadi '⚠️ Warning: ...' + detail),
    # bukan lagi di extra_parts (PASS 3 format).
    output_message = format_message(swing, intra, sentiment, CAPITAL,
                                    narratives=narratives,
                                    concentration_warnings=concentration_warnings,
                                    skip_reasons=skip_reasons,
                                    extra_parts=extra_parts)

    print(output_message)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error("FATAL: %s\n%s", e, traceback.format_exc())
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
