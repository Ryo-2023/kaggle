"""Meta-evaluation and metric definition auditor.

Defines schemas, ranges, directions, and pitfalls for all evaluation metrics.
"""

from __future__ import annotations
from typing import Any

METRIC_REGISTRY = {
    "win_rate": {
        "definition": "Ratio of matches won by the candidate policy including weighted draws.",
        "direction": "higher",
        "range": [0.0, 1.0],
        "known_pitfalls": "Subject to seat and deck imbalances if not stratified."
    },
    "wilson_interval": {
        "definition": "Wilson score interval bounds for the binomial proportion win rate.",
        "direction": "higher",
        "range": [0.0, 1.0],
        "known_pitfalls": "Normal approximation starts to fail at extreme boundaries close to 0 or 1."
    },
    "elo": {
        "definition": "Sequential relative rating estimate updated per game.",
        "direction": "higher",
        "range": [0.0, float("inf")],
        "known_pitfalls": "Highly dependent on game sequence ordering."
    },
    "bradley_terry": {
        "definition": "Maximum likelihood strength parameter representing absolute win probabilities.",
        "direction": "higher",
        "range": [0.0, float("inf")],
        "known_pitfalls": "Requires fully connected matchup graph to converge."
    },
    "ECE": {
        "definition": "Expected Calibration Error measuring difference between confidence and accuracy.",
        "direction": "lower",
        "range": [0.0, 1.0],
        "known_pitfalls": "Binning strategy choices strongly skew calculated error value."
    }
}

def audit_metric_definition(name: str) -> dict[str, Any]:
    """Audit metric metadata definition and attributes."""
    if name not in METRIC_REGISTRY:
        raise KeyError(f"Metric {name} is not defined in meta-evaluation registry")
    return METRIC_REGISTRY[name]
