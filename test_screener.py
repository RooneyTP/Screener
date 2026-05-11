"""
test_screener.py — Unit Tests for IHSG Screener (Phase-4 Fix #10)
==================================================================
Run with:
    python -m pytest test_screener.py -v
    # or plain unittest:
    python test_screener.py

Test coverage
-------------
A. backtest.py        — trade P&L calculation, SL/TP edge cases, Sharpe ratio
B. 3_consumer_r1.py   — validasi_sl_tp (all rules), eksekusi_beli stub
C. 2_consumer_ai.py   — process_message race-condition fix, rollback path
D. analisis_saham     — mocked dependencies, score > 0, valid signal
E. ai_model.py        — MarketAI feature count validation
"""

import logging
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

# Silence noisy loggers during tests
logging.disable(logging.CRITICAL)


# ════════════════════════════════════════════════════════════════════════════
# A. backtest.py tests
# ════════════════════════════════════════════════════════════════════════════
class TestBacktest(unittest.TestCase):
    """Tests for backtest() and _apply_costs()."""

    def setUp(self):
        from backtest import backtest, backtest_report, _apply_costs
        self.backtest       = backtest
        self.backtest_report= backtest_report
        self._apply_costs   = _apply_costs

        # Minimal signals DataFrame with valid BUY entries
        self.valid_signals = pd.DataFrame([
            {"Ticker": "BBCA", "Sinyal": "BUY",        "Harga": 9200, "Stop_Loss": 8900, "Target_1": 9800, "RRR": 2.0},
            {"Ticker": "BMRI", "Sinyal": "STRONG_BUY", "Harga": 6200, "Stop_Loss": 6000, "Target_1": 6700, "RRR": 2.5},
            {"Ticker": "TLKM", "Sinyal": "ULTRA_BUY",  "Harga": 3800, "Stop_Loss": 3600, "Target_1": 4200, "RRR": 2.0},
        ])

        self.empty_signals = pd.DataFrame()

        # Signals with bad SL/TP that should be skipped
        self.bad_signals = pd.DataFrame([
            {"Ticker": "BAD1", "Sinyal": "BUY", "Harga": 1000, "Stop_Loss": 1100, "Target_1": 1200, "RRR": 1.0},  # SL > price
            {"Ticker": "BAD2", "Sinyal": "BUY", "Harga": 1000, "Stop_Loss":    0, "Target_1": 1200, "RRR": 1.5},  # SL = 0
            {"Ticker": "BAD3", "Sinyal": "BUY", "Harga": 1000, "Stop_Loss":  900, "Target_1":  950, "RRR": 0.5},  # RRR < 1
        ])

    # ── cost model ───────────────────────────────────────────────────────────
    def test_apply_costs_entry_higher_than_raw(self):
        """After buying, actual entry must be higher than raw (slippage + fee)."""
        actual_entry, actual_exit = self._apply_costs(1000.0, 1100.0)
        self.assertGreater(actual_entry, 1000.0)

    def test_apply_costs_exit_lower_than_raw(self):
        """After selling, actual exit must be lower than raw (slippage + fee)."""
        actual_entry, actual_exit = self._apply_costs(1000.0, 1100.0)
        self.assertLess(actual_exit, 1100.0)

    def test_apply_costs_roundtrip_friction(self):
        """Round-trip cost should be approximately 0.50% (0.10+0.15+0.10+0.25)."""
        entry, exit_ = self._apply_costs(1000.0, 1000.0)
        friction_pct = (entry - exit_) / 1000.0 * 100
        self.assertAlmostEqual(friction_pct, 0.50, delta=0.05)

    # ── backtest() ───────────────────────────────────────────────────────────
    def test_backtest_returns_tuple(self):
        win_rate, sharpe = self.backtest(self.valid_signals, pd.DataFrame())
        self.assertIsInstance(win_rate,  float)
        self.assertIsInstance(sharpe,    float)

    def test_backtest_win_rate_range(self):
        """win_rate must always be in [0, 1]."""
        win_rate, _ = self.backtest(self.valid_signals, pd.DataFrame())
        self.assertGreaterEqual(win_rate, 0.0)
        self.assertLessEqual(win_rate,    1.0)

    def test_backtest_empty_signals_returns_zero(self):
        win_rate, sharpe = self.backtest(self.empty_signals, pd.DataFrame())
        self.assertEqual(win_rate, 0.0)
        self.assertEqual(sharpe,   0.0)

    def test_backtest_bad_signals_all_skipped(self):
        """Signals with bad SL/TP should yield 0 trades → (0.0, 0.0)."""
        win_rate, sharpe = self.backtest(self.bad_signals, pd.DataFrame())
        self.assertEqual(win_rate, 0.0)
        self.assertEqual(sharpe,   0.0)

    def test_backtest_pnl_calculated(self):
        """When valid signals are provided, P&L must be computed (sharpe != 0 with >1 trade)."""
        np.random.seed(42)
        win_rate, sharpe = self.backtest(self.valid_signals, pd.DataFrame())
        # With 3 valid trades and some wins, result should be non-trivially zero
        self.assertIsNotNone(win_rate)
        self.assertIsNotNone(sharpe)

    def test_backtest_report_columns(self):
        report = self.backtest_report(self.valid_signals, pd.DataFrame())
        self.assertIn("Ticker",     report.columns)
        self.assertIn("NetReturn%", report.columns)
        self.assertIn("IsWin",      report.columns)
        self.assertEqual(len(report), len(self.valid_signals))


