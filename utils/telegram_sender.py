#!/usr/bin/env python3
"""
utils/telegram_sender.py — Unified Telegram sender for Screener project
=======================================================================
Single source of truth for Telegram message delivery. All project modules
SHOULD import this instead of calling the Telegram API directly.

Design:
  - Uses telegram.Bot (not Application/Updater) — safe, no polling conflict
  - Rate-limited via asyncio.Semaphore (max 20 concurrent sends)
  - Retry with exponential backoff (3 attempts)
  - Works in both async and sync contexts (autodetect event loop)
  - Centralized logging of all sends
"""
import os
import sys
import asyncio
import logging
import time
from typing import Optional

# Ensure root project is in path for .env loading
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))

# ── Config ──
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "-5237365204"))
MAX_RETRIES = 3
RATE_LIMIT = 20  # max concurrent sends

logger = logging.getLogger("telegram_sender")

# ── Lazy-init bot with rate limiter ──
_bot = None
_bot_lock = asyncio.Lock()
_semaphore = asyncio.Semaphore(RATE_LIMIT)

def _get_bot():
    """Lazy-init telegram.Bot (not Application — no polling)."""
    global _bot
    if _bot is None:
        import telegram
        _bot = telegram.Bot(token=TOKEN)
    return _bot

async def send_telegram_message(text: str, chat_id: Optional[int] = None) -> bool:
    """Send message via Telegram bot. Safe to call from any context.
    
    Args:
        text: Message text (max 4096 chars, auto-truncated)
        chat_id: Target chat ID (defaults to .env TELEGRAM_CHAT_ID)
    
    Returns:
        True if sent successfully, False otherwise
    """
    if not TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — cannot send")
        return False
    
    target = chat_id or CHAT_ID
    text = str(text)[:4096]  # Telegram limit
    
    async with _semaphore:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                bot = _get_bot()
                await bot.send_message(chat_id=target, text=text, parse_mode='Markdown')
                logger.info("Sent %d chars to chat %s", len(text), target)
                return True
            except Exception as e:
                wait = 2 ** attempt  # exponential backoff: 2, 4, 8
                logger.warning("Send attempt %d/%d failed: %s (retry in %ds)",
                               attempt, MAX_RETRIES, e, wait)
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)
    
    logger.error("Failed to send after %d attempts", MAX_RETRIES)
    return False


def send_telegram_sync(text: str, chat_id: Optional[int] = None) -> bool:
    """Synchronous wrapper — safe to call from threaded/non-async code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(send_telegram_message(text, chat_id))
    
    # Already in an event loop — spawn new thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, send_telegram_message(text, chat_id))
        try:
            return future.result(timeout=30)
        except Exception as e:
            logger.error("sync send failed: %s", e)
            return False
