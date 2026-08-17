"""継続リーグの学習・収集・評価・更新を操作する CLI。"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

import yaml

from main import read_deck_csv
from mage_ptcg.policy_learning.r2d3.learner import LearnerConfig
from mage_ptcg.policy_learning.r2d3.model import R2D3ModelConfig
from mage_ptcg.policy_learning.r2d3.online_collection import (
    MixtureManifest,
    MixtureMember,
)

from .benchmark import BenchmarkManifest, ExposureSnapshot, SubjectDeck
from .cabt import CabtMatchExecutor
from .calibration import (
    CalibrationObservation,
    fit_calibration,
    forecast_public_score,
    load_observations,
    register_observation,
)
from .candidate_runtime import load_runtime_policy
from .catalog import CatalogEntry, CatalogSnapshot
from .checkpoint_stream import publish_checkpoint
from .collector import CollectionRequest, collect_experience
from .contracts import (
    LeagueContractError,
    atomic_write_bytes,
    atomic_write_json,
    content_id,
    load_json,
)
from .coverage import ReplayCoverage
from .cycle import plan_cycle_from_manifest
from .controller import (
    ContinuousLeagueController,
    SubprocessTaskHandler,
    render_checkpoint_benchmark_terminal_summary,
)
from .evaluation import EvaluationJob, compare_evaluations, run_evaluation
from .evaluation_history import record_checkpoint_evaluation
from .learner_service import (
    ContinuousLearner,
    ContinuousLearnerConfig,
    learner_progress_status,
    updates_for_replay_passes,
)
from .population_epoch import (
    PopulationEpoch,
    apply_population_rollover,
    build_rollover_manifest,
)
from .psro_manager import decide_expansion
from .qualification import qualify_ref
from .replay_sealer import import_replay_dataset, load_sealed_replay, seal_replay_dataset
from .report import (
    PromotionGate,
    consume_sealed_holdout,
    evaluate_promotion_gate,
    write_evaluation_report,
)
from .source_intake import build_qualified_submitted_catalog, refresh_sources
from mage_ptcg.bootstrap_champion.contracts import (
    BootstrapChampionManifest,
)
from mage_ptcg.bootstrap_champion.initializer import (
    initialize_from_checkpoint,
    initialize_from_distillation,
    publish_bootstrap_runtime,
)
from mage_ptcg.bootstrap_champion.pipeline import (
    build_candidates_artifact,
    select_champion,
    select_finalists,
    write_schedule,
)
from mage_ptcg.bootstrap_champion.teacher import (
    BootstrapTeacherExample,
    collect_teacher_dataset,
    encoded_examples_from_dataset,
    load_teacher_trace,
)
from mage_ptcg.bootstrap_champion.distillation import (
    DistillationConfig,
    distill_bootstrap_policy,
)
from mage_ptcg.bootstrap_champion.runner import run_schedule as run_bootstrap_schedule


def _load_mapping(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        with path.open(encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
    else:
        value = load_json(path)
    if not isinstance(value, Mapping):
        raise LeagueContractError(f"configuration must be an object: {path}")
    return dict(value)


def _dataclass_config(cls: Any, payload: Mapping[str, Any]) -> Any:
    allowed = {field.name for field in fields(cls)}
    unknown = set(payload) - allowed
    if unknown:
        raise LeagueContractError(
            f"unknown {cls.__name__} settings: {sorted(unknown)}"
        )
    return cls(**payload)


def _catalog(path: Path) -> CatalogSnapshot:
    return CatalogSnapshot.from_dict(load_json(path))


def _exposure(path: Path) -> ExposureSnapshot:
    return ExposureSnapshot.from_dict(load_json(path))


def _mixture(path: Path) -> MixtureManifest:
    payload = load_json(path)
    if "members" in payload:
        return MixtureManifest.build(
            [MixtureMember(**member) for member in payload["members"]]
        )
    return MixtureManifest.from_payload(payload)


def _parse_opponent_episode_quotas(values: list[str] | None) -> tuple[tuple[str, int], ...]:
    quotas: list[tuple[str, int]] = []
    for value in values or []:
        opponent_id, separator, count_text = value.rpartition("=")
        if not separator or not opponent_id:
            raise LeagueContractError(
                "--opponent-episodes must be OPPONENT_INSTANCE_ID=EVEN_GAMES"
            )
        try:
            count = int(count_text)
        except ValueError as exc:
            raise LeagueContractError(
                "--opponent-episodes game count must be an integer"
            ) from exc
        quotas.append((opponent_id, count))
    return tuple(quotas)


def _cmd_learn(args: argparse.Namespace) -> dict[str, Any]:
    configuration = _load_mapping(args.config) if args.config else {}
    learner = ContinuousLearner(
        replay_manifest_path=args.replay_manifest,
        population_epoch_id=args.population_epoch_id,
        output_root=args.output,
        deck=read_deck_csv(args.deck),
        model_config=_dataclass_config(
            R2D3ModelConfig, configuration.get("model", {})
        ),
        learner_config=_dataclass_config(
            LearnerConfig, configuration.get("learner", {})
        ),
        service_config=_dataclass_config(
            ContinuousLearnerConfig, configuration.get("service", {})
        ),
        resume_checkpoint=args.resume,
        resume_training_identity_hash=args.resume_identity,
        bootstrap_checkpoint=args.bootstrap_checkpoint,
    )
    max_replay_passes = getattr(args, "max_replay_passes", None)
    if max_replay_passes is not None:
        max_updates = updates_for_replay_passes(
            sequence_count=len(learner.replay),
            batch_size=learner.service_config.batch_size,
            replay_passes=max_replay_passes,
        )
    else:
        max_updates = args.max_updates
    return learner.run(
        max_updates=max_updates,
        requested_max_replay_passes=max_replay_passes,
    )


def _cmd_learn_status(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_json(args.progress)
    if not isinstance(payload, dict):
        raise LeagueContractError("learner progress summary must be an object")
    return {**payload, **learner_progress_status(
        payload, stale_after_seconds=args.stale_after_seconds
    )}


def _cmd_bootstrap_build_candidates(args: argparse.Namespace) -> dict[str, Any]:
    return build_candidates_artifact(
        catalog=_catalog(args.catalog),
        deck_asset_registry=args.deck_asset_registry,
        simulator_contract_hash=args.simulator_contract_hash,
        output=args.output,
    )


def _cmd_bootstrap_schedule(args: argparse.Namespace) -> dict[str, Any]:
    return write_schedule(
        candidate_registry_path=args.candidate_registry,
        opponent_instance_ids=args.opponent_instance,
        games_per_candidate=args.games_per_candidate,
        seed_namespace=args.seed_namespace,
        output=args.output,
    )


def _cmd_bootstrap_validate(args: argparse.Namespace) -> dict[str, Any]:
    return select_champion(
        candidate_registry_path=args.candidate_registry,
        validation_schedule_path=args.validation_schedule,
        results_path=args.results,
        screen_benchmark_id=args.screen_benchmark_id,
        output=args.output,
    )


def _cmd_bootstrap_rank(args: argparse.Namespace) -> dict[str, Any]:
    return select_finalists(
        candidate_registry_path=args.candidate_registry,
        schedule_path=args.schedule,
        results_path=args.results,
        finalists=args.finalists,
        output=args.output,
    )


def _cmd_bootstrap_run(args: argparse.Namespace) -> dict[str, Any]:
    return run_bootstrap_schedule(
        candidate_registry=args.candidate_registry,
        catalog=_catalog(args.catalog),
        schedule_path=args.schedule,
        output=args.output,
        scratch_root=args.scratch_root,
        max_steps=args.max_steps,
        teacher_output=args.teacher_output,
        teacher_state_size=args.teacher_state_size,
        teacher_action_size=args.teacher_action_size,
    )


def _cmd_bootstrap_collect_teacher(args: argparse.Namespace) -> dict[str, Any]:
    examples: list[BootstrapTeacherExample] = []
    trace_excluded: set[str] = set()
    trace_skipped = 0
    if args.examples.is_dir():
        examples, trace_excluded, trace_skipped = load_teacher_trace(
            args.examples, teacher_candidate_id=args.teacher_candidate_id
        )
    else:
        with args.examples.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LeagueContractError(f"invalid teacher example {args.examples}:{line_number}") from exc
                row["visible_history"] = tuple(row.get("visible_history", ()))
                row["legal_action_keys"] = tuple(row.get("legal_action_keys", ()))
                row["encoded_state"] = tuple(row.get("encoded_state", ()))
                row["encoded_actions"] = tuple(tuple(item) for item in row.get("encoded_actions", ()))
                examples.append(BootstrapTeacherExample(**row))
    result = collect_teacher_dataset(
        examples=examples,
        excluded_game_ids=trace_excluded | set(args.exclude_game or ()),
        skipped_multi_select_decisions=trace_skipped + args.skipped_multi_select_decisions,
        deck_hash=args.deck_hash,
        teacher_candidate_id=args.teacher_candidate_id,
        seed=args.seed,
        output=args.output,
    )
    return result.to_dict()


def _cmd_bootstrap_distill(args: argparse.Namespace) -> dict[str, Any]:
    """Produce initial model weights from one immutable teacher dataset."""

    dataset_manifest = load_json(args.teacher_dataset / "manifest.json")
    if not isinstance(dataset_manifest, Mapping) or dataset_manifest.get("schema_version") != "bootstrap-teacher-dataset-v1":
        raise LeagueContractError("unsupported Bootstrap teacher dataset")
    configuration = _load_mapping(args.config) if args.config else {}
    model_config = _dataclass_config(R2D3ModelConfig, configuration.get("model", {}))
    distillation_config = _dataclass_config(
        DistillationConfig, configuration.get("distillation", {})
    )
    model = __import__(
        "mage_ptcg.policy_learning.r2d3.model", fromlist=["RecurrentDistributionalQ"]
    ).RecurrentDistributionalQ(model_config)
    import torch

    device = torch.device(args.device)
    model.to(device)
    train = encoded_examples_from_dataset(args.teacher_dataset / str(dataset_manifest["train_file"]))
    validation = encoded_examples_from_dataset(args.teacher_dataset / str(dataset_manifest["validation_file"]))
    for example in [*train, *validation]:
        if len(example["state"]) != model_config.state_size:
            raise LeagueContractError(
                "teacher state encoding width differs from the requested model"
            )
        if any(len(action) != model_config.action_size for action in example["actions"]):
            raise LeagueContractError(
                "teacher action encoding width differs from the requested model"
            )
    result = distill_bootstrap_policy(
        model=model,
        train_examples=train,
        validation_examples=validation,
        config=distillation_config,
        output=args.output,
        device=device,
    )
    return {
        **result.to_dict(),
        "teacher_dataset_id": dataset_manifest["teacher_dataset_id"],
        "model_config_hash": _bootstrap_model_hashes(model_config)[0],
    }


def _bootstrap_model_hashes(config: R2D3ModelConfig) -> tuple[str, str]:
    return (
        content_id("bootstrap-model-config-v1", __import__("dataclasses").asdict(config)),
        content_id(
            "bootstrap-action-schema-v1",
            {
                "state_encoder_version": "semantic-public-state-v1",
                "action_encoder_version": "semantic-legal-action-v1",
                "state_size": config.state_size,
                "action_size": config.action_size,
            },
        ),
    )


def _cmd_bootstrap_initialize(args: argparse.Namespace) -> dict[str, Any]:
    champion = BootstrapChampionManifest.from_dict(load_json(args.champion))
    configuration = _load_mapping(args.config) if args.config else {}
    model_config = _dataclass_config(R2D3ModelConfig, configuration.get("model", {}))
    model = __import__("mage_ptcg.policy_learning.r2d3.model", fromlist=["RecurrentDistributionalQ"]).RecurrentDistributionalQ(model_config)
    model_hash, action_hash = _bootstrap_model_hashes(model_config)
    if champion.initialization_mode.value == "DIRECT_CHECKPOINT":
        if args.source_checkpoint is None or args.distilled_weights is not None or args.teacher_dataset_id is not None:
            raise LeagueContractError("DIRECT_CHECKPOINT requires --source-checkpoint only")
        return initialize_from_checkpoint(
            source_checkpoint=args.source_checkpoint,
            champion=champion,
            model_config_hash=model_hash,
            action_schema_hash=action_hash,
            output=args.output,
            expected_model=model,
        ).to_dict()
    if args.distilled_weights is None or args.teacher_dataset_id is None or args.source_checkpoint is not None:
        raise LeagueContractError("TEACHER_DISTILLATION requires --distilled-weights and --teacher-dataset-id")
    return initialize_from_distillation(
        distilled_weights=args.distilled_weights,
        champion=champion,
        model_config_hash=model_hash,
        action_schema_hash=action_hash,
        teacher_dataset_id=args.teacher_dataset_id,
        output=args.output,
        expected_model=model,
    ).to_dict()


def _cmd_bootstrap_publish_runtime(args: argparse.Namespace) -> dict[str, Any]:
    configuration = _load_mapping(args.config) if args.config else {}
    return publish_bootstrap_runtime(
        bootstrap_checkpoint=args.bootstrap_checkpoint,
        output_root=args.output,
        model_config=_dataclass_config(
            R2D3ModelConfig, configuration.get("model", configuration)
        ),
        deck=read_deck_csv(args.deck),
    )


def _cmd_bootstrap_status(args: argparse.Namespace) -> dict[str, Any]:
    root = args.root
    paths = sorted(root.glob("**/manifest.json"))
    return {
        "root": str(root),
        "manifests": [str(path) for path in paths],
        "count": len(paths),
    }


def _cmd_plan_cycle(args: argparse.Namespace) -> dict[str, Any]:
    plan = plan_cycle_from_manifest(
        catalog=_catalog(args.catalog),
        replay_manifest=args.replay_manifest,
        roles=args.role or ["TRAINING_ACTIVE"],
        bootstrap_episodes_per_new_opponent=args.bootstrap_episodes_per_new_opponent,
        refresh_episodes_per_known_opponent=args.refresh_episodes_per_known_opponent,
    )
    atomic_write_json(args.output, plan.to_dict())
    return plan.to_dict()


def _cmd_build_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    catalog = _catalog(args.catalog)
    specification = _load_mapping(args.spec)
    opponent_ids = specification.get("opponent_instance_ids")
    if opponent_ids is None:
        roles = tuple(
            specification.get("opponent_roles", ["BENCHMARK_VISIBLE"])
        )
        opponent_ids = [
            entry.opponent_instance_id for entry in catalog.by_role(*roles)
        ]
    selected = [catalog.get_instance(opponent_id) for opponent_id in opponent_ids]
    minimum_policy_hashes = int(specification.get("minimum_distinct_policy_hashes", 0))
    minimum_submitted_policies = int(
        specification.get("minimum_submitted_policies", 0)
    )
    if minimum_policy_hashes:
        distinct_policy_hashes = {entry.policy_hash for entry in selected}
        if len(distinct_policy_hashes) < minimum_policy_hashes:
            raise LeagueContractError(
                "benchmark has too few distinct policy hashes: "
                f"{len(distinct_policy_hashes)} < {minimum_policy_hashes}"
            )
    if minimum_submitted_policies:
        submitted = [
            entry for entry in selected
            if entry.policy_kind not in {"rule_v0", "rule_v1"}
        ]
        if len(submitted) < minimum_submitted_policies:
            raise LeagueContractError(
                "benchmark has too few non-rule runtime policies: "
                f"{len(submitted)} < {minimum_submitted_policies}"
            )
    benchmark = BenchmarkManifest.build(
        name=str(specification["name"]),
        catalog=catalog,
        subject_decks=(
            SubjectDeck(**deck) for deck in specification["subject_decks"]
        ),
        opponent_instance_ids=opponent_ids,
        repetitions=int(specification.get("repetitions", 1)),
        execution_blocks=specification.get("execution_blocks", ["main"]),
        base_seed=int(specification.get("base_seed", 71_000)),
        sealed=bool(specification.get("sealed", False)),
    )
    atomic_write_json(args.output, benchmark.to_dict())
    return benchmark.to_dict()


def _cmd_build_exposure(args: argparse.Namespace) -> dict[str, Any]:
    catalog = _catalog(args.catalog)
    coverage = ReplayCoverage.from_replay_manifest(args.replay_manifest)
    if (
        args.replay_dataset_version_id is not None
        and args.replay_dataset_version_id != coverage.replay_dataset_version_id
    ):
        raise LeagueContractError("supplied replay dataset version differs from manifest")
    if (
        args.population_epoch_id is not None
        and args.population_epoch_id != coverage.population_epoch_id
    ):
        raise LeagueContractError("supplied population epoch differs from manifest")
    exposure = ExposureSnapshot.from_replay_coverage(
        coverage=coverage,
        catalog=catalog,
    )
    atomic_write_json(args.output, exposure.to_dict())
    if args.coverage_output is not None:
        atomic_write_json(args.coverage_output, coverage.to_dict())
    return {**exposure.to_dict(), "coverage": coverage.to_dict()}


def _cmd_build_population(args: argparse.Namespace) -> dict[str, Any]:
    catalog = _catalog(args.catalog)
    if args.opponent_instance:
        entries = tuple(
            catalog.get_instance(opponent_id)
            for opponent_id in args.opponent_instance
        )
    else:
        entries = catalog.by_role(*(args.role or ["TRAINING_ACTIVE"]))
    if args.policy_kind:
        selected_kinds = set(args.policy_kind)
        entries = tuple(
            entry for entry in entries if entry.policy_kind in selected_kinds
        )
    if not entries:
        raise LeagueContractError("population selection contains no enabled opponents")
    instance_ids = [entry.opponent_instance_id for entry in entries]
    if len(instance_ids) != len(set(instance_ids)):
        raise LeagueContractError("population selection contains duplicate opponents")
    probability = 1.0 / len(entries)
    parent_population_epoch_id = None
    if getattr(args, "parent_population", None) is not None:
        parent_population_epoch_id = PopulationEpoch.from_dict(
            load_json(args.parent_population)
        ).population_epoch_id
    population = PopulationEpoch.build(
        {entry.opponent_instance_id: probability for entry in entries},
        parent_population_epoch_id=parent_population_epoch_id,
    )
    mixture = MixtureManifest.build(
        [
            MixtureMember(
                opponent_policy_id=entry.opponent_instance_id,
                probability=probability,
                policy_hash=entry.policy_hash,
                source_lineage=entry.source_id,
                family=entry.effective_archetype_id,
                kind=entry.policy_kind,
            )
            for entry in entries
        ]
    )
    args.output.mkdir(parents=True, exist_ok=True)
    atomic_write_json(args.output / "population_epoch.json", population.to_dict())
    atomic_write_json(args.output / "mixture.json", mixture.document())
    return {
        "population_epoch_id": population.population_epoch_id,
        "mixture_hash": mixture.mixture_hash,
        "members": len(entries),
    }


def _cmd_build_runtime_catalog(args: argparse.Namespace) -> dict[str, Any]:
    entries = list(_catalog(args.base_catalog).entries) if args.base_catalog else []
    known_asset_ids = {entry.asset_id for entry in entries}
    runtime_entries = []
    deck_root = args.output.parent / f"{args.output.stem}-decks"
    for runtime_path in args.runtime:
        runtime = load_runtime_policy(runtime_path)
        manifest = runtime.manifest
        runtime_id = runtime.runtime_policy_id
        asset_id = f"runtime-policy-{runtime_id}"
        if asset_id in known_asset_ids:
            raise LeagueContractError(
                f"runtime policy already exists in base catalog: {runtime_id}"
            )
        deck_path = deck_root / f"{manifest['deck_hash']}.csv"
        deck_bytes = (
            "\n".join(str(card_id) for card_id in runtime.deck) + "\n"
        ).encode("utf-8")
        if deck_path.exists():
            if deck_path.read_bytes() != deck_bytes:
                raise LeagueContractError(
                    f"runtime deck path collision: {deck_path}"
                )
        else:
            atomic_write_bytes(deck_path, deck_bytes)
        entry = CatalogEntry(
            asset_id=asset_id,
            policy_id=runtime_id,
            deck_id=f"deck-{manifest['deck_hash'][:16]}",
            source_id=f"training-checkpoint:{manifest['training_checkpoint_id']}",
            policy_kind="runtime_policy",
            runtime_path=str(Path(runtime_path).resolve()),
            deck_path=str(deck_path.resolve()),
            policy_hash=runtime_id,
            deck_hash=manifest["deck_hash"],
            source_hash=manifest["training_checkpoint_id"],
            role=args.role,
            deck_family="MODEL_SELF_PLAY",
            archetype_id="R2D3_HISTORY",
            runtime_config_hash=content_id(
                "runtime-opponent-config-v1",
                {
                    "model_config": manifest["model_config"],
                    "action_mode": manifest["action_mode"],
                    "q_reduction": manifest["q_reduction"],
                    "legal_mask_version": manifest["legal_mask_version"],
                    "recurrent_contract_version": manifest[
                        "recurrent_contract_version"
                    ],
                    "tie_break_version": manifest["tie_break_version"],
                },
            ),
        )
        entries.append(entry)
        runtime_entries.append(entry)
        known_asset_ids.add(asset_id)
    catalog = CatalogSnapshot.build(entries)
    atomic_write_json(args.output, catalog.to_dict())
    return {
        "catalog_snapshot_id": catalog.catalog_snapshot_id,
        "entries": len(catalog.entries),
        "runtime_entries": len(runtime_entries),
        "output": str(args.output),
    }


def _cmd_publish(args: argparse.Namespace) -> dict[str, Any]:
    configuration = _load_mapping(args.config) if args.config else {}
    return publish_checkpoint(
        checkpoint_path=args.checkpoint,
        output_root=args.output,
        model_config=_dataclass_config(
            R2D3ModelConfig, configuration.get("model", configuration)
        ),
        deck=read_deck_csv(args.deck),
    )


def _cmd_evaluate(args: argparse.Namespace) -> dict[str, Any]:
    catalog = _catalog(args.catalog)
    benchmark = BenchmarkManifest.from_dict(load_json(args.benchmark), catalog)
    exposure = _exposure(args.exposure)
    runtime = load_runtime_policy(args.runtime)
    if runtime.runtime_policy_id != args.runtime.name:
        raise LeagueContractError(
            "runtime directory name must equal runtime_policy_id"
        )
    executor = CabtMatchExecutor(
        runtime_policy=runtime,
        subject_decks=benchmark.subject_decks,
        output_root=args.output / "matches",
        scratch_root=args.output / "scratch",
        max_steps=args.max_steps,
    )
    job = EvaluationJob.build(benchmark, runtime.runtime_policy_id, exposure)
    return run_evaluation(
        job=job,
        benchmark=benchmark,
        catalog=catalog,
        exposure=exposure,
        output_dir=args.output,
        run_game=executor,
        max_games=args.max_games,
    )


def _cmd_collect(args: argparse.Namespace) -> dict[str, Any]:
    catalog = _catalog(args.catalog)
    runtime = load_runtime_policy(args.runtime)
    subject = SubjectDeck(
        deck_id=args.subject_deck_id,
        deck_path=str(args.deck),
        deck_hash=__import__("hashlib").sha256(args.deck.read_bytes()).hexdigest(),
    )
    executor = CabtMatchExecutor(
        runtime_policy=runtime,
        subject_decks=(subject,),
        output_root=args.output / "matches",
        scratch_root=args.output / "scratch",
        max_steps=args.max_steps,
    )
    quotas = _parse_opponent_episode_quotas(
        getattr(args, "opponent_episodes", None)
    )
    request = CollectionRequest(
        population_epoch_id=args.population_epoch_id,
        candidate_runtime_policy_id=runtime.runtime_policy_id,
        episodes=(sum(count for _opponent_id, count in quotas) if quotas else args.episodes),
        base_seed=args.seed,
        subject_deck_id=args.subject_deck_id,
        execution_block=args.execution_block,
        opponent_episode_quotas=quotas,
    )
    return collect_experience(
        request=request,
        mixture=_mixture(args.mixture),
        catalog=catalog,
        executor=executor,
        output_root=args.output / "chunks",
    )


def _cmd_seal(args: argparse.Namespace) -> dict[str, Any]:
    version = seal_replay_dataset(
        chunk_manifests=args.chunk_manifest,
        output_root=args.output,
        population_epoch_id=args.population_epoch_id,
        capacity=args.capacity,
        parent_replay_manifest=args.parent_replay_manifest,
    )
    return {
        "replay_dataset_version_id": version.replay_dataset_version_id,
        "manifest_path": str(version.manifest_path),
        "replay_path": str(version.replay_path),
        "sequence_count": version.sequence_count,
    }


def _cmd_import_replay(args: argparse.Namespace) -> dict[str, Any]:
    version = import_replay_dataset(
        source_replay_path=args.source_replay,
        source_manifest_path=args.source_manifest,
        output_root=args.output,
        population_epoch_id=args.population_epoch_id,
        source_label=args.source_label,
    )
    return {
        "replay_dataset_version_id": version.replay_dataset_version_id,
        "manifest_path": str(version.manifest_path),
        "replay_path": str(version.replay_path),
        "sequence_count": version.sequence_count,
        "source": args.source_label,
    }


def _cmd_refresh(args: argparse.Namespace) -> dict[str, Any]:
    configuration = _load_mapping(args.config) if args.config else {}
    return refresh_sources(
        repo=args.repo,
        artifact_root=args.output,
        ingest_config=configuration,
        fetch_remotes=args.fetch_remote,
        mode=args.mode,
    )


def _cmd_catalog(args: argparse.Namespace) -> dict[str, Any]:
    counts = _load_mapping(args.role_counts) if args.role_counts else None
    initial_roles = (
        _load_mapping(args.initial_role_map) if args.initial_role_map else None
    )
    return build_qualified_submitted_catalog(
        repo=args.repo,
        qualification_ledger_path=args.qualification_ledger,
        output_root=args.output,
        deck_pool_path=args.deck_pool,
        prior_role_ledger_path=args.prior_role_ledger,
        initial_role_map=initial_roles,
        new_role_counts=counts,
        seed=args.seed,
    )


def _cmd_qualify_ref(args: argparse.Namespace) -> dict[str, Any]:
    return qualify_ref(
        repo=args.repo,
        ref=args.ref,
        asset_id=args.asset_id,
        output_root=args.output,
        base_ledger=args.base_ledger,
        games=args.games,
        seed=args.seed,
        max_steps=args.max_steps,
    )


def _cmd_role_map(args: argparse.Namespace) -> dict[str, Any]:
    role_map: dict[str, str] = {}
    sources = (
        (args.training_population, "TRAINING_ACTIVE"),
        (args.validation_population, "BENCHMARK_VISIBLE"),
        (args.deck_holdout_population, "BENCHMARK_SEALED"),
        (args.final_holdout_population, "BENCHMARK_SEALED"),
    )
    for path, role in sources:
        payload = load_json(path)
        for entry in payload.get("entries", []):
            asset_id = str(entry.get("asset_id") or entry.get("opponent_id") or "")
            if not asset_id:
                raise LeagueContractError(f"population entry has no asset_id: {path}")
            previous = role_map.get(asset_id)
            if previous is not None and previous != role:
                raise LeagueContractError(
                    f"asset {asset_id} appears in incompatible population roles"
                )
            role_map[asset_id] = role
    atomic_write_json(args.output, role_map)
    return {
        "assets": len(role_map),
        "role_counts": {
            role: sum(value == role for value in role_map.values())
            for role in sorted(set(role_map.values()))
        },
        "output": str(args.output),
    }
def _cmd_calibrate(args: argparse.Namespace) -> dict[str, Any]:
    return fit_calibration(
        load_observations(args.observations),
        output_path=args.output,
        minimum_independent_policies=args.minimum_policies,
    )


def _cmd_register_calibration(args: argparse.Namespace) -> dict[str, Any]:
    observation = CalibrationObservation.build(
        runtime_policy_id=args.runtime_policy_id,
        benchmark_id=args.benchmark_id,
        evaluation_result_id=args.evaluation_result_id,
        offline_score_rate=args.offline_score_rate,
        public_score=args.public_score,
        submission_reference=args.submission_reference,
    )
    added = register_observation(args.registry, observation)
    return {"added": added, **observation.to_dict()}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))
    return rows


def _cmd_compare(args: argparse.Namespace) -> dict[str, Any]:
    comparison = compare_evaluations(
        _load_jsonl(args.candidate_games),
        _load_jsonl(args.baseline_games),
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    atomic_write_json(args.output, comparison)
    return comparison


def _cmd_report(args: argparse.Namespace) -> dict[str, Any]:
    evaluation = load_json(args.evaluation)
    comparison = load_json(args.comparison) if args.comparison else None
    calibration_forecast = None
    if args.calibration:
        calibration_forecast = forecast_public_score(
            load_json(args.calibration),
            evaluation["aggregate"]["game_weighted"]["score_rate"],
        )
    gate = evaluate_promotion_gate(
        evaluation,
        comparison=comparison,
        gate=PromotionGate(
            minimum_game_weighted_score_rate=args.minimum_score,
            minimum_worst_opponent_score_rate=args.minimum_worst,
            minimum_baseline_delta=args.minimum_delta,
            require_positive_bootstrap_lower_bound=args.require_positive_bootstrap,
        ),
    )
    return write_evaluation_report(
        evaluation_result=evaluation,
        output_dir=args.output,
        comparison=comparison,
        calibration_forecast=calibration_forecast,
        promotion_gate=gate,
    )


def _cmd_consume_sealed(args: argparse.Namespace) -> dict[str, Any]:
    result = load_json(args.evaluation)
    return consume_sealed_holdout(
        marker_root=args.marker_root,
        holdout_id=args.holdout_id,
        runtime_policy_id=result["runtime_policy_id"],
        benchmark_id=result["benchmark_id"],
        evaluation_result=result,
    )


def _cmd_controller(args: argparse.Namespace) -> dict[str, Any]:
    handlers = {}
    forward_output = sys.stderr.isatty()
    if args.handler_config:
        commands = _load_mapping(args.handler_config)
        for task_type, command in commands.items():
            handlers[str(task_type)] = SubprocessTaskHandler(
                shlex.split(str(command)),
                args.root / "task_requests",
                forward_output=forward_output,
                quiet_result=(
                    forward_output
                    and str(task_type) in {"VISIBLE_EVALUATION", "SEALED_EVALUATION"}
                ),
            )
    if args.evaluation_command:
        handlers["VISIBLE_EVALUATION"] = SubprocessTaskHandler(
            shlex.split(args.evaluation_command),
            args.root / "task_requests",
            forward_output=forward_output,
            quiet_result=forward_output,
        )
    controller = ContinuousLeagueController(
        root=args.root,
        checkpoint_event_dir=args.events,
        task_event_dir=args.inbox,
        visible_benchmark_id=args.benchmark_id,
        exposure_snapshot_id=args.exposure_snapshot_id,
        handlers=handlers,
        cpu_slots=args.cpu_slots,
        gpu_slots=args.gpu_slots,
        max_pending_evaluations=(
            None
            if args.max_pending_evaluations == 0
            else args.max_pending_evaluations
        ),
    )
    checkpoint_history = getattr(args, "checkpoint_history", None)
    if forward_output and checkpoint_history is not None:
        controller.status_reporter = lambda scheduler: render_checkpoint_benchmark_terminal_summary(
            history_root=Path(checkpoint_history), scheduler=scheduler, stream=sys.stderr
        )
    recovered = (
        controller.scheduler.recover_interrupted()
        if args.recover_interrupted
        else 0
    )
    if args.once:
        return {**controller.run_once(), "recovered_interrupted": recovered}
    controller.run(poll_seconds=args.poll_seconds)
    return {"status": "STOPPED", "recovered_interrupted": recovered}


def _cmd_task_worker(args: argparse.Namespace) -> dict[str, Any]:
    request = load_json(args.task_request)
    configuration = _load_mapping(args.config)
    if request.get("task_type") not in {
        "VISIBLE_EVALUATION",
        "SEALED_EVALUATION",
    }:
        raise LeagueContractError(
            f"built-in task worker cannot execute {request.get('task_type')}"
        )
    payload = request["payload"]
    catalog = _catalog(Path(configuration["catalog"]))
    benchmark = BenchmarkManifest.from_dict(
        load_json(Path(configuration["benchmark"])), catalog
    )
    exposure = _exposure(Path(configuration["exposure"]))
    if (
        payload["benchmark_id"] != benchmark.benchmark_id
        or payload["exposure_snapshot_id"] != exposure.exposure_snapshot_id
    ):
        raise LeagueContractError("task request benchmark/exposure mismatch")
    runtime_dir = (
        Path(configuration["runtime_policy_root"]) / payload["runtime_policy_id"]
    )
    runtime = load_runtime_policy(runtime_dir)
    evaluation_root = Path(configuration["evaluation_output_root"])
    executor = CabtMatchExecutor(
        runtime_policy=runtime,
        subject_decks=benchmark.subject_decks,
        output_root=evaluation_root / "matches",
        scratch_root=evaluation_root / "scratch",
        max_steps=int(configuration.get("max_steps", 10_000)),
    )
    job = EvaluationJob.build(benchmark, runtime.runtime_policy_id, exposure)
    evaluation = run_evaluation(
        job=job,
        benchmark=benchmark,
        catalog=catalog,
        exposure=exposure,
        output_dir=evaluation_root / job.evaluation_job_id,
        run_game=executor,
    )
    checkpoint_evaluation = None
    history_root = configuration.get("checkpoint_evaluation_history_root")
    if history_root is not None:
        training_checkpoint_id = payload.get("training_checkpoint_id")
        training_step = payload.get("training_step")
        if not isinstance(training_checkpoint_id, str) or type(training_step) is not int:
            raise LeagueContractError(
                "checkpoint evaluation history requires training checkpoint ID and step"
            )
        checkpoint_evaluation = record_checkpoint_evaluation(
            Path(history_root),
            training_checkpoint_id=training_checkpoint_id,
            training_step=training_step,
            evaluation_result=evaluation,
        )
    result = {"task_id": request["task_id"], "evaluation_result": evaluation}
    if checkpoint_evaluation is not None:
        result["checkpoint_evaluation"] = checkpoint_evaluation
    atomic_write_json(args.result, result)
    return result


def _cmd_psro(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_json(args.input)
    parent = PopulationEpoch.from_dict(payload["parent_population"])
    return decide_expansion(
        parent=parent,
        candidate_opponent_instance_id=payload["candidate_opponent_instance_id"],
        expanded_payoff_matrix=payload["expanded_payoff_matrix"],
        meta_improvement=float(payload["meta_improvement"]),
        validation_improvement=float(payload["validation_improvement"]),
        faults=int(payload["faults"]),
        novel=bool(payload["novel"]),
        single_opponent_overfit=bool(payload["single_opponent_overfit"]),
    )


def _cmd_rollover_manifest(args: argparse.Namespace) -> dict[str, Any]:
    old_epoch = PopulationEpoch.from_dict(load_json(args.old_population))
    new_epoch = PopulationEpoch.from_dict(load_json(args.new_population))
    result = build_rollover_manifest(
        old_epoch=old_epoch,
        new_epoch=new_epoch,
        new_opponent_instance_ids=args.new_opponent,
        bootstrap_chunk_manifests=args.bootstrap_chunk_manifest,
        global_step=args.global_step,
        replay_dataset_version_id=args.replay_dataset_version_id,
        inherit_optimizer=not args.reset_optimizer,
    )
    atomic_write_json(args.output, result)
    return result


def _cmd_rollover_apply(args: argparse.Namespace) -> dict[str, Any]:
    import torch

    configuration = _load_mapping(args.config) if args.config else {}
    model_config = _dataclass_config(
        R2D3ModelConfig, configuration.get("model", {})
    )
    model = __import__(
        "mage_ptcg.policy_learning.r2d3.model", fromlist=["RecurrentDistributionalQ"]
    ).RecurrentDistributionalQ(model_config)
    learner = __import__(
        "mage_ptcg.policy_learning.r2d3.learner", fromlist=["R2D3Learner"]
    ).R2D3Learner(
        model,
        torch.optim.AdamW(
            model.parameters(),
            lr=float(configuration.get("learning_rate", 1e-4)),
        ),
    )
    optimizer = learner.optimizer
    scheduler = torch.optim.lr_scheduler.LambdaLR(
        optimizer, lr_lambda=lambda _step: 1.0
    )
    replay = load_sealed_replay(args.replay_manifest)
    return apply_population_rollover(
        source_checkpoint_path=args.source_checkpoint,
        destination_checkpoint_path=args.output,
        model=model,
        target=learner.target,
        optimizer=optimizer,
        scheduler=scheduler,
        replay=replay,
        old_population_epoch_id=args.old_population_epoch_id,
        old_replay_dataset_version_id=args.old_replay_dataset_version_id,
        new_population_epoch_id=args.new_population_epoch_id,
        new_replay_dataset_version_id=args.new_replay_dataset_version_id,
        transition_manifest=load_json(args.transition_manifest),
        inherit_optimizer=not args.reset_optimizer,
        seed=args.seed,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    benchmark = subparsers.add_parser("build-benchmark")
    benchmark.add_argument("--catalog", type=Path, required=True)
    benchmark.add_argument("--spec", type=Path, required=True)
    benchmark.add_argument("--output", type=Path, required=True)
    benchmark.set_defaults(handler=_cmd_build_benchmark)

    exposure = subparsers.add_parser("build-exposure")
    exposure.add_argument("--catalog", type=Path, required=True)
    exposure.add_argument("--replay-manifest", type=Path, required=True)
    exposure.add_argument("--replay-dataset-version-id")
    exposure.add_argument("--population-epoch-id")
    exposure.add_argument("--coverage-output", type=Path)
    exposure.add_argument("--output", type=Path, required=True)
    exposure.set_defaults(handler=_cmd_build_exposure)

    population = subparsers.add_parser("build-population")
    population.add_argument("--catalog", type=Path, required=True)
    population.add_argument(
        "--role", action="append"
    )
    population.add_argument("--policy-kind", action="append")
    population.add_argument("--opponent-instance", action="append")
    population.add_argument(
        "--parent-population",
        type=Path,
        help="Replay/optimizer rollover 用の直前 population_epoch.json",
    )
    population.add_argument("--output", type=Path, required=True)
    population.set_defaults(handler=_cmd_build_population)

    runtime_catalog = subparsers.add_parser("build-runtime-catalog")
    runtime_catalog.add_argument(
        "--runtime", type=Path, action="append", required=True
    )
    runtime_catalog.add_argument("--base-catalog", type=Path)
    runtime_catalog.add_argument(
        "--role",
        choices=(
            "TRAINING_ACTIVE",
            "TRAINING_RESERVE",
            "BENCHMARK_VISIBLE",
            "BENCHMARK_SEALED",
            "CALIBRATION_ONLY",
        ),
        default="TRAINING_ACTIVE",
    )
    runtime_catalog.add_argument("--output", type=Path, required=True)
    runtime_catalog.set_defaults(handler=_cmd_build_runtime_catalog)

    learn = subparsers.add_parser("learn")
    learn.add_argument("--replay-manifest", type=Path, required=True)
    learn.add_argument("--population-epoch-id", required=True)
    learn.add_argument("--output", type=Path, required=True)
    learn.add_argument("--deck", type=Path, required=True)
    learn.add_argument("--config", type=Path)
    learn.add_argument("--resume", type=Path)
    learn.add_argument("--resume-identity")
    learn.add_argument(
        "--bootstrap-checkpoint",
        type=Path,
        help="RL step 0 として読む Bootstrap weight bundle。--resume とは併用不可",
    )
    budget = learn.add_mutually_exclusive_group(required=True)
    budget.add_argument("--max-updates", type=int)
    budget.add_argument("--max-replay-passes", type=float)
    learn.set_defaults(handler=_cmd_learn)

    learn_status = subparsers.add_parser("learn-status")
    learn_status.add_argument("--progress", type=Path, required=True)
    learn_status.add_argument("--stale-after-seconds", type=float, default=90.0)
    learn_status.set_defaults(handler=_cmd_learn_status)

    bootstrap_candidates = subparsers.add_parser("bootstrap-build-candidates")
    bootstrap_candidates.add_argument("--catalog", type=Path, required=True)
    bootstrap_candidates.add_argument("--deck-asset-registry", type=Path)
    bootstrap_candidates.add_argument("--simulator-contract-hash", required=True)
    bootstrap_candidates.add_argument("--output", type=Path, required=True)
    bootstrap_candidates.set_defaults(handler=_cmd_bootstrap_build_candidates)

    bootstrap_screen = subparsers.add_parser("bootstrap-screen")
    bootstrap_screen.add_argument("--candidate-registry", type=Path, required=True)
    bootstrap_screen.add_argument("--opponent-instance", action="append", required=True)
    bootstrap_screen.add_argument("--games-per-candidate", type=int, default=256)
    bootstrap_screen.add_argument("--seed-namespace", default="bootstrap-screen-v1")
    bootstrap_screen.add_argument("--output", type=Path, required=True)
    bootstrap_screen.set_defaults(handler=_cmd_bootstrap_schedule)

    bootstrap_validate = subparsers.add_parser("bootstrap-validate")
    bootstrap_validate.add_argument("--candidate-registry", type=Path, required=True)
    bootstrap_validate.add_argument("--validation-schedule", type=Path, required=True)
    bootstrap_validate.add_argument("--results", type=Path, required=True)
    bootstrap_validate.add_argument("--screen-benchmark-id", required=True)
    bootstrap_validate.add_argument("--output", type=Path, required=True)
    bootstrap_validate.set_defaults(handler=_cmd_bootstrap_validate)

    bootstrap_rank = subparsers.add_parser("bootstrap-rank")
    bootstrap_rank.add_argument("--candidate-registry", type=Path, required=True)
    bootstrap_rank.add_argument("--schedule", type=Path, required=True)
    bootstrap_rank.add_argument("--results", type=Path, required=True)
    bootstrap_rank.add_argument("--finalists", type=int, default=4)
    bootstrap_rank.add_argument("--output", type=Path, required=True)
    bootstrap_rank.set_defaults(handler=_cmd_bootstrap_rank)

    bootstrap_run = subparsers.add_parser("bootstrap-run")
    bootstrap_run.add_argument("--candidate-registry", type=Path, required=True)
    bootstrap_run.add_argument("--catalog", type=Path, required=True)
    bootstrap_run.add_argument("--schedule", type=Path, required=True)
    bootstrap_run.add_argument("--output", type=Path, required=True)
    bootstrap_run.add_argument("--scratch-root", type=Path, required=True)
    bootstrap_run.add_argument("--max-steps", type=int, default=10_000)
    bootstrap_run.add_argument("--teacher-output", type=Path)
    bootstrap_run.add_argument("--teacher-state-size", type=int, default=128)
    bootstrap_run.add_argument("--teacher-action-size", type=int, default=64)
    bootstrap_run.set_defaults(handler=_cmd_bootstrap_run)

    bootstrap_teacher = subparsers.add_parser("bootstrap-collect-teacher")
    bootstrap_teacher.add_argument("--examples", type=Path, required=True, help="bootstrap-run の --teacher-output ディレクトリ、または JSONL")
    bootstrap_teacher.add_argument("--deck-hash", required=True)
    bootstrap_teacher.add_argument("--teacher-candidate-id", required=True)
    bootstrap_teacher.add_argument("--exclude-game", action="append")
    bootstrap_teacher.add_argument("--skipped-multi-select-decisions", type=int, default=0)
    bootstrap_teacher.add_argument("--seed", type=int, default=71_000)
    bootstrap_teacher.add_argument("--output", type=Path, required=True)
    bootstrap_teacher.set_defaults(handler=_cmd_bootstrap_collect_teacher)

    bootstrap_distill = subparsers.add_parser("bootstrap-distill")
    bootstrap_distill.add_argument("--teacher-dataset", type=Path, required=True)
    bootstrap_distill.add_argument("--config", type=Path)
    bootstrap_distill.add_argument("--device", default="cpu")
    bootstrap_distill.add_argument("--output", type=Path, required=True)
    bootstrap_distill.set_defaults(handler=_cmd_bootstrap_distill)

    bootstrap_initialize = subparsers.add_parser("bootstrap-initialize")
    bootstrap_initialize.add_argument("--champion", type=Path, required=True)
    bootstrap_initialize.add_argument("--config", type=Path)
    bootstrap_initialize.add_argument("--source-checkpoint", type=Path)
    bootstrap_initialize.add_argument("--distilled-weights", type=Path)
    bootstrap_initialize.add_argument("--teacher-dataset-id")
    bootstrap_initialize.add_argument("--output", type=Path, required=True)
    bootstrap_initialize.set_defaults(handler=_cmd_bootstrap_initialize)

    bootstrap_publish_runtime = subparsers.add_parser("bootstrap-publish-runtime")
    bootstrap_publish_runtime.add_argument("--bootstrap-checkpoint", type=Path, required=True)
    bootstrap_publish_runtime.add_argument("--deck", type=Path, required=True)
    bootstrap_publish_runtime.add_argument("--config", type=Path)
    bootstrap_publish_runtime.add_argument("--output", type=Path, required=True)
    bootstrap_publish_runtime.set_defaults(handler=_cmd_bootstrap_publish_runtime)

    bootstrap_status = subparsers.add_parser("bootstrap-status")
    bootstrap_status.add_argument("--root", type=Path, required=True)
    bootstrap_status.set_defaults(handler=_cmd_bootstrap_status)

    cycle_plan = subparsers.add_parser("plan-cycle")
    cycle_plan.add_argument("--catalog", type=Path, required=True)
    cycle_plan.add_argument("--replay-manifest", type=Path, required=True)
    cycle_plan.add_argument("--role", action="append")
    cycle_plan.add_argument("--bootstrap-episodes-per-new-opponent", type=int, default=32)
    cycle_plan.add_argument("--refresh-episodes-per-known-opponent", type=int, default=0)
    cycle_plan.add_argument("--output", type=Path, required=True)
    cycle_plan.set_defaults(handler=_cmd_plan_cycle)

    publish = subparsers.add_parser("publish")
    publish.add_argument("--checkpoint", type=Path, required=True)
    publish.add_argument("--deck", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    publish.add_argument("--config", type=Path)
    publish.set_defaults(handler=_cmd_publish)

    evaluate = subparsers.add_parser("evaluate")
    for name in ("runtime", "catalog", "benchmark", "exposure", "output"):
        evaluate.add_argument(f"--{name}", type=Path, required=True)
    evaluate.add_argument("--max-games", type=int)
    evaluate.add_argument("--max-steps", type=int, default=10_000)
    evaluate.set_defaults(handler=_cmd_evaluate)

    collect = subparsers.add_parser("collect")
    for name in ("runtime", "catalog", "mixture", "deck", "output"):
        collect.add_argument(f"--{name}", type=Path, required=True)
    collect.add_argument("--population-epoch-id", required=True)
    collect.add_argument("--subject-deck-id", required=True)
    collect.add_argument("--episodes", type=int, default=2)
    collect.add_argument(
        "--opponent-episodes",
        action="append",
        help="相手別の偶数局数。指定時は --episodes を使わず、全相手を先後同数で収集する",
    )
    collect.add_argument("--seed", type=int, default=71_000)
    collect.add_argument(
        "--execution-block",
        default="training",
        help="同じ population/runtime 内で chunk を分ける不変の収集ブロック名",
    )
    collect.add_argument("--max-steps", type=int, default=10_000)
    collect.set_defaults(handler=_cmd_collect)

    seal = subparsers.add_parser("seal")
    seal.add_argument("--chunk-manifest", type=Path, action="append", required=True)
    seal.add_argument("--population-epoch-id", required=True)
    seal.add_argument("--output", type=Path, required=True)
    seal.add_argument("--capacity", type=int)
    seal.add_argument("--parent-replay-manifest", type=Path)
    seal.set_defaults(handler=_cmd_seal)

    import_replay = subparsers.add_parser("import-replay")
    for name in ("source-replay", "source-manifest", "output"):
        import_replay.add_argument(f"--{name}", type=Path, required=True)
    import_replay.add_argument("--population-epoch-id", required=True)
    import_replay.add_argument("--source-label", required=True)
    import_replay.set_defaults(handler=_cmd_import_replay)

    refresh = subparsers.add_parser("refresh-sources")
    refresh.add_argument("--repo", type=Path, default=Path.cwd())
    refresh.add_argument("--output", type=Path, required=True)
    refresh.add_argument("--config", type=Path)
    refresh.add_argument("--fetch-remote", action="append", default=[])
    refresh.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    refresh.set_defaults(handler=_cmd_refresh)

    catalog = subparsers.add_parser("build-catalog")
    catalog.add_argument("--repo", type=Path, default=Path.cwd())
    catalog.add_argument("--qualification-ledger", type=Path, required=True)
    catalog.add_argument("--output", type=Path, required=True)
    catalog.add_argument("--deck-pool", type=Path)
    catalog.add_argument("--prior-role-ledger", type=Path)
    catalog.add_argument("--initial-role-map", type=Path)
    catalog.add_argument("--role-counts", type=Path)
    catalog.add_argument("--seed", type=int, default=71_000)
    catalog.set_defaults(handler=_cmd_catalog)

    qualify = subparsers.add_parser("qualify-ref")
    qualify.add_argument("--repo", type=Path, default=Path.cwd())
    qualify.add_argument("--ref", required=True)
    qualify.add_argument("--asset-id")
    qualify.add_argument("--base-ledger", type=Path)
    qualify.add_argument("--output", type=Path, required=True)
    qualify.add_argument("--games", type=int, default=2)
    qualify.add_argument("--seed", type=int, default=76_000)
    qualify.add_argument("--max-steps", type=int, default=10_000)
    qualify.set_defaults(handler=_cmd_qualify_ref)

    role_map = subparsers.add_parser("role-map-from-populations")
    role_map.add_argument("--training-population", type=Path, required=True)
    role_map.add_argument("--validation-population", type=Path, required=True)
    role_map.add_argument("--deck-holdout-population", type=Path, required=True)
    role_map.add_argument("--final-holdout-population", type=Path, required=True)
    role_map.add_argument("--output", type=Path, required=True)
    role_map.set_defaults(handler=_cmd_role_map)

    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--observations", type=Path, required=True)
    calibrate.add_argument("--output", type=Path, required=True)
    calibrate.add_argument("--minimum-policies", type=int, default=30)
    calibrate.set_defaults(handler=_cmd_calibrate)

    register_calibration = subparsers.add_parser("register-calibration")
    register_calibration.add_argument("--registry", type=Path, required=True)
    register_calibration.add_argument("--runtime-policy-id", required=True)
    register_calibration.add_argument("--benchmark-id", required=True)
    register_calibration.add_argument("--evaluation-result-id", required=True)
    register_calibration.add_argument("--offline-score-rate", type=float, required=True)
    register_calibration.add_argument("--public-score", type=float, required=True)
    register_calibration.add_argument("--submission-reference", required=True)
    register_calibration.set_defaults(handler=_cmd_register_calibration)

    compare = subparsers.add_parser("compare")
    compare.add_argument("--candidate-games", type=Path, required=True)
    compare.add_argument("--baseline-games", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--bootstrap-samples", type=int, default=2_000)
    compare.add_argument("--seed", type=int, default=71_000)
    compare.set_defaults(handler=_cmd_compare)

    report = subparsers.add_parser("report")
    report.add_argument("--evaluation", type=Path, required=True)
    report.add_argument("--comparison", type=Path)
    report.add_argument("--calibration", type=Path)
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--minimum-score", type=float, default=0.5)
    report.add_argument("--minimum-worst", type=float, default=0.0)
    report.add_argument("--minimum-delta", type=float, default=0.0)
    report.add_argument("--require-positive-bootstrap", action="store_true")
    report.set_defaults(handler=_cmd_report)

    sealed = subparsers.add_parser("consume-sealed")
    sealed.add_argument("--evaluation", type=Path, required=True)
    sealed.add_argument("--marker-root", type=Path, required=True)
    sealed.add_argument("--holdout-id", required=True)
    sealed.set_defaults(handler=_cmd_consume_sealed)

    controller = subparsers.add_parser("controller")
    controller.add_argument("--root", type=Path, required=True)
    controller.add_argument("--events", type=Path, required=True)
    controller.add_argument("--inbox", type=Path)
    controller.add_argument("--benchmark-id", required=True)
    controller.add_argument("--exposure-snapshot-id", required=True)
    controller.add_argument("--evaluation-command")
    controller.add_argument("--handler-config", type=Path)
    controller.add_argument("--cpu-slots", type=int, default=1)
    controller.add_argument("--gpu-slots", type=int, default=0)
    controller.add_argument("--max-pending-evaluations", type=int, default=0)
    controller.add_argument("--recover-interrupted", action="store_true")
    controller.add_argument("--checkpoint-history", type=Path)
    controller.add_argument("--poll-seconds", type=float, default=10.0)
    controller.add_argument("--once", action="store_true")
    controller.set_defaults(handler=_cmd_controller)

    task_worker = subparsers.add_parser("task-worker")
    task_worker.add_argument("--config", type=Path, required=True)
    task_worker.add_argument("--task-request", type=Path, required=True)
    task_worker.add_argument("--result", type=Path, required=True)
    task_worker.add_argument("--quiet", action="store_true")
    task_worker.set_defaults(handler=_cmd_task_worker)

    psro = subparsers.add_parser("psro-decide")
    psro.add_argument("--input", type=Path, required=True)
    psro.set_defaults(handler=_cmd_psro)

    rollover_manifest = subparsers.add_parser("rollover-manifest")
    rollover_manifest.add_argument("--old-population", type=Path, required=True)
    rollover_manifest.add_argument("--new-population", type=Path, required=True)
    rollover_manifest.add_argument("--new-opponent", action="append", required=True)
    rollover_manifest.add_argument(
        "--bootstrap-chunk-manifest", type=Path, action="append", required=True
    )
    rollover_manifest.add_argument("--global-step", type=int, required=True)
    rollover_manifest.add_argument("--replay-dataset-version-id", required=True)
    rollover_manifest.add_argument("--reset-optimizer", action="store_true")
    rollover_manifest.add_argument("--output", type=Path, required=True)
    rollover_manifest.set_defaults(handler=_cmd_rollover_manifest)

    rollover_apply = subparsers.add_parser("rollover-apply")
    rollover_apply.add_argument("--source-checkpoint", type=Path, required=True)
    rollover_apply.add_argument("--transition-manifest", type=Path, required=True)
    rollover_apply.add_argument("--replay-manifest", type=Path, required=True)
    rollover_apply.add_argument("--old-population-epoch-id", required=True)
    rollover_apply.add_argument("--old-replay-dataset-version-id", required=True)
    rollover_apply.add_argument("--new-population-epoch-id", required=True)
    rollover_apply.add_argument("--new-replay-dataset-version-id", required=True)
    rollover_apply.add_argument("--output", type=Path, required=True)
    rollover_apply.add_argument("--config", type=Path)
    rollover_apply.add_argument("--reset-optimizer", action="store_true")
    rollover_apply.add_argument("--seed", type=int, default=71_000)
    rollover_apply.set_defaults(handler=_cmd_rollover_apply)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except (LeagueContractError, OSError, RuntimeError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 2
    if not getattr(args, "quiet", False):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0
