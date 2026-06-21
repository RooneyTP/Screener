# scalp/config.py — Typed configuration loader for scalping strategy
# ===================================================================
# Loads the 'scalp' section from config/settings.yaml.
# Zero magic numbers in code — all parameters come from config.

import os
import yaml
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Resolve project root (c:/Screener) ──────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _PROJECT_ROOT / "config" / "settings.yaml"


@dataclass
class ScalpConfig:
    """Typed configuration for the scalping strategy.

    All values are loaded from config/settings.yaml → scalp section.
    Sensible defaults are provided as fallback for missing keys.
    """

    # ── Data Source ──────────────────────────────────────────────
    data_source: str = "yfinance"
    cycle_interval_secs: int = 30
    max_concurrent: int = 5
    timeout_secs: int = 5
    max_retries: int = 2
    retry_delay_secs: float = 1.0
    skip_after_failures: int = 3

    # ── Trading Hours (WIB / UTC+7) ──────────────────────────────
    session_start: str = "09:00"
    auction_end: str = "09:05"
    morning_breakout_start: str = "09:05"
    morning_breakout_end: str = "09:30"
    lunch_start: str = "11:30"
    lunch_end: str = "13:00"
    afternoon_session_start: str = "13:00"
    pre_close_start: str = "15:45"
    session_end: str = "16:00"

    # ── Time Filters ─────────────────────────────────────────────
    skip_auction: bool = True
    skip_lunch: bool = True
    skip_pre_close: bool = True

    # ── Indicators ───────────────────────────────────────────────
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    adx_period: int = 14
    adx_threshold: int = 25
    vwap_period: int = 60
    volume_sma_period: int = 10

    # ── Signal Thresholds ────────────────────────────────────────
    morning_min_bars: int = 10
    morning_vol_spike_mult: float = 2.5
    afternoon_min_bars: int = 30
    afternoon_vol_spike_mult: float = 2.0
    afternoon_rsi_min: int = 40
    afternoon_rsi_max: int = 70
    afternoon_adx_min: int = 20
    min_transaction_value: float = 50_000_000  # Rp 50M

    # ── AI ───────────────────────────────────────────────────────
    ai_model_path: str = "ensemble_model.pkl"
    ai_confidence_threshold: float = 55.0

    # ── Execution ────────────────────────────────────────────────
    tp_pct: float = 0.015                # 1.5% take profit
    sl_pct: float = 0.01                 # 1.0% stop loss
    spread_buffer_min_pct: float = 0.002  # 0.2% minimum spread buffer
    breakeven_trigger_pct: float = 0.008  # 0.8% profit → SL to breakeven
    trailing_distance_pct: float = 0.005  # Trail 0.5% below highest price
    trailing_activation_pct: float = 0.015  # After 1.5% profit
    max_daily_loss_pct: float = 0.03     # 3% daily loss → halt
    max_positions: int = 5
    cooldown_minutes: int = 5
    poll_fast_secs: float = 1.0
    poll_idle_secs: float = 3.0
    position_size_pct: float = 0.10      # 10% of equity per position

    # ── Fees ────────────────────────────────────────────────────
    buy_fee_pct: float = 0.0015          # 0.15%
    sell_fee_pct: float = 0.0025         # 0.25%
    slippage_pct: float = 0.002          # 0.20% base

    # ── Portfolio ────────────────────────────────────────────────
    capital_initial: float = 100_000_000.0  # Rp 100M

    # ── Database ─────────────────────────────────────────────────
    histori_db_name: str = "histori_ihsg.db"
    portfolio_db_name: str = "portofolio_virtual.db"

    # ── Tickers ──────────────────────────────────────────────────
    tickers: list[str] = field(default_factory=list)

    # ── Loader ───────────────────────────────────────────────────
    @classmethod
    def from_yaml(cls, config_path: str | Path | None = None) -> "ScalpConfig":
        """Load ScalpConfig from config/settings.yaml.

        Args:
            config_path: Optional path override. Defaults to
                         config/settings.yaml relative to project root.
        """
        path = Path(config_path) if config_path else _CONFIG_PATH

        if not path.exists():
            logger.warning("Config file not found at %s — using defaults", path)
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        scalp = data.get("scalp", {})
        if not scalp:
            logger.warning("No 'scalp' section in config — using defaults")
            return cls()

        # ── Tickers ──────────────────────────────────────────────
        tickers_raw = data.get("tickers")
        if tickers_raw is None:
            # Fallback to hardcoded list in config if present under scalp
            tickers_raw = scalp.get("tickers", [])

        # ── Nested sections ──────────────────────────────────────
        ds = scalp.get("data_source", {})
        th = scalp.get("trading_hours", {})
        tf = scalp.get("time_filters", {})
        ind = scalp.get("indicators", {})
        sig = scalp.get("signals", {})
        ai_sec = scalp.get("ai", {})
        ex = scalp.get("execution", {})
        mbo = sig.get("morning_breakout", {})
        amo = sig.get("afternoon_momentum", {})
        liq = sig.get("liquidity", {})

        return cls(
            # Data Source
            data_source=ds.get("primary", "yfinance"),
            cycle_interval_secs=ds.get("cycle_interval_secs", 30),
            max_concurrent=ds.get("max_concurrent", 5),
            timeout_secs=ds.get("timeout_secs", 5),
            max_retries=ds.get("max_retries", 2),
            retry_delay_secs=ds.get("retry_delay_secs", 1.0),
            skip_after_failures=ds.get("skip_after_failures", 3),
            # Trading Hours
            session_start=th.get("session_start", "09:00"),
            auction_end=th.get("auction_end", "09:05"),
            morning_breakout_start=th.get("morning_breakout_window", {}).get("start", "09:05"),
            morning_breakout_end=th.get("morning_breakout_window", {}).get("end", "09:30"),
            lunch_start=th.get("lunch_start", "11:30"),
            lunch_end=th.get("lunch_end", "13:00"),
            afternoon_session_start=th.get("afternoon_session_start", "13:00"),
            pre_close_start=th.get("pre_close_start", "15:45"),
            session_end=th.get("session_end", "16:00"),
            # Time Filters
            skip_auction=tf.get("skip_auction", True),
            skip_lunch=tf.get("skip_lunch", True),
            skip_pre_close=tf.get("skip_pre_close", True),
            # Indicators
            ema_fast=ind.get("ema_fast", 9),
            ema_slow=ind.get("ema_slow", 21),
            rsi_period=ind.get("rsi_period", 14),
            adx_period=ind.get("adx_period", 14),
            adx_threshold=ind.get("adx_threshold", 25),
            vwap_period=ind.get("vwap_period", 60),
            volume_sma_period=ind.get("volume_sma_period", 10),
            # Signal Thresholds
            morning_min_bars=mbo.get("min_bars", 10),
            morning_vol_spike_mult=mbo.get("vol_spike_mult", 2.5),
            afternoon_min_bars=amo.get("min_bars", 30),
            afternoon_vol_spike_mult=amo.get("vol_spike_mult", 2.0),
            afternoon_rsi_min=amo.get("rsi_min", 40),
            afternoon_rsi_max=amo.get("rsi_max", 70),
            afternoon_adx_min=amo.get("require_adx_above", 20),
            min_transaction_value=liq.get("min_transaction_value", 50_000_000),
            # AI
            ai_model_path=ai_sec.get("model_path", "ensemble_model.pkl"),
            ai_confidence_threshold=ai_sec.get("confidence_threshold", 55.0),
            # Execution
            tp_pct=ex.get("tp_pct", 0.015),
            sl_pct=ex.get("sl_pct", 0.01),
            spread_buffer_min_pct=ex.get("spread_buffer_min_pct", 0.002),
            breakeven_trigger_pct=ex.get("breakeven_trigger_pct", 0.008),
            trailing_distance_pct=ex.get("trailing_distance_pct", 0.005),
            trailing_activation_pct=ex.get("trailing_activation_pct", 0.015),
            max_daily_loss_pct=ex.get("max_daily_loss_pct", 0.03),
            max_positions=ex.get("max_positions", 5),
            cooldown_minutes=ex.get("cooldown_minutes", 5),
            poll_fast_secs=ex.get("poll_fast_secs", 1.0),
            poll_idle_secs=ex.get("poll_idle_secs", 3.0),
            position_size_pct=ex.get("position_size_pct", 0.10),
            # Fees (from shared execution section if not in scalp)
            buy_fee_pct=data.get("execution", {}).get("buy_fee_pct", 0.0015),
            sell_fee_pct=data.get("execution", {}).get("sell_fee_pct", 0.0025),
            slippage_pct=data.get("execution", {}).get("slippage_pct", 0.001),
            # Portfolio
            capital_initial=ex.get("capital_initial", 100_000_000.0),
            # Database
            histori_db_name=scalp.get("database", {}).get("histori_db", "histori_ihsg.db"),
            portfolio_db_name=scalp.get("database", {}).get("portfolio_db", "portofolio_virtual.db"),
            # Tickers
            tickers=list(dict.fromkeys(tickers_raw)) if tickers_raw else [],
        )

    def _parse_time(self, t: str) -> tuple[int, int]:
        """Parse 'HH:MM' → (hour, minute)."""
        parts = t.split(":")
        return int(parts[0]), int(parts[1])

    def to_minutes(self, t: str | None = None) -> int:
        """Convert time string to minutes since midnight."""
        if t is None:
            return 0
        h, m = self._parse_time(t)
        return h * 60 + m

    # ── Computed time-in-minutes properties ──────────────────────
    @property
    def minute_auction_end(self) -> int:
        return self.to_minutes(self.auction_end)

    @property
    def minute_morning_breakout_start(self) -> int:
        return self.to_minutes(self.morning_breakout_start)

    @property
    def minute_morning_breakout_end(self) -> int:
        return self.to_minutes(self.morning_breakout_end)

    @property
    def minute_lunch_start(self) -> int:
        return self.to_minutes(self.lunch_start)

    @property
    def minute_lunch_end(self) -> int:
        return self.to_minutes(self.lunch_end)

    @property
    def minute_pre_close_start(self) -> int:
        return self.to_minutes(self.pre_close_start)

    @property
    def minute_session_end(self) -> int:
        return self.to_minutes(self.session_end)
