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

Baris hasil utk SEMUA ticker (yang tidak lolos gate ikut tampil dengan skor &
alasan) supaya user bisa screening mandiri — bukan cuma sinyal yang muncul.

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
import v7 as v7_engine
from v7_exit import compute_exit
from v7_scan import (_signal_from_score, _swing_gate, gate_swing_signal,
                     INTRADAY_MIN_VOL_RATIO)

WIB = timezone(timedelta(hours=7))
MAX_TICKERS = 20


def _allowed_signals(cfg: dict, regime: str) -> set:
    """Filter regime — sama persis dengan market_mode di v7_scan.main()."""
    if cfg.get("market_mode", {}).get("enabled", True):
        if regime in ("BEAR", "HIGH_VOLATILITY"):
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


def scan_satu(ip, tkr: str, regime: str, allowed: set, df_ihsg) -> dict:
    """Hitung skor & sinyal V7 utk SATU ticker → dict baris CSV.

    Selalu mengembalikan baris (kalau gagal, catatan berisi alasannya) supaya
    semua ticker yang diminta user tetap terlihat di halaman /screener.
    """
    row = {"kode": tkr, "skor": "", "mode": "", "entry": "", "sl": "", "tp": "",
           "catatan": ""}
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

        # 1) filter regime (sama dgn scan terjadwal — di luar izin = bukan kandidat)
        if label not in allowed:
            row["catatan"] = (f"{label} — di luar izin regime {regime}"
                              + (f" · {bft}" if bft else ""))
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

        if swing_ok:
            ex = compute_exit(price, atr, regime, "swing", weekly)
            extra = "" if (gate_vol == "pass" and gate_q == "pass") else " (gate kualitas)"
            row.update(mode="SWING", entry=f"{price:.0f}",
                       sl=f"{ex['stop_loss']:.0f}", tp=f"{ex['take_profit']:.0f}",
                       catatan=(f"SINYAL {swing_signal} · {regime}{extra}"
                                + (f" · {bft}" if bft else "")))
        elif intra_ok:
            ex = compute_exit(price, atr, regime, "intraday", weekly)
            row.update(mode="INTRADAY", entry=f"{price:.0f}",
                       sl=f"{ex['stop_loss']:.0f}", tp=f"{ex['take_profit']:.0f}",
                       catatan=(f"SINYAL {label} (harian) · {regime}"
                                + (f" · {bft}" if bft else "")))
        else:
            row["catatan"] = (f"{label} — belum lolos gate "
                              f"({regime}, vol {vol_ratio:.1f}\u00d7)"
                              + (f" · {bft}" if bft else ""))
        return row
    except Exception as e:
        row["catatan"] = f"gagal hitung: {type(e).__name__}: {e}".strip()[:120]
        return row


def siapkan_konteks():
    """Config + provider + IHSG + regime + izin sinyal.

    Dipakai bersama oleh scan_mandiri.py DAN scan_ihsg.py — jangan duplikasi.
    Return (ip, df_ihsg, regime, allowed).
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
    return ip, df_ihsg, regime, allowed


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

    ip, df_ihsg, regime, allowed = siapkan_konteks()

    n = len(tickers)
    print(f"Scan mandiri: {n} saham · regime {regime} · "
          f"{datetime.now(WIB).strftime('%d/%m %H:%M')} WIB", flush=True)

    rows = []
    for i, tkr in enumerate(tickers, 1):
        print(f"PROGRESS {i}/{n} {tkr}", flush=True)
        row = scan_satu(ip, tkr, regime, allowed, df_ihsg)
        rows.append(row)
        tanda = row["mode"] or "—"
        print(f"  {tkr}: skor {row['skor'] or '—'} ({tanda})", flush=True)

    rows.sort(key=lambda r: -(float(r["skor"]) if r["skor"] else 0.0))

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kode", "skor", "mode", "entry", "sl", "tp", "catatan"])
        for r in rows:
            w.writerow([r["kode"], r["skor"], r["mode"], r["entry"], r["sl"],
                        r["tp"], r["catatan"]])

    n_sig = sum(1 for r in rows if r["entry"])
    print(f"HASIL {n_sig} sinyal berlevel dari {n} saham", flush=True)
    print(f"CSV: {out}", flush=True)
    print("DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
