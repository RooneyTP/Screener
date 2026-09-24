#!/usr/bin/env python3
"""kualitas3.py — studi 'kenapa TURUN' + uji filter kualitas di backtest 153k.

Subset produksi: finalis & RANGING (BEAR sudah diblok di produksi; faktor broker
tak ada riwayat). Semua angka IN-SAMPLE — kandidat wajib validasi maju dulu.
"""
import numpy as np
import pandas as pd

CSV = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
d = pd.read_csv(CSV, parse_dates=["date"])
print(f"Total {len(d):,} baris | {d.date.min().date()} → {d.date.max().date()} | {d.code.nunique()} saham")

base = d[d.finalis & (d.regime == "RANGING")].copy()
print(f"\nSubset produksi (finalis & RANGING): n={len(base):,}")
print(f"  retLast rata2 {base.retLast.mean():+.2f}% med {base.retLast.median():+.2f}% | WR {(base.retLast>0).mean()*100:.0f}%")
print(f"  P(maxdd<=-10%) {(base.maxdd<=-10).mean()*100:.0f}% | P(SL-first) {(base.out=='sl').mean()*100:.0f}% | P(maxup>=15%) {(base.maxup>=15).mean()*100:.0f}%")


def kol(nama, s):
    r = s.retLast.dropna()
    if not len(r):
        print(f"  {nama:26} n={len(s)}")
        return
    print(f"  {nama:26} n={len(s):5} | retLast med {r.median():+5.2f}% rata2 {r.mean():+5.2f}% | WR {(r>0).mean()*100:4.0f}% | "
          f"P(dd<=-10%) {(s.maxdd<=-10).mean()*100:4.0f}% | P(sl) {(s.out=='sl').mean()*100:4.0f}% | P(>=15%) {(s.maxup>=15).mean()*100:4.0f}%")


def band(col, bins, label=None):
    print(f"\n── {label or col} ──")
    kat = pd.cut(base[col], bins=bins, right=False)
    for iv, s in base.groupby(kat, observed=True):
        kol(str(iv), s)


band("v4", [40, 50, 55, 60, 65, 70, 100])
band("vol_ratio", [0, 0.3, 0.5, 1, 2, 3, 6, 1e9])
band("rsi", [0, 30, 40, 50, 60, 70, 100])
band("adx", [0, 15, 20, 25, 30, 100])
band("ret20d", [-100, -20, -10, 0, 10, 20, 40, 10000])
band("atrp", [0, 2, 3, 4, 6, 10, 100])
band("close", [0, 100, 200, 500, 1000, 5000, 100000])
band("rank_liq", [0, 20, 40, 60, 120, 5000], "peringkat likuiditas harian")
kol("weekly BULLISH", base[base.weekly == "BULLISH"])
kol("weekly BEARISH", base[base.weekly == "BEARISH"])

print("\n══════ UJI FILTER 'KUALITAS' — buang kohort yang cenderung TURUN (in-sample) ══════")
n0 = len(base)


def uji(nama, mask):
    k = base[mask]
    if len(k) < 30:
        print(f"  {nama:46} kept {len(k)} (terlalu kecil)")
        return None
    print(f"  {nama:46} kept {len(k):5} ({100*len(k)/n0:3.0f}%) | retLast {k.retLast.mean():+5.2f}% (Δ{k.retLast.mean()-base.retLast.mean():+5.2f}) | "
          f"dd<=-10% {(k.maxdd<=-10).mean()*100:3.0f}% (Δ{(k.maxdd<=-10).mean()*100-(base.maxdd<=-10).mean()*100:+4.0f}) | "
          f"sl {(k.out=='sl').mean()*100:3.0f}% | alpha {k.alpha.mean():+5.2f}% (Δ{k.alpha.mean()-base.alpha.mean():+5.2f})")
    return k


uji("(acuan) semua", base.index == base.index)
uji("buang v4>=70", base.v4 < 70)
uji("buang weekly BULLISH", base.weekly != "BULLISH")
uji("buang rsi>=70", base.rsi < 70)
uji("buang ret20d 10..40 (chase)", ~base.ret20d.between(10, 40))
uji("buang atrp<2 (tanpa bahan bakar)", base.atrp >= 2)
uji("buang close>=5000 (big cap)", base.close < 5000)
uji("buang vol_ratio 1..3 (zona tengah)", ~base.vol_ratio.between(1, 3))
uji("hanya vol<0.5 atau vol>=6 (dua ujung)", (base.vol_ratio < 0.5) | (base.vol_ratio >= 6))
uji("hanya rank_liq<=60", base.rank_liq <= 60)

print()
m1 = (base.v4 < 70) & (base.weekly != "BULLISH")
uji("K1: v4<70 & bukan weekly BULLISH", m1)
m2 = m1 & ~base.ret20d.between(10, 40)
uji("K2: K1 + buang chase ret20d10-40", m2)
m3 = m2 & (base.close < 5000)
uji("K3: K2 + close<5000", m3)
m4 = m3 & (base.atrp >= 2)
uji("K4: K3 + atrp>=2", m4)

print("\n══ Walk-forward 2 paruh (harus searah) ══")
med = base.date.median()
for nama, mask in (("K1", m1), ("K2", m2), ("K3", m3)):
    baris = []
    for lbl, part in (("H1", base[base.date <= med]), ("H2", base[base.date > med])):
        kk = part[mask.reindex(part.index)]
        bb = part
        baris.append(f"{lbl}: {kk.retLast.mean():+5.2f}% vs {bb.retLast.mean():+5.2f}% (Δ{kk.retLast.mean()-bb.retLast.mean():+5.2f}, n={len(kk)})")
    print(f"  {nama}: " + " | ".join(baris))

print("\n══ Bootstrap CI90% (K2: kept vs semua, retLast) ══")
rng = np.random.default_rng(42)
kk = base[m2].retLast.dropna().values
aa = base.retLast.dropna().values
diffs = [rng.choice(kk, len(kk), replace=True).mean() - rng.choice(aa, len(aa), replace=True).mean() for _ in range(3000)]
print(f"  Δ mean = {kk.mean()-aa.mean():+5.2f} pt | CI90% [{np.percentile(diffs,5):+5.2f}, {np.percentile(diffs,95):+5.2f}] | P(Δ>0) = {(np.array(diffs)>0).mean()*100:.0f}%")
print("  ⚠️ IN-SAMPLE. Wajib: label TUNDA + validasi maju via shadow lab sebelum jadi kebijakan.")
