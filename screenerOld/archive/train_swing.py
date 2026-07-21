"""
train_swing.py — Train swing trading model.
Gunakan data 1d/1wk untuk prediksi swing 3-30 hari.
"""

import pandas as pd
import numpy as np
import sqlite3
import joblib
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import StandardScaler
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("train_swing")

DB_NAME = "histori_ihsg.db"
MODEL_PATH = "swing_model.pkl"

FITUR = [
    "RSI", "ADX", "BB_Width%", "RRR",
    "MM_Confidence", "MM_vs_Retail_Ratio",
    "IHSG_Change", "USD_Change", "Volume"
]

def main():
    log.info("Loading data for swing model training...")
    conn = sqlite3.connect(DB_NAME)
    df = pd.read_sql("SELECT * FROM hasil_screener", conn)
    conn.close()
    
    if df.empty:
        log.warning("No data found")
        return
    
    # Target: Harga 5 hari ke depan lebih tinggi 3%+
    df = df.sort_values(['Ticker', 'Tanggal'])
    df['Harga_5d'] = df.groupby('Ticker')['Harga'].shift(-5)
    df['Target'] = ((df['Harga_5d'] - df['Harga']) / df['Harga'] > 0.03).astype(int)
    df = df.dropna(subset=FITUR + ['Target'])
    
    log.info(f"Training samples: {len(df)}")
    
    X = df[FITUR].values
    y = df['Target'].values
    
    # Clean
    X = np.nan_to_num(X, nan=0.0)
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    model = GradientBoostingClassifier(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        random_state=42
    )
    model.fit(X_scaled, y)
    
    acc = model.score(X_scaled, y)
    log.info(f"Training accuracy: {acc:.3f}")
    
    bundle = {
        "model": model,
        "scaler": scaler,
        "features": FITUR,
        "accuracy": float(acc),
        "training_date": str(datetime.now()),
    }
    joblib.dump(bundle, MODEL_PATH)
    log.info(f"Model saved to {MODEL_PATH}")

if __name__ == "__main__":
    main()
