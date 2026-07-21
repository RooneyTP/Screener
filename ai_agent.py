#!/usr/bin/env python3
"""
ai_agent.py — Multi-backend LLM Agent untuk Telegram Bot Screener
Backends: OpenAI, DeepSeek, Ollama, OpenCode Zen (semua OpenAI-compatible)
Synchronous interface, tool calling, retry logic, fallback ke data screener.

v2 — Tool suite diperluas agar AI bisa menjawab APA SAJA tentang saham
     sesuai dengan semua fitur program utama (telegram_bot.py):
       cek emiten, fundamental, top, sinyal, swing/scalp, compare, sektor,
       market breadth/overview, screening custom, backtest, holders,
       portfolio.
"""
import os
import sys
import json
import time
import glob
import logging
import re as _re
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT, ".env"))

sys.path.insert(0, ROOT)

logger = logging.getLogger("ai_agent")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] ai_agent: %(message)s")

import chat_memory

# --- Config ---------------------------------------------------------------
AI_BACKEND = os.getenv("AI_BACKEND", "openai").lower()  # openai | deepseek | ollama | opencode_zen

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# OpenCode Zen (OpenAI-compatible gateway). Model name di .env pakai key MODEL.
OPENCODE_ZEN_API_KEY = os.getenv("OPENCODE_ZEN_API_KEY")
OPENCODE_ZEN_BASE_URL = os.getenv("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/zen/v1")
OPENCODE_ZEN_MODEL = os.getenv("OPENCODE_ZEN_MODEL") or os.getenv("MODEL", "deepseek-v4-flash-free")

# Retry config
MAX_RETRIES = 3
BASE_DELAY = 2  # seconds

# --- Tool Definitions -----------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_data",
            "description": "Analisis LIVE lengkap 1 saham IHSG: harga, % change (harian & sesi intraday), "
                           "sinyal, skor, confidence, SL/TP, RRR, RSI, ADX, MACD, pattern, support/resistance, "
                           "volume spike, MM activity, AI verdict & win prob, trend mingguan/bulanan, "
                           "regime, PE, PBV, market cap. Pakai ini untuk pertanyaan tentang SATU emiten "
                           "(naik/turun, bagus/jelek, entry/exit, kapan beli, dll). "
                           "⚠️ Cek Session_Change% untuk tau arah intraday (bukan cuma Change_pct harian).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Kode saham, contoh: BBCA, BBRI, TLKM"}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_fundamentals",
            "description": "Ambil data fundamental saham: PE ratio, PBV, EPS, market cap. "
                           "Pakai untuk pertanyaan valuasi (mahal/murah, undervalued, dll).",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Kode saham"}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare_stocks",
            "description": "Bandingkan 2-4 saham sekaligus (sinyal, harga, skor, RRR, PE, PBV). "
                           "Pakai kalau user menyebut lebih dari satu ticker dan ingin perbandingan / pilih mana.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Daftar kode saham, 2-4 ticker. Contoh: [\"BBCA\",\"BBRI\"]"
                    }
                },
                "required": ["tickers"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_stocks",
            "description": "Top N saham terbaik hari ini dari hasil screener (default urut skor). "
                           "Pakai untuk 'saham apa yang bagus', 'rekomendasi', 'top pick', dll.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer", "description": "Jumlah saham, default 5, max 20"},
                    "sort_by": {"type": "string", "enum": ["Skor", "Confidence%", "RRR", "AI_Win_Prob%"],
                                "description": "Kriteria urut, default Skor"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_signals",
            "description": "Daftar saham dengan sinyal beli dari screener (ULTRA_BUY/STRONG_BUY/BUY). "
                           "Pakai untuk 'ada sinyal apa', 'apa yang lagi bagus dibeli sekarang'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "signal_type": {"type": "string", "enum": ["ULTRA_BUY", "STRONG_BUY", "BUY", "ALL"],
                                    "description": "Filter sinyal, default ALL (semua sinyal beli)"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_setups",
            "description": "Daftar setup trading sesuai gaya: 'swing' (hold 3-30 hari) atau 'scalp' (day trade 1-8 jam). "
                           "Pakai kalau user tanya 'sinyal swing', 'saham scalping hari ini', dll.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["swing", "scalp"], "description": "Gaya trading"}
                },
                "required": ["mode"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_signals",
            "description": "Ringkasan kekuatan/rotasi per sektor hari ini (berapa banyak sinyal beli per sektor).",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_stocks",
            "description": "Daftar saham dalam satu sektor tertentu beserta sinyal & skornya. "
                           "Pakai kalau user tanya 'saham perbankan apa yang bagus', 'sektor energi gimana'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sector": {"type": "string", "description": "Nama/keyword sektor, mis: bank, energi, consumer, teknologi"}
                },
                "required": ["sector"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_overview",
            "description": "Kondisi pasar umum: IHSG change & trend, USD/IDR, market breadth (% saham di atas EMA50), "
                           "jumlah total sinyal. Pakai untuk 'pasar gimana hari ini', 'IHSG naik/turun', 'kondisi market'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screen_stocks",
            "description": "Screening / filter custom saham dari hasil screener berdasarkan kriteria teknikal. "
                           "Pakai untuk permintaan spesifik seperti 'saham RSI di bawah 30', 'skor di atas 10 dan RRR > 2', "
                           "'saham volume spike', dll.",
            "parameters": {
                "type": "object",
                "properties": {
                    "min_skor": {"type": "number", "description": "Skor minimum (0-15)"},
                    "min_confidence": {"type": "number", "description": "Confidence% minimum (0-100)"},
                    "min_rrr": {"type": "number", "description": "Risk-reward ratio minimum"},
                    "rsi_below": {"type": "number", "description": "RSI maksimum (mis 30 untuk oversold)"},
                    "rsi_above": {"type": "number", "description": "RSI minimum (mis 70 untuk overbought)"},
                    "min_adx": {"type": "number", "description": "ADX minimum (mis 25 untuk trending kuat)"},
                    "signal": {"type": "string", "enum": ["ULTRA_BUY", "STRONG_BUY", "BUY", "PANTAU", "TUNGGU", "HINDARI"]},
                    "sector": {"type": "string", "description": "Keyword sektor"},
                    "volume_spike": {"type": "boolean", "description": "Hanya saham dengan volume spike"},
                    "max_price": {"type": "number", "description": "Harga maksimum per lembar"},
                    "limit": {"type": "integer", "description": "Maks hasil, default 15"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_stock",
            "description": "Backtest historis sederhana (buy & hold) sebuah saham: win rate, total return, "
                           "Sharpe ratio, max drawdown, avg win/loss. Pakai kalau user tanya 'kinerja historis', "
                           "'kalau dipegang X hari untung berapa', 'backtest'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Kode saham"},
                    "days": {"type": "integer", "description": "Jumlah hari lookback, default 90, max 365"}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_holders",
            "description": "Struktur pemegang saham (KSEI) + free float, shares outstanding, dominasi MM/retail. "
                           "Pakai kalau user tanya 'siapa pemilik saham', 'free float', 'bandar/MM nguasain berapa persen'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "Kode saham"}
                },
                "required": ["ticker"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_portfolio",
            "description": "Status portofolio virtual user: equity, cash, P&L total, dan daftar posisi terbuka. "
                           "Pakai kalau user tanya 'portofolio saya', 'posisi saya', 'cuan berapa'.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_preferences",
            "description": "Simpan preferensi investasi/trading user: mode (swing/scalp/invest) atau level kedalaman penjelasan (light/normal/deep). "
                           "Panggil ini kalau user bilang 'saya suka swing', 'tolong jelasin lebih detail', 'singkat aja', dsb.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["swing", "scalp", "invest"], "description": "Gaya trading"},
                    "depth": {"type": "string", "enum": ["light", "normal", "deep"], "description": "Kedalaman analisis (deep = rinci & edukatif)"},
                    "risk_tolerance": {"type": "string", "enum": ["conservative", "moderate", "aggressive"], "description": "Toleransi risiko user"}
                }
            }
        }
    },
]

