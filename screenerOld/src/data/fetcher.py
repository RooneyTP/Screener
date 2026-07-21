# src/data/fetcher.py — Abstract Data Source for 1m OHLCV (SKILL.md §②)
# ======================================================================
# Pluggable data source architecture.
# Primary: Stockbit API (when available)
# Fallback: Yahoo Finance HTTP polling
#
# All implementations inherit from DataSource ABC.
# Producer code never hardcodes URLs — it calls source.fetch_ohlcv().

from __future__ import annotations

import asyncio
import aiohttp
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ── Data Types ──────────────────────────────────────────────────

@dataclass
class OHLCV:
    """Single 1-minute OHLCV candle."""
    ticker: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def is_valid(self) -> bool:
        return self.close > 0


@dataclass
class FetchResult:
    """Result of a single fetch attempt."""
    ticker: str
    ohlcv: Optional[OHLCV]
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.ohlcv is not None and self.ohlcv.is_valid


# ── Abstract Data Source ────────────────────────────────────────

class DataSource(ABC):
    """Abstract base class for 1m OHLCV data sources.

    Subclasses implement fetch_ohlcv() for a single ticker.
    The base class provides batch fetch with concurrency control.
    """

    def __init__(self, max_concurrent: int = 5, timeout_secs: int = 5):
        self.max_concurrent = max_concurrent
        self.timeout_secs = timeout_secs
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @abstractmethod
    async def fetch_ohlcv(self, ticker: str) -> Optional[OHLCV]:
        """Fetch latest 1m OHLCV for a single ticker.

        Returns None if data is unavailable or ticker is invalid.
        """
        ...

    async def fetch_batch(
        self,
        tickers: list[str],
        max_concurrent: int | None = None,
        jitter: bool = True,
    ) -> dict[str, FetchResult]:
        """Fetch OHLCV for multiple tickers concurrently.

        Args:
            tickers: List of ticker symbols
            max_concurrent: Override max concurrent requests
            jitter: Add random delay to avoid thundering herd

        Returns:
            Dict mapping ticker → FetchResult
        """
        sem = (
            asyncio.Semaphore(max_concurrent)
            if max_concurrent
            else self._semaphore
        )

        async def _fetch_one(ticker: str) -> FetchResult:
            async with sem:
                if jitter:
                    await asyncio.sleep(random.uniform(0.05, 0.3))
                try:
                    ohlcv = await self.fetch_ohlcv(ticker)
                    return FetchResult(ticker=ticker, ohlcv=ohlcv)
                except Exception as e:
                    logger.debug("Fetch failed for %s: %s", ticker, e)
                    return FetchResult(ticker=ticker, ohlcv=None, error=str(e))

        tasks = [_fetch_one(t) for t in tickers]
        results = await asyncio.gather(*tasks)
        return {r.ticker: r for r in results}

    async def close(self) -> None:
        """Clean up resources (connections, sessions). Override if needed."""
        pass


# ── Yahoo Finance Source ────────────────────────────────────────

