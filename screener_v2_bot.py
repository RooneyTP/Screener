#!/usr/bin/env python3
"""
screener_v2_bot.py — Telegram Bot Khusus IDX Alpha Screener v2
=============================================================
Menggunakan sistem scoring v2 (threshold >=55 + mandatory swing filters)
sebagai basis analisis. Backend AI: OpenCodeZen.

Run: python screener_v2_bot.py

Commands:
  /start            👋 Welcome
  /help             ❓ Daftar perintah
  /cek TICKER       📊 Analisis satu saham (v2 scoring + swing gate)
  /scan [TICKER..]  🔍 Scan semua saham atau ticker tertentu
  /gate TICKER      🚦 Status swing gate (weekly trend + volume)
  /sinyal           🚥 Sinyal terkini dari scan terakhir
  /top [N]          🏆 Top N saham skor tertinggi
  /config           ⚙️ Tampilkan konfigurasi threshold & swing filter
  /status           📡 Health bot + statistik
"""

import os, sys, logging, time, asyncio, threading, io, csv, json
import traceback
from datetime import datetime
from typing import Optional, List
import pandas as pd
import numpy as np

# ── Path setup ────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
SCREENER_DIR = os.path.join(ROOT, "idx_alpha_screener")
sys.path.insert(0, ROOT)
sys.path.insert(0, SCREENER_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegram.error import Conflict, TimedOut, RetryAfter

# ── Import idx_alpha_screener modules ──────────────────────────────
import data
import scoring as sc
import swing_filters as sf
import regime as rg
import risk as rm
from main import analisis_satu_saham

# ── Import AI Agent (OpenCodeZen backend) ─────────────────────────
from ai_agent import ask_ai

# ── Token ──────────────────────────────────────────────────────────
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Logging ────────────────────────────────────────────────────────
os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] screener_v2: %(message)s",
    handlers=[
        RotatingFileHandler(
            os.path.join(ROOT, "logs", "screener_v2_bot.log"),
            maxBytes=5*1024*1024, backupCount=5, encoding="utf-8"
        ),
        logging.StreamHandler()
    ])
logger = logging.getLogger("screener_v2_bot")

# ── Security: redact token from logs ──────────────────────────────
class _TokenFilter(logging.Filter):
    def filter(self, record):
        if hasattr(record, 'msg') and isinstance(record.msg, str):
            for t in [TOKEN]:
                if t and len(t) > 5:
                    record.msg = record.msg.replace(t, "BOT_TOKEN_REDACTED")
        return True
logging.getLogger("telegram").addFilter(_TokenFilter())
logger.addFilter(_TokenFilter())

# ── Bot start time ─────────────────────────────────────────────────
_start_time = time.time()

# ── Thread pool ────────────────────────────────────────────────────
from concurrent.futures import ThreadPoolExecutor
_thread_pool = ThreadPoolExecutor(max_workers=4)

# ── Retry & Circuit Breaker ───────────────────────────────────────
MAX_RETRIES = 3
BASE_DELAY = 1.0  # detik

def _api_call_with_retry(func, *args, **kwargs):
    """Wrapper exponential backoff untuk Telegram/API calls."""
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** (attempt - 1))  # 1, 2, 4
                logger.warning("API call attempt %d/%d failed: %s. Retry in %.0fs",
                              attempt, MAX_RETRIES, e, delay)
                time.sleep(delay)
            else:
                logger.error("All %d API attempts failed: %s", MAX_RETRIES, e)
    raise last_err

# ── Rate limiting ─────────────────────────────────────────────────
RATE_LIMIT_MAX = 5
RATE_LIMIT_PER_USER = 3
RATE_LIMIT_WINDOW = 10
_rate_semaphore = asyncio.Semaphore(RATE_LIMIT_MAX)
_user_rate: dict[int, list[float]] = {}
_user_rate_lock = asyncio.Lock()

async def _check_rate_limit(user_id: int) -> bool:
    async with _user_rate_lock:
        now = time.time()
        if user_id not in _user_rate:
            _user_rate[user_id] = []
        _user_rate[user_id] = [t for t in _user_rate[user_id] if now - t < RATE_LIMIT_WINDOW]
        if len(_user_rate[user_id]) >= RATE_LIMIT_PER_USER:
            return False
        _user_rate[user_id].append(now)
        return True

