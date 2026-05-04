import sqlite3
import pandas as pd
import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from sklearn.preprocessing import StandardScaler

class MarketDataset(Dataset):
    def __init__(self, db_path="histori_ihsg.db", sequence_length=30):
        print(":gear: Mengekstraksi data dari SQLite...")
        conn = sqlite3.connect(db_path)
# Ambil data. Pastikan tabelnya sesuai dengan nama tabelmu.
        self.df = pd.read_sql("SELECT * FROM hasil_screener", conn)
        conn.close()

        # 1. PERSIAPAN DATA & TARGET LEAKAGE PREVENTION
        if 'Tanggal' in self.df.columns:
            self.df = self.df.sort_values(by=['Ticker', 'Tanggal'])
        
        # Target: Apakah BESOK skornya bagus? (1 = Ya, 0 = Tidak)
        self.df['Target'] = (self.df.groupby('Ticker')['Skor'].shift(-1) >= 11).astype(int)
        self.df = self.df.dropna()

        # Fitur/Indikator yang akan dipelajari AI (Sesuaikan jika ada yang berbeda)
        fitur = ["Skor", "Confidence%", "RSI", "ADX", "Stoch", "CCI", "BB_Width%", "RRR", 
                 "MM_Confidence", "MM_vs_Retail_Ratio", "IHSG_Change", "USD_Change", "RSI_1d", "MACD_1d"]
        
        # 2. NORMALISASI (Sangat krusial untuk Deep Learning)
        self.scaler = StandardScaler()
        self.df[fitur] = self.scaler.fit_transform(self.df[fitur])

        # 3. MEMOTONG JADI 3 DIMENSI (Sliding Window)
        print(f":knife: Memotong data menjadi Sequence {sequence_length} hari...")
        self.X_data = []
        self.y_data = []
# Kelompokkan per saham agar memori masa lalunya tidak tertukar antar saham
        for ticker, group in self.df.groupby('Ticker'):
            fitur_saham = group[fitur].values
            target_saham = group['Target'].values
            
            # Geser jendela (sliding window) hari demi hari
            for i in range(len(group) - sequence_length):
                self.X_data.append(fitur_saham[i : i + sequence_length])
                self.y_data.append(target_saham[i + sequence_length - 1]) # Target di hari terakhir sequence

        # Konversi ke Tensor PyTorch
        self.X_tensor = torch.tensor(np.array(self.X_data), dtype=torch.float32)
        self.y_tensor = torch.tensor(np.array(self.y_data), dtype=torch.float32).unsqueeze(1)

        print(f":white_check_mark: Data Siap! Total Sampel: {len(self.X_tensor)}")

    def __len__(self):
        return len(self.X_tensor)

    def __getitem__(self, idx):
        return self.X_tensor[idx], self.y_tensor[idx]

# =====================================================================
# UJI COBA PIPA DATA
# =====================================================================
if __name__ == "__main__":
    # Inisiasi Dataset
    dataset = MarketDataset(sequence_length=30)
    
    # DataLoader bertugas menyuapi AI dalam porsi kecil (Batch) agar RAM tidak jebol
    dataloader = DataLoader(dataset, batch_size=64, shuffle=True)
    
    # Ambil 1 porsi (1 batch) untuk dicek
    X_batch, y_batch = next(iter(dataloader))
    
    print("\n:mag: Inspeksi Porsi Data (Batch):")
    print(f"Bentuk X (Fitur) : {X_batch.shape} -> [Batch, Sequence, Indikator]")
    print(f"Bentuk y (Target): {y_batch.shape} -> [Batch, Jawaban]")