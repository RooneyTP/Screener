"""berita_sentimen.py — faktor sentimen BERITA utk skor V7 (BARU 21 Sep 2026).

Latar: screener sudah punya "sentimen pasar" (market_sentiment.py — teknikal
IHSG), tapi TIDAK memakai berita. Modul ini menutup celah itu: membaca feed
berita per-simbol Stockbit (modul stockbit_news milik RisetSaham — SATU sumber
dengan fitur berita app; JANGAN duplikasi logika sentimennya), menilai judul
dgn sentimen.py v2.1 (konteks-aware: baik/buruk/netral/campuran), lalu
meringkasnya jadi satu angka delta utk post-adjustment skor V7.

Aturan skor (v1 — sengaja kecil & konservatif, default maks ±2,0 poin):
  - hanya berita ≤ `window_days` (default 7 hari)
  - label baik/buruk → magnitudo skor sentimen (diklem ±3 per judul);
    netral & campuran → 0 (dua arah sekaligus = jangan dipaksa satu ton)
  - bobot recency: ≤72 jam = 1,0; selebihnya 0,5
  - delta = max_points × clamp(Σ / 3, −1, +1)  → maks ±2,0 poin

Belum bisa di-backtest (tanpa riwayat berita) → nilai delta + jumlah judul
direkam di shadow_v7.csv (kolom berita_*) utk evaluasi forward (shadow_eval).
Kill-switch: env SCREENER_NEWS=0 atau config.yaml v7.news.enabled=false.
Cache per kode: data/berita_{KODE}.json (TTL default 6 jam) — hemat panggilan
Stockbit; timeout/error → delta 0 (scan TIDAK pernah mati karena berita).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))

RISET_DIR = "/home/yuan/risetsaham"   # modul bersama (stockbit_news + sentimen)
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DEFAULT_MAX_POINTS = 2.0
DEFAULT_WINDOW_DAYS = 7.0
DEFAULT_CACHE_JAM = 6.0
RECENCY_TAJAM_JAM = 72.0     # ≤72 jam bobot penuh (1,0), selebihnya 0,5
_SATUAN_PENUH = 3.0          # Σ ±3 = sinyal penuh → delta = max_points
_MAKS_PER_JUDUL = 3.0        # magnitudo skor per judul diklem ±3

# Breaker: kegagalan beruntun (mis. sesi Stockbit mati) → jeda 10 menit supaya
# scan 60 finalis tidak menghajar endpoint yang sedang bermasalah.
_GAGAL_SAMPAI = 0.0
_JEDA_GAGAL_DETIK = 600


def diaktifkan() -> bool:
    """Kill-switch cepat: env SCREENER_NEWS=0/false/off → nonaktif."""
    return (os.environ.get("SCREENER_NEWS", "1").strip().lower()
            not in ("0", "false", "off", ""))


def _kode_aman(kode: str) -> str:
    """Sanitasi ticker utk nama file cache — hanya A-Z0-9 (cegah path traversal)."""
    return re.sub(r"[^A-Z0-9]", "", (kode or "").upper())


def _cache_path(kode: str) -> str:
    return os.path.join(_DATA_DIR, f"berita_{_kode_aman(kode)}.json")


def _ts_wib(s: str) -> float | None:
    """'YYYY-MM-DD HH:MM:SS' (WIB) → epoch; gagal → None."""
    try:
        return datetime.strptime(str(s or "")[:19], "%Y-%m-%d %H:%M:%S") \
            .replace(tzinfo=WIB).timestamp()
    except Exception:
        return None


def _muat_cache(kode: str, ttl_jam: float):
    path = _cache_path(kode)
    try:
        if os.path.exists(path):
            age_j = (time.time() - os.path.getmtime(path)) / 3600.0
            if age_j < ttl_jam:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and isinstance(d.get("items"), list):
                    return d["items"]
    except Exception:
        pass
    return None


def _simpan_cache(kode: str, items: list) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _cache_path(kode) + f".{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"items": items, "diambil": time.time()}, f)
        os.replace(tmp, _cache_path(kode))
    except Exception:
        pass


def ambil_berita(kode: str, ttl_jam: float = DEFAULT_CACHE_JAM) -> list[dict]:
    """Berita simbol (maks 20 terbaru) — [{'judul','ts','sid'}]; gagal → [].

    Cache file per kode (TTL default 6 jam). Import modul RisetSaham LAZY
    (di dalam fungsi) supaya tes & lingkungan tanpa RisetSaham tetap aman.
    """
    cached = _muat_cache(kode, ttl_jam)
    if cached is not None:
        return cached
    global _GAGAL_SAMPAI
    if time.time() < _GAGAL_SAMPAI:
        return []
    try:
        if RISET_DIR not in sys.path:
            sys.path.append(RISET_DIR)
        import stockbit_news  # modul RisetSaham (stdlib + broker saja)
        rows = stockbit_news.fetch_symbol(kode, limit=20)
        items, lihat = [], set()
        for r in rows or []:
            judul = str((r or {}).get("title") or "").strip()
            ts = _ts_wib((r or {}).get("created_at"))
            sid = (r or {}).get("stream_id")
            if not judul or ts is None:
                continue
            if sid is not None and sid in lihat:
                continue
            if sid is not None:
                lihat.add(sid)
            items.append({"judul": judul, "ts": float(ts), "sid": sid})
        items.sort(key=lambda x: -x["ts"])
        _simpan_cache(kode, items)
        return items
    except Exception:
        _GAGAL_SAMPAI = time.time() + _JEDA_GAGAL_DETIK
        return []


def _hasil(delta: float, counts: dict, n: int, contoh: str, catatan: str) -> dict:
    """Bentuk seragam hasil faktor (delta, hitungan label, detail utk log)."""
    return {
        "delta": float(delta),
        "n": int(n),
        "n_baik": int(counts.get("baik", 0)),
        "n_buruk": int(counts.get("buruk", 0)),
        "n_netral": int(counts.get("netral", 0)),
        "n_campuran": int(counts.get("campuran", 0)),
        "contoh": contoh,
        "detail": (catatan or
                   f"berita {delta:+.1f} ({counts.get('baik', 0)} baik · "
                   f"{counts.get('buruk', 0)} buruk · {counts.get('campuran', 0)} "
                   f"campuran · {counts.get('netral', 0)} netral dari {n} judul)"),
    }


def _kosong(catatan: str) -> dict:
    return _hasil(0.0, {}, 0, "", catatan)


def hitung_delta(items: list[dict], now: float | None = None,
                 window_days: float = DEFAULT_WINDOW_DAYS,
                 max_points: float = DEFAULT_MAX_POINTS) -> dict:
    """Agregasi sentimen item berita → dict delta & hitungan label (tidak melempar)."""
    now = time.time() if now is None else float(now)
    counts = {"baik": 0, "buruk": 0, "netral": 0, "campuran": 0}
    if not items:
        return _kosong("tanpa berita")
    try:
        if RISET_DIR not in sys.path:
            sys.path.append(RISET_DIR)
        from sentimen import sentimen_judul_skor
    except Exception as e:  # noqa: BLE001
        return _kosong(f"sentimen.py tak tersedia ({type(e).__name__})")

    total_s, n, contoh = 0.0, 0, ""
    for it in items:
        try:
            umur_j = (now - float(it.get("ts") or 0)) / 3600.0
        except (TypeError, ValueError):
            continue
        if umur_j > window_days * 24 or umur_j < -6:
            continue
        try:
            label, skor = sentimen_judul_skor(str(it.get("judul") or ""))
        except Exception:  # noqa: BLE001 — satu judul rusak jangan bikin gagal
            continue
        counts[label if label in counts else "netral"] += 1
        n += 1
        if label in ("netral", "campuran"):
            continue
        val = max(-_MAKS_PER_JUDUL, min(_MAKS_PER_JUDUL, float(skor)))
        total_s += (1.0 if umur_j <= RECENCY_TAJAM_JAM else 0.5) * val
        if not contoh:
            contoh = str(it.get("judul") or "")[:70]

    if n == 0:
        return _kosong(f"tanpa berita ≤{int(window_days)} hari")
    delta = max_points * max(-1.0, min(1.0, total_s / _SATUAN_PENUH))
    return _hasil(round(delta, 2), counts, n, contoh, "")


def faktor_berita(kode: str, cfg: dict | None = None) -> dict:
    """Faktor berita utk skor V7 — dipanggil v7.compute() (jangan pernah melempar)."""
    cfg = cfg or {}
    if not diaktifkan() or not bool(cfg.get("enabled", True)):
        return _kosong("nonaktif")
    try:
        ttl = float(cfg.get("cache_jam") or DEFAULT_CACHE_JAM)
        window = float(cfg.get("window_days") or DEFAULT_WINDOW_DAYS)
        maks = float(cfg.get("max_points") or DEFAULT_MAX_POINTS)
        items = ambil_berita(kode, ttl_jam=ttl)
        return hitung_delta(items, window_days=window, max_points=maks)
    except Exception as e:  # noqa: BLE001 — faktor berita tidak mematikan scan
        return _kosong(f"berita gagal ({type(e).__name__})")
