#!/usr/bin/env python3
"""shadow_eval.py — LAB AKURASI: nilai hasil kandidat "mode bayangan".

Membaca idx_alpha_screener/data/shadow_v7.csv (rekaman DNA faktor SEMUA
finalis V7 — termasuk yang tidak jadi sinyal; lihat shadow_log.py), lalu
menghitung hasil MAJU (forward return) dari riwayat harga asli:
  ret_5 / ret_10 / ret_20 = return % vs harga hari pencatatan (close),
  MFE / MAE jendela 20 hari bursa, status TP/SL (bila level tersedia).

Tujuan: mengukur faktor mana yang memisahkan menang vs kalah — termasuk
kohort yang TIDAK jadi sinyal (kontrol). Sisi broker/asing (45% skor V7)
tidak bisa di-backtest (tanpa riwayat) → ini satu-satunya ukuran.

Dedupe: 1 baris per (tanggal, kode) — diambil yang TERAKHIR (run terakhir
hari itu). Laporan ditulis ke data/shadow_eval.md; ntfy hanya bila sampel
matang cukup (>= 30) — kalau belum, diam.

Pakai:
    .venv/bin/python shadow_eval.py                # normal (cron Sabtu)
    .venv/bin/python shadow_eval.py --csv X.csv    # file lain (uji)
    .venv/bin/python shadow_eval.py --dry          # tanpa ntfy (uji)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(ROOT, "idx_alpha_screener")
sys.path.insert(0, SCAN)

import pandas as pd  # noqa: E402

from shadow_log import read_rows  # noqa: E402

WIB = timezone(timedelta(hours=7))
HORIZON = 20                              # hari bursa utk MFE/MAE & TP/SL
MIN_SAMPEL = 30                           # minimal baris matang sebelum ntfy
CACHE_DIR = os.path.join(SCAN, "data", "shadow_prices")
REPORT = os.path.join(SCAN, "data", "shadow_eval.md")
FAKTOR_BUCKET = ["broker_flow", "foreign_flow", "fundamental",
                 "earnings_momentum", "broker_trend", "v4_core"]
FLAG_BUCKET = ["flow_spike", "conflict"]


# ── utilitas ──────────────────────────────────────────────────────────────

def _f_(x):
    """float aman; None kalau kosong/tak valid."""
    try:
        s = str(x).strip()
        if not s:
            return None
        return float(s)
    except (TypeError, ValueError):
        return None


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    """Rapikan df harga: kolom lowercase unik + kolom 'date' + urut tanggal.

    get_historical mengembalikan kolom DOBEL (Open+open dst) → dedupe.
    """
    df = df.copy()
    df.columns = [str(c).lower().strip() for c in df.columns]
    df = df.loc[:, ~df.columns.duplicated()]
    if "date" not in df.columns:
        df = df.reset_index()
        df.columns = [str(c).lower().strip() for c in df.columns]
        df = df.loc[:, ~df.columns.duplicated()]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    return df.sort_values("date").reset_index(drop=True)


def muat_harga(ip, kode: str) -> "pd.DataFrame | None":
    """Harga 1y utk 1 kode. Cache data/shadow_prices/{kode}.csv (segar 20 jam);
    fetch gagal → pakai cache lama bila ada."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{kode}.csv")
    segar = (os.path.isfile(path)
             and (time.time() - os.path.getmtime(path)) < 20 * 3600)
    if segar:
        try:
            return _norm(pd.read_csv(path))
        except Exception:
            pass
    try:
        df = ip.get_historical(kode, period="1y")
        if df is not None and not df.empty:
            out = _norm(df)
            try:
                out.to_csv(path, index=False)
            except Exception:
                pass
            return out
    except Exception as e:  # noqa: BLE001
        print(f"  [harga] {kode}: fetch gagal ({type(e).__name__}: {e})")
    if os.path.isfile(path):
        try:
            return _norm(pd.read_csv(path))
        except Exception:
            pass
    return None


