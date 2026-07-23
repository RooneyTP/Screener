"""
v7 — Invezgo-powered Enhanced Scoring Engine
=============================================
Memanfaatkan data eksklusif dari Invezgo yang tidak ada di Yahoo:
  1. Broker Flow (bandarmologi) — deteksi akumulasi/distribusi institusi
  2. Sector Akurat — sector rotation analysis
  3. Fundamental Quality — PER, PBV, ROE, dividend yield
  4. KSEI Sentiment — retail vs institusi ownership trend

V7 = V4 core scoring + bonus/malus dari data Invezgo
"""

import logging, numpy as np
from typing import Optional, Dict

logger = logging.getLogger("v7")

enabled: bool = False
config: dict = {}
THRESHOLDS = {"BULL":[62,52,45,38,30],"BEAR":[58,48,42,35,28],"RANGING":[60,50,42,35,28],"HIGH_VOLATILITY":[60,50,42,35,28]}

_invezgo_provider = None

def configure(cfg: dict):
    global config, THRESHOLDS
    if not cfg: return
    config.update(cfg)
    if "thresholds" in cfg:
        THRESHOLDS.update(cfg["thresholds"])

def is_enabled(): return enabled

def get_provider():
    global _invezgo_provider
    if _invezgo_provider is None:
        try:
            from data_invezgo import InvezgoProvider
            _invezgo_provider = InvezgoProvider()
        except Exception as e:
            logger.error("Gagal init Invezgo provider: %s", e)
            return None
    return _invezgo_provider

# ═══════════════════════════════════════════════════════════════
#  NEW FACTORS (dari Invezgo)
# ═══════════════════════════════════════════════════════════════

def factor_broker_flow(code: str) -> dict:
    """
    Broker Flow Factor — skor berdasarkan akumulasi institusi.
    
    Logic:
      - Top 5 broker: hitung net buy volume
      - Foreign net: positif = bagus
      - Institutional accumulation = sinyal kuat
    
    Returns: {"score": 0-100, "detail": str}
    """
    try:
        provider = get_provider()
        if not provider: return {"score": 40, "detail": "no_data"}
        
        summary = provider.get_broker_summary(code, days=3)
        if not summary or not isinstance(summary, list) or len(summary) < 2:
            return {"score": 40, "detail": "no_data"}
        
        # Hitung net buy dari top broker
        total_net = 0
        top_brokers = sorted(summary[:10], key=lambda x: 
                            int(x.get("buy_value", 0)) + int(x.get("sell_value", 0)), 
                            reverse=True)
        
        for b in top_brokers[:5]:
            buy = int(b.get("buy_value", 0))
            sell = int(b.get("sell_value", 0))
            total_net += (buy - sell)
        
        # Kategorikan
        if total_net > 100_000_000_000:  # >100M net buy
            return {"score": 85, "detail": f"akumulasi_masif_{total_net/1e9:.1f}B"}
        elif total_net > 10_000_000_000:
            return {"score": 75, "detail": f"akumulasi_{total_net/1e9:.1f}B"}
        elif total_net > 1_000_000_000:
            return {"score": 65, "detail": f"net_buy_{total_net/1e9:.1f}B"}
        elif total_net > -1_000_000_000:
            return {"score": 50, "detail": "netral"}
        else:
            return {"score": 30, "detail": "distribusi"}
            
    except Exception as e:
        logger.debug("Broker flow error %s: %s", code, e)
        return {"score": 40, "detail": "error"}


def factor_foreign_flow(code: str) -> dict:
    """Foreign Flow Factor — asing beli atau jual?"""
    try:
        provider = get_provider()
        if not provider: return {"score": 40, "detail": "no_data"}
        
        summary = provider.get_broker_summary(code, days=3)
        if not summary or not isinstance(summary, list):
            return {"score": 40, "detail": "no_data"}
        
        # Cari foreign net dari summary
        foreign_net = 0
        for item in summary:
            if item.get("code") in ["AG", "RG", "DB"]:  # Foreign brokers
                buy = int(item.get("buy_value", 0))
                sell = int(item.get("sell_value", 0))
                foreign_net += (buy - sell)
        
        if foreign_net > 10_000_000_000:
            return {"score": 80, "detail": "asing_beli_besar"}
        elif foreign_net > 1_000_000_000:
            return {"score": 65, "detail": "asing_beli"}
        elif foreign_net > -1_000_000_000:
            return {"score": 50, "detail": "asing_netral"}
        else:
            return {"score": 30, "detail": "asing_jual"}
            
    except Exception as e:
        logger.debug("Foreign flow error %s: %s", code, e)
        return {"score": 40, "detail": "error"}


