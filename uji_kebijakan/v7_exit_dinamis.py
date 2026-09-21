#!/usr/bin/env python3
"""Uji EXIT DINAMIS (simulasi jalur OHLC dari cache_v7 925 saham).

Baseline (validasi dulu): TP 3.0×ATR / SL 1.5×ATR (fixed), entry = close sinyal, max H+20.
Varian:
  A) Trailing 2.0×ATR — stop = high-tertinggi-sejak-masuk − 2.0×ATR; tanpa TP; max 20 hr
  D) Trailing 2.5×ATR — idem, 2.5
  B) Break-even — SL 1.5×ATR; begitu high ≥ entry+1.5×ATR → SL pindah ke entry; TP 3×ATR tetap
  C) Time-stop 10 — kalau H+10 belum kena TP/SL → keluar di close hari ke-10

Asumsi: 1 bar kena TP & SL → SL dulu (konservatif). Biaya 0.4% round-trip.
Validasi: fraksi sentuh TP/SL dari sim HARUS ≈ kolom tph30/slh15.
"""
import csv, glob, os

CACHE = "/home/yuan/screener/idx_alpha_screener/data/cache_v7"
P = "/home/yuan/screener/idx_alpha_screener/data/backtest_v7_trades.csv"
COST = 0.4

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# ---- load cache per saham ----
bars = {}
for path in glob.glob(os.path.join(CACHE, "v7_*.csv")):
    code = os.path.basename(path)[3:-7]  # v7_KICI_1y.csv -> KICI
    arr = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            o = fnum(r["Open"]); h = fnum(r["High"]); l = fnum(r["Low"]); c = fnum(r["Close"])
            if None not in (o, h, l, c):
                arr.append((r["Date"][:10], o, h, l, c))
    arr.sort()
    bars[code] = arr

# ---- signals ----
sigs = []
with open(P, encoding="utf-8") as f:
    for r in csv.DictReader(f):
        if r["finalis"] == "True" and r["vol1"] == "True" and r["regime"] == "RANGING":
            a = fnum(r["atrp"]); c = fnum(r["close"])
            if a and a > 0 and c and r["code"] in bars:
                sigs.append(dict(code=r["code"], date=r["date"], atrp=a, close=c,
                                 tph30=(fnum(r["tph30"]) or 0) > 0, slh15=(fnum(r["slh15"]) or 0) > 0))
print(f"Signal dipakai: {len(sigs)}")

skipped = 0
def sim(sig, mode):
    """Return (ret_pct, exit_kind) atau None kalau data kurang."""
    global skipped
    code, d0, atrp, entry = sig["code"], sig["date"], sig["atrp"], sig["close"]
    arr = bars[code]
    idx = next((i for i, b in enumerate(arr) if b[0] == d0), None)
    if idx is None: skipped += 1; return None
    fwd = arr[idx+1: idx+21]
    if len(fwd) < 5: skipped += 1; return None
    sl = entry * (1 - 1.5 * atrp / 100)
    tp = entry * (1 + 3.0 * atrp / 100)
    tstop = entry * (1 - 1.5 * atrp / 100)  # SL awal utk BE
    be_done = False
    max_high = entry
    stop_tr = None
    for n, (dt, o, h, l, c) in enumerate(fwd, start=1):
        if mode == "base":
            if l <= sl: return (-1.5 * atrp - COST, "sl")
            if h >= tp: return (3.0 * atrp - COST, "tp")
        elif mode == "be":
            if l <= (entry if be_done else sl): return (-1.5 * atrp - COST if not be_done else 0.0 - COST, "be" if be_done else "sl")
            if h >= tp: return (3.0 * atrp - COST, "tp")
            if not be_done and h >= entry * (1 + 1.5 * atrp / 100): be_done = True
        elif mode == "ts10":
            if l <= sl: return (-1.5 * atrp - COST, "sl")
            if h >= tp: return (3.0 * atrp - COST, "tp")
            if n == 10: return ((c / entry - 1) * 100 - COST, "time")
        else:  # trailing
            mult = 2.0 if mode == "tr2" else 2.5
            if stop_tr is not None and l <= stop_tr:
                return ((stop_tr / entry - 1) * 100 - COST, "trail")
            if h > max_high:
                max_high = h
            stop_tr = max_high * (1 - mult * atrp / 100)
    return ((fwd[-1][4] / entry - 1) * 100 - COST, "end")

modes = ["base", "be", "ts10", "tr2", "tr25"]
res = {}
for m in modes:
    res[m] = [sim(s, m) for s in sigs]

# Validasi baseline vs kolom
base_rets = [r[0] for r in res["base"] if r]
sim_tp = sum(1 for r in res["base"] if r and r[1] == "tp") / len(base_rets)
sim_sl = sum(1 for r in res["base"] if r and r[1] == "sl") / len(base_rets)
col_tp = sum(1 for s in sigs if s["tph30"]) / len(sigs)
col_sl = sum(1 for s in sigs if s["slh15"]) / len(sigs)
print(f"VALIDASI (fraksi sentuh pertama): sim TP {100*sim_tp:.1f}% vs kolom tph30 {100*col_tp:.1f}% | "
      f"sim SL {100*sim_sl:.1f}% vs kolom slh15 {100*col_sl:.1f}%")
print(f"VALIDASI (exp): sim base {sum(base_rets)/len(base_rets):+.2f}% vs kolom-derived +0.85% (acuan)")
print(f"skip: {skipped}\n")

dates = sorted({s["date"] for s in sigs}); mid = dates[len(dates)//2]

def summarize(name, rs):
    vals = [(s["date"], r[0]) for s, r in zip(sigs, rs) if r]
    n = len(vals)
    exp = sum(v for _, v in vals) / n
    wr = 100 * sum(1 for _, v in vals if v > 0) / n
    h1 = [v for d, v in vals if d < mid]; h2 = [v for d, v in vals if d >= mid]
    return n, exp, wr, sum(h1)/len(h1), sum(h2)/len(h2)

base_n, base_exp, base_wr, base_h1, base_h2 = summarize("base", res["base"])
print(f"{'varian':>6} | {'n':>5} | {'exp':>7} {'Δbase':>7} | {'WR':>6} | {'H1':>7} {'H2':>7}")
for m in modes:
    n, e, wr, h1, h2 = summarize(m, res[m])
    tag = "  ← baseline" if m == "base" else ""
    print(f"{m:>6} | {n:>5} | {e:>+6.2f}% {e-base_exp:>+6.2f}% | {wr:>5.1f}% | {h1:>+6.2f}% {h2:>+6.2f}%{tag}")

# Paired diff: varian terbaik vs base (baris sama)
for m in ["tr2", "tr25", "be", "ts10"]:
    diffs = [a[0] - b[0] for a, b in zip(res[m], res["base"]) if a and b]
    n = len(diffs); mean = sum(diffs) / n
    sd = (sum((x - mean) ** 2 for x in diffs) / (n - 1)) ** 0.5
    se = sd / n ** 0.5
    print(f"Paired {m}−base: n={n} mean={mean:+.2f}% SE={se:.2f}% t={mean/se:+.2f}")
