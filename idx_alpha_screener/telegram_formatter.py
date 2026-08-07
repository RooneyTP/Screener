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
    # L4: kalau hanya ada seller (🔴), info jual tetap ditampilkan —
    # sebelumnya return "" saat tanpa 🔵 → info distribusi hilang dari pesan.
    if not brokers_raw or ("🔵" not in brokers_raw and "🔴" not in brokers_raw):
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
        result += (" 🔴 " if buys else "🔴 ") + " ".join(items)
    return result.strip()


def format_message(
    swing_list: List[dict],
    intra_list: List[dict],
    market_sentiment: Optional[dict] = None,
    capital: float = 20_000_000,
    summary: Optional[dict] = None,
    narratives: Optional[Dict[str, str]] = None,
    concentration_warnings: Optional[List[str]] = None,
    extra_parts: Optional[List[str]] = None,
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
    narratives : dict[str, str], optional — {ticker: kalimat naratif} dari
        ai_narrative.generate_narratives(). Default None/kosong = kompatibel
        dengan pemanggil lama; sinyal tanpa narrative tetap diformat seperti biasa.
    concentration_warnings : list[str], optional — baris peringatan C2
        (guard konsentrasi grup konglomerat) dari v7_scan. Default None =
        tidak ada peringatan → format output TIDAK berubah untuk pemanggil lama.
    extra_parts : list[str], optional — bagian tambahan (mis. alert posisi,
        ringkasan sektor) yang diintegrasikan SEBELUM truncate 3500 (M2).
        Dipakai v7_scan supaya output final tidak melebihi 4096 karakter
        Telegram (sebelumnya di-append setelah truncate → pesan ke-drop).

    Returns
    -------
    str — pesan siap kirim (Markdown)
    """
    now = datetime.now().strftime("%d/%m %H:%M")
    lines = []
    narratives = narratives or {}

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
        # IHSG key levels
        kl = market_sentiment.get("key_levels")
        if kl and kl.get("current", 0) > 0:
            lines.append(
                f"📊 IHSG {kl['current']:,.0f} | Support {kl['support']:,.0f} | "
                f"Resist {kl['resistance']:,.0f}"
            )
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
            grp = f" [{s.get('group')}]" if s.get("group") else ""
            cont = s.get("continuation")
            cont_label = f" (lanjutan - sinyal {cont})" if cont else ""
            lines.append(f"{i+1}. {tkr}{grp} {score:.1f} | {_fmt_price(price)} | {entry_info}{wk}{cont_label}")

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

            # Line 3b: earnings momentum (B1) — 1 baris, hanya kalau ada data
            earn = s.get("earn", "")
            if earn and earn not in ("no_data", "error"):
                lines.append(f"   📈 {earn}")

            # Line 4 (optional): AI narrative — konteks tambahan, bukan prediksi
            nar = narratives.get(tkr)
            if nar:
                lines.append(f"   └ 📝 {nar}")

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

            cont = s.get("continuation")
            cont_label = f" (lanjutan - sinyal {cont})" if cont else ""
            lines.append(f"{tkr} {score:.1f} | {_fmt_price(price)} | Vol {vol:.1f}x{cont_label}")
            lines.append(f"   SL {_fmt_price(sl)} | TP {_fmt_price(tp)} | {entry_info}")
            earn = s.get("earn", "")
            if earn and earn not in ("no_data", "error"):
                lines.append(f"   📈 {earn}")
        lines.append("")

    # ── SUMMARY ──
    lines.append("─" * 25)
    lines.append("📋 RINGKASAN")

    n_swing = len(swing_list)
    n_intra = len(intra_list)

    # Hitung alokasi (top 3 deduplicate — same ticker may appear in both)
    alloc = 0
    total_risk = 0
    counted_tickers = set()
    for s in swing_list[:3]:
        tkr = s.get("tkr", "")
        if tkr in counted_tickers:
            continue
        counted_tickers.add(tkr)
        cost = s.get("sizing", {}).get("cost", 0)
        alloc += cost
        total_risk += s.get("sizing", {}).get("risk_amount", int(cost * 0.05))
    for s in intra_list[:3]:
        tkr = s.get("tkr", "")
        if tkr in counted_tickers:
            continue
        counted_tickers.add(tkr)
        cost = s.get("sizing", {}).get("cost", 0)
        alloc += cost
        total_risk += s.get("sizing", {}).get("risk_amount", int(cost * 0.05))

    lines.append(f"Swing {n_swing} | Intra {n_intra} | Alokasi {_fmt_price(alloc)}")
    lines.append(f"Modal {_fmt_price(capital)} | Risiko {_fmt_price(total_risk)} ({(total_risk/capital*100) if capital>0 else 0:.0f}%)")

    # ── C2: peringatan konsentrasi grup (opsional — tidak muncul saat normal) ──
    if concentration_warnings:
        lines.append("")
        lines.extend(concentration_warnings)

    # ── EXIT STRATEGY ──
    any_cont = any(s.get("continuation") for s in swing_list) or any(
        s.get("continuation") for s in intra_list
    )
    if any_cont:
        lines.append("🔄 (lanjutan) = sinyal sama sudah muncul <14 hari lalu")
    lines.append("")
    lines.append("🛡 EXIT: SL jika tutup <SL | Trailing aktif +3%")
    lines.append("⚠️ Data Invezgo | Keputusan trader")

    result = "\n".join(lines)

    # M2: extra_parts (alert posisi, sektor) diintegrasikan SEBELUM truncate —
    # kalau di-append setelah truncate, output bisa >4096 dan Telegram drop.
    # Bagian utama dipotong LEBIH DULU dengan budget yang menyisakan ruang
    # untuk extra_parts, jadi alert posisi/sektor TETAP tampil dan total ≤ 3500.
    MAX_LEN = 3500
    TRUNC_MARKER = "\n…(truncated)"
    if extra_parts:
        extra_text = "\n\n" + "\n\n".join(extra_parts)
        # N2: extra_parts sendiri juga di-truncate (budget terpisah ~300 chars)
        # — sebelumnya extra tidak pernah dipotong sehingga total bisa >4096
        # (Telegram drop) walau bagian utama sudah di-truncate.
        if len(extra_text) > 300:
            extra_text = extra_text[: 300 - len(TRUNC_MARKER)] + TRUNC_MARKER
        budget = MAX_LEN - len(extra_text)
        if len(result) > budget:
            cut = max(budget - len(TRUNC_MARKER), 100)
            result = result[:cut] + TRUNC_MARKER
        result = result + extra_text
    else:
        # Truncate ke 3500 chars (Telegram-friendly) — perilaku lama tanpa extra_parts
        # L11: potong ke 3487 + marker (13 chars) = 3500 ≤ 3500 (dulu 3490+13=3503).
        if len(result) > MAX_LEN:
            result = result[: MAX_LEN - len(TRUNC_MARKER)] + TRUNC_MARKER

    return result
