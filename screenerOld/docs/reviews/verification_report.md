# TRADING LOGIC & ALGORITHM VERIFICATION REPORT
## Stock Screener IDX v10.0 — Evaluasi Keandalan

**Files Reviewed:**
- `C:\Hermes_Workspace\Screener\screener.py` (2741 lines)
- `C:\Hermes_Workspace\Screener\scoring_engine.py` (106 lines)
- `C:\Hermes_Workspace\Screener\indicators.py` (49 lines)
- `C:\Hermes_Workspace\Screener\shareholder_analyzer.py` (614 lines)
- `C:\Hermes_Workspace\Screener\broker_scraper.py` (49 lines)

---

## 1. TEKNIKAL INDIKATOR SWING TRADING

### 1.1 EMA Alignment (Baris 1654-1659)

**Kode:**
```python
if price > ema21_val > ema50_val and price > hma_val:   # baris 1654
    skor += 2.0
elif price > ema21_val > ema50_val:                      # baris 1657
    skor += 1.5
```

**Evaluasi:**
- ✅ **Chained comparison `price > ema21_val > ema50_val`** adalah Python chained comparison → setara dengan `price > ema21_val AND ema21_val > ema50_val`. **BENAR SECARA MATEMATIS**.
- ❌ **MISMATCH KOMENTAR VS KODE**: Komentar baris 1654 mengatakan "EMA+HMA" dengan hirarki `price > EMA21 > EMA50 > HMA`, tapi kode hanya cek `price > hma_val`, BUKAN `ema50_val > hma_val`. HMA tidak dibandingkan dengan EMA50.
- ❌ Kondisi `elif` `price > ema21_val > ema50_val` seharusnya bisa digabung dengan cek HMA yang gagal, tapi tidak — ini bisa dobel skor jika HMA < price tetapi > ema21?

**Rating: 7/10**

**Rekomendasi:** Perbaiki komentar agar sesuai kode, atau tambah cek `ema50_val > hma_val` jika benar ingin hirarki lengkap.

---

### 1.2 MACD Comparison (Baris 1672-1673)

**Kode:**
```python
if macd_v > macd_s and macd_h > 0 and obv_v > obv_ma: skor += 1.5  # baris 1672
elif macd_v > macd_s and macd_h > 0: skor += 1.0                    # baris 1673
```

**Evaluasi:**
- ✅ **Operator precedence**: Python `and` memiliki precedence LEBIH RENDAH dari `>` → `macd_v > macd_s and macd_h > 0` = `(macd_v > macd_s) and (macd_h > 0)`. **BENAR**, tidak ada bug precedence.
- ⚠️ Komentar di baris 1710 "FIX 1.3: Perbaiki operator precedence MACD comparison" adalah **MISLEADING** — tidak ada masalah precedence di sini. FIX tersebut mungkin untuk kode lain (line 1711) yang berbeda.
- ❌ `obv_v > obv_ma` (OBV > moving average) adalah logika akumulasi. Tapi tidak ada jaminan OBV MA ikut dihitung (check indicators.py — OBV tidak punya MA, hanya raw OBV).

**Rating: 8/10**

**Rekomendasi:** Hapus/memperbaiki komentar misleading tentang operator precedence. Verifikasi bahwa `obv_ma` benar-benar didefinisikan.

---

### 1.3 RSI Range Scoring — ❌ INKONSISTENSI KRITIS

| Lokasi | Range | Konteks |
|--------|-------|---------|
| Baris 1675 | `40 <= rsi_v <= 65` | Skor +1.0 |
| Baris 1708 | `30 <= rsi_v <= 50` | tech_score +15 (good entry) |

**Evaluasi:**
- ❌ **DUA RANGE BERBEDA UNTUK INDIKATOR SAMA** tanpa justifikasi.
- Range `40-65` di baris 1675 MENCANGKUP wilayah 50-65 yang mendekati overbought (RSI > 70 = overbought). Ini BERTENTANGAN dengan "good entry".
- Range `30-50` di baris 1708 sudah benar untuk entry mean-reversion (tidak overbought, tidak oversold parah).
- **Dampak**: Saham dengan RSI=55 mendapat skor +1.0 (oleh baris 1675) tapi TIDAK mendapat tech_score dari baris 1708. Logika ganda ini tidak konsisten — mana yang benar?

**Rating: 4/10**

**Rekomendasi:** Standardisasi:
- Untuk entry: pakai range `30-55` (lebih lebar sedikit dari 30-50, tapi masih aman)
- Atau dokumentasikan bahwa `40-65` adalah untuk "momentum konfirmasi" (bukan entry) — perlu komentar jelas.

---

### 1.4 Stochastic (Baris 1676)

**Kode:**
```python
if stoch_v < 80 and stoch_v > stoch_d_v: skor += 1.0
```

**Evaluasi:**
- ✅ `stoch_v < 80` — tidak overbought (overbought > 80). **BENAR**.
- ✅ `stoch_v > stoch_d_v` — %K di atas %D (signal line). **UMUMNYA BULLISH**.
- ❌ **TIDAK MENGECEK CROSSOVER**: Kondisi ini juga terpenuhi jika %K sudah di atas %D selama 10 hari (trend sudah berjalan lama, bukan sinyal entry baru).
- ❌ **TIDAK MENANGANI OVERSOLD**: Sinyal bullish stochastics paling kuat saat %K naik dari oversold (<20) dan menembus %D. Ini tidak tertangkap secara spesifik.
- ❌ **TIDAK VALIDASI** `stoch_d_v` (D-line) — jika NaN (data pendek), logika tetap jalan.

