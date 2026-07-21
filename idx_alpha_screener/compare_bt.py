"""Compare all backtest iterations side by side"""
import csv, sys
from collections import defaultdict

# v4 (latest - with conviction bonus)
with open('backtest_results_20260706_172219.csv') as f:
    v4 = list(csv.DictReader(f))

# Aggregate v4
def agg(rows):
    d = defaultdict(lambda: {'n':0, 'ret':0})
    for r in rows:
        sig = r['signal']
        n = int(r['count'])
        ret = float(r['avg_return_h5'])
        d[sig]['n'] += n
        d[sig]['ret'] += ret * n
    return d

d4 = agg(v4)

# Results table
iters = {
    'v1 (contrarian)': {'SB': (-0.19,170), 'B':(-0.20,1525),'WB':(-0.28,2252),'H':(-0.74,13338),'S':(-0.12,4126)},
    'v2 (momentum)': {'SB': (0.25,70), 'B':(-0.34,1306),'WB':(-0.40,2847),'H':(-0.64,14080),'S':(-0.47,8818)},
    'v3 (gate)': {'SB': (0.25,70), 'B':(-0.34,1291),'WB':(-0.40,2862),'H':(-0.64,14080),'S':(-0.47,8818)},
}

# v4 from live data
d4_calc = {}
for sig in ['STRONG_BUY','BUY','WEAK_BUY','HOLD','SELL']:
    d4_calc[sig] = (d4[sig]['ret']/d4[sig]['n'], d4[sig]['n']) if d4[sig]['n'] else (0,0)

iters['v4 (conviction)'] = {
    'SB': d4_calc['STRONG_BUY'],
    'B': d4_calc['BUY'],
    'WB': d4_calc['WEAK_BUY'],
    'H': d4_calc['HOLD'],
    'S': d4_calc['SELL'],
}

print(f"{'Iteration':>18} {'SB':>20} {'BUY':>20} {'WB':>20} {'HOLD':>20} {'SELL':>20}")
print('-' * 100)

for name, data in iters.items():
    parts = []
    for sig_key in ['SB','B','WB','H','S']:
        ret, n = data[sig_key]
        if n == 0:
            parts.append(f"{'—':>20}")
        else:
            parts.append(f"{ret:+.2f}% ({n:>5}x)")
    print(f"{name:>18}: {'  |  '.join(parts)}")
