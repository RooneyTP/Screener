# Design — metodologi audit & peningkatan akurasi

## 1. Wajib dibaca dulu
`.kiro/steering/product.md` (kebijakan produksi — WAJIB patuh), `tech.md` (perintah),
`structure.md` (peta file), `INTEGRASI-RISETSAHAM.md`.

## 2. Fase A — Sinkronisasi alat ukur (SEBELUM klaim akurasi apa pun)
Alat backtest harus mencerminkan **kebijakan produksi terkini**:
- Tanpa veto/pra-filter volume (`SWING_MIN_VOL_RATIO=0.0`; vol hanya men-downgrade SB→BUY).
- Tanpa aturan false-breakout; **BEAR = blok total**; izin: RANGING=SB+BUY,
  HIGH_VOLATILITY=hanya SB, BULL=semua.
- **Temuan awal (verifikasi dulu!):** `backtest_v7_teknikal.py` masih memakai `vol1` di
  subset produksi (baris ~252, 293–295, 310, 320) + label "dibuang pra-filter produksi".
  Sinkronkan subset "produksi" (finalis likuid tanpa vol1), pertahankan subset vol1
  hanya sebagai ANALISIS pembanding, lalu regenerate ringkasan.
- Bukti sinkron ditulis di `BASELINE.md` (sebelum dipakai untuk klaim akurasi).

## 3. Protokol validasi perubahan akurasi (WAJIB — semua syarat)
Metrik utama: **expectancy net per trade** (konvensi biaya 0.4%), WR, avg return H+20,
alpha vs IHSG. Hitung untuk: periode penuh + **paruh-1 & paruh-2** (+ per regime bila relevan).

Aturan **ACCEPT** (harus SEMUA benar):
1. Metrik naik di periode penuh, DAN
2. naik di KEDUA paruh (bukan artefak satu periode), DAN
3. subset yang ditolak aturan baru jelas lebih buruk (aturan benar-benar "membuang" yang jelek), DAN
4. tidak digerakkan oleh ≤5 trade (cek kontribusi trade teratas / bootstrap bila tersedia), DAN
5. penurunan jumlah sinyal dilaporkan eksplisit (trade-off transparan).

Aturan proses:
- **Satu eksperimen = satu variabel.** Jangan ubah 2 hal sekaligus.
- Catat SEMUA percobaan (termasuk gagal) di `EKSPERIMEN.md`.
- Bukti tidak konsisten → verdict REJECT/INCONCLUSIVE — tetap ditulis, jangan dipaksa.

## 4. Kandidat awal (hasil riset sebelumnya — WAJIB diverifikasi ulang, bukan langsung dipakai)
- **Gate makro IHSG < MA50** (indikasi +0,58 pt/trade di riset RisetSaham; in-sample → uji dgn protokol).
- **Eksekusi realistis**: entry "open hari berikutnya" vs "close sinyal" (riset: −0,43%/trade!) →
  uji aturan guard gap "0..+3%" (+0,20 pt) & limit-order.
- **Trailing stop 2,5×ATR** (kandidat lemah +0,32 pt, t=1,07) → uji ulang.
- **Grid TP/SL & ambang skor** per regime → uji kecil-kecilan (hati-hati overfit).
- **Sudah GAGAL di riset — jangan diulang tanpa alasan baru:** filter RSI/ADX/ret20/weekly,
  breadth, gate sektor, break-even stop, time-stop, guard konsentrasi berlebih.
Alat: `uji_kebijakan/*.py` (9 skrip: tpsl, wf, filters, gate, boot, breadth, exit dinamis,
sektor, entry guard) + grid bawaan `backtest_v7_teknikal.py`.

## 5. Batas pengetahuan (penting!)
- Faktor **broker/asing/fundamental (45% skor) TIDAK bisa di-backtest** (tak ada riwayat).
  Untuk bagian ini: pastikan `data/shadow_v7.csv` (lab bayangan) merekam bersih; hipotesis
  diperiksa **forward** via `shadow_eval.py` setelah data cukup (evaluasi mingguan).
- Kinerja forward riil: `data/perf_tracker_v7.csv` + panel "Hasil sinyal" — konteks, bukan bukti tunggal.

## 6. Alur kerja
1. Audit statis menyeluruh (checklist di `tasks.md`) → `audit/LAPORAN_AUDIT.md`.
2. Perbaikan aman (tanpa mengubah sinyal) + tes → commit.
3. Sinkron alat ukur (§2) → `BASELINE.md` (angka sebelum).
4. Eksperimen kandidat (offline scripts) → `EKSPERIMEN.md`.
5. ACCEPT → port ke produksi + tes + commit bernomor; REJECT → catat.
6. `HASIL.md` + ringkasan ke user.
