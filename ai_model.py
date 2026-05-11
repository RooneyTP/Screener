"""
=============================================================================
  MARKET AI ENGINE v3.1 - Dual Brain Architecture  [FIXED]
  Mendukung Scalping (1-Minute) dan Swing Trading (End-of-Day)
  Algoritma Dasar: Random Forest / Mixture of Experts (MoE) Proxy

  CHANGELOG v3.1:
  - [FIX #1] Thread-safety: singleton _active_brains kini dilindungi threading.Lock
  - [FIX #2] StandardScaler disimpan bersama model agar prediksi konsisten
  - [FIX #3] train_model() sekarang mencetak accuracy, precision, recall, F1 + classification_report
  - [FIX #4] Validasi feature count lebih ketat dengan pesan error yang jelas
  - [FIX #5] Versi model disimpan ke metadata agar deteksi stale model otomatis
=============================================================================
"""

import os
import numpy as np
import joblib
import warnings
import threading
import logging

from datetime import datetime

warnings.filterwarnings("ignore", category=UserWarning)
logger = logging.getLogger(__name__)

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, classification_report
    )
except ImportError:
    print("⏳ Menginstal scikit-learn untuk Machine Learning...")
    os.system("pip install scikit-learn -q")
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.neural_network import MLPClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import train_test_split, cross_val_score
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, classification_report
    )

# ─── Konstanta ────────────────────────────────────────────────────────────────
MODEL_DIR = "models_ai"
os.makedirs(MODEL_DIR, exist_ok=True)

# Urutan fitur yang WAJIB dipatuhi oleh caller
# Phase-2: Diperluas dari 11 → 14 fitur (tambah rsi_vol_interaction, rolling_vol_20, sector_corr)
# Perubahan urutan di sini HARUS diikuti perubahan di screener.py & train_swing.py
FEATURE_NAMES = [
    "rsi", "adx", "vol_strength", "bb_width", "rrr",
    "mm_confidence", "mm_vs_retail_ratio",
    "ihsg_change", "usd_change", "rsi_1d", "macd_1d",
    # Phase-2 NEW features ──────────────────────────
    "rsi_vol_interaction",   # RSI × volume (momentum quality)
    "rolling_vol_20",        # 20-day realised volatility
    "sector_corr",           # rolling correlation with IHSG
]
N_FEATURES = len(FEATURE_NAMES)  # 14

# Versi model — naikkan setiap kali FEATURE_NAMES berubah agar model lama ditolak
MODEL_VERSION = "3.2"  # bumped from 3.1 because N_FEATURES changed


