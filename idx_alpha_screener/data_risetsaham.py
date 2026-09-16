"""data_risetsaham.py — Provider data pengganti Invezgo utk screener V7.

Sumber data = RISETSAHAM lokal (~/risetsaham): modul `yahoo.py` (OHLCV 1 tahun +
fundamental + harga live Yahoo) dan `broker.py` (broker summary Stockbit, net
asing resmi). TIDAK butuh API key Invezgo.

Kelas `InvezgoProvider` di file ini DROP-IN: nama metode + bentuk return SAMA
dengan `data_invezgo.InvezgoProvider`, sehingga v7_scan.py / v7/* / data.py /
weekly_report.py / position_check_intraday.py cukup ganti import via
`data_provider.py`:

    from data_provider import InvezgoProvider

Adaptasi & degradasi yang JUJUR (tidak crash, tetap jalan):
  - get_broker_summary / get_broker_foreign_summary: Stockbit marketdetectors
    periode LATEST (~1 hari; Invezgo agregat 3 hari) → magnitudo lebih kecil,
    faktor flow V7 tetap menghitung dgn ambang yang sama.
  - get_broker_flow_history: deret NET ASING harian (bukan net semua-broker;
    endpoint Stockbit hanya menyediakan agregat range, jadi per-hari + cache
    permanen + backfill bertahap sampai `days` hari).
  - get_index_intraday: Yahoo 5-menit ^JKSE (range 5d) — key sama (datetime/close).
  - get_financial_statement: laporan kuartalan Yahoo (IS/BS) → bentuk "rows" ala
    Invezgo; kalau gagal → {} (faktor earnings netral 40).
  - get_corporate_calendar: [] — Invezgo-only; blackout corporate action OFF
    (dicatat sebagai keterbatasan, bukan error).
"""
from __future__ import annotations

import importlib
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import pandas as pd

RISET_DIR = os.environ.get("RISETSAHAM_DIR", "/home/yuan/risetsaham")
if RISET_DIR not in sys.path:
    sys.path.append(RISET_DIR)  # append: modul screener sendiri tetap menang

_ry = importlib.import_module("yahoo")     # risetsaham/yahoo.py
_rb = importlib.import_module("broker")    # risetsaham/broker.py

WIB = timezone(timedelta(hours=7))
_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_CACHE_DIR = os.path.join(_DATA_DIR, "cache_v7")

_PERIOD_BARS = {"5d": 5, "1mo": 23, "3mo": 66, "6mo": 130,
                "1y": 260, "2y": 520, "max": 100000}


def _api_code(code: str) -> str:
    """'BBCA.JK' / 'bbca' → 'BBCA' (tanpa suffix)."""
    return re.sub(r"\.JK$", "", str(code or "").strip().upper())


