"""
=============================================================================
  SWING TRADING AI TRAINER v2.0  [FIXED]

  CHANGELOG v2.0:
  - [FIX #1] Validasi kolom — tidak crash jika kolom hilang dari Parquet
  - [FIX #2] Minimum data check — tolak training jika data < MIN_SAMPLES
  - [FIX #3] Cetak Win/Loss class distribution sebelum training
  - [FIX #4] Evaluasi model: accuracy, precision, recall, F1, CV scores
  - [FIX #5] Feature importance dicetak setelah training
  - [FIX #6] Label imbalance di-detect dan diperingatkan
=============================================================================
"""

import sys
import pandas as pd
import numpy as np
from ai_model import get_ai_model, FEATURE_NAMES, N_FEATURES

# ─── Konfigurasi ──────────────────────────────────────────────────────────────
PARQUET_PATH    = "data_lake/histori_ihsg.parquet"
MIN_SAMPLES     = 200   # Minimal baris agar training bermakna
FUTURE_DAYS     = 3     # Labeling: naik berapa hari ke depan
WIN_THRESHOLD   = 1.02  # +2% dianggap WIN (sudah include estimasi biaya transaksi)

# Mapping nama kolom Parquet → nama fitur FEATURE_NAMES
# Key  = nama kolom di Parquet (harus ada di kolom_aman screener.py)
# Value = nama fitur (harus urutan sama dengan FEATURE_NAMES)
KOLOM_FITUR = {
    "RSI"               : "rsi",
    "ADX"               : "adx",
    "Volume"            : "vol_strength",
    "BB_Width%"         : "bb_width",
    "RRR"               : "rrr",
    "MM_Confidence"     : "mm_confidence",
    "MM_vs_Retail_Ratio": "mm_vs_retail_ratio",
    "IHSG_Change"       : "ihsg_change",
    "USD_Change"        : "usd_change",
    "RSI_1d"            : "rsi_1d",
    "MACD_1d"           : "macd_1d",
    "RSI_Vol_Interaction": "rsi_vol_interaction",
    "Rolling_Vol_20"     : "rolling_vol_20",
    "Sector_Corr"        : "sector_corr",
}

FITUR_KOLOM = list(KOLOM_FITUR.keys())   # Daftar nama kolom yang diambil dari Parquet
LABEL_KOLOM = "Harga"                    # Kolom harga penutupan untuk labeling


def _validate_columns(df: pd.DataFrame) -> list[str]:
    """
    [FIX #1] Periksa kolom yang hilang. Return list nama kolom yang tidak ada.
    """
    required = FITUR_KOLOM + [LABEL_KOLOM, "Ticker", "Tanggal"]
    missing = [c for c in required if c not in df.columns]
    return missing


def _print_class_distribution(y: pd.Series, total: int):
    """[FIX #3] Cetak distribusi kelas WIN/LOSS."""
    win_count  = int(y.sum())
    loss_count = total - win_count
    win_pct    = win_count  / total * 100
    loss_pct   = loss_count / total * 100

    print(f"\n   📊 Distribusi Label:")
    print(f"   WIN  (naik >{(WIN_THRESHOLD-1)*100:.0f}% dalam {FUTURE_DAYS}h): {win_count:>5} ({win_pct:.1f}%)")
    print(f"   LOSS                                   : {loss_count:>5} ({loss_pct:.1f}%)")

    # [FIX #6] Deteksi imbalance parah
    ratio = win_count / loss_count if loss_count > 0 else float("inf")
    if ratio > 4 or ratio < 0.25:
        print(f"\n   ⚠️ PERINGATAN: Imbalance parah (ratio WIN:LOSS = {ratio:.2f})!")
        print(f"   Model menggunakan class_weight='balanced', tapi pertimbangkan")
        print(f"   SMOTE atau penyesuaian WIN_THRESHOLD ({WIN_THRESHOLD}).")


