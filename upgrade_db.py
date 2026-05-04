import sqlite3

print("🛠️ Memperluas kapasitas Database...")
conn = sqlite3.connect("histori_ihsg.db")
cursor = conn.cursor()

# Membuat ruangan untuk sensor Makro dan Masa Lalu
kolom_baru = ["IHSG_Change", "USD_Change", "RSI_1d", "MACD_1d"]

for kolom in kolom_baru:
    try:
        cursor.execute(f"ALTER TABLE hasil_screener ADD COLUMN {kolom} REAL DEFAULT 0.0")
        print(f"  ✓ Kolom {kolom} berhasil ditambahkan.")
    except:
        print(f"  - Kolom {kolom} sudah ada.")

conn.commit()
conn.close()
print("✅ Database siap menerima data baru!")