#!/usr/bin/env python3
"""
ai_agent.py — Multi-backend LLM Agent untuk Telegram Bot Screener
Backends: OpenAI, DeepSeek (OpenAI-compatible), Ollama (OpenAI-compatible)
Synchronous interface, tool calling, retry logic, fallback ke data screener.
"""
import os
import sys
import json
import time
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(ROOT, ".env"))

sys.path.insert(0, ROOT)

logger = logging.getLogger("ai_agent")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] ai_agent: %(message)s")

# ─── Config ──────────────────────────────────────────────────────────────
AI_BACKEND = os.getenv("AI_BACKEND", "openai").lower()  # openai | deepseek | ollama

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

# Retry config
MAX_RETRIES = 3
BASE_DELAY = 2  # seconds

# ─── Tool Definitions ────────────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_stock_data",
            "description": "Ambil data lengkap saham IHSG: skor, sinyal, SL/TP, AI verdict, dll.",
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
            "name": "get_sector_signals",
            "description": "Ambil ringkasan sinyal per sektor hari ini.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_breadth",
            "description": "Ambil kondisi pasar: IHSG change, foreign flow, breadth, dll.",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

SYSTEM_PROMPT = """Kamu adalah analis saham IHSG profesional yang menjawab pertanyaan user di Telegram.
Kamu HARUS memakai tools yang tersedia untuk mengambil data real-time sebelum menjawab.
JANGAN mengarang data. Kalau tool gagal, bilang jujur "gagal ambil data".

Format jawaban:
- Ringkas, bahasa Indonesia natural
- Sertakan angka kunci (harga, skor, sinyal, SL/TP, RRR)
- Jangan terlalu panjang, user di Telegram

Tools tersedia:
1. get_stock_data(ticker) → data lengkap 1 saham
2. get_sector_signals() → ringkasan per sektor
3. get_market_breadth() → kondisi pasar umum

Contoh alur:
User: "BBCA naik ga besok?"
→ Panggil get_stock_data("BBCA")
→ Jawab pakai data tool: sinyal, skor, AI verdict, SL/TP

User: "Sektor apa yg kuat hari ini?"
→ Panggil get_sector_signals()
→ Jawab ranking sektor

User: "Pasar gimana hari ini?"
→ Panggil get_market_breadth()
→ Jawab IHSG, foreign flow, breadth
"""

# ─── Helper: Retry dengan Backoff ───────────────────────────────────────
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

# ─── Tool Implementations ───────────────────────────────────────────────
def _tool_get_stock_data(ticker: str) -> Dict[str, Any]:
    from telegram_bot import _lookup_ticker_live
    data = _lookup_ticker_live(ticker.upper())
    if not data or data.get("_error"):
        return {"success": False, "error": data.get("_error", "Data tidak ditemukan")}
    return {
        "success": True,
        "ticker": data.get("Ticker"),
        "harga": data.get("Harga"),
        "sinyal": data.get("Sinyal"),
        "strength": data.get("Strength"),
        "skor": data.get("Skor"),
        "confidence": data.get("Confidence%"),
        "sl": data.get("Stop_Loss"),
        "tp1": data.get("Target_1"),
        "tp2": data.get("Target_2"),
        "rrr": data.get("RRR"),
        "ai_verdict": data.get("AI_Verdict"),
        "ai_win_prob": data.get("AI_Win_Prob%"),
        "rsi": data.get("RSI"),
        "adx": data.get("ADX"),
        "pattern": data.get("Pattern"),
        "mm_activity": data.get("MM_Activity"),
        "mm_confidence": data.get("MM_Confidence"),
        "sector": data.get("Sektor"),
    }

