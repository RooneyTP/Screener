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
    "v4_score": 0.30,          # V4 core scoring (IDE4: digeser 0.40 -> 0.30 utk broker_trend)
    "broker_flow": 0.20,       # Broker accumulation (snapshot harian — informasi hari ini)
    "foreign_flow": 0.15,      # Foreign flow
    "fundamental": 0.15,       # Fundamental quality
    "earnings_momentum": 0.10, # Earnings momentum (B1) — revenue growth, margin trend, D/E
    "broker_trend": 0.10,      # IDE4: trend flow broker HISTORIS 5d/10d/20d — pembeda non-jenuh
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

# ── IDE2: snapshot net asing SEJATI (get_summary_stock investor='f') ──
# Cache TERPISAH data/broker_flow_foreign_{CODE}.json TTL 24 jam (pola sama
# dengan broker_flow_{CODE}.json) + memori per run. Sumber factor_foreign_flow
# — menggantikan tebakan daftar kode broker hardcode (AG/RG domestik, CS
# merger ke UBS) yang membuat skor asing hampir selalu netral 50.
_foreign_mem_cache: dict = {}

def _get_broker_foreign_summary_cached(code: str, days: int = 3):
    """
    Ambil get_broker_foreign_summary() SEKALI per ticker per run.
    Cache: data/broker_flow_foreign_{code}.json TTL 24 jam (pola sama dengan
    _get_broker_summary_cached). Error provider → None (fallback dipakai
    pemanggil); hasil kosong → [] (tidak di-cache).
    """
    code = _safe_code(code)
    if code in _foreign_mem_cache:
        return _foreign_mem_cache[code]

    path = _cache_path(f"broker_flow_foreign_{code}.json")
    data = _load_json_cache(path, ttl_hours=24)
    if data is None:
        provider = get_provider()
        if not provider:
            return None
        data = provider.get_broker_foreign_summary(code, days=days)
        if data:  # hanya simpan kalau ada isi — hindari cache error transient
            _save_json_cache(path, data)

    if not data:
        data = []
    _foreign_mem_cache[code] = data
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


# ── IDE4: riwayat broker flow harian (bandarmologi pembeda) ──
# Sumber: provider.get_broker_flow_history → get_inventory_chart_stock SDK
# (get_summary_chart_stock ternyata infographic 4 item, BUKAN deret harian —
# verifikasi API nyata 08/2026). Cache file TTL 24 jam DI KELOLA provider
# (data/broker_flow_hist_{CODE}.json); memo in-memory di sini agar scan tidak
# membaca file berulang dalam satu run.
_broker_trend_mem_cache: dict = {}


def _get_broker_flow_history_cached(code: str, days: int = 20) -> list:
    """Riwayat net buy harian (ascending) — sekali per ticker per run.

    Provider menangani cache file (TTL 24 jam); helper ini hanya memo
    in-memory per run + guard provider None (→ [] netral, tidak crash).
    """
    code = _safe_code(code)
    if code in _broker_trend_mem_cache:
        return _broker_trend_mem_cache[code]
    hist = []
    provider = get_provider()
    if provider is not None:
        try:
            hist = provider.get_broker_flow_history(code, days=days) or []
        except Exception as e:
            logger.debug("Broker flow history gagal %s: %s", code, e)
            hist = []
    if not isinstance(hist, list):
        hist = []
    _broker_trend_mem_cache[code] = hist
    return hist


# ── L2-A: FLOW SPIKE (bandarmologi user) — net buy MENDADAK = jebakan distribusi ──
# Insight user (trader berpengalaman): bandar bergerak DIAM — net buy besar
# yang muncul tiba-tiba sering justru persiapan DISTRIBUSI besok (bandar pakai
# momentum utk jual ke ritel). Baseline = rata-rata harian hari POSITIF 20d
# (irama akumulasi normal). Spike:
#   A. net_5d > 2.5 × (avg_20d_pos × 5)   → total 5 hari ekstrem vs baseline
#   B. net_1d terakhir > 3 × avg_20d_pos  → 1 hari ekstrem vs baseline
FLOW_SPIKE_NET5D_MULT = 2.5
FLOW_SPIKE_1D_MULT = 3.0


