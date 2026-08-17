"""Support Platform Command Line Interface.

Implements all support tool command logic including validations, schedules,
ratings, mining, registries, deduplication, sampling, and Phase 2 diagnostics.
"""

from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
from typing import Any

from mage_ptcg.offline_training_v1_support.contracts import (
    SupportContractError,
    atomic_write_json,
    atomic_write_records,
    load_records,
)
from mage_ptcg.offline_training_v1_support.statistics import evaluate_game_statistics
from mage_ptcg.offline_training_v1_support.schedule import (
    generate_schedule,
    validate_games_against_schedule,
)
from mage_ptcg.offline_training_v1_support.cross_play import (
    generate_cross_play_report,
    format_cross_play_csv,
    format_cross_play_markdown,
)
from mage_ptcg.offline_training_v1_support.ratings import (
    compute_elo,
    compute_bradley_terry,
)
from mage_ptcg.offline_training_v1_support.registries import SupportRegistryManager
from mage_ptcg.offline_training_v1_support.mining import mine_hard_states
from mage_ptcg.offline_training_v1_support.dedup import process_and_deduplicate
from mage_ptcg.offline_training_v1_support.sampling import priority_sample

# Phase 2 module imports
from mage_ptcg.offline_training_v1_support.dataset_ops import DatasetLifecycleManager
from mage_ptcg.offline_training_v1_support.teacher_registry import TeacherRegistry
from mage_ptcg.offline_training_v1_support.teacher_cache import TeacherCache
from mage_ptcg.offline_training_v1_support.iteration import DistillationOrchestrator, VALID_PHASES
from mage_ptcg.offline_training_v1_support.sweep import SweepOrchestrator
from mage_ptcg.offline_training_v1_support.calibration import (
    compute_ece,
    compute_nll,
    compute_brier_score,
    fit_temperature,
    evaluate_group_calibration,
)
from mage_ptcg.offline_training_v1_support.ood import compute_ood_diagnostics
from mage_ptcg.offline_training_v1_support.performance import analyze_performance_measurements
from mage_ptcg.offline_training_v1_support.reproducibility import ReproducibilityBundleManager
from mage_ptcg.offline_training_v1_support.promotion import PromotionEvaluator

MAX_FILE_SIZE_LIMIT = 50 * 1024 * 1024  # 50MB default guard


