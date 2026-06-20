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
            "description": "Analisis LIVE lengkap 1 saham IHSG: harga, % change, sinyal, skor, "
                           "confidence, SL/TP, RRR, RSI, ADX, MACD, pattern, support/resistance, "
                           "volume spike, MM activity, AI verdict & win prob, trend mingguan/bulanan, "
                           "regime, PE, PBV, market cap. Pakai ini untuk pertanyaan tentang SATU emiten "
                           "(naik/turun, bagus/jelek, entry/exit, kapan beli, dll).",
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
]

SYSTEM_PROMPT = """Kamu adalah QuantYan, asisten analis saham IHSG (Bursa Efek Indonesia) yang ramah, sabar, dan cerdas.
Kamu ngobrol di Telegram dengan trader & investor ritel Indonesia, dari yang pemula sampai yang sudah jago.
Karaktermu: teknis dan paham data, tapi pintar menyederhanakan istilah rumit supaya pemula pun ngerti.
Bahasamu Indonesia yang natural, hangat, dan kasual (seperti sesama teman yang nemenin belajar saham), bukan kaku formal.

Lewat tools, kamu punya akses ke data real-time dan hasil screening: analisis 1 emiten, fundamental, top picks,
daftar sinyal, setup swing/scalp, rotasi sektor, kondisi pasar, screening custom, backtest, struktur pemegang saham,
dan portofolio user. Kamu juga bisa menjawab pertanyaan edukatif umum dari pengetahuanmu sendiri.

ATURAN EKSEKUSI DATA:
- Untuk pertanyaan yang butuh data (harga, sinyal, fundamental, top, perbandingan, screening, backtest, holders,
  portofolio, kondisi pasar), SELALU panggil tool dulu. JANGAN PERNAH mengarang atau menebak angka.
- Pahami maksud user walau bahasanya santai/tidak baku, lalu pilih tool yang paling tepat dengan parameter yang benar.
  Contoh: "saham perbankan yang bagus?" -> get_sector_stocks(sector="bank") atau screen_stocks(sector="bank").
  "BBCA gimana?" -> get_stock_data(ticker="BBCA"). "bandingin BBCA sama BBRI" -> compare_stocks(["BBCA","BBRI"]).
  "backtest BBCA 90 hari" -> backtest_stock(ticker="BBCA", days=90). "cari saham swing RRR > 3" -> screen_stocks(min_rrr=3).
- Boleh memanggil beberapa tool kalau perlu (mis. cek 1 saham + kondisi pasar sekalian).
- KALAU RAGU soal maksud user atau ticker yang dimaksud ambigu, JANGAN langsung asal pilih — tanya klarifikasi singkat dulu.
- Kalau tool gagal / data kosong, katakan jujur dan beri saran (mis. cek ejaan ticker, atau ticker belum masuk screener).
- Terjemahkan hasil tool ke bahasa natural yang enak dibaca. JANGAN nge-dump data mentah / JSON / list panjang apa adanya;
  rangkum yang penting, beri interpretasi (bagus/jelek, kenapa), pandangan pro/kontra bila relevan.

EDUKASI:
- Untuk pertanyaan umum/edukasi (apa itu RSI, cara baca MACD, beda saham vs reksadana, istilah trading), jawab langsung
  dari pengetahuanmu TANPA tool — akurat, ringkas, dan dibuat mudah dipahami pemula dengan analogi sederhana bila perlu.

GAYA & FORMAT JAWABAN (WAJIB DIPATUHI):
- Ringkas dan to the point (user baca di layar HP kecil). Sertakan angka kunci yang relevan (harga, skor, sinyal,
  SL/TP, RRR, RSI, PE, PBV, dll) saat membahas emiten.
- Output HARUS plain text murni. DILARANG memakai:
  * Bold/italic: tanda bintang ** * atau garis bawah _ untuk menebalkan/memiringkan.
  * Heading: tanda pagar # atau ###.
  * Backtick ` untuk membungkus kata/kode.
  * Tabel Markdown (pipa |).
- Untuk struktur, cukup pakai emoji secukupnya + tanda dash (-) untuk poin + baris baru. Jangan berlebihan emoji.
- Format harga Rupiah pakai titik ribuan, mis: Rp6.225 atau Rp9.850.

PERSONA & INTERAKSI:
- Sesekali kasih saran balik atau pertanyaan lanjutan biar obrolan mengalir (mis: "Mau sekalian cek fundamentalnya?",
  "Mau dibandingin sama BBRI?", "Enak buat swing apa scalp nih?") — tapi jangan dipaksakan di tiap jawaban.
- Beri disclaimer wajar dan santai saat memberi rekomendasi/analisis beli-jual (mis: "DYOR ya", "tetap kelola risiko").
  Ini alat bantu analisis, BUKAN ajakan jual/beli. Jangan tempel disclaimer panjang di tiap pesan — secukupnya saja.
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
    files = sorted(glob.glob(os.path.join(ROOT, "screener_ihsg_*.csv")) +
                   glob.glob(os.path.join(ROOT, "Data Screener", "screener_ihsg_*.csv")))
    return files[-1] if files else None

def _load_csv():
    import pandas as pd
    path = _latest_csv_path()
    if not path:
        return None
    try:
        return pd.read_csv(path)
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
    from telegram_bot import _lookup_ticker_live
    data = _lookup_ticker_live(ticker.upper())
    if not data or data.get("_error"):
        return {"success": False, "error": (data or {}).get("_error", "Data tidak ditemukan")}
    keep = ["Ticker", "Harga", "Change_pct", "Sinyal", "Strength", "Skor", "Confidence%",
            "Tech_Score", "Fund_Score", "RS_Score", "RSI", "ADX", "MACD", "BB_Width%",
            "Pattern", "Divergence", "Support", "Resistance", "Stop_Loss", "Target_1",
            "Target_2", "Target_3", "RRR", "ATR", "Vol_Ratio", "Vol_Spike",
            "Weekly_Trend", "Monthly_Trend", "Regime", "Hold", "Hold_Mode",
            "MM_Activity", "MM_Confidence", "AI_Verdict", "AI_Win_Prob%",
            "IHSG_Change", "IHSG_Trend", "ARB_Warning", "PE", "PBV", "MarketCap", "Sektor"]
    return {"success": True, **{k: data.get(k) for k in keep if k in data}}

def _tool_get_fundamentals(ticker: str) -> Dict[str, Any]:
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

def _tool_compare_stocks(tickers: List[str]) -> Dict[str, Any]:
    from telegram_bot import _lookup_ticker_live, _search_ticker
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

def _tool_get_top_stocks(n: int = 5, sort_by: str = "Skor") -> Dict[str, Any]:
    df = _load_csv()
    if df is None:
        return {"success": False, "error": "CSV screener tidak ditemukan"}
    n = min(20, max(1, int(n or 5)))
    if sort_by not in df.columns:
        sort_by = "Skor"
    top = df.sort_values(sort_by, ascending=False)
    cols = ["Ticker", "Sektor", "Sinyal", "Harga", "Skor", "Confidence%", "RRR", "AI_Verdict", "AI_Win_Prob%"]
    return {"success": True, "sort_by": sort_by, "stocks": _records(top, cols, n)}

def _tool_list_signals(signal_type: str = "ALL") -> Dict[str, Any]:
    df = _load_csv()
    if df is None or "Sinyal" not in df.columns:
        return {"success": False, "error": "Data sinyal tidak tersedia"}
    cols = ["Ticker", "Sektor", "Harga", "Skor", "Confidence%", "RRR", "AI_Verdict"]
    if signal_type and signal_type != "ALL":
        sub = df[df["Sinyal"] == signal_type]
        return {"success": True, "signal_type": signal_type, "count": len(sub),
                "stocks": _records(sub.sort_values("Skor", ascending=False), cols, 25)}
    result = {}
    for s in ["ULTRA_BUY", "STRONG_BUY", "BUY"]:
        sub = df[df["Sinyal"] == s].sort_values("Skor", ascending=False)
        result[s] = _records(sub, cols, 12)
    total = sum(len(v) for v in result.values())
    return {"success": True, "total": total, "signals": result}

def _tool_get_trade_setups(mode: str) -> Dict[str, Any]:
    from telegram_bot import _is_scalp_csv
    df = _load_csv()
    if df is None or "Sinyal" not in df.columns:
        return {"success": False, "error": "Data tidak tersedia"}
    buys = df[df["Sinyal"].isin(["ULTRA_BUY", "STRONG_BUY", "BUY"])].copy()
    setups = []
    for _, r in buys.iterrows():
        rec = r.to_dict()
        is_scalp = _is_scalp_csv(rec)
        if (mode == "scalp" and is_scalp) or (mode == "swing" and not is_scalp):
            setups.append({
                "ticker": rec.get("Ticker"), "sinyal": rec.get("Sinyal"),
                "harga": rec.get("Harga"), "skor": rec.get("Skor"),
                "rrr": rec.get("RRR"), "adx": rec.get("ADX"),
                "stop_loss": rec.get("Stop_Loss"), "target_1": rec.get("Target_1"),
                "ai_verdict": rec.get("AI_Verdict"),
            })
    setups.sort(key=lambda x: (x.get("skor") or 0), reverse=True)
    return {"success": True, "mode": mode, "count": len(setups), "setups": setups[:15]}

def _tool_get_sector_signals() -> Dict[str, Any]:
    df = _load_csv()
    if df is None or "Sektor" not in df.columns or "Sinyal" not in df.columns:
        return {"success": False, "error": "Data sektor tidak tersedia"}
    g = df.groupby("Sektor").agg(
        total=("Ticker", "count"),
        ultra=("Sinyal", lambda x: (x == "ULTRA_BUY").sum()),
        strong=("Sinyal", lambda x: (x == "STRONG_BUY").sum()),
        buy=("Sinyal", lambda x: (x == "BUY").sum()),
    ).reset_index()
    g["bullish"] = g["ultra"] + g["strong"] + g["buy"]
    g = g.sort_values("bullish", ascending=False)
    return {"success": True, "sectors": g.to_dict("records")}

def _tool_get_sector_stocks(sector: str) -> Dict[str, Any]:
    df = _load_csv()
    if df is None or "Sektor" not in df.columns:
        return {"success": False, "error": "Data sektor tidak tersedia"}
    mask = df["Sektor"].astype(str).str.contains(sector, case=False, na=False)
    sub = df[mask]
    if sub.empty:
        sectors = sorted(df["Sektor"].dropna().astype(str).unique().tolist())
        return {"success": False, "error": f"Sektor '{sector}' tidak ditemukan",
                "sektor_tersedia": sectors}
    cols = ["Ticker", "Sektor", "Sinyal", "Harga", "Skor", "Confidence%", "RRR"]
    sub = sub.sort_values("Skor", ascending=False)
    return {"success": True, "matched_sector": sub["Sektor"].iloc[0],
            "count": len(sub), "stocks": _records(sub, cols, 20)}

def _tool_get_market_overview() -> Dict[str, Any]:
    from telegram_bot import _compute_market_breadth, _fetch_ihsg_change_cached, _fetch_usd_change_cached, _get_signals
    breadth = _compute_market_breadth()
    ihsg_c, ihsg_t = _fetch_ihsg_change_cached()
    try:
        usd = _fetch_usd_change_cached()
    except Exception:
        usd = 0.0
    s = _get_signals()
    return {"success": True,
            "ihsg_change_pct": ihsg_c, "ihsg_trend": ihsg_t,
            "usd_change_pct": usd,
            "pct_above_ema50": breadth.get("pct_above_ema50"),
            "breadth_total": breadth.get("total"),
            "total_buy_signals": s.get("total"),
            "ultra_buy": len(s.get("ultra", [])),
            "strong_buy": len(s.get("strong", [])),
            "buy": len(s.get("buy", []))}

def _tool_screen_stocks(**filters) -> Dict[str, Any]:
    df = _load_csv()
    if df is None:
        return {"success": False, "error": "CSV screener tidak ditemukan"}
    d = df.copy()
    import pandas as pd

    def numcol(col):
        return pd.to_numeric(d[col], errors="coerce") if col in d.columns else None

    if filters.get("min_skor") is not None and "Skor" in d.columns:
        d = d[numcol("Skor") >= float(filters["min_skor"])]
    if filters.get("min_confidence") is not None and "Confidence%" in d.columns:
        d = d[numcol("Confidence%") >= float(filters["min_confidence"])]
    if filters.get("min_rrr") is not None and "RRR" in d.columns:
        d = d[numcol("RRR") >= float(filters["min_rrr"])]
    if filters.get("rsi_below") is not None and "RSI" in d.columns:
        d = d[numcol("RSI") <= float(filters["rsi_below"])]
    if filters.get("rsi_above") is not None and "RSI" in d.columns:
        d = d[numcol("RSI") >= float(filters["rsi_above"])]
    if filters.get("min_adx") is not None and "ADX" in d.columns:
        d = d[numcol("ADX") >= float(filters["min_adx"])]
    if filters.get("max_price") is not None and "Harga" in d.columns:
        d = d[numcol("Harga") <= float(filters["max_price"])]
    if filters.get("signal") and "Sinyal" in d.columns:
        d = d[d["Sinyal"] == filters["signal"]]
    if filters.get("sector") and "Sektor" in d.columns:
        d = d[d["Sektor"].astype(str).str.contains(str(filters["sector"]), case=False, na=False)]
    if filters.get("volume_spike") and "Volume_Spike" in d.columns:
        d = d[d["Volume_Spike"].astype(str).str.upper().isin(["YES", "EXTREME", "60D_HIGH", "ELEVATED"])]

    limit = int(filters.get("limit") or 15)
    if "Skor" in d.columns:
        d = d.sort_values("Skor", ascending=False)
    cols = ["Ticker", "Sektor", "Sinyal", "Harga", "Skor", "Confidence%", "RSI", "ADX", "RRR", "Volume_Spike"]
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
    m = df[df["Ticker"].astype(str).str.upper() == tkr]
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
                    lines.append(f"  {s.get('Ticker')} - Rp{int(s.get('Harga',0)):,} | Skor {s.get('Skor')} | {s.get('Sinyal')}")
                return "\n".join(lines)
        if any(k in t for k in ["sinyal", "signal", "beli apa"]):
            res = _tool_list_signals("ALL")
            if res.get("success"):
                lines = [f"Sinyal beli - {res.get('total',0)} saham"]
                for s in (res.get("signals", {}).get("ULTRA_BUY", []) + res.get("signals", {}).get("STRONG_BUY", []))[:8]:
                    lines.append(f"  {s.get('Ticker')} - Rp{int(s.get('Harga',0)):,} | RRR{s.get('RRR')}")
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
        return (f"{emoji} {d.get('Ticker')} ({d.get('Sektor','')})\n"
                f"Harga: {d.get('Harga'):,} | Sinyal: {d.get('Sinyal')} {d.get('Strength','')}\n"
                f"Skor: {d.get('Skor')}/15 | Conf: {d.get('Confidence%')}%\n"
                f"SL: {d.get('Stop_Loss'):,} | TP1: {d.get('Target_1'):,} | RRR: {d.get('RRR')}{sr_str}{hold_str}\n"
                f"AI: {d.get('AI_Verdict','N/A')} ({d.get('AI_Win_Prob%','?')}%)\n"
                f"RSI: {d.get('RSI')} | ADX: {d.get('ADX')} | MM: {d.get('MM_Activity')}")

    def ask(self, chat_id: int, user_text: str, user_id: int = 0) -> str:
        u_id = user_id if user_id != 0 else chat_id
        
        # 1. Onboarding Check & Get Context
        ctx = chat_memory.get_user_context(u_id, chat_id)
        is_new = ctx.get("is_new_user", False)
        last_ticker = ctx.get("last_ticker")
        last_sector = ctx.get("last_sector")

        # 2. Dynamic System Prompt
        dyn_prompt = SYSTEM_PROMPT
        if last_ticker or last_sector:
            dyn_prompt += "\n\n[KONTEKS SAAT INI]\n"
            if last_ticker:
                dyn_prompt += f"- Ticker terakhir yang dibahas: {last_ticker}\n"
            if last_sector:
                dyn_prompt += f"- Sektor terakhir yang dibahas: {last_sector}\n"
            dyn_prompt += "Gunakan konteks ini bila user menyebut 'dia', 'saham itu', 'sektor ini', dsb tanpa menyebutkan nama spesifik."

        # 3. Load DB History
        db_history = chat_memory.get_recent_messages(u_id, chat_id, limit=5)
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
                    fn = TOOL_MAP.get(tc["name"])
                    if fn:
                        try:
                            args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            
                            # Deteksi dan simpan konteks dari tool arguments
                            t_val = args.get("ticker") or args.get("tickers")
                            if t_val:
                                t_str = t_val[0].upper() if isinstance(t_val, list) and t_val else str(t_val).upper()
                                last_ticker = t_str
                                chat_memory.update_context(u_id, chat_id, ticker=last_ticker)
                                
                            if args.get("sector"):
                                last_sector = str(args.get("sector"))
                                chat_memory.update_context(u_id, chat_id, sector=last_sector)

                            result = fn(**args)
                        except Exception as e:
                            logger.exception("Tool %s gagal", tc["name"])
                            result = {"success": False, "error": str(e)}
                    else:
                        result = {"success": False, "error": f"Unknown tool: {tc['name']}"}
                    
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
                "👋 Hai! Aku QuantYan, asisten AI-mu. Coba tanya:\n"
                "- 'Cek saham BBCA dong'\n"
                "- 'Fundamental BBRI vs BMRI bagus mana?'\n"
                "- 'Top 3 saham buat swing hari ini'\n"
                "- 'Apa itu RSI?'"
            )
            final_answer += onboarding_msg

        final_answer = _sanitize_plain(final_answer)
        chat_memory.save_message(u_id, chat_id, "user", user_text, last_ticker, last_sector)
        chat_memory.save_message(u_id, chat_id, "assistant", final_answer, last_ticker, last_sector)
        
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