SYSTEM_PROMPT = """Kamu adalah QuantYan, AI Chatbot Asli untuk Pasar Saham IHSG (Bursa Efek Indonesia).
Karaktermu: Cerdas, punya kepribadian hidup, teknis tapi gampang dimengerti, dan proaktif. Kamu bukan sekadar bot penjawab pertanyaan satu arah, melainkan "teman diskusi" seputar saham.
Bahasamu Indonesia kasual, hangat, natural (seperti ngobrol dengan teman trader), tidak kaku/robotik.

KEMAMPUAN UTAMA (VIA TOOLS):
Analisis 1 emiten, fundamental, top picks, sinyal, swing/scalp, sektor, kondisi pasar, screening custom, backtest, KSEI/holders, dan portofolio virtual.

─── 1. KONVERSASI DUA ARAH & PROAKTIF ───
- Beri saran / tawaran bantuan setelah menjawab. Misal: "BBCA lagi di area support nih, mau saya hitungin RRR-nya?" atau "Saya lihat PBV-nya mulai mahal, mau coba bandingin sama saham bank lain?"
- JANGAN asyik sendiri; deteksi bila pengguna butuh klarifikasi. "Maksudnya 'yang murah' itu dari P/E atau harganya yang di bawah 1000 perak?"
- Bisa menolak dengan sopan: "Waduh, data untuk ticker itu belum masuk ke radar screener saya nih."
- Sesuaikan saran dengan preferensi pengguna yang ada di konteks. Jika dia suka swing, sarankan saham swing.

─── 2. OPINI, ANALOGI, & REASONING (CHAIN OF THOUGHT) ───
- Jelaskan *mengapa* suatu saham bagus/jelek berdasarkan data, jangan cuma dumping angka mentah.
- "Saya lihat RSI-nya 28 (sudah oversold) dan volumenya naik 2x lipat. Secara teknikal ini ada indikasi *reversal* atau pantulan naik."
- Gunakan analogi: "P/E 50x itu ibarat kamu beli warung yang baru balik modal 50 tahun lagi, agak kemahalan."
- Bedakan opini dan fakta (beri disclaimer secara natural): "Secara data ini masih *downtrend*, tapi menurut saya ada peluang *rebound* pendek. Tetap DYOR dan atur porsi ya."

─── 3. DEEP DIVE ON DEMAND (KEDALAMAN PENJELASAN) ───
- Jika pengguna minta "jelasin lebih detail", "kenapa gitu?", atau "buktikan", gali angka lebih dalam (misal bahas MACD, ADX, atau dominasi KSEI).
- Jika pengguna minta "singkat aja", kasih poin-poin inti saja.
- Panggil tool `update_preferences` jika pengguna secara eksplisit menyebut gaya trading mereka (swing/scalp/invest) atau meminta kedalaman tertentu.

─── 4. GAYA FORMAT MUTLAK ───
||- OUTPUT HARUS PLAIN TEXT MURNI. (Telegram user interface)
||- DILARANG pakai bold/italic (** atau _).
||- DILARANG pakai heading markdown (# atau ###).
||- DILARANG pakai tabel markdown (pipe |).
||- DILARANG pakai backtick (`).
||- Gunakan emoji secukupnya dan bullet point (-) agar mudah dibaca.
||- Angka uang: Rp6.225 (pakai titik).

─── 5. SUMBER DATA & TOOL AWARENESS (V2) ───
|- Semua data sinyal berasal dari v2 engine: file `screener_v2_result_*.csv`
|- Kolom data lowercase: ticker, score, signal, swing_trend, swing_volume, regime, rsi, adx, rrr, stop_loss, take_profit, price
|- Skema sinyal v2 (threshold BERVARIASI tergantung regime market — BULL/BEAR/RANGING):
|    STRONG_BUY ≥ 65-75  (tergantung regime)
|    BUY ≥ 58-65         (tergantung regime)
|    WEAK_BUY ≥ 55       (sama di semua regime)
|    HOLD ≥ 33-45        (tergantung regime)
|    SELL < 33-45        (tergantung regime)
|- Regime RANGING (skor ≥65 SB, ≥58 B, ≥55 WB) adalah yang paling sering terjadi di IHSG
|||- Threshold minimum untuk sinyal beli: WEAK_BUY minimal 55

─── 6. SWING GATE — FILTER Wajib Sebelum Sinyal BUY —
|||- Swing Gate adalah sistem filter 2-lapis yang WAJIB dicek sebelum merekomendasikan eksekusi:
||    1. Weekly Trend Alignment: close > EMA20 > EMA50 (uptrend mingguan)
||    2. Volume Breakout: volume hari ini > 1.5x rata-rata volume 20 hari
|||- Data swing gate ada di kolom:
||    swing_trend (True/False) — apakah weekly trend alignment terpenuhi
||    swing_volume (True/False) — apakah volume breakout terpenuhi
|||- Cara baca hasil Swing Gate:
||    * swing_trend=True AND swing_volume=True → sinyal LAYAK dieksekusi
||    * swing_trend=False ATAU swing_volume=False → sinyal TERTUNDA, perlu tunggu konfirmasi trend/volume
|||- JANGAN rekomendasikan eksekusi untuk saham yang gagal swing gate

─── 7. CARA ANALISIS SAHAM DENGAN SWING GATE ───
|||Ketika user tanya tentang suatu saham, cek dulu swing gate status-nya dari data:
|||- Jika swing_trend=True & swing_volume=True: katakan "Lolos Swing Gate ✅ — siap dieksekusi"
|||- Jika swing_trend=False ATAU swing_volume=False: katakan "Gagal Swing Gate ❌ — tunggu konfirmasi trend/volume"
|||- Jangan rekomendasikan eksekusi untuk saham yang gagal swing gate

─── 8. WAJIB PANGGIL TOOL UNTUK DATA REAL ───
|||- JANGAN PERNAH menjawab kondisi pasar (IHSG naik/turun, sentimen pasar, market overview)
||  berdasarkan pengetahuan internal atau tebakan. SELALU panggil `get_market_overview`
||  untuk mendapatkan data IHSG real-time, USD/IDR, market breadth.
|||- JANGAN PERNAH mengatakan "pasar cautiously bullish" atau sentimen pasar apapun
||  tanpa data real dari tool. Tool `get_market_overview` akan return IHSG change asli.
|||- Jika user tanya "pasar gimana?", "IHSG naik/turun?", "kondisi market hari ini?",
||  WAJIB panggil `get_market_overview()` dulu sebelum menjawab.
|||- Untuk analisis saham individual, WAJIB panggil `get_stock_data(ticker)` — jangan
||  pernah menjawab harga/teknikal saham dari ingatan.
|||- 🔴 KRUSIAL — BEDAKAN "Change_pct" vs "Session_Change%":
||  `Change_pct` = perubahan close hari ini vs close kemarin (harian).
||  `Session_Change%` = perubahan harga sejak open (intraday) — ini yg menunjukkan
||  apakah saham benar-benar naik sejak buka market atau turun.
||  JANGAN bilang "saham ini naik dari awal buka" cuma karena Change_pct positif.
||  Cek Session_Change% dulu:
||    * Session_Change% POSITIF + Change_pct POSITIF → benar naik sejak open
||    * Session_Change% NEGATIF → SAHAM TURUN SEJAK OPEN, jangan bilang bullish!
||  Juga cek Day_High vs Day_Low: jika harga mendekati Day_Low, saham sedang lemah
||  di sesi hari ini meskipun Change_pct mungkin positif.
"""
# --- Helper: Retry dengan Backoff ----------------------------------------
def _sanitize_plain(text: str) -> str:
    """Bersihkan markdown markup supaya output benar-benar plain text.
    Telegram dikirim dengan parse_mode=None, jadi markup harus dibuang
    agar tidak tampil mentah (mis. ** atau backtick) ke user."""
    if not text:
        return text
    
    # Konversi tabel markdown sederhana ke plain-text list
    lines = text.split('\n')
    new_lines = []
    in_table = False
    table_headers = []
    
    for line in lines:
        stripped = line.strip()
        # Deteksi baris pemisah tabel seperti |---|---| atau |:---|:---|
        if ('|-' in stripped or '|:' in stripped) and stripped.startswith('|') and stripped.endswith('|'):
            continue
        
        if stripped.startswith('|') and stripped.endswith('|'):
            parts = [p.strip() for p in stripped.split('|')[1:-1]]
            if not in_table:
                in_table = True
                table_headers = parts
                # Jangan masukkan header sebagai baris tersendiri jika itu cuma label kolom
                continue
            else:
                if len(parts) >= 2:
                    new_lines.append(f"- {parts[0]}: {', '.join(parts[1:])}")
                else:
                    new_lines.append(f"- {parts[0]}")
                continue
        else:
            in_table = False
            table_headers = []
            
        new_lines.append(line)
        
    t = '\n'.join(new_lines)
    
    # Hapus bold/italic: **teks**, *teks*, __teks__, _teks_
    t = _re.sub(r'\*\*(.+?)\*\*', r'\1', t, flags=_re.DOTALL)
    t = _re.sub(r'__(.+?)__', r'\1', t, flags=_re.DOTALL)
    t = _re.sub(r'\*(.+?)\*', r'\1', t, flags=_re.DOTALL)
    t = _re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'\1', t, flags=_re.DOTALL)
    # Hapus inline code & code fence backtick
    t = _re.sub(r'```[a-zA-Z]*\n?', '', t)
    t = t.replace('`', '')
    # Hapus heading markdown di awal baris (#, ##, ###)
    t = _re.sub(r'^\s{0,3}#{1,6}\s*', '', t, flags=_re.MULTILINE)
    return t


