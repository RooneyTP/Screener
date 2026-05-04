import pandas as pd
import numpy as np
from ta.trend import SMAIndicator, EMAIndicator, MACD, ADXIndicator
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator, VolumeWeightedAveragePrice

def calculate_sma(data: pd.Series, window: int = 20) -> pd.Series:
    return SMAIndicator(close=data, window=window).sma_indicator()

def calculate_ema(data: pd.Series, window: int = 21) -> pd.Series:
    return EMAIndicator(close=data, window=window).ema_indicator()

def calculate_rsi(data: pd.Series, window: int = 14) -> pd.Series:
    return RSIIndicator(close=data, window=window).rsi()

def calculate_macd(data: pd.Series) -> tuple:
    macd_ind = MACD(close=data)
    return macd_ind.macd(), macd_ind.macd_signal(), macd_ind.macd_diff()

def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return ADXIndicator(high=high, low=low, close=close, window=window).adx()

def calculate_bollinger_bands(data: pd.Series, window: int = 20, window_dev: int = 2) -> tuple:
    bb = BollingerBands(close=data, window=window, window_dev=window_dev)
    return bb.bollinger_mavg(), bb.bollinger_hband(), bb.bollinger_lband()

def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    return AverageTrueRange(high=high, low=low, close=close, window=window).average_true_range()

def calculate_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()

def calculate_vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    try:
        return VolumeWeightedAveragePrice(high=high, low=low, close=close, volume=volume).volume_weighted_average_price()
    except:
        return close  # Fallback

# Tambahan fungsi helper
def hma(data: pd.Series, period: int = 20) -> pd.Series:
    wma1 = data.rolling(int(period/2)).mean()
    wma2 = data.rolling(period).mean()
    hma_raw = 2 * wma1 - wma2
    return hma_raw.rolling(int(np.sqrt(period))).mean()

def detect_support_resistance(data: pd.Series, lookback: int = 20) -> tuple:
    support = data.rolling(lookback).min().iloc[-1]
    resistance = data.rolling(lookback).max().iloc[-1]
    return support, resistance