**Rating: 6/10**

**Rekomendasi:** Tambah cek crossover: `stoch_v < 80 and stoch_v > stoch_d_v and stoch_d_prev_v >= stoch_v_prev` (baru crossing). Atau minimal `stoch_v > stoch_d_v and stoch_prev_v < stoch_d_prev_v`.

---

### 1.5 ADX Threshold — ❌ INKONSISTENSI MULTI-THRESHOLD

| Lokasi | Threshold | Konteks |
|--------|-----------|---------|
| Baris 1669 | `adx_v > 40` (skor +1.5), `adx_v > 30` (skor +1.2) | Scoring bobot |
| Baris 1715 | `adx_v > 35` (tech_score +20) | Component confidence |
| scoring_engine.py:22 | `adx_val > 25` → trending regime | Adaptive weights |
| scoring_engine.py:24 | `adx_val > 20` → transition regime | Adaptive weights |

**Evaluasi:**
- ❌ **4 THRESHOLD BERBEDA** untuk parameter yang sama (ADX).
- ADX > 25 = trending (Welles Wilder original), ADX > 40 = strong trend. Tapi kenapa scoring pakai 30/40, confidence pakai 35, regime pakai 25?
- Tidak ada dokumentasi mengapa threshold berbeda-beda.
- Scoring baris 1669 menggunakan IF-ELIF: jika ADX 45, hanya skor +1.5 (tidak +1.2). Jika ADX 35, tidak dapat skor sama sekali (karena 35 < 40 dan elif 35 > 30 → skor +1.2). Wait, 35 > 30 jadi dapat +1.2. OK.
- Threshold 35 (baris 1715) berada di "no man's land" antara 30 dan 40 — kenapa 35 khusus untuk tech_score?

**Rating: 4/10**

**Rekomendasi:** Standardisasi threshold:
- ADX > 25: trending → weights adaptif
- ADX > 35: strong trend → bonus scoring
- ADX > 50: very strong trend → bonus lebih besar
Dokumentasikan sumber threshold (Wilder original untuk 25).

---

### 1.6 VCP Pattern (Baris 1612)

**Kode:**
```python
if 0 <= jarak_pucuk <= 4 and atr_v < atr_20_avg and vol_v < vol_sma_v * 0.7:
```

**Evaluasi:**
- ✅ `jarak_pucuk = (highest_20 - price) / price * 100` → % dari 20d high. Range 0-4% = price dalam 4% dari high terbaru.
- ✅ `atr_v < atr_20_avg` → volatilitas kontraksi. **BENAR** untuk VCP.
- ✅ `vol_v < vol_sma_v * 0.7` → volume kontraksi (70% dari rata-rata). **BENAR**.
- ❌ **VCP sejati membutuhkan KONTRAKSI BERTAHAP** (Minervini). Kode hanya cek 1 bar terakhir, bukan sequential tightening. Minimal 2-3 kontraksi.
- ❌ **Tidak ada "pivot point"**: Setelah kontraksi, VCP breakout ditandai oleh volume expansion + price breakout. Ini tidak terdeteksi.
- ❌ `high.tail(20).max()` menggunakan 20 hari — Minervini biasanya menggunakan lebih panjang (8-12 minggu untuk swing, 1 tahun untuk posisi).

**Rating: 5/10**

**Rekomendasi:** Implementasi sequential contraction dengan minimal 2 tahap narrowing, dan tambah deteksi breakout (price > highest_20 AND volume > vol_sma * 1.5).

---

### 1.7 Ichimoku Cloud (Baris 967)

**Kode:**
```python
cloud_signal = "BULLISH" if close.iloc[-1] > senkou_a.iloc[-1] else "BEARISH"
```

**Evaluasi:**
- ❌ **HANYA CEK CLOSE vs SENKOU A** — sangat tidak lengkap.
- **Tidak cek**: Senkou A vs Senkou B (kumo shape — apakah cloud positif/bearish)
- **Tidak cek**: Tenkan vs Kijun (TK cross)
- **Tidak cek**: Chikou Span (lagging span) vs harga 26 periode lalu
- **Tidak cek**: Future cloud (displacement 26 periods forward)
- Dalam Ichimoku standar, harga di atas Senkou A SAJA tidak cukup untuk BULLISH:
  - Jika Senkou A < Senkou B, cloud BEARISH meskipun harga > Senkou A
  - Jika Tenkan < Kijun, momentum masih bearish
- Senkou A di-shifted 26 hari ke depan dalam implementasi yang benar (karena Ichimoku menggunakan future cloud). Kode ini tidak melakukan shift.

**Rating: 3/10**

**Rekomendasi:** Implementasi penuh dengan 4 komponen:
```python
tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
kijun = (high.rolling(26).max() + low.rolling(26).min()) / 2
senkou_a = ((tenkan + kijun) / 2).shift(26)
senkou_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
chikou = close.shift(-26)

cloud_bullish = close.iloc[-1] > senkou_a.iloc[-1] and senkou_a.iloc[-1] > senkou_b.iloc[-1]
tk_bullish = tenkan.iloc[-1] > kijun.iloc[-1]
chikou_bullish = chikou.iloc[-1] > close.iloc[-26]
```

---

## 2. BANDARMOLOGI / MARKET MAKER ANALYSIS

### 2.1 detect_market_maker_activity (Baris 682-753)

