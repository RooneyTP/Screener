"""
telegram_formatter.py — Format output V7 untuk Telegram readable
=================================================================
Mengganti semua print() di v7_scan.py dengan format pesan yang ringkas.
Gunakan Markdown sederhana, batasi ~3500 karakter.
"""
from datetime import datetime
from typing import List, Dict, Optional


def _fmt_price(val) -> str:
    """Format harga ke Rupiah dengan separator."""
    try:
        return f"Rp{int(val):,}"
    except (ValueError, TypeError):
        return f"Rp{int(0):,}"


def _fmt_brokers_short(brokers_raw: str) -> str:
    """Ringkas broker string jadi 🔵 / 🔴 per broker."""
    if not brokers_raw or "🔵" not in brokers_raw:
        return ""
    parts = brokers_raw.split("|")
    buys, sells = "", ""
    for p in parts:
        if "🔵" in p:
            # Extract like "🔵BK(+45B) JP(+12B)"
            buys = p.replace("🔵", "").strip()
        elif "🔴" in p:
            sells = p.replace("🔴", "").strip()
    result = ""
    if buys:
        # Only show top 2 to keep short
        items = buys.split()[:2]
        result += "🔵 " + " ".join(items)
    if sells:
        items = sells.split()[:2]
        result += " 🔴 " + " ".join(items)
    return result


def format_message(
    swing_list: List[dict],
    intra_list: List[dict],
    market_sentiment: Optional[dict] = None,
    capital: float = 20_000_000,
    summary: Optional[dict] = None,
) -> str:
    """
    Format pesan Telegram lengkap untuk V7 screener.

    Parameters
    ----------
    swing_list : list[dict] — daftar sinyal swing (sorted by score desc)
        Setiap dict: tkr, score, price, exit (dict with stop_loss, take_profit, rrr),
        sizing (dict with lots, cost), bf, ff, weekly, brokers, entry_rec (dict with method, price_range, condition)
    intra_list : list[dict] — daftar sinyal intraday
        Setiap dict: tkr, score, price, exit, sizing, bf, ff, vol, entry_rec
    market_sentiment : dict, optional — dari predict_market_sentiment()
    capital : float — modal total
    summary : dict, optional — ringkasan tambahan

    Returns
    -------
    str — pesan siap kirim (Markdown)
    """
    now = datetime.now().strftime("%d/%m %H:%M")
    lines = []

    # ── HEADER ──
    lines.append(f"📊 SCREENER V7 — {now} WIB")
    lines.append("─" * 25)

    # ── MARKET SENTIMENT ──
    if market_sentiment:
        reason = market_sentiment.get("reason", "")
        lines.append(f"🔮 BESOK: {reason}")
        # 1-line simplification from details
        det = market_sentiment.get("details", [])
        if det:
            # Pick 2-3 most important factors
            key_factors = [d for d in det if "merah" in d or "jual" in d or "beli" in d or "ADX" in d or "tur" in d]
            if not key_factors:
                key_factors = det[:2]
            lines.append(", ".join(key_factors[:3]))
        # Entry advice based on sentiment
        sent = market_sentiment.get("sentiment", "YELLOW")
        if sent == "RED":
            lines.append("→ Limit harga diskon, jangan kejar")
        elif sent == "GREEN":
            lines.append("→ Bisa open entry, trailing longgar")
        else:
            lines.append("→ Harga limit, jangan kejar")
        lines.append("")

    # ── SWING ──
    if swing_list:
        lines.append("🏆 SWING (urut skor)")
        for i, s in enumerate(swing_list[:5]):  # max 5
            tkr = s["tkr"]
            score = s["score"]
            price = s["price"]
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)
            rrr = exit_d.get("rrr", 0)
            entry_rec = s.get("entry_rec", {})

            # Entry method
            entry_method = entry_rec.get("method", "")
            entry_range = entry_rec.get("price_range", "")

            # Line 1: ticker, score, price, entry timing
            entry_info = f"🎯 {entry_method}"
            if entry_range and entry_range != "-":
                entry_info += f" {entry_range}"

            wk = " ⚠️ BEAR" if s.get("weekly") == "BEARISH" else ""
            lines.append(f"{i+1}. {tkr} {score:.1f} | {_fmt_price(price)} | {entry_info}{wk}")

            # Line 2: SL / TP / RRR
            lines.append(f"   SL {_fmt_price(sl)} | TP {_fmt_price(tp)} | RRR {rrr}")

            # Line 3: broker flow (compact) — only if interesting
            brokers = _fmt_brokers_short(s.get("brokers", ""))
            if brokers:
                lines.append(f"   {brokers}")
            elif s.get("bf") and s["bf"] not in ("netral", "no_data"):
                # Show broker detail briefly
                bf_short = s["bf"].replace("akumulasi_masif_", "🔵 ").replace("akumulasi_", "🔵 ").replace("distribusi_", "🔴 ").replace("_", " ")
                if len(bf_short) > 20:
                    bf_short = bf_short[:20]
                lines.append(f"   {bf_short}")

        lines.append("")

    # ── INTRADAY ──
    if intra_list:
        lines.append("⚡ INTRADAY (H+3)")
        for s in intra_list[:5]:  # max 5
            tkr = s["tkr"]
            score = s["score"]
            price = s["price"]
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)
            vol = s.get("vol", 1)
            entry_rec = s.get("entry_rec", {})

            entry_method = entry_rec.get("method", "")
            entry_range = entry_rec.get("price_range", "")

            # Entry timing on same line
            entry_info = f"🎯 {entry_method}"
            if entry_range and entry_range != "-":
                entry_info += f" {entry_range}"

            lines.append(f"{tkr} {score:.1f} | {_fmt_price(price)} | Vol {vol:.1f}x")
            lines.append(f"   SL {_fmt_price(sl)} | TP {_fmt_price(tp)} | {entry_info}")
        lines.append("")

    # ── SUMMARY ──
    lines.append("─" * 25)
    lines.append("📋 RINGKASAN")

    n_swing = len(swing_list)
    n_intra = len(intra_list)

    # Hitung alokasi (top 3 masing-masing)
    alloc = 0
    total_risk = 0
    for s in swing_list[:3]:
        cost = s.get("sizing", {}).get("cost", 0)
        alloc += cost
        total_risk += s.get("sizing", {}).get("risk_amount", int(cost * 0.05))
    for s in intra_list[:3]:
        cost = s.get("sizing", {}).get("cost", 0)
        alloc += cost
        total_risk += s.get("sizing", {}).get("risk_amount", int(cost * 0.05))

    lines.append(f"Swing {n_swing} | Intra {n_intra} | Alokasi {_fmt_price(alloc)}")
    lines.append(f"Modal {_fmt_price(capital)} | Risiko {_fmt_price(total_risk)} ({(total_risk/capital*100) if capital>0 else 0:.0f}%)")

    # ── EXIT STRATEGY ──
    lines.append("")
    lines.append("🛡 EXIT: SL jika tutup <SL | Trailing aktif +3%")
    lines.append("⚠️ Data Invezgo | Keputusan trader")

    result = "\n".join(lines)

    # Truncate to 3500 chars (Telegram-friendly)
    if len(result) > 3500:
        result = result[:3490] + "\n…(truncated)"

    return result
