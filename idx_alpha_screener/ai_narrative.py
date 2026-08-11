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
- Fallback backend berjenjang (maks 2 percobaan per sinyal): coba PRIMARY
  (OpenCodeZen — OPENCODE_ZEN_API_KEY), kalau gagal/timeout coba SECONDARY
  (DeepSeek — DEEPSEEK_API_KEY). Hanya backend yang key-nya ADA yang
  dipakai (kalau hanya 1, ya 1 percobaan). Timeout: 25s percobaan pertama,
  20s percobaan kedua — total budget per sinyal ≤ 45s.
"""
import os
import re
import logging
from typing import Dict, List, Optional

from dotenv import load_dotenv

# Root repo = parent dari folder idx_alpha_screener (tempat file ini)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_ROOT, ".env"))

logger = logging.getLogger("ai_narrative")

FIRST_TRY_TIMEOUT = 25          # detik — percobaan pertama (primary backend)
SECOND_TRY_TIMEOUT = 20         # detik — percobaan kedua (secondary backend)
MAX_ATTEMPTS = 2                # maks 2 percobaan per sinyal (25+20 = 45s ≤ budget)
MAX_SIGNALS = 3                 # top 3 sinyal swing
MAX_TOKENS = 160

# ── Backend murah: OpenCodeZen (primary) → DeepSeek (secondary) ─────────────
def _pick_backends() -> List[dict]:
    """Daftar backend LLM murah dari .env, urut: OpenCodeZen lalu DeepSeek.

    Hanya backend yang API key-nya ADA yang masuk daftar — kalau hanya 1
    key, daftar berisi 1 backend (tidak ada fallback). Return [] kalau
    tidak ada key sama sekali.
    """
    backends = []
    zen_key = os.getenv("OPENCODE_ZEN_API_KEY")
    if zen_key:
        backends.append({
            "name": "opencode_zen",
            "api_key": zen_key,
            "base_url": os.getenv("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/zen/v1"),
            "model": os.getenv("OPENCODE_ZEN_MODEL") or os.getenv("MODEL", "deepseek-v4-flash-free"),
        })
    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        backends.append({
            "name": "deepseek",
            "api_key": deepseek_key,
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        })
    return backends


def _pick_backend() -> Optional[dict]:
    """Kompatibilitas pemanggil lama — backend PRIMARY (pertama) atau None."""
    backends = _pick_backends()
    return backends[0] if backends else None


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

    # Konteks trend mingguan eksplisit — sinyal BEARISH harus dibaca netral
    # sebagai risiko, bukan momentum (V7 akurasi)
    wk_ctx = {"BULLISH": "mendukung (trend mingguan naik)",
              "BEARISH": "kontra — waspada (trend mingguan turun)"}.get(
        str(weekly).upper(), "netral / tidak ada data")

    data_block = (
        f"Ticker: {tkr}\n"
        f"Score: {score}\n"
        f"Weekly trend: {weekly} — {wk_ctx}\n"
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


def _call_llm_once(backend: dict, messages: List[Dict[str, str]],
                   timeout: float = FIRST_TRY_TIMEOUT) -> Optional[str]:
    """
    Satu panggilan LLM (timeout sesuai percobaan: 25s pertama, 20s kedua).
    Return teks jawaban, atau None jika gagal — TIDAK PERNAH melempar exception.
    """
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=backend["api_key"],
            base_url=backend["base_url"],
            timeout=timeout,
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


def _sanitize_narrative(text) -> Optional[str]:
    """Bersihkan output LLM sebelum dipakai (M2).

    Output LLM dipakai verbatim → bom waktu markdown/format Telegram.
    Aturan:
    - strip + normalisasi spasi
    - buang karakter markdown berbahaya: _ * [ ] ` # > <
    - potong maks 2 kalimat
    - tolak kalau tidak masuk akal (terlalu pendek / tanpa huruf) → None
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None
    s = s.replace("**", "").replace("__", "").replace("```", "").replace("`", "")
    s = "".join(ch for ch in s if ch not in "*_[]#><")
    s = " ".join(s.split())
    if not s:
        return None
    parts = re.split(r"(?<=[.!?])\s+", s)
    if len(parts) > 2:
        s = " ".join(parts[:2]).strip()
    if len(s) < 10 or not any(c.isalpha() for c in s):
        return None
    return s


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

    backends = _pick_backends()
    if not backends:
        logger.warning(
            "ai_narrative: tidak ada API key OpenCodeZen/DeepSeek di .env — "
            "narrative dilewati, scan tetap normal"
        )
        return {}

    logger.info("ai_narrative: backend=%s (%d) untuk %d sinyal",
                [b["name"] for b in backends], len(backends),
                min(len(top_signals), MAX_SIGNALS))

    narratives: Dict[str, str] = {}
    for sig in top_signals[:MAX_SIGNALS]:
        tkr = str(sig.get("tkr", "")).upper()
        if not tkr:
            continue
        try:
            messages = _build_prompt(sig, sentiment_ihsg or {})
            # Fallback berjenjang: primary → secondary (maks 2 percobaan,
            # timeout 25s lalu 20s — total budget per sinyal ≤ 45s).
            text = None
            for i, backend in enumerate(backends[:MAX_ATTEMPTS]):
                timeout = FIRST_TRY_TIMEOUT if i == 0 else SECOND_TRY_TIMEOUT
                text = _call_llm_once(backend, messages, timeout=timeout)
                if text:
                    break
                logger.warning("ai_narrative: backend %s gagal untuk %s — "
                               "coba backend berikutnya", backend.get("name"), tkr)
            text = _sanitize_narrative(text)  # M2: jangan pakai output LLM verbatim
            if text:
                narratives[tkr] = text
            else:
                logger.warning(
                    "ai_narrative: semua backend gagal/jawaban tidak valid "
                    "untuk %s — dilewati", tkr)
        except Exception as e:
            # Safety net: SATU ticker gagal → lewati ticker itu, lanjut ticker lain.
            logger.warning("ai_narrative: gagal untuk %s: %s — dilewati", tkr, e)
            continue

    if not narratives:
        logger.warning("ai_narrative: tidak ada narrative dihasilkan — scan tetap normal")
    return narratives