def train_ai_swing():
    print("=" * 55)
    print("  🧠 SWING TRADING AI TRAINER v2.0")
    print("=" * 55)

    # ── 1. Baca data dari Parquet ────────────────────────────────────────────
    try:
        df = pd.read_parquet(PARQUET_PATH)
    except FileNotFoundError:
        print(f"\n❌ File '{PARQUET_PATH}' tidak ditemukan!")
        print("   Jalankan screener.py minimal beberapa hari untuk mengisi data lake.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Gagal membaca Parquet: {e}")
        sys.exit(1)

    if df.empty:
        print("❌ Data Parquet kosong. Jalankan screener dulu beberapa hari.")
        sys.exit(1)

    print(f"\n   📂 Loaded: {len(df):,} baris, {df['Ticker'].nunique()} ticker unik")
    print(f"   Rentang: {df['Tanggal'].min()} → {df['Tanggal'].max()}")

    # ── 2. [FIX #1] Validasi kolom ──────────────────────────────────────────
    missing_cols = _validate_columns(df)
    if missing_cols:
        print(f"\n❌ Kolom berikut tidak ditemukan di Parquet: {missing_cols}")
        print("   Pastikan screener.py menyimpan kolom-kolom tersebut ke 'kolom_aman'.")
        sys.exit(1)

    # ── 3. Sort waktu (krusial untuk labeling masa depan) ───────────────────
    df = df.sort_values(by=["Ticker", "Tanggal"]).reset_index(drop=True)

    # ── 4. Labeling: WIN = harga naik ≥ WIN_THRESHOLD dalam FUTURE_DAYS hari ─
    df["Harga_Future"] = df.groupby("Ticker")[LABEL_KOLOM].shift(-FUTURE_DAYS)
    df_clean = df.dropna(subset=["Harga_Future"]).copy()

    if len(df_clean) == 0:
        print("❌ Tidak ada data setelah labeling. Tambah lebih banyak riwayat.")
        sys.exit(1)

    y = (df_clean["Harga_Future"] > df_clean[LABEL_KOLOM] * WIN_THRESHOLD).astype(int)

    # ── 5. [FIX #2] Minimum data check ─────────────────────────────────────
    if len(df_clean) < MIN_SAMPLES:
        print(f"\n⚠️ Data hanya {len(df_clean)} baris (minimum: {MIN_SAMPLES}).")
        print("   Training dibatalkan. Kumpulkan lebih banyak data terlebih dahulu.")
        sys.exit(1)

    _print_class_distribution(y, len(df_clean))

    # ── 6. Siapkan fitur ─────────────────────────────────────────────────────
    # Ambil kolom sesuai urutan FEATURE_NAMES (11 fitur, urutan HARUS sama)
    X = df_clean[FITUR_KOLOM].copy()
    X.columns = list(KOLOM_FITUR.values())  # Rename ke nama fitur standar

    # Sanitasi: isi NaN dengan 0, clip outlier ekstrem
    X = X.fillna(0)
    for col in X.columns:
        q99 = X[col].quantile(0.99)
        q01 = X[col].quantile(0.01)
        X[col] = X[col].clip(lower=q01, upper=q99)

    # Pastikan urutan kolom sesuai FEATURE_NAMES
    try:
        X = X[FEATURE_NAMES]
    except KeyError as e:
        print(f"\n❌ Urutan fitur tidak cocok dengan FEATURE_NAMES: {e}")
        print(f"   FEATURE_NAMES = {FEATURE_NAMES}")
        sys.exit(1)

    print(f"\n   ✅ Fitur siap: {X.shape[0]} baris × {X.shape[1]} kolom")
    print(f"   Fitur: {FEATURE_NAMES}")

    # ── 7. Eksekusi Training ─────────────────────────────────────────────────
    print("\n   ⏳ Menyuntikkan pengalaman trading ke Otak AI...")
    ai_swing = get_ai_model(model_type="swing")
    ai_swing.train_model(X, y)

    print("\n✅ SUKSES! Model siap digunakan besok pagi!")
    print(f"   Info model: {ai_swing.get_model_info()}")


if __name__ == "__main__":
    train_ai_swing()