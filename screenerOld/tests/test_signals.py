# FIX: Unit test skeleton — SKILL.md recommends pytest + hypothesis
# tests/test_signals.py — signal generation tests
import pytest
import pandas as pd
import numpy as np
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSignalValidation:
    """Minimal signal validation tests — extend with real data."""

    def test_rsi_bounds(self):
        """RSI should always be between 0 and 100."""
        # Placeholder — replace with actual indicator call
        pass

    def test_no_negative_prices(self):
        """Prices should never be negative."""
        pass

    def test_no_lookahead_leakage(self):
        """Signal computation must not reference future data."""
        pass
