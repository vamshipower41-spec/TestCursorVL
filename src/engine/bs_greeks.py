"""Black-Scholes Greeks computed from first principles.

Exact analytical formulas for European-style options (NIFTY/SENSEX).
Replaces crude approximations with proper partial derivatives.

Greeks computed:
  - Delta: dC/dS
  - Gamma: d²C/dS²
  - Charm: dDelta/dT (delta decay per day)
  - Vanna: dDelta/dSigma (delta sensitivity to IV)
  - Theta: dC/dT (time decay)

References:
  - Hull, "Options, Futures, and Other Derivatives"
  - Taleb, "Dynamic Hedging"

Indian market parameters:
  - Risk-free rate: RBI 91-day T-bill rate (~6.5-7.0% annualized)
  - Dividend yield: NIFTY ~1.2-1.5% trailing
  - European-style settlement (cash-settled, no early exercise)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

# Indian market defaults
INDIA_RISK_FREE_RATE = 0.065  # 6.5% annualized (91-day T-bill)
NIFTY_DIVIDEND_YIELD = 0.013  # 1.3% trailing dividend yield

# Numerical safety
_TAU_MIN = 1e-10  # Minimum time to expiry (avoids division by zero)
_SIGMA_MIN = 0.001  # Minimum IV (0.1%)


@dataclass
class StrikeGreeks:
    """Full Greeks for a single strike."""
    strike: float
    delta_call: float
    delta_put: float
    gamma: float
    charm_call: float  # delta decay per day (calls)
    charm_put: float   # delta decay per day (puts)
    vanna: float       # dDelta/dSigma
    theta_call: float  # time decay per day (calls)
    theta_put: float   # time decay per day (puts)


def _d1d2(
    S: float, K: float, r: float, q: float, sigma: float, tau: float,
) -> tuple[float, float]:
    """Compute d1 and d2 for Black-Scholes."""
    sqrt_tau = math.sqrt(tau)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma**2) * tau) / (sigma * sqrt_tau)
    d2 = d1 - sigma * sqrt_tau
    return d1, d2


def compute_bs_greeks(
    S: float,
    K: float,
    sigma: float,
    tau_years: float,
    r: float = INDIA_RISK_FREE_RATE,
    q: float = NIFTY_DIVIDEND_YIELD,
) -> StrikeGreeks:
    """Compute all Greeks for a single strike using exact Black-Scholes formulas.

    Args:
        S: Spot price of underlying (e.g., NIFTY spot)
        K: Strike price
        sigma: Implied volatility (annualized, as decimal e.g. 0.15 for 15%)
        tau_years: Time to expiry in years (e.g., 3 hours = 3/8760)
        r: Risk-free rate (annualized, continuous compounding)
        q: Continuous dividend yield

    Returns:
        StrikeGreeks with all computed values
    """
    # Edge case: expired or nearly expired
    if tau_years < _TAU_MIN:
        itm_call = S > K
        itm_put = S < K
        return StrikeGreeks(
            strike=K,
            delta_call=1.0 if itm_call else (0.5 if abs(S - K) / S < 0.001 else 0.0),
            delta_put=-1.0 if itm_put else (-0.5 if abs(S - K) / S < 0.001 else 0.0),
            gamma=0.0, charm_call=0.0, charm_put=0.0,
            vanna=0.0, theta_call=0.0, theta_put=0.0,
        )

    # Edge case: zero or near-zero IV
    if sigma < _SIGMA_MIN:
        sigma = _SIGMA_MIN

    tau = tau_years
    sqrt_tau = math.sqrt(tau)
    d1, d2 = _d1d2(S, K, r, q, sigma, tau)

    n_d1 = norm.pdf(d1)     # phi(d1) — standard normal PDF
    N_d1 = norm.cdf(d1)     # Phi(d1) — standard normal CDF
    N_neg_d1 = norm.cdf(-d1)
    eq = math.exp(-q * tau)

    # --- Delta ---
    delta_call = eq * N_d1
    delta_put = -eq * N_neg_d1  # = eq * (N_d1 - 1)

    # --- Gamma (same for calls and puts) ---
    gamma = eq * n_d1 / (S * sigma * sqrt_tau)

    # --- Vanna: dDelta/dSigma ---
    # Vanna = -eq * n(d1) * d2 / sigma
    vanna = -eq * n_d1 * d2 / sigma

    # --- Charm: dDelta/dTau ---
    # For calls: dDelta_call/dtau = -q*eq*N(d1) + eq*n(d1)*dd1_dtau
    # where dd1_dtau involves the complex expression below.
    # Standard result:
    # charm_raw = eq * n(d1) * [2(r-q)*tau - d2*sigma*sqrt_tau] / (2*tau*sigma*sqrt_tau)
    charm_inner = (2.0 * (r - q) * tau - d2 * sigma * sqrt_tau) / (2.0 * tau * sigma * sqrt_tau)
    charm_call_raw = eq * n_d1 * charm_inner - q * eq * N_d1
    charm_put_raw = eq * n_d1 * charm_inner + q * eq * N_neg_d1

    # Convert from per-year to per-day (calendar days)
    # charm_per_day = -charm_raw / 365
    # Negative sign: tau decreases as time passes, so dDelta/dt = -dDelta/dtau
    charm_call_per_day = -charm_call_raw / 365.0
    charm_put_per_day = -charm_put_raw / 365.0

    # --- Theta ---
    # Call theta = -eq*n(d1)*S*sigma/(2*sqrt_tau) - r*K*exp(-r*tau)*N(d2) + q*S*eq*N(d1)
    er = math.exp(-r * tau)
    N_d2 = norm.cdf(d2)
    N_neg_d2 = norm.cdf(-d2)

    theta_call_annual = (
        -eq * n_d1 * S * sigma / (2.0 * sqrt_tau)
        - r * K * er * N_d2
        + q * S * eq * N_d1
    )
    theta_put_annual = (
        -eq * n_d1 * S * sigma / (2.0 * sqrt_tau)
        + r * K * er * N_neg_d2
        - q * S * eq * N_neg_d1
    )

    theta_call_per_day = theta_call_annual / 365.0
    theta_put_per_day = theta_put_annual / 365.0

    return StrikeGreeks(
        strike=K,
        delta_call=delta_call,
        delta_put=delta_put,
        gamma=gamma,
        charm_call=charm_call_per_day,
        charm_put=charm_put_per_day,
        vanna=vanna,
        theta_call=theta_call_per_day,
        theta_put=theta_put_per_day,
    )


def compute_chain_greeks(
    chain_df,
    spot_price: float,
    time_to_expiry_hours: float,
    r: float = INDIA_RISK_FREE_RATE,
    q: float = NIFTY_DIVIDEND_YIELD,
):
    """Compute BS Greeks for every strike in the chain DataFrame.

    Uses IV from the API for each strike (call_iv, put_iv).
    Returns a new DataFrame with BS-computed Greeks columns added.

    Added columns:
        bs_gamma, bs_vanna, bs_charm_call, bs_charm_put,
        bs_theta_call, bs_theta_put, bs_delta_call, bs_delta_put
    """
    import pandas as pd

    df = chain_df.copy()
    tau_years = max(time_to_expiry_hours / 8760.0, _TAU_MIN)

    bs_cols = {
        "bs_delta_call": [], "bs_delta_put": [],
        "bs_gamma": [], "bs_vanna": [],
        "bs_charm_call": [], "bs_charm_put": [],
        "bs_theta_call": [], "bs_theta_put": [],
    }

    for _, row in df.iterrows():
        strike = row["strike_price"]

        # Use average of call_iv and put_iv for gamma/vanna (they should be similar)
        call_iv = row.get("call_iv", 0) or 0
        put_iv = row.get("put_iv", 0) or 0

        # Convert IV from percentage to decimal if needed
        if call_iv > 1:
            call_iv = call_iv / 100.0
        if put_iv > 1:
            put_iv = put_iv / 100.0

        # Use call IV for call greeks, put IV for put greeks
        # For gamma/vanna use average (they're based on the same underlying)
        avg_iv = (call_iv + put_iv) / 2.0 if (call_iv > 0 and put_iv > 0) else max(call_iv, put_iv)

        if avg_iv < _SIGMA_MIN:
            # No IV data — use zero greeks
            for col in bs_cols:
                bs_cols[col].append(0.0)
            continue

        greeks = compute_bs_greeks(spot_price, strike, avg_iv, tau_years, r, q)

        bs_cols["bs_delta_call"].append(greeks.delta_call)
        bs_cols["bs_delta_put"].append(greeks.delta_put)
        bs_cols["bs_gamma"].append(greeks.gamma)
        bs_cols["bs_vanna"].append(greeks.vanna)
        bs_cols["bs_charm_call"].append(greeks.charm_call)
        bs_cols["bs_charm_put"].append(greeks.charm_put)
        bs_cols["bs_theta_call"].append(greeks.theta_call)
        bs_cols["bs_theta_put"].append(greeks.theta_put)

    for col, values in bs_cols.items():
        df[col] = values

    return df


def compute_dealer_charm_flow(
    chain_df,
    spot_price: float,
    time_to_expiry_hours: float,
    contract_multiplier: int,
    r: float = INDIA_RISK_FREE_RATE,
    q: float = NIFTY_DIVIDEND_YIELD,
) -> dict:
    """Compute net charm (delta decay) flow using exact BS charm.

    This replaces the approximation in charm_vanna.py with the real formula.
    Charm tells us: as time passes, how much delta do dealers need to re-hedge?

    For dealer-short positions:
      - Call charm > 0 (near ATM): delta decaying → dealers need to sell less hedge → sell underlying
      - Put charm < 0 (near ATM): |delta| decaying → dealers need to buy less hedge → buy underlying

    Net dealer charm flow = bullish if put charm dominates, bearish if call charm dominates.
    """
    enriched = compute_chain_greeks(chain_df, spot_price, time_to_expiry_hours, r, q)

    # Dealer charm exposure = charm * OI * multiplier
    # Call side: dealers short calls → charm decay means they sell underlying (bearish)
    call_charm_exp = float(
        (enriched["bs_charm_call"].abs() * enriched.get("call_oi", 0)).sum()
    ) * contract_multiplier

    # Put side: dealers short puts → charm decay means they buy underlying (bullish)
    put_charm_exp = float(
        (enriched["bs_charm_put"].abs() * enriched.get("put_oi", 0)).sum()
    ) * contract_multiplier

    net_charm = put_charm_exp - call_charm_exp  # positive = bullish

    max_exp = max(call_charm_exp, put_charm_exp, 1.0)
    raw_intensity = abs(net_charm) / max_exp * 100.0

    # Boost in last 2 hours (charm acceleration zone)
    if time_to_expiry_hours < 2.0:
        raw_intensity *= 1.5

    charm_intensity = min(raw_intensity, 100.0)

    return {
        "net_charm_flow": net_charm,
        "charm_intensity": charm_intensity,
        "call_charm_exposure": call_charm_exp,
        "put_charm_exposure": put_charm_exp,
    }


def compute_dealer_vanna_flow(
    chain_df,
    prev_chain_df,
    spot_price: float,
    time_to_expiry_hours: float,
    contract_multiplier: int,
    r: float = INDIA_RISK_FREE_RATE,
    q: float = NIFTY_DIVIDEND_YIELD,
) -> dict:
    """Compute vanna-driven hedging flow using exact BS vanna + real IV changes.

    Vanna = dDelta/dSigma. When IV drops (vol crush on expiry day):
      - For dealer-short calls: IV drop → delta decreases → dealers buy back hedge (bullish)
      - For dealer-short puts: IV drop → |delta| decreases → dealers sell hedge (bearish)

    The net effect depends on OI asymmetry and the magnitude of IV change.
    """
    if prev_chain_df is None:
        return {"net_vanna_flow": 0, "vanna_intensity": 0, "avg_iv_change": 0.0}

    import pandas as pd

    enriched = compute_chain_greeks(chain_df, spot_price, time_to_expiry_hours, r, q)

    # Merge with previous chain to get IV changes
    prev = prev_chain_df[["strike_price", "call_iv", "put_iv"]].copy()
    merged = enriched.merge(prev, on="strike_price", suffixes=("", "_prev"), how="inner")

    if merged.empty:
        return {"net_vanna_flow": 0, "vanna_intensity": 0, "avg_iv_change": 0.0}

    merged["call_iv_change"] = (merged["call_iv"].fillna(0) - merged["call_iv_prev"].fillna(0))
    merged["put_iv_change"] = (merged["put_iv"].fillna(0) - merged["put_iv_prev"].fillna(0))

    # Vanna flow = BS_vanna * IV_change * OI * multiplier
    # For dealer-short calls: -IV_change * vanna * OI (negative IV change = bullish)
    merged["call_vanna_flow"] = (
        -merged["call_iv_change"]
        * merged["bs_vanna"].abs()
        * merged.get("call_oi", pd.Series(0, index=merged.index)).fillna(0)
    )
    merged["put_vanna_flow"] = (
        merged["put_iv_change"]
        * merged["bs_vanna"].abs()
        * merged.get("put_oi", pd.Series(0, index=merged.index)).fillna(0)
    )

    call_vanna = float(merged["call_vanna_flow"].sum()) * contract_multiplier
    put_vanna = float(merged["put_vanna_flow"].sum()) * contract_multiplier

    net_vanna = call_vanna + put_vanna

    avg_iv_change = float(
        (merged["call_iv_change"].mean() + merged["put_iv_change"].mean()) / 2
    )
    if np.isnan(avg_iv_change):
        avg_iv_change = 0.0

    max_flow = max(abs(call_vanna), abs(put_vanna), 1.0)
    vanna_intensity = min(abs(net_vanna) / max_flow * 100, 100.0)

    return {
        "net_vanna_flow": net_vanna,
        "vanna_intensity": vanna_intensity,
        "avg_iv_change": avg_iv_change,
    }
