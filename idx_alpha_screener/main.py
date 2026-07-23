"""
main.py — IDX Alpha Screener v2
================================
Entry point: scan IHSG, skor, rekomendasi, simpan CSV.

Usage:
  python main.py
  python main.py --top 15
  python main.py --ticker BBCA.JK BBRI.JK
"""

import sys
import os
import argparse
import time
import csv
import logging
from datetime import datetime
from typing import List, Optional

import pandas as pd
import numpy as np
import requests
import yfinance as yf

import data
import scoring as sc
import risk as rm
import regime as rg
import swing_filters as sf
import yaml
import signal_manager as sm
import portfolio as pf  # Portfolio heat management
import slippage as slip # Realistic slippage model
import perf_tracker as pt # Performance tracker

# ── v4 Engine (toggleable) ──
import v4 as v4_engine
import v4.conviction as v4_conviction
import v4.confluence as v4_confluence

# ── v5 Engine (toggleable) ──
import v5 as v5_engine
import v5.engine as v5_engine_run
import v5.momentum_score as v5_ms
import v5.dynamic_threshold as v5_dt

# ── v6 Engine (toggleable) ──
import v6 as v6_engine

# ── Telegram via existing Screener infrastructure (utils/telegram_sender) ──
_SCREENER_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCREENER_ROOT not in sys.path:
    sys.path.insert(0, _SCREENER_ROOT)

HAS_TELEGRAM = False
try:
    from utils.telegram_sender import send_telegram_sync
    HAS_TELEGRAM = True
except (ImportError, ModuleNotFoundError, Exception):
    HAS_TELEGRAM = False

# ── Setup Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("main")

# ── Configuration ──────────────────────────────────────────────────────
CONFIG = {}  # Diisi oleh load_config()

