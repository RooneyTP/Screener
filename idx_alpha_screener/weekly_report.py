"""
weekly_report.py — Auto-evaluasi sinyal & laporan WR V7 (MFE/MAE)
=================================================================
Membaca perf_tracker_v7.csv, mengevaluasi sinyal yang sudah cukup umur
(swing >= 10 hari, intraday >= 3 hari) pakai HIGH/LOW sejak entry:
- max(high) sejak entry >= TP  → WIN_TP (take profit tersentuh)
- min(low)  sejak entry <= SL  → LOSS_SL (stop loss tersentuh)
- Keduanya kena → yang pertama kena secara urutan baris yang menang
- Lainnya      → OPEN (belum selesai)
Tambahan: mfe_pct = (max_high-entry)/entry*100, mae_pct = (min_low-entry)/entry*100.
Jika data OHLC gagal diambil → status DATA_MISSING (tidak di-mark, dicoba lagi
run berikutnya).

Hasil disimpan di data/evaluations_v7.csv + kirim ringkasan ke Telegram.

Cara pakai:
  python weekly_report.py            # evaluasi + kirim laporan
  python weekly_report.py --no-send  # evaluasi saja, tanpa kirim Telegram
  python weekly_report.py --roi      # paksa section ROI Invezgo (E3)
"""
import sys, os, json, csv, argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd

from data_invezgo import InvezgoProvider
from perf_tracker import load_signals

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PERF_CSV = os.path.join(DATA_DIR, "perf_tracker_v7.csv")
EVAL_CSV = os.path.join(DATA_DIR, "evaluations_v7.csv")
EVAL_MARK = os.path.join(DATA_DIR, "evaluated_keys.json")
FIELDS = ["date", "ticker", "mode", "score", "signal", "entry_price", "sl", "tp",
          "lots", "cost", "status", "close_price", "return_pct", "mfe_pct", "mae_pct",
          "eval_date"]

# ── E2: mapping grup konglomerat — konsisten dengan v7_scan.py GROUP_NAMES ──
GROUP_NAMES = {
    "BRPT": "Barito", "DSSA": "Barito", "BUMI": "Barito", "ENRG": "Barito",
    "BNBR": "Bakrie", "VBID": "Bakrie", "ELTY": "Bakrie",
    "INDF": "Salim", "ICBP": "Salim", "KLBF": "Salim", "HMSP": "Salim", "BISI": "Salim",
    "ASII": "Astra", "UNTR": "Astra", "AKRA": "Astra", "CPIN": "Astra", "ISAT": "Astra",
}

# E2: peringatan sampel kecil di tabel breakdown
MIN_SAMPLE_WARN = 20
# E3: biaya langganan Invezgo & asumsi fee round-trip
INVEZGO_SUBSCRIPTION = 500_000
FEE_ROUND_TRIP = 0.004  # 0.4%

# ── Telegram (kredensial dari .env root repo — JANGAN hardcode token di source) ──
import json, urllib.request, urllib.parse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5237365204")
if not BOT_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN tidak ditemukan di .env — hentikan.", file=sys.stderr)
    sys.exit(1)


def send_telegram(text: str) -> bool:
    try:
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=data, method="POST")
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read()).get("ok", False)
    except Exception:
        return False


def _load_marked() -> set:
    if os.path.exists(EVAL_MARK):
        try:
            with open(EVAL_MARK, encoding="utf-8", errors="replace") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def _save_marked(marked: set):
    with open(EVAL_MARK, "w", encoding="utf-8") as f:
        json.dump(sorted(marked), f)