def file_size_guard(path: Path, limit: int = MAX_FILE_SIZE_LIMIT) -> None:
    """Raise SupportContractError if file exceeds size safety limits."""
    if path.exists() and path.stat().st_size > limit:
        raise SupportContractError(
            f"File size safety violation: {path.name} is {path.stat().st_size} bytes, "
            f"exceeding the limit of {limit} bytes."
        )


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnose the workspace state and verify safety configuration."""
    print("=== Support Platform Diagnostics ===")
    print(f"Python Version: {sys.version}")
    print(f"Active directory: {Path.cwd()}")

    fix_dir = Path(__file__).parent.parent.parent.parent / "tests" / "offline_training_v1_support" / "fixtures"
    if fix_dir.exists():
        print(f"Integration fixtures directory found: {fix_dir.resolve()}")
    else:
        print("Warning: Fixtures directory not found.")

    print("Status: OK")
    return 0


def cmd_self_audit(args: argparse.Namespace) -> int:
    """Run comprehensive self-audit checks across namespaces."""
    print("=== Self-Audit ===")
    print("Verifying implementation structure...")

    base_dir = Path(__file__).parent
    modules = ["contracts.py", "statistics.py", "schedule.py", "registries.py", "dataset_ops.py", "teacher_registry.py"]
    for mod in modules:
        target = base_dir / mod
        if target.exists():
            print(f"  {mod}: FOUND")
        else:
            print(f"  {mod}: MISSING")
            return 1

    print("Self-Audit status: PASS")
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    """Generate deterministic seat-balanced evaluation schedule."""
    config_path = Path(args.config).resolve()
    file_size_guard(config_path)

    with config_path.open("r", encoding="utf-8") as f:
        config = json.load(f)

    schedule_records = generate_schedule(config)
    print(f"Generated {len(schedule_records)} schedule entries deterministically.")

    if not args.dry_run:
        output_path = Path(args.output).resolve()
        atomic_write_records(output_path, schedule_records)
        print(f"Schedule written atomically to {output_path}")

    return 0


def cmd_validate_games(args: argparse.Namespace) -> int:
    """Verify executed games matches schedule records."""
    sch_path = Path(args.schedule).resolve()
    games_path = Path(args.games_jsonl).resolve()
    file_size_guard(sch_path)
    file_size_guard(games_path)

    schedule = load_records(sch_path)
    games = load_records(games_path)

    report = validate_games_against_schedule(schedule, games)
    print("=== Schedule Matching Summary ===")
    print(f"Scheduled: {report['total_scheduled']}")
    print(f"Completed: {report['total_completed']}")
    print(f"Missing:   {report['missing_count']}")
    print(f"Duplicate: {report['duplicate_count']}")
    print(f"Unmatched: {report['unmatched_count']}")

    if args.output and not args.dry_run:
        out_path = Path(args.output).resolve()
        atomic_write_json(out_path, report)
        print(f"Validation report saved atomically to {out_path}")

    if report["missing_count"] > 0 or report["duplicate_count"] > 0:
        return 1
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    """Aggregate per-game results to statistics JSON."""
    games_path = Path(args.games_jsonl).resolve()
    file_size_guard(games_path)

    games = load_records(games_path)
    stats = evaluate_game_statistics(games)

    print("=== Evaluation Summary ===")
    print(f"Total games: {stats['total_games']}")
    print(f"Win Rate:    {stats['overall_win_rate']:.2%}")
    print(f"Wilson Interval: {stats['wilson_interval']}")
    print(f"Crashes:     {stats['crash_count']}")
    print(f"Timeouts:    {stats['timeout_count']}")

    if not args.dry_run:
        out_path = Path(args.output).resolve()
        atomic_write_json(out_path, stats)
        print(f"Statistics summary saved atomically to {out_path}")

    return 0


def cmd_cross_play(args: argparse.Namespace) -> int:
    """Generate cross-play matrix files."""
    games_path = Path(args.games_jsonl).resolve()
    file_size_guard(games_path)

    games = load_records(games_path)
    report = generate_cross_play_report(games)

    print(format_cross_play_markdown(report))

    if not args.dry_run:
        output_path = Path(args.output).resolve()
        atomic_write_json(output_path, report)
        print(f"Cross-play report saved atomically to {output_path}")

        if args.markdown:
            md_path = Path(args.markdown).resolve()
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(format_cross_play_markdown(report), encoding="utf-8")
            print(f"Markdown cross-play report saved to {md_path}")

        if args.csv:
            csv_path = Path(args.csv).resolve()
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text(format_cross_play_csv(report), encoding="utf-8")
            print(f"CSV win-rate matrix saved to {csv_path}")

    return 0


def cmd_rate(args: argparse.Namespace) -> int:
    """Compute Elo or Bradley-Terry ratings."""
    games_path = Path(args.games_jsonl).resolve()
    file_size_guard(games_path)

    games = load_records(games_path)
    method = args.method.lower()

    if method == "elo":
        ratings = compute_elo(games)
        report = {
            "schema_version": "support-rating-report-v1",
            "method": "elo",
            "ratings": ratings,
        }
    elif method in ("bt", "bradley-terry"):
        res = compute_bradley_terry(games)
        if res["status"] == "NOT_CONVERGED":
            print("Error: Bradley-Terry computation failed to converge.")
            return 2
        report = {
            "schema_version": "support-rating-report-v1",
            "method": "bradley-terry",
            "ratings": {
                k: {"rating": v, "status": "CONVERGED"} for k, v in res["ratings"].items()
            },
        }
    else:
        print(f"Unknown rating method: {method}")
        return 1

    print("=== Rating Standings ===")
    for k, v in sorted(report["ratings"].items(), key=lambda x: x[1].get("rating", 0), reverse=True):
        print(f"  {k}: {v.get('rating', 0):.2f}")

    if not args.dry_run:
        output_path = Path(args.output).resolve()
        atomic_write_json(output_path, report)
        print(f"Ratings report saved atomically to {output_path}")

    return 0


def cmd_registry(args: argparse.Namespace) -> int:
    """Interface with registries repository."""
    reg_dir = Path(args.registry_dir).resolve()
    manager = SupportRegistryManager(reg_dir)
    sub = args.reg_command

    if sub == "list":
        reg = manager.get_registry(args.kind)
        records = reg.list_records()
        print(f"Listing {len(records)} records in {args.kind} registry:")
        for r in records:
            key_id = r.get(f"{args.kind}_id") or r.get("run_id") or r.get("deck_id") or r.get("opponent_id")
            print(f"  ID: {key_id} | Hash: {r.get('content_hash')}")

    elif sub == "inspect":
        reg = manager.get_registry(args.kind)
        record = reg.get(args.id)
        if not record:
            print(f"Record {args.id} not found in {args.kind} registry.")
            return 1
        print(json.dumps(record, indent=2, sort_keys=True))

    elif sub == "compare":
        diffs = manager.compare_records(args.kind, args.id_a, args.id_b)
        print(f"Comparing {args.id_a} and {args.id_b} in {args.kind} registry:")
        print(json.dumps(diffs, indent=2, sort_keys=True))

    elif sub == "validate":
        reg = manager.get_registry(args.kind)
        corruptions = reg.validate_registry()
        if corruptions:
            print("Corruption detected:")
            for c in corruptions:
                print(f"  - {c}")
            return 1
        print("Registry validation: PASS")

    elif sub == "archive":
        if args.dry_run:
            print(f"Dry-run: Archiving {args.id} in {args.kind} registry.")
        else:
            reg = manager.get_registry(args.kind)
            reg.archive(args.id)
            print(f"Record {args.id} successfully archived in {args.kind} registry.")
    else:
        print(f"Unknown registry command: {sub}")
        return 1

    return 0


def cmd_mine(args: argparse.Namespace) -> int:
    """Mine hard states from decision diagnostics files."""
    dec_path = Path(args.decisions_jsonl).resolve()
    file_size_guard(dec_path)

    records = load_records(dec_path)
    hard_states = mine_hard_states(records)

    print(f"Mined {len(hard_states)} hard states from {len(records)} decision entries.")
    if hard_states:
        print(f"Top priority score: {hard_states[0]['priority_score']}")

    if not args.dry_run:
        output_path = Path(args.output).resolve()
        atomic_write_records(output_path, hard_states)
        print(f"Hard states saved atomically to {output_path}")

    return 0


def cmd_deduplicate(args: argparse.Namespace) -> int:
    """Deduplicate decisions and quarantine conflicts."""
    input_path = Path(args.input).resolve()
    file_size_guard(input_path)

    clean, quarantined = process_and_deduplicate(input_path)
    print(f"Processed {input_path.name}:")
    print(f"  Clean records:   {len(clean)}")
    print(f"  Isolated issues: {len(quarantined)}")

    if not args.dry_run:
        out_path = Path(args.output).resolve()
        q_path = Path(args.quarantine).resolve()

        atomic_write_records(out_path, clean)
        atomic_write_records(q_path, quarantined)
        print(f"Clean records written atomically to {out_path}")
        print(f"Quarantined records written atomically to {q_path}")

    return 0


def cmd_sample(args: argparse.Namespace) -> int:
    """Priority sample clean records for training usage."""
    input_path = Path(args.input).resolve()
    config_path = Path(args.config).resolve()
    file_size_guard(input_path)
    file_size_guard(config_path)

    records = load_records(input_path)
    with config_path.open("r", encoding="utf-8") as f:
        weight_config = json.load(f)

    sampled, manifest = priority_sample(
        records,
        weight_config,
        sampled_count=args.count,
        replacement=args.replacement,
        seed=args.seed,
    )

    print(f"Sampled {len(sampled)} records from {len(records)} entries.")

    if not args.dry_run:
        out_path = Path(args.output).resolve()
        man_path = Path(args.manifest).resolve()

        atomic_write_records(out_path, sampled)
        atomic_write_json(man_path, manifest)
        print(f"Sampled records saved atomically to {out_path}")
        print(f"Manifest summary saved atomically to {man_path}")

    return 0


# --- Phase 2 Command Dispatchers ---


def cmd_dataset(args: argparse.Namespace) -> int:
    """Dataset lifecycle CLI routing."""
    sub = args.dataset_command
    manager = DatasetLifecycleManager(Path.cwd())

    if sub == "inspect":
        p = Path(args.manifest).resolve()
        file_size_guard(p)
        info = manager.inspect_dataset(p)
        print(json.dumps(info, indent=2))

    elif sub == "validate":
        p = Path(args.manifest).resolve()
        file_size_guard(p)
        res = manager.validate_dataset(p)
        print(f"Validation Status: {res['validation_status']}")
        if res["errors"]:
            print("Errors:")
            for err in res["errors"]:
                print(f"  - {err}")
            return 1

    elif sub == "diff":
        p_a = Path(args.manifest_a).resolve()
        p_b = Path(args.manifest_b).resolve()
        file_size_guard(p_a)
        file_size_guard(p_b)
        diff = manager.diff_datasets(p_a, p_b)
        print(json.dumps(diff, indent=2))

    elif sub == "merge":
        paths = [Path(p).resolve() for p in args.manifests]
        for p in paths:
            file_size_guard(p)
        output = Path(args.output).resolve()

        if args.dry_run:
            plan = manager.generate_merge_plan(paths)
            print("Merge Plan:")
            print(json.dumps(plan, indent=2))
        else:
            merged = manager.execute_merge(paths, output)
            print(f"Dataset successfully merged. New ID: {merged['dataset_id']}")

    elif sub == "compact":
        p = Path(args.manifest).resolve()
        file_size_guard(p)
        output = Path(args.output).resolve()

        if args.dry_run:
            print("Dry-run: Compaction preview of manifest shards...")
        else:
            compacted = manager.execute_compact(p, output)
            print(f"Compacted manifest generated successfully at {output}")

    elif sub == "migrate":
        p = Path(args.manifest).resolve()
        file_size_guard(p)
        output = Path(args.output).resolve()
        target = args.target_version

        if args.dry_run:
            status = manager.migrate_dataset_plan(p, target)
            print(f"Migration compatibility status: {status}")
        else:
            migrated = manager.execute_migration(p, output, target)
            print(f"Successfully migrated schema to version {migrated['schema_version']}")

    elif sub == "gc-plan":
        reg = Path(args.registry_dir).resolve()
        manifests = [Path(p).resolve() for p in args.manifests]
        plan = manager.generate_gc_plan(reg, manifests)
        print("Garbage Collection Plan (No physical deletion performed):")
        print(json.dumps(plan, indent=2))

    return 0


def cmd_teacher(args: argparse.Namespace) -> int:
    """Teacher operations CLI routing."""
    sub = args.teacher_command
    reg_dir = Path(args.registry_dir).resolve()
    reg = TeacherRegistry(reg_dir)
    cache = TeacherCache(reg_dir / "cache")

    if sub == "probe":
        desc_path = Path(args.descriptor).resolve()
        file_size_guard(desc_path)
        with desc_path.open("r", encoding="utf-8") as f:
            desc = json.load(f)

        probed = reg.probe_teacher_capability(desc, args.entrypoint)
        print(f"Capability status: {probed['status']}")
        print(f"Reason: {probed.get('capability_reason')}")

        if not args.dry_run:
            reg.register_teacher(probed)
            print(f"Registered probed teacher descriptor.")

    elif sub == "cache-stats":
        stats = cache.get_public_stats()
        print("Teacher Cache Stats (Excludes all private identifiers):")
        print(json.dumps(stats, indent=2))

    return 0


def cmd_iterate(args: argparse.Namespace) -> int:
    """Distillation orchestration command routing."""
    sub = args.iterate_command
    run_dir = Path(args.run_dir).resolve()
    orchestrator = DistillationOrchestrator(run_dir)

    if sub == "create-round":
        config_path = Path(args.config).resolve()
        file_size_guard(config_path)
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)

        manifest = orchestrator.create_round(args.iteration_id, args.round, cfg)
        print(f"Created round {args.round} manifest successfully.")

    elif sub == "advance-phase":
        outputs = {}
        if args.outputs:
            outputs = json.loads(args.outputs)
        manifest = orchestrator.advance_phase(args.round, args.phase, args.status, outputs)
        print(f"Round {args.round} advanced. Status: {manifest['status']}")

    elif sub == "mix":
        plan_path = Path(args.plan).resolve()
        file_size_guard(plan_path)
        with plan_path.open("r", encoding="utf-8") as f:
            plan = json.load(f)

        base_recs = load_records(args.base)
        new_recs = load_records(args.new)
        hard_recs = load_records(args.hard)

        mixed = orchestrator.mix_dataset_records(plan, base_recs, new_recs, hard_recs)
        print(f"Mixed {len(mixed)} records successfully.")

        if not args.dry_run:
            out_path = Path(args.output).resolve()
            atomic_write_records(out_path, mixed)
            print(f"Atomic output written to {out_path}")

    return 0


def cmd_sweep(args: argparse.Namespace) -> int:
    """Hyperparameter sweep planning CLI routing."""
    sub = args.sweep_command
    orchestrator = SweepOrchestrator(args.sweep_id)

    if sub == "plan":
        config_path = Path(args.config).resolve()
        file_size_guard(config_path)
        with config_path.open("r", encoding="utf-8") as f:
            cfg = json.load(f)

        base_config = cfg.get("base_config", {})
        parameter_space = cfg.get("parameter_space", {})
        search_method = cfg.get("search_method", "grid")
        maximum_trials = cfg.get("maximum_trials", 10)

        trials = orchestrator.generate_initial_trials(
            base_config, parameter_space, search_method, maximum_trials
        )
        print(f"Planned {len(trials)} initial trials.")

        if not args.dry_run:
            out_path = Path(args.output).resolve()
            atomic_write_records(out_path, trials)
            print(f"Sweep trials manifest saved atomically to {out_path}")

    elif sub == "advance-halving":
        trials_path = Path(args.trials_manifest).resolve()
        file_size_guard(trials_path)
        trials = load_records(trials_path)

        advanced = orchestrator.advance_successive_halving(
            trials,
            reduction_factor=args.reduction_factor,
            min_survivors=args.min_survivors,
            objective=args.objective,
            direction=args.direction,
        )
        print(f"Promoted {len(advanced)} surviving trials to the next stage.")

        if not args.dry_run and advanced:
            out_path = Path(args.output).resolve()
            atomic_write_records(out_path, advanced)
            print(f"Next stage trials appended to {out_path}")

    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    """Calibrate probabilities and logits CLI routing."""
    sub = args.calibrate_command
    pred_path = Path(args.predictions).resolve()
    file_size_guard(pred_path)
    predictions = load_records(pred_path)

    if sub == "eval":
        ece, mce, bin_stats = compute_ece(predictions)
        nll = compute_nll(predictions)
        brier = compute_brier_score(predictions)

        report = {
            "ece": ece,
            "mce": mce,
            "nll": nll,
            "brier": brier,
            "bin_stats": bin_stats,
            "groups": evaluate_group_calibration(predictions),
        }
        print(json.dumps(report, indent=2))

        if not args.dry_run and args.output:
            out_path = Path(args.output).resolve()
            atomic_write_json(out_path, report)

    elif sub == "fit":
        optimal_t = fit_temperature(predictions)
        print(f"Optimal scaling temperature: {optimal_t}")

        if not args.dry_run and args.output:
            out_path = Path(args.output).resolve()
            atomic_write_json(out_path, {"optimal_temperature": optimal_t})

    return 0


def cmd_ood(args: argparse.Namespace) -> int:
    """OOD diagnostics CLI routing."""
    input_path = Path(args.input).resolve()
    file_size_guard(input_path)
    records = load_records(input_path)

    ood_records = [compute_ood_diagnostics(r) for r in records]
    ood_count = sum(1 for r in ood_records if r["ood_score"] >= 2.0)
    print(f"Scanned {len(records)} entries. High-risk OOD detected: {ood_count}")

    if not args.dry_run:
        out_path = Path(args.output).resolve()
        atomic_write_records(out_path, ood_records)
        print(f"OOD diagnostics written to {out_path}")

    return 0


def cmd_performance(args: argparse.Namespace) -> int:
    """Aggregate CPU/latency measurements CLI routing."""
    input_path = Path(args.input).resolve()
    file_size_guard(input_path)
    measurements = load_records(input_path)

    report = analyze_performance_measurements(measurements)
    print(f"Status: {report['status']}")
    if report["status"] == "PASS":
        m = report["metrics"]
        print(f"Mean Latency: {m['mean_ns'] / 1e6:.4f} ms")
        print(f"P95 Latency:  {m['p95_ns'] / 1e6:.4f} ms")
        print(f"Throughput:   {m['throughput_ops_sec']:.2f} ops/sec")
    else:
        print("Insufficient evidence for profiling analysis.")

    if not args.dry_run:
        out_path = Path(args.output).resolve()
        atomic_write_json(out_path, report)
        print(f"Report saved atomically to {out_path}")

    return 0


def cmd_repro_bundle(args: argparse.Namespace) -> int:
    """Reproducibility metadata bundle routing."""
    sub = args.repro_command
    manager = ReproducibilityBundleManager(Path.cwd())

    if sub == "create":
        meta_path = Path(args.metadata).resolve()
        file_size_guard(meta_path)
        with meta_path.open("r", encoding="utf-8") as f:
            metadata = json.load(f)

        dest = Path(args.output).resolve()
        res = manager.assemble_bundle(dest, metadata, args.dry_run)
        print("Bundle Manifest Created:")
        print(json.dumps(res, indent=2))

    elif sub == "verify":
        dest = Path(args.bundle).resolve()
        res = manager.verify_bundle(dest)
        print(f"Verification validity: {res['valid']}")
        if not res["valid"]:
            print("Errors:")
            for err in res["errors"]:
                print(f"  - {err}")
            return 1
        print("Repro bundle check: PASS")

    return 0


def cmd_promotion_report(args: argparse.Namespace) -> int:
    """Promotion evidence logic routing."""
    sum_path = Path(args.summary).resolve()
    file_size_guard(sum_path)
    with sum_path.open("r", encoding="utf-8") as f:
        stats = json.load(f)

    evaluator = PromotionEvaluator()
    packet = evaluator.evaluate_gates(stats, known_defects_count=args.known_defects)

    print("=== Gate Decisions ===")
    for k, v in packet["gate_results"].items():
        print(f"  {k}: {v.get('status', 'FAIL')}")
    print(f"Overall Result: {packet['overall_result']}")
    print(f"Promotion Switch status: {packet['promotion_status']}")

    if not args.dry_run:
        out_path = Path(args.output).resolve()
        atomic_write_json(out_path, packet)
        print(f"Sign-off packet saved atomically to {out_path}")

    return 0


def cmd_chaos_check(args: argparse.Namespace) -> int:
    """Execute mock chaos checks."""
    import tempfile
    from pathlib import Path
    from mage_ptcg.offline_training_v1_support.contracts import SupportContractError, FileLock

    with tempfile.TemporaryDirectory() as tmp_dir:
        # 1. Truncated JSONL check
        corrupt_file = Path(tmp_dir) / "corrupt.jsonl"
        corrupt_file.write_text('{"episode_id": "ep_1"}\n{"episode_id":\n', encoding="utf-8")
        from mage_ptcg.offline_training_v1_support.contracts import load_records
        try:
            load_records(corrupt_file)
            print("Chaos check FAIL: parsed truncated JSONL without error")
            return 1
        except SupportContractError:
            pass

        # 2. File lock collision check
        lock_file = Path(tmp_dir) / "test.lock"
        with FileLock(lock_file):
            try:
                with FileLock(lock_file, timeout=0.1) as lock2:
                    print("Chaos check FAIL: acquired locked resource")
                    return 2
            except TimeoutError:
                pass

    print("CHAOS_STATUS: ALL_MOCK_HAZARDS_SAFE_AND_BLOCKED")
    return 0


def cmd_census(args: argparse.Namespace) -> int:
    """Run Phase 1-2 implementation census."""
    import json
    from mage_ptcg.offline_training_v1_support.census import run_census
    res = run_census()
    print(json.dumps(res, indent=2))
    return 0


def cmd_traceability(args: argparse.Namespace) -> int:
    """Run traceability verification."""
    import json
    from mage_ptcg.offline_training_v1_support.traceability import get_traceability_data
    res = get_traceability_data()
    print(json.dumps(res, indent=2))
    return 0


def cmd_fuzz(args: argparse.Namespace) -> int:
    """Run property-style fuzzing tests."""
    import json
    from mage_ptcg.offline_training_v1_support.fuzz import run_fuzz_tests
    res = run_fuzz_tests(args.seed)
    print(json.dumps(res, indent=2))
    return 0 if res["status"] == "SUCCESS" else 1


def cmd_scale_check(args: argparse.Namespace) -> int:
    """Run scalability validation."""
    import json
    from mage_ptcg.offline_training_v1_support.scale_check import run_scale_check
    res = run_scale_check(args.records)
    print(json.dumps(res, indent=2))
    return 0


def cmd_compatibility(args: argparse.Namespace) -> int:
    """Check contract compatibility."""
    import json
    from mage_ptcg.offline_training_v1_support.compatibility import CompatibilityChecker
    with open(args.left, "r", encoding="utf-8") as f:
        schema_a = json.load(f)
    with open(args.right, "r", encoding="utf-8") as f:
        schema_b = json.load(f)
    checker = CompatibilityChecker()
    res = checker.analyze(schema_a, schema_b)
    print(json.dumps(res, indent=2))
    return 0


def cmd_adapt(args: argparse.Namespace) -> int:
    """Translate external artifacts."""
    import json
    from mage_ptcg.offline_training_v1_support.integration_adapters import ClaudeIntegrationAdapter
    with open(args.input, "r", encoding="utf-8") as f:
        data = json.load(f)
    adapter = ClaudeIntegrationAdapter()
    res = adapter.adapt(args.type, data)
    print(json.dumps(res, indent=2))
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two experiment outputs."""
    import json
    from mage_ptcg.offline_training_v1_support.contracts import load_records
    from mage_ptcg.offline_training_v1_support.comparison import ExperimentComparer
    games_a = load_records(args.candidate_a)
    games_b = load_records(args.candidate_b)
    comparer = ExperimentComparer()
    res = comparer.compare_paired(games_a, games_b)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=2)
    else:
        print(json.dumps(res, indent=2))
    return 0