def _tool_get_sector_signals() -> Dict[str, Any]:
    import pandas as pd
    import glob, os
    csv_files = sorted(glob.glob(os.path.join(ROOT, "screener_ihsg_*.csv")) + \
                       glob.glob(os.path.join(ROOT, "Data Screener", "screener_ihsg_*.csv")))
    if not csv_files:
        return {"success": False, "error": "CSV screener tidak ditemukan"}
    df = pd.read_csv(csv_files[-1])
    sector_order = df.groupby("Sektor").agg(
        total=("Ticker", "count"),
        ultra=("Sinyal", lambda x: (x=="ULTRA_BUY").sum()),
        strong=("Sinyal", lambda x: (x=="STRONG_BUY").sum()),
        buy=("Sinyal", lambda x: (x=="BUY").sum()),
    ).reset_index()
    sector_order["bullish"] = sector_order["ultra"] + sector_order["strong"] + sector_order["buy"]
    sector_order = sector_order.sort_values("bullish", ascending=False)
    return {
        "success": True,
        "sectors": sector_order[["Sektor","total","bullish","ultra","strong","buy"]].to_dict("records")
    }

def _tool_get_market_breadth() -> Dict[str, Any]:
    from telegram_bot import _compute_market_breadth
    breadth = _compute_market_breadth()
    if not breadth:
        return {"success": False, "error": "Gagal hitung breadth"}
    return {"success": True, "breadth": breadth}

TOOL_MAP = {
    "get_stock_data": _tool_get_stock_data,
    "get_sector_signals": _tool_get_sector_signals,
    "get_market_breadth": _tool_get_market_breadth,
}

# ─── Helper: Retry dengan Backoff ───────────────────────────────────────
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

# ─── Backend Clients ────────────────────────────────────────────────────
class LLMClient:
    def __init__(self):
        self.backend = AI_BACKEND
        self._init_client()

    def _init_client(self):
        if self.backend == "openai":
            from openai import OpenAI
            if not OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY not set")
            self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            self.model = OPENAI_MODEL
        elif self.backend == "deepseek":
            from openai import OpenAI
            if not DEEPSEEK_API_KEY:
                raise ValueError("DEEPSEEK_API_KEY not set")
            self.client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
            self.model = DEEPSEEK_MODEL
        elif self.backend == "ollama":
            from openai import OpenAI
            self.client = OpenAI(api_key="ollama", base_url=OLLAMA_BASE_URL)
            self.model = OLLAMA_MODEL
        else:
            raise ValueError(f"Unknown AI_BACKEND: {AI_BACKEND}")

    def chat(self, messages: List[Dict], tools: Optional[List] = None) -> Dict:
        """Return {'role': 'assistant', 'content': ..., 'tool_calls': [...]}"""
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
        }
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