def _safe_code(code: str) -> str:
    """Sanitasi utk nama file cache — hanya A-Z0-9."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def _df_ohlcv(rows) -> pd.DataFrame:
    """rows Yahoo [{t,o,h,l,c,v}] → DataFrame OHLCV (index Date, kolom kapital + lowercase)."""
    recs = []
    for r in rows or []:
        try:
            c = r.get("c")
            if c is None:
                continue
            t = r.get("t")
            ts = pd.to_datetime(t, unit="s") if isinstance(t, (int, float)) else pd.to_datetime(t)
            recs.append({"Date": ts, "Open": r.get("o"), "High": r.get("h"),
                         "Low": r.get("l"), "Close": c, "Volume": r.get("v") or 0})
        except Exception:
            continue
    if not recs:
        return pd.DataFrame()
    df = pd.DataFrame(recs).set_index("Date").sort_index()
    df = df[~df.index.duplicated(keep="last")]
    try:
        if getattr(df.index, "tz", None) is not None:
            df.index = df.index.tz_localize(None)
    except Exception:
        pass
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col.lower()] = df[col]
    return df


class InvezgoProvider:  # nama kelas DIPERTAHANKAN agar drop-in
    """Provider data risetsaham — lihat docstring modul."""

    def __init__(self):
        self._cache_dir = _CACHE_DIR
        os.makedirs(self._cache_dir, exist_ok=True)

    # ── cache CSV histori (pola sama dgn data_invezgo: TTL 20 jam) ──
    def _hist_load(self, fname: str):
        p = os.path.join(self._cache_dir, fname)
        try:
            if os.path.exists(p) and (time.time() - os.path.getmtime(p)) / 3600 < 20:
                df = pd.read_csv(p, index_col=0, parse_dates=True)
                if not df.empty:
                    return df
        except Exception:
            pass
        return None

    def _hist_save(self, fname: str, df: pd.DataFrame):
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            df.to_csv(os.path.join(self._cache_dir, fname), encoding="utf-8")
        except Exception:
            pass

    # ── OHLCV ──────────────────────────────────────────────────────────
    def get_historical(self, code: str, period: str = "1y", use_cache: bool = True) -> pd.DataFrame:
        code_s = _safe_code(code)
        if use_cache:
            c = self._hist_load(f"v7_{code_s}_{period}.csv")
            if c is not None:
                return c
        sym = _api_code(code) + ".JK"
        try:
            d = _ry.fetch_daily(sym)
        except Exception:
            d = None
        df = _df_ohlcv((d or {}).get("rows"))
        df = df.tail(_PERIOD_BARS.get(str(period), 260)) if not df.empty else df
        if use_cache and not df.empty:
            self._hist_save(f"v7_{code_s}_{period}.csv", df)
        return df

    def get_index_history(self, code: str = "COMPOSITE", period: str = "2y",
                          use_cache: bool = True) -> pd.DataFrame:
        code_s = _safe_code(code)
        if use_cache:
            c = self._hist_load(f"v7_IDX_{code_s}_{period}.csv")
            if c is not None:
                return c
        sym = "^JKSE" if str(code).upper() in ("COMPOSITE", "IHSG") else code
        try:
            d = _ry.fetch_daily(sym)
        except Exception:
            d = None
        df = _df_ohlcv((d or {}).get("rows"))
        df = df.tail(_PERIOD_BARS.get(str(period), 520)) if not df.empty else df
        if use_cache and not df.empty:
            self._hist_save(f"v7_IDX_{code_s}_{period}.csv", df)
        return df

    # ── Fundamental ────────────────────────────────────────────────────
    def get_fundamental(self, code: str):
        """PER/PBV/ROE/Dividend Yield (%) dari Yahoo — nama key utk v7."""
        try:
            f = _ry.fetch_fundamentals(_api_code(code) + ".JK") or {}
        except Exception:
            return {}

        def num(k):
            v = f.get(k)
            if v is None:
                return None
            try:
                return float(str(v).replace("%", "").replace(",", "").strip())
            except Exception:
                return None

        per, pbv = num("trailingPE"), num("priceToBook")
        roe, div = num("returnOnEquity"), num("dividendYield")
        if roe is not None and abs(roe) <= 1.5:
            roe *= 100.0          # pecahan → persen (ROE v7 ideal >15)
        if div is not None and div <= 1.0:
            div *= 100.0          # pecahan → persen (div yield v7 ideal >3)
        out = {}
        if per is not None:
            out["PER"] = per
        if pbv is not None:
            out["PBV"] = pbv
        if roe is not None:
            out["ROE"] = roe
        if div is not None:
            out["Dividend Yield"] = div
        return out

    def _yahoo_summary(self, sym: str, modules: str):
        """quoteSummary mentah (pakai crumb+cookie milik risetsaham/yahoo.py)."""
        try:
            crumb = _ry._get_crumb() or _ry._get_crumb(force=True)
            if not crumb:
                return None
            url = (f"{_ry.BASE}/v10/finance/quoteSummary/{urllib.parse.quote(sym)}"
                   f"?modules={modules}&crumb={urllib.parse.quote(crumb)}")
            raw = _ry._opener_get().open(urllib.request.Request(url), timeout=25).read()
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return None

    def _bs_fallback_rows(self, code: str) -> list:
        """BS sintetis dari financialData.debtToEquity — cukup utk RASIO D/E v7.

        Yahoo utk banyak emiten IDX mengembalikan balanceSheetStatements TANPA
        angka (hanya endDate). Faktor earnings memakai liabilitas/ekuitas saja →
        ekuitas=100 & liabilitas=debtToEquity (persen Yahoo, mis. 133,06 → 1,33x).
        """
        try:
            d = self._yahoo_summary(_api_code(code) + ".JK", "financialData")
            res = ((d or {}).get("quoteSummary") or {}).get("result") or []
            fd = (res[0].get("financialData") if res else {}) or {}
            de = fd.get("debtToEquity")
            if isinstance(de, dict):
                de = de.get("raw")
            de = float(de)
            if not math.isfinite(de):
                return []
        except Exception:
            return []
        now = datetime.now()
        year, period = now.year, "Q%d" % ((now.month - 1) // 3 + 1)
        return [{"name": "Total Liabilitas", "level": 0,
                 "values": [{"year": year, "period": period, "amount": de}]},
                {"name": "Total Ekuitas", "level": 0,
                 "values": [{"year": year, "period": period, "amount": 100.0}]}]

    def get_financial_statement(self, code: str, statement: str = "IS", limit: int = 4):
        """Laporan kuartalan Yahoo → {"rows":[{name,level,values:[{year,period,amount}]}]}.

        Dipakai faktor earnings (rev growth YoY/QoQ, margin trend, D/E).
        Cache data/fundamental_yahoo_{CODE}.json TTL 7 hari. Gagal → {}.
        """
        code_s = _safe_code(code)
        cache = os.path.join(_DATA_DIR, f"fundamental_yahoo_{code_s}.json")
        data = None
        try:
            if os.path.exists(cache) and (time.time() - os.path.getmtime(cache)) / 3600 < 24 * 7:
                with open(cache, encoding="utf-8") as f:
                    data = json.load(f)
        except Exception:
            data = None
        if not data:
            d = self._yahoo_summary(_api_code(code) + ".JK",
                                    "incomeStatementHistoryQuarterly,"
                                    "balanceSheetHistoryQuarterly")
            res = ((d or {}).get("quoteSummary") or {}).get("result") or []
            if res:
                r = res[0]

                def qkey(s):
                    try:
                        dt = datetime.strptime(str(s)[:10], "%Y-%m-%d")
                        return dt.year, "Q%d" % ((dt.month - 1) // 3 + 1)
                    except Exception:
                        return None

                def series(items, field):
                    vals = []
                    for it in items or []:
                        end = it.get("endDate")
                        if isinstance(end, dict):
                            end = end.get("fmt") or end.get("raw")
                        yq = qkey(end)
                        amt = it.get(field)
                        if isinstance(amt, dict):
                            amt = amt.get("raw")
                        try:
                            amt = float(amt)
                        except Exception:
                            continue
                        if yq and math.isfinite(amt):
                            vals.append({"year": yq[0], "period": yq[1], "amount": amt})
                    vals.sort(key=lambda x: (x["year"], x["period"]))
                    return vals[-max(int(limit), 8):]

                is_items = (r.get("incomeStatementHistoryQuarterly") or {}).get("incomeStatementHistory") or []
                bs_items = (r.get("balanceSheetHistoryQuarterly") or {}).get("balanceSheetStatements") or []
                data = {"IS": {"rows": [
                            {"name": "Total Pendapatan", "level": 0, "values": series(is_items, "totalRevenue")},
                            {"name": "Laba Bersih", "level": 0, "values": series(is_items, "netIncome")},
                        ]},
                        "BS": {"rows": [
                            {"name": "Total Liabilitas", "level": 0, "values": series(bs_items, "totalLiab")},
                            {"name": "Total Ekuitas", "level": 0, "values": series(bs_items, "totalStockholderEquity")},
                        ]}}
                try:
                    tmp = cache + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    os.replace(tmp, cache)
                except Exception:
                    pass
        if not data:
            return {}
        st = data.get(str(statement).upper()) or {}
        rows = [r for r in st.get("rows", []) if r.get("values")]
        if not rows and str(statement).upper() == "BS":
            # Laporan kuartalan Yahoo utk IDX sering kosong (hanya endDate) →
            # fallback rasio dari financialData.debtToEquity.
            rows = self._bs_fallback_rows(code)
            if rows:
                data.setdefault("BS", {})["rows"] = rows
                try:
                    tmp = cache + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    os.replace(tmp, cache)
                except Exception:
                    pass
        return {"rows": rows} if rows else {}

    # ── Broker (Stockbit via risetsaham/broker.py) ─────────────────────
    def _broker_rows(self, code: str):
        """Gabung daftar beli+jual Stockbit → [{code, buy_value, sell_value, tipe}]."""
        try:
            p = _rb.ambil(_api_code(code))
        except Exception:
            p = None
        if not p:
            return []
        acc = {}
        for sisi, key in (("beli", "buy_value"), ("jual", "sell_value")):
            for it in (p.get(sisi) or []):
                kode = str(it.get("kode") or "").strip().upper()
                if not kode or kode == "?":
                    continue
                try:
                    v = abs(float(it.get("nilai") or 0))
                except Exception:
                    continue
                d = acc.setdefault(kode, {"code": kode, "buy_value": 0.0,
                                          "sell_value": 0.0, "tipe": it.get("tipe") or "—"})
                d[key] += v
        return list(acc.values())

    def get_broker_summary(self, code: str, days: int = 5):
        rows = self._broker_rows(code)
        return [{"code": r["code"], "buy_value": r["buy_value"],
                 "sell_value": r["sell_value"]} for r in rows]

    def get_broker_foreign_summary(self, code: str, days: int = 3):
        rows = [r for r in self._broker_rows(code)
                if str(r.get("tipe", "")).strip().lower() == "asing"]
        return [{"code": r["code"], "buy_value": r["buy_value"],
                 "sell_value": r["sell_value"]} for r in rows]

    def get_broker_flow_history(self, code: str, days: int = 20, use_cache: bool = True) -> list:
        """Deret harian NET ASING (proxy bandarmologi) — [{date, net_buy}] ascending.

        Cache permanen data/broker_flow_hist_{CODE}.json (merge tanggal baru,
        tidak pernah hilang). Backfill: tanggal yang belum ada diambil per-hari
        (1 panggilan/hari, jeda 0.12 dtk) maks `days` hari terbaru per run.
        Tanggal tanpa data (libur) ditandai & hanya diulang <7 hari.
        """
        code_s = _safe_code(code)
        sym = _api_code(code)
        path = os.path.join(_DATA_DIR, f"broker_flow_hist_{code_s}.json")
        days_map, kosong = {}, {}
        try:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    days_map = {k: v for k, v in (obj.get("days") or {}).items()
                                if isinstance(v, (int, float)) and math.isfinite(float(v))}
                    kosong = obj.get("kosong") or {}
        except Exception:
            days_map, kosong = {}, {}

        today = datetime.now(WIB).date()
        need = []
        for i in range(int(days * 1.7) + 3):
            ddate = today - timedelta(days=i)
            if ddate.weekday() >= 5:
                continue  # Sabtu/Minggu — pasar tutup, tidak difetch
            d = ddate.isoformat()
            if d in days_map:
                continue
            if d in kosong:
                try:
                    umur = (today - datetime.strptime(d, "%Y-%m-%d").date()).days
                except Exception:
                    umur = 99
                if umur > 7:
                    continue  # libur lama — tidak diulang
            need.append(d)
        need = need[:int(days)]

        for d in need:
            r = None
            try:
                r = _rb.foreign_flow(sym, dari=d, sampai=d)
            except Exception:
                r = None
            net = (r or {}).get("net")
            if net is None:
                kosong[d] = time.time()
                continue
            try:
                net = float(net)
            except Exception:
                continue
            if not math.isfinite(net):
                continue
            days_map[d] = net
            try:
                time.sleep(0.12)  # santun ke Stockbit
            except Exception:
                pass

        if days_map:
            try:
                os.makedirs(_DATA_DIR, exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"diupdate": time.time(), "days": days_map, "kosong": kosong}, f)
                os.replace(tmp, path)
            except Exception:
                pass

        keys = sorted(days_map)[-int(days):]
        return [{"date": k, "net_buy": float(days_map[k])} for k in keys]

    # ── Intraday ───────────────────────────────────────────────────────
    def get_intraday(self, code: str):
        """Snapshot harga terkini (Yahoo live + bar harian terakhir)."""
        sym = _api_code(code) + ".JK"
        try:
            last = _ry.fetch_last(sym)
        except Exception:
            last = None
        if not last:
            return {}
        row = {}
        try:
            d = _ry.fetch_daily(sym)
            rows = (d or {}).get("rows") or []
            row = rows[-1] if rows else {}
        except Exception:
            row = {}
        px = last.get("last") or row.get("c") or 0
        return {"price": float(px or 0),
                "change": float(last.get("chg_pct") or 0),
                "open": float(row.get("o") or 0),
                "high": float(row.get("h") or 0),
                "low": float(row.get("l") or 0),
                "close": float(row.get("c") or px or 0),
                "volume": int(row.get("v") or 0)}

    def get_index_intraday(self, code: str = "COMPOSITE", days: int = 5,
                           timeframe: str = "5", use_cache: bool = True) -> list:
        """Deret 5-menit IHSG dari Yahoo (^JKSE) — key datetime/close dipakai v7."""
        cache = os.path.join(_DATA_DIR, f"intraday_idx_yahoo_{int(days)}d_{timeframe}.json")
        if use_cache and os.path.exists(cache):
            try:
                if (time.time() - os.path.getmtime(cache)) / 3600 < 8:
                    with open(cache, encoding="utf-8") as f:
                        return json.load(f)
            except Exception:
                pass
        rng = min(max(int(days), 1), 5)
        interval = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1h"}.get(str(timeframe), "5m")
        url = (f"https://query2.finance.yahoo.com/v8/finance/chart/%5EJKSE"
               f"?range={rng}d&interval={interval}")
        try:
            d = _ry._json_get(url)
            r = (d.get("chart") or {}).get("result")[0]
            q = (r.get("indicators") or {}).get("quote")[0]
            ts = r.get("timestamp") or []
            out = []
            for i, t in enumerate(ts):
                c = (q.get("close") or [None])[i]
                if c is None:
                    continue
                dt = datetime.fromtimestamp(t, tz=WIB)
                out.append({"datetime": dt.strftime("%Y-%m-%d %H:%M"),
                            "date": dt.strftime("%Y-%m-%d"),
                            "time": dt.strftime("%H:%M"),
                            "open": (q.get("open") or [None])[i],
                            "high": (q.get("high") or [None])[i],
                            "low": (q.get("low") or [None])[i],
                            "close": c,
                            "volume": (q.get("volume") or [0])[i] or 0})
            try:
                os.makedirs(_DATA_DIR, exist_ok=True)
                tmp = cache + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(out[-400:], f)
                os.replace(tmp, cache)
            except Exception:
                pass
            return out
        except Exception:
            return []

    # ── Invezgo-only → degradasi jujur ─────────────────────────────────
    def get_corporate_calendar(self, code: str, use_cache: bool = True) -> list:
        """Tidak tersedia via data lokal — [] (blackout CA nonaktif)."""
        return []

    def get_stock_list(self):
        """Tidak dipakai runtime (universe dari config.yaml)."""
        return []
