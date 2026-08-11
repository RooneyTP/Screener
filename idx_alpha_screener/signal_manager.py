"""
signal_manager.py — Cooldown Tracker + Sector Concentration Filter
================================================================
Fungsi:
  1. Cooldown: saham yang sudah dapat BUY tidak muncul lagi N hari
  2. Sector cap: maks X saham BUY per sektor
  3. Ringkasan sektor untuk laporan
"""
import json
import os
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger("signal_manager")


class CooldownTracker:
    """Tracker cooldown sinyal beli per saham.

    Data disimpan di JSON file. Format:
    {
        "BBCA": {"date": "2026-07-14", "signal": "STRONG_BUY", "timestamp": "..."},
        "BBCA::swing": {"date": "...", "signal": "...", "timestamp": "..."},
        ...
    }
    Key "TICKER" = level ticker (legacy, dipakai pemanggil tanpa mode);
    key "TICKER::mode" = per mode (R3): swing & intraday punya slot cooldown
    sendiri sehingga record swing TIDAK memblokir intraday (dan sebaliknya).
    """

    def __init__(self, db_path: str, cooldown_days: int = 5):
        self.db_path = db_path
        self.cooldown_days = cooldown_days
        self._cache: Optional[dict] = None

    # ── Internal ────────────────────────────────────────────────────────
    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, Exception):
                logger.warning("Cooldown DB corrupt — reset")
                self._cache = {}
        else:
            self._cache = {}
        return self._cache

    def _save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)

    # ── Public ─────────────────────────────────────────────────────────
    def _key(self, ticker: str, mode: Optional[str] = None) -> str:
        """Key DB cooldown.

        mode=None → "TICKER" (key level-ticker lama, backward compat dengan
        pemanggil seperti main.py yang tidak peduli mode).
        mode="swing"/"intraday" → "TICKER::mode" (R3 — cooldown PER MODE:
        dulu swing & intraday berbagi 1 slot ticker-level sehingga record
        intraday menimpa record swing & swing memblokir intraday).
        """
        t = ticker.upper()
        return f"{t}::{mode}" if mode else t

    def is_on_cooldown(self, ticker: str, mode: Optional[str] = None) -> bool:
        """Cek apakah ticker sedang dalam masa cooldown (hanya BUY signal).

        mode=None → cek key legacy level-ticker (perilaku lama).
        mode="swing"/"intraday" → cek cooldown mode tsb SAJA.
        """
        data = self._load()
        entry = data.get(self._key(ticker, mode))
        if not entry:
            return False
        signal_type = entry.get("signal", "")
        last_date = entry.get("date", "")
        if not last_date:
            return False
        # Hanya cooldown untuk sinyal beli — HOLD/SELL tidak
        if signal_type not in ("STRONG_BUY", "BUY", "WEAK_BUY"):
            return False
        try:
            dt_last = datetime.strptime(last_date, "%Y-%m-%d")
            hari_berlalu = (datetime.now() - dt_last).days
            return hari_berlalu < self.cooldown_days
        except ValueError:
            return False

    def record(self, ticker: str, signal: str, extra: dict = None,
               mode: Optional[str] = None):
        """Catat sinyal ke database cooldown.

        mode=None → key level-ticker (perilaku lama); mode diberikan →
        key "TICKER::mode" sehingga mode lain tidak terpengaruh (R3).
        """
        data = self._load()
        entry = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "signal": signal,
            "timestamp": datetime.now().isoformat(),
        }
        if extra:
            entry.update(extra)
        data[self._key(ticker, mode)] = entry
        self._save()

    def cooldown_info(self, ticker: str, mode: Optional[str] = None) -> Optional[dict]:
        """Info cooldown untuk display."""
        data = self._load()
        entry = data.get(self._key(ticker, mode))
        if not entry:
            return None
        try:
            dt_last = datetime.strptime(entry["date"], "%Y-%m-%d")
            remaining = self.cooldown_days - (datetime.now() - dt_last).days
            return {
                "last_signal": entry["signal"],
                "last_date": entry["date"],
                "remaining_days": max(0, remaining),
            }
        except (ValueError, KeyError):
            return None

    def clean_old(self):
        """Hapus entry > 30 hari untuk cegah db membesar."""
        data = self._load()
        before = len(data)
        cutoff = datetime.now()
        data = {
            k: v for k, v in data.items()
            if "date" in v and (cutoff - datetime.strptime(v["date"], "%Y-%m-%d")).days < 30
        }
        self._cache = data
        self._save()
        after = len(data)
        if before != after:
            logger.info("Cooldown DB: %d entry dibersihkan", before - after)


# ══════════════════════════════════════════════════════════════════════
#  SECTOR CAP
# ══════════════════════════════════════════════════════════════════════

def apply_sector_cap(hasil: list, max_per_sector: int = 2) -> list:
    """Batasi jumlah sinyal BUY per sektor.

    Cara kerja:
      - Sort hasil by skor (tertinggi dulu)
      - Iterasi, hitung per sektor
      - Jika sudah >= max_per_sector, sinyal BUY di-downgrade ke HOLD
      - Non-BUY signal tidak terpengaruh

    Returns list yang sudah dimodifikasi in-place.
    """
    # Sort by score descending — saham dengan skor terbaik diprioritaskan
    sorted_hasil = sorted(hasil, key=lambda x: x.get("score", 0), reverse=True)
    sector_count = {}

    for h in sorted_hasil:
        sector = h.get("sector", "Unknown") or "Unknown"
        # Skip sector cap kalo sektor Unknown (data fundamental gagal fetch)
        if sector == "Unknown":
            continue
        # Potong sektor terlalu panjang untuk key
        sector_key = sector[:25]

        if h.get("signal") in ("STRONG_BUY", "BUY", "WEAK_BUY"):
            current = sector_count.get(sector_key, 0)
            if current >= max_per_sector:
                old_sig = h["signal"]
                h["signal"] = "HOLD"
                logger.info(
                    "Sector cap: %s (sektor %s) — skor %d → HOLD (sudah ada %d BUY di sektor ini)",
                    h["ticker"], sector_key, h["score"], current
                )
                if h.get("_sector_capped"):
                    h["_sector_capped"] = True
        sector_count[sector_key] = sector_count.get(sector_key, 0) + 1

    # Urutkan kembali by score
    return sorted(sorted_hasil, key=lambda x: x.get("score", 0), reverse=True)


def get_sector_buy_summary(hasil: list) -> str:
    """Buat ringkasan jumlah BUY per sektor."""
    sectors = {}
    for h in hasil:
        sector = (h.get("sector") or "Unknown")[:18]
        if sector not in sectors:
            sectors[sector] = {"buy": 0, "hold": 0, "sell": 0}
        sig = h.get("signal", "HOLD")
        if sig in ("STRONG_BUY", "BUY", "WEAK_BUY"):
            sectors[sector]["buy"] += 1
        elif sig == "HOLD":
            sectors[sector]["hold"] += 1
        else:
            sectors[sector]["sell"] += 1

    lines = []
    for s, v in sorted(sectors.items(), key=lambda x: x[1]["buy"], reverse=True):
        if v["buy"] > 0:
            lines.append(f"  • {s}: 🟢{v['buy']} / ⚪{v['hold']} / 🔴{v['sell']}")
    return "\n".join(lines[:8]) if lines else "  (tidak ada)"