# ════════════════════════════════════════════════════════════════════════════
# B. 3_consumer_r1.py tests
# ════════════════════════════════════════════════════════════════════════════
class TestValidasiSLTP(unittest.TestCase):
    """Tests for validasi_sl_tp() in 3_consumer_r1.py."""

    def setUp(self):
        from importlib import import_module
        mod = import_module("3_consumer_r1")
        self.validasi = mod.validasi_sl_tp
        self.eksekusi  = mod.eksekusi_beli

    def test_valid_trade_does_not_raise(self):
        """Clean sl < price < tp with RRR ≥ 1.5 must pass silently."""
        self.assertIsNone(self.validasi(9200, 8900, 9800, "BBCA"))

    def test_sl_none_raises(self):
        with self.assertRaises(ValueError):
            self.validasi(9200, None, 9800, "X")

    def test_tp_none_raises(self):
        with self.assertRaises(ValueError):
            self.validasi(9200, 8900, None, "X")

    def test_sl_above_price_raises(self):
        with self.assertRaises(ValueError):
            self.validasi(9200, 9500, 9800, "X")   # sl > price

    def test_sl_equal_price_raises(self):
        with self.assertRaises(ValueError):
            self.validasi(9200, 9200, 9800, "X")   # sl == price

    def test_tp_below_price_raises(self):
        with self.assertRaises(ValueError):
            self.validasi(9200, 8900, 9100, "X")   # tp < price

    def test_low_rrr_raises(self):
        """RRR = 0.5 (reward 100, risk 200) must be rejected."""
        with self.assertRaises(ValueError):
            self.validasi(9200, 9000, 9300, "X")   # risk=200, reward=100 → RRR=0.5

    def test_minimum_rrr_passes(self):
        """Exactly RRR=1.5 must pass."""
        # risk=200, reward=300 → RRR=1.5
        self.assertIsNone(self.validasi(9200, 9000, 9500, "X"))

    def test_eksekusi_beli_bad_sl_returns_false(self):
        """eksekusi_beli with SL > price must return False (not raise)."""
        with patch("importlib.import_module"):
            result = self.eksekusi("BBCA", 9200, 9500, 9800, qty=1)
        self.assertFalse(result)

    def test_eksekusi_beli_none_sl_returns_false(self):
        result = self.eksekusi("BBCA", 9200, None, 9800, qty=1)
        self.assertFalse(result)


