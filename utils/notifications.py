"""
notifications.py — Alert & Notification Functions for Screener
===============================================================
Extracted from screener.py during dekonstruksi phase.
Handles email alerts, Discord webhooks, and virtual portfolio updates.
"""

import os
import logging
import datetime
import sqlite3

import pandas as pd

from utils.helpers import C

logger = logging.getLogger("notifications")


# ═══════════════════════════════════════════════════════════════════════════
# EMAIL ALERTS
# ═══════════════════════════════════════════════════════════════════════════

def send_email_alert(subject: str, body: str, to_email: str, from_email: str = "screener@alert.com"):
    """Send email alert via SMTP."""
    ALERTS_AVAILABLE = False
    try:
        import smtplib
        from email.mime.text import MIMEText
        ALERTS_AVAILABLE = True
    except ImportError:
        pass

    if not ALERTS_AVAILABLE:
        print(f"Email alert: {subject} - {body}")
        return

    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = from_email
        msg['To'] = to_email

        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        print("Email alert sent successfully")
    except Exception as e:
        print(f"Failed to send email: {e}")


def check_and_alert(df: pd.DataFrame, email: str = None):
    """Check for high-confidence MM signals and send alert."""
    high_conf_signals = df[
        ((df["MM_Activity"] == "ACCUMULATION") & (df["MM_Confidence"] >= 80)) |
        ((df["MM_Activity"] == "DISTRIBUTION") & (df["MM_Confidence"] >= 80))
    ]

    if not high_conf_signals.empty:
        alert_body = "High-confidence Market Maker signals detected:\n\n"
        for _, row in high_conf_signals.iterrows():
            alert_body += f"{row['Ticker']}: {row['MM_Activity']} ({row['MM_Confidence']}%) - Dominance: {row['Dominance']}\n"

        if email:
            send_email_alert("Market Maker Alert", alert_body, email)
        else:
            print(f"{C.BOLD}{C.YELLOW}ALERT: {alert_body}{C.RESET}")


# ═══════════════════════════════════════════════════════════════════════════
# DISCORD NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════

def kirim_notifikasi_discord(df: pd.DataFrame, webhook_url: str):
    """Mengirim hasil screener ke Discord menggunakan Webhook dengan format Embed."""
    import datetime

    if not webhook_url or not webhook_url.startswith("http"):
        print("  [Discord] URL Webhook kosong. Notifikasi Discord dilewati.")
        return

    df_alert = df[df["Sinyal"].isin(["ULTRA_BUY", "STRONG_BUY", "BUY"])]

    if df_alert.empty:
        print("  [Discord] Tidak ada sinyal kuat hari ini, notifikasi dilewati.")
        return

    import requests
    embeds = []
    for _, row in df_alert.iterrows():
        warna = 0x00FF00 if row["Sinyal"] == "ULTRA_BUY" else 0x00AA00

        ai_info = f"\U0001f916 {row.get('AI_Win_Prob%', 0)}% Win Rate\n{row.get('AI_Verdict', 'N/A')}" if row.get('AI_Win_Prob%', 0) > 0 else "Tidak ada Prediksi AI"

        # Estimasi Waktu
        if row['Regime'] == "HIGH_VOLATILITY":
            estimasi_waktu = "\u23f3 **1 - 3 Hari Bursa** (Fast Trade / Volatil)"
        elif row['Regime'] == "TRENDING":
            estimasi_waktu = "\u23f3 **3 - 5 Hari Bursa** (Swing Trend Stable)"
        else:
            estimasi_waktu = "\u23f3 **5 - 10 Hari Bursa** (Swing Range / Sabar)"

        embed = {
            "title": f"\U0001f6a8 {row['Sinyal'].replace('_', ' ')}: {row['Ticker']}",
            "color": warna,
            "fields": [
                {"name": "Harga Entry", "value": f"Rp {row['Harga']:,}", "inline": True},
                {"name": "Target (TP)", "value": f"Rp {row['Target_1']:,}", "inline": True},
                {"name": "Stop Loss", "value": f"Rp {row['Stop_Loss']:,}", "inline": True},
                {"name": "Skor Teknikal", "value": f"\u2b50 {row['Skor']}/15\n\u2696\ufe0f RRR: {row['RRR']}", "inline": True},
                {"name": "Prediksi AI", "value": ai_info, "inline": True},
                {"name": "Perkiraan Waktu Hold", "value": estimasi_waktu, "inline": False},
                {"name": "Market Maker", "value": f"\U0001f40b {row['MM_Activity']} ({row['MM_Confidence']}%)", "inline": True},
                {"name": "Foreign Flow (Asing)", "value": f"\U0001f30d {row.get('Foreign_Status', 'N/A')}", "inline": True},
            ],
            "footer": {"text": f"IHSG Quant Screener v7.0 \u2022 {datetime.datetime.now().strftime('%d %b %Y')}"}
        }
        embeds.append(embed)

        if len(embeds) == 10:
            requests.post(webhook_url, json={"content": "\U0001f4c8 **UPDATE SAHAM POTENSIAL HARI INI**", "embeds": embeds})
            embeds = []

    if embeds:
        response = requests.post(webhook_url, json={"content": "\U0001f4c8 **UPDATE SAHAM POTENSIAL HARI INI**", "embeds": embeds})
        if response.status_code in (204, 200):
            print(f"  {C.GREEN}\u2713 Notifikasi & Estimasi Waktu berhasil dikirim ke Discord Automaton!{C.RESET}")
        else:
            print(f"  {C.RED}\u2717 Gagal mengirim ke Discord: HTTP {response.status_code}{C.RESET}")


