import csv, sys
from collections import defaultdict

with open('backtest_results_20260706_165446.csv') as f:
    rows = list(csv.DictReader(f))

# Analyze signal quality by various filters
buys = [r for r in rows if r['signal'] in ('BUY','STRONG_BUY')]

# 1. BY SIGNAL COUNT per ticker
print("=== BUY signals by count group ===")
grps = defaultdict(lambda: {'n':0, 'ret':0, 'wr':0})
for r in buys:
    n = int(r['count'])
    ret = float(r['avg_return_h5'])
    wr = float(r['wr_h5'])
    if n <= 1: g = '1'
    elif n <= 3: g = '2-3'
    elif n <= 5: g = '4-5'
    else: g = '6+'
    grps[g]['n'] += n
    grps[g]['ret'] += ret * n
    grps[g]['wr'] += wr * n

for g in ['1','2-3','4-5','6+']:
    d = grps[g]
    if d['n']:
        print(f"  N={g:>3}: avg_ret={d['ret']/d['n']:+.2f}% wr={d['wr']/d['n']:.1f}% total_sigs={d['n']}")

# 2. BY SCORE RANGE (for BUY only)
print("\n=== BUY signals by score range ===")
only_buys = [r for r in buys if r['signal'] == 'BUY']
by_score = defaultdict(lambda: {'n':0, 'ret':0})
for r in only_buys:
    s = float(r['avg_score'])
    ret = float(r['avg_return_h5'])
    n = int(r['count'])
    # bin by score
    bin_ = f"{int(s//2*2)}-{int(s//2*2+1)}"
    by_score[bin_]['n'] += n * 5  # approximate
    by_score[bin_]['ret'] += ret * n * 5

# Actually let me do it properly
scores = [(float(r['avg_score']), float(r['avg_return_h5']), int(r['count']), r['ticker']) for r in only_buys]
scores.sort()

print(f"  {'Score Range':>12} {'N':>6} {'AvgRet':>8} {'Worst':>8} {'Best':>8}")
for lo in range(55, 68, 2):
    hi = lo + 1
    in_range = [(s,r,c,t) for s,r,c,t in scores if lo <= s <= hi]
    if in_range:
        total_n = sum(c for _,_,c,_ in in_range)
        avg_ret = sum(r*c for _,r,c,_ in in_range) / total_n if total_n else 0
        worst = min(r for _,r,_,_ in in_range)
        best = max(r for _,r,_,_ in in_range)
        print(f"  {lo:>4}-{hi:<4}    {total_n:>6} {avg_ret:>+7.2f}% {worst:>+7.2f}% {best:>+7.2f}%")

# 3. Key insight: which tickers have best/worst avg_score for BUY?
print("\n=== Extreme BUY tickers ===")
print("  Lowest score BUY:")
for s,r,c,t in scores[:5]:
    print(f"    {t:>6} score={s:.1f} ret={r:+.2f}% count={c}")
print("  Highest score BUY:")
for s,r,c,t in scores[-5:]:
    print(f"    {t:>6} score={s:.1f} ret={r:+.2f}% count={c}")
