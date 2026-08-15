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
    FIELD_DEFAULTS,
    dedup_and_log_batch,
    find_previous_signal,
    load_signals,
    log_signal,
)
from position_tracker import PositionTracker                # noqa: E402
from signal_manager import CooldownTracker                  # noqa: E402
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

    # ── Skenario inti: batch 1 fresh, batch 2 HARI BERIKUTNYA (harga ±1%,
    # <14 hari) fresh=0 (N10: batch hari SAMA sekarang di-skip total — lihat
    # test_same_day_duplicate_skipped_entirely) ──
    def test_duplicate_within_tolerance_not_logged_as_fresh(self):
        # L2: pin tanggal tetap — pakai datetime.now() langsung flaky kalau
        # assertion menyeberang tengah malam (ref_date bisa beda 1 hari).
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 5, 21, 0, 0)
            mdt.strptime.side_effect = datetime.strptime
            r1 = dedup_and_log_batch(self.csv, [_mk_signal()])
            # Harga 10050 vs 10000 = +0.5% (≤ ±1%) & < 14 hari → duplikat
            # (hari BERBEDA → tetap di-log sebagai lanjutan fresh=0)
            mdt.now.return_value = datetime(2026, 8, 6, 21, 0, 0)
            r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10050.0)])
        self.assertTrue(r1[0]["fresh"], "sinyal pertama harus fresh=True")
        self.assertIsNone(r1[0]["ref_date"])
        self.assertTrue(r1[0]["logged"])
        self.assertFalse(r2[0]["fresh"], "sinyal identik ±1% harus fresh=False (lanjutan)")
        self.assertEqual(r2[0]["ref_date"], datetime(2026, 8, 5, 21, 0, 0).strftime("%d/%m"))
        self.assertTrue(r2[0]["logged"], "sinyal lanjutan TETAP di-log (fresh=0)")

        # Isi CSV: 2 baris, kolom fresh = 1 lalu 0
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["fresh"] for r in rows], ["1", "0"])

    # ── N10 (P1): DEDUP BATCH ANTAR-RUN — 2x run HARI SAMA → 1 baris ──
    # Scanner yang jalan 4x semalam (08/08) menulis 44 baris dengan 33
    # duplikat. Aturan baru: sinyal (ticker+mode, entry ±1%) yang SUDAH
    # tercatat hari ini TIDAK di-log ulang (skip total, bukan fresh=0).
    def test_same_day_duplicate_skipped_entirely(self):
        fixed_now = datetime(2026, 8, 5, 21, 0, 0)
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = fixed_now
            mdt.strptime.side_effect = datetime.strptime
            r1 = dedup_and_log_batch(self.csv, [_mk_signal()])
            # Run ke-2 malam yang sama, harga ±1% → SKIP TOTAL
            with self.assertLogs("perf_tracker", level="WARNING") as cm:
                r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10050.0)])
        self.assertTrue(r1[0]["logged"])
        self.assertFalse(r2[0]["logged"], "duplikat batch hari sama TIDAK di-log")
        self.assertTrue(r2[0]["skipped"], "ditandai skipped (bukan fresh=0)")
        self.assertTrue(any("skip duplikat batch" in m.lower() for m in cm.output),
                        "log warning 'skip duplikat batch' harus muncul")
        # 1 baris per sinyal per hari
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 1, "2x run hari sama → hanya 1 baris tercatat")
        self.assertEqual(rows[0]["fresh"], "1")

        # Duplikat DALAM SATU batch (2 sinyal identik) juga di-skip
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 6, 21, 0, 0)
            mdt.strptime.side_effect = datetime.strptime
            res = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10000.0),
                                                 _mk_signal(entry_price=10000.0)])
        self.assertTrue(res[0]["logged"])
        self.assertTrue(res[1]["skipped"], "duplikat dalam batch yang sama di-skip")
        self.assertEqual(len(load_signals(self.csv)), 2)

    # ── N10 (P1): FALSE-FRESH — baris KEMARIN entry IDENTIK → fresh=0 ──
    # Audit: 16 baris (13.6%) fresh=1 padahal ticker+mode+entry identik sudah
    # ada di hari SEBELUMNYA (contoh batch 08/08 22:02, BUMI/AKRA 04-05/08).
    def test_false_fresh_prev_day_identical_entry_is_continuation(self):
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 5, 21, 0, 0)
            mdt.strptime.side_effect = datetime.strptime
            dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10000.0)])
            # Hari berikutnya, entry IDENTIK → harus fresh=0, BUKAN fresh=1
            mdt.now.return_value = datetime(2026, 8, 6, 21, 0, 0)
            r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10000.0)])
        self.assertFalse(r2[0]["fresh"],
                         "baris kemarin entry identik → fresh=0 (bukan fresh=1)")
        self.assertTrue(r2[0]["logged"])
        self.assertEqual(r2[0]["ref_date"], "05/08")
        rows = load_signals(self.csv)
        self.assertEqual([r["fresh"] for r in rows], ["1", "0"])

    # ── N10 (P1): format tanggal ber-detik ikut diparse — sebelumnya baris
    # '%Y-%m-%d %H:%M:%S' lolos dedup diam-diam (date tidak ter-parse) →
    # sinyal identik keesokan harinya di-re-label 'baru' (false-fresh). ──
    def test_seconds_in_date_format_still_dedup(self):
        with open(self.csv, "w", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh,regime\n")
            f.write("2026-08-04 21:03:30,BBCA,swing,55,BUY,10000,9500,11000,2,2000000,1,unknown\n")
        with mock.patch("perf_tracker.datetime") as mdt:
            mdt.now.return_value = datetime(2026, 8, 5, 21, 0, 0)
            mdt.strptime.side_effect = datetime.strptime
            r = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10000.0)])
        self.assertFalse(r[0]["fresh"],
                         "baris kemarin (format dgn detik) entry identik → fresh=0")

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

    # ── N10 (P3): kolom risk_amount di perf CSV — terisi dari sizing, baris
    # lama di-backfill 0 ──
    def test_risk_amount_column_written_and_backfilled(self):
        # Sinyal baru membawa risk_amount → kolom terisi
        s = _mk_signal(risk_amount=500000)
        dedup_and_log_batch(self.csv, [s])
        with open(self.csv, encoding="utf-8") as f:
            header = f.readline().strip()
        self.assertIn("risk_amount", header.split(","),
                      "header CSV harus punya kolom risk_amount")
        rows = load_signals(self.csv)
        self.assertEqual(rows[0]["risk_amount"], "500000",
                         "risk_amount terisi dari sizing")

        # CSV lama tanpa kolom risk_amount → migrasi + backfill 0
        old = os.path.join(self._tmp.name, "old_perf.csv")
        with open(old, "w", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh,regime\n")
            f.write("2026-08-01 10:00,BBCA,swing,55,BUY,10000,9500,11000,2,2000000,1,unknown\n")
        dedup_and_log_batch(old, [_mk_signal(ticker="DSSA")])
        rows2 = load_signals(old)
        self.assertEqual(rows2[0]["risk_amount"], "0",
                         "baris lama di-backfill risk_amount=0")
        self.assertEqual(rows2[1]["risk_amount"], "0",
                         "sinyal tanpa risk_amount → 0")


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
        attempts_json = os.path.join(tmp.name, "data_missing_attempts.json")
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json), \
             mock.patch.object(weekly_report_mod, "MISSING_ATTEMPTS_FILE", attempts_json):
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
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value="Konteks netral: broker akumulasi."):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {"BBCA": "Konteks netral: broker akumulasi."})

    def test_fallback_backend2_when_backend1_fails(self):
        """V7 akurasi — backend primary gagal → coba secondary (maks 2
        percobaan per sinyal; timeout 25s lalu 20s)."""
        b2 = dict(self._backend(), name="deepseek")
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend(), b2]), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               side_effect=[None, "narasi dari backend kedua"]) as llm:
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {"BBCA": "narasi dari backend kedua"},
                         "backend1 gagal → fallback ke backend2 harus sukses")
        self.assertEqual(llm.call_count, 2, "backend1 gagal → tepat 1 percobaan lagi")
        self.assertEqual(llm.call_args_list[0].kwargs.get("timeout"),
                         ai_narrative.FIRST_TRY_TIMEOUT, "percobaan 1: timeout 25s")
        self.assertEqual(llm.call_args_list[1].kwargs.get("timeout"),
                         ai_narrative.SECOND_TRY_TIMEOUT, "percobaan 2: timeout 20s")

    def test_single_backend_no_fallback(self):
        """Hanya 1 key di .env → 1 percobaan (tidak ada backend kedua)."""
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value=None) as llm:
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {})
        self.assertEqual(llm.call_count, 1, "1 backend → maks 1 percobaan")

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
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value="**Bold** teks. `code`. Kalimat ketiga."):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {"BBCA": "Bold teks. code."})

    def test_llm_exception_returns_empty_no_crash(self):
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               side_effect=Exception("API timeout")):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {}, "LLM exception → {} tanpa crash")

    def test_llm_returns_none_skipped(self):
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]), \
             mock.patch.object(ai_narrative, "_call_llm_once", return_value=None):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {})

    def test_no_key_returns_empty(self):
        with mock.patch.object(ai_narrative, "_pick_backends", return_value=[]):
            narratives = ai_narrative.generate_narratives([self.SIG], {})
        self.assertEqual(narratives, {}, "tanpa API key → {} (scan tetap jalan)")

    def test_empty_signals_returns_empty(self):
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]):
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
        with mock.patch.object(ai_narrative, "_pick_backends",
                               return_value=[self._backend()]), \
             mock.patch.object(ai_narrative, "_call_llm_once",
                               return_value="narasi netral singkat") as llm:
            narratives = ai_narrative.generate_narratives(sigs, {})
        self.assertEqual(llm.call_count, ai_narrative.MAX_SIGNALS, "maks 3 sinyal")
        self.assertEqual(len(narratives), ai_narrative.MAX_SIGNALS)

    def test_pick_backends_order_zen_then_deepseek(self):
        """V7 akurasi — urutan backend: OpenCodeZen (primary) lalu DeepSeek
        (secondary); hanya yang key-nya ADA yang masuk daftar."""
        with mock.patch.dict(os.environ,
                             {"OPENCODE_ZEN_API_KEY": "z",
                              "DEEPSEEK_API_KEY": "d"}, clear=False):
            bs = ai_narrative._pick_backends()
        self.assertEqual([b["name"] for b in bs], ["opencode_zen", "deepseek"])
        with mock.patch.dict(os.environ,
                             {"OPENCODE_ZEN_API_KEY": "", "DEEPSEEK_API_KEY": "d"},
                             clear=False):
            bs1 = ai_narrative._pick_backends()
        self.assertEqual([b["name"] for b in bs1], ["deepseek"], "hanya DeepSeek")
        with mock.patch.dict(os.environ,
                             {"OPENCODE_ZEN_API_KEY": "z", "DEEPSEEK_API_KEY": ""},
                             clear=False):
            bs2 = ai_narrative._pick_backends()
        self.assertEqual([b["name"] for b in bs2], ["opencode_zen"], "hanya Zen")
        with mock.patch.dict(os.environ,
                             {"OPENCODE_ZEN_API_KEY": "", "DEEPSEEK_API_KEY": ""},
                             clear=False):
            self.assertEqual(ai_narrative._pick_backends(), [])


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
        # PASS 3: section baru + label entry eksplisit + separator
        self.assertIn("🏆 SWING SIGNALS", msg)
        self.assertIn("🎯 Buy:", msg)
        self.assertIn("⚙️ MANAJEMEN RISIKO", msg)
        self.assertIn("━" * 20, msg)
        # R6: ticker dobel mode tampil SEKALI di SWING (penanda ⚡ di akhir),
        # TIDAK diulang di section INTRADAY
        self.assertEqual(msg.count("SW0"), 1, "ticker dobel hanya muncul 1x")
        swing_sec = msg.split("⚡ INTRADAY")[0]
        self.assertIn("⚡", swing_sec, "penanda mode ganda (⚡) di baris SWING")
        intra_sec = msg.split("⚡ INTRADAY")[1] if "⚡ INTRADAY" in msg else ""
        self.assertNotIn("SW0", intra_sec, "ticker dobel tidak diulang di INTRADAY")

    def test_buy_label_explicit_with_range_and_fallback(self):
        """GAYA A — label '🎯 Buy:' SELALU muncul utk tiap saham swing."""
        swing = self._swing_list(2)
        # entry_rec normal → range dari price_range (titik-ribuan)
        swing[0]["entry_rec"] = {"method": "Limit", "price_range": "Rp2,120 - Rp2,141"}
        swing[0]["price"] = 2120.0
        # entry_rec kosong → fallback '{harga*0.96:.0f} - {harga:.0f}'
        swing[1]["entry_rec"] = None
        swing[1]["price"] = 105.0
        msg = telegram_formatter.format_message(swing, self._intra_list(1))
        self.assertIn("   🎯 Buy: 2.120 - 2.141", msg)
        self.assertIn("   🎯 Buy: 101 - 105", msg)
        # entry_rec '-' (tidak bisa entry) → fallback juga
        swing[1]["entry_rec"] = {"method": "Tidak bisa entry", "price_range": "-"}
        msg2 = telegram_formatter.format_message(swing, self._intra_list(1))
        self.assertIn("   🎯 Buy: 101 - 105", msg2)
        self.assertNotIn("🎯 2.120-2.141", msg2, "label lama '🎯 {range}' dihapus")

    def test_continuation_label(self):
        swing = self._swing_list(2)
        # N10 (P2): ref_date DINAMIS (usia 2 hari) — label statis seperti
        # "05/08" bisa berusia > 3 hari saat suite dijalankan → tidak tampil.
        ref = (datetime.now() - timedelta(days=2)).strftime("%d/%m")
        swing[0]["continuation"] = ref
        msg = telegram_formatter.format_message(swing, self._intra_list(1))
        # V7 akurasi: swing continuation pindah ke section LANJUTAN (1 baris
        # tanpa narrative), TIDAK lagi tampil di section SWING.
        self.assertIn("🔄 LANJUTAN (masih valid)", msg)
        self.assertIn(f"🔄 {ref}", msg)
        self.assertNotIn("SW0", msg.split("🔄 LANJUTAN (masih valid)")[0],
                         "sinyal lanjutan tidak boleh tampil di SWING")
        self.assertIn("🔄 lanjutan = sinyal <14 hari", msg, "legend lanjutan harus muncul")

    def test_lanjutan_section_split_fresh_vs_continuation(self):
        """V7 akurasi — campuran fresh + lanjutan: fresh di SWING (dengan
        narrative), lanjutan di section 'LANJUTAN (masih valid)' (1 baris
        tanpa narrative); ringkasan menghitung SEMUA sinyal."""
        swing = self._swing_list(3)            # SW0, SW1, SW2
        # N10 (P2): ref_date dinamis, usia <= 3 hari supaya tetap tampil
        ref0 = (datetime.now() - timedelta(days=2)).strftime("%d/%m")
        ref1 = (datetime.now() - timedelta(days=1)).strftime("%d/%m")
        ref2 = (datetime.now() - timedelta(days=3)).strftime("%d/%m")
        swing[0]["continuation"] = ref0        # lanjutan (usia 2 hari)
        swing[1]["continuation"] = ref1        # lanjutan (usia 1 hari)
        intra = self._intra_list(1)
        intra[0]["continuation"] = ref2        # intraday lanjutan (usia 3 hari) → tetap di intra
        narratives = {"SW2": "narasi netral untuk sinyal fresh."}
        msg = telegram_formatter.format_message(swing, intra, narratives=narratives)

        self.assertIn("🔄 LANJUTAN (masih valid)", msg)
        swing_sec = msg.split("🔄 LANJUTAN (masih valid)")[0]
        self.assertIn("SW2", swing_sec, "fresh tetap di SWING")
        self.assertNotIn("SW0", swing_sec, "lanjutan TIDAK di SWING")
        self.assertNotIn("SW1", swing_sec, "lanjutan TIDAK di SWING")
        # Format baris lanjutan (PASS 3): 'BUMI 187 | SL 171/TP 213 🔄 08/08'
        self.assertIn(f"SW0 10.000 | SL 9.500/TP 11.000 🔄 {ref0}", msg)
        # Narrative hanya untuk fresh
        self.assertIn("📝 narasi netral untuk sinyal fresh.", msg)
        lanj_sec = msg.split("🔄 LANJUTAN (masih valid)")[1].split("⚙️ MANAJEMEN RISIKO")[0]
        self.assertNotIn("📝", lanj_sec, "section LANJUTAN tanpa narrative")
        # Intraday lanjutan tetap di section intraday dengan 🔄
        intra_sec = msg.split("⚡ INTRADAY")[1]
        intra_sec = intra_sec.split("🔄 LANJUTAN (masih valid)")[0] \
            if "🔄 LANJUTAN (masih valid)" in intra_sec else intra_sec
        self.assertIn("🔄", intra_sec)
        # Ringkasan 'Swing N · Intra N' LAMA dihapus → digantikan MANAJEMEN RISIKO
        self.assertNotIn("Swing 3 · Intra 1", msg)
        self.assertIn("⚙️ MANAJEMEN RISIKO", msg)
        self.assertIn("• Modal: Rp", msg)

    def test_continuation_older_than_3_days_hidden(self):
        """N10 (P2) — CAP LANJUTAN 3 HARI: sinyal continuation (fresh=0) berusia
        > 3 hari sejak ref_date TIDAK ditampilkan di pesan (tetap di CSV);
        usia <= 3 hari tetap tampil. Berlaku utk swing & intraday."""
        swing = self._swing_list(3)            # SW0, SW1, SW2
        old_ref = (datetime.now() - timedelta(days=5)).strftime("%d/%m")
        fresh_ref = (datetime.now() - timedelta(days=2)).strftime("%d/%m")
        swing[0]["continuation"] = old_ref     # usia 5 hari → HIDDEN
        swing[1]["continuation"] = fresh_ref   # usia 2 hari → tampil
        intra = self._intra_list(2)            # IN0, IN1
        intra[0]["continuation"] = old_ref     # intraday usia 5 hari → HIDDEN
        msg = telegram_formatter.format_message(swing, intra)

        self.assertNotIn(old_ref, msg, "lanjutan usia 5 hari tidak boleh tampil")
        self.assertIn(f"🔄 {fresh_ref}", msg, "lanjutan usia 2 hari tetap tampil")
        self.assertIn("🔄 LANJUTAN (masih valid)", msg)
        self.assertNotIn("SW0", msg, "SW0 (lanjutan tua) tidak tampil di mana pun")
        intra_sec = msg.split("⚡ INTRADAY (H+3)")[1]
        intra_sec = intra_sec.split("🔄 LANJUTAN (masih valid)")[0] \
            if "🔄 LANJUTAN (masih valid)" in intra_sec else intra_sec
        self.assertNotIn("IN0", intra_sec, "intraday lanjutan usia 5 hari tidak tampil")
        self.assertIn("IN1", intra_sec, "intraday fresh tetap tampil")

    def test_top5_display_cap(self):
        """N10 (P2) — TOP-5 PER PESAN: maks 5 sinyal SWING + 5 INTRADAY
        terbaik (skor tertinggi; intraday fresh dulu baru lanjutan). Cap
        tampilan saja — CSV tetap mencatat semua sinyal."""
        # 7 swing fresh skor 70..64 → hanya 5 terbaik tampil
        swing = [_mk_swing(tkr=f"S{i}", score=70 - i) for i in range(7)]
        msg = telegram_formatter.format_message(swing, [])
        swing_sec = msg.split("🏆 SWING SIGNALS")[1]
        for i in range(5):
            self.assertIn(f"{i+1}. S{i}", swing_sec, f"S{i} harus tampil di top-5")
        self.assertNotIn("S5", swing_sec, "swing ke-6 di luar top-5 tidak tampil")
        self.assertNotIn("S6", swing_sec, "swing ke-7 di luar top-5 tidak tampil")

        # 7 intraday: 4 fresh skor 55..52 + 3 lanjutan skor LEBIH TINGGI 65..63
        # → fresh dulu baru lanjutan: F0..F3 + 1 lanjutan terbaik (C0)
        intra = [_mk_intra(tkr=f"F{i}", score=55 - i) for i in range(4)]
        for i in range(3):
            s = _mk_intra(tkr=f"C{i}", score=65 - i)
            s["continuation"] = (datetime.now() - timedelta(days=1)).strftime("%d/%m")
            intra.append(s)
        msg2 = telegram_formatter.format_message([], intra)
        intra_sec = msg2.split("⚡ INTRADAY (H+3)")[1]
        for i in range(4):
            self.assertIn(f"• F{i}", intra_sec, f"fresh F{i} harus tampil duluan")
        self.assertIn("• C0", intra_sec, "1 lanjutan terbaik mengisi slot ke-5")
        self.assertNotIn("• C1", intra_sec, "lanjutan ke-2 di luar top-5 tidak tampil")
        self.assertNotIn("• C2", intra_sec, "lanjutan ke-3 di luar top-5 tidak tampil")

    def test_no_lanjutan_section_when_no_continuation(self):
        """V7 akurasi — tanpa sinyal lanjutan → section LANJUTAN tidak muncul."""
        msg = telegram_formatter.format_message(self._swing_list(2), self._intra_list(1))
        self.assertNotIn("LANJUTAN (masih valid)", msg)
        self.assertNotIn("🔄 lanjutan = sinyal <14 hari", msg)

    def test_narrative_and_compact_suffixes(self):
        swing = self._swing_list(2)
        swing[0]["bf"] = "akumulasi_masif_45B"
        swing[0]["earn"] = "Rev +8% YoY | margin 45->47%"
        narratives = {"SW0": "Konteks netral: akumulasi broker naik."}
        msg = telegram_formatter.format_message(swing, self._intra_list(1),
                                                narratives=narratives)
        self.assertIn("📝 Konteks netral: akumulasi broker naik.", msg)
        # PASS 3: suffix pendek dipindah ke baris Data — broker ekstrem & earnings
        self.assertIn("Flow +45B", msg, "broker flow ekstrem → 'Flow +45B' di baris Data")
        self.assertIn("Rev +8%", msg, "earnings dipadatkan → 'Rev +8%' di baris Data")
        self.assertNotIn("margin", msg, "string earnings panjang tidak tampil")
        # bf netral / akumulasi biasa → tanpa suffix broker
        swing[0]["bf"] = "netral"
        msg2 = telegram_formatter.format_message(swing, self._intra_list(1),
                                                 narratives=narratives)
        self.assertNotIn("+45B", msg2, "bf netral → suffix broker tidak tampil")
        # distribusi → suffix 🔴-8B (di baris Data)
        swing[0]["bf"] = "distribusi_8B"
        msg3 = telegram_formatter.format_message(swing, self._intra_list(1))
        self.assertIn("Flow -8B", msg3, "distribusi → 'Flow -8B' di baris Data")

    def test_no_narratives_param_no_crash(self):
        # Panggilan lama tanpa narratives → harus tetap jalan
        msg = telegram_formatter.format_message(self._swing_list(3), self._intra_list(2))
        self.assertIn("⚙️ MANAJEMEN RISIKO", msg)
        self.assertIn("• Modal: Rp", msg)
        self.assertIn("Max Risk: Rp", msg)

    def test_market_sentiment_section(self):
        sentiment = {
            "reason": "window dressing akhir bulan",
            "sentiment": "GREEN",
            "details": ["beli asing masuk", "ADX naik"],
            "key_levels": {"current": 7000, "support": 6800, "resistance": 7200},
        }
        msg = telegram_formatter.format_message(self._swing_list(2), self._intra_list(1),
                                                market_sentiment=sentiment)
        self.assertIn("🟢 Market: AMAN", msg)
        self.assertIn("IHSG 7.000 (S: 6.800 / R: 7.200)", msg)
        # RED → 🔴 BAHAYA; lainnya → 🟡 WASPADA
        sentiment["sentiment"] = "RED"
        msg2 = telegram_formatter.format_message(self._swing_list(1), [],
                                                 market_sentiment=sentiment)
        self.assertIn("🔴 Market: BAHAYA", msg2)
        sentiment["sentiment"] = "YELLOW"
        msg3 = telegram_formatter.format_message(self._swing_list(1), [],
                                                 market_sentiment=sentiment)
        self.assertIn("🟡 Market: WASPADA", msg3)

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
                "⚠️ KONSENTRASI: Grup Barito 45% > 40% — lot dikurangi (BRPT(swing) 15→13 lot) → 39%"])
        # PASS 3: warning dipetakan ke format '⚠️ Warning: {teks}' + '   ↳ {detail}'
        self.assertIn("⚠️ Warning: Konsentrasi Grup Barito 45% (>40%)", msg)
        self.assertIn("↳ Lot BRPT 15→13 lot dipangkas otomatis ke max 40%.", msg)

    def test_skip_reasons_shown_as_warnings(self):
        """IDE5 — CA blackout skip_reasons tampil di section MANAJEMEN RISIKO."""
        msg = telegram_formatter.format_message(
            self._swing_list(1), self._intra_list(1),
            skip_reasons=["⚠️ CA BLACKOUT: TPIA DIVIDEND 15/08 — skip (H+7)"])
        self.assertIn("⚠️ Warning: CA Blackout TPIA DIVIDEND 15/08", msg)
        self.assertIn("↳ skip (H+7)", msg)

    def test_total_risk_warning_mapped(self):
        """IDE6 — warning TOTAL RISK dipetakan tanpa 'ke max X%' (batas portfolio)."""
        msg = telegram_formatter.format_message(
            self._swing_list(2), self._intra_list(1),
            concentration_warnings=[
                "⚠️ TOTAL RISK: 2.4% > 3.0% — lot dikurangi (BRPT(swing) 13→11 lot)"])
        self.assertIn("⚠️ Warning: Total risk 2.4% (>3.0%)", msg)
        self.assertIn("↳ Lot BRPT 13→11 lot dipangkas otomatis.", msg)


