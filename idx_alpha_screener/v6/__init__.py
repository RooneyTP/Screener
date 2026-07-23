"""
v6 — Corrected Weight Scoring Engine
=====================================
Berdasarkan analisis ML (4.689 sampel), beberapa faktor scoring
ternyata NEGATIF terhadap forward return di IDX:

  POSITIF: volume (+0.0056), trend (+0.0048), weekly_trend (+0.0042)
  NEGATIF: vwap (-0.0056), macd (-0.0051), sr_proximity (-0.0016)

Artinya: saham dengan VWAP premium & MACD bullish SUDAH harga
dibentuk pasar — cenderung reversal. V6 meng-invert faktor-faktor
ini untuk mean-reversion approach.
"""

import logging, numpy as np, pandas as pd
from typing import Optional, Dict
logger = logging.getLogger("v6")

enabled: bool = False
config: dict = {}
THRESHOLDS = {"BULL":[68,58,50,42,35],"BEAR":[65,55,48,40,32],"RANGING":[66,56,48,40,32]}

def configure(cfg:dict):
    global config,THRESHOLDS
    if not cfg: return
    config.update(cfg)
    if "thresholds" in cfg and isinstance(cfg["thresholds"],dict):
        THRESHOLDS.update(cfg["thresholds"])

def is_enabled(): return enabled

# ── V6 Factors (dikoreksi berdasarkan ML) ──

def f_trend(row):
    """Trend alignment — POSITIF predictor"""
    e12,e50,p=row.get("ema12",0),row.get("ema50",0),row.get("close",0)
    a=row.get("adx",0)
    if pd.isna(e12)or pd.isna(e50)or p==0:return 30
    s=30
    if p>e12>e50:s+=30
    elif p>e12 and e12<e50:s+=12
    elif p<e12<e50:s-=10
    if not pd.isna(a):
        if a>=25:s+=12
        elif a>=20:s+=5
        elif a>=15:s+=2
    return max(0,min(100,s))

def f_volume(row):
    """Volume confirmation — POSITIF predictor"""
    v=row.get("vol_ratio",1.0)
    if pd.isna(v)or v==0:return 40
    if v>1.8:return 85
    elif v>1.5:return 75
    elif v>1.2:return 65
    elif v>1.0:return 55
    elif v>0.8:return 45
    return 35

def f_weekly_trend(row):
    """Weekly trend — POSITIF predictor"""
    wt=row.get("weekly_trend","NO_DATA")
    if wt=="BULLISH":return 80
    elif wt=="BEARISH":return 20
    return 40

def f_vwap(row):
    """
    VWAP — INVERTED (NEGATIF predictor)
    Harga jauh di atas VWAP = extended, cenderung reversal.
    Harga dekat/bawah VWAP = mean reversion opportunity.
    """
    pct=row.get("pct_vs_vwap",0)
    if pd.isna(pct):return 40
    # Mean reversion: reward di bawah atau dekat VWAP
    if -2<=pct<0:return 75  # sedikit di bawah VWAP = opportunity
    elif -4<=pct<-2:return 65
    elif 0<pct<=1.5:return 60  # di atas wajar
    elif pct<-4:return 45  # terlalu jauh di bawah = bearish
    elif 1.5<pct<=3:return 40  # mulai extended
    elif 3<pct<=5:return 30
    elif pct>5:return 15  # overextended
    return 40

def f_macd(row):
    """
    MACD — INVERTED (NEGATIF predictor)
    MACD bullish adalah lagging indicator di IDX.
    Reward: MACD histogram rising from below zero (early signal)
    """
    hist=row.get("macd_hist",0)
    sig=row.get("macd_signal",0)
    macd=row.get("macd",0)
    if pd.isna(hist):return 40
    bull=macd>sig
    # Histogram naik dari negatif = early bullish (bagus)
    if not bull and hist>0:return 70  # turning point
    elif bull and hist>0:return 55  # confirmed tapi late
    elif bull:return 50
    elif not bull and hist<0:return 30
    return 40

def f_rsi(row):
    """RSI — INVERTED. RSI 40-55 sweet spot, >65 overbought"""
    r=row.get("rsi",50)
    if pd.isna(r)or r==0:return 30
    if 40<=r<=55:return 70
    elif 35<=r<40:return 60
    elif 55<r<=60:return 55
    elif 30<=r<35:return 45
    elif 60<r<=65:return 35
    elif r>65:return 20
    elif r<30:return 15
    return 40

def f_relative_strength(row):
    """Relative strength vs IHSG — NEUTRAL. Reward mean reversion"""
    r=row.get("ret_20d",0)
    i=row.get("idx_ret_20d",0)
    if pd.isna(r):return 40
    if pd.isna(i)or i==0:
        return 50 if r>0 else 30
    rel=r-i
    if 0<rel<=3:return 70  # slight outperformance
    elif rel>5:return 50  # terlalu outperformed = reversion risk
    elif -3<=rel<=0:return 55  # slight underperformance
    elif -8<=rel<-3:return 40
    elif rel<-8:return 25
    return 40

# ── V6 Compute ──
_FACTORS={
    "trend":f_trend,"volume":f_volume,"weekly_trend":f_weekly_trend,
    "vwap":f_vwap,"macd":f_macd,"rsi":f_rsi,"relative_strength":f_relative_strength,
}
_WEIGHTS=[
    ("trend",0.22),("volume",0.18),("weekly_trend",0.15),
    ("vwap",0.14),("macd",0.10),("rsi",0.10),("relative_strength",0.11),
]

def compute_score(row) -> dict:
    """Compute V6 score + signal"""
    factors={}
    for name,func in _FACTORS.items():
        try:factors[name]=func(row)
        except:factors[name]=40

    score=sum(factors[n]*w for n,w in _WEIGHTS)
    score=round(max(0,min(100,score)),1)

    # Signal
    regime=row.get("regime","RANGING")
    th=THRESHOLDS.get(regime,THRESHOLDS["RANGING"])
    if score>=th[0]:sig="STRONG_BUY"
    elif score>=th[1]:sig="BUY"
    elif score>=th[2]:sig="WEAK_BUY"
    elif score>=th[3]:sig="HOLD"
    else:sig="SELL"

    return {"score":score,"signal":sig,"factors":factors}
