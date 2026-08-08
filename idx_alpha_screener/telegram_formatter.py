"""
telegram_formatter.py — Format output V7 untuk Telegram readable
=================================================================
Mengganti semua print() di v7_scan.py dengan format pesan yang ringkas.
Gunakan Markdown sederhana, batasi ~3500 karakter.

Format PASS 2 (lebih ringkas & readable — target ~700 chars utk skenario
3 swing + 2 intraday + 2 narrative + extra_parts):
- SATU baris per saham (swing & intraday):
  swing : `1. BUMI [Barito] 66.2 | 187 | 🎯 179-187 | SL 172/TP 213 📈 Rev+8% ⚡`
  intra : `ASII 55.0 | 5,200 | Vol 1.8x | SL 5,000/TP 5,600`
- Entry langsung range (kata Limit/GTC dihapus); SL/TP digabung `SL x/TP y`;
  RRR dihapus; mode ganda cukup `⚡` di akhir (tanpa '+intra'); earnings
  suffix pendek `📈 Rev+8%`; grup `[Barito]` dipertahankan.
- Sentimen + IHSG digabung 1 baris: `🔮 Waspada | IHSG 6,409 S 6,350/R 6,480`
  (RED→Bahaya, GREEN→Aman, selainnya→Waspada).
- Separator hanya 1 (sebelum ringkasan); baris 🛡 EXIT boilerplate dihapus;
  disclaimer 1 baris pendek: `⚠️ Alat bantu, bukan rekomendasi`.
- Ringkasan 1 baris tanpa kata RINGKASAN:
  `Swing 3 · Intra 1 · Alokasi 4.8jt · Risiko 240rb` (Rp ringkas: 4.8jt/240rb).
- Legenda lanjutan pendek (hanya kalau ada label lanjutan):
  `🔄 lanjutan = sinyal <14 hari`.
- Narrative AI tetap (user suka konteks) — maks 2 sinyal terbaik (└ 📝 ...).
- Ticker yang lolos swing DAN intraday tampil SEKALI di section SWING
  dengan penanda `⚡` di akhir (dulu muncul 2x dengan lot/entry beda → user
  mengira 2 sinyal berbeda). Kedua sinyal tetap di-log ke perf CSV.
- Broker flow hanya tampil saat EKSTREM (akumulasi_masif / distribusi)
  sebagai suffix pendek 🔵+45B / 🔴-8B di baris yang sama.
"""
import re
from datetime import datetime
from typing import List, Dict, Optional


def _fmt_price(val) -> str:
    """Format harga ke Rupiah dengan separator."""
    try:
        return f"Rp{int(val):,}"
    except (ValueError, TypeError):
        return f"Rp{int(0):,}"


def _fmt_num(val) -> str:
    """Format harga PADAT tanpa prefix Rp (mis. 187 / 1,870) untuk baris sinyal."""
    try:
        return f"{int(val):,}"
    except (ValueError, TypeError):
        return "0"


def _fmt_rp_short(val) -> str:
    """Format Rupiah RINGKAS: 4_800_000 → '4.8jt', 240_000 → '240rb', 2e9 → '2M'."""
    try:
        v = float(val)
    except (ValueError, TypeError):
        v = 0.0
    if v >= 1e9:
        return f"{v / 1e9:.1f}".rstrip("0").rstrip(".") + "M"
    if v >= 1e6:
        return f"{v / 1e6:.1f}".rstrip("0").rstrip(".") + "jt"
    if v >= 1e3:
        return f"{v / 1e3:.0f}" + "rb"
    return f"{int(v)}"


def _fmt_range(price_range: str) -> str:
    """Padatkan rentang entry: 'Rp168,000 - Rp187,000' → '168000-187000'.
    Kosong / '-' → '' (tidak ditampilkan)."""
    if not price_range or price_range == "-":
        return ""
    return price_range.replace("Rp", "").replace(",", "").replace(" ", "")


def _fmt_bf_extreme(bf: str) -> str:
    """Suffix broker flow EKSTREM: 'akumulasi_masif_45B' → '🔵+45B',
    'distribusi_8B' → '🔴-8B'. Netral/net_buy/akumulasi biasa → '' (tidak tampil)."""
    if not bf:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)B", bf)
    num = m.group(1) if m else ""
    if "akumulasi_masif" in bf:
        return f"🔵+{num}B"
    if "distribusi" in bf:
        return f"🔴-{num}B"
    return ""


