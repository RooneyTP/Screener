#!/usr/bin/env python
# mini_broker.py = broker simulator (buat testing)

import sqlite3
import datetime

DB = "portofolio_virtual.db"

def init():
    with sqlite3.connect(DB) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                side TEXT, ticker TEXT, price REAL, shares INTEGER, 
                status TEXT DEFAULT 'PENDING', dt TEXT,
                pnl REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS positions (
                ticker TEXT PRIMARY KEY, shares INTEGER, entry_price REAL,
                entry_dt TEXT
            );
        """)

def buy(ticker, price, shares):
    with sqlite3.connect(DB) as c:
        # check cash...
        c.execute("INSERT INTO orders (side,ticker,price,shares,status,dt) VALUES (?,?,?,?,?,?)",
                 ("BUY", ticker, price, shares, "EXECUTED", str(datetime.datetime.now())))
        c.execute("INSERT OR REPLACE INTO positions (ticker,shares,entry_price,entry_dt) VALUES (?, COALESCE((SELECT shares FROM positions WHERE ticker=?),0)+?,?,?)",
                 (ticker, ticker, shares, price, str(datetime.datetime.now())))

def sell(ticker, price, shares):
    with sqlite3.connect(DB) as c:
        c.execute("INSERT INTO orders (side,ticker,price,shares,status,dt) VALUES (?,?,?,?,?,?)",
                 ("SELL", ticker, price, shares, "EXECUTED", str(datetime.datetime.now())))
        c.execute("UPDATE positions SET shares=shares-? WHERE ticker=?", (shares, ticker))
        c.execute("DELETE FROM positions WHERE shares<=0")

if __name__ == "__main__":
    init()
    print("mini_broker ready")
