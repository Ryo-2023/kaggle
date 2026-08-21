"""Every persisted metric must arrive with a receipt that can rebuild its conditions.

The required set is source commit, checkpoint SHA-256, cache digest, device and
command.  These tests first prove the auditor works — one deliberately dropped field at
a time — then apply it to the receipts actually on disk and pin exactly which facts are
missing today, so a later change either fixes the gap or fails this file.

Fixtures are **verbatim copies of real receipts**; payloads the tests build are labelled
``synthetic_``.  No detector inference, no checkpoint, no ``.zarr``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from biohub.reproducibility.receipts import (
    RECOMMENDED_RECEIPT_FIELDS,
    REQUIRED_RECEIPT_FIELDS,
    audit_receipt,
)

FIXTURES = Path(__file__).parent / "fixtures" / "reproducibility" / "real_receipts"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def synthetic_complete_receipt() -> dict[str, Any]:
    """A SYNTHETIC receipt carrying every required and recommended fact."""

    return {
        "synthetic_fixture": True,
        "source_commit": "075fc5f5a52d11077f9dc2b074644618f26939e2",
        "checkpoint_sha256": "3" * 64,
        "cache_hash": "0" * 64,
        "device": "cpu",
        "command": ["python", "-m", "biohub.detector_fixed_race.cli", "associate"],
        "association_code_sha256": "a" * 64,
        "seed": 0,
        "torch_version": "2.13.0+cpu",
    }


def drop(payload: dict[str, Any], field_name: str) -> dict[str, Any]:
    """Return *payload* with every alias of *field_name* removed."""

    from biohub.reproducibility.receipts import _FIELD_ALIASES

    return {key: value for key, value in payload.items() if key not in _FIELD_ALIASES[field_name]}


# --------------------------------------------------------------------------------------
# The auditor itself.
# --------------------------------------------------------------------------------------


def test_complete_receipt_passes() -> None:
    audit = audit_receipt("synthetic", [synthetic_complete_receipt()])

    assert audit.complete is True
    assert audit.missing_required == ()
    assert audit.missing_recommended == ()


@pytest.mark.parametrize("field_name", REQUIRED_RECEIPT_FIELDS)
def test_dropping_one_required_field_is_detected(field_name: str) -> None:
    """Test-the-test: an auditor that asserts nothing would pass all five of these."""

    audit = audit_receipt("synthetic", [drop(synthetic_complete_receipt(), field_name)])

    assert audit.complete is False
    assert audit.missing_required == (field_name,)


@pytest.mark.parametrize("field_name", RECOMMENDED_RECEIPT_FIELDS)
def test_dropping_one_recommended_field_is_detected(field_name: str) -> None:
    audit = audit_receipt("synthetic", [drop(synthetic_complete_receipt(), field_name)])

    assert audit.complete is True, "recommended fields must not make a receipt inadmissible"
    assert audit.missing_recommended == (field_name,)


def test_blank_and_empty_values_do_not_count_as_recorded() -> None:
    payload = synthetic_complete_receipt()
    payload["device"] = "   "
    payload["command"] = []

    audit = audit_receipt("synthetic", [payload])

    assert set(audit.missing_required) == {"device", "command"}


def test_facts_are_found_when_split_across_several_receipt_files() -> None:
    """Provenance may live in sibling files; the auditor must follow all of them."""

    metrics = {"final_score": 0.5}
    run = {"device": "cpu", "command": ["python", "predict.py"], "source_commit": "0" * 40}
    cache = {"checkpoint_sha256": "3" * 64, "cache_hash": "0" * 64}

    assert audit_receipt("split", [metrics, run, cache]).complete is True


def test_nested_values_are_found() -> None:
    nested = {"metrics": {"x": 1}, "provenance": {"device": "cpu", "checkpoint_sha256": "3" * 64}}
    flat = {"source_commit": "0" * 40, "cache_hash": "0" * 64, "command": ["run"]}

    assert audit_receipt("nested", [nested, flat]).complete is True


# --------------------------------------------------------------------------------------
# The receipts that are actually on disk.
# --------------------------------------------------------------------------------------


@pytest.fixture
def race_records() -> list[dict[str, Any]]:
    return load("dev_full_auto_compact_timed_race_receipt.json")


@pytest.fixture
def cache_manifest() -> dict[str, Any]:
    return load("full_auto_cache_manifest.json")


def test_race_receipt_alone_cannot_rebuild_the_run(race_records: list[dict[str, Any]]) -> None:
    """The four-method race receipt records the cache digest and nothing else."""

    audit = audit_receipt("race_receipt", [race_records])

    assert set(audit.missing_required) == {"source_commit", "checkpoint_sha256", "device", "command"}
    assert audit.found["cache_digest"] == "0bc38739fa40d5dc38db99ec52a7ea5891849a6520d95ecbeed9bc126c6a62a8"


def test_race_receipt_plus_cache_manifest_still_omits_the_command(
    race_records: list[dict[str, Any]],
    cache_manifest: dict[str, Any],
) -> None:
    """Attaching the cache manifest recovers four of five facts; ``command`` is nowhere.

    The association CLI never records its argv, so the method list, output root and
    upstream root used for a race cannot be recovered from any persisted file.
    """

    audit = audit_receipt("race+cache", [race_records, cache_manifest])

    assert audit.missing_required == ("command",)
    assert audit.found["device"] == "cpu"
    assert audit.found["source_commit"] == "075fc5f5a52d11077f9dc2b074644618f26939e2"


def test_race_receipt_records_no_association_code_identity(
    race_records: list[dict[str, Any]],
    cache_manifest: dict[str, Any],
) -> None:
    """The association code is the thing that changes most and is tracked least.

    ``adapter_source_sha256`` covers only ``upstream_adapter.py``.  Edits to
    ``association.py``, ``prediction.py`` or ``harmonic.py`` — which decide the numbers
    being compared — leave no trace in any receipt.
    """

    audit = audit_receipt("race+cache", [race_records, cache_manifest])
    assert "association_code_sha256" in audit.missing_recommended
    assert "seed" in audit.missing_recommended
    assert "torch_version" in audit.missing_recommended

    # And prove the auditor would find it if it were ever written.
    with_code = audit_receipt("race+cache+code", [race_records, cache_manifest, {"association_code_sha256": "b" * 64}])
    assert "association_code_sha256" not in with_code.missing_recommended


@pytest.mark.parametrize("method", ["official_ilp", "harmonic_ilp"])
def test_strong_baseline_run_receipts_record_everything_but_a_cache_digest(method: str) -> None:
    """The direct upstream runs are the better-documented path: argv and device included."""

    payloads = [load(f"strong_baseline_v1_{method}_metrics.json"), load(f"strong_baseline_v1_{method}_run.json")]

    audit = audit_receipt(method, payloads)

    assert audit.missing_required == ("cache_digest",)
    assert audit.found["device"] == "cpu"
    assert audit.found["source_commit"] == "075fc5f5a52d11077f9dc2b074644618f26939e2"
    assert audit.found["checkpoint_sha256"] == (
        "347915de9c33883cb2ee69832a8e4552c88b1ec692d0fbfe956422467d3d4235"
    )


def test_only_one_of_the_two_headline_runs_has_a_source_receipt() -> None:
    """`official_ilp` never wrote one; the asymmetry is invisible without a check."""

    assert (FIXTURES / "strong_baseline_v1_harmonic_ilp_source_receipt.json").is_file()
    assert not (FIXTURES / "strong_baseline_v1_official_ilp_source_receipt.json").is_file()


def test_persisted_prediction_manifests_disagree_about_their_own_schema() -> None:
    """Two evaluation paths, two manifest shapes, two unconditional booleans."""

    strong = load("strong_baseline_v1_official_ilp_prediction_manifest.json")
    race = load("dev_full_auto_compact_timed_prediction_manifest.json")

    # The detector-fixed path requires this field; the strong-baseline path omits it,
    # so the same file cannot be validated by both evaluators.
    assert "ground_truth_included" not in strong
    assert race["ground_truth_included"] is False

    # Absolute vs repo-relative: validate_prediction_manifest resolves the recorded path
    # against the process CWD, so the race manifest only validates from the repo root.
    assert Path(strong["prediction_path"]).is_absolute()
    assert not Path(race["prediction_path"]).is_absolute()

    # Both carry a boolean that is written unconditionally and therefore always true.
    assert strong["hash_persisted_before_evaluation"] is True


def test_shared_prediction_manifest_describes_only_the_last_method_written(
    race_records: list[dict[str, Any]],
) -> None:
    """The clobber, in the persisted evidence.

    All four race records point at one ``prediction_manifest.json``.  The surviving file
    names ``motion_gated``, so the ordering evidence for the other three methods —
    including the headline ``harmonic_v1`` — no longer exists on disk.
    """

    shared = load("dev_full_auto_compact_timed_prediction_manifest.json")
    quoted = {record["prediction_manifest_path"] for record in race_records}

    assert len(quoted) == 1, "each method should own a distinct manifest path"
    assert shared["method_id"] == "motion_gated"

    described = {
        record["method_id"]
        for record in race_records
        if record["metrics"]["prediction_manifest_directory_sha256"] == shared["directory_sha256"]
    }
    assert described == {"motion_gated"}

    orphaned = {record["method_id"] for record in race_records} - described
    assert orphaned == {"official_ilp", "harmonic_v1", "mutual_confidence"}