# ═══════════════════════════════════════════════════════════════════════════
# VIRTUAL PORTFOLIO
# ═══════════════════════════════════════════════════════════════════════════

def update_virtual_portfolio(df: pd.DataFrame):
    """Virtual Hedge Fund Manager (Paper Trading)"""
    db_name = "portofolio_virtual.db"
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    cursor.execute('''CREATE TABLE IF NOT EXISTS akun (saldo_cash REAL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS posisi (ticker TEXT, harga_beli REAL, sl REAL, tp REAL, shares INTEGER, tanggal TEXT)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS histori (ticker TEXT, pnl REAL, status TEXT, tanggal TEXT)''')

    cursor.execute("SELECT saldo_cash FROM akun")
    row = cursor.fetchone()
    if not row:
        saldo = 100000000.0
        cursor.execute("INSERT INTO akun (saldo_cash) VALUES (?)", (saldo,))
    else:
        saldo = row[0]

    print(f"\n{C.BOLD}{C.BLUE}{'-'*80}")
    print(f"  \U0001f4bc VIRTUAL HEDGE FUND MANAGER (Modal: Rp 100.000.000)")
    print(f"{'-'*80}{C.RESET}")

    cursor.execute("SELECT ticker, harga_beli, sl, tp, shares FROM posisi")
    posisi_open = cursor.fetchall()
    total_value_saham = 0

    if not posisi_open:
        print("  \U0001f4ed Portofolio saat ini KOSONG (Belum ada saham yang di-hold).")

    for pos in posisi_open:
        tkr, h_beli, sl, tp, shares = pos
        data_saham = df[df['Ticker'] == tkr]

        if not data_saham.empty:
            harga_skrg = float(data_saham.iloc[0]['Harga'])
            total_value_saham += (harga_skrg * shares)

            if harga_skrg <= sl:
                pnl = (harga_skrg - h_beli) * shares
                saldo += (harga_skrg * shares)
                cursor.execute("DELETE FROM posisi WHERE ticker = ?", (tkr,))
                cursor.execute("INSERT INTO histori VALUES (?, ?, ?, ?)", (tkr, pnl, "HIT STOP LOSS", str(datetime.date.today())))
                print(f"  \U0001f534 {tkr} CUTLOSS! Terjual di SL (Rp {harga_skrg:,}). PnL: Rp {pnl:,.0f}")
            elif harga_skrg >= tp:
                pnl = (harga_skrg - h_beli) * shares
                saldo += (harga_skrg * shares)
                cursor.execute("DELETE FROM posisi WHERE ticker = ?", (tkr,))
                cursor.execute("INSERT INTO histori VALUES (?, ?, ?, ?)", (tkr, pnl, "HIT TAKE PROFIT", str(datetime.date.today())))
                print(f"  \U0001f7e2 {tkr} PROFIT! Terjual di TP (Rp {harga_skrg:,}). PnL: +Rp {pnl:,.0f}")
            else:
                unrealized = (harga_skrg - h_beli) * shares
                warna_un = C.GREEN if unrealized > 0 else C.RED
                print(f"  \U0001f6e1\ufe0f HOLD {tkr:<6} | Floating: {warna_un}Rp {unrealized:,.0f}{C.RESET} (Beli: Rp {h_beli:,.0f} -> Skrg: Rp {harga_skrg:,.0f})")
        else:
            total_value_saham += (h_beli * shares)

    kandidat_beli = df[(df['Sinyal'] == 'ULTRA_BUY') & (df['AI_Win_Prob%'] >= 70)]

    for _, row_saham in kandidat_beli.iterrows():
        tkr = row_saham['Ticker']
        cursor.execute("SELECT * FROM posisi WHERE ticker = ?", (tkr,))
        if cursor.fetchone():
            continue

        harga = float(row_saham['Harga'])
        max_alokasi = saldo * 0.20
        shares_to_buy = min(int(row_saham['Position_Shares']), int(max_alokasi / harga))

        if shares_to_buy > 100 and saldo >= (shares_to_buy * harga):
            biaya = shares_to_buy * harga
            saldo -= biaya
            sl = float(row_saham['Stop_Loss'])
            tp = float(row_saham['Target_1'])

            cursor.execute("INSERT INTO posisi VALUES (?, ?, ?, ?, ?, ?)",
                           (tkr, harga, sl, tp, shares_to_buy, str(datetime.date.today())))
            print(f"  \U0001f6d2 BOT MEMBELI {tkr}: {shares_to_buy:,} shares @ Rp {harga:,.0f} (Total: Rp {biaya:,.0f})")

    cursor.execute("UPDATE akun SET saldo_cash = ?", (saldo,))
    conn.commit()
    conn.close()

    total_equity = saldo + total_value_saham
    roi = ((total_equity - 100000000) / 100000000) * 100
    warna_roi = C.GREEN if roi >= 0 else C.RED

    print(f"\n  \U0001f4b5 Cash Tersisa : Rp {saldo:,.0f}")
    print(f"  \U0001f4c8 Total Equity : Rp {total_equity:,.0f}")
    print(f"  \U0001f4ca Total ROI    : {warna_roi}{roi:.2f}%{C.RESET}")
