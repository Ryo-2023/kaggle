"""Ground truth must be unopenable until the prediction is durably persisted.

The pipeline currently records the ordering as a Python literal
(``prediction_manifest_validated_before_gt: True``) plus a timestamp.  Neither can
fail.  These tests exercise a guard that *can* fail, and — just as importantly — each
positive assertion is paired with a deliberately broken condition proving the guard
actually fires.

All fixtures here are **synthetic**: invented file bytes standing in for a prediction
GEFF directory and a ground-truth graph.  They carry no scientific meaning and are
never used in place of a measurement (AGENTS.md §8).  No detector, no checkpoint, no
``.zarr``, no ``tracksdata``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from biohub.reproducibility.digest import directory_digest_report
from biohub.reproducibility.gt_guard import (
    GroundTruthOrderingError,
    PredictionPersistedToken,
    mint_prediction_token,
    open_ground_truth,
    prediction_manifest_path,
    require_token,
    resolve_prediction_manifest,
)


def synthetic_prediction_dir(root: Path, name: str, payload: bytes = b"edges-and-nodes") -> Path:
    """Create a SYNTHETIC stand-in for a prediction ``.geff`` directory."""

    prediction = root / f"{name}.geff"
    (prediction / "nodes").mkdir(parents=True)
    (prediction / "edges").mkdir(parents=True)
    (prediction / "zarr.json").write_bytes(b'{"synthetic": true}')
    (prediction / "nodes" / "c0").write_bytes(payload)
    (prediction / "edges" / "c0").write_bytes(payload[::-1])
    return prediction


def synthetic_ground_truth(root: Path) -> Path:
    """Create a SYNTHETIC stand-in for a ground-truth ``.geff``."""

    gt = root / "synthetic_gt.geff"
    gt.mkdir(parents=True)
    (gt / "zarr.json").write_bytes(b'{"synthetic_ground_truth": true}')
    return gt


def write_manifest(prediction: Path, *, path: Path | None = None, **overrides: Any) -> Path:
    """Write a manifest describing *prediction*, with optional field overrides."""

    report = directory_digest_report(prediction)
    payload: dict[str, Any] = {
        "prediction_path": str(prediction),
        "directory_sha256": report["directory_sha256"],
        "files": report["files"],
        "total_bytes": report["total_bytes"],
        "ground_truth_included": False,
        "synthetic_fixture": True,
    }
    payload.update(overrides)
    target = path or prediction_manifest_path(prediction)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return target


class RecordingOpener:
    """An opener that records whether it was ever allowed to touch ground truth."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    def __call__(self, path: Path) -> str:
        self.calls.append(Path(path))
        return "opened-ground-truth"


# --------------------------------------------------------------------------------------
# The guard fires: no token, forged token, stale token.
# --------------------------------------------------------------------------------------


def test_ground_truth_cannot_be_opened_without_a_token(tmp_path: Path) -> None:
    gt = synthetic_ground_truth(tmp_path)
    opener = RecordingOpener()

    with pytest.raises(GroundTruthOrderingError, match="requires a PredictionPersistedToken"):
        open_ground_truth(gt, None, opener)

    assert opener.calls == [], "ground truth was opened despite the guard rejecting the call"


def test_forged_token_is_rejected(tmp_path: Path) -> None:
    """A hand-built look-alike must not pass; only the mint may issue authority."""

    prediction = synthetic_prediction_dir(tmp_path, "forged")
    gt = synthetic_ground_truth(tmp_path)
    opener = RecordingOpener()
    forged = PredictionPersistedToken(
        prediction_path=prediction,
        manifest_path=prediction_manifest_path(prediction),
        directory_sha256=directory_digest_report(prediction)["directory_sha256"],
        files=3,
        total_bytes=1,
        minted_at="2026-08-21T00:00:00+00:00",
        authority=object(),
    )

    assert forged.is_genuine() is False
    with pytest.raises(GroundTruthOrderingError, match="forged or not minted here"):
        open_ground_truth(gt, forged, opener)
    assert opener.calls == []


