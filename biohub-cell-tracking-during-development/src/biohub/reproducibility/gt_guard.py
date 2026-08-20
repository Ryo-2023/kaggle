"""Enforce, not record, the prediction-before-ground-truth ordering.

The existing pipeline stamps ``prediction_manifest_validated_before_gt: True`` into
every ``metrics.json``.  That literal is written unconditionally; it stays ``True`` if
the ground-truth open is moved above the validation call, if the manifest describes a
different prediction, or if the prediction bytes change afterwards.  A timestamp and a
constant are evidence of intent, not an invariant.

This module makes the ordering structural.  :func:`open_ground_truth` cannot be called
without a :class:`PredictionPersistedToken`, and a token can only be minted by
:func:`mint_prediction_token`, which requires that

1. a prediction manifest already exists **on disk** and names *this* prediction,
2. the manifest's recorded digest equals the digest of the prediction bytes now, and
3. the manifest declares ``ground_truth_included = false``.

The token is re-verified at ground-truth-open time, so a prediction that is rewritten
or clobbered between persistence and evaluation fails loudly instead of producing a
silently incomparable number.  The digest the token carries is what belongs in the
receipt: a value another process can recheck, replacing the unconditional boolean.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from biohub.reproducibility.digest import directory_digest_report

T = TypeVar("T")

# Private, module-local sentinel.  A token is only genuine when it carries this exact
# object, so a caller cannot hand-build a look-alike dataclass and bypass the mint.
_MINT_AUTHORITY = object()

#: Manifest filename that is unique per prediction.  ``prediction_manifest.json`` is
#: shared by every prediction written into the same directory, so the last writer wins
#: and the earlier predictions lose their persistence evidence.
PER_PREDICTION_MANIFEST_SUFFIX = ".manifest.json"

LEGACY_SHARED_MANIFEST_NAME = "prediction_manifest.json"


class GroundTruthOrderingError(RuntimeError):
    """Raised when ground truth would be opened outside the sanctioned ordering."""


@dataclass(frozen=True, slots=True)
class PredictionPersistedToken:
    """Proof that a specific prediction was fully persisted before this moment."""

    prediction_path: Path
    manifest_path: Path
    directory_sha256: str
    files: int
    total_bytes: int
    minted_at: str
    authority: object

    def is_genuine(self) -> bool:
        """Return whether this token was produced by :func:`mint_prediction_token`."""

        return self.authority is _MINT_AUTHORITY


def prediction_manifest_path(prediction_path: Path) -> Path:
    """Return the per-prediction manifest path that cannot be clobbered by a sibling."""

    prediction_path = Path(prediction_path)
    return prediction_path.parent / f"{prediction_path.name}{PER_PREDICTION_MANIFEST_SUFFIX}"


def _same_prediction(recorded: str, prediction_path: Path) -> bool:
    """Compare a manifest's ``prediction_path`` to *prediction_path*, CWD-independently.

    Manifests in the tree store repo-relative paths, so ``Path(recorded).resolve()``
    only agrees with an absolute prediction path when the process happens to run from
    the repository root.  Compare the trailing components instead.
    """

    recorded_path = Path(recorded)
    actual = Path(prediction_path)
    if recorded_path.is_absolute() and actual.is_absolute():
        return recorded_path.resolve() == actual.resolve()
    recorded_parts = recorded_path.parts
    actual_parts = actual.resolve().parts
    if not recorded_parts:
        return False
    depth = min(len(recorded_parts), len(actual_parts))
    return recorded_parts[-depth:] == actual_parts[-depth:]


def _read_manifest(manifest_path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GroundTruthOrderingError(f"prediction manifest is unreadable: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise GroundTruthOrderingError(f"prediction manifest must contain an object: {manifest_path}")
    return payload


def resolve_prediction_manifest(prediction_path: Path) -> Path:
    """Return the manifest that describes *prediction_path*, or fail loudly.

    Prefers the per-prediction manifest.  The legacy shared
    ``prediction_manifest.json`` is accepted only when it actually names this
    prediction; when a sibling method overwrote it, that is a hard error rather than a
    silently reused receipt.
    """

    prediction_path = Path(prediction_path)
    preferred = prediction_manifest_path(prediction_path)
    if preferred.is_file():
        return preferred
    legacy = prediction_path.parent / LEGACY_SHARED_MANIFEST_NAME
    if not legacy.is_file():
        raise GroundTruthOrderingError(
            f"no persisted prediction manifest for {prediction_path}; "
            f"looked for {preferred.name} and {LEGACY_SHARED_MANIFEST_NAME}",
        )
    payload = _read_manifest(legacy)
    recorded = payload.get("prediction_path")
    if not isinstance(recorded, str):
        raise GroundTruthOrderingError(f"prediction manifest is missing prediction_path: {legacy}")
    if not _same_prediction(recorded, prediction_path):
        raise GroundTruthOrderingError(
            f"shared prediction manifest {legacy} describes {recorded!r}, not {prediction_path}; "
            "the manifest was overwritten by another prediction in the same directory",
        )
    return legacy


def mint_prediction_token(prediction_path: Path) -> PredictionPersistedToken:
    """Mint a token proving *prediction_path* is persisted, complete and unmodified."""

    prediction_path = Path(prediction_path)
    if not prediction_path.exists():
        raise GroundTruthOrderingError(f"prediction does not exist: {prediction_path}")
    manifest_path = resolve_prediction_manifest(prediction_path)
    payload = _read_manifest(manifest_path)

    recorded = payload.get("prediction_path")
    if not isinstance(recorded, str) or not _same_prediction(recorded, prediction_path):
        raise GroundTruthOrderingError(
            f"prediction manifest {manifest_path} does not describe {prediction_path}",
        )
    if payload.get("ground_truth_included") is not False:
        raise GroundTruthOrderingError(
            f"prediction manifest must set ground_truth_included=false: {manifest_path}",
        )

    report = directory_digest_report(prediction_path)
    for key in ("directory_sha256", "files", "total_bytes"):
        if payload.get(key) != report[key]:
            raise GroundTruthOrderingError(
                f"prediction {key} changed after the manifest was written: "
                f"manifest {payload.get(key)!r}, bytes {report[key]!r}",
            )

    return PredictionPersistedToken(
        prediction_path=prediction_path,
        manifest_path=manifest_path,
        directory_sha256=str(report["directory_sha256"]),
        files=int(report["files"]),
        total_bytes=int(report["total_bytes"]),
        minted_at=datetime.now(UTC).isoformat(),
        authority=_MINT_AUTHORITY,
    )


def require_token(token: object) -> PredictionPersistedToken:
    """Reject anything that is not a genuine, still-valid persistence token."""

    if token is None:
        raise GroundTruthOrderingError(
            "ground truth requires a PredictionPersistedToken; the prediction must be "
            "persisted and manifest-verified first",
        )
    if not isinstance(token, PredictionPersistedToken) or not token.is_genuine():
        raise GroundTruthOrderingError("prediction persistence token is forged or not minted here")
    current = directory_digest_report(token.prediction_path)
    if current["directory_sha256"] != token.directory_sha256:
        raise GroundTruthOrderingError(
            f"prediction {token.prediction_path} changed after its token was minted: "
            f"token {token.directory_sha256}, bytes {current['directory_sha256']}",
        )
    return token


def open_ground_truth(
    gt_path: Path,
    token: object,
    opener: Callable[[Path], T],
) -> tuple[T, dict[str, Any]]:
    """Open ground truth only behind a verified persistence token.

    Returns the opened object and a receipt fragment.  The fragment carries a digest a
    third party can recheck, which is what ``prediction_manifest_validated_before_gt``
    should have been instead of a constant.
    """

    verified = require_token(token)
    gt_path = Path(gt_path)
    if not gt_path.exists():
        raise GroundTruthOrderingError(f"ground truth does not exist: {gt_path}")
    if Path(verified.prediction_path).resolve() == gt_path.resolve():
        raise GroundTruthOrderingError("prediction path and ground-truth path must differ")
    opened = opener(gt_path)
    receipt = {
        "prediction_path": str(verified.prediction_path),
        "prediction_manifest_path": str(verified.manifest_path),
        "prediction_directory_sha256": verified.directory_sha256,
        "prediction_files": verified.files,
        "prediction_total_bytes": verified.total_bytes,
        "prediction_persisted_at": verified.minted_at,
        "ground_truth_path": str(gt_path),
        "ground_truth_opened_at": datetime.now(UTC).isoformat(),
        "ordering_enforced_by": "biohub.reproducibility.gt_guard.open_ground_truth",
        "ordering_evidence": (
            "prediction bytes re-hashed to prediction_directory_sha256 immediately "
            "before this ground-truth open"
        ),
    }
    return opened, receipt


__all__ = [
    "LEGACY_SHARED_MANIFEST_NAME",
    "PER_PREDICTION_MANIFEST_SUFFIX",
    "GroundTruthOrderingError",
    "PredictionPersistedToken",
    "mint_prediction_token",
    "open_ground_truth",
    "prediction_manifest_path",
    "require_token",
    "resolve_prediction_manifest",
]
