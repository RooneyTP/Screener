#!/usr/bin/env python3
"""
intraday_check.py — Cek pagi: harga buka vs rekomendasi entry (B4)
=================================================================
MASALAH:
  Sinyal v7 keluar 21:00 WIB (setelah market close), entry dilakukan
  besok pagi. Tidak ada info apakah harga buka pagi sesuai rekomendasi
  entry (entry_timing.py: limit order di support, jangan kejar) — user
  bisa missed entry atau kejebak gap up.

SOLUSI:
  Script pagi (dijadwalkan 08:45 WIB, Senin-Jumat) yang:
    1. Membaca sinyal swing FRESH TERAKHIR dari data/perf_tracker_v7.csv
       (per ticker: baris terbaru; filter mode=swing, fresh=1, usia <= 7 hari;
       sinyal intraday H+3 TIDAK dipakai — bukan untuk entry hari ini).
    2. Ambil harga BUKA terkini via Invezgo get_intraday().
    3. Bandingkan dengan entry_price (= harga close saat sinyal = upper
       bound range rekomendasi entry dari entry_timing) → klasifikasi pagi.
    4. Kirim SATU pesan ringkas berisi semua sinyal pagi ke Telegram.

KLASIFIKASI PAGI (gap% = open/entry_price - 1):
  gap < -2%            → GAP DOWN — waspada, cek SL
  open <= entry_price  → OK ENTRY — harga di dalam range rekomendasi
  (gap -2%..0)            (limit order di support seharusnya kena)
  0 < gap <= 3%        → KETAT — entry terbatas (masih bisa limit order tipis)
  gap > 3%             → GAP UP — SKIP, jangan kejar (harga sudah lari)

ANTI-SPAM:
  - Tidak ada sinyal swing fresh -> DIAM, exit 0, TIDAK kirim apa pun.
  - Satu ticker gagal fetch intraday -> dilewati, lanjut ke ticker lain.
  - Semua fetch gagal -> TIDAK kirim (pesan tanpa data tidak berguna).

CARA PAKAI:
  python intraday_check.py            # kirim alert ke Telegram
  python intraday_check.py --dry-run  # print saja, TIDAK kirim Telegram
  python intraday_check.py --csv PATH # sumber CSV lain (testing)

CATATAN:
  entry_price di CSV adalah harga close saat sinyal keluar. Rekomendasi
  entry_timing.recommend_entry() (default "Limit order") memberi range
  [price - 0.5*ATR, price] — ATR tidak tersimpan di CSV, jadi dipakai
  aproksimasi: range entry = harga <= entry_price (upper bound), dan
  gap turun kecil (0..-2%) masih dianggap OK karena limit order di
  support (0.5*ATR ≈ 1-3% harga) tetap berpeluang kena.
"""
import os
import sys
import argparse
import logging
from datetime import datetime, timedelta

