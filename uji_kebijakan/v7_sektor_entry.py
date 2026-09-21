#!/usr/bin/env python3
"""Dua uji baru (lanjutan keluarga gate & eksekusi):
1) GATE SEKTOR — momentum sektor (median ret20 anggota) sebagai filter sinyal.
2) EKSEKUSI NYATA — entry di OPEN besok (realistis) vs close sinyal (asumsi backtest):
   ukur gap buka, dan apakah edge berubah. Plus performa per kantung gap.
Metode tetap: walk-forward 2 paruh + paired + bootstrap.
"""
import csv, glob, json, os, random
from statistics import median

CACHE = "/home/yuan/screener/idx_alpha_screener/data/cache_v7"
P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
UNIV = "/home/yuan/screener/idx_alpha_screener/data/ihsg_universe.json"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# ---- bars per saham ----
bars = {}
for path in glob.glob(os.path.join(CACHE, "v7_*.csv")):
    code = os.path.basename(path)[3:-7]
    arr = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            o, h, l, c = fnum(r["Open"]), fnum(r["High"]), fnum(r["Low"]), fnum(r["Close"])
            if None not in (o, h, l, c):
                arr.append((r["Date"][:10], o, h, l, c))
    arr.sort()
    bars[code] = arr

# ---- sektor map & ret20 series ----
sec_map = {}
with open(UNIV, encoding="utf-8") as f:
    for s in json.load(f)["saham"]:
        sec_map[s["sym"].upper()] = s.get("sektor", "")

ret20 = {}   # code -> {date: ret20 %}
for code, arr in bars.items():
    closes = [c for _, _, _, _, c in arr]
    ret20[code] = {arr[i][0]: (closes[i] / closes[i-20] - 1) * 100 for i in range(20, len(arr))}

sec_ret = {}  # date -> {sector: median ret20}
tmp = {}
for code, dmap in ret20.items():
    sec = sec_map.get(code, "")
    if not sec: continue
    for d, v in dmap.items():
        tmp.setdefault(d, {}).setdefault(sec, []).append(v)
for d, secs in tmp.items():
    sec_ret[d] = {s: median(v) for s, v in secs.items() if len(v) >= 4}

# ---- signals ----
rows = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] == "True" and r["vol1"] == "True" and r["regime"] == "RANGING" and (fnum(r["atrp"]) or 0) > 0:
            if r["code"] in bars and r["date"] in sec_ret and sec_map.get(r["code"]) in sec_ret[r["date"]]:
                rows.append(dict(code=r["code"], date=r["date"], atrp=fnum(r["atrp"]), close=fnum(r["close"]),
                                 sector=sec_map[r["code"]],
                                 tph30=fnum(r["tph30"]) or 0, slh15=fnum(r["slh15"]) or 0))

def outcome(r):
    a = r["atrp"]; tpd = int(r["tph30"]); sld = int(r["slh15"])
    if tpd > 0 and (sld == 0 or tpd < sld): ret = 3.0*a
    elif sld > 0 and (tpd == 0 or sld < tpd): ret = -1.5*a
    elif tpd > 0 and sld > 0: ret = -1.5*a
    else: return None   # butuh ret20f — untuk paruh ini pakai None → di-skip, konsisten antar gate
    return ret - COST

