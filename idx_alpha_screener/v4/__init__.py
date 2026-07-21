"""
v4 — Confluence Gate + Dynamic Conviction Scoring
=================================================
Modul terpisah (toggleable) dari strategi v3.
Bisa di-A/B test tanpa mengubah kode existing.

Cara pakai di main.py:
    import v4
    v4.enabled = True   # atau via --v4 flag

Files:
    __init__.py   — Module init, toggle, helper
    conviction.py — DynamicConviction engine (8 factors)
    confluence.py — Confluence Gate (6 confirmation sources)
"""

import logging
from typing import Optional

logger = logging.getLogger("v4")

# Toggle global — bisa di-set dari main.py
enabled: bool = False

# Mode A/B test
# "v4_only"  → pakai v4 untuk semua
# "compare"  → jalanin v3 dan v4, print perbandingan
ab_test_mode: str = "v4_only"

# Config (diisi dari config.yaml v4: section)
config: dict = {
    "adx_no_trend_penalty": 0.08,
    "ihsg_bear_penalty": 5,
    "weekly_bonus": 3,
    "conviction_bonus": 5,
    "thresholds": {
        "BULL": [65, 58, 50, 42, 35],
        "BEAR": [62, 52, 45, 38, 30],
        "RANGING": [62, 55, 48, 40, 35],
        "HIGH_VOLATILITY": [62, 55, 48, 40, 35],
    },
}


def configure(cfg: dict):
    """Update config dari config.yaml v4: section."""
    if not cfg:
        return
    global config
    for key, val in cfg.items():
        if key == "thresholds" and isinstance(val, dict):
            config["thresholds"].update(val)
            # Sync ke conviction module
            from v4 import conviction as v4c
            for regime, th in val.items():
                if regime in v4c.THRESHOLDS:
                    v4c.THRESHOLDS[regime] = th
        else:
            config[key] = val
    logger.info("v4 configured: %s", {k: v for k, v in config.items()
                if k != "thresholds"})


def is_enabled() -> bool:
    """Cek apakah v4 mode aktif."""
    return enabled


def is_compare_mode() -> bool:
    """Cek apakah A/B compare mode aktif."""
    return enabled and ab_test_mode == "compare"