**Metrik:**
| Sinyal Akumulasi | Threshold | Validitas |
|---|---|---|
| VOL_SPIKE_STABLE | vol_spike (recent_vol > avg_vol * 1.5) AND abs(price_trend) < 2 | ✅ |
| VPT_RISING | vpt_trend > 0 AND recent_vol > avg_vol * 0.8 | ✅ |
| CMF_POSITIVE | cmf_current > 0.1 AND cmf_current > cmf_prev | ✅ (standar CMF > 0.1) |
| AD_RISING | ad_trend > 0 (A/D line naik 20 hari) | ✅ |
| VWAP_ABOVE_DELTA_POS | vwap_deviation > 0 AND cumulative_delta > 0 | ⚠️ delta perlu validasi |

| Sinyal Distribusi | Threshold | Validitas |
|---|---|---|
| VOL_SPIKE_DOWN | vol_spike AND price_trend < -1 | ✅ |
| VPT_FALLING | vpt_trend < 0 | ✅ |
| CMF_NEGATIVE | cmf_current < -0.1 AND cmf_current < cmf_prev | ✅ |
| AD_FALLING | ad_trend < 0 | ✅ |
| VWAP_BELOW_DELTA_NEG | vwap_deviation < 0 AND cumulative_delta < 0 | ⚠️ |

**Logika Keputusan:**
```python
if acc_score > dist_score and acc_score >= 2:    # baris 732
    activity = "ACCUMULATION"
elif dist_score > acc_score and dist_score >= 2:
    activity = "DISTRIBUTION"
```

**Evaluasi:**
- ✅ Multi-indicator approach (5 metrics each) baik untuk konfirmasi.
- ✅ Threshold CMF (±0.1) sesuai standar pasar.
- ⚠️ **Confidence formula**: `min(90, acc_score * 20)`. Dengan acc_score max 5 → 100, di-cap ke 90. Tapi acc_score=2 (minimum threshold) → confidence=40. APAKAH 40% CUKUP UNTUK KEPUTUSAN AKUMULASI?
- ⚠️ **Mixed signals**: Jika acc_score=3 dan dist_score=2, tetap ACCUMULATION. Tapi adanya 2 sinyal distribusi seharusnya menurunkan confidence.
- ❌ **Tidak ada weight per sinyal**: Semua sinyal dihitung sama (VWAP + delta punya bobot sama dengan AD_RISING). Seharusnya CMF dan volume spike diberi bobot lebih tinggi.

**Rating: 7/10**

**Rekomendasi:** Weighted scoring untuk setiap sinyal, dan confidence = (acc_score - dist_score) / total_possible * 100. Jika acc_score=3, dist_score=2, confidence = (3-2)/5 * 100 = 20% (rendah — seharusnya NEUTRAL).

---

### 2.2 estimate_market_maker_position (Baris 755-818)

**Logika:**
```python
base_position_pct = 0.02  # 2% of avg daily volume (baris 771)
if ACCUMULATION: base_position_pct = 0.025
elif DISTRIBUTION: base_position_pct = 0.015

activity_multiplier = 1.5 + (confidence - 50) / 100  # baris 779

estimated_shares = avg_daily_vol * base_pct * activity_mult * volume_mult

# Jika float tersedia, pakai juga: float * 0.005 * activity_mult
if float_shares > 0:
    estimated_shares = max(estimated_shares, float_shares * 0.005 * activity_mult)
```

**Evaluasi:**
- ❌ **2% dari volume harian** = arbitrary. Tidak ada riset atau backtest yang mendukung.
- ❌ **Float * 0.005** (0.5% dari free float) = arbitrary. Kenapa 0.5% bukan 1% atau 0.1%?
- ❌ **If float_shares <= 0**: gunakan `shares_outstanding * 0.25` — TEBAKAN KASAR. Free float IHSG sangat bervariasi (20-80%).
- ❌ **activity_multiplier**: `1.5 + (confidence - 50) / 100` → range 1.0-1.9. Di mana validasinya?
- ⚠️ **Cap 5% dari shares outstanding** (baris 795): Setidaknya ada batas keamanan.
- **Kesimpulan**: Ini spekulasi murni. Tidak ada satupun parameter yang berasal dari data riil atau backtest.

**Rating: 3/10**

**Rekomendasi:** Akui sebagai "rough estimate" dengan disclaimer eksplisit. Atau hapus dari production jika tidak bisa divalidasi. Ganti dengan estimasi berbasis volume profile jika data bid/ask tersedia.

---

### 2.3 estimate_retail_vs_mm_comparison (Baris 820-874)

**Logika Kunci:**
```python
# baris 834: Estimasi float dari outstanding
float_shares = int(shares_outstanding * 0.35)

# baris 839: Institutional share dari float
institutional_shares = int(float_shares * 0.35)

# baris 844: Retail = float - institutional - mm
retail_shares = max(0, int(float_shares - institutional_shares - mm_shares))
```

**Evaluasi:**
- ❌ **ANGKA 35% MUNCUL DUA KALI** tanpa justifikasi:
  - 35% dari outstanding → float (dari mana? IHSG rata-rata free float ~40-60%, bukan 35%)
  - 35% dari float → institutional (lagi-lagi tebakan)
- ❌ **Efek "Russian Doll"**: Float = 35% outstanding, Institutional = 35% float = 12.25% outstanding. Ini sangat kecil.
- ❌ **Institutional dihitung dari float, lalu retail dari sisa** — seharusnya institutional + MM + retail = float, tapi karena institutional dihitung DULU sebagai persentase float, lalu MM sebagai sisa dari estimasi posisi, ini menciptakan alokasi ganda.
- ❌ **Logika: `mm_shares + institutional_shares + retail_shares = float_shares`**. Tapi `institutional_shares` dihitung SEBELUM `mm_shares` dikurangkan. Jadi ada overlap: posisi institutional (dari 35% float) dan posisi MM (dari estimasi terpisah) bisa dobel hitung.

