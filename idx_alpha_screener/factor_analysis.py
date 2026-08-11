"""
factor_analysis.py — Analisis faktor pemenang sinyal V7 (A2)
=============================================================
Membaca data/perf_tracker_v7.csv (sinyal) + data/evaluations_v7.csv
(hasil evaluasi WIN_TP / LOSS_SL, dihasilkan weekly_report.py), lalu
pivot win rate per faktor:

  - mode             : swing / intraday
  - score band       : >=65 / 55-64 / <55
  - grup konglomerat : Barito / Sinar Mas / Bakrie / Salim / Kalbe / dll.
                       (mapping SINGLE SOURCE dari config.yaml section groups)
  - broker_detail    : detail faktor broker flow Invezgo (jika tercatat)
  - foreign_detail   : detail faktor foreign flow Invezgo (jika tercatat)
  - fundamental_detail: detail faktor fundamental Invezgo (jika tercatat)
  - regime           : market regime saat sinyal (jika tercatat)
  - Faktor DNA (IDE1) — kolom baru di perf_tracker_v7.csv, otomatis terbaca
    kalau TERCATAT (baris lama di-backfill 'unknown' → dianggap tidak ada):
      broker_flow / foreign_flow / fundamental / earnings_momentum (band
      0-100: >=65 / 55-64 / <55), weekly_trend, atr_pct (band <1.5 / 1.5-5 /
      >5), vol_ratio (band <1 / 1-2 / >=2), event (corporate action).

CATATAN JUJUR TENTANG DATA:
  Kolom broker_detail / foreign_detail / fundamental_detail / regime
  SAAT INI TIDAK dicatat di perf_tracker_v7.csv maupun evaluations_v7.csv
  (lihat perf_tracker.FIELDS & weekly_report.FIELDS). Analisis faktor-faktor
  itu hanya jalan kalau kolomnya ADA di CSV; kalau tidak ada, script ini
  bilang "tidak tersedia" — TIDAK mengarang angka. Rekomendasi logging ada
  di output.

Aturan sampel (A2): WR per grup TIDAK boleh disimpulkan kalau n < 30.
Peringatan kuat: "sampel terlalu kecil — belum bisa disimpulkan".

CLI:
  python factor_analysis.py                 # CSV default di data/
  python factor_analysis.py --csv path.csv  # CSV sinyal kustom
  python factor_analysis.py --eval path.csv # CSV evaluasi kustom
  python factor_analysis.py --min-sample N  # ubah batas minimal sampel
"""
import argparse
import csv
import os
import sys

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_CSV = os.path.join(DATA_DIR, "perf_tracker_v7.csv")
DEFAULT_EVAL = os.path.join(DATA_DIR, "evaluations_v7.csv")

MIN_SAMPLE = 30  # A2: n < 30 → belum bisa disimpulkan

# Mapping grup konglomerat — SINGLE SOURCE: config.yaml section 'groups'
# (dibaca via groups_config.load_groups(); dulu hardcode GROUP_NAMES di sini
# + duplikat di v7_scan.py & weekly_report.py = 4 sumber drift).
# Ticker di luar mapping dikategorikan "lainnya".
from groups_config import load_groups

GROUP_NAMES = load_groups()  # {TICKER: nama_grup} — fallback {} kalau config gagal

CLOSED_STATUSES = ("WIN_TP", "LOSS_SL")

# IDE1: kolom faktor DNA baru — dipakai presence report + pivot WR
FACTOR_COLUMNS = ["broker_detail", "foreign_detail", "fundamental_detail", "regime",
                  "broker_flow", "foreign_flow", "fundamental", "earnings_momentum",
                  "weekly_trend", "atr_pct", "vol_ratio", "event"]

# Nilai backfill migrasi CSV lama — dianggap TIDAK tercatat (jujur)
UNKNOWN_VALUES = (None, "", "unknown")


def factor_band(value) -> str:
    """Banding faktor numerik 0-100: >=65 / 55-64 / <55; non-angka → 'unknown'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if v >= 65:
        return ">=65"
    if v >= 55:
        return "55-64"
    return "<55"


def atr_pct_band(value) -> str:
    """Banding atr_pct (volatilitas): <1.5 / 1.5-5 / >5 — selaras threshold
    position_sizing (atr_pct > 5 → alokasi setengah, < 1.5 → lebih berani)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if v < 1.5:
        return "<1.5"
    if v <= 5.0:
        return "1.5-5"
    return ">5"


def vol_ratio_band(value) -> str:
    """Banding vol_ratio: <1 / 1-2 / >=2."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if v < 1:
        return "<1"
    if v < 2:
        return "1-2"
    return ">=2"


def group_of(ticker: str) -> str:
    """Label grup konglomerat; '' kalau tidak dikenal (dihitung 'lainnya')."""
    return GROUP_NAMES.get(str(ticker).strip().upper(), "")


def load_csv(path: str) -> list:
    """Baca CSV apa pun → list dict dengan key lowercase (case-insensitive)."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            raw = list(csv.DictReader(f))
        rows = []
        for r in raw:
            rows.append({str(k).strip().lower(): v for k, v in r.items()})
        return rows
    except Exception as e:
        print(f"⚠️ Gagal baca {path}: {e}", file=sys.stderr)
        return []


