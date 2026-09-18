#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""backtest_v7_teknikal.py — Uji balik (replay) mesin V7 untuk komponen teknikal.

KENAPA TERBATAS: faktor broker/asing/fundamental V7 memakai snapshot Stockbit
yang BARU punya riwayat beberapa hari → tidak bisa di-backtest. Skrip ini
menguji SEMUA komponen yang bisa dihitung dari data harga 1 tahun
(cache_v7, ~921 saham), persis rantai keputusan scan produksi bagian teknikal:

  compute_all_indicators → align IHSG → skor inti V4 (compute_total_score)
  → regime pasar per-hari (replika detect_market_regime)
  → pra-filter likuiditas Rp 800 jt/hari + pra-filter volume (vol_ratio>=1.0)
  → quality_gate → level exit ATR asli (compute_exit) → hasil maju H+20.

Diukur per (saham, hari): skor, peringkat pasar (finalis = top-60 likuid),
semua flag gate, level SL/TP aktual, hasil maju (TP/SL/none, ret H+5/10/20,
alpha vs IHSG, maxup/maxdd) + grid TP/SL alternatif (2.0-4.0 / 1.0-2.5 ATR).

Output:
  idx_alpha_screener/data/backtest_v7_trades.csv     (per kandidat)
  idx_alpha_screener/data/backtest_v7_ringkasan.md   (tabel ringkas)

