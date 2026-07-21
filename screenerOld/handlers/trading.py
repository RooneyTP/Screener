"""Trading signal handlers — cek, cepat, swing, scalp, sinyal."""
import logging
import telegram_bot as _bot
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
import asyncio

logger = logging.getLogger("telegram_bot")

async def _render_cek(update: Update, ticker: str, compact: bool = False):
    await update.message.chat.send_action("typing")
    data = await asyncio.to_thread(_bot._lookup_ticker_live, ticker, compact)
    if data and data.get("_error"):
        await update.message.reply_text(f"❌ {data['_error']}"); return
    source = "🌐 YH Finance Live"
    if not data:
        data = _bot._search_ticker(ticker); source = "📁 Screener CSV"
    if not data:
        await update.message.reply_text(f"❌ {ticker} tidak ditemukan.", parse_mode=None); return

    def _e(t): return _bot._html.escape(str(t)) if not isinstance(t, str) else _bot._html.escape(t)
    emoji = {"ULTRA_BUY": "🟢", "STRONG_BUY": "🔵", "BUY": "⚪", "PANTAU": "🟡", "TUNGGU": "⚫", "HINDARI": "🔴"}.get(data.get("Sinyal", ""), "❓")
    change = data.get("Change_pct", float('nan'))
    change_str = f" ({round(change, 2):+.2f}%)" if change == change else ""
    arb_warn = data.get("ARB_Warning", ""); arb_line = f"⚠️ <b>{_e(arb_warn)}</b>\n" if arb_warn else ""
    hold = _e(data.get("Hold", "")); hold_line = f"⏱️ Hold: {hold}\n" if hold else ""
    vol_line = f"Vol: {data.get('Vol_Ratio', 0):.1f}x avg" if data.get("Vol_Ratio", 0) > 0.01 else "Vol: market tutup"
    vs = data.get("Vol_Spike", "NO"); spike_str = f"\n🔥 <b>VOLUME SPIKE!</b> — {_e(data.get('Vol_Spike_Label', ''))}" if vs in ("EXTREME", "YES", "60D_HIGH") else ""
    ihsg_c, ihsg_t = data.get("IHSG_Change", 0), data.get("IHSG_Trend", "?")
    market_str = f" | IHSG: {ihsg_c:+.1f}% ({_e(ihsg_t)})"
    strength = data.get("Strength", ""); strength_str = f" [{strength}]" if strength else ""

    if compact:
        msg = (f"{emoji} {data.get('Ticker', '?')} — {data.get('Sinyal', '?')}{strength_str} "
               f"Rp{int(data.get('Harga', 0)):,}{change_str}\n"
               f"⭐{data.get('Skor', '?')}/15 | Conf {data.get('Confidence%', '?')}% | RSI {data.get('RSI', '?')}\n"
               f"🔻Rp{int(data.get('Support', 0) or 0):,} | 🔺Rp{int(data.get('Resistance', 0) or 0):,} | "
               f"🛑Rp{int(data.get('Stop_Loss', 0) or 0):,} | 🎯Rp{int(data.get('Target_1', 0) or 0):,} | RRR {data.get('RRR', '?')}\n"
               f"⏱️{hold}\n{arb_line}"
               f"📅 {data.get('Weekly_Trend', '?')}W | {data.get('Monthly_Trend', '?')}M | {data.get('Regime', '?')}\n"
               f"{source}")
        await update.message.reply_text(msg, parse_mode=None)
        return

    pe = data.get("PE", 0); pbv = data.get("PBV", 0)
    fund_line = ""
    if pe > 0 or pbv > 0:
        parts = []
        if pe > 0: parts.append(f"PE: {pe:.1f}")
        if pbv > 0: parts.append(f"PBV: {pbv:.2f}")
        fund_line = " | " + " | ".join(parts)

    msg = (
        f"{emoji} {_e(str(data.get('Ticker', '?')))} — {_e(str(data.get('Sinyal', '?')))}{strength_str}{change_str}\n"
        f"{hold_line}{arb_line}{spike_str}\n"
        f"💰 Harga: Rp {int(data.get('Harga', 0)):,}\n"
        f"⭐ Skor: {data.get('Skor', '?')}/15 | Conf: {data.get('Confidence%', '?')}% | "
        f"Tech:{data.get('Tech_Score', '?')} Fund:{data.get('Fund_Score', '?')}\n"
        f"📈 RSI:{data.get('RSI', '?')} ADX:{data.get('ADX', '?')} MACD:{_e(str(data.get('MACD', '?')))} | {vol_line}{fund_line}\n"
        f"📐 BB:{data.get('BB_Width%', '?')}% Pattern:{_e(str(data.get('Pattern', '?')))}{market_str}\n\n"
        f"🔻 Support: Rp{int(data.get('Support', 0) or 0):,} | 🔺 Resistance: Rp{int(data.get('Resistance', 0) or 0):,}\n"
        f"🛑 SL Rp{int(data.get('Stop_Loss', 0) or 0):,} | 🎯 TP1 Rp{int(data.get('Target_1', 0) or 0):,} | "
        f"TP2 Rp{int(data.get('Target_2', 0) or 0):,}\n"
        f"⚖️ RRR:{data.get('RRR', '?')} | ATR:{data.get('ATR', '?')}\n\n"
        f"🐋 MM:{_e(str(data.get('MM_Activity', '?')))} ({data.get('MM_Confidence', '?')}%) | "
        f"AI:{_e(str(data.get('AI_Verdict', '?')))} ({data.get('AI_Win_Prob%', '?')}%)\n"
        f"📅 {_e(str(data.get('Weekly_Trend', '?')))}W | {_e(str(data.get('Monthly_Trend', '?')))}M | {_e(str(data.get('Regime', '?')))}"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def cmd_cek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("⚠️ /cek TICKER"); return
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await _render_cek(update, ctx.args[0], compact=False)

async def cmd_cepat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("⚠️ /cepat TICKER"); return
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await _render_cek(update, ctx.args[0], compact=True)

async def _show_dual(update: Update, mode: str):
    await update.message.chat.send_action("typing")
    _bot._run_screener_background()
    s = _bot._get_signals()
    if s["total"] == 0:
        await update.message.reply_text("📊 Tidak ada sinyal.\n🔄 Screener berjalan..."); return
    if mode == "swing":
        ultra = [r for r in s["ultra"] if not _bot._is_scalp_csv(r)]
        strong = [r for r in s["strong"] if not _bot._is_scalp_csv(r)]
        buy = [r for r in s["buy"] if not _bot._is_scalp_csv(r)]
        title = "📈 SWING (Hold 3-30 Hari)"; subtitle = ""
    else:
        ultra = [r for r in s["ultra"] if _bot._is_scalp_csv(r)]
        strong = [r for r in s["strong"] if _bot._is_scalp_csv(r)]
        buy = [r for r in s["buy"] if _bot._is_scalp_csv(r)]
        title = "⚡ SCALP (Day Trade 1-8 Jam)"; subtitle = ""
    total = len(ultra) + len(strong) + len(buy)
    if total == 0:
        await update.message.reply_text(
            f"{'📈' if mode == 'swing' else '⚡'} Tidak ada sinyal {'SWING' if mode == 'swing' else 'SCALP'}.\n"
            f"Coba /{'scalp' if mode == 'swing' else 'swing'}"); return
    lines = [f"{'📈' if mode == 'swing' else '⚡'} {title} — {total} saham{subtitle}\n"]
    if ultra:
        lines.append("🟢 ULTRA BUY")
        for r in ultra[:8]:
            lines.append(f"  {r['Ticker']} — Rp{int(r.get('Harga', 0)):,} | RRR{r.get('RRR', '?')} | {r.get('AI_Verdict', '?')}")
    if strong:
        lines.append("\n🔵 STRONG BUY")
        for r in strong[:8]:
            lines.append(f"  {r['Ticker']} — Rp{int(r.get('Harga', 0)):,} | Conf{r.get('Confidence%', '?')}%")
    if buy:
        lines.append("\n⚪ BUY")
        for r in buy[:8]:
            lines.append(f"  {r['Ticker']} — Rp{int(r.get('Harga', 0)):,} | RRR{r.get('RRR', '?')}")
    lines.append(f"\n🔍 /cek TICKER | {'⚡ /scalp' if mode == 'swing' else '📈 /swing'}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_swing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await _show_dual(update, "swing")

async def cmd_scalp(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await _show_dual(update, "scalp")

async def cmd_sinyal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    _bot._run_screener_background()
    s = _bot._get_signals()
    if s["total"] == 0:
        await update.message.reply_text("📊 Tidak ada sinyal.\n🔄 Screener berjalan..."); return
    lines = [f"📊 Sinyal — {s['total']} saham\n"]
    if s["ultra"]:
        lines.append("🟢 ULTRA BUY")
        for r in s["ultra"][:8]:
            lines.append(f"  {r['Ticker']} — Rp{int(r.get('Harga', 0)):,} | RRR{r.get('RRR', '?')} | {r.get('AI_Verdict', '?')}")
    if s["strong"]:
        lines.append("\n🔵 STRONG BUY")
        for r in s["strong"][:8]:
            lines.append(f"  {r['Ticker']} — Rp{int(r.get('Harga', 0)):,} | Conf{r.get('Confidence%', '?')}%")
    if s["buy"]:
        lines.append("\n⚪ BUY")
        for r in s["buy"][:8]:
            lines.append(f"  {r['Ticker']} — Rp{int(r.get('Harga', 0)):,} | RRR{r.get('RRR', '?')}")
    lines.append(f"\n📈 /swing | ⚡ /scalp | 🔍 /cek TICKER")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

def register(app):
    """Register trading signal command handlers."""
    app.add_handler(CommandHandler("cek", cmd_cek))
    app.add_handler(CommandHandler("cepat", cmd_cepat))
    app.add_handler(CommandHandler("swing", cmd_swing))
    app.add_handler(CommandHandler("scalp", cmd_scalp))
    app.add_handler(CommandHandler("sinyal", cmd_sinyal))
    logger.info("Trading handlers: cek, cepat, swing, scalp, sinyal")
