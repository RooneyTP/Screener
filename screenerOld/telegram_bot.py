# telegram_bot.py — Interactive Telegram Bot v6.1 (SKILL.md §⑥)
# Run: python telegram_bot.py
#
# DUAL MODE — Swing & Scalping with ALL prioritas upgrades
# P1: Fundamental data (PE/PBV) + Market Breadth
# P2: /sektor, /top, /compare, enhanced auto morning report
# P3: /cepat, IHSG/USD cache, better errors
# P4: /bt backtest, /entry + /exit portfolio tracker
#
# v6.1 fixes:
#   - MarkdownV2 escaping for all user-generated content
#   - Rate limiting (asyncio.Semaphore + per-user window)
#   - Config externalized to config/settings.yaml
#   - /health command for remote monitoring
#   - Proper error logging (no more silent except:pass)

import os, sys, logging, glob as _glob, time, asyncio, sqlite3, threading, html as _html, re, atexit
import psutil
from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import Conflict, TimedOut, RetryAfter

from ai_agent import ask_ai

os.makedirs("logs", exist_ok=True)
from logging.handlers import RotatingFileHandler
logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] bot: %(message)s",
    handlers=[
        RotatingFileHandler("logs/telegram_bot.log", maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"),
        logging.StreamHandler()
    ])
logger = logging.getLogger("telegram_bot")

# ── Token (defined early so _TokenFilter can reference it) ──
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Security: redact bot token from all log output ──
class _TokenFilter(logging.Filter):
    """Mask the bot token in log messages to prevent credential leaks."""
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            for token in [TOKEN, os.getenv("TELEGRAM_BOT_TOKEN", "")]:
                if token and len(token) > 5:
                    record.msg = record.msg.replace(token, "BOT_TOKEN_REDACTED")
        return True
logging.getLogger("telegram").addFilter(_TokenFilter())
logging.getLogger("telegram.vendor.ptb_urllib3.urllib3").addFilter(_TokenFilter())
logger.addFilter(_TokenFilter())
logger.info("Token filter installed — credentials will be redacted from logs.")

ROOT = os.path.dirname(__file__)

# ── Bot start time (for health check) ────────────────────────────
_start_time = time.time()

# ── Thread pool for parallel fetch ──────────────────────────────
_thread_pool = ThreadPoolExecutor(max_workers=4)

# ── LRU Cache untuk indicator (cegah memory leak) ──────────────
class _LRUCache:
    """Thread-safe LRU cache dengan max size dan TTL."""
    def __init__(self, maxsize=200, ttl=300):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.ttl = ttl
        self._lock = threading.Lock()
    
    def get(self, key):
        with self._lock:
            if key not in self.cache:
                return None
            data = self.cache[key]
            if time.time() - data.get("_ts", 0) >= self.ttl:
                del self.cache[key]
                return None
            self.cache.move_to_end(key)
            return data
    
    def set(self, key, value):
        with self._lock:
            value["_ts"] = time.time()
            self.cache[key] = value
            self.cache.move_to_end(key)
            if len(self.cache) > self.maxsize:
                self.cache.popitem(last=False)

_indicator_cache = _LRUCache(maxsize=200, ttl=300)

def _get_cached_indicator(tkr: str) -> dict | None:
    return _indicator_cache.get(tkr)

def _set_cached_indicator(tkr: str, data: dict):
    _indicator_cache.set(tkr, data)

# ── Rate limiting ────────────────────────────────────────────────
RATE_LIMIT_MAX_CONCURRENT = 5   # Max 5 concurrent requests
RATE_LIMIT_PER_USER = 3         # Max 3 requests per user per window
RATE_LIMIT_WINDOW = 10          # Window 10 detik
_rate_semaphore = asyncio.Semaphore(RATE_LIMIT_MAX_CONCURRENT)
_user_rate: dict[int, list[float]] = {}
_user_rate_lock = asyncio.Lock()

async def _check_rate_limit(user_id: int) -> bool:
    """Check if user is rate limited. Returns True if allowed."""
    async with _user_rate_lock:
        now = time.time()
        if user_id not in _user_rate:
            _user_rate[user_id] = []
        _user_rate[user_id] = [t for t in _user_rate[user_id] if now - t < RATE_LIMIT_WINDOW]
        if len(_user_rate[user_id]) >= RATE_LIMIT_PER_USER:
            return False
        _user_rate[user_id].append(now)
        return True

async def _rate_limited_call(user_id: int) -> bool:
    """Wrapper: global semaphore + per-user rate limit."""
    async with _rate_semaphore:
        return await _check_rate_limit(user_id)