# ══════════════════════════════════════════════════════════════════════════
# TEST 5c — mapping grup SINGLE SOURCE dari config.yaml (V7 akurasi)
# ══════════════════════════════════════════════════════════════════════════

class TestGroupMappingConfig(unittest.TestCase):
    """V7 akurasi — mapping grup final dibaca dari config.yaml section
    'groups' (via groups_config), konsisten di v7_scan / factor_analysis /
    weekly_report — TIDAK ada lagi hardcode GROUP_NAMES (4 sumber drift)."""

    def test_load_groups_final_mapping(self):
        from groups_config import load_groups
        g = load_groups()
        self.assertEqual(g["BRPT"], "Barito")
        self.assertEqual(g["TPIA"], "Barito")
        self.assertEqual(g["DSSA"], "Sinar Mas")
        self.assertEqual(g["BUMI"], "Bakrie")
        self.assertEqual(g["ENRG"], "Bakrie")
        self.assertEqual(g["BNBR"], "Bakrie")
        self.assertEqual(g["ELTY"], "Bakrie")
        self.assertEqual(g["INDF"], "Salim")
        self.assertEqual(g["ICBP"], "Salim")
        self.assertEqual(g["KLBF"], "Kalbe")
        self.assertEqual(g["HMSP"], "Philip Morris")
        self.assertEqual(g["ASII"], "Astra")
        self.assertEqual(g["UNTR"], "Astra")
        self.assertEqual(g["CPIN"], "Charoen Pokphand")
        self.assertEqual(g["ISAT"], "Ooredoo")

    def test_unlabeled_tickers_empty_label(self):
        from groups_config import group_of
        for t in ("BISI", "AKRA", "ALII", "CUAN", "MPPA", "VBID"):
            self.assertEqual(group_of(t), "", f"{t} belum terverifikasi → label kosong")
        self.assertEqual(group_of(""), "")
        self.assertEqual(group_of(None), "")

    def test_all_three_modules_read_same_config_mapping(self):
        """Verifikasi konsistensi: GROUP_NAMES di v7_scan, factor_analysis,
        dan weekly_report SEMUA berasal dari config.yaml yang sama."""
        from groups_config import load_groups
        expected = load_groups()

        class _NullHandler:
            level = 1000
            def setFormatter(self, *a, **k):
                return None
        with mock.patch("logging.FileHandler", _NullHandler):
            import v7_scan
        # R4: GROUP_NAMES dihapus dari v7_scan (def group_of lokal men-shadow
        # import & crash utk None) — sekarang v7_scan memakai group_of dari
        # groups_config; verifikasi perilaku, bukan atribut:
        self.assertEqual(v7_scan.group_of("BRPT"), expected["BRPT"],
                         "v7_scan harus baca mapping dari config.yaml")
        self.assertEqual(v7_scan.group_of("UNKNOWN"), "")
        import factor_analysis
        self.assertEqual(factor_analysis.GROUP_NAMES, expected,
                         "factor_analysis harus baca mapping dari config.yaml")
        if weekly_report_mod is not None:
            self.assertEqual(weekly_report_mod.GROUP_NAMES, expected,
                             "weekly_report harus baca mapping dari config.yaml")

    def test_group_of_case_insensitive_and_missing(self):
        import factor_analysis
        self.assertEqual(factor_analysis.group_of("dssa"), "Sinar Mas")
        self.assertEqual(factor_analysis.group_of("TpIa"), "Barito")
        self.assertEqual(factor_analysis.group_of("ZZZZ"), "")

    def test_load_groups_fallback_empty_on_bad_path(self):
        from groups_config import load_groups
        self.assertEqual(load_groups("C:/tidak/ada/config.yaml"), {},
                         "config hilang → mapping kosong (fallback aman)")


# ══════════════════════════════════════════════════════════════════════════
# TEST 5d — weekly trend masuk scoring V7 (V7 akurasi)
# ══════════════════════════════════════════════════════════════════════════

