"""
perf_tracker.py — Signal Performance Logger untuk V7
====================================================
Mencatat setiap sinyal yang dikeluarkan V7 ke CSV.
Fungsinya: mengukur Win Rate asli V7 secara forward (karena
backtest V7 tidak mungkin — broker flow tidak punya history).

CSV: data/perf_tracker_v7.csv
Kolom: date, ticker, mode, score, signal, entry_price, sl, tp, lots, cost, fresh, regime
  + faktor DNA (IDE1): broker_flow, foreign_flow, fundamental, earnings_momentum
    (nilai numerik faktor 0-100 dari v7r['factors']), weekly_trend (string
    BULLISH/BEARISH/NO_DATA), atr_pct & vol_ratio (angka dari baris harga),
    event (nama corporate action + tanggal, atau '' — dari CA calendar IDE5).
    Baris lama (CSV migrasi) di-backfill 'unknown' (event → '').
    Dipakai factor_analysis (A2) untuk pivot WR per faktor DNA.
  - fresh: 1 = sinyal baru, 0 = lanjutan (duplikat sinyal lama <14 hari, ±1% harga)
    N10 (P1, fix noise): fresh=1 HANYA jika TIDAK ada baris (ticker+mode,
    entry ±1%) dalam 7 hari kalender terakhir ATAU anchor fresh=1 <7 hari.
  - risk_amount: risiko sejati (entry−SL)/entry×cost dari position_sizing
    (N10 P3); baris lama di-backfill 0.
  - regime: regime market saat sinyal dikeluarkan (BULL/BEAR/HIGH_VOLATILITY/RANGING)
    dari detect_market_regime di v7_scan; baris lama (CSV migrasi) di-backfill 'unknown'.
    Dipakai weekly_report (E2) untuk tabel WR per regime market.

DEDUP PERSISTEN (A1):
Sebelum menulis sinyal baru, riwayat CSV dicek. Sinyal dianggap SAMA
(lanjutan) jika ticker + mode yang sama:
  A. entry_price dalam toleransi ±1% DAN usia sinyal < 14 hari (aturan lama), ATAU
  B. ada sinyal BARU (fresh=1) berusia < 7 hari, APAPUN beda harganya (aturan R6).
     Mencegah saham yang naik >1%/hari (mis. BUMI 168→179→187 dalam 4 hari)
     tampil sebagai 'sinyal baru' setiap malam — label '(lanjutan - sinyal dd/mm)'
     mengacu tanggal sinyal baru terakhir.
Sinyal tersebut TIDAK di-log sebagai sinyal baru — dicatat dengan fresh=0 agar
bisa dilabel '(lanjutan)' di Telegram. Logika cooldown (signal_manager) tetap jalan
sebagai lapisan terpisah; dedup ini lapisan tambahan di sisi logging, bukan
pengganti cooldown.

N10 (P1, fix noise — DEDUP BATCH ANTAR-RUN):
Sinyal (ticker+mode, entry ±1%) yang SUDAH tercatat HARI INI (tanggal kalender
sama) TIDAK di-log ulang sama sekali (skip total, bukan fresh=0) — 1 baris per
sinyal per hari. Scanner yang jalan beberapa kali semalam sebelumnya menulis
ulang sinyal identik (44 baris dengan 33 duplikat pada 08/08).

N10 (P1, fix noise — FALSE-FRESH):
Sebelumnya 16 baris (13.6%) dilabel fresh=1 padahal ticker+mode+entry identik
sudah tercatat di hari SEBELUMNYA (contoh batch 08/08 22:02, BUMI/AKRA
04-05/08). Sekarang penentuan fresh eksplisit: fresh=1 HANYA jika tidak ada
baris (ticker+mode, entry ±1%) dalam FRESH_MAX_AGE_DAYS (7) hari kalender
terakhir (jaring ganda di atas rule A 14 hari di find_previous_signal), dan
format tanggal "%Y-%m-%d %H:%M:%S" ikut diparse (baris ber-detik sebelumnya
lolos dedup diam-diam → re-label 'baru').
"""
import os
import csv
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("perf_tracker")

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "perf_tracker_v7.csv")
FIELDS = ["date", "ticker", "mode", "score", "signal", "entry_price", "sl", "tp", "lots", "cost", "fresh", "regime",
          "broker_flow", "foreign_flow", "fundamental", "earnings_momentum", "weekly_trend",
          "atr_pct", "vol_ratio", "event", "risk_amount"]