# ─── Agent Orchestrator ─────────────────────────────────────────────────
class AIAgent:
    def __init__(self):
        self._client: Optional[LLMClient] = None
        self.history: Dict[int, List[Dict]] = {}  # chat_id -> messages

    @property
    def client(self) -> Optional[LLMClient]:
        if self._client is None:
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

    def _trim_history(self, chat_id: int, max_msgs: int = 20):
        hist = self.history.get(chat_id, [])
        if len(hist) > max_msgs:
            self.history[chat_id] = [hist[0]] + hist[-(max_msgs-1):]

    def _tool_only_answer(self, user_text: str) -> str:
        """Fallback: run tools directly based on keywords, format answer without LLM."""
        text_lower = user_text.lower()
        if any(k in user_text.lower() for k in ["sektor", "sector"]):
            res = _tool_get_sector_signals()
            if res.get("success"):
                lines = ["📊 **Sinyal per Sektor (Top 5)**"]
                for s in res["sectors"][:5]:
                    lines.append(f"  {s['Sektor']}: {s['bullish']}/{s['total']} bullish ({s['ultra']}UB {s['strong']}SB {s['buy']}B)")
                return "\n".join(lines)
        if any(k in user_text.lower() for k in ["pasar", "market", "ihsg", "breadth", "kondisi"]):
            res = _tool_get_market_breadth()
            if res.get("success"):
                b = res["breadth"]
                return (f"📈 **Kondisi Pasar**\n"
                        f"IHSG: {b.get('ihsg_change',0):+.2f}%\n"
                        f"Foreign: {b.get('foreign_net',0):+,} lot\n"
                        f"Advance/Decline: {b.get('advance',0)}/{b.get('decline',0)}")
        import re
        tickers = re.findall(r'\b([A-Z]{3,5})\b', user_text.upper())
        if tickers:
            ticker = tickers[0]
            res = _tool_get_stock_data(ticker)
            if res.get("success"):
                return self._format_stock_answer(res)
        return "(Maaf, AI tidak tersedia & tidak ketemu data terkait. Coba ketik nama ticker lengkap, mis: BBCA)"

    def _format_stock_answer(self, d: Dict) -> str:
        emoji = {"ULTRA_BUY":"🟢","STRONG_BUY":"🟢","BUY":"🟢","TUNGGU":"🟡","PANTAU":"🟡","HINDARI":"🔴"}.get(d.get("sinyal"),"")
        return (f"{emoji} **{d['ticker']}** ({d.get('sector','')})\n"
                f"Harga: {d['harga']:,} | Sinyal: **{d['sinyal']}** {d.get('strength','')}\n"
                f"Skor: {d['skor']}/15 | Conf: {d['confidence']}%\n"
                f"SL: {d['sl']:,} | TP1: {d['tp1']:,} | RRR: {d['rrr']}\n"
                f"AI: {d.get('ai_verdict','N/A')} ({d.get('ai_win_prob','?')}%)\n"
                f"RSI: {d.get('rsi')} | ADX: {d.get('adx')} | MM: {d.get('mm_activity')} ({d.get('mm_confidence')}%)")

    def _get_history(self, chat_id: int) -> List[Dict]:
        if chat_id not in self.history:
            self.history[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
        return self.history[chat_id]

    def _trim_history(self, chat_id: int, max_msgs: int = 20):
        hist = self.history.get(chat_id, [])
        if len(hist) > max_msgs:
            self.history[chat_id] = [hist[0]] + hist[-(max_msgs-1):]

    def ask(self, chat_id: int, user_text: str) -> str:
        hist = self._get_history(chat_id)
        hist.append({"role": "user", "content": user_text})

        # If no LLM client available, use tool-only fallback immediately
        if self._client is None:
            return self._tool_only_answer(user_text)

        for _ in range(3):  # max 3 tool-call rounds
            try:
                resp = self.client.chat(hist, tools=TOOLS)
            except Exception as e:
                logger.warning(f"LLM call failed, using tool-only fallback: {e}")
                return self._tool_only_answer(user_text)

            hist.append({"role": "assistant", "content": resp["content"] or ""})
            if resp["tool_calls"]:
                for tc in resp["tool_calls"]:
                    fn = TOOL_MAP.get(tc["name"])
                    if fn:
                        try:
                            args = json.loads(tc["arguments"])
                            result = fn(**args)
                        except Exception as e:
                            result = {"success": False, "error": str(e)}
                    else:
                        result = {"success": False, "error": f"Unknown tool: {tc['name']}"}
                    hist.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False)
                    })
                continue
            answer = resp["content"] or "(kosong)"
            hist.append({"role": "assistant", "content": answer})
            self._trim_history(chat_id)
            return answer

        return self._tool_only_answer(user_text)

# ─── Singleton Instance ─────────────────────────────────────────────────
_agent_instance: Optional[AIAgent] = None

def get_agent() -> AIAgent:
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = AIAgent()
    return _agent_instance

def ask_ai(chat_id: int, text: str) -> str:
    """Synchronous entry point dipakai dari telegram_bot.py"""
    try:
        return get_agent().ask(chat_id, text)
    except Exception as e:
        logger.exception("AI agent error")
        return f"⚠️ Error AI: {e}"

if __name__ == "__main__":
    # Quick test dengan dummy chat_id
    print("Testing AI agent (no API key, will use tool-only fallback)...")
    try:
        ans = ask_ai(12345, "BBCA naik ga besok?")
        print("Answer:", ans)
    except Exception as e:
        print(f"Expected error (no API key): {e}")