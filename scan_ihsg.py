#!/usr/bin/env python3
"""scan_ihsg.py — Screening SELURUH SAHAM IDX (IHSG) dalam 2 fase.

Dipakai panel "Screening Mandiri" RisetSaham (tombol 🌐 Semua saham IHSG).

Cara kerja (2 fase — sopan ke sumber data):
  FASE 1  Daftar saham = sweep 11 sektor SAHAM Stockbit (~975 emiten; cache
          harian). Tiap saham diambil riwayat harganya (Yahoo, cache 20 jam),
          dihitung indikator + skor inti V4 → URUTAN SELURUH PASAR.
  FASE 2  Top-K finalis (default 60) diperiksa V7 PENUH (broker/asing/
          fundamental → Stockbit) memakai fungsi scan_satu() dari
          scan_mandiri.py — rantai keputusan identik dgn scan terjadwal.

Kenapa 2 fase: panggilan Stockbit (broker+asing) dibatasi ±2/saham. Kalau semua
975 disuruh lewat Stockbit = ±2000 panggilan → tidak sopan & berisiko sesi.
Fase 1 menyaring dgn data murah (harga) dulu; Stockbit hanya utk finalis.

Output: CSV `kode,skor,mode,entry,sl,tp,entry_ideal,bandar_sesi,bandar_3bln,catatan,tampil,berita_skor,berita_jml` (semua
finalis; `tampil=ya` = sinyal lolos gate — hanya itu yang ditampilkan app;
sisanya dihitung "tak ditampilkan") + baris RINGKASAN utk aplikasi.

LEBIH CEPAT: (1) pra-filter LIKUIDITAS — saham nilai transaksi < Rp 800
juta/hari (±51% daftar) tak pernah diambil riwayat harganya; (2) fase 2
PARALEL 3 worker (sopan). [19 Sep 2026: pra-filter volume DIHAPUS — backtest
1 th menunjukkan tidak membantu; lihat scan_mandiri.py]

Progress stdout: "FASE …" + "PROGRESS i/n LABEL" (dibaca panel app).

Pakai:
    .venv/bin/python scan_ihsg.py                        # penuh (~975 saham)
    .venv/bin/python scan_ihsg.py --limit 50 --top 10    # uji cepat
    .venv/bin/python scan_ihsg.py --refresh-universe     # paksa segarkan daftar
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(ROOT, "idx_alpha_screener")
sys.path.insert(0, SCAN)

import pandas as pd

from data import compute_all_indicators, align_to_market
from scoring import compute_total_score
from data_provider import InvezgoProvider
from scan_mandiri import (scan_satu, siapkan_konteks,   # noqa: E402 — single source
                          MIN_VALUE_20D, MIN_VALUE_LABEL)

WIB = timezone(timedelta(hours=7))
SESS_PATH = "/home/yuan/risetsaham/data/stockbit_session.json"
UNIV_PATH = os.path.join(SCAN, "data", "ihsg_universe.json")
UNIV_TTL_H = 20

# 11 sektor SAHAM IDX di Stockbit (id → nama). Sektor non-saham (Currencies,
# Crypto, Global Index, Reksadana, Others/ETF, Delisted) SENGAJA tidak diikut.
SEKTOR = {
    "1": "Barang Konsumen Primer",
    "2": "Kesehatan",
    "3": "Keuangan",
    "4": "Barang Konsumen Non-Primer",
    "5": "Properti & Real Estat",
    "6": "Perindustrian",
    "7": "Energi",
    "8": "Barang Baku",
    "9": "Infrastruktur",
    "50": "Teknologi",
    "51": "Transportasi & Logistik",
}


# ── Stockbit: daftar seluruh saham (sweep sektor, cache harian) ──────────────

def _sb_get(path: str, sesi: dict, timeout: int = 60):
    url = "https://exodus.stockbit.com" + path
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + sesi["access_token"],
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def sweep_universe(force: bool = False) -> list[dict]:
    """Daftar seluruh saham IDX dari Stockbit (11 sektor saham). Cache 20 jam."""
    if not force and os.path.isfile(UNIV_PATH):
        try:
            c = json.load(open(UNIV_PATH, encoding="utf-8"))
            umur = (time.time() - float(c.get("ts") or 0)) / 3600
            if umur < UNIV_TTL_H and c.get("saham"):
                print(f"daftar saham: cache {len(c['saham'])} saham "
                      f"(umur {umur:.1f} jam)", flush=True)
                return c["saham"]
        except Exception:
            pass

    try:
        with open(SESS_PATH, encoding="utf-8") as f:
            sesi = json.load(f)
    except Exception as e:
        raise RuntimeError(f"file sesi Stockbit tidak terbaca: {e}")
    if not sesi.get("access_token"):
        raise RuntimeError("sesi Stockbit kosong — jalankan scripts/stockbit_renew.py")

    print("daftar saham: sweep 11 sektor Stockbit …", flush=True)
    saham: list[dict] = []
    dilihat: set[str] = set()
    for sid, nama in SEKTOR.items():
        try:
            d = _sb_get(f"/emitten/v3/sector/{sid}/company", sesi)
            data = d.get("data")
            rows = data if isinstance(data, list) else ((data or {}).get("rows") or [])
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise RuntimeError(
                    "sesi Stockbit mati/kadaluarsa — jalankan: cd ~/risetsaham && "
                    "~/.hermes/bin/uv run scripts/stockbit_renew.py")
            print(f"  sektor {sid} ({nama}) gagal: HTTP {e.code}", flush=True)
            continue
        for r in rows:
            sym = str(r.get("symbol") or "").strip().upper()
            if not re.fullmatch(r"[A-Z]{4}", sym) or sym in dilihat:
                continue
            dilihat.add(sym)
            saham.append({
                "sym": sym,
                "nama": str(r.get("name") or "")[:60],
                "sektor": nama,
                "last": r.get("last"),
                "avgvol": r.get("avgvolume"),
                "mc": r.get("marketcap"),
                "vma20": r.get("valuema20"),
            })
        time.sleep(0.3)  # jeda sopan antar sektor

    if len(saham) < 300:
        raise RuntimeError(f"sweep hanya dapat {len(saham)} saham — dibatalkan "
                           "(cek sesi/jaringan Stockbit)")
    try:
        tmp = UNIV_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "saham": saham}, f, ensure_ascii=False)
        os.replace(tmp, UNIV_PATH)
    except Exception:
        pass
    print(f"daftar saham: {len(saham)} saham IDX (segar dari Stockbit)", flush=True)
    return saham


def _nilai_univ(s: dict) -> float | None:
    """Nilai transaksi rata-rata harian (Rp) dari data sweep: utamakan
    `vma20` Stockbit (string → float); fallback avgvol × last. avgvol = 0
    → 0.0 (saham 'zombi'/suspensi — tanpa transaksi, DILEWATI). None hanya
    bila benar-benar tak diketahui (→ saham dipertahankan)."""
    def fnum(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None
    v = fnum(s.get("vma20"))
    if v and v > 0:
        return v
    a, l = fnum(s.get("avgvol")), fnum(s.get("last"))
    if a and l:
        return a * l
    if a == 0:
        return 0.0
    return None


def _lolos_likuid(s: dict) -> bool:
    """Ambang likuiditas (permintaan user 16 Sep): nilai < Rp 800 jt/hari
    dilewati SEBELUM fase 1 — hemat riwayat harga, hasil lebih tradeable."""
    v = _nilai_univ(s)
    return v is None or v >= MIN_VALUE_20D


# ── FASE 1: peringkat seluruh pasar (indikator harga → skor inti V4) ─────────

def fase1_rank(ip, saham: list[dict], regime: str, df_ihsg,
               max_workers: int = 5) -> tuple[list[tuple[str, float]], int]:
    """Hitung skor inti V4 tiap saham (PARALEL, cache diska) → urutan menurun."""
    n = len(saham)
    t0 = time.time()
    hasil: list[tuple[str, float]] = []
    gagal = 0
    done = 0

    def skor_satu(item):
        sym = item["sym"]
        try:
            df = ip.get_historical(sym, period="1y")
            if df is None or df.empty or len(df) < 60:
                return sym, None
            df = compute_all_indicators(df)
            if df_ihsg is not None and not df_ihsg.empty:
                df = align_to_market(df, df_ihsg=df_ihsg).dropna()
            else:
                df["idx_close"] = 0.0
                df["idx_ret_20d"] = 0.0
                df["idx_volatility"] = 0.0
            if len(df) < 30:
                return sym, None
            r = df.iloc[-1]
            if pd.isna(r.get("rsi")):
                return sym, None
            return sym, float(compute_total_score(r, regime))
        except Exception:
            return sym, None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(skor_satu, it) for it in saham]
        for f in as_completed(futs):
            done += 1
            sym, sk = f.result()
            if sk is None:
                gagal += 1
            else:
                hasil.append((sym, sk))
            if done % 10 == 0 or done == n:
                laju = done / max(time.time() - t0, 0.1)
                print(f"PROGRESS {done}/{n} peringkat "
                      f"({laju:.1f}/dtk · tanpa data {gagal})", flush=True)

    hasil.sort(key=lambda x: -x[1])
    print(f"FASE 1 selesai: {len(hasil)} saham dapat skor · "
          f"{gagal} tanpa data cukup · {int(time.time()-t0)} dtk", flush=True)
    return hasil, gagal


# ── FASE 2: finalis diperiksa V7 penuh ──────────────────────────────────────

def fase2_finalis(ip, ranked: list[tuple[str, float]], regime: str, allowed: set,
                  df_ihsg, top: int, sentiment: dict | None = None,
                  max_workers: int = 3) -> list[dict]:
    """V7 penuh utk finalis — PARALEL 3 worker (sopan: maks 3 panggilan
    Stockbit bersamaan, sejalan panduan maxConcurrent stockbit-mcp).
    (19 Sep 2026: pra-filter volume di scan_satu DIHAPUS — semua finalis
    diperiksa penuh; lihat scan_mandiri.scan_satu.)"""
    kandidat = ranked[:top]
    n = len(kandidat)
    print(f"FASE 2: {n} finalis diperiksa V7 penuh (broker/asing/fundamental,"
          f" {max_workers} paralel) …", flush=True)
    rows: list[dict] = []
    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(scan_satu, ip, sym, regime, allowed, df_ihsg, sentiment)
                for sym, _sk4 in kandidat]
        for f in as_completed(futs):
            done += 1
            row = f.result()
            rows.append(row)
            print(f"PROGRESS {done}/{n} {row['kode']}", flush=True)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Screening seluruh saham IHSG (2 fase)")
    ap.add_argument("--out", default=None, help="CSV output")
    ap.add_argument("--top", type=int, default=60, help="jumlah finalis fase 2")
    ap.add_argument("--tampil", type=int, default=120,
                    help="maks baris CSV (default 120 ≥ jumlah finalis)")
    ap.add_argument("--limit", type=int, default=0, help="uji cepat: batasi jumlah saham fase 1")
    ap.add_argument("--refresh-universe", action="store_true", help="paksa sweep ulang")
    a = ap.parse_args()

    out = a.out or os.path.join(SCAN, "data", "ihsg_terakhir.csv")
    t_mulai = time.time()
    print(f"Scan SEMUA SAHAM IDX · {datetime.now(WIB).strftime('%d/%m %H:%M')} WIB",
          flush=True)

    saham = sweep_universe(force=a.refresh_universe)
    if a.limit and a.limit > 0:
        saham = saham[:a.limit]
        print(f"(uji cepat: dibatasi {len(saham)} saham)", flush=True)

    # Pra-filter likuiditas (user 16 Sep): saham nilai transaksi < Rp 800 jt/hari
    # tidak pernah diambil riwayat harganya → fase 1 lebih cepat & hasil tradeable.
    n_dinilai = len(saham)
    saham = [s for s in saham if _lolos_likuid(s)]
    if n_dinilai - len(saham):
        print(f"pra-filter likuiditas: {n_dinilai - len(saham)} saham sepi "
              f"(nilai < {MIN_VALUE_LABEL}/hari / tanpa transaksi) dilewati → "
              f"{len(saham)} saham likuid", flush=True)

    ip, df_ihsg, regime, allowed, sentiment = siapkan_konteks()
    print(f"regime pasar: {regime}", flush=True)

    print("FASE 1: memeringkat seluruh saham (indikator harga, cache diska) …",
          flush=True)
    ranked, _gagal = fase1_rank(ip, saham, regime, df_ihsg)
    if not ranked:
        print("GAGAL: tidak ada satu pun saham yang bisa diskor")
        return 1

    rows = fase2_finalis(ip, ranked, regime, allowed, df_ihsg, a.top,
                         sentiment=sentiment)

    # ── LAB AKURASI: rekam DNA faktor semua finalis (mode bayangan) ──
    try:
        from shadow_log import append_rows as _lab_append
        _sh = [r.pop("_shadow", None) for r in rows]
        n_lab = _lab_append([s for s in _sh if s], sumber="ihsg")
        print(f"LAB: {n_lab} kandidat tercatat (mode bayangan)", flush=True)
    except Exception as e:  # noqa: BLE001 — lab tidak boleh mematikan scan
        print(f"LAB: gagal catat ({type(e).__name__}: {e}) — scan tetap jalan", flush=True)

    def _urut(r):
        try:
            sk = float(r["skor"])
        except Exception:
            sk = -1.0
        return (0 if r["entry"] else 1, -sk)

    rows.sort(key=_urut)
    tampil = rows[:max(a.tampil, 1)]

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kode", "skor", "mode", "entry", "sl", "tp",
                    "entry_ideal", "bandar_sesi", "bandar_3bln", "catatan", "tampil",
                    "berita_skor", "berita_jml"])
        for r in tampil:
            w.writerow([r["kode"], r["skor"], r["mode"], r["entry"], r["sl"],
                        r["tp"], r.get("entry_ideal", ""),
                        r.get("bandar_sesi", ""), r.get("bandar_3bln", ""),
                        r["catatan"], r.get("tampil", ""),
                        r.get("berita_skor", ""), r.get("berita_jml", "")])

    n_sig = sum(1 for r in rows if r["entry"])
    n_sembunyi = sum(1 for r in rows if r.get("tampil") == "tidak")
    dur = int(time.time() - t_mulai)
    print(f"RINGKASAN {len(saham)} saham likuid diperingkat (dari {n_dinilai} IDX; "
          f"{n_dinilai - len(saham)} sepi < {MIN_VALUE_LABEL} dilewati) · "
          f"{len(rows)} finalis V7 · {n_sig} sinyal berlevel · "
          f"{n_sembunyi} tak tampil · {dur // 60}m{dur % 60}s", flush=True)
    print(f"CSV: {out}", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
