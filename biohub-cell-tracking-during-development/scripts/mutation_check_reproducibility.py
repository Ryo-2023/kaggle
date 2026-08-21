"""Mutation-test the reproducibility guards: break one condition, expect a failure.

A passing test suite proves nothing unless the tests can fail.  This harness disables
one guard at a time in ``src/biohub/reproducibility/`` and asserts that the test which
is supposed to notice actually goes red.  Every file is restored from an in-memory
backup afterwards, so the tree is byte-identical before and after.

    /opt/venv/bin/python scripts/mutation_check_reproducibility.py

Exit status is 0 only when every mutation was caught.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "biohub" / "reproducibility"
TESTS = ROOT / "tests"

_MISSING_CREATED_AT = (
    '    if not isinstance(value, str) or not value.strip():\n'
    '        raise GroundTruthOrderingError(f"prediction manifest is missing {field}")'
)
_TOLERATE_CREATED_AT = (
    '    if not isinstance(value, str) or not value.strip():\n'
    '        value = "2020-01-01T00:00:00+00:00"'
)
_REJECT_WRONG_MANIFEST = (
    '    if not _same_prediction(recorded, prediction_path):\n'
    '        raise GroundTruthOrderingError(\n'
    '            f"shared prediction manifest'
)
_ACCEPT_WRONG_MANIFEST = (
    '    if False:\n'
    '        raise GroundTruthOrderingError(\n'
    '            f"shared prediction manifest'
)

#: ``(label, file, anchor, replacement, test node that MUST fail)``
MUTATIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "guard accepts a missing token",
        SRC / "gt_guard.py",
        "    if token is None:\n        raise GroundTruthOrderingError(",
        "    if False:\n        raise GroundTruthOrderingError(",
        "test_reproducibility_gt_ordering.py::test_ground_truth_cannot_be_opened_without_a_token",
    ),
    (
        "mint skips the digest comparison",
        SRC / "gt_guard.py",
        "        if payload.get(key) != report[key]:",
        "        if False and payload.get(key) != report[key]:",
        "test_reproducibility_gt_ordering.py::"
        "test_token_rejects_a_prediction_edited_after_its_manifest_was_written",
    ),
    (
        "shared manifest accepted for the wrong prediction",
        SRC / "gt_guard.py",
        _REJECT_WRONG_MANIFEST,
        _ACCEPT_WRONG_MANIFEST,
        "test_reproducibility_gt_ordering.py::test_token_requires_the_manifest_to_name_this_prediction",
    ),
    (
        "missing manifest_created_at tolerated",
        SRC / "gt_guard.py",
        _MISSING_CREATED_AT,
        _TOLERATE_CREATED_AT,
        "test_reproducibility_gt_ordering.py::test_manifest_without_a_creation_time_is_rejected",
    ),
    (
        "token forgery check removed",
        SRC / "gt_guard.py",
        "    if not isinstance(token, PredictionPersistedToken) or not token.is_genuine():",
        "    if not isinstance(token, PredictionPersistedToken):",
        "test_reproducibility_gt_ordering.py::test_forged_token_is_rejected",
    ),
    (
        "detector content invariant hardcoded true",
        SRC / "cache_identity.py",
        '        "detector_content_invariant_holds": (not same_inputs) or same_outputs,',
        '        "detector_content_invariant_holds": True,',
        "test_reproducibility_cache_identity.py::test_invariant_fails_when_the_rewrite_would_have_changed_one_node",
    ),
    (
        "content digest stops covering detector_config",
        SRC / "cache_identity.py",
        '    payload["detector_config"] = manifest.get("detector_config")',
        '    payload["detector_config"] = None',
        "test_reproducibility_cache_identity.py::test_content_input_digest_moves_for_every_detector_setting",
    ),
    (
        "detector invariance hardcoded true",
        SRC / "receipts.py",
        '        "invariant_holds": not missing and len(distinct) == 1,',
        '        "invariant_holds": True,',
        "test_reproducibility_detector_invariance.py::test_invariance_check_fails_when_one_method_moves_the_detector",
    ),
    (
        "method sensitivity hardcoded true",
        SRC / "receipts.py",
        '        "invariant_holds": not missing and not collisions,',
        '        "invariant_holds": True,',
        "test_reproducibility_method_sensitivity.py::test_sensitivity_check_fails_when_two_methods_collide",
    ),
    (
        "receipt auditor reports nothing missing",
        SRC / "receipts.py",
        "        if value is None:\n            missing_required.append(name)",
        "        if False:\n            missing_required.append(name)",
        "test_reproducibility_receipt_completeness.py::test_dropping_one_required_field_is_detected",
    ),
    (
        "directory digest ignores file bytes",
        SRC / "digest.py",
        "        digest.update(payload)",
        "        payload = payload",
        "test_reproducibility_method_sensitivity.py::"
        "test_recorded_prediction_digests_still_match_the_bytes_on_disk",
    ),
    (
        "prediction identity check accepts any stem",
        SRC / "receipts.py",
        "    matches_sample = stem == sample_id or stem.startswith(f\"{sample_id}\")",
        "    matches_sample = True",
        "test_reproducibility_submission_identity.py::test_identity_check_rejects_a_method_named_prediction",
    ),
    (
        "device consistency check disabled",
        SRC / "receipts.py",
        '        if requested not in (None, "auto") and actual is not None and requested != actual:',
        "        if False:",
        "test_reproducibility_receipt_completeness.py::test_device_audit_flags_a_requested_device_that_was_not_used",
    ),
]


def run(node: str) -> int:
    file_part, _, test_part = node.partition("::")
    target = str(TESTS / file_part) + (f"::{test_part}" if test_part else "")
    result = subprocess.run(
        ["/opt/venv/bin/python", "-m", "pytest", target, "-q", "-p", "no:cacheprovider", "--no-header", "-x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={
            "PYTHONPATH": str(ROOT / "src"),
            "PATH": "/usr/bin:/bin",
            "HOME": "/root",
            "OMP_NUM_THREADS": "1",
        },
    )
    return result.returncode


def main() -> int:
    backups: dict[pathlib.Path, str] = {}
    for _, path, _, _, _ in MUTATIONS:
        backups.setdefault(path, path.read_text())

    uncaught: list[str] = []
    try:
        for label, path, anchor, replacement, node in MUTATIONS:
            original = backups[path]
            if anchor not in original:
                print(f"SKIP        {label}: anchor not found in {path.name}")
                uncaught.append(label)
                continue
            path.write_text(original.replace(anchor, replacement, 1))
            code = run(node)
            path.write_text(original)
            print(f"{'CAUGHT' if code else 'NOT CAUGHT':<11} {label}\n            -> {node}")
            if code == 0:
                uncaught.append(label)
    finally:
        for path, text in backups.items():
            path.write_text(text)

    print()
    print(f"{len(MUTATIONS) - len(uncaught)}/{len(MUTATIONS)} mutations caught")
    return 1 if uncaught else 0


if __name__ == "__main__":
    sys.exit(main())