def _append_eval(row: dict):
    new_file = not os.path.exists(EVAL_CSV)
    with open(EVAL_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def _pick_col(df, name: str):
    """Ambil kolom dengan nama case-insensitive (high/High/High)."""
    if name in df.columns:
        return df[name]
    for c in df.columns:
        if str(c).lower() == name:
            return df[c]
    raise KeyError(f"Kolom '{name}' tidak ada di data OHLC")


def _period_for_age(age_days: int) -> str:
    """Pilih periode fetch agar data mencakup tanggal entry."""
    if age_days <= 30:
        return "1mo"
    if age_days <= 90:
        return "3mo"
    if age_days <= 180:
        return "6mo"
    if age_days <= 365:
        return "1y"
    return "2y"  # batas maksimal Invezgo


def classify_ohlc(df, entry_price: float, sl: float, tp: float) -> dict:
    """Klasifikasi status sinyal pakai HIGH/LOW sejak entry (MFE/MAE).

    Pure function — mudah diuji dengan df OHLC sintetis.
    Urutan pengecekan: baris pertama yang kena (TP vs SL) yang menang;
    jika keduanya kena di baris yang sama (data harian tidak bisa lihat
    urutan intraday) → dihitung konservatif sebagai LOSS_SL.

    Returns dict:
        status    : "WIN_TP" | "LOSS_SL" | "OPEN"
        max_high  : high tertinggi sejak entry
        min_low   : low terendah sejak entry
        mfe_pct   : (max_high - entry_price) / entry_price * 100
        mae_pct   : (min_low - entry_price) / entry_price * 100
        first_hit : "TP" | "SL" | "" (mana yang kena duluan secara baris)
    """
    empty = {"status": "OPEN", "max_high": None, "min_low": None,
             "mfe_pct": 0.0, "mae_pct": 0.0, "first_hit": ""}
    if df is None or df.empty:
        return empty
    try:
        highs = _pick_col(df, "high").astype(float)
        lows = _pick_col(df, "low").astype(float)
    except Exception:
        return empty
    if highs.empty or lows.empty:
        return empty

    max_high = float(highs.max())
    min_low = float(lows.min())
    entry = float(entry_price)
    mfe_pct = (max_high - entry) / entry * 100
    mae_pct = (min_low - entry) / entry * 100

    tp_idx = sl_idx = None
    for i, (h, l) in enumerate(zip(highs.tolist(), lows.tolist())):
        if tp_idx is None and h >= tp:
            tp_idx = i
        if sl_idx is None and l <= sl:
            sl_idx = i
        if tp_idx is not None and sl_idx is not None:
            break

    if tp_idx is None and sl_idx is None:
        return {"status": "OPEN", "max_high": max_high, "min_low": min_low,
                "mfe_pct": mfe_pct, "mae_pct": mae_pct, "first_hit": ""}
    if tp_idx is None:
        status, first = "LOSS_SL", "SL"
    elif sl_idx is None:
        status, first = "WIN_TP", "TP"
    elif tp_idx < sl_idx:
        status, first = "WIN_TP", "TP"
    elif sl_idx < tp_idx:
        status, first = "LOSS_SL", "SL"
    else:
        # Kena di baris yang sama (hari yang sama): urutan intraday tidak
        # diketahui dari data harian → hitung konservatif (SL dulu).
        status, first = "LOSS_SL", "SL"
    return {"status": status, "max_high": max_high, "min_low": min_low,
            "mfe_pct": mfe_pct, "mae_pct": mae_pct, "first_hit": first}


def _data_missing_row(s: dict, mode: str, today: datetime) -> dict:
    """Row penanda sinyal yang gagal diambil data OHLC-nya."""
    return {
        "date": s["date"], "ticker": s["ticker"], "mode": mode,
        "score": s.get("score", ""), "signal": s.get("signal", ""),
        "entry_price": float(s["entry_price"]), "sl": float(s["sl"]),
        "tp": float(s["tp"]),
        "lots": s.get("lots", ""), "cost": s.get("cost", ""),
        "status": "DATA_MISSING", "close_price": "",
        "return_pct": "", "mfe_pct": "", "mae_pct": "",
        "eval_date": today.strftime("%Y-%m-%d"),
    }


def evaluate_signals(provider=None) -> list:
    """Evaluasi sinyal yang belum dievaluasi & sudah cukup umur. Return list hasil."""
    signals = load_signals(PERF_CSV)
    if not signals:
        print("Tidak ada sinyal di perf_tracker.")
        return []

    if provider is None:
        provider = InvezgoProvider()

    marked = _load_marked()
    today = datetime.now()
    results = []
    new_marks = []

    for s in signals:
        try:
            dt = datetime.strptime(s["date"], "%Y-%m-%d %H:%M")
            mode = s.get("mode", "swing")
            min_age = 10 if mode == "swing" else 3
            age_days = (today - dt).days
            if age_days < min_age:
                continue  # belum cukup umur

            key = f"{s['date']}|{s['ticker']}|{mode}"
            if key in marked:
                continue  # sudah dievaluasi

            ticker = s["ticker"]
            entry = float(s["entry_price"])
            sl = float(s["sl"])
            tp = float(s["tp"])

            # ── Ambil OHLC sejak entry (bukan cuma close terakhir) ──
            try:
                df = provider.get_historical(ticker, period=_period_for_age(age_days),
                                             use_cache=True)
            except Exception:
                df = None
            if df is None or df.empty:
                # Data gagal diambil (network/rate limit) → tandai, jangan crash.
                # Tidak di-append ke CSV & tidak di-mark → dicoba lagi run berikutnya.
                results.append(_data_missing_row(s, mode, today))
                continue

            # Baris sejak tanggal entry (termasuk baris hari entry itu sendiri)
            if isinstance(df.index, pd.DatetimeIndex):
                since = df[df.index >= pd.Timestamp(dt)]
            else:
                since = df
            if since is None or since.empty:
                results.append(_data_missing_row(s, mode, today))
                continue

            # Klasifikasi MFE/MAE pakai high/low sejak entry
            res = classify_ohlc(since, entry, sl, tp)
            status = res["status"]
            close = float(_pick_col(df, "close").iloc[-1])
            ret_pct = (close - entry) / entry * 100
            row = {
                "date": s["date"], "ticker": ticker, "mode": mode,
                "score": s.get("score", ""), "signal": s.get("signal", ""),
                "entry_price": entry, "sl": sl, "tp": tp,
                "lots": s.get("lots", ""), "cost": s.get("cost", ""),
                "status": status, "close_price": round(close, 2),
                "return_pct": round(ret_pct, 2),
                "mfe_pct": round(res["mfe_pct"], 2),
                "mae_pct": round(res["mae_pct"], 2),
                "eval_date": today.strftime("%Y-%m-%d"),
            }
            _append_eval(row)
            results.append(row)
            new_marks.append(key)
        except (ValueError, KeyError, TypeError) as e:
            continue

    if new_marks:
        marked.update(new_marks)
        _save_marked(marked)
    return results


# ═══════════════════════════════════════════════════════════════
#  E2+E3: breakdown WR per faktor & ROI Invezgo
# ═══════════════════════════════════════════════════════════════

def group_of(ticker: str) -> str:
    """Label grup konglomerat; '' kalau tidak dikenal (dihitung 'lainnya')."""
    return GROUP_NAMES.get(str(ticker).strip().upper(), "")


def _score_band(score) -> str:
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


def _to_float(v):
    """Coerce ke float; None kalau tidak bisa (row dari CSV = string)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_table(headers: list, rows: list) -> list:
    """Tabel monospace rata-kiri (ramah Telegram)."""
    col_w = [len(str(h)) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            col_w[i] = max(col_w[i], len(str(cell)))
    out = ["  " + " | ".join(str(h).ljust(col_w[i]) for i, h in enumerate(headers)),
           "  " + "-+-".join("-" * w for w in col_w)]
    for r in rows:
        out.append("  " + " | ".join(str(c).ljust(col_w[i]) for i, c in enumerate(r)))
    return out


def _load_all_evals() -> list:
    """Baca seluruh histori evaluations_v7.csv (untuk breakdown/ROI)."""
    if not os.path.exists(EVAL_CSV):
        return []
    try:
        with open(EVAL_CSV, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def _all_eval_rows(new_rows: list) -> list:
    """Gabung evaluasi baru + histori CSV, dedup by (date, ticker, mode)."""
    by_key = {}
    for r in _load_all_evals():
        by_key[(r.get("date", ""), r.get("ticker", ""), r.get("mode", ""))] = r
    for r in new_rows:
        by_key[(r.get("date", ""), r.get("ticker", ""), r.get("mode", ""))] = r
    return list(by_key.values())


def _wr_breakdown(rows: list, key_fn, title: str, min_n: int = MIN_SAMPLE_WARN) -> list:
    """Tabel WR per grup + peringatan sampel kecil (n selesai < min_n)."""
    groups = {}
    for r in rows:
        k = key_fn(r) or "(tanpa nilai)"
        groups.setdefault(k, []).append(r)
    lines = [f"\n▫️ {title}"]
    if not groups:
        lines.append("  Tidak ada data.")
        return lines
    table, warns = [], []
    for k in sorted(groups):
        g = groups[k]
        closed = [r for r in g if r.get("status") in ("WIN_TP", "LOSS_SL")]
        wins = sum(1 for r in closed if r.get("status") == "WIN_TP")
        n_c = len(closed)
        wr = wins / n_c * 100 if n_c else None
        table.append([k, len(g), n_c, wins, n_c - wins,
                      f"{wr:.0f}%" if wr is not None else "—"])
        if n_c == 0:
            warns.append(f"  ⚠️ {k}: belum ada sinyal selesai")
        elif n_c < min_n:
            warns.append(f"  ⚠️ {k}: sampel kecil n={n_c} < {min_n} — belum bisa disimpulkan")
    lines += _fmt_table(["Faktor", "n", "selesai", "WIN", "LOSS", "WR"], table)
    lines += warns
    return lines


def _estimate_fees(rows: list, fee_pct: float = FEE_ROUND_TRIP) -> dict:
    """Estimasi biaya fee: fee_pct × Σ cost (cost = nilai transaksi per sinyal).

    Asumsi jujur: semua sinyal yang tercatat dianggap dieksekusi (proxy
    'jumlah sinyal dieksekusi' — tidak ada kolom executed di data).
    """
    total_cost, n = 0.0, 0
    for r in rows:
        try:
            cost = float(r.get("cost", 0) or 0)
        except (TypeError, ValueError):
            cost = 0.0
        if cost > 0:
            total_cost += cost
            n += 1
    return {"fee": total_cost * fee_pct, "n": n, "cost": total_cost}


def roi_invezgo_section(rows: list, min_sample: int = 30) -> list:
    """E3: ROI langganan Invezgo (Rp 500rb/bln) — section laporan bulanan.

    NET = total return terealisasi (Σ return_pct × cost, sinyal selesai)
          − estimasi fee (0.4% round-trip)
          − Rp 500.000 langganan.
    Kalau sinyal selesai < 30 → placeholder jujur, tidak mengarang angka.
    """
    closed = [r for r in rows if r.get("status") in ("WIN_TP", "LOSS_SL")]
    n = len(closed)
    lines = ["", "─" * 25, "💰 ROI INVEZGO (bulanan)"]
    if n < min_sample:
        deficit = min_sample - n
        lines.append(f"Belum bisa dihitung (data < {min_sample} sampel) — baru {n} sinyal selesai.")
        lines.append(f"Butuh {deficit} lagi; dengan 5-10 sinyal selesai/minggu, estimasi "
                     f"{max(1, -(-deficit // 10))}-{max(1, -(-deficit // 5))} minggu.")
        lines.append(f"Biaya langganan Invezgo: Rp {INVEZGO_SUBSCRIPTION:,}/bulan "
                     f"(tetap berjalan sambil menunggu data).")
        return lines

    gross, cost_total = 0.0, 0.0
    for r in closed:
        try:
            cost = float(r.get("cost", 0) or 0)
            ret = float(r.get("return_pct", 0) or 0)
        except (TypeError, ValueError):
            continue
        gross += cost * ret / 100.0
        cost_total += cost
    fee = cost_total * FEE_ROUND_TRIP
    net = gross - fee - INVEZGO_SUBSCRIPTION

    lines.append(f"Total return terealisasi (Σ return_pct × cost, {n} sinyal): Rp {gross:,.0f}")
    lines.append(f"Estimasi fee (0.4% round-trip × Rp {cost_total:,.0f}): Rp {fee:,.0f}")
    lines.append(f"Biaya langganan Invezgo: Rp {INVEZGO_SUBSCRIPTION:,}")
    lines.append(f"NET: Rp {net:,.0f}")
    lines.append("Apakah langganan Invezgo menghasilkan lebih dari biayanya? "
                 + ("✅ YA — NET positif." if net > 0 else "❌ BELUM — NET negatif/nol."))
    lines.append("Catatan: return_pct = close saat evaluasi (bukan harga TP/SL) — estimasi kasar.")
    return lines


def _append_breakdown_sections(lines: list, eval_results: list, with_roi: bool = None):
    """E2+E3: append breakdown WR + estimasi fee + ROI Invezgo ke laporan.

    Breakdown memakai histori evaluations_v7.csv ∪ evaluasi baru (bukan cuma
    batch hari ini) supaya tabel lebih informatif — dilabel 'seluruh evaluasi'.
    """
    all_rows = _all_eval_rows(eval_results)
    lines.append("")
    lines.append("─" * 25)
    lines.append("📊 BREAKDOWN WR (seluruh evaluasi tercatat)")
    lines += _wr_breakdown(all_rows,
                           lambda r: f"{r.get('mode', '?')} × {_score_band(r.get('score'))}",
                           "mode × score band")
    lines += _wr_breakdown(all_rows,
                           lambda r: group_of(r.get("ticker", "")) or "lainnya",
                           "per grup konglomerat")
    if any(r.get("regime") not in (None, "") for r in all_rows):
        lines += _wr_breakdown(all_rows, lambda r: r.get("regime"), "per regime market")
    else:
        lines.append("")
        lines.append("▫️ Per regime market")
        lines.append("  ⚠️ regime tidak tercatat di data evaluasi (weekly_report.FIELDS "
                     "belum punya kolom regime) — belum bisa dihitung.")
    fees = _estimate_fees(all_rows)
    lines.append("")
    lines.append(f"💸 Estimasi biaya fee (0.4% round-trip × {fees['n']} sinyal dieksekusi): "
                 f"Rp {fees['fee']:,.0f}")
    if with_roi is None:
        with_roi = datetime.now().day <= 7  # awal bulan
    if with_roi:
        lines += roi_invezgo_section(all_rows)


def build_report(eval_results: list, with_roi: bool = None) -> str:
    """Buat laporan ringkas dari hasil evaluasi (MFE/MAE).

    with_roi: None → auto-detect laporan awal bulan (tanggal ≤ 7);
              True → selalu sertakan section ROI Invezgo (E3);
              False → jangan sertakan.
    Semua output lama (WR agregat, hit rate TP/SL, MFE/MAE, avg return)
    dipertahankan; section breakdown (E2) + ROI (E3) ditambahkan di bawahnya.
    """
    if not eval_results:
        lines = ["📊 WR EVALUASI", "Tidak ada sinyal baru yang dievaluasi hari ini."]
        _append_breakdown_sections(lines, eval_results, with_roi)
        return "\n".join(lines)

    closed = [r for r in eval_results if r["status"] in ("WIN_TP", "LOSS_SL")]
    n_closed = len(closed)
    wins = sum(1 for r in closed if r["status"] == "WIN_TP")
    losses = n_closed - wins
    wr = wins / n_closed * 100 if n_closed else 0
    avg_ret = sum((_to_float(r.get("return_pct")) or 0.0) for r in closed) / n_closed if n_closed else 0

    # Hit rate TP vs SL terpisah (dari sinyal yang sudah selesai)
    hit_tp = wins / n_closed * 100 if n_closed else 0
    hit_sl = losses / n_closed * 100 if n_closed else 0

    # Rata-rata MFE/MAE (semua sinyal yang punya data, termasuk OPEN)
    with_data = [r for r in eval_results
                 if _to_float(r.get("mfe_pct")) is not None]
    n_data = len(with_data)
    avg_mfe = sum((_to_float(r.get("mfe_pct")) or 0.0) for r in with_data) / n_data if n_data else 0
    avg_mae = sum((_to_float(r.get("mae_pct")) or 0.0) for r in with_data) / n_data if n_data else 0

    n_missing = sum(1 for r in eval_results if r["status"] == "DATA_MISSING")

    lines = [
        "📊 LAPORAN WR V7 (MFE/MAE)",
        "─" * 25,
        f"Dievaluasi: {len(eval_results)} sinyal",
        f"Selesai: {n_closed} (WIN {wins} | LOSS {losses})",
        f"Win Rate: {wr:.0f}%",
        f"Hit Rate TP: {hit_tp:.0f}% | Hit Rate SL: {hit_sl:.0f}%",
        f"Avg Return (closed): {avg_ret:+.2f}%",
        f"Avg MFE: {avg_mfe:+.1f}% | Avg MAE: {avg_mae:+.1f}%",
        "─" * 25,
    ]
    # Rincian yang baru selesai (max 8)
    done = [r for r in eval_results if r["status"] in ("WIN_TP", "LOSS_SL")]
    for r in done[:8]:
        icon = "🟢" if r["status"] == "WIN_TP" else "🔴"
        lines.append(f"{icon} {r['ticker']} {(_to_float(r.get('return_pct')) or 0.0):+.1f}% ({r['signal']})")
    if len(done) > 8:
        lines.append(f"... +{len(done) - 8} lainnya")
    if n_missing:
        lines.append(f"⚠️ {n_missing} sinyal DATA_MISSING (gagal ambil OHLC, akan dicoba lagi)")
    if n_closed == 0 and n_missing == 0:
        lines.append("Semua masih OPEN — belum ada yang selesai.")
    if n_data:
        lines.append("─" * 25)
        lines.append("Catatan: status pakai high/low sejak entry; urutan TP vs SL")
        lines.append("ikut urutan baris harian — intraday same-day tidak diketahui,")
        lines.append("dianggap konservatif (SL dulu).")
    _append_breakdown_sections(lines, eval_results, with_roi)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-send", action="store_true", help="Jangan kirim ke Telegram")
    parser.add_argument("--roi", action="store_true",
                        help="Paksa sertakan section ROI Invezgo (default: otomatis kalau awal bulan)")
    args = parser.parse_args()

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Evaluasi sinyal V7...")
    results = evaluate_signals()
    report = build_report(results, with_roi=True if args.roi else None)
    print(report)

    if not args.no_send:
        ok = send_telegram(report)
        print(f"\nTerkirim ke Telegram: {ok}")


if __name__ == "__main__":
    main()
