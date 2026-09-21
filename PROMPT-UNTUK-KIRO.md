# PROMPT UNTUK KIRO — Paket Screener V7

Paket ini berisi **3 hal**:
1. **`PROMPT-UNTUK-KIRO.md`** (file ini) — prompt siap copas ke Kiro.
2. **`screener/`** — seluruh proyek Screener V7 (folder ini yang dibuka di Kiro).
3. **`PANDUAN-KIRO.md`** — panduan lengkap aturan main untuk Kiro
   (salinannya juga ada di dalam `screener/PANDUAN-KIRO.md`).

## Cara pakai (3 langkah)
1. Buka folder **`screener/`** di Kiro (File → Open Folder).
2. Copas prompt di bawah ke chat Kiro. (Mau mulai dari AUDIT saja dulu?
   Potong bagian "lanjut eksperimen akurasi" — sisanya tetap.)
3. Minta ringkasan di tiap akhir fase. Kiro wajib: 263 tes tetap hijau,
   semua bukti angka, dan menulis laporan di folder `audit/`.

---

## ⬇️ COPAS MULAI DARI BARIS DIBAWAH INI ⬇️

Halo Kiro! Kerjakan spec di `.kiro/specs/audit-akurasi/` — baca requirements.md,
design.md, tasks.md, lalu ikuti urutannya fase demi fase. Patuhi PANDUAN-KIRO.md
dan .kiro/steering/. Mulai Fase 0–1 (audit menyeluruh) → tulis
`audit/LAPORAN_AUDIT.md` → lanjut sinkron alat ukur + BASELINE → eksperimen
akurasi sesuai protokol → laporan `audit/akurasi/HASIL.md`.

Aturan: satu variabel per eksperimen; semua bukti angka wajib; 263 tes harus
tetap hijau; commit lokal dengan angka; jangan push tanpa izin; jangan ubah
kontrak output. Tunjukkan ringkasan di tiap akhir fase.

## ⬆️ COPAS SAMPAI BARIS DI ATAS INI ⬆️

---

## Catatan penting
- **Kiro di laptop Windows?** Butuh Python 3.11 + jalankan:
  `pip install -r screener/idx_alpha_screener/requirements.txt`
- **Scan live penuh** (data Stockbit) hanya bisa di server — tapi **semua fase
  audit & eksperimen akurasi bisa jalan offline** pakai cache yang sudah terpaket.
- Data pasar = pemakaian **non-komersial**. Jangan dipublikasikan.
