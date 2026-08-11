"""
telegram_formatter.py — Format output V7 untuk Telegram readable
=================================================================
Mengganti semua print() di v7_scan.py dengan format pesan yang ringkas.
Gunakan Markdown sederhana, batasi ~3500 karakter.

Format GAYA A (baris pendek vertikal — pilihan user):
- Header: `📊 SCREENER V7 — 10/08 (21:04)` (jam dari run).
- Market: `🟢 Market: AMAN | IHSG 6.365 (S: 6.003 / R: 6.454)`
  (GREEN→🟢 AMAN, RED→🔴 BAHAYA, lainnya→🟡 WASPADA; angka TITIK ribuan).
- Section `🏆 SWING SIGNALS` + separator `━━...━━` (20 chars). Per saham
  LIMA baris (+narrative opsional) — baris pendek vertikal:
    1. TPIA — Skor: 70.6                    (nomor + ticker + skor)
       Harga: 2.120 ⚠️ BEAR                 (⚠️ BEAR kalau weekly BEARISH;
                                             ⚡ kalau dobel mode; 🔄 dd/mm
                                             kalau lanjutan — di akhir baris)
       🎯 Buy: 2.120 - 2.141                (label entry EKSPLISIT — dari
                                             entry_rec.price_range, fallback
                                             harga*0.96 - harga)
       🛡️ SL 1.922 | TP 2.448
       📊 Flow +255B | Rev +103%            (broker ekstrem + earnings;
                                             baris hilang kalau keduanya kosong)
       └ 📝 {narasi}                        (opsional, maks 2 terbaik)
- Section `⚡ INTRADAY (H+3)` + separator. Per saham TIGA baris:
      • HMSP — Skor: 60.0
        Harga: 760 | Vol 1.9x               (+ 🔄 kalau lanjutan)
        🛡️ SL 740 | TP 789 | Rev +2%       (earnings opsional)
- Section `⚙️ MANAJEMEN RISIKO`:
      • Modal: Rp 4.600.000 | Max Risk: Rp 231.000   (alokasi SEMUA sinyal,
        Σ risk_amount dedup ticker — konsisten dgn IDE6 guard)
      ⚠️ Warning: {teks} + `   ↳ {detail}` per peringatan (KONSENTRASI /
        TOTAL RISK / CA BLACKOUT) — dipetakan rapi dari list v7_scan.
- Ringkasan `Swing N · Intra N` LAMA DIHAPUS (digantikan section MANAJEMEN
  RISIKO). Label `🎯 Limit`/`🎯` lama diganti `🎯 Buy: {range}`.
- Section `🔄 LANJUTAN (masih valid)` DI BAWAH INTRADAY (1 baris per saham:
  `BUMI 187 | SL 171/TP 213 🔄 08/08`) — hanya kalau ada lanjutan.
- Ticker yang lolos swing DAN intraday tampil SEKALI di section SWING
  dengan penanda `⚡` (dulu muncul 2x → user mengira 2 sinyal berbeda).
  Kedua sinyal tetap di-log ke perf CSV.
- Broker flow hanya tampil saat EKSTREM (akumulasi_masif / distribusi)
  sebagai `Flow +45B` / `Flow -8B` di baris Data.
- Angka di SEMUA tempat memakai TITIK ribuan (1.920), bukan koma.
- N10 (P2): CAP LANJUTAN 3 HARI — sinyal continuation (fresh=0) hanya tampil
  maks 3 hari sejak ref_date (usia > 3 hari → TIDAK ditampilkan di pesan,
  tapi tetap tercatat di CSV). N10 (P2): TOP-5 PER PESAN — maks 5 sinyal
  SWING + 5 INTRADAY terbaik (skor tertinggi, fresh dulu baru lanjutan);
  cap tampilan saja, CSV tetap mencatat semua.
"""
import re
from datetime import datetime
from typing import List, Dict, Optional

# Separator section (20 karakter) — persis contoh user
SEP = "━" * 20

# N10 (P2): usia maksimum sinyal lanjutan (fresh=0) yang masih ditampilkan
# di pesan, dihitung dari continuation ref_date ('dd/mm'). Lebih tua dari ini
# → tidak tampil di pesan (tetap di CSV).
CONTINUATION_MAX_AGE_DAYS = 3


