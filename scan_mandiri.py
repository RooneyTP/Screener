#!/usr/bin/env python3
"""scan_mandiri.py — Screening MANDIRI V7: scan on-demand untuk daftar ticker pilihan.

Dipakai halaman /screener RisetSaham (panel "Screening Mandiri"): user pilih
kode (watchlist / ketik sendiri) → app menjalankan skrip ini → CSV hasil dibaca
app → tampil sebagai kartu & riwayat di /screener.

Beda dari v7_scan.py (scan TERJADWAL penuh):
- Hanya ticker yang diminta (--tickers), maks 20.
- TANPA efek samping: tidak menulis perf_tracker_v7.csv, tidak menyentuh
  cooldown, tidak kirim Telegram — murni "apa kata V7 saat ini" untuk daftar user.
- Rantai keputusan SAMA dengan scan terjadwal: compute_all_indicators →
  align IHSG → compute_total_score → v7.compute → _swing_gate →
  gate_swing_signal (fungsi diimpor dari v7_scan — single source, jangan
  salin ulang logikanya).

Kolom CSV: kode,skor,mode,entry,sl,tp,entry_ideal,catatan,tampil.
- `tampil` = "ya"/"tidak" — app hanya MENAMPILKAN baris "ya" (sinyal lolos
  gate); sisanya tetap di CSV utk transparansi (hitungan "tak ditampilkan").
- `entry_ideal` = zona entry terbaik dari entry_timing.recommend_entry()
  (modul engine yang sama dgn scan terjadwal) — utk baris sinyal.

PRA-FILTER VOLUME DIHAPUS (19 Sep 2026): dulu vol<1.0× dilewati supaya hemat
faktor mahal; backtest 1 tahun (../backtest_v7_teknikal.py) membuktikan tidak
ada bukti vol≥1.0× membantu — kohort vol<1.0 justru lebih baik. Semua finalis
kini diperiksa penuh (masih dalam batas sopan: maks 3 panggilan paralel).

Fase 2 berjalan PARALEL 3 worker (sopan: maks 3 panggilan Stockbit bersamaan).

⚠ Kolom catatan HARUS selalu multi-kata (jangan taruh kata pendek spt
BEAR/BARU/HOLD sebagai sel sendiri) — parser /screener bisa salah mengira
sel 3-4 huruf sebagai kolom kode.

Progress ke stdout dipantau app: baris "PROGRESS i/n KODE".

Pakai:
    .venv/bin/python scan_mandiri.py --tickers TLKM,BRPT --out /tmp/hasil.csv
"""
import argparse
import csv
import math
import os
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.abspath(__file__))
SCAN = os.path.join(ROOT, "idx_alpha_screener")
sys.path.insert(0, SCAN)

import yaml
import pandas as pd

from data import compute_all_indicators, align_to_market, fetch_ihsg_cached
from regime import detect_market_regime
from scoring import compute_total_score
from data_provider import InvezgoProvider
from entry_timing import recommend_entry
from market_sentiment import predict_market_sentiment
import v7 as v7_engine
from v7_exit import compute_exit
from v7_scan import (_signal_from_score, _swing_gate, gate_swing_signal,
                     INTRADAY_MIN_VOL_RATIO)

WIB = timezone(timedelta(hours=7))
MAX_TICKERS = 20

# Ambang likuiditas (permintaan user 16 Sep 2026 malam): nilai transaksi
# rata-rata harian 20 hari terakhir minimal Rp 800 juta. Saham "sepi" di
# bawah itu TIDAK diskrining — lebih cepat (Yahoo/Stockbit dihemat) DAN
# hasilnya bisa benar-benar diperdagangkan. Ubah satu angka ini untuk
# mengetatkan (mis. 1_000_000_000 = Rp 1 M — terukur hanya ±37 saham lagi
# yang tersaring, dari 490 → 527 di data 16 Sep).
MIN_VALUE_20D = 800_000_000
MIN_VALUE_LABEL = "Rp 800 juta"


def _allowed_signals(cfg: dict, regime: str) -> set:
    """Filter regime — sama persis dengan market_mode di v7_scan.main().

    19 Sep 2026 — BEAR DIBLOKIR TOTAL (backtest 1 th backtest_v7_teknikal.py:
    sinyal hari BEAR avg -3,4% · WR 35%; skor tinggi makin parah — v4>=65 di
    BEAR: -7,4% · WR 22%). HIGH_VOLATILITY tetap hanya STRONG_BUY.
    """
    if cfg.get("market_mode", {}).get("enabled", True):
        if regime == "BEAR":
            return set()
        if regime == "HIGH_VOLATILITY":
            return {"STRONG_BUY"}
        if regime == "RANGING":
            return {"STRONG_BUY", "BUY"}
    return {"STRONG_BUY", "BUY", "WEAK_BUY"}