class TestV7WeeklyTrendScoring(unittest.TestCase):
    """V7 akurasi — weekly_trend masuk scoring sebagai post-adjustment DI
    LUAR weighted sum (bobot faktor tetap total 1.0):
      BEARISH -12 + cap maksimum BUY | BULLISH +5 | NO_DATA/lain 0.
    Tidak boleh ada pasangan STRONG_BUY + weekly BEARISH di output."""

    def setUp(self):
        self._old_enabled = v7_engine.enabled
        v7_engine.enabled = True
        self._patchers = [
            mock.patch.object(v7_engine, "factor_broker_flow",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_foreign_flow",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_fundamental_quality",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_earnings_momentum",
                              return_value={"score": 80, "detail": "x"}),
            # IDE4: faktor baru — di-mock sama (80) supaya tidak ada network
            # call & weighted sum tetap teruji (base = 80 utk semua non-v4).
            mock.patch.object(v7_engine, "factor_broker_trend",
                              return_value={"score": 80, "detail": "x"}),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        v7_engine.enabled = self._old_enabled

    def _base_score(self, v4):
        """Weighted sum tanpa adjustment weekly (bobot config/default 1.0)."""
        w = v7_engine._V7_WEIGHTS
        return v4 * w["v4_score"] + 80.0 * (1.0 - w["v4_score"])

    def test_bearish_penalty_12_and_strong_buy_capped_to_buy(self):
        # v4=90 + faktor 80 → base 84; BEARISH -12 → 72 → threshold BULL
        # [62,...] masih STRONG_BUY → harus di-cap ke BUY.
        r = v7_engine.compute("BBCA", 90.0, "BULL", weekly_trend="BEARISH")
        self.assertAlmostEqual(r["score"], self._base_score(90.0) - 12, places=1)
        self.assertNotEqual(r["signal"], "STRONG_BUY",
                            "tidak boleh ada STRONG_BUY + weekly BEARISH")
        self.assertEqual(r["signal"], "BUY")
        self.assertEqual(r["factors"]["weekly_trend"], "BEARISH")
        self.assertIn("weekly_bearish_-12", r["factors"]["weekly_adjustment"])

    def test_bullish_bonus_5(self):
        r = v7_engine.compute("BBCA", 90.0, "BULL", weekly_trend="BULLISH")
        self.assertAlmostEqual(r["score"], self._base_score(90.0) + 5, places=1)
        self.assertEqual(r["signal"], "STRONG_BUY", "BULLISH tidak di-cap")
        self.assertIn("weekly_bullish_+5", r["factors"]["weekly_adjustment"])

    def test_no_data_and_missing_param_neutral(self):
        r1 = v7_engine.compute("BBCA", 90.0, "BULL", weekly_trend="NO_DATA")
        r2 = v7_engine.compute("BBCA", 90.0, "BULL")  # param opsional
        self.assertAlmostEqual(r1["score"], self._base_score(90.0), places=1)
        self.assertAlmostEqual(r2["score"], self._base_score(90.0), places=1)
        self.assertEqual(r1["factors"]["weekly_adjustment"], "weekly_neutral")
        self.assertEqual(r2["factors"]["weekly_adjustment"], "weekly_neutral")

    def test_bearish_lower_score_drops_signal_level(self):
        # base 72 - 12 = 60 → di bawah STRONG_BUY (62) → BUY (bukan SB)
        r = v7_engine.compute("BBCA", 60.0, "BULL", weekly_trend="BEARISH")
        self.assertAlmostEqual(r["score"], self._base_score(60.0) - 12, places=1)
        self.assertEqual(r["signal"], "BUY")

    def test_weights_total_stays_1_point_0(self):
        self.assertAlmostEqual(sum(v7_engine._V7_WEIGHTS.values()), 1.0, places=6,
                               msg="bobot faktor harus tetap total 1.0")


# ══════════════════════════════════════════════════════════════════════════
# TEST 5c (IDE4) — factor_broker_trend: pembeda non-jenuh (bandarmologi)
# ══════════════════════════════════════════════════════════════════════════

def _broker_hist(vals):
    """Deret history sintetis [{date, net_buy}] ascending — hari mulai 2026-07-01."""
    start = datetime(2026, 7, 1)
    return [{"date": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "net_buy": v}
            for i, v in enumerate(vals)]


class TestBrokerTrendFactor(unittest.TestCase):
    """IDE4 — skor factor_broker_trend harus MEMBEDAKAN (tidak jenuh seperti
    snapshot lama yang 85 utk semua saham):
      A. 20d positif kuat + streak 5 + momentum naik → skor > 70
      B. 20d negatif → skor < 40
      C. data kosong/error → netral 50 (TIDAK menghukum)
      + campuran/melemah tidak boleh dapat skor tinggi."""

    def setUp(self):
        self._patchers = []
        v7_engine._broker_trend_mem_cache.clear()

    def tearDown(self):
        v7_engine._broker_trend_mem_cache.clear()

    def _patch_hist(self, hist):
        return mock.patch.object(v7_engine, "_get_broker_flow_history_cached",
                                 return_value=hist)

    def test_scenario_a_bullish_streak_scores_above_70(self):
        # 20 hari net buy positif & NAIK (avg5 > avg10, streak 20)
        vals = [1e9 + i * 0.5e9 for i in range(20)]
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertGreater(r["score"], 70, "trend bullish kuat harus skor tinggi")
        self.assertLessEqual(r["score"], 100)
        self.assertIn("streak", r["detail"])
        self.assertIn("trend", r["detail"])

    def test_scenario_b_bearish_scores_below_40(self):
        vals = [-1e9 for _ in range(20)]
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertLess(r["score"], 40, "20d negatif harus skor rendah")

    def test_scenario_c_empty_data_neutral_50(self):
        for hist in ([], None, [{"date": "2026-07-01", "net_buy": "abc"}],
                     [{"date": "2026-07-01"}]):
            with self._patch_hist(hist):
                r = v7_engine.factor_broker_trend("BRPT")
            self.assertEqual(r["score"], 50, f"data {hist!r} → netral 50")
            self.assertEqual(r["detail"], "no_data")

    def test_mixed_flow_not_saturated(self):
        # 10 hari +1B lalu 10 hari -1B: 20d ≈ 0, 5d negatif → JANGAN skor tinggi
        vals = [1e9] * 10 + [-1e9] * 10
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertLess(r["score"], 50, "flow campuran ≠ akumulasi → di bawah netral")

    def test_weakening_trend_below_strong(self):
        # 20d positif tapi 5 hari terakhir negatif (melemah) → skor sedang,
        # JAUH di bawah skenario A (skor tinggi HANYA kalau 5d tidak melemah)
        vals = [2e9] * 15 + [-1e9] * 5
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertLess(r["score"], 70, "fase 5d melemah → bukan skor tinggi")
        with self._patch_hist(_broker_hist([1e9 + i * 0.5e9 for i in range(20)])):
            strong = v7_engine.factor_broker_trend("BRPT")
        self.assertLess(r["score"], strong["score"])

    def test_streak_bonus_only_for_consecutive_positive(self):
        # 20d positif tapi 2 hari terakhir negatif (streak 0) → tanpa bonus streak
        vals = [1e9] * 18 + [-1e9, -1e9]
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertNotIn("streak", r["detail"])

    def test_short_history_still_scores_partial(self):
        # hanya 8 hari data positif → 20d tidak dihitung, tapi 10d/5d jalan
        vals = [1e9] * 8
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertGreater(r["score"], 50)


class TestBrokerTrendWeightsIntegration(unittest.TestCase):
    """IDE4 — bobot baru: v4 0.30 + broker_trend 0.10 (total 1.0), konsisten
    dengan config.yaml, dan compute() memakai faktor broker_trend."""

    def test_default_weights_total_1_with_broker_trend(self):
        w = v7_engine._V7_WEIGHTS
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
        self.assertAlmostEqual(w["v4_score"], 0.30)
        self.assertAlmostEqual(w["broker_trend"], 0.10)
        self.assertAlmostEqual(w["broker_flow"], 0.20)

    def test_config_yaml_weights_consistent_and_applied(self):
        import yaml
        with open(os.path.join(_HERE, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        w = cfg["v7"]["weights"]
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6,
                               msg="bobot config.yaml harus total 1.0")
        self.assertEqual(w["v4_score"], 0.30)
        self.assertEqual(w["broker_trend"], 0.10)
        old = dict(v7_engine._V7_WEIGHTS)
        try:
            v7_engine.configure({"weights": w})
            for k, v in w.items():
                self.assertAlmostEqual(v7_engine._V7_WEIGHTS[k], float(v))
            self.assertAlmostEqual(sum(v7_engine._V7_WEIGHTS.values()), 1.0, places=6)
        finally:
            v7_engine._V7_WEIGHTS = old

    def test_compute_uses_broker_trend_weight(self):
        old_enabled = v7_engine.enabled
        v7_engine.enabled = True
        base = {"score": 50, "detail": "x"}
        trend_scores = iter([100, 0])  # call #1: trend 100; call #2: trend 0
        try:
            with mock.patch.object(v7_engine, "factor_broker_flow", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_foreign_flow", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_fundamental_quality", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_earnings_momentum", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_broker_trend",
                                   side_effect=lambda code: {"score": next(trend_scores),
                                                             "detail": "trend 20d +5.0B streak5"}):
                r_hi = v7_engine.compute("BRPT", 50.0, "RANGING")
                r_lo = v7_engine.compute("BRPT", 50.0, "RANGING")
            self.assertEqual(r_hi["factors"]["broker_trend"], 100)
            self.assertEqual(r_hi["factors"]["broker_trend_detail"], "trend 20d +5.0B streak5")
            # beda skor = bobot 0.10 × selisih 100 poin = 10.0
            self.assertAlmostEqual(r_hi["score"] - r_lo["score"], 10.0, places=6)
        finally:
            v7_engine.enabled = old_enabled


# ══════════════════════════════════════════════════════════════════════════
# LAPISAN 2 — FLOW SPIKE (L2-A) & IHSG LATE-SESSION SURGE (L2-B)
# Insight bandarmologi user: (1) net buy MENDADAK = jebakan distribusi besok
# (bandar bergerak diam); (2) IHSG LONCAT penutupan 15.30-16.00 = waspada
# distribusi besok (bandar ajak ritel beli).
# ══════════════════════════════════════════════════════════════════════════

def _mk_intraday_bars(day_closes_map):
    """Deret intraday 5-menit sintetis {datetime, close} utk deteksi late-surge.

    day_closes_map : dict {'YYYY-MM-DD': {HH:MM: close}} — close per waktu
    spesifik; waktu lain memakai close bar pertama (flat). Waktu 08:55-16:00
    step 5 menit (86 bar/hari) — meniru get_index_intraday (close auction 16:00).
    """
    rows = []
    for d, tm in day_closes_map.items():
        base = next(iter(tm.values()))
        for m in range(8 * 60 + 55, 16 * 60 + 1, 5):
            hh, mm = divmod(m, 60)
            t = f"{hh:02d}:{mm:02d}"
            rows.append({"datetime": f"{d} {t}", "close": tm.get(t, base)})
    return rows


class TestFlowSpikeDetection(unittest.TestCase):
    """L2-A — detect_flow_spike + cap skor factor_broker_trend saat spike."""

    def setUp(self):
        v7_engine._broker_trend_mem_cache.clear()

    def tearDown(self):
        v7_engine._broker_trend_mem_cache.clear()

    def _patch_hist(self, hist):
        return mock.patch.object(v7_engine, "_get_broker_flow_history_cached",
                                 return_value=hist)

    def test_1d_spike_5x_baseline_detected_and_score_capped(self):
        # 19 hari baseline 1B + 1 hari MENDADAK 5B (5× rata-rata harian)
        vals = [1e9] * 19 + [5e9]
        spk = v7_engine.detect_flow_spike(vals)
        self.assertTrue(spk["spike"])
        self.assertEqual(spk["kind"], "1d")
        self.assertAlmostEqual(spk["avg_20d_pos"], 1.2e9)
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertIs(r["flow_spike"], True)
        self.assertLessEqual(r["score"], 50,
                             "spike → JANGAN beri bonus: skor di-cap netral 50")
        self.assertIn("flow spike", r["detail"])
        self.assertIn("waspada distribusi", r["detail"])
        # bandingkan: tanpa spike (akumulasi konsisten) skor jauh lebih tinggi
        with self._patch_hist(_broker_hist([1e9] * 20)):
            r_norm = v7_engine.factor_broker_trend("BRPT")
        self.assertGreater(r_norm["score"], r["score"])

    def test_5d_spike_detected(self):
        # baseline 0.5B, 5 hari terakhir 3B → net_5d 15B > 12.5×1.125B (kondisi A)
        vals = [0.5e9] * 15 + [3e9] * 5
        spk = v7_engine.detect_flow_spike(vals)
        self.assertTrue(spk["spike"])
        self.assertEqual(spk["kind"], "5d")
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertIs(r["flow_spike"], True)
        self.assertLessEqual(r["score"], 50)

    def test_consistent_accumulation_no_spike_high_score(self):
        # Akumulasi konsisten 20 hari 1B (streak, tanpa spike) → skor normal TINGGI
        vals = [1e9] * 20
        spk = v7_engine.detect_flow_spike(vals)
        self.assertFalse(spk["spike"])
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertIs(r.get("flow_spike"), False)
        self.assertGreater(r["score"], 70, "akumulasi konsisten tanpa spike → skor tinggi")
        self.assertNotIn("flow spike", r["detail"])

    def test_choppy_flow_no_spike(self):
        # 10 hari +1B lalu 10 hari -0.5B: bukan spike, flow campuran
        vals = [1e9] * 10 + [-0.5e9] * 10
        self.assertFalse(v7_engine.detect_flow_spike(vals)["spike"])

    def test_no_positive_baseline_no_spike(self):
        # Semua hari negatif (baseline 0) → bukan spike (sudah distribusi)
        vals = [-1e9] * 20
        spk = v7_engine.detect_flow_spike(vals)
        self.assertFalse(spk["spike"])
        self.assertEqual(spk["avg_20d_pos"], 0.0)

    def test_empty_or_invalid_data_no_spike(self):
        for nets in ([], None, ["abc", "xyz"], [float("nan"), 1e9]):
            spk = v7_engine.detect_flow_spike(nets)
            self.assertFalse(spk["spike"], f"data {nets!r} → bukan spike")

    def test_gradual_ramp_not_spike(self):
        # Ramp 1B→10.5B (momentum naik sehat, bukan mendadak) — skenario A IDE4
        vals = [1e9 + i * 0.5e9 for i in range(20)]
        spk = v7_engine.detect_flow_spike(vals)
        self.assertFalse(spk["spike"])
        with self._patch_hist(_broker_hist(vals)):
            r = v7_engine.factor_broker_trend("BRPT")
        self.assertIs(r.get("flow_spike"), False)
        self.assertGreater(r["score"], 70)

    def test_compute_exposes_flow_spike_flag(self):
        old_enabled = v7_engine.enabled
        v7_engine.enabled = True
        base = {"score": 50, "detail": "x"}
        try:
            with mock.patch.object(v7_engine, "factor_broker_flow", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_foreign_flow", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_fundamental_quality", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_earnings_momentum", return_value=dict(base)), \
                 mock.patch.object(v7_engine, "factor_broker_trend",
                                   side_effect=[{"score": 80, "detail": "trend 20d +5.0B streak5",
                                                 "flow_spike": True},
                                                {"score": 80, "detail": "trend 20d +5.0B streak5"}]):
                r_spike = v7_engine.compute("BRPT", 50.0, "RANGING")
                r_plain = v7_engine.compute("BRPT", 50.0, "RANGING")
            self.assertIs(r_spike["factors"]["flow_spike"], True)
            self.assertIs(r_plain["factors"]["flow_spike"], False)
        finally:
            v7_engine.enabled = old_enabled


class TestLateSessionSurge(unittest.TestCase):
    """L2-B — detect_late_session_surge: loncat kodok 15.30-16.00 (IHSG)."""

    @staticmethod
    def _df(n=80):
        import numpy as np
        rng = np.random.default_rng(42)
        close = 7000 + np.cumsum(rng.normal(0, 30, n))
        high = close + rng.uniform(5, 40, n)
        low = close - rng.uniform(5, 40, n)
        vol = rng.uniform(1e9, 3e9, n)
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame({"open": close, "high": high, "low": low,
                             "close": close, "volume": vol}, index=idx)

    def test_surge_absolute_0_5pct_detected(self):
        import market_sentiment as ms
        rows = _mk_intraday_bars({"2026-08-10": {
            "15:30": 7000.0, "15:35": 7007.0, "15:40": 7014.0,
            "15:45": 7021.0, "15:50": 7028.0, "15:55": 7035.0, "16:00": 7035.0}})
        r = ms.detect_late_session_surge(rows)
        self.assertTrue(r["late_surge"], "kenaikan 0.5% di 30 mnt terakhir = surge")
        self.assertAlmostEqual(r["surge_pct"], 7035.0 / 7000.0 - 1, places=5)
        self.assertEqual(r["date"], "2026-08-10")
        self.assertIn("15:30", r["window"])

    def test_flat_close_no_surge(self):
        import market_sentiment as ms
        rows = _mk_intraday_bars({"2026-08-10": {"08:55": 7000.0, "16:00": 7000.0}})
        r = ms.detect_late_session_surge(rows)
        self.assertFalse(r["late_surge"])
        self.assertEqual(r["surge_pct"], 0.0)

    def test_relative_surge_2x_avg_detected_without_absolute(self):
        # Sesi berombak ±2 poin per 30 mnt (avg ~0.029%); 30 mnt terakhir +0.12%
        # → kenaikan mutlak < 0.3% tapi > 2× rata-rata → surge RELATIF
        import market_sentiment as ms
        tm = {}
        for m in range(8 * 60 + 55, 16 * 60 + 1, 5):
            hh, mm = divmod(m, 60)
            t = f"{hh:02d}:{mm:02d}"
            step = (m - (8 * 60 + 55)) // 5
            tm[t] = 7000.0 if (step // 6) % 2 == 0 else 7002.0
        tm.update({"15:30": 7000.0, "15:35": 7003.0, "15:40": 7005.0,
                   "15:45": 7006.0, "15:50": 7007.0, "15:55": 7008.0,
                   "16:00": 7008.4})
        r = ms.detect_late_session_surge(_mk_intraday_bars({"2026-08-10": tm}))
        self.assertTrue(r["late_surge"], "kenaikan > 2× rata-rata pergerakan 30 mnt = surge")
        self.assertLess(r["surge_pct"], ms.LATE_SURGE_MIN_PCT,
                        "kenaikan mutlak kecil — trigger lewat cabang RELATIF")
        self.assertGreater(r["surge_pct"], 2 * r["avg_30m_pct"])

    def test_incomplete_session_ignored(self):
        # Bar terakhir 12:00 (sesi belum lengkap) → jangan analisis parsial
        import market_sentiment as ms
        rows = []
        for m in range(8 * 60 + 55, 12 * 60 + 1, 5):
            hh, mm = divmod(m, 60)
            rows.append({"datetime": f"2026-08-10 {hh:02d}:{mm:02d}", "close": 7000.0})
        self.assertFalse(ms.detect_late_session_surge(rows)["late_surge"])

    def test_empty_data_false(self):
        import market_sentiment as ms
        for rows in ([], None, [{"close": "abc"}], [{"datetime": "x"}]):
            r = ms.detect_late_session_surge(rows)
            self.assertFalse(r["late_surge"], f"data {rows!r} → bukan surge")
            self.assertIn("late_surge", r)

    def test_predict_integration_with_provider(self):
        import market_sentiment as ms
        df = self._df()

        class ProvSurge:
            def get_index_intraday(self):
                return _mk_intraday_bars({"2026-08-10": {
                    "15:30": 7000.0, "15:35": 7007.0, "15:40": 7014.0,
                    "15:45": 7021.0, "15:50": 7028.0, "15:55": 7035.0, "16:00": 7035.0}})

        res = ms.predict_market_sentiment(df, ProvSurge())
        self.assertIs(res["late_surge"], True)
        self.assertTrue(res["late_surge_label"])
        self.assertIn("+0.50%", res["late_surge_label"])
        self.assertTrue(any("loncat penutupan" in d for d in res["details"]))

        class ProvPlain:  # provider tanpa method intraday indeks → netral
            pass

        res2 = ms.predict_market_sentiment(df, ProvPlain())
        self.assertIs(res2["late_surge"], False)
        self.assertEqual(res2["late_surge_label"], "")
        self.assertFalse(any("loncat penutupan" in d for d in res2["details"]))

    def test_predict_without_provider_false(self):
        import market_sentiment as ms
        res = ms.predict_market_sentiment(self._df())
        self.assertIs(res["late_surge"], False)


class TestFlowSpikeWarningsV7Scan(unittest.TestCase):
    """L2-A — flow_spike_warnings di v7_scan: maks 3, top skor, dedup ticker."""

    @classmethod
    def setUpClass(cls):
        class _NullHandler:
            level = 1000
            def setFormatter(self, *a, **k):
                return None
        with mock.patch("logging.FileHandler", _NullHandler):
            import v7_scan as v7s
        cls.v7s = v7s

    def _sig(self, tkr, score, spike=True):
        return {"tkr": tkr, "score": score, "flow_spike": spike}

    def test_top3_only_and_dedup_across_modes(self):
        swing = [self._sig("BRPT", 70.0), self._sig("BUMI", 60.0)]
        intra = [self._sig("BUMI", 75.0), self._sig("TPIA", 50.0), self._sig("DSSA", 40.0)]
        warns = self.v7s.flow_spike_warnings(swing, intra, max_n=3)
        self.assertEqual(len(warns), 3, "cap 3 warning (top skor)")
        self.assertEqual(warns[0].count("BUMI"), 1, "BUMI dihitung sekali (skor tertinggi 75)")
        self.assertIn("BRPT", warns[1])
        self.assertIn("TPIA", warns[2])
        self.assertNotIn("DSSA", " ".join(warns), "skor terendah dibuang")
        for w in warns:
            self.assertIn("FLOW SPIKE", w)
            self.assertIn("waspada distribusi", w)

    def test_no_spike_no_warnings(self):
        swing = [self._sig("BRPT", 70.0, spike=False), self._sig("BUMI", 60.0, spike=False)]
        self.assertEqual(self.v7s.flow_spike_warnings(swing, []), [])
        self.assertEqual(self.v7s.flow_spike_warnings([], []), [])

    def test_warnings_render_in_telegram_risk_section(self):
        warns = [
            "⚠️ FLOW SPIKE: BRPT net buy mendadak — waspada distribusi (jebakan bandar)",
            "⚠️ IHSG LONCAT: +0.52% di 15.30-16.00 (vs rata-rata 0.10%/30mnt) — waspada distribusi besok",
        ]
        msg = telegram_formatter.format_message([], [], capital=20_000_000,
                                                concentration_warnings=warns)
        self.assertIn("⚠️ Warning: Flow spike BRPT net buy mendadak", msg)
        self.assertIn("waspada distribusi", msg)
        self.assertIn("⚠️ Warning: IHSG loncat penutupan +0.52% di 15.30-16.00", msg)
        self.assertIn("waspada distribusi besok", msg)


class TestGetIndexIntraday(unittest.TestCase):
    """L2-B — InvezgoProvider.get_index_intraday: parsing deret 5-menit indeks."""

    def test_parses_sorted_rows_and_caches(self):
        import data_invezgo as di

        class FakeAnalysis:
            def get_multi_time_chart(self, code, from_date, to_date, timeframe):
                self.called = True
                return [
                    # tidak urut sengaja — harus di-sort ascending
                    {"date": "2026-08-10T16:00:00.000Z", "open": 6365.0, "high": 6365.0,
                     "low": 6365.0, "close": 6365.374, "volume": "815.948.000"},
                    {"date": "2026-08-10T08:55:00.000Z", "open": 6409.0, "high": 6409.0,
                     "low": 6409.0, "close": 6409.0, "volume": 1000},
                ]

        class FakeClient:
            analysis = FakeAnalysis()

        tmp = tempfile.mkdtemp()
        old_dir = di._DATA_DIR
        di._DATA_DIR = tmp
        try:
            with mock.patch.object(di, "get_client", return_value=FakeClient()):
                prov = di.InvezgoProvider()
                rows = prov.get_index_intraday(code="COMPOSITE", days=3, use_cache=True)
        finally:
            di._DATA_DIR = old_dir
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["datetime"], "2026-08-10 08:55")
        self.assertEqual(rows[0]["time"], "08:55")
        self.assertEqual(rows[1]["datetime"], "2026-08-10 16:00", "harus ascending")
        self.assertAlmostEqual(rows[1]["close"], 6365.374)
        self.assertEqual(rows[1]["volume"], 815948000, "format ribuan ID '815.948.000'")
        # cache NON-kosong ditulis (di tempdir, bukan data/ asli)
        files = [f for f in os.listdir(tmp) if f.startswith("intraday_idx_COMPOSITE_3d_5")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(tmp, files[0]), encoding="utf-8") as f:
            cached = json.load(f)
        self.assertEqual(len(cached), 2)

    def test_error_response_returns_empty(self):
        import data_invezgo as di

        class FakeAnalysis:
            def get_multi_time_chart(self, code, from_date, to_date, timeframe):
                return {"message": "boom", "error": "Unprocessable Entity"}

        class FakeClient:
            analysis = FakeAnalysis()

        tmp = tempfile.mkdtemp()
        old_dir = di._DATA_DIR
        di._DATA_DIR = tmp
        try:
            with mock.patch.object(di, "get_client", return_value=FakeClient()):
                prov = di.InvezgoProvider()
                rows = prov.get_index_intraday(code="COMPOSITE", days=3, use_cache=False)
        finally:
            di._DATA_DIR = old_dir
        self.assertEqual(rows, [], "response error → [] (scan tidak crash)")


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
        # Ticker grup SAMA per mapping FINAL config.yaml: BUMI/ENRG/BNBR = Bakrie
        # (BRPT/TPIA = Barito hanya 2 ticker; DSSA = Sinar Mas — bukan Barito)
        swing = [self._sig("BUMI", price, lots, "Bakrie"),
                 self._sig("ENRG", price, lots, "Bakrie"),
                 self._sig("BNBR", price, lots, "Bakrie")]  # total 45% > 40%
        logged = self._logged(["BUMI", "ENRG", "BNBR"], lots=lots, price=price)

        warns = self.v7s.enforce_group_concentration_guard(
            swing, [], logged, CAP, max_pct=40.0)

        self.assertEqual(len(warns), 1, "satu peringatan untuk grup Bakrie")
        self.assertIn("KONSENTRASI", warns[0])
        self.assertIn("Bakrie", warns[0])
        self.assertIn("45% > 40%", warns[0])
        self.assertIn("lot dikurangi", warns[0])
        # Lot benar-benar diturunkan (15 → 13) & total cost ≤ 40% modal
        self.assertEqual(swing[0]["sizing"]["lots"], 13)
        total = sum(s["sizing"]["cost"] for s in swing)
        self.assertLessEqual(total, CAP * 0.40 + 1, "total grup ≤ 40% setelah guard")
        # logged_signals ikut disinkronkan (CSV konsisten dengan pesan)
        self.assertEqual(logged[0]["lots"], swing[0]["sizing"]["lots"])
        self.assertEqual(logged[0]["cost"], swing[0]["sizing"]["cost"])
        # Peringatan tampil di format_message (PASS 3: '⚠️ Warning: Konsentrasi ...')
        msg = telegram_formatter.format_message(swing, [], capital=CAP,
                                                concentration_warnings=warns)
        self.assertIn("⚠️ Warning: Konsentrasi Grup Bakrie", msg)
        self.assertIn("Bakrie", msg)
        self.assertIn("↳ Lot BUMI 15→13 lot", msg)

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
        swing = [self._sig("BUMI", price, lots, "Bakrie"),   # 15%
                 self._sig("ENRG", price, lots, "Bakrie"),   # 15%
                 self._sig("BNBR", price, lots, "Bakrie"),   # 15% → 45% Bakrie
                 self._sig("ASII", price, lots, "Astra")]    # 15% Astra — normal
        logged = self._logged(["BUMI", "ENRG", "BNBR", "ASII"], lots=lots, price=price)

        warns = self.v7s.enforce_group_concentration_guard(
            swing, [], logged, CAP, max_pct=40.0)

        self.assertEqual(len(warns), 1, "hanya Bakrie yang melanggar")
        self.assertIn("Bakrie", warns[0])
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
        swing = [self._sig("BUMI", price, lots, "Bakrie"),
                 self._sig("ENRG", price, lots, "Bakrie")]
        intra = [self._sig("BNBR", price, lots, "Bakrie")]  # total 45% > 40%
        for s in swing + intra:
            s["sizing"]["risk_amount"] = 999_999_999  # nilai lama (overstate)
        logged = (self._logged(["BUMI", "ENRG"], mode="swing", lots=lots, price=price)
                  + self._logged(["BNBR"], mode="intraday", lots=lots, price=price))

        warns = self.v7s.enforce_group_concentration_guard(
            swing, intra, logged, CAP, max_pct=40.0)

        self.assertEqual(len(warns), 1)
        self.assertIn("BUMI(swing)", warns[0], "L1: label mode di detail")
        self.assertIn("ENRG(swing)", warns[0], "L1: label mode di detail")
        self.assertIn("BNBR(intraday)", warns[0], "L1: label mode di detail")
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

    def _find_pythonutf8_in_env(self):
        """Cari key PYTHONUTF8='1' di dict env subprocess.run (AST).

        Versi jaminan kirim (sinkron dengan profile): PYTHONUTF8 di-set
        INLINE di env dict literal subprocess.run — bukan assignment
        `_env['PYTHONUTF8']='1'` seperti versi lama.
        """
        call = self._find_subprocess_run()
        if call is None:
            return []
        found = []
        for kw in call.keywords:
            if kw.arg != "env" or not isinstance(kw.value, ast.Dict):
                continue
            for k, v in zip(kw.value.keys, kw.value.values):
                if (isinstance(k, ast.Constant) and k.value == "PYTHONUTF8"
                        and isinstance(v, ast.Constant) and str(v.value) == "1"):
                    found.append(kw.value)
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
        # Versi jaminan kirim: PYTHONUTF8='1' di-set INLINE di env dict
        # subprocess.run (bukan assignment `_env['PYTHONUTF8']='1'` lama).
        self.assertTrue(self._find_pythonutf8_in_env(),
                        "cron_v3_scan harus men-set PYTHONUTF8='1' di env subprocess.run")

    def test_subprocess_run_uses_env_and_safe_args(self):
        call = self._find_subprocess_run()
        self.assertIsNotNone(call, "subprocess.run harus dipanggil")
        kwargs = {k.arg: k.value for k in call.keywords if k.arg}
        # env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        self.assertIn("env", kwargs)
        self.assertIsInstance(kwargs["env"], ast.Dict)
        env_pairs = {k.value: v.value
                     for k, v in zip(kwargs["env"].keys, kwargs["env"].values)
                     if isinstance(k, ast.Constant)}
        self.assertEqual(env_pairs.get("PYTHONUTF8"), "1")
        self.assertEqual(env_pairs.get("PYTHONIOENCODING"), "utf-8")
        # Argumen aman UTF-8 (text=True menggantikan encoding/errors lama)
        self.assertIs(ast.literal_eval(kwargs["text"]), True)
        self.assertIs(ast.literal_eval(kwargs["capture_output"]), True)
        self.assertEqual(ast.literal_eval(kwargs["timeout"]), 600)
        # cwd = SCAN_DIR (folder idx_alpha_screener)
        self.assertIn("cwd", kwargs)
        self.assertIsInstance(kwargs["cwd"], ast.Name)
        self.assertEqual(kwargs["cwd"].id, "SCAN_DIR")

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

    def __init__(self, chart=None, intraday=None, keystat=None, inventory=None):
        self.chart = chart or []
        self.keystat = keystat or {}
        self.inventory = inventory or {}
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

    def get_inventory_chart_stock(self, code, from_date, to_date,
                                  scope="val", investor="all", market="ALL"):
        self.calls.append(("get_inventory_chart_stock", code))
        return self.inventory


class _FakeClient:
    def __init__(self, chart=None, intraday=None, keystat=None, inventory=None):
        self.analysis = _FakeAnalysis(chart, intraday, keystat, inventory)


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
# TEST 4b (IDE4) — get_broker_flow_history: parsing inventory chart,
# normalisasi net buy harian, cache TTL 24 jam, error → [] (tidak crash)
# ══════════════════════════════════════════════════════════════════════════

def _inventory_payload(brokers):
    """Response get_inventory_chart_stock sintetis: {price, broker:[{broker,data}]}."""
    return {"price": [{"code": "BRPT", "date": "2026-07-13", "open": 1, "high": 2,
                       "low": 1, "close": 2, "volume": 100}],
            "broker": brokers}


class TestDataInvezgoBrokerFlowHistory(unittest.TestCase):
    """IDE4 — history broker flow dari get_inventory_chart_stock (mock, tanpa
    network): normalisasi Σ net broker per hari, cache file TTL 24 jam
    (2 call → 1 fetch), error/response aneh → []."""

    def _provider(self, inventory=None, tmp=None):
        fake = _FakeClient(inventory=inventory)
        if tmp is None:
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
        # _DATA_DIR di-patch ke tempdir supaya cache file TIDAK menyentuh
        # data/ asli dan tidak bocor antar-test (pola test cache lain).
        self._data_dir_patch = mock.patch.object(data_invezgo, "_DATA_DIR", tmp.name)
        self._data_dir_patch.start()
        self.addCleanup(self._data_dir_patch.stop)
        with mock.patch.object(data_invezgo, "get_client", return_value=fake):
            p = data_invezgo.InvezgoProvider()
        p._cache_dir = tmp.name
        return p, fake, tmp

    def test_normalizes_daily_net_buy_sum_of_brokers(self):
        inv = _inventory_payload([
            {"broker": "AI", "data": [{"date": "2026-07-13", "value": -5_962_730_500},
                                      {"date": "2026-07-14", "value": 1_000_000_000}]},
            {"broker": "AK", "data": [{"date": "2026-07-13", "value": 2_000_000_000},
                                      {"date": "2026-07-14", "value": -500_000_000}]},
        ])
        p, fake, _ = self._provider(inv)
        hist = p.get_broker_flow_history("BRPT", days=20)
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["date"], "2026-07-13")
        self.assertEqual(hist[0]["net_buy"], -3_962_730_500.0)   # -5.96B + 2B
        self.assertEqual(hist[1]["date"], "2026-07-14")
        self.assertEqual(hist[1]["net_buy"], 500_000_000.0)      # 1B + (-0.5B)
        self.assertEqual(fake.analysis.calls[0][0], "get_inventory_chart_stock")
        self.assertEqual(fake.analysis.calls[0][1], "BRPT", "kode API tanpa .JK")

    def test_sorted_ascending_and_limited_to_days(self):
        inv = _inventory_payload([
            {"broker": "AI", "data": [{"date": f"2026-07-{d:02d}", "value": 1_000_000_000}
                                      for d in range(1, 26)]},
        ])
        p, fake, _ = self._provider(inv)
        hist = p.get_broker_flow_history("BRPT", days=10)
        self.assertEqual(len(hist), 10, "hanya `days` entri terbaru")
        self.assertEqual(hist[0]["date"], "2026-07-16")
        self.assertEqual(hist[-1]["date"], "2026-07-25")

    def test_cache_file_ttl_24h_second_call_no_fetch(self):
        inv = _inventory_payload([
            {"broker": "AI", "data": [{"date": "2026-07-13", "value": 1_000_000_000}]},
        ])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(data_invezgo, "_DATA_DIR", tmp.name):
            p, fake, _ = self._provider(inv, tmp=tmp)
            h1 = p.get_broker_flow_history("BRPT", days=20)
            h2 = p.get_broker_flow_history("BRPT", days=20)   # dari cache file
        self.assertEqual(h1, h2)
        inventory_calls = [c for c in fake.analysis.calls if c[0] == "get_inventory_chart_stock"]
        self.assertEqual(len(inventory_calls), 1, "2 call → 1 fetch (cache TTL 24 jam)")
        cache_path = os.path.join(tmp.name, "broker_flow_hist_BRPT.json")
        self.assertTrue(os.path.exists(cache_path), "cache data/broker_flow_hist_BRPT.json")

    def test_error_returns_empty_list_no_crash(self):
        p, fake, _ = self._provider(_inventory_payload([]))
        with mock.patch.object(fake.analysis, "get_inventory_chart_stock",
                               side_effect=RuntimeError("API down")):
            hist = p.get_broker_flow_history("BRPT", days=20)
        self.assertEqual(hist, [], "error → [] — scan tidak boleh crash")

    def test_unknown_response_shape_returns_empty(self):
        p, fake, _ = self._provider([{"label": "D Buy", "value": 0}])  # bukan dict payload
        hist = p.get_broker_flow_history("BRPT", days=20)
        self.assertEqual(hist, [])

    def test_invalid_values_skipped_not_crash(self):
        inv = _inventory_payload([
            {"broker": "AI", "data": [{"date": "2026-07-13", "value": "1.000"},
                                      {"date": "2026-07-14", "value": None},
                                      {"date": "2026-07-15"}]},
        ])
        p, fake, _ = self._provider(inv)
        hist = p.get_broker_flow_history("BRPT", days=20)
        self.assertEqual(len(hist), 1, "hanya tanggal dengan value valid")
        self.assertEqual(hist[0]["net_buy"], 1.0)  # float("1.000") = 1.0


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


# ══════════════════════════════════════════════════════════════════════════
# TEST 8 — R3: weekly_report return_pct di harga EXIT + dedup evaluasi
# ══════════════════════════════════════════════════════════════════════════

def _eval_setup(tmp, perf_rows, sig_date, highs, lows):
    """Tulis perf CSV + buat provider mock; patch path weekly_report."""
    tmp = tmp.name if hasattr(tmp, "name") else tmp
    perf_csv = os.path.join(tmp, "perf_tracker_v7.csv")
    eval_csv = os.path.join(tmp, "evaluations_v7.csv")
    mark_json = os.path.join(tmp, "evaluated_keys.json")
    with open(perf_csv, "w", newline="", encoding="utf-8") as f:
        f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
        for row in perf_rows:
            f.write(row + "\n")
    df = _ohlc(highs=highs, lows=lows, start=sig_date.date())
    return perf_csv, eval_csv, mark_json, MockProvider(df=df)


class TestWeeklyReportR3(unittest.TestCase):
    """R3 — return_pct berbasis harga EXIT & evaluator dedup.

    Bukti audit: INDF LOSS_SL tampil +4.2% (close 7450 vs entry 7150)
    padahal rugi; ISAT WIN_TP tampil +11.86% padahal TP cuma +3.9%;
    BUMI intraday 04/08 & 05/08 (entry 168 sama) dievaluasi 2×.
    """

    def test_win_tp_return_at_tp_not_close(self):
        """WIN_TP: return_pct = (tp-entry)/entry & exit_price = TP, bukan
        return-to-now dari close terakhir (close bisa jatuh di bawah entry)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sig_date = datetime.now() - timedelta(days=11)
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,"
               f"100,90,110,2,20000000,1\n")
        # TP kena hari ke-2 (high 115), close terakhir jatuh ke 95 (< entry)
        highs = [105, 115, 108, 107, 106, 105, 104, 103, 102, 101, 100]
        lows = [95, 96, 97, 98, 99, 100, 99, 98, 97, 96, 95]
        closes = [100, 112, 108, 107, 106, 105, 104, 103, 102, 101, 95]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        # pakai closes yang dibuat khusus
        provider._df["Close"] = closes
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "WIN_TP")
        self.assertEqual(results[0]["exit_price"], 110.0)
        self.assertAlmostEqual(results[0]["return_pct"], 10.0, places=4)
        self.assertEqual(results[0]["close_price"], 95.0,
                         "close_price tetap konteks, bukan dasar return")
        with open(eval_csv, encoding="utf-8") as f:
            eval_rows = list(csv_reader(f))
        self.assertEqual(len(eval_rows), 1)
        self.assertEqual(eval_rows[0]["exit_price"], "110.0",
                         "kolom exit_price terisi di CSV")

    def test_loss_sl_return_at_sl_negative(self):
        """LOSS_SL: return_pct negatif = (sl-entry)/entry & exit_price = SL
        (dulu close terakhir bisa positif → LOSS tampil untung)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sig_date = datetime.now() - timedelta(days=11)
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,"
               f"100,90,110,2,20000000,1\n")
        # Baris ke-2: TP (115) & SL (88) kena SAMA BARIS → konservatif LOSS_SL
        highs = [105, 115, 112, 111, 110, 109, 108, 107, 106, 105, 104]
        lows = [95, 88, 89, 90, 91, 92, 93, 94, 95, 96, 97]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "LOSS_SL")
        self.assertEqual(results[0]["exit_price"], 90.0)
        self.assertAlmostEqual(results[0]["return_pct"], -10.0, places=4)

    def test_eval_csv_schema_migration_exit_price(self):
        """CSV evaluasi lama (tanpa kolom exit_price) dimigrasi: header
        ditambah, baris lama exit_price kosong, baris baru terisi."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        old_header = ("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,"
                      "status,close_price,return_pct,mfe_pct,mae_pct,eval_date,regime")
        with open(eval_csv, "w", newline="", encoding="utf-8") as f:
            f.write(old_header + "\n")
            f.write("2026-07-01 09:00,TLKM,swing,50.0,BUY,4000,3800,4200,1,400000,"
                    "WIN_TP,4200,5.0,5.0,-5.0,2026-07-15,unknown\n")

        sig_date = datetime.now() - timedelta(days=11)
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,"
               f"100,90,110,2,20000000,1\n")
        highs = [105, 115, 108, 107, 106, 105, 104, 103, 102, 101, 100]
        lows = [95, 96, 97, 98, 99, 100, 99, 98, 97, 96, 95]
        perf_csv, _, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results), 1)

        with open(eval_csv, encoding="utf-8") as f:
            eval_rows = list(csv_reader(f))
        self.assertEqual(len(eval_rows), 2)
        self.assertIn("exit_price", eval_rows[0],
                      "header CSV lama harus dimigrasi + exit_price")
        self.assertEqual(eval_rows[0]["exit_price"], "",
                         "baris lama: exit_price kosong")
        self.assertEqual(eval_rows[1]["exit_price"], "110.0",
                         "baris baru: exit_price = TP")

    def test_evaluate_dedup_same_signal_single_eval(self):
        """Dedup dalam satu run: 2 baris BUMI intraday entry sama (±1%,
        jarak 1 hari) → hanya baris TERBARU dievaluasi, 1 duplikat di-skip
        & key-nya di-mark (run berikutnya tidak muncul lagi)."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = datetime.now()
        d_a = (base - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
        d_b = (base - timedelta(days=4)).strftime("%Y-%m-%d %H:%M")
        rows = [
            f"{d_a},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,1",
            f"{d_b},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,0",
        ]
        highs = [170, 182, 180, 179, 178]
        lows = [165, 166, 167, 168, 169]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, rows, base - timedelta(days=5), highs, lows)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)
            self.assertEqual(len(results), 1, "satu sinyal unik → satu evaluasi")
            self.assertEqual(results[0]["date"], d_b,
                             "baris TERBARU yang dipilih dievaluasi")
            self.assertEqual(results[0]["status"], "WIN_TP")
            self.assertEqual(weekly_report_mod._LAST_EVAL_SKIPPED, 1,
                             "1 baris duplikat di-skip")
            # kedua key di-mark → run berikutnya tidak mengevaluasi apa pun
            results2 = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(results2, [], "duplikat tidak muncul lagi")
        with open(eval_csv, encoding="utf-8") as f:
            self.assertEqual(len(list(csv_reader(f))), 1)
        with open(mark_json, encoding="utf-8") as f:
            marked = json.load(f)
        self.assertEqual(len(marked), 2, "key baris yang dievaluasi + duplikat di-mark")

    def test_evaluate_dedup_cross_run_already_evaluated(self):
        """Dedup lintas run: sinyal sudah dievaluasi di run 1; baris duplikat
        (entry sama) yang baru cukup umur di run 2 TIDAK dievaluasi ulang —
        persis kasus BUMI intraday 04/08 (dievaluasi 08/07) & 05/08."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = datetime.now()
        d_a = (base - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
        d_b = (base - timedelta(days=4)).strftime("%Y-%m-%d %H:%M")
        row_a = f"{d_a},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,1"
        highs = [170, 182, 180, 179, 178]
        lows = [165, 166, 167, 168, 169]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row_a], base - timedelta(days=5), highs, lows)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            r1 = weekly_report_mod.evaluate_signals(provider=provider)
            self.assertEqual(len(r1), 1, "run 1: sinyal pertama dievaluasi")
            # run 2: baris duplikat (entry sama) muncul belakangan
            with open(perf_csv, "a", newline="", encoding="utf-8") as f:
                f.write(f"{d_b},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,0\n")
            r2 = weekly_report_mod.evaluate_signals(provider=provider)
            self.assertEqual(r2, [],
                             "duplikat yang sudah dievaluasi run lalu TIDAK dievaluasi ulang")
            self.assertEqual(weekly_report_mod._LAST_EVAL_SKIPPED, 1)
            r3 = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(r3, [], "semua key sudah di-mark")
        with open(eval_csv, encoding="utf-8") as f:
            self.assertEqual(len(list(csv_reader(f))), 1,
                             "hanya 1 baris evaluasi untuk 1 sinyal unik")

    def test_dedup_price_gap_not_duplicate(self):
        """Entry beda > ±1% (BUMI 168 vs 179) = sinyal berbeda → keduanya
        dievaluasi, bukan duplikat."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = datetime.now()
        d_a = (base - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
        d_b = (base - timedelta(days=4)).strftime("%Y-%m-%d %H:%M")
        rows = [
            f"{d_a},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,1",
            f"{d_b},BUMI,intraday,62.7,STRONG_BUY,179.0,168.0,194.0,66,1181400,1",
        ]
        highs = [170, 182, 190, 195, 198]
        lows = [165, 166, 167, 168, 169]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, rows, base - timedelta(days=5), highs, lows)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results), 2,
                         "entry beda > ±1% → 2 sinyal unik, keduanya dievaluasi")
        self.assertEqual(weekly_report_mod._LAST_EVAL_SKIPPED, 0)