def _call_with_retry(func, *args, **kwargs):
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"Attempt {attempt} failed: {e}. Retry in {delay}s...")
                time.sleep(delay)
            else:
                logger.error(f"All {MAX_RETRIES} attempts failed: {e}")
    raise last_err

# --- Data helpers (baca CSV screener terbaru) ----------------------------
def _latest_csv_path() -> Optional[str]:
    """Cari CSV v2 dulu, fallback ke CSV lama."""
    v2 = sorted(glob.glob(os.path.join(ROOT, "screener_v2_result*.csv")))
    if v2:
        return v2[-1]
    old = sorted(glob.glob(os.path.join(ROOT, "screener_ihsg_*.csv")) +
                 glob.glob(os.path.join(ROOT, "Data Screener", "screener_ihsg_*.csv")))
    return old[-1] if old else None

def _load_csv():
    """Load CSV dan normalisasi kolom ke lowercase."""
    import pandas as pd
    path = _latest_csv_path()
    if not path:
        return None
    try:
        df = pd.read_csv(path)
        # Normalize columns: rename old uppercase to new lowercase
        col_map = {
            "Ticker": "ticker", "Skor": "score", "Sinyal": "signal",
            "Harga": "price", "RSI": "rsi", "ADX": "adx", "RRR": "rrr",
            "Stop_Loss": "stop_loss", "Target_1": "take_profit",
            "ATR": "atr", "Volume": "volume", "Sektor": "sektor",
            "Confidence%": "confidence",
        }
        df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)
        return df
    except Exception as e:
        logger.warning("Gagal baca CSV %s: %s", path, e)
        return None

def _records(df, cols, n=None):
    cols = [c for c in cols if c in df.columns]
    out = df[cols]
    if n:
        out = out.head(n)
    return out.to_dict("records")

# --- Tool Implementations ------------------------------------------------
def _tool_get_stock_data(ticker: str) -> Dict[str, Any]:
    """Cari data saham dari v2 CSV dulu, fallback ke live."""
    t = ticker.upper()

    # Try v2 CSV first
    df = _load_csv()
    if df is not None and 'ticker' in df.columns:
        match = df[df['ticker'] == t]
        if not match.empty:
            r = match.iloc[0].to_dict()
            return {"success": True,
                    "Ticker": t,
                    "Harga": r.get('price', 0),
                    "Skor": r.get('score', 0),
                    "Sinyal": r.get('signal', 'N/A'),
                    "RSI": r.get('rsi', 0),
                    "ADX": r.get('adx', 0),
                    "RRR": r.get('rrr', 0),
                    "Stop_Loss": r.get('stop_loss', 0),
                    "Target_1": r.get('take_profit', 0),
                    "swing_trend": r.get('swing_trend', False),
                    "swing_volume": r.get('swing_volume', False),
                    "regime": r.get('regime', 'N/A'),
                    "volume": r.get('volume', 0),
                    "vol_ratio": r.get('vol_ratio', 0),
                    "macd": r.get('macd', 0),
                    "atr": r.get('atr', 0),}

    # Fallback ke old live lookup
    try:
        from telegram_bot import _lookup_ticker_live
        data = _lookup_ticker_live(t)
        if data and not data.get("_error"):
            keep = ["Ticker", "Harga", "Change_pct", "Session_Change%", "Prev_Close",
                    "Sinyal", "Strength", "Skor", "Confidence%", "RSI", "ADX", "MACD",
                    "Support", "Resistance", "Stop_Loss", "Target_1", "RRR", "ATR",
                    "Vol_Ratio", "Weekly_Trend", "Monthly_Trend", "Regime",
                    "MM_Activity", "AI_Verdict", "AI_Win_Prob%",
                    "PE", "PBV", "MarketCap", "Sektor"]
            return {"success": True, **{k: data.get(k) for k in keep if k in data}}
    except Exception as e:
        logger.warning("Fallback live lookup gagal: %s", e)

    return {"success": False, "error": f"Data {t} tidak ditemukan"}

