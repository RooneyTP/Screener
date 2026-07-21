"""
test_screener.py — Unit test IDX Alpha Screener v2 (tanpa network)
==================================================================
Usage:
    python -m unittest test_screener.py -v
    python -m pytest test_screener.py -v
"""

import sys, os, unittest, tempfile, shutil, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
import data, scoring as sc, risk as rm, regime as rg


class TestFilterStocks(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        np.random.seed(42)
        self.df_good = pd.DataFrame({
            "close": 1000 + np.cumsum(np.random.randn(200) * 5),
            "high": 1010 + np.cumsum(np.random.randn(200) * 5),
            "low": 990 + np.cumsum(np.random.randn(200) * 5),
            "volume": np.random.randint(1_000_000, 10_000_000, 200),
        }, index=dates)

        self.df_penny = pd.DataFrame({
            "close": 50 + np.cumsum(np.random.randn(200) * 1),
            "high": 51 + np.cumsum(np.random.randn(200) * 1),
            "low": 49 + np.cumsum(np.random.randn(200) * 1),
            "volume": np.random.randint(100, 5000, 200),
        }, index=dates)

    def test_good_stock_passes(self):
        self.assertTrue(data.filter_stocks(self.df_good))

    def test_penny_stock_fails(self):
        self.assertFalse(data.filter_stocks(self.df_penny))

    def test_empty_df_fails(self):
        self.assertFalse(data.filter_stocks(pd.DataFrame()))

    def test_short_df_fails(self):
        self.assertFalse(data.filter_stocks(self.df_good.iloc[:30]))


class TestScoringComponents(unittest.TestCase):
    def setUp(self):
        self.row_bull = pd.Series({
            "rsi": 55.0, "macd": 10, "macd_signal": 5, "macd_hist": 5,
            "ema12": 1050, "ema50": 1000, "close": 1080,
            "adx": 35, "vol_ratio": 1.8, "ret_20d": 0.05,
            "atr": 20, "bb_width_pct": 12,
        })
        self.row_bear = pd.Series({
            "rsi": 25.0, "macd": -10, "macd_signal": -5, "macd_hist": -5,
            "ema12": 900, "ema50": 1000, "close": 850,
            "adx": 40, "vol_ratio": 0.5, "ret_20d": -0.08,
            "atr": 40, "bb_width_pct": 30,
        })

    def test_rsi_sweet_spot(self):
        s = sc.score_rsi(self.row_bull)
        self.assertGreaterEqual(s, 70)  # RSI 55 should score high

    def test_rsi_bear_scores_low(self):
        s = sc.score_rsi(self.row_bear)
        self.assertLessEqual(s, 40)  # RSI 25 should score low

    def test_rsi_nan_default(self):
        row = pd.Series({"rsi": float("nan")})
        s = sc.score_rsi(row)
        self.assertEqual(s, 30)  # default strict untuk NaN

    def test_macd_bullish(self):
        s = sc.score_macd(self.row_bull)
        self.assertGreaterEqual(s, 70)

    def test_macd_bearish(self):
        s = sc.score_macd(self.row_bear)
        self.assertLessEqual(s, 30)

    def test_volume_high(self):
        s = sc.score_volume(self.row_bull)
        self.assertGreaterEqual(s, 60)

    def test_volume_low(self):
        s = sc.score_volume(self.row_bear)
        self.assertLessEqual(s, 50)

    def test_trend_bull(self):
        s = sc.score_trend(self.row_bull)
        self.assertGreaterEqual(s, 60)

    def test_trend_bear(self):
        s = sc.score_trend(self.row_bear)
        self.assertLessEqual(s, 40)

    def test_volatility_low(self):
        s = sc.score_volatility(self.row_bull)
        self.assertGreaterEqual(s, 60)

    def test_volatility_high(self):
        s = sc.score_volatility(self.row_bear)
        self.assertLessEqual(s, 40)

    def test_score_components_all_in_0_100(self):
        for fn in [sc.score_rsi, sc.score_macd, sc.score_volume, sc.score_trend, sc.score_volatility]:
            s = fn(self.row_bull)
            self.assertGreaterEqual(s, 0, f"{fn.__name__} < 0")
            self.assertLessEqual(s, 100, f"{fn.__name__} > 100")

    def test_compute_total_score_range(self):
        t = sc.compute_total_score(self.row_bull, "BULL")
        self.assertGreaterEqual(t, 0)
        self.assertLessEqual(t, 100)

    def test_compute_total_score_bull_higher_than_bear(self):
        s_bull = sc.compute_total_score(self.row_bull, "BULL")
        s_bear = sc.compute_total_score(self.row_bear, "BEAR")
        self.assertGreater(s_bull, s_bear)


class TestClassify(unittest.TestCase):
    def test_bull_strong_buy(self):
        self.assertEqual(sc.classify(80, "BULL"), "STRONG_BUY")

    def test_bull_buy(self):
        self.assertEqual(sc.classify(65, "BULL"), "BUY")

    def test_bull_weak_buy(self):
        self.assertEqual(sc.classify(50, "BULL"), "WEAK_BUY")

    def test_bull_hold(self):
        self.assertEqual(sc.classify(38, "BULL"), "HOLD")

    def test_bull_sell(self):
        self.assertEqual(sc.classify(30, "BULL"), "SELL")

    def test_bear_strong_buy(self):
        self.assertEqual(sc.classify(82, "BEAR"), "STRONG_BUY")

    def test_bear_sell(self):
        self.assertEqual(sc.classify(35, "BEAR"), "SELL")

    def test_ranging_strong_buy(self):
        self.assertEqual(sc.classify(72, "RANGING"), "STRONG_BUY")

    def test_ranging_sell(self):
        self.assertEqual(sc.classify(25, "RANGING"), "SELL")

    def test_edge_boundaries(self):
        """Test exact boundary values with new thresholds."""
        # BULL: STRONG_BUY ≥78, BUY ≥63, WEAK_BUY ≥50, HOLD ≥38
        self.assertEqual(sc.classify(78, "BULL"), "STRONG_BUY")
        self.assertEqual(sc.classify(77, "BULL"), "BUY")
        self.assertEqual(sc.classify(63, "BULL"), "BUY")
        self.assertEqual(sc.classify(62, "BULL"), "WEAK_BUY")
        # RANGING: STRONG_BUY ≥72, BUY ≥58, WEAK_BUY ≥45
        self.assertEqual(sc.classify(72, "RANGING"), "STRONG_BUY")
        self.assertEqual(sc.classify(71, "RANGING"), "BUY")
        self.assertEqual(sc.classify(58, "RANGING"), "BUY")
        self.assertEqual(sc.classify(57, "RANGING"), "WEAK_BUY")


class TestRiskManagement(unittest.TestCase):
    def test_position_size_normal(self):
        lots = rm.position_size(100_000_000, 5000, 150)
        self.assertGreater(lots, 0)
        self.assertEqual(lots % 100, 0)

    def test_position_size_penny_return_0(self):
        lots = rm.position_size(100_000_000, 50, 0.1)
        self.assertEqual(lots, 0)

    def test_position_size_capped_at_10pct(self):
        lots = rm.position_size(1_000_000_000, 5000, 150)
        # 10% of 1B = 100jt, at 5000 = 20,000 saham max
        self.assertLessEqual(lots, 20000)

    def test_stop_loss_below_price(self):
        sl = rm.calculate_stop_loss(5000, 150)
        self.assertLess(sl, 5000)

    def test_take_profit_above_price(self):
        tp = rm.calculate_take_profit(5000, 150)
        self.assertGreater(tp, 5000)

    def test_rrr_at_least_1(self):
        sl = rm.calculate_stop_loss(5000, 150)
        tp = rm.calculate_take_profit(5000, 150)
        rrr = (tp - 5000) / (5000 - sl)
        self.assertGreaterEqual(rrr, 1.0)

    def test_kelly_reasonable(self):
        k = rm.kelly_fraction(0.55, 300, 200)
        self.assertGreater(k, 0)
        self.assertLessEqual(k, 0.25)

    def test_small_account_position_zero(self):
        lots = rm.position_size(1_000_000, 50000, 2000)
        self.assertEqual(lots, 0)


class TestRegimeDetection(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2025-01-01", periods=150, freq="B")
        np.random.seed(42)
        # Trending up + strong ADX
        self.df_bull = pd.DataFrame({
            "close": 1000 + np.cumsum(np.random.randn(150) * 3) + np.linspace(0, 200, 150),
            "adx": np.random.uniform(25, 45, 150),
        }, index=dates)

        self.df_bear = pd.DataFrame({
            "close": 2000 - np.cumsum(np.random.randn(150) * 3) - np.linspace(0, 200, 150),
            "adx": np.random.uniform(25, 45, 150),
        }, index=dates)

        self.df_ranging = pd.DataFrame({
            "close": 1000 + np.random.randn(150) * 10,
            "adx": np.random.uniform(10, 20, 150),
        }, index=dates)

    def test_regime_returns_valid_string(self):
        r, s, a = rg.detect_market_regime(self.df_bull)
        self.assertIn(r, ("BULL", "BEAR", "RANGING", "HIGH_VOLATILITY"))

    def test_regime_score_is_float(self):
        r, s, a = rg.detect_market_regime(self.df_bull)
        self.assertIsInstance(s, float)

    def test_adx_is_float(self):
        r, s, a = rg.detect_market_regime(self.df_bull)
        self.assertIsInstance(a, float)

    def test_empty_df_returns_ranging(self):
        r, s, a = rg.detect_market_regime(pd.DataFrame())
        self.assertEqual(r, "RANGING")


class TestRiskReward(unittest.TestCase):
    def test_risk_reward_structure(self):
        row = pd.Series({"close": 5000, "atr": 150})
        rr = sc.compute_risk_reward(row)
        self.assertIn("stop_loss", rr)
        self.assertIn("take_profit", rr)
        self.assertIn("rrr", rr)
        self.assertGreater(rr["stop_loss"], 0)
        self.assertGreater(rr["take_profit"], 0)
        self.assertGreaterEqual(rr["rrr"], 1.0)

    def test_risk_reward_nan_atr(self):
        row = pd.Series({"close": 5000, "atr": float("nan")})
        rr = sc.compute_risk_reward(row)
        self.assertEqual(rr["stop_loss"], 0)


class TestComputeAllIndicators(unittest.TestCase):
    def test_all_indicators_present(self):
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        df = pd.DataFrame({
            "open": 1000 + np.cumsum(np.random.randn(200)),
            "high": 1010 + np.cumsum(np.random.randn(200)),
            "low": 990 + np.cumsum(np.random.randn(200)),
            "close": 1000 + np.cumsum(np.random.randn(200)),
            "volume": np.random.randint(1_000_000, 10_000_000, 200),
        }, index=dates)
        result = data.compute_all_indicators(df)
        required = ["rsi", "macd", "adx", "atr", "vol_ratio", "ema12", "ema50"]
        for col in required:
            self.assertIn(col, result.columns, f"Missing column: {col}")

    def test_last_row_no_nan_in_critical(self):
        dates = pd.date_range("2025-01-01", periods=200, freq="B")
        df = pd.DataFrame({
            "open": 1000 + np.cumsum(np.random.randn(200)),
            "high": 1010 + np.cumsum(np.random.randn(200)),
            "low": 990 + np.cumsum(np.random.randn(200)),
            "close": 1000 + np.cumsum(np.random.randn(200)),
            "volume": np.random.randint(1_000_000, 10_000_000, 200),
        }, index=dates)
        result = data.compute_all_indicators(df)
        last = result.iloc[-1]
        critical = ["rsi", "macd", "adx", "atr", "vol_ratio"]
        for col in critical:
            self.assertFalse(pd.isna(last.get(col)),
                            f"NaN in last row: {col}")


class TestAlignToMarket(unittest.TestCase):
    def test_align_adds_idx_columns(self):
        dates = pd.date_range("2025-01-01", periods=100, freq="B")
        df = pd.DataFrame({
            "close": 1000 + np.cumsum(np.random.randn(100)),
            "high": 1010 + np.cumsum(np.random.randn(100)),
            "low": 990 + np.cumsum(np.random.randn(100)),
            "volume": np.random.randint(1_000_000, 10_000_000, 100),
        }, index=dates)
        result = data.align_to_market(df)
        for col in ["idx_close", "idx_ret_20d", "idx_volatility"]:
            self.assertIn(col, result.columns)


class TestExpectedReturn(unittest.TestCase):
    """Test expected_return() dengan fallback values real dari backtest."""

    def test_expected_return_strong_buy_fallback(self):
        """STRONG_BUY fallback ≈ 0.31 (delta 0.5 karena CSV bisa beda)."""
        val = sc.expected_return("STRONG_BUY")
        self.assertAlmostEqual(val, 0.31, delta=0.5)

    def test_expected_return_buy_fallback(self):
        """BUY fallback ≈ -0.95 (delta 0.5 karena CSV bisa beda)."""
        val = sc.expected_return("BUY")
        self.assertAlmostEqual(val, -0.95, delta=0.5)

    def test_expected_return_weak_buy_fallback(self):
        val = sc.expected_return("WEAK_BUY")
        self.assertAlmostEqual(val, -0.94, delta=0.5)

    def test_expected_return_hold_fallback(self):
        val = sc.expected_return("HOLD")
        self.assertAlmostEqual(val, -1.22, delta=0.5)

    def test_expected_return_sell_fallback(self):
        val = sc.expected_return("SELL")
        self.assertAlmostEqual(val, -0.07, delta=0.5)

    def test_expected_return_case_insensitive(self):
        val = sc.expected_return("strong_buy")
        self.assertAlmostEqual(val, 0.31, delta=0.5)

    def test_expected_return_unknown_signal(self):
        val = sc.expected_return("INVALID")
        self.assertEqual(val, 0.0)


if __name__ == "__main__":
    unittest.main()
