"""
============================================================
  AI TRAINING MODULE v5.0 — SMOTE + ENSEMBLE + THRESHOLD TUNING
  Membaca CSV screener, memvalidasi dengan harga masa depan nyata,
  melatih ensemble XGBoost+RF+HGB dengan SMOTE, optimal threshold,
  dan mengekspor model siap pakai untuk screener.

  CHANGELOG v5.0:
  - [NEW] SMOTE oversampling pada training data (via imbalanced-learn)
  - [NEW] Optimal threshold tuning via precision-recall curve
  - [NEW] Ensemble VotingClassifier: XGBoost + RandomForest + HistGradientBoosting
  - [NEW] Export ensemble ke 'ensemble_model.pkl' untuk screener
  - [KEPT] Stratified time-based split + holdout
  - [KEPT] Sideways trades sebagai NOT-WIN (tidak dihapus)
============================================================
"""

import pandas as pd
import numpy as np
import yfinance as yf
import xgboost as xgb
from sklearn.ensemble import RandomForestClassifier, HistGradientBoostingClassifier, VotingClassifier
from sklearn.model_selection import TimeSeriesSplit, RandomizedSearchCV
from sklearn.metrics import (accuracy_score, classification_report, f1_score,
                              precision_score, recall_score, precision_recall_curve)
from imblearn.over_sampling import SMOTE
import joblib
import warnings
import datetime
import time
import os
import glob
import re
import sys
import io

# Fix UTF-8 encoding for Windows console
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

warnings.filterwarnings("ignore")

print("=" * 60)
print("  AI TRAIN v5.0 — SMOTE + ENSEMBLE + THRESHOLD TUNING")
print("=" * 60)

# ==============================================================================
# 1. BACA DATA DARI CSV SCREENER
# ==============================================================================
csv_files = []
csv_files.extend(glob.glob("screener_ihsg_*.csv"))
csv_files.extend(glob.glob("Data Screener/screener_ihsg_*.csv"))

if not csv_files:
    print("[ERROR] Tidak ditemukan file screener_ihsg_*.csv.")
    exit()

print(f"\n[DATA] {len(csv_files)} file CSV historis")
df_list = []
for f in sorted(csv_files):
    try:
        df_part = pd.read_csv(f)
        if "Tanggal" not in df_part.columns:
            match = re.search(r'(\d{8})', f)
            if match:
                df_part["Tanggal"] = pd.to_datetime(match.group(1), format='%Y%m%d')
        df_list.append(df_part)
    except Exception as e:
        print(f"       [WARN] Gagal baca {f}: {e}")

df = pd.concat(df_list, ignore_index=True)
df['Tanggal'] = pd.to_datetime(df['Tanggal'])
df = df[df['Sinyal'] != 'HINDARI']
print(f"       {len(df)} baris (setelah filter HINDARI)")


# v10.1: Historical data awareness
historical_parquet = "data_lake/ohlcv_historical.parquet"
if os.path.exists(historical_parquet):
    print("\n[DATA] 2-year historical OHLCV data found.")
    df_hist = pd.read_parquet(historical_parquet)
    print(f"       {len(df_hist):,} rows, {df_hist['Ticker'].nunique()} tickers")
    print(f"       {df_hist['Tanggal'].min().date()} -> {df_hist['Tanggal'].max().date()}")
    print("       NOTE: Available for future training expansion.")
else:
    print("\n[DATA] No historical data. Run python backfill_data.py first.")
# ==============================================================================
# 2. DOWNLOAD FORWARD PRICES (GROUND TRUTH)
# ==============================================================================
print("\n[DOWNLOAD] Forward prices dari Yahoo Finance...")
tickers_unik = df['Ticker'].unique()
tanggal_mulai = df['Tanggal'].min().strftime('%Y-%m-%d')
tanggal_akhir = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

harga_historis = {}
for i, ticker in enumerate(tickers_unik, 1):
    if i % 20 == 0:
        print(f"   [{i}/{len(tickers_unik)}] ...", end="\r")
    tkr_yf = ticker if ticker.endswith(".JK") else f"{ticker}.JK"
    try:
        tkr_obj = yf.Ticker(tkr_yf)
        data = tkr_obj.history(start=tanggal_mulai, end=tanggal_akhir)
        if not data.empty:
            if data.index.tz is not None:
                data.index = data.index.tz_localize(None)
            harga_historis[ticker] = data
    except:
        pass
    time.sleep(0.1)
print(f"   [{len(tickers_unik)}/{len(tickers_unik)}] Selesai. {len(harga_historis)} ticker.\n")

