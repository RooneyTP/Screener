#!/usr/bin/env python3
"""
send_telegram_alert.py — kirim notifikasi saham via AlertManager yang sudah ada di telegram_bot.py

Usage:
    python send_telegram_alert.py --ticker BBCA --signal BUY --score 8.5 --price 6175
    python send_telegram_alert.py --custom "Portfolio update: BBCA naik 2%"
"""
import asyncio
import argparse
import sys
import os

# pastikan root project di path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

# load .env (token & chat_id)
from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from dashboard.alerts import AlertManager


def build_stock_message(ticker: str, signal: str = None, score: float = None,
                        price: int = None, sl: int = None, tp: int = None, rrr: float = None) -> str:
    lines = [f"📈 **{ticker}**"]
    if signal:
        lines.append(f"Sinyal: **{signal}**")
    if score is not None:
        lines.append(f"Skor: **{score:.1f}/15**")
    if price:
        lines.append(f"Harga: **Rp {price:,}**")
    if sl:
        lines.append(f"SL: **Rp {sl:,}**")
    if tp:
        lines.append(f"TP: **Rp {tp:,}**")
    if rrr:
        lines.append(f"RRR: **{rrr:.2f}**")
    return "\n".join(lines)


async def main():
    parser = argparse.ArgumentParser(description="Kirim alert saham ke Telegram via AlertManager")
    parser.add_argument("--ticker", help="Kode saham (contoh: BBCA)")
    parser.add_argument("--signal", help="Sinyal: BUY/SELL/TUNGGU/etc")
    parser.add_argument("--score", type=float, help="Skor teknikal (0-15)")
    parser.add_argument("--price", type=int, help="Harga terakhir")
    parser.add_argument("--sl", type=int, help="Stop Loss")
    parser.add_argument("--tp", type=int, help="Take Profit")
    parser.add_argument("--rrr", type=float, help="Risk:Reward Ratio")
    parser.add_argument("--custom", help="Pesan custom bebas (override field lain)")
    args = parser.parse_args()

    if args.custom:
        body = args.custom
        title = "📢 Custom Alert"
    else:
        if not args.ticker:
            parser.error("--ticker wajib kalau tidak pakai --custom")
        body = build_stock_message(
            ticker=args.ticker, signal=args.signal, score=args.score,
            price=args.price, sl=args.sl, tp=args.tp, rrr=args.rrr
        )
        title = f"🔔 {args.ticker} Alert"

    mgr = AlertManager()  # ambil token & chat_id dari .env otomatis
    ok = await mgr.send("INFO", title, body)
    if ok:
        print("✅ Terkirim ke Telegram")
    else:
        print("⚠️ Gagal kirim (cek token/chat_id di .env)")


if __name__ == "__main__":
    asyncio.run(main())