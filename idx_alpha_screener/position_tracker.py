"""
position_tracker.py — Position & Exit Monitor untuk V7
=======================================================
Menyimpan posisi user (entry manual) dan mengecek setiap hari:
- Harga vs Stop Loss → ALERT EXIT
- Harga vs Take Profit → ALERT TP
- Time stop (max hold) → ALERT
- Trailing stop → update otomatis

Data disimpan di data/positions.json.
Cara pakai dari cron: check_positions(current_price_getter)
"""
import os
import json
import logging
from datetime import datetime, timedelta

logger = logging.getLogger("position_tracker")

DEFAULT_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "positions.json")


class PositionTracker:
    """Tracker posisi terbuka dengan exit monitoring."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        self._cache = None

    # ── Internal ──
    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, encoding="utf-8") as f:
                    self._cache = json.load(f)
            except (json.JSONDecodeError, Exception):
                logger.warning("Positions DB corrupt — reset")
                self._cache = {}
        else:
            self._cache = {}
        return self._cache

    def _save(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)

    # ── Public ──
    def add_position(self, ticker: str, entry_price: float, lots: int = 1,
                     stop_loss: float = 0, take_profit: float = 0,
                     mode: str = "swing", score: float = 0) -> bool:
        """Tambah posisi baru. Return True jika berhasil."""
        if ticker is None or not ticker.strip() or entry_price <= 0:
            return False
        data = self._load()
        key = ticker.upper()
        if key in data:
            # M6: JANGAN timpa posisi existing diam-diam — pertahankan yang
            # sudah ada (paling aman), beri warning + tolak add baru.
            logger.warning("Position %s sudah ada — add ditolak, pertahankan existing", key)
            return False
        data[key] = {
            "entry_price": float(entry_price),
            "lots": int(lots),
            "stop_loss": float(stop_loss) if stop_loss > 0 else round(entry_price * 0.95, 2),
            "take_profit": float(take_profit) if take_profit > 0 else round(entry_price * 1.10, 2),
            "mode": mode,
            "score": float(score),
            "entry_date": datetime.now().strftime("%Y-%m-%d"),
            "highest_price": float(entry_price),
            "trailing_active": False,
            "trailing_stop": 0.0,
        }
        self._save()
        logger.info("Position ditambahkan: %s @ %.0f (%d lot)", ticker.upper(), entry_price, lots)
        return True

    def close_position(self, ticker: str) -> bool:
        """Tutup/remove posisi. Return True jika ada dan dihapus."""
        data = self._load()
        if ticker.upper() in data:
            del data[ticker.upper()]
            self._save()
            logger.info("Position ditutup: %s", ticker.upper())
            return True
        return False

    def get_positions(self) -> dict:
        """Kembalikan semua posisi aktif."""
        return dict(self._load())

    def update_price(self, ticker: str, current_price: float) -> bool:
        """Update highest_price untuk trailing stop. Return True jika ada posisi."""
        data = self._load()
        pos = data.get(ticker.upper())
        if not pos:
            return False
        if current_price > pos["highest_price"]:
            pos["highest_price"] = current_price
            gain_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            if gain_pct >= 3.0:
                if not pos.get("trailing_active"):
                    pos["trailing_active"] = True
                # Hitung trailing stop: highest - setengah jarak SL (naik saja, tak pernah turun)
                sl_dist = max(pos["entry_price"] - pos["stop_loss"], 1.0)
                new_trail = round(pos["highest_price"] - sl_dist * 0.5, 2)
                pos["trailing_stop"] = max(pos.get("trailing_stop", 0.0), new_trail)
                logger.info("Trailing: %s aktif di %.0f (gain %.1f%%)", ticker.upper(), pos["trailing_stop"], gain_pct)
            self._save()
        return True

    def check_positions(self, price_getter, mutate: bool = True) -> list:
        """
        Cek semua posisi terhadap harga terkini.

        Parameters
        ----------
        price_getter : callable(ticker) -> float
            Fungsi untuk ambil harga terbaru. Return 0 jika gagal.
        mutate : bool
            True (default) → perilaku normal cron: update highest/trailing,
            tutup posisi yang kena SL/TP/trailing/time-stop, simpan ke DB.
            False → evaluasi SAJA (dry-run): tidak ada update harga, tidak
            ada penutupan posisi, tidak ada penulisan file.

        Returns
        -------
        list of dict — alert/status per posisi yang perlu dilaporkan
        """
        data = self._load()
        alerts = []
        today = datetime.now()
        for ticker, pos in list(data.items()):
            try:
                price = float(price_getter(ticker) or 0)
                if price <= 0:
                    # M7: time-stop dievaluasi TERPISAH — tetap dicek walau
                    # harga gagal diambil (posisi basi tetap harus EXIT).
                    if self._time_stop_hit(pos, today):
                        alerts.append({"ticker": ticker, "level": "EXIT",
                                       "message": f"TIME STOP! {ticker} sudah {self._days_held(pos, today)} hari "
                                                  f"(max {self._max_hold(pos)}) — EXIT"})
                        if mutate:
                            self.close_position(ticker)
                        continue
                    alerts.append({"ticker": ticker, "level": "INFO",
                                   "message": f"Tidak bisa ambil harga {ticker}"})
                    continue

                entry = pos["entry_price"]
                sl = pos["stop_loss"]
                tp = pos["take_profit"]

                # Update highest & trailing (hanya mode mutate — dry-run tidak
                # boleh mengubah state positions.json)
                if mutate:
                    self.update_price(ticker, price)

                # 1. Stop loss kena
                if price <= sl:
                    alerts.append({"ticker": ticker, "level": "EXIT",
                                   "message": f"SL KENA! {ticker} di {price:,.0f} <= SL {sl:,.0f} — EXIT SEKARANG"})
                    if mutate:
                        self.close_position(ticker)
                    continue

                # 2. Take profit kena
                if price >= tp:
                    alerts.append({"ticker": ticker, "level": "EXIT",
                                   "message": f"TP KENA! {ticker} di {price:,.0f} >= TP {tp:,.0f} — AMBIL PROFIT"})
                    if mutate:
                        self.close_position(ticker)
                    continue

                # 3. Trailing stop
                trailing = pos.get("trailing_stop", 0.0)
                if pos.get("trailing_active") and trailing > 0 and price <= trailing:
                    alerts.append({"ticker": ticker, "level": "EXIT",
                                   "message": f"TRAILING KENA! {ticker} di {price:,.0f} <= trail {trailing:,.0f} — EXIT"})
                    if mutate:
                        self.close_position(ticker)
                    continue

                # 4. Time stop
                if self._time_stop_hit(pos, today):
                    alerts.append({"ticker": ticker, "level": "EXIT",
                                   "message": f"TIME STOP! {ticker} sudah {self._days_held(pos, today)} hari "
                                              f"(max {self._max_hold(pos)}) — EXIT"})
                    if mutate:
                        self.close_position(ticker)
                    continue

                # 5. Status hold biasa (hanya info ringkas)
                pct = (price - entry) / entry * 100
                remaining = self._max_hold(pos) - self._days_held(pos, today)
                alerts.append({"ticker": ticker, "level": "HOLD",
                               "message": f"{ticker} {pct:+.1f}% (entry {entry:,.0f}) | SL {sl:,.0f} | TP {tp:,.0f} | hold {remaining}d lagi"})

            except Exception as e:
                logger.debug("Check %s gagal: %s", ticker, e)
                continue

        return alerts

    @staticmethod
    def _max_hold(pos: dict) -> int:
        """Max hari hold: swing 20 hari, mode lain (intraday) 3 hari."""
        return 20 if pos.get("mode", "swing") == "swing" else 3

    @staticmethod
    def _days_held(pos: dict, today: datetime) -> int:
        """Lama hari posisi dipegang (0 kalau entry_date tidak valid)."""
        try:
            dt_entry = datetime.strptime(pos.get("entry_date", ""), "%Y-%m-%d")
            return (today - dt_entry).days
        except (ValueError, TypeError):
            return 0

    def _time_stop_hit(self, pos: dict, today: datetime) -> bool:
        """True kalau posisi sudah melewati max hold."""
        return self._days_held(pos, today) >= self._max_hold(pos)

    def clean_old(self, max_days: int = 60):
        """Hapus posisi yang sudah ditutup terlalu lama (safety)."""
        data = self._load()
        today = datetime.now()
        to_remove = []
        for ticker, pos in data.items():
            try:
                dt = datetime.strptime(pos.get("entry_date", ""), "%Y-%m-%d")
                if (today - dt).days > max_days:
                    to_remove.append(ticker)
            except (ValueError, TypeError):
                pass
        for t in to_remove:
            del data[t]
        if to_remove:
            self._cache = data
            self._save()
            logger.info("Cleanup: %d posisi lama dihapus", len(to_remove))


def format_position_alerts(alerts: list) -> str:
    """Format alerts jadi pesan Telegram yang ringkas."""
    if not alerts:
        return ""
    lines = ["📌 POSISI HARI INI", "─" * 25]
    for a in alerts:
        icon = {"EXIT": "🚨", "HOLD": "🟢", "INFO": "ℹ️"}.get(a["level"], "•")
        lines.append(f"{icon} {a['message']}")
    return "\n".join(lines)