# ==============================================================================
# 3. LABELING (WIN / LOSS / SIDEWAYS)
# ==============================================================================
print("[LABEL] Koreksi hasil masa lalu...")
HAMBATAN_PASAR = 0.006
labels = []
stats = {"WIN": 0, "LOSS": 0, "SIDEWAYS": 0}

for _, row in df.iterrows():
    ticker = row['Ticker']
    tgl = row['Tanggal']
    tp_raw = row['Target_1']
    sl_raw = row['Stop_Loss']

    if ticker not in harga_historis or harga_historis[ticker].empty:
        labels.append(np.nan); continue

    df_h = harga_historis[ticker]
    fwd = df_h.loc[df_h.index > tgl].head(10)
    if fwd.empty:
        labels.append(np.nan); continue

    status = 2  # SIDEWAYS
    tp_real = tp_raw * (1 + HAMBATAN_PASAR)
    sl_real = sl_raw * (1 + HAMBATAN_PASAR / 2)

    for _, bar in fwd.iterrows():
        if bar['High'] >= tp_real:
            status = 1; break
        elif bar['Low'] <= sl_real:
            status = 0; break

    stats[["SIDEWAYS","LOSS","WIN"][status]] += 1
    labels.append(status)

df['Target_Menang'] = labels
df = df.dropna(subset=['Target_Menang'])
df['Target_Menang'] = df['Target_Menang'].astype(int)

print(f"       WIN={stats['WIN']} ({stats['WIN']/max(1,sum(stats.values()))*100:.1f}%)")
print(f"       LOSS={stats['LOSS']} SIDEWAYS={stats['SIDEWAYS']}")

# ==============================================================================
# 4. FEATURES & STRATIFIED TIME-BASED SPLIT
# ==============================================================================
features_columns = [
    "Skor", "Confidence%", "RSI", "ADX", "Stoch", "CCI",
    "BB_Width%", "RRR", "MM_Confidence", "MM_vs_Retail_Ratio",
    "IHSG_Change", "USD_Change", "RSI_1d", "MACD_1d"
]

df = df.dropna(subset=features_columns)
df = df.sort_values('Tanggal').reset_index(drop=True)

X_full = df[features_columns].values
y_full = (df['Target_Menang'] == 1).astype(int).values

# FIX: TRUE chronological split — no np.random.shuffle!
# "Is there look-ahead bias? (check index alignment meticulously)" — SKILL.md
# Data oldest 70% → train, next 15% → val, newest 15% → test
n_total = len(X_full)
n_train = int(n_total * 0.70)
n_val   = int(n_total * 0.85)

X_train, y_train = X_full[:n_train], y_full[:n_train]
X_val,   y_val   = X_full[n_train:n_val], y_full[n_train:n_val]
X_test,  y_test  = X_full[n_val:], y_full[n_val:]

print(f"\n[SPLIT] Train={len(X_train)}(WIN={y_train.sum()}) Val={len(X_val)}(WIN={y_val.sum()}) Test={len(X_test)}(WIN={y_test.sum()})")

# ==============================================================================
# 5. SMOTE OVERSAMPLING (on TRAIN ONLY)
# ==============================================================================
print(f"\n[SMOTE] Oversampling WIN class pada TRAIN data saja...")
print(f"        Sebelum: {y_train.sum()} WIN vs {(y_train==0).sum()} NOT-WIN")

smote = SMOTE(sampling_strategy=0.50, random_state=42)  # Target 50% WIN
X_train_sm, y_train_sm = smote.fit_resample(X_train, y_train)

print(f"        Sesudah: {y_train_sm.sum()} WIN vs {(y_train_sm==0).sum()} NOT-WIN  (ratio={y_train_sm.sum()/len(y_train_sm):.2f})")

# ==============================================================================
# 6. TRAIN ENSEMBLE: XGBoost + RandomForest + HistGradientBoosting
# ==============================================================================
print(f"\n[TRAIN] Ensemble VotingClassifier (XGB + RF + HGB)...")

scale_w = len(y_train[y_train==0]) / max(1, len(y_train[y_train==1]))

# XGBoost
xgb_model = xgb.XGBClassifier(
    objective='binary:logistic', eval_metric='logloss',
    max_depth=4, n_estimators=200, learning_rate=0.05,
    scale_pos_weight=scale_w, random_state=42,
    subsample=0.8, colsample_bytree=0.8,
    reg_lambda=1.0, reg_alpha=0.1
)

# Random Forest
rf_model = RandomForestClassifier(
    n_estimators=200, max_depth=8,
    min_samples_split=10, min_samples_leaf=5,
    class_weight='balanced', random_state=42, n_jobs=-1
)

# HistGradientBoosting
hgb_model = HistGradientBoostingClassifier(
    max_iter=300, max_depth=5, learning_rate=0.05,
    l2_regularization=0.5, early_stopping=True,
    validation_fraction=0.15, n_iter_no_change=20,
    random_state=42, class_weight='balanced'
)

