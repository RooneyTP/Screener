"""portfolio.py — Portfolio Heat Management untuk IDX Alpha Screener v3
Mengelola batasan portofolio: maks posisi, maks per sektor, eksposur sektor.

Usage:
    pm = PortfolioManager(config)
    ok, reason = pm.can_enter("BBCA", "Perbankan", 6000, 500_000_000)
    if ok:
        pm.enter_position("BBCA", "Perbankan", 6000, 100000, 500_000_000)
"""
import logging
from datetime import datetime, date
from typing import Optional, Tuple

logger = logging.getLogger("portfolio")


class PortfolioManager:
    """Portfolio heat management — batasi risiko over-konsentrasi."""

    def __init__(self, config: dict = None):
        """
        Parameters
        ----------
        config : dict
          Dari section 'portfolio' di config.yaml.
          {max_positions: 5, max_per_sector: 2, max_sector_exposure_pct: 40}
        """
        cfg = config or {}
        self.max_positions = cfg.get("max_positions", 5)
        self.max_per_sector = cfg.get("max_per_sector", 2)
        self.max_sector_exposure_pct = cfg.get("max_sector_exposure_pct", 40.0)
        self.enabled = cfg.get("enabled", True)

        # State: position tracking
        self._positions = {}  # ticker -> {"sector": str, "entry_price": float, "shares": int, "entry_date": date}
        self._sector_counts = {}  # sector -> int (jumlah posisi aktif)

    def reset(self):
        """Reset semua posisi — panggil di awal scan baru."""
        self._positions.clear()
        self._sector_counts.clear()

    # ── Query Methods ────────────────────────────────────────────────

    @property
    def total_positions(self) -> int:
        return len(self._positions)

    @property
    def is_full(self) -> bool:
        """True jika sudah mencapai max_positions."""
        return self.total_positions >= self.max_positions

    def sector_count(self, sector: str) -> int:
        return self._sector_counts.get(sector, 0)

    def sector_exposure_pct(self, sector: str, capital: float) -> float:
        """Persentase modal yang terpakai di sektor tertentu."""
        if capital <= 0:
            return 0.0
        total_sector_value = sum(
            pos["shares"] * pos["entry_price"]
            for pos in self._positions.values()
            if pos["sector"] == sector
        )
        return (total_sector_value / capital) * 100

    def total_exposure_pct(self, capital: float) -> float:
        """Persentase modal yang sudah dipakai semua posisi."""
        if capital <= 0:
            return 0.0
        total_value = sum(
            pos["shares"] * pos["entry_price"]
            for pos in self._positions.values()
        )
        return (total_value / capital) * 100

    def list_positions(self) -> list:
        """Return list posisi aktif (untuk display)."""
        return [
            {
                "ticker": tkr,
                "sector": info["sector"],
                "entry_price": info["entry_price"],
                "shares": info["shares"],
                "entry_date": info["entry_date"],
            }
            for tkr, info in sorted(self._positions.items())
        ]

    # ── Entry Gate ───────────────────────────────────────────────────

    def can_enter(self, ticker: str, sector: str,
                  price: float, capital: float) -> Tuple[bool, str]:
        """
        Cek apakah masih bisa entry posisi baru.
        Return (allowed: bool, reason: str).

        Rules:
        1. Max positions cap
        2. Max per sector cap
        3. Max sector exposure cap
        4. Duplicate ticker check
        """
        if not self.enabled:
            return True, "portfolio management disabled"

        # Already holding this ticker?
        if ticker in self._positions:
            return False, f"Sudah hold {ticker}"

        # Max positions check
        if self.is_full:
            return False, f"Portfolio penuh ({self.total_positions}/{self.max_positions})"

        # Max per sector check
        if self.sector_count(sector) >= self.max_per_sector:
            return False, f"Sektor '{sector}' penuh ({self.sector_count(sector)}/{self.max_per_sector})"

        # Max sector exposure check
        if capital > 0:
            new_entry_value = price * 100  # 1 lot minimal
            # Tentative new exposure
            existing_sector_value = sum(
                pos["shares"] * pos["entry_price"]
                for pos in self._positions.values()
                if pos["sector"] == sector
            )
            tentative_exposure = ((existing_sector_value + new_entry_value) / capital) * 100
            if tentative_exposure > self.max_sector_exposure_pct:
                return False, (
                    f"Exposure sektor '{sector}' akan {tentative_exposure:.0f}% "
                    f"(maks {self.max_sector_exposure_pct}%)"
                )

        return True, "OK"

    def enter_position(self, ticker: str, sector: str,
                       price: float, shares: int, capital: float,
                       entry_date: date = None) -> bool:
        """
        Catat posisi baru. Wajib dipanggil setelah can_enter() == True.
        Return True jika berhasil.
        """
        if ticker in self._positions:
            logger.warning("enter_position: %s sudah di portofolio", ticker)
            return False

        if shares <= 0:
            logger.warning("enter_position: shares=%d untuk %s tidak valid", shares, ticker)
            return False

        self._positions[ticker] = {
            "sector": sector or "",
            "entry_price": float(price),
            "shares": int(shares),
            "entry_date": entry_date or date.today(),
        }
        effective_sector = sector or ""
        self._sector_counts[effective_sector] = self._sector_counts.get(effective_sector, 0) + 1

        logger.info("Portfolio ENTRY: %s sektor=%s %d shares @ Rp %.0f (posisi %d/%d)",
                    ticker, effective_sector, shares, price,
                    self.total_positions, self.max_positions)
        return True

    # ── Exit ─────────────────────────────────────────────────────────

    def exit_position(self, ticker: str) -> bool:
        """Hapus posisi dari portofolio. Return True jika berhasil."""
        if ticker not in self._positions:
            return False

        sector = self._positions[ticker].get("sector", "")
        del self._positions[ticker]

        if sector in self._sector_counts:
            self._sector_counts[sector] = max(0, self._sector_counts[sector] - 1)
            if self._sector_counts[sector] == 0:
                del self._sector_counts[sector]

        logger.info("Portfolio EXIT: %s (posisi tersisa %d)", ticker, self.total_positions)
        return True

    def exit_all(self):
        """Exit semua posisi."""
        self._positions.clear()
        self._sector_counts.clear()
        logger.info("Portfolio: semua posisi di-exit")

    # ── Serialization ────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "positions": {
                tkr: {**info, "entry_date": str(info["entry_date"])}
                for tkr, info in self._positions.items()
            },
            "max_positions": self.max_positions,
            "max_per_sector": self.max_per_sector,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PortfolioManager":
        pm = cls({
            "max_positions": data.get("max_positions", 5),
            "max_per_sector": data.get("max_per_sector", 2),
        })
        for tkr, info in data.get("positions", {}).items():
            pm._positions[tkr] = {
                "sector": info["sector"],
                "entry_price": info["entry_price"],
                "shares": info["shares"],
                "entry_date": datetime.strptime(info["entry_date"], "%Y-%m-%d").date()
                if isinstance(info["entry_date"], str) else info["entry_date"],
            }
            sec = info.get("sector", "")
            pm._sector_counts[sec] = pm._sector_counts.get(sec, 0) + 1
        return pm
