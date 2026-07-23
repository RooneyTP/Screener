"""
v7_scan.py — V7 Dual Mode Scanner (Invezgo ONLY)
==================================================
Data 100% dari Invezgo — no Yahoo Finance.

Pipeline:
  1. Scan watchlist via Invezgo historical + fundamental + broker
  2. Compute indicators from Invezgo OHLCV data
  3. V7 scoring: V4 core (50%) + broker flow (20%) + foreign (15%) + fundamental (15%)
  4. Pisahin Swing vs Intraday + exit strategy + sizing
  5. Output ke Telegram
"""

import sys, os, warnings, json
warnings.filterwarnings('ignore')
ROOT = r'C:\Hermes_Workspace\Screener\idx_alpha_screener'
sys.path.insert(0, ROOT)
from datetime import datetime, timedelta
import pandas as pd, logging, yaml
logging.basicConfig(level=logging.WARNING)

from data import compute_all_indicators, align_to_market, fetch_ihsg_cached
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
WATCHLIST = list(dict.fromkeys(WATCHLIST))

CAPITAL = 20_000_000
v7_engine.enabled = True
ip = InvezgoProvider()
df_ihsg = fetch_ihsg_cached(period="2y")  # masih pake Yahoo untuk IHSG (cache aja)

# ── Broker name map ──
BROKER_NAMES = {}
try:
    bl = ip.client.analysis.get_broker_list()
    for b in bl:
        BROKER_NAMES[b.get("code","")] = b.get("name","")[:20]
except:
    pass

def broker_name(code):
    return BROKER_NAMES.get(code, code)

def parse_brokers(brokers_str):
    """Parse '🔵BK(+374B) AK(+207B) | 🔴GI SA DU' jadi nama broker"""
    if not brokers_str: return brokers_str
    import re
    parts = brokers_str.split("|")
    result = []
    for part in parts:
        part = part.strip()
        codes = re.findall(r'([A-Z]{2})\(', part)
        named = []
        for c in codes:
            name = BROKER_NAMES.get(c, c)
            named.append(name)
        result.append(f"{'🔵' if '🔵' in part else '🔴'}{', '.join(named)}")
    return " | ".join(result)

# ── Header ──
print(f"📈 *V7 Dual Mode Scan*")
print(f"🗓 {datetime.now().strftime('%d/%m/%Y %H:%M')} WIB")
print(f"👤 Modal: Rp {CAPITAL:,}")
print()

swing_list = []
intraday_list = []

for tkr in WATCHLIST:
    try:
        # ── AMBIL DATA DARI INVEZGO ──
        df = ip.get_historical(tkr, period="6mo")
        if df.empty or len(df) < 60:
            continue
        
        df = compute_all_indicators(df)
        df = align_to_market(df, df_ihsg=df_ihsg).dropna()
        if len(df) < 30:
            continue
        
        row = df.iloc[-1]
        if pd.isna(row.get("rsi")):
            continue
        
        regime, _, _ = detect_market_regime(df)
        
        # ── V4 SCORE ──
        v4s = compute_total_score(row, regime)
        
        # ── V7 SCORE (dengan Invezgo factors) ──
        v7r = v7_engine.compute(tkr, v4s, regime)
        
        if v7r["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY"):
            price = float(row["close"])
            atr = float(row.get("atr", 0))
            atr_pct = (atr / price * 100) if price > 0 else 0
            bf = v7r["factors"].get("broker_detail", "")
            ff = v7r["factors"].get("foreign_detail", "")
            vol_ratio = float(row.get("vol_ratio", 1))
            weekly = row.get("weekly_trend", "NO_DATA")
            brokers_raw = v7r["factors"].get("brokers", "")
            brokers_named = parse_brokers(brokers_raw)
            
            # ── Swing ──
            swing_score = v7r["score"]
            if "akumulasi" in bf and v7r["score"] >= 48:
                swing_score += 5
            
            if swing_score >= 50 or ("akumulasi" in bf and v7r["score"] >= 48):
                if "distribusi" in bf and v7r["score"] < 55:
                    continue
                    
                exit_s = compute_exit(price, atr, regime, "swing", weekly)
                sizing = position_sizing(CAPITAL, price, swing_score, atr_pct)
                
                swing_list.append({
                    "tkr": tkr, "score": swing_score, "price": price,
                    "exit": exit_s, "sizing": sizing,
                    "bf": bf, "ff": ff, "weekly": weekly,
                    "brokers": brokers_named,
                })
            
            # ── Intraday (volume threshold turun ke 1.0x) ──
            if v7r["score"] >= 48 and vol_ratio >= 1.0:
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
    for i, s in enumerate(swing_list):
        e = s["exit"]
        si = s["sizing"]
        cap = " ⚠️ WEEKLY BEAR" if s["weekly"] == "BEARISH" else ""
        print(f"#{i+1} {s['tkr']:<6} Skor {s['score']:.1f} | Rp{s['price']:,}")
        print(f"  🛑 SL Rp{e['stop_loss']:,} | 🎯 TP Rp{e['take_profit']:,} | 📏 Trail >Rp{e['trailing_start']:,}")
        print(f"  📊 Hold max {e['max_hold_days']} hari | RRR {e['rrr']} | Foreign: {s['ff']}{cap}")
        print(f"  💰 Lot {si['lots']} (Rp{si['cost']:,} = {si['pct_modal']}% modal)")
        if s['bf'] and s['bf'] != 'netral' and s['bf'] != 'no_data':
            print(f"  🏦 Flow: {s['bf']}")
            if s.get('brokers'):
                print(f"  {s['brokers']}")
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
        if s['bf'] and s['bf'] != 'netral' and s['bf'] != 'no_data':
            print(f"  🏦 Flow: {s['bf']}")
else:
    print("Tidak ada sinyal intraday hari ini.")
print()

# ── Summary ──
sep = "▬" * 30
print(sep)
print(f"🏆 *Ringkasan*")
print(f"🟢 Swing: {len(swing_list)} sinyal")
print(f"🔵 Intraday: {len(intraday_list)} sinyal")
alloc = sum(s['sizing']['cost'] for s in swing_list[:3])
alloc += sum(s['sizing']['cost'] for s in intraday_list[:3])
print(f"💰 Alokasi: Rp{alloc:,} dari Rp{CAPITAL:,}")
target_swing = sum((s['exit']['take_profit'] - s['price']) * s['sizing']['lots'] * 100 for s in swing_list[:3])
target_intra = sum((s['exit']['take_profit'] - s['price']) * s['sizing']['lots'] * 100 for s in intraday_list[:3])
print(f"🎯 Potensi profit: Rp{target_swing + target_intra:,}")
print()
print(f"📊 *Exit Strategy*")
print(f"• Swing: Trailing stop >entry + 1.5×ATR | Hold max {swing_list[0]['exit']['max_hold_days'] if swing_list else 20} hr")
print(f"• Intraday: Time stop H+3 | Exit otomatis")
print(f"• Stop loss: tutup di bawah SL")
print()
print(sep)
print("⚠️ *Disclaimer*")
print("Data 100% dari Invezgo. Keputusan trading sepenuhnya")
print("di tangan pengguna. Selalu gunakan money management.")
