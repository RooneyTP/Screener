"""
v7 — Invezgo-powered Enhanced Scoring Engine
=============================================
Memanfaatkan data eksklusif dari Invezgo yang tidak ada di Yahoo:
  1. Broker Flow (bandarmologi) — deteksi akumulasi/distribusi institusi
  2. Sector Akurat — sector rotation analysis
  3. Fundamental Quality — PER, PBV, ROE, dividend yield
  4. KSEI Sentiment — retail vs institusi ownership trend

V7 = V4 core scoring + bonus/malus dari data Invezgo
"""

import logging, os, json, re, time, math, numpy as np
from typing import Optional, Dict

logger = logging.getLogger("v7")

enabled: bool = False
config: dict = {}
THRESHOLDS = {"BULL":[62,52,45,38,30],"BEAR":[58,48,42,35,28],"RANGING":[60,50,42,35,28],"HIGH_VOLATILITY":[60,50,42,35,28]}

# Bobot default faktor V7 — total HARUS 1.0 (bisa di-override via config.yaml section v7)
_V7_DEFAULT_WEIGHTS = {
    "v4_score": 0.40,          # V4 core scoring (digeser 0.50 -> 0.40 utk earnings momentum)
    "broker_flow": 0.20,       # Broker accumulation
    "foreign_flow": 0.15,      # Foreign flow
    "fundamental": 0.15,       # Fundamental quality
    "earnings_momentum": 0.10, # Earnings momentum (B1) — revenue growth, margin trend, D/E
}
_V7_WEIGHTS = dict(_V7_DEFAULT_WEIGHTS)

# ── V7 akurasi: weekly trend masuk scoring (post-adjustment) ──
# Penalty/bonus DI LUAR weighted sum — bobot faktor tetap total 1.0.
#   BEARISH : -12 (tengah rentang kalibrasi -10..-15) + cap sinyal maks BUY
#   BULLISH : +5
#   NO_DATA / lain : 0 (netral)
WEEKLY_BEARISH_PENALTY = 12
WEEKLY_BULLISH_BONUS = 5

# Dir cache JSON (data/ sejajar dengan screener.log) — fundamental TTL 7 hari, broker flow 1 hari
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_invezgo_provider = None

def configure(cfg: dict):
    global config, THRESHOLDS, _V7_WEIGHTS
    if not cfg: return
    config.update(cfg)
    if "thresholds" in cfg:
        THRESHOLDS.update(cfg["thresholds"])
    if "weights" in cfg:
        # Override bobot dari config — hanya kunci yang dikenal; total tetap dikelola user
        w = dict(_V7_DEFAULT_WEIGHTS)
        w.update({k: float(v) for k, v in cfg["weights"].items() if k in _V7_DEFAULT_WEIGHTS})
        _V7_WEIGHTS = w

def is_enabled(): return enabled

def get_provider():
    global _invezgo_provider
    if _invezgo_provider is None:
        try:
            from data_invezgo import InvezgoProvider
            _invezgo_provider = InvezgoProvider()
        except Exception as e:
            logger.error("Gagal init Invezgo provider: %s", e)
            return None
    return _invezgo_provider

# ═══════════════════════════════════════════════════════════════
#  CACHE HELPERS (JSON, pola sama dengan cache CSV yang sudah ada)
# ═══════════════════════════════════════════════════════════════

def _cache_path(name: str) -> str:
    return os.path.join(_DATA_DIR, name)

