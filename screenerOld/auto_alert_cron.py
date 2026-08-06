#!/usr/bin/env python3
"""
Auto-Alert Cron — IDX Screener Daily Report (no_agent mode)
=============================================================
Reads the latest screener CSV, detects anomalies, and outputs
a formatted Telegram report to stdout.

Designed for Hermes cron with no_agent=True — stdout is delivered
verbatim to the user.
"""

import sys
import os
import glob
from datetime import datetime

MCP_DIR = r"C:\Hermes_Workspace\mcp_servers"
SCREENER_DIR = r"C:\Hermes_Workspace\Screener\idx_alpha_screener"
CSV_PATH = os.path.join(SCREENER_DIR, "screener_v2_result.csv")

now_str = datetime.now().strftime("%Y-%m-%d %H:%M WIB")


def get_anomalies() -> str:
    """Run anomaly detection via stock_mcp functions."""
    sys.path.insert(0, MCP_DIR)
    import importlib
    stock_mcp = importlib.import_module("stock_mcp")
    importlib.reload(stock_mcp)

    import pandas as pd
    import numpy as np
    from stock_mcp import ANOMALY_THRESHOLDS, _read_screener_csv

    try:
        df = _read_screener_csv()
    except FileNotFoundError:
        return "❌ File CSV tidak ditemukan. Jalankan screener dulu."

    # Convert numeric
    df["vol_ratio"] = pd.to_numeric(df["vol_ratio"], errors="coerce")
    df["rsi"] = pd.to_numeric(df["rsi"], errors="coerce")
    df["stoch_k"] = pd.to_numeric(df["stoch_k"], errors="coerce")
    df["pct_vs_vwap"] = pd.to_numeric(df["pct_vs_vwap"], errors="coerce")
    df["ret_20d"] = pd.to_numeric(df["ret_20d"], errors="coerce")

    T = ANOMALY_THRESHOLDS

    vol_spike = df[df["vol_ratio"] > T["volume_spike"]]
    oversold = df[df["rsi"] < T["oversold_extreme"]]
    overbought = df[df["rsi"] > T["overbought_extreme"]]
    vwap_dev = df[df["pct_vs_vwap"].abs() > T["vwap_deviation"]]
    vol_dry = df[df["vol_ratio"] < T["volume_dry"]]
    stoch_ext = df[(df["stoch_k"] < T["stoch_oversold"]) | (df["stoch_k"] > T["stoch_overbought"])]

    # Build per-ticker anomaly labels
    ticker_labels = {}
    def tag(tkr, cat, detail):
        ticker_labels.setdefault(str(tkr), []).append((cat, detail))

    for _, r in vol_spike.iterrows(): tag(r["ticker"], "Vol Spike", f"{r['vol_ratio']:.1f}x")
    for _, r in oversold.iterrows(): tag(r["ticker"], "Oversold", f"RSI {r['rsi']:.1f}")
    for _, r in overbought.iterrows(): tag(r["ticker"], "Overbought", f"RSI {r['rsi']:.1f}")
    for _, r in vwap_dev.iterrows(): tag(r["ticker"], "VWAP", f"{r['pct_vs_vwap']:+.1f}%")
    for _, r in vol_dry.iterrows(): tag(r["ticker"], "Vol Dry", f"{r['vol_ratio']:.1f}x")
    for _, r in stoch_ext.iterrows(): tag(r["ticker"], "Stochastic", f"StochK {r['stoch_k']:.1f}")

    lines = [
        "🚀 *Auto-Alert — IDX Screener*",
        f"📅 {now_str}",
        f"📊 {len(df)} saham discan",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "📊 *Ringkasan Anomali*",
        f"• Volume Spike (>2.0x)  : {len(vol_spike)} saham",
        f"• Oversold (RSI<25)     : {len(oversold)} saham",
        f"• Overbought (RSI>75)   : {len(overbought)} saham",
        f"• VWAP Deviasi (>±8%)   : {len(vwap_dev)} saham",
        f"• Volume Dry (<0.3x)    : {len(vol_dry)} saham",
        f"• Stoch Extreme          : {len(stoch_ext)} saham",
        "",
    ]

    # Multi-anomaly
    multi = {t: v for t, v in ticker_labels.items() if len(v) >= 2}
    if multi:
        lines.append("🔥 *Multi-Anomali (2+ sinyal)*")
        for tkr in sorted(multi):
            entries = multi[tkr]
            details = " ".join(e[1] for e in entries)
            cats = "+".join(e[0] for e in entries)
            match = df[df["ticker"] == tkr]
            sig = ""
            if not match.empty:
                s = str(match.iloc[0].get("signal", "")).strip().upper()
                if s in ("BUY", "SELL", "HOLD", "PICK"):
                    sig = f" {s}"
            lines.append(f"• {tkr:<6} {details:<30} → {cats}{sig}")
        lines.append("")

    # Top BUY picks
    if "signal" in df.columns:
        buys = df[df["signal"].isin(["BUY", "STRONG_BUY", "PICK"])].sort_values("score", ascending=False)
        if not buys.empty:
            lines.append("🏆 *Top BUY Signals*")
            for _, r in buys.head(5).iterrows():
                score = r.get("score", "?")
                lines.append(f"• {r['ticker']:<6} Score {score:>5}  RSI {r.get('rsi', '?'):>5}  {r.get('sector', '')[:12]}")
            lines.append("")

    # Oversold detail
    if not oversold.empty:
        lines.append("🆘 *Oversold (RSI<25)*")
        for _, r in oversold.head(10).iterrows():
            lines.append(f"• {r['ticker']:<6} RSI {r['rsi']:.1f}  StochK {r['stoch_k']:.1f}")
        lines.append("")

    return "\n".join(lines)


def main():
    # Check CSV exists
    if not os.path.exists(CSV_PATH):
        print(f"❌ *Auto-Alert Error*\nCSV tidak ditemukan: {CSV_PATH}\nJalankan screener dulu.")
        sys.exit(1)

    csv_age = datetime.now().timestamp() - os.path.getmtime(CSV_PATH)
    csv_age_hours = csv_age / 3600

    report = get_anomalies()

    # Add age warning if data is old
    if csv_age_hours > 48:
        report += f"\n\n⚠️ *Data {csv_age_hours:.0f} jam yang lalu* — mungkin sudah tidak relevan."

    print(report)


if __name__ == "__main__":
    main()