def merge_signals(perf_rows: list, eval_rows: list) -> list:
    """Gabung sinyal + hasil evaluasi via key (date, ticker, mode).

    Status default sinyal tanpa evaluasi = 'BELUM_DIEVALUASI' (bukan OPEN —
    beda arti: OPEN = dievaluasi tapi belum selesai; BELUM_DIEVALUASI = belum
    cukup umur / belum diproses weekly_report).
    """
    eval_by_key = {}
    for e in eval_rows:
        key = (e.get("date", ""), e.get("ticker", ""), e.get("mode", ""))
        eval_by_key[key] = e  # baris terakhir menang (unik per key di praktiknya)

    merged = []
    for s in perf_rows:
        key = (s.get("date", ""), s.get("ticker", ""), s.get("mode", ""))
        row = dict(s)
        ev = eval_by_key.get(key)
        if ev is not None:
            row.update(ev)
            row["status"] = ev.get("status", "OPEN")
        else:
            row["status"] = "BELUM_DIEVALUASI"
        merged.append(row)
    return merged


def score_band(score) -> str:
    """Kategorisasi score: >=65 / 55-64 / <55."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "(tanpa score)"
    if s >= 65:
        return ">=65"
    if s >= 55:
        return "55-64"
    return "<55"


def wr_stats(rows: list) -> dict:
    """Hitung n, n_closed, win, loss, wr% dari daftar baris."""
    closed = [r for r in rows if r.get("status") in CLOSED_STATUSES]
    wins = sum(1 for r in closed if r.get("status") == "WIN_TP")
    n = len(rows)
    n_closed = len(closed)
    wr = wins / n_closed * 100 if n_closed else None
    return {"n": n, "n_closed": n_closed, "wins": wins,
            "losses": n_closed - wins, "wr": wr}


def wr_table(rows: list, key_fn, label: str, min_sample: int = MIN_SAMPLE) -> list:
    """Pivot WR per grup → list baris markdown + peringatan sampel kecil.

    Peringatan KUAT (A2): n_closed < min_sample → "sampel terlalu kecil —
    belum bisa disimpulkan". n=0 → "belum ada sinyal".
    """
    groups = {}
    for r in rows:
        k = key_fn(r) or "(tanpa nilai)"
        groups.setdefault(k, []).append(r)

    lines = [f"\n### WR per {label}", "", "| Faktor | n sinyal | n selesai | WIN | LOSS | WR |", "|---|---|---|---|---|---|"]
    for k in sorted(groups):
        st = wr_stats(groups[k])
        wr_txt = f"{st['wr']:.0f}%" if st["wr"] is not None else "—"
        lines.append(f"| {k} | {st['n']} | {st['n_closed']} | {st['wins']} | {st['losses']} | {wr_txt} |")
        if st["n_closed"] == 0:
            lines.append(f"  ⚠️ **{k}**: belum ada sinyal yang selesai dievaluasi (n selesai = 0).")
        elif st["n_closed"] < min_sample:
            deficit = min_sample - st["n_closed"]
            lines.append(f"  ⚠️ **{k}**: sampel terlalu kecil — belum bisa disimpulkan "
                         f"(n selesai {st['n_closed']} < {min_sample}; butuh {deficit} lagi).")
    return lines


def sample_projection(rows: list, min_sample: int = MIN_SAMPLE,
                      weekly_rate: tuple = (5, 10)) -> str:
    """Estimasi jujur kapan cukup sampel, asumsi X sinyal selesai/minggu."""
    n_closed = sum(1 for r in rows if r.get("status") in CLOSED_STATUSES)
    deficit = max(0, min_sample - n_closed)
    if deficit == 0:
        return f"✅ Total sinyal selesai {n_closed} ≥ {min_sample} — cukup untuk kesimpulan agregat."
    lo, hi = weekly_rate
    weeks_lo = max(1, -(-deficit // hi))  # ceiling division
    weeks_hi = max(1, -(-deficit // lo))
    span = f"{weeks_lo}-{weeks_hi}" if weeks_lo != weeks_hi else str(weeks_lo)
    return (f"📊 Proyeksi sampel: butuh {deficit} sinyal selesai lagi untuk n≥{min_sample} "
            f"→ estimasi {span} minggu (asumsi {lo}-{hi} sinyal selesai/minggu). "
            f"Catatan: per grup (mode/band/grup konglomerat) butuh 30 sampel SENDIRI — "
            f"jauh lebih lama.")


def report_columns_presence(rows: list) -> list:
    """Cek kolom faktor mana yang TERCATAT di data. Jujur kalau tidak ada.

    Nilai backfill migrasi ('unknown' / '') TIDAK dihitung sebagai tercatat —
    hanya nilai nyata yang masuk (IDE1).
    """
    lines = []
    present = set()
    for r in rows:
        for c in FACTOR_COLUMNS:
            if r.get(c) not in UNKNOWN_VALUES:
                present.add(c)
    lines.append("\n### Ketersediaan kolom faktor")
    lines.append("")
    lines.append("| Faktor | Tercatat di CSV? |", )
    lines.append("|---|---|")
    for c in FACTOR_COLUMNS:
        ok = c in present
        lines.append(f"| {c} | {'✅ ya' if ok else '❌ tidak ada / kosong'} |")
    missing = [c for c in FACTOR_COLUMNS if c not in present]
    if missing:
        lines.append("")
        lines.append("⚠️ Kolom berikut **tidak tercatat** di perf_tracker_v7.csv / evaluations_v7.csv "
                     "(lihat `perf_tracker.FIELDS` & `weekly_report.FIELDS`): "
                     + ", ".join(missing) + ".")
        lines.append("   → WR per faktor tersebut BELUM bisa dihitung. Rekomendasi: tambahkan kolom "
                     "`regime` (dari v7_scan) dan detail faktor Invezgo saat logging sinyal, "
                     "supaya analisis ini bisa jalan ~4-8 minggu lagi.")
    return lines


def main():
    ap = argparse.ArgumentParser(description="Analisis faktor pemenang sinyal V7")
    ap.add_argument("--csv", default=DEFAULT_CSV, help="Path CSV sinyal (default: data/perf_tracker_v7.csv)")
    ap.add_argument("--eval", default=DEFAULT_EVAL, help="Path CSV evaluasi (default: data/evaluations_v7.csv)")
    ap.add_argument("--min-sample", type=int, default=MIN_SAMPLE, help=f"Batas minimal sampel (default {MIN_SAMPLE})")
    args = ap.parse_args()

    perf = load_csv(args.csv)
    evals = load_csv(args.eval)
    if not perf:
        print(f"❌ Tidak ada sinyal di {args.csv} — cek path dengan --csv.")
        sys.exit(1)

    rows = merge_signals(perf, evals)
    n_closed = sum(1 for r in rows if r.get("status") in CLOSED_STATUSES)

    out = []
    out.append("# ANALISIS FAKTOR PEMENANG — SINYAL V7")
    out.append("")
    out.append(f"- Sumber sinyal : {args.csv}")
    out.append(f"- Sumber evaluasi: {args.eval} ({'ADA' if evals else 'BELUM ADA — WR belum bisa dihitung'})")
    out.append(f"- Total sinyal   : {len(rows)}")
    out.append(f"- Selesai        : {n_closed} (WIN_TP/LOSS_SL)")
    out.append(f"- Belum dievaluasi: {sum(1 for r in rows if r.get('status') == 'BELUM_DIEVALUASI')}")
    out.append(f"- Masih OPEN     : {sum(1 for r in rows if r.get('status') == 'OPEN')}")
    out.append("")

    st = wr_stats(rows)
    if st["wr"] is not None:
        out.append(f"**WR agregat: {st['wr']:.0f}%** ({st['wins']}W/{st['losses']}L dari {st['n_closed']} selesai)")
    else:
        out.append("**WR agregat: belum bisa dihitung** (0 sinyal selesai)")

    out += wr_table(rows, lambda r: r.get("mode", ""), "mode", args.min_sample)
    out += wr_table(rows, lambda r: score_band(r.get("score")), "score band", args.min_sample)
    out += wr_table(rows, lambda r: group_of(r.get("ticker", "")) or "lainnya",
                    "grup konglomerat", args.min_sample)

    # Faktor Invezgo + regime: hanya kalau kolomnya ADA (lihat catatan di docstring)
    for col in ("broker_detail", "foreign_detail", "fundamental_detail", "regime"):
        if any(r.get(col) not in UNKNOWN_VALUES for r in rows):
            out += wr_table(rows, lambda r, c=col: r.get(c), col, args.min_sample)

    # ── Faktor DNA (IDE1): kolom baru di perf_tracker_v7.csv — pivot WR
    # otomatis muncul kalau kolomnya TERCATAT (baris lama 'unknown' di-skip).
    for col in ("broker_flow", "foreign_flow", "fundamental", "earnings_momentum"):
        if any(r.get(col) not in UNKNOWN_VALUES for r in rows):
            out += wr_table(rows, lambda r, c=col: factor_band(r.get(c)),
                            f"{col} (band 0-100)", args.min_sample)
    for col in ("weekly_trend", "event"):
        if any(r.get(col) not in UNKNOWN_VALUES for r in rows):
            out += wr_table(rows, lambda r, c=col: r.get(c) or "(kosong)",
                            col, args.min_sample)
    for col, band_fn in (("atr_pct", atr_pct_band), ("vol_ratio", vol_ratio_band)):
        if any(r.get(col) not in UNKNOWN_VALUES for r in rows):
            out += wr_table(rows, lambda r, c=col, f=band_fn: f(r.get(c)),
                            col, args.min_sample)

    out += report_columns_presence(rows)
    out.append("")
    out.append(sample_projection(rows, args.min_sample))
    out.append("")
    out.append("---")
    out.append("*Semua angka di atas adalah hasil nyata dari CSV — tidak ada estimasi yang diarang.*")

    print("\n".join(out))


if __name__ == "__main__":
    main()