# Nilai default saat migrasi CSV lama (kolom ditambahkan ke header + baris lama di-backfill)
# IDE1: kolom faktor DNA lama → 'unknown' (jujur: nilainya tidak diketahui, bukan 0);
# event → '' (tidak ada event tercatat).
FIELD_DEFAULTS = {"fresh": "1", "regime": "unknown",
                  "broker_flow": "unknown", "foreign_flow": "unknown", "fundamental": "unknown",
                  "earnings_momentum": "unknown", "weekly_trend": "unknown",
                  "atr_pct": "unknown", "vol_ratio": "unknown", "event": "",
                  "risk_amount": "0"}

# ── Parameter dedup ──
DEDUP_TOLERANCE = 0.01      # toleransi entry_price ±1%
DEDUP_MAX_AGE_DAYS = 14     # sinyal lama < 14 hari dianggap masih "sama"
FRESH_MAX_AGE_DAYS = 7      # anchor sinyal BARU (fresh=1) < 7 hari → lanjutan,
                            # apapun beda harganya (aturan R6)

DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def _parse_date(value):
    """Parse tanggal CSV. Return datetime atau None."""
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def _ensure_header(csv_path):
    """Pastikan header CSV punya SEMUA kolom FIELDS (migrasi aman untuk CSV lama).

    Jika file sudah ada tapi header belum punya kolom tertentu (mis. 'fresh'
    atau 'regime'), seluruh baris lama di-backfill nilai default kolom itu
    (fresh=1, regime='unknown') dan header ditulis ulang.
    No-op jika file belum ada atau header sudah lengkap.
    """
    if not os.path.exists(csv_path):
        return
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            first_line = f.readline()
        if not first_line.strip():
            return
        header_cols = [c.strip().lower() for c in first_line.strip().split(",")]
        missing = [f for f in FIELDS if f not in header_cols]
        if not missing:
            return
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            for row in rows:
                for col in missing:
                    row[col] = FIELD_DEFAULTS.get(col, "")
                writer.writerow(row)
        logger.info("Perf CSV migrasi: kolom %s ditambahkan, %d baris lama di-backfill (%s)",
                    ",".join(missing), len(rows),
                    ", ".join(f"{c}={FIELD_DEFAULTS.get(c, '')}" for c in missing))
    except Exception as e:
        logger.warning("Gagal migrasi header perf CSV (%s): %s", csv_path, e)


