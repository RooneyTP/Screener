"""
v7_scan.py — V7 Dual Mode Scanner (Swing + Intraday)
======================================================
Output ke Telegram: daftar terpisah Swing vs Intraday + exit strategy.

Pipeline:
  1. Scan watchlist konglomerat (35+ ticker)
  2. V7 scoring: V4 core (50%) + broker flow (20%) + foreign (15%) + fundamental (15%)
  3. Pisahin: Swing (score>=55) vs Intraday (score>=48 + volume surge)
  4. Hitung exit: TP, SL, trailing, time stop, sizing
  5. Output ke stdout untuk cron + Telegram
"""

import sys, os, warnings, json
warnings.filterwarnings('ignore')
ROOT = r'C:\Hermes_Workspace\Screener\idx_alpha_screener'
sys.path.insert(0, ROOT)
from datetime import datetime, timedelta
import pandas as pd, logging, yaml
logging.basicConfig(level=logging.WARNING)

from data import fetch_with_cache, compute_all_indicators, align_to_market, fetch_ihsg_cached
from regime import detect_market_regime
from scoring import compute_total_score, classify
from data_invezgo import InvezgoProvider
import v7 as v7_engine
from v7_exit import compute_exit, position_sizing

# ── Config ──
with open(os.path.join(ROOT, "config.yaml")) as f:
    CONFIG = yaml.safe_load(f)

WATCHLIST = []
for grp in CONFIG.get("watchlist", {}).values():
    if isinstance(grp, list): WATCHLIST.extend(grp)
WATCHLIST = list(dict.fromkeys(WATCHLIST))  # unique

CAPITAL = 20_000_000  # modal Rp 20jt
v7_engine.enabled = True
ip = InvezgoProvider()
df_ihsg = fetch_ihsg_cached(period="2y")

# ── Header ──
print(f"📈 *V7 Dual Mode Scan*")
print(f"🗓 {datetime.now().strftime('%d/%m/%Y %H:%M')} WIB")
print(f"👤 Modal: Rp {CAPITAL:,}")
print()

swing_list = []
intraday_list = []

for tkr in WATCHLIST:
    try:
        df = fetch_with_cache(tkr+'.JK', period="6mo")
        if df.empty or len(df) < 60: continue
        df = compute_all_indicators(df)
        df = align_to_market(df, df_ihsg=df_ihsg).dropna()
        row = df.iloc[-1]
        if pd.isna(row.get("rsi")): continue
        
        regime, _, _ = detect_market_regime(df)
        v4s = compute_total_score(row, regime)
        v7r = v7_engine.compute(tkr, v4s, regime)
        
        if v7r["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY"):
            price = float(row["close"])
            atr = float(row.get("atr", 0))
            atr_pct = (atr / price * 100) if price > 0 else 0
            bf = v7r["factors"].get("broker_detail", "")
            ff = v7r["factors"].get("foreign_detail", "")
            vol_ratio = float(row.get("vol_ratio", 1))
            weekly = row.get("weekly_trend", "NO_DATA")
            
            # ── Swing: score>=55 atau broker akumulasi ──
            swing_score = v7r["score"]
            if "akumulasi" in bf and v7r["score"] >= 48:
                swing_score += 5  # bonus broker flow
            
            if swing_score >= 50 or ("akumulasi" in bf and v7r["score"] >= 48):
                exit_s = compute_exit(price, atr, regime, "swing", weekly)
                sizing = position_sizing(CAPITAL, price, swing_score, atr_pct)
                swing_list.append({
                    "tkr": tkr, "score": swing_score, "price": price,
                    "exit": exit_s, "sizing": sizing,
                    "bf": bf, "ff": ff, "weekly": weekly,
                })
            
            # ── Intraday Swing: volume surge + score>=48 ──
            if v7r["score"] >= 48 and vol_ratio > 1.2:
                exit_i = compute_exit(price, atr, regime, "intraday", weekly)
                sizing_i = position_sizing(CAPITAL, price, v7r["score"], atr_pct)
                intraday_list.append({
                    "tkr": tkr, "score": v7r["score"], "price": price,
                    "exit": exit_i, "sizing": sizing_i,
                    "bf": bf, "ff": ff, "vol": vol_ratio,
                })
    except Exception as e:
        pass

# ── Urutin ──
swing_list.sort(key=lambda x: x["score"], reverse=True)
intraday_list.sort(key=lambda x: x["score"], reverse=True)

# ── Output Swing ──
print("🟢 *SWING TRADE (H+5 hingga H+20)*")
if swing_list:
    print(f"Total: {len(swing_list)} sinyal")
    for s in swing_list:
        e = s["exit"]
        si = s["sizing"]
        cap = " ⚠️" if s["weekly"] == "BEARISH" else ""
        print(f"{s['tkr']:<6} Skor {s['score']:.1f} | Rp{s['price']:,}")
        print(f"  🛑 SL Rp{e['stop_loss']:,} | 🎯 TP Rp{e['take_profit']:,} | 📏 Trail >Rp{e['trailing_start']:,}")
        print(f"  📊 Hold max {e['max_hold_days']} hari | RRR {e['rrr']}")
        print(f"  💰 Lot {si['lots']} (Rp{si['cost']:,} = {si['pct_modal']}% modal){cap}")
        if s['bf'] != 'netral' and s['bf'] != 'no_data':
            print(f"  🏦 Broker: {s['bf']} | Foreign: {s['ff']}")
else:
    print("Tidak ada sinyal swing hari ini.")
print()

# ── Output Intraday ──
print("🔵 *INTRADAY SWING (H+1 hingga H+3)*")
if intraday_list:
    print(f"Total: {len(intraday_list)} sinyal")
    for s in intraday_list:
        e = s["exit"]
        si = s["sizing"]
        print(f"{s['tkr']:<6} Skor {s['score']:.1f} | Rp{s['price']:,}")
        print(f"  🛑 SL Rp{e['stop_loss']:,} | 🎯 TP Rp{e['take_profit']:,}")
        print(f"  ⏱️ Hold max {e['max_hold_days']} hari | RRR {e['rrr']} | Vol {s['vol']:.1f}x")
        print(f"  💰 Lot {si['lots']} (Rp{si['cost']:,} = {si['pct_modal']}% modal)")
        if s['bf'] != 'netral' and s['bf'] != 'no_data':
            print(f"  🏦 Broker: {s['bf']} | Foreign: {s['ff']}")
else:
    print("Tidak ada sinyal intraday hari ini.")
print()

# ── Summary ──
sep = "▬" * 30
print(sep)
print(f"🏆 *Ringkasan*")
print(f"🟢 Swing: {len(swing_list)} sinyal")
print(f"🔵 Intraday: {len(intraday_list)} sinyal")
alloc = sum(s['sizing']['cost'] for s in swing_list[:3]) + sum(s['sizing']['cost'] for s in intraday_list[:3])
print(f"💰 Alokasi top 3 swing + 3 intraday: Rp{alloc:,} dari Rp{CAPITAL:,}")
print()
print(f"📊 *Exit Strategy*")
print(f"• Swing: Trailing stop aktif setelah harga > entry + 1.5×ATR")
print(f"• Intraday: Time stop H+3 — exit automatis")
print(f"• Stop loss: harga tutup di bawah SL")
print()
print(sep)
print("⚠️ *Disclaimer*")
print("Sinyal berdasarkan data real-time Invezgo dan V7 engine.")
print("Keputusan trading sepenuhnya di tangan pengguna.")
print("Selalu gunakan money management yang baik.")