def _cont_age_days(cont, now=None):
    """Umur sinyal lanjutan (hari) dari label continuation 'dd/mm' (ref_date).

    Tahun diasumsikan tahun berjalan; kalau hasilnya di masa depan (label
    dari akhir tahun, mis. 31/12 saat ini 01/01) mundur 1 tahun. Return None
    kalau tidak ter-parse (defensif: tampilkan saja).
    """
    if not cont:
        return None
    try:
        d, m = str(cont).strip().split("/")
        now = now or datetime.now()
        ref = datetime(now.year, int(m), int(d))
        if ref > now:
            ref = datetime(now.year - 1, int(m), int(d))
        return (now - ref).days
    except (ValueError, TypeError):
        return None


def _cont_visible(s: dict, now=None) -> bool:
    """N10 (P2) — cap lanjutan 3 hari: sinyal continuation (fresh=0) berusia
    > CONTINUATION_MAX_AGE_DAYS sejak ref_date TIDAK ditampilkan di pesan
    (tetap tercatat di CSV). Sinyal fresh selalu tampil."""
    cont = s.get("continuation")
    if not cont:
        return True
    age = _cont_age_days(cont, now=now)
    if age is None:
        return True
    return age <= CONTINUATION_MAX_AGE_DAYS


def _top_display(signals, n=5):
    """N10 (P2) — top-n display: fresh dulu (skor tertinggi), baru lanjutan
    (skor tertinggi). Cap TAMPILAN saja — CSV tetap mencatat semua sinyal."""
    fresh = sorted((s for s in signals if not s.get("continuation")),
                   key=lambda x: x.get("score", 0), reverse=True)
    cont = sorted((s for s in signals if s.get("continuation")),
                  key=lambda x: x.get("score", 0), reverse=True)
    return (fresh + cont)[:n]


def _fmt_num(val) -> str:
    """Format angka dengan TITIK ribuan (1.920) — tanpa prefix Rp."""
    try:
        return f"{int(val):,}".replace(",", ".")
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


def _fmt_range(price_range) -> str:
    """Rentang entry → titik-ribuan: 'Rp2,120 - Rp2,141' → '2.120 - 2.141'.
    Kosong / '-' / tidak valid → '' (pemanggil memakai fallback)."""
    if not price_range or str(price_range).strip() in ("", "-"):
        return ""
    raw = str(price_range).replace("Rp", "").replace(",", "")
    parts = [p.strip() for p in raw.split("-")]
    out = []
    for p in parts:
        if not p:
            return ""
        try:
            out.append(f"{int(float(p)):,}".replace(",", "."))
        except (ValueError, TypeError):
            return ""
    return " - ".join(out)


def _fmt_buy_range(s: dict) -> str:
    """Baris Buy EKSPLISIT: '   🎯 Buy: 2.120 - 2.141'.
    Sumber: entry_rec.price_range (titik-ribuan); kalau entry_rec kosong /
    tidak ada / '-' → fallback '{harga*0.96:.0f} - {harga:.0f}'."""
    er = s.get("entry_rec") or {}
    r = _fmt_range(er.get("price_range", ""))
    if r:
        return f"   🎯 Buy: {r}"
    price = float(s.get("price", 0) or 0)
    # fallback {harga*0.96:.0f} - {harga:.0f} (pembulatan :.0f, bukan truncate)
    lo = f"{int(round(price * 0.96)):,}".replace(",", ".")
    hi = f"{int(round(price)):,}".replace(",", ".")
    return f"   🎯 Buy: {lo} - {hi}"


def _fmt_bf_extreme(bf: str) -> str:
    """Suffix broker flow EKSTREM utk baris Data: 'akumulasi_masif_45B' → '+45B',
    'distribusi_8B' → '-8B'. Netral/net_buy/akumulasi biasa → '' (tidak tampil)."""
    if not bf:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)B", bf)
    num = m.group(1) if m else ""
    if "akumulasi_masif" in bf:
        return f"+{num}B"
    if "distribusi" in bf:
        return f"-{num}B"
    return ""


def _fmt_earn_short(earn: str) -> str:
    """Suffix earnings pendek utk baris Data: 'Rev +103% YoY | margin ...'
    → 'Rev +103%'. Return '' kalau tidak ada data / tidak ada potongan Rev."""
    if not earn or earn in ("no_data", "error"):
        return ""
    m = re.search(r"Rev\s*[+-]?\d+(?:\.\d+)?%", earn)
    if not m:
        return ""
    return re.sub(r"Rev\s*([+-]?\d)", r"Rev \1", m.group(0))


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


