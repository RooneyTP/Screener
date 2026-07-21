# dashboard/alerts.py — Centralized alert dispatcher (SKILL.md §⑥)
# Channels: Discord embed + Telegram (via utils/telegram_sender) + persistent log
# Alerts: daily drawdown >3% warning, >5% halt, unrealized loss >2%, API error, kill switch
#
# ── STRATEGY ASSUMPTIONS ─────────────────────────────────────────
# Discord: embed berwarna (blue=info, yellow=warning, red=critical)
# Telegram: pesan teks (max 4096 karakter, via telegram_sender.py)
# All alerts also logged to logs/screener_YYYYMMDD.log
# ─────────────────────────────────────────────────────────────────

import os
import sys
import logging
from datetime import datetime

# Ensure project root in path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

from utils.telegram_sender import send_telegram_sync

logger = logging.getLogger("alerts")


class AlertManager:
    """Centralized alert dispatcher — Discord + Telegram (via telegram_sender) + persistent log."""

    def __init__(
        self,
        discord_webhook: str = "",
    ):
        self.discord_webhook = discord_webhook or os.getenv("DISCORD_WEBHOOK", "")

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

        # ── Telegram (via shared telegram_sender) ──
        try:
            ok = send_telegram_sync(full_msg[:4096])
            if ok:
                any_sent = True
                logger.info("Telegram sent: %s", subject)
            else:
                logger.warning("Telegram send failed (check token in .env)")
        except Exception as e:
            logger.warning("Telegram send error: %s", e)

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

        # Telegram: via shared sender (no async issues)
        try:
            ok = send_telegram_sync(body[:4096])
            if ok:
                logger.info("Telegram screener report sent (%d signals)", total)
            else:
                logger.warning("Telegram screener report failed to send")
        except Exception as e:
            logger.warning("Telegram report failed: %s", e)

        logger.info("Screener report: %d signals sent", total)
        return f"OK — {total} signals sent to Discord + Telegram"
