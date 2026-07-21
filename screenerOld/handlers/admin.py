"""Admin handlers — start, help, istilah, status, health."""
import logging
import telegram_bot as _bot
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram_bot")

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.reply_text(
        "🤖 Quant Trader Bot v6.1\n\n"
        "📈 /swing — SWING (hold 3-30 hari)\n"
        "⚡ /scalp — SCALP (day trade)\n"
        "🔍 /cek TICKER — Analisis live + fundamental\n"
        "⚡ /cepat TICKER — Ringkas\n\n"
        "📊 /sektor | /top 5 | /compare A B\n"
        "📋 /help — semua perintah\n"
        "🩺 /health — Health check",
        parse_mode=None)

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.reply_text(
        "📋 v6.1 — Semua Perintah\n\n"
        "📈 /swing — Sinyal SWING (hold)\n"
        "⚡ /scalp — Sinyal SCALP (day trade)\n"
        "🔍 /cek TICKER — Analisis lengkap\n"
        "⚡ /cepat TICKER — Ringkas\n"
        "📊 /sinyal — Semua sinyal\n"
        "📊 /sektor — Per sektor\n"
        "🔝 /top N — Top N saham\n"
        "🔄 /compare A B — Bandingkan\n"
        "📈 /report — Laporan + Discord\n"
        "🧪 /bt TICKER 90 — Backtest 90 hari\n"
        "💰 /portfolio — Portofolio\n"
        "✏️ /entry TICKER HARGA LOT — Catat entry\n"
        "✏️ /exit TICKER HARGA — Catat exit\n"
        "🩺 /status — Data + market breadth\n"
        "🩺 /health — Health check bot\n"
        "👥 /holders TICKER — Pemegang saham\n"
        "🔪 /scalp_pos — Posisi scalp aktif\n"
        "💰 /scalp_pnl — P&L scalp\n"
        "📖 /istilah — Istilah\n"
        "❓ /help — Pesan ini",
        parse_mode=None)

async def cmd_istilah(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.reply_text(
        "📖 ISTILAH v6.1\n\n"
        "MODE:\n📈 SWING — Hold 3-30 hari\n⚡ SCALP — Day trade 1-8 jam\n\n"
        "SINYAL:\n🟢ULTRA_BUY 🔵STRONG_BUY ⚪BUY 🟡PANTAU ⚫TUNGGU 🔴HINDARI\n\n"
        "ARB/ARA:\n🔴 ARB — Auto Reject Bawah (-35%, ga bisa jual)\n"
        "🔴 ARA — Auto Reject Atas (+35%, ga bisa beli)\n\n"
        "INDIKATOR:\nRSI<30 oversold | RSI>70 overbought | ADX>25 trending\n"
        "PE<12 murah | PBV<1 undervalued\n\n"
        "MARKET BREADTH: % saham di atas EMA50 — <30% = bearish kuat",
        parse_mode=None)

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    path = _bot._latest_csv()
    if not path:
        await update.message.reply_text("🩺 Bot: Online\n❌ Data: Tidak ada")
        return
    import time
    from datetime import datetime as dt
    mtime = dt.fromtimestamp(_bot.os.path.getmtime(path))
    age = (dt.now() - mtime).total_seconds() / 3600
    s = _bot._get_signals()
    mb = _bot._compute_market_breadth()
    ihsg_c, ihsg_t = _bot._fetch_ihsg_change_cached()
    breadth_str = (
        f"{'🟢' if mb['pct_above_ema50'] >= 50 else '🟡' if mb['pct_above_ema50'] >= 30 else '🔴'}"
        f" {mb['pct_above_ema50']:.0f}% above EMA50"
    ) if mb['total'] > 0 else "N/A"
    await update.message.reply_text(
        f"🩺 Bot Status v6.1\n✅ Online | 📁 {_bot.os.path.basename(path)} ({age:.1f}h)\n"
        f"📊 Sinyal: {s['total']} (🟢{len(s['ultra'])}/🔵{len(s['strong'])}/⚪{len(s['buy'])})\n"
        f"📈 IHSG: {ihsg_c:+.2f}% ({ihsg_t})\n"
        f"📊 Breadth: {breadth_str}\n"
        f"📈 /swing | ⚡ /scalp | 🔝 /top 5",
        parse_mode=None)

async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    uptime = _bot.time.time() - _bot._start_time
    uptime_str = f"{int(uptime // 86400)}d {int((uptime % 86400) // 3600)}h {int((uptime % 3600) // 60)}m"

    services_ok = True
    services_lines = []
    try:
        from core.scraper import fetch_price_data_sync
        test = fetch_price_data_sync("^JKSE", period="5d", interval="1d", skip_cache=True)
        if test is not None and not test.empty:
            services_lines.append("✅ Yahoo Finance")
        else:
            services_lines.append("❌ Yahoo Finance"); services_ok = False
    except Exception:
        services_lines.append("❌ Yahoo Finance"); services_ok = False

    csv_ok = bool(_bot._latest_csv())
    services_lines.append(f"{'✅' if csv_ok else '❌'} CSV Data")
    if not csv_ok: services_ok = False

    import os as _os
    db_ok = _os.path.exists(_os.path.join(_bot.ROOT, "portofolio_virtual.db"))
    services_lines.append(f"{'✅' if db_ok else '⚠️'} Portfolio DB")

    mem_mb = _bot.psutil.Process().memory_info().rss / 1024 / 1024

    msg = (
        f"{'✅' if services_ok else '⚠️'} Bot Health v6.1\n\n"
        f"⏱️ Uptime: {uptime_str}\n"
        f"💾 Memory: {mem_mb:.1f} MB\n"
        f"🧵 Threads: {_bot.psutil.Process().num_threads()}\n"
        f"---\n" + "\n".join(services_lines)
    )
    await update.message.reply_text(msg, parse_mode=None)

def register(app):
    """Register admin command handlers."""
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("istilah", cmd_istilah))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("health", cmd_health))
    logger.info("Admin handlers: start, help, istilah, status, health")
