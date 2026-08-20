"""Receipt completeness, detector invariance and method sensitivity auditing.

A number is only comparable when the conditions that produced it are recorded well
enough to reconstruct.  These auditors answer three questions mechanically:

* **Completeness** — does a persisted ``metrics.json`` come with a receipt that names
  source commit, checkpoint SHA-256, cache digest, device and command?
* **Detector invariance** — do all methods in one race quote the *same* detector cache
  digest?  Changing the association method must not change the detector.
* **Method sensitivity** — do different methods produce *different* prediction digests?
  If they do not, the race is comparing nothing.

Pure stdlib.  No detector, no checkpoint, no ``.zarr``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The five provenance facts the brief requires beside every persisted metric.
REQUIRED_RECEIPT_FIELDS: tuple[str, ...] = (
    "source_commit",
    "checkpoint_sha256",
    "cache_digest",
    "device",
    "command",
)

#: Facts that are not recorded anywhere today but decide whether two numbers are
#: comparable.  Association code changes leave no trace in any current receipt.
RECOMMENDED_RECEIPT_FIELDS: tuple[str, ...] = (
    "association_code_sha256",
    "seed",
    "torch_version",
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "source_commit": ("source_commit", "upstream_commit", "commit"),
    "checkpoint_sha256": ("checkpoint_sha256",),
    "cache_digest": ("cache_hash", "cache_digest", "cache_sha256"),
    "device": ("device",),
    "command": ("command", "argv", "cmd"),
    "association_code_sha256": (
        "association_code_sha256",
        "association_source_sha256",
        "code_sha256",
        "source_tree_sha256",
    ),
    "seed": ("seed", "random_seed", "torch_seed"),
    "torch_version": ("torch_version", "torch"),
}


def _iter_values(payload: Any, key: str) -> Iterable[Any]:
    """Yield every value stored under *key* anywhere inside *payload*."""

    if isinstance(payload, Mapping):
        for item_key, item in payload.items():
            if item_key == key:
                yield item
            yield from _iter_values(item, key)
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        for item in payload:
            yield from _iter_values(item, key)


def _find_field(payloads: Sequence[Any], field_name: str) -> Any:
    for alias in _FIELD_ALIASES[field_name]:
        for payload in payloads:
            for value in _iter_values(payload, alias):
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                if isinstance(value, (list, tuple)) and not value:
                    continue
                return value
    return None


@dataclass(frozen=True, slots=True)
class ReceiptAudit:
    """Result of auditing one persisted metric against its receipts."""

    label: str
    found: Mapping[str, Any] = field(default_factory=dict)
    missing_required: tuple[str, ...] = ()
    missing_recommended: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.missing_required


def audit_receipt(label: str, payloads: Sequence[Any]) -> ReceiptAudit:
    """Audit *payloads* (a metrics.json plus every receipt beside it) for completeness."""

    found: dict[str, Any] = {}
    missing_required: list[str] = []
    missing_recommended: list[str] = []
    for name in REQUIRED_RECEIPT_FIELDS:
        value = _find_field(payloads, name)
        if value is None:
            missing_required.append(name)
        else:
            found[name] = value
    for name in RECOMMENDED_RECEIPT_FIELDS:
        value = _find_field(payloads, name)
        if value is None:
            missing_recommended.append(name)
        else:
            found[name] = value
    return ReceiptAudit(
        label=label,
        found=found,
        missing_required=tuple(missing_required),
        missing_recommended=tuple(missing_recommended),
    )


def _record_cache_hash(record: Mapping[str, Any]) -> str | None:
    for key in ("cache_hash", "cache_digest"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _record_prediction_digest(record: Mapping[str, Any]) -> str | None:
    metrics = record.get("metrics")
    if isinstance(metrics, Mapping):
        value = metrics.get("prediction_manifest_directory_sha256")
        if isinstance(value, str) and value:
            return value
    value = record.get("directory_sha256") or record.get("prediction_sha256")
    return value if isinstance(value, str) and value else None


def detector_invariance_report(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check that every method in a race quotes one identical detector cache digest.

    This is the check that would have flagged a race whose "fixed" detector silently
    moved between methods.  It looks only at recorded digests, so it is cheap and can
    run on any saved ``race_receipt.json``.
    """

    if not records:
        raise ValueError("detector invariance needs at least one race record")
    by_method: dict[str, str | None] = {}
    for record in records:
        method_id = str(record.get("method_id", "<unknown>"))
        by_method[method_id] = _record_cache_hash(record)
    missing = sorted(method for method, digest in by_method.items() if not digest)
    distinct = sorted({digest for digest in by_method.values() if digest})
    return {
        "methods": sorted(by_method),
        "cache_hash_by_method": dict(sorted(by_method.items())),
        "distinct_cache_hashes": distinct,
        "methods_missing_cache_hash": missing,
        "invariant_holds": not missing and len(distinct) == 1,
    }


def method_sensitivity_report(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Check that distinct association methods produced distinct prediction digests."""

    if len(records) < 2:
        raise ValueError("method sensitivity needs at least two race records")
    digests: dict[str, str | None] = {}
    for record in records:
        method_id = str(record.get("method_id", "<unknown>"))
        digests[method_id] = _record_prediction_digest(record)
    missing = sorted(method for method, digest in digests.items() if not digest)
    collisions: list[tuple[str, str]] = []
    methods = sorted(digests)
    for index, left in enumerate(methods):
        for right in methods[index + 1 :]:
            if digests[left] and digests[left] == digests[right]:
                collisions.append((left, right))
    return {
        "prediction_digest_by_method": dict(sorted(digests.items())),
        "methods_missing_digest": missing,
        "colliding_method_pairs": collisions,
        "invariant_holds": not missing and not collisions,
    }


def prediction_manifest_candidates(prediction_path: Path) -> dict[str, Path]:
    """Return the manifest locations that could describe *prediction_path*."""

    prediction_path = Path(prediction_path)
    return {
        "per_prediction": prediction_path.parent / f"{prediction_path.name}.manifest.json",
        "legacy_shared": prediction_path.parent / "prediction_manifest.json",
    }


__all__ = [
    "RECOMMENDED_RECEIPT_FIELDS",
    "REQUIRED_RECEIPT_FIELDS",
    "ReceiptAudit",
    "audit_receipt",
    "detector_invariance_report",
    "method_sensitivity_report",
    "prediction_manifest_candidates",
]