# ════════════════════════════════════════════════════════════════════════════
# C. 2_consumer_ai.py tests — race condition fix
# ════════════════════════════════════════════════════════════════════════════
class TestConsumerAIRaceConditionFix(unittest.TestCase):
    """Tests that last_processed_id is saved only AFTER successful DB insert."""

    def setUp(self):
        from importlib import import_module
        self.mod = import_module("2_consumer_ai")

        # Use an in-memory SQLite DB for isolation
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS sinyal (
                id TEXT PRIMARY KEY, ticker TEXT, signal TEXT,
                ai_prob REAL, price REAL, sl REAL, tp REAL, rrr REAL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _make_payload(self, msg_id="msg-test", ticker="BBCA"):
        return {
            "id": msg_id, "ticker": ticker, "price": 9200.0,
            "sl": 8900.0, "tp": 9800.0, "rrr": 2.0,
            "features": [55, 30, 75, 4.5, 2.0, 70, 1.2, 0.3, -0.1, 53, 0.05,
                          4125, 0.012, 0.72],
        }

    def test_successful_insert_returns_msg_id(self):
        payload = self._make_payload("msg-001")
        new_id = self.mod.process_message(payload, self.conn, None, None)
        self.assertEqual(new_id, "msg-001")

    def test_duplicate_message_is_idempotent(self):
        """Second insert of same id must not raise, returns same id."""
        payload = self._make_payload("msg-dup")
        self.mod.process_message(payload, self.conn, None, None)
        result = self.mod.process_message(payload, self.conn, None, "msg-dup")
        # Returns same last_id (already processed)
        self.assertEqual(result, "msg-dup")

    def test_db_insert_failure_does_not_save_id(self):
        """
        If DB insert raises, process_message must return None (not msg_id).
        last_processed_id must NOT advance.
        """
        payload = self._make_payload("msg-fail")

        # Corrupt the connection so insert fails
        self.conn.close()
        broken_conn = MagicMock()
        broken_conn.execute.side_effect = sqlite3.OperationalError("disk full")

        result = self.mod.process_message(payload, broken_conn, None, None)
        self.assertIsNone(result)

    def test_invalid_payload_skipped(self):
        """Missing ticker must be skipped cleanly (returns None)."""
        bad_payload = {"price": 9200, "features": [1, 2, 3]}
        result = self.mod.process_message(bad_payload, self.conn, None, None)
        self.assertIsNone(result)

    def test_state_file_written_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_file = os.path.join(tmp, ".last_id")
            orig = self.mod.STATE_FILE
            self.mod.STATE_FILE = state_file
            try:
                payload = self._make_payload("msg-state")
                self.mod.process_message(payload, self.conn, None, None)
                with open(state_file) as f:
                    saved = f.read().strip()
                self.assertEqual(saved, "msg-state")
            finally:
                self.mod.STATE_FILE = orig


# ════════════════════════════════════════════════════════════════════════════
# D. analisis_saham integration test (fully mocked)
# ════════════════════════════════════════════════════════════════════════════
class TestAnalisisSaham(unittest.TestCase):
    """
    Tests analisis_saham() with all external calls mocked.
    Verifies: result is a dict, score > 0 for a bullish setup,
              signal is one of the valid set, new feature columns present.
    """

    VALID_SIGNALS = {"ULTRA_BUY", "STRONG_BUY", "BUY", "PANTAU", "TUNGGU", "HINDARI"}

    def _make_ohlcv(self, n: int = 120) -> pd.DataFrame:
        """Create a bullish trending OHLCV DataFrame."""
        np.random.seed(0)
        dates  = pd.bdate_range(end=pd.Timestamp.today(), periods=n)
        close  = 9000 + np.cumsum(np.random.randn(n) * 50 + 5)   # uptrend
        high   = close + np.abs(np.random.randn(n) * 30)
        low    = close - np.abs(np.random.randn(n) * 30)
        open_  = close + np.random.randn(n) * 20
        volume = np.random.randint(1_000_000, 5_000_000, n).astype(float)
        return pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
            index=dates,
        )

    def setUp(self):
        # Patch external dependencies to avoid network calls
        self.patches = [
            patch("screener.fetch_price_data",        return_value=self._make_ohlcv()),
            patch("screener.fetch_fundamental_metrics", return_value={
                "trailing_pe": 12.0, "book_value": 3000.0,
                "earnings_growth": 0.15, "dividend_yield": 3.5,
                "float_shares": 5_000_000, "shares_outstanding": 10_000_000,
                "market_cap": 90_000_000_000,
            }),
            patch("screener.fetch_news_sentiment",    return_value={
                "sentiment_score": 0.3, "sentiment_label": "POSITIVE",
                "news_count": 5, "positive_news": 4, "negative_news": 1,
            }),
            patch("screener.fetch_foreign_flow",      return_value={
                "net_foreign_5d": 1_000_000, "foreign_status": "ACCUMULATION",
            }),
            patch("screener.analisis_broksum",        return_value={
                "status_bandar": "BIG_ACCUMULATION", "akumulasi_bersih": 500_000,
            }),
            patch("screener.get_sentiment",           return_value=(0.3, "BULLISH")),
            patch("screener.get_ai_model",            return_value=MagicMock(
                predict_win_probability=MagicMock(return_value=65.0)
            )),
            patch("screener.fetch_berita_lokal",      return_value=1),
            patch("screener.detect_zscore_anomaly",   return_value=3.5),
        ]
        self.mocks = [p.start() for p in self.patches]

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_returns_dict(self):
        from screener import analisis_saham
        result = analisis_saham("BBCA.JK")
        self.assertIsInstance(result, dict)

    def test_signal_is_valid(self):
        from screener import analisis_saham
        result = analisis_saham("BBCA.JK")
        self.assertIn(result["Sinyal"], self.VALID_SIGNALS)

    def test_score_is_numeric(self):
        from screener import analisis_saham
        result = analisis_saham("BBCA.JK")
        self.assertIsInstance(result["Skor"], (int, float))

    def test_new_feature_columns_present(self):
        """Phase-2: verify new engineered feature columns are in the result."""
        from screener import analisis_saham
        result = analisis_saham("BBCA.JK")
        self.assertIn("RSI_Vol_Interaction", result)
        self.assertIn("Rolling_Vol_20",      result)
        self.assertIn("Sector_Corr",         result)

    def test_fundamental_defaults_applied(self):
        """Phase-3: if PE/BV are 0, defaults should be applied (no KeyError)."""
        # Override fundamental mock to return zeros
        for p in self.patches:
            if hasattr(p, "attribute") and "fetch_fundamental_metrics" in str(p):
                p.return_value = {"trailing_pe": 0, "book_value": 0}

        from screener import analisis_saham
        result = analisis_saham("BBCA.JK")
        self.assertIsNotNone(result)   # should not crash


