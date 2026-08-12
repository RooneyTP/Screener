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
Jika data OHLC gagal diambil → status DATA_MISSING (tidak di-append, dicoba lagi
run berikutnya s/d 3x berturut-turut, lalu key di-mark supaya peringatan tidak
muncul selamanya — R4).

R3 (audit Round 3):
- return_pct dihitung dari harga EXIT, bukan close terakhir: WIN_TP → harga TP,
  LOSS_SL → harga SL (dulu INDF LOSS_SL tampil +4.2% & ISAT WIN_TP +11.86%
  padahal TP-nya cuma +3.9% — menyesatkan). Kolom exit_price mencatat harga
  exit tsb; close_price tetap disimpan sebagai konteks (return-to-now).
  Catatan: baris OPEN tidak pernah ditulis ke CSV (dievaluasi ulang), jadi
  return_pct di CSV selalu berbasis harga exit; label 'open' tidak relevan.
- R3-hardening (IDE4): exit price tidak valid (NaN/<=0) TIDAK di-fallback
  diam-diam ke close — log warning & kolom exit_price ditandai 'nan' (fallback
  ke close hanya dengan penanda eksplisit itu). Konsistensi status-vs-return
  dicek: LOSS_SL dengan return >= 0 (atau WIN_TP <= 0) di-log warning.
- Evaluasi di-dedup: (ticker, mode, entry ±1%, jarak tanggal <= 14 hari)
  dianggap SATU sinyal — hanya baris TERBARU yang dievaluasi; duplikat
  (termasuk yang sudah pernah dievaluasi di run sebelumnya) di-skip,
  key-nya di-mark & tiap skip di-log 'Dup-skip evaluasi' (jejak audit per
  baris). Jumlah yang di-skip dilaporkan.

Hasil disimpan di data/evaluations_v7.csv + kirim ringkasan ke Telegram.

Cara pakai:
  python weekly_report.py            # evaluasi + kirim laporan
  python weekly_report.py --no-send  # evaluasi saja, tanpa kirim Telegram
  python weekly_report.py --dry-run  # PREVIEW: evaluasi TANPA menulis file
                                     # (CSV/mark) & TANPA kirim Telegram
  python weekly_report.py --roi      # paksa section ROI Invezgo (E3)