def nilai_baris(row: dict, df: pd.DataFrame) -> "dict | None":
    """Hitung hasil forward utk satu baris shadow. None = tak bisa dinilai."""
    tgl = str(row.get("tanggal") or "").strip()
    if not tgl:
        return None
    try:
        pd.Timestamp(tgl)
    except Exception:
        return None
    kandidat = df.index[df["date"] >= tgl]
    if len(kandidat) == 0:
        return None
    i0 = int(kandidat[0])
    gap = (pd.Timestamp(df["date"].iloc[i0]) - pd.Timestamp(tgl)).days
    if gap > 4:                       # run saat libur / tanggal di luar seri → skip
        return None
    n = len(df)
    harga_row = _f_(row.get("harga"))
    # entry = harga rekaman (basis keputusan saat scan) — lebih setia drpd
    # close hari-bursa-terdekat saat run tak tepat hari bursa (mis. manual Sabtu).
    # Fallback: close hari bursa pertama >= tanggal.
    entry = harga_row if harga_row else float(df["close"].iloc[i0])
    if not entry or entry <= 0:
        return None

    out: dict = {"entry": round(entry, 2)}
    for h in (5, 10, 20):
        j = i0 + h
        out[f"ret_{h}"] = (round((float(df["close"].iloc[j]) - entry) / entry * 100, 2)
                           if j < n else None)

    hi = df["high"].iloc[i0 + 1: i0 + 1 + HORIZON].astype(float)
    lo = df["low"].iloc[i0 + 1: i0 + 1 + HORIZON].astype(float)
    out["mfe"] = round((hi.max() - entry) / entry * 100, 2) if len(hi) else None
    out["mae"] = round((lo.min() - entry) / entry * 100, 2) if len(lo) else None

    tp, sl = _f_(row.get("tp")), _f_(row.get("sl"))
    out["status"], out["hit_hari"] = "", ""
    if tp and sl:
        st, day = "none", ""
        for k in range(i0 + 1, min(i0 + 1 + HORIZON, n)):
            h_ = float(df["high"].iloc[k])
            l_ = float(df["low"].iloc[k])
            if l_ <= sl:               # keduanya kena di hari sama → SL (konservatif)
                st, day = "sl", k - i0
                break
            if h_ >= tp:
                st, day = "tp", k - i0
                break
        else:
            if i0 + HORIZON >= n:
                st = "open"            # jendela belum selesai
        out["status"], out["hit_hari"] = st, day
    return out


def _bucket(x):
    v = _f_(x)
    if v is None:
        return None
    if v >= 65:
        return "tinggi≥65"
    if v >= 35:
        return "sedang35-64"
    return "rendah<35"


