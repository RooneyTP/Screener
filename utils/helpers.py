"""
helpers.py — Shared utility functions for Screener
===================================================
Extracted from screener.py during dekonstruksi phase.
Provides ANSI colors, safe type converters, signal colorizer, etc.
"""

import numpy as _np


# ─── Warna Terminal (ANSI) ───────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    GRAY   = "\033[90m"
    MAGENTA = "\033[95m"
    BG_GREEN  = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_RED    = "\033[41m"


def warna_sinyal(sinyal: str) -> str:
    if "ULTRA" in sinyal:
        return f"{C.BG_GREEN}{C.BOLD}{C.WHITE} {sinyal} {C.RESET}"
    elif "STRONG" in sinyal:
        return f"{C.BG_GREEN}{C.BOLD}{C.WHITE} {sinyal} {C.RESET}"
    elif "BUY" in sinyal and "STRONG" not in sinyal:
        return f"{C.GREEN}{C.BOLD}{sinyal}{C.RESET}"
    elif "PANTAU" in sinyal:
        return f"{C.YELLOW}{sinyal}{C.RESET}"
    else:
        return f"{C.GRAY}{sinyal}{C.RESET}"


# ── Lazy import helper ───────────────────────────────────────────────────
def _lazy_func(module_name, func_name, fallback=None):
    """Lazily import a function with graceful fallback if module missing."""
    try:
        mod = __import__(module_name, fromlist=[func_name])
        return getattr(mod, func_name)
    except (ImportError, AttributeError):
        return fallback


# ── Centralized NaN guard untuk ekstraksi indikator ─────────────────────
def _safe_float(val, default: float = 0.0) -> float:
    """Extract float from scalar/Series with NaN/Inf guard."""
    if val is None:
        return default
    if hasattr(val, 'iloc'):
        val = val.iloc[-1] if val.size > 0 else default
    try:
        v = float(val)
        if _np.isnan(v) or _np.isinf(v):
            return default
        return v
    except (ValueError, TypeError, IndexError):
        return default


def safe_int(value, default=0):
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(float(value))
    except Exception:
        return default


def safe_float(value, default=0.0):
    """Alias for _safe_float with slightly different semantics."""
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default
