#!/usr/bin/env python3
"""kualitas5.py — kombinasi filter 'kualitas' + profil kohort yang DIBUANG.

Fokus: paket yang paling menjanjikan dari kualitas4 (junk-tail: chase/liar/skor-inti-tinggi).
"""
import numpy as np
import pandas as pd

CSV = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
d = pd.read_csv(CSV, parse_dates=["date"])
base = d[d.finalis & (d.regime == "RANGING")].copy()
sig = base[base.qg_buy == 1].copy()
med = base.date.median()


def stat(s):
    return (f"n={len(s):5} | retLast {s.retLast.mean():+5.2f}% | WR {(s.retLast>0).mean()*100:3.0f}% | "
            f"dd<=-10% {(s.maxdd<=-10).mean()*100:3.0f}% | sl {(s.out=='sl').mean()*100:3.0f}% | "
            f"P15 {(s.maxup>=15).mean()*100:3.0f}% | alpha {s.alpha.mean():+5.2f}%")


def halves(nama, s_all, mask):
    mask = pd.Series(mask).reindex(s_all.index).fillna(False).astype(bool)
    k, rem = s_all[mask], s_all[~mask]
    print(f"  {nama:48} kept {len(k):5} ({100*len(k)/len(s_all):3.0f}%)")
    print(f"     KEPT   : {stat(k)}")
    print(f"     DIBUANG: {stat(rem)}")
    for lbl, part in (("H1", s_all[s_all.date <= med]), ("H2", s_all[s_all.date > med])):
        mm = pd.Series(mask).reindex(part.index).fillna(False).astype(bool)
        kk = part[mm]
        print(f"     {lbl}: kept {kk.retLast.mean():+5.2f}% vs {part.retLast.mean():+5.2f}% "
              f"(dMean {kk.retLast.mean()-part.retLast.mean():+5.2f}) | "
              f"dDd {(kk.maxdd<=-10).mean()*100-(part.maxdd<=-10).mean()*100:+4.0f} | "
              f"dSl {(kk.out=='sl').mean()*100-(part.out=='sl').mean()*100:+4.0f}")


F = {
    "F11 junk-tail (ret20<40 & atrp<10 & v4<65)": lambda s: (s.ret20d < 40) & (s.atrp < 10) & (s.v4 < 65),
    "F12 = F11 + tanpa weekly BULLISH": lambda s: (s.ret20d < 40) & (s.atrp < 10) & (s.v4 < 65) & (s.weekly != "BULLISH"),
    "F13 = F11 + tanpa vol 1..3": lambda s: (s.ret20d < 40) & (s.atrp < 10) & (s.v4 < 65) & (~s.vol_ratio.between(1, 3)),
    "F14 = ret20<40 & atrp<10 (tanpa v4)": lambda s: (s.ret20d < 40) & (s.atrp < 10),
}

for nm, s_all in (("BASE", base), ("SIG", sig)):
    print(f"\n══ {nm} (n={len(s_all)}) ══")
    for nama, fn in F.items():
        halves(nama, s_all, fn(s_all))
