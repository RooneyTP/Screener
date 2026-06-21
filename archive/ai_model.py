"""
=============================================================================
  MARKET AI ENGINE v4.0 - Dual Brain Architecture  [ANTI-OVERFITTING]
  Mendukung Scalping (1-Minute) dan Swing Trading (End-of-Day)
  Algoritma Dasar: HistGradientBoosting (auto early-stopping + L2 regularization)

  CHANGELOG v4.0:
  - [FIX #6] Fitur dipangkas 14→10 (hapus rsi_1d, bb_width, rsi_vol_interaction, sector_corr)
  - [FIX #7] RandomForest → HistGradientBoosting (early_stopping + l2_regularization)
  - [FIX #8] train_test_split (random) → time-based chronological split
  - [FIX #9] cross_val_score(cv=5) → TimeSeriesSplit(n_splits=5)
  - [FIX #10] Added walk-forward validation report (mean ± std across time windows)

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
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, classification_report
    )
except ImportError:
    print("⏳ Menginstal scikit-learn untuk Machine Learning...")
    os.system("pip install scikit-learn -q")
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score,
        f1_score, classification_report
    )

# ─── Konstanta ────────────────────────────────────────────────────────────────
MODEL_DIR = "models_ai"
os.makedirs(MODEL_DIR, exist_ok=True)

# Urutan fitur yang WAJIB dipatuhi oleh caller
# v4.0: Dipangkas 14→10 untuk mengurangi korelasi tinggi & overfitting
# DROP: rsi_1d (99% korelasi dgn rsi), bb_width (korelasi dgn rolling_vol_20),
#       rsi_vol_interaction (korelasi dgn rsi), sector_corr (korelasi dgn ihsg_change)
# Perubahan urutan di sini HARUS diikuti perubahan di screener.py
FEATURE_NAMES = [
    "rsi",                    # 0  — RSI hari ini
    "adx",                    # 1  — ADX trend strength
    "vol_strength",           # 2  — Volume strength %
    "rrr",                    # 3  — Risk/Reward Ratio
    "mm_confidence",          # 4  — Market Maker confidence
    "mm_vs_retail_ratio",     # 5  — MM vs Retail ratio
    "ihsg_change",            # 6  — IHSG daily change %
    "usd_change",             # 7  — USD/IDR daily change %
    "macd_1d",                # 8  — MACD value yesterday
    "rolling_vol_20",         # 9  — 20-day realised volatility
]
N_FEATURES = len(FEATURE_NAMES)  # 10

# Versi model — naikkan setiap kali FEATURE_NAMES berubah agar model lama ditolak
MODEL_VERSION = "4.0"  # bumped from 3.2: N_FEATURES 14→10 + new algorithm


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
        """
        [FIX #7] HistGradientBoostingClassifier menggantikan RandomForest.
        Keunggulan:
          - early_stopping bawaan → berhenti saat validation loss tidak membaik
          - l2_regularization → mencegah bobot ekstrem
          - max_depth=5 (dangkal) → generalisasi lebih baik
          - learning_rate=0.05 → belajar perlahan, tidak menghafal noise
        """
        return HistGradientBoostingClassifier(
            max_iter=500,                # Maksimum iterasi (early stopping akan potong)
            max_depth=5,                 # Dangkal → anti-overfitting
            learning_rate=0.05,          # Belajar perlahan
            l2_regularization=0.5,       # Regularisasi L2 eksplisit
            early_stopping=True,         # Berhenti saat validation loss plateau
            validation_fraction=0.15,    # 15% training data untuk early stopping
            n_iter_no_change=20,         # Sabar 20 iterasi sebelum stop
            random_state=42,
            class_weight="balanced",
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def train_model(self, X, y):
        """
        [FIX #8] Melatih AI dengan dataset historis MENGGUNAKAN TIME-BASED SPLIT.
        Tidak ada lagi random shuffling — data lama untuk train, data baru untuk test.
        [FIX #9] Walk-forward validation dengan TimeSeriesSplit.
        Mencetak metrik evaluasi lengkap (accuracy, F1, precision, recall).
        """
        if len(X) < 100:
            logger.warning(
                "[AI TRAINING] Data terlalu sedikit (%d baris). Minimal 100 untuk training andal.", len(X)
            )
            return

        logger.info("[AI TRAINING] Memulai pelatihan otak %s (v%s)...", self.model_type.upper(), MODEL_VERSION)
        logger.info("   Dataset: %d baris, %d fitur", len(X), X.shape[1])
        logger.info("   Split: TIME-BASED chronological (NO random shuffle) ← ANTI-OVERFITTING")

        # ── [FIX #8] TIME-BASED CHRONOLOGICAL SPLIT ─────────────────────────
        # Data harus sudah diurutkan berdasarkan waktu oleh caller.
        # 80% data paling awal → training, 20% data paling akhir → testing.
        split_idx = int(len(X) * 0.8)
        X_train = X[:split_idx]
        X_test  = X[split_idx:]
        y_train = y[:split_idx]
        y_test  = y[split_idx:]

        if len(X_test) < 10:
            logger.warning("[AI] Test set terlalu kecil (%d baris). Butuh minimal 10 baris.", len(X_test))
            return

        logger.info("   Train: %d baris (oldest 80%%)", len(X_train))
        logger.info("   Test : %d baris (newest 20%%)  ← NO data leakage from future", len(X_test))

        # [FIX #2] Fit scaler HANYA pada training data (tidak bocor ke test)
        self.scaler = StandardScaler()
        X_train_scaled = self.scaler.fit_transform(X_train)
        X_test_scaled  = self.scaler.transform(X_test)

        # Proses Fit dengan HistGradientBoosting (early stopping bawaan)
        self.model = self._build_fresh_model()
        self.model.fit(X_train_scaled, y_train)

        # ── Evaluasi Performa pada Test Set (data masa depan) ───────────────
        y_pred = self.model.predict(X_test_scaled)
        acc  = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec  = recall_score(y_test, y_pred, zero_division=0)
        f1   = f1_score(y_test, y_pred, zero_division=0)

        # ── [FIX #9] WALK-FORWARD VALIDATION (TimeSeriesSplit) ──────────────
        # TimeSeriesSplit memastikan setiap fold: train < test secara kronologis
        tscv = TimeSeriesSplit(n_splits=5)
        cv_f1_scores = []
        cv_auc_scores = []

        for fold, (train_idx, val_idx) in enumerate(tscv.split(X_train_scaled)):
            fold_model = self._build_fresh_model()
            X_fold_train = X_train_scaled[train_idx]
            y_fold_train = y_train[train_idx]
            X_fold_val   = X_train_scaled[val_idx]
            y_fold_val   = y_train[val_idx]

            if len(np.unique(y_fold_train)) < 2:
                continue  # Skip fold yang labelnya cuma 1 kelas

            fold_model.fit(X_fold_train, y_fold_train)
            y_fold_pred = fold_model.predict(X_fold_val)

            cv_f1_scores.append(f1_score(y_fold_val, y_fold_pred, zero_division=0))

            # ROC AUC butuh predict_proba
            if hasattr(fold_model, "predict_proba") and len(np.unique(y_fold_val)) >= 2:
                try:
                    from sklearn.metrics import roc_auc_score
                    y_fold_proba = fold_model.predict_proba(X_fold_val)[:, 1]
                    cv_auc_scores.append(roc_auc_score(y_fold_val, y_fold_proba))
                except Exception:
                    pass

        logger.info("   ── HASIL EVALUASI MODEL (%s) ──", self.model_type.upper())
        logger.info("   Accuracy (time-split) : %.2f%%  ← INI YANG JUJUR", acc * 100)
        logger.info("   Precision             : %.2f%%", prec * 100)
        logger.info("   Recall                : %.2f%%", rec * 100)
        logger.info("   F1-Score              : %.2f%%", f1 * 100)

        if cv_f1_scores:
            logger.info("   WF-CV F1  (5-fold)    : %.2f%% ± %.2f%%  ← walk-forward", 
                       np.mean(cv_f1_scores) * 100, np.std(cv_f1_scores) * 100)
        if cv_auc_scores:
            logger.info("   WF-CV AUC (5-fold)    : %.4f ± %.4f  ← overfitting check",
                       np.mean(cv_auc_scores), np.std(cv_auc_scores))

        # Overfitting warning: train acc vs test acc gap
        train_acc = accuracy_score(y_train, self.model.predict(X_train_scaled))
        gap = train_acc - acc
        if gap > 0.15:
            logger.warning(
                "[AI] ⚠️ OVERFITTING DETECTED: train_acc=%.2f%% test_acc=%.2f%% (gap=%.2f%%)",
                train_acc * 100, acc * 100, gap * 100
            )
        elif gap > 0.08:
            logger.info(
                "[AI] Train/test gap: %.2f%% (acceptable < 8%%)",
                gap * 100
            )
        else:
            logger.info(
                "[AI] ✅ Train/test gap minimal: %.2f%% (good generalization)",
                gap * 100
            )

        logger.info("\n%s", classification_report(y_test, y_pred, target_names=["LOSS", "WIN"]))

        # Feature importance (top 5)
        if hasattr(self.model, "feature_importances_"):
            importances = self.model.feature_importances_
            # Handle case where importances length doesn't match FEATURE_NAMES
            actual_n = len(importances)
            top_n = min(5, actual_n)
            top_idx = np.argsort(importances)[-top_n:][::-1]
            logger.info("   Top %d Fitur Terpenting:", top_n)
            for idx in top_idx:
                feat_name = FEATURE_NAMES[idx] if idx < len(FEATURE_NAMES) else f"feat_{idx}"
                logger.info("   %-25s: %.3f", feat_name, importances[idx])

        # Simpan model, scaler, dan metadata ke disk
        joblib.dump(self.model, self.model_path)
        joblib.dump(self.scaler, self.scaler_path)
        cv_auc_mean = float(np.mean(cv_auc_scores)) if cv_auc_scores else 0.0
        cv_auc_std  = float(np.std(cv_auc_scores)) if cv_auc_scores else 0.0
        joblib.dump({
            "model_version": MODEL_VERSION,
            "n_features": N_FEATURES,
            "feature_names": FEATURE_NAMES,
            "trained_at": datetime.now().isoformat(),
            "n_samples": len(X),
            "n_train": len(X_train),
            "n_test": len(X_test),
            "f1_score": float(f1),
            "accuracy": float(acc),
            "train_test_gap": float(gap),
            "cv_auc_mean": cv_auc_mean,
            "cv_auc_std":  cv_auc_std,
            "algorithm": "HistGradientBoosting",
        }, self.meta_path)

        self.is_trained = True
        logger.info("[AI SUCCESS] Otak %s v%s berhasil disimpan di '%s'!", 
                   self.model_type.upper(), MODEL_VERSION, self.model_path)

    def predict_win_probability(self, features: list) -> float:
        """
        Menerima 10 Fitur dan mengembalikan Win Rate (0-100%).
        Urutan fitur HARUS sesuai FEATURE_NAMES v4.0:
        [rsi, adx, vol_strength, rrr, mm_conf, mm_retail, ihsg_chg, usd_chg, macd_1d, rolling_vol_20]
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
        Index sesuai FEATURE_NAMES v4.0 (10 fitur).
        """
        skor_dasar = 40.0

        rsi      = f[0]  # rsi
        adx      = f[1]  # adx
        vol      = f[2]  # vol_strength
        rrr      = f[3]  # rrr
        mm_conf  = f[4]  # mm_confidence
        ihsg_chg = f[6]  # ihsg_change

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