# ══════════════════════════════════════════════════════════════════════════
# TEST 9 — R3: signal_manager cooldown PER MODE (swing vs intraday)
# ══════════════════════════════════════════════════════════════════════════

class TestCooldownPerMode(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "cooldown.json")
        self.tr = CooldownTracker(self.db, cooldown_days=5)

    def test_swing_record_does_not_block_intraday(self):
        """R3 inti: record swing → intraday ticker sama TIDAK kena cooldown."""
        self.tr.record("BBCA", "STRONG_BUY", {"score": 70.0}, mode="swing")
        self.assertTrue(self.tr.is_on_cooldown("BBCA", "swing"))
        self.assertFalse(self.tr.is_on_cooldown("BBCA", "intraday"),
                         "record swing tidak boleh memblokir intraday")

    def test_intraday_record_does_not_overwrite_swing(self):
        """Record intraday → slot swing tetap utuh (dulu 1 slot ticker-level
        → record intraday MENIMPA swing)."""
        self.tr.record("BBCA", "STRONG_BUY", {"score": 70.0}, mode="swing")
        self.tr.record("BBCA", "BUY", {"score": 55.0}, mode="intraday")
        self.assertTrue(self.tr.is_on_cooldown("BBCA", "swing"))
        self.assertTrue(self.tr.is_on_cooldown("BBCA", "intraday"))
        info = self.tr.cooldown_info("BBCA", "swing")
        self.assertEqual(info["last_signal"], "STRONG_BUY",
                         "slot swing tidak tertimpa record intraday")

    def test_record_without_mode_backward_compat(self):
        """Pemanggil lama (mode=None, mis. main.py) tetap key level-ticker:
        is_on_cooldown(ticker) & is_on_cooldown(ticker, mode) tidak tercampur."""
        self.tr.record("BBCA", "BUY", {"score": 55.0})
        self.assertTrue(self.tr.is_on_cooldown("BBCA"))
        self.assertFalse(self.tr.is_on_cooldown("BBCA", "swing"))
        self.assertFalse(self.tr.is_on_cooldown("BBCA", "intraday"))
        # DB lama (key level-ticker) tetap terbaca oleh cek tanpa mode
        info = self.tr.cooldown_info("BBCA")
        self.assertEqual(info["last_signal"], "BUY")

    def test_cooldown_expiry_per_mode(self):
        """Masa cooldown berjalan per mode: mode yang lain tidak ikut
        kedaluwarsa/di-blokir oleh record mode lain."""
        self.tr.record("BBCA", "STRONG_BUY", mode="swing")
        self.assertTrue(self.tr.is_on_cooldown("BBCA", "swing"))
        self.assertFalse(self.tr.is_on_cooldown("BBCA", "intraday"))
        self.tr.record("BBCA", "STRONG_BUY", mode="intraday")
        self.assertTrue(self.tr.is_on_cooldown("BBCA", "intraday"))
        self.assertTrue(self.tr.is_on_cooldown("BBCA", "swing"))