def _map_warning(w: str):
    """Petakan string warning v7_scan → (teks, detail) utk section MANAJEMEN RISIKO.

      '⚠️ KONSENTRASI: Grup Barito 45% > 40% — lot dikurangi (BRPT(swing) 15→13 lot) → 39%'
        → ('Konsentrasi Grup Barito 45% (>40%)',
           'Lot BRPT 15→13 lot dipangkas otomatis ke max 40%.')
      '⚠️ TOTAL RISK: 2.5% > 3.0% — lot dikurangi (BRPT 15→13 lot)'
        → ('Total risk 2.5% (>3.0%)', 'Lot BRPT 15→13 lot dipangkas otomatis.')
      '⚠️ CA BLACKOUT: TPIA DIVIDEND 15/08 — skip (H+7)'
        → ('CA Blackout TPIA DIVIDEND 15/08', 'skip (H+7)')

    Return None utk string kosong; (teks, detail) utk sisanya.
    """
    w = (w or "").strip()
    if not w:
        return None
    body = re.sub(r"^⚠️\s*", "", w)
    head, detail = body, ""
    if " — " in body:
        head, detail = body.split(" — ", 1)
    is_konsentrasi = head.startswith("KONSENTRASI:")
    if is_konsentrasi:
        head = "Konsentrasi " + head[len("KONSENTRASI:"):].strip()
    elif head.startswith("TOTAL RISK:"):
        head = "Total risk " + head[len("TOTAL RISK:"):].strip()
    elif head.startswith("CA BLACKOUT:"):
        head = "CA Blackout " + head[len("CA BLACKOUT:"):].strip()
    # '45% > 40%' → '45% (>40%)'
    head = re.sub(r"(\d+(?:\.\d+)?%)\s*>\s*(\d+(?:\.\d+)?%)", r"\1 (>\2)", head)
    detail = detail.strip()
    if detail:
        # Detail berisi daftar lot: '(BRPT(swing) 15→13 lot, BUMI(intraday) 20→18 lot)'
        items = re.findall(r"([A-Z]{2,5})(?:\((?:swing|intraday)\))?\s*(\d+)→(\d+)", detail)
        if items:
            parts = [f"{t} {a}→{b} lot" for t, a, b in items]
            detail = "Lot " + ", ".join(parts) + " dipangkas otomatis"
            # 'ke max X%' hanya utk KONSENTRASI (batas eksposur grup);
            # TOTAL RISK (batas portfolio) cukup 'dipangkas otomatis.'
            if is_konsentrasi:
                mp = re.search(r"\(>\s*(\d+(?:\.\d+)?)%\)", head)
                if mp:
                    detail += f" ke max {float(mp.group(1)):.0f}%."
                    return head, detail
            detail += "."
    return head, detail


def _alloc_and_risk(swing_list: List[dict], intra_list: List[dict]):
    """Alokasi & risiko total utk section MANAJEMEN RISIKO.

    Menghitung SEMUA sinyal (fresh + lanjutan); ticker yang muncul di KEDUA
    mode dihitung SEKALI (alokasi pakai entry pertama/swing, risiko pakai yang
    LEBIH BESAR — konsisten dgn enforce_total_risk_guard IDE6). risk_amount
    sekarang risiko SEJATI (entry−SL)/entry×cost dari position_sizing; kalau
    key tidak ada (pemanggil lama) fallback 5% cost.

    Return (alloc_total, total_risk) dalam Rupiah (int).
    """
    cost_by_tkr = {}
    risk_by_tkr = {}
    for s in list(swing_list) + list(intra_list):
        tkr = s.get("tkr", "")
        sz = s.get("sizing") or {}
        cost = float(sz.get("cost", 0) or 0)
        risk = float(sz.get("risk_amount", cost * 0.05) or 0)
        if tkr not in cost_by_tkr:
            cost_by_tkr[tkr] = cost
            risk_by_tkr[tkr] = risk
        else:
            risk_by_tkr[tkr] = max(risk_by_tkr[tkr], risk)
    return int(sum(cost_by_tkr.values())), int(sum(risk_by_tkr.values()))


