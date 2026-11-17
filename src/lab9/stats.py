"""Statistics students implement (M2 judge calibration, M4 paired bootstrap).

Rules: a judge is a classifier -- measure it like one. A higher mean is not
"better" until the paired CI of the difference excludes 0.
Reference values to self-check are in tests/test_lab9_stats.py.
"""

from __future__ import annotations

from collections.abc import Sequence
import math

import numpy as np
from scipy import stats as scipy_stats

POS = "FAIL"   # the judge's job is to FIND failures, so FAIL is the positive class


def _labels(human: Sequence[str], judge: Sequence[str]) -> tuple[list[str], list[str]]:
    if len(human) != len(judge):
        raise ValueError("human and judge label counts must match")
    h, j = [str(x).strip().upper() for x in human], [str(x).strip().upper() for x in judge]
    if any(x not in {"PASS", POS} for x in h + j):
        raise ValueError("labels must be PASS or FAIL")
    if not h:
        raise ValueError("at least one label pair is required")
    return h, j


def confusion(human: Sequence[str], judge: Sequence[str]) -> dict[str, int]:
    """tp/fp/fn/tn with FAIL as positive."""
    h, j = _labels(human, judge)
    return {
        "tp": sum(a == POS and b == POS for a, b in zip(h, j)),
        "fp": sum(a != POS and b == POS for a, b in zip(h, j)),
        "fn": sum(a == POS and b != POS for a, b in zip(h, j)),
        "tn": sum(a != POS and b != POS for a, b in zip(h, j)),
    }


def precision_recall(human: Sequence[str], judge: Sequence[str]) -> tuple[float, float]:
    cm = confusion(human, judge)
    precision = cm["tp"] / (cm["tp"] + cm["fp"]) if cm["tp"] + cm["fp"] else 0.0
    recall = cm["tp"] / (cm["tp"] + cm["fn"]) if cm["tp"] + cm["fn"] else 0.0
    return precision, recall


def cohen_kappa(human: Sequence[str], judge: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e). Raise ValueError when undefined (p_e == 1):
    a constant judge has no signal and must FAIL calibration, not pass on a technicality."""
    h, j = _labels(human, judge)
    observed = sum(a == b for a, b in zip(h, j)) / len(h)
    human_positive = sum(a == POS for a in h) / len(h)
    judge_positive = sum(b == POS for b in j) / len(j)
    expected = human_positive * judge_positive + (1 - human_positive) * (1 - judge_positive)
    if expected == 1:
        raise ValueError("Cohen's kappa is undefined when expected agreement is 1")
    return (observed - expected) / (1 - expected)


def rogan_gladen(observed_rate: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed failure rate for judge error; clip to [0, 1]."""
    values = (observed_rate, sensitivity, specificity)
    if any(not math.isfinite(x) or x < 0 or x > 1 for x in values):
        raise ValueError("rates must be finite values between 0 and 1")
    denominator = sensitivity + specificity - 1
    if denominator == 0:
        raise ValueError("Rogan-Gladen correction is undefined when sensitivity + specificity = 1")
    estimate = (observed_rate + specificity - 1) / denominator
    return min(1.0, max(0.0, estimate))


def paired_bootstrap(a: Sequence[float], b: Sequence[float], *, n_resamples: int = 1000,
                     rng: int = 2027, confidence: float = 0.95) -> tuple[float, float, float]:
    """a[i], b[i] = per-item mean over seeds, SAME items, SAME order.
    Return (mean(a)-mean(b), lo, hi) with scipy.stats.bootstrap(paired=True, method="percentile",
    rng=numpy.random.default_rng(rng)). If a == b everywhere return (0.0, 0.0, 0.0)."""
    left, right = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if left.ndim != 1 or right.ndim != 1 or left.size != right.size or left.size == 0:
        raise ValueError("paired samples must be non-empty one-dimensional sequences of equal length")
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("paired samples must contain only finite values")
    if n_resamples < 1:
        raise ValueError("n_resamples must be positive")
    if not 0 < confidence < 1:
        raise ValueError("confidence must be between 0 and 1")
    if np.array_equal(left, right):
        return 0.0, 0.0, 0.0
    result = scipy_stats.bootstrap(
        (left, right),
        lambda x, y: np.mean(x - y),
        paired=True,
        n_resamples=n_resamples,
        confidence_level=confidence,
        method="percentile",
        rng=np.random.default_rng(rng),
    )
    return float(np.mean(left - right)), float(result.confidence_interval.low), float(result.confidence_interval.high)


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that AT LEAST ONE of k trials drawn from n (c of them passing) passes.
    Unbiased estimator (Chen et al. 2021): 1 - C(n-c, k) / C(n, k). Raise ValueError if k > n."""
    _validate_pass_counts(n, c, k)
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """pass^k (tau-bench, Yao et al. 2024): probability that ALL k trials drawn from n pass.
    Unbiased estimator: C(c, k) / C(n, k). Raise ValueError if k > n.
    pass^k <= pass@1 <= pass@k; a reliable agent keeps pass^k close to pass@1."""
    _validate_pass_counts(n, c, k)
    return math.comb(c, k) / math.comb(n, k)


def _validate_pass_counts(n: int, c: int, k: int) -> None:
    if any(isinstance(x, bool) or not isinstance(x, int) for x in (n, c, k)):
        raise ValueError("n, c, and k must be integers")
    if n < 1 or c < 0 or c > n or k < 1 or k > n:
        raise ValueError("require n >= 1, 0 <= c <= n, and 1 <= k <= n")