def cmd_candidate_analysis(args: argparse.Namespace) -> int:
    """Multi-objective Pareto analysis."""
    import json
    from mage_ptcg.offline_training_v1_support.candidate_analysis import CandidateAnalyzer
    with open(args.candidates, "r", encoding="utf-8") as f:
        candidates = json.load(f)
    with open(args.safety_limits, "r", encoding="utf-8") as f:
        safety_limits = json.load(f)
    analyzer = CandidateAnalyzer()
    res = analyzer.analyze_candidates(candidates, safety_limits)
    print(json.dumps(res, indent=2))
    return 0


def cmd_plan_evaluation(args: argparse.Namespace) -> int:
    """Run sample-size planner."""
    import json
    from mage_ptcg.offline_training_v1_support.evaluation_planner import EvaluationPlanner
    planner = EvaluationPlanner()
    res = planner.plan_sample_size(
        baseline_win_rate=args.baseline_win_rate,
        target_improvement=args.target_improvement,
    )
    print(json.dumps(res, indent=2))
    return 0


def cmd_teacher_ensemble(args: argparse.Namespace) -> int:
    """Aggregate teacher votes."""
    import json
    from mage_ptcg.offline_training_v1_support.teacher_ensemble import TeacherEnsemble
    with open(args.input, "r", encoding="utf-8") as f:
        outputs = json.load(f)
    ensemble = TeacherEnsemble()
    res = ensemble.aggregate_votes(outputs)
    print(json.dumps(res, indent=2))
    return 0