def _bf_tag(bf) -> str:
    """Label singkat broker summary utk catatan kartu — mis. '🏦 net buy 2.0B'.

    Sumber: faktor broker_detail V7 (Stockbit marketdetectors, net semua broker
    hari terakhir). Ditampilkan apa adanya supaya analisis broker KELIHATAN di
    hasil screening (permintaan user 16 Sep).
    """
    s = str(bf or "").strip()
    if not s:
        return ""
    s = s.split("|")[0].strip()  # buang embel-embel peringatan ('| ⚠️ …') — cukup inti broker-nya
    if not s:
        return ""
    if s == "netral":
        return "🏦 netral"
    s = (s.replace("net_buy_", "net buy ").replace("net_sell_", "net sell ")
         .replace("akumulasi_", "akum ").replace("distribusi_", "distrib ")
         .replace("_", " ").strip())
    return "🏦 " + s[:30]


# Ringkas metode entry_timing → teks sel pendek (tanpa koma — aturan CSV).
_METODE_ENTRY = (
    ("Open Entry / Market Order", "entry pasar"),
    ("Limit order di VWAP", "limit di VWAP"),
    ("Limit order di support", "limit di support"),
    ("Limit order diskon", "limit diskon"),
    ("Limit di harga pasaran", "limit pasaran"),
    ("Limit diskon dalam", "limit diskon"),
    ("Tunggu konfirmasi reversal", "tunggu reversal"),
    ("Jangan entry — tunggu pullback", "tunggu pullback"),
    ("GTC @ support Donchian", "di support"),
    ("Dekat support + harga bandar", "dekat support & bandar"),
    ("HOLD CASH", "tahan dulu"),
)


def _entry_ideal(rec: dict | None) -> str:
    """Sel 'entry_ideal' dari keluaran recommend_entry(): '2641-2668 · limit di VWAP'.

    price_range engine berformat 'Rp2.641 - Rp2.668' (pemisah ribuan koma ala
    Python) → di sini diubah ke angka polos (2641) supaya tidak ada koma di
    CSV dan mudah dibaca di kartu. Gagal parse → string kosong.
    """
    if not rec:
        return ""
    nums = []
    for x in re.findall(r"Rp([\d,.]+)", str(rec.get("price_range") or "")):
        try:
            n = int(x.replace(",", "").replace(".", ""))
        except ValueError:
            continue
        if n > 0:
            nums.append(n)
    if not nums:
        return ""
    lo, hi = min(nums), max(nums)
    rng = f"{lo}" if lo == hi else f"{lo}-{hi}"
    m = str(rec.get("method") or "").strip()
    m = re.sub(r"^[^\w]+", "", m)          # buang emoji/penanda di depan
    for asal, ganti in _METODE_ENTRY:
        if m.startswith(asal):
            m = ganti
            break
    else:
        m = m.replace("—", "-").lower()
    m = m.strip()[:28]
    return (rng + (" · " + m if m else "")).strip()


def _nilai_rata2(df) -> float | None:
    """Perkiraan nilai transaksi rata-rata harian (Rp): mean(close × volume)
    20 bar terakhir dari candle harian. None bila data tak cukup (→ dianggap
    TIDAK tersaring — jangan buang saham hanya karena data harga bolong)."""
    try:
        tail = df.tail(20)
        v = (tail["close"].astype(float) * tail["volume"].astype(float)).dropna()
        if len(v) < 5:
            return None
        return float(v.mean())
    except Exception:
        return None


