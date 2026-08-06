#!/usr/bin/env python3
"""
position_check_intraday.py — Cek posisi terbuka pakai harga INTRADAY (jam trading)
===================================================================================
MASALAH:
  v7_scan.py hanya mengecek SL/TP/trailing/time-stop posisi jam 21:00 WIB
  (setelah market close, pakai harga close). Kalau SL kena siang hari,
  user baru tahu malam hari.

SOLUSI:
  Script mandiri yang menjalankan logika PositionTracker yang SAMA PERSIS
  (check_positions — reuse 100%, TIDAK ada duplikasi logika SL/TP/trailing/
  time-stop) tetapi memberi harga REAL-TIME intraday dari Invezgo
  (get_intraday). Dijalankan saat jam trading via cron (mis. 14:30 WIB).

CARA PAKAI:
  python position_check_intraday.py            # kirim alert ke Telegram
  python position_check_intraday.py --dry-run  # print saja, TIDAK kirim

ANTI-SPAM:
  Hanya kirim Telegram jika ada PERUBAHAN STATUS (level EXIT: SL kena,
  TP kena, trailing kena, time-stop habis). Semua posisi aman (HOLD) atau
  harga gagal diambil (INFO) → DIAM, tidak kirim apa pun.

CATATAN:
  check_positions() milik PositionTracker ikut meng-update state di
  data/positions.json (highest_price, trailing_stop) dan MENUTUP posisi
  yang kena SL/TP/trailing/time-stop — perilaku yang sama dengan v7_scan.
  Jadi run intraday ini otomatis memperbarui trailing & menutup posisi,
  dan run berikutnya tidak akan spam (posisi sudah tidak ada).
"""
import os
import sys
import argparse
import logging
from datetime import datetime

# ── Path setup: ROOT (folder script) + parent (untuk utils/) ──
ROOT = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(ROOT)
for _p in (ROOT, PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from position_tracker import PositionTracker          # noqa: E402
from data_invezgo import InvezgoProvider              # noqa: E402
from utils.telegram_sender import send_telegram_sync  # noqa: E402

logger = logging.getLogger("position_check_intraday")

ICONS = {"EXIT": "🚨", "HOLD": "🟢", "INFO": "ℹ️"}


def setup_logging():
    """Log ke file (untuk jejak cron headless) + console WARNING."""
    logging.basicConfig(level=logging.WARNING)
    try:
        log_path = os.path.join(ROOT, "data", "position_check_intraday.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass  # logging file gagal — jangan crash


def intraday_price(provider, ticker) -> float:
    """Ambil harga intraday terkini. Return 0.0 kalau gagal (skip ticker, lanjut).

    Catatan: SDK Invezgo sering mengisi "price" = 0.0 (mis. di luar jam aktif),
    padahal field open/high/low/close terisi. Fallback ke "close" (harga
    terakhir yang diketahui) supaya posisi tetap bisa dievaluasi — sama seperti
    v7_scan yang memberi close dari get_historical().
    """
    try:
        data = provider.get_intraday(ticker)
        if data:
            price = float(data.get("price") or 0)
            if price <= 0:
                price = float(data.get("close") or 0)
            return price
    except Exception as e:
        logger.debug("get_intraday gagal untuk %s: %s", ticker, e)
    return 0.0


def format_alert_lines(alerts: list) -> list:
    """Format alert (dari check_positions) jadi baris pesan ringkas per posisi."""
    lines = []
    for a in alerts:
        icon = ICONS.get(a.get("level", ""), "•")
        lines.append(f"{icon} {a.get('message', '')}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cek posisi terbuka pakai harga intraday (Invezgo). "
                    "Kirim alert ke Telegram kalau ada SL/TP/trailing/time-stop kena."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print saja, TIDAK kirim Telegram (default: kirim).")
    args = parser.parse_args()

    setup_logging()
    now = datetime.now().strftime("%H:%M")
    dry = args.dry_run

    tracker = PositionTracker()

    # ── Baca posisi aktif ──
    positions = tracker.get_positions()
    if not positions:
        print("Tidak ada posisi aktif")
        return 0

    print(f"Posisi aktif: {len(positions)} — {', '.join(sorted(positions.keys()))}")

    # ── Init provider intraday ──
    try:
        provider = InvezgoProvider()
    except Exception as e:
        print(f"❌ Gagal init InvezgoProvider: {e}")
        logger.error("Init provider gagal: %s", e)
        return 1

    # ── Reuse logika PositionTracker 100% — hanya ganti sumber harga ──
    alerts = tracker.check_positions(lambda tkr: intraday_price(provider, tkr))

    if not alerts:
        print("Tidak ada perubahan status")
        return 0

    # ── Yang WAJIB dikirim: hanya perubahan status (EXIT) ──
    exit_alerts = [a for a in alerts if a.get("level") == "EXIT"]
    hold_alerts = [a for a in alerts if a.get("level") != "EXIT"]

    # Tampilkan status semua posisi di console (berguna utk diagnosa/dry-run)
    if hold_alerts:
        print("Status saat ini:")
        for line in format_alert_lines(hold_alerts):
            print(f"  {line}")
    if exit_alerts:
        print("PERUBAHAN STATUS (perlu alert):")
        for line in format_alert_lines(exit_alerts):
            print(f"  {line}")

    # ── Anti-spam: tidak ada perubahan status → DIAM ──
    if not exit_alerts:
        print("Tidak ada perubahan status (SL/TP/trailing/time-stop) — tidak kirim Telegram")
        return 0

    # ── Format pesan ringkas per posisi ──
    header = f"⏰ ALERT POSISI INTRADAY ({now} WIB)"
    body_lines = format_alert_lines(exit_alerts)
    message = header + "\n" + "─" * 28 + "\n" + "\n".join(body_lines)

    if dry:
        print(f"\n[DRY-RUN] Pesan yang AKAN dikirim ke Telegram:\n{message}")
        return 0

    # ── Kirim via utils/telegram_sender (sync) ──
    ok = send_telegram_sync(message)
    if ok:
        print(f"✅ Telegram terkirim ({len(exit_alerts)} alert)")
        return 0
    print("❌ Gagal kirim Telegram")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        logger.error("FATAL: %s", e, exc_info=True)
        sys.exit(1)
