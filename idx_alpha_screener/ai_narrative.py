"""
ai_narrative.py — AI Narrative per sinyal swing (E1)
=====================================================
Menambahkan 1-2 kalimat analisis naratif BERBASIS DATA untuk top 3 sinyal
swing terbaik dari scan v7, ditampilkan di pesan Telegram.

PRINSIP KRITIS:
- Narrative = KONTEKS tambahan, BUKAN prediksi harga, BUKAN rekomendasi beli/jual.
- TIDAK PERNAH memblokir cron scan: setiap kegagalan (API timeout / rate-limit /
  key tidak ada / error lain) → return {} dan log warning. Scan tetap jalan normal.
- Hanya pakai backend LLM MURAH: DeepSeek (deepseek-chat) atau OpenCodeZen
  (model dari .env: MODEL / OPENCODE_ZEN_MODEL). TIDAK memakai model mahal.
- API key TIDAK di-hardcode — dibaca dari .env (folder root repo), reuse
  variabel env yang sama dengan ai_agent.py.
- 1 percobaan, timeout 15 detik.
"""
import os
import logging
from typing import Dict, List, Optional

from dotenv import load_dotenv

# Root repo = parent dari folder idx_alpha_screener (tempat file ini)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

logger = logging.getLogger("ai_narrative")

NARRATIVE_TIMEOUT = 15          # detik — 1 percobaan
MAX_SIGNALS = 3                 # top 3 sinyal swing
MAX_TOKENS = 160

# ── Backend murah: prefer DeepSeek, fallback OpenCodeZen ────────────────────
def _pick_backend() -> Optional[dict]:
    """Pilih backend LLM murah dari .env. Return None kalau tidak ada key sama sekali."""
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        return {
            "name": "deepseek",
            "api_key": deepseek_key,
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        }
    zen_key = os.getenv("OPENCODE_ZEN_API_KEY")
    if zen_key:
        return {
            "name": "opencode_zen",
            "api_key": zen_key,
            "base_url": os.getenv("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/zen/v1"),
            "model": os.getenv("OPENCODE_ZEN_MODEL") or os.getenv("MODEL", "deepseek-v4-flash-free"),
        }
    return None


def _fmt_flow(raw: str) -> str:
    """Normalisasi string broker/foreign detail agar terbaca prompt."""
    if not raw:
        return "tidak ada data"
    s = str(raw).strip()
    return s.replace("_", " ")


def _build_prompt(signal: dict, sentiment_ihsg: dict) -> List[Dict[str, str]]:
    """Susun prompt HANYA dari data yang sudah ada di sinyal + sentiment IHSG."""
    tkr = signal.get("tkr", "?")
    score = signal.get("score", 0)
    weekly = signal.get("weekly", "NO_DATA")
    rsi = signal.get("rsi", 0)
    group = signal.get("group", "") or "tidak ada"
    bf = _fmt_flow(signal.get("bf", ""))
    ff = _fmt_flow(signal.get("ff", ""))

    sent_label = sentiment_ihsg.get("label") or sentiment_ihsg.get("sentiment", "N/A")
    sent_reason = (sentiment_ihsg.get("reason") or "").strip()
    sent_details = sentiment_ihsg.get("details") or []
    sent_detail_str = "; ".join(str(d) for d in sent_details[:3])

    data_block = (
        f"Ticker: {tkr}\n"
        f"Score: {score}\n"
        f"Weekly trend: {weekly}\n"
        f"RSI: {rsi}\n"
        f"Grup konglomerat: {group}\n"
        f"Broker flow: {bf}\n"
        f"Foreign flow: {ff}\n"
        f"Sentimen IHSG: {sent_label} — {sent_reason}\n"
        f"Detail sentimen IHSG: {sent_detail_str}"
    )

    system = (
        "Kamu adalah asisten analisis data pasar saham Indonesia yang NETRAL dan HATI-HATI. "
        "Kamu hanya mendeskripsikan FAKTA dari data yang diberikan, tanpa interpretasi berlebihan."
    )
    user = (
        "Buat 1-2 kalimat narasi singkat dalam Bahasa Indonesia yang merangkum KONTEKS "
        "data sinyal swing berikut.\n\n"
        "INSTRUKSI KETAT:\n"
        "- Deskripsikan fakta data ini secara netral.\n"
        "- JANGAN memprediksi harga.\n"
        "- JANGAN menyarankan beli/jual.\n"
        "- JANGAN klaim kepastian.\n"
        "- Maksimal 2 kalimat.\n"
        "- Jangan sebutkan kata 'sinyal beli' / 'rekomendasi' / 'target harga'.\n"
        "- Output langsung kalimat narasi, tanpa label, tanpa markdown, tanpa emoji.\n\n"
        f"DATA:\n{data_block}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _call_llm_once(backend: dict, messages: List[Dict[str, str]]) -> Optional[str]:
    """
    Satu panggilan LLM (1 percobaan, timeout 15 detik).
    Return teks jawaban, atau None jika gagal — TIDAK PERNAH melempar exception.
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=backend["api_key"],
            base_url=backend["base_url"],
            timeout=NARRATIVE_TIMEOUT,
        )
        resp = client.chat.completions.create(
            model=backend["model"],
            messages=messages,
            temperature=0.3,
            max_tokens=MAX_TOKENS,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:
        logger.warning("ai_narrative: LLM call gagal (%s/%s): %s",
                       backend.get("name"), backend.get("model"), e)
        return None


def generate_narratives(top_signals: list, sentiment_ihsg: dict) -> Dict[str, str]:
    """
    Generate narasi untuk maks 3 sinyal swing terbaik.

    Parameters
    ----------
    top_signals : list[dict] — sinyal swing terurut score desc (top N diambil di sini).
        Setiap dict minimal: tkr, score, weekly, rsi, bf, ff, group.
    sentiment_ihsg : dict — output predict_market_sentiment() (label, reason, details, ...).

    Returns
    -------
    dict[str, str] — {ticker: kalimat_naratif}. KOSONG jika gagal / tidak ada key.
    """
    if not top_signals:
        return {}

    backend = _pick_backend()
    if backend is None:
        logger.warning(
            "ai_narrative: tidak ada API key DeepSeek/OpenCodeZen di .env — "
            "narrative dilewati, scan tetap normal"
        )
        return {}

    logger.info("ai_narrative: backend=%s model=%s untuk %d sinyal",
                backend["name"], backend["model"], min(len(top_signals), MAX_SIGNALS))

    narratives: Dict[str, str] = {}
    for sig in top_signals[:MAX_SIGNALS]:
        tkr = str(sig.get("tkr", "")).upper()
        if not tkr:
            continue
        try:
            messages = _build_prompt(sig, sentiment_ihsg or {})
            text = _call_llm_once(backend, messages)
            if text:
                narratives[tkr] = text
            else:
                logger.warning("ai_narrative: tidak ada jawaban untuk %s — dilewati", tkr)
        except Exception as e:
            # Safety net: SATU ticker gagal → lewati ticker itu, lanjut ticker lain.
            logger.warning("ai_narrative: gagal untuk %s: %s — dilewati", tkr, e)
            continue

    if not narratives:
        logger.warning("ai_narrative: tidak ada narrative dihasilkan — scan tetap normal")
    return narratives