# ══════════════════════════════════════════════════════════════════════════
# TEST 10 — R3: v7_scan label signal konsisten dengan score FINAL
# ══════════════════════════════════════════════════════════════════════════

class TestV7ScanLabelConsistency(unittest.TestCase):
    """R3 — kolom score & signal di perf CSV harus dari perhitungan yang
    SAMA: signal dihitung ulang dari score FINAL (sesudah bonus +5) dengan
    threshold v7_engine yang sama (termasuk override config + cap BEARISH)."""

    def _helper(self):
        import v7_scan as vs  # import berat → lazy, hanya saat dibutuhkan
        return vs._signal_from_score

    def test_score_63_ranging_is_strong_buy(self):
        with mock.patch.dict(v7_engine.THRESHOLDS,
                             {"RANGING": [60, 50, 42, 35, 28]}):
            sig = self._helper()
            self.assertEqual(sig(63, "RANGING", "NO_DATA"), "STRONG_BUY",
                             "score 63 (N10: bonus akumulasi +5 sudah dihapus) "
                             "→ STRONG_BUY (>=60)")
            self.assertEqual(sig(58, "RANGING", "NO_DATA"), "BUY",
                             "score 58 → BUY (konsisten)")

    def test_bearish_cap_matches_compute(self):
        with mock.patch.dict(v7_engine.THRESHOLDS,
                             {"RANGING": [60, 50, 42, 35, 28]}):
            sig = self._helper()
            self.assertEqual(sig(63, "RANGING", "BEARISH"), "BUY",
                             "weekly BEARISH → STRONG_BUY di-cap ke BUY (sama dgn compute)")
            self.assertEqual(sig(52, "RANGING", "BULLISH"), "BUY")

    def test_unknown_regime_falls_back_ranging(self):
        with mock.patch.dict(v7_engine.THRESHOLDS,
                             {"RANGING": [60, 50, 42, 35, 28]}):
            sig = self._helper()
            self.assertEqual(sig(63, "REGIME_ANEH", "NO_DATA"), "STRONG_BUY")


# ══════════════════════════════════════════════════════════════════════════
# TEST 10b — N10 (fix noise): gate swing BULL-only, bonus +5 dihapus,
# vol_ratio 1.2, cooldown fallback 2 hari, log skip INFO, risk_amount CSV
# ══════════════════════════════════════════════════════════════════════════

class TestV7ScanNoiseFixes(unittest.TestCase):
    """N10 (fix noise) — verifikasi item P2/P3 di v7_scan.py:
    gate swing akumulasi 48-49 hanya BULL, bonus +5 dihapus, vol_ratio
    intraday 1.2, cooldown fallback 2 hari, log skip INFO, risk_amount."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(_HERE, "v7_scan.py"), encoding="utf-8") as f:
            cls.src = f.read()

    def _vs(self):
        import v7_scan as vs  # import berat → lazy, hanya saat dibutuhkan
        return vs

    # ── Item 7: gate swing — cabang akumulasi 48-49 HANYA di BULL ──
    def test_swing_gate_akumulasi_only_bull(self):
        vs = self._vs()
        self.assertTrue(vs._swing_gate(48, "akumulasi", "BULL"),
                        "BULL skor 48 akumulasi → lolos (WEAK_BUY 48-49)")
        self.assertFalse(vs._swing_gate(48, "akumulasi", "RANGING"),
                         "RANGING skor 48 akumulasi → TIDAK lolos swing")
        self.assertFalse(vs._swing_gate(49, "akumulasi", "HIGH_VOLATILITY"),
                         "HIGH_VOL skor 49 akumulasi → TIDAK lolos")
        self.assertFalse(vs._swing_gate(49, "akumulasi", "BEAR"),
                         "BEAR skor 49 akumulasi → TIDAK lolos")
        self.assertFalse(vs._swing_gate(47, "akumulasi", "BULL"),
                         "BULL skor 47 (di bawah 48) → TIDAK lolos")
        self.assertTrue(vs._swing_gate(50, "akumulasi", "RANGING"),
                        "skor >= 50 selalu lolos (threshold market_mode)")
        self.assertFalse(vs._swing_gate(54, "distribusi", "BULL"),
                         "distribusi skor < 55 ditolak")
        self.assertFalse(vs._swing_gate(53, "netral", "BULL"),
                         "netral skor < 55 ditolak")
        self.assertTrue(vs._swing_gate(56, "netral", "BULL"),
                        "netral skor >= 55 tetap lolos")

    # ── Item 4: bonus akumulasi +5 DIHAPUS ──
    def test_swing_bonus_plus5_removed(self):
        self.assertNotIn("swing_score += 5", self.src,
                         "bonus +5 akumulasi harus dihapus dari v7_scan")
        self.assertNotIn("swing_score = min(100, swing_score)", self.src)
        self.assertIn("swing_score = v7r[\"score\"]", self.src,
                      "score swing = skor final langsung dari v7.compute")

    # ── Item 8: vol_ratio intraday 1.0 → 1.2 ──
    def test_intraday_min_vol_ratio_1_2(self):
        vs = self._vs()
        self.assertEqual(vs.INTRADAY_MIN_VOL_RATIO, 1.2)
        self.assertIn("vol_ratio >= INTRADAY_MIN_VOL_RATIO", self.src)
        self.assertNotIn("vol_ratio >= 1.0", self.src,
                         "syarat vol_ratio lama 1.0 harus diganti 1.2")

    # ── Item 3: cooldown fallback 2 hari (config.yaml di-handle paralel) ──
    def test_cooldown_fallback_days_2(self):
        self.assertIn('cd_cfg.get("days", 2)', self.src,
                      "fallback cooldown days harus 2 (bukan 1)")
        self.assertNotIn('cd_cfg.get("days", 1)', self.src)

    # ── Item 9: log skip INFO (jejak audit) ──
    def test_skip_info_logs_present(self):
        self.assertIn('logger.info("Skip', self.src,
                      "skip per-ticker harus punya jejak INFO")
        self.assertIn('logger.info("Watchlist disable', self.src,
                      "watchlist disable harus di-log INFO per ticker")

    # ── Item 10: risk_amount di logged_signals (swing & intraday) ──
    def test_risk_amount_in_logged_signals(self):
        self.assertIn('"risk_amount": int(sz.get("risk_amount", 0) or 0)', self.src)
        self.assertIn('"risk_amount": int(sz2.get("risk_amount", 0) or 0)', self.src)


class TestCooldownTwoDays(unittest.TestCase):
    """N10 (P2) — cooldown 2 hari: streak sinyal harian terpotong (sebelumnya
    days=1 → sinyal bisa muncul tiap malam, BUMI 8 hari berturut)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = os.path.join(self._tmp.name, "cooldown.json")
        self.tr = CooldownTracker(self.db, cooldown_days=2)

    def test_2day_cooldown_breaks_daily_streak(self):
        days = []
        for i in range(8):
            with mock.patch("signal_manager.datetime") as mdt:
                mdt.now.return_value = datetime(2026, 8, 1 + i, 21, 0, 0)
                mdt.strptime.side_effect = datetime.strptime
                if not self.tr.is_on_cooldown("BUMI", "swing"):
                    days.append(i)
                    self.tr.record("BUMI", "STRONG_BUY", mode="swing")
        self.assertEqual(days, [0, 2, 4, 6],
                         "cooldown 2 hari → sinyal tiap 2 hari, bukan tiap malam")
        self.assertLessEqual(len(days), 4,
                             "streak 8 hari terpotong jadi maks 4 kemunculan")


# ══════════════════════════════════════════════════════════════════════════
# TEST 11 — R4: fix audit Round 4 (unit-bug quality_gate, Wilder sentiment,
# header file 0-byte, DATA_MISSING retry limit, group_of shadowing,
# flag _sector_capped)
# ══════════════════════════════════════════════════════════════════════════

class TestQualityGateUnits(unittest.TestCase):
    """R4 — quality_gate: ret_20d dalam satuan FRAKSI (0.05 = -5%).
    Dulu dibandingkan dgn -3.0 / 8.0 (persen) → falling-knife &
    false-breakout tidak pernah terpenuhi utk data riil (fraksi)."""

    def _row(self, ret_20d, rsi=50.0, vol_ratio=1.0, adx=25.0,
             atr=1.0, close=100.0):
        return {"rsi": rsi, "vol_ratio": vol_ratio, "ret_20d": ret_20d,
                "adx": adx, "atr": atr, "close": close}

    def test_falling_knife_fraction_units(self):
        from scoring import quality_gate
        # ret_20d = -5% (fraksi -0.05) + RSI 30 + volume spike → SELL
        row = self._row(ret_20d=-0.05, rsi=30.0, vol_ratio=2.0)
        self.assertEqual(quality_gate(row, "BUY"), "SELL",
                         "-5% + RSI 30 + vol 2x harus falling knife → SELL")
        self.assertEqual(quality_gate(row, "STRONG_BUY"), "SELL")

    def test_falling_knife_not_triggered_below_threshold(self):
        from scoring import quality_gate
        # -2% (fraksi -0.02) di atas ambang -3% → BUKAN falling knife
        row = self._row(ret_20d=-0.02, rsi=30.0, vol_ratio=2.0)
        self.assertEqual(quality_gate(row, "BUY"), "BUY")

    def test_false_breakout_fraction_units(self):
        from scoring import quality_gate
        # ret_20d = +10% (fraksi 0.10) + volume di bawah rata-rata → downgrade
        row = self._row(ret_20d=0.10, rsi=60.0, vol_ratio=0.5)
        self.assertEqual(quality_gate(row, "STRONG_BUY"), "BUY",
                         "+10% + vol rendah harus false breakout → downgrade")
        self.assertEqual(quality_gate(row, "BUY"), "WEAK_BUY")

    def test_false_breakout_not_triggered_below_threshold(self):
        from scoring import quality_gate
        # +7% (fraksi 0.07) di bawah ambang 8% → BUKAN false breakout
        row = self._row(ret_20d=0.07, rsi=60.0, vol_ratio=0.5)
        self.assertEqual(quality_gate(row, "STRONG_BUY"), "STRONG_BUY")


class TestSectorCapFlag(unittest.TestCase):
    """R4 — apply_sector_cap: flag _sector_capped di-set utk saham yang
    di-cap (dulu `if h.get(...)` terbalik → flag tidak pernah di-set,
    padahal main.py pop() & memakainya)."""

    def test_capped_stock_flagged(self):
        from signal_manager import apply_sector_cap
        hasil = [
            {"ticker": "A", "sector": "Bank", "signal": "BUY", "score": 80},
            {"ticker": "B", "sector": "Bank", "signal": "BUY", "score": 70},
            {"ticker": "C", "sector": "Bank", "signal": "BUY", "score": 60},
        ]
        apply_sector_cap(hasil, max_per_sector=2)
        capped = [h for h in hasil if h.get("_sector_capped")]
        self.assertEqual(len(capped), 1, "hanya saham ke-3 yang di-cap")
        self.assertEqual(capped[0]["ticker"], "C")
        self.assertEqual(capped[0]["signal"], "HOLD")
        for h in hasil[:2]:
            self.assertFalse(h.get("_sector_capped", False),
                             "saham yang lolos cap tidak boleh di-flag")


class TestWeeklyReportZeroByteHeader(unittest.TestCase):
    """R4 — file evaluations 0-byte dihitung BARU → baris pertama = header
    (dulu new_file hanya cek exists() → baris data tanpa header)."""

    def _row(self):
        return {k: f"v{i}" for i, k in enumerate(weekly_report_mod.FIELDS)}

    def test_append_eval_zero_byte_file_gets_header(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        open(eval_csv, "w").close()  # 0 byte
        with mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv):
            weekly_report_mod._append_eval(self._row())
        with open(eval_csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(weekly_report_mod.FIELDS),
                         "file 0-byte → baris pertama harus header")
        self.assertEqual(len(lines), 2)

    def test_append_eval_new_file_gets_header(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        eval_csv = os.path.join(tmp.name, "evaluations_v7.csv")
        with mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv):
            weekly_report_mod._append_eval(self._row())
        with open(eval_csv, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(weekly_report_mod.FIELDS))


