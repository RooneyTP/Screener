"""shadow_log.py — Lab Akurasi V7: "mode bayangan" (rekam DNA faktor finalis).

Setiap kandidat yang sampai ke perhitungan V7 penuh (punya faktor broker/asing/
fundamental) dicatat ke data/shadow_v7.csv — TERMASUK yang tidak jadi sinyal
(diblok regime / gagal gate). Tujuan: setelah data terkumpul beberapa minggu,
diukur faktor mana yang benar-benar memisahkan menang vs kalah. Sisi
broker/asing (45% skor V7) TIDAK bisa di-backtest (datanya tanpa riwayat) —
satu-satunya cara mengukur = merekam MAJU (forward) lalu menilai hasilnya
via shadow_eval.py setelah cukup umur.

Dipanggil dari:
  - scan_mandiri.py main()  → sumber "mandiri"
  - scan_ihsg.py main()     → sumber "ihsg" (60 finalis/hari, sumber data utama)

Kolom: lihat FIELDS (27 kolom — identitas + DNA faktor + hasil keputusan).
Baris ditulis append-only + timestamp (tanggal, jam, WIB). Duplikat antar-run
di hari yang sama dibersihkan saat EVALUASI (ambil yang terakhir per
tanggal+kode) — bukan saat tulis, supaya penulisan tetap sederhana & bebas
race antar proses.

Override path utk uji: env SCREENER_SHADOW_CSV (mis. /tmp/shadow_test.csv).
"""
from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

FIELDS = [
    # identitas & keputusan
    "tanggal", "jam", "sumber", "kode", "regime", "skor", "label", "mode",
    "tampil", "alasan",
    # DNA faktor (sisi yang tidak bisa di-backtest + teknikal pendukung)
    "v4_core", "broker_flow", "broker_flow_raw", "broker_trend", "flow_spike",
    "conflict", "foreign_flow", "fundamental", "earnings_momentum", "weekly_trend",
    # harga & level
    "harga", "atr_pct", "vol_ratio", "sl", "tp", "bandar_sesi", "bandar_3bln",
]

# Nilai `alasan` yang mungkin:
#   sinyal_swing / sinyal_intraday = tampil sebagai sinyal
#   diblok_regime                  = label di luar izin regime (mis. WEAK_BUY di RANGING)
#   gagal_gate                     = lolos izin tapi gagal gate swing & intraday


def shadow_path() -> str:
    """Path file shadow. Default idx_alpha_screener/data/shadow_v7.csv."""
    return os.environ.get("SCREENER_SHADOW_CSV") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "data", "shadow_v7.csv")


def append_rows(rows: list[dict], sumber: str) -> int:
    """Tulis baris shadow (append-only). Return jumlah baris tertulis.

    Baris tanpa kode dilewati. Field yang tidak ada → string kosong.
    Tidak pernah raise ke pemanggil? — TIDAK: raise tetap dibiarkan supaya
    pemanggil yang membungkus try/except (scan tetap jalan walau lab gagal).
    """
    rows = [r for r in rows if r and r.get("kode")]
    if not rows:
        return 0
    path = shadow_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    now = datetime.now(WIB)
    baru = not os.path.isfile(path)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if baru:
            w.writeheader()
        n = 0
        for r in rows:
            r = dict(r)
            r.setdefault("tanggal", now.strftime("%Y-%m-%d"))
            r.setdefault("jam", now.strftime("%H:%M:%S"))
            r["sumber"] = r.get("sumber") or sumber
            w.writerow(r)   # field yang tak ada → "" (restval default)
            n += 1
    return n


def read_rows(path: str | None = None) -> list[dict]:
    """Baca semua baris shadow (utk shadow_eval)."""
    path = path or shadow_path()
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))
