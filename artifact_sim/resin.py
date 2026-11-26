"""Resin waiting-time metrics for a per-drop artifact upgrade probability."""

from __future__ import annotations

from math import ceil, expm1, isfinite, log1p


def estimate_first_upgrade(
    probability: float,
    expected_gain: float,
    baseline: float,
    *,
    resin_per_claim: int = 20,
    quantiles: tuple[float, ...] = (0.5, 0.9),
    horizon_resin: int = 1000,
) -> dict:
    """Convert a per-five-star upgrade probability into resin waiting times.

    ``probability`` is the chance that one five-star piece is a qualifying
    upgrade. ``expected_gain`` is the unconditional damage gain of one such
    drop. ``baseline`` is the current damage used for relative percentages.
    One claim costs ``resin_per_claim`` resin and yields one five-star piece;
    this is the model used by the existing farming examples.
    """
    if type(probability) not in (int, float) or not isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("probability 必须是 0 到 1 的有限数")
    if type(expected_gain) not in (int, float) or not isfinite(expected_gain) or expected_gain < 0:
        raise ValueError("expected_gain 必须是有限的非负数")
    if type(baseline) not in (int, float) or not isfinite(baseline) or baseline < 0:
        raise ValueError("baseline 必须是有限的非负数")
    if type(resin_per_claim) is not int or resin_per_claim <= 0:
        raise ValueError("resin_per_claim 必须是正整数")
    if type(horizon_resin) is not int or horizon_resin < 0:
        raise ValueError("horizon_resin 必须是非负整数")
    if not quantiles or any(type(q) not in (int, float) or not isfinite(q) or not 0 < q < 1 for q in quantiles):
        raise ValueError("quantiles 必须是 (0, 1) 内的有限数")

    p = float(probability)
    gain = float(expected_gain)
    base = float(baseline)
    gain_pct = gain / base * 100 if base > 0 else None
    horizon_claims = horizon_resin // resin_per_claim
    result = {
        "probability_per_drop": p,
        "expected_claims": None,
        "expected_resin": None,
        "probability_within_horizon": 0.0,
        "horizon_resin": horizon_resin,
        "expected_gain_per_drop": gain,
        "expected_gain_pct_per_drop": gain_pct,
        "expected_gain_per_upgrade": None,
        "expected_gain_pct_per_upgrade": None,
        "expected_gain_per_100_resin": gain * 100 / resin_per_claim,
        "expected_gain_pct_per_100_resin": gain_pct * 100 / resin_per_claim if gain_pct is not None else None,
    }
    for q in quantiles:
        result[f"resin_for_{round(q * 100)}pct"] = None
    if p <= 0:
        return result

    p = min(p, 1.0)
    expected_claims = 1.0 / p
    result["expected_claims"] = expected_claims
    result["expected_resin"] = expected_claims * resin_per_claim
    result["expected_gain_per_upgrade"] = gain / p
    result["expected_gain_pct_per_upgrade"] = gain_pct / p if gain_pct is not None else None
    if horizon_claims:
        result["probability_within_horizon"] = (
            1.0 if p >= 1 else -expm1(horizon_claims * log1p(-p))
        )
    for q in quantiles:
        if p >= 1:
            claims = 1
        else:
            claims = max(1, ceil(log1p(-q) / log1p(-p)))
        result[f"resin_for_{round(q * 100)}pct"] = claims * resin_per_claim
    return result