class MarketAI:
    def __init__(self, model_type: str = "scalping"):
        """
        Inisialisasi Otak AI.
        model_type: "scalping" atau "swing"
        """
        self.model_type = model_type.lower()

        # Penentuan path file model dan scaler
        prefix = "swing" if self.model_type == "swing" else "scalper"
        self.model_path  = os.path.join(MODEL_DIR, f"ai_{prefix}_brain.joblib")
        self.scaler_path = os.path.join(MODEL_DIR, f"ai_{prefix}_scaler.joblib")
        self.meta_path   = os.path.join(MODEL_DIR, f"ai_{prefix}_meta.joblib")

        self.model  = self._load_model()
        self.scaler = self._load_scaler()
        self.is_trained = hasattr(self.model, "classes_")

    # ──────────────────────────────────────────────────────────────────────────
    # Private Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _load_model(self):
        """Muat model dari disk jika ada dan versinya cocok; buat baru jika tidak."""
        if os.path.exists(self.model_path):
            try:
                # [FIX #5] Cek versi metadata sebelum muat model
                if os.path.exists(self.meta_path):
                    meta = joblib.load(self.meta_path)
                    saved_version = meta.get("model_version", "0")
                    saved_n_features = meta.get("n_features", 0)
                    if saved_version != MODEL_VERSION or saved_n_features != N_FEATURES:
                        logger.warning(
                            f"[AI] Model lama (v{saved_version}, {saved_n_features} fitur) "
                            f"tidak kompatibel dengan v{MODEL_VERSION} ({N_FEATURES} fitur). "
                            f"Membuat otak baru..."
                        )
                        return self._build_fresh_model()
                return joblib.load(self.model_path)
            except Exception as e:
                logger.warning(f"[AI] Gagal memuat {self.model_path}: {e}. Membuat otak baru...")
        return self._build_fresh_model()

    def _load_scaler(self):
        """Muat scaler dari disk jika ada."""
        if os.path.exists(self.scaler_path):
            try:
                return joblib.load(self.scaler_path)
            except Exception as e:
                logger.warning(f"[AI] Gagal memuat scaler: {e}. Scaler baru dibuat saat training.")
        return StandardScaler()

    def _build_fresh_model(self):
        """Buat Random Forest baru (Mixture-of-Experts proxy)."""
        return RandomForestClassifier(
            n_estimators=200,       # Lebih banyak pohon → lebih stabil
            max_depth=10,           # Dikurangi dari 12 → kurangi overfitting
            min_samples_split=10,   # Naikkan → regulasi lebih kuat
            min_samples_leaf=5,     # Guard minimum samples per leaf
            max_features="sqrt",    # Standard untuk klasifikasi
            random_state=42,
            class_weight="balanced",
            n_jobs=-1               # Gunakan semua core CPU
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def train_model(self, X, y):
        """
        Melatih AI dengan dataset historis.
        Phase-2: 5-fold CV dengan roc_auc scoring + F1; print mean ± std.
        Mencetak metrik evaluasi lengkap (accuracy, F1, precision, recall).
        """
        if len(X) < 100:
            logger.warning(
                "[AI TRAINING] Data terlalu sedikit (%d baris). Minimal 100 untuk training andal.", len(X)
            )
            return

        logger.info("[AI TRAINING] Memulai pelatihan otak %s...", self.model_type.upper())
        logger.info("   Dataset: %d baris, %d fitur", len(X), X.shape[1])

        # [FIX #3] Train/test split untuk evaluasi yang jujur
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        # [FIX #2] Fit scaler HANYA pada training data (tidak bocor ke test)
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled  = self.scaler.transform(X_test)

        # Proses Fit
        self.model = self._build_fresh_model()
        self.model.fit(X_train_scaled, y_train)

        # ── Evaluasi Performa ────────────────────────────────────────────────
        y_pred = self.model.predict(X_test_scaled)
        acc  = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec  = recall_score(y_test, y_pred, zero_division=0)
        f1   = f1_score(y_test, y_pred, zero_division=0)

        # Phase-2: 5-fold CV with BOTH f1 AND roc_auc scoring
        # roc_auc mendeteksi overfitting lebih baik dari F1 pada data imbalanced
        cv_f1 = cross_val_score(
            self._build_fresh_model(), X_train_scaled, y_train,
            cv=5, scoring="f1", n_jobs=-1
        )
        cv_auc = cross_val_score(
            self._build_fresh_model(), X_train_scaled, y_train,
            cv=5, scoring="roc_auc", n_jobs=-1
        )

        logger.info("   ── HASIL EVALUASI MODEL (%s) ──", self.model_type.upper())
        logger.info("   Accuracy      : %.2f%%", acc * 100)
        logger.info("   Precision     : %.2f%%", prec * 100)
        logger.info("   Recall        : %.2f%%", rec * 100)
        logger.info("   F1-Score      : %.2f%%", f1 * 100)
        # Phase-2: print CV roc_auc mean ± std
        logger.info("   CV F1  (5-fold): %.2f%% ± %.2f%%", cv_f1.mean() * 100, cv_f1.std() * 100)
        logger.info("   CV AUC (5-fold): %.4f ± %.4f  ← overfitting check", cv_auc.mean(), cv_auc.std())

        # Overfitting warning: if train acc >> cv_auc, model may be overfit
        train_acc = accuracy_score(y_train, self.model.predict(X_train_scaled))
        if train_acc - acc > 0.15:
            logger.warning(
                "[AI] Possible overfitting detected: train_acc=%.2f%% test_acc=%.2f%% (gap=%.2f%%)",
                train_acc * 100, acc * 100, (train_acc - acc) * 100
            )

        logger.info("\n%s", classification_report(y_test, y_pred, target_names=["LOSS", "WIN"]))

        # Feature importance (top 5)
        if hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_
            top5_idx = np.argsort(importances)[-5:][::-1]
            logger.info("   Top 5 Fitur Terpenting:")
            for idx in top5_idx:
                feat_name = FEATURE_NAMES[idx] if idx < len(FEATURE_NAMES) else f"feat_{idx}"
                logger.info("   %-25s: %.3f", feat_name, importances[idx])

        # Simpan model, scaler, dan metadata ke disk
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.scaler, self.scaler_path)
        joblib.dump({
            "model_version": MODEL_VERSION,
            "n_features": N_FEATURES,
            "feature_names": FEATURE_NAMES,
            "trained_at": datetime.now().isoformat(),
            "n_samples": len(X),
            "f1_score": float(f1),
            "accuracy": float(acc),
            "cv_auc_mean": float(cv_auc.mean()),   # Phase-2: store for diagnostics
            "cv_auc_std":  float(cv_auc.std()),
        }, self.meta_path)

        self.is_trained = True
        logger.info("[AI SUCCESS] Otak %s berhasil disimpan di '%s'!", self.model_type.upper(), self.model_path)

    def predict_win_probability(self, features: list) -> float:
        """
        Menerima 11 Fitur dan mengembalikan Win Rate (0-100%).
        Urutan fitur HARUS sesuai FEATURE_NAMES: [rsi, adx, vol, bb_width, rrr,
        mm_conf, mm_retail, ihsg_chg, usd_chg, rsi_1d, macd_1d]
        """
        # [FIX #4] Validasi input lebih ketat dengan pesan error yang informatif
        if not isinstance(features, (list, np.ndarray)):
            logger.warning(f"[AI] Input bukan list/array: {type(features)}")
            return 0.0
        if len(features) != N_FEATURES:
            logger.warning(
                f"[AI] Jumlah fitur salah: dapat {len(features)}, "
                f"harap {N_FEATURES}. Urutan: {FEATURE_NAMES}"
            )
            return 0.0

        try:
            clean = np.nan_to_num(
                np.array(features, dtype=float),
                nan=0.0, posinf=0.0, neginf=0.0
            ).reshape(1, -1)

            if self.is_trained and hasattr(self.model, "predict_proba"):
                # [FIX #2] Scale fitur sebelum prediksi
                clean_scaled = self.scaler.transform(clean)
                probs = self.model.predict_proba(clean_scaled)[0]
                win_prob = probs[1] * 100 if len(probs) > 1 else probs[0] * 100
                return round(float(win_prob), 2)
            else:
                return self._fallback_instinct(clean[0])

        except Exception as e:
            logger.debug(f"[AI] Prediction error: {e}")
            return 0.0

    def get_model_info(self) -> dict:
        """Mengembalikan info singkat tentang status model."""
        meta = {}
        if os.path.exists(self.meta_path):
            try:
                meta = joblib.load(self.meta_path)
            except Exception:
                pass
        return {
            "type": self.model_type,
            "is_trained": self.is_trained,
            "model_path": self.model_path,
            **meta
        }

    def _fallback_instinct(self, f: np.ndarray) -> float:
        """
        Insting Bawaan AI (Darurat).
        Hanya dipakai kalau .joblib belum ada / belum pernah di-train.
        """
        skor_dasar = 40.0

        rsi    = f[0]; adx    = f[1]; vol    = f[2]
        rrr    = f[4]; mm_conf = f[5]; ihsg_chg = f[7]

        if 40 <= rsi <= 65:  skor_dasar += 10.0
        if adx > 25:         skor_dasar += 10.0
        if vol > 60:         skor_dasar += 10.0
        if rrr >= 1.5:       skor_dasar += 5.0
        if mm_conf >= 70:    skor_dasar += 15.0
        if ihsg_chg > 0:     skor_dasar += 5.0

        return round(min(85.0, skor_dasar), 2)


# =====================================================================
# SINGLETON MANAGER — Thread-Safe
# =====================================================================
_active_brains: dict[str, MarketAI] = {}
_brains_lock = threading.Lock()  # [FIX #1] Lock untuk thread safety


def get_ai_model(model_type: str = "scalping") -> MarketAI:
    """
    Fungsi utama yang dipanggil oleh file eksternal.
    Thread-safe: aman digunakan bersamaan oleh ThreadPoolExecutor.
    """
    m_type = model_type.lower()
    with _brains_lock:  # [FIX #1] Acquire lock sebelum akses dict
        if m_type not in _active_brains:
            _active_brains[m_type] = MarketAI(model_type=m_type)
    return _active_brains[m_type]