"""
test_v7_berita.py — tes faktor sentimen BERITA di skor V7 (BARU 21 Sep 2026).

Cakupan: matematika agregasi (delta, recency, klem, campuran/netral), cache TTL,
kill-switch, fail-soft (tanpa network), integrasi compute() (post-adjustment).

Semua tes OFFLINE: modul sentimen & fetch berita di-stub (mock) — tidak
menyentuh Stockbit maupun data produksi.
Jalankan (dari root repo):
    PYTHONUTF8=1 .venv/bin/python -m unittest discover -s idx_alpha_screener -p "test_v7*.py" -v
"""
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import berita_sentimen as bs                                    # noqa: E402
import v7 as v7_engine                                          # noqa: E402

WIB = timezone(timedelta(hours=7))


def _ts(jam_lalu: float) -> float:
    """Timestamp WIB `jam_lalu` jam yang lalu (epoch float)."""
    return (datetime.now(WIB) - timedelta(hours=jam_lalu)).timestamp()


def _item(judul: str, jam_lalu: float, sid=None) -> dict:
    return {"judul": judul, "ts": _ts(jam_lalu), "sid": sid}


class _StubSentimen:
    """Stub modul sentimen (dipasang ke sys.modules): MAP judul → (label, skor)."""
    MAP: dict = {}

    @classmethod
    def sentimen_judul_skor(cls, judul):
        return cls.MAP.get(judul, ("netral", 0.0))


class TestHitungDelta(unittest.TestCase):
    """Matematika agregasi — sentimen di-stub supaya bebas lingkungan."""

    def setUp(self):
        self._orig = sys.modules.get("sentimen")
        _StubSentimen.MAP = {
            "Laba naik": ("baik", 2.0),
            "Dividen naik": ("baik", 3.0),
            "Rugi membengkak": ("buruk", -3.0),
            "Saham turun tipis": ("buruk", -1.0),
            "Campur aduk": ("campuran", 0.0),
            "Biasa saja": ("netral", 0.0),
            "Sangat baik": ("baik", 10.0),
        }
        sys.modules["sentimen"] = _StubSentimen

    def tearDown(self):
        if self._orig is not None:
            sys.modules["sentimen"] = self._orig
        else:
            sys.modules.pop("sentimen", None)

    def test_positif_sampai_batas_atas(self):
        # +2 & +3 segar → Σ5 → klem → +2,0 (max_points)
        h = bs.hitung_delta([_item("Laba naik", 2), _item("Dividen naik", 1)])
        self.assertAlmostEqual(h["delta"], 2.0, places=2)
        self.assertEqual(h["n"], 2)
        self.assertEqual(h["n_baik"], 2)

    def test_negatif_sampai_batas_bawah(self):
        h = bs.hitung_delta([_item("Rugi membengkak", 1),
                             _item("Rugi membengkak", 2),
                             _item("Rugi membengkak", 3)])
        self.assertAlmostEqual(h["delta"], -2.0, places=2)
        self.assertEqual(h["n_buruk"], 3)

    def test_bobot_recency_setengah_di_atas_72jam(self):
        # −3 pada umur 100 jam → bobot 0,5 → Σ −1,5 → delta = 2 × (−1,5/3) = −1,0
        h = bs.hitung_delta([_item("Rugi membengkak", 100)])
        self.assertAlmostEqual(h["delta"], -1.0, places=2)
        self.assertEqual(h["n"], 1)

    def test_skala_linear_untuk_sinyal_kecil(self):
        # −1 segar → Σ −1 → delta = 2 × (−1/3) ≈ −0,67
        h = bs.hitung_delta([_item("Saham turun tipis", 1)])
        self.assertAlmostEqual(h["delta"], -0.67, places=2)

    def test_klem_magnitudo_per_judul(self):
        # skor mentah +10 diklem +3 → Σ3 → delta +2,0
        h = bs.hitung_delta([_item("Sangat baik", 1)])
        self.assertAlmostEqual(h["delta"], 2.0, places=2)

    def test_netral_dan_campuran_tidak_menggeser(self):
        h = bs.hitung_delta([_item("Campur aduk", 1), _item("Biasa saja", 1)])
        self.assertEqual(h["delta"], 0.0)
        self.assertEqual(h["n"], 2)
        self.assertEqual(h["n_campuran"], 1)
        self.assertEqual(h["n_netral"], 1)

    def test_item_lama_dilewati(self):
        h = bs.hitung_delta([_item("Laba naik", 200)])
        self.assertEqual(h["delta"], 0.0)
        self.assertEqual(h["n"], 0)
        self.assertIn("tanpa berita", h["detail"])

    def test_tanpa_item(self):
        h = bs.hitung_delta([])
        self.assertEqual(h["delta"], 0.0)
        self.assertEqual(h["n"], 0)

    def test_max_points_bisa_diatur(self):
        # max_points 4,0 → sinyal penuh = ±4,0
        h = bs.hitung_delta([_item("Dividen naik", 1)], max_points=4.0)
        self.assertAlmostEqual(h["delta"], 4.0, places=2)


