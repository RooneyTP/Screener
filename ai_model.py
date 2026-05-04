import torch
import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
import torch.nn as nn
import joblib
import sqlite3
import os

from liquid_moe import LiquidMoE

class AIModel:
    def __init__(self, num_experts=3, input_size=11, hidden_size=32):
        self.model = None
        self.scaler = None
        self.sentiment_analyzer = None
        self._sentiment_loaded = False
        self.load_model(num_experts, input_size, hidden_size)

    def load_model(self, num_experts, input_size, hidden_size):
        """Load AI model with robust error handling (silent mode)"""
        try:
            self.model = LiquidMoE(num_experts=num_experts, input_size=input_size, hidden_size=hidden_size)
            if os.path.exists("liquid_moe_brain.pth"):
                try:
                    self.model.load_state_dict(torch.load("liquid_moe_brain.pth", weights_only=True, map_location='cpu'))
                except RuntimeError:
                    pass  # Use untrained model silently
            self.model.eval()
            
            if os.path.exists("kacamata_ai.pkl"):
                self.scaler = joblib.load("kacamata_ai.pkl")
        except Exception as e:
            # Silent fail - model will default to 50% prediction
            self.model = None

    def _load_sentiment_analyzer(self):
        """Lazy load sentiment analyzer on first use (silent mode)"""
        if self._sentiment_loaded:
            return self.sentiment_analyzer
        
        try:
            from transformers import pipeline as hf_pipeline
            self.sentiment_analyzer = hf_pipeline("sentiment-analysis", model="cardiffnlp/twitter-roberta-base-sentiment")
            self._sentiment_loaded = True
            return self.sentiment_analyzer
        except Exception:
            # Fail silently - will use neutral sentiment
            self._sentiment_loaded = True
            return None

    # Ganti historical_features menjadi current_features (hanya 1 baris)
    def predict_win_probability(self, current_features: list) -> float:
        if not self.model or not self.scaler:
            return 50.0

        try:
            # Hanya memproses data HARI INI
            data = pd.DataFrame([current_features])
            data = data.apply(pd.to_numeric, errors='coerce').fillna(0.0)
            
            normalized = self.scaler.transform(data.values)
            
            # Tensor 3D: (Batch=1, Sequence=1, Features=11)
            tensor_X = torch.tensor(normalized, dtype=torch.float32).unsqueeze(1)

            with torch.no_grad():
                raw_output = self.model(tensor_X)
                
                # 🔥 FIX FINAL: Ratakan semua dimensi tensor ke bentuk 1 baris
                # Tidak peduli outputnya 2D atau 3D, kita ambil angka terakhirnya saja
                nilai_prediksi = raw_output.flatten()[-1]
                
                prob = torch.sigmoid(nilai_prediksi).item() * 100
                return round(prob, 1)
                
        except Exception as e:
            print(f"AI Prediction error: {e}")
            return 50.0

    def analyze_sentiment(self, text: str) -> dict:
        """Analyze sentiment with lazy loading"""
        try:
            analyzer = self._load_sentiment_analyzer()
            if not analyzer:
                return {"label": "NEUTRAL", "score": 0.5}

            result = analyzer(text[:512])  # Limit text length
            label = result[0]['label'].upper()
            score = result[0]['score']
            return {"label": label, "score": score}
        except Exception as e:
            print(f"Sentiment analysis error: {e}")
            return {"label": "NEUTRAL", "score": 0.5}

# Global instance - lazy initialized
ai_model = None

def get_ai_model():
    """Get or create AI model instance (lazy loading)"""
    global ai_model
    if ai_model is None:
        ai_model = AIModel()
    return ai_model