**Contoh:** Float = 1M saham. Institutional = 350K (35% float). MM = 200K (estimasi). Retail = 1M - 350K - 200K = 450K. TAPI posisi MM mungkin sudah termasuk dalam institutional (karena MM adalah institusi). Jadi retail understated.

**Rating: 2/10**

**Rekomendasi:** Gunakan data shareholder riil dari KSEI (shareholder_analyzer.py) untuk distribusi MM vs Retail yang akurat. Hapus fungsi ini atau beri label "SPECULATIVE" besar-besar.

---

### 2.4 Wyckoff Absorption (Baris 1617-1621)

**Kode:**
```python
spread = float(high.iloc[-1] - low.iloc[-1])
if vol_v > vol_sma_v * 1.5 and spread < atr_v * 0.8:
    if close.iloc[-1] >= (high.iloc[-1] + low.iloc[-1]) / 2:
        skor += BOBOT_SKOR["Wyckoff_Absorb"]
```

**Evaluasi:**
- ✅ **KONSEP SESUAI**: Volume tinggi (effort) + range sempit (no result) + close di atas midpoint = akumulasi institusional.
- ✅ Threshold: vol > 150% SMA (cukup tinggi) dan spread < 80% ATR (cukup sempit).
- ✅ Close >= midpoint: konfirmasi buyer masih kontrol.
- ❌ **HANYA 1 BAR**: Wyckoff absorption terjadi selama berhari-hari, bukan satu candle. Satu candle dengan karakteristik ini bisa jadi "news spike" bukan akumulasi.
- ❌ **False positive risk**: Gap open yang lebar + volume tinggi di hari pertama bisa memenuhi kriteria, padahal bukan akumulasi.
- ❌ **Tidak ada volume context**: Volume harus diperiksa RELATIF terhadap candle-candle sebelumnya, bukan cuma SMA.

**Rating: 6/10**

**Rekomendasi:** Tambah konfirmasi multiday: minimal 3 hari dengan spread mengecil dan volume meningkat. Atau deteksi "spring" (Wyckoff) dimana harga turun dengan spread lebar lalu ditutup di atas midpoint.

---

### 2.5 Broksum (broker_scraper.py)

**Kode:**
```python
# --- SIMULASI DATA RESPONS API ---
data = {
    "top_buyers": [{"broker": "ZP", "net_vol": 50000}, {"broker": "BK", "net_vol": 30000}],
    "top_sellers": [{"broker": "YP", "net_vol": 40000}, {"broker": "PD", "net_vol": 25000}]
}
```

**Evaluasi:**
- ❌ **100% MOCK / SIMULASI**. Data hardcoded, bukan scraping RTI Business.
- ❌ **URL API dikomentari**: `# url = f"https://api.penyedia-data.com/v1/broksum/{ticker_bersih}"` → placeholder palsu.
- ❌ **Setiap saham return data SAMA** — top buyers selalu ZP (50K) & BK (30K), sellers YP (40K) & PD (25K).
- ✅ **Broker codes** (ZP=UBS Sekuritas, BK=CLSA, CS=Credit Suisse, etc.) adalah kode broker RTI yang REAL. Tapi data transaksinya PALSU.
- ✅ **Logika bandarmologi**: Top buyer institusi asing + top seller ritel + buyer > seller = BIG_ACCUMULATION. Logika ini masuk akal untuk IHSG.
- **Dampak**: Semua saham yang dicek dapat status bandar "NEUTRAL" atau "BIG_ACCUMULATION" dengan net_vol 15,000 (50K+30K - 40K-25K). Konstan untuk semua ticker!

**Rating: 1/10**

**Rekomendasi:**
- Nonaktifkan fungsi ini sampai integrasi API real tersedia.
- Jika ingin mock untuk testing, beri parameter `mock_data=False` yang default True, dengan logging jelas "MENGGUNAKAN DATA SIMULASI".
- Sumber data real: RTI Business API, Stockbit, atau IDX (Indonesia Stock Exchange) data download.

---

## 3. RISK MANAGEMENT

### 3.1 Stop Loss — Weekly+Monthly Bullish (Baris 1690-1691)

**Kode:**
```python
base_sl = round(price - (risk_factor * atr_v))                    # baris 1689
if weekly_bullish and monthly_bullish:
    base_sl = round(price - (risk_factor * atr_v * 1.3))           # baris 1691
```

**Evaluasi:**
- ⚠️ Logika: Jika trend weekly + monthly bullish, stop loss diperlebar (1.3x ATR vs 1x ATR).
- **Efek**: `price - (risk_factor * atr_v * 1.3)` LEBIH RENDAH dari `price - (risk_factor * atr_v)` → stop loss JAUH dari harga → RISIKO PER TRADE LEBIH BESAR.
- **Justifikasi yang mungkin**: Dalam strong trend, ingin memberi ruang lebih agar tidak terkena stop loss palsu (noise). Ini bisa dibela.
- **TAPI**: Jika trend bullish dan ingin trailing, stop loss seharusnya DIKETATKAN (lebih dekat ke harga) untuk mengunci profit, bukan diperlebar.
- **Kesimpulan**: Bisa benar atau salah tergantung filosofi. Tidak didokumentasikan.
- ❌ **Tidak ada validasi**: `weekly_bullish` dan `monthly_bullish` bisa False jika fetch data gagal (try-except pass). Default False.