class _StubNews:
    """Stub modul stockbit_news: fetch_symbol menghitung panggilan."""
    CALLS = 0

    @classmethod
    def fetch_symbol(cls, kode, limit=20):
        cls.CALLS += 1
        return [{"title": "Laba naik",
                 "created_at": datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S"),
                 "stream_id": 100 + cls.CALLS}]


class TestCacheBerita(unittest.TestCase):
    """Cache file per kode: TTL dihormati; gagal → breaker, tidak melempar."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_dir = bs._DATA_DIR
        self._orig_breaker = bs._GAGAL_SAMPAI
        bs._DATA_DIR = self.tmp.name
        bs._GAGAL_SAMPAI = 0.0
        self._orig_news = sys.modules.get("stockbit_news")
        _StubNews.CALLS = 0
        sys.modules["stockbit_news"] = _StubNews

    def tearDown(self):
        bs._DATA_DIR = self._orig_dir
        bs._GAGAL_SAMPAI = self._orig_breaker
        if self._orig_news is not None:
            sys.modules["stockbit_news"] = self._orig_news
        else:
            sys.modules.pop("stockbit_news", None)
        self.tmp.cleanup()

    def test_cache_dipakai_dalam_ttl(self):
        a = bs.ambil_berita("TLKM")
        b = bs.ambil_berita("TLKM")
        self.assertEqual(len(a), 1)
        self.assertEqual(a, b)
        self.assertEqual(_StubNews.CALLS, 1, "panggilan kedua harus dari cache")

    def test_cache_kadaluarsa_diambil_ulang(self):
        bs.ambil_berita("TLKM")
        path = bs._cache_path("TLKM")
        old = time.time() - 7 * 3600
        os.utime(path, (old, old))
        bs.ambil_berita("TLKM")
        self.assertEqual(_StubNews.CALLS, 2, "cache kedaluwarsa harus refetch")

    def test_gagal_tidak_melempar_dan_breaker_aktif(self):
        def _raise(kode, limit=20):
            raise RuntimeError("sesi mati")
        with mock.patch.object(_StubNews, "fetch_symbol", side_effect=_raise):
            a = bs.ambil_berita("TLKM")
        self.assertEqual(a, [])
        self.assertGreater(bs._GAGAL_SAMPAI, time.time(), "breaker harus menyala")
        # panggilan berikutnya (kode lain) tidak menghajar fetch lagi
        bs.ambil_berita("BBCA")
        self.assertEqual(_StubNews.CALLS, 0, "breaker: fetch tidak dipanggil")


class TestFaktorBerita(unittest.TestCase):
    """faktor_berita(): kill-switch, fail-soft, dan jalur normal."""

    def setUp(self):
        self._orig = sys.modules.get("sentimen")
        _StubSentimen.MAP = {"Rugi membengkak": ("buruk", -3.0)}
        sys.modules["sentimen"] = _StubSentimen

    def tearDown(self):
        if self._orig is not None:
            sys.modules["sentimen"] = self._orig
        else:
            sys.modules.pop("sentimen", None)

    def test_kill_switch_env(self):
        with mock.patch.dict(os.environ, {"SCREENER_NEWS": "0"}), \
                mock.patch.object(bs, "ambil_berita",
                                  side_effect=AssertionError("tidak boleh dipanggil")):
            h = bs.faktor_berita("TLKM")
        self.assertEqual(h["delta"], 0.0)
        self.assertEqual(h["detail"], "nonaktif")

    def test_config_enabled_false(self):
        with mock.patch.dict(os.environ, {"SCREENER_NEWS": "1"}):
            h = bs.faktor_berita("TLKM", {"enabled": False})
        self.assertEqual(h["detail"], "nonaktif")

    def test_fail_soft_ambil_gagal(self):
        with mock.patch.dict(os.environ, {"SCREENER_NEWS": "1"}), \
                mock.patch.object(bs, "ambil_berita", side_effect=RuntimeError("x")):
            h = bs.faktor_berita("TLKM")
        self.assertEqual(h["delta"], 0.0)
        self.assertIn("gagal", h["detail"])

    def test_jalur_normal(self):
        with mock.patch.dict(os.environ, {"SCREENER_NEWS": "1"}), \
                mock.patch.object(bs, "ambil_berita",
                                  return_value=[_item("Rugi membengkak", 1)]):
            h = bs.faktor_berita("TLKM")
        self.assertAlmostEqual(h["delta"], -2.0, places=2)
        self.assertEqual(h["n_buruk"], 1)


class TestComputeAdopsiDelta(unittest.TestCase):
    """compute(): delta berita ditambahkan SEKALI di luar weighted sum."""

    def setUp(self):
        self._old_enabled = v7_engine.enabled
        v7_engine.enabled = True
        self._env = mock.patch.dict(os.environ, {"SCREENER_NEWS": "1"})
        self._env.start()
        self._patchers = [
            mock.patch.object(v7_engine, "factor_broker_flow",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_foreign_flow",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_fundamental_quality",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_earnings_momentum",
                              return_value={"score": 80, "detail": "x"}),
            mock.patch.object(v7_engine, "factor_broker_trend",
                              return_value={"score": 80, "detail": "x"}),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        self._env.stop()
        v7_engine.enabled = self._old_enabled

    def _base(self, v4=90.0):
        """Weighted sum tanpa adjustment (v4 × 0.30 + faktor 80 × 0.70)."""
        w = v7_engine._V7_WEIGHTS
        return v4 * w["v4_score"] + 80.0 * (1.0 - w["v4_score"])

    def test_delta_positif_ditambahkan(self):
        with mock.patch.object(bs, "faktor_berita",
                               return_value={"delta": 1.4, "n": 5, "n_baik": 2,
                                             "n_buruk": 1, "detail": "uji"}):
            r = v7_engine.compute("BBCA", 90.0, "BULL")
        self.assertAlmostEqual(r["score"], self._base() + 1.4, places=1)
        self.assertAlmostEqual(r["factors"]["berita_delta"], 1.4, places=2)
        self.assertEqual(r["factors"]["berita_n"], 5)
        self.assertEqual(r["factors"]["berita_baik"], 2)

    def test_delta_negatif_dikurangkan(self):
        with mock.patch.object(bs, "faktor_berita",
                               return_value={"delta": -1.0, "n": 2, "detail": "uji"}):
            r = v7_engine.compute("BBCA", 90.0, "BULL")
        self.assertAlmostEqual(r["score"], self._base() - 1.0, places=1)

    def test_env_off_tidak_memanggil_faktor(self):
        with mock.patch.dict(os.environ, {"SCREENER_NEWS": "0"}), \
                mock.patch.object(bs, "faktor_berita",
                                  side_effect=AssertionError("tidak boleh dipanggil")):
            r = v7_engine.compute("BBCA", 90.0, "BULL")
        self.assertAlmostEqual(r["score"], self._base(), places=1)
        self.assertEqual(r["factors"]["berita_delta"], 0.0)
        self.assertEqual(r["factors"]["berita_n"], 0)

    def test_skor_masih_diklem_0_100(self):
        # delta besar + v4 tinggi → tetap ≤ 100
        with mock.patch.object(bs, "faktor_berita",
                               return_value={"delta": 2.0, "n": 9, "detail": "uji"}):
            r = v7_engine.compute("BBCA", 100.0, "BULL")
        self.assertLessEqual(r["score"], 100.0)
        self.assertGreaterEqual(r["score"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
