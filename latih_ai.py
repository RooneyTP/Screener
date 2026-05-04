"""
============================================================
  AI TRAINING MODULE (Versi 3.0 - EVOLUSI XGBOOST)
  Membaca database, memvalidasi dengan harga masa depan nyata,
  dan melatih AI menggunakan algoritma XGBoost + Hyperparameter Tuning
============================================================
"""

import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
import xgboost as xgb
from sklearn.model_selection import train_test_split, RandomizedSearchCV
from sklearn.metrics import accuracy_score, classification_report
import joblib
import warnings
import datetime
import time

warnings.filterwarnings("ignore")

print("🧠 Membangunkan Sistem AI (Evolusi XGBoost: Level 3)...\n")

# ==============================================================================
# 1. BUKA DATABASE & AMBIL DATA
# ==============================================================================
try:
    conn = sqlite3.connect("histori_ihsg.db")
    query = "SELECT * FROM hasil_screener WHERE Sinyal != 'HINDARI'"
    df = pd.read_sql(query, conn)
    conn.close()
    
    if df.empty:
        print("❌ Database kosong atau tidak ada sinyal valid.")
        exit()
except Exception as e:
    print(f"❌ Gagal membaca database: {e}")
    exit()

df['Tanggal'] = pd.to_datetime(df['Tanggal'])

# ==============================================================================
# 2. MENGUNDUH "KUNCI JAWABAN" DARI MASA DEPAN (YAHOO FINANCE)
# ==============================================================================
print("📥 Mengunduh data harga masa depan dari bursa (Harap tunggu)...")
tickers_unik = df['Ticker'].unique()
tanggal_mulai = df['Tanggal'].min().strftime('%Y-%m-%d')
tanggal_akhir = (datetime.date.today() + datetime.timedelta(days=1)).strftime('%Y-%m-%d')

harga_historis = {}
total_tkr = len(tickers_unik)

for i, ticker in enumerate(tickers_unik, 1):
    print(f"   [{i:02d}/{total_tkr}] Mengambil sejarah nyata {ticker}...", end="\r")
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

print("\n✓ Kunci Jawaban Realita berhasil diamankan!\n")

# ==============================================================================
# 3. LABELING: CUAN NYATA vs BONCOS
# ==============================================================================
# ==============================================================================
# 3. LABELING: CUAN NYATA vs BONCOS (MODE REALITA KEJAM)
# ==============================================================================
print("⚙️ Mengoreksi hasil masa lalu (Menerapkan Pajak, Fee Broker & Slippage)...")
labels = []

# Asumsi hambatan: Fee Beli (0.15%) + Fee Jual (0.25%) + Slippage/Beda Harga (0.2%)
HAMBATAN_PASAR = 0.006 

for index, row in df.iterrows():
    ticker = row['Ticker']
    tanggal_screener = row['Tanggal']
    target_asli = row['Target_1']
    stop_loss_asli = row['Stop_Loss']

    if ticker not in harga_historis or harga_historis[ticker].empty:
        labels.append(np.nan)
        continue

    df_harga = harga_historis[ticker]
    masa_depan = df_harga.loc[df_harga.index > tanggal_screener].head(10)

    if masa_depan.empty:
        labels.append(np.nan)
        continue

    status = 0 # Default: Boncos
    
    # AI diajari bahwa Target Profit harus menutupi BIAYA FEE BROKER
    target_realistis = target_asli * (1 + HAMBATAN_PASAR) 
    
    # AI diajari bahwa Stop Loss bisa tertembus lebih cepat karena Slippage
    stop_loss_realistis = stop_loss_asli * (1 + (HAMBATAN_PASAR / 2))

    for _, harga_harian in masa_depan.iterrows():
        if harga_harian['High'] >= target_realistis:
            status = 1 # CUAN BERSIH! (Sudah dipotong fee)
            break
        elif harga_harian['Low'] <= stop_loss_realistis:
            status = 0 # BONCOS / KENA SL!
            break
            
    labels.append(status)

df['Target_Menang'] = labels
df = df.dropna(subset=['Target_Menang'])
df['Target_Menang'] = df['Target_Menang'].astype(int)

# ==============================================================================
# 4. DATA ENGINEERING & HYPERPARAMETER TUNING (XGBOOST)
# ==============================================================================
print(f"📊 Total Data Valid: {len(df)} kasus.")

