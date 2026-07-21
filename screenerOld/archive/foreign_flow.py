"""
foreign_flow.py — Foreign Flow Scraper (GRATIS)
Scrape net foreign buy/sell dari RTI Business.
Fallback ke estimasi kalau website berubah struktur.
"""

import requests
import re
import json
import datetime
import logging

logger = logging.getLogger(__name__)

# Cache 15 menit supaya tidak spam request
_cache = {}
_cache_time = {}


_fail_count = 0
_FAIL_THRESHOLD = 5

def _should_skip():
    global _fail_count
    return _fail_count >= _FAIL_THRESHOLD

def _mark_fail():
    global _fail_count
    _fail_count += 1
    if _fail_count >= _FAIL_THRESHOLD:
        logger.warning("[FOREIGN] Too many failures -- skipping foreign flow")

def _cache_get(key, ttl_seconds=900):
    now = datetime.datetime.now()
    if key in _cache and (now - _cache_time.get(key, now)).seconds < ttl_seconds:
        return _cache[key]
    return None

def _cache_set(key, value):
    _cache[key] = value
    _cache_time[key] = datetime.datetime.now()

def fetch_foreign_flow(ticker: str) -> dict:
    """
    Ambil data foreign flow untuk satu ticker.
    
    Returns dict:
        net_foreign_5d: float — net foreign buy/sell dalam Rupiah (estimasi)
        net_foreign_pct: float — persentase dari total volume
        foreign_status: str — ACCUMULATION / DISTRIBUTION / NEUTRAL
        source: str — "rti" / "yahoo" / "estimate"
    """
    ticker_clean = ticker.replace(".JK", "").upper()
    result = {
        "net_foreign_5d": 0.0,
        "net_foreign_pct": 0.0,
        "foreign_status": "NEUTRAL",
        "source": "estimate"
    }
    
    # ── Method 1: RTI Business (primary) ──
    cache_key = f"rti_{ticker_clean}"
    cached = _cache_get(cache_key)
    if cached:
        return cached
    
    try:
        # RTI foreign flow endpoint (free, no API key)
        url = f"https://www.rti.co.id/foreign-flow"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/html"
        }
        resp = requests.get(url, headers=headers, timeout=3)
        if resp.status_code == 200:
            text = resp.text
            # Try JSON first (some RTI pages have embedded JSON)
            try:
                # Look for JSON data block
                json_match = re.search(r'data\s*:\s*(\[.*?\])', text, re.DOTALL)
                if json_match:
                    data = json.loads(json_match.group(1))
                    for item in data:
                        if isinstance(item, dict) and item.get("code", "").upper() == ticker_clean:
                            result["net_foreign_5d"] = float(item.get("net_buy", 0) or 0) * 1000
                            result["net_foreign_pct"] = float(item.get("pct", 0) or 0)
                            result["source"] = "rti"
                            break
            except:
                pass
            
            # Fallback: scan HTML table
            if result["source"] == "estimate":
                pattern = rf'{ticker_clean}.*?([\d,.-]+).*?([\d,.-]+)'
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    result["net_foreign_5d"] = float(match.group(1).replace(",", "")) * 1_000_000
                    result["source"] = "rti_html"
    except Exception as e:
        logger.debug(f"[FOREIGN] RTI gagal untuk {ticker}: {e}")
    
    # ── Method 2: Yahoo Finance (estimasi dari data institusi) ──
    if result["source"] == "estimate":
        try:
            import yfinance as yf
            tkr = ticker if "." in ticker else f"{ticker}.JK"
            info = yf.Ticker(tkr).info or {}
            inst_held = info.get("heldPercentInstitutions", 0) or 0
            float_shares = info.get("floatShares", 0) or 0
            
            if inst_held > 0 and float_shares > 0:
                # Perubahan institusi per kuartal ≈ foreign flow proxy
                result["net_foreign_pct"] = round(inst_held * 0.01, 2)
                result["source"] = "yahoo"
        except:
            pass
    
    # ── Tentukan status ──
    net = result["net_foreign_5d"]
    pct = result["net_foreign_pct"]
    
    if net > 50_000_000 or pct > 5:
        result["foreign_status"] = "ACCUMULATION"
    elif net < -50_000_000 or pct < -5:
        result["foreign_status"] = "DISTRIBUTION"
    else:
        result["foreign_status"] = "NEUTRAL"
    
    _cache_set(cache_key, result)
    return result


def fetch_ihsg_foreign_flow() -> dict:
    """
    Ambil total foreign flow IHSG hari ini (market-wide).
    Returns dict: net_buy, net_sell, net_total (dalam miliar Rupiah)
    """
    result = {"net_buy": 0.0, "net_sell": 0.0, "net_total": 0.0, "source": "none"}
    
    try:
        url = "https://www.rti.co.id/foreign-flow"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=3)
        if resp.status_code == 200:
            text = resp.text
            # Cari total foreign buy/sell
            buy_match = re.search(r'Foreign\s*Buy[:\\s]*([\d,.-]+)\s*[TMB]', text, re.IGNORECASE)
            sell_match = re.search(r'Foreign\s*Sell[:\\s]*([\d,.-]+)\s*[TMB]', text, re.IGNORECASE)
            if buy_match and sell_match:
                result["net_buy"] = float(buy_match.group(1).replace(",", ""))
                result["net_sell"] = float(sell_match.group(1).replace(",", ""))
                # Konversi ke miliar
                if "T" in text[buy_match.start():buy_match.end()+10]:
                    result["net_buy"] *= 1000
                result["net_total"] = result["net_buy"] - result["net_sell"]
                result["source"] = "rti"
    except:
        pass
    
    return result