# Voting Ensemble (soft voting = rata-rata probabilitas)
ensemble = VotingClassifier(
    estimators=[
        ('xgb', xgb_model),
        ('rf', rf_model),
        ('hgb', hgb_model),
    ],
    voting='soft'  # Rata-rata probabilitas → lebih stabil
)

print("       Fitting ensemble pada SMOTE-augmented data...")
ensemble.fit(X_train_sm, y_train_sm)
print("       [OK] Ensemble trained.")

# ==============================================================================
# 7. FIND OPTIMAL THRESHOLD
# ==============================================================================
print(f"\n[THRESHOLD] Mencari threshold optimal via Precision-Recall curve...")

y_val_proba = ensemble.predict_proba(X_val)[:, 1]
precisions, recalls, thresholds = precision_recall_curve(y_val, y_val_proba)

# Cari threshold yang memaksimalkan F1
f1_scores = 2 * (precisions * recalls) / (precisions + recalls + 1e-10)
best_threshold = thresholds[np.argmax(f1_scores)]
best_f1_at_threshold = np.max(f1_scores)

print(f"       Best threshold: {best_threshold:.3f}  (default=0.500)")
print(f"       F1 at best threshold: {best_f1_at_threshold*100:.1f}%")

# ==============================================================================
# 8. EVALUATE ON HOLDOUT WITH OPTIMAL THRESHOLD
# ==============================================================================
y_test_proba = ensemble.predict_proba(X_test)[:, 1]
y_pred_default = (y_test_proba >= 0.50).astype(int)
y_pred_optimal = (y_test_proba >= best_threshold).astype(int)

f1_default  = f1_score(y_test, y_pred_default, zero_division=0) * 100
f1_optimal  = f1_score(y_test, y_pred_optimal, zero_division=0) * 100
rec_default = recall_score(y_test, y_pred_default, zero_division=0) * 100
rec_optimal = recall_score(y_test, y_pred_optimal, zero_division=0) * 100
prec_default = precision_score(y_test, y_pred_default, zero_division=0) * 100
prec_optimal = precision_score(y_test, y_pred_optimal, zero_division=0) * 100

print(f"\n[EVAL] PERBANDINGAN PADA HOLDOUT ({len(X_test)} baris, WIN={y_test.sum()}):")
print(f"       {'':25} {'Default(0.50)':>15} {'Optimal(' + str(round(best_threshold,3)) + ')':>15}")
print(f"       {'-'*55}")
print(f"       {'F1-Score':25} {f1_default:>14.1f}% {f1_optimal:>15.1f}%")
print(f"       {'Recall':25} {rec_default:>14.1f}% {rec_optimal:>15.1f}%")
print(f"       {'Precision':25} {prec_default:>14.1f}% {prec_optimal:>15.1f}%")

print(f"\n       >>> Menggunakan threshold={best_threshold:.3f} untuk prediksi final.")

# ==============================================================================
# 9. EXPORT ENSEMBLE + THRESHOLD
# ==============================================================================
export_path = "ensemble_model.pkl"
joblib.dump({
    "ensemble": ensemble,
    "threshold": float(best_threshold),
    "features": features_columns,
    "n_features": len(features_columns),
    "trained_at": datetime.datetime.now().isoformat(),
    "n_train": len(X_train),
    "n_train_smote": len(X_train_sm),
    "n_test": len(X_test),
    "n_test_win": int(y_test.sum()),
    "holdout_f1": float(f1_optimal),
    "holdout_recall": float(rec_optimal),
    "holdout_precision": float(prec_optimal),
    "version": "5.0",
}, export_path)

print(f"\n[EXPORT] Ensemble + threshold tersimpan di '{export_path}'")
print(f"         Siap dipakai oleh screener.py.")

# ==============================================================================
# 10. FEATURE IMPORTANCE (from XGBoost)
# ==============================================================================
xgb_trained = ensemble.named_estimators_['xgb']
importances = xgb_trained.feature_importances_
print("\n[TOP 5] Feature Importance (XGBoost):")
feat_imp = pd.DataFrame({'Feature': features_columns, 'Importance': importances})
feat_imp = feat_imp.sort_values('Importance', ascending=False)
for _, r in feat_imp.head(5).iterrows():
    print(f"       {r['Feature']:<20}: {r['Importance']*100:.1f}%")

print(f"\n{'='*60}")
print(f"  TRAINING SELESAI — Model siap pakai.")
print(f"  F1 Holdout (optimal threshold): {f1_optimal:.1f}%")
print(f"  Recall Holdout: {rec_optimal:.1f}% | Precision: {prec_optimal:.1f}%")
print(f"{'='*60}")
