"""
monte_carlo.py — Monte Carlo Position Sizing
Simulasi 5000 jalur equity untuk menentukan size optimal.
Mencegah akun -50% dari sequence risk.
"""

import numpy as np
import pandas as pd

def monte_carlo_sizing(win_rate: float, avg_win_pct: float, avg_loss_pct: float,
                       capital: float = 10_000_000, n_simulations: int = 5000,
                       n_trades: int = 100, max_drawdown_limit: float = 0.50) -> dict:
    """
    Simulasi Monte Carlo untuk position sizing optimal.
    
    Parameters:
        win_rate: float 0-1 (contoh: 0.55)
        avg_win_pct: float (contoh: 0.04 = 4% profit per win)
        avg_loss_pct: float (contoh: -0.02 = -2% loss)
        capital: modal awal (Rp)
        n_simulations: jumlah simulasi Monte Carlo
        n_trades: jumlah trade per simulasi
        max_drawdown_limit: batas maksimum drawdown (0.5 = -50%)
    
    Returns:
        dict dengan optimal_size, max_size, risk_of_ruin, expected_return
    """
    # Cari size optimal: 1% - 25% modal per trade
    size_fractions = np.arange(0.01, 0.26, 0.01)
    best_size = 0.05  # default conservative
    best_expected_return = 0
    best_risk_of_ruin = 1.0
    
    results = []
    
    for size in size_fractions:
        final_equities = []
        drawdowns = []
        ruin_count = 0
        
        for _ in range(n_simulations):
            equity = capital
            peak = capital
            max_dd = 0
            
            for _ in range(n_trades):
                if equity <= 0:
                    break
                
                # Random win/loss based on win_rate
                if np.random.random() < win_rate:
                    pnl_pct = avg_win_pct
                else:
                    pnl_pct = avg_loss_pct
                
                trade_value = equity * size
                pnl = trade_value * pnl_pct
                equity += pnl
                
                # Track drawdown
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak if peak > 0 else 1.0
                if dd > max_dd:
                    max_dd = dd
                
                if equity <= capital * (1 - max_drawdown_limit):
                    ruin_count += 1
                    break
            
            final_equities.append(equity)
            drawdowns.append(max_dd)
        
        avg_final = np.mean(final_equities)
        risk_of_ruin = ruin_count / n_simulations
        avg_dd = np.mean(drawdowns)
        expected_return = (avg_final - capital) / capital
        
        results.append({
            "size_pct": size * 100,
            "expected_return_pct": expected_return * 100,
            "risk_of_ruin_pct": risk_of_ruin * 100,
            "avg_drawdown_pct": avg_dd * 100,
            "avg_final_equity": avg_final,
        })
        
        # Pilih size terbaik: expected return tertinggi dengan risk of ruin < 5%
        if risk_of_ruin < 0.05 and expected_return > best_expected_return:
            best_expected_return = expected_return
            best_size = size
            best_risk_of_ruin = risk_of_ruin
    
    # Conservative cap: 20% maksimum
    best_size = min(best_size, 0.20)
    
    return {
        "optimal_size_pct": round(best_size * 100, 1),
        "optimal_size_rp": round(capital * best_size),
        "expected_return_pct": round(best_expected_return * 100, 1),
        "risk_of_ruin_pct": round(best_risk_of_ruin * 100, 1),
        "max_size_pct": 20.0,  # Absolute cap
        "all_results": results,
    }


def suggest_size(ai_win_prob: float, rrr: float, capital: float = 10_000_000) -> str:
    """
    Berikan saran position size berdasarkan Monte Carlo.
    Dipanggil dari screener untuk menggantikan Kelly sederhana.
    """
    if ai_win_prob <= 0:
        return "0 Lot (AI tidak tersedia)"
    
    win_rate = ai_win_prob / 100.0
    # Asumsi: avg_win = RRR * avg_loss, average loss = 2%
    avg_loss = 0.02
    avg_win = rrr * avg_loss
    
    mc = monte_carlo_sizing(win_rate, avg_win, -avg_loss, capital=capital,
                            n_simulations=1000, n_trades=50)
    
    size_pct = mc["optimal_size_pct"]
    lot_size = int((capital * size_pct / 100) / 100)  # 1 lot = 100 shares
    
    return f"{size_pct:.1f}% Modal (~{max(1, lot_size)} Lot) | RoR: {mc['risk_of_ruin_pct']:.1f}%"