def find_previous_signal(csv_path, ticker, mode, entry_price,
                         tolerance=DEDUP_TOLERANCE,
                         max_age_days=DEDUP_MAX_AGE_DAYS,
                         fresh_max_age_days=FRESH_MAX_AGE_DAYS,
                         rows=None):
    """Cari sinyal lama yang dianggap sama dengan sinyal baru.

    Kriteria match (ticker + mode sama):
      A. entry_price dalam toleransi ±tolerance (default ±1%) DAN usia sinyal
         < max_age_days (default 14 hari) — aturan lama (termasuk baris fresh=0).
      B. Anchor sinyal BARU (fresh=1 di CSV) berusia < fresh_max_age_days
         (default 7 hari), APAPUN beda harganya — aturan R6. Mencegah saham
         yang naik >1%/hari (mis. BUMI 168→179→187) tampil sebagai 'sinyal
         baru' setiap malam. Baris fresh=0 (lanjutan) BUKAN anchor — kalau
         tidak, label lanjutan tidak akan pernah berakhir.

    rows : list[dict], optional — snapshot riwayat dari load_signals().
    Jika diberikan, dipakai langsung (hindari membandingkan dengan baris
    yang baru ditulis di batch yang sama); jika None, baca dari file.

    Return (is_duplicate: bool, ref_date: str|None).
    ref_date = tanggal sinyal match TERBARU, format '%d/%m' (untuk label
    '(lanjutan - sinyal dd/mm)' di Telegram). None jika tidak ada match.
    """
    try:
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        return False, None
    if entry_price <= 0:
        return False, None

    history = rows if rows is not None else load_signals(csv_path)
    now = datetime.now()
    best_dt = None
    for row in history:
        if row.get("ticker") != ticker or row.get("mode") != mode:
            continue
        try:
            old_price = float(row.get("entry_price", 0))
        except (TypeError, ValueError):
            continue
        if old_price <= 0:
            continue
        dt = _parse_date(row.get("date"))
        if dt is None:
            continue
        if now - dt >= timedelta(days=max_age_days):
            continue
        # A: harga dalam toleransi ±1% (aturan lama)
        price_match = abs(entry_price - old_price) / old_price <= tolerance
        # B: anchor sinyal BARU (fresh=1) berusia < 7 hari → lanjutan apapun
        # beda harganya. Baris lama tanpa kolom fresh dianggap fresh=1
        # (konsisten dengan backfill load_signals).
        is_fresh_anchor = str(row.get("fresh") or "1").strip() == "1"
        fresh_match = is_fresh_anchor and (now - dt) < timedelta(days=fresh_max_age_days)
        if not (price_match or fresh_match):
            continue
        if best_dt is None or dt > best_dt:
            best_dt = dt

    if best_dt is None:
        return False, None
    return True, best_dt.strftime("%d/%m")


