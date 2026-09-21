# Produk: IDX Alpha Screener V7 (oleh Yan / RisetSaham)

## Apa ini
Screener saham IDX harian — engine **V7 Dual Mode** (SWING + INTRADAY), dipakai
untuk keputusan trading keluarga (non-komersial). Hasil tampil sebagai **kartu
visual di app RisetSaham** (`/riset/screener`): skor, mode, level Entry/SL/TP,
info bandar, pelacakan hasil. Telegram hanya kanal opsional.

## Alur produksi (tiga jalur)
- **21:00 WIB (hari bursa)** — `cron_v3_scan.py` → `v7_scan.py` (watchlist) → `kirim_ke_risetsaham.py`
- **19:00 WIB** — auto-scan seluruh pasar (`scan_ihsg.py`), dijadwalkan dari sisi
  RisetSaham (`~/risetsaham/scripts/auto_scan_ihsg.py`)
- **Sabtu 08:30** — `shadow_eval.py` (evaluasi lab bayangan)

## Aturan kebijakan (WAJIB dipatuhi — hasil backtest 1 tahun, 19 Sep 2026)
1. **Market mode**: `BEAR` = blokir TOTAL (tanpa sinyal) · `HIGH_VOLATILITY` = hanya
   STRONG_BUY · `RANGING` = STRONG_BUY + BUY · `BULL` = semua label.
2. **Veto volume swing & aturan false-breakout DIHAPUS** (backtest: tidak prediktif;
   kohort yang dulu diblokir justru lebih baik). JANGAN dihidupkan lagi tanpa
   bukti backtest baru.
3. Perubahan logika sinyal / gate / threshold = **WAJIB lewat
   `backtest_v7_teknikal.py`** + unit test diperbarui + jelaskan buktinya.
4. **Rantai keputusan TUNGGAL**: `_swing_gate`, `gate_swing_signal`,
   `_signal_from_score` ada di `idx_alpha_screener/v7_scan.py` — semua skrip lain
   HARUS mengimpor dari sana, JANGAN menyalin ulang.
5. **Data non-komersial** (pemakaian keluarga, volume panggilan santun).
   Jangan publikasikan data/hasil.

## Kontrak output (jangan diubah tanpa koordinasi)
CSV: `kode,skor,mode,entry,sl,tp,entry_ideal,bandar_sesi,bandar_3bln,catatan,tampil`
+ baris `RINGKASAN` untuk aplikasi.
- `tampil="ya"` → hanya sinyal yang lolos gate & izin regime; aplikasi hanya
  menampilkan baris ini (sisanya dihitung "tak ditampilkan" — transparansi).
- Batch dikirim ke `POST /riset/screener/api` → tabel `screener_batch` (sisi app).

## Kualitas & pengujian
- Suite uji: `idx_alpha_screener/test_v7.py` (unittest; jalankan dengan PYTHONUTF8=1).
- Lab bayangan: SEMUA finalis (termasuk yang tak jadi sinyal) dicatat ke
  `data/shadow_v7.csv` — untuk mengukur faktor broker/asing secara forward.
- Track record sinyal berjalan: `data/perf_tracker_v7.csv` + panel "Hasil sinyal".
