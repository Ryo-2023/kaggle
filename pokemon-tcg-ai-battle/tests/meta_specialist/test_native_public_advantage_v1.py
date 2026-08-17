import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from mage_ptcg.meta_specialist.meta_distribution_v1 import (
    MetaDistributionManifestV1,
    MetaDistributionRowV1,
    MetaSourceArtifactV1,
    save_meta_distribution_manifest_v1,
)
from mage_ptcg.meta_specialist.native_public_advantage_v1 import (
    NativePublicAdvantageError,
    PublicAdvantageTableV1,
    build_native_public_advantage_policy_v1,
    build_public_advantage_table_v1,
)


STATE_A = "a" * 64
STATE_B = "b" * 64
ACTION_0 = "0" * 64
ACTION_1 = "1" * 64
ACTION_2 = "2" * 64
BASELINE_SHA = "f" * 64
CONFIG_SHA = "e" * 64


def _manifest(tmp_path: Path) -> Path:
    source = tmp_path / "source.json"
    source.write_text("{}", encoding="utf-8")
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = []
    for index, (opponent_id, split) in enumerate(
        (("opp-train", "META_TRAIN"), ("opp-dev", "META_DEV"), ("opp-final", "META_FINAL"))
    ):
        rows.append(
            MetaDistributionRowV1(
                opponent_id=opponent_id,
                pair_id=f"pair-{index}",
                deck_sha256=(str(index + 1) * 64),
                policy_sha256=(str(index + 4) * 64),
                archetype="Archaludon",
                runtime_class="native_fast",
                source="fixture",
                source_sha256=(str(index + 7) * 64),
                usage_boundary="training_local",
                evaluation_allowed=True,
                training_allowed=True,
                behavior_allowed=True,
                submission_allowed=False,
                observed_strength=0.5,
                observed_games=96,
                observed_fault_rate=0.0,
                frequency_proxy=1 / 3,
                hard_negative_score=1 / 3,
                diversity_contribution=1.0,
                top_meta_component=1 / 3,
                hard_negative_component=1 / 3,
                diversity_component=1 / 3,
                weight=1 / 3,
                split=split,
                runtime_status="measured",
                evidence_status="observed",
            )
        )
    manifest = MetaDistributionManifestV1(
        schema_version="meta-specialist-meta-distribution-v1",
        candidate_id="candidate",
        sources=(MetaSourceArtifactV1(str(source), source_sha, "fixture"),),
        rows=tuple(rows),
        component_targets={"top_meta": 0.60, "hard_negative": 0.25, "diversity": 0.15},
        split_ids={
            "META_TRAIN": ("opp-train",),
            "META_DEV": ("opp-dev",),
            "META_FINAL": ("opp-final",),
        },
        training_authority=False,
        promotion_authority=False,
        submission_authority=False,
        research_only=True,
        notes=("fixture",),
    )
    path = tmp_path / "meta-manifest.json"
    save_meta_distribution_manifest_v1(manifest, path)
    return path


def _write_rows(tmp_path: Path, rows: list[dict[str, object]], *, raw: str | None = None) -> Path:
    path = tmp_path / "rows.jsonl"
    if raw is None:
        raw = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(raw, encoding="utf-8")
    return path


