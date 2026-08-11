"""
data_invezgo.py — Invezgo API integration untuk screener IDX
===========================================================
Menggantikan data.py (Yahoo Finance) dengan data real-time dari Invezgo.

Cara pakai:
  from data_invezgo import InvezgoProvider
  provider = InvezgoProvider()
  df = provider.fetch_historical("BBCA", period="1y")
"""

import os, logging, re, warnings, time, json, math
from datetime import datetime, timedelta
from typing import Optional
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("invezgo")

# Dir data/ — cache CA calendar (IDE5) + fundamental; dipatch di test supaya
# I/O tidak menyentuh data/ asli (pola sama dengan v7_engine._DATA_DIR).
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _to_int(v):
    """Konversi aman ke int (volume Invezgo kadang string dengan ribuan)."""
    try:
        s = str(v).strip()
        if not s:
            return 0
        # NB2: titik ribuan ala Indonesia ('1.234.567') — dulu
        # float('1.234.567') gagal → volume diam-diam jadi 0 (saham ke-filter).
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")
        else:
            s = s.replace(",", "")
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _to_float(v):
    """Konversi aman ke float — handle format angka Indonesia.

    - '1,5'       → 1.5      (koma desimal)
    - '1.234.567' → 1234567.0 (titik ribuan, tanpa koma)
    - '1,234'     → 1234.0   (koma ribuan — 3 digit persis di akhir)
    - '1.234,5' / '7.000,5' → 1234.5 / 7000.5 (titik ribuan + koma desimal)
    """
    try:
        if v is None:
            return 0.0
        s = str(v).strip()
        if not s:
            return 0.0
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
            s = s.replace(".", "")                      # '1.234.567' -> '1234567'
        elif re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s):
            s = s.replace(".", "").replace(",", ".")    # '7.000,5' -> '7000.5'
        elif re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")                      # '1,234' -> '1234'
        elif "," in s:
            s = s.replace(",", ".")                     # '1,5' -> '1.5'
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _to_float_opt(v):
    """Konversi nilai fundamental API ke float; None kalau bukan angka.

    L7: get_fundamental dulu menyimpan string mentah dari API (format
    Indonesia '1.234,5' dsb.) → np.isnan(string) TypeError di v7 factor.
    Bedakan '0'/'0,0' yang sah dari string tak-terparse ('N/A' → None).
    """
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    f = _to_float(v)
    if f == 0.0 and not re.fullmatch(r"[-+]?\d[\d.,\s]*", s):
        return None
    return f


def _api_code(code: str) -> str:
    """Kode utk API Invezgo — uppercase + buang suffix bursa (.JK).

    TIDAK memakai _safe_code (yang menghapus semua karakter non-A-Z0-9):
    kode 'BBCA.JK' harus tetap 'BBCA' untuk API, bukan 'BBCAJK' (404).
    Sanitasi ketat tetap dipakai HANYA untuk nama file cache.
    """
    return str(code or "").upper().replace(".JK", "")