# ── Load config.yaml ──────────────────────────────────────────────
def _load_config() -> dict:
    import yaml
    path = os.path.join(SCREENER_DIR, "config.yaml")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Gagal load config: %s", e)
        return {}

_CONFIG = _load_config()

# ── Helpers ────────────────────────────────────────────────────────

def _fmt_rp(val) -> str:
    """Format Rupiah dengan titik."""
    try:
        return f"Rp{int(val):,}".replace(",", ".")
    except (ValueError, TypeError):
        return "Rp0"

def _latest_csv() -> str:
    """Cari CSV hasil scan terbaru di folder screener."""
    import glob
    files = sorted(glob.glob(os.path.join(SCREENER_DIR, "screener_v2_result*.csv")))
    return files[-1] if files else ""

def _read_latest_csv() -> Optional[pd.DataFrame]:
    path = _latest_csv()
    if not path:
        return None
    try:
        return pd.read_csv(path)
    except Exception:
        return None

def _generate_report_text() -> str:
    """Generate morning/evening report dari hasil scan terbaru."""
    df = _read_latest_csv()
    if df is None:
        return "❌ Belum ada data scan. Jalankan /scan dulu."

    # Signal distribution
    total = len(df)
    strong = len(df[df['signal']=='STRONG_BUY'])
    buy_count = len(df[df['signal']=='BUY'])
    weak = len(df[df['signal']=='WEAK_BUY'])
    hold = len(df[df['signal']=='HOLD'])
    sell = len(df[df['signal']=='SELL'])

    # Swing gate stats (jika kolom ada)
    swing_pass = 0
    if 'swing_trend' in df.columns and 'swing_volume' in df.columns:
        swing_pass = len(df[df['swing_trend'] & df['swing_volume']])

    # Top 3 by score
    top3 = df.sort_values('score', ascending=False).head(3)

    # IHSG context
    ihsg_str = "N/A"
    try:
        ihsg_df = data.fetch_ihsg_cached()
        if ihsg_df is not None and not ihsg_df.empty and len(ihsg_df) >= 2:
            close_col = 'close' if 'close' in ihsg_df.columns else 'Close' if 'Close' in ihsg_df.columns else None
            if close_col:
                ihsg_chg = (ihsg_df[close_col].iloc[-1] / ihsg_df[close_col].iloc[-2] - 1) * 100
                ihsg_str = f"{ihsg_chg:+.2f}%"
    except:
        pass

    # Build report
    lines = [
        f"📈 *Laporan Screening v2* — {datetime.now().strftime('%d/%m/%Y %H:%M')}",
        f"",
        f"🌍 IHSG: {ihsg_str} | Saham discan: {total}",
        f"",
        f"🟢 STRONG BUY: {strong}",
        f"🔵 BUY: {buy_count}",
        f"🟡 WEAK BUY: {weak}",
        f"⚪ HOLD: {hold}",
        f"🔴 SELL: {sell}",
        f"",
        f"🚦 Lolos Swing Gate: {swing_pass}/{total}",
        f"",
        f"🏆 *Top 3:*",
    ]
    for _, r in top3.iterrows():
        swing_mark = "🟢" if (r.get('swing_trend', False) and r.get('swing_volume', False)) else "⚫"
        lines.append(f"  #{r['ticker']} {swing_mark} — Skor {r['score']:.1f} | RRR {r.get('rrr',0):.1f}")

    lines.append(f"")
    lines.append(f"💡 /cek TICKER — detail | /gate TICKER — swing status")

    return "\n".join(lines)


def _check_data_freshness() -> Optional[str]:
    """Cek apakah data CSV masih fresh (< 12 jam). Return warning atau None."""
    path = _latest_csv()
    if not path:
        return "⚠️ Belum pernah scan. Ketik /scan untuk mulai."
    mtime = os.path.getmtime(path)
    age_hours = (time.time() - mtime) / 3600
    if age_hours > 24:
        return f"⚠️ Data terakhir {age_hours:.0f} jam yang lalu. Disarankan /scan ulang.\n"
    if age_hours > 12:
        return f"⚠️ Data terakhir {age_hours:.0f} jam yang lalu. Mungkin sudah tidak relevan.\n"
    return f"📅 Data: {age_hours:.1f} jam yang lalu\n"