# ── Path setup: ROOT (folder script) + parent (untuk utils/) ──
ROOT = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(ROOT)
for _p in (ROOT, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from perf_tracker import load_signals                              # noqa: E402
from data_invezgo import InvezgoProvider                           # noqa: E402
from utils.telegram_sender import send_telegram_sync               # noqa: E402

logger = logging.getLogger("intraday_check")

DEFAULT_CSV = os.path.join(ROOT, "data", "perf_tracker_v7.csv")

# ── Parameter klasifikasi pagi ──
MAX_SIGNAL_AGE_DAYS = 7    # sinyal > 7 hari dianggap basi (bukan untuk entry pagi ini)
GAP_TIGHT_PCT = 3.0        # gap naik 0-3% → KETAT; >3% → GAP UP
GAP_DOWN_PCT = -2.0        # gap turun >2% → GAP DOWN

LABELS = {
    "ok": "OK ENTRY — harga di range rekomendasi",
    "ketat": "KETAT — entry terbatas (limit order tipis)",
    "gapup": "GAP UP — SKIP, jangan kejar",
    "gapdown": "GAP DOWN — waspada, cek SL",
}
ICONS = {"ok": "🟢", "ketat": "🟠", "gapup": "🔴", "gapdown": "🔵"}

DATE_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")


def setup_logging():
    """Log ke file (jejak cron headless) + console WARNING."""
    logging.basicConfig(level=logging.WARNING)
    try:
        log_path = os.path.join(ROOT, "data", "intraday_check.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass  # file logging gagal — jangan crash


def parse_date(value):
    """Parse tanggal CSV. Return datetime atau None."""
    if not value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def load_candidates(csv_path, now=None, max_age_days=MAX_SIGNAL_AGE_DAYS) -> list:
    """Baca sinyal swing fresh TERAKHIR dari perf CSV.

    Per ticker diambil baris dengan tanggal TERBARU yang memenuhi:
      - mode == 'swing'          (sinyal intraday H+3 bukan untuk entry pagi ini)
      - fresh == '1'             (sinyal baru; baris tanpa kolom fresh dianggap 1)
      - usia <= max_age_days     (default 7 hari; lebih tua = basi/skip)

    Return list of dict (sorted tanggal desc): ticker, date, dt,
    entry_price, sl, tp, signal, score.
    """
    now = now or datetime.now()
    rows = load_signals(csv_path)          # reuse perf_tracker (normalisasi fresh)
    best = {}                              # ticker -> (dt, row)
    for row in rows:
        if str(row.get("mode", "")).strip().lower() != "swing":
            continue
        if str(row.get("fresh", "1")).strip() != "1":
            continue
        dt = parse_date(row.get("date"))
        if dt is None:
            continue
        if now - dt > timedelta(days=max_age_days):
            continue
        try:
            entry_price = float(row.get("entry_price", 0) or 0)
        except (TypeError, ValueError):
            continue
        if entry_price <= 0:
            continue
        ticker = str(row.get("ticker", "")).strip().upper()
        if not ticker:
            continue
        if ticker not in best or dt > best[ticker][0]:
            best[ticker] = (dt, row)

    candidates = []
    for ticker, (dt, row) in best.items():
        def _f(key, default=0.0):
            try:
                return float(row.get(key, default) or default)
            except (TypeError, ValueError):
                return default
        candidates.append({
            "ticker": ticker,
            "date": str(row.get("date", "")),
            "dt": dt,
            "entry_price": _f("entry_price"),
            "sl": _f("sl"),
            "tp": _f("tp"),
            "signal": str(row.get("signal", "")),
            "score": str(row.get("score", "")),
        })
    candidates.sort(key=lambda c: c["dt"], reverse=True)
    return candidates


def classify_morning(entry_price: float, open_price: float):
    """Klasifikasi pagi berdasarkan harga buka vs entry_price.

    Return (label_key, gap_pct). Mutually exclusive, urutan cek:
      gap < -2%            → gapdown
      open <= entry_price  → ok        (termasuk gap -2%..0)
      0 < gap <= 3%        → ketat
      gap > 3%             → gapup
    """
    gap_pct = round((open_price / entry_price - 1.0) * 100.0, 4)  # round: hindari float error di boundary (mis. -2.0000000000000018)
    if gap_pct < GAP_DOWN_PCT:
        return "gapdown", gap_pct
    if open_price <= entry_price:
        return "ok", gap_pct
    if gap_pct <= GAP_TIGHT_PCT:
        return "ketat", gap_pct
    return "gapup", gap_pct


def fetch_open_price(provider, ticker):
    """Ambil harga BUKA terkini via Invezgo. Return float atau None.

    Fallback: kalau 'open' kosong (0), pakai 'price' lalu 'close' — sama
    dengan position_check_intraday.py (SDK Invezgo kadang mengisi price=0
    di luar jam aktif).
    """
    try:
        data = provider.get_intraday(ticker)
        if not data:
            return None
        open_p = float(data.get("open") or 0)
        if open_p > 0:
            return open_p
        price = float(data.get("price") or 0)
        if price > 0:
            return price
        close = float(data.get("close") or 0)
        if close > 0:
            return close
    except Exception as e:
        logger.debug("get_intraday gagal %s: %s", ticker, e)
    return None


def format_message(results: list, now_str: str) -> str:
    """Satu pesan ringkas berisi semua sinyal pagi (hasil klasifikasi)."""
    lines = [f"🌅 CEK ENTRY PAGI ({now_str} WIB)", "─" * 28]
    for r in results:
        icon = ICONS.get(r["label_key"], "•")
        lines.append(f"{icon} {r['ticker']} | {LABELS.get(r['label_key'], r['label_key'])}")
        lines.append(f"   Open {r['open_price']:,.0f} vs entry {r['entry_price']:,.0f} "
                     f"({r['gap_pct']:+.1f}%) | SL {r['sl']:,.0f}")
    return "\n".join(lines)


def run_check(csv_path, fetch_open, dry_run=False, now=None) -> int:
    """Inti script — testable: fetch_open di-inject (callable ticker -> float|None).

    Return exit code. TIDAK pernah raise (error per-ticker dilewati).
    """
    now = now or datetime.now()
    now_str = now.strftime("%d/%m %H:%M")

    candidates = load_candidates(csv_path, now=now)
    if not candidates:
        print("Tidak ada sinyal swing fresh untuk entry pagi — DIAM (tidak kirim apa pun)")
        return 0
    print(f"Sinyal swing fresh: {len(candidates)} — {', '.join(c['ticker'] for c in candidates)}")

    # ── Ambil harga buka per ticker; satu gagal → lewati, lanjut ──
    results = []
    for cand in candidates:
        ticker = cand["ticker"]
        try:
            open_price = fetch_open(ticker)
        except Exception as e:
            logger.debug("fetch gagal %s: %s", ticker, e)
            open_price = None
        if not open_price or open_price <= 0:
            print(f"  ⚠️  {ticker}: harga intraday tidak tersedia — dilewati")
            continue
        key, gap = classify_morning(cand["entry_price"], open_price)
        results.append({**cand, "open_price": open_price, "label_key": key, "gap_pct": gap})
        print(f"  {ICONS[key]} {ticker}: open {open_price:,.0f} vs entry {cand['entry_price']:,.0f} "
              f"({gap:+.1f}%) → {LABELS[key]}")

    if not results:
        print("Semua harga intraday gagal diambil — tidak kirim Telegram (anti-spam)")
        return 0

    message = format_message(results, now_str)
    print("\n" + message)

    if dry_run:
        print("\n[DRY-RUN] Pesan di atas TIDAK dikirim ke Telegram.")
        return 0

    ok = send_telegram_sync(message)
    if ok:
        print(f"✅ Telegram terkirim ({len(results)} sinyal)")
        return 0
    print("❌ Gagal kirim Telegram")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cek pagi 08:45: harga buka vs rekomendasi entry (sinyal swing fresh "
                    "dari perf_tracker_v7.csv + harga intraday Invezgo). "
                    "Kirim 1 pesan klasifikasi ke Telegram. Tidak ada sinyal -> DIAM."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print saja, TIDAK kirim Telegram (default: kirim).")
    parser.add_argument("--csv", default=DEFAULT_CSV,
                        help=f"Path CSV sinyal (default: {DEFAULT_CSV}).")
    args = parser.parse_args()

    setup_logging()
    dry = args.dry_run

    if not os.path.exists(args.csv):
        print(f"CSV tidak ditemukan: {args.csv} — DIAM (tidak kirim apa pun)")
        return 0

    # Pre-check kandidat: kalau tidak ada, TIDAK perlu init provider (anti-spam, hemat API)
    if not load_candidates(args.csv):
        print("Tidak ada sinyal swing fresh untuk entry pagi — DIAM (tidak kirim apa pun)")
        return 0

    try:
        provider = InvezgoProvider()
    except Exception as e:
        print(f"❌ Gagal init InvezgoProvider: {e}")
        logger.error("Init provider gagal: %s", e)
        return 1

    return run_check(args.csv, lambda t: fetch_open_price(provider, t), dry_run=dry)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        logger.error("FATAL: %s", e, exc_info=True)
        sys.exit(1)
