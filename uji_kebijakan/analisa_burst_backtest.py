#!/usr/bin/env python3
"""analisa_burst.py — cari pola saham yang MELEDAK >=15% (153k baris backtest + detail live)."""
import json
import sqlite3
import numpy as np
import pandas as pd

CSV = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
d = pd.read_csv(CSV, parse_dates=["date"])
d = d.dropna(subset=["maxup"])
print(f"Total: {len(d):,} baris | {d.code.nunique()} saham | {d.date.min().date()} → {d.date.max().date()}")

P15 = lambda x: (x >= 15).mean() * 100
print(f"P(maxup>=15%) keseluruhan: {P15(d.maxup):.2f}% | median maxup {d.maxup.median():.2f}%")


def tabel(nama, col, bins):
    kat = pd.cut(d[col], bins=bins, right=False)
    g = d.groupby(kat, observed=True).agg(n=("maxup", "size"), P15=("maxup", P15), mean_maxup=("maxup", "mean"))
    print(f"\n── {nama} ──")
    for idx, r in g.iterrows():
        print(f"  {str(idx):16} n={int(r.n):6}  P(>=15%)={r.P15:5.2f}%  rata-rata maxup={r.mean_maxup:+.2f}%")


print("\n══════ DIAGNOSTIK UNIVARIAT (semua baris) ══════")
tabel("vol_ratio (volume vs rata2)", "vol_ratio", [0, 0.3, 0.5, 1, 2, 3, 6, 100])
tabel("RSI", "rsi", [0, 30, 40, 50, 60, 70, 100])
tabel("ADX", "adx", [0, 20, 25, 30, 40, 100])
tabel("ret20d (momentum 20 hari SEBELUM)", "ret20d", [-100, -20, -10, -3, 3, 10, 20, 40, 500])
tabel("atrp (ATR %)", "atrp", [0, 2, 3, 4, 6, 10, 100])
tabel("harga Rp", "close", [0, 100, 200, 500, 1000, 5000, 100000])
tabel("nilai20 (Rp/hari)", "nilai20", [0, 1e9, 5e9, 2e10, 1e11, 1e15])

g = d.groupby("regime").agg(n=("maxup", "size"), P15=("maxup", P15))
print("\n── regime ──")
for idx, r in g.iterrows():
    print(f"  {idx:10} n={int(r.n):7}  P(>=15%)={r.P15:5.2f}%")

sub = d[d.maxup >= 15]
print("\n── median fitur: baris ledakan vs lainnya ──")
for c in ("rsi", "adx", "atrp", "vol_ratio", "ret20d", "nilai20", "close"):
    print(f"  {c:10} ledakan={sub[c].median():10.2f}   lain={d.loc[~d.index.isin(sub.index), c].median():10.2f}")

print("\n══════ GRID: vol_ratio x momentum (subset FINALIS = kandidat produksi) ══════")
f = d[d.finalis].copy()
print(f"(finalis n={len(f):,}, P15 dasar={P15(f.maxup):.2f}%)")
f["vr"] = pd.cut(f.vol_ratio, [0, 0.5, 1, 2, 100], labels=["vr<0.5", "0.5-1", "1-2", ">=2"])
f["mom"] = pd.cut(f.ret20d, [-100, -10, 0, 10, 500], labels=["ret20<-10", "-10..0", "0..10", ">10"])
hit = f.pivot_table(index="vr", columns="mom", values="maxup", aggfunc=P15, observed=True)
cnt = f.pivot_table(index="vr", columns="mom", values="maxup", aggfunc="size", observed=True)
print("\nP(>=15%)%:")
print(hit.round(1).to_string())
print("\nn:")
print(cnt.to_string())

print("\n══════ KOMBINASI TERBAIK (finalis, cari lift tinggi) ══════")
def cell(nama, sel):
    s = f[sel]
    if len(s) < 100:
        return
    print(f"  {nama:58} n={len(s):5}  P15={P15(s.maxup):5.2f}%  lift={P15(s.maxup)/P15(f.maxup):4.2f}x")

cell("quiet: vol<0.5 & |mom|<10", (f.vol_ratio < 0.5) & (f.ret20d.abs() < 10))
cell("quiet + ATR<4", (f.vol_ratio < 0.5) & (f.ret20d.abs() < 10) & (f.atrp < 4))
cell("hot: vol>=2 & mom>0", (f.vol_ratio >= 2) & (f.ret20d > 0))
cell("hot: vol>=2 & mom>10", (f.vol_ratio >= 2) & (f.ret20d > 10))
cell("oversold: RSI<40 & vol>=1", (f.rsi < 40) & (f.vol_ratio >= 1))
cell("RSI 45-60 & vol<1 & mom 0..10", (f.rsi.between(45, 60)) & (f.vol_ratio < 1) & (f.ret20d.between(0, 10)))
cell("ADX>=25 & mom>0 & vol>=1", (f.adx >= 25) & (f.ret20d > 0) & (f.vol_ratio >= 1))
cell("murah<500 & vol>=1 & mom>0", (f.close < 500) & (f.vol_ratio >= 1) & (f.ret20d > 0))

print("\n══════ DETAIL PEMENANG LIVE (batch app: catatan & bandar) ══════")
con = sqlite3.connect("/home/yuan/risetsaham/data/risetsaham.db")
want = {"SRSN", "TOOL", "BIPP", "AGAR", "ASLI", "AYLS", "DSFI", "DFAM", "FITT", "BSSR", "KICI"}
for bid, ts, sumber, rows_json in con.execute("SELECT id, ts, sumber, rows_json FROM screener_batch ORDER BY ts"):
    d2 = json.loads(rows_json)
    kolom = d2["kolom"]
    from datetime import datetime
    tanggal = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    for item in d2["baris"]:
        if item["kode"] not in want:
            continue
        data = dict(zip(kolom, item["data"]))
        print(f"  {item['kode']:5} {tanggal} [{sumber}] skor={data.get('skor')} mode={data.get('mode')} "
              f"entry={data.get('entry')} sl={data.get('sl')} tp={data.get('tp')} "
              f"ideal={data.get('entry_ideal','')} bandarSesi={data.get('bandar_sesi','')} "
              f"bandar3bln={data.get('bandar_3bln','')} tampil={data.get('tampil','')} catatan={data.get('catatan','')}")