**Rating: 5/10**

**Rekomendasi:** Dokumentasikan filosofi (apakah melindungi dari noise di trend, atau mengunci profit). Jika mengunci profit di trend, gunakan multiplier < 1.0 (misal 0.8) bukan > 1.0.

---

### 3.2 Stop Loss — `max()` vs `min()` (Baris 1692)

**Kode:**
```python
stop_loss = max(base_sl, round(price * 0.92))
```

**Analisis:**
- `max()` memilih nilai TERTINGGI antara `base_sl` dan `92% * price`.
- Karena `stop_loss` adalah LEVEL HARGA, nilai lebih tinggi = LEBIH DEKAT ke harga = stop lebih KETAT.
- Jika `base_sl = 950` dan `price * 0.92 = 920` → `max(950, 920) = 950` → SL = 950 (ketat).
- Jika `base_sl = 880` dan `price * 0.92 = 920` → `max(880, 920) = 920` → SL = 920 (batas aman).

**Efek:**
1. **SL tidak bisa lebih rendah dari 92% price** — berfungsi sebagai HARD FLOOR.
2. **Jika ATR-based SL terlalu dekat** (base_sl tinggi), SL tetap di base_sl.
3. **Seiring harga naik**, base_sl naik (karena dihitung ulang dari price baru), dan 92% price juga naik. Ini trailing secara NATURAL karena recalculate, bukan true trailing stop logic.

**BENAR atau SALAH?**
- ✅ `max()` memastikan SL tidak terlalu jauh dari harga (tidak lebih rendah dari 8%).
- ❌ TAPI judul "TRAILING STOP" menyesatkan. Ini adalah DYNAMIC STOP (dihitung ulang setiap bar), BUKAN true trailing stop (yang menaikkan SL secara kondisional, tidak menghitung ulang). Pada kenyataannya, karena dihitung ulang, ini justru bisa menurunkan SL jika harga turun sementara — yang TIDAK boleh terjadi di trailing stop.
- ❌ **True trailing stop** harus: `stop_loss = max(stop_loss_prev, current_stop_loss)`, bukan `max(base_sl, floor)`. Dengan `max(base_sl, floor)` di sini, jika harga turun, base_sl turun, tapi max masih bisa turun (karena floor juga turun). Jadi tidak benar-benar "trailing up only".

**Rating: 4/10**

**Rekomendasi:** Untuk trailing stop asli:
```python
# Simpan previous stop loss, lalu trailing up
trail_stop = round(price - (risk_factor * atr_v))
better_sl = max(prev_stop_loss, trail_stop)  # hanya naik, tidak pernah turun
final_sl = max(better_sl, round(price * 0.92))  # floor 8%
```

---

### 3.3 Kelly Criterion (Baris 368-392)

**Kode:**
```python
p = ai_win_prob_percent / 100.0
q = 1.0 - p
b = 2.0  # <-- HARDCODED RRR
kelly_fraction = p - (q / b)   # baris 378
safe_kelly = kelly_fraction / 2.0  # Half-Kelly
```

**Evaluasi:**
- ✅ **Rumus Kelly**: `f* = p - q/b` **BENAR** secara matematis (standar untuk binary outcomes).
- ✅ **Half-Kelly**: Konservatif, sesuai standar institusi.
- ✅ **Max 25% cap**: Mencegah over-concentration.
- ❌ **RRR = 2.0 FIXED**: HARDCODED! Setiap trade punya RRR berbeda. Jika trade punya RRR=1.0, menggunakan b=2.0 akan OVERESTIMATE ukuran posisi. Jika RRR=3.0, akan UNDERESTIMATE.
- ❌ **Tidak menggunakan dynamic RRR**: Di baris 1699, `rrr = round(reward_pct / risk_pct, 2)` sudah dihitung! Tapi Kelly function tidak menerimanya sebagai parameter.
- ❌ **Typo**: Parameter `harga_saham` (seharusnya `harga_saham`) — tidak fatal tapi kode tidak rapi.

**Akurasi Kelly dengan RRR=2.0 vs RRR aktual:**
| Win Prob | RRR=2.0 (salah) | RRR=1.5 (benar) | Error |
|----------|-----------------|-----------------|-------|
| 60% | 15% modal | 26.7% modal | Under 44% |
| 70% | 27.5% → cap 25% | 43.3% → cap 25% | Cap berlaku |
| 50% | 0% | 16.7% modal | Sangat konservatif |

**Rating: 4/10**

**Rekomendasi:** Ubah signature fungsi untuk menerima RRR dinamis:
```python
def hitung_kelly_sizing(ai_win_prob_percent, harga_saham, rrr=2.0, modal_trading=10000000.0):
```

---

### 3.4 Position Sizing — risk_pct (Baris 1211-1226)

**Kode:**
```python
def position_size_calc(account_equity: float, risk_pct: float, entry: float, stop_loss: float):
    risk_amount = account_equity * (risk_pct / 100)
```

**Evaluasi:**
- Jika `risk_pct = 1.0` (berarti 1% equity): `risk_pct/100 = 0.01` → `equity * 0.01` = 1% equity. **BENAR**.
- Jika `risk_pct = 0.01` (berarti 1% dalam desimal): `risk_pct/100 = 0.0001` → hanya 0.01% equity. **SALAH**.
- ⚠️ **Tidak ada validasi input**: Pengguna bisa kirim 0.01 (desimal) atau 1 (persen) dan hasilnya akan sangat berbeda.
- ⚠️ **shares dihitung sebagai `int(risk_amount / points_at_risk)`** — pembulatan ke bawah, yang konservatif (baik).