def scan_satu(ip, tkr: str, regime: str, allowed: set, df_ihsg,
              sentiment: dict | None = None) -> dict:
    """Hitung skor & sinyal V7 utk SATU ticker → dict baris CSV.

    Selalu mengembalikan baris (gagal → catatan berisi alasannya). `tampil`
    menandai baris yang layak ditampilkan app ("ya"/"tidak"); baris "tidak"
    tetap ada di CSV utk transparansi & hitungan. `sentiment` = keluaran
    predict_market_sentiment (dipakai recommend_entry; None = netral).
    """
    row: dict[str, object] = {"kode": tkr, "skor": "", "mode": "", "entry": "", "sl": "", "tp": "",
           "entry_ideal": "", "bandar_sesi": "", "bandar_3bln": "",
           "catatan": "", "tampil": ""}
    try:
        df = ip.get_historical(tkr, period="1y")
        if df is None or df.empty or len(df) < 60:
            row["catatan"] = "data harga tidak cukup (min 60 hari bursa)"
            return row
        df = compute_all_indicators(df)
        if df_ihsg is not None and not df_ihsg.empty:
            df = align_to_market(df, df_ihsg=df_ihsg).dropna()
        else:
            df["idx_close"] = 0.0
            df["idx_ret_20d"] = 0.0
            df["idx_volatility"] = 0.0
        if len(df) < 30:
            row["catatan"] = "data <30 baris setelah diselaraskan IHSG"
            return row
        r = df.iloc[-1]
        if pd.isna(r.get("rsi")):
            row["catatan"] = "indikator belum lengkap (RSI kosong)"
            return row

        # ── PRA-FILTER LIKUIDITAS (permintaan user 16 Sep): nilai transaksi
        # < Rp 800 juta/hari → saham sepi, TIDAK dilanjut (hemat + hasilnya
        # bisa benar-benar diperdagangkan). Estimasi dari candle; data kurang
        # = dilewatkan (jangan buang saham karena data bolong).
        nilai = _nilai_rata2(df)
        if nilai is not None and nilai < MIN_VALUE_20D:
            row["catatan"] = (f"tidak dilanjut — nilai transaksi Rp{nilai/1e6:.0f} jt/hari"
                              f" < {MIN_VALUE_LABEL} (saham sepi · pra-filter)")
            row["tampil"] = "tidak"
            return row

        # ── PRA-FILTER VOLUME DIHAPUS (19 Sep 2026) ──
        # Dulu vol<1.0× dilewati utk hemat faktor mahal. Backtest 1 tahun
        # (../backtest_v7_teknikal.py) membuktikan TIDAK ada bukti vol≥1.0
        # membantu — kohort yang dibuang (vol<1.0) justru sedikit lebih BAIK,
        # dan band terendah (<0.1×) justru terbaik (+5,2% avg · WR 56%).
        # Semua finalis kini diperiksa penuh; veto volume di gate swing juga
        # dihapus (SWING_MIN_VOL_RATIO=0 di v7_scan.py).

        weekly = r.get("weekly_trend", "NO_DATA")
        v4s = compute_total_score(r, regime)
        v7r = v7_engine.compute(tkr, v4s, regime, weekly_trend=weekly)
        price = float(r["close"])
        atr = float(r.get("atr", 0) or 0)
        bf = v7r["factors"].get("broker_detail", "")
        vol_ratio = float(r.get("vol_ratio", 1) or 1)
        if not math.isfinite(vol_ratio):
            vol_ratio = 1.0
        score = float(v7r["score"])
        label = v7r["signal"]
        row["skor"] = f"{score:.1f}"
        bft = _bf_tag(bf)  # label broker utk catatan (terlihat di kartu)

        # ── LAB AKURASI (mode bayangan, 19 Sep 2026): DNA faktor SEMUA finalis ──
        # Dicatat lewat row["_shadow"] → ditulis ke data/shadow_v7.csv oleh
        # pemanggil (scan_mandiri.main / scan_ihsg.main). Termasuk kandidat
        # yang TIDAK jadi sinyal — supaya faktor broker/asing (45% skor, tanpa
        # riwayat → tak bisa di-backtest) bisa diukur forward via shadow_eval.py.
        _f = v7r.get("factors") or {}

        def _num(key):
            try:
                return round(float(_f.get(key, 0) or 0), 1)
            except (TypeError, ValueError):
                return ""

        sh = {
            "kode": tkr, "regime": regime, "skor": f"{score:.1f}", "label": label,
            "v4_core": _num("v4_core"),
            "broker_flow": _num("broker_flow"),
            "broker_flow_raw": _num("broker_flow_raw"),
            "broker_trend": _num("broker_trend"),
            "flow_spike": int(bool(_f.get("flow_spike"))),
            "conflict": int(bool(_f.get("conflict_snapshot_vs_trend"))),
            "foreign_flow": _num("foreign_flow"),
            "fundamental": _num("fundamental"),
            "earnings_momentum": _num("earnings_momentum"),
            "weekly_trend": str(weekly or "NO_DATA"),
            "harga": f"{price:.2f}",
            "atr_pct": round((atr / price * 100) if price > 0 else 0.0, 2),
            "vol_ratio": round(vol_ratio, 2),
            "bandar_sesi": "", "bandar_3bln": "",
        }

        def _sh_attach(alasan, mode="", tampil="", sl="", tp=""):
            sh.update({"alasan": alasan, "mode": mode, "tampil": tampil,
                       "sl": sl, "tp": tp})
            row["_shadow"] = sh

        # 1) filter regime (sama dgn scan terjadwal — di luar izin = bukan kandidat)
        if label not in allowed:
            row["catatan"] = (f"{label} — di luar izin regime {regime}"
                              + (f" · {bft}" if bft else ""))
            row["tampil"] = "tidak"
            _sh_attach("diblok_regime", tampil="tidak")
            return row

        # 2) gate swing + gate kualitas (volume & quality_gate)
        swing_ok = _swing_gate(score, bf, regime)
        swing_signal = _signal_from_score(score, regime, weekly)
        gate_vol = gate_q = "pass"
        if swing_ok:
            g = gate_swing_signal(True, swing_signal, r.get("vol_ratio"),
                                  regime, r, allowed)
            swing_ok, swing_signal = g["ok"], g["signal"]
            gate_vol, gate_q = g["gate_vol"], g["gate_quality"]

        # 3) cabang intraday (skor >= 48 & lonjakan volume >= 1.2x) — sama dgn nightly
        intra_ok = score >= 48 and vol_ratio >= INTRADAY_MIN_VOL_RATIO

        # 4) zona entry terbaik (modul engine — SATU sumber dgn scan terjadwal)
        try:
            rec = recommend_entry(tkr, price, atr, r, v7r, sentiment)
        except Exception:
            rec = None
        ideal = _entry_ideal(rec)
        # Info "harga bandar" (user 17 Sep) — kini KOLOM TERPISAH (bandar_sesi /
        # bandar_3bln) supaya kartu punya baris sendiri yang mudah dibaca
        # (permintaan user: "buat jadi lebih readable"); angka dari faktor
        # broker_flow (top-5 net buyer, rata-tertimbang).
        try:
            _b, _b3 = _f.get("bandar_avg"), _f.get("bandar_3m")
            if isinstance(_b, (int, float)) and _b > 0:
                row["bandar_sesi"] = f"{int(_b)}"
                sh["bandar_sesi"] = int(_b)
            if isinstance(_b3, (int, float)) and _b3 > 0:
                row["bandar_3bln"] = f"{int(_b3)}"
                sh["bandar_3bln"] = int(_b3)
        except Exception:
            pass

        if swing_ok:
            ex = compute_exit(price, atr, regime, "swing", weekly)
            extra = "" if (gate_vol == "pass" and gate_q == "pass") else " (gate kualitas)"
            row.update(mode="SWING", entry=f"{price:.0f}",
                       sl=f"{ex['stop_loss']:.0f}", tp=f"{ex['take_profit']:.0f}",
                       entry_ideal=ideal, tampil="ya",
                       catatan=(f"SINYAL {swing_signal} · {regime}{extra}"
                                + (f" · {bft}" if bft else "")))
            _sh_attach("sinyal_swing", mode="SWING", tampil="ya",
                       sl=f"{ex['stop_loss']:.0f}", tp=f"{ex['take_profit']:.0f}")
        elif intra_ok:
            ex = compute_exit(price, atr, regime, "intraday", weekly)
            row.update(mode="INTRADAY", entry=f"{price:.0f}",
                       sl=f"{ex['stop_loss']:.0f}", tp=f"{ex['take_profit']:.0f}",
                       entry_ideal=ideal, tampil="ya",
                       catatan=(f"SINYAL {label} (harian) · {regime}"
                                + (f" · {bft}" if bft else "")))
            _sh_attach("sinyal_intraday", mode="INTRADAY", tampil="ya",
                       sl=f"{ex['stop_loss']:.0f}", tp=f"{ex['take_profit']:.0f}")
        else:
            row["catatan"] = (f"{label} — belum lolos gate "
                              f"({regime} · vol {vol_ratio:.1f}\u00d7)"
                              + (f" · {bft}" if bft else ""))
            row["tampil"] = "tidak"
            _sh_attach("gagal_gate", tampil="tidak")
        return row
    except Exception as e:
        row["catatan"] = (f"gagal hitung: {type(e).__name__}: {e}".strip()[:120]
                          .replace(",", ";"))
        return row