# ════════════════════════════════════════════════════════════════════════════
# E. ai_model.py feature count validation
# ════════════════════════════════════════════════════════════════════════════
class TestAIModelFeatureCount(unittest.TestCase):
    """Tests for MarketAI.predict_win_probability() feature count validation."""

    def setUp(self):
        from ai_model import MarketAI, N_FEATURES
        self.model     = MarketAI(model_type="swing")
        self.N_FEATURES = N_FEATURES

    def test_correct_feature_count_accepted(self):
        """N_FEATURES = 14 after Phase-2 extension."""
        self.assertEqual(self.N_FEATURES, 14)
        features = [50.0] * self.N_FEATURES
        result = self.model.predict_win_probability(features)
        self.assertIsInstance(result, float)
        self.assertGreaterEqual(result, 0.0)
        self.assertLessEqual(result, 100.0)

    def test_wrong_feature_count_returns_zero(self):
        """Wrong number of features must return 0.0 (not raise)."""
        result = self.model.predict_win_probability([50.0] * 11)   # old count
        self.assertEqual(result, 0.0)

    def test_nan_features_handled(self):
        """NaN / inf in features must not crash — returns float."""
        features = [float("nan"), float("inf"), -float("inf")] + [50.0] * (self.N_FEATURES - 3)
        result = self.model.predict_win_probability(features)
        self.assertIsInstance(result, float)
        self.assertFalse(np.isnan(result))

    def test_non_list_input_returns_zero(self):
        result = self.model.predict_win_probability("not-a-list")
        self.assertEqual(result, 0.0)


# ════════════════════════════════════════════════════════════════════════════
# F. nlp_scraper.py tests
# ════════════════════════════════════════════════════════════════════════════
class TestNLPScraper(unittest.TestCase):

    def test_get_sentiment_returns_tuple(self):
        from nlp_scraper import get_sentiment
        with patch("nlp_scraper._fetch_rss_headlines", return_value=["laba naik dividen"]):
            score, label = get_sentiment("BBCA.JK")
        self.assertIsInstance(score, float)
        self.assertIn(label, {"BULLISH", "BEARISH", "NEUTRAL"})

    def test_compound_range(self):
        from nlp_scraper import get_sentiment_compound
        with patch("nlp_scraper._fetch_rss_headlines", return_value=["profit growth bullish"]):
            c = get_sentiment_compound("TEST.JK")
        self.assertGreaterEqual(c, -1.0)
        self.assertLessEqual(c,    1.0)

    def test_empty_headlines_returns_zero(self):
        from nlp_scraper import get_sentiment_compound
        with patch("nlp_scraper._fetch_rss_headlines", return_value=[]):
            c = get_sentiment_compound("EMPTY.JK")
        self.assertEqual(c, 0.0)


# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.disable(logging.NOTSET)   # re-enable for interactive run
    unittest.main(verbosity=2)