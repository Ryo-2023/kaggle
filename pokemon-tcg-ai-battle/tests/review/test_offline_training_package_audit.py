"""Independent submission-package and screening-statistics audit.

Verifies byte-reproducible double builds, the member allowlist, canonical tar
metadata, gzip determinism, training-artifact exclusion, clean-room behaviour,
and recomputes the screening counters from their own per-game records.
"""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.offline_training import export as export_mod
from mage_ptcg.offline_training import package

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def built(tmp_path_factory, review_export_document):
    root = tmp_path_factory.mktemp("pkg")
    export_path = root / "export.json"
    export_mod.write_export(review_export_document, export_path)
    manifests = []
    for name in ("a", "b"):
        manifest = package.build_package(
            export_path=export_path, output_dir=root / name,
            repository_root=REPO_ROOT, build_commit="review-audit",
        )
        manifests.append((root / name, manifest))
    return manifests


def test_double_build_is_byte_reproducible(built):
    (dir_a, man_a), (dir_b, man_b) = built
    bytes_a = (dir_a / package.ARCHIVE_NAME).read_bytes()
    bytes_b = (dir_b / package.ARCHIVE_NAME).read_bytes()
    assert man_a["archive_sha256"] == man_b["archive_sha256"]
    assert bytes_a == bytes_b, "two builds of the same export differ byte-wise"


def test_member_allowlist_and_training_artifact_exclusion(built):
    (dir_a, man_a), _ = built
    with tarfile.open(dir_a / package.ARCHIVE_NAME, "r:gz") as archive:
        names = [m.name for m in archive.getmembers()]
    expected = list(package.RUNTIME_PATHS) + [package.MODEL_MEMBER]
    assert names == expected, "archive members deviate from the fixed allowlist"
    assert len(names) == len(set(names)), "duplicate archive member"
    forbidden_fragments = ("torch", "checkpoint", ".pt", "optimizer", "dataset.jsonl",
                          "private_bindings", "rule-bc-v1", "neural.py", "package.py", "cli.py")
    for name in names:
        assert not any(fragment in name for fragment in forbidden_fragments), name
        # no path traversal / absolute members
        assert not name.startswith("/") and ".." not in name.split("/")


def test_member_metadata_is_canonical(built):
    (dir_a, _), _ = built
    with tarfile.open(dir_a / package.ARCHIVE_NAME, "r:gz") as archive:
        for member in archive.getmembers():
            assert member.isreg(), f"{member.name} is not a regular file"
            assert not member.issym() and not member.islnk()
            assert member.mode == 0o644 and member.uid == 0 and member.gid == 0
            assert member.mtime == 0 and member.uname == "" and member.gname == ""


def test_gzip_header_has_zero_timestamp(built):
    (dir_a, _), _ = built
    raw = (dir_a / package.ARCHIVE_NAME).read_bytes()
    assert raw[:2] == b"\x1f\x8b"
    assert raw[4:8] == b"\x00\x00\x00\x00", "gzip MTIME field is not zero"


def test_no_torch_import_reachable_from_archive_runtime(built):
    """Every packaged python module must be importable without torch installed;
    statically require that no member contains a module-level `import torch`."""
    (dir_a, _), _ = built
    with tarfile.open(dir_a / package.ARCHIVE_NAME, "r:gz") as archive:
        for member in archive.getmembers():
            if not member.name.endswith(".py"):
                continue
            data = archive.extractfile(member).read().decode("utf-8")
            for line in data.splitlines():
                stripped = line.strip()
                if stripped.startswith(("import torch", "from torch")):
                    indent = len(line) - len(line.lstrip())
                    assert indent > 0, f"module-level torch import in {member.name}"


def test_model_member_matches_export_hash(built):
    (dir_a, manifest), _ = built
    with tarfile.open(dir_a / package.ARCHIVE_NAME, "r:gz") as archive:
        payload = json.loads(archive.extractfile(package.MODEL_MEMBER).read().decode("utf-8"))
    assert payload["model_hash"] == manifest["model_hash"]
    export_mod.validate_export(payload)


def test_clean_room_verify_detects_archive_tamper(built, tmp_path):
    import shutil

    (dir_a, _), _ = built
    tampered = tmp_path / "tampered"
    shutil.copytree(dir_a, tampered)
    data = (tampered / package.ARCHIVE_NAME).read_bytes()
    # Re-gzip with an extra member appended
    inner = gzip.decompress(data)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(inner)
        # any byte change breaks the recorded archive hash
        gz.write(b"\x00")
    (tampered / package.ARCHIVE_NAME).write_bytes(buf.getvalue())
    with pytest.raises(package.PackageError):
        package.clean_room_verify(tampered)


