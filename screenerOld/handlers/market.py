"""Market data handlers — sektor, top, compare, report."""
import logging
import telegram_bot as _bot
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

logger = logging.getLogger("telegram_bot")

async def cmd_sektor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    df = _bot._get_csv_dataframe()
    if df is None or "Sektor" not in df.columns:
        await update.message.reply_text("📊 Data sektor tidak tersedia."); return
    buy_mask = df["Sinyal"].isin(["ULTRA_BUY", "STRONG_BUY", "BUY"]) if "Sinyal" in df.columns else _bot.pd.Series(False, index=df.index)
    sector_stats = df.groupby("Sektor").agg(
        Count=("Ticker", "count"),
        Buy=("Sinyal", lambda x: (x.isin(["ULTRA_BUY", "STRONG_BUY", "BUY"])).sum() if "Sinyal" in df.columns else 0),
        AvgSkor=("Skor", "mean") if "Skor" in df.columns else _bot.pd.Series(),
    ).sort_values("Buy", ascending=False)
    lines = ["📊 Rotasi Sektor Hari Ini\n"]
    for sektor, row in sector_stats.iterrows():
        lines.append(f"  {sektor}: {int(row['Buy'])} BUY / {int(row['Count'])} saham | Avg Skor {row.get('AvgSkor', 0):.1f}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_top(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    n = 5
    if ctx.args and ctx.args[0].isdigit(): n = min(20, max(1, int(ctx.args[0])))
    await update.message.chat.send_action("typing")
    df = _bot._get_csv_dataframe()
    if df is None: await update.message.reply_text("📊 Tidak ada data."); return
    if "Skor" not in df.columns: await update.message.reply_text("📊 Kolom skor tidak tersedia."); return
    top = df.nlargest(n, "Skor")[["Ticker", "Sinyal", "Harga", "Skor", "Confidence%", "RRR", "Sektor"]]
    lines = [f"🔝 Top {n} Saham Hari Ini\n"]
    for _, r in top.iterrows():
        emoji = {"ULTRA_BUY": "🟢", "STRONG_BUY": "🔵", "BUY": "⚪", "PANTAU": "🟡", "TUNGGU": "⚫"}.get(r.get("Sinyal", ""), "⚫")
        lines.append(f"{emoji} {r['Ticker']} — Rp{int(r['Harga']):,} | ⭐{r['Skor']} | {r.get('Sektor', '?')}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_compare(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("⚠️ /compare TICKER1 TICKER2"); return
    await update.message.chat.send_action("typing")
    import asyncio
    results = []
    for tkr in ctx.args[:4]:
        d = await asyncio.to_thread(_bot._lookup_ticker_live, tkr, compact=True)
        if d and d.get("_error"): d = _bot._search_ticker(tkr)
        results.append((tkr, d))
    lines = ["🔄 Compare\n"]
    for tkr, d in results:
        if not d: lines.append(f"❌ {tkr.upper()}: tidak ditemukan"); continue
        emoji = {"ULTRA_BUY": "🟢", "STRONG_BUY": "🔵", "BUY": "⚪", "PANTAU": "🟡", "TUNGGU": "⚫", "HINDARI": "🔴"}.get(d.get("Sinyal", ""), "❓")
        lines.append(f"{emoji} {d.get('Ticker', '?')} — {d.get('Sinyal', '?')} | Rp{int(d.get('Harga', 0)):,} | "
                     f"⭐{d.get('Skor', '?')}/15 | RRR{d.get('RRR', '?')} | PE{d.get('PE', '?')} | PBV{d.get('PBV', '?')}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    _bot._run_screener_background()
    s = _bot._get_signals()
    if s["total"] == 0: await update.message.reply_text("📊 Tidak ada sinyal.\n🔄 Screener berjalan..."); return
    try:
        from dashboard.alerts import AlertManager
        am = AlertManager()
        result = am.send_screener_report(s["ultra"], s["strong"], s["buy"])
        await update.message.reply_text(f"📈 {result}\n📈 /swing | ⚡ /scalp")
    except ImportError:
        await update.message.reply_text("📊 Dashboard module not available. Run screener.py first.\n📈 /swing | ⚡ /scalp")

def register(app):
    """Register market data command handlers."""
    app.add_handler(CommandHandler("sektor", cmd_sektor))
    app.add_handler(CommandHandler("top", cmd_top))
    app.add_handler(CommandHandler("compare", cmd_compare))
    app.add_handler(CommandHandler("report", cmd_report))
    logger.info("Market handlers: sektor, top, compare, report")
