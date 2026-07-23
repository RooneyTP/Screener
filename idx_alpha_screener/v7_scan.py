"""
v7_scan.py — V7 Dual Mode Scanner (Invezgo ONLY)
Data 100% dari Invezgo. Output ke Telegram via cron.
"""
import sys, os, warnings, re, yaml
warnings.filterwarnings('ignore')
ROOT = r'C:\Hermes_Workspace\Screener\idx_alpha_screener'
sys.path.insert(0, ROOT)
from datetime import datetime
import pandas as pd, logging
logging.basicConfig(level=logging.WARNING)

from data import compute_all_indicators, align_to_market, fetch_ihsg_cached
from regime import detect_market_regime
from scoring import compute_total_score
from data_invezgo import InvezgoProvider
import v7 as v7_engine
from v7_exit import compute_exit, position_sizing

# Config
with open(os.path.join(ROOT,"config.yaml")) as f:
    CONFIG = yaml.safe_load(f)
WATCHLIST = list(dict.fromkeys([
    t for g in CONFIG.get("watchlist",{}).values() if isinstance(g,list) for t in g
]))
CAPITAL = 20_000_000
v7_engine.enabled = True
ip = InvezgoProvider()
df_ihsg = fetch_ihsg_cached(period="2y")

# Broker names
BROKER_NAMES = {}
try:
    for b in ip.client.analysis.get_broker_list():
        BROKER_NAMES[b.get("code","")] = b.get("name","")[:25]
except: pass

def format_brokers(s):
    if not s: return ""
    p = s.split("|")
    r = []
    for part in p:
        part = part.strip()
        if not part: continue
        codes = re.findall(r'([A-Z]{2})\(', part)
        named = []
        for c in codes:
            name = BROKER_NAMES.get(c,"")
            # Ambil kata pertama aja biar pendek
            if name:
                short = name.split()[0] if " " in name else name
            else:
                short = c
            named.append(short)
        is_buy = "B" in part[:1] or "blue" in part.lower() or part.startswith("\U0001f535")
        prefix = "Beli: " if is_buy else "Jual: "
        r.append(prefix + ", ".join(named))
    return " | ".join(r)

# Print header
print("V7 Dual Mode Scan")
print("{} WIB".format(datetime.now().strftime('%d/%m/%Y %H:%M')))
print("Modal: Rp {:,}".format(CAPITAL))
print()

swing = []
intra = []

for tkr in WATCHLIST:
    try:
        df = ip.get_historical(tkr, period="1y")
        if df.empty or len(df)<60: continue
        df = compute_all_indicators(df)
        df = align_to_market(df, df_ihsg=df_ihsg).dropna()
        if len(df)<30: continue
        row = df.iloc[-1]
        if pd.isna(row.get("rsi")): continue
        regime,_,_ = detect_market_regime(df)
        v4s = compute_total_score(row, regime)
        v7r = v7_engine.compute(tkr, v4s, regime)
        
        if v7r["signal"] not in ("STRONG_BUY","BUY","WEAK_BUY"): 
            continue
        
        price = float(row["close"])
        atr = float(row.get("atr",0) or 0)
        atr_pct = (atr/price*100) if price>0 else 0
        bf = v7r["factors"].get("broker_detail","")
        ff = v7r["factors"].get("foreign_detail","")
        vol_ratio = float(row.get("vol_ratio",1) or 1)
        weekly = row.get("weekly_trend","NO_DATA")
        brokers_raw = v7r["factors"].get("brokers","")
        
        swing_score = v7r["score"]
        if "akumulasi" in bf and v7r["score"]>=48: swing_score += 5
        
        ok = swing_score>=50 or ("akumulasi" in bf and v7r["score"]>=48)
        if not ok: 
            continue
        if "distribusi" in bf and v7r["score"]<55: 
            continue
        nn = ("netral" in bf) or ("net_buy" in bf and swing_score<52)
        if swing_score<55 and nn: 
            continue
        
        ex = compute_exit(price, atr, regime, "swing", weekly)
        sz = position_sizing(CAPITAL, price, swing_score, atr_pct)
        swing.append({
            "tkr":tkr,"score":swing_score,"price":price,
            "exit":ex,"sizing":sz,
            "bf":bf,"ff":ff,"weekly":weekly,"brokers":format_brokers(brokers_raw),
        })
        
        # Intraday
        if v7r["score"]>=48 and vol_ratio>=1.0:
            ex2 = compute_exit(price, atr, regime, "intraday", weekly)
            sz2 = position_sizing(CAPITAL, price, v7r["score"], atr_pct)
            intra.append({
                "tkr":tkr,"score":v7r["score"],"price":price,
                "exit":ex2,"sizing":sz2,"bf":bf,"ff":ff,"vol":vol_ratio,
            })
    except Exception:
        pass

swing.sort(key=lambda x:x["score"],reverse=True)
intra.sort(key=lambda x:x["score"],reverse=True)

# Print Swing
print("SWING TRADE (H+5 sd H+20)")
if swing:
    print("Total: {} sinyal".format(len(swing)))
    for i,s in enumerate(swing):
        e=s["exit"]; si=s["sizing"]
        cap = " WEEKLY BEAR" if s["weekly"]=="BEARISH" else ""
        print("#{:<2} {} {:>5.1f} | Rp{:,} | SL {} | TP {}{}".format(i+1,s["tkr"],s["score"],int(s["price"]),int(e["stop_loss"]),int(e["take_profit"]),cap))
        if s["bf"] and s["bf"] not in ("netral","no_data"):
            print("  {} | Foreign: {}".format(s["bf"],s["ff"]))
else:
    print("Tidak ada sinyal swing hari ini.")
print()

# Print Intraday
print("INTRADAY SWING (H+1 sd H+3)")
if intra:
    print("Total: {} sinyal".format(len(intra)))
    for s in intra:
        e=s["exit"]; si=s["sizing"]
        print("{:<6} {:>5.1f} | Rp{:,} | SL {} | TP {} | Vol {:.1f}x".format(s["tkr"],s["score"],int(s["price"]),int(e["stop_loss"]),int(e["take_profit"]),s["vol"]))
        if s["bf"] and s["bf"] not in ("netral","no_data"):
            print("  {}".format(s["bf"]))
else:
    print("Tidak ada sinyal intraday hari ini.")
print()

# Summary
alloc = sum(s["sizing"]["cost"] for s in swing[:3]) + sum(s["sizing"]["cost"] for s in intra[:3])
tp_swing = sum((s["exit"]["take_profit"]-int(s["price"]))*s["sizing"]["lots"]*100 for s in swing[:3])
tp_intra = sum((s["exit"]["take_profit"]-int(s["price"]))*s["sizing"]["lots"]*100 for s in intra[:3])
print("-"*30)
print("Ringkasan")
print("Swing: {} sinyal".format(len(swing)))
print("Intraday: {} sinyal".format(len(intra)))
print("Alokasi: Rp{:,} dari Rp{:,}".format(alloc,CAPITAL))
print("Potensi profit: Rp{:,}".format(tp_swing+tp_intra))
print()
print("Exit Strategy:")
print("- Swing: Trailing stop >entry+1.5ATR | Hold max 20 hr")
print("- Intraday: Time stop H+3 | Exit otomatis")
print("- SL: harga tutup di bawah SL")
print()
print("-"*30)
print("Disclaimer:")
print("Data 100% dari Invezgo. Keputusan trading sepenuhnya")
print("di tangan pengguna. Gunakan money management.")
