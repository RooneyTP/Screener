"""
perf_tracker.py — Signal Performance Logger untuk V7
====================================================
Mencatat setiap sinyal yang dikeluarkan V7 ke CSV.
Fungsinya: mengukur Win Rate asli V7 secara forward (karena
backtest V7 tidak mungkin — broker flow tidak punya history).

CSV: data/perf_tracker_v7.csv
Kolom: date, ticker, mode, score, signal, entry_price, sl, tp, lots, cost, fresh, regime
  - fresh: 1 = sinyal baru, 0 = lanjutan (duplikat sinyal lama <14 hari, ±1% harga)
  - regime: regime market saat sinyal dikeluarkan (BULL/BEAR/HIGH_VOLATILITY/RANGING)
    dari detect_market_regime di v7_scan; baris lama (CSV migrasi) di-backfill 'unknown'.
    Dipakai weekly_report (E2) untuk tabel WR per regime market.

DEDUP PERSISTEN (A1):
Sebelum menulis sinyal baru, riwayat CSV dicek. Jika ticker + mode yang sama
sudah ada dengan entry_price dalam toleransi ±1% DAN usia sinyal < 14 hari,
sinyal TIDAK di-log sebagai sinyal baru — dicatat dengan fresh=0 agar bisa
dilabel '(lanjutan)' di Telegram. Logika cooldown (signal_manager) tetap jalan
sebagai lapisan terpisah; dedup ini lapisan tambahan di sisi logging, bukan
pengganti cooldown.
"""
import os
import csv
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("perf_tracker")

DEFAULT_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "perf_tracker_v7.csv")
FIELDS = ["date", "ticker", "mode", "score", "signal", "entry_price", "sl", "tp", "lots", "cost", "fresh", "regime"]

# Nilai default saat migrasi CSV lama (kolom ditambahkan ke header + baris lama di-backfill)
FIELD_DEFAULTS = {"fresh": "1", "regime": "unknown"}

# ── Parameter dedup ──
DEDUP_TOLERANCE = 0.01      # toleransi entry_price ±1%
DEDUP_MAX_AGE_DAYS = 14     # sinyal lama < 14 hari dianggap masih "sama"

DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")


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
                         rows=None):
    """Cari sinyal lama yang dianggap sama dengan sinyal baru.

    Kriteria match:
      - ticker + mode sama
      - entry_price dalam toleransi ±tolerance (default ±1%)
      - usia sinyal < max_age_days (default 14 hari)

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
        if abs(entry_price - old_price) / old_price > tolerance:
            continue
        dt = _parse_date(row.get("date"))
        if dt is None:
            continue
        if now - dt >= timedelta(days=max_age_days):
            continue
        if best_dt is None or dt > best_dt:
            best_dt = dt

    if best_dt is None:
        return False, None
    return True, best_dt.strftime("%d/%m")


def log_signal(csv_path, ticker, mode, score, signal, entry_price, sl, tp,
               lots, cost, fresh=True, regime="unknown") -> bool:
    """Log satu sinyal ke CSV. Return True jika sukses.

    fresh=True  → sinyal baru (fresh=1 di CSV)
    fresh=False → lanjutan/duplikat (fresh=0 di CSV, dilabel '(lanjutan)' di Telegram)
    regime      → regime market saat sinyal (BULL/BEAR/HIGH_VOLATILITY/RANGING),
                  default 'unknown' untuk pemanggil lama (kompatibel).
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
        }
        new_file = not os.path.exists(csv_path)
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


def dedup_and_log_batch(csv_path, signals) -> list:
    """Log batch sinyal DENGAN dedup persistén (lapisan tambahan di atas cooldown).

    Untuk tiap sinyal:
      1. Cek riwayat CSV (find_previous_signal): ticker+mode sama, entry_price ±1%,
         usia < 14 hari → fresh=False (lanjutan), kalau tidak → fresh=True.
      2. Tetap tulis baris ke CSV (sinyal tetap tercatat, tapi ditandai fresh).

    Return list of dict per sinyal:
      {ticker, mode, fresh (bool), ref_date (str|None), logged (bool)}
    — dipakai v7_scan untuk memberi label '(lanjutan - sinyal dd/mm)' di Telegram.
    """
    results = []
    history = load_signals(csv_path)  # snapshot riwayat SEBELUM batch (baris batch baru tidak ikut dibandingkan)
    for s in signals:
        is_dup, ref_date = find_previous_signal(
            csv_path, s.get("ticker"), s.get("mode"), s.get("entry_price"),
            rows=history)
        fresh = not is_dup  # True = sinyal baru, False = lanjutan/duplikat
        ok = log_signal(csv_path, **s, fresh=fresh)
        results.append({
            "ticker": s.get("ticker"),
            "mode": s.get("mode"),
            "fresh": fresh,
            "ref_date": ref_date,
            "logged": ok,
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

    # Filter 7 hari terakhir
    cutoff = datetime.now()
    recent = []
    for s in signals:
        try:
            dt = datetime.strptime(s["date"], "%Y-%m-%d %H:%M")
            if (cutoff - dt).days <= 7:
                recent.append(s)
        except (ValueError, KeyError):
            continue

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