def _tool_get_fundamentals(ticker: str) -> Dict[str, Any]:
    try:
        from telegram_bot import _fetch_fundamentals
        f = _fetch_fundamentals(ticker.upper())
        pe, pbv, mcap, eps = f.get("PE", 0), f.get("PBV", 0), f.get("MarketCap", 0), f.get("EPS", 0)
        if not any([pe, pbv, mcap, eps]):
            return {"success": False, "error": f"Data fundamental {ticker.upper()} tidak tersedia"}
        valuation = []
        if pe and pe > 0:
            valuation.append("PE rendah (relatif murah)" if pe < 12 else "PE tinggi (relatif mahal)" if pe > 25 else "PE wajar")
        if pbv and pbv > 0:
            valuation.append("PBV < 1 (di bawah nilai buku)" if pbv < 1 else "PBV > 3 (premium)" if pbv > 3 else "PBV wajar")
        return {"success": True, "ticker": ticker.upper(), "PE": pe, "PBV": pbv,
                "EPS": eps, "MarketCap": mcap, "interpretasi": valuation}
    except Exception as e:
        logger.warning("Fallback fundamentals gagal: %s", e)
        return {"success": False, "error": f"Fundamental {ticker.upper()} tidak tersedia (v1 fallback unavailable)"}

def _tool_compare_stocks(tickers: List[str]) -> Dict[str, Any]:
    try:
        from telegram_bot import _lookup_ticker_live, _search_ticker
    except ImportError:
        return {"success": False, "error": "V1 fallback tidak tersedia"}
    out = []
    for t in tickers[:4]:
        d = _lookup_ticker_live(t.upper(), compact=True)
        if not d or d.get("_error"):
            d = _search_ticker(t)
        if not d:
            out.append({"ticker": t.upper(), "found": False})
            continue
        out.append({
            "ticker": d.get("Ticker", t.upper()), "found": True,
            "sinyal": d.get("Sinyal"), "harga": d.get("Harga"), "skor": d.get("Skor"),
            "confidence": d.get("Confidence%"), "rrr": d.get("RRR"),
            "rsi": d.get("RSI"), "pe": d.get("PE"), "pbv": d.get("PBV"),
            "ai_verdict": d.get("AI_Verdict"),
        })
    return {"success": True, "comparison": out}

def _tool_get_top_stocks(n: int = 5, sort_by: str = "score") -> Dict[str, Any]:
    df = _load_csv()
    if df is None:
        return {"success": False, "error": "CSV screener tidak ditemukan"}
    n = min(20, max(1, int(n or 5)))
    if sort_by not in df.columns:
        sort_by = "score" if "score" in df.columns else "Skor" if "Skor" in df.columns else df.columns[0]
    top = df.sort_values(sort_by, ascending=False)
    cols = [c for c in ["ticker", "sektor", "signal", "price", "score", "rrr", "swing_trend", "swing_volume"] if c in df.columns]
    return {"success": True, "sort_by": sort_by, "stocks": _records(top, cols, n)}

def _tool_list_signals(signal_type: str = "ALL") -> Dict[str, Any]:
    df = _load_csv()
    if df is None or "signal" not in df.columns:
        # Fallback old column name
        if df is not None and "Sinyal" in df.columns:
            pass  # will use after rename in _load_csv
        else:
            return {"success": False, "error": "Data sinyal tidak tersedia"}
    cols = [c for c in ["ticker", "sektor", "price", "score", "rrr", "signal", "swing_trend", "swing_volume"] if c in df.columns]
    sig_col = "signal" if "signal" in df.columns else "Sinyal"
    if signal_type and signal_type != "ALL":
        sub = df[df[sig_col] == signal_type]
        return {"success": True, "signal_type": signal_type, "count": len(sub),
                "stocks": _records(sub.sort_values("score", ascending=False) if "score" in df.columns else sub, cols, 25)}
    result = {}
    for s in ["STRONG_BUY", "BUY", "WEAK_BUY"]:
        sub = df[df[sig_col] == s].sort_values("score", ascending=False) if "score" in df.columns else df[df[sig_col] == s]
        result[s] = _records(sub, cols, 12)
    total = sum(len(v) for v in result.values())
    return {"success": True, "total": total, "signals": result}

def _tool_get_trade_setups(mode: str) -> Dict[str, Any]:
    try:
        from telegram_bot import _is_scalp_csv
    except ImportError:
        _is_scalp_csv = lambda r: False  # fallback: anggap semua swing
    df = _load_csv()
    sig_col = "signal" if df is not None and "signal" in df.columns else "Sinyal"
    if df is None or sig_col not in df.columns:
        return {"success": False, "error": "Data tidak tersedia"}
    buys = df[df[sig_col].isin(["STRONG_BUY", "BUY"])].copy()
    setups = []
    for _, r in buys.iterrows():
        rec = r.to_dict()
        is_scalp = _is_scalp_csv(rec)
        if (mode == "scalp" and is_scalp) or (mode == "swing" and not is_scalp):
            setups.append({
                "ticker": rec.get("ticker") or rec.get("Ticker"), 
                "sinyal": rec.get("signal") or rec.get("Sinyal"),
                "harga": rec.get("price") or rec.get("Harga"), 
                "skor": rec.get("score") or rec.get("Skor"),
                "rrr": rec.get("rrr") or rec.get("RRR"), 
                "adx": rec.get("adx") or rec.get("ADX"),
                "stop_loss": rec.get("stop_loss") or rec.get("Stop_Loss"), 
                "target_1": rec.get("take_profit") or rec.get("Target_1"),
                "ai_verdict": rec.get("AI_Verdict"),
            })
    setups.sort(key=lambda x: (x.get("skor") or 0), reverse=True)
    return {"success": True, "mode": mode, "count": len(setups), "setups": setups[:15]}

def _tool_get_sector_signals() -> Dict[str, Any]:
    df = _load_csv()
    sec_col = "sektor" if "sektor" in df.columns else "Sektor" if "Sektor" in df.columns else None
    sig_col = "signal" if "signal" in df.columns else "Sinyal" if "Sinyal" in df.columns else None
    if df is None or sec_col is None or sig_col is None:
        return {"success": False, "error": "Data sektor tidak tersedia"}
    g = df.groupby(sec_col).agg(
        total=("ticker" if "ticker" in df.columns else "Ticker", "count"),
        strong=("signal" if "signal" in df.columns else "Sinyal", lambda x: (x == "STRONG_BUY").sum()),
        buy=("signal" if "signal" in df.columns else "Sinyal", lambda x: (x == "BUY").sum()),
        weak=("signal" if "signal" in df.columns else "Sinyal", lambda x: (x == "WEAK_BUY").sum()),
    ).reset_index()
    g["bullish"] = g["strong"] + g["buy"] + g["weak"]
    g = g.sort_values("bullish", ascending=False)
    return {"success": True, "sectors": g.to_dict("records")}

