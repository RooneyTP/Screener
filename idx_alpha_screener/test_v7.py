"""
test_v7.py — Test Suite F2 untuk modul V7 (IDX Alpha Screener)
================================================================
Mencakup fitur v7 yang sebelumnya tidak punya test sama sekali:
  TEST 1  perf_tracker  : dedup sinyal (±1%/<14 hari ATAU anchor fresh <7 hari),
                          kolom fresh
  TEST 2  weekly_report : classify_ohlc (WIN_TP / LOSS_SL / OPEN / same-bar),
                          evaluate_signals dengan provider mock
  TEST 3  position_tracker : SL/TP/trailing/time-stop, tanpa posisi → aman
  TEST 4  ai_narrative  : LLM success / exception / tanpa key (TIDAK panggil API)
  TEST 5  telegram_formatter : <3500 chars, label (lanjutan), narrative
  TEST 6  position_check_intraday : tanpa positions.json → exit 0 / tidak crash
  TEST 7  cron_v3_scan  : AST inspection — subprocess.run memakai env PYTHONUTF8=1

Style: unittest (testcase class) — kompatibel dengan pytest bila diinstall.
Semua I/O memakai tempdir — TIDAK menyentuh data/ asli.
Mock point Invezgo: MockProvider class di bawah (get_historical / get_intraday /
get_broker_summary / get_financial_statement) — interface meniru InvezgoProvider.

Jalankan (dari C:\\Hermes_Workspace\\Screener):
    PYTHONUTF8=1 python -m unittest discover -s idx_alpha_screener -p "test_v7.py" -v
    # atau bila pytest tersedia:
    python -m pytest idx_alpha_screener/test_v7.py -v
"""
import asyncio
import ast
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

import pandas as pd

# ── Path setup: folder modul (idx_alpha_screener) + root repo (utils/) ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from perf_tracker import (                                  # noqa: E402
    DEDUP_MAX_AGE_DAYS,
    DEDUP_TOLERANCE,
    FIELDS,
    dedup_and_log_batch,
    find_previous_signal,
    load_signals,
    log_signal,
)
from position_tracker import PositionTracker                # noqa: E402
import ai_narrative                                         # noqa: E402
import telegram_formatter                                   # noqa: E402
import position_check_intraday as pci                       # noqa: E402
import intraday_check as ic                                 # noqa: E402
import v7 as v7_engine                                      # noqa: E402
import v7_exit                                             # noqa: E402
from v7_exit import compute_exit, position_sizing          # noqa: E402
import data_invezgo                                         # noqa: E402
import entry_timing                                         # noqa: E402
from utils import telegram_sender                            # noqa: E402

# weekly_report memanggil sys.exit(1) saat import kalau TELEGRAM_BOT_TOKEN
# tidak ada di .env — tangani supaya suite tetap bisa jalan di env tanpa .env.
try:
    import weekly_report as weekly_report_mod                # noqa: E402
except SystemExit:
    weekly_report_mod = None


# ══════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════

class MockProvider:
    """Mock InvezgoProvider — interface meniru data_invezgo.InvezgoProvider.

    get_historical  → DataFrame OHLC (kolom Open/High/Low/Close/Volume, index DatetimeIndex)
    get_intraday    → dict snapshot harga
    get_broker_summary / get_financial_statement → dict kosong (cukup untuk
    integrasi yang tidak fokus ke data broker).
    """

    def __init__(self, df=None, intraday=None):
        self._df = df if df is not None else pd.DataFrame()
        self._intraday = intraday or {}
        self.calls = []

    def get_historical(self, code, period="1y", use_cache=True):
        self.calls.append(("get_historical", code, period, use_cache))
        return self._df

    def get_intraday(self, code):
        self.calls.append(("get_intraday", code))
        return dict(self._intraday)

    def get_broker_summary(self, code, days=5):
        self.calls.append(("get_broker_summary", code, days))
        return {}

    def get_financial_statement(self, code, statement="IS", limit=4):
        self.calls.append(("get_financial_statement", code, statement, limit))
        return {}

    def get_fundamental(self, code):
        self.calls.append(("get_fundamental", code))
        return {}


def _ohlc(highs, lows, closes=None, start=None):
    """Buat DataFrame OHLC sintetis dengan index DatetimeIndex harian."""
    n = len(highs)
    if closes is None:
        closes = lows
    if start is None:
        start = datetime.now().date() - timedelta(days=n)
    idx = pd.date_range(str(start), periods=n, freq="D")
    return pd.DataFrame({
        "Open": [float(c) for c in closes],
        "High": [float(h) for h in highs],
        "Low": [float(l) for l in lows],
        "Close": [float(c) for c in closes],
        "Volume": [1000] * n,
    }, index=idx)


def _mk_signal(ticker="BBCA", mode="swing", entry_price=10000.0, **over):
    """Dict sinyal — key PERSIS yang diterima log_signal(**s)."""
    s = {
        "ticker": ticker, "mode": mode, "score": 55.4, "signal": "BUY",
        "entry_price": entry_price, "sl": 9500.0, "tp": 11000.0,
        "lots": 2, "cost": 20000000,
    }
    s.update(over)
    return s


def _mk_swing(tkr="BBCA", score=60.0, price=10000, cont=None):
    """Dict sinyal swing — format yang diterima format_message()."""
    s = {
        "tkr": tkr, "score": score, "price": price,
        "exit": {"stop_loss": 9500, "take_profit": 11000, "rrr": 2.0},
        "sizing": {"lots": 2, "cost": 20000000, "risk_amount": 1000000},
        "bf": "akumulasi", "ff": "net_buy", "weekly": "BULLISH",
        "brokers": "🔵BK(+45B) JP(+12B)|🔴MG(-10B)",
        "entry_rec": {"method": "Limit", "price_range": "9900-10100"},
        "group": "Astra",
    }
    if cont:
        s["continuation"] = cont
    return s


def _mk_intra(tkr="BBRI", score=52.0, price=5000, cont=None):
    """Dict sinyal intraday — format yang diterima format_message()."""
    s = {
        "tkr": tkr, "score": score, "price": price,
        "exit": {"stop_loss": 4800, "take_profit": 5300, "rrr": 2.5},
        "sizing": {"lots": 5, "cost": 25000000, "risk_amount": 1200000},
        "bf": "netral", "ff": "net_buy", "earn": "no_data", "vol": 2.5,
        "entry_rec": {"method": "Market", "price_range": "-"},
    }
    if cont:
        s["continuation"] = cont
    return s


# ══════════════════════════════════════════════════════════════════════════
# TEST 1 — perf_tracker: dedup sinyal (±1%, <14 hari) & kolom fresh
# ══════════════════════════════════════════════════════════════════════════

class TestPerfTrackerDedup(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.csv = os.path.join(self._tmp.name, "perf_tracker_v7.csv")

    # ── Skenario inti: batch 1 fresh, batch 2 (harga ±1%, <14 hari) fresh=0 ──
    def test_duplicate_within_tolerance_not_logged_as_fresh(self):
        # L2: pin tanggal tetap — pakai datetime.now() langsung flaky kalau
        # assertion menyeberang tengah malam (ref_date bisa beda 1 hari).
        fixed_now = datetime(2026, 8, 5, 21, 0, 0)
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = fixed_now
            mdt.strptime.side_effect = datetime.strptime
            r1 = dedup_and_log_batch(self.csv, [_mk_signal()])
            # Harga 10050 vs 10000 = +0.5% (≤ ±1%) & < 14 hari → duplikat
            r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10050.0)])
        self.assertTrue(r1[0]["fresh"], "sinyal pertama harus fresh=True")
        self.assertIsNone(r1[0]["ref_date"])
        self.assertTrue(r1[0]["logged"])
        self.assertFalse(r2[0]["fresh"], "sinyal identik ±1% harus fresh=False (lanjutan)")
        self.assertEqual(r2[0]["ref_date"], fixed_now.strftime("%d/%m"))
        self.assertTrue(r2[0]["logged"], "sinyal lanjutan TETAP di-log (fresh=0)")

        # Isi CSV: 2 baris, kolom fresh = 1 lalu 0
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["fresh"] for r in rows], ["1", "0"])

    # ── R6: beda harga > ±1% TAPI anchor fresh=1 < 7 hari → tetap lanjutan ──
    # Skenario BUMI 168→179→187: harga naik >1%/hari sehingga dedup ±1% lama
    # tidak memblokir. Aturan baru: sinyal fresh=1 (baru) berusia <7 hari = anchor
    # → sinyal berikutnya fresh=0, tidak tampil 'sinyal baru' setiap malam.
    def test_price_diff_but_fresh_anchor_7d_is_continuation(self):
        fixed_now = datetime(2026, 8, 5, 21, 0, 0)
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = fixed_now
            mdt.strptime.side_effect = datetime.strptime
            dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10000.0)])
            # Harga +10% (di luar ±1%) — dulu fresh=1, sekarang fresh=0
            r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=11000.0)])
        self.assertFalse(r2[0]["fresh"],
                         "anchor fresh=1 <7 hari → lanjutan apapun beda harga (+10%)")
        self.assertEqual(r2[0]["ref_date"], fixed_now.strftime("%d/%m"))
        self.assertTrue(r2[0]["logged"], "sinyal lanjutan TETAP di-log (fresh=0)")
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["fresh"] for r in rows], ["1", "0"])

    # ── Mode berbeda (swing vs intraday) → bukan duplikat ──
    def test_different_mode_not_duplicate(self):
        dedup_and_log_batch(self.csv, [_mk_signal(mode="swing")])
        r2 = dedup_and_log_batch(self.csv, [_mk_signal(mode="intraday")])
        self.assertTrue(r2[0]["fresh"], "mode berbeda = sinyal berbeda")

    # ── find_previous_signal: match hanya jika usia < 14 hari ──
    def test_find_previous_signal_age_window(self):
        now = datetime.now()
        recent = [{
            "date": (now - timedelta(days=5)).strftime("%Y-%m-%d %H:%M"),
            "ticker": "BBCA", "mode": "swing", "entry_price": "10000",
        }]
        is_dup, ref = find_previous_signal(self.csv, "BBCA", "swing", 10050, rows=recent)
        self.assertTrue(is_dup)
        self.assertEqual(ref, (now - timedelta(days=5)).strftime("%d/%m"))

        old = [{
            "date": (now - timedelta(days=DEDUP_MAX_AGE_DAYS + 6)).strftime("%Y-%m-%d %H:%M"),
            "ticker": "BBCA", "mode": "swing", "entry_price": "10000",
        }]
        is_dup2, ref2 = find_previous_signal(self.csv, "BBCA", "swing", 10050, rows=old)
        self.assertFalse(is_dup2, "sinyal berusia >= 14 hari TIDAK dianggap duplikat")
        self.assertIsNone(ref2)

    # ── find_previous_signal: toleransi harga, ticker & aturan anchor 7 hari ──
    def test_find_previous_signal_tolerance(self):
        now = datetime.now()
        rows = [{
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "ticker": "BBCA", "mode": "swing", "entry_price": "10000",
        }]
        # Dalam toleransi ±1% → duplikat (aturan harga)
        is_dup, _ = find_previous_signal(self.csv, "BBCA", "swing", 10050, rows=rows)
        self.assertTrue(is_dup)
        # R6: di luar ±1% TAPI anchor fresh < 7 hari → tetap duplikat
        is_dup4, _ = find_previous_signal(self.csv, "BBCA", "swing", 10200, rows=rows)
        self.assertTrue(is_dup4, "+2% tapi anchor fresh <7 hari → lanjutan (R6)")
        # Ticker berbeda → bukan duplikat
        is_dup2, _ = find_previous_signal(self.csv, "BBRI", "swing", 10050, rows=rows)
        self.assertFalse(is_dup2, "ticker berbeda bukan duplikat")
        # Entry invalid → aman, bukan duplikat
        is_dup3, _ = find_previous_signal(self.csv, "BBCA", "swing", "abc", rows=rows)
        self.assertFalse(is_dup3)
        # Baris fresh=0 (lanjutan) BUKAN anchor → +2% = sinyal baru
        cont = [dict(rows[0], fresh="0")]
        is_dup5, _ = find_previous_signal(self.csv, "BBCA", "swing", 10200, rows=cont)
        self.assertFalse(is_dup5, "fresh=0 bukan anchor 7 hari")
        # Anchor fresh=1 TAPI berusia > 7 hari → +2% = sinyal baru
        old = [{
            "date": (now - timedelta(days=8)).strftime("%Y-%m-%d %H:%M"),
            "ticker": "BBCA", "mode": "swing", "entry_price": "10000", "fresh": "1",
        }]
        is_dup6, _ = find_previous_signal(self.csv, "BBCA", "swing", 10200, rows=old)
        self.assertFalse(is_dup6, "anchor >7 hari + >±1% → sinyal baru")

    # ── Header CSV selalu punya kolom fresh (migrasi aman) ──
    def test_csv_header_has_fresh_column(self):
        dedup_and_log_batch(self.csv, [_mk_signal()])
        with open(self.csv, encoding="utf-8") as f:
            header = f.readline().strip()
        self.assertIn("fresh", header.split(","))