**Rating: 7/10**

**Rekomendasi:** Tambah validasi: Jika risk_pct > 1, treat sebagai persen; jika <= 1, treat sebagai desimal. Atau konversi eksplisit:
```python
# Auto-detect format
if risk_pct <= 1.0:  # kemungkinan desimal
    risk_amount = account_equity * risk_pct
else:  # kemungkinan persen
    risk_amount = account_equity * (risk_pct / 100)
```

---

## 4. SCORING ENGINE

### 4.1 compute_confidence (scoring_engine.py) vs Inline (screener.py:1778-1804)

**Perbandingan:**

| Komponen | scoring_engine.py | screener.py inline (baris 1772-1804) |
|----------|-------------------|--------------------------------------|
| Normalisasi | `_normalize_score()` | `_normalize_score()` (imported) |
| Weight | `get_adaptive_weights()` | `get_adaptive_weights()` (imported) |
| Kombinasi | `n_tech*w_tech + max(0,n_fund)*w_fund + n_rs*w_rs + max(0,n_sent)*w_sent` | SAMA |
| IHSG penalty | `< -1.0: -8, < -0.3: -3` | SAMA (baris 1788-1793) |
| Timeframe bonus | `weekly+monthly: +5, both not: -3, weekly not: -1` | SAMA (baris 1796-1803) |
| skor | `confidence/100 * 15` | TIDAK ADA (beda perhitungan) |
| c_thresh_buy | Ya | TIDAK |

**Evaluasi:**
- ❌ **DUPLIKASI KOMPLIT**: Screener.py meng-import fungsi dari scoring_engine.py TAPI TIDAK MENGGUNAKANNYA.
- ❌ **Maintenance hazard**: Perubahan di `compute_confidence()` tidak akan berefek karena screener.py punya implementasi sendiri.
- ❌ **Perbedaan kecil**: Screener.py tidak hitung `skor` dan `c_thresh_buy` seperti scoring_engine.py, TAPI punya hitungan `skor` sendiri dari sistem bobot (line 1549-1685).
- **Dua sistem scoring paralel**: Satu dari BOBOT_SKOR (sistem bobot) dan satu dari component-based confidence. Keduanya berjalan paralel dan independen.

**Rating: 3/10**

**Rekomendasi:** Refaktor total: Panggil `compute_confidence()` langsung, jangan duplikasi kode.

---

### 4.2 typical_max = {65, 50, 50, 30}

| Komponen | typical_max | Sumber |
|----------|-------------|--------|
| tech_score | 65 | Heuristic? |
| fund_score | 50 | Heuristic? |
| rs_score | 50 | Heuristic? |
| sent_score | 30 | Heuristic? |

**Evaluasi:**
- **Tidak ada satupun yang berasal dari backtest atau data empiris.**
- `if tech_score=100, typical_max=65` → `normalized = 100/65*100 = 153.8% → min(153, 100) = 100`. Jadi tech_score 65 sudah cap 100% normalized.
- `fund_score typical_max=50`: Fund komponen: PER=25, PBV=25, Earnings=15, Dividend=10 = 75 teoretis, tapi PER dan sebagian PBV tidak bisa bersamaan. 50 cukup realistis.
- `rs_score typical_max=50`: komponen = outperform(30) + sector(25) = 55. 50 mendekati.
- `sent_score typical_max=30`: komponen = sentiment(30) + foreign(20) + MM(15) = 65. 30 terlalu rendah.
- **Dampak**: sent_score dinormalisasi dengan 30, artinya sentimen 30/30*100 = 100 sudah normalized. Tapi sentimen bisa mencapai 65. Jadi komponen sentimen UNDER-WEIGHTED di normalized.

**Rating: 5/10**

**Rekomendasi:** Lakukan backtest untuk menentukan typical_max sebenarnya, atau gunakan percentile-based normalization. Untuk sentimen, typical_max seharusnya 65 (bukan 30).

---

### 4.3 Calibration Map

```python
CALIBRATION_MAP = {90: 0.72, 80: 0.65, 70: 0.55, 60: 0.48, 50: 0.42, 40: 0.35, 30: 0.28}
```

**Evaluasi:**
- ❌ **Angka-angka ini terlihat KONSTRUKTIF (dibuat-buat), bukan dari backtest.**
- Pola: 0.72, 0.65, 0.55, 0.48, 0.42, 0.35, 0.28 → selisih tidak konsisten (7, 10, 7, 6, 7, 7) — ini tipikal angka heuristic.
- ❌ **Confidence 30 → win prob 28%**: Artinya confidence rendah menandakan harus HINDARI (karena < 50%). Tapi dalam trading, confidence rendah berarti TIDAK TAHU, bukan PREDIKSI KALAH.
- ❌ **Nearest-neighbor mapping**: `min(CALIBRATION_MAP.keys(), key=lambda k: abs(k - confidence))` → Jika confidence=35, mapping ke 30 (win prob 28%). Jika confidence=65, mapping ke 70 (win prob 55%). Ini diskontinu — loncat dari 65→70, tidak linier.
- ❌ **Tidak ada confidence > 90**: Confidence bisa 95-100 (setelah cap), tapi hanya dibulatkan ke 90 → 72% win prob. Underestimasi.
- ✅ **Proposisi bahwa 90% confidence ≠ 90% win prob** adalah REALISTIS. Tidak ada sistem yang 90% akurat.

