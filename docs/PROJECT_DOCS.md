# FullStack Quant Trader — Project Documentation v3.0

> **Last Updated:** 2026-05-15  
> **Architecture:** Swing (Daily) + Scalping (Intraday)  
> **Built per** [`SKILL.md`](SKILL.md) — Fullstack Quantitative Trading System  

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [File Catalog](#2-file-catalog)
3. [Swing System — File-by-File](#3-swing-system)
4. [Scalping System — File-by-File](#4-scalping-system)
5. [Shared Infrastructure](#5-shared-infrastructure)
6. [Configuration Files](#6-configuration-files)
7. [Runtime Commands](#7-runtime-commands)
8. [Weekly Schedule](#8-weekly-schedule)

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                    FULLSTACK TRADING SYSTEM                          │
│                                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐        │
│  │  DATA    │─▶│  SIGNAL  │─▶│  RISK    │─▶│  EXECUTION   │        │
│  │  ENGINE  │  │  ENGINE  │  │  ENGINE  │  │  ENGINE      │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────┘        │
│       │              │              │               │                │
│       └──────────────┴──────────────┴───────────────┘               │
│                              │                                       │
│                    ┌─────────▼──────────┐                           │
│                    │  MONITORING + BOT   │                           │
│                    │  Dashboard / Alert  │                           │
│                    └────────────────────┘                           │
└──────────────────────────────────────────────────────────────────────┘
```

### Modes

| Mode | Timeframe | Hold Duration | Data | Entry Point |
|------|-----------|---------------|------|-------------|
| **Swing** | 1D | Hours–Days | Yahoo Finance → CSV/Parquet | `python screener.py` |
| **Scalping** | 1M | Seconds–Minutes | Yahoo Finance → SQLite | `python -m scalp.run all` |

---

## 2. File Catalog

### Core (Root)

| File | Mode | Purpose |
|------|------|---------|
| [`screener.py`](screener.py) | 🔄 Swing | Main engine: 170+ IHSG tickers, 18 indicators, 15-pt scoring, AI ensemble, CSV output |
| [`backtest.py`](backtest.py) | 🔄 Swing | Event-driven backtest + walk-forward optimization + tearsheet metrics |
| [`latih_ai.py`](latih_ai.py) | 🔄 Swing | Train XGBoost+RF+HGB ensemble → `ensemble_model.pkl` |
| [`auto_train.py`](auto_train.py) | 🔄 Swing | Automated periodic AI model retraining |
| [`indicators.py`](indicators.py) | 🔄 Swing | Pure functions: EMA, SMA, RSI, MACD, ADX, ATR, Bollinger, VWAP |
| [`ai_model.py`](ai_model.py) | 🔄 Both | `MarketAI` class — HistGradientBoosting fallback for swing & scalping |
| [`data_fetcher.py`](data_fetcher.py) | 🔄 Swing | Macro data fetcher (IHSG, SP500, USD, Oil, Gold) |
| [`foreign_flow.py`](foreign_flow.py) | 🔄 Swing | Foreign flow scraper from RTI Business |
| [`nlp_scraper.py`](nlp_scraper.py) | 🔄 Swing | NLP sentiment from Yahoo Finance news |
| [`broker_scraper.py`](broker_scraper.py) | 🔄 Swing | Broker summary analysis (bandarmologi) |
| [`monte_carlo.py`](monte_carlo.py) | 🔄 Both | Monte Carlo simulation + position size suggestion |
| [`mean_reversion.py`](mean_reversion.py) | 🔄 Swing | Mean reversion signal detector |
| [`security.py`](security.py) | 🔄 Both | `.env` loader, encryption, API key management |
| [`performance.py`](performance.py) | 🔄 Swing | Portfolio performance analytics |
| [`trade_journal.py`](trade_journal.py) | 🔄 Both | Trade logging + weekly heatmap generator |
| [`mesin_waktu.py`](mesin_waktu.py) | 🔄 Swing | Time-machine backtest (multi-period replay) |

### Dashboard & Automation

| File | Purpose |
|------|---------|
| [`dashboard/app.py`](dashboard/app.py) | Streamlit dashboard — 6 tabs (Account, Backtest, Drill-Down, WF+AI, Alerts, Scalping) |
| [`dashboard/alerts.py`](dashboard/alerts.py) | Centralized AlertManager — Discord embed + Telegram + persistent log |
| [`telegram_bot.py`](telegram_bot.py) | Interactive Telegram bot — 10 commands for swing & scalp |
| [`auto_alert.py`](auto_alert.py) | APScheduler background — drawdown check (5m), morning (09:00), closing (15:00) |
| [`run_bot.py`](run_bot.py) | Legacy run wrapper |
| [`evaluasi_bot.py`](evaluasi_bot.py) | Bot performance evaluator |

### Risk Management

| File | Purpose |
|------|---------|
| [`risk/kill_switch.py`](risk/kill_switch.py) | 5-level hierarchy: per-trade (1%), daily (5%), weekly (8%), monthly (15%), peak (20%) |
| [`risk/correlation.py`](risk/correlation.py) | Pairwise correlation tracker — treat correlated positions as single |

### Config

| File | Purpose |
|------|---------|
| [`config/settings.yaml`](config/settings.yaml) | All strategy + system parameters (swing + scalp sections) |
| [`.env`](.env) | API keys: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DISCORD_WEBHOOK |
| [`docker-compose.yml`](docker-compose.yml) | Docker placeholder (currently not needed — everything local) |

### Scalping Package (`scalp/`)

| File | Purpose |
|------|---------|
| [`scalp/__init__.py`](scalp/__init__.py) | Package metadata v2.0.0 |
| [`scalp/config.py`](scalp/config.py) | `ScalpConfig` dataclass — 50+ typed fields from YAML |
| [`scalp/signals.py`](scalp/signals.py) | Intraday signal pipeline — 15 real features, ORB, momentum |
| [`scalp/ai.py`](scalp/ai.py) | Intraday AI — 8-feat vector, ensemble inference, heuristic fallback |
| [`scalp/producer.py`](scalp/producer.py) | Data producer — 1m OHLCV → SQLite, 30s cycle |
| [`scalp/executor.py`](scalp/executor.py) | Paper trading executor — trailing stop, shared alerts + kill switch |
| [`scalp/backtest.py`](scalp/backtest.py) | Intraday event-driven backtest — time-band metrics |
| [`scalp/run.py`](scalp/run.py) | CLI entry: `python -m scalp.run producer/executor/all` |

### Shared Libraries (`src/`)

| File | Purpose |
|------|---------|
| [`src/data/schema.py`](src/data/schema.py) | Unified DB schema — all CREATE TABLE in one place |
| [`src/data/fetcher.py`](src/data/fetcher.py) | Pluggable DataSource — YahooFinance, Composite, Stockbit placeholder |
| [`src/signals/scoring.py`](src/signals/scoring.py) | Swing scoring utilities |
| [`src/signals/swing_strategy.py`](src/signals/swing_strategy.py) | Swing-specific signal logic |
| [`src/signals/ai_coordinator.py`](src/signals/ai_coordinator.py) | AI coordinator — ensemble > fallback routing |
| [`src/execution/sizer.py`](src/execution/sizer.py) | Position sizing models (fixed fractional, ATR-based, half-Kelly) |
| [`src/execution/slippage.py`](src/execution/slippage.py) | ATR-based slippage model for scalping |

### Legacy (Working — Keep for reference)

| File | Purpose |
|------|---------|
| [`1_producer_data.py`](1_producer_data.py) | Original scalping producer (now in `scalp/producer.py`) |
| [`2_consumer_ai.py`](2_consumer_ai.py) | Original scalping signal generator (now in `scalp/signals.py` + `scalp/ai.py`) |
| [`3_consumer_r1.py`](3_consumer_r1.py) | Original scalping executor (now in `scalp/executor.py`) |
| [`data_pipeline.py`](data_pipeline.py) | Legacy data pipeline |
| [`train_scalping.py`](train_scalping.py) | Standalone scalping AI trainer |

### Storage & Output

| Path | Contents |
|------|----------|
| `screener_ihsg_YYYYMMDD.csv` | Daily screener output (full columns including AI predictions) |
| `data_lake/histori_ihsg.parquet` | Aggregated historical data (93K+ rows, 560 dates) |
| `histori_ihsg.db` | 1m OHLCV + signals for scalping |
| `portofolio_virtual.db` | Virtual portfolio: positions, trade history, cash, state |
| `ensemble_model.pkl` | Trained XGBoost+RF+HGB ensemble |
| `logs/` | All system logs (screener, telegram_bot, scalp_producer, etc.) |

---

## 3. Swing System

### 3.1 [`screener.py`](screener.py) — Core Engine

**Purpose:** Daily IHSG stock screener — scans 170+ tickers across 11 sectors.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `analisis_saham(ticker)` | 1322 | Complete analysis: 18 indicators + scoring + AI → signal |
| `jalankan_screener()` | 2100+ | Main orchestrator — iterate all tickers, generate CSV |
| `compute_sector_momentum()` | 550 | Lazy-load sector rotation data |
| `update_macro_globals()` | 73 | Update IHSG, SP500, USD, Oil, Gold macro data |
| `backtest_signals(df)` | 996 | Run event-driven backtest on screener output |
| `position_size_calc()` | 1211 | Fixed fractional position sizing (1% risk) |
| `kirim_notifikasi_discord()` | 1228 | Send screener report to Discord via webhook |
| `optimize_portfolio()` | 1128 | Mean-variance optimization via scipy |
| `hitung_kelly_sizing()` | 368 | Half-Kelly position size calculator |

**Signal Output (15-point scoring):**

| Signal | Threshold | Meaning |
|--------|-----------|---------|
| ULTRA_BUY (A+) | Confidence ≥85, Skor ≥10, RRR ≥1.8 | Everything aligned — strongest |
| STRONG_BUY (B) | Confidence ≥75, Skor ≥8 | High confidence, good setup |
| BUY (C) | Confidence ≥50, Skor ≥4 | Moderate, tradable |
| PANTAU (D) | Confidence ≥30, Skor ≥2 | Monitor — not ready yet |
| TUNGGU (E) | Skor ≥-15 | Wait for better entry |
| HINDARI (F) | Critical risk or very negative | Avoid entirely |

**Scoring Components (35/25/20/20):**

| Tier | Weight | Components |
|------|--------|------------|
| Technical | 35% | EMA (21/50/HMA), RSI (14), MACD, ADX, Volume, VCP, Bollinger |
| Fundamental | 25% | PER ≤12, PBV ≤1.0, Earnings Growth |
| Relative Strength | 20% | vs IHSG, Sector Leadership, Alpha |
| Sentiment | 20% | NLP News (VADER), Foreign Flow, Broker Accumulation |

---

### 3.2 [`backtest.py`](backtest.py) — Backtest Engine

**Purpose:** Event-driven backtest + walk-forward optimization for swing signals.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `backtest(signals_df, prices_df, sl_atr_mult, tp_atr_mult)` | 34 | Event-driven backtest with adjustable SL/TP multipliers |
| `_simulate_exit(entry, sl, tp, rrr, ticker, prices_df, confidence, skor)` | 90 | Deterministic exit model — quality-score based (no random) |
| `_apply_costs(entry_price, exit_price)` | 28 | Apply slippage + buy/sell fees |
| `backtest_report(signals_df, prices_df)` | 102 | Detailed per-trade report |
| `walk_forward_optimize(signals_by_date, param_grid)` | 124 | Rolling IS/OOS windows + grid search SL×TP multiplier |
| `compute_tearsheet(trade_returns)` | 165 | Full metrics: Sharpe, Sortino, PF, MaxDD, Win Rate, Expectancy |

**Cost Model:**
| Cost | Rate |
|------|------|
| Slippage | 0.10% per side |
| Buy fee | 0.15% |
| Sell fee | 0.25% |
| Total round-trip | ~0.50% |

---

### 3.3 [`latih_ai.py`](latih_ai.py) — AI Training

**Purpose:** Train XGBoost+RandomForest+HistGradientBoosting ensemble with SMOTE oversampling.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `train_ensemble()` | Main | Load CSV data → train 3 models → save `ensemble_model.pkl` |
| `prepare_features()` | Helper | Build 14-feature vector from screener CSV |

**14 Features:** Skor, Confidence%, RSI, ADX, Stoch, CCI, BB_Width%, RRR, MM_Confidence, MM_vs_Retail_Ratio, IHSG_Change, USD_Change, RSI_1d, MACD_1d

---

### 3.4 [`indicators.py`](indicators.py) — Technical Indicators

**Purpose:** Pure functions for all technical indicator calculations.

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `calculate_sma(data, period)` | Simple Moving Average |
| `calculate_ema(data, period)` | Exponential Moving Average |
| `calculate_rsi(data, period=14)` | Relative Strength Index |
| `calculate_macd(data)` | MACD (line, signal, histogram) |
| `calculate_adx(high, low, close, period=14)` | Average Directional Index |
| `calculate_atr(high, low, close, period=14)` | Average True Range |
| `calculate_bollinger_bands(data, period=20)` | Bollinger Bands (upper, middle, lower) |
| `calculate_vwap(high, low, close, volume)` | Volume Weighted Average Price |
| `hma(data, period=20)` | Hull Moving Average |

---

### 3.5 [`ai_model.py`](ai_model.py) — AI Model Base

**Purpose:** `MarketAI` class — HistGradientBoosting fallback used by both swing & scalping.

**Key Functions:**

| Function | Purpose |
|----------|---------|
| `MarketAI(model_type)` | Initialize AI model (scalping or swing) |
| `train_model(X, y)` | Train HistGradientBoosting model |
| `predict_win_probability(features)` | Predict win probability (0-100) |
| `get_ai_model(model_type)` | Factory: get singleton AI instance |

---

## 4. Scalping System

### 4.1 [`scalp/producer.py`](scalp/producer.py) — Data Producer

**Purpose:** Fetch 1-minute OHLCV for 170+ IHSG tickers, store in SQLite every 30 seconds.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `producer_loop(source, db_path)` | 59 | Infinite loop: fetch → store → stats → sleep |
| `should_skip(ticker)` | 44 | Skip tickers with ≥3 consecutive failures |
| `run_producer()` | 109 | Synchronous entry point |

**Data Flow:** `Yahoo Finance API → asyncio batch fetch → histori_ihsg.db`

**Configurable Parameters:** Cycle interval (30s), max concurrent (5), timeout (5s), retries (2)

---

### 4.2 [`scalp/signals.py`](scalp/signals.py) — Signal Pipeline

**Purpose:** Compute 15 real intraday features, detect ORB + momentum signals. **Zero proxy values.**

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `is_trading_allowed(config)` | 85 | Time filter: auction, lunch, pre-close |
| `get_session(config)` | 106 | Return current session: morning/afternoon/closed |
| `compute_intraday_features(open_, high, low, close, volume, config)` | 140 | Compute ALL 15 features from actual 1m OHLCV |
| `detect_morning_breakout(feat, config)` | 244 | ORB strategy: 09:05-09:30, price>VWAP+volume 2.5× |
| `detect_afternoon_momentum(feat, config)` | 290 | VWAP+EMA+RSI+Volume+ADX: 09:30-15:45 |
| `get_market_context(config)` | 210 | IHSG/USD % change, daily RSI trend |
| `build_signal(ticker, ...)` | 311 | Complete pipeline: filter → features → strategy → signal |
| `compute_signal_score(result)` | 335 | 0-15 point quality score for AI input |

**15 Computed Features:**

| # | Feature | Source |
|---|---------|--------|
| 1 | VWAP | volume-weighted calculation |
| 2 | EMA9 | Exponential moving average |
| 3 | EMA21 | Exponential moving average |
| 4 | RSI | RSI(14) |
| 5 | ADX | ADX(14) |
| 6 | Stoch K/D | Stochastic(14,3) |
| 7 | CCI | CCI(20) |
| 8 | BB Width% | Bollinger Band width |
| 9 | Vol SMA10 | Volume moving average |
| 10 | Vol Ratio | Current/avg volume |
| 11 | VWAP Distance% | Distance from VWAP |
| 12 | EMA Distance% | Distance from EMA9 |
| 13 | Spread% | Avg (H-L)/Close |
| 14 | ORB Position% | Within opening range |
| 15 | Cumulative Delta | Net candle direction |

---

### 4.3 [`scalp/ai.py`](scalp/ai.py) — AI Model

**Purpose:** Build 8-feature intraday vector, predict via ensemble or heuristic.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `build_scalp_feature_vector(feat, ctx, signal_score, rrr)` | 40 | 8 real features + 4 context features |
| `_predict_ensemble(features, config)` | 65 | Ensemble model inference with 14-dim padding |
| `_predict_heuristic(feat, ctx)` | 115 | Fallback heuristic when model unavailable |
| `predict_scalp_signal(result, config)` | 138 | Main prediction: ensemble → heuristic → verdict |
| `get_market_context_cached(ttl_secs)` | 170 | Cached market context (refresh every 5 min) |
| `filter_signals_with_ai(signals, config)` | 180 | Batch prediction + threshold filter |

**Verdicts:** ULTRA BUY (≥65%), BUY (≥55%), WEAK (<55%)

---

### 4.4 [`scalp/executor.py`](scalp/executor.py) — Trade Executor

**Purpose:** Paper trade execution with trailing stop + shared risk management.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `_reset_daily_if_new_day(current_equity)` | 80 | Reset daily PnL tracking at midnight |
| `_read_equity(conn)` | 88 | Read total equity (cash + positions) |
| `_check_limits(conn)` | 96 | Pre-trade risk: kill switch, daily loss, max positions |
| `_check_cooldown(ticker)` | 124 | Enforce cooldown period per ticker |
| `init_portfolio()` | 136 | Initialize portfolio database |
| `_fetch_signal(ticker, hist_conn)` | 143 | Fetch 60 bars → compute signal → AI predict → store |
| `execute_buy(signal, port_conn)` | 175 | Execute paper buy: size check → cash check → insert → alert |
| `monitor_positions(hist_conn, port_conn)` | 210 | Check all open positions: trailing stop → exit → alert |
| `executor_loop(hist_conn, port_conn)` | 278 | Main loop: monitor → filter → scan → sleep |
| `run_executor()` | 310 | Synchronous entry point |

**Trailing Stop Logic:**

| Trigger | Action |
|---------|--------|
| Profit ≥ 0.8% | SL → breakeven (entry price) |
| Profit ≥ 1.5% | Trail SL 0.5% below highest price |
| Price ≥ TP | Exit — TAKE PROFIT |
| Price ≤ SL | Exit — CUT LOSS |

**Risk Limits:**
| Limit | Value |
|-------|-------|
| Max daily loss | 3% (tighter than swing's 5%) |
| Max concurrent positions | 5 |
| Position size | 10% of equity |
| Cooldown | 5 minutes per ticker |

---

### 4.5 [`scalp/backtest.py`](scalp/backtest.py) — Backtest Engine

**Purpose:** Event-driven replay of 1-minute bars with OHLC exit simulation.

**Key Functions:**

| Function | Line | Purpose |
|----------|------|---------|
| `IntradayBacktest.run(db_path, start_date, end_date)` | 72 | Full backtest: replay each day for all tickers |
| `_replay_day(conn, day)` | 100 | Replay single trading day — morning + afternoon |
| `_check_strategy(ticker, day, df, session)` | 130 | Check signal + simulate exit for a session |
| `_simulate_exit(entry, sl, tp, bars, entry_idx)` | 185 | Realistic OHLC exit: high≥TP, low≤SL, trailing stop |
| `_compute_metrics()` | 210 | Full tearsheet with time-band breakdown |

**Output Metrics:**

| Standard | Time-Band (Scalp-Specific) |
|----------|---------------------------|
| Total Trades, Win Rate, Profit Factor | Morning Trades, Morning WR, Morning PnL |
| Sharpe, Sortino, Max Drawdown | Afternoon Trades, Afternoon WR, Afternoon PnL |
| Expectancy, Avg Win/Loss, Avg Holding | Equity Curve, Drawdown Curve |

**CLI:** `python scalp/backtest.py --start 2026-05-01 --end 2026-05-15 --detail`

---

### 4.6 [`scalp/run.py`](scalp/run.py) — CLI Entry

**Commands:**

| Command | What It Starts |
|---------|---------------|
| `python -m scalp.run producer` | Data producer only |
| `python -m scalp.run executor` | Trade executor only |
| `python -m scalp.run all` | Both (2 processes) |
| `python -m scalp.run help` | Help message |

---

## 5. Shared Infrastructure

### 5.1 [`dashboard/app.py`](dashboard/app.py) — Streamlit Dashboard

**6 Tabs:**

| Tab | Content | Refresh |
|-----|---------|---------|
| 💰 Account & Positions | Equity, Cash, Today's PnL, Margin, Kill Switch, Open Positions, Trade History | 10-60s |
| 📈 Backtest Tearsheet | Total Trades, Sharpe, Sortino, PF, MaxDD, Equity Curve, Drawdown Curve | 120s |
| 🔍 Stock Drill-Down | Filterable table: Signal/Sector/MM/Confidence%, Sector breakdown chart | 60s |
| 🧪 Walk-Forward + AI | Best SL/TP, window Sharpe chart, AI histogram, AI verdict pie | 120s |
| 📡 Alerts | Discord/Telegram status, Test Alert, Send Screener Report, Alert log | Manual |
| ⚡ Scalping | Today's PnL, Open Positions (trailing status), Recent Signals, Config | 5-10s |

### 5.2 [`dashboard/alerts.py`](dashboard/alerts.py) — Alert Manager

**Class:** `AlertManager` — single dispatcher for all notification channels.

**Methods:**

| Method | Trigger | Channel | Level |
|--------|---------|---------|-------|
| `send(level, subject, body)` | Generic | Discord+Telegram+Log | Any |
| `daily_drawdown_warning(pnl_pct)` | PnL ≤ -3% / ≤ -5% | All | WARNING/CRITICAL |
| `unrealized_loss_alert(ticker, loss_pct)` | Loss ≥ 2% | Telegram | WARNING |
| `api_error(component, error_msg)` | Any API fail | Telegram | WARNING |
| `kill_switch_triggered(reason)` | Kill switch | ALL CHANNELS | CRITICAL |
| `send_screener_report(ultra, strong, buy)` | Manual/Auto | Discord embed+Telegram | INFO |

### 5.3 [`telegram_bot.py`](telegram_bot.py) — Telegram Bot

**10 Commands:**

| Command | Purpose |
|---------|---------|
| `/start` | Welcome message |
| `/help` | List all commands |
| `/cek TICKER` | Full stock analysis (Skor, RSI, ADX, SL, TP, MM, AI) |
| `/sinyal` | Today's BUY signals (compact list) |
| `/report` | Full screener report → Discord + Telegram |
| `/portfolio` | Equity, Cash, PnL, Open Positions |
| `/status` | Data freshness + signal count |
| `/scalp` | Recent scalp signals |
| `/scalp_pos` | Open scalp positions with trailing status |
| `/scalp_pnl` | Today's scalp PnL |

### 5.4 [`auto_alert.py`](auto_alert.py) — Scheduler

**Scheduled Jobs:**

| Job | Schedule | Action |
|-----|----------|--------|
| Drawdown check | Every 5 minutes | Cek equity vs session start, peak drawdown |
| Morning report | 09:00 WIB Mon-Fri | Kirim screener signal report |
| Closing report | 15:00 WIB Mon-Fri | Kirim PnL daily summary |

### 5.5 [`risk/kill_switch.py`](risk/kill_switch.py) — Kill Switch

**5-Level Hierarchy:**

| Level | Trigger | Action |
|-------|---------|--------|
| 1 — Per Trade | 1% equity risk | Fixed fractional sizing |
| 2 — Per Session | 5% daily loss (swing) / 3% (scalp) | Halt trading for the day |
| 3 — Per Week | 8% weekly drawdown | Reduce position size 50% |
| 4 — Per Month | 15% monthly drawdown | Full strategy review |
| 5 — Account Floor | 20% from peak | **KILL SWITCH — stop all trading** |

### 5.6 [`src/data/schema.py`](src/data/schema.py) — DB Schema

**Tables:**

| Database | Table | Purpose |
|----------|-------|---------|
| `histori_ihsg.db` | `histori_ihsg` | 1m OHLCV data |
| `histori_ihsg.db` | `sinyal_trading` | Generated scalp signals |
| `histori_ihsg.db` | `log_error` | Error logging |
| `histori_ihsg.db` | `consumer_state` | Key-value state store |
| `portofolio_virtual.db` | `akun` | Cash balance |
| `portofolio_virtual.db` | `posisi` | Open positions (swing + scalp via `strategy` column) |
| `portofolio_virtual.db` | `histori_trade` | Completed trade history |
| `portofolio_virtual.db` | `state` | Key-value state (peak equity, etc.) |

### 5.7 [`src/data/fetcher.py`](src/data/fetcher.py) — Data Source

**Classes:**

| Class | Status | Purpose |
|-------|--------|---------|
| `DataSource` (ABC) | ✅ | Abstract base: `fetch_ohlcv()`, `fetch_batch()` |
| `OHLCV` (dataclass) | ✅ | 1m candle: open, high, low, close, volume |
| `FetchResult` (dataclass) | ✅ | Fetch result wrapper |
| `YahooFinanceSource` | ✅ | Production source — Yahoo Finance chart API |
| `CompositeSource` | ✅ | Primary → fallback chain |
| `StockbitSource` | ⬜ Placeholder | Target primary source (WebSocket streaming) |

---

## 6. Configuration Files

### 6.1 [`config/settings.yaml`](config/settings.yaml)

**Sections:**

| Section | Key Parameters |
|---------|---------------|
| `strategy` | mode (swing/scalping/both), asset_class, exchange |
| `data` | source, period, interval, cache, sanity filters |
| `scoring` | max_score, weights (technical/fundamental/RS/sentiment) |
| `signals` | ultra_buy/strong_buy/buy thresholds |
| `risk` | per_trade_risk_pct, kill_switch limits, position_sizing, volatility_filter, correlation |
| `execution` | slippage_pct, buy/sell fee, max_positions, cooldown, latency_buffer, order_types |
| `backtest` | engine, min_trades, required_metrics, overfitting_defenses |
| `notifications` | discord/telegram enabled flags |
| `logging` | level, format, persistent, log_dir |
| `scalp` | Full scalping config (data_source, trading_hours, indicators, signals, AI, execution, backtest) |
| `tickers` | 173 IHSG tickers by sector |

### 6.2 [`.env`](.env) — Secrets

| Key | Purpose |
|-----|---------|
| `DISCORD_WEBHOOK` | Discord webhook URL for rich embeds |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | Telegram chat/group ID (negative = group) |
| `API_KEY_YFINANCE` | Yahoo Finance API (optional) |
| `EMAIL_ALERT_TO/FROM` | Email alert settings |
| `REDIS_HOST/PORT` | Redis cache (optional) |

---

## 7. Runtime Commands

### Daily Operations

```bash
# Swing — run once daily at 08:55 WIB
python screener.py

# Scalping — run during market hours (Mon-Fri 09:00-15:45)
python -m scalp.run all

# AI training — 2× per week (Wed + Sat)
python latih_ai.py

# Dashboard — open when you want to view
streamlit run dashboard/app.py

# Telegram bot — keep running 24/7
python telegram_bot.py

# Auto alerts — keep running 24/7
python auto_alert.py
```

### Quick Reference

| What | Command | When |
|------|---------|------|
| Swing screener | `python screener.py` | Daily 08:55 |
| Train AI | `python latih_ai.py` | Wed + Sat |
| Scalp producer | `python -m scalp.run producer` | Market hours |
| Scalp executor | `python -m scalp.run executor` | Market hours |
| Scalp both | `python -m scalp.run all` | Market hours |
| Intraday backtest | `python scalp/backtest.py --detail` | Any time |
| Dashboard | `streamlit run dashboard/app.py` | Any time |
| Telegram bot | `python telegram_bot.py` | 24/7 |
| Auto alerts | `python auto_alert.py` | 24/7 |

---

## 8. Weekly Schedule

```
SENIN   08:55  python screener.py
SELASA  08:55  python screener.py
RABU    08:55  python screener.py
        20:00  python latih_ai.py
KAMIS   08:55  python screener.py
JUMAT   08:55  python screener.py
SABTU   10:00  python latih_ai.py
MINGGU  —      (istirahat / review)
```

**24/7 (jalan terus):**
- `python telegram_bot.py`
- `python auto_alert.py`

**Market hours (Mon-Fri 09:00-15:45):**
- `python -m scalp.run all`

---

> **Disclaimer:** All strategies, code, and outputs are for educational and research purposes only. Past backtest performance does not guarantee future live results. Trade only capital you can afford to lose.
