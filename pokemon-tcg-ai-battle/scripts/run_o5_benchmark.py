"""Run the O5 versioned Benchmark through the official cabt loader only.

Builds a ``VersionedBenchmarkManifest`` from the current, honestly-reported
O5 population state (0 active archetypes unless overridden with real
registry counts), then executes every populated member as an opponent of a
fixed candidate agent via the resumable Evaluation Runner. ``current_meta``
and archetype-``adversarial`` stay at 0 games / BENCHMARK_BLOCKED... until a
human-reviewed Rules attestation and Team permission manifests unblock the
Deck Archetype Registry population -- this script never pads that number.

``--benchmark-set`` is required and always exactly ``performance`` or
``safety``: a fault-injection opponent (``exception_agent`` etc.) can never
end up inside the same manifest/report as a performance win rate.

``--candidate-agent-id`` is always required -- there is no default, so a
run can never silently fall back to Rule Agent v0. A hash-pinned artifact
candidate (currently only ``neural_actual_trained``) additionally requires
``--candidate-model-path`` pointing at its real export JSON; the path is
never hardcoded in source, only its expected hash is.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time as _time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import Deck, read_deck_csv  # noqa: E402
from mage_ptcg.competition_intelligence.o5_activation import O5_ACTIVATION_SCHEMA_VERSION  # noqa: E402
from mage_ptcg.competition_intelligence.o5_benchmark import build_versioned_benchmark_manifest  # noqa: E402
from mage_ptcg.competition_intelligence.o5_candidate_factory import build_neural_candidate  # noqa: E402
from mage_ptcg.competition_intelligence.o5_candidate_registry import (  # noqa: E402
    CANDIDATE_ARTIFACT_REGISTRY,
    resolve_candidate_identity,
)
from mage_ptcg.competition_intelligence.o5_evaluation import KNOWN_AGENT_FACTORIES, run_o5_benchmark  # noqa: E402
from mage_ptcg.competition_intelligence.o5_registry import O5_REGISTRY_SCHEMA_VERSION  # noqa: E402
from mage_ptcg.distillation.contracts import atomic_write_json  # noqa: E402
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256  # noqa: E402
from scripts.cabt_capability import diagnose_cabt_capability  # noqa: E402
from scripts.test_sim import run_match  # noqa: E402


class O5BenchmarkCliError(RuntimeError):
    """Raised for a CLI-level failure before any Benchmark artifact is written."""


def _git_head() -> str:
    completed = subprocess.run(
        ("git", "-C", str(REPOSITORY_ROOT), "rev-parse", "HEAD"), capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _is_known_agent_id(agent_id: str) -> bool:
    return agent_id in KNOWN_AGENT_FACTORIES or agent_id in CANDIDATE_ARTIFACT_REGISTRY


def _normalize_seat_status(value: object) -> str:
    if not isinstance(value, str):
        return "NOT_OBSERVABLE"
    text = value.upper()
    return text if text in {"DONE", "INVALID", "ERROR", "TIMEOUT"} else "UNKNOWN"


def _resolve_agent_builder(agent_id: str, *, candidate_model_path: Path | None):
    """Return a ``(deck, seed) -> Agent`` builder for any known agent id."""
    if agent_id in CANDIDATE_ARTIFACT_REGISTRY:
        identity = resolve_candidate_identity(agent_id)

        def build(deck: Deck, _seed: int):
            return build_neural_candidate(identity, model_path=candidate_model_path, deck=deck)

        return build
    return KNOWN_AGENT_FACTORIES[agent_id]


def _make_run_match_factory(*, deck_path: Path, max_steps: int, transient_root: Path, candidate_model_path: Path | None):
    def factory(champion_id: str, challenger_id: str):
        champion_builder = _resolve_agent_builder(champion_id, candidate_model_path=candidate_model_path)
        challenger_builder = _resolve_agent_builder(challenger_id, candidate_model_path=candidate_model_path)

        def play(schedule: dict[str, object]) -> dict[str, object]:
            champion_seat = int(schedule["champion_player_index"])
            captured: dict[str, object] = {}

            def wrap(builder, key: str):
                def built(deck, seed):
                    agent = builder(deck, seed)
                    captured[key] = agent
                    return agent

                return built

            if champion_seat == 0:
                agent_a_name, agent_b_name = champion_id, challenger_id
                agent_a_factory, agent_b_factory = wrap(champion_builder, "champion"), wrap(challenger_builder, "challenger")
            else:
                agent_a_name, agent_b_name = challenger_id, champion_id
                agent_a_factory, agent_b_factory = wrap(challenger_builder, "challenger"), wrap(champion_builder, "champion")

            raw = run_match(
                deck_a_path=deck_path,
                deck_b_path=deck_path,
                agent_a_name=agent_a_name,
                agent_b_name=agent_b_name,
                agent_a_factory=agent_a_factory,
                agent_b_factory=agent_b_factory,
                seed=int(schedule["seed"]),
                max_steps=max_steps,
                output_dir=transient_root,
                save_html=False,
                save_result=False,
            )
            winner = raw.get("winner")
            if raw.get("status") == "DONE":
                winner_agent = "draw" if winner == 2 else ("champion" if winner == champion_seat else "challenger")
            else:
                winner_agent = None

            agent_status = raw.get("agent_status")
            if isinstance(agent_status, list) and len(agent_status) == 2:
                champion_raw_status, challenger_raw_status = agent_status[champion_seat], agent_status[1 - champion_seat]
            else:
                champion_raw_status = challenger_raw_status = None

            def fallback_count(key: str) -> int | None:
                value = getattr(captured.get(key), "fallback_count", None)
                return value if isinstance(value, int) and not isinstance(value, bool) else None

            return {
                "status": raw.get("status"),
                "winner_agent": winner_agent,
                "elapsed_seconds": raw.get("elapsed_seconds"),
                "fallback_count": 0,
                "steps": raw.get("steps"),
                "champion_status": _normalize_seat_status(champion_raw_status),
                "challenger_status": _normalize_seat_status(challenger_raw_status),
                "champion_fallback_count": fallback_count("champion"),
                "challenger_fallback_count": fallback_count("challenger"),
            }

        return play

    return factory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark-id", required=True)
    parser.add_argument("--benchmark-version", required=True)
    parser.add_argument("--benchmark-set", required=True, choices=("performance", "safety"))
    parser.add_argument("--candidate-agent-id", required=True)
    parser.add_argument("--candidate-model-path", type=Path, default=None)
    parser.add_argument("--baseline-artifact-ids", nargs="+", default=["random_legal"])
    parser.add_argument("--deck", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--games-per-member", type=int, default=8)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--time-budget-seconds", type=float, default=3600.0)
    parser.add_argument("--active-exact-decks", type=int, default=0)
    parser.add_argument("--runnable-families", type=int, default=0)
    parser.add_argument("--verified-links", type=int, default=0)
    parser.add_argument("--created-at", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if not _is_known_agent_id(args.candidate_agent_id):
        print(f"unknown --candidate-agent-id: {args.candidate_agent_id!r}", file=sys.stderr)
        return 3
    if args.candidate_agent_id in CANDIDATE_ARTIFACT_REGISTRY:
        candidate_artifact_hash = resolve_candidate_identity(args.candidate_agent_id).model_hash
        if args.candidate_model_path is None:
            print(
                f"--candidate-model-path is required for hash-pinned candidate {args.candidate_agent_id!r}",
                file=sys.stderr,
            )
            return 3
    else:
        candidate_artifact_hash = "NOT_APPLICABLE"

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    created_at = args.created_at or _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    deck = read_deck_csv(args.deck)

    # Capability is always probed, dry-run or not, so a dry-run manifest's
    # cabt_version has the same meaning as a real run's -- a previous
    # revision left cabt_version at a placeholder in dry-run mode, which
    # meant the same CLI args produced a *different* manifest_hash purely
    # because of --dry-run, which is exactly the ambiguity this project's
    # own audit flagged.
    capability_report = diagnose_cabt_capability()
    if not args.dry_run and capability_report.get("status") != "READY":
        print(f"cabt capability unavailable: {capability_report.get('reason_code', 'UNKNOWN')}", file=sys.stderr)
        return 4
    cabt_version = str(capability_report.get("kaggle_environments_version", "unknown"))

    manifest = build_versioned_benchmark_manifest(
        (),
        benchmark_id=args.benchmark_id,
        benchmark_version=args.benchmark_version,
        benchmark_kind=args.benchmark_set,
        created_at=created_at,
        source_snapshot_ids=(),
        deck_registry_version=O5_REGISTRY_SCHEMA_VERSION,
        policy_pack_version=O5_ACTIVATION_SCHEMA_VERSION,
        agent_family_versions={name: "1" for name in (*KNOWN_AGENT_FACTORIES, *CANDIDATE_ARTIFACT_REGISTRY)},
        ruleset_version="unknown",
        cabt_version=cabt_version,
        seed_set=tuple(args.seeds),
        seat_swap_policy="ALWAYS_SWAP",
        game_count=args.games_per_member,
        time_budget_seconds=args.time_budget_seconds,
        candidate_artifact_id=args.candidate_agent_id,
        candidate_artifact_hash=candidate_artifact_hash,
        baseline_artifact_ids=tuple(args.baseline_artifact_ids),
        environment="local",
        commit=_git_head(),
        active_exact_decks=args.active_exact_decks,
        runnable_families=args.runnable_families,
        verified_links=args.verified_links,
    )
    # The filename includes manifest_hash (not just benchmark_kind/candidate)
    # so that two runs with the same --benchmark-set/--candidate-agent-id but
    # a different benchmark_id, version, game_count, or seed_set pointed at
    # the same --output-dir can never silently overwrite each other's
    # manifest file -- an independent audit reproduced exactly this
    # collision against the previous (kind+candidate only) filename.
    manifest_slug = f"versioned_benchmark_manifest__{manifest.benchmark_kind}__{args.candidate_agent_id}__{manifest.manifest_hash[:16]}.json"
    manifest_path = output_dir / manifest_slug
    atomic_write_json(manifest_path, manifest.as_dict())

    if args.dry_run:
        print(json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    run_match_factory = _make_run_match_factory(
        deck_path=args.deck,
        max_steps=args.max_steps,
        transient_root=output_dir / ".cabt-benchmark-transient",
        candidate_model_path=args.candidate_model_path,
    )
    report = run_o5_benchmark(
        manifest,
        candidate_agent_id=args.candidate_agent_id,
        deck_fingerprint=canonical_deck_sha256(deck),
        output_dir=output_dir,
        run_match_factory=run_match_factory,
    )
    report_path = output_dir / f"o5_benchmark_report__{manifest.benchmark_kind}__{args.candidate_agent_id}__{manifest.manifest_hash[:16]}.json"
    atomic_write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    # Non-zero invalid/crash/timeout counts are the *expected* signal from
    # the safety opponent family (they deliberately misbehave), so a
    # nonzero count here is not itself a CLI failure; only an unhandled
    # exception above is.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
