# Panduan untuk Kiro AI — IDX Alpha Screener V7

Halo Kiro! Folder ini = proyek **IDX Alpha Screener V7** milik Yan (screener saham
IDX harian; hasil tampil di app RisetSaham).

**Baca dulu sebelum ngoding:** file ini → `.kiro/steering/product.md`,
`tech.md`, `structure.md` → `INTEGRASI-RISETSAHAM.md`.

## ⛔ 5 aturan emas
1. **TES dulu, klaim belakangan.**
   `PYTHONUTF8=1 .venv/bin/python -m unittest discover -s idx_alpha_screener -p "test_v7.py" -v`
   — wajib hijau SEBELUM bilang "selesai". Kalau ada yang merah, jangan diklaim beres.
2. **Ubah kebijakan sinyal? WAJIB backtest dulu** (`backtest_v7_teknikal.py`, 1 tahun
   data) + tunjukkan buktinya. Kebijakan saat ini hasil backtest — jangan diubah
   berdasarkan feeling.
3. **Jangan ubah kontrak output** (kolom CSV / `POST /riset/screener/api`) tanpa
   bilang user — app RisetSaham bergantung pada format itu.
4. **Rahasia tetap rahasia**: jangan hardcode kredensial; jangan commit `.env` atau `data/`.
5. **Bahasa Indonesia** untuk semua label/tampilan yang dibaca user.

## 🚀 Cara menjalankan
Lihat `tech.md` (perintah lengkap: tes, uji scan kecil, scan mandiri, backtest).
Instalasi: buat venv + `pip install -r idx_alpha_screener/requirements.txt`.

## 📦 Catatan penting soal paket ini
- Folder ini adalah salinan kerja dari server Linux (`~/screener`). Scan penuh
  butuh sesi Stockbit + modul RisetSaham (tidak ada di paket ini) → di mesin lain
  gunakan **cache lokal + mode uji**; scan live penuh dijalankan di server.
- Data pasar = pemakaian **non-komersial**. Jangan publikasikan.

## 💡 Kalau mau mulai
Tanya user apa yang mau dikerjakan, lalu:
1. Baca file terkait (peta di `structure.md`).
2. Buat perubahan kecil → jalankan tes → tunjukkan hasil tes.
3. Commit kecil dengan pesan jelas (branch aktif: `feat/integrasi-risetsaham`).
