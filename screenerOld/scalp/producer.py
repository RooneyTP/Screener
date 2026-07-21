# scalp/producer.py — 1m OHLCV Data Producer (SKILL.md §① scalping data layer)
# =========================================================================
# Migrated from 1_producer_data.py with:
#   - Unified DB schema via src/data/schema.py (no duplicate CREATE TABLE)
#   - Config-driven via scalp/config.py (no hardcoded params)
#   - Pluggable DataSource via src/data/fetcher.py (no hardcoded URLs)
#   - Same asyncio + aiohttp pattern with 170 IHSG tickers, 30s cycle
#
# Run: python -m scalp.run producer
#    or: python scalp/producer.py

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import time
from datetime import datetime

# ── Path setup ───────────────────────────────────────────────────────
import os as _os
_sys_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _sys_root not in sys.path:
    sys.path.insert(0, _sys_root)

from scalp.config import ScalpConfig
from src.data.fetcher import YahooFinanceSource, FetchResult
from src.data.schema import init_histori_db

# ── Logging ──────────────────────────────────────────────────────────
_os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] producer: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("logs/scalp_producer.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("scalp.producer")

# ── Config ───────────────────────────────────────────────────────────
config = ScalpConfig.from_yaml()
TICKERS = config.tickers if config.tickers else []

# Track consecutive failures per ticker
_failures: dict[str, int] = {}


def should_skip(ticker: str) -> bool:
    """Check if ticker should be skipped due to repeated failures."""
    return _failures.get(ticker, 0) >= config.skip_after_failures


async def producer_loop(source: YahooFinanceSource, db_path: str) -> None:
    """Main producer loop: fetch → store → repeat."""
    logger.info("SCALP PRODUCER v3.0 STARTING")
    logger.info("  Tickers: %d | Concurrent: %d | Cycle: %ds | Source: %s",
                len(TICKERS), config.max_concurrent,
                config.cycle_interval_secs, config.data_source)

    with sqlite3.connect(db_path) as conn:
        init_histori_db(conn)
        cur = conn.cursor()
        cycle = 0

        while True:
            cycle += 1
            t_start = time.time()
            logger.info("Cycle #%d — %s", cycle, datetime.now().strftime("%H:%M:%S"))

            # ── Filter active tickers ────────────────────────────────
            active = [t for t in TICKERS if not should_skip(t)]
            skipped = len(TICKERS) - len(active)
            if skipped > 0:
                logger.debug("Skipping %d tickers (consecutive failures)", skipped)

            # ── Fetch batch ──────────────────────────────────────────
            results: dict[str, FetchResult] = await source.fetch_batch(
                active, max_concurrent=config.max_concurrent
            )

            # ── Store to DB ──────────────────────────────────────────
            ok_count = 0
            fail_count = 0
            for ticker, result in results.items():
                if result.success and result.ohlcv:
                    try:
                        cur.execute(
                            "INSERT INTO histori_ihsg (ticker, open, high, low, harga, volume) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                ticker,
                                result.ohlcv.open,
                                result.ohlcv.high,
                                result.ohlcv.low,
                                result.ohlcv.close,
                                result.ohlcv.volume,
                            ),
                        )
                        ok_count += 1
                        _failures[ticker] = 0  # reset on success
                    except sqlite3.Error as e:
                        logger.error("DB insert failed for %s: %s", ticker, e)
                        fail_count += 1
                else:
                    _failures[ticker] = _failures.get(ticker, 0) + 1
                    fail_count += 1

            conn.commit()

            # ── Stats ────────────────────────────────────────────────
            elapsed = time.time() - t_start
            pct = ok_count / max(1, len(active)) * 100
            status = "OK" if pct >= 80 else "WARN" if pct >= 50 else "LOW"
            logger.info(
                "[%s] OK=%d/%d (%.0f%%) | Fail=%d | Skip=%d | %.2fs",
                status, ok_count, len(active), pct, fail_count, skipped, elapsed,
            )

            if pct < 50:
                logger.warning("Success rate < 50%% — IP mungkin dibatasi atau bursa tutup.")

            # ── Sleep ────────────────────────────────────────────────
            wait = max(0, config.cycle_interval_secs - elapsed)
            await asyncio.sleep(wait)


def run_producer() -> None:
    """Synchronous entry point for the producer."""
    source = YahooFinanceSource(
        max_concurrent=config.max_concurrent,
        timeout_secs=config.timeout_secs,
        max_retries=config.max_retries,
        retry_delay=config.retry_delay_secs,
    )
    db_path = _os.path.join(_sys_root, config.histori_db_name)
    asyncio.run(producer_loop(source, db_path))


if __name__ == "__main__":
    run_producer()