class TestWeeklyReportDataMissingLimit(unittest.TestCase):
    """R4 — sinyal DATA_MISSING dicoba maks DATA_MISSING_MAX_ATTEMPTS run
    lalu key di-mark (dulu tidak pernah di-mark → peringatan selamanya)."""

    def _setup(self, tmp):
        perf_csv = os.path.join(tmp, "perf_tracker_v7.csv")
        eval_csv = os.path.join(tmp, "evaluations_v7.csv")
        mark_json = os.path.join(tmp, "evaluated_keys.json")
        attempts_json = os.path.join(tmp, "data_missing_attempts.json")
        sig_date = datetime.now() - timedelta(days=11)
        with open(perf_csv, "w", newline="", encoding="utf-8") as f:
            f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh\n")
            f.write(f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,100,90,110,2,20000000,1\n")
        return perf_csv, eval_csv, mark_json, attempts_json

    def _patched(self, perf_csv, eval_csv, mark_json, attempts_json):
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv))
        stack.enter_context(mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv))
        stack.enter_context(mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json))
        stack.enter_context(mock.patch.object(weekly_report_mod, "MISSING_ATTEMPTS_FILE", attempts_json))
        return stack

    def test_marked_after_max_attempts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        perf_csv, eval_csv, mark_json, attempts_json = self._setup(tmp.name)
        provider = MockProvider(df=pd.DataFrame())  # get_historical → kosong
        max_att = weekly_report_mod.DATA_MISSING_MAX_ATTEMPTS
        for _ in range(max_att):
            with self._patched(perf_csv, eval_csv, mark_json, attempts_json):
                results = weekly_report_mod.evaluate_signals(provider=provider)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["status"], "DATA_MISSING")
        # Setelah limit → key di-mark → run berikutnya tidak mencoba lagi
        with self._patched(perf_csv, eval_csv, mark_json, attempts_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(results, [], "key sudah di-mark → tidak di-retry lagi")
        with open(mark_json, encoding="utf-8") as f:
            marked = json.load(f)
        self.assertEqual(len(marked), 1, "key DATA_MISSING harus tercatat di-mark")

    def test_dry_run_does_not_increment_attempts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        perf_csv, eval_csv, mark_json, attempts_json = self._setup(tmp.name)
        provider = MockProvider(df=pd.DataFrame())
        with self._patched(perf_csv, eval_csv, mark_json, attempts_json):
            results = weekly_report_mod.evaluate_signals(provider=provider, dry_run=True)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "DATA_MISSING")
        self.assertFalse(os.path.exists(attempts_json), "dry-run tidak menulis counter")
        self.assertFalse(os.path.exists(mark_json), "dry-run tidak menulis mark")


class TestMarketSentimentWilder(unittest.TestCase):
    """R4 — market_sentiment memakai Wilder smoothing (alpha=1/14) konsisten
    dgn data.py compute_all_indicators, bukan rolling-mean Cutler/SMA14."""

    @staticmethod
    def _df(n=80):
        import numpy as np
        rng = np.random.default_rng(42)
        close = 7000 + np.cumsum(rng.normal(0, 30, n))
        high = close + rng.uniform(5, 40, n)
        low = close - rng.uniform(5, 40, n)
        vol = rng.uniform(1e9, 3e9, n)
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame({"open": close, "high": high, "low": low,
                             "close": close, "volume": vol}, index=idx)

    def test_rsi_adx_match_wilder_reference(self):
        import numpy as np
        import market_sentiment as ms
        df = self._df()
        out = ms._compute_indicators(df.copy())

        # Referensi RSI Wilder independen (alpha=1/14)
        delta = df["close"].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta.where(delta < 0, 0.0))
        avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi_ref = 100 - (100 / (1 + rs))
        rsi_ref = rsi_ref.where(avg_loss != 0, 100.0).where(avg_gain != 0, 0.0)
        self.assertAlmostEqual(out["rsi"].iloc[-1], rsi_ref.iloc[-1], places=6)
        # Bukan lagi Cutler (SMA14) — nilai harus beda nyata
        rsi_cutler = 100 - (100 / (1 + gain.rolling(14).mean()
                                   / loss.rolling(14).mean().replace(0, np.nan)))
        self.assertGreater(abs(out["rsi"].iloc[-1] - rsi_cutler.iloc[-1]), 0.5,
                           "RSI harus Wilder, bukan rolling-mean Cutler")

        # Referensi ADX Wilder independen
        prev_close = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"],
                        (df["high"] - prev_close).abs(),
                        (df["low"] - prev_close).abs()], axis=1).max(axis=1)
        atr_ref = tr.ewm(alpha=1/14, adjust=False).mean()
        up = df["high"].diff()
        dn = -df["low"].diff()
        pdm = ((up > dn) & (up > 0)).astype(float) * up
        mdm = ((dn > up) & (dn > 0)).astype(float) * dn
        pdi = 100 * (pdm.ewm(alpha=1/14, adjust=False).mean() / atr_ref.replace(0, np.nan))
        mdi = 100 * (mdm.ewm(alpha=1/14, adjust=False).mean() / atr_ref.replace(0, np.nan))
        dx = 100 * ((pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan))
        adx_ref = dx.ewm(alpha=1/14, adjust=False).mean()
        self.assertAlmostEqual(out["adx"].iloc[-1], adx_ref.iloc[-1], places=6)

    def test_predict_sentiment_returns_valid_dict(self):
        import market_sentiment as ms
        res = ms.predict_market_sentiment(self._df())
        self.assertIn(res["sentiment"], ("GREEN", "YELLOW", "RED"))
        self.assertTrue(res["reason"])


class TestV7ScanGroupOf(unittest.TestCase):
    """R4 — v7_scan.group_of tidak lagi men-shadow import groups_config:
    aman utk ticker None (dulu AttributeError) & label sama dgn sumber
    tunggal."""

    def _vs(self):
        import v7_scan as vs  # import berat → lazy, hanya saat dibutuhkan
        return vs

    def test_group_of_none_safe(self):
        vs = self._vs()
        self.assertEqual(vs.group_of(None), "")
        self.assertEqual(vs.group_of(""), "")

    def test_group_of_matches_groups_config(self):
        import groups_config
        vs = self._vs()
        for t in ("BRPT", "brpt", "BUMI", "INDF", "UNKNOWN"):
            self.assertEqual(vs.group_of(t), groups_config.group_of(t))
        self.assertEqual(vs.group_of("BRPT"), "Barito")


# ══════════════════════════════════════════════════════════════════════════
# VERIFIKASI FASE 2 — IDE6 risk_amount sejati, guard total risk, CA blackout,
# Faktor DNA (IDE1)
# ══════════════════════════════════════════════════════════════════════════

class TestPositionSizingRiskAmount(unittest.TestCase):
    """IDE6 — risk_amount = risiko SEJATI (entry−SL)/entry×cost; SL invalid
    (0 / >= harga) → fallback 5% cost (perilaku lama)."""

    def test_sl_9pct_risk_amount_is_true_risk(self):
        cap, price, score = 20_000_000, 1000.0, 75.0
        sz = position_sizing(cap, price, score, atr_pct=1.0, sl=910.0)
        # risk_frac = (1000−910)/1000 = 0.09 → 9% dari cost (bukan 5% flat)
        self.assertEqual(sz["risk_amount"], int(sz["cost"] * 0.09),
                         "SL 9% di bawah harga → risk_amount harus 9% cost")

    def test_sl_zero_fallback_5pct(self):
        cap, price, score = 20_000_000, 1000.0, 75.0
        sz = position_sizing(cap, price, score, atr_pct=1.0, sl=0.0)
        self.assertEqual(sz["risk_amount"], int(sz["cost"] * 0.05),
                         "SL tidak tersedia (0) → fallback 5% cost")

    def test_sl_above_price_fallback_5pct(self):
        cap, price, score = 20_000_000, 1000.0, 75.0
        sz = position_sizing(cap, price, score, atr_pct=1.0, sl=1050.0)
        self.assertEqual(sz["risk_amount"], int(sz["cost"] * 0.05),
                         "SL >= harga (invalid) → fallback 5% cost")


class TestV7RiskAndCaGuards(unittest.TestCase):
    """Verifikasi fase 2: enforce_total_risk_guard (IDE6) + CA calendar
    blackout (IDE5) + clamp fraksi N8 di kedua guard."""

    @classmethod
    def setUpClass(cls):
        # Import v7_scan tanpa menulis file log data/screener.log (pola
        # TestC2ConcentrationGuard) — FileHandler diganti null inert.
        class _NullHandler:
            level = 1000
            def setFormatter(self, *a, **k):
                return None
        with mock.patch("logging.FileHandler", _NullHandler):
            import v7_scan as v7s
        cls.v7s = v7s

    def _sig(self, tkr, price, lots, risk_frac=0.05):
        """Sinyal swing sintetis — sizing dengan risk_amount sesuai fraksi."""
        cost = lots * price * 100
        return {
            "tkr": tkr, "score": 70.0, "price": price,
            "exit": {"stop_loss": int(price * 0.9), "take_profit": int(price * 1.2),
                     "rrr": 2.0},
            "sizing": {"lots": lots, "cost": cost, "pct_modal": 15.0,
                       "risk_amount": int(cost * risk_frac)},
            "bf": "akumulasi", "ff": "net_buy", "weekly": "BULLISH",
            "brokers": "", "entry_rec": {"method": "Limit", "price_range": "-"},
            "group": "Bakrie",
        }

    def _logged(self, tickers, mode="swing", lots=0, price=0.0):
        return [{"ticker": t, "mode": mode, "lots": lots, "cost": lots * price * 100}
                for t in tickers]

    # ── enforce_total_risk_guard (IDE6) ──

    def test_total_risk_5_positions_over_3pct_lots_reduced(self):
        """5 posisi @ risk 0.75% modal (total 3.75% > 3%) → lot diturunkan
        bertahap sampai total risk ≤ limit."""
        CAP = 20_000_000
        price = 2000.0
        lots = 15  # cost 3jt = 15% modal, risk 5% cost = 150rb (0.75% modal)
        swing = [self._sig(f"B{t}", price, lots) for t in range(5)]
        logged = self._logged([f"B{t}" for t in range(5)], lots=lots, price=price)
        warns = self.v7s.enforce_total_risk_guard(swing, [], logged, CAP, max_risk_pct=3.0)

        self.assertTrue(warns, "harus ada peringatan TOTAL RISK")
        self.assertIn("TOTAL RISK", warns[0])
        total = sum(s["sizing"]["risk_amount"] for s in swing)
        self.assertLessEqual(total, CAP * 0.03 + 1, "total risk ≤ 3% modal setelah guard")
        self.assertTrue(any(s["sizing"]["lots"] < lots for s in swing),
                        "ada lot yang diturunkan")
        self.assertTrue(all(s["sizing"]["lots"] >= 1 for s in swing),
                        "tidak ada lot < 1")
        self.assertEqual(sum(s["sizing"]["cost"] for s in swing),
                         sum(l["cost"] for l in logged),
                         "logged_signals sinkron dengan sizing")

    def test_total_risk_normal_noop(self):
        """1 posisi risk 0.75% modal ≤ 3% → no-op total (tidak ada warning,
        lot tidak berubah)."""
        CAP = 20_000_000
        price = 2000.0
        swing = [self._sig("BBCA", price, 15)]
        warns = self.v7s.enforce_total_risk_guard(swing, [], [], CAP, max_risk_pct=3.0)
        self.assertEqual(warns, [])
        self.assertEqual(swing[0]["sizing"]["lots"], 15)

    def test_total_risk_dedup_ticker_two_modes(self):
        """Ticker di swing+intraday dihitung SEKALI (pakai risk lebih besar):
        X swing 500rb + X intraday 100rb + Y swing 100rb → dedup total 600rb
        = limit 3% → no-op. Tanpa dedup 700rb > 600rb pasti kena reduksi."""
        CAP = 20_000_000
        price = 2000.0
        x_swing = self._sig("BBCA", price, 15)
        x_swing["sizing"]["risk_amount"] = 500_000
        x_intra = self._sig("BBCA", price, 10)
        x_intra["sizing"]["risk_amount"] = 100_000
        y_swing = self._sig("BBRI", price, 10)
        y_swing["sizing"]["risk_amount"] = 100_000
        warns = self.v7s.enforce_total_risk_guard(
            [x_swing, y_swing], [x_intra], [], CAP, max_risk_pct=3.0)
        self.assertEqual(warns, [], "dedup ticker → total 600rb ≤ limit → no-op")
        self.assertEqual(x_swing["sizing"]["lots"], 15,
                         "sinyal X(swing) tidak boleh disentuh")
        self.assertEqual(x_intra["sizing"]["lots"], 10,
                         "mode alternatif (intraday) tidak boleh disentuh")

    def test_total_risk_overstated_risk_amount_clamped(self):
        """N8 — risk_amount overstate (999_999_999, pemanggil lama) → fraksi
        di-clamp ke 5% cost (bukan 333x cost) saat lot diturunkan."""
        CAP = 20_000_000
        price = 2000.0
        swing = [self._sig(f"B{t}", price, 15) for t in range(5)]
        for s in swing:
            s["sizing"]["risk_amount"] = 999_999_999
        warns = self.v7s.enforce_total_risk_guard(swing, [], [], CAP, max_risk_pct=3.0)
        self.assertTrue(warns)
        for s in swing:
            self.assertLessEqual(s["sizing"]["risk_amount"], int(s["sizing"]["cost"] * 0.5),
                                 "risk_amount hasil guard tidak boleh > 50% cost")

    # ── CA calendar blackout (IDE5) ──

    @staticmethod
    def _dstr(days):
        return (datetime.now().date() + timedelta(days=days)).strftime("%Y-%m-%d")

    class _FakeCalendar:
        def __init__(self, events):
            self._events = events
            self.raises = False
        def get_corporate_calendar(self, tkr):
            if self.raises:
                raise RuntimeError("kalender mati")
            return self._events

    def test_ca_blackout_rups_blocks_signal_with_reason(self):
        """Event RUPS H+3 dalam horizon → blocked; alur main() men-skip
        sinyal dengan alasan '⚠️ CA BLACKOUT'."""
        ip = self._FakeCalendar([{"date": self._dstr(3), "type": "RUPS"}])
        blocked, label = self.v7s._ca_calendar_check(ip, "BBCA", 7)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], "RUPS")
        self.assertIn("RUPS", label)
        # Alur skip di v7_scan.main() (baris CA blackout):
        swing_ok = intra_ok = True
        skip_reasons = []
        if blocked:
            skip_reasons.append(
                f"⚠️ CA BLACKOUT: BBCA {blocked[0]} {blocked[1]} — skip (H+7)")
            swing_ok = False
            intra_ok = False
        self.assertFalse(swing_ok and intra_ok, "sinyal harus di-skip")
        self.assertIn("⚠️ CA BLACKOUT", skip_reasons[0])
        self.assertIn("RUPS", skip_reasons[0])

    def test_ca_blackout_no_event_normal(self):
        """Tanpa event → (None, '') → sinyal TIDAK di-skip."""
        ip = self._FakeCalendar([])
        blocked, label = self.v7s._ca_calendar_check(ip, "BBCA", 7)
        self.assertIsNone(blocked)
        self.assertEqual(label, "")
        swing_ok = intra_ok = True
        if blocked:
            swing_ok = intra_ok = False
        self.assertTrue(swing_ok and intra_ok, "tanpa event = normal, tidak skip")

    def test_ca_blackout_provider_exception_no_raise(self):
        """Calendar gagal (exception) → no-op, TIDAK pernah raise."""
        ip = self._FakeCalendar([])
        ip.raises = True
        blocked, label = self.v7s._ca_calendar_check(ip, "BBCA", 7)
        self.assertIsNone(blocked)
        self.assertEqual(label, "")

    def test_ca_blackout_event_outside_horizon_label_only(self):
        """Event di luar H+7 → sinyal TIDAK diblokir, label event tetap
        tercatat (kolom event perf CSV)."""
        ip = self._FakeCalendar([{"date": self._dstr(10), "type": "SPLIT"}])
        blocked, label = self.v7s._ca_calendar_check(ip, "BBCA", 7)
        self.assertIsNone(blocked)
        self.assertTrue(label.startswith("SPLIT"))

    def test_ca_blackout_nearest_public_expose_not_blocking(self):
        """PUBLIC_EXPOSE terdekat (tidak memblokir) + RUPS dalam horizon →
        blocked = RUPS, label = event TERDEKAT (unpacking tuple benar)."""
        ip = self._FakeCalendar([
            {"date": self._dstr(1), "type": "PUBLIC_EXPOSE"},
            {"date": self._dstr(5), "type": "RUPS_RESULT"},
        ])
        blocked, label = self.v7s._ca_calendar_check(ip, "BBCA", 7)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked[0], "RUPS_RESULT")
        self.assertTrue(label.startswith("PUBLIC_EXPOSE"),
                        "label harus event terdekat (PUBLIC_EXPOSE)")


