# Spec: Audit Menyeluruh + Peningkatan Akurasi — IDX Alpha Screener V7

- Status: aktif · Dibuat: 21 Sep 2026 (Hermes untuk Yan)
- Pemilik: Yan · Repo: `~/screener` (branch `feat/integrasi-risetsaham`)

## 1. Tujuan
1. **AUDIT semua program produksi** di repositori ini: bug, inkonsistensi kebijakan,
   risiko data, celah tes, dokumentasi usang. Hasil = laporan berprioritas.
2. **TINGKATKAN AKURASI sinyal V7** — HANYA dengan bukti backtest. Tidak ada perubahan
   logika sinyal tanpa bukti angka + tes.

## 2. Ruang lingkup (yang diaudit)
Semua kode produksi di repo, **kecuali**: `idx_alpha_screener/v4|v5|v6` (engine lama)
dan `screenerOld/` (abaikan). Repo `~/risetsaham` → **read-only** (temuan ditulis,
jangan diubah dari sini).

Berkas fokus: `scan_ihsg.py`, `scan_mandiri.py`, `cron_v3_scan.py`,
`kirim_ke_risetsaham.py`, `shadow_eval.py`, `backtest_v7_teknikal.py`,
`idx_alpha_screener/{v7_scan.py, scoring.py, v7_exit.py, perf_tracker.py,
shadow_log.py, weekly_report.py, market_sentiment.py, entry_timing.py,
signal_manager.py, risk.py, portfolio.py, slippage.py, data*.py, utils/*, config.yaml}`.

## 3. Kebutuhan fungsional
- **FR1** Laporan `audit/LAPORAN_AUDIT.md` — tiap temuan: ID, severity (P0/P1/P2),
  lokasi `file:line`, bukti, usulan perbaikan, status.
- **FR2** Perbaikan bug P0/P1 yang **TIDAK mengubah perilaku sinyal** + tes baru untuk
  tiap bug yang diperbaiki. Suite `test_v7.py` (263 tes) tetap hijau.
- **FR3** **Sinkronkan alat ukur (backtest) dengan kebijakan produksi terkini** sebelum
  klaim akurasi apa pun. Temuan awal: `backtest_v7_teknikal.py` masih memakai `vol1`
  (vol_ratio≥1.0) di subset "produksi" dan label "dibuang pra-filter produksi" —
  padahal produksi sudah TIDAK memakai filter itu sejak 19 Sep (lihat `product.md`).
- **FR4** Baseline akurasi terdokumentasi di `audit/akurasi/BASELINE.md` (angka SEBELUM
  perubahan apa pun, termasuk split 2 paruh).
- **FR5** Eksperimen akurasi HANYA offline (skrip di `audit/akurasi/eksperimen/`),
  **satu variabel per eksperimen**; SEMUA percobaan dicatat di `EKSPERIMEN.md`
  (termasuk yang gagal — anti cherry-picking).
- **FR6** Hanya kandidat yang lolos protokol validasi (design.md §3) dipindah ke kode
  produksi — perubahan kecil + tes + commit terpisah berisi angka sebelum/sesudah.
- **FR7** Laporan akhir `audit/akurasi/HASIL.md`: tabel sebelum/sesudah + keputusan
  (ACCEPT/REJECT/INCONCLUSIVE) + rekomendasi lanjutan (termasuk yang butuh data forward).

## 4. Non-tujuan
- Jangan ubah kontrak output (kolom CSV / `POST /riset/screener/api`) — lihat `structure.md`.
- Jangan ubah bobot faktor broker/asing/fundamental (45% skor) — tak ada riwayat,
  hanya bisa dievaluasi forward via lab bayangan.
- Jangan sentuh kebijakan 19 Sep tanpa bukti baru yang lolos protokol.
- Jangan push ke GitHub (commit lokal saja; minta konfirmasi untuk push).
- Jangan jalankan scan pasar penuh berulang-ulang (hemat panggilan; pakai cache).

## 5. Kriteria selesai (DoD)
- `audit/LAPORAN_AUDIT.md` lengkap; semua P0 ditangani/dijelaskan.
- Suite tes hijau (jumlah tes tidak berkurang; bertambah untuk bug yang diperbaiki).
- Baseline + eksperimen + hasil terdokumentasi; setiap perubahan punya angka
  H1 / H2 / periode penuh.
- Ringkasan akhir ke user (Bahasa Indonesia).