def _fmt_earn_short(earn: str) -> str:
    """Suffix earnings pendek: 'Rev +8% YoY | margin ...' → '📈 Rev+8%'.
    Return '' kalau tidak ada data / tidak ada potongan Rev."""
    if not earn or earn in ("no_data", "error"):
        return ""
    m = re.search(r"Rev\s*[+-]?\d+(?:\.\d+)?%", earn)
    if not m:
        return ""
    return f"📈 {m.group(0).replace(' ', '')}"


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
    Format pesan Telegram lengkap untuk V7 screener (PASS 2 — ringkas).

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
        Hanya 2 sinyal terbaik yang ditampilkan (└ 📝 ...).
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

    # ── Dedup mode ganda (R6) ──
    # Ticker yang lolos SWING & INTRADAY → tampil SEKALI di section SWING
    # dengan penanda '⚡' di akhir; TIDAK diulang di section INTRADAY (dulu muncul
    # 2x dengan lot/entry beda → user mengira 2 sinyal berbeda). Kedua sinyal
    # tetap di-log ke perf CSV (mode berbeda, WR per mode tetap akurat).
    swing_disp = list(swing_list[:5])          # maks 5 ditampilkan
    intra_disp_all = list(intra_list[:5])
    swing_tickers = {s.get("tkr") for s in swing_disp}
    dual_tickers = {s.get("tkr") for s in swing_disp
                    if any(x.get("tkr") == s.get("tkr") for x in intra_list)}
    intra_disp = [s for s in intra_disp_all if s.get("tkr") not in swing_tickers]

    # ── HEADER (1 baris) ──
    lines.append(f"📊 SCREENER V7 — {now}")

    # ── SENTIMEN + IHSG (1 baris gabungan) ──
    if market_sentiment:
        sent = market_sentiment.get("sentiment", "YELLOW")
        label = {"RED": "Bahaya", "GREEN": "Aman"}.get(sent, "Waspada")
        line = f"🔮 {label}"
        kl = market_sentiment.get("key_levels")
        if kl and kl.get("current", 0) > 0:
            line += (f" | IHSG {kl['current']:,.0f} "
                     f"S {kl['support']:,.0f}/R {kl['resistance']:,.0f}")
        lines.append(line)
    lines.append("")

    # ── SWING (1 baris per saham, paling kompak) ──
    if swing_disp:
        lines.append("🏆 SWING (urut skor)")
        for i, s in enumerate(swing_disp):
            tkr = s["tkr"]
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)
            entry_range = _fmt_range(s.get("entry_rec", {}).get("price_range", ""))

            grp = f" [{s.get('group')}]" if s.get("group") else ""
            wk = " ⚠️ BEAR" if s.get("weekly") == "BEARISH" else ""
            cont = s.get("continuation")
            cont_label = f" 🔄 {cont}" if cont else ""
            dual = " ⚡" if tkr in dual_tickers else ""

            parts = [f"{i+1}. {tkr}{grp} {s['score']:.1f}", _fmt_num(s["price"])]
            if entry_range:
                parts.append(f"🎯 {entry_range}")
            parts.append(f"SL {_fmt_num(sl)}/TP {_fmt_num(tp)}")
            line = " | ".join(parts)

            # Suffix pendek di baris yang sama (bukan baris terpisah)
            if wk:
                line += wk
            bf_sfx = _fmt_bf_extreme(s.get("bf", ""))
            if bf_sfx:
                line += f" {bf_sfx}"
            earn_sfx = _fmt_earn_short(s.get("earn", ""))
            if earn_sfx and len(line) + len(earn_sfx) <= 150:
                line += f" {earn_sfx}"
            line += cont_label + dual
            lines.append(line)

            # Narrative AI — maks 2 sinyal terbaik (user suka konteks singkat)
            nar = narratives.get(tkr)
            if nar and i < 2:
                lines.append(f"└ 📝 {nar}")

        lines.append("")

    # ── INTRADAY (1 baris per saham; ticker dobel mode sudah di section SWING) ──
    if intra_disp:
        lines.append("⚡ INTRADAY (H+3)")
        for s in intra_disp:
            tkr = s["tkr"]
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)
            vol = s.get("vol", 1)
            cont = s.get("continuation")
            cont_label = f" 🔄 {cont}" if cont else ""

            line = (f"{tkr} {s['score']:.1f} | {_fmt_num(s['price'])} | "
                    f"Vol {vol:.1f}x | SL {_fmt_num(sl)}/TP {_fmt_num(tp)}")
            bf_sfx = _fmt_bf_extreme(s.get("bf", ""))
            if bf_sfx:
                line += f" {bf_sfx}"
            earn_sfx = _fmt_earn_short(s.get("earn", ""))
            if earn_sfx and len(line) + len(earn_sfx) <= 150:
                line += f" {earn_sfx}"
            line += cont_label
            lines.append(line)
        lines.append("")

    # ── RINGKASAN (1 baris, tanpa kata RINGKASAN) — separator tunggal ──
    lines.append("─" * 9)
    n_swing = len(swing_disp)
    n_intra = len(intra_disp)

    # Alokasi (top 3, deduplicate — ticker yang muncul di kedua mode dihitung sekali)
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

    lines.append(
        f"Swing {n_swing} · Intra {n_intra} · Alokasi {_fmt_rp_short(alloc)} · "
        f"Risiko {_fmt_rp_short(total_risk)}"
    )

    # ── Legenda lanjutan (hanya kalau ada label lanjutan di pesan) ──
    any_cont = any(s.get("continuation") for s in swing_disp) or any(
        s.get("continuation") for s in intra_disp
    )
    if any_cont:
        lines.append("🔄 lanjutan = sinyal <14 hari")

    # ── C2: peringatan konsentrasi grup (opsional — tidak muncul saat normal) ──
    if concentration_warnings:
        lines.append("")
        lines.extend(concentration_warnings)

    # ── Disclaimer singkat (1 baris) ──
    lines.append("")
    lines.append("⚠️ Alat bantu, bukan rekomendasi")

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
        if len(result) > MAX_LEN:
            result = result[: MAX_LEN - len(TRUNC_MARKER)] + TRUNC_MARKER

    return result