class TestFaktorDnaLogging(unittest.TestCase):
    """IDE1 — log_signal mencatat kolom faktor DNA; CSV lama di-migrasi
    (header ditambah + baris lama di-backfill 'unknown'/'event')."""

    def test_log_signal_with_factors_columns_filled(self):
        with tempfile.TemporaryDirectory(prefix="dna_") as td:
            csvp = os.path.join(td, "perf.csv")
            ok = log_signal(
                csvp, ticker="BBRI", mode="swing", score=62.0, signal="BUY",
                entry_price=5000, sl=4750, tp=5500, lots=5, cost=2500000,
                regime="BULL", broker_flow=72.0, foreign_flow=58.0,
                fundamental=65.0, earnings_momentum=40.0,
                weekly_trend="BULLISH", atr_pct=2.3, vol_ratio=1.8,
                event="DIVIDEND 12/08")
            self.assertTrue(ok)
            rows = load_signals(csvp)
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual(r["broker_flow"], "72.0")
            self.assertEqual(r["foreign_flow"], "58.0")
            self.assertEqual(r["fundamental"], "65.0")
            self.assertEqual(r["earnings_momentum"], "40.0")
            self.assertEqual(r["weekly_trend"], "BULLISH")
            self.assertEqual(r["atr_pct"], "2.3")
            self.assertEqual(r["vol_ratio"], "1.8")
            self.assertEqual(r["event"], "DIVIDEND 12/08")

    def test_old_csv_migrated_backfill_unknown(self):
        """CSV lama tanpa kolom faktor → log_signal baru memicu migrasi
        header; baris lama di-backfill 'unknown' (event → '')."""
        with tempfile.TemporaryDirectory(prefix="dna_mig_") as td:
            csvp = os.path.join(td, "perf.csv")
            with open(csvp, "w", newline="", encoding="utf-8") as f:
                f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh,regime\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,10000,9500,11000,2,2000000,1,unknown\n")
            ok = log_signal(
                csvp, ticker="BBRI", mode="swing", score=62.0, signal="BUY",
                entry_price=5000, sl=4750, tp=5500, lots=5, cost=2500000,
                regime="BULL", broker_flow=72.0, weekly_trend="BULLISH",
                atr_pct=2.3, vol_ratio=1.8, event="DIVIDEND 12/08")
            self.assertTrue(ok)
            rows = load_signals(csvp)
            self.assertEqual(len(rows), 2, "baris lama + baris baru")
            old, new = rows[0], rows[1]
            for c in ("broker_flow", "broker_trend", "foreign_flow", "fundamental",
                      "earnings_momentum", "weekly_trend", "atr_pct", "vol_ratio"):
                self.assertEqual(old[c], "unknown", f"baris lama {c} = 'unknown'")
            self.assertEqual(old["event"], "", "baris lama event = ''")
            self.assertEqual(new["broker_flow"], "72.0")
            self.assertEqual(new["event"], "DIVIDEND 12/08")

    def test_broker_trend_column_written_and_backfilled(self):
        """IDE4 — kolom broker_trend (faktor DNA baru) ditulis log_signal;
        default migrasi 'unknown' (sama seperti kolom faktor lain)."""
        self.assertIn("broker_trend", FIELDS, "FIELDS perf_tracker harus punya broker_trend")
        self.assertEqual(FIELD_DEFAULTS["broker_trend"], "unknown")
        with tempfile.TemporaryDirectory(prefix="bt_dna_") as td:
            csvp = os.path.join(td, "perf.csv")
            ok = log_signal(
                csvp, ticker="BRPT", mode="swing", score=62.0, signal="BUY",
                entry_price=5000, sl=4750, tp=5500, lots=5, cost=2500000,
                regime="BULL", broker_flow=72.0, broker_trend=88.5,
                weekly_trend="BULLISH", atr_pct=2.3, vol_ratio=1.8)
            self.assertTrue(ok)
            rows = load_signals(csvp)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["broker_trend"], "88.5")
            self.assertEqual(rows[0]["broker_flow"], "72.0")
            # Pemanggil lama (tanpa broker_trend) → kolom tetap ada, isi ''
            csvp2 = os.path.join(td, "perf2.csv")
            log_signal(csvp2, ticker="BBCA", mode="swing", score=60.0, signal="BUY",
                       entry_price=10000, sl=9500, tp=11000, lots=2, cost=2000000)
            rows2 = load_signals(csvp2)
            self.assertEqual(rows2[0]["broker_trend"], "",
                             "pemanggil lama tanpa broker_trend → kolom kosong")


# ══════════════════════════════════════════════════════════════════════════
# IDE1 — FLOW REGIME CONFLICT CAP (snapshot akumulasi vs trend distribusi)
# ══════════════════════════════════════════════════════════════════════════

class TestFlowRegimeConflictCap(unittest.TestCase):
    """IDE1 — snapshot broker 3 hari akumulasi > 65 di tengah trend 20 hari
    distribusi < 35 → kontribusi broker_flow di-cap PARSIAL netral 50 +
    detail conflict + warning (BUKAN veto):
      a. konflik → broker_flow = 50, broker_flow_raw = 75, flag True, detail
      b. trend akumulasi + snapshot netral → TIDAK di-cap
      c. snapshot > 65 + trend >= 35 → normal (tidak di-cap)
      d. awal akumulasi (spike 3d, trend masih netral 50 karena cap flow_spike)
         → TIDAK kena cap
      e. cap parsial: selisih skor = bobot 0.20 × (75 − 50) = 5 poin,
         bukan veto (skor tetap dihitung, hanya kontribusi snapshot di-cap)"""

    def setUp(self):
        self._old_enabled = v7_engine.enabled
        v7_engine.enabled = True

    def tearDown(self):
        v7_engine.enabled = self._old_enabled

    def _compute(self, bf_score, bt_score, bf_detail="akumulasi_12.3B",
                 bt_detail="trend 20d -5.0B", weekly="NO_DATA"):
        base = {"score": 50, "detail": "x"}
        with mock.patch.object(v7_engine, "factor_broker_flow",
                               return_value={"score": bf_score, "detail": bf_detail,
                                             "brokers": "🔵DB(+1B)"}), \
             mock.patch.object(v7_engine, "factor_foreign_flow", return_value=dict(base)), \
             mock.patch.object(v7_engine, "factor_fundamental_quality", return_value=dict(base)), \
             mock.patch.object(v7_engine, "factor_earnings_momentum", return_value=dict(base)), \
             mock.patch.object(v7_engine, "factor_broker_trend",
                               return_value={"score": bt_score, "detail": bt_detail,
                                             "flow_spike": False}):
            return v7_engine.compute("BNBR", 50.0, "RANGING", weekly_trend=weekly)

    def test_a_conflict_snapshot_accum_trend_distribution_capped(self):
        r = self._compute(bf_score=75, bt_score=30)
        f = r["factors"]
        self.assertEqual(f["broker_flow"], 50, "kontribusi snapshot di-cap netral 50")
        self.assertEqual(f["broker_flow_raw"], 75, "skor asli tetap di-audit")
        self.assertTrue(f["conflict_snapshot_vs_trend"])
        self.assertIn("conflict snapshot vs trend", f["broker_detail"])
        self.assertIn("waspada distribusi", f["broker_detail"])

    def test_b_trend_accum_snapshot_neutral_not_capped(self):
        r = self._compute(bf_score=50, bt_score=80, bt_detail="trend 20d +5.0B streak5")
        f = r["factors"]
        self.assertEqual(f["broker_flow"], 50, "snapshot netral tidak di-cap (bukan konflik)")
        self.assertFalse(f["conflict_snapshot_vs_trend"])
        self.assertNotIn("conflict", f["broker_detail"])

    def test_c_snapshot_accum_trend_ok_normal(self):
        r = self._compute(bf_score=75, bt_score=60, bt_detail="trend 20d +3.0B")
        f = r["factors"]
        self.assertEqual(f["broker_flow"], 75, "trend >= 35 → akumulasi snapshot normal")
        self.assertFalse(f["conflict_snapshot_vs_trend"])
        self.assertNotIn("conflict", f["broker_detail"])

    def test_d_early_accumulation_spike_not_vetoed(self):
        # Awal akumulasi baru: trend di-cap 50 oleh flow_spike (L2-A) atau
        # masih 40 — snapshot akumulasi TIDAK boleh kena cap/veto.
        for bt in (50, 40):
            r = self._compute(bf_score=75, bt_score=bt)
            self.assertFalse(r["factors"]["conflict_snapshot_vs_trend"],
                             f"trend {bt} (awal tren) → tidak conflict")
            self.assertEqual(r["factors"]["broker_flow"], 75,
                             f"trend {bt} → snapshot tetap dihitung penuh")

    def test_e_partial_cap_score_delta_5_points(self):
        # Konflik: bf 75 → dipakai 50 (selisih 25 × 0.20 = 5 poin).
        # Bukti 1: dengan trend sama (30), bf 75 yang di-cap memberi skor
        # IDENTIK dengan bf 50 (kontribusi benar-benar 50, bukan veto).
        r_cap = self._compute(bf_score=75, bt_score=30)   # conflict → bf 75 dipakai 50
        r_ref = self._compute(bf_score=50, bt_score=30)   # netral → bf 50
        self.assertAlmostEqual(r_cap["score"], r_ref["score"], places=6,
                               msg="bf 75 saat konflik = bf 50 (cap parsial ke 50)")
        # Bukti 2: vs skenario normal (bf 75, trend 60) selisih total 8.0 =
        # 5.0 (cap broker_flow) + 3.0 (beda broker_trend 30 poin × 0.10).
        r_normal = self._compute(bf_score=75, bt_score=60)
        self.assertAlmostEqual(r_normal["score"] - r_cap["score"], 8.0,
                               places=6,
                               msg="5.0 cap broker_flow + 3.0 beda broker_trend")

    def test_boundaries_35_and_65(self):
        # trend tepat 35 → TIDAK konflik (syarat < 35); snapshot tepat 65 → TIDAK
        self.assertFalse(self._compute(bf_score=75, bt_score=35)["factors"]["conflict_snapshot_vs_trend"])
        self.assertFalse(self._compute(bf_score=65, bt_score=30)["factors"]["conflict_snapshot_vs_trend"])
        self.assertTrue(self._compute(bf_score=66, bt_score=34.9)["factors"]["conflict_snapshot_vs_trend"])


class TestConflictWarningsV7Scan(unittest.TestCase):
    """IDE1 — conflict_warnings di v7_scan: maks 3, top skor, dedup ticker
    antar mode, tampil di section MANAJEMEN RISIKO Telegram (pola
    flow_spike_warnings)."""

    @classmethod
    def setUpClass(cls):
        class _NullHandler:
            level = 1000
            def setFormatter(self, *a, **k):
                return None
        with mock.patch("logging.FileHandler", _NullHandler):
            import v7_scan as v7s
        cls.v7s = v7s

    def _sig(self, tkr, score, conflict=True):
        return {"tkr": tkr, "score": score,
                "conflict_snapshot_vs_trend": conflict}

    def test_top3_only_and_dedup_across_modes(self):
        swing = [self._sig("BNBR", 70.0), self._sig("BUMI", 60.0)]
        intra = [self._sig("BUMI", 75.0), self._sig("ASII", 50.0), self._sig("TPIA", 40.0)]
        warns = self.v7s.conflict_warnings(swing, intra, max_n=3)
        self.assertEqual(len(warns), 3, "cap 3 warning (top skor)")
        self.assertEqual(warns[0].count("BUMI"), 1, "BUMI dihitung sekali (skor tertinggi 75)")
        self.assertIn("BNBR", warns[1])
        self.assertIn("ASII", warns[2])
        self.assertNotIn("TPIA", " ".join(warns), "skor terendah dibuang")
        for w in warns:
            self.assertIn("CONFLICT FLOW", w)
            self.assertIn("waspada jebakan distribusi", w)

    def test_no_conflict_no_warnings(self):
        swing = [self._sig("BNBR", 70.0, conflict=False), self._sig("BUMI", 60.0, conflict=False)]
        self.assertEqual(self.v7s.conflict_warnings(swing, []), [])
        self.assertEqual(self.v7s.conflict_warnings([], []), [])

    def test_warnings_render_in_telegram_risk_section(self):
        warns = [
            "⚠️ CONFLICT FLOW: BNBR snapshot akumulasi vs trend distribusi 20d — "
            "kontribusi broker flow di-cap netral (waspada jebakan distribusi)",
        ]
        msg = telegram_formatter.format_message([], [], capital=20_000_000,
                                                concentration_warnings=warns)
        self.assertIn("⚠️ Warning: Conflict flow BNBR snapshot akumulasi", msg)
        self.assertIn("waspada jebakan distribusi", msg)


# ══════════════════════════════════════════════════════════════════════════
# IDE2 — FOREIGN FLOW: snapshot investor='f' (net asing sejati) + fallback
# ══════════════════════════════════════════════════════════════════════════

class TestForeignFlowFactor(unittest.TestCase):
    """IDE2 — factor_foreign_flow memakai snapshot investor='f' (net asing
    SEJATI via cache broker_flow_foreign_{CODE}.json) — skor bervariasi
    mengikuti logika factor_broker_flow (akumulasi besar → skor tinggi),
    bukan statis 50; fallback daftar kode broker DIPERBAIKI (AG/RG/CS dibuang,
    AI=UOB Kay Hian ditambahkan)."""

    def setUp(self):
        v7_engine._foreign_mem_cache.clear()
        v7_engine._broker_mem_cache.clear()

    def tearDown(self):
        v7_engine._foreign_mem_cache.clear()
        v7_engine._broker_mem_cache.clear()

    @staticmethod
    def _summary(net: float, n_brokers: int = 3) -> list:
        """List broker mock: broker pertama membawa net, sisanya nol."""
        rows = []
        if n_brokers >= 1:
            bv = str(int(max(net, 0)))
            sv = str(int(max(-net, 0)))
            rows.append({"code": "DB", "buy_value": bv, "sell_value": sv,
                         "net_value": str(int(net))})
        for i in range(1, n_brokers):
            rows.append({"code": f"B{i}", "buy_value": "0", "sell_value": "0",
                         "net_value": "0"})
        return rows

    def test_real_foreign_summary_scoring_varied(self):
        cases = [
            (150e9, 85), (50e9, 75), (15e9, 75), (5e9, 65), (1.5e9, 65),
            (0.3e9, 50), (-0.3e9, 50), (-1.5e9, 30), (-50e9, 30),
        ]
        for net, want in cases:
            with mock.patch.object(v7_engine, "_get_broker_foreign_summary_cached",
                                   return_value=self._summary(net)):
                r = v7_engine.factor_foreign_flow("BRPT")
            self.assertEqual(r["score"], want, f"net {net/1e9:.1f}B → skor {want}")
            self.assertIn("asing_", r["detail"])

    def test_13_of_15_tickers_not_all_50(self):
        # Sebelum IDE2: 13/15 ticker statis 50 (daftar broker asing salah).
        # Dengan snapshot 'f' mock yang bervariasi, skor HARUS membedakan.
        nets = [-120e9, -50e9, -30e9, -8e9, -5e9, -2e9, -1.5e9,
                0.1e9, 0.3e9, 1.5e9, 5e9, 8e9, 30e9, 80e9, 150e9]
        by_code = {f"T{i:02d}": self._summary(net) for i, net in enumerate(nets)}
        scores = {}
        with mock.patch.object(v7_engine, "_get_broker_foreign_summary_cached",
                               side_effect=lambda code, days=3: by_code.get(code, [])):
            for code in by_code:
                scores[code] = v7_engine.factor_foreign_flow(code)["score"]
        non_neutral = [s for s in scores.values() if s != 50]
        self.assertGreaterEqual(len(non_neutral), 13,
                                "maks 2 ticker boleh netral 50 — skor harus membedakan")
        self.assertGreater(len(set(scores.values())), 3,
                           "distribusi skor bervariasi (30/50/65/75/85)")

    def test_fallback_fixed_broker_list(self):
        """Snapshot 'f' kosong → fallback daftar kode yang DIPERBAIKI:
        AG (KIWOOM, domestik) & CS (merger ke UBS) TIDAK dihitung;
        AI (UOB Kay Hian) dihitung."""
        summary_all = [
            {"code": "AG", "buy_value": "50000000000", "sell_value": "0"},  # domestik — TIDAK dihitung
            {"code": "CS", "buy_value": "40000000000", "sell_value": "0"},  # merger UBS — TIDAK dihitung
            {"code": "AI", "buy_value": "5000000000", "sell_value": "0"},   # UOB Kay Hian — DIHITUNG
            {"code": "UBS", "buy_value": "0", "sell_value": "500000000"},   # asing — DIHITUNG
        ]
        with mock.patch.object(v7_engine, "_get_broker_foreign_summary_cached",
                               return_value=[]), \
             mock.patch.object(v7_engine, "_get_broker_summary_cached",
                               return_value=summary_all):
            r = v7_engine.factor_foreign_flow("AKRA")
        # net asing sejati = AI 5B − UBS 0.5B = 4.5B → asing_beli (65).
        # Daftar LAMA (AG+CS+UBS, tanpa AI) = 89.5B → asing_beli_besar (80).
        self.assertEqual(r["score"], 65, "fallback harus pakai daftar yang diperbaiki")
        self.assertEqual(r["detail"], "asing_beli")

    def test_foreign_cache_file_and_single_call(self):
        calls = []

        class FProvider:
            def get_broker_foreign_summary(self, code, days=3):
                calls.append(code)
                return [{"code": "DB", "buy_value": "2000000000", "sell_value": "0"},
                        {"code": "GS", "buy_value": "0", "sell_value": "0"}]

        tmp = tempfile.mkdtemp()
        v7_engine._foreign_mem_cache.clear()
        with mock.patch.object(v7_engine, "get_provider", return_value=FProvider()), \
             mock.patch.object(v7_engine, "_DATA_DIR", tmp):
            r1 = v7_engine.factor_foreign_flow("BBCA")
            r2 = v7_engine.factor_foreign_flow("BBCA")
        self.assertEqual(calls, ["BBCA"], "hanya 1 call per ticker (memori cache)")
        self.assertEqual(r1["score"], r2["score"])
        cache_file = os.path.join(tmp, "broker_flow_foreign_BBCA.json")
        self.assertTrue(os.path.exists(cache_file),
                        "cache broker_flow_foreign_BBCA.json harus dibuat (TTL 24 jam)")

    def test_no_foreign_data_netral_40(self):
        with mock.patch.object(v7_engine, "_get_broker_foreign_summary_cached",
                               return_value=[]), \
             mock.patch.object(v7_engine, "_get_broker_summary_cached",
                               return_value=[]):
            r = v7_engine.factor_foreign_flow("ZZZZ")
        self.assertEqual(r["score"], 40)
        self.assertEqual(r["detail"], "no_data")


# ══════════════════════════════════════════════════════════════════════════
# IDE3 — GATE KUALITAS SWING (volume confirmation + quality_gate)
# ══════════════════════════════════════════════════════════════════════════