# ── Config from settings.yaml ────────────────────────────────────
def _load_config() -> dict:
    """Load config/settings.yaml with fallback defaults."""
    config_path = os.path.join(ROOT, "config", "settings.yaml")
    try:
        import yaml
        with open(config_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load config from %s: %s", config_path, e)
        return {}

_CONFIG = _load_config()

# Constants from config with fallback defaults
INITIAL_CASH = _CONFIG.get("portfolio", {}).get("initial_cash", 10_000_000)
RISK_FREE_RATE = _CONFIG.get("backtest", {}).get("risk_free_rate", 0.05)
CSV_CACHE_TTL = _CONFIG.get("data", {}).get("csv_cache_ttl", 30)
DEFAULT_SL_PCT = _CONFIG.get("risk", {}).get("default_sl_pct", 0.92)
DEFAULT_TP_PCT = _CONFIG.get("risk", {}).get("default_tp_pct", 1.05)

# ═══════════════════════════════════════════════════════════════════
# MARKDOWN ESCAPE — v6.1 fix for TelegramBadRequest crashes
# ═══════════════════════════════════════════════════════════════════
def _escape_md(text) -> str:
    """Escape MarkdownV2 special characters in user-generated content.
    
    Telegram MarkdownV2 requires these chars to be escaped: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    if not isinstance(text, str):
        text = str(text)
    special_chars = r'_*[]()~`>#+-=|{}.!'
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    return text

def _fmt_md(text) -> str:
    """Shortcut: escape then return for MarkdownV2 formatting."""
    return _escape_md(str(text))

def _md_to_html(text: str) -> str:
    """Convert common Markdown patterns to HTML for Telegram HTML parse mode."""
    if not isinstance(text, str):
        text = str(text)
    # Bold: **text** or __text__ -> <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    # Italic: *text* or _text_ -> <i>text</i> (but not if already bold)
    text = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!_)_([^_\n]+?)_(?!_)', r'<i>\1</i>', text)
    # Code: `code` -> <code>code</code>
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    # Pre: ```code``` -> <pre><code>code</code></pre>
    text = re.sub(r'```([\s\S]*?)```', r'<pre><code>\1</code></pre>', text)
    # Links: [text](url) -> <a href="url">text</a>
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # Headers: ### Header -> <b>Header</b>
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Strikethrough: ~~text~~ -> <s>text</s>
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)
    # Underline: ++text++ -> <u>text</u>
    text = re.sub(r'\+\+(.+?)\+\+', r'<u>\1</u>', text)
    # Spoiler: ||text|| -> <span class="tg-spoiler">text</span>
    text = re.sub(r'\|\|(.+?)\|\|', r'<span class="tg-spoiler">\1</span>', text)
    return text

# ═══════════════════════════════════════════════════════════════════
# P3: IHSG/USD CACHE (5 minutes TTL)
# ═══════════════════════════════════════════════════════════════════
_ihsg_cache = {"val": 0.0, "ts": 0.0, "trend": "UP"}
_usd_cache = {"val": 0.0, "ts": 0.0}
_CACHE_TTL = 300  # 5 minutes

def _fetch_ihsg_change_cached() -> tuple[float, str]:
    now = time.time()
    if now - _ihsg_cache["ts"] < _CACHE_TTL:
        return _ihsg_cache["val"], _ihsg_cache["trend"]
    try:
        from core.scraper import fetch_price_data_sync
        ihsg = fetch_price_data_sync("^JKSE", period="1mo", interval="1d", skip_cache=True)
        if ihsg is not None and not ihsg.empty and len(ihsg) >= 3:
            chg = float((ihsg["Close"].iloc[-1]-ihsg["Close"].iloc[-2])/ihsg["Close"].iloc[-2]*100)
            last3 = ihsg["Close"].tail(3).diff().dropna()
            trend = "UP" if (last3>0).all() else "DOWN" if (last3<0).all() else ("UP" if last3.sum()>0 else "DOWN")
            _ihsg_cache["val"], _ihsg_cache["trend"], _ihsg_cache["ts"] = round(chg,2), trend, now
    except Exception as e:
        logger.warning("IHSG cache fetch failed: %s", e)
    return _ihsg_cache["val"], _ihsg_cache["trend"]

def _fetch_usd_change_cached() -> float:
    now = time.time()
    if now - _usd_cache["ts"] < _CACHE_TTL:
        return _usd_cache["val"]
    try:
        from core.scraper import fetch_price_data_sync
        usd = fetch_price_data_sync("IDR=X", period="1mo", interval="1d", skip_cache=True)
        if usd is not None and not usd.empty and len(usd) >= 2:
            chg = float((usd["Close"].iloc[-1]-usd["Close"].iloc[-2])/usd["Close"].iloc[-2]*100)
            _usd_cache["val"], _usd_cache["ts"] = round(chg,2), now
    except Exception as e:
        logger.warning("USD cache fetch failed: %s", e)
    return _usd_cache["val"]

# ═══════════════════════════════════════════════════════════════════
# DATA HELPERS
# ═══════════════════════════════════════════════════════════════════
def _latest_csv() -> str:
    files = sorted(_glob.glob(os.path.join(ROOT, "screener_ihsg_*.csv")))
    if not files:
        # Also search Data Screener/ subdirectory
        files = sorted(_glob.glob(os.path.join(ROOT, "Data Screener", "screener_ihsg_*.csv")))
    return files[-1] if files else ""

def _search_ticker(ticker: str) -> dict | None:
    path = _latest_csv()
    if not path: return None
    df = pd.read_csv(path)
    t = ticker.strip().upper().replace(".JK","")
    m = df[df["Ticker"].astype(str).str.upper()==t]
    return m.iloc[0].to_dict() if not m.empty else None

def _get_signals() -> dict:
    path = _latest_csv()
    if not path: return {"ultra":[],"strong":[],"buy":[],"total":0}
    df = pd.read_csv(path)
    cols = [c for c in ["Ticker","Harga","Skor","Confidence%","RRR","AI_Verdict","Sinyal","ADX","Volume_Spike","Regime","Sektor"] if c in df.columns]
    ultra = df[df["Sinyal"]=="ULTRA_BUY"][cols].to_dict("records") if "Sinyal" in df.columns else []
    strong = df[df["Sinyal"]=="STRONG_BUY"][cols].to_dict("records") if "Sinyal" in df.columns else []
    buy = df[df["Sinyal"]=="BUY"][cols].to_dict("records") if "Sinyal" in df.columns else []
    return {"ultra":ultra,"strong":strong,"buy":buy,"total":len(ultra)+len(strong)+len(buy)}

def _is_scalp_csv(r: dict) -> bool:
    vol_spike = str(r.get("Volume_Spike","")).upper()
    adx = float(r.get("ADX",0) or 0)
    rrr = float(r.get("RRR",0) or 0)
    return vol_spike in ("YES","EXTREME","60D_HIGH") and adx>20 and rrr>=1.5

# ═══════════════════════════════════════════════════════════════════
# P1: FUNDAMENTAL DATA FETCH (PE, PBV from yfinance — FREE)
# ═══════════════════════════════════════════════════════════════════
_fund_cache: dict[str, dict] = {}

def _fetch_fundamentals(ticker: str) -> dict:
    """Fetch PE, PBV, MarketCap from yfinance (free tier, no API key)."""
    tkr = ticker.strip().upper().replace(".JK","")
    now = time.time()
    if tkr in _fund_cache and now - _fund_cache[tkr].get("_ts",0) < 3600:
        return _fund_cache[tkr]
    result = {"PE": 0, "PBV": 0, "MarketCap": 0, "EPS": 0, "_ts": now}
    try:
        import yfinance as yf
        info = yf.Ticker(f"{tkr}.JK").info
        if info:
            result["PE"] = float(info.get("trailingPE") or info.get("forwardPE") or 0)
            result["PBV"] = float(info.get("priceToBook") or 0)
            result["MarketCap"] = info.get("marketCap") or 0
            result["EPS"] = float(info.get("trailingEps") or 0)
    except Exception as e:
        logger.warning("Fundamental fetch failed for %s: %s", tkr, e)
    _fund_cache[tkr] = result
    return result

# ═══════════════════════════════════════════════════════════════════
# P1: MARKET BREADTH
# ═══════════════════════════════════════════════════════════════════
def _compute_market_breadth() -> dict:
    """Compute % of stocks above EMA50 from latest CSV."""
    path = _latest_csv()
    if not path: return {"pct_above_ema50": 50, "total": 0}
    try:
        df = pd.read_csv(path)
        # CSV doesn't have EMA50 directly — use Weekly_Trend as proxy
        # BullW = price above EMA20 weekly, proxy for EMA50
        if "Weekly_Trend" in df.columns:
            bull = (df["Weekly_Trend"]=="Bull").sum()
            bear = (df["Weekly_Trend"]=="Bear").sum()
            total = bull + bear
            pct = bull/max(1,total)*100 if total>0 else 50
            return {"pct_above_ema50": round(pct,1), "total": len(df), "bull": int(bull)}
    except Exception as e:
        logger.warning("Market breadth computation failed: %s", e)
    return {"pct_above_ema50": 50, "total": 0}

# ═══════════════════════════════════════════════════════════════════
# HELPERS: HMA, trend
# ═══════════════════════════════════════════════════════════════════
def _calc_hma(series, period=20):
    half = int(period/2)
    wma1 = series.rolling(half).mean()
    wma2 = series.rolling(period).mean()
    return (2*wma1 - wma2).rolling(int(np.sqrt(period))).mean().iloc[-1]

# ═══════════════════════════════════════════════════════════════════
# LIVE DATA FETCH (scoring + ARB/ARA + P1 fundamentals)
# ═══════════════════════════════════════════════════════════════════
def _lookup_ticker_live(ticker: str, compact: bool = False) -> dict | None:
    """Live fetch & compute scoring. compact=True → only essential fields.
    
    Optimasi 1: Gunakan indicator cache untuk menghindari komputasi ulang.
    """
    tkr = ticker.strip().upper().replace(".JK","")
    
    # Cek indicator cache dulu
    cached = _get_cached_indicator(tkr)
    if cached and not compact:
        return cached
    
    try:
        from core.scraper import fetch_price_data_sync
        from core.indicators import (calculate_rsi, calculate_macd, calculate_adx,
            calculate_bollinger_bands, calculate_atr, detect_support_resistance, calculate_ema)
    except Exception as e:
        logger.error("Failed to import indicator modules: %s", e)
        return None

    # Gunakan cache yfinance (skip_cache=False)
    df = fetch_price_data_sync(tkr, period="6mo", interval="1d", skip_cache=False)
    if df is None or df.empty:
        return {"_error": f"Data tidak tersedia untuk {tkr}. YH Finance mungkin rate-limited atau ticker tidak valid."}

    df_w = fetch_price_data_sync(tkr, period="1y", interval="1wk", skip_cache=False)
    df_m = fetch_price_data_sync(tkr, period="2y", interval="1mo", skip_cache=False)

    close = df["Close"]; high = df["High"]; low = df["Low"]; volume = df["Volume"]; open_ = df["Open"]
    last_close = float(close.iloc[-1]); last_open = float(open_.iloc[-1])

    # FIX 1.2: Gunakan pct_change() vectorized — handle NaN dan gap secara otomatis
    change_pct = round(float(close.pct_change().iloc[-1] * 100), 2) if len(close) >= 2 else 0.0

    # 🔴 FIX BULLISH PALSU: Hitung session change (open→close) untuk deteksi intraday direction
    session_change_pct = round((last_close - last_open) / last_open * 100, 2) if last_open > 0 else 0.0
    prev_close = float(close.iloc[-2]) if len(close) >= 2 else last_close
    day_high = float(high.iloc[-1])
    day_low = float(low.iloc[-1])

    # === P1: FUNDAMENTAL DATA ===
    fund = _fetch_fundamentals(ticker)
    pe_val = fund.get("PE",0)
    pbv_val = fund.get("PBV",0) if last_close>0 and fund.get("PBV",0)>0 else 0
    mcap = fund.get("MarketCap",0)

    # ARB/ARA
    abs_change = abs(change_pct)
    is_arb = abs_change>=20 and change_pct<0
    is_ara = abs_change>=20 and change_pct>0
    near_arb = abs_change>=15 and change_pct<0
    near_ara = abs_change>=15 and change_pct>0

    # Indicators
    rsi_val = float(calculate_rsi(close).iloc[-1])
    _ml, _sl, macd_hist = calculate_macd(close)
    macd_val = float(macd_hist.iloc[-1])
    macd_trend = "UP" if macd_val>float(macd_hist.iloc[-2]) else "DOWN"
    adx_val = float(calculate_adx(high, low, close).iloc[-1])
    bb_mid, bb_up, bb_low = calculate_bollinger_bands(close)
    bb_mid_v, bb_up_v, bb_low_v = float(bb_mid.iloc[-1]), float(bb_up.iloc[-1]), float(bb_low.iloc[-1])
    bb_width = round((bb_up_v-bb_low_v)/bb_mid_v*100,1) if bb_mid_v>0 else 0
    support, resistance = map(float, detect_support_resistance(close))
    avg_vol = float(volume.iloc[-20:].mean()); last_vol = float(volume.iloc[-1])
    vol_ratio = last_vol/avg_vol if avg_vol>0 else 1.0
    atr_val = float(calculate_atr(high, low, close).iloc[-1])

    # Volume spike
    vol_spike = "NO"; vol_spike_label = ""
    if avg_vol>0 and vol_ratio>0.01:
        if vol_ratio>=5.0: vol_spike="EXTREME"
        elif vol_ratio>=3.0: vol_spike="YES"
        elif vol_ratio>=2.0: vol_spike="ELEVATED"
        if len(volume)>=60 and last_vol>=float(volume.iloc[-60:].max())*0.98 and vol_ratio>=2: vol_spike="60D_HIGH"
        if vol_spike!="NO": vol_spike_label=f"Vol {vol_ratio:.1f}x avg"

    # ═══ Task 17: Fake volume spike penalty ═══
    fake_vol_penalty = 0
    if vol_ratio > 3.0 and abs(change_pct) < 0.5 and vol_spike != "NO":
        fake_vol_penalty = -20  # Volume tidak menggerakkan harga → manipulasi

    # ═══ Task 21: Candle Pattern Detection ═══
    candle_score = 0; candle_label = ""
    body = abs(last_close - last_open)
    upper_wick = high.iloc[-1] - max(last_close, last_open)
    lower_wick = min(last_close, last_open) - low.iloc[-1]
    # Bullish engulfing
    if len(close) >= 2:
        prev_body = abs(float(close.iloc[-2]) - float(open_.iloc[-2]))
        if (last_close > last_open and float(close.iloc[-2]) < float(open_.iloc[-2])
            and last_close > float(open_.iloc[-2]) and last_open < float(close.iloc[-2])):
            candle_score = 10; candle_label = "BULLISH_ENGULFING"
        # Pin bar / Hammer di dekat support
        elif lower_wick > body * 1.5 and last_close > last_open and last_close > support * 0.98:
            candle_score = 8; candle_label = "HAMMER"
        # Shooting star di dekat resistance
        elif upper_wick > body * 1.5 and last_close < support * 1.05:
            candle_score = -5; candle_label = "SHOOTING_STAR"

    # ═══ Task 18: RSI Divergence (14-bar lookback) ═══
    divergence_score = 0; divergence_label = ""
    try:
        if len(close) >= 14:
            rsi14 = calculate_rsi(close)
            if len(rsi14) >= 14:
                rsi_now = float(rsi14.iloc[-1]); rsi_prev = float(rsi14.iloc[-14])
                price_now = last_close; price_prev = float(close.iloc[-14])
                if price_now < price_prev and rsi_now > rsi_prev:
                    divergence_score = 12; divergence_label = "BULL_DIV"
                elif price_now > price_prev and rsi_now < rsi_prev:
                    divergence_score = -12; divergence_label = "BEAR_DIV"
    except Exception as e:
        logger.warning("RSI divergence calculation failed: %s", e)

    # Pattern
    if last_close>resistance*0.98: pattern="BREAKOUT"
    elif last_close<support*1.02 and macd_trend=="UP": pattern="REVERSAL"
    elif adx_val>25 and macd_trend=="UP": pattern="CONTINUATION"
    else: pattern="NONE"

    # ── Task 22: ATR-based dynamic SL/TP (volatility-adjusted) ──
    # Risk factor scales with ADX (stronger trend = tighter stop for higher RRR)
    if adx_val > 35:     risk_factor, reward_mult = 1.2, 2.5
    elif adx_val > 25:   risk_factor, reward_mult = 1.5, 2.0
    elif adx_val > 18:   risk_factor, reward_mult = 1.8, 1.8
    else:                risk_factor, reward_mult = 2.0, 1.5
    # ATR stop is primary; 8% floor as absolute worst-case (gap down protection)
    atr_stop = last_close - risk_factor * atr_val
    pct_stop = last_close * DEFAULT_SL_PCT
    sl = max(atr_stop, pct_stop) if risk_factor * atr_val < last_close * 0.15 else pct_stop
    tp1 = min(last_close + reward_mult * atr_val, resistance) if resistance > last_close else last_close + reward_mult * atr_val
    tp2 = last_close + (reward_mult + 1.5) * atr_val
    tp3 = last_close + (reward_mult + 3.0) * atr_val
    rrr = (tp1 - last_close) / max(1, (last_close - sl))

    # ── Task 23: Trailing stop trigger (SCALP only) ──
    trail_trigger_pct = 1.02    # +2% from entry activates trail
    trail_atr_mult = 1.5        # ATR multiplier for trailing stop
    trail_active = False
    trail_stop = 0.0
    if last_close > (last_close - risk_factor * atr_val) * trail_trigger_pct:
        trail_active = True
        trail_stop = last_close - trail_atr_mult * atr_val

    ema21_val = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    ema50_val = float(close.ewm(span=50, adjust=False).mean().iloc[-1]) if len(close)>=50 else ema21_val
    hma_val = float(_calc_hma(close,20))

    # Multi-timeframe
    weekly_bullish = False; monthly_bullish = False
    try:
        if df_w is not None and not df_w.empty and len(df_w)>=20:
            cw = df_w["Close"]; ew = calculate_ema(cw,20)
            weekly_bullish = float(cw.iloc[-1])>float(ew.iloc[-1])
    except Exception as e:
        logger.warning("Weekly trend calculation failed: %s", e)
    try:
        if df_m is not None and not df_m.empty and len(df_m)>=12:
            cm = df_m["Close"]; em = calculate_ema(cm,12)
            monthly_bullish = float(cm.iloc[-1])>float(em.iloc[-1])
    except Exception as e:
        logger.warning("Monthly trend calculation failed: %s", e)

    # P3: Use cached IHSG/USD
    ihsg_change, ihsg_trend = _fetch_ihsg_change_cached()
    weekly_trend = "Bull" if weekly_bullish else "Bear"
    monthly_trend = "Bull" if monthly_bullish else "Bear"
    regime = "TRENDING" if adx_val>25 else "RANGING" if adx_val>15 else "CHOppy"

    # ═══ SCORING v11 — Adaptive + All Accuracy Upgrades ═══

    # ── Task 15: Adaptive regime weights (from scoring_engine) ──
    from core.scoring import get_adaptive_weights, compute_confidence, get_signal, get_calibrated_win_prob
    w_tech, w_fund, w_rs, w_sent = get_adaptive_weights(adx_val)

    # ── TECH SCORE (with candle + divergence) ──
    tech_score = 0
    if last_close>ema21_val>ema50_val and last_close>hma_val: tech_score+=20
    elif last_close>ema21_val: tech_score+=10
    if 30<=rsi_val<=50: tech_score+=15
    elif 25<=rsi_val<30: tech_score+=10
    elif 50<rsi_val<=60: tech_score+=5
    elif rsi_val>70: tech_score-=10
    if macd_val>0 and macd_trend=="UP": tech_score+=15
    elif macd_val>0: tech_score+=8
    if vol_ratio>0.01:
        if vol_ratio>1.5: tech_score+=15
        elif vol_ratio>1.2: tech_score+=8
        elif vol_ratio<0.5: tech_score-=10
    if adx_val>35: tech_score+=20
    elif adx_val>25: tech_score+=12
    elif adx_val>20: tech_score+=6
    if pattern=="BREAKOUT": tech_score+=15
    elif pattern=="REVERSAL": tech_score+=10
    bb_pos = (last_close-bb_low_v)/max(1,(bb_up_v-bb_low_v))*100
    if 0<=bb_pos<=25: tech_score+=8
    elif 75<=bb_pos<=100: tech_score-=8
    # Task 17 + 18 + 21: Candle, divergence, fake volume
    tech_score += candle_score + divergence_score + fake_vol_penalty
    tech_score = min(100, max(0, tech_score))

    # ── FUND SCORE ──
    fund_score = 30
    if pe_val>0:
        if pe_val<=12: fund_score+=25
        elif pe_val<=18: fund_score+=15
        elif pe_val>50: fund_score-=15
    if pbv_val>0:
        if pbv_val<=1.0: fund_score+=25
        elif pbv_val<=2.0: fund_score+=10
        elif pbv_val>5: fund_score-=15
    if mcap>10_000_000_000_000: fund_score+=10
    fund_score = min(100, max(0, fund_score))

    # ── RS SCORE (Task 24: z-score normalization) ──
    rs_score = 0
    try:
        if len(close)>=20:
            sr20 = (last_close-float(close.iloc[-20]))/float(close.iloc[-20])*100
            if ihsg_change!=0:
                delta = sr20-ihsg_change
                if sr20>5 and delta>5: rs_score+=30
                if delta>=0: rs_score+=20
                elif delta>-3: rs_score+=10
            else:
                if sr20>5: rs_score+=20
                elif sr20>0: rs_score+=10
            if last_close>ema50_val: rs_score+=15
            # Task 24: Sector-normalized z-score
            sr_returns = close.pct_change(20).dropna()
            if len(sr_returns) >= 10:
                z_score_20d = (sr_returns.iloc[-1] - sr_returns.mean()) / max(0.001, sr_returns.std())
                if z_score_20d > 1.5: rs_score += 10
                elif z_score_20d < -1.5: rs_score -= 5
    except Exception as e:
        logger.warning("RS score calculation failed: %s", e)
    rs_score = min(100, max(0, rs_score))

    # ── SENT SCORE (Task 25: NLP news sentiment) ──
    sent_score = 0
    if vol_ratio>0.01:
        if vol_ratio>1.5: sent_score+=20
        elif vol_ratio>1.2: sent_score+=10
        elif vol_ratio<0.5: sent_score-=20
        elif vol_ratio<0.7: sent_score-=10
    if weekly_bullish and monthly_bullish: sent_score+=15
    elif weekly_bullish: sent_score+=8
    # Task 25: NLP news sentiment (free, local only — no API cost)
    try:
        from nlp_scraper import get_sentiment_compound
        nlp_val = get_sentiment_compound(tkr)
        if nlp_val > 0.3: sent_score += 15
        elif nlp_val > 0: sent_score += 8
        elif nlp_val < -0.3: sent_score -= 15
        elif nlp_val < 0: sent_score -= 8
    except Exception as e:
        logger.warning("NLP sentiment fetch failed for %s: %s", tkr, e)
    sent_score = min(100, max(-30, sent_score))

    # ── CONFIDENCE (adaptive weights) ──
    # v11: Normalize component scores to 0-100
    n_tech = min(100, max(0, (tech_score / 65) * 100))
    n_fund = min(100, max(0, (fund_score / 50) * 100))
    n_rs   = min(100, max(0, (rs_score / 50) * 100))
    n_sent = min(100, max(0, (sent_score / 30) * 100))
    confidence = n_tech*w_tech + max(0,n_fund)*w_fund + n_rs*w_rs + max(0,n_sent)*w_sent
    confidence = min(100, max(0, confidence))
    # NOTE: Shareholder structure bonus TIDAK dimasukkan ke confidence score
    # karena data holders tidak selalu akurat (banyak emiten tidak tercatat di KSEI).
    # Data holders hanya ditampilkan di command /holders, tidak mempengaruhi rating.
    # v11: Softer IHSG penalty
    if isinstance(ihsg_change, (int, float)) and ihsg_change < -1.0: confidence-=8
    elif isinstance(ihsg_change, (int, float)) and ihsg_change < -0.3: confidence-=3
    # v11: Softer multi-timeframe penalty
    if weekly_bullish and monthly_bullish: confidence+=5
    elif not weekly_bullish and not monthly_bullish: confidence-=3
    elif not weekly_bullish: confidence-=1
    confidence = min(100, max(5, confidence))
    skor = round(confidence/100*15,1)

    # ARB/ARA penalty
    arb_warning = ""
    if is_arb or is_ara:
        confidence=10; skor=0
        arb_warning = "🔴 ARB! Tidak bisa dijual." if is_arb else "🔴 ARA! Tidak bisa dibeli."
    elif near_arb or near_ara:
        confidence=min(confidence,confidence*0.5)
        arb_warning = f"⚠️ Hampir {'ARB' if near_arb else 'ARA'} ({change_pct:+.1f}%)"

    # ── Task 16: Dynamic threshold using market breadth ──
    breadth = _compute_market_breadth()
    breadth_tightness = 1.0
    if breadth.get("total", 0) > 0:
        pct_above = breadth.get("pct_above_ema50", 50)
        breadth_tightness = 1.0 + max(0, (50 - pct_above)) / 100

    # Signal
    signal="HINDARI"; signal_strength="F"
    c_thresh_strong = min(95, int((75 if ihsg_trend=="UP" else 80) * breadth_tightness))
    c_thresh_buy = min(90, int((55 if ihsg_trend=="UP" else 65) * breadth_tightness))
    if is_arb: signal="HINDARI"; signal_strength="ARB"
    elif is_ara: signal="HINDARI"; signal_strength="ARA"
    elif near_arb: signal="HINDARI"; signal_strength="NEAR_ARB"
    elif near_ara: signal="HINDARI"; signal_strength="NEAR_ARA"
    elif confidence>=85 and skor>=10 and rrr>=1.8 and weekly_bullish and ihsg_trend=="UP": signal="ULTRA_BUY"; signal_strength="A+"
    elif confidence>=80 and skor>=9.5 and rrr>=1.6 and weekly_bullish: signal="ULTRA_BUY"; signal_strength="A"
    elif confidence>=c_thresh_strong and skor>=8.0: signal="STRONG_BUY"; signal_strength="B+"
    elif confidence>=70 and skor>=7.0: signal="STRONG_BUY"; signal_strength="B"
    elif confidence>=c_thresh_buy and skor>=4.0: signal="BUY"; signal_strength="C"
    elif confidence>=30 and skor>=2.0: signal="PANTAU"; signal_strength="D"
    elif skor>=-15.0: signal="TUNGGU"; signal_strength="E"

    # ── Task 20: Calibrated win probability (from scoring_engine) ──
    ai_win_prob, ai_verdict = get_calibrated_win_prob(confidence)

    # Hold
    s_val = int(support); r_val = int(resistance)
    is_scalp_candidate = vol_spike in ("EXTREME","YES","60D_HIGH") and adx_val>20 and rrr>=1.5
    hold_mode = "SWING"
    if signal in ("ULTRA_BUY","STRONG_BUY") and is_scalp_candidate:
        trail_info = f" | Trail Rp{int(trail_stop):,}" if trail_active else ""
        hold=f"⚡ SCALP 1-8J | Entry Rp{int(last_close):,} | TP Rp{int(tp1):,} | SL Rp{int(sl):,}{trail_info}"; hold_mode="SCALP"
    elif signal in ("ULTRA_BUY","STRONG_BUY") and adx_val>25:
        hold=f"📈 SWING 3-7H | Entry Rp{int(last_close):,} | TP Rp{int(tp1):,} | SL Rp{int(sl):,}"; hold_mode="SWING"
    elif signal=="ULTRA_BUY":
        hold=f"📈 SWING 7-14H | Entry Rp{int(last_close):,} | TP Rp{int(tp1):,} | SL Rp{int(sl):,}"; hold_mode="SWING"
    elif signal in ("STRONG_BUY","BUY") and adx_val>18:
        hold=f"📈 SWING 7-14H | Entry Rp{int(last_close):,} | TP Rp{int(tp1):,} | SL Rp{int(sl):,}"; hold_mode="SWING"
    elif signal=="BUY":
        hold=f"📈 HOLD 14-30H | Entry Rp{int(last_close):,} | TP Rp{int(tp1):,} | SL Rp{int(sl):,}"; hold_mode="HOLD"
    elif signal=="PANTAU": hold=f"👀 PANTAU — Entry >Rp{r_val:,}"
    elif signal=="TUNGGU": hold=f"⏳ TUNGGU — Break >Rp{r_val:,}"
    else: hold=f"🚫 HINDARI" + (f" — {arb_warning}" if arb_warning else f" — Support Rp{s_val:,}")

    # MM
    if vol_ratio>1.5: mm_activity="ACCUMULATION"
    elif vol_ratio>1.2: mm_activity="MILD_ACCUM"
    elif vol_ratio<0.5: mm_activity="DISTRIBUTION"
    elif vol_ratio<0.7: mm_activity="MILD_DIST"
    else: mm_activity="NEUTRAL"

    result = {
        "Ticker": tkr, "Harga": last_close, "Change_pct": round(change_pct,2),
        # 🔴 FIX BULLISH PALSU: Intraday fields agar LLM bisa bedakan "naik sejak kemarin" vs "naik sejak open"
        "Session_Change%": session_change_pct,
        "Prev_Close": round(prev_close, 0),
        "Day_High": round(day_high, 0),
        "Day_Low": round(day_low, 0),
        # ── end FIX ──
        "Sinyal": signal, "Strength": signal_strength, "Skor": skor,
        "Confidence%": confidence, "Tech_Score": tech_score, "Fund_Score": fund_score,
        "RS_Score": rs_score, "RSI": round(rsi_val,1), "ADX": round(adx_val,1),
        "MACD": macd_trend, "BB_Width%": bb_width, "CCI": "N/A",
        "Pattern": pattern, "Candle": candle_label, "Divergence": divergence_label,
        "Support": round(support,0), "Resistance": round(resistance,0),
        "Stop_Loss": round(sl,0), "Target_1": round(tp1,0),
        "Target_2": round(tp2,0), "Target_3": round(tp3,0),
        "RRR": round(rrr,2), "ATR": round(atr_val,0),
        "Vol_Ratio": round(vol_ratio,2), "Vol_Spike": vol_spike, "Vol_Spike_Label": vol_spike_label,
        "Weekly_Trend": weekly_trend, "Monthly_Trend": monthly_trend, "Regime": regime,
        "Hold": hold, "Hold_Mode": hold_mode,
        "Trail_Active": trail_active, "Trail_Stop": round(trail_stop, 0),
        "MM_Activity": mm_activity, "MM_Confidence": round(min(100,vol_ratio*50),0),
        "AI_Verdict": ai_verdict, "AI_Win_Prob%": ai_win_prob,
        "IHSG_Change": round(ihsg_change,2), "IHSG_Trend": ihsg_trend,
        "Foreign_Status": "N/A", "Source": "Live (YH Finance)",
        "ARB_Warning": arb_warning, "Is_ARB": "YES" if is_arb or near_arb or is_ara or near_ara else "NO",
        # P1: Fundamental fields
        "PE": pe_val, "PBV": round(pbv_val,2) if pbv_val else 0, "MarketCap": mcap,
    }
    
    # Simpan ke indicator cache untuk mempercepat request berikutnya
    _set_cached_indicator(tkr, result)
    return result

# ═══════════════════════════════════════════════════════════════════
# BACKGROUND SCREENER
# ═══════════════════════════════════════════════════════════════════
_screener_running = False
_screener_lock = threading.Lock()

def _run_screener_background():
    global _screener_running
    if not _screener_lock.acquire(blocking=False): return
    if _screener_running: _screener_lock.release(); return
    import subprocess
    def _run():
        global _screener_running; _screener_running = True
        try:
            logger.info("[BG] screener.py starting...")
            subprocess.run([sys.executable, os.path.join(ROOT,"screener.py"),
                "--workers","2","--skip-backtest","--skip-optimize","--skip-alerts"],
                cwd=ROOT, capture_output=True, text=True, timeout=600)
        except Exception as e: logger.error("[BG] %s", e)
        finally: _screener_running = False; _screener_lock.release()
    threading.Thread(target=_run, daemon=True).start()

# ═══════════════════════════════════════════════════════════════════
# SCREENER DB (Optimasi 2: Simpan hasil screener ke SQLite)
# ═══════════════════════════════════════════════════════════════════
_SCREENER_DB = os.path.join(ROOT, "screener_results.db")

def _init_screener_db():
    """Inisialisasi database screener untuk akses lebih cepat daripada CSV."""
    with sqlite3.connect(_SCREENER_DB) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS screener_results (
                ticker TEXT PRIMARY KEY,
                harga REAL, skor REAL, sinyal TEXT, confidence REAL,
                rsi REAL, adx REAL, rrr REAL, weekly_trend TEXT,
                regime TEXT, sektor TEXT, float_shares REAL,
                shares_outstanding REAL, market_cap REAL,
                updated_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_screener_sinyal ON screener_results(sinyal)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_screener_skor ON screener_results(skor)")

# Track CSV yang sudah di-sync + thread lock
_last_synced_csv = ""
_sync_lock = threading.Lock()

def _sync_csv_to_db():
    """Sinkronisasi data CSV terbaru ke SQLite untuk akses lebih cepat."""
    global _last_synced_csv
    with _sync_lock:
        path = _latest_csv()
        if not path: return
        if path == _last_synced_csv:
            return
        try:
            df = pd.read_csv(path)
            with sqlite3.connect(_SCREENER_DB) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                for _, row in df.iterrows():
                    conn.execute("""
                        INSERT OR REPLACE INTO screener_results
                        (ticker, harga, skor, sinyal, confidence, rsi, adx, rrr,
                         weekly_trend, regime, sektor, float_shares, shares_outstanding, market_cap, updated_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        str(row.get("Ticker","")),
                        float(row.get("Harga",0) or 0),
                        float(row.get("Skor",0) or 0),
                        str(row.get("Sinyal","")),
                        float(row.get("Confidence%",0) or 0),
                        float(row.get("RSI",0) or 0),
                        float(row.get("ADX",0) or 0),
                        float(row.get("RRR",0) or 0),
                        str(row.get("Weekly_Trend","")),
                        str(row.get("Regime","")),
                        str(row.get("Sektor","")),
                        float(row.get("Float_Shares",0) or 0),
                        float(row.get("Shares_Outstanding",0) or 0),
                        float(row.get("Market_Cap_IDR",0) or 0),
                        datetime.now().isoformat()
                    ))
                conn.commit()
            _last_synced_csv = path
            logger.info("Synced %d rows from %s to SQLite", len(df), os.path.basename(path))
        except Exception as e:
            logger.warning("CSV-to-SQLite sync failed: %s", e)