def factor_fundamental_quality(code: str) -> dict:
    """
    Fundamental Quality Factor — PER, PBV, ROE, dividend.
    
    Ideal value profile IDX:
      PER: 8-15x
      PBV: 1-3x
      ROE: >15%
      Div Yield: >3%
    """
    try:
        provider = get_provider()
        if not provider: return {"score": 40, "detail": "no_data"}
        
        fund = provider.get_fundamental(code)
        if not fund or not isinstance(fund, dict):
            return {"score": 40, "detail": "no_data"}
        
        per = fund.get("PER", fund.get("per", fund.get("pe_ratio", None)))
        pbv = fund.get("PBV", fund.get("pbv", fund.get("pb_ratio", None)))
        roe = fund.get("ROE", fund.get("roe", None))
        div = fund.get("DividendYield", fund.get("dividend_yield", None))
        
        score = 50
        
        # PER: ideal 8-15
        if per is not None and not np.isnan(per):
            if 8 <= per <= 15: score += 20
            elif 5 <= per < 8: score += 10
            elif 15 < per <= 20: score += 5
            elif per > 30: score -= 10
        
        # PBV: ideal 1-3
        if pbv is not None and not np.isnan(pbv):
            if 1 <= pbv <= 3: score += 15
            elif 0.5 <= pbv < 1: score += 8
        
        # ROE: ideal >15%
        if roe is not None and not np.isnan(roe):
            if roe > 20: score += 15
            elif roe > 15: score += 10
            elif roe > 10: score += 5
        
        # Dividend yield
        if div is not None and not np.isnan(div) and div > 0:
            if div > 5: score += 10
            elif div > 3: score += 5
        
        return {"score": max(0, min(100, score)), "detail": f"per={per}_roe={roe}"}
        
    except Exception as e:
        logger.debug("Fundamental error %s: %s", code, e)
        return {"score": 40, "detail": "error"}


# ═══════════════════════════════════════════════════════════════
#  V7 MASTER SCORE — menggabungkan V4 + Invezgo factors
# ═══════════════════════════════════════════════════════════════

_V7_WEIGHTS = {
    "v4_score": 0.50,       # V4 core scoring masih 50%
    "broker_flow": 0.20,    # Broker accumulation 20%
    "foreign_flow": 0.15,   # Foreign flow 15%
    "fundamental": 0.15,    # Fundamental quality 15%
}

def compute(code: str, v4_score: float, regime: str) -> dict:
    """
    Hitung V7 score dengan data Invezgo.
    
    Parameters
    ----------
    code : str — kode saham tanpa .JK
    v4_score : float — skor dari V4 engine
    regime : str — market regime
    
    Returns
    -------
    dict dengan score, signal, detail
    """
    if not enabled:
        return {"score": v4_score, "signal": "HOLD", "factors": {}}
    
    # Ambil faktor Invezgo
    bf = factor_broker_flow(code)
    ff = factor_foreign_flow(code)
    fq = factor_fundamental_quality(code)
    
    # Weighted score
    v7_score = (
        v4_score * _V7_WEIGHTS["v4_score"] +
        bf["score"] * _V7_WEIGHTS["broker_flow"] +
        ff["score"] * _V7_WEIGHTS["foreign_flow"] +
        fq["score"] * _V7_WEIGHTS["fundamental"]
    )
    v7_score = round(max(0, min(100, v7_score)), 1)
    
    # Signal dari threshold
    th = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])
    if v7_score >= th[0]: signal = "STRONG_BUY"
    elif v7_score >= th[1]: signal = "BUY"
    elif v7_score >= th[2]: signal = "WEAK_BUY"
    elif v7_score >= th[3]: signal = "HOLD"
    else: signal = "SELL"
    
    return {
        "score": v7_score,
        "signal": signal,
        "factors": {
            "v4_core": round(v4_score, 1),
            "broker_flow": bf["score"],
            "broker_detail": bf["detail"],
            "foreign_flow": ff["score"],
            "foreign_detail": ff["detail"],
            "fundamental": fq["score"],
            "fundamental_detail": fq["detail"],
        }
    }
