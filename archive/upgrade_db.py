import sqlite3
import pandas as pd

DB = "histori_ihsg.db"

def upgrade_schema():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    
    # Cek existing columns in histori_ihsg
    c.execute("PRAGMA table_info(histori_ihsg)")
    cols = {row[1] for row in c.fetchall()}
    
    new_cols = {
        "open": "REAL DEFAULT 0",
        "high": "REAL DEFAULT 0",
        "low": "REAL DEFAULT 0",
        "volume": "REAL DEFAULT 0",
        "change_pct": "REAL DEFAULT 0",
        "sma_20": "REAL DEFAULT 0",
        "sma_50": "REAL DEFAULT 0",
        "bid": "REAL DEFAULT 0",
        "ask": "REAL DEFAULT 0",
    }
    
    for col_name, col_type in new_cols.items():
        if col_name not in cols:
            try:
                c.execute(f"ALTER TABLE histori_ihsg ADD COLUMN {col_name} {col_type}")
                print(f"Added column: {col_name}")
            except Exception as e:
                print(f"Failed to add {col_name}: {e}")
    
    conn.commit()
    conn.close()

if __name__ == "__main__":
    upgrade_schema()
