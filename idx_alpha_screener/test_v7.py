"""
test_v7.py — Test Suite F2 untuk modul V7 (IDX Alpha Screener)
================================================================
Mencakup fitur v7 yang sebelumnya tidak punya test sama sekali:
  TEST 1  perf_tracker  : dedup sinyal (±1%, <14 hari), kolom fresh
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
import ast
import json
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
    dedup_and_log_batch,
    find_previous_signal,
    load_signals,
)
from position_tracker import PositionTracker                # noqa: E402
import ai_narrative                                         # noqa: E402
import telegram_formatter                                   # noqa: E402
import position_check_intraday as pci                       # noqa: E402

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
        r1 = dedup_and_log_batch(self.csv, [_mk_signal()])
        self.assertTrue(r1[0]["fresh"], "sinyal pertama harus fresh=True")
        self.assertIsNone(r1[0]["ref_date"])
        self.assertTrue(r1[0]["logged"])

        # Harga 10050 vs 10000 = +0.5% (≤ ±1%) & < 14 hari → duplikat
        r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10050.0)])
        self.assertFalse(r2[0]["fresh"], "sinyal identik ±1% harus fresh=False (lanjutan)")
        self.assertEqual(r2[0]["ref_date"], datetime.now().strftime("%d/%m"))
        self.assertTrue(r2[0]["logged"], "sinyal lanjutan TETAP di-log (fresh=0)")

        # Isi CSV: 2 baris, kolom fresh = 1 lalu 0
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["fresh"] for r in rows], ["1", "0"])

    # ── Sinyal beda harga (> ±1%) → tetap di-log sebagai fresh=1 ──
    def test_different_price_logged_as_fresh(self):
        dedup_and_log_batch(self.csv, [_mk_signal(entry_price=10000.0)])
        r2 = dedup_and_log_batch(self.csv, [_mk_signal(entry_price=11000.0)])  # +10%
        self.assertTrue(r2[0]["fresh"], "beda harga >±1% bukan duplikat")
        self.assertIsNone(r2[0]["ref_date"])
        rows = load_signals(self.csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["fresh"] for r in rows], ["1", "1"])

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

    # ── find_previous_signal: toleransi harga & ticker ──
    def test_find_previous_signal_tolerance(self):
        now = datetime.now()
        rows = [{
            "date": now.strftime("%Y-%m-%d %H:%M"),
            "ticker": "BBCA", "mode": "swing", "entry_price": "10000",
        }]
        # Di luar toleransi ±1%
        is_dup, _ = find_previous_signal(self.csv, "BBCA", "swing", 10200, rows=rows)
        self.assertFalse(is_dup, "+2% harus di luar toleransi")
        # Ticker berbeda
        is_dup2, _ = find_previous_signal(self.csv, "BBRI", "swing", 10050, rows=rows)
        self.assertFalse(is_dup2, "ticker berbeda bukan duplikat")
        # Entry invalid → aman, bukan duplikat
        is_dup3, _ = find_previous_signal(self.csv, "BBCA", "swing", "abc", rows=rows)
        self.assertFalse(is_dup3)

    # ── Header CSV selalu punya kolom fresh (migrasi aman) ──
    def test_csv_header_has_fresh_column(self):
        dedup_and_log_batch(self.csv, [_mk_signal()])
        with open(self.csv, encoding="utf-8") as f:
            header = f.readline().strip()
        self.assertIn("fresh", header.split(","))


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


def csv_reader(f):
    import csv
    return csv.DictReader(f)


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
                               return_value="narasi") as llm:
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
        msg = telegram_formatter.format_message(self._swing_list(5), self._intra_list(5))
        self.assertLess(len(msg), 3500, f"pesan terlalu panjang: {len(msg)} chars")
        self.assertIn("SCREENER V7", msg)

    def test_continuation_label(self):
        swing = self._swing_list(2)
        swing[0]["continuation"] = "05/08"
        msg = telegram_formatter.format_message(swing, self._intra_list(1))
        self.assertIn("(lanjutan - sinyal 05/08)", msg)
        self.assertIn("🔄 (lanjutan)", msg, "legend lanjutan harus muncul")

    def test_narrative_included(self):
        swing = self._swing_list(2)
        narratives = {"SW0": "Konteks netral: akumulasi broker naik."}
        msg = telegram_formatter.format_message(swing, self._intra_list(1),
                                                narratives=narratives)
        self.assertIn("📝 Konteks netral: akumulasi broker naik.", msg)

    def test_no_narratives_param_no_crash(self):
        # Panggilan lama tanpa narratives → harus tetap jalan
        msg = telegram_formatter.format_message(self._swing_list(3), self._intra_list(2))
        self.assertIn("RINGKASAN", msg)
        self.assertIn("Swing 3 | Intra 2", msg)

    def test_market_sentiment_section(self):
        sentiment = {
            "reason": "window dressing akhir bulan",
            "sentiment": "GREEN",
            "details": ["beli asing masuk", "ADX naik"],
            "key_levels": {"current": 7000, "support": 6800, "resistance": 7200},
        }
        msg = telegram_formatter.format_message(self._swing_list(2), self._intra_list(1),
                                                market_sentiment=sentiment)
        self.assertIn("🔮 BESOK", msg)
        self.assertIn("IHSG 7,000", msg)

    def test_long_message_truncated(self):
        # Narrative panjang × 5 sinyal → pesan >3500 → di-truncate + marker
        swing = self._swing_list(5)
        narratives = {s["tkr"]: "kalimat naratif " * 150 for s in swing}
        msg = telegram_formatter.format_message(swing, self._intra_list(5),
                                                narratives=narratives)
        self.assertIn("(truncated)", msg)
        self.assertLessEqual(len(msg), 3510, "pesan terpotong tetap di batas wajar")


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
        """Posisi aman (HOLD) → tidak ada EXIT → tidak kirim Telegram, return 0."""
        class FakeTracker:
            def get_positions(self):
                return {"BBCA": {"entry_price": 100}}
            def check_positions(self, getter):
                return [{"ticker": "BBCA", "level": "HOLD",
                         "message": "BBCA +2.0% (entry 100) | SL 95 | TP 110 | hold 18d lagi"}]

        with mock.patch.object(pci, "PositionTracker", FakeTracker), \
             mock.patch.object(pci, "setup_logging", lambda: None), \
             mock.patch.object(sys, "argv", ["position_check_intraday.py", "--dry-run"]):
            rc = pci.main()

        self.assertEqual(rc, 0, "tidak ada perubahan status → diam, exit 0")


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