def test_token_requires_a_manifest_that_is_already_on_disk(tmp_path: Path) -> None:
    prediction = synthetic_prediction_dir(tmp_path, "unpersisted")

    with pytest.raises(GroundTruthOrderingError, match="no persisted prediction manifest"):
        mint_prediction_token(prediction)


def test_token_requires_the_manifest_to_name_this_prediction(tmp_path: Path) -> None:
    """Reproduces the shared-manifest clobber that the real race directory exhibits.

    ``write_prediction_manifest`` writes to ``<parent>/prediction_manifest.json``, so
    every method written into one output directory overwrites its siblings' evidence.
    The surviving file names only the last method.
    """

    first = synthetic_prediction_dir(tmp_path, "official_ilp", payload=b"aaa")
    second = synthetic_prediction_dir(tmp_path, "harmonic_v1", payload=b"bbb")
    shared = tmp_path / "prediction_manifest.json"

    write_manifest(first, path=shared)
    write_manifest(second, path=shared)  # second writer clobbers the first

    token = mint_prediction_token(second)
    assert token.prediction_path == second

    with pytest.raises(GroundTruthOrderingError, match="was overwritten by another prediction"):
        mint_prediction_token(first)


def test_per_prediction_manifest_survives_a_sibling_write(tmp_path: Path) -> None:
    """The fix: a manifest named after the prediction cannot be clobbered."""

    first = synthetic_prediction_dir(tmp_path, "official_ilp", payload=b"aaa")
    second = synthetic_prediction_dir(tmp_path, "harmonic_v1", payload=b"bbb")
    write_manifest(first)
    write_manifest(second)

    assert resolve_prediction_manifest(first).name == "official_ilp.geff.manifest.json"
    assert resolve_prediction_manifest(second).name == "harmonic_v1.geff.manifest.json"
    assert mint_prediction_token(first).prediction_path == first
    assert mint_prediction_token(second).prediction_path == second


def test_token_requires_ground_truth_included_false(tmp_path: Path) -> None:
    prediction = synthetic_prediction_dir(tmp_path, "leaky")
    write_manifest(prediction, ground_truth_included=True)

    with pytest.raises(GroundTruthOrderingError, match="ground_truth_included=false"):
        mint_prediction_token(prediction)


def test_token_rejects_a_prediction_edited_after_its_manifest_was_written(tmp_path: Path) -> None:
    prediction = synthetic_prediction_dir(tmp_path, "edited")
    write_manifest(prediction)
    (prediction / "edges" / "c0").write_bytes(b"rewritten-after-the-fact")

    with pytest.raises(GroundTruthOrderingError, match="changed after the manifest was written"):
        mint_prediction_token(prediction)


def test_token_is_invalidated_when_the_prediction_changes_after_minting(tmp_path: Path) -> None:
    """Minting is not enough; the bytes are re-hashed at the ground-truth open."""

    prediction = synthetic_prediction_dir(tmp_path, "mutated")
    gt = synthetic_ground_truth(tmp_path)
    write_manifest(prediction)
    token = mint_prediction_token(prediction)
    opener = RecordingOpener()

    (prediction / "nodes" / "c0").write_bytes(b"mutated-between-mint-and-evaluation")

    with pytest.raises(GroundTruthOrderingError, match="changed after its token was minted"):
        open_ground_truth(gt, token, opener)
    assert opener.calls == []


def test_guard_refuses_when_prediction_and_ground_truth_are_the_same_path(tmp_path: Path) -> None:
    prediction = synthetic_prediction_dir(tmp_path, "self")
    write_manifest(prediction)
    token = mint_prediction_token(prediction)

    with pytest.raises(GroundTruthOrderingError, match="must differ"):
        open_ground_truth(prediction, token, RecordingOpener())


