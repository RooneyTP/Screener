#!/usr/bin/env python3
"""kirim_ke_risetsaham.py — Kirim hasil scan V7 ke RisetSaham (halaman /screener).

Sumber: idx_alpha_screener/data/perf_tracker_v7.csv (log sinyal V7; kolom
date, ticker, mode, score, entry_price, sl, tp, fresh, regime, ...).
Hanya baris bertanggal HARI INI (WIB) yang dikirim.

Cara pakai:
    .venv/bin/python kirim_ke_risetsaham.py             # kirim (hari ini WIB)
    .venv/bin/python kirim_ke_risetsaham.py --dry       # tampilkan saja
    .venv/bin/python kirim_ke_risetsaham.py --file /path/uji.csv --tanpa-notif

Endpoint RisetSaham: POST /riset/screener/api (header X-Screener-Token; token
dibaca dari /home/yuan/risetsaham/.env — TIDAK disimpan di repo ini).
Di app, batch dari sumber "V7 Alpha" tampil sebagai kartu visual
(skor + grafik + garis Entry/SL/TP + harga live).
"""
import argparse
import csv
import io
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
HERE = os.path.dirname(os.path.abspath(__file__))
PERF_CSV = os.path.join(HERE, "idx_alpha_screener", "data", "perf_tracker_v7.csv")
RISET_ENV = "/home/yuan/risetsaham/.env"
BASE_API = "http://127.0.0.1:5400/riset/screener/api"
KODE_RE = re.compile(r"^[A-Z]{3,4}$")


def baca_token_risetsaham():
    """Token API RisetSaham — dari env SCREENER_TOKEN atau .env RisetSaham."""
    token = os.environ.get("SCREENER_TOKEN", "").strip()
    if token:
        return token
    if os.path.isfile(RISET_ENV):
        with open(RISET_ENV, encoding="utf-8", errors="replace") as f:
            for baris in f:
                if baris.strip().startswith("SCREENER_TOKEN="):
                    return baris.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def ambil_sinyal(path, tanggal):
    """Baca baris CSV sinyal bertanggal `tanggal` (YYYY-MM-DD)."""
    if not os.path.isfile(path):
        return [], f"CSV tidak ditemukan: {path} (scan belum pernah jalan?)"
    baris = []
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        for r in csv.DictReader(f):
            if (r.get("date") or "")[:10] == tanggal:
                baris.append(r)
    return baris, ""


def ke_csv_riset(rows):
    """Bentuk CSV ringkas utk halaman /screener: kode, skor, mode, entry, sl, tp, catatan."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["kode", "skor", "mode", "entry", "sl", "tp", "catatan"])
    n = 0
    for r in rows:
        kode = (r.get("ticker") or "").strip().upper()
        if not KODE_RE.match(kode):
            continue
        fresh = str(r.get("fresh") or "").strip().lower()
        status = "baru" if fresh in ("1", "1.0", "true") else "lanjutan"
        catatan = status
        regime = (r.get("regime") or "").strip()
        if regime and regime != "unknown":
            catatan += f" \u00b7 {regime}"
        mode = "SWING" if str(r.get("mode")) == "swing" else "INTRADAY"
        w.writerow([kode, r.get("score") or "", mode,
                    r.get("entry_price") or "", r.get("sl") or "", r.get("tp") or "",
                    catatan])
        n += 1
    return out.getvalue(), n


def kirim(body, notif=True):
    token = baca_token_risetsaham()
    if not token:
        print("GAGAL: SCREENER_TOKEN tidak ditemukan (cek ~/risetsaham/.env).")
        return 1
    url = BASE_API + "?sumber=V7+Alpha" + ("&notif=1" if notif else "")
    req = urllib.request.Request(
        url,
        data=body.encode("utf-8"),
        headers={"X-Screener-Token": token,
                 "Content-Type": "text/csv; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            hasil = resp.read().decode("utf-8", errors="replace")
        print("TERKIRIM ke RisetSaham:", hasil)
        return 0
    except Exception as e:
        print(f"GAGAL kirim: {type(e).__name__}: {e}")
        return 1


def main():
    ap = argparse.ArgumentParser(description="Kirim sinyal V7 hari ini ke RisetSaham")
    ap.add_argument("--dry", action="store_true", help="tampilkan saja, tidak kirim")
    ap.add_argument("--tanpa-notif", action="store_true",
                    help="jangan minta notifikasi HP dari app (utk uji)")
    ap.add_argument("--file", default=None,
                    help="CSV sumber (default: idx_alpha_screener/data/perf_tracker_v7.csv)")
    ap.add_argument("--tanggal", default=None, help="YYYY-MM-DD (default: hari ini WIB)")
    a = ap.parse_args()

    path = a.file or PERF_CSV
    tgl = a.tanggal or datetime.now(WIB).strftime("%Y-%m-%d")
    rows, err = ambil_sinyal(path, tgl)
    if err:
        print("GAGAL:", err)
        return 1
    if not rows:
        print(f"Tidak ada sinyal V7 tercatat untuk {tgl} — tidak ada yang dikirim.")
        return 0

    body, n = ke_csv_riset(rows)
    print(f"Sinyal {tgl}: {len(rows)} baris ditemukan, {n} dikirim setelah filter kode.")
    print(body.rstrip())
    if a.dry:
        print("(--dry: tidak dikirim)")
        return 0
    return kirim(body, notif=not a.tanpa_notif)


if __name__ == "__main__":
    sys.exit(main())