def _format_single_result(r: dict) -> str:
    """Format dict hasil analisis_satu_saham ke string Telegram."""
    signal_emoji = {
        "STRONG_BUY": "🟢 STRONG BUY",
        "BUY": "🔵 BUY",
        "WEAK_BUY": "🟡 WEAK BUY",
        "HOLD": "⚪ HOLD",
        "SELL": "🔴 SELL",
    }
    signal = r.get("signal", "HOLD")
    sig_str = signal_emoji.get(signal, signal)

    swing_t = r.get("swing_trend", False)
    swing_v = r.get("swing_volume", False)
    swing_icon = "🟢" if (swing_t and swing_v) else "⚫"
    swing_status = "✅ Lolos" if (swing_t and swing_v) else "❌ Gagal"

    lines = [
        f"{swing_icon} *{r.get('ticker', '?')}* — {sig_str}",
        f"   Skor: {r.get('score', 0):.1f} | Regime: {r.get('regime', '?')}",
        f"   Harga: {_fmt_rp(r.get('price', 0))} | RSI: {r.get('rsi', 0)} | ADX: {r.get('adx', 0)}",
        f"   SL: {_fmt_rp(r.get('stop_loss', 0))} → TP: {_fmt_rp(r.get('take_profit', 0))} | RRR: {r.get('rrr', 0):.1f}",
        f"   Volume: {r.get('volume', 0):,}× | Vol Ratio: {r.get('vol_ratio', 0):.1f}x",
        f"   Swing Gate: {swing_status} (Trend:{'🟢' if swing_t else '🔴'} Vol:{'🟢' if swing_v else '🔴'})",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# COMMAND HANDLERS
# ═══════════════════════════════════════════════════════════════════

# ── /start ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Selamat datang di *IDX Alpha Screener v2*\n\n"
        "Bot ini menganalisis saham IHSG menggunakan sistem scoring v2\n"
        "dengan threshold ≥55 + mandatory swing filters.\n\n"
        "📌 Ketik /help untuk lihat semua perintah.\n"
        "💬 Atau tag @ aku di grup untuk tanya natural language."
    )

# ── /help ─────────────────────────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "❓ *Perintah Tersedia*\n\n"
        "/cek TICKER — Analisis satu saham (scoring + swing gate)\n"
        "  Contoh: `/cek BBCA`\n\n"
        "/scan [TICKER] — Scan saham dengan scoring v2\n"
        "  Contoh: `/scan` (semua 128 saham)\n"
        "  Contoh: `/scan BBCA BBRI ASII`\n\n"
        "/gate TICKER — Status swing gate saja\n"
        "  Contoh: `/gate BBCA`\n\n"
        "/sinyal — Lihat sinyal dari scan terakhir\n\n"
        "/top [N] — Top N saham skor tertinggi (default 10)\n\n"
        "/config — Konfigurasi threshold & swing filter\n\n"
        "/status — Health bot + statistik\n\n"
        "💬 *AI Chat:* Tag @ aku di grup untuk tanya natural language\n"
        "  Contoh: `@QuantYan_bot BBCA cocok buat swing?`"
    )
    await update.message.reply_text(help_text)

# ── /cek TICKER ────────────────────────────────────────────────────
async def cmd_cek(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Gunakan: `/cek BBCA`")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if not await _check_rate_limit(user_id):
        await update.message.reply_text("⏳ Sabar ya, jangan spam. Coba 10 detik lagi.")
        return

    ticker = context.args[0].upper().replace(".JK", "")
    logger.info("cmd_cek: %s oleh user %s", ticker, user_id)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_thread_pool, _do_cek, ticker)

    if result is None:
        await update.message.reply_text(f"❌ Data tidak tersedia untuk `{ticker}`. Cek koneksi atau ticker tidak valid.")
    else:
        await update.message.reply_text(result)