def siapkan_konteks():
    """Config + provider + IHSG + regime + izin sinyal + sentimen.

    Dipakai bersama oleh scan_mandiri.py DAN scan_ihsg.py — jangan duplikasi.
    Return (ip, df_ihsg, regime, allowed, sentiment).
    """
    with open(os.path.join(SCAN, "config.yaml"), encoding="utf-8", errors="replace") as f:
        CONFIG = yaml.safe_load(f)
    v7_engine.enabled = bool(CONFIG.get("v7", {}).get("enabled", True))
    v7_engine.configure(CONFIG.get("v7", {}))

    ip = InvezgoProvider()

    try:
        df_ihsg = fetch_ihsg_cached(period="2y")
    except Exception as e:
        print(f"(IHSG gagal diambil: {e} — lanjut tanpa align)", flush=True)
        df_ihsg = pd.DataFrame()
    if df_ihsg is None or df_ihsg.empty or len(df_ihsg) < 21:
        df_ihsg = pd.DataFrame()
    if not df_ihsg.empty and len(df_ihsg) >= 50:
        regime, _, _ = detect_market_regime(compute_all_indicators(df_ihsg.copy()))
    else:
        regime = "RANGING"
    allowed = _allowed_signals(CONFIG, regime)

    # Sentimen pasar (utk entry_timing.recommend_entry) — 1x per scan, murah;
    # gagal → netral (recommend_entry default YELLOW).
    sentiment: dict = {}
    try:
        sentiment = predict_market_sentiment(df_ihsg, ip)
    except Exception as e:
        print(f"(sentimen pasar gagal: {e} — lanjut netral)", flush=True)

    return ip, df_ihsg, regime, allowed, sentiment