def _safe_code(code: str) -> str:
    """Sanitasi ticker utk nama file cache — hanya A-Z0-9 (cegah path traversal)."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())

def _load_json_cache(path: str, ttl_hours: float):
    """Baca cache JSON kalau masih fresh (mtime < ttl_hours). None kalau miss/rusak."""
    try:
        if os.path.exists(path):
            age_h = (time.time() - os.path.getmtime(path)) / 3600
            if age_h < ttl_hours:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
    except Exception:
        pass
    return None

def _save_json_cache(path: str, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception:
        pass

# ── B2: SATU call broker per ticker (memori per-run + file harian TTL 24 jam) ──
_broker_mem_cache: dict = {}

def _get_broker_summary_cached(code: str, days: int = 3):
    """
    Ambil get_broker_summary() SEKALI per ticker — dipakai bersama oleh
    factor_broker_flow() dan factor_foreign_flow(). Cache:
      1. in-memory per run (kedua faktor baca data sama persis)
      2. data/broker_flow_{code}.json TTL 24 jam (data harian jarang berubah)
    Parsing kedua faktor tidak berubah — hanya sumber data yang di-share,
    jadi hasil faktor untuk data yang sama identik (regresi nol).
    """
    code = _safe_code(code)
    if code in _broker_mem_cache:
        return _broker_mem_cache[code]

    path = _cache_path(f"broker_flow_{code}.json")
    data = _load_json_cache(path, ttl_hours=24)
    if data is None:
        provider = get_provider()
        if not provider:
            return None
        data = provider.get_broker_summary(code, days=days)
        if data:  # hanya simpan kalau ada isi — hindari cache error transient
            _save_json_cache(path, data)

    if not data:
        data = []
    _broker_mem_cache[code] = data
    return data

# ── B1: data laporan keuangan dicache 7 hari (angka kuartalan jarang berubah) ──
_fund_mem_cache: dict = {}

def _get_fundamental_cached(code: str):
    """
    IS (8 kuartal — butuh kuartal sama tahun lalu utk YoY) + BS (4 kuartal utk D/E).
    Cache: data/fundamental_{code}.json TTL 7 hari. Kalau API error, TIDAK di-cache
    (retry di run berikutnya) tapi di-memori per run agar scan tidak melambat.
    """
    code = _safe_code(code)
    if code in _fund_mem_cache:
        return _fund_mem_cache[code]

    path = _cache_path(f"fundamental_{code}.json")
    data = _load_json_cache(path, ttl_hours=24 * 7)
    if data is None:
        provider = get_provider()
        if not provider:
            return None
        is_data = provider.get_financial_statement(code, statement="IS", limit=8)
        bs_data = provider.get_financial_statement(code, statement="BS", limit=4)
        data = {"IS": is_data, "BS": bs_data, "fetched_at": time.time()}
        if is_data or bs_data:
            _save_json_cache(path, data)

    _fund_mem_cache[code] = data
    return data

# ── M4: keystat (get_fundamental) dicache 7 hari — tanpa cache, tiap scan memicu
# 1 request per ticker ke endpoint keystat (~20-40 req/scan) ──
_keystat_mem_cache: dict = {}

def _get_keystat_cached(code: str):
    """Ambil get_fundamental() (PER/PBV/ROE/div yield) SEKALI per ticker.

    Cache: data/fundamental_keystat_{code}.json TTL 7 hari (angka fundamental
    jarang berubah) + memori per run. Pola sama dengan _get_fundamental_cached.
    """
    code = _safe_code(code)
    if code in _keystat_mem_cache:
        return _keystat_mem_cache[code]

    path = _cache_path(f"fundamental_keystat_{code}.json")
    data = _load_json_cache(path, ttl_hours=24 * 7)
    if data is None:
        provider = get_provider()
        if not provider:
            return None
        data = provider.get_fundamental(code)
        if data:  # hanya simpan kalau ada isi — hindari cache error transient
            _save_json_cache(path, data)

    _keystat_mem_cache[code] = data
    return data

# ── Parsing laporan keuangan Invezgo ──
# Format: {"rows": [{"name": "...", "level": 0, "values": [{"year":..,"period":"Q1","amount":..}]}]}
_PERIOD_ORDER = {"Q1": 1, "Q2": 2, "Q3": 3, "Q4": 4}

def _parse_fs_series(fs: dict, name_priority: list, exclude: tuple = ()) -> list:
    """
    Ekstrak deret {year, period, amount} (sorted ascending) dari baris pertama
    yang namanya cocok dengan prioritas name_priority (level paling atas menang).
    """
    if not fs or not isinstance(fs, dict):
        return []
    rows = fs.get("rows") or []
    best = None
    for prio in name_priority:
        for r in rows:
            name = str(r.get("name", "")).lower()
            if prio not in name:
                continue
            if exclude and any(ex in name for ex in exclude):
                continue
            if best is None or int(r.get("level", 0)) < int(best.get("level", 0)):
                best = r
        if best is not None:
            break
    if best is None:
        return []
    vals = []
    for v in (best.get("values") or []):
        try:
            amt = float(v.get("amount"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(amt):  # H1: NaN/inf dari API jangan dipakai (skor jadi -25/-15 palsu)
            continue
        period = str(v.get("period", ""))
        try:
            year = int(v.get("year", 0))
        except (TypeError, ValueError):
            continue
        if period and year:
            vals.append({"year": year, "period": period, "amount": amt})
    vals.sort(key=lambda x: (x["year"], _PERIOD_ORDER.get(x["period"], 99)))
    return vals

_REV_PRIORITY = ["pendapatan usaha", "total pendapatan", "penjualan bersih",
                 "penjualan", "pendapatan bersih", "revenue", "pendapatan"]
_REV_EXCLUDE = ("lain", "bunga", "premi", "komisi", "operasi lain")
_PROFIT_PRIORITY = ["laba bersih", "laba tahun berjalan", "laba periode berjalan",
                    "net income", "laba neto", "net profit"]
_PROFIT_EXCLUDE = ("laba kotor", "laba bruto", "laba usaha", "laba sebelum",
                   "laba operasi", "laba komprehensif")
_LIAB_PRIORITY = ["total liabilitas", "jumlah liabilitas", "total kewajiban",
                  "jumlah kewajiban", "liabilitas"]
_LIAB_EXCLUDE = ("jangka pendek", "jangka panjang", "sewa", "pajak", "dan ekuitas")
_EQUITY_PRIORITY = ["total ekuitas", "jumlah ekuitas", "ekuitas yang dapat diatribusikan",
                    "ekuitas", "equity"]
_EQUITY_EXCLUDE = ("non pengendali", "nonpengendali", "liabilitas")

# ═══════════════════════════════════════════════════════════════
#  NEW FACTORS (dari Invezgo)
# ═══════════════════════════════════════════════════════════════

def factor_broker_flow(code: str) -> dict:
    """
    Broker Flow Factor — skor berdasarkan akumulasi institusi.
    
    Logic:
      - Hitung net buy ALL brokers
      - Ambil top 5 net buyers + top 5 net sellers
      - Kalau net buyers > net sellers = akumulasi
    """
    try:
        summary = _get_broker_summary_cached(code, days=3)
        if not summary or not isinstance(summary, list) or len(summary) < 2:
            return {"score": 40, "detail": "no_data"}
        
        # Hitung net per broker
        broker_nets = []
        for b in summary:
            try:
                buy = int(b.get("buy_value", 0))
                sell = int(b.get("sell_value", 0))
                net = buy - sell
                broker_nets.append({"code": b.get("code","??"), "net": net, "buy": buy, "sell": sell})
            except (TypeError, ValueError) as e:
                # N8: nilai broker tidak valid → lewati broker ini (dulu bare
                # except: pass — drop senyap tanpa jejak di log).
                logger.debug("Broker %s nilai tidak valid (%s): %s",
                             b.get("code", "??"), e, b)
        
        # Sort by net (descending)
        broker_nets.sort(key=lambda x: x["net"], reverse=True)
        
        top_buyers = [b for b in broker_nets if b["net"] > 0]
        top_sellers = [b for b in broker_nets if b["net"] < 0]
        
        total_buy_net = sum(b["net"] for b in top_buyers[:5])
        total_sell_net = abs(sum(b["net"] for b in top_sellers[:5]))
        net_flow = total_buy_net - total_sell_net
        
        # Kode broker top 3
        top3_buyers = " ".join(f"{b['code']}(+{b['net']/1e9:.0f}B)" for b in top_buyers[:3])
        top3_sellers = " ".join(f"{b['code']}({b['net']/1e9:.0f}B)" for b in top_sellers[:3])
        
        # Skor berdasarkan net flow
        if net_flow > 100_000_000_000:
            return {"score": 85, "detail": f"akumulasi_masif_{net_flow/1e9:.0f}B", 
                    "brokers": f"🔵{top3_buyers} | 🔴{top3_sellers}"}
        elif net_flow > 10_000_000_000:
            return {"score": 75, "detail": f"akumulasi_{net_flow/1e9:.1f}B",
                    "brokers": f"🔵{top3_buyers} | 🔴{top3_sellers}"}
        elif net_flow > 1_000_000_000:
            return {"score": 65, "detail": f"net_buy_{net_flow/1e9:.1f}B",
                    "brokers": f"🔵{top3_buyers} | 🔴{top3_sellers}"}
        elif net_flow > -1_000_000_000:
            return {"score": 50, "detail": "netral",
                    "brokers": f"🔵{top3_buyers} | 🔴{top3_sellers}"}
        else:
            return {"score": 30, "detail": f"distribusi_{abs(net_flow)/1e9:.0f}B",
                    "brokers": f"🔵{top3_buyers} | 🔴{top3_sellers}"}
            
    except Exception as e:
        logger.debug("Broker flow error %s: %s", code, e)
        return {"score": 40, "detail": "error", "brokers": ""}


def factor_foreign_flow(code: str) -> dict:
    """Foreign Flow Factor — asing beli atau jual?"""
    try:
        summary = _get_broker_summary_cached(code, days=3)
        if not summary or not isinstance(summary, list):
            return {"score": 40, "detail": "no_data"}
        
        # Cari foreign net dari summary
        foreign_net = 0
        for item in summary:
            if item.get("code") in ["AG", "RG", "DB", "GS", "ML", "CS", "UBS"]:  # Foreign brokers
                buy = int(item.get("buy_value", 0))
                sell = int(item.get("sell_value", 0))
                foreign_net += (buy - sell)
        
        if foreign_net > 10_000_000_000:
            return {"score": 80, "detail": "asing_beli_besar"}
        elif foreign_net > 1_000_000_000:
            return {"score": 65, "detail": "asing_beli"}
        elif foreign_net > -1_000_000_000:
            return {"score": 50, "detail": "asing_netral"}
        else:
            return {"score": 30, "detail": "asing_jual"}
            
    except Exception as e:
        logger.debug("Foreign flow error %s: %s", code, e)
        return {"score": 40, "detail": "error"}


def _to_float(value):
    """Konversi nilai fundamental ke float. None/non-numerik/NaN/inf → None.

    L7: API Invezgo kadang mengembalikan angka sebagai STRING ('12.5') —
    np.isnan(string) memicu TypeError yang ditangkap except luar → seluruh
    faktor jadi 40 'error' diam-diam walau data valid. Nilai invalid → None
    (netral: sub-skornya dilewati, bukan 'error').
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def factor_fundamental_quality(code: str) -> dict:
    """
    Fundamental Quality Factor — PER, PBV, ROE, dividend.
    
    Ideal value profile IDX:
      PER: 8-15x
      PBV: 1-3x
      ROE: >15%
      Div Yield: >3%
    """
    try:
        fund = _get_keystat_cached(code)  # M4: dicache 7 hari (data/fundamental_keystat_{code}.json)
        if not fund or not isinstance(fund, dict):
            return {"score": 40, "detail": "no_data"}
        
        per = _to_float(fund.get("PER", fund.get("per", None)))
        pbv = _to_float(fund.get("PBV", fund.get("pbv", None)))
        roe = _to_float(fund.get("ROE", fund.get("roe", None)))
        div = _to_float(fund.get("Dividend Yield", fund.get("dividend_yield", None)))
        
        score = 50
        
        # PER: ideal 8-15
        if per is not None:
            if 8 <= per <= 15: score += 20
            elif 5 <= per < 8: score += 10
            elif 15 < per <= 20: score += 5
            elif per > 30: score -= 10
        
        # PBV: ideal 1-3
        if pbv is not None:
            if 1 <= pbv <= 3: score += 15
            elif 0.5 <= pbv < 1: score += 8
        
        # ROE: ideal >15%
        if roe is not None:
            if roe > 20: score += 15
            elif roe > 15: score += 10
            elif roe > 10: score += 5
        
        # Dividend yield
        if div is not None and div > 0:
            if div > 5: score += 10
            elif div > 3: score += 5
        
        return {"score": max(0, min(100, score)), "detail": f"per={per}_roe={roe}"}
        
    except Exception as e:
        logger.debug("Fundamental error %s: %s", code, e)
        return {"score": 40, "detail": "error"}