def _manifest_variant(
    tmp_path: Path,
    *,
    row_changes: dict[str, object] | None = None,
    manifest_changes: dict[str, object] | None = None,
) -> Path:
    path = _manifest(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if row_changes:
        payload["rows"][0].update(row_changes)
    if manifest_changes:
        payload.update(manifest_changes)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _row(action: str, outcome: str, *, state: str = STATE_A, weight: float = 1.0, seat: int = 0):
    return {
        "state_digest": state,
        "action_key": action,
        "opponent_id": "opp-train",
        "seat": seat,
        "split": "META_TRAIN",
        "outcome": outcome,
        "weight": weight,
    }


def _build(tmp_path: Path, rows: list[dict[str, object]], **kwargs):
    manifest_path = kwargs.pop("manifest_path", None) or _manifest(tmp_path)
    return build_public_advantage_table_v1(
        source_rows_path=_write_rows(tmp_path, rows),
        meta_manifest_path=manifest_path,
        baseline_policy_sha256=BASELINE_SHA,
        iteration=0,
        **kwargs,
    )


def test_deterministic_state_action_aggregation_and_canonical_sha(tmp_path):
    rows = [
        _row(ACTION_0, "win"),
        _row(ACTION_0, "loss", weight=2.0),
        _row(ACTION_1, "draw", weight=3.0),
        _row(ACTION_1, "draw", weight=4.0),
    ]
    table_a = _build(tmp_path, rows, min_support=2, delta_cap=1.0)
    table_b = _build(tmp_path, list(reversed(rows)), min_support=2, delta_cap=1.0)
    assert table_a.table_sha256 == table_b.table_sha256
    assert table_a.meta_manifest_sha256 == table_b.meta_manifest_sha256
    assert table_a.coverage_summary["supported_action_pairs"] == 2
    assert table_a.entry(STATE_A, ACTION_0).support == 2
    assert table_a.entry(STATE_A, ACTION_1).support == 2
    assert table_a.authority_false is True


def test_positive_and_negative_delta_are_capped(tmp_path):
    rows = [
        _row(ACTION_0, "win", weight=9.0),
        _row(ACTION_0, "win", weight=8.0),
        _row(ACTION_1, "loss", weight=1.0),
        _row(ACTION_1, "loss", weight=2.0),
    ]
    table = _build(tmp_path, rows, min_support=1, delta_cap=0.10)
    assert table.entry(STATE_A, ACTION_0).delta == pytest.approx(0.10)
    assert table.entry(STATE_A, ACTION_1).delta == pytest.approx(-0.10)


def test_insufficient_support_is_not_materialized(tmp_path):
    table = _build(
        tmp_path,
        [_row(ACTION_0, "win"), _row(ACTION_1, "loss"), _row(ACTION_1, "win", weight=2.0)],
        min_support=2,
        delta_cap=1.0,
    )
    assert table.entry(STATE_A, ACTION_0) is None
    assert table.entry(STATE_A, ACTION_1) is not None
    assert table.coverage_summary["insufficient_support"] == 1


@pytest.mark.parametrize("split", ["META_DEV", "META_FINAL"])
def test_heldout_split_is_rejected(tmp_path, split):
    row = _row(ACTION_0, "win")
    row["split"] = split
    with pytest.raises(NativePublicAdvantageError, match="META_TRAIN"):
        _build(tmp_path, [row])


@pytest.mark.parametrize(
    ("row_changes", "message"),
    [
        ({"usage_boundary": "local_eval_only"}, "usage boundary"),
        ({"training_allowed": False}, "training permission"),
        ({"behavior_allowed": False}, "behavior permission"),
        ({"submission_allowed": True}, "submission authority"),
    ],
)
def test_manifest_row_permission_boundary_is_fail_closed(tmp_path, row_changes, message):
    manifest_path = _manifest_variant(tmp_path, row_changes=row_changes)
    with pytest.raises(NativePublicAdvantageError, match=message):
        _build(tmp_path, [_row(ACTION_0, "win")], manifest_path=manifest_path)


@pytest.mark.parametrize("authority_field", ["training_authority", "promotion_authority", "submission_authority"])
def test_manifest_authority_true_is_rejected(tmp_path, authority_field):
    manifest_path = _manifest_variant(tmp_path, manifest_changes={authority_field: True})
    with pytest.raises(NativePublicAdvantageError, match="verified meta manifest rejected"):
        _build(tmp_path, [_row(ACTION_0, "win")], manifest_path=manifest_path)


def test_private_or_unknown_row_key_is_rejected(tmp_path):
    row = _row(ACTION_0, "win")
    row["private_hand"] = "hidden"
    with pytest.raises(NativePublicAdvantageError, match="private|unsupported"):
        _build(tmp_path, [row])


def test_duplicate_record_is_rejected(tmp_path):
    row = _row(ACTION_0, "win")
    with pytest.raises(NativePublicAdvantageError, match="duplicate"):
        _build(tmp_path, [row, dict(row)])


def test_duplicate_json_key_is_rejected(tmp_path):
    row = json.dumps(_row(ACTION_0, "win"), sort_keys=True, separators=(",", ":"))
    row = row[:-1] + ',"weight":2}'
    source_rows = tmp_path / "duplicate-keys.jsonl"
    source_rows.write_text(row + "\n", encoding="utf-8")
    with pytest.raises(NativePublicAdvantageError, match="duplicate"):
        build_public_advantage_table_v1(
            source_rows_path=source_rows,
            meta_manifest_path=_manifest(tmp_path),
            baseline_policy_sha256=BASELINE_SHA,
            iteration=0,
        )


def test_native_first_policy_overrides_only_supported_single_main_action(tmp_path):
    rows = [_row(ACTION_0, "loss"), _row(ACTION_0, "loss", weight=2.0), _row(ACTION_1, "win"), _row(ACTION_1, "win", weight=2.0)]
    table = _build(tmp_path, rows, min_support=2, delta_cap=1.0)
    calls = []

    def native(obs):
        calls.append(obs)
        return [0]

    policy = build_native_public_advantage_policy_v1(
        native_agent=native,
        table=table,
        baseline_policy_sha256=BASELINE_SHA,
        candidate_config_sha256=CONFIG_SHA,
    )
    obs = {
        "state_digest": STATE_A,
        "select": {
            "context": "MAIN",
            "option": [{"action_key": ACTION_0}, {"action_key": ACTION_1}],
            "minCount": 1,
            "maxCount": 1,
        },
    }
    assert policy(obs) == [1]
    assert calls == [obs]
    assert policy.snapshot().override_applied == 1


@pytest.mark.parametrize(
    "obs",
    [
        {"state_digest": "9" * 64, "select": {"context": "MAIN", "option": [{"action_key": ACTION_1}], "minCount": 1, "maxCount": 1}},
        {"state_digest": STATE_A, "select": "malformed"},
        {"state_digest": STATE_A, "select": {"context": "MAIN", "option": [{"action_key": ACTION_0}, {"action_key": ACTION_1}], "minCount": 1, "maxCount": 2}},
        {"state_digest": STATE_A, "select": {"context": "ORDERED", "option": [{"action_key": ACTION_0}, {"action_key": ACTION_1}], "minCount": 1, "maxCount": 1}},
        {"state_digest": STATE_A, "select": {"context": "MAIN", "option": [{"bad": ACTION_0}, {"action_key": ACTION_1}], "minCount": 1, "maxCount": 1}},
    ],
)
def test_unknown_malformed_multiselect_ordered_inputs_return_exact_native(tmp_path, obs):
    table = _build(tmp_path, [_row(ACTION_0, "loss"), _row(ACTION_0, "loss", weight=2.0)], min_support=2)
    native_result = [0]
    policy = build_native_public_advantage_policy_v1(
        native_agent=lambda _obs: native_result,
        table=table,
        baseline_policy_sha256=BASELINE_SHA,
        candidate_config_sha256=CONFIG_SHA,
    )
    assert policy(obs) == native_result
    assert policy.snapshot().override_applied == 0


def test_nonfinite_and_bad_digest_rows_are_rejected(tmp_path):
    row = _row(ACTION_0, "win", weight=float("nan"))
    with pytest.raises(NativePublicAdvantageError, match="finite"):
        _build(tmp_path, [row])
    row = _row("not-an-action", "win")
    with pytest.raises(NativePublicAdvantageError, match="SHA|digest"):
        _build(tmp_path, [row])


def test_table_sha_is_self_verified_and_roundtrips(tmp_path):
    table = _build(
        tmp_path,
        [_row(ACTION_0, "loss"), _row(ACTION_0, "loss", weight=2.0)],
        min_support=2,
    )
    restored = PublicAdvantageTableV1.from_dict(table.to_dict())
    assert restored.to_dict() == table.to_dict()
    with pytest.raises(NativePublicAdvantageError, match="table_sha256|canonical"):
        replace(table, table_sha256="0" * 64)
    forged_payload = table.to_dict()
    forged_payload["table_sha256"] = "0" * 64
    with pytest.raises(NativePublicAdvantageError, match="table_sha256|canonical"):
        PublicAdvantageTableV1.from_dict(forged_payload)


def test_table_entries_and_coverage_are_deep_immutable(tmp_path):
    table = _build(
        tmp_path,
        [_row(ACTION_0, "loss"), _row(ACTION_0, "loss", weight=2.0)],
        min_support=2,
    )
    with pytest.raises(TypeError):
        table.coverage_summary["new_field"] = 1
    with pytest.raises(TypeError):
        table.coverage_summary["seat_counts"]["0"] = 99
    with pytest.raises(TypeError):
        table.coverage_summary["outcome_counts"]["loss"] = 99
    with pytest.raises(TypeError):
        table.entries[0] = table.entries[0]
    assert isinstance(table.entries, tuple)
