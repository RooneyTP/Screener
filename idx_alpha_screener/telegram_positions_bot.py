"""
telegram_positions_bot.py — Bot Telegram untuk perintah posisi
================================================================
Polling pesan dari grup Saham, handle command:
  /posisi             → list posisi aktif + status
  /posisi add TICKER HARGA LOT [SL] [TP] [mode]  → tambah posisi
  /posisi close TICKER → tutup posisi
  /posisi del TICKER   → hapus posisi (alias close)

Jalankan sebagai service background. TTL 60 detik (tidak mengganggu cron).
"""
import os, sys, json, re, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from position_tracker import PositionTracker, format_position_alerts

# ── Telegram (kredensial dari .env root repo — JANGAN hardcode token di source) ──
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "-5237365204")
if not BOT_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN tidak ditemukan di .env — hentikan.", file=sys.stderr)
    sys.exit(1)
POLL_TIMEOUT = 60  # long-poll detik
ALLOWED_USERS = {"7794835737"}  # hanya Yan

import urllib.request, urllib.parse


def _api(method: str, **params) -> dict:
    try:
        data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            data=data, method="POST")
        resp = urllib.request.urlopen(req, timeout=70)
        return json.loads(resp.read())
    except Exception:
        return {"ok": False}


def send_message(text: str) -> bool:
    return _api("sendMessage", chat_id=CHAT_ID, text=text).get("ok", False)


def get_updates(offset: int) -> list:
    res = _api("getUpdates", offset=offset, timeout=POLL_TIMEOUT)
    return res.get("result", []) if res.get("ok") else []


def handle_command(text: str) -> str:
    """Parse & jalankan command. Return pesan balasan."""
    parts = text.strip().split()
    if not parts:
        return "Gunakan: /posisi add BRPT 1840 5 [SL] [TP]"
    cmd = parts[0].lower()
    tracker = PositionTracker()

    if cmd == "/posisi" or cmd == "/posisi@quantlyan_bot":
        if len(parts) == 1:
            # List posisi
            positions = tracker.get_positions()
            if not positions:
                return "📭 Belum ada posisi aktif.\n\nCara: /posisi add BRPT 1840 5"
            lines = ["📌 POSISI AKTIF", "─" * 25]
            for tkr, p in positions.items():
                entry = p["entry_price"]
                sl = p["stop_loss"]
                tp = p["take_profit"]
                lots = p["lots"]
                mode = p.get("mode", "swing")
                lines.append(f"{tkr} {lots} lot @ {entry:,.0f} ({mode})")
                lines.append(f"   SL {sl:,.0f} | TP {tp:,.0f}")
            return "\n".join(lines)

        sub = parts[1].lower()
        if sub in ("add", "tambah", "+"):
            # Format: add TICKER HARGA LOT [SL] [TP] [mode]
            try:
                ticker = parts[2].upper()
                price = float(parts[3])
                lots = int(parts[4]) if len(parts) > 4 else 1
                sl = float(parts[5]) if len(parts) > 5 else 0
                tp = float(parts[6]) if len(parts) > 6 else 0
                mode = parts[7] if len(parts) > 7 else "swing"
                if mode not in ("swing", "intraday"):
                    mode = "swing"
                ok = tracker.add_position(ticker, price, lots, sl, tp, mode)
                if ok:
                    return f"✅ Posisi ditambahkan: {ticker} {lots} lot @ {price:,.0f} ({mode})\nSL {sl or price*0.95:,.0f} | TP {tp or price*1.10:,.0f}"
                return f"❌ Gagal tambah {ticker} — cek format."
            except (IndexError, ValueError):
                return "❌ Format salah. Contoh:\n/posisi add BRPT 1840 5 1692 2057"

        if sub in ("close", "del", "jual", "-"):
            if len(parts) < 3:
                return "❌ Format: /posisi close TICKER"
            ticker = parts[2].upper()
            ok = tracker.close_position(ticker)
            return f"✅ {ticker} ditutup." if ok else f"❌ {ticker} tidak ada di posisi."

    return "Command tidak dikenal. Gunakan /posisi (list), /posisi add TICKER HARGA LOT [SL] [TP]"


def main():
    print(f"[{datetime.now():%H:%M:%S}] Bot /posisi started (poll every {POLL_TIMEOUT}s)")
    offset = 0
    while True:
        try:
            updates = get_updates(offset)
            for upd in updates:
                offset = max(offset, upd.get("update_id", 0) + 1)
                msg = upd.get("message", {})
                chat = msg.get("chat", {})
                # Hanya dari grup target
                if str(chat.get("id", "")) != CHAT_ID:
                    continue
                user_id = str(msg.get("from", {}).get("id", ""))
                if user_id not in ALLOWED_USERS:
                    continue
                text = msg.get("text", "")
                if text and text.startswith("/"):
                    reply = handle_command(text)
                    if reply:
                        send_message(reply)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
