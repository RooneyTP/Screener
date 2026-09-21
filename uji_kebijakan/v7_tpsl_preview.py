#!/usr/bin/env python3
"""Pratinjau cepat: cari kombinasi TP/SL (×ATR) dengan ekspektasi terbaik.

Data: backtest_v7_trades.csv (153.504 baris, replay H+20).
Kolom tph20/25/30/40 = hari TP 2.0/2.5/3.0/4.0×ATR tersentuh (0=tidak),
slh10/15/20/25 = hari SL 1.0/1.5/2.0/2.5×ATR tersentuh (0=tidak).
Yang tersentuh lebih dulu menang; seri 1 hari → dihitung konservatif (SL).
Tidak tersentuh keduanya → keluar di H+20 (ret20f).
Biaya transaksi diasumsikan 0.4% round-trip (beli+sell+fee) — dikurangkan.
"""
import csv

P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


rows = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if fnum(r["atrp"]) is None:
            continue
        rows.append(r)

print(f"Total baris terbaca: {len(rows)}\n")

TP_CHOICES = [2.0, 2.5, 3.0, 4.0]
SL_CHOICES = [1.0, 1.5, 2.0, 2.5]
COST = 0.4  # % round-trip


def analyze(subset, tp_m, sl_m):
    tpk = f"tph{int(round(tp_m * 10))}"
    slk = f"slh{int(round(sl_m * 10))}"
    n = wins = losses = ties = to = 0
    skip = 0
    tot = 0.0
    for r in subset:
        atrp = fnum(r["atrp"])
        tpd = fnum(r.get(tpk))
        sld = fnum(r.get(slk))
        if atrp is None or atrp <= 0 or tpd is None or sld is None:
            skip += 1
            continue
        tpd, sld = int(tpd), int(sld)
        tp_hit, sl_hit = tpd > 0, sld > 0
        if tp_hit and (not sl_hit or tpd < sld):
            ret = tp_m * atrp
            wins += 1
        elif sl_hit and (not tp_hit or sld < tpd):
            ret = -sl_m * atrp
            losses += 1
        elif tp_hit and sl_hit:  # seri hari yang sama → konservatif
            ret = -sl_m * atrp
            ties += 1
        else:
            r20 = fnum(r["ret20f"])
            if r20 is None:
                skip += 1
                continue
            ret = r20
            to += 1
        tot += ret - COST
        n += 1
    if n == 0:
        return None
    return dict(n=n, wr=100 * wins / n, losses=losses, ties=ties, to=to,
                exp=tot / n, skip=skip)


def table(subset, title):
    print(f"=== {title} ===")
    res = []
    for tp_m in TP_CHOICES:
        for sl_m in SL_CHOICES:
            a = analyze(subset, tp_m, sl_m)
            if a:
                res.append((a["exp"], tp_m, sl_m, a))
    res.sort(reverse=True)
    print(f"{'TP×ATR':>7} {'SL×ATR':>7} | {'WR':>6} | {'timeout':>7} | {'exp_net':>8} | n")
    for exp, tp_m, sl_m, a in res:
        tag = "  ← SEKARANG" if (tp_m == 3.0 and sl_m == 1.5) else ""
        print(f"{tp_m:>7.1f} {sl_m:>7.1f} | {a['wr']:>5.1f}% | {a['to']:>7d} | {a['exp']:>+7.2f}% | {a['n']}{tag}")
    best = res[0]
    cur = [x for x in res if x[1] == 3.0 and x[2] == 1.5]
    if cur:
        gain = best[0] - cur[0][0]
        print(f"→ Terbaik: TP {best[1]}× / SL {best[2]}× (exp {best[0]:+.2f}%) vs sekarang {cur[0][0]:+.2f}% "
              f"(selisih {gain:+.2f} poin/trade)")
    print()


# Subset sesuai kebijakan V7 baru: finalis + vol>=1.0 (BEAR diblokir → fokus RANGING)
s2 = [r for r in rows if r["finalis"] == "True" and r["vol1"] == "True"]
s4 = [r for r in s2 if r["regime"] == "RANGING"]
s2_bear = [r for r in s2 if r["regime"] == "BEAR"]

table(s2, "SEMUA REGIME (finalis & vol>=1.0)")
table(s4, "RANGING saja (kebijakan sekarang)")
table(s2_bear, "BEAR (sekarang diblokir — pembanding)")

# Sanity: distribusi 'out' data asli
from collections import Counter
c = Counter(r["out"] for r in s4)
print("Distribusi out asli (RANGING):", dict(c))