"""
import sys, os, json, csv, argparse, logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("weekly_report")

import pandas as pd
import numpy as np

from data_invezgo import InvezgoProvider
from perf_tracker import load_signals, DEDUP_TOLERANCE, DEDUP_MAX_AGE_DAYS

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
PERF_CSV = os.path.join(DATA_DIR, "perf_tracker_v7.csv")
EVAL_CSV = os.path.join(DATA_DIR, "evaluations_v7.csv")
EVAL_MARK = os.path.join(DATA_DIR, "evaluated_keys.json")
# R4: sinyal DATA_MISSING (gagal ambil OHLC) dicoba ulang maksimal
# DATA_MISSING_MAX_ATTEMPTS run berturut-turut, lalu key-nya di-mark —
# dulu tidak pernah di-mark → peringatan "DATA_MISSING" muncul tiap run
# selamanya. Data yang sementara unavailable tetap punya 3x kesempatan.
DATA_MISSING_MAX_ATTEMPTS = 3
MISSING_ATTEMPTS_FILE = os.path.join(DATA_DIR, "data_missing_attempts.json")
FIELDS = ["date", "ticker", "mode", "score", "signal", "entry_price", "sl", "tp",
          "lots", "cost", "status", "close_price", "exit_price", "return_pct",
          "mfe_pct", "mae_pct", "eval_date", "regime"]

# R3: jumlah baris duplikat evaluasi yang di-skip pada run terakhir — dibaca
# build_report() untuk dilaporkan. Di-reset di awal evaluate_signals().
_LAST_EVAL_SKIPPED = 0

# ── E2: mapping grup konglomerat — SINGLE SOURCE: config.yaml section
# 'groups' (dibaca via groups_config.load_groups(); dulu hardcode di sini
# + duplikat di v7_scan.py & factor_analysis.py = 4 sumber drift) ──
from groups_config import load_groups

GROUP_NAMES = load_groups()  # {TICKER: nama_grup} — fallback {} kalau config gagal

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


def _load_missing_attempts() -> dict:
    """Baca counter percobaan DATA_MISSING per key (persisten antar run)."""
    if os.path.exists(MISSING_ATTEMPTS_FILE):
        try:
            with open(MISSING_ATTEMPTS_FILE, encoding="utf-8", errors="replace") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def _save_missing_attempts(attempts: dict):
    try:
        with open(MISSING_ATTEMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(attempts, f)
    except Exception as e:
        # R4: counter DATA_MISSING gagal disimpan → sinyal akan dicoba lagi
        # run berikutnya (tidak fatal), tapi jejak error tetap perlu terlihat.
        logger.warning("Gagal simpan data_missing_attempts.json: %s", e)


def _record_data_missing(key, s, mode, today, results, attempts, dry_run=False) -> bool:
    """Catat satu kegagalan ambil data OHLC (status DATA_MISSING).

    Tidak di-append ke CSV; dicoba lagi run berikutnya SAMPAI
    DATA_MISSING_MAX_ATTEMPTS kali berturut-turut — setelah itu key di-mark
    (berhenti dicoba & tidak muncul lagi di laporan) supaya peringatan tidak
    berulang selamanya (R4). dry_run: hasil tetap dihitung utk preview tapi
    counter & mark TIDAK ditulis.

    Returns True kalau key baru di-mark (pemanggil menambahkannya ke new_marks).
    """
    results.append(_data_missing_row(s, mode, today))
    if dry_run:
        return False
    n = int(attempts.get(key, 0) or 0) + 1
    attempts[key] = n
    if n >= DATA_MISSING_MAX_ATTEMPTS:
        attempts.pop(key, None)
        return True
    return False


def _ensure_eval_schema():
    """Migrasi header evaluations_v7.csv ke urutan FIELDS kanonik (R3).

    Bila kolom FIELDS belum ada di file lama (mis. kolom exit_price dari R3),
    header ditulis ulang SELENGKAP & SEURUTAN FIELDS — PENTING: baris baru
    di-append via DictWriter(fieldnames=FIELDS), jadi header harus persis
    FIELDS (kolom baru di posisi kanoniknya, BUKAN ditambahkan di ujung)
    supaya nilai baris baru tidak bergeser ke kolom yang salah. Baris lama
    diberi nilai kosong untuk kolom yang belum ada. File append-only &
    kecil → rewrite aman."""
    if not os.path.exists(EVAL_CSV):
        return
    try:
        with open(EVAL_CSV, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            return
        if list(rows[0].keys()) == FIELDS:
            return  # sudah kanonik — tidak perlu rewrite
        with open(EVAL_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow({k: r.get(k, "") for k in FIELDS})
    except Exception:
        pass  # gagal migrasi → baris baru tetap bisa di-append (kolom ekstra diabaikan pembaca)


def _append_eval(row: dict):
    # R4: file 0-byte (mis. pernah di-truncate) dihitung BARU → header wajib
    # ditulis. Dulu hanya cek exists() → _ensure_eval_schema() return early
    # utk 0 baris → baris data jadi baris pertama tanpa header (pola sama
    # dgn perf_tracker.py).
    new_file = not os.path.exists(EVAL_CSV) or os.path.getsize(EVAL_CSV) == 0
    _ensure_eval_schema()
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

    entry = float(entry_price)
    if entry <= 0:
        return empty

    # R1 (regresi M8): JANGAN drop baris NaN per-kolom — highs.dropna() dan
    # lows.dropna() terpisah membuat baris TIDAK sejajar: baris yang high-nya
    # NaN ikut ter-buang dari penentuan urutan TP/SL padahal low-nya valid.
    # Akibatnya: same-bar NaN → WIN_TP palsu (padahal konservatif LOSS_SL) &
    # SL kena di baris NaN-nya high → status OPEN (SL hilang). Perbaikan:
    #   - status ditentukan per-baris UTUH (NaN di satu kolom tidak menghapus
    #     baris itu dari pengecekan kolom lain);
    #   - MFE/MAE pakai np.nanmax/np.nanmin; kalau SEMUA high (atau low) NaN
    #     → kolom itu diabaikan (None → mfe/mae 0.0, bukan 'nan').
    tp = float(tp)
    sl = float(sl)
    high_arr = highs.to_numpy(dtype=float)
    low_arr = lows.to_numpy(dtype=float)
    n = len(high_arr)

    if n and not np.isnan(high_arr).all():
        max_high = float(np.nanmax(high_arr))
        mfe_pct = (max_high - entry) / entry * 100
    else:
        max_high, mfe_pct = None, 0.0
    if n and not np.isnan(low_arr).all():
        min_low = float(np.nanmin(low_arr))
        mae_pct = (min_low - entry) / entry * 100
    else:
        min_low, mae_pct = None, 0.0

    tp_idx = sl_idx = None
    for i in range(n):
        h, l = high_arr[i], low_arr[i]
        if tp_idx is None and not np.isnan(h) and h >= tp:
            tp_idx = i
        if sl_idx is None and not np.isnan(l) and l <= sl:
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
        "exit_price": "", "return_pct": "", "mfe_pct": "", "mae_pct": "",
        "eval_date": today.strftime("%Y-%m-%d"),
        "regime": s.get("regime", "") or "",
    }


def _dedup_eval_candidates(candidates: list, hist: list = None) -> tuple:
    """Saring kandidat evaluasi — SATU sinyal unik hanya dievaluasi SEKALI.

    Dua baris dianggap sinyal yang SAMA (R3) bila:
      - ticker & mode sama, DAN
      - entry_price sama dalam toleransi ±1% (DEDUP_TOLERANCE), DAN
      - tanggalnya berjarak <= 14 hari (DEDUP_MAX_AGE_DAYS — jendela dedup
        yang sama dengan perf_tracker; baris > 14 hari = sinyal baru wajar).

    Baris yang dipilih: yang TERBARU (metadata paling mutakhir: score/signal/
    regime terakhir & tanggal paling dekat dengan jendela evaluasi). Duplikat
    dari batch yang sama (mis. 08/08 22:02 + 22:32 fresh=0) MAUPUN duplikat
    terhadap sinyal yang sudah pernah dievaluasi di run sebelumnya (dicek ke
    histori evaluations_v7.csv — kasus BUMI intraday 04/08 dievaluasi 08/07,
    baris 05/08 entry sama baru cukup umur di run berikutnya) dikembalikan
    sebagai 'skipped' — key-nya di-mark oleh pemanggil agar tidak dievaluasi
    sebagai sinyal independen di run berikutnya.

    Return (uniq, skipped): uniq = kandidat terpilih sebagai 5-tuple
    (s, dt, mode, key, eval_from) — eval_from = tanggal ENTRY PALING AWAL
    grup dedup (baris re-log dievaluasi sejak sinyal pertama muncul, bukan
    sejak baris terbaru; kalau tidak, TP/SL yang kena di hari entry baris
    terbaru tidak terlihat → status OPEN palsu & hasilnya hilang).
    skipped = 4-tuple (s, dt, mode, key) duplikat.
    """
    hist = hist or []
    hist_idx = {}
    for r in hist:
        if r.get("status") not in ("WIN_TP", "LOSS_SL"):
            continue
        hist_idx.setdefault((str(r.get("ticker", "")).upper(), r.get("mode", "")),
                            []).append(r)

    groups = {}
    for c in candidates:
        s, _dt, mode, _key = c
        groups.setdefault((str(s["ticker"]).upper(), mode), []).append(c)

    uniq, skipped = [], []
    for rows in groups.values():
        rows.sort(key=lambda c: c[1], reverse=True)  # tanggal turun → baris terbaru = rep pertama
        reps = []  # [(date, entry_price, uniq_entry)] sinyal unik yang sudah diterima
        for c in rows:
            s, dt, _mode, _key = c
            try:
                entry = float(s["entry_price"])
            except (TypeError, ValueError):
                uniq.append([s, dt, _mode, _key, dt])  # entry tidak valid → evaluasi apa adanya
                continue
            if entry <= 0:
                uniq.append([s, dt, _mode, _key, dt])
                continue
            is_dup = False
            # 1) Duplikat dalam batch yang sama (baris re-log / fresh=0)
            for rdate, rentry, ue in reps:
                if (abs(rentry - entry) / rentry <= DEDUP_TOLERANCE
                        and abs((dt - rdate).days) <= DEDUP_MAX_AGE_DAYS):
                    is_dup = True
                    # sinyal re-log: evaluasi tetap dihitung sejak entry
                    # PALING AWAL grup, bukan sejak baris terbaru
                    ue[4] = min(ue[4], dt)
                    break
            # 2) Duplikat terhadap sinyal yang SUDAH dievaluasi run lalu
            if not is_dup:
                for hr in hist_idx.get((str(s["ticker"]).upper(), _mode), []):
                    try:
                        h_entry = float(hr.get("entry_price", 0) or 0)
                        h_date = datetime.strptime(hr["date"], "%Y-%m-%d %H:%M")
                    except (TypeError, ValueError, KeyError):
                        continue
                    if (h_entry > 0
                            and abs(h_entry - entry) / h_entry <= DEDUP_TOLERANCE
                            and 0 <= (dt - h_date).days <= DEDUP_MAX_AGE_DAYS):
                        is_dup = True
                        break
            if is_dup:
                skipped.append(c)
            else:
                ue = [s, dt, _mode, _key, dt]
                reps.append((dt, entry, ue))
                uniq.append(ue)
    return uniq, skipped


def evaluate_signals(provider=None, dry_run: bool = False) -> list:
    """Evaluasi sinyal yang belum dievaluasi & sudah cukup umur. Return list hasil.

    dry_run=True (C1): evaluasi dihitung & dikembalikan (untuk preview laporan)
    tapi TIDAK ada yang ditulis — tidak append evaluations_v7.csv & tidak
    menandai evaluated_keys.json. Berguna untuk uji coba: sinyal TIDAK
    ter-mark sebagian, sehingga run berikutnya mengevaluasi ulang dengan benar.
    """
    signals = load_signals(PERF_CSV)
    if not signals:
        print("Tidak ada sinyal di perf_tracker.")
        return []

    if provider is None:
        provider = InvezgoProvider()

    marked = _load_marked()
    missing_attempts = _load_missing_attempts()
    missing_dirty = False
    today = datetime.now()
    results = []
    new_marks = []
    global _LAST_EVAL_SKIPPED
    _LAST_EVAL_SKIPPED = 0

    # ── Kumpulkan kandidat layak evaluasi (cukup umur & belum di-mark) ──
    candidates = []
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

            candidates.append((s, dt, mode, key))
        except (ValueError, KeyError, TypeError):
            continue

    # ── R3: dedup evaluasi — (ticker, mode, entry ±1%, <=14 hari) = SATU
    # sinyal unik. Dulu BUMI intraday 04/08 & 05/08 (entry 168 sama)
    # dievaluasi 2× dan batch 08/08 ×4 (baris duplikat fresh=0) akan
    # dievaluasi sebagai sinyal independen → WR terkontaminasi. Sekarang
    # hanya baris TERBARU yang dievaluasi; duplikat di-skip & key-nya
    # di-mark (tidak di-mark saat dry-run) supaya tidak muncul lagi.
    uniq, skipped = _dedup_eval_candidates(candidates, hist=_load_all_evals())
    _LAST_EVAL_SKIPPED = len(skipped)
    if not dry_run:
        new_marks.extend(k for (_s, _dt, _m, k) in skipped)
    # R3-hardening: tiap duplikat yang di-skip di-log eksplisit (jejak audit
    # 'dup-skip' per baris — dulu hanya counter _LAST_EVAL_SKIPPED).
    for _s, _dt, _m, _k in skipped:
        logger.warning(
            "Dup-skip evaluasi: %s (%s) entry=%s — sinyal sama (entry ±%d%%, "
            "jarak <=%d hari) sudah dievaluasi/tercatat; key di-mark, tidak "
            "dievaluasi ulang",
            _s.get("ticker"), _m, _s.get("entry_price"),
            int(round(DEDUP_TOLERANCE * 100)), DEDUP_MAX_AGE_DAYS)

    for s, dt, mode, key, eval_from in uniq:
        try:
            ticker = s["ticker"]
            entry = float(s["entry_price"])
            sl = float(s["sl"])
            tp = float(s["tp"])
            age_days = (today - dt).days

            # ── Ambil OHLC sejak entry (bukan cuma close terakhir) ──
            try:
                df = provider.get_historical(ticker, period=_period_for_age(age_days),
                                             use_cache=True)
            except Exception:
                df = None
            if df is None or df.empty:
                # Data gagal diambil (network/rate limit) → tandai, jangan crash.
                # Tidak di-append ke CSV; dicoba lagi run berikutnya s/d
                # DATA_MISSING_MAX_ATTEMPTS kali, lalu key di-mark (R4).
                if _record_data_missing(key, s, mode, today, results, missing_attempts, dry_run):
                    new_marks.append(key)
                if not dry_run:
                    missing_dirty = True
                continue

            # Baris SEJAK entry. Index harian = 00:00 sedangkan timestamp
            # sinyal > 00:00 → baris hari entry TIDAK ikut (ter-exclude).
            # R3: untuk grup dedup (sinyal re-log), window dihitung sejak
            # eval_from = entry PALING AWAL grup, bukan sejak baris terbaru —
            # kalau tidak, TP/SL yang kena di hari entry baris terbaru tidak
            # terlihat (status OPEN palsu → hasil evaluasi hilang).
            # M3-doc: guard potong eksplisit (index > waktu sinyal) supaya
            # konsisten walau format date CSV berubah (dengan/tanpa jam) —
            # docstring di bawah ini MENYESUAIKAN perilaku tersebut.
            if isinstance(df.index, pd.DatetimeIndex):
                since = df[df.index > pd.Timestamp(eval_from)]
            else:
                since = df
            if since is None or since.empty:
                if _record_data_missing(key, s, mode, today, results, missing_attempts, dry_run):
                    new_marks.append(key)
                if not dry_run:
                    missing_dirty = True
                continue

            # Klasifikasi MFE/MAE pakai high/low sejak entry
            res = classify_ohlc(since, entry, sl, tp)
            status = res["status"]
            if status == "OPEN":
                # H2: JANGAN mark/append baris OPEN — kalau di-mark, sinyal
                # tidak pernah dievaluasi ulang dan WR bias ke bawah. Sinyal
                # OPEN dievaluasi lagi di run berikutnya sampai selesai.
                continue
            # M8: close terakhir NaN/0 → anggap data belum siap (DATA_MISSING),
            # jangan tulis mfe/mae 'nan%' ke CSV.
            closes = _pick_col(df, "close").astype(float).dropna()
            if closes.empty:
                if _record_data_missing(key, s, mode, today, results, missing_attempts, dry_run):
                    new_marks.append(key)
                if not dry_run:
                    missing_dirty = True
                continue
            close = float(closes.iloc[-1])
            if close <= 0:
                if _record_data_missing(key, s, mode, today, results, missing_attempts, dry_run):
                    new_marks.append(key)
                if not dry_run:
                    missing_dirty = True
                continue
            # R3: return_pct dihitung dari harga EXIT, bukan close terakhir:
            # WIN_TP → exit di harga TP; LOSS_SL → exit di harga SL. (Dulu
            # pakai close terakhir → INDF LOSS_SL tampil +4.2% padahal rugi &
            # ISAT WIN_TP tampil +11.86% padahal TP-nya cuma +3.9% — data
            # menyesatkan untuk WR/avg return.) close_price tetap dicatat
            # sebagai konteks (harga saat evaluasi), exit_price = harga exit.
            # R3-hardening: exit price tidak valid (NaN/<=0) JANGAN di-fallback
            # diam-diam ke close — log warning & exit_price ditandai 'nan';
            # return_pct fallback ke close HANYA dengan penanda eksplisit itu.
            exit_price = tp if status == "WIN_TP" else sl
            if exit_price is None or not np.isfinite(exit_price) or exit_price <= 0:
                logger.warning(
                    "Exit price tidak tersedia utk %s (%s, %s): %s=%r → "
                    "exit_price ditandai 'nan', return_pct fallback ke close "
                    "%.2f (data perlu dicek)",
                    ticker, mode, status,
                    "TP" if status == "WIN_TP" else "SL", exit_price, close)
                exit_price = float("nan")
                ret_pct = (close - entry) / entry * 100
            else:
                ret_pct = (exit_price - entry) / entry * 100
            # R3-hardening: konsistensi status vs return — LOSS_SL harus
            # negatif & WIN_TP harus positif; anomali di-log (tidak diubah,
            # karena bisa jadi data aneh yang perlu dilihat manusia).
            if status == "LOSS_SL" and ret_pct >= 0:
                logger.warning(
                    "Konsistensi status-vs-return: %s (%s) LOSS_SL tapi "
                    "return_pct %+.2f%% (entry=%.2f, SL=%.2f, close=%.2f) — "
                    "status aneh, cek data!",
                    ticker, mode, ret_pct, entry, sl, close)
            elif status == "WIN_TP" and ret_pct <= 0:
                logger.warning(
                    "Konsistensi status-vs-return: %s (%s) WIN_TP tapi "
                    "return_pct %+.2f%% (entry=%.2f, TP=%.2f, close=%.2f) — "
                    "status aneh, cek data!",
                    ticker, mode, ret_pct, entry, tp, close)
            row = {
                "date": s["date"], "ticker": ticker, "mode": mode,
                "score": s.get("score", ""), "signal": s.get("signal", ""),
                "entry_price": entry, "sl": sl, "tp": tp,
                "lots": s.get("lots", ""), "cost": s.get("cost", ""),
                "status": status, "close_price": round(close, 2),
                "exit_price": round(exit_price, 2),
                "return_pct": round(ret_pct, 2),
                "mfe_pct": round(res["mfe_pct"], 2),
                "mae_pct": round(res["mae_pct"], 2),
                "eval_date": today.strftime("%Y-%m-%d"),
                "regime": s.get("regime", "") or "",
            }
            # C1: dry-run TIDAK menulis apa pun — hasil tetap dihitung untuk
            # preview laporan, tapi CSV & mark keys tidak tersentuh.
            if not dry_run:
                _append_eval(row)
            results.append(row)
            if not dry_run:
                new_marks.append(key)
        except (ValueError, KeyError, TypeError) as e:
            # Jangan senyap: sinyal yang gagal diproses tetap dicoba lagi di
            # run berikutnya (tidak di-mark), tapi error perlu terlihat di log.
            logger.warning("Skip evaluasi %s|%s: %s", s.get("ticker"), mode, e)
            continue

    if new_marks:
        marked.update(new_marks)
        _save_marked(marked)
    if not dry_run and missing_dirty:
        _save_missing_attempts(missing_attempts)
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
    lines.append("Catatan: return_pct = harga exit (TP/SL — bukan close saat evaluasi); estimasi kasar.")
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
        if _LAST_EVAL_SKIPPED:
            lines.append(f"⏭️ {_LAST_EVAL_SKIPPED} baris duplikat evaluasi di-skip "
                         f"(sinyal sama, entry ±1%)")
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
    ]
    if _LAST_EVAL_SKIPPED:
        lines.append(f"⏭️ {_LAST_EVAL_SKIPPED} baris duplikat evaluasi di-skip "
                     f"(sinyal sama, entry ±1%)")
    lines += [
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
        lines.append("return_pct = harga exit (TP utk WIN_TP, SL utk LOSS_SL) —")
        lines.append("bukan close saat evaluasi (kolom close_price = konteks).")
    _append_breakdown_sections(lines, eval_results, with_roi)
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-send", action="store_true", help="Jangan kirim ke Telegram")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview: evaluasi TANPA menulis file (CSV/mark) & TANPA kirim")
    parser.add_argument("--roi", action="store_true",
                        help="Paksa sertakan section ROI Invezgo (default: otomatis kalau awal bulan)")
    args = parser.parse_args()

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Evaluasi sinyal V7...")
    # C1: --dry-run = tidak menulis evaluations_v7.csv / evaluated_keys.json
    # (footgun lama: --no-send tetap menulis → data uji ter-mark sebagian).
    # --no-send tetap seperti semula: evaluasi ditulis, hanya kirim yang dilewati.
    results = evaluate_signals(dry_run=args.dry_run)
    report = build_report(results, with_roi=True if args.roi else None)
    print(report)

    if args.dry_run:
        print("\n[dry-run] Tidak ada file yang ditulis & tidak ada pesan dikirim.")
    elif not args.no_send:
        ok = send_telegram(report)
        print(f"\nTerkirim ke Telegram: {ok}")


if __name__ == "__main__":
    main()
