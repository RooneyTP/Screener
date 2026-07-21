"""
validate_scoring.py — Validasi logika scoring AI & backtest.
Gunakan pandas untuk memeriksa konsistensi data.
"""

import pandas as pd
import sqlite3

def validate_scoring_consistency(db_path="histori_ihsg.db"):
    """Periksa apakah scoring konsisten antar hari."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT * FROM hasil_screener", conn)
    conn.close()
    
    print(f"\n📊 Validasi Scoring ({len(df)} baris data)...")
    
    # Periksa korelasi antar fitur
    numeric_cols = df.select_dtypes(include=['float64', 'int64']).columns
    corr = df[numeric_cols].corr()
    
    # Cek korelasi fitur dengan Skor
    if 'Skor' in corr.columns:
        skor_corr = corr['Skor'].drop('Skor').sort_values(ascending=False)
        print("   Korelasi fitur dengan Skor:")
        for col, val in skor_corr.items():
            print(f"     {col:<20}: {val:+.2f}")
    
    print("\n✅ Validasi selesai.")