class TestV7SwingQualityGate(unittest.TestCase):
    """IDE3 — gate_swing_signal di v7_scan: volume confirmation
    (vol_ratio >= 1.0 utk sinyal; STRONG_BUY butuh >= 1.2 — >= 1.0 di BULL;
    NaN/0 = GAGAL gate) + quality_gate (downgrade bertingkat
    SB→BUY→WEAK_BUY→HOLD). Threshold skor SB65/BUY55 TIDAK diubah — gate
    bekerja di ATAS label sinyal, kolom score tetap skor v7 asli."""

    @classmethod
    def setUpClass(cls):
        class _NullHandler:
            level = 1000
            def setFormatter(self, *a, **k):
                return None
        with mock.patch("logging.FileHandler", _NullHandler):
            import v7_scan as v7s
        cls.v7s = v7s

    @staticmethod
    def _row(vol_ratio=1.5, rsi=55.0, ret_20d=0.02, atr=200.0, close=5000.0, adx=25.0):
        return {"rsi": rsi, "vol_ratio": vol_ratio, "ret_20d": ret_20d,
                "atr": atr, "close": close, "adx": adx}

    def test_high_score_low_vol_056_downgraded_to_hold(self):
        # Kasus AKRA 11/08: STRONG_BUY skor 67.5 dengan vol_ratio 0.56
        g = self.v7s.gate_swing_signal(True, "STRONG_BUY", 0.56, "RANGING",
                                       self._row(vol_ratio=0.56))
        self.assertFalse(g["ok"], "vol 0.56 < 1.0 → sinyal dibatalkan (HOLD/skip)")
        self.assertEqual(g["signal"], "HOLD")
        self.assertEqual(g["gate_vol"], "fail_vol<1.0")
        self.assertEqual(g["gate_quality"], "pass")

    def test_high_score_vol_13_normal(self):
        g = self.v7s.gate_swing_signal(True, "STRONG_BUY", 1.3, "RANGING",
                                       self._row(vol_ratio=1.3))
        self.assertTrue(g["ok"])
        self.assertEqual(g["signal"], "STRONG_BUY")
        self.assertEqual(g["gate_vol"], "pass")
        self.assertEqual(g["gate_quality"], "pass")

    def test_sb_vol_11_downgraded_to_buy_non_bull(self):
        g = self.v7s.gate_swing_signal(True, "STRONG_BUY", 1.1, "RANGING",
                                       self._row(vol_ratio=1.1))
        self.assertTrue(g["ok"], "downgrade BUKAN veto — BUY tetap lolos di RANGING")
        self.assertEqual(g["signal"], "BUY")
        self.assertEqual(g["gate_vol"], "downgrade_sb_vol<1.2")

    def test_sb_vol_11_allowed_in_bull(self):
        g = self.v7s.gate_swing_signal(True, "STRONG_BUY", 1.1, "BULL",
                                       self._row(vol_ratio=1.1))
        self.assertTrue(g["ok"])
        self.assertEqual(g["signal"], "STRONG_BUY", "BULL: SB cukup vol_ratio >= 1.0")

    def test_nan_vol_ratio_fails_gate(self):
        g = self.v7s.gate_swing_signal(True, "BUY", float("nan"), "RANGING",
                                       self._row(vol_ratio=float("nan")))
        self.assertFalse(g["ok"], "NaN → dianggap GAGAL gate")
        self.assertEqual(g["gate_vol"], "fail_vol<1.0")

    def test_zero_vol_ratio_fails_gate(self):
        g = self.v7s.gate_swing_signal(True, "BUY", 0.0, "RANGING",
                                       self._row(vol_ratio=0.0))
        self.assertFalse(g["ok"], "0 → dianggap GAGAL gate")
        self.assertEqual(g["gate_vol"], "fail_vol<1.0")

    def test_quality_gate_no_trend_downgrades_sb_to_buy(self):
        # ADX < 15 (no trend) → SB turun ke BUY via quality_gate
        g = self.v7s.gate_swing_signal(True, "STRONG_BUY", 1.5, "RANGING",
                                       self._row(adx=10.0))
        self.assertTrue(g["ok"])
        self.assertEqual(g["signal"], "BUY")
        self.assertEqual(g["gate_quality"], "downgrade_STRONG_BUY->BUY")

    def test_quality_gate_low_liquidity_to_hold(self):
        # ATR < 0.3% harga → HOLD (quality_gate low_liquidity)
        g = self.v7s.gate_swing_signal(True, "BUY", 1.5, "RANGING",
                                       self._row(atr=10.0, close=5000.0))
        self.assertFalse(g["ok"], "HOLD tidak lolos allowed_signals → skip")
        self.assertEqual(g["signal"], "HOLD")
        self.assertEqual(g["gate_quality"], "downgrade_BUY->HOLD")

    def test_swing_ok_false_short_circuits(self):
        g = self.v7s.gate_swing_signal(False, "STRONG_BUY", 0.5, "RANGING",
                                       self._row(vol_ratio=0.5))
        self.assertFalse(g["ok"])
        self.assertEqual(g["gate_vol"], "pass", "tidak lolos _swing_gate → gate tak berlaku")
        self.assertEqual(g["gate_quality"], "pass")

    def test_gate_columns_written_to_perf_csv(self):
        with tempfile.TemporaryDirectory(prefix="gate_") as td:
            csvp = os.path.join(td, "perf.csv")
            ok = log_signal(csvp, ticker="AKRA", mode="swing", score=67.5,
                            signal="BUY", entry_price=5000, sl=4750, tp=5500,
                            lots=5, cost=2500000, regime="RANGING",
                            gate_vol="downgrade_sb_vol<1.2", gate_quality="pass")
            self.assertTrue(ok)
            rows = load_signals(csvp)
            self.assertEqual(rows[0]["gate_vol"], "downgrade_sb_vol<1.2")
            self.assertEqual(rows[0]["gate_quality"], "pass")
            # Pemanggil lama (tanpa gate) → kolom tetap ada, isi ''
            csvp2 = os.path.join(td, "perf2.csv")
            log_signal(csvp2, ticker="BBCA", mode="intraday", score=60.0,
                       signal="BUY", entry_price=10000, sl=9500, tp=11000,
                       lots=2, cost=2000000)
            rows2 = load_signals(csvp2)
            self.assertEqual(rows2[0]["gate_vol"], "")
            self.assertEqual(rows2[0]["gate_quality"], "")

    def test_old_csv_migrated_gate_unknown(self):
        with tempfile.TemporaryDirectory(prefix="gate_mig_") as td:
            csvp = os.path.join(td, "perf.csv")
            with open(csvp, "w", newline="", encoding="utf-8") as f:
                f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh,regime\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,10000,9500,11000,2,2000000,1,unknown\n")
            log_signal(csvp, ticker="BBRI", mode="swing", score=62.0, signal="BUY",
                       entry_price=5000, sl=4750, tp=5500, lots=5, cost=2500000)
            rows = load_signals(csvp)
            self.assertEqual(rows[0]["gate_vol"], "unknown",
                             "baris lama (sebelum gate) di-backfill 'unknown'")
            self.assertEqual(rows[0]["gate_quality"], "unknown")


class TestIde4Ide5Hardening(unittest.TestCase):
    """IDE4/IDE5-hardening — guard evaluasi weekly_report (exit price tidak
    valid, log 'dup-skip', konsistensi status-vs-return) + kolom flow_spike &
    broker_trend_detail di perf_tracker (default aman 'unknown', migrasi CSV).

    Simulasi memakai mock data (path semua diarahkan ke temp dir — data
    produksi data/*.csv TIDAK tersentuh).
    """

    # ── IDE4: INDF-like — LOSS_SL dengan close terakhir DI ATAS entry harus
    # tetap return NEGATIF dari harga SL (bukan close). ──
    def test_indf_like_loss_sl_negative_from_sl_not_close(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sig_date = datetime.now() - timedelta(days=11)
        # INDF audit: entry 7150, SL 7017, close terakhir 7450 (> entry)
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},INDF,swing,60.1,BUY,"
               f"7150,7017,7850,1,7150000,1\n")
        # TP (7850) tidak pernah kena; SL (7017) kena di baris ke-3;
        # close terakhir 7450 > entry → dulu tampil +4.2% (SALAH).
        highs = [7200, 7300, 7350, 7400, 7450]
        lows = [7150, 7100, 7017, 7000, 7020]
        closes = [7200, 7300, 7350, 7400, 7450]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        provider._df["Close"] = closes
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "LOSS_SL")
        self.assertEqual(results[0]["exit_price"], 7017.0,
                         "exit_price = harga SL, bukan close")
        self.assertAlmostEqual(results[0]["return_pct"],
                               (7017 - 7150) / 7150 * 100, places=2,
                               msg="LOSS_SL harus return NEGATIF dari harga SL "
                                   "walau close terakhir > entry")
        self.assertLess(results[0]["return_pct"], 0)
        self.assertEqual(results[0]["close_price"], 7450.0,
                         "close_price tetap konteks, bukan dasar return")
        with open(eval_csv, encoding="utf-8") as f:
            eval_rows = list(csv_reader(f))
        self.assertEqual(eval_rows[0]["exit_price"], "7017.0",
                         "kolom exit_price terisi harga SL di CSV")

    # ── IDE4: ISAT-like — WIN_TP return dari harga TP, bukan close yang
    # bahkan lebih tinggi dari TP. ──
    def test_isat_like_win_tp_return_from_tp_not_close(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sig_date = datetime.now() - timedelta(days=11)
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},ISAT,swing,62.0,BUY,"
               f"100,90,110,2,20000000,1\n")
        highs = [105, 115, 116, 118, 120]
        lows = [95, 96, 97, 98, 99]
        closes = [105, 112, 116, 118, 120]  # close terakhir 120 > TP 110
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        provider._df["Close"] = closes
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json):
            results = weekly_report_mod.evaluate_signals(provider=provider)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "WIN_TP")
        self.assertEqual(results[0]["exit_price"], 110.0)
        self.assertAlmostEqual(results[0]["return_pct"], 10.0, places=4,
                               msg="return dari TP (+10%), bukan close (+20%)")
        self.assertEqual(results[0]["close_price"], 120.0)

    # ── IDE4: tiap duplikat evaluasi yang di-skip di-log 'Dup-skip' ──
    def test_dup_eval_skip_logs_warning(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = datetime.now()
        d_a = (base - timedelta(days=5)).strftime("%Y-%m-%d %H:%M")
        d_b = (base - timedelta(days=4)).strftime("%Y-%m-%d %H:%M")
        rows = [
            f"{d_a},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,1",
            f"{d_b},BUMI,intraday,60.5,STRONG_BUY,168.0,158.0,182.0,41,688800,0",
        ]
        highs = [170, 182, 180, 179, 178]
        lows = [165, 166, 167, 168, 169]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, rows, base - timedelta(days=5), highs, lows)
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json), \
             self.assertLogs("weekly_report", level="WARNING") as cm:
            results = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results), 1, "1 sinyal unik → 1 evaluasi")
        self.assertTrue(any("Dup-skip evaluasi" in m for m in cm.output),
                        "log warning 'Dup-skip evaluasi' harus muncul per baris "
                        f"duplikat — output: {cm.output}")

    # ── IDE4: exit price tidak valid (TP=0) → log warning + exit_price 'nan'
    # (fallback ke close TERTANDA, bukan diam-diam). ──
    def test_exit_price_invalid_logged_and_nan(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sig_date = datetime.now() - timedelta(days=11)
        # TP=0 tidak valid → classify: high>=0 kena baris 1 → WIN_TP dgn TP 0
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,"
               f"100,90,0,2,20000000,1\n")
        highs = [105, 106, 107, 108, 109]
        lows = [95, 96, 97, 98, 99]
        closes = [105, 106, 107, 108, 109]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        provider._df["Close"] = closes
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json), \
             self.assertLogs("weekly_report", level="WARNING") as cm:
            results = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results), 1)
        self.assertTrue(any("Exit price tidak tersedia" in m for m in cm.output),
                        "fallback ke close harus DITANDAI warning — "
                        f"output: {cm.output}")
        self.assertTrue(math.isnan(results[0]["exit_price"]),
                        "exit_price ditandai NaN, bukan close diam-diam")
        with open(eval_csv, encoding="utf-8") as f:
            eval_rows = list(csv_reader(f))
        self.assertEqual(eval_rows[0]["exit_price"], "nan",
                         "kolom exit_price berisi 'nan' di CSV (penanda eksplisit)")
        self.assertAlmostEqual(results[0]["return_pct"],
                               (109 - 100) / 100 * 100, places=4,
                               msg="return_pct fallback ke close TERAKHIR (tertanda)")

    # ── IDE4: konsistensi status-vs-return — LOSS_SL dgn return >= 0 (SL di
    # atas entry, kasus data aneh) harus di-log warning. ──
    def test_loss_sl_positive_return_logs_consistency_warning(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sig_date = datetime.now() - timedelta(days=11)
        # SL=150 DI ATAS entry 100 → low<=150 kena baris 1 (SL dulu) → LOSS_SL
        row = (f"{sig_date.strftime('%Y-%m-%d %H:%M')},BBCA,swing,55.4,BUY,"
               f"100,150,110,2,20000000,1\n")
        highs = [105, 112, 113, 114, 115]
        lows = [95, 96, 97, 98, 99]
        closes = [105, 106, 107, 108, 109]
        perf_csv, eval_csv, mark_json, provider = _eval_setup(
            tmp, [row], sig_date, highs, lows)
        provider._df["Close"] = closes
        with mock.patch.object(weekly_report_mod, "PERF_CSV", perf_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_CSV", eval_csv), \
             mock.patch.object(weekly_report_mod, "EVAL_MARK", mark_json), \
             self.assertLogs("weekly_report", level="WARNING") as cm:
            results = weekly_report_mod.evaluate_signals(provider=provider)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "LOSS_SL")
        self.assertGreaterEqual(results[0]["return_pct"], 0)
        self.assertTrue(any("Konsistensi status-vs-return" in m for m in cm.output),
                        "anomali LOSS_SL return positif harus di-log warning — "
                        f"output: {cm.output}")

    # ── IDE5: kolom flow_spike & broker_trend_detail — default aman
    # 'unknown', nilai terisi kalau dikirim, migrasi CSV lama backfill. ──
    def test_flow_spike_and_broker_trend_detail_columns(self):
        self.assertIn("flow_spike", FIELDS, "FIELDS perf_tracker harus punya flow_spike")
        self.assertIn("broker_trend_detail", FIELDS,
                      "FIELDS perf_tracker harus punya broker_trend_detail")
        self.assertEqual(FIELD_DEFAULTS["flow_spike"], "unknown")
        self.assertEqual(FIELD_DEFAULTS["broker_trend_detail"], "unknown")
        with tempfile.TemporaryDirectory(prefix="fs_dna_") as td:
            # Pemanggil LAMA (dict sinyal tanpa flow_spike/broker_trend_detail)
            # → default aman 'unknown', TIDAK crash (kasus dedup_and_log_batch).
            csvp = os.path.join(td, "perf.csv")
            ok = log_signal(csvp, ticker="BBCA", mode="swing", score=60.0,
                            signal="BUY", entry_price=10000, sl=9500, tp=11000,
                            lots=2, cost=2000000)
            self.assertTrue(ok)
            rows = load_signals(csvp)
            self.assertEqual(rows[0]["flow_spike"], "unknown")
            self.assertEqual(rows[0]["broker_trend_detail"], "unknown")
            # Pemanggil BARU mengirim flow_spike (bool) + detail → tercatat.
            csvp2 = os.path.join(td, "perf2.csv")
            log_signal(csvp2, ticker="BBRI", mode="swing", score=62.0,
                       signal="BUY", entry_price=5000, sl=4750, tp=5500,
                       lots=5, cost=2500000,
                       flow_spike=True, broker_trend_detail="streak=3 arah=up")
            rows2 = load_signals(csvp2)
            self.assertEqual(rows2[0]["flow_spike"], "1",
                             "bool True → '1' (konsisten dgn kolom fresh)")
            self.assertEqual(rows2[0]["broker_trend_detail"], "streak=3 arah=up")
            # CSV lama tanpa kedua kolom → migrasi header + backfill 'unknown'.
            old = os.path.join(td, "old.csv")
            with open(old, "w", encoding="utf-8") as f:
                f.write("date,ticker,mode,score,signal,entry_price,sl,tp,lots,cost,fresh,regime\n")
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M')},BBCA,swing,"
                        f"55.4,BUY,10000,9500,11000,2,2000000,1,unknown\n")
            log_signal(old, ticker="DSSA", mode="swing", score=60.0, signal="BUY",
                       entry_price=10000, sl=9500, tp=11000, lots=2, cost=2000000)
            rows3 = load_signals(old)
            self.assertEqual(rows3[0]["flow_spike"], "unknown",
                             "baris lama di-backfill flow_spike='unknown'")
            self.assertEqual(rows3[0]["broker_trend_detail"], "unknown",
                             "baris lama di-backfill broker_trend_detail='unknown'")

    # ── IDE5-complete: dict ala v7_scan (logged_signals swing/intraday) yang
    # memuat flow_spike + broker_trend_detail diteruskan dedup_and_log_batch
    # → CSV berisi nilai NYATA (bukan 'unknown'). Menutup celah pipeline:
    # dulu v7_scan tidak mengirim 2 key ini → kolom DNA selalu 'unknown'. ──
    def test_v7_scan_logged_signals_dna_columns_reach_csv(self):
        with tempfile.TemporaryDirectory(prefix="dna_pipe_") as td:
            csvp = os.path.join(td, "perf.csv")
            # Dict persis ala v7_scan.py logged_signals swing (IDE5-complete):
            # flow_spike (bool) + broker_trend_detail (string) IKUT dikirim.
            sig = {
                "ticker": "BBCA", "mode": "swing", "score": 60.0,
                "signal": "BUY", "entry_price": 10000.0,
                "sl": 9500.0, "tp": 11000.0, "lots": 2, "cost": 20000000,
                "risk_amount": 1000000, "regime": "BULL",
                "broker_flow": 62.5, "broker_trend": 55.0,
                "flow_spike": True,
                "broker_trend_detail": "streak=3 arah=up",
                "foreign_flow": 40.0, "fundamental": 70.0,
                "earnings_momentum": 50.0, "weekly_trend": "BULLISH",
                "atr_pct": 2.5, "vol_ratio": 1.8, "event": "",
                "gate_vol": "pass", "gate_quality": "pass",
            }
            results = dedup_and_log_batch(csvp, [sig])
            self.assertTrue(results[0]["logged"],
                            "sinyal dict v7_scan harus tercatat")
            rows = load_signals(csvp)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["flow_spike"], "1",
                             "flow_spike=True dari dict v7_scan → '1' di CSV")
            self.assertEqual(rows[0]["broker_trend_detail"], "streak=3 arah=up",
                             "broker_trend_detail dari dict v7_scan → nilai nyata")

    # ── IDE5: guard append duplikat batch SUDAH ada di perf_tracker
    # (dedup_and_log_batch/_logged_today) — verifikasi end-to-end: 4 run
    # malam yang sama = 1 baris per sinyal. ──
    def test_same_night_four_runs_one_row_per_signal(self):
        with tempfile.TemporaryDirectory(prefix="night4_") as td:
            csvp = os.path.join(td, "perf.csv")
            fixed = datetime(2026, 8, 8, 22, 2, 0)
            with mock.patch("perf_tracker.datetime") as mdt:
                mdt.now.return_value = fixed
                mdt.strptime.side_effect = datetime.strptime
                # 4 run malam 08/08 (22:02/22:32/22:44/23:01) — sinyal identik
                for _ in range(4):
                    dedup_and_log_batch(csvp, [_mk_signal()])
            rows = load_signals(csvp)
            self.assertEqual(len(rows), 1,
                             "4 run malam sama → 1 baris per sinyal (bukan 4)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
