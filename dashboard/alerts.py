# dashboard/alerts.py — Centralized alert dispatcher (SKILL.md §⑥)
# Channels: Discord embed + Telegram + persistent log
# Alerts: daily drawdown >3% warning, >5% halt, unrealized loss >2%, API error, kill switch
#
# ── STRATEGY ASSUMPTIONS ─────────────────────────────────────────
# Discord: embed berwarna (blue=info, yellow=warning, red=critical)
# Telegram: pesan teks (max 4096 karakter)
# All alerts also logged to logs/screener_YYYYMMDD.log
# ─────────────────────────────────────────────────────────────────

import os
import sys
import logging
import asyncio
from datetime import datetime


def _run_async_safely(coro):
    """Run async coroutine safely — works inside or outside existing event loop.

    asyncio.run() crashes with RuntimeError if called from within a running
    event loop (e.g., inside telegram_bot.py's run_polling()). This wrapper
    detects the current state and uses the correct method.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    # Already in an event loop — use run_coroutine_threadsafe in a new thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=30)


# ── Load .env FIRST (before reading any env vars) ──────────────────
from dotenv import load_dotenv
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_ENV_PATH)

logger = logging.getLogger("alerts")

TELEGRAM_AVAILABLE = False
try:
    import telegram
    TELEGRAM_AVAILABLE = True
except ImportError:
    pass


class AlertManager:
    """Centralized alert dispatcher — Discord + Telegram + persistent log."""

    def __init__(
        self,
        discord_webhook: str = "",
        telegram_token: str = "",
        telegram_chat_id: str = "",
    ):
        self.discord_webhook = discord_webhook or os.getenv("DISCORD_WEBHOOK", "")
        self.telegram_token = telegram_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.telegram_chat_id = telegram_chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self.telegram_bot = None

        if TELEGRAM_AVAILABLE and self.telegram_token:
            try:
                self.telegram_bot = telegram.Bot(token=self.telegram_token)
                logger.info("Telegram bot connected")
            except Exception as e:
                logger.warning("Telegram bot init failed: %s", e)

    async def _telegram_send(self, text: str) -> None:
        """Async send message via Telegram bot."""
        if self.telegram_bot:
            await self.telegram_bot.send_message(
                chat_id=self.telegram_chat_id,
                text=text,
            )

    def send(self, level: str, subject: str, body: str) -> bool:
        """Send alert to all configured channels. Returns True if at least one channel succeeded."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] [{level}] {subject}\n{body}"
        any_sent = False

        # ── Discord ──────────────────────────────────────────────────
        if self.discord_webhook and self.discord_webhook.startswith("http"):
            import requests
            color_map = {"INFO": 3447003, "WARNING": 16776960, "CRITICAL": 16711680}
            try:
                r = requests.post(
                    self.discord_webhook,
                    json={
                        "embeds": [{
                            "title": f"[{level}] {subject}",
                            "description": body[:4096],
                            "color": color_map.get(level, 0),
                        }]
                    },
                    timeout=10,
                )
                if r.status_code in (200, 204):
                    any_sent = True
                    logger.info("Discord sent: %s", subject)
                else:
                    logger.warning("Discord HTTP %d: %s", r.status_code, r.text[:200])
            except Exception as e:
                logger.warning("Discord send failed: %s", e)

        # ── Telegram (safe async: works inside or outside event loop) ──
        if self.telegram_bot and self.telegram_chat_id:
            try:
                _run_async_safely(self._telegram_send(full_msg[:4096]))
                any_sent = True
                logger.info("Telegram sent: %s", subject)
            except Exception as e:
                logger.warning(
                    "Telegram send failed: %s — maybe bot not in chat? "
                    "Send /start to your bot first, or add bot to group.",
                    e
                )

        # ── Persistent log ───────────────────────────────────────────
        if level == "CRITICAL":
            logger.critical(full_msg)
        elif level == "WARNING":
            logger.warning(full_msg)
        else:
            logger.info(full_msg)

        return any_sent

    # ── Convenience methods ──────────────────────────────────────────
    def daily_drawdown_warning(self, pnl_pct: float) -> None:
        if pnl_pct <= -0.05:
            self.send("CRITICAL", "DAILY DRAWDOWN > 5% — HALT", f"PnL: {pnl_pct*100:.1f}%")
        elif pnl_pct <= -0.03:
            self.send("WARNING", "Daily drawdown > 3%", f"PnL: {pnl_pct*100:.1f}%")

    def unrealized_loss_alert(self, ticker: str, loss_pct: float) -> None:
        if loss_pct >= 0.02:
            self.send("WARNING", f"Unrealized loss > 2%: {ticker}", f"Loss: {loss_pct*100:.1f}%")

    def api_error(self, component: str, error_msg: str) -> None:
        self.send("WARNING", f"API Error: {component}", error_msg)

    def kill_switch_triggered(self, reason: str) -> None:
        self.send("CRITICAL", "KILL SWITCH TRIGGERED", f"Reason: {reason}\nAll trading halted.")

    # ── Screener Report ─────────────────────────────────────────────
    def send_screener_report(
        self,
        ultra_buy: list[dict],
        strong_buy: list[dict],
        buy: list[dict],
        screener_date: str = "",
    ) -> str:
        """Send screener signal report to Discord + Telegram.

        Each dict: {"Ticker": str, "Harga": int, "Skor": float, "Confidence%": int, "RRR": float, "AI_Verdict": str}
        Returns status string.
        """
        date_str = screener_date or datetime.now().strftime("%Y-%m-%d")
        total = len(ultra_buy) + len(strong_buy) + len(buy)

        if total == 0:
            body = f"📊 Screener {date_str}\nTidak ada sinyal BUY hari ini."
            self.send("INFO", "Screener Report", body)
            return "No signals — report sent."

        lines = [f"📊 *IHSG Screener — {date_str}*", f"Total sinyal: {total}\n"]

        if ultra_buy:
            lines.append("🟢 *ULTRA BUY*")
            for s in ultra_buy:
                lines.append(
                    f"  • {s['Ticker']} — Rp {s['Harga']:,} | Skor {s.get('Skor','?')} "
                    f"| RRR {s.get('RRR','?')} | AI: {s.get('AI_Verdict','?')}"
                )

        if strong_buy:
            lines.append("\n🔵 *STRONG BUY*")
            for s in strong_buy:
                lines.append(
                    f"  • {s['Ticker']} — Rp {s['Harga']:,} | Skor {s.get('Skor','?')} "
                    f"| Conf {s.get('Confidence%','?')}% | {s.get('AI_Verdict','?')}"
                )

        if buy:
            lines.append("\n⚪ *BUY*")
            for s in buy:
                lines.append(
                    f"  • {s['Ticker']} — Rp {s['Harga']:,} | Skor {s.get('Skor','?')} "
                    f"| RRR {s.get('RRR','?')}"
                )

        body = "\n".join(lines)

        # Discord: rich embed
        if self.discord_webhook and self.discord_webhook.startswith("http"):
            import requests
            try:
                # Build fields for embed
                fields = []
                if ultra_buy:
                    fields.append({
                        "name": f"🟢 ULTRA BUY ({len(ultra_buy)})",
                        "value": "\n".join(
                            f"**{s['Ticker']}** — Rp {s['Harga']:,} | Skor {s.get('Skor','?')} | RRR {s.get('RRR','?')} | AI {s.get('AI_Verdict','?')}"
                            for s in ultra_buy[:10]
                        )[:1024],
                    })
                if strong_buy:
                    fields.append({
                        "name": f"🔵 STRONG BUY ({len(strong_buy)})",
                        "value": "\n".join(
                            f"{s['Ticker']} — Rp {s['Harga']:,} | Conf {s.get('Confidence%','?')}%"
                            for s in strong_buy[:10]
                        )[:1024],
                    })
                if buy:
                    fields.append({
                        "name": f"⚪ BUY ({len(buy)})",
                        "value": "\n".join(
                            f"{s['Ticker']} — Rp {s['Harga']:,} | RRR {s.get('RRR','?')}"
                            for s in buy[:15]
                        )[:1024],
                    })

                requests.post(
                    self.discord_webhook,
                    json={
                        "embeds": [{
                            "title": f"📊 IHSG Screener — {date_str}",
                            "description": f"Total: **{total}** sinyal (ULTRA: {len(ultra_buy)}, STRONG: {len(strong_buy)}, BUY: {len(buy)})",
                            "color": 0x00ff88 if ultra_buy else 0x3498db,
                            "fields": fields,
                            "footer": {"text": "Quant Trader Dashboard · Auto-generated"},
                        }]
                    },
                    timeout=10,
                )
                logger.info("Discord screener report sent (%d signals)", total)
            except Exception as e:
                logger.warning("Discord report failed: %s", e)

        # Telegram: plain text (safe async)
        if self.telegram_bot and self.telegram_chat_id:
            try:
                _run_async_safely(self._telegram_send(body[:4096]))
                logger.info("Telegram screener report sent (%d signals)", total)
            except Exception as e:
                logger.warning("Telegram report failed: %s", e)

        logger.info("Screener report: %d signals sent", total)
        return f"OK — {total} signals sent to Discord + Telegram"