def _growth_score(g: float) -> int:
    if not math.isfinite(g):  # H1: NaN/inf = data tidak valid → netral, bukan -25
        return 0
    if g > 0.20: return 25
    if g > 0.10: return 18
    if g > 0.05: return 12
    if g > 0.01: return 6
    if g >= -0.01: return 0
    if g >= -0.05: return -6
    if g >= -0.10: return -12
    if g >= -0.20: return -18
    return -25

def _margin_score(t: float) -> int:
    if not math.isfinite(t):  # H1: NaN/inf = data tidak valid → netral, bukan -15
        return 0
    if t > 0.02: return 15
    if t > 0.005: return 8
    if t >= -0.005: return 0
    if t >= -0.02: return -8
    return -15

def _de_score(de: float) -> int:
    if not math.isfinite(de):  # H1: NaN/inf = data tidak valid → netral, bukan -10
        return 0
    if de < 0.5: return 10
    if de < 1.0: return 6
    if de < 1.5: return 0
    if de < 2.5: return -6
    return -10


def factor_earnings_momentum(code: str) -> dict:
    """
    Earnings Momentum Factor (B1) — memakai get_financial_statement():
      1. Revenue growth YoY (IS, kuartal sama tahun lalu; fallback QoQ)
      2. Net margin trend (laba bersih / pendapatan, 4 kuartal terakhir)
      3. D/E dari neraca (BS)
    Data laporan dicache 7 hari di data/fundamental_{code}.json (angka
    kuartalan jarang berubah). Kalau laporan tidak tersedia → skor netral 0
    (tidak crash, tidak memperlambat scan).
    """
    try:
        data = _get_fundamental_cached(code)
        if not data:
            return {"score": 40, "detail": "no_data"}  # H2: netral, bukan 0 (konsisten faktor lain)

        rev_rows = _parse_fs_series(data.get("IS"), _REV_PRIORITY, _REV_EXCLUDE)
        profit_rows = _parse_fs_series(data.get("IS"), _PROFIT_PRIORITY, _PROFIT_EXCLUDE)
        liab_rows = _parse_fs_series(data.get("BS"), _LIAB_PRIORITY, _LIAB_EXCLUDE)
        equity_rows = _parse_fs_series(data.get("BS"), _EQUITY_PRIORITY, _EQUITY_EXCLUDE)

        # ── 1. Revenue growth: YoY (kuartal sama tahun lalu) / fallback QoQ ──
        growth, growth_label = None, ""
        if len(rev_rows) >= 2:
            latest = rev_rows[-1]
            prev = None
            for v in rev_rows[:-1]:
                if v["period"] == latest["period"] and v["year"] == latest["year"] - 1:
                    prev = v
                    break
            if prev is not None and prev["amount"]:
                growth = latest["amount"] / prev["amount"] - 1
                growth_label = "YoY"
            elif rev_rows[-2]["amount"]:
                growth = latest["amount"] / rev_rows[-2]["amount"] - 1
                growth_label = "QoQ"

        # ── 2. Net margin trend: 4 kuartal terakhir (samakan periode) ──
        rev_map = {f"{v['year']}{v['period']}": v["amount"] for v in rev_rows}
        prof_map = {f"{v['year']}{v['period']}": v["amount"] for v in profit_rows}
        common = sorted(k for k in rev_map if k in prof_map)
        margins = []
        for k in common[-4:]:
            if rev_map[k]:
                margins.append((k, prof_map[k] / rev_map[k]))
        margin_trend, margin_base, margin_latest = None, None, None
        if len(margins) >= 2:
            margin_base, margin_latest = margins[0][1], margins[-1][1]
            margin_trend = margin_latest - margin_base

        # ── 3. D/E dari neraca (nilai terbaru) ──
        # N3: ekuitas <= 0 (ekuitas negatif) → D/E tidak valid — JANGAN dihitung
        # (dulu D/E negatif dapat skor TERBAIK +10 di _de_score).
        # L8: samakan PERIODE liabilitas & ekuitas (pola common seperti margin);
        # fallback ke indeks terakhir masing-masing bila tidak ada periode sama.
        de = None
        if liab_rows and equity_rows:
            liab_map = {f"{v['year']}{v['period']}": v["amount"] for v in liab_rows}
            eq_map = {f"{v['year']}{v['period']}": v["amount"] for v in equity_rows}
            common_de = sorted(k for k in liab_map if k in eq_map)
            if common_de:
                k = common_de[-1]
                eq = eq_map[k]
                if eq > 0:
                    de = liab_map[k] / eq
            else:
                eq = equity_rows[-1]["amount"]
                if eq > 0:
                    de = liab_rows[-1]["amount"] / eq

        if growth is None and margin_trend is None and de is None:
            return {"score": 40, "detail": "no_data"}  # H2: netral, bukan 0 (konsisten faktor lain)

        score = 50
        parts = []
        if growth is not None:
            score += _growth_score(growth)
            parts.append(f"Rev {growth*100:+.0f}% {growth_label}")
        if margin_trend is not None:
            score += _margin_score(margin_trend)
            parts.append(f"margin {margin_base*100:.0f}->{margin_latest*100:.0f}%")
        if de is not None:
            score += _de_score(de)
            parts.append(f"D/E {de:.1f}")

        return {"score": max(0, min(100, score)), "detail": " | ".join(parts)}

    except Exception as e:
        logger.debug("Earnings momentum error %s: %s", code, e)
        return {"score": 40, "detail": "error"}  # H2: netral saat error (konsisten faktor lain)


