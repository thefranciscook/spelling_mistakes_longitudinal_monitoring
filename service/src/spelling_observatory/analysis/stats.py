"""Small statistical helpers: Wilson intervals and day-clustered bootstrap.

Word observations are not independent: errors cluster within messages and days (fatigue,
topic, typing speed). Interval estimates for comparisons therefore resample whole *days*
rather than words. Wilson intervals are shown for single periods but are optimistic for
the same reason; the report says so.
"""

from __future__ import annotations

import math

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n <= 0:
        return (float("nan"),) * 3
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return p, max(0.0, centre - half), min(1.0, centre + half)


def bootstrap_rate_difference(base_k: np.ndarray, base_n: np.ndarray, recent_k: np.ndarray, recent_n: np.ndarray,
                              iterations: int = 2000, seed: int = 0) -> dict:
    """Difference in pooled rate (recent - baseline), resampling days within each window.

    Inputs are per-day numerators and denominators. Returns point estimate and 95% interval.
    """
    if base_n.sum() == 0 or recent_n.sum() == 0 or len(base_n) < 2 or len(recent_n) < 2:
        return {"diff": float("nan"), "lo": float("nan"), "hi": float("nan")}
    rng = np.random.default_rng(seed)
    bi = rng.integers(0, len(base_n), size=(iterations, len(base_n)))
    ri = rng.integers(0, len(recent_n), size=(iterations, len(recent_n)))
    with np.errstate(invalid="ignore", divide="ignore"):
        b = base_k[bi].sum(1) / base_n[bi].sum(1)
        r = recent_k[ri].sum(1) / recent_n[ri].sum(1)
    d = r - b
    d = d[np.isfinite(d)]
    point = recent_k.sum() / recent_n.sum() - base_k.sum() / base_n.sum()
    return {"diff": float(point), "lo": float(np.percentile(d, 2.5)), "hi": float(np.percentile(d, 97.5))}