def _tool_get_sector_stocks(sector: str) -> Dict[str, Any]:
    df = _load_csv()
    sec_col = "sektor" if df is not None and "sektor" in df.columns else "Sektor"
    if df is None or sec_col not in df.columns:
        return {"success": False, "error": "Data sektor tidak tersedia"}
    mask = df[sec_col].astype(str).str.contains(sector, case=False, na=False)
    sub = df[mask]
    if sub.empty:
        sectors = sorted(df[sec_col].dropna().astype(str).unique().tolist())
        return {"success": False, "error": f"Sektor '{sector}' tidak ditemukan",
                "sektor_tersedia": sectors}
    score_col = "score" if "score" in df.columns else "Skor"
    sub = sub.sort_values(score_col, ascending=False)
    cols = [c for c in ["ticker", "sektor", "signal", "price", "score", "rrr"] if c in sub.columns]
    return {"success": True, "matched_sector": sub[sec_col].iloc[0],
            "count": len(sub), "stocks": _records(sub, cols, 20)}

def _tool_get_market_overview() -> Dict[str, Any]:
    """Market overview dari v2 data."""
    result = {"success": True, "total_buy_signals": 0, "strong_buy": 0, "buy": 0}
    df = _load_csv()
    if df is not None:
        sig_col = "signal" if "signal" in df.columns else "Sinyal" if "Sinyal" in df.columns else None
        if sig_col:
            result["strong_buy"] = int((df[sig_col] == "STRONG_BUY").sum())
            result["buy"] = int((df[sig_col] == "BUY").sum())
            result["weak_buy"] = int((df[sig_col] == "WEAK_BUY").sum())
            result["total_buy_signals"] = result["strong_buy"] + result["buy"] + result["weak_buy"]

    # IHSG context
    try:
        from idx_alpha_screener import data
        ihsg_df = data.fetch_ihsg_cached()
        if ihsg_df is not None and not ihsg_df.empty and len(ihsg_df) >= 2:
            close_col = 'close' if 'close' in ihsg_df.columns else 'Close'
            if close_col in ihsg_df.columns:
                result["ihsg_change_pct"] = float((ihsg_df[close_col].iloc[-1] / ihsg_df[close_col].iloc[-2] - 1) * 100)
                result["ihsg_trend"] = "BULL" if result["ihsg_change_pct"] > 0 else "BEAR"
    except Exception:
        pass

    return result

def _tool_screen_stocks(**filters) -> Dict[str, Any]:
    df = _load_csv()
    if df is None:
        return {"success": False, "error": "CSV screener tidak ditemukan"}
    d = df.copy()
    import pandas as pd

    def numcol(col):
        return pd.to_numeric(d[col], errors="coerce") if col in d.columns else None

    # Map old column names to lowercase (normalized by _load_csv)
    skor_col = "score" if "score" in d.columns else "Skor"
    conf_col = "confidence" if "confidence" in d.columns else "Confidence%"
    rrr_col = "rrr" if "rrr" in d.columns else "RRR"
    rsi_col = "rsi" if "rsi" in d.columns else "RSI"
    adx_col = "adx" if "adx" in d.columns else "ADX"
    price_col = "price" if "price" in d.columns else "Harga"
    sig_col = "signal" if "signal" in d.columns else "Sinyal"
    sec_col = "sektor" if "sektor" in d.columns else "Sektor"
    vol_spike_col = "volume_spike" if "volume_spike" in d.columns else "Volume_Spike"

    if filters.get("min_skor") is not None and skor_col in d.columns:
        d = d[numcol(skor_col) >= float(filters["min_skor"])]
    if filters.get("min_confidence") is not None and conf_col in d.columns:
        d = d[numcol(conf_col) >= float(filters["min_confidence"])]
    if filters.get("min_rrr") is not None and rrr_col in d.columns:
        d = d[numcol(rrr_col) >= float(filters["min_rrr"])]
    if filters.get("rsi_below") is not None and rsi_col in d.columns:
        d = d[numcol(rsi_col) <= float(filters["rsi_below"])]
    if filters.get("rsi_above") is not None and rsi_col in d.columns:
        d = d[numcol(rsi_col) >= float(filters["rsi_above"])]
    if filters.get("min_adx") is not None and adx_col in d.columns:
        d = d[numcol(adx_col) >= float(filters["min_adx"])]
    if filters.get("max_price") is not None and price_col in d.columns:
        d = d[numcol(price_col) <= float(filters["max_price"])]
    if filters.get("signal") and sig_col in d.columns:
        d = d[d[sig_col] == filters["signal"]]
    if filters.get("sector") and sec_col in d.columns:
        d = d[d[sec_col].astype(str).str.contains(str(filters["sector"]), case=False, na=False)]
    if filters.get("volume_spike") and vol_spike_col in d.columns:
        d = d[d[vol_spike_col].astype(str).str.upper().isin(["YES", "EXTREME", "60D_HIGH", "ELEVATED"])]

    limit = int(filters.get("limit") or 15)
    if skor_col in d.columns:
        d = d.sort_values(skor_col, ascending=False)
    cols = [c for c in ["ticker", "sektor", "signal", "price", "score", "confidence", "rsi", "adx", "rrr", "volume_spike"] if c in d.columns]
    return {"success": True, "match_count": len(d), "applied_filters": {k: v for k, v in filters.items() if v is not None},
            "stocks": _records(d, cols, limit)}