def log_signal(csv_path, ticker, mode, score, signal, entry_price, sl, tp,
               lots, cost, fresh=True, regime="unknown",
               broker_flow="", foreign_flow="", fundamental="",
               earnings_momentum="", weekly_trend="", atr_pct="",
               vol_ratio="", event="", risk_amount=0) -> bool:
    """Log satu sinyal ke CSV. Return True jika sukses.

    fresh=True  → sinyal baru (fresh=1 di CSV)
    fresh=False → lanjutan/duplikat (fresh=0 di CSV, dilabel '(lanjutan)' di Telegram)
    regime      → regime market saat sinyal (BULL/BEAR/HIGH_VOLATILITY/RANGING),
                  default 'unknown' untuk pemanggil lama (kompatibel).
    IDE1 (faktor DNA) — kolom faktor dari v7r['factors'] + baris harga:
      broker_flow / foreign_flow / fundamental / earnings_momentum : nilai
      numerik faktor 0-100 (default '' kalau pemanggil lama tidak mengirim).
      weekly_trend : string (BULLISH/BEARISH/NO_DATA; default 'unknown').
      atr_pct / vol_ratio : angka (default '').
      event : nama corporate action + tanggal atau '' (CA calendar IDE5).
      risk_amount : risiko sejati (entry−SL)/entry×cost (N10 P3); default 0.
    """
    try:
        os.makedirs(os.path.dirname(csv_path), exist_ok=True)
        row = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "ticker": ticker,
            "mode": mode,
            "score": round(float(score), 1),
            "signal": signal,
            "entry_price": round(float(entry_price), 2),
            "sl": round(float(sl), 2),
            "tp": round(float(tp), 2),
            "lots": int(lots),
            "cost": int(cost),
            "fresh": 1 if fresh else 0,
            "regime": regime if regime else "unknown",
            "broker_flow": broker_flow if broker_flow not in (None, "") else "",
            "foreign_flow": foreign_flow if foreign_flow not in (None, "") else "",
            "fundamental": fundamental if fundamental not in (None, "") else "",
            "earnings_momentum": earnings_momentum if earnings_momentum not in (None, "") else "",
            "weekly_trend": weekly_trend if weekly_trend else "unknown",
            "atr_pct": atr_pct if atr_pct not in (None, "") else "",
            "vol_ratio": vol_ratio if vol_ratio not in (None, "") else "",
            "event": event if event else "",
            "risk_amount": int(risk_amount or 0),
        }
        new_file = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0  # L10: file 0 byte = baru → header wajib
        _ensure_header(csv_path)  # migrasi header kalau CSV lama (no-op jika sudah lengkap)
        with open(csv_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
        return True
    except Exception as e:
        logger.warning("Gagal log sinyal %s: %s", ticker, e)
        return False


def _logged_today(history, ticker, mode, entry_price,
                  tolerance=DEDUP_TOLERANCE, now=None) -> bool:
    """True jika (ticker, mode, entry ±tolerance) SUDAH tercatat HARI INI
    (tanggal kalender sama dengan `now`).

    N10 (P1) — DEDUP BATCH ANTAR-RUN: scanner yang jalan beberapa kali
    semalam menulis ulang sinyal identik (08/08: 44 baris, 33 duplikat).
    Aturan baru: 1 baris per sinyal per hari — sinyal yang sudah tercatat
    hari ini TIDAK di-log ulang sama sekali (skip total, bukan fresh=0).
    """
    try:
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        return False
    if entry_price <= 0:
        return False
    now = now or datetime.now()
    today = now.date()
    for row in history:
        if row.get("ticker") != ticker or row.get("mode") != mode:
            continue
        try:
            old_price = float(row.get("entry_price", 0))
        except (TypeError, ValueError):
            continue
        if old_price <= 0:
            continue
        dt = _parse_date(row.get("date"))
        if dt is None or dt.date() != today:
            continue
        if abs(entry_price - old_price) / old_price <= tolerance:
            return True
    return False


def _recent_price_match(history, ticker, mode, entry_price,
                        days=FRESH_MAX_AGE_DAYS, tolerance=DEDUP_TOLERANCE,
                        now=None) -> bool:
    """True jika ada baris (ticker+mode) dengan entry ±tolerance berusia
    <= `days` HARI KALENDER terakhir (default 7 = FRESH_MAX_AGE_DAYS).

    N10 (P1) — FALSE-FRESH: fresh=1 HANYA jika TIDAK ada baris seperti ini
    dalam 7 hari kalender terakhir. Sebelumnya 16 baris (13.6%) dilabel
    fresh=1 padahal ticker+mode+entry identik sudah tercatat di hari
    sebelumnya (contoh batch 08/08 22:02, BUMI/AKRA 04-05/08). Jaring ganda
    di atas rule A 14 hari di find_previous_signal (14 ⊇ 7) — kalau suatu
    saat jendela rule A dipersempit, fresh tetap aman.
    """
    try:
        entry_price = float(entry_price)
    except (TypeError, ValueError):
        return False
    if entry_price <= 0:
        return False
    now = now or datetime.now()
    for row in history:
        if row.get("ticker") != ticker or row.get("mode") != mode:
            continue
        try:
            old_price = float(row.get("entry_price", 0))
        except (TypeError, ValueError):
            continue
        if old_price <= 0:
            continue
        dt = _parse_date(row.get("date"))
        if dt is None:
            continue
        if (now - dt).days > days:
            continue
        if abs(entry_price - old_price) / old_price <= tolerance:
            return True
    return False


def dedup_and_log_batch(csv_path, signals) -> list:
    """Log batch sinyal DENGAN dedup persistén (lapisan tambahan di atas cooldown).

    Untuk tiap sinyal:
      1. N10 (P1): kalau (ticker+mode, entry ±1%) SUDAH tercatat HARI INI
         (tanggal kalender sama) → SKIP TOTAL: tidak di-log ulang sama sekali
         (bukan fresh=0) + log warning 'skip duplikat batch'. 1 baris per
         sinyal per hari.
      2. Cek riwayat CSV (find_previous_signal): ticker+mode sama, entry ±1% &
         usia < 14 hari ATAU anchor fresh=1 < 7 hari (aturan R6) → fresh=False
         (lanjutan), kalau tidak → fresh=True. N10 (P1): fresh=1 HANYA jika
         tidak ada baris (ticker+mode, entry ±1%) dalam 7 hari kalender
         terakhir (jaring ganda _recent_price_match).
      3. Tetap tulis baris ke CSV (sinyal lanjutan tetap tercatat, fresh=0).

    Return list of dict per sinyal:
      {ticker, mode, fresh (bool), ref_date (str|None), logged (bool)}
      + skipped (bool, True kalau di-skip sebagai duplikat batch hari ini)
    — dipakai v7_scan untuk memberi label '(lanjutan - sinyal dd/mm)' di Telegram.
    """
    results = []
    history = load_signals(csv_path)  # snapshot riwayat SEBELUM batch (baris batch baru tidak ikut dibandingkan)
    written = []  # baris yang berhasil ditulis batch ini (belum ada di snapshot history)
    for s in signals:
        ticker = s.get("ticker")
        mode = s.get("mode")
        entry = s.get("entry_price")
        # N10 (P1): dedup batch antar-run — skip total kalau sudah tercatat hari ini.
        if _logged_today(history + written, ticker, mode, entry):
            logger.warning(
                "Skip duplikat batch: %s (%s) entry=%s sudah tercatat hari ini — "
                "tidak di-log ulang (1 baris per sinyal per hari)",
                ticker, mode, entry)
            results.append({
                "ticker": ticker, "mode": mode,
                "fresh": False, "ref_date": None, "logged": False,
                "skipped": True,
            })
            continue
        is_dup, ref_date = find_previous_signal(
            csv_path, ticker, mode, entry, rows=history)
        # N10 (P1): false-fresh — fresh=1 HANYA jika tidak ada baris
        # (ticker+mode, entry ±1%) dalam 7 hari kalender terakhir.
        recent_price = _recent_price_match(history, ticker, mode, entry,
                                           FRESH_MAX_AGE_DAYS)
        fresh = (not is_dup) and (not recent_price)
        ok = log_signal(csv_path, **s, fresh=fresh)
        if ok:
            written.append({
                "ticker": ticker, "mode": mode,
                "entry_price": entry,
                "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            })
        results.append({
            "ticker": ticker,
            "mode": mode,
            "fresh": fresh,
            "ref_date": ref_date,
            "logged": ok,
            "skipped": False,
        })
    return results


def log_signal_batch(csv_path, signals) -> int:
    """Log batch sinyal (dedup aktif). Return jumlah sinyal yang sukses tercatat."""
    return sum(1 for r in dedup_and_log_batch(csv_path, signals) if r["logged"])


def load_signals(csv_path) -> list:
    """Baca semua sinyal dari CSV. Return list of dict.

    Baris lama tanpa kolom 'fresh' dinormalisasi fresh=1 (sinyal baru).
    """
    if not os.path.exists(csv_path):
        return []
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if "fresh" not in row or row["fresh"] in (None, ""):
                row["fresh"] = "1"
        return rows
    except Exception as e:
        logger.warning("Gagal baca perf CSV: %s", e)
        return []


def weekly_stats(csv_path: str) -> str:
    """Ringkasan statistik sinyal (7 hari terakhir)."""
    signals = load_signals(csv_path)
    if not signals:
        return "Belum ada sinyal tercatat."

    # Filter 7 hari terakhir — L9: pakai _parse_date (2 format: '%Y-%m-%d %H:%M'
    # dan '%Y-%m-%d'), dulu cuma 1 format → baris berformat lain di-skip diam-diam.
    cutoff = datetime.now()
    recent = []
    for s in signals:
        dt = _parse_date(s.get("date"))
        if dt is None:
            continue
        if (cutoff - dt).days <= 7:
            recent.append(s)

    # L9: hanya sinyal BARU (fresh='1') yang dihitung — sinyal lanjutan
    # (fresh=0) adalah duplikat <14 hari, bukan sinyal baru; dulu ikut
    # dihitung sehingga statistik mingguan overstate.
    recent = [s for s in recent if str(s.get("fresh", "1")).strip() == "1"]

    if not recent:
        return f"7 hari terakhir: 0 sinyal (total {len(signals)} tercatat)."

    from collections import Counter
    by_mode = Counter(s["mode"] for s in recent)
    by_signal = Counter(s["signal"] for s in recent)
    lines = [
        f"📈 PERF TRACKER (7 hari)",
        f"Sinyal: {len(recent)} | Swing {by_mode.get('swing', 0)} | Intra {by_mode.get('intraday', 0)}",
        f"Breakdown: " + ", ".join(f"{k} {v}" for k, v in by_signal.most_common()),
    ]
    return "\n".join(lines)