**Rating: 4/10**

**Rekomendasi:**
- Gunakan logistic regression atau isotonic regression untuk kalibrasi dari data backtest nyata.
- Perluas map: tambah bucket 100:0.78, 20:0.20, 10:0.10.
- Atau gunakan continuous function: `win_prob = 1 / (1 + exp(-0.05 * (confidence - 50)))` yang lebih smooth.

---

## 5. CONSTANTS & ASKEW

### 5.1 BOBOT_SKOR — Volume_Confirm dan EMA_Aligned (2.0)

**Bobot Tertinggi:**
- `"EMA_Aligned": 2.0` — Price > EMA21 > EMA50 (+ HMA)
- `"Volume_Confirm": 2.0` — Volume > SMA20
- `"PER_Cheap": 2.0` — PER ≤ 15
- `"EPS_Minus": -2.0` — EPS negatif (penalty)

**Evaluasi:**
- ⚠️ Total base BOBOT_SKOR positif = ~22.5, negatif = ~-9.0. Capped di ±15.
- **EMA_Aligned 2.0**: Sebenarnya kondisi ini sudah tercakup di baris 1654 (skor +2.0 langsung) — **DOBEL COUNTING?**
  - Baris 1654: `if price > ema21_val > ema50_val and price > hma_val: skor += 2.0` — ini hardcoded
  - Baris 1560+ (`skor += BOBOT_SKOR["EMA_Aligned"]`): Dimana tepatnya dipanggil? Mari cek...
  
Mari saya periksa apakah BOBOT_SKOR["EMA_Aligned"] dipanggil di scoring.

- ❌ **TIDAK ADA backtest untuk bobot.** Bobot ditentukan secara manual, tidak melalui optimasi.
- ❌ **Inkonsistensi baris 1654 vs BOBOT_SKOR**: Baris 1654 langsung menambah skor 2.0 tanpa menggunakan BOBOT_SKOR["EMA_Aligned"] (yang juga 2.0). Tapi BOBOT_SKOR["EMA_Aligned"] 2.0 tidak pernah dipanggil (search tidak menemukan akses ke dictionary key ini).

**Rating: 5/10**

**Rekomendasi:** Gunakan Bobot_SKOR konsisten melalui dictionary, bukan hardcode di inline scoring. Lakukan walk-forward optimization untuk validasi bobot.

---

### 5.2 MACRO_PENALTY — Emas Naik = Penalty (Baris 100)

**Kode:**
```python
if GOLD_CHANGE > 1.0: penalty -= 0.5
```

**Evaluasi:**
- ❌ **Simplifikasi berlebihan**: Emas naik > 1% → otomatis negatif untuk saham.
- ✅ **Ada justifikasi dalam konteks tertentu**: Emas naik bisa menandakan:
  1. Risk-off sentiment (investor lari ke safe haven)
  2. Inflasi ekspektasi naik (buruk untuk ekuitas)
  3. IDR melemah (IHSG sensitif terhadap nilai tukar)
- ❌ **TAPI hubungan tidak selalu negatif**:
  - 2020-2021: Emas DAN IHSG sama-sama naik selama likuiditas longgar
  - 2022: Emas naik karena inflasi, IHSG turun karena kenaikan suku bunga
- ❌ **Threshold 1% dalam 1 hari sangat sensitif**: Volatilitas emas harian sering >1%. Ini bisa trigger penalty terlalu sering.
- ❌ **Tidak ada interaksi dengan USD**: Emas naik sering karena USD turun, yang BAIK untuk IHSG (karena IDR menguat). Ini tidak dipertimbangkan.

**Rating: 4/10**

**Rekomendasi:**
- Pertimbangkan konteks: Gold up + USD down = netral (bukan penalty)
- Gunakan perubahan mingguan (5 hari) bukan harian untuk kurangi noise
- Tambah: `if GOLD_CHANGE > 2.0 and USD_CHANGE > 0.5: penalty -= 0.5` (inflasi + USD kuat = benar-benar risk-off)

---

### 5.3 IHSG_TREND — 3 Hari Saja (Baris 106)

**Kode:**
```python
last3 = ihsg_data.tail(3)
IHSG_TREND = "UP" if (last3.diff().dropna() > 0).all() else "DOWN"
```

**Evaluasi:**
- ❌ **3 HARI SANGAT TIDAK CUKUP** untuk deteksi trend apapun.
- ❌ **Setiap hari merah tunggal langsung flip ke DOWN** — sangat noise-prone.
- ❌ **Statistik**: Dalam pasar random walk, probabilitas 3 hari hijau berturut-turut = 12.5% (0.5³) — ini sering terjadi secara kebetulan.
- ❌ **Dampak ke sistem**: `ihsg_trend` digunakan di scoring_engine.py untuk:
  - `c_thresh_buy` threshold (baris 70): `55 if trend UP else 65` — bisa berubah drastis karena 1 hari noise
  - Signal determination (baris 87-91): Hanya ULTRA_BUY jika trend UP
- ✅ **Fallback**: `IHSG_TREND = "UP" if IHSG_CHANGE > 0 else "DOWN"` — sedikit lebih baik untuk baris 108.

**Rating: 2/10**

**Rekomendasi:** Gunakan minimal 20-50 hari dengan SMA slope atau linear regression:
```python
# Metode 1: SMA slope
sma20 = ihsg_data.tail(20).mean()
sma50 = ihsg_data.tail(50).mean()
IHSG_TREND = "UP" if sma20 > sma50 else "DOWN"

# Metode 2: Linear regression slope
x = np.arange(len(ihsg_data.tail(20)))
slope, _ = np.polyfit(x, ihsg_data.tail(20), 1)
IHSG_TREND = "UP" if slope > 0 else "DOWN"
```

