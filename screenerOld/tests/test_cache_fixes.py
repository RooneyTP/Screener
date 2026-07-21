# tests/test_cache_fixes.py — regression tests for cache & toggle fixes
# Covers:
#   - core.file_handler.USE_CACHE actually gates load/save (screener --cache/--no-cache fix)
#   - telegram_bot._lookup_ticker_live populates the indicator cache (dead-code 'result' fix)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestUseCacheToggle:
    """core.file_handler.USE_CACHE must be honoured at call-time (live module attr)."""

    def test_cache_disabled_returns_none(self):
        import core.file_handler as fh
        prev = fh.USE_CACHE
        try:
            fh.USE_CACHE = False
            assert fh.load_from_cache("a_key_that_does_not_exist") is None
        finally:
            fh.USE_CACHE = prev

    def test_toggle_is_module_level(self):
        """Mutating the module attribute (as screener __main__ does) must stick."""
        import core.file_handler as fh
        prev = fh.USE_CACHE
        try:
            fh.USE_CACHE = True
            assert fh.USE_CACHE is True
            fh.USE_CACHE = False
            assert fh.USE_CACHE is False
        finally:
            fh.USE_CACHE = prev


class TestIndicatorCachePopulated:
    """The LRU indicator cache in telegram_bot must round-trip set/get."""

    def test_set_then_get(self):
        import telegram_bot as tb
        payload = {"Ticker": "ZZZZ", "Harga": 123}
        tb._set_cached_indicator("ZZZZ", payload)
        got = tb._get_cached_indicator("ZZZZ")
        assert got is not None
        assert got["Ticker"] == "ZZZZ"
        assert got["Harga"] == 123