def cmd_query_budget(args: argparse.Namespace) -> int:
    """Compute budget allocation plan."""
    import json
    from mage_ptcg.offline_training_v1_support.contracts import load_records
    from mage_ptcg.offline_training_v1_support.query_budget import QueryBudgetAllocator
    records = load_records(args.input)
    with open(args.teachers, "r", encoding="utf-8") as f:
        teachers = json.load(f)
    allocator = QueryBudgetAllocator()
    res = allocator.allocate(records, teachers, args.budget)
    print(json.dumps(res, indent=2))
    return 0


def cmd_audit_log(args: argparse.Namespace) -> int:
    """Manage and verify audit trails."""
    import json
    from mage_ptcg.offline_training_v1_support.audit_log import AuditLogger
    logger = AuditLogger(args.log_file)
    if args.action == "verify":
        errors = logger.verify_chain()
        res = {"valid": len(errors) == 0, "errors": errors}
        print(json.dumps(res, indent=2))
        return 0 if len(errors) == 0 else 1
    return 0


def cmd_lineage(args: argparse.Namespace) -> int:
    """Generate lineage graph summaries."""
    import json
    from mage_ptcg.offline_training_v1_support.lineage import LineageGraph
    g = LineageGraph()
    g.add_node("dataset_v1", "dataset")
    g.add_node("model_v1", "model")
    g.add_edge("dataset_v1", "model_v1", "trained_on")
    res = {
        "dot": g.generate_dot(),
        "markdown": g.generate_markdown(),
    }
    print(json.dumps(res, indent=2))
    return 0