def test_clean_room_report_is_measured_not_hardcoded(built):
    """REV-I1 fix: the report's rate must be computed from executed cases.  A
    mutation that reintroduces the constant is detected both statically (no
    hardcoded rate in source) and dynamically (counters must be consistent)."""
    import inspect

    source = inspect.getsource(package.clean_room_verify)
    assert '"legal_action_rate": 1.0' not in source, "legal_action_rate is hardcoded again"
    (_dir_a, manifest), _ = built
    report = manifest["clean_room"]
    assert report["executed_cases"] > 1, "clean room must execute multiple decision cases"
    assert report["legal_cases"] + report["illegal_cases"] + report["exception_cases"] == report["executed_cases"]
    assert report["legal_action_rate"] == pytest.approx(report["legal_cases"] / report["executed_cases"])
    assert report["fallback_cases"] >= 1, "the missing-model fallback lane was not exercised"


# --------------------------------------------------------------------------- #
# measure_legality: the counting contract behind the clean-room report
# --------------------------------------------------------------------------- #


def _obs(option_count, min_count=1, max_count=1):
    return {"select": {"type": 0, "context": 0,
                       "option": [{"type": 14}] * option_count,
                       "minCount": min_count, "maxCount": max_count}}


def test_measure_legality_all_legal():
    result = package.measure_legality(lambda obs: [0], [_obs(2), _obs(3), _obs(4)])
    assert result == {"executed_cases": 3, "legal_cases": 3, "illegal_cases": 0,
                      "exception_cases": 0, "legal_action_rate": 1.0}


def test_measure_legality_mixed_legal_and_illegal():
    answers = iter([[0], [9], [0, 0], [1]])
    result = package.measure_legality(lambda obs: next(answers), [_obs(2)] * 4)
    assert result["executed_cases"] == 4
    assert result["legal_cases"] == 2      # [0] and [1]
    assert result["illegal_cases"] == 2    # out-of-range and duplicate
    assert result["legal_action_rate"] == pytest.approx(0.5)


def test_measure_legality_counts_exceptions():
    def flaky(obs):
        raise ValueError("boom")

    result = package.measure_legality(flaky, [_obs(2), _obs(2)])
    assert result["exception_cases"] == 2
    assert result["legal_cases"] == 0
    assert result["legal_action_rate"] == 0.0


def test_measure_legality_zero_cases_is_unmeasured_not_one():
    result = package.measure_legality(lambda obs: [0], [])
    assert result["executed_cases"] == 0
    assert result["legal_action_rate"] is None, "an unmeasured rate must never be 1.0"


def test_measure_legality_respects_selection_bounds():
    # too few for minCount=2, then a valid pair
    answers = iter([[0], [0, 1]])
    result = package.measure_legality(lambda obs: next(answers), [_obs(3, min_count=2, max_count=2)] * 2)
    assert result["legal_cases"] == 1 and result["illegal_cases"] == 1


# --------------------------------------------------------------------------- #
# Screening statistics: recompute aggregates from per-game records
# --------------------------------------------------------------------------- #


def test_tiny_screening_counters_match_reference(review_export_document):
    from mage_ptcg.offline_training.evaluate import tiny_screening

    report = tiny_screening(export_document=review_export_document, deck=[1] * 60, games=6, base_seed=500)
    games = report["per_game"]
    assert len(games) == 6
    decisions = sum(g["decisions"] for g in games)
    legal = sum(g["legal_actions"] for g in games)
    fallbacks = sum(g["fallback_count"] for g in games)
    assert report["legal_action_rate"] == pytest.approx(legal / decisions)
    assert report["fallback_rate"] == pytest.approx(fallbacks / decisions)
    assert report["invalid"] == sum(g["invalid"] for g in games)
    # exact seat balance for an even game count
    assert report["seat_balance"] == {"seat0": 3, "seat1": 3}
    assert [g["seat"] for g in games] == [0, 1, 0, 1, 0, 1]
    # the fixture harness must never claim a win rate
    assert report["wins"] is None and report["overall_win_rate"] is None
    assert report["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert report["actual_cabt"] == "ACTUAL_CABT_NOT_RUN"


def test_tiny_screening_rejects_odd_games(review_export_document):
    from mage_ptcg.offline_training.evaluate import tiny_screening

    with pytest.raises(ValueError):
        tiny_screening(export_document=review_export_document, deck=[1] * 60, games=3, base_seed=1)