def detect_flow_spike(nets: list) -> dict:
    """Deteksi flow spike dari deret net_buy harian (ascending, terbaru akhir).

    Returns {"spike": bool, "kind": "5d"|"1d"|"", "net_5d": float,
             "avg_20d_pos": float, "last_1d": float}
    Baseline 0 (tidak ada hari positif dalam 20d — flow sudah distribusi,
    ditangani skor trend rendah) atau data kosong → spike False (netral).
    """
    out = {"spike": False, "kind": "", "net_5d": 0.0, "avg_20d_pos": 0.0,
           "last_1d": 0.0}
    if not nets:
        return out
    vals = []
    for x in nets:
        try:
            v = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            vals.append(v)
    if not vals:
        return out
    window = vals[-20:]
    pos = [v for v in window if v > 0]
    avg_pos = (sum(pos) / len(pos)) if pos else 0.0
    net5 = sum(window[-5:])
    last1 = window[-1]
    out.update({"net_5d": net5, "avg_20d_pos": avg_pos, "last_1d": last1})
    if avg_pos <= 0:
        return out  # baseline nol → bukan spike (sudah distribusi)
    spike_a = net5 > FLOW_SPIKE_NET5D_MULT * avg_pos * 5.0
    spike_b = last1 > FLOW_SPIKE_1D_MULT * avg_pos
    if spike_a:
        out["spike"], out["kind"] = True, "5d"
    elif spike_b:
        out["spike"], out["kind"] = True, "1d"
    return out


