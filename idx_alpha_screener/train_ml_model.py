"""
train_ml_model.py — Training Logistic Regression untuk scoring
===============================================================
Mengambil data historis, menghitung 8 fitur conviction, dan melatih
model Logistic Regression untuk memprediksi P(return > 0% dalam 5 hari).

Output: ml_model_weights.json (b0, b1..b8, threshold)

Cara pakai:
  python train_ml_model.py
"""
import sys, os, warnings, json, math
warnings.filterwarnings('ignore')
ROOT = r'C:\Hermes_Workspace\Screener\idx_alpha_screener'
sys.path.insert(0, ROOT)

import numpy as np
import pandas as pd
import logging
logging.basicConfig(level=logging.WARNING)

from data import fetch_with_cache, compute_all_indicators, align_to_market, fetch_ihsg_cached
from regime import detect_market_regime
from v4.conviction import _factor_trend, _factor_volume, _factor_relative_strength
from v4.conviction import _factor_vwap, _factor_rsi, _factor_macd
from v4.conviction import _factor_weekly_trend, _factor_sr_proximity

TICKERS = [
    "BBCA.JK","BBRI.JK","BMRI.JK","BJBR.JK","DMAS.JK","ADMR.JK",
    "TLKM.JK","ASII.JK","UNVR.JK","INCO.JK","PGAS.JK","EXCL.JK",
    "ADRO.JK","ANTM.JK","CTRA.JK","BBNI.JK","BRIS.JK","NCKL.JK",
    "MAPI.JK","ACES.JK","PWON.JK","BSDE.JK","SMRA.JK","POWR.JK",
    "INDF.JK","KLBF.JK","SCMA.JK","MNCN.JK","ERAA.JK","JSMR.JK",
    "BBTN.JK","BNGA.JK","BDMN.JK","NISP.JK","PNBN.JK",
]

print("=" * 60)
print("  TRAINING LOGISTIC REGRESSION MODEL")
print("=" * 60)

df_ihsg = fetch_ihsg_cached(period="2y")
X_all, y_all = [], []
feature_names = ["trend","volume","relative_strength","vwap","rsi","macd","weekly_trend","sr_proximity"]

total_signals = 0
for tkr in TICKERS:
    df = fetch_with_cache(tkr, period="18mo")
    if df.empty or len(df) < 120: continue
    df = compute_all_indicators(df)
    df = align_to_market(df, df_ihsg=df_ihsg)
    df = df.dropna()
    if len(df) < 60: continue
    
    for i in range(40, len(df) - 5):
        row = df.iloc[i]
        if pd.isna(row.get("rsi")): continue
        
        # 8 fitur
        factors = {
            "trend": _factor_trend(row),
            "volume": _factor_volume(row),
            "relative_strength": _factor_relative_strength(row),
            "vwap": _factor_vwap(row),
            "rsi": _factor_rsi(row),
            "macd": _factor_macd(row),
            "weekly_trend": _factor_weekly_trend(row),
            "sr_proximity": _factor_sr_proximity(row),
        }
        
        entry = float(row["close"])
        if entry <= 0: continue
        exit_ = float(df.iloc[i+5]["close"])
        ret = (exit_ - entry) / entry * 100 - 0.4
        
        # Target: 1 jika return > 0%, 0 jika tidak
        target = 1 if ret > 0 else 0
        
        X_all.append([factors[f] for f in feature_names])
        y_all.append(target)
        total_signals += 1

print(f"\nTotal sinyal: {total_signals}")
print(f"Total fitur: {len(feature_names)}")

X = np.array(X_all)
y = np.array(y_all)

# Distribution target
n_pos = y.sum()
n_neg = len(y) - n_pos
print(f"Target: 1 (win)={n_pos} ({n_pos/len(y)*100:.1f}%)  0 (loss)={n_neg} ({n_neg/len(y)*100:.1f}%)")

# ── Logistic Regression via sklearn ──
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score, roc_auc_score

# Split 70/30
X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=42, stratify=y)

# Train
model = LogisticRegression(penalty='l2', C=1.0, solver='lbfgs', max_iter=1000, class_weight='balanced')
model.fit(X_train, y_train)

# Evaluate
y_pred = model.predict(X_test)
y_proba = model.predict_proba(X_test)[:, 1]

print(f"\n── Hasil Training ──")
print(f"  Akurasi:       {accuracy_score(y_test, y_pred)*100:.1f}%")
print(f"  Presisi (win): {precision_score(y_test, y_pred)*100:.1f}%")
print(f"  Recall (win):  {recall_score(y_test, y_pred)*100:.1f}%")
print(f"  F1-score:      {f1_score(y_test, y_pred):.3f}")
print(f"  AUC-ROC:       {roc_auc_score(y_test, y_pred):.3f}")

# ── Coefficient Analysis ──
print(f"\n── Koefisien Model ──")
print(f"  {'Fitur':<20} {'Koefisien':>10} {'Pengaruh':>10}")
print(f"  {'─'*40}")
intercept = model.intercept_[0]
print(f"  {'Intercept':<20} {intercept:>10.4f}")
for name, coef in zip(feature_names, model.coef_[0]):
    direction = "+" if coef > 0 else "-"
    print(f"  {name:<20} {coef:>+10.4f} {'positif' if coef > 0 else 'negatif':>10}")

# ── Cari Threshold Optimal ──
print(f"\n── Mencari Threshold Optimal ──")
results = []
for th in np.arange(0.30, 0.85, 0.02):
    pred = (y_proba >= th).astype(int)
    wr = (pred[y_test == 1].sum() / max(pred.sum(), 1)) * 100
    n_sinyal = pred.sum()
    if n_sinyal >= 5:
        results.append({"threshold": round(th, 2), "wr": round(wr, 1), "n": n_sinyal})

# Top 5 by WR
results.sort(key=lambda x: x["wr"], reverse=True)
print(f"  {'Threshold':>10} {'WR%':>7} {'N':>5}")
for r in results[:8]:
    print(f"  {r['threshold']:>10.2f} {r['wr']:>7.1f}% {r['n']:>5}")

# Cari threshold optimal: WR tertinggi dengan minimal sinyal
best_th = 0.55
for r in results:
    if r["n"] >= 20 and r["wr"] > 50:
        best_th = r["threshold"]
        break

# ── Save Model Weights ──
weights = {
    "b0": float(intercept),
}
for i, (name, coef) in enumerate(zip(feature_names, model.coef_[0])):
    weights[f"b{i+1}"] = float(coef)

model_data = {
    "model": "LogisticRegression",
    "features": feature_names,
    "weights": weights,
    "threshold": round(best_th, 2),
    "training_metrics": {
        "n_samples": len(y),
        "n_features": len(feature_names),
        "accuracy": round(accuracy_score(y_test, y_pred)*100, 1),
        "auc_roc": round(roc_auc_score(y_test, y_pred), 3),
        "class_balance": f"{n_pos}/{n_neg}",
    },
    "feature_importance": {
        name: round(abs(coef), 4)
        for name, coef in zip(feature_names, model.coef_[0])
    }
}

output_path = os.path.join(ROOT, "ml_model_weights.json")
with open(output_path, "w") as f:
    json.dump(model_data, f, indent=2)

print(f"\n✅ Model disimpan ke {output_path}")
print(f"   Threshold: {best_th}")
print(f"   AUC-ROC: {roc_auc_score(y_test, y_pred):.3f}")
print(f"{'='*60}")