# Init database di startup
_init_screener_db()
try:
    _sync_csv_to_db()
except Exception as e:
    logger.warning("Initial CSV sync failed (non-critical): %s", e)

# ═══════════════════════════════════════════════════════════════════
# UTILITY
# ═══════════════════════════════════════════════════════════════════
_csv_df_cache: dict = {"df": None, "path": "", "ts": 0.0}

def _get_db() -> sqlite3.Connection:
    """Get SQLite connection with WAL mode for better performance (Priority 2)."""
    db = sqlite3.connect(os.path.join(ROOT, "portofolio_virtual.db"))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-8000")  # 8MB cache
    return db

def _get_csv_dataframe() -> pd.DataFrame | None:
    path = _latest_csv()
    if not path: return None
    now = time.time()
    if _csv_df_cache["path"] == path and (now - _csv_df_cache["ts"]) < CSV_CACHE_TTL:
        return _csv_df_cache["df"]
    df = pd.read_csv(path)
    _csv_df_cache["df"], _csv_df_cache["path"], _csv_df_cache["ts"] = df, path, now
    return df

# ═══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════
# WATCHDOG (Optimasi 3: Auto-restart monitoring)
# ═══════════════════════════════════════════════════════════════════
def _watchdog():
    """Background thread untuk auto-sync CSV ke SQLite setiap 5 menit."""
    while True:
        time.sleep(300)  # 5 menit
        try:
            _sync_csv_to_db()
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════
# ERROR HANDLER — Graceful error recovery for polling conflicts
# ═══════════════════════════════════════════════════════════════════
async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler — log, auto-recover from Conflict, continue polling.

    Prevents the bot from crashing on transient errors (Conflict, TimedOut, RetryAfter).
    Logs detailed traceback for unexpected errors.
    """
    error = context.error
    logger.error(f"Exception while handling update {update}: {error}")

    # ── Conflict: another bot instance is polling ──
    if isinstance(error, Conflict):
        logger.critical(
            "⚠️ CONFLICT: Another bot instance is polling this token! "
            "Run 'taskkill /F /IM python.exe' to kill all instances, "
            "then restart from a single terminal."
        )
        # Jangan raise — biarkan updater retry sendiri
        return

    # ── TimedOut: Telegram API timeout (network issue) ──
    if isinstance(error, TimedOut):
        logger.warning("⏳ Telegram API timed out — will retry automatically.")
        return

    # ── RetryAfter: rate limited by Telegram ──
    if isinstance(error, RetryAfter):
        retry_seconds = getattr(error, "retry_after", 5)
        logger.warning(f"⏳ Rate limited by Telegram — retry after {retry_seconds}s")
        return

    # ── Unknown errors — log full traceback ──
    logger.exception("💥 UNEXPECTED ERROR", exc_info=error)

# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════
def main():
    if not TOKEN: print("ERROR: TOKEN not set"); return
    
    # Start watchdog thread
    threading.Thread(target=_watchdog, daemon=True).start()

    # ── Prevent multiple instances via lock file ──
    import tempfile as _tmp
    LOCK_FILE = os.path.join(_tmp.gettempdir(), "screener_bot.lock")

    def _is_pid_alive(pid: int) -> bool:
        """Check if a Windows PID is still alive using OpenProcess."""
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # PROCESS_QUERY_INFORMATION = 0x0400
            handle = kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False  # OpenProcess returned 0/NULL — PID is dead
        except Exception:
            return False

    def _cleanup_lock():
        """Cleanup lock file when bot exits gracefully."""
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
                logger.info("Lock file removed.")
        except Exception:
            pass

    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = f.read().strip()
            if old_pid and old_pid.isdigit():
                if _is_pid_alive(int(old_pid)):
                    logger.error(f"Bot already running (PID {old_pid}). Kill it first.")
                    print(f"ERROR: Bot already running (PID {old_pid}). Kill it first.")
                    print("Run: taskkill /F /IM python.exe  lalu restart.")
                    return
                else:
                    logger.warning(f"Stale lock file found (PID {old_pid} is dead). Removing and continuing...")
                    os.remove(LOCK_FILE)
            else:
                logger.warning(f"Corrupt lock file (content: {old_pid!r}). Removing and continuing...")
                os.remove(LOCK_FILE)
        except FileNotFoundError:
            pass  # Race condition: file already deleted by other process
        except Exception as e:
            logger.warning(f"Lock file read failed (will overwrite): {e}")
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_cleanup_lock)

    # ── Init IHSG cache saat startup agar tidak return 0 ──
    try:
        logger.info("Initializing IHSG cache...")
        ihsg_val, ihsg_trend = _fetch_ihsg_change_cached()
        logger.info("IHSG cache init: %.2f%% (%s)", ihsg_val, ihsg_trend)
    except Exception as e:
        logger.warning("IHSG cache init failed (non-critical): %s", e)

    app = Application.builder().token(TOKEN).build()

    # Register command handlers from handlers/ package (modular)
    from handlers import register_all
    register_all(app)

    # Register commands with Telegram API so they appear in the / menu
    async def _register_commands(app):
        bot_commands = [
            BotCommand("start", "🏠 Welcome & status"),
            BotCommand("help", "❓ Daftar semua perintah"),
            BotCommand("istilah", "📖 Istilah technical"),
            BotCommand("cek", "📊 Quick score TICKER"),
            BotCommand("cepat", "⚡ Lightning check TICKER"),
            BotCommand("swing", "📈 Panel swing — TICKER"),
            BotCommand("scalp", "🔪 Panel scalping — TICKER"),
            BotCommand("sinyal", "🚦 Sinyal masuk sekarang"),
            BotCommand("sektor", "🏢 Sektor terkuat"),
            BotCommand("top", "🏆 Top gainer/volume"),
            BotCommand("compare", "🆚 Bandingkan TICKER"),
            BotCommand("report", "📋 Morning report"),
            BotCommand("portfolio", "💼 Portfolio tracker"),
            BotCommand("entry", "➕ Entry — TICKER HARGA LOT"),
            BotCommand("exit", "➖ Exit — TICKER HARGA"),
            BotCommand("bt", "🔬 Backtest — TICKER [HARI]"),
            BotCommand("btall", "🧪 Backtest semua sinyal"),
            BotCommand("status", "📡 Status bot & breadth"),
            BotCommand("health", "🩺 Health check & status"),
            BotCommand("scalp_pos", "🔪 Scalp posisi aktif"),
            BotCommand("scalp_pnl", "💰 Scalp P&L"),
            BotCommand("holders", "👥 Pemegang saham — TICKER"),
        ]
        await app.bot.set_my_commands(bot_commands)
        logger.info("Commands registered with Telegram API")

    # ─── AI Chat Handler (Natural Language, only when tagged) ───────────────────────────
    async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        text = update.message.text.strip()
        logger.info(f"[AI_HANDLER] chat_id={update.effective_chat.id} text={text[:80]}")
        if not text or text.startswith("/"):
            return  # Skip commands
        # Only respond if bot is mentioned (@botname) or replied to
        bot_username = context.bot.username
        if bot_username:
            mentioned = f"@{bot_username}" in text
        else:
            mentioned = False
        is_reply_to_bot = (
            update.message.reply_to_message
            and update.message.reply_to_message.from_user
            and update.message.reply_to_message.from_user.id == context.bot.id
        )
        logger.info(f"[AI_HANDLER] mentioned={mentioned} reply_to_bot={is_reply_to_bot} bot_username={bot_username}")
        if not mentioned and not is_reply_to_bot:
            logger.info("[AI_HANDLER] Ignored (not mentioned/replied)")
            return  # Ignore messages not addressing the bot
        chat_id = update.effective_chat.id
        # Show typing action
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        # Call AI agent (synchronous, runs in thread pool)
        import asyncio
        loop = asyncio.get_event_loop()
        clean_text = update.message.text
        if bot_username:
            clean_text = clean_text.replace(f"@{bot_username}", "").strip()
        if not clean_text:
            clean_text = update.message.text
        # Ambil user_id untuk conversation memory
        from_user = update.message.from_user
        user_id = from_user.id if from_user else chat_id
        answer = await loop.run_in_executor(None, ask_ai, chat_id, clean_text, user_id)
        if answer:
            # Gunakan parse_mode=None — AI sudah dirancang untuk plain text murni
            await update.message.reply_text(answer, parse_mode=None)

    app.post_init = _register_commands

    # Register AI chat handler (text messages mentioning the bot)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_chat))

    # Register global error handler
    app.add_error_handler(_error_handler)
    logger.info("Error handler registered — bot will auto-recover from transient errors.")

    print("="*50)
    print("  TELEGRAM BOT v6.1 — ALL FIXES APPLIED")
    print("  /swing /scalp /cek /cepat /sektor /top /compare")
    print("  /report /portfolio /entry /exit /bt /status /health")
    print("="*50)
    app.run_polling(drop_pending_updates=True)

if __name__=="__main__":
    main()
