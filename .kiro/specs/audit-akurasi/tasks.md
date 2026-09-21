# Tasks — kerjakan berurutan (jangan lompat fase)

## Fase 0 — Persiapan
- [ ] 0.1 Baca: `PANDUAN-KIRO.md`, `.kiro/steering/*`, `INTEGRASI-RISETSAHAM.md`
- [ ] 0.2 venv + `pip install -r idx_alpha_screener/requirements.txt`
- [ ] 0.3 Jalankan suite tes → harus OK (263 tes):
      `PYTHONUTF8=1 .venv/bin/python -m unittest discover -s idx_alpha_screener -p "test_v7.py" -v`
- [ ] 0.4 Buat folder `audit/` + `audit/akurasi/eksperimen/`

## Fase 1 — AUDIT (semua program produksi)
- [ ] 1.1 Periksa modul satu per satu (daftar di requirements.md §2). Catat: fungsi utama, alur data, risiko.
- [ ] 1.2 Checklist khusus:
  - [ ] a. **Lookahead/leakage** — semua indikator & sinyal hanya pakai data ≤ tanggal sinyal (termasuk `align_to_market`, backtest).
  - [ ] b. **Konsistensi kebijakan** di SEMUA jalur (`scan_ihsg`, `scan_mandiri`, `cron_v3`, backtest): izin regime, tanpa veto volume, tanpa false-breakout, BEAR blok.
  - [ ] c. **Single-source** — cari logika gate/scoring yang DISALIN (harus impor dari `v7_scan.py`/`scoring.py`).
  - [ ] d. Bug umum: NaN/div-0, timezone WIB, off-by-one, `except` senyap.
  - [ ] e. Keamanan: tidak ada kredensial hardcoded / token di log.
  - [ ] f. Ketahanan: data kosong / hari separuh / libur; retry & backoff Stockbit.
  - [ ] g. Celah tes: tandai jalur kritis tanpa tes → tambahkan saat memperbaiki bug.
  - [ ] h. Dokumentasi usang (mis. README masih klaim "Invezgo 100%") → tandai.
- [ ] 1.3 Tulis `audit/LAPORAN_AUDIT.md` (tabel: ID | Severity | file:line | masalah | bukti | usulan | status)
- [ ] 1.4 Perbaiki P0/P1 yang TIDAK mengubah perilaku sinyal; tiap perbaikan: tes baru + suite hijau.
- [ ] 1.5 Commit (pesan menyebut temuan ID).

## Fase 2 — Sinkron alat ukur + BASELINE
- [ ] 2.1 Sinkronkan backtest vs kebijakan produksi (design.md §2) — jadikan temuan bila menyimpang.
- [ ] 2.2 Jalankan backtest: `.venv/bin/python backtest_v7_teknikal.py` (smoke dulu `--limit 300` bila perlu; catat runtime).
- [ ] 2.3 Tulis `audit/akurasi/BASELINE.md`: n, WR, expectancy net, avg H+20, alpha;
      split paruh-1/paruh-2 + per regime. Salin output mentah ke `audit/akurasi/baseline/`.

## Fase 3 — EKSPERIMEN AKURASI
- [ ] 3.1 Kumpulkan kandidat (design.md §4 + temuan audit sendiri) → pilih 3–5 prioritas.
- [ ] 3.2 Tiap kandidat: skrip di `audit/akurasi/eksperimen/` → jalankan → catat H1/H2/penuh di `EKSPERIMEN.md`.
- [ ] 3.3 Verdict per protokol (design.md §3). HANYA ACCEPT yang diporting ke produksi
      (perubahan kecil + tes + commit terpisah dengan angka).
- [ ] 3.4 Jangan ubah kebijakan 19 Sep kecuali kandidat baru lolos SEMUA syarat protokol.

## Fase 4 — Penutup
- [ ] 4.1 Suite tes final hijau (263+).
- [ ] 4.2 `audit/akurasi/HASIL.md` (before/after + daftar REJECT + next steps forward-only).
- [ ] 4.3 Update `.kiro/steering/product.md` bila ada kebijakan berubah (tanggal + bukti).
- [ ] 4.4 Ringkas ke user (Bahasa Indonesia): temuan, perubahan, angka, rekomendasi.
