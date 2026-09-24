#!/usr/bin/env python3
"""kualitas4.py — uji halus FILTER KUALITAS: walk-forward 2 paruh + bootstrap per filter.

Pertanyaan: filter mana yang benar-benar mengurangi kohort TURUN (bukan artefak sampel).
Dua subset: base=finalis&RANGING; sig=base&qg_buy (paling dekat dgn sinyal tampil).
"""
import numpy as np
import pandas as pd

CSV = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
d = pd.read_csv(CSV, parse_dates=["date"])
base = d[d.finalis & (d.regime == "RANGING")].copy()
sig = base[base.qg_buy == 1].copy()
med = base.date.median()

print(f"base=finalis&RANGING n={len(base)} | sig=+qg_buy n={len(sig)}\n")
for nm, s in (("base", base), ("sig", sig)):
    print(f"  {nm:4}: retLast {s.retLast.mean():+5.2f}% | WR {(s.retLast>0).mean()*100:3.0f}% | "
          f"dd<=-10% {(s.maxdd<=-10).mean()*100:3.0f}% | sl {(s.out=='sl').mean()*100:3.0f}% | "
          f"P>=15% {(s.maxup>=15).mean()*100:3.0f}% | alpha {s.alpha.mean():+5.2f}%")


def metrik(n, mean, wr, dd, sl, p15, alpha):
    return dict(n=n, mean=mean, wr=wr, dd=dd, sl=sl, p15=p15, alpha=alpha)


def hitung(s):
    return metrik(len(s), np.nanmean(s.retLast), (s.retLast > 0).mean() * 100,
                  (s.maxdd <= -10).mean() * 100, (s.out == "sl").mean() * 100,
                  (s.maxup >= 15).mean() * 100, np.nanmean(s.alpha))


def boot(nama, s_all, mask, n_boot=2000, seed=7):
    mask = pd.Series(mask).reindex(s_all.index).fillna(False).astype(bool)
    k = s_all[mask]
    if len(k) < 30:
        print(f"  {nama:44} kept {len(k)} (kecil, skip)")
        return
    m_all, m_k = hitung(s_all), hitung(k)
    a_ret = s_all.retLast.values
    a_dd = (s_all.maxdd <= -10).values.astype(float)
    a_sl = (s_all.out == "sl").values.astype(float)
    idx_k = np.where(mask.values)[0]
    rng = np.random.default_rng(seed)
    nk, nall = len(idx_k), len(a_ret)
    sims_mean, sims_dd, sims_sl = np.empty(n_boot), np.empty(n_boot), np.empty(n_boot)
    for i in range(n_boot):
        pick = rng.integers(0, nall, nk)
        sims_mean[i] = np.nanmean(a_ret[pick])
        sims_dd[i] = a_dd[pick].mean() * 100
        sims_sl[i] = a_sl[pick].mean() * 100
    bagian = []
    for lbl, arr, act in (("retLast", sims_mean, m_k["mean"]), ("dd<=-10%", sims_dd, m_k["dd"]), ("sl", sims_sl, m_k["sl"])):
        persen = (arr < act).mean() * 100
        bagian.append(f"{lbl} {act:+5.2f} (percentile {persen:3.0f})")
    h = []
    for lbl, part in (("H1", s_all[s_all.date <= med]), ("H2", s_all[s_all.date > med])):
        mm = pd.Series(mask).reindex(part.index).fillna(False).astype(bool)
        kk = part[mm]
        h.append(f"{lbl} dMean {kk.retLast.mean()-part.retLast.mean():+5.2f} dDd {(kk.maxdd<=-10).mean()*100-(part.maxdd<=-10).mean()*100:+4.0f}")
    print(f"  {nama:44} kept {m_k['n']:5} ({100*m_k['n']/m_all['n']:3.0f}%) | " + " | ".join(bagian))
    print(f"  {'':44} {' | '.join(h)} | P15 {m_k['p15']:.0f}% alpha {m_k['alpha']:+.2f}")


FILTERS = [
    ("F1 buang weekly BULLISH", lambda s: s.weekly != "BULLISH"),
    ("F2 buang ret20d>=40 (terlalu lari)", lambda s: s.ret20d < 40),
    ("F3 buang atrp>=10 (terlalu liar)", lambda s: s.atrp < 10),
    ("F4 buang chase ret20d 10..40", lambda s: ~s.ret20d.between(10, 40)),
    ("F5 buang vol_ratio 1..3 (zona tengah)", lambda s: ~s.vol_ratio.between(1, 3)),
    ("F6 = F1 & F2", lambda s: (s.weekly != "BULLISH") & (s.ret20d < 40)),
    ("F7 = F1 & F5", lambda s: (s.weekly != "BULLISH") & ~s.vol_ratio.between(1, 3)),
    ("F8 = F1 & F2 & F3", lambda s: (s.weekly != "BULLISH") & (s.ret20d < 40) & (s.atrp < 10)),
    ("F9 buang v4>=65 (skor inti tinggi)", lambda s: s.v4 < 65),
    ("F10 hanya weekly BEARISH (notch)", lambda s: s.weekly == "BEARISH"),
]

for nama_sub, s_all in (("BASE", base), ("SIG", sig)):
    print(f"\n══════ {nama_sub} (n={len(s_all)}) ══════")
    for nama, fn in FILTERS:
        boot(nama, s_all, fn(s_all))
