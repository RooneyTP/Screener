import sqlite3
import json

DB_FILE = "broker_lokal.db"

def init_broker():
    """Membuat tabel antrean pesan jika belum ada"""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS antrean (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topik TEXT,
            pesan TEXT,
            status TEXT DEFAULT 'UNREAD'
        )
    """)
    conn.commit()
    conn.close()

def kirim_pesan(topik, pesan_dict):
    """Producer melempar pesan ke antrean"""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    pesan_str = json.dumps(pesan_dict)
    conn.execute("INSERT INTO antrean (topik, pesan) VALUES (?, ?)", (topik, pesan_str))
    conn.commit()
    conn.close()

def baca_pesan(topik):
    """Consumer membaca 1 pesan tertua yang belum dibaca"""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    cur = conn.cursor()
    cur.execute("SELECT id, pesan FROM antrean WHERE topik=? AND status='UNREAD' ORDER BY id ASC LIMIT 1", (topik,))
    row = cur.fetchone()
    
    if row:
        msg_id, pesan_str = row
        # Tandai pesan sudah dibaca agar tidak ditelan 2 kali
        conn.execute("UPDATE antrean SET status='READ' WHERE id=?", (msg_id,))
        conn.commit()
        conn.close()
        return json.loads(pesan_str)
        
    conn.close()
    return None