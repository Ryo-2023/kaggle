"""Dataset and Model Cards generator.

Provides standard structured Markdown files capturing provenance and limitations.
"""

from __future__ import annotations
from typing import Any

def generate_dataset_card(
    dataset_hash: str,
    record_count: int,
    provenance: str = "Synthetic PTGC battle data",
    limitations: str = "Small sample size, not representative of complex solver policies."
) -> str:
    """Generate a structured Dataset Card in Markdown."""
    lines = [
        "# Dataset Card",
        "",
        f"**Dataset Hash**: {dataset_hash}",
        f"**Record Count**: {record_count}",
        "",
        "## Provenance",
        provenance,
        "",
        "## Limitations",
        limitations,
        "",
        "## Privacy Classification",
        "- Internal Identifiers: LOCAL_PRIVATE (redacted from public exports)",
        "- Public Aggregate: Win rates and counts",
    ]
    return "\n".join(lines)

def generate_model_card(
    model_id: str,
    architecture: str,
    metrics: dict[str, float] = None
) -> str:
    """Generate a structured Model Card in Markdown."""
    metrics = metrics or {}
    lines = [
        "# Model Card",
        "",
        f"**Model ID**: {model_id}",
        f"**Architecture**: {architecture}",
        "",
        "## Intended Use",
        "PTCG AI battle agent model for simulation challenge.",
        "",
        "## Metrics",
    ]
    for k, v in metrics.items():
        lines.append(f"- **{k}**: {v}")

    lines.extend([
        "",
        "## Limitations",
        "Evaluation is scoped to 100 game matches. Generalization to unseen rule permutations is not guaranteed."
    ])
    return "\n".join(lines)
