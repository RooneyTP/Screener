#!/usr/bin/env python3
"""ihsg_ctx.py — konteks IHSG window 16–24 Sep + alpha kandidat vs IHSG.

Jawab: 'yang turun itu karena pasar atau karena seleksi?' (alpha = ret saham − ret IHSG).
"""
import csv
import statistics as st


def closes(path):
    rows = []
    with open(path, newline="") as f:
        rd = csv.reader(f)
        next(rd, None)
        for r in rd:
            if len(r) < 5 or not r[0][:4].isdigit():
                continue
            try:
                rows.append((r[0][:10], float(r[4])))
            except ValueError:
                continue
    rows.sort()
    return rows


ih = closes("/home/yuan/screener/cache/_IHSG_.csv")
cl = [c for _, c in ih]
print(f"IHSG rows={len(ih)} | terakhir {ih[-1][0]} close {ih[-1][1]:.1f}")
ma50 = sum(cl[-50:]) / 50
print(f"MA50 kini: {ma50:.1f} | {'DI BAWAH MA50' if cl[-1] < ma50 else 'di atas MA50'} ({(cl[-1]/ma50-1)*100:+.2f}%)")

print("\n— per tanggal bursa window —")
print("  tanggal       close     MA50     jarak%    <MA50?")
for d, c in ih:
    if not (d >= "2026-09-10" and d <= "2026-09-30"):
        continue
    i = next(j for j, (dd, _) in enumerate(ih) if dd == d)
    if i >= 49:
        m = sum(cl[i - 49:i + 1]) / 50
        print(f"  {d}  {c:8.1f}  {m:8.1f}  {(c/m-1)*100:+6.2f}%   {'YA' if c < m else 'tidak'}")
    else:
        print(f"  {d}  {c:8.1f}  (MA50 kurang data)")

d0 = "2026-09-16"
c0 = next(c for d, c in ih if d == d0)
print(f"\nIHSG {d0} → {ih[-1][0]}: {(cl[-1]/c0-1)*100:+.2f}%")

# — alpha kandidat —
def ihsg_ret(dari, sampai):
    """close(dari) → close(sampai); pakai tanggal bursa terdekat ≤ dari."""
    base = None
    for d, c in ih:
        if d <= dari:
            base = c
        else:
            break
    if base is None:
        return None
    return cl[-1] / base - 1


kand = []
with open("/home/yuan/screener/idx_alpha_screener/data/kandidat_hasil_live.csv", newline="") as f:
    for r in csv.DictReader(f):
        try:
            r["lastret_f"] = float(r["lastret"])
            r["maxdd_f"] = float(r["maxdd"])
            r["maxup_f"] = float(r["maxup"])
            r["n_fwd_i"] = int(r["n_fwd"])
        except (ValueError, KeyError):
            continue
        if r["n_fwd_i"] < 1:
            continue
        ir = ihsg_ret(r["tgl"], ih[-1][0])
        if ir is None:
            continue
        r["alpha"] = r["lastret_f"] - ir
        r["ihsg"] = ir
        kand.append(r)

print(f"\n— ALPHA vs IHSG (n={len(kand)} kandidat, {d0}→{ih[-1][0]}; alpha = ret − IHSG) —")


def grp(nama, sel):
    if not sel:
        print(f"  {nama:34} n=0")
        return
    a = [x["alpha"] for x in sel]
    lr = [x["lastret_f"] for x in sel]
    print(f"  {nama:34} n={len(sel):3} | alpha med {st.median(a)*100:+5.1f}% rata2 {st.mean(a)*100:+5.1f}% | "
          f"ret med {st.median(lr)*100:+5.1f}% | positif: {100*sum(1 for v in a if v>0)/len(a):3.0f}%")


grp("SEMUA kandidat", kand)
grp("tampil=ya (sinyal)", [x for x in kand if x["tampil"] == "ya"])
grp("tampil=tidak", [x for x in kand if x["tampil"] == "tidak"])

liq = [x for x in kand if x.get("atr_pct") not in ("", None)]
# alpha per band skor
for lo, hi in ((0, 50), (50, 60), (60, 65), (65, 100)):
    grp(f"skor [{lo},{hi})", [x for x in kand if lo <= (float(x["skor"]) if x["skor"] else 0) < hi])

print("\n— 10 alpha terbaik / terburuk —")
for x in sorted(kand, key=lambda v: -v["alpha"])[:10]:
    print(f"  +{x['alpha']*100:5.1f}%  {x['kode']:5} {x['tgl']}  ret {x['lastret_f']*100:+5.1f}%  tampil={x['tampil']}")
for x in sorted(kand, key=lambda v: v["alpha"])[:10]:
    print(f"  {x['alpha']*100:+6.1f}%  {x['kode']:5} {x['tgl']}  ret {x['lastret_f']*100:+5.1f}%  tampil={x['tampil']}")
