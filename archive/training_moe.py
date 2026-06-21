import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
import sqlite3

DB_NAME = "histori_ihsg.db"
# ... [Konfigurasi dari auto_train.py]

FITUR_AI = ["Skor", "Confidence%", "RSI", "ADX", "Stoch", "CCI", "BB_Width%", "RRR", 
            "MM_Confidence", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change"]

from liquid_moe import LiquidMoE

class MoeManager:
    def __init__(self, num_experts=3, hidden_size=32):
        self.num_experts = num_experts
        self.hidden_size = hidden_size

    def load_data(self):
        conn = sqlite3.connect(DB_NAME)
        df = pd.read_sql("SELECT * FROM hasil_screener", conn)
        conn.close()
        df = df.sort_values(by=['Ticker', 'Tanggal'])
        df['Target'] = (df.groupby('Ticker')['Skor'].shift(-1) >= 11).astype(int)
        df = df.dropna(subset=FITUR_AI + ['Target'])
        X = df[FITUR_AI].values
        y = df['Target'].values
        # Normalisasi sederhana
        ss = StandardScaler()
        X = ss.fit_transform(X)
        return X, y, ss

    def build_and_train(self):
        X, y, scaler = self.load_data()
        if len(X) < 100:
            print("Data kurang dari 100 sampel, skip training")
            return None
        # Panggil LiquidMoE dan latih dengan sequence length = 1 (data tidak berurutan)
        # Karena data sudah di-shift per ticker, kita bisa treat per baris.
        X_t = torch.tensor(X, dtype=torch.float32).unsqueeze(1)  # [N, 1, features]
        y_t = torch.tensor(y, dtype=torch.float32).unsqueeze(1)
        model = LiquidMoE(self.num_experts, X.shape[1], self.hidden_size)
        opt = torch.optim.Adam(model.parameters(), lr=1e-3)
        loss_fn = nn.BCEWithLogitsLoss()
        for epoch in range(100):
            opt.zero_grad()
            out = model(X_t)
            loss = loss_fn(out, y_t)
            loss.backward()
            opt.step()
        return model, scaler

if __name__ == "__main__":
    mm = MoeManager()
    result = mm.build_and_train()
    if result:
        print("Training selesai!")