vals = {i: outcome(r) for i, r in enumerate(rows)}
dates = sorted({r["date"] for r in rows}); mid = dates[len(dates)//2]
b_all = [v for v in vals.values() if v is not None]
b1 = [vals[i] for i, r in enumerate(rows) if r["date"] < mid and vals[i] is not None]
b2 = [vals[i] for i, r in enumerate(rows) if r["date"] >= mid and vals[i] is not None]
print(f"Sinyal RANGING dengan data sektor: {len(rows)} (n valid {len(b_all)})")
print(f"Baseline exp: ALL {sum(b_all)/len(b_all):+.2f}% | H1 {sum(b1)/len(b1):+.2f}% | H2 {sum(b2)/len(b2):+.2f}%\n")

def gate(name, fn):
    keep_idx = [i for i, r in enumerate(rows) if vals[i] is not None and fn(rows[i])]
    k = [vals[i] for i in keep_idx]
    k1 = [vals[i] for i in keep_idx if rows[i]["date"] < mid]
    k2 = [vals[i] for i in keep_idx if rows[i]["date"] >= mid]
    no = [vals[i] for i in range(len(rows)) if vals[i] is not None and i not in set(keep_idx)]
    e, e1, e2 = sum(k)/len(k), sum(k1)/len(k1), sum(k2)/len(k2)
    ok = "✅" if (e1 > sum(b1)/len(b1) and e2 > sum(b2)/len(b2) and len(k) >= 700) else ""
    print(f"{name:>26} | n={len(k):>5} ALL {e:+.2f}% | H1 {e1:+.2f}% H2 {e2:+.2f}% | dibuang {sum(no)/len(no):+.2f}% {ok}")
    return e

print("=== GATE SEKTOR (momentum sektor) ===")
gate("sector ret20 > 0", lambda r: sec_ret[r["date"]][r["sector"]] > 0)
gate("sector ret20 > -3", lambda r: sec_ret[r["date"]][r["sector"]] > -3)
def top_half(r):
    day = sec_ret[r["date"]]
    thr = median(day.values())
    return day[r["sector"]] >= thr
gate("sector >= median hari itu", top_half)

# Bootstrap untuk 'sector ret20 > 0'
keep = [(vals[i], sec_ret[rows[i]["date"]][rows[i]["sector"]] > 0) for i in range(len(rows)) if vals[i] is not None]
random.seed(42); imp = []
for _ in range(3000):
    smp = [keep[random.randrange(len(keep))] for _ in range(len(keep))]
    kk = [v for v, g in smp if g]; aa = [v for v, _ in smp]
    imp.append(sum(kk)/len(kk) - sum(aa)/len(aa))
imp.sort()
print(f"Bootstrap sector>0: {sum(imp)/len(imp):+.2f} pt, CI90% [{imp[int(0.05*len(imp))]:+.2f}, {imp[int(0.95*len(imp))]:+.2f}]")

# ============ 2) EKSEKUSI NYATA ============
print("\n=== EKSEKUSI: entry di OPEN besok vs close sinyal ===")
def sim_open(r):
    arr = bars[r["code"]]
    i = next((j for j, b in enumerate(arr) if b[0] == r["date"]), None)
    if i is None: return None, None
    fwd = arr[i+1: i+21]
    if not fwd: return None, None
    ent = fwd[0][1]  # open
    gap = (ent / r["close"] - 1) * 100
    a = r["atrp"]
    sl = ent * (1 - 1.5*a/100); tp = ent * (1 + 3*a/100)
    for (dt, o, h, l, c) in fwd:
        if l <= sl: return (-1.5*a - COST, gap)
        if h >= tp: return (3.0*a - COST, gap)
    return ((fwd[-1][4]/ent - 1)*100 - COST, gap)

open_rets, gaps, both = [], [], []
base_sim = []
for r in rows:
    oret, gap = sim_open(r)
    if oret is None: continue
    # base simulasi (entry close) untuk paired
    arr = bars[r["code"]]; i = next(j for j, b in enumerate(arr) if b[0] == r["date"])
    fwd = arr[i+1: i+21]; a = r["atrp"]; ent = r["close"]
    sl = ent*(1-1.5*a/100); tp = ent*(1+3*a/100); bret = None
    for (dt, o, h, l, c) in fwd:
        if l <= sl: bret = -1.5*a - COST; break
        if h >= tp: bret = 3.0*a - COST; break
    if bret is None: bret = (fwd[-1][4]/ent - 1)*100 - COST
    open_rets.append(oret); gaps.append(gap); both.append(oret - bret)

gaps_sorted = sorted(gaps)
print(f"Gap open vs close: rata2 {sum(gaps)/len(gaps):+.2f}% · median {gaps_sorted[len(gaps)//2]:+.2f}% · "
      f">+1%: {100*sum(1 for g in gaps if g>1)/len(gaps):.0f}% · >+2%: {100*sum(1 for g in gaps if g>2)/len(gaps):.0f}%")
m = sum(both)/len(both); sd = (sum((x-m)**2 for x in both)/(len(both)-1))**0.5; se = sd/len(both)**0.5
print(f"Exp entry-open {sum(open_rets)/len(open_rets):+.2f}% vs entry-close {(-m + sum(open_rets)/len(open_rets)):+.2f}%")
print(f"Paired (open-close): n={len(both)} mean={m:+.2f}% SE={se:.2f}% t={m/se:+.2f}")

# Kantung gap
buckets = [(-99,-1,"gap < -1%"),(-1,0,"-1..0%"),(0,1,"0..+1%"),(1,2,"+1..+2%"),(2,3,"+2..+3%"),(3,99,">+3%")]
print("\nPerforma per kantung gap (entry-open):")
for lo, hi, name in buckets:
    sub = [o for o, g in zip(open_rets, gaps) if lo <= g < hi]
    if sub:
        print(f"  {name:>9}: n={len(sub):>4} exp={sum(sub)/len(sub):+.2f}%")