# Features yang dipelajari AI
features_columns = [
    "Skor", "Confidence%", "RSI", "ADX", "Stoch", "CCI", 
    "BB_Width%", "RRR", "MM_Confidence", "MM_vs_Retail_Ratio",
    "IHSG_Change", "USD_Change", "RSI_1d", "MACD_1d"  # <-- Pastikan 4 ini ada
]

df = df.dropna(subset=features_columns)
X = df[features_columns]
y = df['Target_Menang']

if len(y.unique()) < 2:
    print("❌ Data terlalu homogen. AI butuh melihat kasus menang DAN kalah untuk belajar.")
    exit()

X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

print(f"\n🔬 Memulai Evolusi AI (Hyperparameter Tuning)...")
print("   (AI sedang mencoba puluhan kombinasi otak untuk mencari yang terkuat. Mohon tunggu)")

# Konfigurasi dasar XGBoost
xgb_model = xgb.XGBClassifier(
    objective='binary:logistic',
    eval_metric='logloss',
    random_state=42,
    scale_pos_weight=len(y_train[y_train == 0]) / max(1, len(y_train[y_train == 1])) # Menyeimbangkan data
)

# Ruang Pencarian Kombinasi (Grid)
param_dist = {
    'max_depth': [3, 4, 5, 6, 8],               # Kedalaman berpikir
    'learning_rate': [0.01, 0.05, 0.1, 0.2],    # Kecepatan belajar
    'n_estimators': [50, 100, 200, 300],        # Jumlah "pohon" keputusan
    'subsample': [0.6, 0.8, 1.0],               # Persentase sampel untuk mencegah hafalan
    'colsample_bytree': [0.6, 0.8, 1.0],        # Persentase indikator yang diacak
    'gamma': [0, 0.1, 0.2, 0.5]                 # Hukuman untuk pembuatan cabang tak berguna
}

# Menyuruh AI melakukan Turnamen Pencarian (Tuning) selama 20 ronde
random_search = RandomizedSearchCV(
    xgb_model, 
    param_distributions=param_dist, 
    n_iter=20,          # Coba 20 kombinasi acak
    scoring='accuracy', 
    cv=3,               # Uji silang 3 kali lipat
    verbose=0, 
    n_jobs=-1,          # Gunakan seluruh core CPU laptop
    random_state=42
)

# PROSES BELAJAR INTI
random_search.fit(X_train, y_train)

# Mengambil Otak Terbaik dari Turnamen
best_ai = random_search.best_estimator_
print(f"✨ Tuning Selesai! Pengaturan terbaik yang ditemukan:")
print(f"   Kedalaman (max_depth): {best_ai.max_depth}")
print(f"   Pohon (n_estimators) : {best_ai.n_estimators}")
print(f"   Rate Belajar (lr)    : {best_ai.learning_rate}")

# ==============================================================================
# 5. EVALUASI HASIL UJIAN (XGBOOST)
# ==============================================================================
y_pred = best_ai.predict(X_test)
akurasi = accuracy_score(y_test, y_pred) * 100

print(f"\n📈 HASIL UJIAN XGBOOST (PERFORMA DUNIA NYATA):")
print(f"   -> Akurasi Prediksi: {akurasi:.2f}%")
print("\n📝 Rapor Detail:")
print(classification_report(y_test, y_pred, target_names=["Boncos/Jebakan (0)", "Cuan Nyata (1)"]))

# XGBoost punya fitur importance bawaan
importances = best_ai.feature_importances_
print("\n🏆 INDIKATOR YANG PALING MENGGERAKKAN HARGA MENURUT XGBOOST:")
feature_imp = pd.DataFrame({'Indikator': features_columns, 'Pentingnya': importances})
feature_imp = feature_imp.sort_values(by='Pentingnya', ascending=False)
for idx, row in feature_imp.head(4).iterrows():
    print(f"   {row['Indikator']:<20}: {row['Pentingnya']*100:.1f}%")

# ==============================================================================
# 6. MENYIMPAN OTAK AI TERBARU
# ==============================================================================
nama_file_otak = "otak_screener_ai.pkl"
joblib.dump(best_ai, nama_file_otak)
print(f"\n💾 SUKSES! Otak XGBoost (Level 3) telah menimpa '{nama_file_otak}'")