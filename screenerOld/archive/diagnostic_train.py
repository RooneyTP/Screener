# diagnostic_train.py
import sqlite3
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from liquid_moe import LiquidMoE

# ... (Copy konfigurasi DB_NAME dan FITUR_AI dari auto_train_11.py) ...
# 🔥 TAMBAHKAN BLOK INI:
FITUR_AI = [
    "RSI", "ADX", "Volume", "BB_Width%", "RRR", 
    "MM_Confidence", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change", 
    "RSI_Kemarin", "MACD_Kemarin"
]

def walk_forward_test():
    print("🔬 MEMULAI WALK-FORWARD VALIDATION (5 FOLDS)...")
    
    # 1. Tarik Data & Preprocessing persis seperti auto_train
    conn = sqlite3.connect("histori_ihsg.db")
    df = pd.read_sql("SELECT * FROM hasil_screener", conn)
    conn.close()
    
    df = df.sort_values(by=['Ticker', 'Tanggal'])
    df['RSI_Kemarin'] = df.groupby('Ticker')['RSI'].shift(1)
    df['MACD_Kemarin'] = df.groupby('Ticker')['MACD_1d'].shift(1)
    df['Harga_Besok'] = df.groupby('Ticker')['Harga'].shift(-1)
    df = df.dropna(subset=['Harga_Besok', 'RSI_Kemarin'])
    
    X = df[FITUR_AI].values
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    y = (df['Harga_Besok'] > df['Harga']).astype(float).values

    # 2. Time-Series Split (Membelah waktu secara dinamis)
    tscv = TimeSeriesSplit(n_splits=5)
    
    fold = 1
    for train_index, test_index in tscv.split(X):
        X_train, X_test = X[train_index], X[test_index]
        y_train, y_test = y[train_index], y[test_index]
        
        # Normalisasi HANYA berdasarkan data Train (mencegah kebocoran masa depan)
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        # Konversi ke Tensor
        X_tr_t = torch.tensor(X_train_scaled, dtype=torch.float32).unsqueeze(1)
        y_tr_t = torch.tensor(y_train, dtype=torch.float32).unsqueeze(1)
        X_te_t = torch.tensor(X_test_scaled, dtype=torch.float32).unsqueeze(1)
        
        # Inisialisasi Model Baru setiap fold
        model = LiquidMoE(num_experts=3, input_size=len(FITUR_AI), hidden_size=32)
        optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
        criterion = nn.BCEWithLogitsLoss()
        
        # Training singkat (misal 100 epoch)
        model.train()
        for _ in range(100):
            optimizer.zero_grad()
            loss = criterion(model(X_tr_t), y_tr_t)
            loss.backward()
            optimizer.step()
            
        # Uji coba di data masa depan (Test Set)
        model.eval()
        with torch.no_grad():
            pred_raw = model(X_te_t).squeeze()
            pred_prob = torch.sigmoid(pred_raw).numpy()
            
            # Hitung akurasi jika Win Rate > 50%
            tebakan_benar = ((pred_prob > 0.5) == (y_test > 0.5)).mean() * 100
            
        print(f"✅ Fold {fold}: Train {len(X_train)} baris -> Test {len(X_test)} baris | Akurasi AI: {tebakan_benar:.1f}%")
        fold += 1

if __name__ == "__main__":
    walk_forward_test()