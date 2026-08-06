"""
data_invezgo.py — Invezgo API integration untuk screener IDX
===========================================================
Menggantikan data.py (Yahoo Finance) dengan data real-time dari Invezgo.

Cara pakai:
  from data_invezgo import InvezgoProvider
  provider = InvezgoProvider()
  df = provider.fetch_historical("BBCA", period="1y")
"""

import os, logging, re, warnings, time
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("invezgo")


def _to_int(v):
    """Konversi aman ke int (volume Invezgo kadang string dengan ribuan)."""
    try:
        return int(float(str(v).replace(",", "")))
    except (TypeError, ValueError):
        return 0


def _safe_code(code: str) -> str:
    """Sanitasi ticker utk nama file cache — hanya A-Z0-9 (cegah path traversal)."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())

# ── Load API Key dari .env ──
API_KEY = ""
# Coba dari environment variable
_API_KEY_ENV = os.getenv("INVEZGO_API_KEY", "")
if _API_KEY_ENV:
    API_KEY = _API_KEY_ENV
else:
    # Coba baca dari .env file di root proyek (Screener/)
    # Cari dari dir skript dan parentnya
    _script_dir=os.path.dirname(os.path.abspath(__file__))
    _search_ev=""
    for _p in [os.path.join(_script_dir,"..",".env"),_script_dir,os.path.join(_script_dir,".env")]:
        _fp=os.path.abspath(_p)
        if os.path.exists(_fp):
            _search_ev=_fp
            break
    if not _search_ev:
        # Fallback: coba path hardcode
        for _hp in ["C:\\Hermes_Workspace\\Screener\\.env",os.path.expanduser("~/.env")]:
            if os.path.exists(_hp):
                _search_ev=_hp
                break
    if _search_ev:
        with open(_search_ev, encoding="utf-8", errors="replace") as f:
            for line in f:
                line=line.strip()
                if line.startswith("INVEZGO_API_KEY="):
                    API_KEY=line.split("=",1)[1].strip().strip('"').strip("'")
                    break

# ── SDK ──
_invezgo_client = None

def get_client():
    global _invezgo_client
    if _invezgo_client is None:
        if not API_KEY:
            raise ValueError("INVEZGO_API_KEY tidak ditemukan. Set di .env atau config.yaml")
        try:
            from invezgo import InvezgoClient
            _invezgo_client = InvezgoClient(api_key=API_KEY)
        except ImportError:
            raise ImportError("invezgo-sdk belum terinstall. Jalankan: pip install invezgo-sdk")
    return _invezgo_client

class InvezgoProvider:
    """Provider data dari Invezgo API — drop-in replacement untuk Yahoo Finance."""
    
    def __init__(self):
        self.client = get_client()
        self._stock_list_cache = None
        self._cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")
    
    def get_stock_list(self):
        """Dapatkan daftar semua saham IDX."""
        if self._stock_list_cache is not None:
            return self._stock_list_cache
        data = self.client.analysis.get_stock_list()
        self._stock_list_cache = data
        return data
    
    def get_historical(self, code: str, period: str = "1y", use_cache: bool = True) -> pd.DataFrame:
        """
        Ambil data historis harian (OHLCV) dari Invezgo, dengan cache harian.

        Parameters
        ----------
        code : str
            Kode saham tanpa .JK (contoh: "BBCA")
        period : str
            "1mo", "3mo", "6mo", "1y", "2y", "max"
        use_cache : bool
            True = pakai cache file harian (default). False = fetch langsung.

        Returns
        -------
        pd.DataFrame dengan kolom: open, high, low, close, volume
        """
        code = _safe_code(code)

        # ── Cache harian: simpan per (code, period) dengan TTL 20 jam ──
        if use_cache:
            cache_path = os.path.join(self._cache_dir, f"v7_{code}_{period}.csv")
            if os.path.exists(cache_path):
                age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
                if age_hours < 20:
                    try:
                        df = pd.read_csv(cache_path, index_col=0, parse_dates=True,
                                         encoding="utf-8", encoding_errors="replace")
                        if not df.empty:
                            return df
                    except Exception:
                        pass

        # Hitung tanggal
        today = datetime.now()
        period_map = {
            "1mo": 30, "3mo": 90, "6mo": 180,
            "1y": 365, "2y": 730, "max": 730  # Invezgo max 2 tahun
        }
        days = period_map.get(period, 365)
        from_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        try:
            data = self.client.analysis.get_chart_stock(code=code, from_date=from_date, to_date=to_date)
            if not data:
                logger.warning("Data kosong untuk %s", code)
                return pd.DataFrame()
            
            # Konversi ke DataFrame
            rows = []
            for item in data:
                if "date" not in item and "Date" not in item:
                    continue
                rows.append({
                    "Date": pd.to_datetime(item.get("date", item.get("Date", ""))),
                    "Open": float(item.get("open", item.get("Open", 0))),
                    "High": float(item.get("high", item.get("High", 0))),
                    "Low": float(item.get("low", item.get("Low", 0))),
                    "Close": float(item.get("close", item.get("Close", 0))),
                    "Volume": int(item.get("volume", item.get("Volume", 0))),
                })
            
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            df.set_index("Date", inplace=True)
            df.sort_index(inplace=True)
            df = df[~df.index.duplicated(keep='last')]
            # Strip timezone biar kompatibel dengan data.py (non-UTC index)
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            # Tambah lowercase aliases untuk kompatibilitas compute_all_indicators
            for col in ["Open","High","Low","Close","Volume"]:
                if col in df.columns:
                    df[col.lower()]=df[col]

            # Simpan cache
            if use_cache:
                try:
                    os.makedirs(self._cache_dir, exist_ok=True)
                    df.to_csv(cache_path, encoding="utf-8")
                except Exception:
                    pass
            return df
            
        except Exception as e:
            logger.error("Gagal ambil data historis %s: %s", code, e)
            return pd.DataFrame()
    
    def get_index_history(self, code: str = "COMPOSITE", period: str = "2y", use_cache: bool = True) -> pd.DataFrame:
        """
        Ambil data historis INDEKS dari Invezgo (IHSG = COMPOSITE), cache harian.
        Drop-in untuk fetch_ihsg_cached() — kolom lowercase OHLCV sama seperti
        get_historical(). Invezgo max 2 tahun (sama seperti get_historical).

        Parameters
        ----------
        code : str — kode indeks Invezgo ("COMPOSITE" = IHSG, "LQ45", dll)
        period : str — "1mo", "3mo", "6mo", "1y", "2y", "max"
        use_cache : bool — True = pakai cache file (TTL 20 jam, default)

        Returns
        -------
        pd.DataFrame dengan kolom open, high, low, close, volume
        """
        code = _safe_code(code)

        # ── Cache harian: sama pola dengan get_historical ──
        if use_cache:
            cache_path = os.path.join(self._cache_dir, f"v7_IDX_{code}_{period}.csv")
            if os.path.exists(cache_path):
                age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
                if age_hours < 20:
                    try:
                        df = pd.read_csv(cache_path, index_col=0, parse_dates=True,
                                         encoding="utf-8", encoding_errors="replace")
                        if not df.empty:
                            return df
                    except Exception:
                        pass

        today = datetime.now()
        period_map = {
            "1mo": 30, "3mo": 90, "6mo": 180,
            "1y": 365, "2y": 730, "max": 730  # Invezgo max 2 tahun
        }
        days = period_map.get(period, 365)
        from_date = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        to_date = today.strftime("%Y-%m-%d")

        try:
            data = self.client.analysis.get_chart_index(code=code, from_date=from_date, to_date=to_date)
            if not data:
                logger.warning("Data kosong untuk indeks %s", code)
                return pd.DataFrame()

            rows = []
            for item in data:
                if "date" not in item:
                    continue
                rows.append({
                    "Date": pd.to_datetime(item.get("date", "")),
                    "Open": float(item.get("open", 0)),
                    "High": float(item.get("high", 0)),
                    "Low": float(item.get("low", 0)),
                    "Close": float(item.get("close", 0)),
                    "Volume": _to_int(item.get("volume", 0)),
                })

            df = pd.DataFrame(rows)
            if df.empty:
                return df
            df.set_index("Date", inplace=True)
            df.sort_index(inplace=True)
            df = df[~df.index.duplicated(keep='last')]
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                if col in df.columns:
                    df[col.lower()] = df[col]

            if use_cache:
                try:
                    os.makedirs(self._cache_dir, exist_ok=True)
                    df.to_csv(cache_path, encoding="utf-8")
                except Exception:
                    pass
            return df

        except Exception as e:
            logger.error("Gagal ambil historis indeks %s: %s", code, e)
            return pd.DataFrame()

    def get_fundamental(self, code: str):
        """Ambil data fundamental (PER, PBV, ROE, dll) dari Invezgo."""
        code = _safe_code(code)
        try:
            keystat = self.client.analysis.get_keystat(code=code, type_period="Q", limit=8)
            if not keystat or "rows" not in keystat:
                return {}
            
            result = {}
            for row in keystat["rows"]:
                name = row.get("name", "")
                values = row.get("values", [])
                if values and len(values) > 0:
                    latest = values[-1]
                    val = latest.get("amount", None)
                    if val is not None:
                        result[name] = val
            return result
        except Exception as e:
            logger.debug("Gagal ambil fundamental %s: %s", code, e)
            return {}
    
    def get_financial_statement(self, code: str, statement: str = "IS", limit: int = 4):
        """Ambil laporan keuangan: IS (labarugi), BS (neraca), CF (aruskas)."""
        code = _safe_code(code)
        try:
            return self.client.analysis.get_financial_statement(
                code=code, statement=statement, type_period="Q", limit=limit
            )
        except Exception as e:
            logger.debug("Gagal ambil financial %s: %s", code, e)
            return {}
    
    def get_broker_summary(self, code: str, days: int = 5):
        """Ambil data broker summary & foreign flow."""
        code = _safe_code(code)
        try:
            to_date = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            return self.client.analysis.get_summary_stock(
                code=code, from_date=from_date, to_date=to_date, investor="all", market="RG"
            )
        except Exception as e:
            logger.debug("Gagal ambil broker summary %s: %s", code, e)
            return {}
    
    def get_intraday(self, code: str):
        """Ambil snapshot harga real-time."""
        code = _safe_code(code)
        try:
            data = self.client.analysis.get_intraday_data(code=code, market="RG")
            if data and isinstance(data, dict):
                return {
                    "price": float(data.get("price", 0)),
                    "change": float(data.get("change", "0%").replace("%", "")),
                    "open": float(data.get("open", 0)),
                    "high": float(data.get("high", 0)),
                    "low": float(data.get("low", 0)),
                    "close": float(data.get("close", 0)),
                    "volume": int(data.get("volume", 0)),
                }
            return {}
        except Exception as e:
            logger.debug("Gagal ambil intraday %s: %s", code, e)
            return {}


# ── Test ──
if __name__ == "__main__":
    p = InvezgoProvider()
    df = p.get_historical("BBCA", period="1mo")
    print(df.tail())
    
    fund = p.get_fundamental("BBCA")
    print("Fundamental:", fund)
    
    broker = p.get_broker_summary("BBCA")
    print("Broker:", broker)