def load_config(path: str = None) -> dict:
    """Load config.yaml. Fallback ke default dict jika gagal."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
    default = {
        "cooldown": {"enabled": True, "days": 5, "db_path": "data/signal_cooldown.json"},
        "sector": {"enabled": True, "max_per_sector": 2},
        "telegram": {"top_buy_count": 10, "top_overall_count": 5},
        "scoring": {
            "adx_filter": {"no_trend": 12, "weak_trend": 16},
            "entry_zone": {"floor_ideal_pct": 0.85, "floor_good_pct": 0.90, "ideal_min_pct": 0.88},
        },
    }
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        # Merge with default — isi yang ada di config.yaml menang
        for section, default_vals in default.items():
            if section not in cfg:
                cfg[section] = default_vals
            elif isinstance(default_vals, dict):
                for k, v in default_vals.items():
                    if k not in cfg[section]:
                        cfg[section][k] = v
        logger.info("Config loaded dari %s", path)
        return cfg
    except Exception as e:
        logger.warning("Gagal load config (%s), pakai default", e)
        return default


# ── Ticker Sources ─────────────────────────────────────────────────────
SEMUA_TICKER = data.TICKERS_IHSG_LIQUID


# ── Analisis Saham Tunggal ─────────────────────────────────────────────
def analisis_satu_saham(ticker: str, df: Optional[pd.DataFrame] = None,
                        no_ihsg: bool = False,
                        df_ihsg: Optional[pd.DataFrame] = None) -> Optional[dict]:
    """
    Fetch + compute + score untuk satu saham.
    Return dict hasil atau None jika gagal.

    Jika df diberikan (dari parallel fetch), skip panggilan fetch_prices.
    Jika no_ihsg=True, skip IHSG alignment (lebih cepat untuk daily scan).
    Jika df_ihsg diberikan, pakai untuk alignment (hindari download ulang).
    """
    try:
        # 1. Fetch data (atau pakai yang sudah diberikan)
        if df is None:
            df = data.fetch_with_cache(ticker, period="1y")
        if df.empty or len(df) < 60:
            logger.debug("Data tidak cukup untuk %s (%d baris)", ticker, len(df))
            return None

        # 2. Filter fundamental & likuiditas
        if not data.filter_stocks(df):
            logger.debug("Skip %s — gagal filter fundamental/likuiditas", ticker)
            return None

        # 3. Compute indicators
        df = data.compute_all_indicators(df)

        # 3. Align with IHSG (skip jika --no-ihsg)
        if not no_ihsg:
            df = data.align_to_market(df, df_ihsg=df_ihsg)

        # 4. Ambil baris terakhir (valid)
        row = df.iloc[-1].copy()
        if pd.isna(row.get("rsi")):
            logger.debug("Indikator NaN untuk %s — skip", ticker)
            return None

        # 5. Deteksi regime
        regime, trend_score, adx_now = rg.detect_market_regime(df)

        # 6. Scoring — v3, v4, v5, atau v6
        if v6_engine.is_enabled():
            # ── v6 Engine: V4 Scoring + Konglomerat Universe ──
            # V6 pake engine V4 (terbukti paling bagus) + threshold
            # khusus large caps + konfigurasi konglomerat.
            total_score = sc.compute_total_score(row, regime)
            signal = sc.classify(total_score, regime)
            swing_result = sf.swing_gate_pass(df)
            if not swing_result['passed']:
                logger.debug("Swing gate FAILED untuk %s — %s", ticker, ', '.join(swing_result['reasons']))
            _orig_signal = signal
            if signal in ("STRONG_BUY", "BUY", "WEAK_BUY") and not swing_result['passed']:
                if swing_result.get('trend_aligned') or swing_result.get('volume_breakout'):
                    if signal == "STRONG_BUY": signal = "BUY"
                    else: signal = "HOLD"
                else: signal = "HOLD"
            # ADX filter (mild — hanya untuk no trend)
            if signal in ("STRONG_BUY", "BUY", "WEAK_BUY") and not pd.isna(adx_now):
                if adx_now < 12:
                    _old = signal; signal = "HOLD"
            v4_meta = {"conviction": total_score, "confluence": 0, "conviction_raw": total_score,
                       "confluence_bonus": 0, "positive_factors": 0, "factor_breakdown": "v6",
                       "confluence_detail": "v6"}
            
        elif v5_engine.is_enabled():
            # ── v5 Engine: 3 Profile Adaptive Scoring ──
            # Reset momentum tracker per ticker
            ticker_clean = ticker.replace('.JK', '')
            v5_result = v5_engine_run.process_stock(ticker_clean, row, regime, df)
            
            total_score = v5_result["score"]
            signal = v5_result["signal"]
            v4_meta = {
                "conviction": total_score,
                "confluence": 0,
                "conviction_raw": v5_result["base_score"],
                "confluence_bonus": v5_result.get("conf_bonus", 0),
                "positive_factors": 0,
                "factor_breakdown": f"Profil: {v5_result['profile']} | Delta: {v5_result.get('momentum_delta', 'N/A')}",
                "confluence_detail": v5_result.get("profile", "MOMENTUM"),
            }
            swing_result = {"trend_aligned": v5_result["signal"] in ("STRONG_BUY", "BUY"),
                           "volume_breakout": v5_result["profile"] == "MOMENTUM"}
            
            logger.debug("%s: v5 %s profile score=%.1f signal=%s",
                        ticker_clean, v5_result["profile"], total_score, signal)
            
        elif v4_engine.is_enabled():
            # ── v4 Engine: Confluence Gate + Dynamic Conviction ──
            # Confluence score (multi-source confirmation)
            conf_result = v4_confluence.score_confluence(row)
            # Conviction score (8 faktor dengan soft penalties)
            v4_cfg = v4_engine.config
            conv_result = v4_conviction.compute_conviction(
                row, regime, v4_cfg
            )
            # Confluence bonus
            conf_bonus = v4_confluence.get_confluence_bonus(
                conf_result["confluence"]
            )
            conf_mult = v4_cfg.get("confluence_bonus_multiplier", 0.5)
            conviction = conv_result["conviction"] + conf_bonus * conf_mult
            conviction = round(max(0, min(100, conviction)), 1)
            
            # Signal dari FINAL conviction (setelah confluence bonus)
            v4_cfg_th = v4_cfg.get("thresholds", {})
            if v4_cfg_th and isinstance(v4_cfg_th, dict):
                th = v4_cfg_th.get(regime, v4_conviction.THRESHOLDS.get(regime, [62,55,48,40,35]))
            else:
                th = v4_conviction.THRESHOLDS.get(regime, [62,55,48,40,35])
            sb, b, wb, h, _ = th
            if conviction >= sb:     signal = "STRONG_BUY"
            elif conviction >= b:    signal = "BUY"
            elif conviction >= wb:   signal = "WEAK_BUY"
            elif conviction >= h:    signal = "HOLD"
            else:                    signal = "SELL"
            total_score = conviction
            
            # Simpan metadata v4 untuk output
            v4_meta = {
                "conviction": conviction,
                "confluence": conf_result["confluence"],
                "conviction_raw": conv_result["conviction"],
                "confluence_bonus": round(conf_bonus * conf_mult, 1),
                "positive_factors": conv_result["positive_factors"],
                "factor_breakdown": conv_result["breakdown"],
                "confluence_detail": conf_result["detail"],
            }
            swing_result = {"trend_aligned": bool(conf_result["confluence"] >= 60),
                           "volume_breakout": bool(conv_result["factors"].get("volume", 0) >= 60)}
            logger.debug(
                "%s: v4 conviction=%.1f conf=%.1f signal=%s (pos_factors=%d/8)",
                ticker.replace('.JK',''), conviction, conf_result["confluence"],
                signal, conv_result["positive_factors"]
            )
        else:
            # ── v3 Scoring Engine ──
            total_score = sc.compute_total_score(row, regime)

            # 6a. Swing filter gate (mandatory untuk sinyal BUY)
            swing_result = sf.swing_gate_pass(df)

            # Log hasil swing gate
            if not swing_result['passed']:
                logger.debug("Swing gate FAILED untuk %s — %s", ticker, ', '.join(swing_result['reasons']))

            signal = sc.classify(total_score, regime)

            # 6b. Jika swing gate gagal, strict downgrade (lebih ketat)
            _orig_signal = signal  # simpan untuk log
            if signal in ("STRONG_BUY", "BUY", "WEAK_BUY") and not swing_result['passed']:
                if swing_result.get('trend_aligned') or swing_result.get('volume_breakout'):
                    # Partial pass: hanya STRONG_BUY yg survive jadi BUY
                    if signal == "STRONG_BUY":
                        signal = "BUY"
                    else:
                        # BUY/WEAK_BUY dengan swing partial → HOLD
                        signal = "HOLD"
                else:
                    # Gagal total → HOLD
                    signal = "HOLD"
                if signal != _orig_signal:
                    logger.info("%s: score %.1f (%s) → %s (swing gate %s)",
                               ticker.replace('.JK',''), total_score, _orig_signal, signal,
                               "partial pass" if swing_result.get('trend_aligned') or swing_result.get('volume_breakout') else "gagal total")

            # ── ADX Filter (mandatory trend strength untuk BUY) ──
            adx_cfg = CONFIG.get("scoring", {}).get("adx_filter", {})
            adx_no_trend = adx_cfg.get("no_trend", 12)
            adx_weak = adx_cfg.get("weak_trend", 16)
            if signal in ("STRONG_BUY", "BUY", "WEAK_BUY") and not pd.isna(adx_now):
                if adx_now < adx_no_trend:
                    # Sangat lemah — semua BUY → HOLD
                    _old = signal
                    signal = "HOLD"
                    logger.info("%s: ADX %.1f < %d, %s → HOLD (no trend)", ticker.replace('.JK',''), adx_now, adx_no_trend, _old)
                elif adx_now < adx_weak:
                    # Lemah — hanya STRONG_BUY yang survive (jadi BUY)
                    if signal in ("BUY", "WEAK_BUY"):
                        _old = signal
                        signal = "HOLD"
                        logger.info("%s: ADX %.1f < %d, %s → HOLD (trend lemah)", ticker.replace('.JK',''), adx_now, adx_weak, _old)
            
            # ── Weekly Trend Filter (higher timeframe) ──
            # Jangan BUY lawan arah mingguan — prinsip higher timeframe rules
            wt = row.get("weekly_trend", "NO_DATA")
            if signal in ("STRONG_BUY", "BUY", "WEAK_BUY") and wt == "BEARISH":
                _old = signal
                signal = "HOLD"
                logger.info("%s: weekly trend BEARISH, %s → HOLD", ticker.replace('.JK',''), _old)
            
            v4_meta = None  # tidak ada metadata v4

        rr = sc.compute_risk_reward(row)

        # ── Earnings Blackout Check ─────────────────────────────────────
        # Kalau earnings dalam 7 hari → HOLD (jangan entry)
        if signal in ("STRONG_BUY", "BUY", "WEAK_BUY"):
            blackout_days = CONFIG.get("exit_strategy", {}).get("earnings_blackout_days", 7)
            if sc.is_earnings_blackout(ticker, blackout_days=blackout_days):
                _old = signal
                signal = "HOLD"
                logger.info("%s: earnings blackout %d hari, %s → HOLD",
                           ticker.replace('.JK',''), blackout_days, _old)

        # 7. Harga dan volume
        price = row.get("close", 0)
        atr = row.get("atr", 0)
        if pd.isna(atr):
            atr = price * 0.02

        volume = int(row.get("volume", 0))

        # ── Fundamental Data via yfinance ──
        try:
            fund = data.fetch_fundamental(ticker)
            if fund:
                mcap = fund.get("market_cap")
                result_fund = {
                    "market_cap": mcap,
                    "pe_ratio": fund.get("pe_ratio"),
                    "forward_pe": fund.get("forward_pe"),
                    "pbv": fund.get("pbv"),
                    "dividend_yield": fund.get("dividend_yield"),
                    "beta": fund.get("beta"),
                    "sector": fund.get("sector"),
                    "industry": fund.get("industry"),
                    "analyst_rating": fund.get("analyst_rating"),
                    "target_price": fund.get("target_price"),
                    "roe": fund.get("roe"),
                    "profit_margin": fund.get("profit_margin"),
                    "revenue_growth": fund.get("revenue_growth"),
                    "eps_ttm": fund.get("eps_ttm"),
                }
                # Format market cap ke triliun/bilion
                if mcap and mcap > 0:
                    result_fund["market_cap_t"] = round(mcap / 1e12, 2)
                else:
                    result_fund["market_cap_t"] = None
            else:
                result_fund = {}
        except Exception:
            result_fund = {}

        # ── Entry Zone (harga rekomendasi beli) ──
        entry_zone = {}
        ez_cfg = CONFIG.get("scoring", {}).get("entry_zone", {})
        floor_ideal = ez_cfg.get("floor_ideal_pct", 0.85)
        floor_good = ez_cfg.get("floor_good_pct", 0.90)
        ideal_min_pct = ez_cfg.get("ideal_min_pct", 0.88)
        try:
            sr = data.compute_support_resistance(df["close"], lookback=60)
            support = sr.get("nearest_support", 0)
            resistance = sr.get("nearest_resistance", 0)

            vwap_val = row.get("vwap", 0)
            ema12_val = row.get("ema12", 0)
            bb_lower_val = row.get("bb_lower", 0)
            atr_val = row.get("atr", 0)

            # Entry Ideal: harga serendah mungkin yang realistis
            candidates = []
            if vwap_val > 0 and price > vwap_val:
                candidates.append(vwap_val)  # mean reversion ke VWAP
            if support > 0 < support < price:
                candidates.append(support)    # support level
            if bb_lower_val > 0 < bb_lower_val < price:
                candidates.append(bb_lower_val)  # lower band

            if candidates:
                entry_ideal = max(min(candidates), price * ideal_min_pct)
            else:
                entry_ideal = price - atr_val * 0.5

            # Entry Good: pullback level wajar (EMA12 atau VWAP)
            if ema12_val > 0 < ema12_val < price and ema12_val >= entry_ideal:
                entry_good = ema12_val
            elif vwap_val > 0 < vwap_val < price:
                entry_good = vwap_val
            elif support > 0 < support < price:
                entry_good = support * 1.02
            else:
                entry_good = price - atr_val * 0.3

            entry_zone = {
                "entry_ideal": max(int(entry_ideal), int(price * floor_ideal)),
                "entry_good": min(max(int(entry_good), int(price * floor_good)), int(price)),
                "entry_max": int(price),
                "support": int(support) if support > 0 else 0,
                "resistance": int(resistance) if resistance > 0 else 0,
            }
        except Exception:
            entry_zone = {
                "entry_ideal": int(price * 0.95),
                "entry_good": int(price * 0.97),
                "entry_max": int(price),
                "support": 0,
                "resistance": 0,
            }

        return {
            "ticker": ticker.replace(".JK", ""),
            "price": int(price),
            "score": total_score,
            "signal": signal,
            "swing_trend": swing_result.get('trend_aligned', False) if not v4_engine.is_enabled() else False,
            "swing_volume": swing_result.get('volume_breakout', False) if not v4_engine.is_enabled() else False,
            "regime": regime,
            "rsi": round(row.get("rsi", 0), 1),
            "macd": row.get("macd", 0),
            "adx": round(adx_now, 1),
            "vol_ratio": round(row.get("vol_ratio", 1.0), 2),
            "ret_20d": round(row.get("ret_20d", 0) * 100, 1),
            "stop_loss": int(rr["stop_loss"]),
            "take_profit": int(rr["take_profit"]),
            "rrr": rr["rrr"],
            "volume": volume,
            "atr": round(atr, 1),
            # Teknikal Level 2
            "vwap": round(row.get("vwap", 0), 0),
            "pct_vs_vwap": round(row.get("pct_vs_vwap", 0), 1),
            "dc_position": round(row.get("dc_position", 50), 0),
            "dc_breakout": int(row.get("dc_breakout", 0)),
            "obv_trend": int(row.get("obv_trend", 0)),
            "stoch_k": round(row.get("stoch_k", 50), 1),
            "stoch_d": round(row.get("stoch_d", 50), 1),
            # Entry Zone
            "entry_ideal": entry_zone.get("entry_ideal", int(price * 0.95)),
            "entry_good": entry_zone.get("entry_good", int(price * 0.97)),
            "entry_max": entry_zone.get("entry_max", int(price)),
            "support": entry_zone.get("support", 0),
            "resistance": entry_zone.get("resistance", 0),
            # v4 metadata
            "v4_conviction": v4_meta.get("conviction") if v4_meta else None,
            "v4_confluence": v4_meta.get("confluence") if v4_meta else None,
            "v4_positive_factors": v4_meta.get("positive_factors") if v4_meta else None,
            # v5 metadata
            "v5_score": v5_result.get("score") if v5_engine.is_enabled() and locals().get("v5_result") else None,
            "v5_signal": v5_result.get("signal") if v5_engine.is_enabled() and locals().get("v5_result") else None,
            "v5_profile": v5_result.get("profile") if v5_engine.is_enabled() and locals().get("v5_result") else None,
            "v5_momentum_delta": v5_result.get("momentum_delta") if v5_engine.is_enabled() and locals().get("v5_result") else None,
            # Fundamental
            **result_fund,
        }

        # Add slippage info
        slip_result = slip.get_slippage_pct(
            ticker=ticker, price=price,
            volume=volume, mcap=result_fund.get("market_cap", None)
        )
        result["slippage_tier"] = slip_result["tier"]
        result["slippage_label"] = slip_result["label"]
        result["total_cost_pct"] = slip_result["total_roundtrip_pct"]
        result["spread_pct"] = slip_result["spread_pct"]

    except requests.exceptions.ConnectionError:
        logger.error("Koneksi gagal untuk %s — retry 1x", ticker)
        try:
            time.sleep(2)
            df = data.fetch_with_cache(ticker, period="1y")
            if df.empty or len(df) < 60:
                return None
            # Jika df diberikan dari parallel, retry penuh dari awal — jatuh ke fallback di bawah
            return None
        except Exception:
            logger.error("Retry gagal untuk %s", ticker)
            return None

    except ValueError as e:
        logger.error("Data error %s: %s", ticker, e)
        return None

    except Exception as e:
        logger.error("Gagal %s: %s", ticker, e)
        return None


# ── Rekomendasi ────────────────────────────────────────────────────────
def rekomendasi(signal: str) -> str:
    """Map signal ke aksi."""
    mapping = {
        "STRONG_BUY": "🟢 STRONG BUY",
        "BUY": "🔵 BUY",
        "WEAK_BUY": "🟡 WEAK BUY",
        "HOLD": "⚪ HOLD",
        "SELL": "🔴 SELL",
    }
    return mapping.get(signal, signal)


# ── Print Detail ───────────────────────────────────────────────────────
def print_hasil(hasil: list, top_n: int = 10):
    """Cetak rekomendasi ke terminal."""
    if not hasil:
        print("\n❌ Tidak ada hasil screening.")
        return

    buy = [h for h in hasil if h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY")]
    hold = [h for h in hasil if h["signal"] == "HOLD"]
    sell = [h for h in hasil if h["signal"] == "SELL"]

    now = datetime.now().strftime("%d %B %Y %H:%M")
    print(f"\n{'='*75}")
    print(f"  IDX ALPHA SCREENER v2  —  {now}")
    print(f"  Saham discan: {len(hasil)} | Lolos filter: {len(buy)} BUY, {len(hold)} HOLD, {len(sell)} SELL")
    print(f"{'='*75}")

    # ── BUY signals ──
    if buy:
        print(f"\n  {'🟢 REKOMENDASI BUY':^83}")
        print(f"  {'─'*83}")
        header = f"  {'Ticker':<7} {'Harga':>7} {'Skor':>6} {'Sinyal':<12} {'Swing':<6} {'Port':<5} {'Biaya':>7} {'Regime':<12} {'RSI':>5} {'ADX':>5} {'RRR':>5} {'SL':>8} {'TP':>8}"
        print(header)
        print(f"  {'─'*83}")
        for h in sorted(buy, key=lambda x: x["score"], reverse=True)[:top_n]:
            signal_icon = rekomendasi(h["signal"])
            regime = h["regime"]
            swing_icon = "🟢" if h.get('swing_trend') and h.get('swing_volume') else "⚫"
            portfolio_ok = h.get("portfolio_ok", True)
            portfolio_icon = "📋" if portfolio_ok else "🚫"
            print(f"  {h['ticker']:<7} {h['price']:>7,} {h['score']:>5.1f} {signal_icon:<12} {swing_icon:<6} {portfolio_icon:<5} {h.get('total_cost_pct', 0):>6.2f}% {regime:<12} {h['rsi']:>5} {h['adx']:>5} {h['rrr']:>4.1f} {h['stop_loss']:>8,} {h['take_profit']:>8,}")
        # Portfolio heat info
        portfolio_blocked = [h for h in buy if not h.get("portfolio_ok", True)]
        if portfolio_blocked:
            print(f"\n  {'🚫 Tidak masuk portofolio (batas sektor/posisi):':<73}")
            for h in portfolio_blocked:
                print(f"    {h['ticker']:<6} — {h.get('portfolio_reason', '')}")
        print()

    # ── SELL signals ──
    if sell:
        print(f"\n  {'🔴 REKOMENDASI SELL / HINDARI':^73}")
        print(f"  {'─'*73}")
        sell_top = sorted(sell, key=lambda x: x["score"])[:5]
        for h in sell_top:
            print(f"  {h['ticker']:<7} {h['price']:>7,} {h['score']:>5.1f} Skor rendah — risiko tinggi")
        print()

    print(f"{'='*75}\n")


# ── Simpan CSV ─────────────────────────────────────────────────────────
def simpan_csv(hasil: list, path: str = "screener_v2_result.csv"):
    """Simpan hasil ke CSV."""
    if not hasil:
        return
    fieldnames = [
        "ticker", "price", "score", "signal", "swing_trend", "swing_volume",
        "regime", "rsi", "adx", "macd", "vol_ratio", "ret_20d",
        "stop_loss", "take_profit", "rrr", "volume", "atr",
        # Teknikal Level 2
        "vwap", "pct_vs_vwap", "dc_position", "dc_breakout",
        "obv_trend", "stoch_k", "stoch_d",
        # Entry Zone
        "entry_ideal", "entry_good", "entry_max", "support", "resistance",
        # v4 Engine
        "v4_conviction", "v4_confluence", "v4_positive_factors",
        # v5 Engine
        "v5_score", "v5_signal", "v5_profile", "v5_momentum_delta",
        # Portfolio
        "portfolio_ok", "portfolio_reason",
        # Slippage
        "slippage_tier", "slippage_label", "total_cost_pct", "spread_pct",
        # Fundamental
        "market_cap", "market_cap_t", "pe_ratio", "forward_pe", "pbv",
        "dividend_yield", "beta", "sector", "industry",
        "analyst_rating", "target_price", "roe",
        "profit_margin", "revenue_growth", "eps_ttm",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        hasil_sorted = sorted(hasil, key=lambda x: x["score"], reverse=True)
        # Bersihkan field internal sebelum simpan CSV
        clean = []
        for h in hasil_sorted:
            h.pop("_cooldown_hit", None)
            h.pop("_sector_capped", None)
            clean.append(h)
        writer.writerows(clean)
    logger.info("Hasil disimpan ke %s", os.path.abspath(path))


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="IDX Alpha Screener v2")
    parser.add_argument("--top", type=int, default=10,
                       help="Jumlah rekomendasi BUY yang ditampilkan")
    parser.add_argument("--ticker", type=str, nargs="+",
                       help="Saham spesifik (contoh: BBCA.JK BBRI.JK)")
    parser.add_argument("--output", type=str, default="screener_v2_result.csv",
                       help="File output CSV")
    parser.add_argument("--quiet", action="store_true",
                       help="Minimalkan output terminal")
    parser.add_argument("--parallel", action="store_true", default=False,
                       help="Gunakan fetch paralel (ThreadPoolExecutor) untuk mempercepat")
    parser.add_argument("--telegram", action="store_true", default=False,
                       help="Kirim rekomendasi BUY/STRONG_BUY ke Telegram")
    parser.add_argument("--no-ihsg", action="store_true", default=False,
                       help="Skip IHSG alignment (lebih cepat, untuk daily scan)")
    parser.add_argument("--force", action="store_true", default=False,
                       help="Skip IHSG market filter (paksa scan walaupun IHSG bearish)")
    parser.add_argument("--v4", action="store_true", default=False,
                       help="Gunakan v4 engine (Confluence Gate + Dynamic Conviction)")
    parser.add_argument("--v5", action="store_true", default=False,
                       help="Gunakan v5 engine (3 Profile Adaptive Scoring)")
    parser.add_argument("--v6", action="store_true", default=False,
                       help="Gunakan v6 engine (Corrected Weight Scoring)")
    args = parser.parse_args()

    # ── Load config ────────────────────────────────────────────────────
    global CONFIG
    CONFIG.clear()
    CONFIG.update(load_config())

    # ── Configure scoring module ────────────────────────────────────────
    sc_cfg = CONFIG.get("scoring", {})
    sc.configure(sc_cfg)

    # ── Configure v4 Engine ─────────────────────────────────────────────
    v4_cfg_yaml = CONFIG.get("v4", {})
    v4_engine.enabled = args.v4 or v4_cfg_yaml.get("enabled", False)
    v4_engine.ab_test_mode = v4_cfg_yaml.get("ab_test_mode", "v4_only")
    if v4_cfg_yaml:
        v4_engine.configure(v4_cfg_yaml)
    if v4_engine.is_enabled():
        logger.info("🧠 v4 Engine AKTIF (mode=%s) — Confluence Gate + Dynamic Conviction",
                   v4_engine.ab_test_mode)

    # ── Configure v5 Engine ─────────────────────────────────────────────
    v5_cfg_yaml = CONFIG.get("v5", {})
    v5_engine.enabled = args.v5 or v5_cfg_yaml.get("enabled", False)
    if v5_cfg_yaml:
        v5_engine.configure(v5_cfg_yaml)
    if v5_engine.is_enabled():
        logger.info("🚀 v5 Engine AKTIF — 3 Profile Adaptive Scoring (Momentum/Reversal/Value)")
        logger.info("   Dynamic percentile: %s, Momentum lookback: %d hari",
                   v5_engine.config.get("dynamic_percentile", True),
                   v5_engine.config.get("score_momentum_days", 5))

    # ── Configure v6 Engine ─────────────────────────────────────────────
    v6_cfg_yaml = CONFIG.get("v6", {})
    v6_engine.enabled = args.v6 or v6_cfg_yaml.get("enabled", False)
    if v6_cfg_yaml:
        v6_engine.configure(v6_cfg_yaml)
    if v6_engine.is_enabled():
        logger.info("🔬 v6 Engine AKTIF — Corrected Weight Scoring (ML-based factor inversion)")

    # ── Init Cooldown Tracker ───────────────────────────────────────────
    cd_cfg = CONFIG.get("cooldown", {})
    cooldown_tracker = None
    if cd_cfg.get("enabled", True):
        db_path = cd_cfg.get("db_path", "data/signal_cooldown.json")
        if not os.path.isabs(db_path):
            data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            db_path = os.path.join(data_dir, os.path.basename(db_path))
        cooldown_days = cd_cfg.get("days", 5)
        cooldown_tracker = sm.CooldownTracker(db_path, cooldown_days)
        cooldown_tracker.clean_old()
        logger.info("Cooldown aktif: %d hari (db: %s)", cooldown_days, db_path)

    # ── Sector config ───────────────────────────────────────────────────
    sector_cfg = CONFIG.get("sector", {})
    max_per_sector = sector_cfg.get("max_per_sector", 2) if sector_cfg.get("enabled", True) else 999

    # ── Portfolio Heat Management ───────────────────────────────────────
    portfolio_cfg = CONFIG.get("portfolio", {})
    portfolio_mgr = pf.PortfolioManager(portfolio_cfg)
    logger.info("Portfolio heat: posisi=%d, per sektor=%d, exposure max=%.0f%%",
               portfolio_mgr.max_positions, portfolio_mgr.max_per_sector,
               portfolio_mgr.max_sector_exposure_pct)

    # ── Slippage Model ────────────────────────────────────────────────
    slip.SLIPPAGE_ENABLED = CONFIG.get("slippage", {}).get("enabled", True)
    logger.info("Slippage model: %s", "AKTIF" if slip.SLIPPAGE_ENABLED else "NONAKTIF")

    # ── Performance Tracker ───────────────────────────────────────────
    perf_tracker = None
    perf_cfg = CONFIG.get("perf_tracker", {"enabled": False})
    if perf_cfg.get("enabled", True):
        csv_path = perf_cfg.get("csv_path", "data/perf_tracker.csv")
        if not os.path.isabs(csv_path):
            csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), csv_path)
        perf_tracker = pt.PerformanceTracker(csv_path=csv_path)
        logger.info("Performance tracker: AKTIF")

    # ── Exit Strategy Config ────────────────────────────────────────────
    exit_cfg = CONFIG.get("exit_strategy", {})
    earnings_blackout_days = exit_cfg.get("earnings_blackout_days", 7)

    # ── Load Earnings Calendar (optional) ──────────────────────────────
    sc.load_earnings_calendar()

    # Pilih ticker
    if args.ticker:
        tickers = [t.upper() if t.endswith(".JK") else f"{t.upper()}.JK"
                  for t in args.ticker]
    else:
        tickers = SEMUA_TICKER
        if args.top < len(tickers):
            tickers = tickers[:args.top]
            logger.info("Mode --top %d: scan terbatas %d saham paling liquid", args.top, len(tickers))

    logger.info("Memindai %d saham...", len(tickers))

    # Pre-fetch IHSG sekali (kecuali --no-ihsg) — hindari download per ticker
    df_ihsg = None
    if not args.no_ihsg:
        logger.info("Pre-fetch IHSG...")
        df_ihsg = data.fetch_ihsg_cached()
        if df_ihsg.empty:
            logger.warning("IHSG data kosong — alignment akan skip otomatis")

    # ── IHSG Market Context Filter (P0) ──
    # Pencegahan: jangan scan jika IHSG sedang bearish parah.
    # IHSG turun > 4% dalam 20 hari = sinyal bear market → skip.
    # Bisa di-override dengan --force.
    if not args.force and df_ihsg is not None and not df_ihsg.empty and len(df_ihsg) > 20:
        try:
            ihsg_close = df_ihsg["close"]
            ihsg_ret_20d = (ihsg_close.iloc[-1] / ihsg_close.iloc[-21] - 1) * 100
            ihsg_ret_5d = (ihsg_close.iloc[-1] / ihsg_close.iloc[-6] - 1) * 100
            
            # Deteksi EMA crossover IHSG
            ihsg_ema12 = ihsg_close.ewm(span=12).mean().iloc[-1]
            ihsg_ema50 = ihsg_close.ewm(span=50).mean().iloc[-1]
            ihsg_bearish_cross = ihsg_ema12 < ihsg_ema50
            
            logger.info("IHSG Market Context: 5d=%+.1f%% 20d=%+.1f%% EMA12/50=%s",
                       ihsg_ret_5d, ihsg_ret_20d,
                       "BULLISH" if ihsg_ema12 >= ihsg_ema50 else "BEARISH")
            
            if ihsg_ret_20d < -4 and ihsg_bearish_cross:
                msg = (
                    "⚠️ IHSG BEARISH — skip screening otomatis.\n"
                    f"  IHSG turun {ihsg_ret_20d:.1f}% dalam 20 hari, "
                    f"EMA12 < EMA50 (bearish cross)\n"
                    f"  Sinyal BUY di bear market punya failure rate tinggi.\n"
                    f"  Gunakan --force untuk tetap scan."
                )
                logger.warning(msg)
                print(f"\n{msg}\n")
                print_hasil([], args.top)
                return
            elif ihsg_ret_5d < -3:
                logger.warning(
                    "IHSG turun %.1f%% dalam 5 hari — waspada, "
                    "tapi tetap lanjut scan.",
                    ihsg_ret_5d
                )
        except Exception as e:
            logger.warning("IHSG market context check gagal: %s — tetap lanjut", e)

    hasil = []
    errors = 0
    start = time.time()

    if args.parallel:
        # ── Parallel fetch ─────────────────────────────────────────────
        logger.info("Mode paralel: fetch data dengan ThreadPoolExecutor...")
        price_data = data.scan_multiple(tickers, max_workers=5, delay_between=0.3)

        # Proses satu-satu hasil fetch paralel
        for i, tkr in enumerate(tickers, 1):
            df = price_data.get(tkr, pd.DataFrame())
            res = analisis_satu_saham(tkr, df=df, no_ihsg=args.no_ihsg, df_ihsg=df_ihsg)
            if res:
                hasil.append(res)
                # Record to perf tracker
                if perf_tracker and res.get("signal") in ("STRONG_BUY", "BUY", "WEAK_BUY"):
                    perf_tracker.record_signal(
                        ticker=res["ticker"], signal=res["signal"],
                        score=res["score"], regime=res["regime"],
                        slippage_tier=res.get("slippage_tier", "MID"),
                        total_cost_pct=res.get("total_cost_pct", 0.75),
                        entry_price=res["price"],
                    )
                # Track berapa banyak yg lolos swing gate
                if res.get('signal') in ("STRONG_BUY", "BUY", "WEAK_BUY"):
                    pass  # auto-logged by classifier
            else:
                errors += 1

            if not args.quiet and (i % 10 == 0 or i == len(tickers)):
                elapsed = time.time() - start
                logger.info("Progress: %d/%d | dapat: %d | error: %d | %.0fs",
                           i, len(tickers), len(hasil), errors, elapsed)

            # Rate limit antar proses (lebih ringan karena fetch sudah paralel)
            if i < len(tickers):
                time.sleep(0.3)
    else:
        # ── Sequential (default) ───────────────────────────────────────
        for i, tkr in enumerate(tickers, 1):
            res = analisis_satu_saham(tkr, no_ihsg=args.no_ihsg, df_ihsg=df_ihsg)
            if res:
                hasil.append(res)
                # Record to perf tracker
                if perf_tracker and res.get("signal") in ("STRONG_BUY", "BUY", "WEAK_BUY"):
                    perf_tracker.record_signal(
                        ticker=res["ticker"], signal=res["signal"],
                        score=res["score"], regime=res["regime"],
                        slippage_tier=res.get("slippage_tier", "MID"),
                        total_cost_pct=res.get("total_cost_pct", 0.75),
                        entry_price=res["price"],
                    )
            else:
                errors += 1

            if not args.quiet and (i % 10 == 0 or i == len(tickers)):
                elapsed = time.time() - start
                logger.info("Progress: %d/%d | dapat: %d | error: %d | %.0fs",
                           i, len(tickers), len(hasil), errors, elapsed)

            # Rate limit protection
            if i < len(tickers):
                time.sleep(0.5)

    elapsed = time.time() - start
    logger.info("Selesai dalam %.0f detik. %d hasil, %d error.",
               elapsed, len(hasil), errors)

    # ── Cooldown Filter ──
    if hasil and cooldown_tracker:
        for h in hasil:
            ticker = h["ticker"]
            if h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY") and cooldown_tracker.is_on_cooldown(ticker):
                old = h["signal"]
                h["signal"] = "HOLD"
                h["_cooldown_hit"] = True
                cd_info = cooldown_tracker.cooldown_info(ticker)
                if cd_info:
                    logger.info("Cooldown: %s (%s %s) → HOLD (sisa %d hari)",
                               ticker, cd_info['last_signal'], cd_info['last_date'], cd_info['remaining_days'])
                else:
                    logger.info("Cooldown: %s → HOLD", ticker)

    # ── Sector Cap ──
    if hasil and max_per_sector < 999:
        hasil = sm.apply_sector_cap(hasil, max_per_sector)
        logger.info("Sector cap diterapkan: maks %d per sektor", max_per_sector)

    # ── Portfolio Heat Filter ──
    # Simulasi: kalau semua BUY diambil, mana yang masuk batas portofolio?
    if hasil and portfolio_mgr.enabled:
        buy_signals = [h for h in hasil if h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY")]
        for h in buy_signals:
            sector = h.get("sector", "")
            price = h.get("price", 0)
            can_enter, reason = portfolio_mgr.can_enter(h["ticker"], sector, price, capital=500_000_000)
            h["portfolio_ok"] = can_enter
            h["portfolio_reason"] = reason if not can_enter else "OK"
            if can_enter:
                # Simulate entry untuk filter
                portfolio_mgr.enter_position(h["ticker"], sector, price, 100, capital=500_000_000)
        # Tandai mana yang masuk portfolio
        for h in hasil:
            if "portfolio_ok" not in h:
                h["portfolio_ok"] = True
                h["portfolio_reason"] = "N/A"
        logger.info("Portfolio heat: %d dari %d BUY bisa masuk portofolio",
                   sum(1 for h in hasil if h.get("portfolio_ok") and h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY")),
                   sum(1 for h in hasil if h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY")))

    # ── Exit Strategy Info ──
    exit_cfg = CONFIG.get("exit_strategy", {})
    if exit_cfg:
        logger.info("Exit strategy: max_hold=%d hari | flat_exit=%d hr/%d%% | hard_stop=%.0f%%",
                   exit_cfg.get("max_hold_days", 15),
                   exit_cfg.get("flat_exit_days", 7),
                   exit_cfg.get("flat_exit_threshold_pct", 2),
                   exit_cfg.get("hard_stop_pct", -15))

    # ── Record Cooldown for new BUY signals ──
    if hasil and cooldown_tracker:
        for h in hasil:
            if h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY"):
                cooldown_tracker.record(h["ticker"], h["signal"], {
                    "score": h["score"],
                    "price": h["price"],
                    "sector": h.get("sector", ""),
                })

    # Output
    if hasil:
        signal_counts = [h["signal"] for h in hasil]
        buy_now = sum(1 for s in signal_counts if s in ("STRONG_BUY", "BUY", "WEAK_BUY"))
        logger.info("Final: %d BUY, %d HOLD, %d SELL dari %d total",
                   buy_now,
                   sum(1 for s in signal_counts if s == "HOLD"),
                   sum(1 for s in signal_counts if s == "SELL"),
                   len(hasil))
        print_hasil(hasil, args.top)
        simpan_csv(hasil, args.output)
    else:
        print("\n❌ Tidak ada hasil screening. Cek koneksi atau coba lagi nanti.\n")

    # ── Telegram ────────────────────────────────────────────────────────
    if args.telegram and hasil:
        if HAS_TELEGRAM:
            buy_all = [h for h in hasil if h["signal"] in ("STRONG_BUY", "BUY", "WEAK_BUY")]
            strong = [h for h in hasil if h["signal"] == "STRONG_BUY"]
            buy_only = [h for h in hasil if h["signal"] == "BUY"]
            weak = [h for h in hasil if h["signal"] == "WEAK_BUY"]
            hold = [h for h in hasil if h["signal"] == "HOLD"]
            sell = [h for h in hasil if h["signal"] == "SELL"]

            sep = "▬" * 30
            tg_cfg = CONFIG.get("telegram", {})
            top_n_overall = tg_cfg.get("top_overall_count", 5)
            top_n_buy = tg_cfg.get("top_buy_count", 10)
            lines = [
                f"📈 *IDX Alpha Screener v2*",
                f"Saham discan: {len(hasil)} | {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                sep,
                f"🟢 STRONG BUY: {len(strong)}",
                f"🔵 BUY: {len(buy_only)}",
                f"🟡 WEAK BUY: {len(weak)}",
                f"⚪ HOLD: {len(hold)}",
                f"🔴 SELL: {len(sell)}",
                sep,
            ]

            if buy_all:
                lines.append(f"🏆 *Top BUY:*")
                for i, h in enumerate(sorted(buy_all, key=lambda x: x["score"], reverse=True)[:top_n_buy], 1):
                    lines.append(
                        f"#{i} *{h['ticker']}* — Rp {h['price']:,} | Skor {h['score']:.1f}"
                    )
                    # Fundamental snapshot
                    fund_line = ""
                    pe = h.get("pe_ratio")
                    mcap = h.get("market_cap_t")
                    div = h.get("dividend_yield")
                    if mcap: fund_line += f"MC Rp{mcap}T"
                    if pe:   fund_line += f" | P/E {pe:.1f}" if fund_line else f"P/E {pe:.1f}"
                    if div:  fund_line += f" | Div {div:.1f}%" if fund_line else f"Div {div:.1f}%"
                    if fund_line:
                        lines.append(f"   _{fund_line}_")
                    # Level 2: VWAP + Stochastic
                    l2_line = ""
                    vwap = h.get("pct_vs_vwap")
                    stoch = h.get("stoch_k")
                    if vwap is not None: l2_line += f"VWAP {vwap:+.1f}%"
                    if stoch is not None: l2_line += f" | Stoch {stoch:.0f}" if l2_line else f"Stoch {stoch:.0f}"
                    if l2_line:
                        lines.append(f"   `{l2_line}`")
                    # Entry Zone
                    e_ideal = h.get("entry_ideal", 0)
                    e_good = h.get("entry_good", 0)
                    if e_ideal and e_good:
                        lines.append(
                            f"   🎯 *Entry:* Ideal Rp{e_ideal:,} / Good Rp{e_good:,}"
                        )
                    # Trailing stop estimate — TP fixed diganti trailing
                    entry_p = h['price']
                    atr_v = h.get('atr', 0)
                    if atr_v and atr_v > 0:
                        trail_stop = int(max(entry_p * 0.95, entry_p - atr_v * 2.5))
                        gain_pct = ((entry_p - trail_stop) / entry_p) * 100
                        if trail_stop < entry_p:
                            lines.append(
                                f"   📏 *Trail:* Rp {trail_stop:,} ({gain_pct:.1f}% dr entry)"
                            )
                        else:
                            lines.append(
                                f"   📏 *Trail:* Rp {trail_stop:,}"
                            )
                    else:
                        lines.append(
                            f"   🛑 *Stop:* Rp {h['stop_loss']:,}"
                        )
                lines.append(sep)
            else:
                lines.append("📭 *Tidak ada sinyal BUY* — semua saham gagal swing gate atau skor < threshold")
                lines.append(sep)

            # Cooldown info
            if cooldown_tracker:
                cooldown_active = sum(1 for h in hasil if h.get("_cooldown_hit"))
                if cooldown_active > 0:
                    lines.append(f"⏳ {cooldown_active} saham dalam cooldown (skip)")
                    lines.append(sep)

            # Sector summary
            sector_lines = sm.get_sector_buy_summary(hasil)
            if sector_lines:
                lines.append("🏭 *Sektor:*")
                lines.append(sector_lines)
                lines.append(sep)

            # Always show Top N overall
            tg_cfg = CONFIG.get("telegram", {})
            top_n_overall = tg_cfg.get("top_overall_count", 5)
            top_n_buy = tg_cfg.get("top_buy_count", 10)
            top_all = sorted(hasil, key=lambda x: x["score"], reverse=True)[:top_n_overall]
            lines.append(f"📊 *Top {top_n_overall} Keseluruhan:*")
            for h in top_all:
                swing_ok = h.get('swing_trend', False) and h.get('swing_volume', False)
                swing_mark = "🟢" if swing_ok else "⚫"
                signal_emoji = "🟢" if h['signal'] == "STRONG_BUY" else "🔵" if h['signal'] == "BUY" else "🟡" if h['signal'] == "WEAK_BUY" else "⚪"
                sector_tag = f" [{h.get('sector', '')[:12]}]" if h.get('sector') else ""
                l2_tag = ""
                vwap = h.get("pct_vs_vwap")
                stoch = h.get("stoch_k")
                if vwap is not None: l2_tag += f" VWAP {vwap:+.1f}%"
                if stoch is not None: l2_tag += f" Stoch{stoch:.0f}"
                lines.append(
                    f"  {signal_emoji} {h['ticker']}{sector_tag} {swing_mark} — Skor {h['score']:.1f} | "
                    f"Rp {h['price']:,} | RRR {h['rrr']:.1f}{l2_tag}"
                )
            lines.append(sep)
            lines.append("🤖 Auto-generated oleh IDX Alpha Screener v2")
            msg = "\n".join(lines)

            ok = send_telegram_sync(msg)
            if ok:
                logger.info("Telegram terkirim: %d sinyal BUY, %d total saham.", len(buy_all), len(hasil))
            else:
                logger.warning("Gagal kirim ke Telegram.")
        else:
            logger.warning("Telegram tidak tersedia (utils.telegram_sender tidak ditemukan).")


if __name__ == "__main__":
    main()
