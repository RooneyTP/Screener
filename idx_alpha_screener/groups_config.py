"""
groups_config.py — SINGLE SOURCE mapping grup konglomerat (V7 akurasi)
======================================================================
Mapping final grup konglomerat terverifikasi (08/2026) disimpan di
config.yaml section 'groups'. Modul ini adalah SATU-SATUNYA helper yang
membacanya — v7_scan.py, factor_analysis.py, dan weekly_report.py TIDAK
boleh lagi punya mapping hardcode sendiri (sebelumnya ada 4 sumber drift:
3 dict GROUP_NAMES + 1 section watchlist di config.yaml).

Struktur config.yaml (dua bentuk didukung, dinormalisasi ke {TICKER: grup}):

    groups:
      Barito: [BRPT, TPIA]     # bentuk {grup: [ticker,...]}
      BRPT: Barito             # bentuk {ticker: grup} (alternatif)

Pemakaian:
    from groups_config import load_groups, group_of
    GROUP_NAMES = load_groups()          # fallback {} kalau config gagal
    group_of("brpt")                     # -> "Barito" (case-insensitive)
"""
import os
import logging

import yaml

logger = logging.getLogger("groups_config")

# config.yaml satu tingkat di atas file ini (folder idx_alpha_screener)
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")

_cache: dict = None  # hasil load per proses — config jarang berubah saat runtime


def load_groups(config_path: str = None) -> dict:
    """Baca mapping grup dari config.yaml section 'groups'.

    Returns
    -------
    dict — {TICKER: nama_grup}. {} kalau config hilang/rusak/section kosong;
    pemanggil harus tahan terhadap mapping kosong (fallback aman — label
    grup tidak tampil, guard C2 tidak menghukum apa pun).

    Ticker yang BELUM terverifikasi tidak ada di mapping → group_of() = "".
    """
    global _cache
    if config_path is None and _cache is not None:
        return _cache
    path = config_path or CONFIG_PATH
    groups: dict = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            cfg = yaml.safe_load(f) or {}
        raw = cfg.get("groups") or {}
        for k, v in raw.items():
            key = str(k).strip()
            if isinstance(v, (list, tuple)):
                # Bentuk {Grup: [TICKER, ...]}
                for t in v:
                    t = str(t).strip().upper() if t else ""
                    if t:
                        groups[t] = key
            elif isinstance(v, str) and v.strip():
                # Bentuk {TICKER: Grup}
                groups[key.upper()] = v.strip()
    except Exception as e:
        logger.warning("groups_config: gagal baca %s → mapping kosong: %s", path, e)
        groups = {}
    if config_path is None:
        _cache = groups
    return groups


def group_of(ticker: str, groups: dict = None) -> str:
    """Label grup konglomerat untuk ticker; '' kalau tidak dikenal/unlabeled."""
    if not ticker:
        return ""
    g = groups if groups is not None else load_groups()
    return g.get(str(ticker).strip().upper(), "")
