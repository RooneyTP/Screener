"""
auto_alert.py — Auto Alert Engine
Kirim notifikasi real-time berdasarkan sinyal dari bot.
"""

import sqlite3
import datetime
import os
import logging
import sys
import io

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

DB = "histori_ihsg.db"
COOLDOWN_MINUTES = 5

_sent_signals = {}

def _check_cooldown(ticker: str, signal_type: str) -> bool:
    """Check if we already sent this signal type for this ticker recently."""
    key = f"{ticker}_{signal_type}"
    last = _sent_signals.get(key)
    if last:
        delta = datetime.datetime.now() - last
        if delta.total_seconds() < COOLDOWN_MINUTES * 60:
            return True  # In cooldown
    return False

def _mark_sent(ticker: str, signal_type: str):
    key = f"{ticker}_{signal_type}"
    _sent_signals[key] = datetime.datetime.now()

def check_new_signals():
    """Check for new signals and send alerts."""
    try:
        conn = sqlite3.connect(DB)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, ticker, harga, sinyal, tp, sl, waktu 
            FROM sinyal_trading 
            ORDER BY id DESC LIMIT 20
        """)
        signals = cursor.fetchall()
        conn.close()
        
        for sig in signals:
            sig_id, ticker, price, signal_type, tp, sl, time_str = sig
            
            # Extract just the signal name (ULTRA_BUY, STRONG_BUY, etc)
            short_sig = signal_type.split("_")[-1] if "_" in str(signal_type) else str(signal_type)
            
            if _check_cooldown(ticker, short_sig):
                continue
            
            _mark_sent(ticker, short_sig)
            
            if "BUY" in str(signal_type).upper():
                log.info(f"📈 {ticker} @ {price} | TP: {tp} | SL: {sl}")
            elif "SELL" in str(signal_type).upper():
                log.info(f"📉 {ticker} @ {price}")
                
    except Exception as e:
        log.error(f"Alert check failed: {e}")

if __name__ == "__main__":
    check_new_signals()