def _tool_backtest_stock(ticker: str, days: int = 90) -> Dict[str, Any]:
    import numpy as np
    days = min(365, max(7, int(days or 90)))
    try:
        from core.scraper import fetch_price_data_sync
        period = f"{days}d" if days <= 60 else f"{max(1, days // 30)}mo"
        df = fetch_price_data_sync(ticker.upper(), period=period, interval="1d", skip_cache=False)
        if df is None or df.empty:
            return {"success": False, "error": f"Data tidak tersedia untuk {ticker.upper()}"}
        close = df["Close"].astype(float)
        pct = close.pct_change().dropna()
        if len(pct) < 10:
            return {"success": False, "error": f"Data terlalu sedikit ({len(pct)} hari)"}
        wins = pct[pct > 0]
        losses = pct[pct < 0]
        cum = (1 + pct).cumprod()
        max_dd = (cum.cummax() - cum).max() * 100
        rf = 0.05
        sharpe = (pct.mean() - rf / 252) / pct.std() * np.sqrt(252) if pct.std() > 0 else 0
        return {
            "success": True, "ticker": ticker.upper(), "days": days, "trades": len(pct),
            "win_rate_pct": round(len(wins) / len(pct) * 100, 1),
            "total_return_pct": round((cum.iloc[-1] - 1) * 100, 2),
            "sharpe": round(float(sharpe), 2),
            "max_drawdown_pct": round(float(max_dd), 1),
            "avg_win_pct": round(wins.mean() * 100, 2) if len(wins) else 0,
            "avg_loss_pct": round(losses.mean() * 100, 2) if len(losses) else 0,
            "note": "Simulasi buy-and-hold, tanpa SL/TP",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def _tool_get_holders(ticker: str) -> Dict[str, Any]:
    tkr = ticker.upper().replace(".JK", "")
    df = _load_csv()
    if df is None:
        return {"success": False, "error": "CSV screener tidak ditemukan"}
    m = df[df["ticker"].astype(str).str.upper() == tkr]
    if m.empty:
        return {"success": False, "error": f"{tkr} tidak ada di data screener"}
    row = m.iloc[0]
    ts = int(float(row.get("Shares_Outstanding", 0) or 0))
    fs = int(float(row.get("Float_Shares", 0) or 0))
    ff_pct = round(fs / ts * 100, 2) if ts > 0 and fs > 0 else 0
    result = {
        "success": True, "ticker": tkr,
        "sektor": str(row.get("Sektor", "?")),
        "harga": float(row.get("Harga", 0) or 0),
        "market_cap": float(row.get("Market_Cap_IDR", 0) or 0),
        "shares_outstanding": ts, "float_shares": fs, "free_float_pct": ff_pct,
        "mm_float_pct": float(row.get("MM_Float_Pct", 0) or 0),
        "dominance": str(row.get("Dominance", "N/A")),
    }
    try:
        from shareholder_analyzer import analyze_shareholder_structure
        ksei = analyze_shareholder_structure(tkr, total_shares_outstanding=ts, track_trend=True, float_shares=fs)
        if ksei.get("status") == "ok" and ksei.get("n_holders", 0) > 0:
            result["ksei"] = {
                "mm_pct": ksei.get("mm_pct"), "retail_pct": ksei.get("retail_pct"),
                "free_float_pct": ksei.get("free_float_pct"), "mm_trend": ksei.get("mm_trend"),
                "dominance": ksei.get("dominance"),
                "top_holders": [{"name": h.get("name"), "pct": h.get("pct"),
                                 "class": h.get("classification")} for h in ksei.get("top_holders", [])[:5]],
            }
    except Exception as e:
        logger.warning("KSEI fetch gagal untuk %s: %s", tkr, e)
    return result

def _tool_get_portfolio() -> Dict[str, Any]:
    import sqlite3
    db_path = os.path.join(ROOT, "portofolio_virtual.db")
    if not os.path.exists(db_path):
        return {"success": False, "error": "Belum ada data portofolio"}
    try:
        conn = sqlite3.connect(db_path)
        cash_row = conn.execute("SELECT saldo_cash FROM akun").fetchone()
        cash = cash_row[0] if cash_row else 0
        pos = conn.execute("SELECT ticker,harga_beli,shares,sl,tp FROM posisi").fetchall()
        conn.close()
    except Exception as e:
        return {"success": False, "error": str(e)}
    initial = 10_000_000
    try:
        from telegram_bot import INITIAL_CASH
        initial = INITIAL_CASH
    except Exception:
        pass
    pos_val = sum(p[1] * p[2] for p in pos)
    equity = cash + pos_val
    pnl = equity - initial
    return {"success": True, "equity": equity, "cash": cash,
            "pnl": pnl, "pnl_pct": round(pnl / initial * 100, 2) if initial else 0,
            "open_positions": [{"ticker": p[0], "entry": p[1], "shares": p[2], "sl": p[3], "tp": p[4]} for p in pos]}

TOOL_MAP = {
    "get_stock_data": _tool_get_stock_data,
    "get_fundamentals": _tool_get_fundamentals,
    "compare_stocks": _tool_compare_stocks,
    "get_top_stocks": _tool_get_top_stocks,
    "list_signals": _tool_list_signals,
    "get_trade_setups": _tool_get_trade_setups,
    "get_sector_signals": _tool_get_sector_signals,
    "get_sector_stocks": _tool_get_sector_stocks,
    "get_market_overview": _tool_get_market_overview,
    "screen_stocks": _tool_screen_stocks,
    "backtest_stock": _tool_backtest_stock,
    "get_holders": _tool_get_holders,
    "get_portfolio": _tool_get_portfolio,
}

# --- Backend Clients -----------------------------------------------------
class LLMClient:
    def __init__(self):
        self.backend = AI_BACKEND
        self._init_client()

    def _init_client(self):
        from openai import OpenAI
        if self.backend == "openai":
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not set")
            self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            self.model = OPENAI_MODEL
        elif self.backend == "deepseek":
            if not DEEPSEEK_API_KEY:
                raise ValueError("DEEPSEEK_API_KEY not set")
            self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
            self.model = DEEPSEEK_MODEL
        elif self.backend == "ollama":
            self.client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL)
            self.model = OLLAMA_MODEL
        elif self.backend in ("opencode_zen", "opencode", "zen"):
            if not OPENCODE_ZEN_API_KEY:
                raise ValueError("OPENCODE_ZEN_API_KEY not set")
            self.client = OpenAI(api_key=OPENCODE_ZEN_API_KEY, base_url=OPENCODE_ZEN_BASE_URL)
            self.model = OPENCODE_ZEN_MODEL
        else:
            raise ValueError(f"Unknown AI_BACKEND: {AI_BACKEND}")
        logger.info("LLM client init: backend=%s model=%s", self.backend, self.model)

    def chat(self, messages: List[Dict], tools: Optional[List] = None) -> Dict:
        kwargs = {"model": self.model, "messages": messages, "temperature": 0.3}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = _call_with_retry(self.client.chat.completions.create, **kwargs)
        msg = resp.choices[0].message
        return {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
                for tc in (msg.tool_calls or [])
            ]
        }