# ══════════════════════════════════════════════════════════════════════════
# TEST 1b — perf_tracker: kolom regime (C2 kolom) + backfill CSV lama
# ══════════════════════════════════════════════════════════════════════════

class TestPerfTrackerRegimeColumn(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.csv = os.path.join(self._tmp.name, "perf_tracker_v7.csv")

    def test_new_signal_logged_with_regime(self):
        s = _mk_signal()
        s["regime"] = "BULL"
        dedup_and_log_batch(self.csv, [s])
        with open(self.csv, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        self.assertIn("regime", header, "header CSV harus punya kolom regime")
        rows = load_signals(self.csv)
        self.assertEqual(rows[0]["regime"], "BULL", "baris baru terisi regime nyata")

    def test_signal_without_regime_defaults_unknown(self):
        # Pemanggil lama (tanpa key regime) → default 'unknown', tidak crash
        dedup_and_log_batch(self.csv, [_mk_signal()])
        rows = load_signals(self.csv)
        self.assertEqual(rows[0]["regime"], "unknown")

    def test_old_csv_without_regime_backfilled_unknown(self):
        # CSV lama persis format data/ asli (tanpa kolom fresh & regime)
        with open(self.csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost\n")
            f.write("2026-08-03 13:44,BRPT,swing,65.0,STRONG_BUY,1840.0,1692.0,2057.0,13,2392000\n")
        # Log sinyal baru → migrasi header aman + backfill baris lama
        s = _mk_signal(ticker="DSSA", entry_price=5000.0)
        s["regime"] = "RANGING"
        dedup_and_log_batch(self.csv, [s])
        with open(self.csv, encoding="utf-8") as f:
            header = f.readline().strip().split(",")
        self.assertIn("regime", header, "kolom regime ditambahkan ke header CSV lama")
        self.assertIn("fresh", header, "kolom fresh tetap ditambahkan juga")
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["regime"], "unknown", "baris lama di-backfill 'unknown'")
        self.assertEqual(rows[0]["fresh"], "1", "baris lama di-backfill fresh=1")
        self.assertEqual(rows[1]["regime"], "RANGING", "baris baru terisi regime nyata")


# ══════════════════════════════════════════════════════════════════════════
# TEST 2 — weekly_report: classify_ohlc (MFE/MAE) & evaluate_signals
# ══════════════════════════════════════════════════════════════════════════

class TestWeeklyReportClassify(unittest.TestCase):
    """Skenario: entry=100, SL=90, TP=110."""

    ENTRY, SL, TP = 100.0, 90.0, 110.0

    def test_tp_hit_then_close_falls_win_tp(self):
        # Hari 2 TP kena (high 115), hari 3 close turun ke 96 → tetap WIN_TP
        df = _ohlc(highs=[105, 115, 108], lows=[95, 100, 96],
                   closes=[100, 112, 96])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "WIN_TP")
        self.assertEqual(res["first_hit"], "TP")
        self.assertEqual(res["max_high"], 115.0)
        self.assertAlmostEqual(res["mfe_pct"], 15.0, places=4)

    def test_sl_hit_first_loss_sl(self):
        # Hari 1 SL kena (low 88) DULUAN, hari 2 TP kena → LOSS_SL (urut baris)
        df = _ohlc(highs=[105, 115], lows=[88, 100], closes=[100, 110])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "LOSS_SL")
        self.assertEqual(res["first_hit"], "SL")
        self.assertAlmostEqual(res["mae_pct"], -12.0, places=4)

    def test_no_hit_open(self):
        df = _ohlc(highs=[105, 108], lows=[95, 98], closes=[104, 107])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "OPEN")
        self.assertEqual(res["first_hit"], "")
        self.assertEqual(res["max_high"], 108.0)
        self.assertEqual(res["min_low"], 95.0)

    def test_same_bar_conservative_loss_sl(self):
        # TP & SL kena di BARIS YANG SAMA → konservatif LOSS_SL
        df = _ohlc(highs=[115], lows=[85], closes=[100])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "LOSS_SL", "same-bar harus konservatif (SL dulu)")
        self.assertEqual(res["first_hit"], "SL")

    def test_same_bar_nan_row_alignment_loss_sl(self):
        """R1 (regresi M8): baris high-NaN TIDAK boleh menghapus baris dari
        penentuan status. Baris 1 high NaN (low aman), baris 2 TP 115 & SL 88
        kena SAMA BARIS → konservatif LOSS_SL. Dulu: dropna() terpisah →
        high [115] berpasangan dengan low [100] → WIN_TP palsu."""
        df = _ohlc(highs=[float("nan"), 115.0], lows=[100.0, 88.0],
                   closes=[100.0, 100.0])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "LOSS_SL")
        self.assertEqual(res["first_hit"], "SL")
        self.assertEqual(res["max_high"], 115.0)

    def test_sl_hit_with_nan_high_loss_sl(self):
        """R1: SL kena di baris 2, SEMUA high NaN → LOSS_SL (dulu: highs.
        dropna() kosong → return OPEN — SL hilang)."""
        df = _ohlc(highs=[float("nan"), float("nan")], lows=[float("nan"), 85.0],
                   closes=[100.0, 100.0])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "LOSS_SL")
        self.assertEqual(res["first_hit"], "SL")
        self.assertEqual(res["min_low"], 85.0)
        self.assertAlmostEqual(res["mae_pct"], -15.0, places=4)

    def test_tp_first_with_nan_low_still_win_tp(self):
        """R1: TP kena di baris 1, low semua NaN → WIN_TP (TP tidak hilang)."""
        df = _ohlc(highs=[115.0, 108.0], lows=[float("nan"), float("nan")],
                   closes=[100.0, 100.0])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "WIN_TP")
        self.assertEqual(res["first_hit"], "TP")
        self.assertEqual(res["mfe_pct"], 15.0)
        self.assertEqual(res["mae_pct"], 0.0, "semua low NaN → mae 0.0, bukan nan")

    def test_empty_df_open(self):
        res = weekly_report_mod.classify_ohlc(pd.DataFrame(), self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "OPEN")
        res2 = weekly_report_mod.classify_ohlc(None, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res2["status"], "OPEN")

    def test_evaluate_signals_with_mock_provider(self):
        """Integrasi: perf CSV + provider mock → evaluasi menghasilkan WIN_TP,
        baris eval ditulis ke CSV, key di-mark agar tidak dievaluasi 2x."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        perf_csv = os.path.join(tmp.name, "perf_tracker_v7.csv")
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        mark_json = os.path.join(tmp.name, "evaluated_keys.json")

        # Sinyal swing berusia 11 hari (>= 10 hari → layak evaluasi)
        sig_date = datetime.now() - timedelta(days=11)
        with open(perf_csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
            f.write(f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,100,90,110,2,20000000,1\n")

        # OHLC 12 hari: TP kena di hari ke-6 setelah entry (high 115), SL tidak pernah
        n = 12
        highs = [101 + i for i in range(5)] + [115] + [112, 111, 113, 110, 112, 111]
        lows = [96, 95, 94, 93, 92, 91, 90.5] + [91, 92, 91, 92, 91]
        df = _ohlc(highs=highs[:n], lows=lows[:n], start=sig_date.date())
        provider = MockProvider(df=df)

        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)

        self.assertEqual(len(results), 1, "1 sinyal layak dievaluasi")
        self.assertEqual(results[0]["status"], "WIN_TP")
        self.assertEqual(results[0]["ticker"], "BBCA")
        self.assertTrue(os.path.exists(eval_csv), "baris evaluasi ditulis ke CSV")
        with open(eval_csv, encoding="utf-8") as f:
            eval_rows = list(csv_reader(f))
        self.assertEqual(len(eval_rows), 1)
        self.assertEqual(eval_rows[0]["status"], "WIN_TP")
        # Key ter-mark → run kedua tidak mengevaluasi lagi
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results2 = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results2), 0, "sinyal sudah di-mark, tidak dievaluasi ulang")

    def test_evaluate_signals_data_missing_no_crash(self):
        """Provider gagal (df kosong) → status DATA_MISSING, bukan crash."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        perf_csv = os.path.join(tmp.name, "perf_tracker_v7.csv")
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        mark_json = os.path.join(tmp.name, "evaluated_keys.json")

        sig_date = datetime.now() - timedelta(days=11)
        with open(perf_csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
            f.write(f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,100,90,110,2,20000000,1\n")

        provider = MockProvider(df=pd.DataFrame())  # get_historical → kosong
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "DATA_MISSING")

    def test_classify_ohlc_nan_guard(self):
        """M8: high/low NaN di-drop sebelum komputasi — bukan status salah
        atau mfe/mae 'nan%'."""
        # NaN di tengah → TP tetap terdeteksi, mfe angka normal
        df = _ohlc(highs=[105, float("nan"), 115], lows=[95, float("nan"), 98])
        res = weekly_report_mod.classify_ohlc(df, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res["status"], "WIN_TP")
        self.assertAlmostEqual(res["mfe_pct"], 15.0, places=4)
        self.assertFalse(str(res["mfe_pct"]).lower().startswith("nan"))
        # Semua NaN → OPEN dengan mfe 0.0 (bukan nan%)
        df2 = _ohlc(highs=[float("nan")] * 3, lows=[float("nan")] * 3)
        res2 = weekly_report_mod.classify_ohlc(df2, self.ENTRY, self.SL, self.TP)
        self.assertEqual(res2["status"], "OPEN")
        self.assertEqual(res2["mfe_pct"], 0.0)
        self.assertEqual(res2["mae_pct"], 0.0)

    def test_open_signal_not_marked_and_reevaluated(self):
        """H2: sinyal OPEN TIDAK di-append & TIDAK di-mark → dievaluasi ulang
        run berikutnya; begitu data menunjukkan TP kena → WIN_TP."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        perf_csv = os.path.join(tmp.name, "perf_tracker_v7.csv")
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        mark_json = os.path.join(tmp.name, "evaluated_keys.json")

        sig_date = datetime.now() - timedelta(days=11)
        with open(perf_csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
            f.write(f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,100,90,110,2,20000000,1\n")

        # Run 1: harga masih dalam range SL-TP → OPEN → tidak di-mark
        df_open = _ohlc(highs=[101, 102, 103, 104], lows=[96, 97, 98, 99],
                        start=sig_date.date())
        provider = MockProvider(df=df_open)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            r1 = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(r1, [], "OPEN tidak boleh masuk hasil evaluasi")
        self.assertFalse(os.path.exists(eval_csv), "OPEN tidak boleh di-append ke CSV")

        # Run 2: data baru menunjukkan TP kena → WIN_TP (karena tidak di-mark)
        df_win = _ohlc(highs=[101, 102, 115, 112], lows=[96, 97, 98, 99],
                       start=sig_date.date())
        provider2 = MockProvider(df=df_win)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            r2 = weekly_report_mod.evaluate_signals(provider=provider2)
        self.assertEqual(len(r2), 1)
        self.assertEqual(r2[0]["status"], "WIN_TP", "OPEN harus bisa jadi WIN_TP di run ke-2")


def csv_reader(f):
    import csv
    return csv.DictReader(f)


# ══════════════════════════════════════════════════════════════════════════
# TEST 2b — weekly_report: --dry-run TIDAK menulis apa pun (C1)
# ══════════════════════════════════════════════════════════════════════════

class TestWeeklyReportDryRun(unittest.TestCase):
    """C1 — dry-run: evaluasi dihitung (untuk preview) tapi TIDAK append
    evaluations_v7.csv & TIDAK mark evaluated_keys.json (footgun lama:
    --no-send tetap menulis → data uji ter-mark sebagian)."""

    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        perf_csv = os.path.join(tmp.name, "perf_tracker_v7.csv")
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        mark_json = os.path.join(tmp.name, "evaluated_keys.json")
        sig_date = datetime.now() - timedelta(days=11)
        with open(perf_csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
            f.write(f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,100,90,110,2,20000000,1\n")
        n = 12
        highs = [101 + i for i in range(5)] + [115] + [112, 111, 113, 110, 112, 111]
        lows = [96, 95, 94, 93, 92, 91, 90.5] + [91, 92, 91, 92, 91]
        df = _ohlc(highs=highs[:n], lows=lows[:n], start=sig_date.date())
        return perf_csv, eval_csv, mark_json, MockProvider(df=df)

    def test_evaluate_signals_dry_run_writes_nothing(self):
        perf_csv, eval_csv, mark_json, provider = self._setup()
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider,
                                                         dry_run=True)
        self.assertEqual(len(results), 1, "dry-run tetap menghitung hasil")
        self.assertEqual(results[0]["status"], "WIN_TP")
        self.assertFalse(os.path.exists(eval_csv),
                         "dry-run tidak boleh menulis evaluations_v7.csv")
        self.assertFalse(os.path.exists(mark_json),
                         "dry-run tidak boleh menulis evaluated_keys.json")

        # Belum di-mark → run normal berikutnya tetap mengevaluasi sinyal
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results2 = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results2), 1, "dry-run tidak boleh menandai sinyal")
        self.assertTrue(os.path.exists(eval_csv), "run normal menulis CSV")

    def test_main_dry_run_no_send_no_write(self):
        sent = []
        def fake_send(text):
            sent.append(text)
            return True
        with mock.patch.object(weekly_report_mod, "evaluate_signals",
                               return_value=[]), \
             mock.patch.object(weekly_report_mod, "send_telegram", fake_send), \
             mock.patch.object(sys, "argv", ["weekly_report.py", "--dry-run"]):
            weekly_report_mod.main()
        self.assertEqual(sent, [], "--dry-run tidak boleh kirim Telegram")

    def test_main_no_send_still_evaluates(self):
        """--no-send tetap seperti semula: evaluasi dijalankan (menulis),
        hanya kirim Telegram yang dilewati."""
        with mock.patch.object(weekly_report_mod, "evaluate_signals",
                               return_value=[]) as ev, \
             mock.patch.object(weekly_report_mod, "send_telegram",
                               return_value=True) as st, \
             mock.patch.object(sys, "argv", ["weekly_report.py", "--no-send"]):
            weekly_report_mod.main()
        ev.assert_called_once()
        st.assert_not_called()

    def test_main_default_sends(self):
        with mock.patch.object(weekly_report_mod, "evaluate_signals",
                               return_value=[]) as ev, \
             mock.patch.object(weekly_report_mod, "send_telegram",
                               return_value=True) as st, \
             mock.patch.object(sys, "argv", ["weekly_report.py"]):
            weekly_report_mod.main()
        ev.assert_called_once()
        st.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════
# TEST 3 — position_tracker: SL / TP / trailing / time-stop / aman
# ══════════════════════════════════════════════════════════════════════════

class TestPositionTracker(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "positions.json")
        self.tr = PositionTracker(db_path=self.db)

    def test_sl_hit_exit_alert_and_position_closed(self):
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        alerts = self.tr.check_positions(lambda t: 94.0)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["level"], "EXIT")
        self.assertIn("SL KENA", alerts[0]["message"])
        self.assertEqual(self.tr.get_positions(), {}, "posisi harus ditutup setelah SL kena")

    def test_tp_hit_exit(self):
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        alerts = self.tr.check_positions(lambda t: 111.0)
        self.assertEqual(alerts[0]["level"], "EXIT")
        self.assertIn("TP KENA", alerts[0]["message"])
        self.assertEqual(self.tr.get_positions(), {})

    def test_trailing_stop_exit(self):
        # entry 100, SL 95 → jarak SL 5. Gain >= 3% (harga 104) mengaktifkan
        # trailing: highest 104 - (5 * 0.5) = 101.5. Turun ke 101 → trailing kena.
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=120)
        first = self.tr.check_positions(lambda t: 104.0)
        self.assertEqual(first[0]["level"], "HOLD", "harga 104 belum kena exit apa pun")
        pos = self.tr.get_positions()["BBCA"]
        self.assertTrue(pos["trailing_active"])
        self.assertEqual(pos["trailing_stop"], 101.5)

        alerts = self.tr.check_positions(lambda t: 101.0)
        self.assertEqual(alerts[0]["level"], "EXIT")
        self.assertIn("TRAILING KENA", alerts[0]["message"])
        self.assertEqual(self.tr.get_positions(), {})

    def test_trailing_stop_never_decreases(self):
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=120)
        self.tr.check_positions(lambda t: 104.0)
        self.tr.check_positions(lambda t: 103.0)  # harga turun, highest tetap 104
        pos = self.tr.get_positions()["BBCA"]
        self.assertEqual(pos["trailing_stop"], 101.5, "trailing stop tidak boleh turun")

    def test_time_stop_exit(self):
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        # Backdate entry_date 30 hari → swing max_hold = 20 hari → TIME STOP
        data = self.tr._load()
        data["BBCA"]["entry_date"] = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.tr._save()
        self.tr._cache = None
        alerts = self.tr.check_positions(lambda t: 100.0)  # harga aman, tapi waktu habis
        self.assertEqual(alerts[0]["level"], "EXIT")
        self.assertIn("TIME STOP", alerts[0]["message"])
        self.assertEqual(self.tr.get_positions(), {})

    def test_no_positions_safe(self):
        alerts = self.tr.check_positions(lambda t: 100.0)
        self.assertEqual(alerts, [], "tanpa posisi → tidak ada alert, tidak crash")

    def test_hold_status_between_sl_tp(self):
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        alerts = self.tr.check_positions(lambda t: 102.0)
        self.assertEqual(alerts[0]["level"], "HOLD")
        self.assertIn("hold", alerts[0]["message"])
        self.assertIn("BBCA", self.tr.get_positions(), "posisi tetap terbuka saat HOLD")

    def test_price_fetch_failure_info(self):
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        alerts = self.tr.check_positions(lambda t: 0.0)  # getter gagal
        self.assertEqual(alerts[0]["level"], "INFO")
        self.assertIn("Tidak bisa ambil harga", alerts[0]["message"])
        self.assertIn("BBCA", self.tr.get_positions(), "gagal ambil harga ≠ tutup posisi")

    def test_dry_run_mutate_false_no_state_change(self):
        """H1: check_positions(mutate=False) TIDAK mengubah state — posisi
        yang kena SL tetap ADA setelah dry-run, isi positions.json identik."""
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        self.tr.check_positions(lambda t: 104.0)  # pemanasan: trailing aktif
        with open(self.db, encoding="utf-8") as f:
            before = json.load(f)
        alerts = self.tr.check_positions(lambda t: 94.0, mutate=False)  # SL kena!
        self.assertEqual(alerts[0]["level"], "EXIT", "dry-run tetap mendeteksi EXIT")
        with open(self.db, encoding="utf-8") as f:
            after = json.load(f)
        self.assertEqual(after, before, "dry-run (mutate=False) tidak boleh mengubah positions.json")
        self.assertIn("BBCA", self.tr.get_positions(), "posisi tetap ada saat dry-run")

    def test_time_stop_checked_when_price_fetch_fails(self):
        """M7: harga gagal (price<=0) TETAP mengecek time-stop — posisi basi
        tidak lolos hanya karena getter harga error."""
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        data = self.tr._load()
        data["BBCA"]["entry_date"] = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.tr._save()
        self.tr._cache = None
        alerts = self.tr.check_positions(lambda t: 0.0)
        self.assertEqual(alerts[0]["level"], "EXIT")
        self.assertIn("TIME STOP", alerts[0]["message"])
        self.assertEqual(self.tr.get_positions(), {}, "mutate default → time-stop menutup posisi")
        # versi dry-run: terdeteksi tapi TIDAK menutup
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        data = self.tr._load()
        data["BBCA"]["entry_date"] = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.tr._save()
        self.tr._cache = None
        alerts2 = self.tr.check_positions(lambda t: 0.0, mutate=False)
        self.assertEqual(alerts2[0]["level"], "EXIT")
        self.assertIn("TIME STOP", alerts2[0]["message"])
        self.assertIn("BBCA", self.tr.get_positions(), "dry-run tidak menutup posisi")

    def test_add_position_duplicate_keeps_existing(self):
        """M6: add_position untuk ticker yang sudah ada → ditolak (False),
        posisi existing dipertahankan (tidak ditimpa diam-diam)."""
        self.tr.add_position("BBCA", 100, stop_loss=95, take_profit=110)
        ok = self.tr.add_position("BBCA", 200, stop_loss=190, take_profit=220)
        self.assertFalse(ok, "add duplikat harus ditolak")
        pos = self.tr.get_positions()["BBCA"]
        self.assertEqual(pos["entry_price"], 100.0, "existing dipertahankan")
        self.assertEqual(pos["stop_loss"], 95.0, "SL existing tidak berubah")


# ══════════════════════════════════════════════════════════════════════════
# TEST 4 — ai_narrative: LLM success / exception / tanpa key (SEMUA di-mock)
# ══════════════════════════════════════════════════════════════════════════

class TestAiNarrative(unittest.TestCase):

    SIG = {"tkr": "BBCA", "score": 55.0, "weekly": "BULLISH", "rsi": 62,
           "group": "Salim", "bf": "akumulasi", "ff": "net_buy"}

    def _backend(self):
        return {"name": "fake", "api_key": "k", "base_url": "http://x", "model": "m"}

    def test_llm_success_narrative_present(self):
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=self._backend()), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value="Konteks netral: broker akumulasi."):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {"BBCA": "Konteks netral: broker akumulasi."})

    def test_sanitize_strips_markdown_and_limits_sentences(self):
        """M2: output LLM di-strip — markdown berbahaya dibuang, maks 2
        kalimat, teks tidak masuk akal → None (dilewati)."""
        self.assertEqual(
            ai_narrative._sanitize_narrative("**Saham** ini [baik]. `RSI` naik. Kalimat ketiga buang."),
            "Saham ini baik. RSI naik.")
        self.assertIsNone(ai_narrative._sanitize_narrative(""))
        self.assertIsNone(ai_narrative._sanitize_narrative("***"))
        self.assertIsNone(ai_narrative._sanitize_narrative("a"))
        self.assertIsNone(ai_narrative._sanitize_narrative(None))

    def test_generate_sanitizes_llm_output(self):
        """M2: generate_narratives memakai output yang SUDAH di-sanitasi."""
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=self._backend()), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value="**Bold** teks. `code`. Kalimat ketiga."):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {"BBCA": "Bold teks. code."})

    def test_llm_exception_returns_empty_no_crash(self):
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=self._backend()), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               side_effect=Exception("API timeout")):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {}, "LLM exception → {} tanpa crash")

    def test_llm_returns_none_skipped(self):
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=self._backend()), \
             mock.patch.object(ai_narrative, "_call_llm_once", return_value=None):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {})

    def test_no_key_returns_empty(self):
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=None):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {}, "tanpa API key → {} (scan tetap jalan)")

    def test_empty_signals_returns_empty(self):
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=self._backend()):
            narratives = ai_narrative.generate_narratives([], {})
        self.assertEqual(narratives, {})

    def test_call_llm_once_import_failure_returns_none(self):
        # openai tidak tersedia → _call_llm_once harus return None, TIDAK raise
        with mock.patch.dict(sys.modules, {"openai": None}):
            text = ai_narrative._call_llm_once(
                self._backend(), [{"role": "user", "content": "hi"}])
        self.assertIsNone(text)

    def test_generate_caps_at_max_signals(self):
        sigs = [{"tkr": f"T{i}", "score": 60 - i, "weekly": "BULLISH", "rsi": 60,
                 "group": "", "bf": "", "ff": ""} for i in range(6)]
        with mock.patch.object(ai_narrative, "_pick_backend", return_value=self._backend()), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value="narasi netral singkat") as llm:
            narratives = ai_narrative.generate_narratives(sigs, {})
        self.assertEqual(llm.call_count, ai_narrative.MAX_SIGNALS, "maks 3 sinyal")
        self.assertEqual(len(narratives), ai_narrative.MAX_SIGNALS)


# ══════════════════════════════════════════════════════════════════════════
# TEST 5 — telegram_formatter: <3500 chars, (lanjutan), narrative
# ══════════════════════════════════════════════════════════════════════════

class TestTelegramFormatter(unittest.TestCase):

    def _swing_list(self, n=3):
        return [_mk_swing(tkr=f"SW{i}", score=60 - i) for i in range(n)]

    def _intra_list(self, n=2):
        return [_mk_intra(tkr=f"IN{i}", score=52 - i) for i in range(n)]

    def test_message_under_3500_chars(self):
        swing = self._swing_list(5)
        intra = self._intra_list(5)
        intra[0]["tkr"] = "SW0"  # SW0 lolos swing & intraday → mode ganda
        msg = telegram_formatter.format_message(swing, intra)
        self.assertLess(len(msg), 3500, f"pesan terlalu panjang: {len(msg)} chars")
        self.assertIn("SCREENER V7", msg)
        # R6: ticker dobel mode tampil SEKALI di SWING (penanda ⚡ di akhir),
        # TIDAK diulang di section INTRADAY
        self.assertEqual(msg.count("SW0"), 1, "ticker dobel hanya muncul 1x")
        swing_sec = msg.split("⚡ INTRADAY")[0]
        self.assertIn("⚡", swing_sec, "penanda mode ganda (⚡) di baris SWING")
        intra_sec = msg.split("⚡ INTRADAY")[1] if "⚡ INTRADAY" in msg else ""
        self.assertNotIn("SW0", intra_sec, "ticker dobel tidak diulang di INTRADAY")

    def test_continuation_label(self):
        swing = self._swing_list(2)
        swing[0]["continuation"] = "05/08"
        msg = telegram_formatter.format_message(swing, self._intra_list(1))
        self.assertIn("🔄 05/08", msg)
        self.assertIn("🔄 lanjutan = sinyal <14 hari", msg, "legend lanjutan harus muncul")

    def test_narrative_and_compact_suffixes(self):
        swing = self._swing_list(2)
        swing[0]["bf"] = "akumulasi_masif_45B"
        swing[0]["earn"] = "Rev +8% YoY | margin 45->47%"
        narratives = {"SW0": "Konteks netral: akumulasi broker naik."}
        msg = telegram_formatter.format_message(swing, self._intra_list(1),
                                                narratives=narratives)
        self.assertIn("📝 Konteks netral: akumulasi broker naik.", msg)
        # R6: suffix pendek di baris yang sama — broker ekstrem & earnings
        self.assertIn("🔵+45B", msg, "broker flow ekstrem → suffix 🔵+45B")
        self.assertIn("📈 Rev+8%", msg, "earnings dipadatkan → 📈 Rev+8%")
        self.assertNotIn("margin", msg, "string earnings panjang tidak tampil")
        # bf netral / akumulasi biasa → tanpa suffix broker
        swing[0]["bf"] = "netral"
        msg2 = telegram_formatter.format_message(swing, self._intra_list(1),
                                                 narratives=narratives)
        self.assertNotIn("🔵+45B", msg2, "bf netral → suffix broker tidak tampil")
        # distribusi → suffix 🔴-8B
        swing[0]["bf"] = "distribusi_8B"
        msg3 = telegram_formatter.format_message(swing, self._intra_list(1))
        self.assertIn("🔴-8B", msg3, "distribusi → suffix 🔴-8B")

    def test_no_narratives_param_no_crash(self):
        # Panggilan lama tanpa narratives → harus tetap jalan
        msg = telegram_formatter.format_message(self._swing_list(3), self._intra_list(2))
        self.assertIn("Swing 3 · Intra 2", msg)
        self.assertIn("Alokasi", msg)

    def test_market_sentiment_section(self):
        sentiment = {
            "reason": "window dressing akhir bulan",
            "sentiment": "GREEN",
            "details": ["beli asing masuk", "ADX naik"],
            "key_levels": {"current": 7000, "support": 6800, "resistance": 7200},
        }
        msg = telegram_formatter.format_message(self._swing_list(2), self._intra_list(1),
                                                market_sentiment=sentiment)
        self.assertIn("🔮 Aman", msg)
        self.assertIn("IHSG 7,000 S 6,800/R 7,200", msg)

    def test_long_message_truncated(self):
        # Narrative panjang × 5 sinyal → pesan >3500 → di-truncate + marker
        swing = self._swing_list(5)
        narratives = {s["tkr"]: "kalimat naratif " * 150 for s in swing}
        msg = telegram_formatter.format_message(swing, self._intra_list(5),
                                                narratives=narratives)
        self.assertIn("(truncated)", msg)
        # L11: jalur truncate tanpa extra_parts = 3490 + marker("\n…(truncated)"=13)
        # → maks 3503 (akan ≤3500 setelah N2 merapikan truncate).
        self.assertLessEqual(len(msg), 3503, "output terpotong di batas implementasi")
        self.assertGreater(len(msg), 3400, "fixture benar-benar memicu truncate")

    def test_concentration_warnings_absent_by_default(self):
        # Tanpa parameter C2 → format lama, tidak ada baris KONSENTRASI
        msg = telegram_formatter.format_message(self._swing_list(2), self._intra_list(1))
        self.assertNotIn("KONSENTRASI", msg)

    def test_concentration_warnings_shown_when_provided(self):
        msg = telegram_formatter.format_message(
            self._swing_list(2), self._intra_list(1),
            concentration_warnings=[
                "⚠️ KONSENTRASI: Grup Barito 45% > 40% — lot dikurangi (BRPT 15→13 lot) → 39%"])
        self.assertIn("⚠️ KONSENTRASI: Grup Barito 45% > 40%", msg)
        self.assertIn("lot dikurangi", msg)


# ══════════════════════════════════════════════════════════════════════════
# TEST 5b — C2 guard konsentrasi grup konglomerat (v7_scan)
# ══════════════════════════════════════════════════════════════════════════

class TestC2ConcentrationGuard(unittest.TestCase):
    """C2 — 3 sinyal grup sama >40% modal → lot diturunkan proporsional +
    peringatan di format_message; saat normal (<40%) → no-op total."""

    @classmethod
    def setUpClass(cls):
        # Import v7_scan tanpa menulis file log data/screener.log
        # (jangan sentuh data/ asli selama test). FileHandler diganti
        # handler null inert (level 1000) supaya logging lain tidak rusak.
        class _NullHandler:
            level = 1000
            def setFormatter(self, *a, **k):
                return None
        with mock.patch("logging.FileHandler", _NullHandler):
            import v7_scan as v7s
        cls.v7s = v7s

    def _sig(self, tkr, price, lots, group):
        """Sinyal swing sintetis — sizing dibuat persis seperti position_sizing."""
        return {
            "tkr": tkr, "score": 70.0, "price": price,
            "exit": {"stop_loss": int(price * 0.9), "take_profit": int(price * 1.2),
                     "rrr": 2.0},
            "sizing": {"lots": lots, "cost": lots * price * 100, "pct_modal": 15.0,
                       "risk_amount": lots * price * 100 * 0.05},
            "bf": "akumulasi", "ff": "net_buy", "weekly": "BULLISH",
            "brokers": "", "entry_rec": {"method": "Limit", "price_range": "-"},
            "group": group,
        }

    def _logged(self, tickers, mode="swing", lots=0, price=0.0):
        return [{"ticker": t, "mode": mode, "lots": lots, "cost": lots * price * 100}
                for t in tickers]

    def test_3_signals_same_group_45pct_lots_reduced_and_warned(self):
        CAP = 20_000_000
        price = 2000.0
        lots = int(CAP * 0.15 / (price * 100))  # 15 lot = 15% modal per sinyal
        self.assertEqual(lots, 15)
        swing = [self._sig("BRPT", price, lots, "Barito"),
                 self._sig("BUMI", price, lots, "Barito"),
                 self._sig("DSSA", price, lots, "Barito")]  # total 45% > 40%
        logged = self._logged(["BRPT", "BUMI", "DSSA"], lots=lots, price=price)

        warns = self.v7s.enforce_group_concentration_guard(
            swing, [], logged, CAP, max_pct=40.0)

        self.assertEqual(len(warns), 1, "satu peringatan untuk grup Barito")
        self.assertIn("KONSENTRASI", warns[0])
        self.assertIn("Barito", warns[0])
        self.assertIn("45% > 40%", warns[0])
        self.assertIn("lot dikurangi", warns[0])
        # Lot benar-benar diturunkan (15 → 13) & total cost ≤ 40% modal
        self.assertEqual(swing[0]["sizing"]["lots"], 13)
        total = sum(s["sizing"]["cost"] for s in swing)
        self.assertLessEqual(total, CAP * 0.40 + 1, "total grup ≤ 40% setelah guard")
        # logged_signals ikut disinkronkan (CSV konsisten dengan pesan)
        self.assertEqual(logged[0]["lots"], swing[0]["sizing"]["lots"])
        self.assertEqual(logged[0]["cost"], swing[0]["sizing"]["cost"])
        # Peringatan tampil di format_message
        msg = telegram_formatter.format_message(swing, [], capital=CAP,
                                                concentration_warnings=warns)
        self.assertIn("⚠️ KONSENTRASI", msg)
        self.assertIn("Barito", msg)

    def test_1_signal_15pct_normal_no_change_no_warning(self):
        CAP = 20_000_000
        price = 2000.0
        lots = int(CAP * 0.15 / (price * 100))
        swing = [self._sig("BRPT", price, lots, "Barito")]  # 15% saja
        logged = self._logged(["BRPT"], lots=lots, price=price)

        warns = self.v7s.enforce_group_concentration_guard(
            swing, [], logged, CAP, max_pct=40.0)

        self.assertEqual(warns, [], "konsentrasi normal → tanpa peringatan")
        self.assertEqual(swing[0]["sizing"]["lots"], lots, "lot tidak berubah")
        self.assertEqual(swing[0]["sizing"]["cost"], lots * price * 100,
                         "cost tidak berubah")
        self.assertEqual(logged[0]["lots"], lots, "logged_signals tidak berubah")

    def test_two_groups_only_offending_one_reduced(self):
        CAP = 20_000_000
        price = 2000.0
        lots = int(CAP * 0.15 / (price * 100))
        swing = [self._sig("BRPT", price, lots, "Barito"),   # 15%
                 self._sig("BUMI", price, lots, "Barito"),   # 15%
                 self._sig("DSSA", price, lots, "Barito"),   # 15% → 45% Barito
                 self._sig("ASII", price, lots, "Astra")]    # 15% Astra — normal
        logged = self._logged(["BRPT", "BUMI", "DSSA", "ASII"], lots=lots, price=price)

        warns = self.v7s.enforce_group_concentration_guard(
            swing, [], logged, CAP, max_pct=40.0)

        self.assertEqual(len(warns), 1, "hanya Barito yang melanggar")
        self.assertIn("Barito", warns[0])
        astra = swing[3]
        self.assertEqual(astra["sizing"]["lots"], lots, "grup normal tidak disentuh")

    def test_empty_signals_no_crash(self):
        self.assertEqual(self.v7s.enforce_group_concentration_guard([], [], [], 20_000_000), [])

    def test_warning_has_mode_label_and_risk_amount_scaled(self):
        """L1 — detail warning C2 memakai label mode (swing/intraday) supaya
        ticker yang muncul di kedua mode tidak ambigu; L3 — risk_amount ikut
        di-scale proporsional (5% dari cost baru), tidak overstate."""
        CAP = 20_000_000
        price = 2000.0
        lots = int(CAP * 0.15 / (price * 100))  # 15 lot = 15% per sinyal
        swing = [self._sig("BRPT", price, lots, "Barito"),
                 self._sig("BUMI", price, lots, "Barito")]
        intra = [self._sig("DSSA", price, lots, "Barito")]  # total 45% > 40%
        for s in swing + intra:
            s["sizing"]["risk_amount"] = 999_999_999  # nilai lama (overstate)
        logged = (self._logged(["BRPT", "BUMI"], mode="swing", lots=lots, price=price)
                  + self._logged(["DSSA"], mode="intraday", lots=lots, price=price))

        warns = self.v7s.enforce_group_concentration_guard(
            swing, intra, logged, CAP, max_pct=40.0)

        self.assertEqual(len(warns), 1)
        self.assertIn("BRPT(swing)", warns[0], "L1: label mode di detail")
        self.assertIn("BUMI(swing)", warns[0], "L1: label mode di detail")
        self.assertIn("DSSA(intraday)", warns[0], "L1: label mode di detail")
        # L3: risk_amount = 5% dari cost BARU (di-scale proporsional)
        for s in swing + intra:
            new_cost = s["sizing"]["cost"]
            self.assertEqual(s["sizing"]["risk_amount"], int(new_cost * 0.05),
                             "risk_amount harus mengikuti cost baru")
            self.assertLess(s["sizing"]["risk_amount"], 999_999_999)


# ══════════════════════════════════════════════════════════════════════════
# TEST 6 — position_check_intraday: tanpa positions.json → exit 0 / tidak crash
# ══════════════════════════════════════════════════════════════════════════

class TestPositionCheckIntraday(unittest.TestCase):

    def test_main_no_positions_exit_zero(self):
        """Tanpa posisi aktif (get_positions → {}) → main() return 0, tidak crash,
        tidak menyentuh data/ asli & tidak memanggil network."""
        calls = []

        class FakeTracker:
            def get_positions(self):
                calls.append("get_positions")
                return {}

        with mock.patch.object(pci, "PositionTracker", FakeTracker), \
             mock.patch.object(pci, "setup_logging", lambda: None), \
             mock.patch.object(sys, "argv", ["position_check_intraday.py", "--dry-run"]):
            rc = pci.main()

        self.assertEqual(rc, 0, "tanpa posisi harus exit 0")
        self.assertIn("get_positions", calls)
        self.assertNotIn("check_positions", calls, "tidak perlu cek harga tanpa posisi")

    def test_main_with_positions_no_status_change(self):
        """Posisi aman (HOLD) → tidak ada EXIT → tidak kirim Telegram, return 0.
        M5: InvezgoProvider DI-MOCK (dulu provider NYATA ikut ter-instantiate
        di main() sebelum check_positions)."""
        class FakeTracker:
            def get_positions(self):
                return {"BBCA": {"entry_price": 100}}
            def check_positions(self, getter, **kwargs):
                return [{"ticker": "BBCA", "level": "HOLD",
                         "message": "BBCA +2.0% (entry 100) | SL 95 | TP 110 | hold 18d lagi"}]

        with mock.patch.object(pci, "PositionTracker", FakeTracker), \
             mock.patch.object(pci, "setup_logging", lambda: None), \
             mock.patch.object(pci, "InvezgoProvider", lambda: object()), \
             mock.patch.object(sys, "argv", ["position_check_intraday.py", "--dry-run"]):
            rc = pci.main()

        self.assertEqual(rc, 0, "tidak ada perubahan status → diam, exit 0")


# ══════════════════════════════════════════════════════════════════════════
# TEST 6b — intraday_check: state anti-duplikat (H3) & open tanpa fallback (H4)
# ══════════════════════════════════════════════════════════════════════════

class TestIntradayCheck(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.csv = os.path.join(self._tmp.name, "perf_tracker_v7.csv")
        self.state = os.path.join(self._tmp.name, "intraday_check_state.json")
        with open(self.csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
            now = datetime.now()
            f.write(f"{now.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,100,90,110,2,20000000,1\n")
        self._now = datetime.now().replace(hour=8, minute=45, second=0, microsecond=0)

    def test_same_day_same_fingerprint_skips_send(self):
        """H3: run ganda tanggal sama + fingerprint sama → run ke-2 skip kirim."""
        sent = []
        def fake_send(msg):
            sent.append(msg)
            return True
        with mock.patch.object(ic, "STATE_FILE", self.state), \
             mock.patch.object(ic, "send_telegram_sync", fake_send):
            rc1 = ic.run_check(self.csv, lambda t: (100.0, "open"),
                               dry_run=False, now=self._now)
            rc2 = ic.run_check(self.csv, lambda t: (100.0, "open"),
                               dry_run=False, now=self._now)
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual(len(sent), 1, "run ke-2 harus skip kirim (anti-duplikat)")
        with open(self.state, encoding="utf-8") as f:
            state = json.load(f)
        self.assertEqual(state["date"], self._now.strftime("%Y-%m-%d"))
        self.assertEqual(state["sent"], ["BBCA|100.0"])

    def test_next_day_sends_again(self):
        """H3: tanggal BERBEDA → kirim lagi (state per hari)."""
        sent = []
        def fake_send(msg):
            sent.append(msg)
            return True
        with mock.patch.object(ic, "STATE_FILE", self.state), \
             mock.patch.object(ic, "send_telegram_sync", fake_send):
            ic.run_check(self.csv, lambda t: (100.0, "open"),
                         dry_run=False, now=self._now)
            ic.run_check(self.csv, lambda t: (100.0, "open"),
                         dry_run=False, now=self._now + timedelta(days=1))
        self.assertEqual(len(sent), 2, "hari berbeda = pesan baru → kirim lagi")

    def test_dry_run_does_not_write_state(self):
        """H3: dry-run tetap print & TIDAK menulis state anti-duplikat."""
        sent = []
        def fake_send(msg):
            sent.append(msg)
            return True
        with mock.patch.object(ic, "STATE_FILE", self.state), \
             mock.patch.object(ic, "send_telegram_sync", fake_send):
            rc = ic.run_check(self.csv, lambda t: (100.0, "open"),
                              dry_run=True, now=self._now)
        self.assertEqual(rc, 0)
        self.assertEqual(sent, [], "dry-run tidak boleh kirim")
        self.assertFalse(os.path.exists(self.state), "dry-run tidak boleh menulis state")

    def test_fetch_open_price_no_stale_close_fallback(self):
        """H4: open & price kosong → (None, None) — TIDAK fallback ke close
        lama (close sesi sebelumnya = OK ENTRY palsu di 08:45)."""
        class P1:
            def get_intraday(self, tkr):
                return {"open": 0, "price": 0, "close": 100}
        self.assertEqual(ic.fetch_open_price(P1(), "BBCA"), (None, None))
        class P2:
            def get_intraday(self, tkr):
                return {"open": 5000, "price": 0, "close": 100}
        self.assertEqual(ic.fetch_open_price(P2(), "BBCA"), (5000.0, "open"))
        class P3:
            def get_intraday(self, tkr):
                return {"open": 0, "price": 5050, "close": 100}
        self.assertEqual(ic.fetch_open_price(P3(), "BBCA"), (5050.0, "price"))

    def test_unavailable_open_skipped_not_classified_ok(self):
        """H4: open tidak tersedia → ticker dilewati, TIDAK diklasifikasi OK."""
        sent = []
        def fake_send(msg):
            sent.append(msg)
            return True
        with mock.patch.object(ic, "STATE_FILE", self.state), \
             mock.patch.object(ic, "send_telegram_sync", fake_send):
            rc = ic.run_check(self.csv, lambda t: (None, None),
                              dry_run=False, now=self._now)
        self.assertEqual(rc, 0)
        self.assertEqual(sent, [], "data sesi belum tersedia → tidak kirim")


# ══════════════════════════════════════════════════════════════════════════
# TEST 7 — cron_v3_scan: subprocess.run memakai env PYTHONUTF8=1 (AST inspection)
# ══════════════════════════════════════════════════════════════════════════

class TestCronV3Scan(unittest.TestCase):
    """Inspeksi AST cron_v3_scan.py — TIDAK menjalankan cron sungguhan (akan
    memanggil scan & Telegram). Memastikan perbaikan bug charmap (PYTHONUTF8=1)
    tetap ada di kode."""

    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(_ROOT, "cron_v3_scan.py")
        with open(cls.path, encoding="utf-8") as f:
            cls.tree = ast.parse(f.read(), filename=cls.path)

    def _find_env_assign(self):
        """Cari `_env["PYTHONUTF8"] = "1"` di AST."""
        found = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            t = node.targets[0]
            if (isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                    and t.value.id == "_env"
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "PYTHONUTF8"
                    and isinstance(node.value, ast.Constant)
                    and str(node.value.value) == "1"):
                found.append(node)
        return found

    def _find_subprocess_run(self):
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "run"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "subprocess"):
                return node
        return None

    def test_env_pythonutf8_set(self):
        assigns = self._find_env_assign()
        self.assertTrue(assigns, "cron_v3_scan harus men-set _env['PYTHONUTF8'] = '1'")

    def test_subprocess_run_uses_env_and_safe_args(self):
        call = self._find_subprocess_run()
        self.assertIsNotNone(call, "subprocess.run harus dipanggil")
        kwargs = {k.arg: k.value for k in call.keywords if k.arg}
        # env=_env (variabel yang sudah diberi PYTHONUTF8=1)
        self.assertIn("env", kwargs)
        self.assertIsInstance(kwargs["env"], ast.Name)
        self.assertEqual(kwargs["env"].id, "_env")
        # Argumen aman UTF-8
        self.assertEqual(ast.literal_eval(kwargs["encoding"]), "utf-8")
        self.assertEqual(ast.literal_eval(kwargs["errors"]), "replace")
        self.assertIs(ast.literal_eval(kwargs["capture_output"]), True)
        self.assertEqual(ast.literal_eval(kwargs["timeout"]), 600)
        # cwd = SCREENER_DIR (folder idx_alpha_screener)
        self.assertIn("cwd", kwargs)
        self.assertIsInstance(kwargs["cwd"], ast.Name)
        self.assertEqual(kwargs["cwd"].id, "SCREENER_DIR")

    def test_script_not_run_at_import(self):
        """cron_v3_scan punya efek samping saat import — test ini hanya memastikan
        kita TIDAK meng-import-nya (inspeksi statis saja)."""
        self.assertTrue(os.path.exists(self.path))

    def test_main_called_only_inside_name_main_guard(self):
        """B1: import cron_v3_scan TIDAK boleh mengeksekusi scan — panggilan
        main() hanya boleh ada di dalam blok if __name__ == '__main__'
        (inspeksi AST; import sungguhan tetap dihindari)."""
        def is_guard(node):
            return (isinstance(node, ast.If)
                    and isinstance(node.test, ast.Compare)
                    and isinstance(node.test.left, ast.Name)
                    and node.test.left.id == "__name__"
                    and any(isinstance(c, ast.Constant) and c.value == "__main__"
                            for c in node.test.comparators))

        guards = [n for n in ast.walk(self.tree) if is_guard(n)]
        self.assertTrue(guards, "harus ada blok if __name__ == '__main__'")

        parents = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent

        outside = []
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "main"):
                p = parents.get(id(node))
                while p is not None and not is_guard(p):
                    p = parents.get(id(p))
                if p is None:
                    outside.append(node.lineno)
        self.assertEqual(outside, [],
                         "main() tidak boleh dipanggil di luar guard __main__")


# ══════════════════════════════════════════════════════════════════════════
# FIX BATCH ROUND 1 — 13 fix audit (H1/H2/H3/H4/M1/M2/M3/M4/L1/L3/L4/L5/L10)
# ══════════════════════════════════════════════════════════════════════════

class TestV7EngineNaNGuard(unittest.TestCase):
    """H1 — NaN/inf TIDAK boleh lolos ke _growth_score/_margin_score/_de_score
    (sebelumnya NaN → -25/-15 padahal data tidak valid = harus netral 0)."""

    def test_growth_score_nan_inf_neutral(self):
        self.assertEqual(v7_engine._growth_score(float("nan")), 0)
        self.assertEqual(v7_engine._growth_score(float("inf")), 0)
        self.assertEqual(v7_engine._growth_score(float("-inf")), 0)

    def test_margin_score_nan_inf_neutral(self):
        self.assertEqual(v7_engine._margin_score(float("nan")), 0)
        self.assertEqual(v7_engine._margin_score(float("inf")), 0)

    def test_de_score_nan_inf_neutral(self):
        self.assertEqual(v7_engine._de_score(float("nan")), 0)
        self.assertEqual(v7_engine._de_score(float("inf")), 0)

    def test_parse_fs_series_filters_nan(self):
        fs = {"rows": [{"name": "Pendapatan Usaha", "level": 0, "values": [
            {"year": 2025, "period": "Q4", "amount": float("nan")},
            {"year": 2024, "period": "Q4", "amount": 1000.0},
            {"year": 2023, "period": "Q4", "amount": "800"}]}]}
        vals = v7_engine._parse_fs_series(fs, ["pendapatan usaha"])
        self.assertEqual(len(vals), 2, "NaN harus dibuang dari deret")
        self.assertTrue(all(math.isfinite(v["amount"]) for v in vals))

    def test_earnings_momentum_nan_data_neutral(self):
        # IS penuh NaN → sebelum fix growth=NaN → -25; sekarang netral no_data 40
        fs = {"rows": [{"name": "Pendapatan Usaha", "level": 0, "values": [
            {"year": 2025, "period": "Q4", "amount": float("nan")},
            {"year": 2024, "period": "Q4", "amount": float("nan")}]}]}
        v7_engine._fund_mem_cache.clear()
        v7_engine._keystat_mem_cache.clear()
        v7_engine._broker_mem_cache.clear()
        with mock.patch.object(v7_engine, "_get_fundamental_cached",
                               return_value={"IS": fs, "BS": {}}):
            r = v7_engine.factor_earnings_momentum("BBCA")
        self.assertEqual(r["score"], 40, "data NaN → netral, bukan skor negatif")
        self.assertNotIn("-", r["detail"])


class TestV7EarningsNoDataNeutral(unittest.TestCase):
    """H2 — earnings no_data/error → skor netral 40 (konsisten faktor lain)."""

    def _clean_caches(self):
        v7_engine._fund_mem_cache.clear()
        v7_engine._keystat_mem_cache.clear()
        v7_engine._broker_mem_cache.clear()

    def test_no_data_score_40(self):
        self._clean_caches()
        with mock.patch.object(v7_engine, "get_provider") as gp, \
             mock.patch.object(v7_engine, "_DATA_DIR", tempfile.mkdtemp()):
            gp.return_value = MockProvider()  # get_financial_statement → {}
            r = v7_engine.factor_earnings_momentum("ZZZZ")
        self.assertEqual(r["score"], 40)
        self.assertEqual(r["detail"], "no_data")

    def test_empty_series_score_40(self):
        # data ada tapi tidak ada baris yang bisa di-parse → no_data → 40
        fs = {"rows": [{"name": "Pendapatan Usaha", "level": 0, "values": []}]}
        self._clean_caches()
        with mock.patch.object(v7_engine, "_get_fundamental_cached",
                               return_value={"IS": fs, "BS": {}}):
            r = v7_engine.factor_earnings_momentum("BBCA")
        self.assertEqual(r["score"], 40)
        self.assertEqual(r["detail"], "no_data")


class TestV7KeystatCache(unittest.TestCase):
    """M4 — get_fundamental (keystat) dicache 7 hari: 1 request per ticker per minggu."""

    def test_keystat_cached_file_and_single_call(self):
        calls = []

        class FundProvider:
            def get_fundamental(self, code):
                calls.append(code)
                return {"PER": 10.0, "PBV": 1.5, "ROE": 18.0, "Dividend Yield": 4.0}

        tmp = tempfile.mkdtemp()
        v7_engine._keystat_mem_cache.clear()
        with mock.patch.object(v7_engine, "get_provider",
                               return_value=FundProvider()), \
             mock.patch.object(v7_engine, "_DATA_DIR", tmp):
            r1 = v7_engine.factor_fundamental_quality("BBCA")
            r2 = v7_engine.factor_fundamental_quality("BBCA")  # kedua: dari cache
            r3 = v7_engine.factor_fundamental_quality("BBRI")
        self.assertEqual(len(calls), 2, "hanya 1 call per ticker (BBCA + BBRI)")
        self.assertEqual(calls, ["BBCA", "BBRI"])
        self.assertEqual(r1["score"], r2["score"])
        cache_file = os.path.join(tmp, "fundamental_keystat_BBCA.json")
        self.assertTrue(os.path.exists(cache_file),
                        "cache file keystat harus dibuat di data/")
        self.assertEqual(r3["score"], r2["score"])


class TestPositionSizingCap(unittest.TestCase):
    """H3 — vol-adjustment (1.3x low-vol) TIDAK boleh menembus cap 15% modal/posisi."""

    def test_low_vol_boost_capped_at_15pct(self):
        cap = 20_000_000
        sz = position_sizing(cap, 1000.0, 75.0, atr_pct=1.0)
        # sebelum fix: 0.15*1.3 = 19.5% → cost 3.9jt > cap 3jt
        self.assertLessEqual(sz["cost"], cap * 0.15 + 1)
        self.assertLessEqual(sz["pct_modal"], 15.0)
        self.assertGreater(sz["lots"], 0)

    def test_score_70_also_capped(self):
        sz = position_sizing(20_000_000, 1000.0, 70.0, atr_pct=1.0)
        self.assertLessEqual(sz["cost"], 20_000_000 * 0.15)

    def test_high_vol_halved_under_cap(self):
        sz = position_sizing(20_000_000, 1000.0, 75.0, atr_pct=8.0)
        self.assertLessEqual(sz["cost"], 20_000_000 * 0.15)


class TestExitSlFloorFromConfig(unittest.TestCase):
    """L5 — SL cap dibaca dari config.yaml (sl_floor_pct 0.85), fallback 0.92."""

    def test_config_floor_085(self):
        with mock.patch("v7_exit._sl_floor_from_config", return_value=0.85):
            ex = compute_exit(10000.0, 2000.0, "RANGING", "swing")
        self.assertEqual(ex["stop_loss"], 8500, "floor 0.85 → SL = 85% harga")

    def test_fallback_092(self):
        with mock.patch("v7_exit._sl_floor_from_config", return_value=0.92):
            ex = compute_exit(10000.0, 2000.0, "RANGING", "swing")
        self.assertEqual(ex["stop_loss"], 9200, "fallback 0.92 = perilaku lama")

    def test_real_config_value_loaded(self):
        # config.yaml asli punya scoring.risk_reward.sl_floor_pct = 0.85
        self.assertAlmostEqual(v7_exit._sl_floor_from_config(), 0.85, places=3)


class TestBrokersSellerOnly(unittest.TestCase):
    """L4 — string broker tanpa buyer (hanya seller) tetap ditampilkan."""

    def test_seller_only_shown(self):
        out = telegram_formatter._fmt_brokers_short("🔴BK(-45B)")
        self.assertIn("🔴", out)
        self.assertIn("BK", out)

    def test_both_buyer_seller_shown(self):
        out = telegram_formatter._fmt_brokers_short("🔵BK(+45B) JP(+12B)|🔴MG(-30B)")
        self.assertIn("🔵 BK", out)
        self.assertIn("🔴 MG", out)

    def test_empty_still_empty(self):
        self.assertEqual(telegram_formatter._fmt_brokers_short(""), "")
        self.assertEqual(telegram_formatter._fmt_brokers_short("no_data"), "")


class TestExtraPartsBeforeTruncate(unittest.TestCase):
    """M2 — extra_parts diintegrasikan SEBELUM truncate 3500 (output ≤4096 Telegram)."""

    def _swing_list(self, n=5):
        return [_mk_swing(tkr=f"SW{i}", score=60 - i) for i in range(n)]

    def _intra_list(self, n=5):
        return [_mk_intra(tkr=f"IN{i}", score=52 - i) for i in range(n)]

    def test_extra_parts_included_when_no_truncate(self):
        msg = telegram_formatter.format_message([], [], extra_parts=["A", "B"])
        self.assertIn("A\n\nB", msg)

    def test_long_message_with_extra_parts_stays_bounded(self):
        swing = self._swing_list(5)
        narratives = {s["tkr"]: "kalimat naratif " * 150 for s in swing}
        extra = ["🟢 POSISI: BBCA TP 10.000", "🏭 Grup: Barito: 🔵2"]
        msg = telegram_formatter.format_message(swing, self._intra_list(5),
                                                narratives=narratives,
                                                extra_parts=extra)
        # L11: jalur truncate + extra_parts memakai budget 3500 - len(extra)
        # → total output DIJAMIN ≤ 3500 (dulu test longgar ≤3510 = false-green).
        self.assertLessEqual(len(msg), 3500,
                             "output final ≤ 3500 — aman untuk Telegram")
        self.assertIn("(truncated)", msg)
        self.assertIn("POSISI: BBCA", msg, "extra_parts masuk SEBELUM truncate")


class TestPerfTrackerEmptyCsvHeader(unittest.TestCase):
    """L10 — CSV 0 byte: baris pertama harus header (sebelumnya data tanpa header)."""

    def test_zero_byte_csv_gets_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "perf.csv")
            open(path, "w").close()  # 0 byte
            ok = log_signal(path, "BBCA", "swing", 60.0, "BUY", 10000.0,
                            9500.0, 11000.0, 2, 2000000, regime="RANGING")
            self.assertTrue(ok)
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
            self.assertEqual(lines[0], ",".join(FIELDS),
                             "baris pertama harus header")
            self.assertEqual(len(lines), 2, "header + 1 baris data")
            self.assertIn("BBCA", lines[1])


class TestIhsgEmptyGuard(unittest.TestCase):
    """H4 — IHSG gagal/kosong: scan lanjut tanpa align, TIDAK diam-diam 0 sinyal."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_HERE, "v7_scan.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_scan_guard_before_align(self):
        self.assertIn("if df_ihsg is not None and not df_ihsg.empty:", self.src)
        self.assertIn('df["idx_close"] = 0.0', self.src)
        self.assertIn("IHSG kosong/tidak tersedia", self.src)

    def test_align_still_used_when_ihsg_available(self):
        self.assertIn("df = align_to_market(df, df_ihsg=df_ihsg).dropna()", self.src)

    def test_no_signal_loss_when_ihsg_empty(self):
        # Akar masalah: align_to_market + IHSG kosong → idx_close NaN →
        # dropna() → 0 baris (0 sinyal diam-diam)
        from data import align_to_market
        n = 60
        df = _ohlc(highs=[100.0 + i for i in range(n)],
                   lows=[99.0 + i for i in range(n)],
                   closes=[100.0 + i for i in range(n)])
        bad = align_to_market(df.copy(), df_ihsg=pd.DataFrame())
        self.assertTrue(bad["idx_close"].isna().all())
        self.assertEqual(len(bad.dropna()), 0, "path lama: dropna membuang semua baris")
        # Path fix (H4): kolom idx diisi 0.0 → dropna tidak membuang apa pun
        df["idx_close"] = 0.0
        df["idx_ret_20d"] = 0.0
        df["idx_volatility"] = 0.0
        self.assertEqual(len(df.dropna()), n,
                         "path fix: baris tetap utuh → sinyal tidak hilang")


class TestScanSkipLogLevelAndConfig(unittest.TestCase):
    """M1 — skip/error per-ticker di level WARNING (debug tidak tampil di level
    WARNING); M3 — v7.enabled dibaca dari config.yaml, bukan hardcode True."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_HERE, "v7_scan.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def test_skip_logs_use_warning_not_debug(self):
        self.assertNotIn('logger.debug("Skip', self.src)
        self.assertNotIn('logger.debug("Cooldown', self.src)
        self.assertIn('logger.warning("Skip', self.src)
        self.assertIn('logger.warning("Cooldown', self.src)

    def test_v7_enabled_from_config(self):
        self.assertIn('bool(CONFIG.get("v7", {}).get("enabled", True))', self.src)
        self.assertNotIn("v7_engine.enabled = True", self.src)


# ══════════════════════════════════════════════════════════════════════════
# TEST 8 — data_invezgo (audit Round 1): volume string ribuan, OHLC koma
# desimal (H1), kode API vs cache filename (M1), intraday change/volume (L3/L4)
# ══════════════════════════════════════════════════════════════════════════

class _FakeAnalysis:
    """Fake Invezgo analysis — meniru interface SDK (tanpa network)."""

    def __init__(self, chart=None, intraday=None, keystat=None):
        self.chart = chart or []
        self.keystat = keystat or {}
        self.intraday = intraday or {
            "price": 7080.5, "change": "0,55%", "open": 7000.0,
            "high": 7100.0, "low": 6990.0, "close": 7080.5,
            "volume": "1,234,567",
        }
        self.calls = []

    def get_chart_stock(self, code, from_date, to_date):
        self.calls.append(("get_chart_stock", code))
        return self.chart

    def get_chart_index(self, code, from_date, to_date):
        self.calls.append(("get_chart_index", code))
        return self.chart

    def get_intraday_data(self, code, market="RG"):
        self.calls.append(("get_intraday_data", code))
        return dict(self.intraday)

    def get_keystat(self, code, type_period="Q", limit=8):
        self.calls.append(("get_keystat", code))
        return self.keystat


class _FakeClient:
    def __init__(self, chart=None, intraday=None, keystat=None):
        self.analysis = _FakeAnalysis(chart, intraday, keystat)


class TestDataInvezgoParsing(unittest.TestCase):
    """H1/M1/L3/L4 — parsing & kode API Invezgo (SEMUA di-mock, tanpa network)."""

    def _provider(self, chart=None, intraday=None, keystat=None):
        fake = _FakeClient(chart, intraday, keystat)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(data_invezgo, "get_client", return_value=fake):
            p = data_invezgo.InvezgoProvider()
        p._cache_dir = tmp.name  # cache ditulis ke tempdir, BUKAN cache/ repo
        return p, fake

    def test_volume_thousands_string_and_comma_ohlc(self):
        """H1: volume '1,234,567' & OHLC '7.050,5' TIDAK boleh crash."""
        chart = [{
            "date": "2026-08-05", "open": "7.050,5", "high": "7.200,5",
            "low": "7.000,25", "close": "7.080,5", "volume": "1,234,567",
        }]
        p, fake = self._provider(chart)
        df = p.get_historical("BBCA", period="1mo", use_cache=False)
        self.assertEqual(len(df), 1)
        self.assertEqual(df["Volume"].iloc[0], 1_234_567)
        self.assertEqual(df["Open"].iloc[0], 7050.5)
        self.assertEqual(df["High"].iloc[0], 7200.5)
        self.assertEqual(df["Low"].iloc[0], 7000.25)
        self.assertEqual(df["Close"].iloc[0], 7080.5)
        self.assertEqual(fake.analysis.calls[0][1], "BBCA")

    def test_dot_jk_code_sent_to_api_without_dot(self):
        """M1: 'BBCA.JK' → API menerima 'BBCA' (bukan 'BBCAJK' → 404)."""
        p, fake = self._provider([])  # data kosong → df kosong, bukan crash
        df = p.get_historical("BBCA.JK", period="1mo", use_cache=False)
        self.assertTrue(df.empty)
        self.assertEqual(fake.analysis.calls[0][1], "BBCA")

    def test_cache_filename_uses_sanitized_code(self):
        """M1: sanitasi ketat tetap dipakai utk NAMA FILE cache (tanpa '.' / traversal)."""
        chart = [{"date": "2026-08-05", "open": 1.0, "high": 2.0,
                  "low": 1.0, "close": 2.0, "volume": 10}]
        p, fake = self._provider(chart)
        df = p.get_historical("BBCA.JK", period="1y")  # use_cache=True → simpan cache
        self.assertEqual(len(df), 1)
        # Nama file memakai hasil sanitasi ketat ('BBCAJK' — perilaku lama),
        # BUKAN kode asli ber-titik ('BBCA.JK' — path traversal)
        self.assertTrue(os.path.exists(os.path.join(p._cache_dir, "v7_BBCAJK_1y.csv")))
        self.assertFalse(os.path.exists(os.path.join(p._cache_dir, "v7_BBCA.JK_1y.csv")))
        self.assertEqual(fake.analysis.calls[0][1], "BBCA")

    def test_safe_code_vs_api_code(self):
        """M1: _safe_code (perilaku lama, utk cache) vs _api_code (utk API)."""
        self.assertEqual(data_invezgo._safe_code("BBCA.JK"), "BBCAJK")
        self.assertEqual(data_invezgo._safe_code("bbca"), "BBCA")
        self.assertEqual(data_invezgo._api_code("BBCA.JK"), "BBCA")
        self.assertEqual(data_invezgo._api_code("bbca"), "BBCA")
        self.assertEqual(data_invezgo._api_code("COMPOSITE"), "COMPOSITE")

    def test_intraday_change_comma_and_volume_string(self):
        """L3/L4: change '0,55%' → 0.55; volume '1,234,567' → int, tanpa crash."""
        p, fake = self._provider([])
        r = p.get_intraday("BBCA.JK")
        self.assertEqual(r["change"], 0.55)
        self.assertEqual(r["volume"], 1_234_567)
        self.assertEqual(fake.analysis.calls[0][1], "BBCA")

    def test_intraday_change_none_no_crash(self):
        """L3: change None → 0.0, bukan crash."""
        p, fake = self._provider([], intraday={"price": 100.0, "change": None,
                                               "volume": 10})
        r = p.get_intraday("BBCA")
        self.assertEqual(r["change"], 0.0)

    def test_index_history_parses_id_format_strings(self):
        """NB1: get_index_history memakai _to_float — string format ID
        '7.000,5' dulu float() mentah → ValueError → IHSG gagal diam-diam
        ke fallback. Sekarang harus ter-parse bersih."""
        chart = [{
            "date": "2026-08-05", "open": "7.000,5", "high": "7.200,5",
            "low": "7.000,25", "close": "7.080,5", "volume": "1.234.567",
        }]
        p, fake = self._provider(chart)
        df = p.get_index_history("COMPOSITE", period="1mo", use_cache=False)
        self.assertEqual(len(df), 1)
        self.assertEqual(df["Open"].iloc[0], 7000.5)
        self.assertEqual(df["High"].iloc[0], 7200.5)
        self.assertEqual(df["Low"].iloc[0], 7000.25)
        self.assertEqual(df["Close"].iloc[0], 7080.5)
        self.assertEqual(df["Volume"].iloc[0], 1_234_567)
        self.assertEqual(fake.analysis.calls[0][1], "COMPOSITE")

    def test_get_fundamental_values_converted_to_float(self):
        """L7: nilai fundamental dikonversi ke float SEBELUM dikembalikan —
        string mentah API (format ID '1.234,5') tidak boleh lolos ke pemakai
        (np.isnan(string) → TypeError di v7 factor); string tak-terparse → None."""
        keystat = {"rows": [
            {"name": "PER", "values": [{"amount": "12,5"}]},
            {"name": "PBV", "values": [{"amount": "1.234,5"}]},
            {"name": "ROE", "values": [{"amount": "N/A"}]},
            {"name": "Dividend Yield", "values": [{"amount": None}]},
        ]}
        p, fake = self._provider([], keystat=keystat)
        r = p.get_fundamental("BBCA")
        self.assertEqual(r.get("PER"), 12.5)
        self.assertEqual(r.get("PBV"), 1234.5)
        self.assertIsNone(r.get("ROE"), "string tak-terparse → None, bukan string")
        self.assertIsNone(r.get("Dividend Yield"))
        self.assertTrue(all(not isinstance(v, str) for v in r.values()),
                        "TIDAK boleh ada string mentah yang lolos ke pemakai")
        self.assertEqual(fake.analysis.calls[0][1], "BBCA")


class TestDataInvezgoNumberHelpers(unittest.TestCase):
    """NB2 — format angka Indonesia: titik/koma ribuan TIDAK boleh jadi 0
    (korupsi diam-diam: volume 0 → saham ke-filter)."""

    def test_to_float_thousands_dots(self):
        self.assertEqual(data_invezgo._to_float("1.234.567"), 1234567.0)

    def test_to_float_mixed_dots_and_comma(self):
        self.assertEqual(data_invezgo._to_float("1.234,5"), 1234.5)
        self.assertEqual(data_invezgo._to_float("7.000,5"), 7000.5)

    def test_to_float_comma_thousands(self):
        self.assertEqual(data_invezgo._to_float("1,234"), 1234.0)

    def test_to_float_decimal_comma(self):
        self.assertEqual(data_invezgo._to_float("1,5"), 1.5)

    def test_to_float_plain(self):
        self.assertEqual(data_invezgo._to_float(1234), 1234.0)
        self.assertEqual(data_invezgo._to_float("1234.5"), 1234.5)
        self.assertEqual(data_invezgo._to_float(""), 0.0)
        self.assertEqual(data_invezgo._to_float(None), 0.0)

    def test_to_int_dots_thousands(self):
        self.assertEqual(data_invezgo._to_int("1.234.567"), 1234567)

    def test_to_int_comma_thousands(self):
        self.assertEqual(data_invezgo._to_int("1,234,567"), 1234567)

    def test_to_int_plain(self):
        self.assertEqual(data_invezgo._to_int(1000), 1000)
        self.assertEqual(data_invezgo._to_int("abc"), 0)


# ══════════════════════════════════════════════════════════════════════════
# TEST 9 — entry_timing: NaN price/ATR → "Tidak bisa entry", bukan crash
# ══════════════════════════════════════════════════════════════════════════

class TestEntryTimingNaN(unittest.TestCase):

    def _row(self):
        return pd.Series({"rsi": 50.0, "pct_vs_vwap": 0.0, "vol_ratio": 1.0,
                          "weekly_trend": "BULLISH", "vwap": 100.0,
                          "dc_lower": 95.0, "ema50": 100.0})

    def _v7(self):
        return {"signal": "BUY", "factors": {"broker_detail": "netral"},
                "score": 60.0}

    def test_nan_price_no_entry(self):
        res = entry_timing.recommend_entry("BBCA", float("nan"), 100.0,
                                           self._row(), self._v7())
        self.assertEqual(res["method"], "Tidak bisa entry")

    def test_nan_atr_no_entry(self):
        res = entry_timing.recommend_entry("BBCA", 100.0, float("nan"),
                                           self._row(), self._v7())
        self.assertEqual(res["method"], "Tidak bisa entry")

    def test_none_price_no_entry(self):
        res = entry_timing.recommend_entry("BBCA", None, 100.0,
                                           self._row(), self._v7())
        self.assertEqual(res["method"], "Tidak bisa entry")

    def test_valid_price_normal_recommendation(self):
        res = entry_timing.recommend_entry("BBCA", 100.0, 2.0,
                                           self._row(), self._v7())
        self.assertEqual(res["method"], "Limit order di VWAP")


# ══════════════════════════════════════════════════════════════════════════
# TEST 10 — telegram_sender (audit Round 1): parse_mode Markdown fallback
# (H4) & CHAT_ID fallback saat env kosong (M4) — SEMUA di-mock, tanpa network
# ══════════════════════════════════════════════════════════════════════════

class TestTelegramSenderParseMode(unittest.TestCase):

    def _send(self, text, bot, chat_id=None):
        # L10: semaphore dibuat per-call di dalam send_telegram_message —
        # tidak perlu (dan tidak bisa) di-patch lagi dari luar.
        with mock.patch.object(telegram_sender, "TOKEN", "FAKE"), \
             mock.patch.object(telegram_sender, "_get_bot", return_value=bot):
            return asyncio.run(telegram_sender.send_telegram_message(text, chat_id))

    def test_clean_text_uses_markdown(self):
        """Teks bersih → 1 kiriman dengan parse_mode='Markdown'."""
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(return_value=None)
        ok = self._send("Teks bersih tanpa karakter aneh", bot)
        self.assertTrue(ok)
        self.assertEqual(bot.send_message.await_count, 1)
        self.assertEqual(bot.send_message.await_args.kwargs.get("parse_mode"),
                         "Markdown")

    def test_underscore_text_falls_back_to_plain_once(self):
        """Karakter '_' ditolak Telegram (400) → kirim ulang SEKALI tanpa parse_mode."""
        from telegram.error import BadRequest
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(side_effect=[
            BadRequest("Can't parse entities: ..."), None])
        ok = self._send("Harga _aneh_ [x] `y` *z*", bot)
        self.assertTrue(ok)
        self.assertEqual(bot.send_message.await_count, 2)
        first = bot.send_message.await_args_list[0]
        self.assertEqual(first.kwargs.get("parse_mode"), "Markdown")
        second = bot.send_message.await_args_list[1]
        self.assertNotIn("parse_mode", second.kwargs)
        self.assertEqual(second.kwargs["text"], "Harga _aneh_ [x] `y` *z*")

    def test_non_markdown_error_no_plain_resend(self):
        """Error NON-Markdown (mis. network) → TIDAK kirim ulang plain di attempt sama."""
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(side_effect=RuntimeError("network down"))
        with mock.patch.object(telegram_sender, "MAX_RETRIES", 1):
            ok = self._send("teks biasa", bot)
        self.assertFalse(ok)
        self.assertEqual(bot.send_message.await_count, 1)

    def test_chat_id_fallback_when_env_empty(self):
        """M4/NB3: TELEGRAM_CHAT_ID kosong → CHAT_ID fallback -5237365204,
        bukan ValueError — DAN fallback memunculkan WARNING (tidak diam-diam)."""
        import importlib
        import dotenv
        with mock.patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "",
                                          "TELEGRAM_BOT_TOKEN": "x"}, clear=False), \
             mock.patch.object(dotenv, "load_dotenv", lambda *a, **k: None):
            with self.assertLogs("telegram_sender", level="WARNING") as cm:
                mod = importlib.reload(telegram_sender)
        try:
            self.assertEqual(mod.CHAT_ID, -5237365204)
            self.assertTrue(any("TELEGRAM_CHAT_ID tidak diset" in m
                                for m in cm.output),
                            "fallback default harus log WARNING (NB3)")
        finally:
            importlib.reload(telegram_sender)  # restore state dari .env asli

    def test_chat_not_found_no_plain_resend(self):
        """NB4: error 'chat not found' BUKAN penolakan markdown → langsung
        raise/retry, TIDAK ada resend plain sia-sia (dulu semua BadRequest
        dianggap markdown-rejected → resend percuma)."""
        from telegram.error import BadRequest
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(
            side_effect=BadRequest("Bad Request: chat not found"))
        with mock.patch.object(telegram_sender, "MAX_RETRIES", 1):
            ok = self._send("teks biasa", bot)
        self.assertFalse(ok)
        self.assertEqual(bot.send_message.await_count, 1,
                         "chat not found tidak boleh memicu resend plain")


class TestTelegramSenderCrossLoop(unittest.TestCase):
    """L10 — Semaphore dibuat per-call (bukan module-level): 2 kiriman dari
    event loop BERBEDA tidak boleh RuntimeError ('attached to a different
    loop') — dulu _semaphore module-level ter-bind ke loop pertama."""

    def test_two_calls_from_different_loops_ok(self):
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(return_value=None)

        async def _call(text):
            with mock.patch.object(telegram_sender, "TOKEN", "FAKE"), \
                 mock.patch.object(telegram_sender, "_get_bot", return_value=bot):
                return await telegram_sender.send_telegram_message(text)

        r1 = asyncio.run(_call("pesan 1"))   # loop pertama
        r2 = asyncio.run(_call("pesan 2"))   # loop kedua — dulu RuntimeError
        self.assertTrue(r1)
        self.assertTrue(r2)
        self.assertEqual(bot.send_message.await_count, 2)

    def test_sync_wrapper_after_async_call_ok(self):
        """send_telegram_sync (loop baru tiap call) tetap aman setelah
        send_telegram_message dipakai di loop lain."""
        bot = mock.MagicMock()
        bot.send_message = mock.AsyncMock(return_value=None)
        async def _async_call():
            with mock.patch.object(telegram_sender, "TOKEN", "FAKE"), \
                 mock.patch.object(telegram_sender, "_get_bot", return_value=bot):
                return await telegram_sender.send_telegram_message("async")
        self.assertTrue(asyncio.run(_async_call()))
        with mock.patch.object(telegram_sender, "TOKEN", "FAKE"), \
             mock.patch.object(telegram_sender, "_get_bot", return_value=bot):
            ok = telegram_sender.send_telegram_sync("sync")
        self.assertTrue(ok)
        self.assertEqual(bot.send_message.await_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