Pakai:  .venv/bin/python backtest_v7_teknikal.py [--start-idx 65] [--limit N]
"""
from __future__ import annotations

import argparse
import glob
import math
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(ROOT, "idx_alpha_screener")
sys.path.insert(0, SCAN)

from data import compute_all_indicators, align_to_market   # noqa: E402
from scoring import compute_total_score, quality_gate       # noqa: E402
from v7_exit import compute_exit                            # noqa: E402

CACHE = os.path.join(SCAN, "data", "cache_v7")
IHSG_CSV = os.path.join(ROOT, "cache", "_IHSG_.csv")
OUT_CSV = os.path.join(SCAN, "data", "backtest_v7_trades.csv")
OUT_MD = os.path.join(SCAN, "data", "backtest_v7_ringkasan.md")

MIN_VALUE_20D = 800_000_000
MAX_FWD = 20
TP_GRID = (2.0, 2.5, 3.0, 4.0)
SL_GRID = (1.0, 1.5, 2.0, 2.5)
FINALIS_N = 60


def evaluate(entry: float, atr: float, sl: float, tp: float, fwd: pd.DataFrame) -> dict:
    """Hasil maju 1 kandidat: TP/SL live + grid + ret/alpha + maxup/maxdd.

    Semantik sama dgn tracker produksi (screener_hasil): 'hit pertama' menang.
    ret5/10/20f dalam PERSEN. Grid TP/SL pakai multiplier ATR (tanpa floor).
    """
    out = {"out": "none", "hit_day": 0,
           "tph20": 0, "tph25": 0, "tph30": 0, "tph40": 0,
           "slh10": 0, "slh15": 0, "slh20": 0, "slh25": 0,
           "ret5": None, "ret10": None, "ret20f": None, "retLast": None,
           "maxup": None, "maxdd": None}
    n = len(fwd)
    if n == 0:
        return out
    hs = fwd["high"].to_numpy(float)
    ls = fwd["low"].to_numpy(float)
    cs = fwd["close"].to_numpy(float)
    mu, md = -9e18, 9e18
    done = False
    for j in range(n):
        h, l = hs[j], ls[j]
        if math.isfinite(h):
            if out["tph20"] == 0 and h >= entry + atr * 2.0:
                out["tph20"] = j + 1
            if out["tph25"] == 0 and h >= entry + atr * 2.5:
                out["tph25"] = j + 1
            if out["tph30"] == 0 and h >= entry + atr * 3.0:
                out["tph30"] = j + 1
            if out["tph40"] == 0 and h >= entry + atr * 4.0:
                out["tph40"] = j + 1
            if h > mu:
                mu = h
        if math.isfinite(l):
            if out["slh10"] == 0 and l <= entry - atr * 1.0:
                out["slh10"] = j + 1
            if out["slh15"] == 0 and l <= entry - atr * 1.5:
                out["slh15"] = j + 1
            if out["slh20"] == 0 and l <= entry - atr * 2.0:
                out["slh20"] = j + 1
            if out["slh25"] == 0 and l <= entry - atr * 2.5:
                out["slh25"] = j + 1
            if l < md:
                md = l
        if not done and ((math.isfinite(h) and h >= tp) or (math.isfinite(l) and l <= sl)):
            done = True
            out["hit_day"] = j + 1
            ht = math.isfinite(h) and h >= tp
            hlt = math.isfinite(l) and l <= sl
            out["out"] = "both" if (ht and hlt) else ("tp" if ht else "sl")
    if n >= 5 and math.isfinite(cs[4]):
        out["ret5"] = (cs[4] / entry - 1) * 100
    if n >= 10 and math.isfinite(cs[9]):
        out["ret10"] = (cs[9] / entry - 1) * 100
    if n >= 20 and math.isfinite(cs[19]):
        out["ret20f"] = (cs[19] / entry - 1) * 100
    if math.isfinite(cs[-1]):
        out["retLast"] = (cs[-1] / entry - 1) * 100
    if mu > -9e17:
        out["maxup"] = (mu / entry - 1) * 100
    if md < 9e17:
        out["maxdd"] = (md / entry - 1) * 100
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start-idx", type=int, default=65,
                    help="baris awal yg dievaluasi (warmup indikator)")
    ap.add_argument("--limit", type=int, default=0, help="uji cepat: batasi jumlah saham")
    a = ap.parse_args()

    t0 = time.time()
    ih = pd.read_csv(IHSG_CSV, index_col=0, parse_dates=True)
    need_ih = ["open", "high", "low", "close", "volume"]
    missing = [c for c in need_ih if c not in ih.columns]
    if missing:
        ih.columns = [str(c).lower() for c in ih.columns]
        missing = [c for c in need_ih if c not in ih.columns]
        if missing:
            print("FATAL: kolom IHSG tidak lengkap:", missing)
            return 1
    ih = ih[need_ih].astype(float)
    ih = ih[~ih.index.duplicated(keep="last")].sort_index()
    ih = compute_all_indicators(ih)
    ema12 = ih["close"].ewm(span=12, adjust=False).mean()
    ema50 = ih["close"].ewm(span=50, adjust=False).mean()
    regs = []
    for i in range(len(ih)):
        px = float(ih["close"].iat[i])
        e12 = float(ema12.iat[i])
        e50 = float(ema50.iat[i])
        adx = ih["adx"].iat[i]
        adxc = 0.0 if pd.isna(adx) else float(adx)
        if not (math.isfinite(px) and math.isfinite(e12) and math.isfinite(e50)) or e50 == 0:
            regs.append("RANGING")
            continue
        diff = (e12 - e50) / e50 * 100
        if adxc > 30 and diff > 1.0 and px > e50:
            regs.append("BULL")
        elif adxc > 30 and diff < -1.0 and px < e50:
            regs.append("BEAR")
        elif adxc > 30:
            regs.append("HIGH_VOLATILITY")
        else:
            regs.append("RANGING")
    reg_ih = pd.Series(regs, index=ih.index)
    print(f"[bt] IHSG {len(ih)} baris {ih.index[0].date()} → {ih.index[-1].date()}")

    files = sorted(glob.glob(os.path.join(CACHE, "v7_*_1y.csv")))
    files = [f for f in files if not os.path.basename(f).startswith("v7_IDX")]
    if a.limit:
        files = files[: a.limit]
    print(f"[bt] {len(files)} saham; mulai {time.strftime('%H:%M:%S')}")

    recs: list[dict] = []
    n_err = 0
    t1 = time.time()
    for fi, fp in enumerate(files):
        code = os.path.basename(fp)[3:-7].upper()
        try:
            df = pd.read_csv(fp, index_col=0, parse_dates=True)
            if not all(c in df.columns for c in need_ih):
                df.columns = [str(c).lower() for c in df.columns]
                if not all(c in df.columns for c in need_ih):
                    n_err += 1
                    continue
            df = df[need_ih].astype(float)
            df = df[~df.index.duplicated(keep="last")].sort_index()
            if len(df) < a.start_idx + 10:
                continue
            df = compute_all_indicators(df)
            df = align_to_market(df, df_ihsg=ih)
            reg = reg_ih.reindex(df.index, method="ffill")
            nilai20 = (df["close"] * df["volume"]).rolling(20).mean()
            ihc = ih["close"].reindex(df.index, method="ffill")
            n = len(df)
            for i in range(a.start_idx, n - 1):
                r = df.iloc[i]
                if (pd.isna(r.get("rsi")) or pd.isna(r.get("atr"))
                        or pd.isna(r.get("vol_ratio")) or pd.isna(r.get("adx"))
                        or pd.isna(r.get("ema12")) or pd.isna(r.get("ema50"))
                        or pd.isna(r.get("idx_ret_20d")) or pd.isna(r.get("avg_vol_60d"))
                        or pd.isna(r.get("bb_mid"))):
                    continue
                regime = reg.iat[i]
                if not isinstance(regime, str):
                    regime = "RANGING"
                v4 = compute_total_score(r, regime)
                vr = float(r["vol_ratio"])
                entry = float(r["close"])
                atr = float(r["atr"])
                if not (math.isfinite(entry) and math.isfinite(atr)) or entry <= 0 or atr <= 0:
                    continue
                weekly = str(r.get("weekly_trend") or "NO_DATA")
                ex = compute_exit(entry, atr, regime, "swing", weekly)
                fwd = df.iloc[i + 1: i + 1 + MAX_FWD]
                res = evaluate(entry, atr, ex["stop_loss"], ex["take_profit"], fwd)
                alpha = None
                if len(fwd):
                    d0 = df.index[i]
                    d1 = fwd.index[-1]
                    try:
                        ih0 = float(ihc.loc[d0])
                        ih1 = float(ihc.loc[d1])
                        if ih0 and ih1:
                            alpha = res["retLast"] - (ih1 / ih0 - 1) * 100
                    except Exception:
                        alpha = None
                recs.append({
                    "date": df.index[i].strftime("%Y-%m-%d"), "code": code,
                    "close": entry, "v4": v4,
                    "nilai20": float(nilai20.iat[i]) if pd.notna(nilai20.iat[i]) else None,
                    "vol_ratio": round(vr, 3), "rsi": round(float(r["rsi"]), 1),
                    "adx": round(float(r["adx"]), 1),
                    "ret20d": round(float(r["ret_20d"]) * 100, 2),
                    "atrp": round(atr / entry * 100, 2),
                    "regime": regime, "weekly": weekly,
                    "qg_sb": int(quality_gate(r, "STRONG_BUY") == "STRONG_BUY"),
                    "qg_buy": int(quality_gate(r, "BUY") == "BUY"),
                    "sl": int(ex["stop_loss"]), "tp": int(ex["take_profit"]),
                    **res, "alpha": alpha,
                })
        except Exception as e:  # noqa: BLE001 — 1 saham rusak jangan bunuh run
            n_err += 1
            if n_err <= 5:
                print(f"  [{code}] error: {type(e).__name__}: {e}")
        if (fi + 1) % 100 == 0:
            print(f"  {fi + 1}/{len(files)} saham · {len(recs)} baris · "
                  f"{time.time() - t1:.0f}s", flush=True)

    d = pd.DataFrame(recs)
    if d.empty:
        print("FATAL: tidak ada baris terkumpul")
        return 1
    d["liq_ok"] = d["nilai20"].fillna(0) >= MIN_VALUE_20D
    d["vol1"] = d["vol_ratio"] >= 1.0
    d["rank_all"] = d.groupby("date")["v4"].rank(ascending=False, method="min")
    rl = d[d["liq_ok"]].groupby("date")["v4"].rank(ascending=False, method="min")
    d["rank_liq"] = rl
    d["finalis"] = d["rank_liq"].le(FINALIS_N).fillna(False)
    for c in ("ret5", "ret10", "ret20f", "retLast", "alpha", "maxup", "maxdd"):
        d[c] = pd.to_numeric(d[c], errors="coerce").round(3)
    d.to_csv(OUT_CSV, index=False)
    print(f"[bt] CSV: {OUT_CSV} · {len(d)} baris · {time.time() - t0:.0f}s total")

    # ── ringkasan ─────────────────────────────────────────────────────────
    L: list[str] = []
    add = L.append
    add("# Backtest V7 — komponen teknikal (replay data 1 tahun)")
    add("")
    add(f"- Window: {d['date'].min()} → {d['date'].max()} · {len(d)} baris kandidat "
        f"· {d['code'].nunique()} saham")
    add(f"- Finalis (top-{FINALIS_N} likuid/hari): {int(d['finalis'].sum())} baris")
    add("- Catatan: skor = V4 core (tanpa broker/asing/fundamental — tidak ada riwayat). "
        "TP/SL = compute_exit() asli. 'out' = TP/SL tersentuh pertama (semantik tracker).")

    def block(title: str, x: pd.DataFrame) -> None:
        r = x["retLast"].dropna()
        r20 = x["ret20f"].dropna()
        al = x["alpha"].dropna()
        if len(x) == 0:
            add(f"- **{title}**: (kosong)")
            return
        wr = (r > 0).mean() * 100 if len(r) else float("nan")
        tp = (x["out"] == "tp").mean() * 100 if len(x) else float("nan")
        sl = (x["out"] == "sl").mean() * 100 if len(x) else float("nan")
        add(f"- **{title}**: n={len(x)} · WR={wr:.1f}% · avgLast="
            f"{r.mean():+.2f}% · med={r.median():+.2f}% · "
            f"avgH20={r20.mean():+.2f}% (n={len(r20)}) · "
            f"alphaLast={al.mean():+.2f}% (n={len(al)}) · TP {tp:.0f}% / SL {sl:.0f}%")

    add("")
    add("## Setup inti")
    f60 = d[d["finalis"]]
    block("S0 · semua baris", d)
    block("S1 · finalis top-60", f60)
    block("S2 · finalis & vol>=1.0", f60[f60["vol1"]])
    block("S3 · finalis & vol>=1.0 & qg(BUY) lolos", f60[f60["vol1"] & f60["qg_buy"].eq(1)])
    block("S4 · finalis & vol>=1.0 & regime RANGING", f60[f60["vol1"] & f60["regime"].eq("RANGING")])
    add("")
    add("## Pertanyaan pra-filter (vol & likuiditas)")
    block("vol>=1.0 (semua baris)", d[d["vol1"]])
    block("vol<1.0 (dibuang pra-filter produksi)", d[~d["vol1"]])
    block("likuid >=Rp800jt", d[d["liq_ok"]])
    block("tidak likuid <Rp800jt", d[~d["liq_ok"]])
    add("")
    add("## Uji peringkat (top-N harian, likuid)")
    for N in (20, 40, 60, 100, 200):
        block(f"top-{N}", d[d["rank_liq"].le(N)])

    add("")
    add("## Per regime (finalis & vol>=1.0)")
    for rg in ("BULL", "RANGING", "HIGH_VOLATILITY", "BEAR"):
        block(rg, f60[f60["vol1"] & f60["regime"].eq(rg)])

    add("")
    add("## Per band skor V4 (semua baris)")
    d["band"] = pd.cut(d["v4"], bins=[40, 45, 50, 55, 60, 65, 70, 75, 100], right=False)
    for band, g in d.groupby("band", observed=True):
        block(f"v4 {band}", g)

    add("")
    add("## Grid TP/SL (finalis & vol>=1.0) — % tersentuh dalam H+20")
    x = f60[f60["vol1"]]
    if len(x):
        add(f"- TP 2.0xATR: {(x['tph20'] > 0).mean() * 100:.0f}% · "
            f"2.5x: {(x['tph25'] > 0).mean() * 100:.0f}% · "
            f"3.0x: {(x['tph30'] > 0).mean() * 100:.0f}% · "
            f"4.0x: {(x['tph40'] > 0).mean() * 100:.0f}%")
        add(f"- SL 1.0xATR: {(x['slh10'] > 0).mean() * 100:.0f}% · "
            f"1.5x: {(x['slh15'] > 0).mean() * 100:.0f}% · "
            f"2.0x: {(x['slh20'] > 0).mean() * 100:.0f}% · "
            f"2.5x: {(x['slh25'] > 0).mean() * 100:.0f}%")

    add("")
    add("## Validasi silang vs kejadian nyata (16/09)")
    san = d[(d["code"].isin(["SRSN", "TOTL", "KICI", "ARII", "TLKM", "AYAM"]))
            & d["date"].eq("2026-09-16")]
    if len(san):
        cols = ["code", "v4", "rank_liq", "nilai20", "vol_ratio", "regime",
                "out", "hit_day", "ret20f", "retLast", "maxup", "maxdd"]
        add("```")
        add(san[cols].to_string(index=False))
        add("```")
    else:
        add("(tidak ada baris 16/09 utk kode validasi)")

    md = "\n".join(L)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    print(md)
    print(f"\n[bt] ringkasan: {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