# --- Agent Orchestrator --------------------------------------------------
class AIAgent:
    def __init__(self):
        self._client: Optional[LLMClient] = None
        self._client_tried = False
        self.history: Dict[int, List[Dict]] = {}  # chat_id -> messages

    @property
    def client(self) -> Optional[LLMClient]:
        if self._client is None and not self._client_tried:
            self._client_tried = True
            try:
                self._client = LLMClient()
            except ValueError as e:
                logger.warning(f"LLM client not available: {e}")
                self._client = None
        return self._client

    def _get_history(self, chat_id: int) -> List[Dict]:
        if chat_id not in self.history:
            self.history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return self.history[chat_id]

    def _trim_history(self, chat_id: int, max_msgs: int = 24):
        hist = self.history.get(chat_id, [])
        if len(hist) > max_msgs:
            self.history[chat_id] = [hist[0]] + hist[-(max_msgs - 1):]

    # -- Fallback tanpa LLM: jalankan tool berdasarkan keyword --
    def _tool_only_answer(self, user_text: str) -> str:
        t = user_text.lower()
        if any(k in t for k in ["sektor", "sector"]):
            res = _tool_get_sector_signals()
            if res.get("success"):
                lines = ["Sinyal per Sektor (Top 5)"]
                for s in res["sectors"][:5]:
                    lines.append(f"  {s['Sektor']}: {s['bullish']}/{s['total']} bullish")
                return "\n".join(lines)
        if any(k in t for k in ["pasar", "market", "ihsg", "breadth", "kondisi"]):
            res = _tool_get_market_overview()
            if res.get("success"):
                return (f"Kondisi Pasar\nIHSG: {res.get('ihsg_change_pct',0):+.2f}% ({res.get('ihsg_trend','?')})\n"
                        f"USD: {res.get('usd_change_pct',0):+.2f}%\n"
                        f"Breadth: {res.get('pct_above_ema50','?')}% di atas EMA50\n"
                        f"Sinyal beli: {res.get('total_buy_signals',0)}")
        if any(k in t for k in ["top", "terbaik", "rekomendasi", "bagus apa"]):
            res = _tool_get_top_stocks(5)
            if res.get("success"):
                lines = ["Top 5 Saham Hari Ini"]
                for s in res["stocks"]:
                    lines.append(f"  {s.get('ticker') or s.get('Ticker','?')} - Skor {s.get('score') or s.get('Skor',0)} | {s.get('signal') or s.get('Sinyal','?')}")
                return "\n".join(lines)
        if any(k in t for k in ["sinyal", "signal", "beli apa"]):
            res = _tool_list_signals("ALL")
            if res.get("success"):
                lines = [f"Sinyal beli - {res.get('total',0)} saham"]
                for s in (res.get("signals", {}).get("STRONG_BUY", []) + res.get("signals", {}).get("BUY", []))[:8]:
                    lines.append(f"  {s.get('ticker') or s.get('Ticker','?')} - Skor {s.get('score') or s.get('Skor',0)} | {s.get('signal') or s.get('Sinyal','?')}")
                return "\n".join(lines)
        tickers = _re.findall(r'\b([A-Z]{3,5})\b', user_text.upper())
        ignore = {"YANG", "SAHAM", "GIMANA", "BAGUS", "JELEK", "NAIK", "TURUN", "BESOK", "HARI"}
        tickers = [x for x in tickers if x not in ignore]
        if tickers:
            res = _tool_get_stock_data(tickers[0])
            if res.get("success"):
                return self._format_stock_answer(res)
            return res.get("error", "Data tidak ditemukan")
        return ("(AI sedang tidak aktif.) Coba sebut ticker (mis: BBCA), atau kata kunci: "
                "'top', 'sinyal', 'sektor', 'pasar'. Atau pakai command seperti /cek BBCA, /top 5, /sinyal.")

    def _format_stock_answer(self, d: Dict) -> str:
        emoji = {"ULTRA_BUY": "[+]", "STRONG_BUY": "[+]", "BUY": "[+]",
                 "TUNGGU": "[~]", "PANTAU": "[~]", "HINDARI": "[-]"}.get(d.get("Sinyal"), "")
        hold = d.get('Hold', '')
        hold_str = f"\n⏱️ Hold: {hold}" if hold else ""
        s = d.get('Support', 0) or 0
        r = d.get('Resistance', 0) or 0
        sr_str = f"\n🔻 Support: {int(s):,} | 🔺 Resistance: {int(r):,}" if s > 0 or r > 0 else ""
        
        # 🔴 FIX BULLISH PALSU: Tampilkan intraday direction
        session_chg = d.get("Session_Change%", 0)
        if session_chg is not None and session_chg != 0:
            arrow = "🔺" if session_chg > 0 else "🔻"
            session_str = f"\n{arrow} Sesi: {session_chg:+.2f}%"
        else:
            session_str = ""

        return (f"{emoji} {d.get('Ticker')} ({d.get('Sektor','')})\n"
                f"Harga: {d.get('Harga'):,} | Sinyal: {d.get('Sinyal')} {d.get('Strength','')}\n"
                f"Skor: {d.get('Skor')}/15 | Conf: {d.get('Confidence%')}%{session_str}\n"
                f"SL: {d.get('Stop_Loss'):,} | TP1: {d.get('Target_1'):,} | RRR: {d.get('RRR')}{sr_str}{hold_str}\n"
                f"AI: {d.get('AI_Verdict','N/A')} ({d.get('AI_Win_Prob%','?')}%)\n"
                f"RSI: {d.get('RSI')} | ADX: {d.get('ADX')} | MM: {d.get('MM_Activity')}")

    def ask(self, chat_id: int, user_text: str, user_id: int = 0) -> str:
        u_id = user_id if user_id != 0 else chat_id
        
        # 1. Onboarding & Context
        import user_prefs
        ctx = chat_memory.get_user_context(u_id, chat_id)
        is_new = ctx.get("is_new_user", False)
        last_ticker = ctx.get("last_ticker")
        last_sector = ctx.get("last_sector")

        # Load & Inject User Preferences
        prefs = user_prefs.get_ticker_prefs(u_id, chat_id)
        
        # 2. Dynamic System Prompt
        dyn_prompt = SYSTEM_PROMPT
        
        # Suntikkan Profil User agar bot bisa personalisasi
        dyn_prompt += "\n\n[PROFIL & PREFERENSI PENGGUNA]\n"
        dyn_prompt += f"- Gaya Trading Favorit: {prefs.get('mode')}\n"
        dyn_prompt += f"- Profil Risiko: {prefs.get('risk')}\n"
        dyn_prompt += f"- Tingkat Kedalaman Penjelasan (Depth): {prefs.get('depth')}\n"
        if prefs.get('fav_tickers'):
            dyn_prompt += f"- Ticker yang sering ditanyakan: {', '.join(prefs.get('fav_tickers'))}\n"
            
        if last_ticker or last_sector:
            dyn_prompt += "\n[KONTEKS OBROLAN SAAT INI]\n"
            if last_ticker:
                dyn_prompt += f"- Sedang membahas ticker: {last_ticker}\n"
            if last_sector:
                dyn_prompt += f"- Sedang membahas sektor: {last_sector}\n"
            dyn_prompt += "Gunakan konteks ini bila user menyebut 'dia', 'saham itu', 'sektor ini', dsb tanpa menyebutkan nama spesifik.\n"

        # 3. Load DB History — v2: 10 pesan + summary + web cache
        conv = chat_memory.get_conversation_for_inject(u_id, chat_id)
        db_history = conv["recent_messages"]  # 10 pesan (dari 5)
        ctx_dict = conv["context"]

        # Tambahkan summary percakapan & topik ke system prompt
        if ctx_dict.get("conversation_summary"):
            dyn_prompt += "\n\n[CATATAN PERCAKAPAN SEBELUMNYA]\n"
            dyn_prompt += ctx_dict["conversation_summary"]
            dyn_prompt += "\n\n"
        if ctx_dict.get("topics"):
            dyn_prompt += "\n[TOPIK YANG SUDAH DBAHAS]\n"
            dyn_prompt += ctx_dict["topics"]
            dyn_prompt += "\n\n"

        # Inject hasil riset web terakhir (web_search_cache)
        web_cache = chat_memory.get_web_search_cache(u_id, chat_id)
        if web_cache:
            dyn_prompt += "\n[CATATAN RISET TERAKHIR]\n"
            dyn_prompt += web_cache
            dyn_prompt += "\n\n"

        # Tambahkan riwayat ticker & sektor yang pernah ditanyakan
        stats = conv.get("stats", {})
        if stats.get("recent_tickers"):
            dyn_prompt += "[RIWAYAT TICKER USER]\n"
            dyn_prompt += "Ticker pernah ditanyakan: " + ", ".join(stats["recent_tickers"]) + "\n\n"

        # Hitung total percakapan — untuk auto-summary tiap 12 turn
        total_msgs = ctx_dict.get("message_count", 0)

        # Cek apakah saatnya auto-summary (habis kelipatan 10 user-assistant pair)
        needs_summary = total_msgs > 0 and total_msgs % 10 == 0 and not ctx_dict.get("is_new_user")

        hist = [{"role": "system", "content": dyn_prompt}]
        for msg in db_history:
            hist.append({"role": msg["role"], "content": msg["content"]})
        hist.append({"role": "user", "content": user_text})

        # --- Fallback jika AI mati ---
        if self.client is None:
            ans = self._tool_only_answer(user_text)
            chat_memory.save_message(u_id, chat_id, "user", user_text, last_ticker, last_sector)
            chat_memory.save_message(u_id, chat_id, "assistant", ans, last_ticker, last_sector)
            return ans

        # --- LLM Execution ---
        final_answer = "(kosong)"
        for _ in range(5):  # max 5 tool-call rounds
            try:
                resp = self.client.chat(hist, tools=TOOLS)
            except Exception as e:
                logger.warning(f"LLM call failed, using tool-only fallback: {e}")
                final_answer = self._tool_only_answer(user_text)
                break

            assistant_msg = {"role": "assistant", "content": resp["content"] or ""}
            if resp["tool_calls"]:
                assistant_msg["tool_calls"] = [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in resp["tool_calls"]
                ]
            hist.append(assistant_msg)

            if resp["tool_calls"]:
                for tc in resp["tool_calls"]:
                    fn_name = tc["name"]
                    
                    try:
                        args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        args = {}
                        
                    if fn_name == "update_preferences":
                        # Handle special tool manually
                        if "mode" in args: user_prefs.update_pref(u_id, chat_id, "mode", args["mode"])
                        if "depth" in args: user_prefs.update_pref(u_id, chat_id, "depth_mode", args["depth"])
                        if "risk_tolerance" in args: user_prefs.update_pref(u_id, chat_id, "risk_tolerance", args["risk_tolerance"])
                        result = {"success": True, "message": "Preferences updated successfully."}
                        
                    elif fn_name in TOOL_MAP:
                        fn = TOOL_MAP[fn_name]
                        try:
                            # Deteksi dan simpan konteks dari tool arguments
                            t_val = args.get("ticker") or args.get("tickers")
                            if t_val:
                                t_str = t_val[0].upper() if isinstance(t_val, list) and t_val else str(t_val).upper()
                                last_ticker = t_str
                                chat_memory.update_context(u_id, chat_id, ticker=last_ticker)
                                user_prefs.track_ticker_interest(u_id, chat_id, last_ticker)
                                
                            if args.get("sector"):
                                last_sector = str(args.get("sector"))
                                chat_memory.update_context(u_id, chat_id, sector=last_sector)
                                user_prefs.track_ticker_interest(u_id, chat_id, "IHSG", sector=last_sector)

                            result = fn(**args)
                        except Exception as e:
                            logger.exception("Tool %s gagal", fn_name)
                            result = {"success": False, "error": str(e)}
                    else:
                        result = {"success": False, "error": f"Unknown tool: {fn_name}"}
                    
                    hist.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False, default=str)
                    })
                continue

            final_answer = resp["content"] or "(kosong)"
            break
        else:
            # Terlalu banyak ronde tool
            try:
                hist.append({"role": "user", "content": "Rangkum jawaban final untuk user berdasarkan data di atas."})
                final = self.client.chat(hist)
                final_answer = final["content"] or self._tool_only_answer(user_text)
            except Exception:
                final_answer = self._tool_only_answer(user_text)
                
        if is_new:
            onboarding_msg = (
                "\n\n---\n"
                "👋 Hai! Aku QuantYan, teman diskusi IHSG-mu.\n"
                "Coba tanya:\n"
                "- 'Cek teknikal BBCA dong'\n"
                "- 'Bagusan BMRI atau BBNI buat invest?'\n"
                "- 'Cariin saham swing RRR > 2 yang lagi di support'\n"
                "- 'Porto saya gimana?'"
            )
            final_answer += onboarding_msg

        final_answer = _sanitize_plain(final_answer)
        chat_memory.save_message(u_id, chat_id, "user", user_text, last_ticker, last_sector)
        chat_memory.save_message(u_id, chat_id, "assistant", final_answer, last_ticker, last_sector)

        # Auto-summary tiap 10 turn — preserve inti diskusi
        if needs_summary:
            try:
                from openai import OpenAI
                # Generate summary dari riwayat terakhir
                hist_plain = "\n".join(
                    f"{m['role']}: {m['content'][:200]}"
                    for m in db_history[-6:] + [{"role": "user", "content": user_text}, {"role": "assistant", "content": final_answer}]
                )
                summary_prompt = [
                    {"role": "system", "content": "Ringkas percakapan saham ini dalam 2-3 kalimat. Fokus: ticker, sektor, analisis, keputusan utama."},
                    {"role": "user", "content": f"Ringkasan percakapan saham:\n{hist_plain}"}
                ]
                if self.client and self.client.backend == "opencode_zen":
                    # Untuk opencode_zen minimal panggil summary langsung
                    pass
                if self.client:
                    sum_resp = self.client.chat(summary_prompt)
                    summary_text = sum_resp.get("content", "")
                    if summary_text:
                        chat_memory.update_conversation_summary(u_id, chat_id, summary_text)
            except Exception as e:
                logger.warning("Auto-summary gagal: %s", e)
        
        return final_answer

# --- Singleton Instance --------------------------------------------------
_agent_instance: Optional[AIAgent] = None

def get_agent() -> AIAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = AIAgent()
    return _agent_instance

def ask_ai(chat_id: int, text: str, user_id: int = 0) -> str:
    """Synchronous entry point dipakai dari telegram_bot.py"""
    try:
        return get_agent().ask(chat_id, text, user_id)
    except Exception as e:
        logger.exception("AI agent error")
        return f"Error AI: {e}"

if __name__ == "__main__":
    print(f"AI backend: {AI_BACKEND}")
    agent = get_agent()
    print("LLM client:", "ON" if agent.client else "OFF (tool-only fallback)")
    for q in ["Pasar gimana hari ini?", "BBCA bagus ga buat swing?", "kasih top 3 saham dong"]:
        print(f"\n>>> {q}")
        print(ask_ai(99999, q))
