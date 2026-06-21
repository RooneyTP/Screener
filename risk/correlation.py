# FIX: Correlation risk check — "If 2+ positions have correlation > 0.75 → treat as single" (SKILL.md §⑤)
# risk/correlation.py
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger("correlation")


def check_pairwise_correlation(
    open_positions: dict[str, float],  # ticker -> current_price
    historical_data: dict[str, pd.Series],  # ticker -> daily close series
    max_corr: float = 0.75,
    min_days: int = 20,
) -> dict[str, list[str]]:
    """
    Check pairwise correlation across open positions.
    Returns dict: correlated_groups grouped by base ticker.

    Example return: {"BBCA": ["BBRI", "BMRI"]} → treat BBCA+BBRI+BMRI as one combined position.
    """
    if len(open_positions) < 2:
        return {}

    # Build returns matrix
    returns_dict = {}
    for ticker in open_positions:
        close_series = historical_data.get(ticker)
        if close_series is not None and len(close_series) >= min_days:
            returns_dict[ticker] = close_series.pct_change().dropna().tail(60)

    if len(returns_dict) < 2:
        return {}

    returns_df = pd.DataFrame(returns_dict)
    corr_matrix = returns_df.corr()

    correlated_groups: dict[str, list[str]] = {}
    seen = set()
    for t1 in corr_matrix.columns:
        if t1 in seen:
            continue
        group = []
        for t2 in corr_matrix.columns:
            if t1 != t2 and not pd.isna(corr_matrix.loc[t1, t2]) and corr_matrix.loc[t1, t2] > max_corr:
                group.append(t2)
                seen.add(t2)
        if group:
            correlated_groups[t1] = group
            seen.add(t1)

    if correlated_groups:
        logger.warning("High correlation detected: %s — treat as single combined position", correlated_groups)
    return correlated_groups