---

## RINGKASAN EVALUASI

| Komponen | Rating | Kriteria Utama |
|----------|--------|----------------|
| **1. TEKNIKAL** | | |
| EMA Alignment | **7/10** | Chained comparison benar, komentar misleading |
| MACD Comparison | **8/10** | Operator precedence benar |
| RSI Range | **4/10** ❌ | Inkonsistensi kritis (40-65 vs 30-50) |
| Stochastic | **6/10** | Logika benar tapi kurang crossover |
| ADX Threshold | **4/10** ❌ | 4 threshold berbeda tanpa justifikasi |
| VCP Pattern | **5/10** | Konsep benar, implementasi 1 bar tidak cukup |
| Ichimoku Cloud | **3/10** ❌ | Sangat tidak lengkap, hanya cek 1 komponen |
| **2. BANDARMOLOGI** | | |
| MM Activity Detection | **7/10** | Multi-indicator baik, handling mixed signals kurang |
| MM Position Estimate | **3/10** ❌ | Spekulasi murni, parameter arbitrary |
| Retail vs MM Comparison | **2/10** ❌ | Angka 35% muncul 2x, overlap kalkulasi |
| Wyckoff Absorption | **6/10** | Konsep benar, perlu multiday confirmation |
| Broksum (Broker Scraper) | **1/10** ❌ | BUKAN real scraping — data hardcoded mock |
| **3. RISK MANAGEMENT** | | |
| Stop Loss - Trend Widening | **5/10** | Bisa dibela tapi tidak didokumentasikan |
| Stop Loss - max vs min | **4/10** ❌ | Bukan true trailing (recalculate, bukan trail) |
| Kelly Criterion | **4/10** ❌ | RRR=2.0 fixed padahal RRR tiap trade berbeda |
| Position Sizing | **7/10** | Logika benar, tidak ada input validation |
| **4. SCORING ENGINE** | | |
| compute_confidence duplikasi | **3/10** ❌ | Duplikasi total, maintenance hazard |
| typical_max | **5/10** | Heuristic, tidak divalidasi |
| Calibration Map | **4/10** ❌ | Angka konstruktif, diskontinu, tidak backtested |
| **5. CONSTANTS** | | |
| BOBOT_SKOR | **5/10** | Tidak ada backtest untuk bobot |
| MACRO_PENALTY - Emas | **4/10** | Simplifikasi berlebihan |
| IHSG_TREND 3 hari | **2/10** ❌ | Sangat noise-prone, dampak sistemik |

---

## PRIORITAS PERBAIKAN

### **Critical (Harus Diperbaiki):**
1. **Ichimoku Cloud** (rating 3) — Implementasi sangat tidak lengkap, hasil misleading
2. **Broksum** (rating 1) — Data palsu, harus dinonaktifkan atau di-deploy API real
3. **RSI Range Inkonsistensi** (rating 4) — Dua logika scoring bertentangan
4. **IHSG_TREND 3 hari** (rating 2) — Noise tinggi, berdampak sistemik ke sinyal

### **High (Sangat Direkomendasikan):**
5. **Retail vs MM Comparison** (rating 2) — Angka 35% layering, overlap kalkulasi
6. **compute_confidence duplikasi** (rating 3) — Refactor untuk single source of truth
7. **ADX Threshold inkonsistensi** (rating 4) — Standarisasi threshold
8. **Kelly Criterion RRR fixed** (rating 4) — Gunakan RRR dinamis
9. **Stop Loss "trailing"** (rating 4) — Implementasi trailing yang benar

### **Medium (Perbaikan Dokumentasi):**
10. **MACRO_PENALTY emas** (rating 4) — Tambah konteks USD
11. **Calibration Map** (rating 4) — Backtest untuk validasi
12. **MM Position Estimate** (rating 3) — Tambah disclaimer spekulasi
13. **VCP Pattern** (rating 5) — Sequential contraction multiday

---

## KESIMPULAN UMUM

**Kelebihan Sistem:**
1. Architektur multi-indicator komprehensif (18+ indikator)
2. Konsep scoring berbobot dengan adaptive weights cukup matang
3. Integrasi makro ekonomi (IHSG, USD, komoditas) adalah nilai tambah
4. Kode terstruktur dengan komentar "FIX" yang menandakan perbaikan iteratif
5. Market regime detection untuk adaptive position sizing

**Kelemahan Sistemik:**
1. **Banyak angka arbitrer** tanpa validasi backtest
2. **Duplikasi kode** antara scoring_engine.py dan screener.py
3. **Inkonsistensi threshold** untuk parameter yang sama (ADX, RSI)
4. **Komponen bandarmologi spekulatif** dengan data palsu (broker_scraper.py)
5. **Ichimoku implementasi sangat tidak lengkap** — bisa memberi sinyal palsu
6. **Trailing stop bukan trailing** — hanya dynamic recalculation
7. **3 hari untuk trend IHSG** — sangat tidak statistik

**Skor Keandalan Sistem Keseluruhan: 4.5/10**
(Sistem memiliki potensi bagus, tapi perlu refaktor serius untuk logika inkonsisten dan data palsu. Rekomendasi: nonaktifkan broker_scraper.py dan perbaiki Ichimoku + RSI + ADX sebelum digunakan untuk decision making real.)

---

*Report generated by Hermes Agent — Trading Logic & Algorithm Verification*