# --------------------------------------------------------------------------------------
# The guard permits the sanctioned ordering, and emits recheckable evidence.
# --------------------------------------------------------------------------------------


def test_sanctioned_ordering_opens_ground_truth_and_returns_recheckable_evidence(tmp_path: Path) -> None:
    prediction = synthetic_prediction_dir(tmp_path, "ordered")
    gt = synthetic_ground_truth(tmp_path)
    write_manifest(prediction)
    token = mint_prediction_token(prediction)
    opener = RecordingOpener()

    opened, receipt = open_ground_truth(gt, token, opener)

    assert opened == "opened-ground-truth"
    assert opener.calls == [gt]
    independent = directory_digest_report(prediction)["directory_sha256"]
    assert receipt["prediction_directory_sha256"] == independent
    assert receipt["ground_truth_path"] == str(gt)


def test_evidence_is_a_digest_not_a_constant(tmp_path: Path) -> None:
    """A boolean that is always ``True`` carries no information; a digest does.

    This is the substantive complaint against
    ``prediction_manifest_validated_before_gt``: two different predictions must not be
    able to produce the same ordering evidence.
    """

    gt = synthetic_ground_truth(tmp_path)
    receipts = []
    for name, payload in (("m1", b"aaa"), ("m2", b"bbb")):
        prediction = synthetic_prediction_dir(tmp_path, name, payload=payload)
        write_manifest(prediction)
        _, receipt = open_ground_truth(gt, mint_prediction_token(prediction), RecordingOpener())
        receipts.append(receipt)

    assert "prediction_manifest_validated_before_gt" not in receipts[0]
    assert receipts[0]["prediction_directory_sha256"] != receipts[1]["prediction_directory_sha256"]


# --------------------------------------------------------------------------------------
# Test-the-test: show the unguarded ordering is genuinely undetectable.
# --------------------------------------------------------------------------------------


def unguarded_evaluate(prediction: Path, gt: Path, opener: RecordingOpener) -> dict[str, Any]:
    """Mimic the current code path with the two statements swapped.

    ``evaluate_prediction`` validates the manifest on one line and opens ground truth on
    the next, then stamps a constant.  Swap the lines and the stamp is unchanged — which
    is exactly why the stamp is not an invariant.
    """

    opener(gt)  # ground truth opened FIRST
    write_manifest(prediction)  # manifest persisted afterwards
    return {
        "prediction_manifest_validated_before_gt": True,
        "prediction_manifest_validated_at": "2026-08-21T00:00:00+00:00",
    }


def test_unguarded_flag_stays_true_even_when_ground_truth_is_opened_first(tmp_path: Path) -> None:
    prediction = synthetic_prediction_dir(tmp_path, "out_of_order")
    gt = synthetic_ground_truth(tmp_path)
    opener = RecordingOpener()

    metrics = unguarded_evaluate(prediction, gt, opener)

    # The recorded claim is indistinguishable from a correctly ordered run.
    assert metrics["prediction_manifest_validated_before_gt"] is True
    assert opener.calls == [gt], "ground truth really was read before the manifest existed"


def test_guard_rejects_the_same_out_of_order_sequence(tmp_path: Path) -> None:
    """The condition the constant could not detect is the condition the guard rejects."""

    prediction = synthetic_prediction_dir(tmp_path, "out_of_order_guarded")
    gt = synthetic_ground_truth(tmp_path)
    opener = RecordingOpener()

    with pytest.raises(GroundTruthOrderingError):
        token = mint_prediction_token(prediction)  # manifest does not exist yet
        open_ground_truth(gt, token, opener)

    assert opener.calls == []


def test_require_token_rejects_every_non_token_object(tmp_path: Path) -> None:
    for candidate in (None, True, "token", 1, {"directory_sha256": "0" * 64}, object()):
        with pytest.raises(GroundTruthOrderingError):
            require_token(candidate)
