#!/usr/bin/env python3
"""kualitas2.py — Studi kualitas screening V7 dari kandidat LIVE (16–24 Sep 2026).

Sumber: screener_batch (DB RisetSaham) + shadow_v7.csv + cache_v7/ harga harian.
Sasaran: pola naik vs TURUN — supaya kualitas screening bisa diperbaiki berbasis bukti.
Output: ringkasan stdout + data/kandidat_hasil_live.csv
"""
import csv
import json
import os
import sqlite3
import statistics as st
from datetime import datetime, timedelta, timezone

DATA = "/home/yuan/screener/idx_alpha_screener/data"
DB = "/home/yuan/risetsaham/data/risetsaham.db"
WIB = timezone(timedelta(hours=7))
LAST = "2026-09-24"

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
                    rows.append([r[0][:10], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
                except ValueError:
                    continue
    rows.sort(key=lambda r: r[0])
    _cache[kode] = rows
    return rows


def num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None


# ---------- KUMPULKAN KANDIDAT ----------
kand = {}
con = sqlite3.connect(DB)
for bid, ts, sumber, n, rows_json in con.execute("SELECT id, ts, sumber, n, rows_json FROM screener_batch ORDER BY ts"):
    try:
        d = json.loads(rows_json)
    except Exception:
        continue
    kolom = [str(cl).strip().lower() for cl in d.get("kolom", [])]
    tgl = datetime.fromtimestamp(ts, WIB).strftime("%Y-%m-%d")
    for item in d.get("baris", []):
        kode = str(item.get("kode", "")).upper()
        dd = dict(zip(kolom, item.get("data") or []))
        c = kand.setdefault((kode, tgl), {"kode": kode, "tgl": tgl})
        c["bid"] = f"{sumber}#{bid}"
        for f in ("skor", "mode", "entry", "sl", "tp", "catatan", "entry_ideal",
                  "bandar_sesi", "bandar_3bln", "berita_skor", "tampil"):
            v = dd.get(f)
            if v not in (None, ""):
                c[f] = v
        c["n_kirim"] = c.get("n_kirim", 0) + 1

for row in csv.DictReader(open(f"{DATA}/shadow_v7.csv", newline="")):
    c = kand.setdefault((row["kode"], row["tanggal"]), {"kode": row["kode"], "tgl": row["tanggal"]})
    for f in ("skor", "label", "mode", "tampil", "alasan", "regime", "v4_core", "broker_flow",
              "broker_flow_raw", "broker_trend", "flow_spike", "conflict", "foreign_flow",
              "fundamental", "earnings_momentum", "weekly_trend", "harga", "atr_pct", "vol_ratio",
              "sl", "tp", "bandar_sesi", "bandar_3bln", "berita_delta"):
        v = row.get(f)
        if v not in (None, ""):
            c["dna_" + f] = v
            if f in ("skor", "mode", "tampil", "sl", "tp") and not c.get(f):
                c[f] = v

# ---------- HITUNG HASIL KE DEPAN ----------
hasil = []
for c in kand.values():
    if c["tgl"] > LAST:
        continue
    rows = candles(c["kode"])
    if not rows:
        continue
    i0 = None
    for i, r in enumerate(rows):
        if r[0] >= c["tgl"]:
            i0 = i
            break
    if i0 is None or i0 + 1 >= len(rows) + 1:
        continue
    close0 = rows[i0][4]
    entry = num(c.get("entry")) or close0
    fwd = rows[i0 + 1:i0 + 1 + 15]
    h = {"n_fwd": len(fwd), "entry": entry, "close0": close0,
         "gap1": (fwd[0][1] / close0 - 1) if fwd else None}
    if fwd:
        h["maxup"] = max(r[2] for r in fwd) / entry - 1
        h["maxdd"] = min(r[3] for r in fwd) / entry - 1
        for n, nm in ((1, "ret1"), (3, "ret3"), (5, "ret5"), (10, "ret10")):
            h[nm] = (fwd[n - 1][4] / entry - 1) if len(fwd) >= n else None
        h["lastret"] = fwd[-1][4] / entry - 1
        tp, sl = num(c.get("tp")), num(c.get("sl"))
        h["hit"] = None
        h["hit_hari"] = None
        if tp and sl and tp > 0 and sl > 0:
            for i, r in enumerate(fwd):
                ht, hs = r[2] >= tp, r[3] <= sl
                if ht or hs:
                    h["hit"] = "both" if (ht and hs) else ("tp" if ht else "sl")
                    h["hit_hari"] = i + 1
                    break
            if not h["hit"]:
                h["hit"] = "naik" if (h["lastret"] or 0) > 0.005 else ("turun" if (h["lastret"] or 0) < -0.005 else "datar")
    c["h"] = h
    hasil.append(c)

print(f"Total kandidat (kode,tgl): {len(hasil)} | ada hari lanjut: {sum(1 for c in hasil if c['h']['n_fwd'])}")
matang = [c for c in hasil if c["h"]["n_fwd"] >= 3]
print(f"Matang (>=3 hari data lanjut): {len(matang)} | belum matang: {len(hasil) - len(matang)}")

# ---------- A. DISTRIBUSI SEMUA KANDIDAT (matang) ----------
print("\n══════ A. DISTRIBUSI HASIL SEMUA KANDIDAT MATANG (basis entry/close hari pick) ══════")


def pct(sel, cond):
    return 100.0 * sum(1 for c in sel if cond(c["h"])) / len(sel) if sel else 0.0


def stat(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return "n/a"
    return f"med {st.median(vals)*100:+.1f}%  rata2 {st.mean(vals)*100:+.1f}%  n={len(vals)}"


m = [c for c in matang if c["h"].get("maxup") is not None]
print(f"n={len(m)}")
print(f"  maxup >=5%: {pct(m, lambda h: h['maxup']>=0.05):5.1f}% | >=10%: {pct(m, lambda h: h['maxup']>=0.10):5.1f}% | >=15%: {pct(m, lambda h: h['maxup']>=0.15):5.1f}%")
print(f"  maxdd <=-5%: {pct(m, lambda h: h['maxdd']<=-0.05):5.1f}% | <=-10%: {pct(m, lambda h: h['maxdd']<=-0.10):5.1f}% | <=-15%: {pct(m, lambda h: h['maxdd']<=-0.15):5.1f}%")
print(f"  lastret: {stat([c['h'].get('lastret') for c in m])}")
print(f"  ret3:    {stat([c['h'].get('ret3') for c in m])}")
print(f"  ret5:    {stat([c['h'].get('ret5') for c in m])}")
# perubahan 1 hari setelah pick (close0 -> close1) = 'besoknya turun/naik'
r1 = [c["h"].get("ret1") for c in m if c["h"].get("ret1") is not None]
if r1:
    turun = sum(1 for v in r1 if v < 0)
    print(f"  besoknya (ret1): {stat(r1)} | turun: {turun}/{len(r1)} ({100*turun/len(r1):.0f}%)")
g1 = [c["h"].get("gap1") for c in m if c["h"].get("gap1") is not None]
if g1:
    gt = sum(1 for v in g1 if v > 0.01)
    print(f"  gap pagi berikutnya: {stat(g1)} | buka >+1%: {gt}/{len(g1)} ({100*gt/len(g1):.0f}%)")

# ---------- B. EPISODE SINYAL (tampil=ya + entry/sl/tp) ----------
print("\n══════ B. EPISODE SINYAL (tampil=ya, dedup ala screener_hasil) ══════")
sigs = [c for c in hasil if str(c.get("tampil", "")).lower() == "ya" and num(c.get("entry")) and num(c.get("sl")) and num(c.get("tp"))]
sigs.sort(key=lambda c: (c["kode"], c["tgl"]))
eps = []
for s in sigs:
    if eps and eps[-1]["kode"] == s["kode"]:
        e = eps[-1]
        gap = (datetime.strptime(s["tgl"], "%Y-%m-%d") - datetime.strptime(e["tgl_akhir"], "%Y-%m-%d")).days
        if gap <= 4 and abs(s["h"]["entry"] / e["h"]["entry"] - 1) <= 0.02:
            e["tgl_akhir"] = s["tgl"]
            e["n"] += 1
            continue
    eps.append({**s, "tgl_akhir": s["tgl"], "n": 1})

c_tp = c_sl = c_both = c_open = 0
for e in sorted(eps, key=lambda x: (x["tgl"], x["kode"])):
    h = e["h"]
    stt = h.get("hit") or "?"
    if stt == "tp": c_tp += 1
    elif stt == "sl": c_sl += 1
    elif stt == "both": c_both += 1
    else: c_open += 1
    print(f"  {e['kode']:5} {e['tgl']}→{e['tgl_akhir']} E{h['entry']:>8.0f} SL{num(e.get('sl')) or 0:>8.0f} TP{num(e.get('tp')) or 0:>8.0f} "
          f"| {stt:5} h{h.get('hit_hari') or '-'} | maxup {(h.get('maxup') or 0)*100:+5.1f}% maxdd {(h.get('maxdd') or 0)*100:+6.1f}% "
          f"last {(h.get('lastret') or 0)*100:+5.1f}% | skim={e.get('skor','')} cat={str(e.get('catatan',''))[:40]}")
print(f"  → TP {c_tp} · SL {c_sl} · both {c_both} · terbuka {c_open} (total {len(eps)} episode)")

# ---------- C. COHORT: TAMPIL vs DIBLOK vs SEMUA ----------
print("\n══════ C. COHORT (matang) — mana yang paling sering TURUN? ══════")


def baris(nama, sel):
    if not sel:
        print(f"  {nama:38} n=0")
        return
    sel = [c for c in sel if c["h"].get("maxup") is not None]
    if not sel:
        print(f"  {nama:38} n=0")
        return
    n = len(sel)
    d5 = 100 * sum(1 for c in sel if c["h"]["maxdd"] <= -0.05) / n
    d10 = 100 * sum(1 for c in sel if c["h"]["maxdd"] <= -0.10) / n
    u5 = 100 * sum(1 for c in sel if c["h"]["maxup"] >= 0.05) / n
    med = st.median([c["h"]["lastret"] for c in sel if c["h"].get("lastret") is not None] or [0])
    print(f"  {nama:38} n={n:3} | dd<=-5%: {d5:4.0f}% dd<=-10%: {d10:4.0f}% | up>=5%: {u5:4.0f}% | med last {med*100:+5.1f}%")


def tam(c):
    return str(c.get("tampil", "")).lower()


baris("SEMUA kandidat", m)
baris("tampil=ya", [c for c in m if tam(c) == "ya"])
baris("tampil=tidak", [c for c in m if tam(c) == "tidak"])
baris("catatan ada 'izin regime' (diblok)", [c for c in m if "izin regime" in str(c.get("catatan", "")).lower()])
baris("alasan=diblok_* (shadow)", [c for c in m if str(c.get("dna_alasan", "")).startswith("diblok")])
baris("alasan=sinyal_* (shadow)", [c for c in m if str(c.get("dna_alasan", "")).startswith("sinyal")])

print("\n  -- per band skor --")
for lo, hi in ((0, 50), (50, 55), (55, 60), (60, 65), (65, 100)):
    baris(f"skor [{lo},{hi})", [c for c in m if (num(c.get("skor")) or (num(c.get("dna_skor")) or 0)) >= lo and (num(c.get("skor")) or (num(c.get("dna_skor")) or 0)) < hi])

print("\n  -- bandar net buy --")


def bs(c):
    v = num(c.get("bandar_sesi")) or num(c.get("dna_bandar_sesi"))
    return v or 0


baris("bandar_sesi > 0", [c for c in m if bs(c) > 0])
baris("bandar_sesi = 0/kosong", [c for c in m if bs(c) <= 0])

print("\n  -- faktor DNA (hanya 21–24 Sep) --")
md = [c for c in m if c.get("dna_v4_core")]
if md:
    for lo, hi in ((0, 45), (45, 55), (55, 65), (65, 100)):
        baris(f"v4_core [{lo},{hi})", [c for c in md if (num(c.get("dna_v4_core")) or 0) >= lo and (num(c.get("dna_v4_core")) or 0) < hi])
    for lo, hi in ((0, 0.5), (0.5, 1), (1, 2), (2, 100)):
        baris(f"vol_ratio [{lo},{hi})", [c for c in md if (num(c.get("dna_vol_ratio")) or 0) >= lo and (num(c.get("dna_vol_ratio")) or 0) < hi])
    for lo, hi in ((0, 2), (2, 4), (4, 6), (6, 100)):
        baris(f"atr_pct [{lo},{hi})", [c for c in md if (num(c.get("dna_atr_pct")) or 0) >= lo and (num(c.get("dna_atr_pct")) or 0) < hi])
    baris("weekly_trend BULLISH", [c for c in md if str(c.get("dna_weekly_trend","")).upper() == "BULLISH"])
    baris("weekly_trend BEARISH", [c for c in md if str(c.get("dna_weekly_trend","")).upper() == "BEARISH"])
    baris("broker_flow >=65", [c for c in md if (num(c.get("dna_broker_flow")) or 0) >= 65])
    baris("flow_spike=1", [c for c in md if (num(c.get("dna_flow_spike")) or 0) == 1])

# ---------- D. YANG PALING TURUN & PALING NAIK ----------
print("\n══════ D. DAFTAR EKSTREM (matang) ══════")
mx = sorted(m, key=lambda c: c["h"]["maxdd"])
print("  — 15 TURUN TERDALAM (maxdd) —")
for c in mx[:15]:
    print(f"  {c['kode']:5} {c['tgl']}  dd {(c['h']['maxdd'] or 0)*100:+6.1f}%  maxup {(c['h']['maxup'] or 0)*100:+5.1f}%  "
          f"last {(c['h']['lastret'] or 0)*100:+5.1f}%  skim={c.get('skor','')}  tampil={tam(c)}  "
          f"vol={c.get('dna_vol_ratio','')} atr={c.get('dna_atr_pct','')} bandar={c.get('bandar_sesi','')}")
print("  — 12 NAIK TERTINGGI (maxup) —")
for c in sorted(m, key=lambda c: -(c["h"]["maxup"] or 0))[:12]:
    print(f"  {c['kode']:5} {c['tgl']}  maxup {(c['h']['maxup'] or 0)*100:+6.1f}%  dd {(c['h']['maxdd'] or 0)*100:+6.1f}%  "
          f"last {(c['h']['lastret'] or 0)*100:+5.1f}%  skim={c.get('skor','')}  tampil={tam(c)}  "
          f"vol={c.get('dna_vol_ratio','')} atr={c.get('dna_atr_pct','')} bandar={c.get('bandar_sesi','')}")

# ---------- E. CSV ----------
outp = f"{DATA}/kandidat_hasil_live.csv"
with open(outp, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["kode", "tgl", "skor", "mode", "tampil", "alasan", "entry", "sl", "tp", "catatan",
                "bandar_sesi", "vol_ratio", "atr_pct", "v4_core", "broker_flow", "foreign_flow",
                "weekly_trend", "n_fwd", "gap1", "maxup", "maxdd", "ret1", "ret3", "ret5", "lastret", "hit", "hit_hari"])
    for c in sorted(hasil, key=lambda x: (x["tgl"], x["kode"])):
        h = c["h"]
        w.writerow([c["kode"], c["tgl"], c.get("skor", ""), c.get("mode", ""), tam(c), c.get("dna_alasan", c.get("alasan", "")),
                    h["entry"], c.get("sl", ""), c.get("tp", ""), str(c.get("catatan", ""))[:80],
                    c.get("bandar_sesi", ""), c.get("dna_vol_ratio", ""), c.get("dna_atr_pct", ""),
                    c.get("dna_v4_core", ""), c.get("dna_broker_flow", ""), c.get("dna_foreign_flow", ""),
                    c.get("dna_weekly_trend", ""), h["n_fwd"], h.get("gap1"), h.get("maxup"), h.get("maxdd"),
                    h.get("ret1"), h.get("ret3"), h.get("ret5"), h.get("lastret"), h.get("hit"), h.get("hit_hari")])
print(f"\nCSV: {outp}")