def main() -> int:
    ap = argparse.ArgumentParser(description="Screening mandiri V7 (on-demand)")
    ap.add_argument("--tickers", required=True,
                    help="Kode dipisah koma, mis. TLKM,BRPT,CUAN (maks 20)")
    ap.add_argument("--out", default=None,
                    help="Path CSV output (default: idx_alpha_screener/data/mandiri_terakhir.csv)")
    a = ap.parse_args()

    tickers = []
    for t in re.split(r"[,\s;]+", (a.tickers or "").upper()):
        t = t.strip().replace(".JK", "")
        if t and re.fullmatch(r"[A-Z]{2,5}", t) and t not in tickers:
            tickers.append(t)
    tickers = tickers[:MAX_TICKERS]
    if not tickers:
        print("GAGAL: tidak ada kode valid (contoh: TLKM,BRPT)")
        return 1

    out = a.out or os.path.join(SCAN, "data", "mandiri_terakhir.csv")

    ip, df_ihsg, regime, allowed, sentiment = siapkan_konteks()

    n = len(tickers)
    print(f"Scan mandiri: {n} saham · regime {regime} · "
          f"{datetime.now(WIB).strftime('%d/%m %H:%M')} WIB", flush=True)

    rows = []
    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:      # sopan: maks 3 bersamaan
        futs = [ex.submit(scan_satu, ip, tkr, regime, allowed, df_ihsg, sentiment)
                for tkr in tickers]
        for f in as_completed(futs):
            done += 1
            row = f.result()
            rows.append(row)
            print(f"PROGRESS {done}/{n} {row['kode']}", flush=True)
            tanda = row["mode"] or "—"
            print(f"  {row['kode']}: skor {row['skor'] or '—'} ({tanda})", flush=True)

    rows.sort(key=lambda r: -(float(r["skor"]) if r["skor"] else 0.0))

    # ── LAB AKURASI: rekam DNA faktor semua kandidat (mode bayangan) ──
    try:
        from shadow_log import append_rows as _lab_append
        _sh = [r.pop("_shadow", None) for r in rows]
        n_lab = _lab_append([s for s in _sh if s], sumber="mandiri")
        print(f"LAB: {n_lab} kandidat tercatat (mode bayangan)", flush=True)
    except Exception as e:  # noqa: BLE001 — lab tidak boleh mematikan scan
        print(f"LAB: gagal catat ({type(e).__name__}: {e}) — scan tetap jalan", flush=True)

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kode", "skor", "mode", "entry", "sl", "tp",
                    "entry_ideal", "bandar_sesi", "bandar_3bln", "catatan", "tampil"])
        for r in rows:
            w.writerow([r["kode"], r["skor"], r["mode"], r["entry"], r["sl"],
                        r["tp"], r["entry_ideal"], r.get("bandar_sesi", ""),
                        r.get("bandar_3bln", ""), r["catatan"], r["tampil"]])

    n_sig = sum(1 for r in rows if r["entry"])
    n_sembunyi = sum(1 for r in rows if r.get("tampil") == "tidak")
    print(f"RINGKASAN {n} saham dipindai · {n_sig} sinyal berlevel · "
          f"{n_sembunyi} tak tampil (pra-filter/gate)", flush=True)
    print(f"CSV: {out}", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
