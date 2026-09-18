"""bt_analisa.py — analisis lanjutan hasil backtest_v7_teknikal."""
import numpy as np
import pandas as pd

CSV = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
d = pd.read_csv(CSV, parse_dates=["date"])
print("total baris:", len(d), "| saham:", d.code.nunique(), "| hari:", d.date.nunique())

core = d[d.finalis & d.vol1]          # paling dekat ke set sinyal produksi (tanpa broker)
print("core (finalis & vol>=1):", len(core))

def desc(name, s):
    r = s.retLast.dropna()
    r20 = s.ret20f.dropna()
    print(f"\n== {name} == n={len(s)}")
    if len(r):
        print(f"  retLast: mean {r.mean():+.2f} med {r.median():+.2f} "
              f"p10 {np.percentile(r,10):+.1f} p25 {np.percentile(r,25):+.1f} "
              f"p75 {np.percentile(r,75):+.1f} p90 {np.percentile(r,90):+.1f} "
              f"p99 {np.percentile(r,99):+.1f} max {r.max():+.0f}")
        print(f"  winsor(+/-50): {np.clip(r,-50,50).mean():+.2f} | >0: {(r>0).mean()*100:.1f}%")
    if len(r20):
        print(f"  ret20f:  mean {r20.mean():+.2f} med {r20.median():+.2f} "
              f"| >0: {(r20>0).mean()*100:.1f}% (n={len(r20)})")

desc("core ALL", core)
desc("core RANGING", core[core.regime == "RANGING"])
desc("core BEAR", core[core.regime == "BEAR"])

# ── sampel non-tumpang-tindih: 1 sinyal per saham per 20 hari ──
c2 = core.sort_values(["code", "date"]).reset_index(drop=True)
keep, prev = [], {}
for i, row in c2.iterrows():
    c = row["code"]
    if c not in prev or (row["date"] - prev[c]).days >= 20:
        keep.append(i)
        prev[c] = row["date"]
nl = c2.loc[keep]
print(f"\n== core NON-OVERLAP (1/saham/20hr) == n={len(nl)}")
r = nl.retLast.dropna()
print(f"  retLast mean {r.mean():+.2f} med {r.median():+.2f} | WR {(r>0).mean()*100:.1f}%")
r20 = nl.ret20f.dropna()
print(f"  ret20f  mean {r20.mean():+.2f} med {r20.median():+.2f} | WR {(r20>0).mean()*100:.1f}% (n={len(r20)})")

# ── KONTRF AKTUAL pra-filter volume (finalis, vol<1.0 yg DIBUANG) ──
f = d[d.finalis]
kept, excl = f[f.vol1], f[~f.vol1]
print(f"\n== pra-filter volume (finalis) ==")
print(f"  vol>=1.0 (dipakai):  n={len(kept)} avgLast {kept.retLast.mean():+.2f}% med {kept.retLast.median():+.2f}%")
print(f"  vol<1.0  (dibuang):  n={len(excl)} avgLast {excl.retLast.mean():+.2f}% med {excl.retLast.median():+.2f}%")

# ── likuiditas vs ret ──
print(f"\n== likuiditas ==")
for nm, s in (("likuid>=800jt", d[d.liq_ok]), ("tidak likuid", d[~d.liq_ok])):
    print(f"  {nm}: n={len(s)} avgLast {s.retLast.mean():+.2f}% med {s.retLast.median():+.2f}%")

# ── EV Live config (dari kolom out/sl/tp aktual) ──
x = core.dropna(subset=["retLast"]).copy()
ret_tp = (x.tp / x.close - 1) * 100
ret_sl = (x.sl / x.close - 1) * 100
pnl = np.where(x.out == "tp", ret_tp, np.where((x.out == "sl") | (x.out == "both"), ret_sl, x.retLast))
x["pnl_live"] = pnl
print(f"\n== EV konfigurasi EXIT LIVE (core) ==")
print(f"  TP-first {(x.out=='tp').mean()*100:.0f}% · SL-first {(x.out=='sl').mean()*100:.0f}% · both {(x.out=='both').mean()*100:.0f}% · none {(x.out=='none').mean()*100:.0f}%")
print(f"  EV per observasi: {x.pnl_live.mean():+.2f}% (med {np.median(x.pnl_live):+.2f}%)")
for tmp in ("tp", "sl", "none"):
    s = x[x.out == tmp]
    print(f"  [{tmp}] n={len(s)} avg pnl {s.pnl_live.mean():+.2f}% avg hari-held ~{s.hit_day.replace(0, np.nan).mean():.1f}" if len(s) else f"  [{tmp}] -")

# ── GRID EV: kombinasi SL x TP (first-touch, level ATR polos) ──
print(f"\n== grid EV: SL x TP (mult ATR, first-touch, hold 20 bila tak tersentuh) ==")
hold = x.ret20f.fillna(x.retLast)
print("        " + "".join(f"TP{tm:<7}" for tm in (2.0, 2.5, 3.0, 4.0)))
for slm in (1.0, 1.5, 2.0, 2.5):
    rowtxt = f"SL{slm:<5}"
    for tpm in (2.0, 2.5, 3.0, 4.0):
        sd = x[f"slh{int(slm*10)}"]; td = x[f"tph{int(tpm*10)}"]
        tp_first = (td > 0) & ((sd == 0) | (td < sd))
        sl_first = (sd > 0) & ((td == 0) | (sd < td))
        same = (td > 0) & (sd > 0) & (td == sd)
        pnl = np.where(tp_first, tpm * x.atrp, np.where(sl_first, -slm * x.atrp, np.where(same, -slm * x.atrp, hold)))
        rowtxt += f"{np.nanmean(pnl):+6.2f}% "
    print(rowtxt)
print("(angka = EV % per observasi; atrp = ATR/entry; hold=ret20f|retLast; same-hari dihitung -SL)")

# ── top outlier check (data sanity) ──
print(f"\n== top-5 retLast core (cek sampah data) ==")
top = core.nlargest(5, "retLast")[["code", "date", "close", "vol_ratio", "nilai20", "retLast"]]
print(top.to_string(index=False))

# ── per-bulan (core) — apakah edge berubah dgn waktu ──
print(f"\n== per bulan (core, avg retLast / WR) ==")
x2 = core.copy()
x2["bln"] = x2.date.dt.to_period("M")
g = x2.groupby("bln")["retLast"].agg(["count", "mean", lambda s: (s > 0).mean() * 100])
g.columns = ["n", "avg", "wr"]
print(g.to_string())