def format_message(
    swing_list: List[dict],
    intra_list: List[dict],
    market_sentiment: Optional[dict] = None,
    capital: float = 20_000_000,
    summary: Optional[dict] = None,
    narratives: Optional[Dict[str, str]] = None,
    concentration_warnings: Optional[List[str]] = None,
    extra_parts: Optional[List[str]] = None,
    skip_reasons: Optional[List[str]] = None,
) -> str:
    """
    Format pesan Telegram lengkap untuk V7 screener (GAYA A — baris pendek
    vertikal, pilihan user).

    Parameters
    ----------
    swing_list : list[dict] — daftar sinyal swing (sorted by score desc)
        Setiap dict: tkr, score, price, exit (dict with stop_loss, take_profit, rrr),
        sizing (dict with lots, cost, risk_amount), bf, ff, weekly, brokers,
        entry_rec (dict with method, price_range, condition)
    intra_list : list[dict] — daftar sinyal intraday
        Setiap dict: tkr, score, price, exit, sizing, bf, ff, vol, entry_rec
    market_sentiment : dict, optional — dari predict_market_sentiment()
    capital : float — modal total (dipertahankan utk kompatibilitas pemanggil
        lama; alokasi dihitung dari sizing.cost SEMUA sinyal)
    summary : dict, optional — ringkasan tambahan (legacy, tidak dipakai PASS 3)
    narratives : dict[str, str], optional — {ticker: kalimat naratif} dari
        ai_narrative.generate_narratives(). Default None/kosong = kompatibel
        dengan pemanggil lama; sinyal tanpa narrative tetap diformat seperti
        biasa. Hanya 2 sinyal terbaik yang ditampilkan (└ 📝 ...).
    concentration_warnings : list[str], optional — baris peringatan C2
        (guard konsentrasi grup) + IDE6 (guard total risk) dari v7_scan.
        Dipetakan ke `⚠️ Warning: {teks}` + `   ↳ {detail}` di section
        MANAJEMEN RISIKO.
    extra_parts : list[str], optional — bagian tambahan (mis. alert posisi,
        ringkasan sektor) yang diintegrasikan SEBELUM truncate 3500 (M2).
        Dipakai v7_scan supaya output final tidak melebihi 3500 karakter.
    skip_reasons : list[str], optional — alasan skip CA blackout (IDE5) dari
        v7_scan; tampil sebagai warning di section MANAJEMEN RISIKO.

    Returns
    -------
    str — pesan siap kirim (Markdown)
    """
    now = datetime.now().strftime("%d/%m (%H:%M)")
    lines = []
    narratives = narratives or {}

    # ── Dedup mode ganda (R6) ──
    # Ticker yang lolos SWING & INTRADAY → tampil SEKALI di section SWING
    # dengan penanda '⚡' di akhir; TIDAK diulang di section INTRADAY.
    # ── Section LANJUTAN (V7 akurasi) ──
    # SWING hanya menampilkan sinyal FRESH (continuation kosong). Sinyal swing
    # yang merupakan LANJUTAN (<14 hari, punya continuation) dipindah ke section
    # 'LANJUTAN (masih valid)' DI BAWAH INTRADAY — 1 baris per saham TANPA
    # narrative. Intraday tetap 1 section (label 🔄 di baris yang sama).
    swing_fresh = [s for s in swing_list if not s.get("continuation")]
    swing_cont = [s for s in swing_list if s.get("continuation")]
    # N10 (P2): top-5 per pesan (skor tertinggi, fresh dulu) + cap lanjutan
    # 3 hari (usia > 3 hari sejak ref_date → tidak tampil, tetap di CSV).
    swing_disp = list(_top_display(swing_fresh, 5))   # SWING: hanya fresh, maks 5
    swing_cont_disp = [s for s in _top_display(swing_cont, 5)
                       if _cont_visible(s)]           # LANJUTAN: maks 5, usia <= 3 hari
    intra_disp_all = _top_display(intra_list, 5)      # INTRADAY: fresh dulu, maks 5
    swing_tickers = {s.get("tkr") for s in swing_disp}
    dual_tickers = {s.get("tkr") for s in swing_disp
                    if any(x.get("tkr") == s.get("tkr") for x in intra_list)}
    intra_disp = [s for s in intra_disp_all if s.get("tkr") not in swing_tickers]
    intra_disp = [s for s in intra_disp if _cont_visible(s)]

    # ── HEADER (1 baris) ──
    lines.append(f"📊 SCREENER V7 — {now}")

    # ── MARKET (1 baris): 🟢 AMAN / 🔴 BAHAYA / 🟡 WASPADA + IHSG titik-ribuan ──
    if market_sentiment:
        sent = market_sentiment.get("sentiment", "YELLOW")
        emoji, label = {"GREEN": ("🟢", "AMAN"), "RED": ("🔴", "BAHAYA")}.get(
            sent, ("🟡", "WASPADA"))
        line = f"{emoji} Market: {label}"
        kl = market_sentiment.get("key_levels")
        if kl and kl.get("current", 0) > 0:
            line += (f" | IHSG {_fmt_num(kl['current'])} "
                     f"(S: {_fmt_num(kl['support'])} / R: {_fmt_num(kl['resistance'])})")
        lines.append(line)
    lines.append("")

    # ── SWING (Gaya A: 5 baris + narrative opsional, blank antar saham) ──
    if swing_disp:
        lines.append("🏆 SWING SIGNALS")
        lines.append(SEP)
        for i, s in enumerate(swing_disp):
            tkr = s["tkr"]
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)

            # Baris 1: '1. TPIA — Skor: 70.6' (nomor + ticker + skor)
            lines.append(f"{i+1}. {tkr} — Skor: {s['score']:.1f}")

            # Baris 2: '   Harga: 2.120' + ⚠️ BEAR + ⚡ + 🔄 dd/mm
            line2 = f"   Harga: {_fmt_num(s['price'])}"
            if s.get("weekly") == "BEARISH":
                line2 += " ⚠️ BEAR"
            if tkr in dual_tickers:
                line2 += " ⚡"
            cont = s.get("continuation")
            if cont:
                line2 += f" 🔄 {cont}"
            lines.append(line2)

            # Baris 3: entry EKSPLISIT (selalu muncul — fallback kalau kosong)
            lines.append(_fmt_buy_range(s))
            # Baris 4: risk
            lines.append(f"   🛡️ SL {_fmt_num(sl)} | TP {_fmt_num(tp)}")

            # Baris 5: Data (Flow broker ekstrem + earnings) — hilang kalau kosong
            data_parts = []
            bf_sfx = _fmt_bf_extreme(s.get("bf", ""))
            if bf_sfx:
                data_parts.append(f"Flow {bf_sfx}")
            earn_sfx = _fmt_earn_short(s.get("earn", ""))
            if earn_sfx:
                data_parts.append(earn_sfx)
            if data_parts:
                lines.append("   📊 " + " | ".join(data_parts))

            # Narrative AI — maks 2 sinyal terbaik (user suka konteks singkat).
            nar = narratives.get(tkr)
            if nar and i < 2:
                lines.append(f"   └ 📝 {nar}")
            lines.append("")

    # ── INTRADAY (Gaya A: 3 baris per saham; dobel mode sudah di SWING) ──
    if intra_disp:
        lines.append("⚡ INTRADAY (H+3)")
        lines.append(SEP)
        for s in intra_disp:
            tkr = s["tkr"]
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)
            vol = s.get("vol", 1)
            # Baris 1: '• HMSP — Skor: 60.0'
            lines.append(f"• {tkr} — Skor: {s['score']:.1f}")
            # Baris 2: '  Harga: 760 | Vol 1.9x' (+ 🔄 kalau lanjutan)
            line2 = f"  Harga: {_fmt_num(s['price'])} | Vol {vol:.1f}x"
            if s.get("continuation"):
                line2 += " 🔄"
            lines.append(line2)
            # Baris 3: '  🛡️ SL 740 | TP 789' + ' | Rev +2%' kalau ada
            line3 = f"  🛡️ SL {_fmt_num(sl)} | TP {_fmt_num(tp)}"
            earn_sfx = _fmt_earn_short(s.get("earn", ""))
            if earn_sfx:
                line3 += f" | {earn_sfx}"
            lines.append(line3)
            lines.append("")

    # ── LANJUTAN (masih valid) — di bawah INTRADAY, 1 baris per saham ──
    # 'BUMI 187 | SL 171/TP 213 🔄 08/08' — tanpa narrative.
    if swing_cont_disp:
        lines.append("🔄 LANJUTAN (masih valid)")
        lines.append(SEP)
        for s in swing_cont_disp:
            exit_d = s.get("exit", {})
            sl = exit_d.get("stop_loss", 0)
            tp = exit_d.get("take_profit", 0)
            lines.append(
                f"{s['tkr']} {_fmt_num(s['price'])} | "
                f"SL {_fmt_num(sl)}/TP {_fmt_num(tp)} 🔄 {s.get('continuation', '')}"
            )
        lines.append("")

    # ── Legenda lanjutan (hanya kalau ada label 🔄 di pesan) ──
    any_cont = bool(swing_cont_disp) or any(
        s.get("continuation") for s in intra_disp
    )
    if any_cont:
        lines.append("🔄 lanjutan = sinyal <14 hari")
        lines.append("")

    # ── MANAJEMEN RISIKO (menggantikan ringkasan 'Swing N · Intra N') ──
    alloc_total, total_risk = _alloc_and_risk(swing_list, intra_list)
    lines.append("⚙️ MANAJEMEN RISIKO")
    lines.append(f"• Modal: Rp {_fmt_num(alloc_total)} | Max Risk: Rp {_fmt_num(total_risk)}")
    for w in list(concentration_warnings or []) + list(skip_reasons or []):
        mapped = _map_warning(w)
        if not mapped:
            continue
        head, detail = mapped
        lines.append(f"⚠️ Warning: {head}")
        if detail:
            lines.append(f"   ↳ {detail}")

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