class YahooFinanceSource(DataSource):
    """Fetch 1m OHLCV from Yahoo Finance chart API.

    This is the current production source. Known limitations:
    - Rate-limited (HTTP 429 after ~50 requests)
    - 1m data sparse for many IHSG tickers
    - No WebSocket — polling only

    Used as FALLBACK once StockbitSource is available.
    """

    BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        max_concurrent: int = 5,
        timeout_secs: int = 5,
        max_retries: int = 2,
        retry_delay: float = 1.0,
    ):
        super().__init__(max_concurrent, timeout_secs)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout_secs)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def fetch_ohlcv(self, ticker: str) -> Optional[OHLCV]:
        """Fetch from Yahoo Finance v8 chart API."""
        session = await self._get_session()

        # Normalize ticker: strip .JK → re-add
        ticker_clean = ticker.replace(".JK", "").upper()
        url = f"{self.BASE_URL}/{ticker_clean}.JK?interval=1m&range=1d"
        headers = {"User-Agent": self.USER_AGENT}

        for attempt in range(1, self.max_retries + 2):
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 429:
                        logger.debug("Rate limited for %s (attempt %d)", ticker, attempt)
                        if attempt <= self.max_retries:
                            await asyncio.sleep(self.retry_delay * attempt)
                        continue

                    if resp.status != 200:
                        if attempt <= self.max_retries:
                            await asyncio.sleep(self.retry_delay)
                        continue

                    data = await resp.json()
                    result = data.get("chart", {}).get("result")

                    if not result:
                        return None

                    quotes = result[0]["indicators"]["quote"][0]
                    close_list = quotes.get("close", [])
                    open_list = quotes.get("open", [])
                    high_list = quotes.get("high", [])
                    low_list = quotes.get("low", [])
                    volume_list = quotes.get("volume", [])

                    # Find last valid data point
                    valid_idx = -1
                    for idx in range(len(close_list) - 1, -1, -1):
                        if close_list[idx] is not None:
                            valid_idx = idx
                            break

                    if valid_idx < 0:
                        return None

                    harga = close_list[valid_idx]
                    if harga is None or harga <= 0:
                        return None

                    n = len(open_list)
                    open_p = (
                        open_list[valid_idx]
                        if valid_idx < n and open_list[valid_idx] is not None
                        else harga
                    )
                    high_p = (
                        high_list[valid_idx]
                        if valid_idx < n and high_list[valid_idx] is not None
                        else harga
                    )
                    low_p = (
                        low_list[valid_idx]
                        if valid_idx < n and low_list[valid_idx] is not None
                        else harga
                    )
                    vol = (
                        volume_list[valid_idx]
                        if valid_idx < n and volume_list[valid_idx] is not None
                        else 0.0
                    )

                    return OHLCV(
                        ticker=ticker,
                        open=float(open_p) if open_p else harga,
                        high=float(high_p) if high_p else harga,
                        low=float(low_p) if low_p else harga,
                        close=float(harga),
                        volume=float(vol) if vol else 0.0,
                    )

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug("Network error for %s: %s", ticker, e)
                if attempt <= self.max_retries:
                    await asyncio.sleep(self.retry_delay)
                continue
            except Exception as e:
                logger.debug("Unexpected error for %s: %s", ticker, e)
                return None

        return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


# ── Composite Source (Primary → Fallback) ───────────────────────

class CompositeSource(DataSource):
    """Try primary source first, fall back to secondary on failure.

    Example:
        primary = StockbitSource(...)
        fallback = YahooFinanceSource(...)
        source = CompositeSource(primary, fallback)
    """

    def __init__(self, primary: DataSource, fallback: DataSource):
        super().__init__()
        self.primary = primary
        self.fallback = fallback

    async def fetch_ohlcv(self, ticker: str) -> Optional[OHLCV]:
        result = await self.primary.fetch_ohlcv(ticker)
        if result is not None:
            return result
        logger.debug("Primary failed for %s — using fallback", ticker)
        return await self.fallback.fetch_ohlcv(ticker)

    async def close(self) -> None:
        await self.primary.close()
        await self.fallback.close()


# ── Stockbit Source (Placeholder) ───────────────────────────────

class StockbitSource(DataSource):
    """Stockbit real-time data source (NOT YET IMPLEMENTED).

    Stockbit provides WebSocket streaming for IHSG tickers.
    This is the TARGET primary source for Phase 6.

    Implementation requires:
    - Stockbit API token (from .env)
    - socket.io client for WebSocket
    - Authentication flow
    """

    def __init__(self, max_concurrent: int = 10, timeout_secs: int = 5):
        super().__init__(max_concurrent, timeout_secs)
        logger.warning(
            "StockbitSource is a PLACEHOLDER — not yet implemented. "
            "Use YahooFinanceSource or CompositeSource instead."
        )

    async def fetch_ohlcv(self, ticker: str) -> Optional[OHLCV]:
        """Not implemented — returns None."""
        return None