def _stat(rows, key="ret_10"):
    """(n, rata2, median, winrate%) utk baris yang punya key tsb."""
    vals = [h[key] for _r, h in rows if h.get(key) is not None]
    if not vals:
        return None
    n = len(vals)
    vals_sorted = sorted(vals)
    med = vals_sorted[n // 2] if n % 2 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
    wr = 100 * sum(1 for v in vals if v > 0) / n
    return n, round(sum(vals) / n, 2), round(med, 2), round(wr, 1)


def _fmt(stat):
    if not stat:
        return "n=0"
    n, avg, med, wr = stat
    return f"n={n} · rata {avg:+.2f}% · med {med:+.2f}% · WR {wr:.0f}%"


def _ntfy(pesan: str) -> None:
    try:
        import urllib.request as _u
        topic = os.environ.get("NTFY_TOPIC", "quantYuan")
        req = _u.Request(f"https://ntfy.sh/{topic}", data=pesan.encode("utf-8"),
                         headers={"Title": "RisetSaham - Lab akurasi sinyal",
                                  "Priority": "default"})
        _u.urlopen(req, timeout=15)
        print("ntfy terkirim")
    except Exception as e:  # noqa: BLE001
        print(f"ntfy gagal: {str(e)[:120]}")


# ── laporan ───────────────────────────────────────────────────────────────

def buat_laporan(ded: list[dict], hasil: list[tuple[dict, dict]]) -> str:
    matang = [(r, h) for r, h in hasil if h.get("ret_10") is not None]
    tgl_list = sorted({str(r.get("tanggal") or "") for r in ded})
    L = []
    L.append("# 🧪 Lab Akurasi V7 — hasil kandidat \"mode bayangan\"")
    L.append("")
    L.append(f"Periode: **{tgl_list[0]} → {tgl_list[-1]}** · "
             f"kandidat unik: **{len(ded)}** · bisa dinilai (punya ret_10): **{len(matang)}** · "
             f"belum matang: {len(hasil) - len(matang)}")
    L.append("")
    if not matang:
        L.append("_Belum ada baris yang matang (butuh ≥10 hari bursa setelah pencatatan)._")
        return "\n".join(L) + "\n"

    L.append("## Keseluruhan")
    L.append(f"- ret_5:  {_fmt(_stat(matang, 'ret_5'))}")
    L.append(f"- ret_10: {_fmt(_stat(matang, 'ret_10'))}  ← utama (horizon swing)")
    L.append(f"- ret_20: {_fmt(_stat(matang, 'ret_20'))}")
    mfe = [h["mfe"] for _r, h in matang if h.get("mfe") is not None]
    mae = [h["mae"] for _r, h in matang if h.get("mae") is not None]
    if mfe:
        L.append(f"- MFE rata {sum(mfe)/len(mfe):+.2f}% · MAE rata {sum(mae)/len(mae):+.2f}% (jendela 20 hari)")
    L.append("")

    L.append("## KUNCI 1: sinyal (tampil) vs non-sinyal (kontrol)")
    for lbl, filt in (("✅ tampil (sinyal)", lambda r: str(r.get("tampil")).lower() == "ya"),
                      ("⛔ tidak tampil (kontrol)", lambda r: str(r.get("tampil")).lower() != "ya")):
        sel = [(r, h) for r, h in matang if filt(r)]
        L.append(f"- {lbl}: {_fmt(_stat(sel))}")
    L.append("")

    L.append("## KUNCI 2: per faktor (bucket nilai faktor vs ret_10)")
    for f in FAKTOR_BUCKET:
        for b in ("tinggi≥65", "sedang35-64", "rendah<35"):
            sel = [(r, h) for r, h in matang if _bucket(r.get(f)) == b]
            if len(sel) >= 5:
                L.append(f"- {f} [{b}]: {_fmt(_stat(sel))}")
    for f in FLAG_BUCKET:
        for b, lbl in ((1, "AKTIF"), (0, "nonaktif")):
            sel = [(r, h) for r, h in matang
                   if str(r.get(f)).strip() in ("1", "1.0", "True") and b == 1
                   or str(r.get(f)).strip() in ("0", "0.0", "False") and b == 0]
            if len(sel) >= 5:
                L.append(f"- {f} [{lbl}]: {_fmt(_stat(sel))}")
    L.append("")

    L.append("## KUNCI 3: per alasan keputusan")
    alasan_semua = sorted({str(r.get("alasan") or "") for r in ded})
    for a in alasan_semua:
        if not a:
            continue
        sel = [(r, h) for r, h in matang if str(r.get("alasan")) == a]
        L.append(f"- {a}: {_fmt(_stat(sel))}")
    L.append("")

    sinyal = [(r, h) for r, h in hasil if h.get("status")]
    if sinyal:
        from collections import Counter
        c = Counter(h["status"] for _r, h in sinyal)
        L.append("## TP/SL (baris sinyal)")
        L.append(f"- 🎯 tp: {c.get('tp', 0)} · 🛑 sl: {c.get('sl', 0)} · "
                 f"➖ none (selesai, tak tersentuh): {c.get('none', 0)} · ⏳ open: {c.get('open', 0)}")
        L.append("")
    L.append("_Catatan: sampel kecil → pembacaan awal, bukan kesimpulan final._")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Lab akurasi — evaluator mode bayangan")
    ap.add_argument("--csv", default=None, help="file shadow (default data/shadow_v7.csv)")
    ap.add_argument("--dry", action="store_true", help="tanpa ntfy (uji)")
    args = ap.parse_args()

    rows = read_rows(args.csv)
    if not rows:
        print("LAB: belum ada baris shadow — belum ada yang bisa dinilai.")
        return 0

    # dedupe (tanggal, kode) → ambil yang TERAKHIR (run terakhir hari itu)
    ded_map: dict = {}
    for r in rows:
        ded_map[(str(r.get("tanggal") or ""), str(r.get("kode") or "").upper())] = r
    ded = list(ded_map.values())
    print(f"LAB: {len(rows)} baris terbaca → {len(ded)} kandidat unik (tanggal+kode)")

    from data_provider import InvezgoProvider  # noqa: E402
    ip = InvezgoProvider()
    harga: dict = {}
    hasil: list = []
    for r in sorted(ded, key=lambda x: str(x.get("tanggal") or "")):
        kode = str(r.get("kode") or "").upper()
        if not kode:
            continue
        if kode not in harga:
            harga[kode] = muat_harga(ip, kode)
            time.sleep(0.2)  # sopan antar panggilan
        df = harga.get(kode)
        if df is None or df.empty:
            continue
        h = nilai_baris(r, df)
        if h:
            hasil.append((r, h))

    teks = buat_laporan(ded, hasil)
    print(teks)
    try:
        with open(REPORT, "w", encoding="utf-8") as f:
            f.write(teks)
        print(f"Laporan: {REPORT}")
    except Exception as e:  # noqa: BLE001
        print(f"tulis laporan gagal: {e}")

    matang = [(r, h) for r, h in hasil if h.get("ret_10") is not None]
    if len(matang) >= MIN_SAMPEL and not args.dry:
        s_all = _stat(matang)
        s_ya = _stat([(r, h) for r, h in matang if str(r.get("tampil")).lower() == "ya"])
        s_tid = _stat([(r, h) for r, h in matang if str(r.get("tampil")).lower() != "ya"])
        pesan = [f"{len(matang)} kandidat matang · ret_10 {_fmt(s_all)}"]
        if s_ya:
            pesan.append(f"tampil: {_fmt(s_ya)}")
        if s_tid:
            pesan.append(f"kontrol: {_fmt(s_tid)}")
        pesan.append(datetime.now(WIB).strftime("Update %d/%m %H:%M WIB"))
        _ntfy("\n".join(pesan))
    elif len(matang) < MIN_SAMPEL:
        print(f"(belum kirim ntfy — sampel matang {len(matang)} < {MIN_SAMPEL})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
