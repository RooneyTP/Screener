#!/usr/bin/env python3
"""analisa_ara.py — cari pola kandidat screener yang naik >=15% (ARA-class).

Sumber kandidat: shadow_v7.csv (DNA faktor lengkap) + screener_batch (yang tampil di app).
Hasil ke depan dihitung dari cache_v7/v7_<KODE>_1y.csv (OHLC harian 1 tahun, tanpa jaringan).
"""
import csv
import json
import os
import sqlite3
import statistics as st
from datetime import datetime

DATA = "/home/yuan/screener/idx_alpha_screener/data"
DB = "/home/yuan/risetsaham/data/risetsaham.db"

_cache = {}


def candles(kode):
    if kode in _cache:
        return _cache[kode]
    p = f"{DATA}/cache_v7/v7_{kode}_1y.csv"
    rows = []
    if os.path.exists(p):
        with open(p, newline="") as f:
            rd = csv.reader(f)
            next(rd, None)
            for r in rd:
                if len(r) < 6 or not r[0][:4].isdigit():
                    continue
                try:
                    rows.append((r[0][:10], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    continue
    rows.sort()
    _cache[kode] = rows
    return rows


def hasil_ke_depan(kode, pick, entry, maks_hari=10):
    rows = candles(kode)
    if not rows:
        return None
    idx0 = None
    for i, r in enumerate(rows):
        if r[0] >= pick:
            idx0 = i
            break
    if idx0 is None:
        return None
    fwd = rows[idx0 + 1: idx0 + 1 + maks_hari]
    if not fwd:
        return {"hari": 0, "max_gain": None}
    max_gain = max(r[2] / entry - 1 for r in fwd)
    max_close = max(r[4] / entry - 1 for r in fwd)
    best_1d, best_1d_tgl = 0.0, None
    prev = rows[idx0][4]
    for r in fwd:
        g = r[4] / prev - 1
        if g > best_1d:
            best_1d, best_1d_tgl = g, r[0]
        prev = r[4]
    return {"hari": len(fwd), "max_gain": max_gain, "max_close": max_close,
            "best_1d": best_1d, "best_1d_tgl": best_1d_tgl,
            "tgl_awal": fwd[0][0], "tgl_akhir": fwd[-1][0], "last_date": rows[-1][0]}


# ── kandidat dari shadow (DNA) ────────────────────────────────
kandidat = {}
with open(f"{DATA}/shadow_v7.csv", newline="") as f:
    for row in csv.DictReader(f):
        k = (row["kode"], row["tanggal"])
        kandidat[k] = {"kode": row["kode"], "tgl": row["tanggal"], "sumber": "shadow:" + row["sumber"],
                       "skor": row["skor"], "label": row["label"], "mode": row["mode"],
                       "tampil": row["tampil"], "alasan": row["alasan"], "harga": row["harga"],
                       "broker_flow": row["broker_flow"], "flow_spike": row["flow_spike"],
                       "foreign_flow": row["foreign_flow"], "vol_ratio": row["vol_ratio"],
                       "bandar_sesi": row["bandar_sesi"], "bandar_3bln": row["bandar_3bln"],
                       "v4_core": row["v4_core"], "regime": row["regime"], "conflict": row["conflict"],
                       "earnings": row["earnings_momentum"], "weekly": row["weekly_trend"]}

# ── kandidat dari batch DB (yang masuk app) ───────────────────
con = sqlite3.connect(DB)
for bid, ts, sumber, n, rows_json in con.execute("SELECT id, ts, sumber, n, rows_json FROM screener_batch"):
    d = json.loads(rows_json)
    kolom = d["kolom"]
    tanggal = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    for item in d["baris"]:
        kode = item["kode"]
        data = dict(zip(kolom, item["data"]))
        k = (kode, tanggal)
        c = kandidat.setdefault(k, {"kode": kode, "tgl": tanggal, "sumber": "batch"})
        c["batch"] = f"{sumber}#{bid}"
        c["tampil_batch"] = data.get("tampil")
        c["catatan"] = data.get("catatan")
        try:
            c["entry_batch"] = float(data.get("entry"))
        except (TypeError, ValueError):
            pass
        if not c.get("bandar_sesi"):
            c["bandar_sesi"] = data.get("bandar_sesi")
        c["berita_skor"] = data.get("berita_skor")

# ── hitung hasil ──────────────────────────────────────────────
out = []
for c in kandidat.values():
    entry = None
    for src in ("harga", "entry_batch"):
        try:
            entry = float(c.get(src))
            break
        except (TypeError, ValueError):
            continue
    if not entry:
        continue
    c["entry"] = entry
    c["hasil"] = hasil_ke_depan(c["kode"], c["tgl"], entry)
    out.append(c)

matang = [c for c in out if c.get("hasil") and c["hasil"].get("hari", 0) >= 3]
print(f"Total kandidat unik (kode,tgl): {len(out)}")
print(f"Matang (>=3 hari bursa data lanjutan): {len(matang)}")
if matang:
    ld = sorted({c['hasil'].get('last_date') for c in matang})
    print(f"Coverage data terakhir: {ld[-1]}")


def g(c):
    try:
        return c["hasil"]["max_gain"]
    except Exception:
        return -9


winners = sorted([c for c in matang if g(c) >= 0.15], key=g, reverse=True)
print(f"\n=== NAIK >=15% (max high dalam <=10 hari) : {len(winners)} kasus ===\n")
for c in winners:
    h = c["hasil"]
    print(f"  {c['kode']:5} {c['tgl']}  entry={c['entry']:g}  max={h['max_gain']*100:5.1f}%  "
          f"best1d={h['best_1d']*100:5.1f}% ({h['best_1d_tgl']})  skor={c.get('skor','')}  "
          f"tampil={c.get('tampil') or c.get('tampil_batch','')}  alasan={c.get('alasan','')}  "
          f"bandarSesi={c.get('bandar_sesi','')}  volRatio={c.get('vol_ratio','')}  "
          f"flowSpike={c.get('flow_spike','')}  brokerFlow={c.get('broker_flow','')}  berita={c.get('berita_skor','')}")

# lonjakan 1 hari
print("\n=== LONJAKAN 1 HARI >=14% (close-to-close) di antara kandidat ===")
ara = sorted([c for c in matang if c["hasil"]["best_1d"] >= 0.14], key=lambda c: -c["hasil"]["best_1d"])
for c in ara:
    h = c["hasil"]
    print(f"  {c['kode']:5} {c['tgl']}  lompat={h['best_1d']*100:5.1f}% pada {h['best_1d_tgl']}  "
          f"max={h['max_gain']*100:5.1f}%  alasan={c.get('alasan','')}  flowSpike={c.get('flow_spike','')}  "
          f"bandarSesi={c.get('bandar_sesi','')}")


def angka(c, key):
    v = c.get(key)
    if v in (None, "", "None"):
        v = c.get(key + "_batch")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


print("\n=== PROFIL FAKTOR: pemenang(>=15%) vs semua matang ===")
for key in ("skor", "v4_core", "broker_flow", "flow_spike", "foreign_flow", "vol_ratio",
            "bandar_sesi", "earnings", "weekly"):
    w = [angka(c, key) for c in winners]
    w = [x for x in w if x is not None]
    a = [angka(c, key) for c in matang]
    a = [x for x in a if x is not None]
    if w and a:
        print(f"  {key:12}  winners med={st.median(w):8.1f}  n={len(w):3}   |   semua med={st.median(a):8.1f}  n={len(a)}")

print("\n=== HIT-RATE per kondisi (P naik >=15% | kondisi) ===")


def hit(pred, nama):
    sel = [c for c in matang if pred(c)]
    if not sel:
        print(f"  {nama:42} n=0")
        return
    w = sum(1 for c in sel if g(c) >= 0.15)
    base = sum(1 for c in matang if g(c) >= 0.15) / len(matang) * 100
    print(f"  {nama:42} n={len(sel):3}  hit={w/len(sel)*100:5.1f}%  (baseline {base:.1f}%)")


hit(lambda c: (angka(c, "broker_flow") or 0) >= 65, "broker_flow >= 65")
hit(lambda c: (angka(c, "flow_spike") or 0) >= 1, "flow_spike = 1")
hit(lambda c: (angka(c, "vol_ratio") or 0) >= 2, "vol_ratio >= 2")
hit(lambda c: (angka(c, "vol_ratio") or 0) < 0.5, "vol_ratio < 0.5")
hit(lambda c: (angka(c, "skor") or 0) >= 65, "skor >= 65")
hit(lambda c: (angka(c, "bandar_sesi") or 0) > 0, "bandar_sesi > 0 (ada net buy bandar)")
hit(lambda c: (c.get("alasan") or "") == "sinyal_swing", "alasan = sinyal_swing")
hit(lambda c: "diblok" in (c.get("alasan") or ""), "alasan = diblok_* (tak tampil)")
hit(lambda c: (c.get("tampil") or c.get("tampil_batch") or "") == "ya", "tampil = ya (jadi sinyal di app)")

im = [c for c in out if not (c.get("hasil") and c["hasil"].get("hari", 0) >= 3)]
print(f"\n(Belum matang, dikecualikan: {len(im)} kandidat — sinyal 23-24 Sep yang datanya belum cukup)")
