"""Portfolio & backtest handlers — portfolio, entry, exit, bt, btall, scalp_pos, scalp_pnl, holders."""
import logging, os, asyncio
import telegram_bot as _bot
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from datetime import datetime

logger = logging.getLogger("telegram_bot")

async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    db = os.path.join(_bot.ROOT, "portofolio_virtual.db")
    if not os.path.exists(db): await update.message.reply_text("💰 Belum ada data portofolio."); return
    with _bot._get_db() as conn:
        cash_row = conn.execute("SELECT saldo_cash FROM akun").fetchone()
        cash = cash_row[0] if cash_row else 0
        pos = conn.execute("SELECT ticker,harga_beli,shares,sl,tp FROM posisi").fetchall()
    pos_val = sum(p[1] * p[2] for p in pos); equity = cash + pos_val; pnl = equity - _bot.INITIAL_CASH
    lines = [f"💰 Portfolio", f"Equity: Rp{equity:,.0f} | Cash: Rp{cash:,.0f}",
             f"P&L: Rp{pnl:+,.0f} ({pnl / _bot.INITIAL_CASH * 100:+.2f}%)"]
    if pos:
        lines.append(f"\nOpen ({len(pos)})")
        for p in pos: lines.append(f"  {p[0]} — {p[2]}s @ Rp{p[1]:,.0f}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_entry(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    if len(ctx.args) < 3: await update.message.reply_text("⚠️ /entry TICKER HARGA LOT\nContoh: /entry BBCA 10500 5"); return
    tkr = ctx.args[0].upper().replace(".JK", "")
    try: harga = float(ctx.args[1]); lot = int(ctx.args[2])
    except (ValueError, IndexError): await update.message.reply_text("⚠️ Format: /entry BBCA 10500 5"); return
    shares = lot * 100
    with _bot._get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS akun (saldo_cash REAL)")
        conn.execute("CREATE TABLE IF NOT EXISTS posisi (ticker TEXT, harga_beli REAL, sl REAL, tp REAL, shares INTEGER, "
                     "tanggal TEXT, highest_price REAL DEFAULT 0, strategy TEXT DEFAULT 'manual')")
        conn.execute("CREATE TABLE IF NOT EXISTS histori_trade (ticker TEXT, pnl REAL, status TEXT, tanggal TEXT, "
                     "strategy TEXT DEFAULT 'manual')")
        cur = conn.cursor()
        cur.execute("SELECT saldo_cash FROM akun")
        if not cur.fetchone(): cur.execute("INSERT INTO akun VALUES (?)", (_bot.INITIAL_CASH,))
        cur.execute("INSERT INTO posisi (ticker,harga_beli,sl,tp,shares,tanggal) VALUES (?,?,?,?,?,?)",
                    (tkr, harga, harga * _bot.DEFAULT_SL_PCT, harga * _bot.DEFAULT_TP_PCT, shares,
                     datetime.now().strftime("%Y-%m-%d")))
    await update.message.reply_text(
        f"✅ Entry: {tkr} — {shares} shares @ Rp{harga:,.0f}\n"
        f"SL: Rp{harga * _bot.DEFAULT_SL_PCT:,.0f} | TP: Rp{harga * _bot.DEFAULT_TP_PCT:,.0f}",
        parse_mode=None)

async def cmd_exit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    if len(ctx.args) < 2: await update.message.reply_text("⚠️ /exit TICKER HARGA"); return
    tkr = ctx.args[0].upper().replace(".JK", "")
    try: harga = float(ctx.args[1])
    except (ValueError, IndexError): await update.message.reply_text("⚠️ Format: /exit BBCA 10800"); return
    if not os.path.exists(os.path.join(_bot.ROOT, "portofolio_virtual.db")):
        await update.message.reply_text("💰 Belum ada data."); return
    with _bot._get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT rowid,harga_beli,shares,sl,tp FROM posisi WHERE ticker=?", (tkr,))
        pos = cur.fetchone()
        if not pos: await update.message.reply_text(f"❌ Tidak ada posisi untuk {tkr}"); return
        rowid, entry, shares, sl, tp = pos
        pnl = (harga - entry) * shares; pnl_pct = (harga - entry) / entry * 100
        cur.execute("DELETE FROM posisi WHERE rowid=?", (rowid,))
        cur.execute("INSERT INTO histori_trade VALUES (?,?,?,?,?)",
                    (tkr, pnl, "MANUAL_EXIT", datetime.now().strftime("%Y-%m-%d"), "manual"))
    emoji = "🟢" if pnl > 0 else "🔴"
    await update.message.reply_text(
        f"{emoji} Exit: {tkr} @ Rp{harga:,.0f}\nPnL: Rp{pnl:+,.0f} ({pnl_pct:+.2f}%)\n"
        f"Entry Rp{entry:,.0f} → Exit Rp{harga:,.0f}", parse_mode=None)

async def cmd_bt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    if not ctx.args: await update.message.reply_text("⚠️ /bt TICKER [HARI]\nContoh: /bt BBCA 90"); return
    tkr = ctx.args[0].upper().replace(".JK", "")
    days = 90
    if len(ctx.args) > 1 and ctx.args[1].isdigit(): days = min(365, max(7, int(ctx.args[1])))
    await update.message.chat.send_action("typing")
    try:
        from core.scraper import fetch_price_data_sync
        df = fetch_price_data_sync(tkr, period=f"{days}d" if days <= 60 else f"{days // 30}mo",
                                   interval="1d", skip_cache=True)
        if df is None or df.empty: await update.message.reply_text(f"❌ Data tidak tersedia untuk {tkr}"); return
        close = df["Close"].astype(float); pct = close.pct_change().dropna()
        if len(pct) < 10: await update.message.reply_text(f"❌ Data terlalu sedikit ({len(pct)} hari)"); return
        wins = pct[pct > 0]; losses = pct[pct < 0]
        win_rate = len(wins) / len(pct) * 100
        avg_win = wins.mean() * 100 if len(wins) > 0 else 0
        avg_loss = losses.mean() * 100 if len(losses) > 0 else 0
        cum_ret = (1 + pct).cumprod().iloc[-1]; total_ret = (cum_ret - 1) * 100
        import numpy as np
        sharpe = (pct.mean() - _bot.RISK_FREE_RATE / 252) / pct.std() * np.sqrt(252) if pct.std() > 0 else 0
        max_dd = ((1 + pct).cumprod().cummax() - (1 + pct).cumprod()).max() * 100
        await update.message.reply_text(
            f"🧪 Backtest {tkr} — {days} hari ({len(pct)} trades)\n\n"
            f"📊 Win Rate: {win_rate:.1f}%\n"
            f"💰 Total Return: {total_ret:+.2f}%\n"
            f"📈 Sharpe: {sharpe:.2f}\n"
            f"📉 Max DD: {max_dd:.1f}%\n"
            f"✅ Avg Win: {avg_win:+.2f}% | ❌ Avg Loss: {avg_loss:+.2f}%\n"
            f"⚖️ Avg Win/Loss: {abs(avg_win / max(0.001, abs(avg_loss))):.1f}x\n\n"
            f"🤖 Simulasi buy-and-hold, tanpa SL/TP.",
            parse_mode=None)
    except Exception as e:
        _bot.logger.error("Backtest failed for %s: %s", tkr, e)
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_btall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    path = _bot._latest_csv()
    if not path: await update.message.reply_text("📊 Tidak ada data screener."); return
    df = _bot.pd.read_csv(path)
    signals = df[df["Sinyal"].isin(["ULTRA_BUY", "STRONG_BUY"])]
    if signals.empty:
        await update.message.reply_text("📊 Tidak ada sinyal ULTRA_BUY atau STRONG_BUY."); return
    tickers = signals["Ticker"].head(10).tolist()
    await update.message.reply_text(f"🧪 Backtest {len(tickers)} ticker... Mohon tunggu.")
    results = []
    for tkr in tickers:
        try:
            from core.scraper import fetch_price_data_sync
            df_bt = fetch_price_data_sync(tkr, period="3mo", interval="1d", skip_cache=False)
            if df_bt is None or df_bt.empty or len(df_bt) < 10: continue
            close = df_bt["Close"].astype(float)
            pct = close.pct_change().dropna()
            ret = (1 + pct).cumprod().iloc[-1] - 1
            import numpy as np
            sharpe = (pct.mean() * 252) / (pct.std() * np.sqrt(252)) if pct.std() > 0 else 0
            results.append({"Ticker": tkr, "Return%": round(ret * 100, 2), "Sharpe": round(sharpe, 2)})
        except Exception:
            continue
    if not results:
        await update.message.reply_text("❌ Gagal backtest semua ticker."); return
    lines = ["🧪 Backtest Multi-Ticker (3 bulan)\n"]
    for r in sorted(results, key=lambda x: x["Return%"], reverse=True):
        emoji = "🟢" if r["Return%"] > 0 else "🔴"
        lines.append(f"{emoji} {r['Ticker']}: Return {r['Return%']:+.2f}% | Sharpe {r['Sharpe']:.2f}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_scalp_pos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    if not os.path.exists(os.path.join(_bot.ROOT, "portofolio_virtual.db")):
        await update.message.reply_text("📋 Belum ada"); return
    with _bot._get_db() as conn:
        rows = conn.execute(
            "SELECT ticker,harga_beli,sl,tp,shares,highest_price FROM posisi WHERE strategy='scalp'"
        ).fetchall()
    if not rows: await update.message.reply_text("📋 Tidak ada posisi scalp."); return
    lines = [f"📋 Scalp Positions — {len(rows)}\n"]
    for r in rows:
        tkr, entry, sl, tp, shares, peak = r
        profit = (peak - entry) / entry * 100 if entry > 0 else 0
        lines.append(f"{'🟢' if profit > 0 else '🔴'} {tkr} {shares}s @ Rp{int(entry):,} | +{profit:.2f}% | SL Rp{int(sl):,}")
    await update.message.reply_text("\n".join(lines), parse_mode=None)

async def cmd_scalp_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    await update.message.chat.send_action("typing")
    today = datetime.now().strftime("%Y-%m-%d")
    if not os.path.exists(os.path.join(_bot.ROOT, "portofolio_virtual.db")):
        await update.message.reply_text("📊 Belum ada"); return
    with _bot._get_db() as conn:
        df = _bot.pd.read_sql(
            "SELECT pnl,status FROM histori_trade WHERE strategy='scalp' AND tanggal=?",
            conn, params=[today])
    if df.empty:
        await update.message.reply_text(f"📊 Scalp PnL {today}\nBelum ada trade.", parse_mode=None); return
    total = int(df["pnl"].sum()); wins = int((df["pnl"] > 0).sum()); wr = wins / max(1, len(df)) * 100
    await update.message.reply_text(f"📊 Scalp PnL {today}\nTotal: Rp{total:+,.0f}\nTrades: {len(df)} | WR: {wr:.0f}%",
                                    parse_mode=None)

async def cmd_holders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await _bot._check_rate_limit(update.effective_user.id):
        await update.message.reply_text(f"⏳ Mohon tunggu {_bot.RATE_LIMIT_WINDOW} detik sebelum mengirim perintah lagi.")
        return
    if not ctx.args: await update.message.reply_text("⚠️ /holders TICKER\nContoh: /holders BBCA"); return
    tkr = ctx.args[0].upper().replace(".JK", "")
    await update.message.chat.send_action("typing")
    try:
        path = _bot._latest_csv()
        if not path: await update.message.reply_text("📊 Tidak ada data CSV screener."); return
        df = _bot.pd.read_csv(path)
        match = df[df["Ticker"].astype(str).str.upper() == tkr]
        if match.empty:
            await update.message.reply_text(f"❌ {tkr} tidak ditemukan di data screener.", parse_mode=None); return
        row = match.iloc[0]
        harga = float(row.get("Harga", 0) or 0)
        ts = int(float(row.get("Shares_Outstanding", 0) or 0))
        fs = int(float(row.get("Float_Shares", 0) or 0))
        mcap = float(row.get("Market_Cap_IDR", 0) or 0)
        sektor = str(row.get("Sektor", "?"))
        sinyal = str(row.get("Sinyal", "?"))
        skor = float(row.get("Skor", 0) or 0)
        confidence = float(row.get("Confidence%", 0) or 0)
        ff_pct = round(fs / ts * 100, 2) if ts > 0 and fs > 0 else 0
        mm_float_pct = float(row.get("MM_Float_Pct", 0) or 0)
        dominance = str(row.get("Dominance", "N/A"))
        weekly_trend = str(row.get("Weekly_Trend", "?"))
        regime = str(row.get("Regime", "?"))
        try:
            from shareholder_analyzer import analyze_shareholder_structure
            ksei = analyze_shareholder_structure(tkr, total_shares_outstanding=ts, track_trend=True, float_shares=fs)
        except Exception:
            ksei = {"status": "error"}
        msg = (
            f"📋 Informasi Saham — {tkr}\n\n"
            f"🏢 Sektor: {sektor}\n💰 Harga: Rp{harga:,.0f}\n📊 Market Cap: Rp{mcap:,.0f}\n"
            f"📦 Shares Outstanding: {ts:,} lbr\n📊 Free Float: {ff_pct:.1f}% ({fs:,} lbr)\n"
            f"🏦 MM Float: {mm_float_pct:.1f}%\n👑 Dominance: {dominance}\n"
            f"📈 Trend: {weekly_trend} | Regime: {regime}\n"
            f"⭐ Skor: {skor}/15 | Confidence: {confidence}%\n🚦 Sinyal: {sinyal}\n\n"
        )
        if ksei.get("status") == "ok" and ksei.get("n_holders", 0) > 0:
            top = ksei.get("top_holders", [])
            holder_lines = []
            for h in top:
                nm = h.get("name", "?")
                pct = h.get("pct", 0)
                cls = h.get("classification", "")
                emoji_cls = "🏦" if "MM" in cls else ("👤" if "INSIDER" in cls else "👥")
                holder_lines.append(f"  {emoji_cls} {nm} — {pct:.2f}%")
            msg += f"📋 Struktur Pemegang Saham (KSEI):\n"
            msg += f"  🏦 MM: {ksei.get('mm_pct', 0):.1f}% | 👥 Retail: {ksei.get('retail_pct', 0):.1f}%\n"
            msg += f"  📊 Free Float KSEI: {ksei.get('free_float_pct', 0):.1f}%\n"
            if ksei.get("mm_trend") != "N/A":
                trend_emoji = "📈" if "ACCUM" in str(ksei.get("mm_trend", "")) else "📉"
                msg += f"  {trend_emoji} MM Trend: {ksei.get('mm_trend', '?')} ({ksei.get('mm_trend_pct', 0):+.1f}%)\n"
            msg += f"  👑 Dominance: {str(ksei.get('dominance', '?'))}\n"
            msg += f"\nTop {len(holder_lines)} Holders:\n" + "\n".join(holder_lines)
            msg += f"\n\n📁 Sumber: KSEI + CSV Screener"
        else:
            msg += f"📁 Sumber: CSV Screener\n"
            if ksei.get("status") == "no_data":
                msg += "Tidak ada data pemegang saham dari KSEI untuk emiten ini."
        await update.message.reply_text(msg)
    except Exception as e:
        _bot.logger.error("Holders command failed for %s: %s", tkr, e)
        await update.message.reply_text(f"❌ Error: {e}")

def register(app):
    """Register portfolio & backtest command handlers."""
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("entry", cmd_entry))
    app.add_handler(CommandHandler("exit", cmd_exit))
    app.add_handler(CommandHandler("bt", cmd_bt))
    app.add_handler(CommandHandler("btall", cmd_btall))
    app.add_handler(CommandHandler("scalp_pos", cmd_scalp_pos))
    app.add_handler(CommandHandler("scalp_pnl", cmd_scalp_pnl))
    app.add_handler(CommandHandler("holders", cmd_holders))
    logger.info("Portfolio handlers: portfolio, entry, exit, bt, btall, scalp_pos, scalp_pnl, holders")