# ═══════════════════════════════════════════════════════════════
#  V7 MASTER SCORE — menggabungkan V4 + Invezgo factors
# ═══════════════════════════════════════════════════════════════

# _V7_WEIGHTS dikelola di atas (L23-30 via _V7_DEFAULT_WEIGHTS) — dup, jangan hardcode ulang

def compute(code: str, v4_score: float, regime: str, weekly_trend: str = None) -> dict:
    """
    Hitung V7 score dengan data Invezgo.

    Parameters
    ----------
    code : str — kode saham tanpa .JK
    v4_score : float — skor dari V4 engine
    regime : str — market regime
    weekly_trend : str, optional — trend mingguan dari baris data
        ("BULLISH" / "BEARISH" / "NO_DATA"; None/absent = netral).
        Masuk scoring sebagai POST-ADJUSTMENT DI LUAR weighted sum sehingga
        bobot faktor TETAP total 1.0:
          - BEARISH → skor -12 (tengah rentang kalibrasi -10..-15) DAN
            sinyal di-cap maksimum BUY (STRONG_BUY diturunkan ke BUY —
            tidak ada pasangan STRONG_BUY + weekly BEARISH di output).
          - BULLISH → skor +5.
          - NO_DATA/lain/None → 0 (netral).

    Returns
    -------
    dict dengan score, signal, detail
    """
    if not enabled:
        return {"score": v4_score, "signal": "HOLD", "factors": {}}

    # Ambil faktor Invezgo
    bf = factor_broker_flow(code)
    ff = factor_foreign_flow(code)
    fq = factor_fundamental_quality(code)
    em = factor_earnings_momentum(code)
    w = _V7_WEIGHTS

    # Weighted score (total bobot = 1.0)
    v7_score = (
        v4_score * w["v4_score"] +
        bf["score"] * w["broker_flow"] +
        ff["score"] * w["foreign_flow"] +
        fq["score"] * w["fundamental"] +
        em["score"] * w["earnings_momentum"]
    )

    # ── V7 akurasi: weekly trend — post-adjustment di luar weighted sum ──
    # Bobot faktor tidak diubah (total tetap 1.0); penyesuaian diterapkan
    # SETELAH agregasi, SEBELUM penentuan sinyal.
    weekly = str(weekly_trend or "NO_DATA").strip().upper()
    if weekly == "BEARISH":
        v7_score -= WEEKLY_BEARISH_PENALTY
        weekly_note = f"weekly_bearish_-{WEEKLY_BEARISH_PENALTY}"
    elif weekly == "BULLISH":
        v7_score += WEEKLY_BULLISH_BONUS
        weekly_note = f"weekly_bullish_+{WEEKLY_BULLISH_BONUS}"
    else:
        weekly_note = "weekly_neutral"
    v7_score = round(max(0, min(100, v7_score)), 1)

    # Signal dari threshold
    th = THRESHOLDS.get(regime, THRESHOLDS["RANGING"])
    if v7_score >= th[0]: signal = "STRONG_BUY"
    elif v7_score >= th[1]: signal = "BUY"
    elif v7_score >= th[2]: signal = "WEAK_BUY"
    elif v7_score >= th[3]: signal = "HOLD"
    else: signal = "SELL"

    # Cap: weekly BEARISH → maksimum BUY (STRONG_BUY tidak boleh lolos)
    if weekly == "BEARISH" and signal == "STRONG_BUY":
        signal = "BUY"

    return {
        "score": v7_score,
        "signal": signal,
        "factors": {
            "v4_core": round(v4_score, 1),
            "broker_flow": bf["score"],
            "broker_detail": bf["detail"],
            "foreign_flow": ff["score"],
            "foreign_detail": ff["detail"],
            "fundamental": fq["score"],
            "fundamental_detail": fq["detail"],
            "earnings_momentum": em["score"],
            "earnings_detail": em["detail"],
            "brokers": bf.get("brokers", ""),
            "weekly_trend": weekly,
            "weekly_adjustment": weekly_note,
            }
    }
