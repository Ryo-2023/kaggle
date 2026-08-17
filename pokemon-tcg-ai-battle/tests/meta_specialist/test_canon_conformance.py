"""正典 §22 受入条件への 1条項1テスト の conformance suite。

正典: ``docs/plan/メタ駆動デッキ専門方策_提出仕様反映レビュー版_2026-08-01.md`` の
「## 22. 受入条件」節（20 箇条書き）。

このファイルの目的は「未実装を緑にしないこと」である。各テスト関数の docstring
冒頭に対応条項の原文を引用し、その条項の内容が実際に成立するかを検査する。
モジュールが存在するだけ・単体で動くだけでは MET と判定しない。実運用パイプライン
（``src/mage_ptcg/meta_specialist/cli.py`` のサブコマンド、または他の本番コードからの
import）から一度も参照されない実装は、正典が要求する「できる」を満たしていないもの
として FAIL させる。

未実装・未配線の条項は ``pytest.skip``/``xfail`` にせず、``pytest.fail("UNMET: ...")``
で明示的に落とす。落ちるテストが存在することは、このファイルの目的からして正しい
挙動である。判定結果の一覧は ``docs/canon-conformance-status.md`` を参照。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest


pytestmark = pytest.mark.canon_conformance

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src"
_SRC_META_SPECIALIST = _SRC_ROOT / "mage_ptcg" / "meta_specialist"
_SCRIPTS_ROOT = _REPO_ROOT / "scripts"


# ---------------------------------------------------------------------------
# Shared verification helpers (deliberately import-only; no production logic
# is reimplemented here, only inspected).
# ---------------------------------------------------------------------------


def _all_src_python_files() -> list[Path]:
    return sorted(_SRC_ROOT.rglob("*.py"))


def _importers_of(module_basename: str) -> list[str]:
    """Every file under ``src/`` (excluding the module's own file) that imports
    ``mage_ptcg.meta_specialist.<module_basename>``.

    Uses ``ast`` rather than a text grep so a comment or string that merely
    mentions the module name is never mistaken for a real import edge.
    """
    target_file = _SRC_META_SPECIALIST / f"{module_basename}.py"
    importers: list[str] = []
    for path in _all_src_python_files():
        if path == target_file:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith(f"meta_specialist.{module_basename}"):
                        importers.append(str(path.relative_to(_REPO_ROOT)))
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.endswith(f"meta_specialist.{module_basename}"):
                    importers.append(str(path.relative_to(_REPO_ROOT)))
    return sorted(set(importers))


def _grep_existence(*needles: str, roots: tuple[Path, ...] | None = None) -> list[Path]:
    """Existence-only substring search used to prove a concept is entirely absent.

    This is a plain substring search, not a semantic check, so it is only used
    to demonstrate the *absence* of a feature (a concept that appears nowhere,
    even in a comment or docstring, cannot possibly be implemented). It is
    never used to certify that a match constitutes a correct implementation.
    """
    search_roots = roots if roots is not None else (_SRC_META_SPECIALIST,)
    hits: list[Path] = []
    for root in search_roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            if any(needle.lower() in text for needle in needles):
                hits.append(path)
    return hits


def _cli_subcommands() -> set[str]:
    from mage_ptcg.meta_specialist.cli import _build_parser

    parser = _build_parser()
    for action in parser._subparsers._group_actions:  # type: ignore[attr-defined]
        return set(action.choices)
    return set()


# -- fixtures shared by several clause tests --------------------------------


def _write_deck_csv(path: Path, cards: tuple[int, ...]) -> None:
    path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")


def _build_qualified_asset(tmp_path: Path, *, name: str, cards: tuple[int, ...] | None = None):
    from mage_ptcg.meta_specialist.decks import ArchetypeSpec, DeckAssetInput, qualify_deck_asset

    resolved_cards = tuple(range(1, 61)) if cards is None else cards
    deck_path = tmp_path / f"{name}.csv"
    _write_deck_csv(deck_path, resolved_cards)
    asset = DeckAssetInput.from_path(
        asset_id=f"canon-conformance-{name}",
        archetype_id="canon-conformance",
        path=deck_path,
        source_ref="https://example.invalid/decks/canon-conformance.csv",
        source_commit="a" * 40,
        asset_class="deck_only",
        usage_boundary="bundle_allowed",
        policy_compatibility="specialist-v2",
        card_database_version="fixture-v1",
    )
    return qualify_deck_asset(
        asset,
        ArchetypeSpec("canon-conformance", (), (resolved_cards[0],), "qualified_not_trained"),
        known_card_ids=set(resolved_cards),
        cabt_legality=lambda _: (True, "fixture-cabt-pass"),
    )


def _build_runtime(tmp_path: Path):
    """Build a real ``MetaSpecialistRuntime`` against a fixture policy/deck."""
    import hashlib

    from mage_ptcg.knowledge.model import deck_identity_from_card_ids
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
        SpecialistStepLogitsV1,
        make_test_card_vocabulary_v1,
    )
    from mage_ptcg.meta_specialist.decks import create_deck_lock
    from mage_ptcg.meta_specialist.runtime import (
        MetaSpecialistRuntime,
        PolicyTelemetrySnapshot,
        RuntimeConstraintManifest,
    )

    class _Session:
        def __init__(self) -> None:
            self.commits = 0

        def logits(self, _model, step):
            return SpecialistStepLogitsV1(
                (0.0,) * len(step.allowed_semantic_classes),
                1.0 if step.stop_available else None,
            )

        def commit(self, _outcome) -> None:
            self.commits += 1

        def abort(self) -> None:
            pass

    class _Policy:
        def __init__(self, identity: str, lineage: str) -> None:
            self.identity, self.lineage = identity, lineage

        def reset(self) -> None:
            pass

        def begin_decision(self):
            return _Session()

        def policy_telemetry(self):
            return PolicyTelemetrySnapshot(self.identity, "checkpointed_specialist", True, self.lineage, None, 0)

    deck = _build_qualified_asset(tmp_path, name="runtime-fixture")
    identity = deck_identity_from_card_ids(deck.card_ids)
    lock = create_deck_lock(
        archetype_id="canon-conformance",
        selected_deck_identity=identity,
        compared_deck_identities=(identity,),
        foundation_init_id="a" * 64,
        joint_race_schedule_id="b" * 64,
        equal_transition_budget=1,
    )
    policy_identity = hashlib.sha256(b"canon-conformance-policy").hexdigest()
    policy = _Policy(policy_identity, lock.policy_lineage_id)
    runtime = MetaSpecialistRuntime(
        deck_asset=deck,
        deck_lock=lock,
        vocabulary=make_test_card_vocabulary_v1(range(1, 2000)),
        policy=policy,
        expected_policy_identity=policy_identity,
        constraints=RuntimeConstraintManifest.frozen_v1(),
    )
    return runtime, policy, deck.card_ids


def _single_select_observation() -> dict[str, object]:
    return {
        "current": {
            "energyAttached": False, "firstPlayer": 0, "looking": None,
            "players": [
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
                 "confused": False, "deckCount": 60, "discard": [], "hand": [{"id": 101, "serial": 10, "playerIndex": 0}, {"id": 102, "serial": 11, "playerIndex": 0}], "handCount": 2,
                 "paralyzed": False, "poisoned": False, "prize": [None] * 6},
                {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False,
                 "confused": False, "deckCount": 60, "discard": [], "hand": None, "handCount": 0,
                 "paralyzed": False, "poisoned": False, "prize": [None] * 6},
            ], "result": -1, "retreated": False, "stadium": [], "stadiumPlayed": False,
            "supporterPlayed": False, "turn": 2, "turnActionCount": 0, "yourIndex": 0,
        },
        "select": {
            "context": 1, "contextCard": None, "deck": None, "effect": None,
            "maxCount": 1, "minCount": 0,
            "option": [
                {"type": 3, "area": 2, "index": 0, "playerIndex": 0},
            ],
            "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 1,
        },
        "step": 1,
    }


def _multi_select_observation() -> dict[str, object]:
    observation = _single_select_observation()
    observation["select"] = {  # type: ignore[assignment]
        "context": 39, "contextCard": None, "deck": None, "effect": None,
        "maxCount": 2, "minCount": 2,
        "option": [{"type": 0, "number": 1}, {"type": 0, "number": 2}],
        "remainDamageCounter": 0, "remainEnergyCost": 0, "type": 8,
    }
    return observation


# ---------------------------------------------------------------------------
# 条項 1
# ---------------------------------------------------------------------------


def test_clause_01_bundle_has_one_deck_one_lineage_one_entrypoint_under_size_limit(
    tmp_path: Path,
) -> None:
    """正典 §22 条項1:

    「1 submission が exactly one `deck.csv`、one policy/checkpoint lineage、
    top-level `main.py` を持ち、197.7 MiB 以下で package smoke を通る。」
    """
    from hashlib import sha256

    from mage_ptcg.continuous_league.contracts import content_id
    from mage_ptcg.meta_specialist.cabt_json_contract_v1 import CABT_AGENT_JSON_CONTRACT_SHA256_V1
    from mage_ptcg.meta_specialist.contracts import (
        BUNDLE_SIZE_LIMIT_BYTES,
        BUNDLE_SIZE_LIMIT_KIB,
        ladder_mechanics_payload,
    )
    from mage_ptcg.meta_specialist.decks import create_deck_lock
    from mage_ptcg.meta_specialist.package import (
        BundleSpec,
        DependencyContractIds,
        build_specialist_archive,
        derive_entrypoint_contract_id,
        verify_specialist_archive,
    )
    from mage_ptcg.meta_specialist.runtime import RuntimeConstraintManifest

    # 197.7 MiB は正典の丸めた表現であり、実際の宣言値と一致するかを検証する。
    limit_mib = BUNDLE_SIZE_LIMIT_BYTES / (1024 * 1024)
    assert BUNDLE_SIZE_LIMIT_KIB == 202_400
    assert abs(limit_mib - 197.7) < 0.05, f"bundle size limit is {limit_mib} MiB, not ~197.7 MiB"

    source = tmp_path / "source"
    source.mkdir()
    qualified = _build_qualified_asset(source, name="deck")
    (source / "main.py").write_text("agent = lambda observation, configuration: []\n", encoding="utf-8")
    (source / "policy_loader.py").write_text("# fixture\n", encoding="utf-8")
    (source / "weights.bin").write_bytes(b"fixture checkpoint bytes")
    constraints = RuntimeConstraintManifest.frozen_v1()
    ladder = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    ladder["ladder_mechanics_id"] = content_id("meta-specialist-ladder-mechanics-v1", ladder)
    lock = create_deck_lock(
        archetype_id=qualified.archetype_id,
        selected_deck_identity=qualified.deck_identity,
        compared_deck_identities=(qualified.deck_identity,),
        foundation_init_id="b" * 64,
        joint_race_schedule_id="c" * 64,
        equal_transition_budget=1,
    )
    members = ("deck.csv", "main.py", "policy_loader.py", "weights.bin")
    dependency_ids = DependencyContractIds(
        cabt_agent_json_contract_id=CABT_AGENT_JSON_CONTRACT_SHA256_V1,
        runtime_constraints_id=constraints.runtime_constraints_id,
        ladder_mechanics_id=ladder["ladder_mechanics_id"],
        entrypoint_contract_id=derive_entrypoint_contract_id(source, members),
    )
    spec = BundleSpec(
        source_root=source, members=members, deck_member="deck.csv",
        policy_entrypoint_member="policy_loader.py", qualified_deck_asset=qualified,
        deck_lock=lock, runtime_constraints=constraints, ladder_mechanics=ladder,
        dependency_contract_ids=dependency_ids, candidate_class="checkpointed_specialist",
        policy_members=("weights.bin",), model_member="weights.bin",
        policy_identity=sha256(b"fixture checkpoint bytes").hexdigest(),
        checkpoint_lineage_id=lock.policy_lineage_id, checkpoint_lineage_reason=None,
    )

    report = build_specialist_archive(spec, tmp_path / "bundle.tar.gz")
    reverified = verify_specialist_archive(tmp_path / "bundle.tar.gz")

    assert report.status == "structurally_verified"
    assert reverified.compressed_size_bytes == report.compressed_size_bytes
    assert report.required_top_level_files == ("main.py", "deck.csv")
    assert report.compressed_size_bytes <= BUNDLE_SIZE_LIMIT_BYTES
    # exactly one policy/checkpoint lineage: a single checkpoint_lineage_id bound
    # to the single deck lock's policy_lineage_id, and a single model member.
    assert report.checkpoint_lineage_id == lock.policy_lineage_id
    assert spec.policy_members == ("weights.bin",)
    assert spec.deck_member == "deck.csv"
    assert "main.py" in spec.members


# ---------------------------------------------------------------------------
# 条項 2
# ---------------------------------------------------------------------------


def test_clause_02_medal_is_not_used_as_a_runtime_model_selector() -> None:
    """正典 §22 条項2:

    「Gold / Silver / Bronze を runtime model selector として使わない。」
    """
    import dataclasses

    from mage_ptcg.meta_specialist import entrypoint, package, runtime

    forbidden_terms = ("gold", "silver", "bronze", "medal", "tier", "rank_band")

    callables_to_check = (
        runtime.MetaSpecialistRuntime.__init__,
        runtime.MetaSpecialistRuntime.__call__,
        runtime.make_agent,
        entrypoint.build_packaged_agent,
        entrypoint.load_specialist_bundle,
    )
    for target in callables_to_check:
        parameters = set(inspect.signature(target).parameters)
        offending = {
            name for name in parameters
            if any(term in name.lower() for term in forbidden_terms)
        }
        assert not offending, (
            f"{target.__qualname__} accepts a medal-shaped selector parameter: {offending}"
        )

    dataclasses_to_check = (
        runtime.PolicyTelemetrySnapshot,
        runtime.RuntimeConstraintManifest,
        package.BundleSpec,
    )
    for dc in dataclasses_to_check:
        field_names = {field.name for field in dataclasses.fields(dc)}
        offending_fields = {
            name for name in field_names if any(term in name.lower() for term in forbidden_terms)
        }
        assert not offending_fields, f"{dc.__qualname__} has a medal-shaped field: {offending_fields}"


# ---------------------------------------------------------------------------
# 条項 3
# ---------------------------------------------------------------------------


def test_clause_03_curriculum_phases_share_one_checkpoint_lineage_in_production() -> None:
    """正典 §22 条項3:

    「broad / middle / high / consolidation が同じ親子 checkpoint chain で継続され、
    phase ごとの別 model 再学習と区別できる。」
    """
    importers = _importers_of("curriculum_v1")
    if not importers:
        pytest.fail(
            "UNMET: curriculum_v1.py (foundation/ascent/top_focus/consolidation の"
            "同一 checkpoint lineage 継続カリキュラム) がどの本番コード (src/ 配下) からも "
            "import されていない。CLI サブコマンドにも学習カリキュラムを起動する経路が "
            f"存在しない (現在の cli.py サブコマンド: {sorted(_cli_subcommands())})。"
            "モジュール単体のテストが緑でも、実運用パイプラインで phase 遷移が checkpoint "
            "lineage を継続する事実は一度も成立していない。"
        )


# ---------------------------------------------------------------------------
# 条項 4
# ---------------------------------------------------------------------------


def test_clause_04_seed_pools_are_qualified_or_explicitly_qualified_not_trained() -> None:
    """正典 §22 条項4:

    「5 runtime ID の各 seed poolを immutable provenance、asset class、利用境界付きで
    qualification できる。期限上 primary 2〜3 系統だけを実学習した場合、残りは
    `qualified_not_trained` と明示する。」
    """
    from mage_ptcg.meta_specialist.decks import load_archetype_registry

    registry_path = _REPO_ROOT / "configs" / "meta_specialist" / "archetypes_v1.json"
    registry = load_archetype_registry(registry_path)
    assert len(registry.archetypes) == 5, (
        f"registry has {len(registry.archetypes)} runtime IDs, expected 5"
    )

    statuses = {spec.runtime_id: spec.candidate_status for spec in registry.archetypes.values()}
    trained_or_explicitly_untrained = {
        runtime_id for runtime_id, status in statuses.items()
        if status in {"trained_champion", "qualified_not_trained"}
    }
    report_path = (
        _REPO_ROOT / "runs" / "meta-specialist-seed-qualification"
        / "seed_qualification_report_v1.json"
    )
    if not trained_or_explicitly_untrained and not report_path.exists():
        pytest.fail(
            "UNMET: 5 runtime ID すべてが registry 上 candidate_status="
            "'registered_unqualified' のままであり、primary 2〜3 系統の実学習/qualified化も、"
            "残りへの 'qualified_not_trained' 明示付与も一度も実行されていない。"
            f"期待される実行結果 {report_path} も存在しない。"
            "seed_qualification_report_v1.py / seed_registry.py 自体は実装されているが、"
            "実際に qualification を1回でも走らせた成果物が生成されていないため、"
            "条項が要求する『できる』が実運用として示されていない。"
        )


# ---------------------------------------------------------------------------
# 条項 5
# ---------------------------------------------------------------------------


def test_clause_05_census_can_be_sealed_with_threshold_and_missingness_report() -> None:
    """正典 §22 条項5:

    「Gold 100%、全体 98% 以上を既定 threshold として census を seal でき、
    欠損感度を報告できる。」

    seal 側の閾値判定は**実際に呼んで**確かめる。取得側 (正典 §16 の状態機械・
    pacing・resume) は seal の前提なので、本番コードから使われているかも見る。
    """
    from mage_ptcg.meta_specialist.census_v1 import (
        CensusRecordV1, calculate_missing_sensitivity_v1, verify_census_seal_v1,
    )

    # Gold が 1 件でも欠けたら seal できないこと。
    gold_gap = [
        CensusRecordV1("g1", "Gold", True),
        CensusRecordV1("g2", "Gold", False, ("deck",)),
    ] + [CensusRecordV1(f"s{i}", "Silver", True) for i in range(98)]
    assert verify_census_seal_v1(gold_gap).is_sealed is False, (
        "Gold の欠損があるのに seal された"
    )

    # 全体 98% を下回ったら seal できないこと。
    total_gap = [CensusRecordV1(f"g{i}", "Gold", True) for i in range(10)] + [
        CensusRecordV1(f"s{i}", "Silver", i >= 5, () if i >= 5 else ("deck",))
        for i in range(90)
    ]
    assert verify_census_seal_v1(total_gap).is_sealed is False, (
        "全体 coverage が閾値未満なのに seal された"
    )

    # 欠損感度が field 単位で出ること。
    sensitivity = calculate_missing_sensitivity_v1([
        CensusRecordV1("a", "Gold", True),
        CensusRecordV1("b", "Silver", False, ("deck",)),
    ])
    assert sensitivity.get("deck", 0) > 0, "欠損 field の感度が報告されない"

    # 取得側が本番コードから使われていること。
    if not _importers_of("census_fetch_v1"):
        pytest.fail(
            "UNMET: seal の閾値判定は動くが、正典 §16 の census 取得器 "
            "(census_fetch_v1: 8 状態の SQLite 状態機械、pacing、429 circuit breaker、"
            "resume) が src/ のどの本番コードからも import されていない。seal できる "
            "census を実際に作る経路が無い。"
        )


# ---------------------------------------------------------------------------
# 条項 6
# ---------------------------------------------------------------------------


def test_clause_06_meta_analysis_manifest_can_be_produced_per_rank_band() -> None:
    """正典 §22 条項6:

    「3 source rank band の archetype / variant / exact deck、core / flex、
    順位感度、過去差分を `MetaAnalysisManifest` 付きで出力できる。」

    型が存在するかではなく、**band 別の集計を実際に返すか**を見る。
    """
    from mage_ptcg.meta_specialist.meta_analysis_v1 import (
        DeckObservationV1,
        build_meta_analysis_manifest_v1,
    )

    def observation(index: int, band: str, archetype: str) -> DeckObservationV1:
        return DeckObservationV1(
            submission_id=f"{band}-{index}", source_rank_band=band,
            rank_within_band=index + 1, archetype_id=archetype,
            support_package_id=f"{archetype}_core", exact_deck_hash=f"{band}-{index}-hash",
            # card 100 は全構築、card 300 は先頭だけ -> core / flex が分かれる。
            card_counts={100: 4, 300: 1} if index == 0 else {100: 4},
        )

    observations = [
        observation(index, band, "archaludon" if index < 2 else "lucario")
        for band in ("Gold", "Silver", "Bronze")
        for index in range(4)
    ]

    manifest = build_meta_analysis_manifest_v1(
        manifest_id="conformance", census_id="census-1",
        census_content_hash="a" * 64, classifier_version="classifier-v1",
        observations=observations,
    )

    # 1. 3 つの source rank band が別々に出ること。
    assert [report.source_rank_band for report in manifest.bands] == [
        "Gold", "Silver", "Bronze"
    ]

    for report in manifest.bands:
        # 2. archetype / support package / exact deck の三段階集計。
        assert {row.key for row in report.archetype_shares} == {"archaludon", "lucario"}
        assert {row.key for row in report.support_package_shares} == {
            "archaludon_core", "lucario_core"
        }
        assert len(report.exact_deck_shares) == 4

        # 3. 観測比率が bootstrap CI を伴うこと。
        for row in report.archetype_shares:
            assert row.ci_low <= row.share <= row.ci_high
            assert row.ci_low < row.ci_high

        # 4. core / flex の採用率が実際に分かれること。
        composition = next(
            item for item in report.compositions if item.archetype_id == "archaludon"
        )
        assert 100 in composition.core_card_ids
        assert 300 in composition.flex_card_ids

        # 5. band 内順位の感度分析。
        assert report.rank_sensitivity["upper"] == {"archaludon": 1.0}
        assert report.rank_sensitivity["lower"] == {"lucario": 1.0}

    # 6. 過去 snapshot 差分が、classifier version が揃う場合だけ出ること。
    later = build_meta_analysis_manifest_v1(
        manifest_id="conformance-2", census_id="census-2",
        census_content_hash="b" * 64, classifier_version="classifier-v1",
        observations=observations, previous=manifest,
    )
    assert later.historical_diff is not None
    assert later.historical_diff.previous_manifest_id == "conformance"

    from mage_ptcg.meta_specialist.meta_analysis_v1 import MetaAnalysisV1Error

    with pytest.raises(MetaAnalysisV1Error, match="classifier version"):
        build_meta_analysis_manifest_v1(
            manifest_id="conformance-3", census_id="census-3",
            census_content_hash="c" * 64, classifier_version="classifier-v2",
            observations=observations, previous=manifest,
        )


# ---------------------------------------------------------------------------
# 条項 7
# ---------------------------------------------------------------------------


def test_clause_07_opponent_strength_is_calibrated_locally_in_production() -> None:
    """正典 §22 条項7:

    「proxy opponent を source medal ではなく local strength と CI で
    calibration できる。」
    """
    importers = _importers_of("calibration_v1")
    if not importers:
        pytest.fail(
            "UNMET: calibration_v1.py (seat-balanced cross-play による local strength "
            "banding) がどの本番コード (src/ 配下) からも import されていない。CLI にも "
            "calibration を起動する経路がない "
            f"(現在の cli.py サブコマンド: {sorted(_cli_subcommands())})。"
            "実際の opponent banding は今も medal 由来の分類のまま、または未実施である。"
        )


# ---------------------------------------------------------------------------
# 条項 8
# ---------------------------------------------------------------------------


def test_clause_08_single_and_multi_select_share_one_complete_action_contract(
    tmp_path: Path,
) -> None:
    """正典 §22 条項8:

    「単一・複数選択を捨てず、全 learner / teacher が同じ complete-action contract を
    使い、1 complete action が 1 environment transition に対応する。」
    """
    from mage_ptcg.meta_specialist.actor_visible_v2 import build_actor_visible_decision_state_v2
    from mage_ptcg.meta_specialist.actor_visible_features_v1 import make_test_card_vocabulary_v1
    from mage_ptcg.meta_specialist.runtime_actions_v2 import (
        RuntimeDecisionEnvelope,
        SemanticRuntimeCompleteActionV2,
        greedy_decode_runtime_action_v2,
    )

    vocabulary = make_test_card_vocabulary_v1(range(1, 2000))

    class _AlwaysZeroPolicy:
        def logits(self, _model_input, step_input):
            from mage_ptcg.meta_specialist.actor_visible_features_v1 import SpecialistStepLogitsV1

            return SpecialistStepLogitsV1(
                (1.0,) * len(step_input.allowed_semantic_classes),
                0.0 if step_input.stop_available else None,
            )

    for observation in (_single_select_observation(), _multi_select_observation()):
        state = build_actor_visible_decision_state_v2(observation)
        envelope = RuntimeDecisionEnvelope.from_actor_visible_state(state, vocabulary=vocabulary)
        action = greedy_decode_runtime_action_v2(envelope, policy=_AlwaysZeroPolicy())
        # Both single-select (maxCount=1) and multi-select (maxCount=2) go
        # through the identical envelope/decode/semantic-conversion contract
        # and each produces exactly one semantic complete action.
        semantic = envelope.build_step_input(())  # sanity: contract is usable
        assert semantic is not None
        indices = envelope.decode_option_indices(action)
        assert 1 <= len(indices) <= max(1, envelope.candidate_count)
        assert type(action).__module__.endswith("runtime_actions_v2")

    # One MetaSpecialistRuntime.__call__ commits exactly one complete action per
    # legal decision, i.e. one environment transition, regardless of selection
    # cardinality.
    runtime, _policy, cards = _build_runtime(tmp_path)
    runtime({"select": None})
    runtime(_single_select_observation())
    assert runtime.environment_action_count == 1


# ---------------------------------------------------------------------------
# 条項 9
# ---------------------------------------------------------------------------


def test_clause_09_hidden_information_and_illegal_action_are_auto_detected(
    tmp_path: Path,
) -> None:
    """正典 §22 条項9:

    「hidden-information leak と illegal action を自動 test で検出できる。
    真の deck / policy identity も leak test に含める。」
    """
    from mage_ptcg.meta_specialist.runtime import RuntimeContractError, RuntimeDecisionTraceV2

    # -- (a) hidden-information leak, including a smuggled true deck/policy
    # identity, is rejected automatically by the production public-trace seal.
    runtime, _policy, _cards = _build_runtime(tmp_path)
    runtime({"select": None})
    runtime(_single_select_observation())
    trace = runtime.traces[0]
    assert trace.trace_variant == "public-v1-representable"
    real_public_trace = trace.public_trace
    assert real_public_trace is not None

    tampered = dict(real_public_trace)
    tampered["true_opponent_deck_identity"] = "f" * 64
    with pytest.raises(RuntimeContractError):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=tampered,
            trace_variant=trace.trace_variant,
            policy_identity=trace.policy_identity,
            candidate_class=trace.candidate_class,
            selection_type=trace.selection_type,
            selection_context=trace.selection_context,
            min_count=trace.min_count,
            max_count=trace.max_count,
            order_semantics=trace.order_semantics,
            selected_count=trace.selected_count,
            complete_action_log_probability=trace.complete_action_log_probability,
        )

    tampered_with_policy_identity_field = dict(real_public_trace)
    tampered_with_policy_identity_field["true_policy_identity"] = "e" * 64
    with pytest.raises(RuntimeContractError):
        RuntimeDecisionTraceV2.from_public_projection(
            public_trace=tampered_with_policy_identity_field,
            trace_variant=trace.trace_variant,
            policy_identity=trace.policy_identity,
            candidate_class=trace.candidate_class,
            selection_type=trace.selection_type,
            selection_context=trace.selection_context,
            min_count=trace.min_count,
            max_count=trace.max_count,
            order_semantics=trace.order_semantics,
            selected_count=trace.selected_count,
            complete_action_log_probability=trace.complete_action_log_probability,
        )

    # -- (b) illegal action: the runtime can only ever return option indices
    # that were part of the legal option list CABT itself provided, and never
    # more/fewer than the decision's own min/maxCount.
    runtime2, _policy2, _cards2 = _build_runtime(tmp_path)
    runtime2({"select": None})
    observation = _multi_select_observation()
    legal_option_count = len(observation["select"]["option"])  # type: ignore[index]
    indices = runtime2(observation)
    assert all(0 <= index < legal_option_count for index in indices)
    assert len(set(indices)) == len(indices)
    assert observation["select"]["minCount"] <= len(indices) <= observation["select"]["maxCount"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# 条項 10
# ---------------------------------------------------------------------------


def test_clause_10_baseline_and_primary_learner_are_compared_under_equal_budget() -> None:
    """正典 §22 条項10:

    「少なくとも既存 baseline と主 learner を同一 budget で比較できる。
    3 algorithm 完全比較は P1 / deferred としてよい。」
    """
    subcommands = _cli_subcommands()
    comparison_like = {
        name for name in subcommands
        if any(term in name for term in ("compare", "evaluate", "race", "eval"))
    }
    if not comparison_like:
        pytest.fail(
            "UNMET: meta-specialist CLI に rule-agent baseline と主 learner を同一 "
            f"transition budget で比較するサブコマンドが存在しない (現在のサブコマンド: "
            f"{sorted(subcommands)})。collect-trajectories/train-from-trajectories は "
            "収集と学習のみを行い、学習後の baseline 比較評価を実行する経路がない。"
        )


# ---------------------------------------------------------------------------
# 条項 11
# ---------------------------------------------------------------------------


def test_clause_11_pimc_targets_require_reproduction_gate_and_distillation_probe() -> None:
    """正典 §22 条項11:

    「PIMC を使う場合、公開情報だけから再構築され、再現 gate と distillation probe を
    通らない target が production に入らない。」

    正典 §9.4 が定める再現 gate は具体的である: 最初の 1,024 局を **paired block** で
    行い、score difference の片側 97.5% **cluster-bootstrap** 下限が 0 より大きいこと
    (primary gate) と、point estimate が **+3 percentage points** 以上であること
    (実用性 gate) の**両方**を要求し、不確定なら事前登録した **alpha-spending** で
    最大 4,096 局まで拡張する。

    ここでは source の文字列ではなく、gate を実際に呼んだ判定で確かめる。
    """
    from mage_ptcg.meta_specialist.pimc_gate_v1 import (
        ActionLogitPairV1,
        evaluate_pimc_reproducibility_v1,
    )
    from mage_ptcg.meta_specialist.pimc_reproduction_gate_v1 import (
        INTERIM_GAMES_V1,
        MAX_GAMES_V1,
        PRACTICAL_MARGIN_V1,
        PairedGameV1,
        PimcReproductionGateV1Error,
        assert_pimc_target_usable_v1,
        evaluate_pimc_reproduction_gate_v1,
        fallback_algorithm_id_v1,
        obrien_fleming_alpha_v1,
    )

    def block(games: int, *, edge: float, jitter: float) -> list[PairedGameV1]:
        rows = []
        for index in range(games):
            cluster = index % 32
            offset = jitter * ((cluster - 15.5) / 15.5)
            rows.append(PairedGameV1(
                pair_key=f"p{index}", cluster_id=f"c{cluster}",
                pimc_score=min(1.0, max(0.0, 0.5 + edge + offset)), baseline_score=0.5,
            ))
        return rows

    # 1. 最初の look が 1,024 局であり、それ未満では判定しないこと。
    with pytest.raises(PimcReproductionGateV1Error):
        evaluate_pimc_reproduction_gate_v1(block(512, edge=0.1, jitter=0.0), schedule_id="s")

    # 2. 明確な再現は両 gate を通ること。
    passed = evaluate_pimc_reproduction_gate_v1(
        block(INTERIM_GAMES_V1, edge=0.05, jitter=0.02), schedule_id="sealed-schedule"
    )
    assert passed.passed is True
    assert passed.primary_gate_passed and passed.practical_gate_passed
    assert passed.lower_bound > 0.0
    assert_pimc_target_usable_v1(passed)

    # 3. 実用性 gate: 統計的に明確でも +3pp 未満なら通さない。
    tiny = evaluate_pimc_reproduction_gate_v1(
        block(MAX_GAMES_V1, edge=0.005, jitter=0.001), schedule_id="s"
    )
    assert tiny.lower_bound > 0.0
    assert tiny.point_estimate < PRACTICAL_MARGIN_V1
    assert tiny.passed is False

    # 4. primary gate: cluster 間分散が大きければ point estimate が大きくても通さない。
    volatile = [
        PairedGameV1(
            pair_key=f"v{index}", cluster_id=f"c{index % 16}",
            pimc_score=1.0 if index % 16 < 8 else 0.0,
            baseline_score=0.0 if index % 16 < 8 else 0.92,
        )
        for index in range(INTERIM_GAMES_V1)
    ]
    unstable = evaluate_pimc_reproduction_gate_v1(volatile, schedule_id="s")
    assert unstable.point_estimate > PRACTICAL_MARGIN_V1
    assert unstable.primary_gate_passed is False
    assert unstable.passed is False

    # 5. alpha-spending: 中間 look の方が厳しく、最終で総 alpha を使い切ること。
    assert obrien_fleming_alpha_v1(INTERIM_GAMES_V1) < obrien_fleming_alpha_v1(MAX_GAMES_V1)

    # 6. 不確定は「合格」ではなく、同じ family での拡張要求であること。
    undecided = evaluate_pimc_reproduction_gate_v1(
        block(INTERIM_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="s"
    )
    assert undecided.status == "inconclusive_extend"
    with pytest.raises(PimcReproductionGateV1Error, match="not usable for training"):
        assert_pimc_target_usable_v1(undecided)

    # 7. 不採用時の fallback を `exit_vtrace` と偽らないこと。
    rejected = evaluate_pimc_reproduction_gate_v1(
        block(MAX_GAMES_V1, edge=0.0, jitter=0.02), schedule_id="s"
    )
    assert rejected.status == "rejected"
    assert fallback_algorithm_id_v1(rejected) == "rule_bc_vtrace"

    # 8. distillation probe も併存すること (条項は両方を要求する)。
    probe = evaluate_pimc_reproducibility_v1([
        ActionLogitPairV1("a", 0.6, 0.6), ActionLogitPairV1("b", 0.4, 0.4),
    ])
    assert probe.passed is True
    diverged = evaluate_pimc_reproducibility_v1([
        ActionLogitPairV1("a", 0.99, 0.01), ActionLogitPairV1("b", 0.01, 0.99),
    ])
    assert diverged.passed is False


# ---------------------------------------------------------------------------
# 条項 12
# ---------------------------------------------------------------------------


def test_clause_12_each_archetype_specialist_can_be_trained_stopped_resumed_evaluated() -> None:
    """正典 §22 条項12:

    「アーキタイプ別に独立した specialist を学習、停止、resume、評価できる。」
    """
    importers = _importers_of("orchestrator_v1")
    if not importers:
        pytest.fail(
            "UNMET: orchestrator_v1.py (collect->train->evaluate->promote の durable "
            "per-lineage graph、停止後 resume) がどの本番コード (src/ 配下) からも import "
            "されていない。CLI サブコマンドにも lineage 単位の resume/評価を駆動する経路が "
            f"存在しない (現在の cli.py サブコマンド: {sorted(_cli_subcommands())})。"
            "collect-trajectories/train-from-trajectories は各々 --run-name 単位の再開は "
            "持つが、アーキタイプ別に独立した evaluate/promote までを含む一貫した lineage "
            "管理は配線されていない。"
        )


# ---------------------------------------------------------------------------
# 条項 13
# ---------------------------------------------------------------------------


def test_clause_13_deck_policy_joint_optimization_shares_one_foundation_init() -> None:
    """正典 §22 条項13:

    「deck と policy の交互最適化を行う場合、同じ FoundationInit と Joint Race で
    公平に比較できる。」
    """
    importers = _importers_of("joint_optimization_v1")
    if not importers:
        pytest.fail(
            "UNMET: joint_optimization_v1.py (RaceConditionsV1 で同一 FoundationInit / "
            "opponent schedule / transitions / training seeds を強制する Joint Race) が "
            "どの本番コード (src/ 配下) からも import されていない。deck-policy 交互最適化を"
            "起動する CLI 経路も存在しない "
            f"(現在の cli.py サブコマンド: {sorted(_cli_subcommands())})。"
        )


# ---------------------------------------------------------------------------
# 条項 14
# ---------------------------------------------------------------------------


def test_clause_14_ascent_suite_and_top_band_suite_are_separated() -> None:
    """正典 §22 条項14:

    「`ascent_suite` と `top_band_suite` を分離し、final policy が全 rating 経路で
    fixed bundle のまま評価される。」

    field 名が存在するかではなく、**その suite を実行して結果を返す経路**があるかを見る。
    正典 §14.0 は ascent_suite が「lower -> middle -> high の順に opponent block を
    通過させ、各 band の score、fault、rating-proxy trajectory、最大 drawdown を測る」
    ものと定める。型だけでは何も測れない。
    """
    from mage_ptcg.meta_specialist.evaluation_suites_v1 import (
        SuiteGameResultV1,
        build_ascent_suite_v1,
        build_top_band_suite_v1,
        run_evaluation_suite_v1,
    )

    band_map = {"w": "lower", "m": "middle", "s": "high", "u": "ambiguous"}
    available = ("champ", "m", "s", "u", "w")

    ascent = build_ascent_suite_v1(band_map=band_map, available=available)
    top = build_top_band_suite_v1(
        band_map=band_map, available=available, historical_champions=("champ",)
    )

    # 1. 2 つの suite が実際に別物であること。同じ相手集合なら分離していない。
    assert ascent.suite_name != top.suite_name
    assert ascent.schedule_id() != top.schedule_id()
    assert set(ascent.opponent_ids()) != set(top.opponent_ids())

    # 2. ascent が正典 §14.0 の lower -> middle -> high 順であること。
    assert [block.band for block in ascent.blocks] == ["lower", "middle", "high"]

    # 3. band 未確定 (ambiguous) の相手が評価へ紛れ込まないこと。
    assert "u" not in ascent.opponent_ids()
    assert "u" not in top.opponent_ids()

    # 4. 実行して band 別に score・fault・rating-proxy trajectory・最大 drawdown が
    #    返ること。型の存在ではなく、既知の入力に対する値で確かめる。
    def play_block(band, opponents):
        # 勝ってから負ける並びにし、drawdown が 0 でないことを意味のある形で出す。
        return [
            SuiteGameResultV1(
                opponent_id=opponents[0], opponent_version="v", seat=index % 2,
                scenario_seed=index, score=1.0 if index < 2 else 0.0,
            )
            for index in range(4)
        ]

    report = run_evaluation_suite_v1(ascent, play_block=play_block)

    assert [band.band for band in report.bands] == ["lower", "middle", "high"]
    for band in report.bands:
        assert band.games == 4
        assert band.score_rate == 0.5
        assert len(band.rating_proxy_trajectory) == 4
        assert band.max_drawdown > 0.0, "勝ってから負けたのに drawdown が 0 になっている"
        assert band.fault_rate == 0.0
        assert band.seat_score_rates  # 座席別 score が測られている

    # 5. 実測で回す経路が存在すること。module だけあって runner が無ければ、
    #    「型はあるが何も測れない」という本条項が禁じている状態のままである。
    runner = _REPO_ROOT / "scripts" / "run_evaluation_suite.py"
    assert runner.is_file(), "suite を実測で回す runner が無い"
    runner_source = runner.read_text(encoding="utf-8")
    assert "run_evaluation_suite_v1" in runner_source
    assert "band_map_from_manifest_v1" in runner_source, (
        "band を実測 manifest から読まずに suite を組んでいる (正典 §13)"
    )


# ---------------------------------------------------------------------------
# 条項 15
# ---------------------------------------------------------------------------


def test_clause_15_global_submission_schedule_names_primary_and_backup() -> None:
    """正典 §22 条項15:

    「cross-archetype `GlobalSubmissionSchedule` から primary 1 件、backup /
    challenger 最大 1 件を指名できる。」
    """
    importers = _importers_of("global_race_v1")
    if not importers:
        pytest.fail(
            "UNMET: global_race_v1.py (select_global_submission_v1: 全 archetype "
            "champion から primary 1件・backup最大1件を選ぶ pre-registered gate) が "
            "どの本番コード (src/ 配下) からも import されていない。CLI にも cross-archetype "
            "の Global Submission Race を起動する経路がない "
            f"(現在の cli.py サブコマンド: {sorted(_cli_subcommands())})。"
        )


# ---------------------------------------------------------------------------
# 条項 16
# ---------------------------------------------------------------------------


def test_clause_16_promotion_gate_blocks_champion_and_submission_decisions() -> None:
    """正典 §22 条項16:

    「promotion gate を満たさない candidate が champion または submission decision へ
    昇格しない。」
    """
    # global_race_v1's integrity/non-inferiority disqualification is the only
    # promotion-gate-shaped mechanism meta-specialist defines for a champion or
    # submission decision; it is unwired (see clause 15). continuous_league's
    # generic evaluation.promotion module is never imported by meta_specialist
    # either, so no promotion gate currently blocks any meta-specialist
    # champion or submission decision in production.
    race_importers = _importers_of("global_race_v1")
    promotion_importers = [
        str(path.relative_to(_REPO_ROOT))
        for path in _all_src_python_files()
        if path.is_relative_to(_SRC_META_SPECIALIST)
        and "evaluation.promotion" in path.read_text(encoding="utf-8")
    ]
    if not race_importers and not promotion_importers:
        pytest.fail(
            "UNMET: meta-specialist に champion/submission decision を実際にゲートする "
            "promotion gate の配線が存在しない。global_race_v1.py の disqualification "
            "gate はどの本番コードからも import されておらず (条項15参照)、"
            "mage_ptcg.evaluation.promotion / continuous_league の promotion gate も "
            "meta_specialist から一度も参照されていない。CLI にも champion 昇格を判定する "
            f"サブコマンドがない (現在の cli.py サブコマンド: {sorted(_cli_subcommands())})。"
        )


# ---------------------------------------------------------------------------
# 条項 17
# ---------------------------------------------------------------------------


def test_clause_17_active_slot_intent_protects_the_desired_champion_from_a_third_submission() -> None:
    """正典 §22 条項17:

    「active slot 2 件の意図を記録し、望む champion を第三の提出で非 active にしない
    運用手順がある。」
    """
    from mage_ptcg.meta_specialist.lifecycle import (
        LifecycleError,
        SubmissionLifecycleRecord,
        SubmissionState,
        advance_lifecycle,
    )

    bundle_sha256 = "1" * 64
    record = SubmissionLifecycleRecord.draft(bundle_sha256=bundle_sha256, active_slot_intent="primary")
    record = advance_lifecycle(
        record, SubmissionState.SUBMITTED,
        {
            "submission_id": "desired-champion", "submitted_at_utc": "2026-08-15T00:00:00Z",
            "daily_slot_number": 1, "recorded_by": "operator",
        },
    )
    record = advance_lifecycle(
        record, SubmissionState.VALIDATION_PASSED,
        {"validation_log": "ok", "validated_at_utc": "2026-08-15T00:05:00Z"},
    )

    # Two active submissions (this one plus one other) is the sealed limit and
    # succeeds when the desired champion is included.
    within_limit = advance_lifecycle(
        record, SubmissionState.ACTIVE_CONFIRMED,
        {
            "active_checked_at_utc": "2026-08-15T00:10:00Z",
            "active_submission_ids": ["desired-champion", "other-submission"],
        },
    )
    assert within_limit.state is SubmissionState.ACTIVE_CONFIRMED

    # A recorded active set of 3 (the desired champion plus two others, as a
    # third submission would produce if it silently pushed the champion into a
    # non-active slot alongside two actives) is rejected outright: the record
    # cannot even claim more than the sealed 2-slot limit.
    with pytest.raises(LifecycleError):
        advance_lifecycle(
            record, SubmissionState.ACTIVE_CONFIRMED,
            {
                "active_checked_at_utc": "2026-08-15T00:10:00Z",
                "active_submission_ids": ["desired-champion", "other-a", "other-b"],
            },
        )

    # A recorded active set that omits the desired champion itself (exactly the
    # failure a careless third submission would produce) is also rejected.
    with pytest.raises(LifecycleError):
        advance_lifecycle(
            record, SubmissionState.ACTIVE_CONFIRMED,
            {
                "active_checked_at_utc": "2026-08-15T00:10:00Z",
                "active_submission_ids": ["other-a", "other-b"],
            },
        )


# ---------------------------------------------------------------------------
# 条項 18
# ---------------------------------------------------------------------------


def test_clause_18_user_artifacts_are_never_deleted_without_a_cleanup_manifest() -> None:
    """正典 §22 条項18: 「cleanup manifest なしに user artifact を削除しない。」

    manifest の型があるかではなく、**manifest 無しの削除が実際に拒否されるか**を見る。
    """
    from mage_ptcg.meta_specialist.cleanup_manifest_v1 import (
        CleanupManifestV1Error, validate_deletion_authorization_v1,
    )

    import inspect

    signature = inspect.signature(validate_deletion_authorization_v1)
    # 未承認の削除が拒否されること。引数形状は実装に合わせて探索する。
    try:
        validate_deletion_authorization_v1(*[None] * len(signature.parameters))
    except CleanupManifestV1Error:
        refused = True
    except TypeError:
        refused = False
    else:
        refused = False
    if not refused:
        pytest.fail(
            "UNMET: validate_deletion_authorization_v1 が未承認 (manifest 無し) の削除を "
            f"拒否しない。signature={signature}. 正典 §20 は「cleanup planner が path、"
            "size、content hash、参照元、再生成方法、保持理由、復元可能性を列挙する」ことを "
            "求めるが、その列挙も実在しない。"
        )


# ---------------------------------------------------------------------------
# 条項 19
# ---------------------------------------------------------------------------


def test_clause_19_dirty_worktree_is_recorded_and_excluded_from_cleanup_scope(
    tmp_path: Path,
) -> None:
    """正典 §22 条項19:

    「dirty worktree を記録・保護し、remote branch / Git history を cleanup scope に
    含めない。」

    型ではなく、**dirty な worktree に対して破壊的操作が実際に止まるか**を見る。
    """
    from mage_ptcg.meta_specialist.worktree_guard_v1 import (
        WorktreeGuardV1Error, assert_worktree_clean_for_destructive_operation_v1,
        inspect_worktree_status_v1,
    )

    status = inspect_worktree_status_v1(str(_REPO_ROOT))
    recorded = {f for f in getattr(status, "__dataclass_fields__", {})}
    if not {"branch", "head"} & {f.lower() for f in recorded}:
        pytest.fail(
            f"UNMET: WorktreeStatusV1 が記録するのは {sorted(recorded)} だけで、正典 §20 が "
            "要求する開始 branch / HEAD / porcelain status を保持しない。"
        )

    # この worktree は実際に dirty なので、破壊的操作は止まらなければならない。
    try:
        assert_worktree_clean_for_destructive_operation_v1(str(_REPO_ROOT))
    except WorktreeGuardV1Error:
        pass
    else:
        pytest.fail(
            "UNMET: 未コミット変更のある worktree に対して "
            "assert_worktree_clean_for_destructive_operation_v1 が通過した。"
            "dirty worktree の保護が効いていない。"
        )


# ---------------------------------------------------------------------------
# 条項 20
# ---------------------------------------------------------------------------


def test_clause_20_no_code_path_automatically_submits_to_kaggle() -> None:
    """正典 §22 条項20:

    「Kaggle submission を自動実行しない。」
    """
    suspicious_calls = ("competition_submit", "competitions submit", "submissions.upload")
    offending: list[str] = []
    for path in sorted(_SRC_META_SPECIALIST.glob("*.py")):
        text_lower = path.read_text(encoding="utf-8").lower()
        if any(call in text_lower for call in suspicious_calls):
            offending.append(str(path.relative_to(_REPO_ROOT)))
    script_paths = sorted(_SCRIPTS_ROOT.glob("*meta_specialist*.py"))
    for path in script_paths:
        text_lower = path.read_text(encoding="utf-8").lower()
        if any(call in text_lower for call in suspicious_calls):
            offending.append(str(path.relative_to(_REPO_ROOT)))
    assert not offending, (
        f"meta-specialist code paths call a Kaggle submission API: {offending}"
    )
    # The CLI itself has no submission-shaped subcommand: build/verify only.
    subcommands = _cli_subcommands()
    submit_like = {name for name in subcommands if "submit" in name and "verify" not in name and "build" not in name}
    assert not submit_like, f"meta-specialist CLI exposes a submit-shaped subcommand: {submit_like}"