def _safe_code(code: str) -> str:
    """Sanitasi ticker utk nama file cache — hanya A-Z0-9 (cegah path traversal)."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


# Format tanggal payload calendar Invezgo (ID: '20/08/2026'; ISO; dsb.)
_CAL_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y")

def _parse_calendar_date(v) -> str:
    """Parse string tanggal payload CA calendar Invezgo → 'YYYY-MM-DD' atau ''."""
    if v is None:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    for fmt in _CAL_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""

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
        api_code = _api_code(code)   # utk API — 'BBCA.JK' -> 'BBCA' (bukan 'BBCAJK')
        code = _safe_code(code)      # sanitasi ketat HANYA utk nama file cache

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
            data = self.client.analysis.get_chart_stock(code=api_code, from_date=from_date, to_date=to_date)
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
                    "Open": _to_float(item.get("open", item.get("Open", 0))),
                    "High": _to_float(item.get("high", item.get("High", 0))),
                    "Low": _to_float(item.get("low", item.get("Low", 0))),
                    "Close": _to_float(item.get("close", item.get("Close", 0))),
                    "Volume": _to_int(item.get("volume", item.get("Volume", 0))),
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
        api_code = _api_code(code)   # utk API — kode indeks (COMPOSITE) tetap utuh
        code = _safe_code(code)      # sanitasi ketat HANYA utk nama file cache

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
            data = self.client.analysis.get_chart_index(code=api_code, from_date=from_date, to_date=to_date)
            if not data:
                logger.warning("Data kosong untuk indeks %s", code)
                return pd.DataFrame()

            rows = []
            for item in data:
                if "date" not in item:
                    continue
                rows.append({
                    "Date": pd.to_datetime(item.get("date", "")),
                    # NB1: pakai _to_float (bukan float() mentah) — string format
                    # ID '7.000,5' dulu ValueError → IHSG gagal diam-diam ke fallback
                    "Open": _to_float(item.get("open", 0)),
                    "High": _to_float(item.get("high", 0)),
                    "Low": _to_float(item.get("low", 0)),
                    "Close": _to_float(item.get("close", 0)),
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
        api_code = _api_code(code)
        code = _safe_code(code)
        try:
            keystat = self.client.analysis.get_keystat(code=api_code, type_period="Q", limit=8)
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
                        # L7: konversi ke float SEBELUM dikembalikan — string
                        # mentah API (format ID '1.234,5') tidak boleh lolos ke
                        # pemakai (np.isnan(string) → TypeError di v7 factor);
                        # nilai tak-terparse → None (bukan string / 0.0 palsu).
                        result[name] = _to_float_opt(val)
            return result
        except Exception as e:
            logger.debug("Gagal ambil fundamental %s: %s", code, e)
            return {}
    
    def get_financial_statement(self, code: str, statement: str = "IS", limit: int = 4):
        """Ambil laporan keuangan: IS (labarugi), BS (neraca), CF (aruskas)."""
        api_code = _api_code(code)
        code = _safe_code(code)
        try:
            return self.client.analysis.get_financial_statement(
                code=api_code, statement=statement, type_period="Q", limit=limit
            )
        except Exception as e:
            logger.debug("Gagal ambil financial %s: %s", code, e)
            return {}
    
    def get_broker_summary(self, code: str, days: int = 5):
        """Ambil data broker summary & foreign flow."""
        api_code = _api_code(code)
        code = _safe_code(code)
        try:
            to_date = datetime.now().strftime("%Y-%m-%d")
            from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            return self.client.analysis.get_summary_stock(
                code=api_code, from_date=from_date, to_date=to_date, investor="all", market="RG"
            )
        except Exception as e:
            logger.debug("Gagal ambil broker summary %s: %s", code, e)
            return {}

    def get_broker_flow_history(self, code: str, days: int = 20, use_cache: bool = True) -> list:
        """Broker flow HISTORIS harian (IDE4 — bandarmologi pembeda).

        Sumber: SDK analysis.get_inventory_chart_stock (bukan
        get_summary_chart_stock — verifikasi API nyata 08/2026: endpoint itu
        hanya infographic 4 item {label,value,fill} = D/F Buy/Sell untuk SATU
        periode, BUKAN deret harian). get_inventory_chart_stock mengembalikan
        time series per broker: {"price": [{date,open,high,low,close,volume}],
        "broker": [{"broker": kode, "data": [{date, value}]}]} dengan value =
        net harian broker tsb (rupiah; negatif = jual bersih, positif = beli
        bersih — terverifikasi non-kumulatif dari data BRPT).

        Net buy per hari = Σ value semua broker pada tanggal tsb (investor
        ALL — asing + lokal; breakdown terpisah bisa didapat via
        investor='f'/'d' tapi butuh 1 call tambahan per ticker per hari).

        Return list of dict ascending by date:
            [{"date": "YYYY-MM-DD", "net_buy": float}, ...]
        (maks `days` entri terbaru). Error / bentuk respons tak dikenal → []
        + warning (scan TIDAK boleh crash). Cache data/broker_flow_hist_{CODE}.json
        TTL 24 jam (pola get_corporate_calendar); hasil KOSONG tidak dicache
        supaya data yang baru tersedia muncul di scan berikutnya.
        """
        api_code = _api_code(code)
        safe = _safe_code(code)
        cache_path = os.path.join(_DATA_DIR, f"broker_flow_hist_{safe}.json")
        if use_cache and os.path.exists(cache_path):
            age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
            if age_hours < 24:
                try:
                    with open(cache_path, encoding="utf-8") as f:
                        cached = json.load(f)
                    if isinstance(cached, list) and cached:
                        return cached
                except Exception:
                    pass

        rows = []
        try:
            to_date = datetime.now().strftime("%Y-%m-%d")
            # Buffer kalender ~1.8x+10 hari supaya dapat >= days hari trading
            # (akhir pekan/libur); 30 hari kalender → 21 hari trading (nyata).
            from_date = (datetime.now() - timedelta(days=int(days * 1.8) + 10)).strftime("%Y-%m-%d")
            data = self.client.analysis.get_inventory_chart_stock(
                code=api_code, from_date=from_date, to_date=to_date,
                scope="val", investor="all", market="ALL",
            )
            if isinstance(data, dict):
                by_date = {}
                for br in (data.get("broker") or []):
                    if not isinstance(br, dict):
                        continue
                    for item in (br.get("data") or []):
                        if not isinstance(item, dict):
                            continue
                        d = str(item.get("date", "") or "").strip()
                        if not d:
                            continue
                        try:
                            v = float(item.get("value"))
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(v):
                            continue
                        by_date[d] = by_date.get(d, 0.0) + v
                rows = [{"date": d, "net_buy": by_date[d]} for d in sorted(by_date)][-int(days):]
        except Exception as e:
            logger.warning("Gagal ambil broker flow history %s: %s", code, e)
            rows = []
        if use_cache and rows:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(rows, f)
            except Exception:
                pass
        return rows

    
    def get_intraday(self, code: str):
        """Ambil snapshot harga real-time."""
        api_code = _api_code(code)
        code = _safe_code(code)
        try:
            data = self.client.analysis.get_intraday_data(code=api_code, market="RG")
            if data and isinstance(data, dict):
                return {
                    "price": float(data.get("price", 0)),
                    "change": _to_float((data.get("change", "0%") or "0%").replace("%", "")),
                    "open": float(data.get("open", 0)),
                    "high": float(data.get("high", 0)),
                    "low": float(data.get("low", 0)),
                    "close": float(data.get("close", 0)),
                    "volume": _to_int(data.get("volume", 0)),
                }
            return {}
        except Exception as e:
            logger.debug("Gagal ambil intraday %s: %s", code, e)
            return {}

    def get_corporate_calendar(self, code: str, use_cache: bool = True) -> list:
        """Calendar corporate action Invezgo (IDE5) — get_calendar + cache 24 jam.

        SDK: analysis.get_calendar(code=..., limit=50) → response
        {data: [{code, type, payload{...tanggal...}}]}. Tanggal event di-parse
        dari payload (TradingPeriodStr / ExcPeriodStr / ...) — beberapa format
        tanggal ID/ISO didukung.

        Return list of dict: [{"type": "RUPS_RESULT", "date": "YYYY-MM-DD"}, ...]
        HANYA event yang tanggalnya ter-parse. Kalau SDK gagal / response aneh →
        [] (scan TIDAK boleh crash karena calendar). Cache data/calendar_{CODE}.json
        TTL 24 jam (hanya hasil NON-kosong — hasil kosong tidak dicache supaya
        event baru muncul di scan berikutnya).

        Dipakai v7_scan._ca_calendar_check untuk blackout sinyal (IDE5).
        """
        api_code = _api_code(code)
        safe = _safe_code(code)
        cache_path = os.path.join(_DATA_DIR, f"calendar_{safe}.json")
        if use_cache and os.path.exists(cache_path):
            age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
            if age_hours < 24:
                try:
                    with open(cache_path, encoding="utf-8") as f:
                        cached = json.load(f)
                    if isinstance(cached, list):
                        return cached
                except Exception:
                    pass
        events = []
        try:
            data = self.client.analysis.get_calendar(code=api_code, limit=50)
            items = (data or {}).get("data") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                etype = str(item.get("type", "") or "").strip().upper()
                if not etype:
                    continue
                payload = item.get("payload") or {}
                if not isinstance(payload, dict):
                    payload = {}
                ev_date = ""
                for key in ("TradingPeriodStr", "ExcPeriodStr", "TradingPeriodStart",
                            "ExcPeriodStart", "TradingPeriodEnd", "ExcPeriodEnd"):
                    ev_date = _parse_calendar_date(payload.get(key))
                    if ev_date:
                        break
                if not ev_date:
                    continue
                events.append({"type": etype, "date": ev_date})
        except Exception as e:
            logger.debug("Gagal ambil calendar %s: %s", code, e)
            events = []
        if use_cache and events:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(events, f)
            except Exception:
                pass
        return events


# ── Test ──
if __name__ == "__main__":
    p = InvezgoProvider()
    df = p.get_historical("BBCA", period="1mo")
    print(df.tail())
    
    fund = p.get_fundamental("BBCA")
    print("Fundamental:", fund)
    
    broker = p.get_broker_summary("BBCA")
    print("Broker:", broker)
