# scalp/__init__.py — Scalping Strategy Package (v2.0)
# ==============================================================
# Replaces: 1_producer_data.py, 2_consumer_ai.py, 3_consumer_r1.py
#
# Architecture:
#   scalp/producer.py  — Data ingestion (1m OHLCV → SQLite)
#   scalp/signals.py   — Signal generation (ORB + VWAP + Momentum)
#   scalp/ai.py        — Intraday AI model inference (no proxies)
#   scalp/executor.py  — Paper trading + trailing stop execution
#   scalp/backtest.py  — 1m event-driven replay backtest
#   scalp/run.py       — CLI entry point (python -m scalp.run)
#
# Infrastructure (shared with swing system):
#   src/data/schema.py        — Unified DB schema
#   src/data/fetcher.py       — Abstract DataSource + implementations
#   dashboard/alerts.py       — Discord + Telegram dispatch
#   risk/kill_switch.py       — 5-level risk hierarchy
#   config/settings.yaml      — All parameters (scalp section)

__version__ = "2.0.0"
__package_name__ = "scalp"