def factor_broker_trend(code: str, days: int = 20) -> dict:
    """Broker Flow TREND Factor (IDE4) — bandarmologi PEMBEDA, non-jenuh.

    Memakai RIWAYAT net buy harian (get_broker_flow_history, cache 24 jam),
    bukan snapshot 3 hari (yang jenuh di 85 untuk hampir semua saham).

    Metrik dari deret net_buy (ascending, terbaru di akhir):
      net_5d / net_10d / net_20d : Σ net buy jendela tsb
      streak                     : hari berturut-turut net_buy > 0 (dari akhir)
      momentum                   : rata-rata harian 5d vs 10d (naik/turun)

    Skor 0-100 basis 50 — TIDAK jenuh: tiap komponen diskalakan relatif
    terhadap total aktivitas (Σ|net|) sehingga magnitudo kecil tidak
    mendapat skor penuh; skor TINGGI (>70) hanya tercapai kalau 20d positif
    KUAT, fase 5d tidak melemah, momentum naik, DAN streak >= 2:
      ±20 arah 20d | ±10 arah 10d | ±10 arah 5d | ±10 momentum | +5..+10 streak
    Data tidak tersedia / error → netral 50 (TIDAK menghukum).
    """
    try:
        hist = _get_broker_flow_history_cached(code, days=days)
        if not hist or not isinstance(hist, list):
            return {"score": 50, "detail": "no_data"}
        nets = []
        for h in hist:
            if not isinstance(h, dict):
                continue
            try:
                v = float(h.get("net_buy"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                nets.append(v)
        if not nets:
            return {"score": 50, "detail": "no_data"}

        n = len(nets)
        net5 = sum(nets[-5:]) if n >= 5 else None
        net10 = sum(nets[-10:]) if n >= 10 else None
        net20 = sum(nets[-20:]) if n >= 20 else None

        streak = 0
        for x in reversed(nets):
            if x > 0:
                streak += 1
            else:
                break

        score = 50.0
        parts = []

        # 1) Arah & magnitudo RELATIF per jendela (±20 / ±10 / ±10)
        for label, net_k, k in (("20d", net20, 20), ("10d", net10, 10), ("5d", net5, 5)):
            if net_k is None:
                continue
            denom = sum(abs(x) for x in nets[-k:]) or 1.0
            rel = max(-1.0, min(1.0, net_k / denom))  # -1..+1 — skala membedakan
            score += (20.0 if k == 20 else 10.0) * rel
            parts.append(f"{label} {net_k/1e9:+.1f}B")

        # 2) Momentum: rata-rata harian 5d vs 10d
        if net5 is not None and net10 is not None:
            avg5, avg10 = net5 / 5.0, net10 / 10.0
            base = (abs(avg5) + abs(avg10)) / 2.0
            mom = 0.0 if base == 0 else max(-1.0, min(1.0, (avg5 - avg10) / base))
            score += 10.0 * mom
            parts.append("mom " + ("naik" if mom > 0.05 else "turun" if mom < -0.05 else "datar"))

        # 3) Streak positif berturut-turut (bonus non-linear)
        if streak >= 5:
            score += 10
            parts.append(f"streak{streak}")
        elif streak >= 3:
            score += 8
            parts.append(f"streak{streak}")
        elif streak >= 2:
            score += 5
            parts.append(f"streak{streak}")

        # ── L2-A: flow spike — net buy MENDADAK = jebakan distribusi (bandar) ──
        # Insight user: bandar bergerak DIAM; net buy besar yang tiba-tiba
        # muncul sering persiapan DISTRIBUSI besok. Kalau spike → JANGAN beri
        # bonus akumulasi: skor di-cap netral 50 + detail peringatan.
        spk = detect_flow_spike(nets)
        flow_spike = spk["spike"]
        if flow_spike:
            score = min(score, 50.0)
            parts.append(f"flow_spike_{spk['kind']}")

        detail = "trend " + (" ".join(parts) if parts else "flat")
        if flow_spike:
            detail += " | ⚠️ flow spike — waspada distribusi"

        return {"score": round(max(0.0, min(100.0, score)), 1),
                "detail": detail,
                "flow_spike": flow_spike}

    except Exception as e:
        logger.debug("Broker trend error %s: %s", code, e)
        return {"score": 50, "detail": "error"}


# ── IDE2: daftar kode broker asing FALLBACK (dipakai HANYA kalau snapshot
# investor='f' tidak tersedia — error/offline). Diperbaiki 08/2026:
#   AG = KIWOOM (DOMESTIK)      → dibuang
#   RG = PROFINDO (DOMESTIK)    → dibuang
#   CS = Credit Suisse (merger ke UBS sejak 2023) → dibuang
#   AI = UOB Kay Hian (asing sejati, dulu TIDAK ada di daftar) → ditambahkan
# ⚠️ Daftar ini PERLU DIVERIFIKASI BERKALA terhadap data broker aktual.
_FOREIGN_BROKER_CODES_FALLBACK = ["DB", "GS", "ML", "UBS", "AI"]


def factor_foreign_flow(code: str) -> dict:
    """Foreign Flow Factor — asing beli atau jual?

    IDE2: memakai snapshot net asing SEJATI (get_summary_stock investor='f',
    cache data/broker_flow_foreign_{CODE}.json TTL 24 jam) — sebelumnya hanya
    menjumlahkan kode broker hardcode dari snapshot 'all' yang salah daftarnya
    (AG/RG domestik, CS merger) → 13/15 ticker dapat skor 50 statis (bonus
    0.15×50 = 7.5 poin tidak informatif). Logika skor sama dengan
    factor_broker_flow: akumulasi besar → skor tinggi.

    Kalau snapshot 'f' tidak tersedia (provider error / offline) → fallback
    minimal: daftar kode broker asing yang DIPERBAIKI dihitung dari snapshot
    'all' yang sudah di-cache (broker_flow_{CODE}.json).
    """
    try:
        summary = _get_broker_foreign_summary_cached(code, days=3)
        if summary and isinstance(summary, list) and len(summary) >= 2:
            foreign_net = 0
            for item in summary:
                try:
                    foreign_net += int(item.get("buy_value", 0)) - int(item.get("sell_value", 0))
                except (TypeError, ValueError) as e:
                    logger.debug("Broker asing %s nilai tidak valid (%s): %s",
                                 item.get("code", "??"), e, item)
            if foreign_net > 100_000_000_000:
                return {"score": 85, "detail": f"asing_akumulasi_masif_{foreign_net/1e9:.0f}B"}
            elif foreign_net > 10_000_000_000:
                return {"score": 75, "detail": f"asing_akumulasi_{foreign_net/1e9:.1f}B"}
            elif foreign_net > 1_000_000_000:
                return {"score": 65, "detail": f"asing_beli_{foreign_net/1e9:.1f}B"}
            elif foreign_net > -1_000_000_000:
                return {"score": 50, "detail": "asing_netral"}
            else:
                return {"score": 30, "detail": f"asing_jual_{abs(foreign_net)/1e9:.0f}B"}

        # ── Fallback: snapshot 'all' + daftar kode broker asing (diperbaiki) ──
        summary = _get_broker_summary_cached(code, days=3)
        if not summary or not isinstance(summary, list):
            return {"score": 40, "detail": "no_data"}

        foreign_net = 0
        for item in summary:
            if item.get("code") in _FOREIGN_BROKER_CODES_FALLBACK:
                try:
                    foreign_net += int(item.get("buy_value", 0)) - int(item.get("sell_value", 0))
                except (TypeError, ValueError) as e:
                    logger.debug("Broker asing %s nilai tidak valid (%s): %s",
                                 item.get("code", "??"), e, item)

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
    bt = factor_broker_trend(code)  # IDE4: trend flow harian (pembeda non-jenuh)
    w = _V7_WEIGHTS

    # ── IDE1: FLOW REGIME CONFLICT CAP — snapshot akumulasi vs trend distribusi ──
    # Snapshot 3 hari (get_broker_summary) bisa BERTENTANGAN dengan trend 20
    # hari (get_broker_flow_history): snapshot akumulasi > 65 di tengah trend
    # distribusi < 35 = jebakan distribusi (bandar jual ke ritel memakai
    # momentum — kasus nyata BNBR/ASII/BUMI 08/2026). Kontribusi broker_flow
    # di-cap PARSIAL netral 50 + warning (BUKAN veto): awal akumulasi baru
    # (spike 3d = awal tren) TIDAK kena cap (trend-nya belum < 35, atau sudah
    # di-cap 50 oleh flow_spike — 50 >= 35 → tidak conflict), dan arah
    # sebaliknya ringan (trend akumulasi + snapshot netral) TETAP diizinkan.
    # factor_broker_trend TIDAK disentuh — hanya kontribusi snapshot yang di-cap.
    bf_score = float(bf["score"])
    conflict_snapshot_vs_trend = bt["score"] < 35 and bf_score > 65
    if conflict_snapshot_vs_trend:
        bf_score = 50.0

    # Weighted score (total bobot = 1.0)
    # IDE4: broker_trend (riwayat 5d/10d/20d) FAKTOR TERPISAH berbobot 0.10 —
    # desain terpilih: snapshot broker_flow (0.20) tetap sebagai informasi
    # harian; trend historis punya bobot sendiri sehingga tidak saling
    # meniadakan dan skor total tidak jenuh (alih-alih menggabung 60/40
    # menjadi satu faktor, dua bobot terpisah lebih bersih & transparan).
    v7_score = (
        v4_score * w["v4_score"] +
        bf_score * w["broker_flow"] +
        ff["score"] * w["foreign_flow"] +
        fq["score"] * w["fundamental"] +
        em["score"] * w["earnings_momentum"] +
        bt["score"] * w["broker_trend"]
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
            "broker_flow": round(bf_score, 1),           # IDE1: sudah termasuk cap conflict
            "broker_flow_raw": bf["score"],              # IDE1: skor snapshot asli (audit)
            "broker_detail": bf["detail"] + (" | ⚠️ conflict snapshot vs trend — waspada distribusi" if conflict_snapshot_vs_trend else ""),
            "broker_trend": bt["score"],              # IDE4
            "broker_trend_detail": bt["detail"],      # IDE4
            "flow_spike": bt.get("flow_spike", False),  # L2-A: net buy mendadak (jebakan distribusi)
            "conflict_snapshot_vs_trend": conflict_snapshot_vs_trend,  # IDE1: snapshot akumulasi vs trend distribusi
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