def _do_cek(ticker: str) -> Optional[str]:
    """Run analisis_satu_saham in thread pool."""
    try:
        res = analisis_satu_saham(f"{ticker}.JK", no_ihsg=True)
        if res is None:
            return None
        return _format_single_result(res)
    except Exception as e:
        logger.error("cek gagal untuk %s: %s", ticker, e)
        return None

# ── /gate TICKER ──────────────────────────────────────────────────
async def cmd_gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Gunakan: `/gate BBCA`")
        return

    user_id = update.effective_user.id if update.effective_user else 0
    if not await _check_rate_limit(user_id):
        await update.message.reply_text("⏳ Sabar ya.")
        return

    ticker = context.args[0].upper().replace(".JK", "")
    logger.info("cmd_gate: %s", ticker)

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(_thread_pool, _do_gate, ticker)

    if result is None:
        await update.message.reply_text(f"❌ Data tidak cukup untuk `{ticker}`.")
    else:
        await update.message.reply_text(result)

def _do_gate(ticker: str) -> Optional[str]:
    """Fetch data dan cek swing gate saja."""
    try:
        df = yf.download(f"{ticker}.JK", period="1y", progress=False, auto_adjust=True)
        if df.empty or len(df) < 60:
            return None

        # Flatten MultiIndex columns if needed
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Rename to lowercase
        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index)

        # Ensure we have required columns
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                return None

        gate = sf.swing_gate_pass(df)

        # Dapatkan data tambahan untuk konteks
        price = df["close"].iloc[-1]
        vol = df["volume"].iloc[-1]
        avg_vol = df["volume"].iloc[-21:-1].mean() if len(df) >= 21 else vol
        vol_ratio = vol / avg_vol if avg_vol > 0 else 1.0
        ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]

        lines = [
            f"🚦 *Swing Gate — {ticker}*\n",
            f"Harga: {_fmt_rp(price)}",
            f"EMA20: {_fmt_rp(ema20)} | EMA50: {_fmt_rp(ema50)}",
            f"Volume: {int(vol):,} | Vol Ratio: {vol_ratio:.1f}x\n",
        ]

        # Trend alignment detail
        lines.append("📈 *Weekly Trend Alignment:*")
        if gate["trend_aligned"]:
            lines.append("   ✅ Close > EMA20 > EMA50 (uptrend weekly)")
        else:
            cond_close_ema20 = price > ema20
            cond_close_ema50 = price > ema50
            cond_ema20_ema50 = ema20 > ema50
            lines.append(f"   ❌ Close>EMA20: {'✅' if cond_close_ema20 else '❌'} | "
                         f"Close>EMA50: {'✅' if cond_close_ema50 else '❌'} | "
                         f"EMA20>EMA50: {'✅' if cond_ema20_ema50 else '❌'}")

        # Volume detail
        lines.append("\n📊 *Volume Breakout:*")
        if gate["volume_breakout"]:
            lines.append(f"   ✅ Volume {vol_ratio:.1f}x avg + Close > Open")
        else:
            close = df["close"].iloc[-1]
            open_ = df["open"].iloc[-1]
            vol_ok = vol > 1.5 * avg_vol
            price_ok = close > open_
            lines.append(f"   ❌ Vol>1.5x: {'✅' if vol_ok else '❌'} | "
                         f"Close>Open: {'✅' if price_ok else '❌'}")

        # Kesimpulan
        lines.append(f"\n🔑 *Kesimpulan:* {'✅ LOLOS SWING GATE' if gate['passed'] else '❌ GAGAL — sinyal BUY akan di-HOLD-kan'}")
        if gate["reasons"]:
            for r in gate["reasons"]:
                lines.append(f"   • {r}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("gate gagal untuk %s: %s", ticker, e)
        return None

# ── /scan ─────────────────────────────────────────────────────────
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else 0
    if not await _check_rate_limit(user_id):
        await update.message.reply_text("⏳ Sabar ya.")
        return

    msg = await update.message.reply_text("🔍 *Scanning...* (bisa 2-5 menit untuk 128 saham)")

    loop = asyncio.get_event_loop()

    if context.args:
        # Scan specific tickers
        tickers = [t.upper() if t.endswith(".JK") else f"{t.upper()}.JK" for t in context.args]
        result = await loop.run_in_executor(_thread_pool, _do_scan_specific, tickers)
    else:
        # Scan all
        result = await loop.run_in_executor(_thread_pool, _do_scan_all)

    await msg.edit_text(result, parse_mode=None)

def _do_scan_all() -> str:
    """Scan all 128 saham using v2 system."""
    return _run_scan(data.TICKERS_IHSG_LIQUID)

def _do_scan_specific(tickers: List[str]) -> str:
    """Scan specific tickers."""
    return _run_scan(tickers)

def _run_scan(tickers: List[str]) -> str:
    """Core scan logic — parallel fetch untuk kecepatan."""
    start = time.time()
    results = []
    errors = 0
    total = len(tickers)

    # Parallel fetch — pola dari main.py
    try:
        price_data = data.scan_multiple(tickers, max_workers=1, delay_between=0.3)
    except Exception as e:
        logger.error("Parallel fetch gagal, fallback ke sequential: %s", e)
        price_data = {}

    for i, tkr in enumerate(tickers, 1):
        try:
            df = price_data.get(tkr, pd.DataFrame())
            res = analisis_satu_saham(tkr, df=df, no_ihsg=True)
            if res:
                results.append(res)
            else:
                errors += 1

            if i % 10 == 0:
                logger.info("Scan progress: %d/%d | dapat: %d | error: %d",
                           i, total, len(results), errors)

        except Exception as e:
            logger.error("Scan error %s: %s", tkr, e)
            errors += 1

    elapsed = time.time() - start

    if not results:
        return f"❌ Scan selesai ({elapsed:.0f}s). Tidak ada hasil. Error: {errors}/{total}."

    # Save to CSV
    csv_path = os.path.join(SCREENER_DIR, f"screener_v2_result_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
    fieldnames = [
        "ticker", "price", "score", "signal", "swing_trend", "swing_volume",
        "regime", "rsi", "adx", "macd", "vol_ratio", "ret_20d",
        "stop_loss", "take_profit", "rrr", "volume", "atr",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        results_sorted = sorted(results, key=lambda x: x["score"], reverse=True)
        writer.writerows(results_sorted)
    logger.info("Hasil scan disimpan ke %s", csv_path)

    # Auto-send report ke Telegram
    try:
        from utils.telegram_sender import send_telegram_sync
        report_msg = _generate_report_text()
        send_telegram_sync(report_msg)
        logger.info("Auto-send report ke Telegram")
    except Exception as e:
        logger.warning("Gagal auto-send Telegram: %s", e)

    # Stats
    buy_all = [r for r in results if r["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY")]
    strong = [r for r in results if r["signal"] == "STRONG_BUY"]
    buy_only = [r for r in results if r["signal"] == "BUY"]
    weak = [r for r in results if r["signal"] == "WEAK_BUY"]
    hold = [r for r in results if r["signal"] == "HOLD"]
    sell = [r for r in results if r["signal"] == "SELL"]

    # Top 5
    top5 = sorted(results, key=lambda x: x["score"], reverse=True)[:5]

    lines = [
        f"📈 *IDX Alpha Screener v2 — Scan Selesai*",
        f"Waktu: {elapsed:.0f}s | Saham: {len(results)} | Error: {errors}\n",
        f"🟢 STRONG BUY: {len(strong)}",
        f"🔵 BUY: {len(buy_only)}",
        f"🟡 WEAK BUY: {len(weak)}",
        f"⚪ HOLD: {len(hold)}",
        f"🔴 SELL/HINDARI: {len(sell)}\n",
        f"🏆 *Top 5:*",
    ]

    for r in top5:
        signal_emoji = "🟢" if r["signal"] == "STRONG_BUY" else "🔵" if r["signal"] == "BUY" else "🟡" if r["signal"] == "WEAK_BUY" else "⚪"
        swing_ok = r.get("swing_trend", False) and r.get("swing_volume", False)
        swing_mark = "🟢" if swing_ok else "⚫"
        lines.append(
            f"  {signal_emoji} {r['ticker']} — Skor {r['score']:.1f} | "
            f"Harga {_fmt_rp(r['price'])} | RRR {r['rrr']:.1f} | "
            f"Swing {swing_mark}"
        )

    lines.append(f"\n📁 Disimpan: screener_v2_result_*.csv")
    lines.append(f"💡 /top — lihat lebih banyak | /cek TICKER — detail saham")

    return "\n".join(lines)


# ── /sinyal ──────────────────────────────────────────────────────
async def cmd_sinyal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    df = _read_latest_csv()
    if df is None:
        await update.message.reply_text("❌ Belum ada data scan. Jalankan `/scan` dulu.")
        return

    buy = df[df["signal"].isin(["STRONG_BUY", "BUY", "WEAK_BUY"])].sort_values("score", ascending=False)
    strong = df[df["signal"] == "STRONG_BUY"].sort_values("score", ascending=False)

    lines = [f"🚦 *Sinyal Screening v2* ({datetime.now().strftime('%d/%m/%Y %H:%M')})",
             f"Total sinyal BUY: {len(buy)}\n"]

    # Swing gate stats
    if 'swing_trend' in df.columns and 'swing_volume' in df.columns:
        swing_pass = len(df[df['swing_trend'] & df['swing_volume']])
        swing_fail = len(df[~(df['swing_trend'] & df['swing_volume'])])
        lines.append(f"🚦 Swing Gate: ✅ {swing_pass} lolos | ❌ {swing_fail} gagal")
        lines.append(f"   (hanya yg lolos gate yang bisa dapat sinyal BUY)\n")

    if not strong.empty:
        lines.append(f"🟢 *STRONG BUY ({len(strong)}):*")
        for _, r in strong.head(5).iterrows():
            swing_ok = r.get("swing_trend", False) and r.get("swing_volume", False)
            swing_mark = "🟢" if swing_ok else "⚫"
            lines.append(f"  {swing_mark} {r['ticker']} — Skor {r['score']:.1f} | {_fmt_rp(r['price'])} | RRR {r.get('rrr', 0):.1f}")
        lines.append("")

    weak = df[df["signal"] == "WEAK_BUY"].sort_values("score", ascending=False)
    if not weak.empty:
        lines.append(f"🟡 *WEAK BUY ({len(weak)}):*")
        for _, r in weak.head(3).iterrows():
            swing_ok = r.get("swing_trend", False) and r.get("swing_volume", False)
            swing_mark = "🟢" if swing_ok else "⚫"
            lines.append(f"  {swing_mark} {r['ticker']} — Skor {r['score']:.1f} | {_fmt_rp(r['price'])}")
        if len(weak) > 3:
            lines.append(f"  ... dan {len(weak) - 3} lainnya")
        lines.append("")

    lines.append(f"💡 /cek TICKER — detail | /gate TICKER — swing status")

    await update.message.reply_text("\n".join(lines))


# ── /quick ──────────────────────────────────────────────────────────
async def cmd_quick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⚡ Tampilkan hanya sinyal BUY yang lolos swing gate."""
    df = _read_latest_csv()
    if df is None:
        await update.message.reply_text("❌ Belum ada data scan. Jalankan /scan dulu.")
        return

    n = 10
    if context.args:
        try:
            n = int(context.args[0])
            n = min(max(n, 1), 50)
        except ValueError:
            pass

    # Filter: hanya BUY signals yang lolos swing gate
    if 'swing_trend' in df.columns and 'swing_volume' in df.columns:
        filtered = df[(df['signal'].isin(['STRONG_BUY','BUY','WEAK_BUY'])) &
                       (df['swing_trend'] == True) &
                       (df['swing_volume'] == True)].sort_values('score', ascending=False)
    else:
        filtered = df[df['signal'].isin(['STRONG_BUY','BUY','WEAK_BUY'])].sort_values('score', ascending=False)

    if filtered.empty:
        await update.message.reply_text("⚡ Tidak ada sinyal yang lolos swing gate saat ini.")
        return

    lines = [f"⚡ *Quick Picks — Lolos Swing Gate* ({len(filtered)} total)\n"]

    for i, (_, r) in enumerate(filtered.head(n).iterrows(), 1):
        signal_emoji = "🟢" if r['signal'] == "STRONG_BUY" else "🔵" if r['signal'] == "BUY" else "🟡"
        lines.append(
            f"#{i} {signal_emoji} *{r['ticker']}* — Skor {r['score']:.1f} | "
            f"{_fmt_rp(r.get('price',0))} | RRR {r.get('rrr',0):.1f} | "
            f"RSI {r.get('rsi',0)} | ADX {r.get('adx',0)}"
        )

    await update.message.reply_text("\n".join(lines))


# ── /top ─────────────────────────────────────────────────────────
async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    df = _read_latest_csv()
    if df is None:
        await update.message.reply_text("❌ Belum ada data scan. Jalankan `/scan` dulu.")
        return

    n = 10
    if context.args:
        try:
            n = int(context.args[0])
            n = min(max(n, 1), 50)
        except ValueError:
            pass

    top = df.sort_values("score", ascending=False).head(n)

    lines = [f"🏆 *Top {n} — IDX Alpha Screener v2* ({datetime.now().strftime('%d/%m/%Y')})\n"]

    for i, (_, r) in enumerate(top.iterrows(), 1):
        signal_emoji = "🟢" if r["signal"] == "STRONG_BUY" else "🔵" if r["signal"] == "BUY" else "🟡" if r["signal"] == "WEAK_BUY" else "⚪"
        swing_ok = r.get("swing_trend", False) and r.get("swing_volume", False)
        swing_mark = "🟢" if swing_ok else "⚫"
        lines.append(
            f"#{i} {signal_emoji} *{r['ticker']}* {swing_mark}\n"
            f"   Skor {r['score']:.1f} | {_fmt_rp(r['price'])} | "
            f"RSI {r.get('rsi', 0)} | ADX {r.get('adx', 0)} | "
            f"RRR {r.get('rrr', 0):.1f}"
        )

    await update.message.reply_text("\n".join(lines))


# ── /config ───────────────────────────────────────────────────────
async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = _load_config()
    thresholds = cfg.get("scoring", {}).get("thresholds", {})
    swing = cfg.get("swing_filters", {})

    lines = [
        "⚙️ *Konfigurasi Threshold & Swing Filter*\n",
        "📊 *Threshold Scoring (minimum WEAK_BUY = 55):*",
    ]

    for regime, vals in thresholds.items():
        lines.append(f"  • {regime.upper()}: "
                     f"SB≥{vals.get('strong_buy', '?')} | "
                     f"B≥{vals.get('buy', '?')} | "
                     f"WB≥{vals.get('weak_buy', '?')}")

    lines.extend([
        "",
        "🚦 *Swing Filters:*",
        f"  • Enabled: {'✅' if swing.get('enabled', True) else '❌'}",
        f"  • Weekly EMA Short: {swing.get('weekly_ema_short', 20)}",
        f"  • Weekly EMA Long: {swing.get('weekly_ema_long', 50)}",
        f"  • Volume Multiplier: {swing.get('volume_multiplier', 1.5)}x",
        f"  • Volume Lookback: {swing.get('volume_lookback_days', 20)} hari",
        f"  • Target sinyal: {swing.get('min_signals_per_month', 20)}–{swing.get('max_signals_per_month', 30)}/bulan",
    ])

    await update.message.reply_text("\n".join(lines))


# ── /status ────────────────────────────────────────────────────────
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uptime = time.time() - _start_time
    hours, rem = divmod(int(uptime), 3600)
    minutes, secs = divmod(rem, 60)

    df = _read_latest_csv()
    last_scan = "Belum pernah"
    total_stocks = 0
    if df is not None:
        last_scan = os.path.basename(_latest_csv())
        total_stocks = len(df)

    lines = [
        "📡 *Status Bot — Screener v2*",
        f"⏱ Uptime: {hours}j {minutes}m {secs}d",
        f"📁 Scan terakhir: {last_scan}",
        f"📊 Total saham discan: {total_stocks}",
        f"🔧 Threshold: 55 (universal) + swing filters",
        f"🤖 AI Backend: OpenCodeZen (deepseek-v4-flash-free)",
    ]

    await update.message.reply_text("\n".join(lines))


# ── /report ────────────────────────────────────────────────────────
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """📋 Laporan screening komprehensif."""
    user_id = update.effective_user.id if update.effective_user else 0
    logger.info("cmd_report by user %s", user_id)

    if not await _check_rate_limit(user_id):
        await update.message.reply_text("⏳ Sabar ya, jangan spam.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    loop = asyncio.get_event_loop()
    report = await loop.run_in_executor(_thread_pool, _generate_report_text)
    await update.message.reply_text(report)


# ═══════════════════════════════════════════════════════════════════
# AI CHAT HANDLER — Natural Language (OpenCodeZen)
# ═══════════════════════════════════════════════════════════════════
async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    # Only respond if bot is mentioned or replied to
    bot_username = context.bot.username
    mentioned = f"@{bot_username}" in text if bot_username else False
    is_reply_to_bot = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
        and update.message.reply_to_message.from_user.id == context.bot.id
    )
    if not mentioned and not is_reply_to_bot:
        return

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    loop = asyncio.get_event_loop()
    clean_text = text.replace(f"@{bot_username}", "").strip() if bot_username else text
    from_user = update.message.from_user
    user_id = from_user.id if from_user else chat_id

    try:
        answer = await loop.run_in_executor(None, ask_ai, chat_id, clean_text, user_id)
        if answer:
            await update.message.reply_text(answer, parse_mode=None)
    except Exception as e:
        logger.error("AI chat error: %s", e)
        await update.message.reply_text("⚠️ Maaf, ada gangguan. Coba lagi nanti.")


# ═══════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ═══════════════════════════════════════════════════════════════════
async def _error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(f"Exception: {error}")
    if isinstance(error, Conflict):
        logger.critical("⚠️ CONFLICT: Another bot instance is polling this token!")
        return
    if isinstance(error, (TimedOut, RetryAfter)):
        logger.warning("⏳ Telegram API rate limit/timeout — will retry.")
        return
    logger.exception("💥 UNEXPECTED ERROR", exc_info=error)


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════
def main():
    if not TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env")
        return

    # ── Lock file ──
    import tempfile as _tmp, atexit
    LOCK_FILE = os.path.join(_tmp.gettempdir(), "screener_v2_bot.lock")

    def _is_pid_alive(pid: int) -> bool:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def _cleanup_lock():
        try:
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)
        except Exception:
            pass

    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = f.read().strip()
            if old_pid and old_pid.isdigit() and _is_pid_alive(int(old_pid)):
                print(f"ERROR: Bot already running (PID {old_pid}). Kill it first.")
                print("Run: taskkill /F /IM python.exe  lalu restart.")
                return
            else:
                os.remove(LOCK_FILE)
        except Exception:
            try:
                os.remove(LOCK_FILE)
            except Exception:
                pass

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_cleanup_lock)

    # ── Build app ──
    app = Application.builder().token(TOKEN).build()

    # ── Register handlers ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("scan", cmd_scan))
    app.add_handler(CommandHandler("gate", cmd_gate))
    app.add_handler(CommandHandler("sinyal", cmd_sinyal))
    app.add_handler(CommandHandler("quick", cmd_quick))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))

    # AI Chat handler (natural language, only when tagged)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ai_chat))

    # Error handler
    app.add_error_handler(_error_handler)

    # ── Register bot commands ──
    async def _register_commands(app):
        bot_commands = [
            BotCommand("start", "👋 Welcome"),
            BotCommand("help", "❓ Daftar perintah"),
            BotCommand("cek", "📊 Analisis satu saham v2"),
            BotCommand("scan", "🔍 Scan semua saham IHSG"),
            BotCommand("gate", "🚦 Status swing gate TICKER"),
            BotCommand("sinyal", "🚥 Sinyal dari scan terakhir"),
            BotCommand("quick", "⚡ Sinyal yang lolos swing gate"),
            BotCommand("top", "🏆 Top N saham"),
            BotCommand("config", "⚙️ Threshold & swing filter"),
            BotCommand("status", "📡 Health bot"),
            BotCommand("report", "📋 Laporan screening"),
        ]
        await app.bot.set_my_commands(bot_commands)

    app.post_init = _register_commands

    print("=" * 55)
    print("  SCREENER v2 BOT — Threshold ≥55 + Swing Filters")
    print("  /cek /scan /gate /sinyal /top /config /status")
    print("  AI Chat: OpenCodeZen (deepseek-v4-flash-free)")
    print("=" * 55)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