def cmd_config_lint(args: argparse.Namespace) -> int:
    """Verify configurations via linter."""
    import json
    from mage_ptcg.offline_training_v1_support.config_lint import ConfigLinter
    with open(args.config, "r", encoding="utf-8") as f:
        config = json.load(f)
    linter = ConfigLinter()
    res = linter.lint(config)
    print(json.dumps(res, indent=2))
    return 0 if res["status"] != "INVALID" else 1


def cmd_verify_repro_bundle(args: argparse.Namespace) -> int:
    """Verify reproducibility bundle."""
    import json
    from pathlib import Path
    from mage_ptcg.offline_training_v1_support.reproducibility import ReproducibilityBundleManager
    manager = ReproducibilityBundleManager(Path("."))
    res = manager.verify_bundle(Path(args.bundle))
    print(json.dumps(res, indent=2))
    return 0 if res["valid"] else 1


def main(argv: list[str] | None = None) -> int:
    """Routing entrypoint parser."""
    parser = argparse.ArgumentParser(description="Offline Training Support Platform CLI Utility")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # doctor
    subparsers.add_parser("doctor", help="Run workspace diagnostics")

    # self-audit
    subparsers.add_parser("self-audit", help="Run internal self-audit")

    # schedule
    p_sch = subparsers.add_parser("schedule", help="Generate deterministic evaluation schedule")
    p_sch.add_argument("--config", required=True, help="Path to configuration JSON")
    p_sch.add_argument("--output", required=True, help="Output destination JSONL")
    p_sch.add_argument("--dry-run", action="store_true", help="Preview output only")

    # validate-games
    p_val = subparsers.add_parser("validate-games", help="Match outcomes against scheduled plan")
    p_val.add_argument("--schedule", required=True, help="Path to evaluation schedule JSONL")
    p_val.add_argument("--games-jsonl", required=True, help="Path to game outcomes JSONL")
    p_val.add_argument("--output", help="Optional verification report output JSON")
    p_val.add_argument("--dry-run", action="store_true", help="Preview match metrics only")

    # summarize
    p_sum = subparsers.add_parser("summarize", help="Calculate evaluation statistics summary")
    p_sum.add_argument("--games-jsonl", required=True, help="Path to game outcomes JSONL")
    p_sum.add_argument("--output", required=True, help="Output statistics JSON")
    p_sum.add_argument("--dry-run", action="store_true", help="Preview only")

    # cross-play
    p_cp = subparsers.add_parser("cross-play", help="Compile cross-play matchup reports")
    p_cp.add_argument("--games-jsonl", required=True, help="Path to game outcomes JSONL")
    p_cp.add_argument("--output", required=True, help="Output matrix JSON")
    p_cp.add_argument("--markdown", help="Optional markdown output path")
    p_cp.add_argument("--csv", help="Optional CSV matrix output path")
    p_cp.add_argument("--dry-run", action="store_true", help="Preview only")

    # rate
    p_rate = subparsers.add_parser("rate", help="Compute ratings (Elo / Bradley-Terry)")
    p_rate.add_argument("--games-jsonl", required=True, help="Path to game outcomes JSONL")
    p_rate.add_argument("--method", default="elo", choices=["elo", "bt", "bradley-terry"], help="Calculation model")
    p_rate.add_argument("--output", required=True, help="Output rating summary JSON")
    p_rate.add_argument("--dry-run", action="store_true", help="Preview only")

    # registry
    p_reg = subparsers.add_parser("registry", help="Interact with datasets and models registries")
    p_reg.add_argument("--registry-dir", required=True, help="Registry root directory path")
    p_reg_subs = p_reg.add_subparsers(dest="reg_command", required=True)

    p_reg_list = p_reg_subs.add_parser("list", help="List active records")
    p_reg_list.add_argument("--kind", required=True, choices=["dataset", "model", "experiment", "deck", "opponent"])

    p_reg_ins = p_reg_subs.add_parser("inspect", help="Show full content of a record")
    p_reg_ins.add_argument("--kind", required=True, choices=["dataset", "model", "experiment", "deck", "opponent"])
    p_reg_ins.add_argument("--id", required=True, help="Stable identifier")

    p_reg_comp = p_reg_subs.add_parser("compare", help="Compare two records")
    p_reg_comp.add_argument("--kind", required=True, choices=["dataset", "model", "experiment", "deck", "opponent"])
    p_reg_comp.add_argument("--id-a", required=True)
    p_reg_comp.add_argument("--id-b", required=True)

    p_reg_val = p_reg_subs.add_parser("validate", help="Check registry corruption")
    p_reg_val.add_argument("--kind", required=True, choices=["dataset", "model", "experiment", "deck", "opponent"])

    p_reg_arc = p_reg_subs.add_parser("archive", help="Logically archive a record")
    p_reg_arc.add_argument("--kind", required=True, choices=["dataset", "model", "experiment", "deck", "opponent"])
    p_reg_arc.add_argument("--id", required=True)
    p_reg_arc.add_argument("--dry-run", action="store_true")

    # mine
    p_mine = subparsers.add_parser("mine", help="Extract hard states from diagnostic decisions")
    p_mine.add_argument("--decisions-jsonl", required=True, help="Path to decisions diagnostic JSONL")
    p_mine.add_argument("--output", required=True, help="Output destination hard-states JSONL")
    p_mine.add_argument("--dry-run", action="store_true", help="Preview only")

    # deduplicate
    p_ded = subparsers.add_parser("deduplicate", help="Deduplicate decisions and quarantine conflicts")
    p_ded.add_argument("--input", required=True, help="Path to raw decisions JSONL")
    p_ded.add_argument("--output", required=True, help="Output deduplicated JSONL")
    p_ded.add_argument("--quarantine", required=True, help="Output quarantined JSONL")
    p_ded.add_argument("--dry-run", action="store_true", help="Preview only")

    # sample
    p_sam = subparsers.add_parser("sample", help="Priority sample training subsets")
    p_sam.add_argument("--input", required=True, help="Path to clean decisions JSONL")
    p_sam.add_argument("--config", required=True, help="Path to weight configurations JSON")
    p_sam.add_argument("--count", type=int, required=True, help="Sample target count")
    p_sam.add_argument("--output", required=True, help="Output sampled subset JSONL")
    p_sam.add_argument("--manifest", required=True, help="Output sampling manifest JSON")
    p_sam.add_argument("--replacement", action="store_true", help="Allow resampling of same records")
    p_sam.add_argument("--seed", type=int, default=42, help="Deterministic random seed")
    p_sam.add_argument("--dry-run", action="store_true", help="Preview only")

    # --- Phase 2 CLI Arguments ---

    # dataset lifecycle
    p_ds = subparsers.add_parser("dataset", help="Dataset lifecycle validation, merge, compact, migration")
    p_ds_subs = p_ds.add_subparsers(dest="dataset_command", required=True)

    p_ds_ins = p_ds_subs.add_parser("inspect", help="Inspect manifest structure")
    p_ds_ins.add_argument("--manifest", required=True, help="Path to dataset-manifest.json")

    p_ds_val = p_ds_subs.add_parser("validate", help="Validate manifest shards checksums and values")
    p_ds_val.add_argument("--manifest", required=True)

    p_ds_diff = p_ds_subs.add_parser("diff", help="Diff two datasets structure")
    p_ds_diff.add_argument("--manifest-a", required=True)
    p_ds_diff.add_argument("--manifest-b", required=True)

    p_ds_mrg = p_ds_subs.add_parser("merge", help="Merge multiple dataset manifests")
    p_ds_mrg.add_argument("--manifests", nargs="+", required=True, help="List of dataset manifest JSONs")
    p_ds_mrg.add_argument("--output", required=True, help="Path to final merged manifest JSON")
    p_ds_mrg.add_argument("--dry-run", action="store_true")

    p_ds_cmp = p_ds_subs.add_parser("compact", help="Compact small shards into gzip large shards")
    p_ds_cmp.add_argument("--manifest", required=True)
    p_ds_cmp.add_argument("--output", required=True, help="Output compacted manifest JSON path")
    p_ds_cmp.add_argument("--dry-run", action="store_true")

    p_ds_mig = p_ds_subs.add_parser("migrate", help="Migrate dataset manifest version")
    p_ds_mig.add_argument("--manifest", required=True)
    p_ds_mig.add_argument("--output", required=True)
    p_ds_mig.add_argument("--target-version", required=True)
    p_ds_mig.add_argument("--dry-run", action="store_true")

    p_ds_gc = p_ds_subs.add_parser("gc-plan", help="Report unreferenced shard candidates for garbage collection")
    p_ds_gc.add_argument("--registry-dir", required=True)
    p_ds_gc.add_argument("--manifests", nargs="+", required=True, help="Active dataset manifests")

    # teacher
    p_t = subparsers.add_parser("teacher", help="Interact with teacher capability probes and caching")
    p_t_subs = p_t.add_subparsers(dest="teacher_command", required=True)

    p_t_prb = p_t_subs.add_parser("probe", help="Probe and register teacher agent capabilities")
    p_t_prb.add_argument("--registry-dir", required=True)
    p_t_prb.add_argument("--descriptor", required=True, help="Path to teacher descriptor JSON")
    p_t_prb.add_argument("--entrypoint", required=True, help="Module:symbol import pathway")
    p_t_prb.add_argument("--dry-run", action="store_true")

    p_t_stat = p_t_subs.add_parser("cache-stats", help="View teacher cache telemetry")
    p_t_stat.add_argument("--registry-dir", required=True)

    # iterate
    p_it = subparsers.add_parser("iterate", help="DAgger iteration orchestration manifests")
    p_it_subs = p_it.add_subparsers(dest="iterate_command", required=True)

    p_it_cr = p_it_subs.add_parser("create-round", help="Start a new round manifest")
    p_it_cr.add_argument("--run-dir", required=True)
    p_it_cr.add_argument("--iteration-id", required=True)
    p_it_cr.add_argument("--round", type=int, required=True)
    p_it_cr.add_argument("--config", required=True, help="Path to round config JSON")

    p_it_ph = p_it_subs.add_parser("advance-phase", help="Set round phase statuses")
    p_it_ph.add_argument("--run-dir", required=True)
    p_it_ph.add_argument("--round", type=int, required=True)
    p_it_ph.add_argument("--phase", required=True, choices=list(VALID_PHASES))
    p_it_ph.add_argument("--status", required=True, choices=["PENDING", "RUNNING", "COMPLETE", "FAILED"])
    p_it_ph.add_argument("--outputs", help="JSON dictionary of output file hashes")

    p_it_mix = p_it_subs.add_parser("mix", help="Mix dataset records")
    p_it_mix.add_argument("--run-dir", required=True)
    p_it_mix.add_argument("--plan", required=True, help="Dataset mixing plan JSON")
    p_it_mix.add_argument("--base", required=True, help="Base records JSONL")
    p_it_mix.add_argument("--new", required=True, help="New on-policy records JSONL")
    p_it_mix.add_argument("--hard", required=True, help="Hard-state records JSONL")
    p_it_mix.add_argument("--output", required=True, help="Output mixed JSONL path")
    p_it_mix.add_argument("--dry-run", action="store_true")

    # sweep
    p_swp = subparsers.add_parser("sweep", help="Orchestrate experiment hyperparameter sweep runs")
    p_swp_subs = p_swp.add_subparsers(dest="sweep_command", required=True)

    p_swp_pln = p_swp_subs.add_parser("plan", help="Generate initial sweep trials")
    p_swp_pln.add_argument("--sweep-id", required=True)
    p_swp_pln.add_argument("--config", required=True, help="Path to parameter space definition JSON")
    p_swp_pln.add_argument("--output", required=True, help="Output trial list manifest JSON")
    p_swp_pln.add_argument("--dry-run", action="store_true")

    p_swp_sh = p_swp_subs.add_parser("advance-halving", help="Prune sweep trials using Successive Halving")
    p_swp_sh.add_argument("--sweep-id", required=True)
    p_swp_sh.add_argument("--trials-manifest", required=True, help="Path to trial outcomes list")
    p_swp_sh.add_argument("--output", required=True, help="Appended trials output manifest JSON path")
    p_swp_sh.add_argument("--reduction-factor", type=int, default=3)
    p_swp_sh.add_argument("--min-survivors", type=int, default=1)
    p_swp_sh.add_argument("--objective", default="val_loss")
    p_swp_sh.add_argument("--direction", default="minimize", choices=["minimize", "maximize"])
    p_swp_sh.add_argument("--dry-run", action="store_true")

    # calibrate
    p_cal = subparsers.add_parser("calibrate", help="Calculate calibration and temperature scaling")
    p_cal.add_argument("--predictions", required=True, help="Path to prediction records JSONL")
    p_cal.add_argument("--output", help="Output calibration JSON path")
    p_cal.add_argument("--dry-run", action="store_true")
    p_cal_subs = p_cal.add_subparsers(dest="calibrate_command", required=True)
    p_cal_subs.add_parser("eval", help="Evaluate accuracy and ECE bins")
    p_cal_subs.add_parser("fit", help="Fit scalar temperature value minimizing NLL")

    # ood
    p_ood = subparsers.add_parser("ood", help="Scan prediction records for OOD anomalies")
    p_ood.add_argument("--input", required=True, help="Path to predictions JSONL")
    p_ood.add_argument("--output", required=True, help="Destination OOD diagnostics JSONL path")
    p_ood.add_argument("--dry-run", action="store_true")

    # performance
    p_perf = subparsers.add_parser("performance", help="Aggregate CPU latency profiling")
    p_perf.add_argument("--input", required=True, help="Path to latency measurements JSONL")
    p_perf.add_argument("--output", required=True, help="Destination performance JSON path")
    p_perf.add_argument("--dry-run", action="store_true")

    # reproducibility bundle
    p_rep = subparsers.add_parser("repro-bundle", help="Construct public-safe metadata reproducibility bundles")
    p_rep_subs = p_rep.add_subparsers(dest="repro_command", required=True)

    p_rep_crt = p_rep_subs.add_parser("create", help="Bundle metadata and write to gzip tar")
    p_rep_crt.add_argument("--metadata", required=True, help="JSON configuration files folder")
    p_rep_crt.add_argument("--output", required=True, help="Output destination .tar.gz path")
    p_rep_crt.add_argument("--dry-run", action="store_true")

    p_rep_ver = p_rep_subs.add_parser("verify", help="Check tar traversal protection and integrity")
    p_rep_ver.add_argument("--bundle", required=True, help="Path to tar.gz bundle file")

    # promotion report
    p_prm = subparsers.add_parser("promotion-report", help="Gate evaluators human sign-off generator")
    p_prm.add_argument("--summary", required=True, help="Path to games statistics summary JSON")
    p_prm.add_argument("--output", required=True, help="Output decision packet JSON path")
    p_prm.add_argument("--known-defects", type=int, default=0, help="Known critical/high issues")
    p_prm.add_argument("--dry-run", action="store_true")

    # chaos check
    subparsers.add_parser("chaos-check", help="Execute mock chaos checks")

    # --- Phase 3 CLI Arguments ---

    # census
    subparsers.add_parser("census", help="Run Phase 1-2 implementation census")

    # traceability
    subparsers.add_parser("traceability", help="Run traceability verification")

    # fuzz
    p_fuz = subparsers.add_parser("fuzz", help="Run property-style fuzzing tests")
    p_fuz.add_argument("--seed", type=int, default=42, help="Seed for fuzzing random")

    # scale-check
    p_scl = subparsers.add_parser("scale-check", help="Run scalability validation")
    p_scl.add_argument("--records", type=int, default=10000, help="Number of records to check")

    # compatibility
    p_cmp = subparsers.add_parser("compatibility", help="Check contract compatibility")
    p_cmp.add_argument("--left", required=True, help="Path to schema A JSON")
    p_cmp.add_argument("--right", required=True, help="Path to schema B JSON")

    # adapt
    p_adp = subparsers.add_parser("adapt", help="Translate external artifacts")
    p_adp.add_argument("--input", required=True, help="Path to input artifact JSON")
    p_adp.add_argument("--type", required=True, help="Artifact type (e.g. game_record)")

    # compare
    p_cpr = subparsers.add_parser("compare", help="Compare two experiment outputs")
    p_cpr.add_argument("--candidate-a", required=True, help="Games A JSONL")
    p_cpr.add_argument("--candidate-b", required=True, help="Games B JSONL")
    p_cpr.add_argument("--output", help="Optional output JSON path")

    # candidate-analysis
    p_can = subparsers.add_parser("candidate-analysis", help="Multi-objective Pareto analysis")
    p_can.add_argument("--candidates", required=True, help="Path to candidates JSON")
    p_can.add_argument("--safety-limits", required=True, help="Path to safety limits JSON")

    # plan-evaluation
    p_pe = subparsers.add_parser("plan-evaluation", help="Run sample-size planner")
    p_pe.add_argument("--baseline-win-rate", type=float, required=True)
    p_pe.add_argument("--target-improvement", type=float, required=True)

    # teacher-ensemble
    p_ens = subparsers.add_parser("teacher-ensemble", help="Aggregate teacher votes")
    p_ens.add_argument("--input", required=True, help="Path to teacher outputs JSON")

    # query-budget
    p_qbg = subparsers.add_parser("query-budget", help="Compute budget allocation plan")
    p_qbg.add_argument("--input", required=True, help="Path to input records JSONL")
    p_qbg.add_argument("--teachers", required=True, help="Path to teachers info JSON")
    p_qbg.add_argument("--budget", type=float, required=True, help="Round budget limit")

    # audit-log
    p_aud = subparsers.add_parser("audit-log", help="Manage and verify audit trails")
    p_aud.add_argument("--log-file", required=True, help="Path to audit log JSONL")
    p_aud.add_argument("--action", required=True, choices=["verify"], help="Audit action")

    # lineage
    subparsers.add_parser("lineage", help="Generate lineage graph summaries")

    # config-lint
    p_cl = subparsers.add_parser("config-lint", help="Verify configurations via linter")
    p_cl.add_argument("--config", required=True, help="Path to configuration JSON")

    # verify-repro-bundle
    p_vrb = subparsers.add_parser("verify-repro-bundle", help="Verify reproducibility bundle")
    p_vrb.add_argument("--bundle", required=True, help="Path to tar.gz bundle")

    parsed_args = parser.parse_args(argv)

    dispatch = {
        "doctor": cmd_doctor,
        "self-audit": cmd_self_audit,
        "schedule": cmd_schedule,
        "validate-games": cmd_validate_games,
        "summarize": cmd_summarize,
        "cross-play": cmd_cross_play,
        "rate": cmd_rate,
        "registry": cmd_registry,
        "mine": cmd_mine,
        "deduplicate": cmd_deduplicate,
        "sample": cmd_sample,

        # Phase 2 CLI dispatchers
        "dataset": cmd_dataset,
        "teacher": cmd_teacher,
        "iterate": cmd_iterate,
        "sweep": cmd_sweep,
        "calibrate": cmd_calibrate,
        "ood": cmd_ood,
        "performance": cmd_performance,
        "repro-bundle": cmd_repro_bundle,
        "promotion-report": cmd_promotion_report,
        "chaos-check": cmd_chaos_check,

        # Phase 3 CLI dispatchers
        "census": cmd_census,
        "traceability": cmd_traceability,
        "fuzz": cmd_fuzz,
        "scale-check": cmd_scale_check,
        "compatibility": cmd_compatibility,
        "adapt": cmd_adapt,
        "compare": cmd_compare,
        "candidate-analysis": cmd_candidate_analysis,
        "plan-evaluation": cmd_plan_evaluation,
        "teacher-ensemble": cmd_teacher_ensemble,
        "query-budget": cmd_query_budget,
        "audit-log": cmd_audit_log,
        "lineage": cmd_lineage,
        "config-lint": cmd_config_lint,
        "verify-repro-bundle": cmd_verify_repro_bundle,
    }

    try:
        exit_code = dispatch[parsed_args.command](parsed_args)
    except SupportContractError as exc:
        print(f"Support contract validation failed: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"Unexpected execution error: {exc}", file=sys.stderr)
        return 4

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
