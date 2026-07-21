import csv, sys
from collections import defaultdict

with open(sys.argv[1]) as f:
    reader = csv.DictReader(f)
    rows = list(reader)

agg = defaultdict(lambda: {'count':0, 'score':0, 'ret':0, 'wr':0, 'n':0})
for r in rows:
    sig = r['signal']
    agg[sig]['count'] += int(r['count'])
    agg[sig]['score'] += float(r['avg_score']) * int(r['count'])
    agg[sig]['ret'] += float(r['avg_return_h5']) * int(r['count'])
    agg[sig]['wr'] += float(r['wr_h5']) * int(r['count'])
    agg[sig]['n'] += int(r['count'])

# Compare old vs new
old = {
    'STRONG_BUY': (-0.19, 170),
    'BUY': (-0.20, 1525),
    'WEAK_BUY': (-0.28, 2252),
    'HOLD': (-0.74, 13338),
    'SELL': (-0.12, 4126),
}

print(f"{'Sinyal':>12} {'N_old':>6} {'N_new':>6} {'WR_old':>7} {'WR_new':>7} {'Ret_old':>8} {'Ret_new':>8} {'Delta':>8}")
print('-'*70)
for sig in ['STRONG_BUY','BUY','WEAK_BUY','HOLD','SELL']:
    d = agg[sig]
    n = d['n']
    avg_ret = d['ret']/d['n'] if d['n'] else 0
    avg_wr = d['wr']/d['n'] if d['n'] else 0
    old_ret, old_n = old.get(sig, (0,0))
    old_wr = 0
    print(f"{sig:>12} {old_n:>6} {n:>6} {'-':>7} {avg_wr:>6.1f}% {old_ret:>+7.2f}% {avg_ret:>+7.2f}% {avg_ret-old_ret:>+7.2f}%")

print()
print('=== TOP 10 BUY by exp_return_h5 ===')
buys = [r for r in rows if r['signal'] in ('BUY','STRONG_BUY')]
buys.sort(key=lambda r: float(r['exp_return_h5']), reverse=True)
print(f"{'Ticker':>7} {'Signal':>12} {'N':>4} {'AvgScore':>9} {'WR_H5':>7} {'AvgRet':>8} {'ExpRet':>8}")
for r in buys[:10]:
    print(f"{r['ticker']:>7} {r['signal']:>12} {int(r['count']):>4} {float(r['avg_score']):>8.1f} {float(r['wr_h5']):>6.1f}% {float(r['avg_return_h5']):>+7.2f}% {float(r['exp_return_h5']):>+7.2f}%")

print()
print('=== WORST 10 BUY by exp_return_h5 ===')
buys.sort(key=lambda r: float(r['exp_return_h5']))
for r in buys[:10]:
    print(f"{r['ticker']:>7} {r['signal']:>12} {int(r['count']):>4} {float(r['avg_score']):>8.1f} {float(r['wr_h5']):>6.1f}% {float(r['avg_return_h5']):>+7.1f}% {float(r['exp_return_h5']):>+7.1f}%")
