"""Run the real CABT qualification probe over the 15 fixed-lane seed decks.

For each of the 15 candidates registered in
``configs/meta_specialist/seed_candidates_v1.json`` this script:

1. Acquires the candidate's exact provider bytes via
   :func:`mage_ptcg.meta_specialist.seed_registry.acquire_seed_candidate_blob`,
   fetching the exact Git blob with ``git cat-file blob <commit>:<path>``
   against the main repository checkout.  A candidate whose materialization
   status or permission status is not eligible for Git-blob acquisition is
   refused here -- by the existing, already-committed registry code, not by
   anything added in this script -- and is recorded as ``not_run``.
2. Materializes the acquired bytes and builds a ``DeckAssetInput`` via
   :func:`materialize_seed_candidate` / :func:`build_deck_asset_input`.
3. Runs exactly one real CABT game via
   :func:`mage_ptcg.meta_specialist.cabt_legality_v1.probe_deck_legality_v1`
   and feeds its verdict into
   :func:`mage_ptcg.meta_specialist.decks.qualify_deck_asset`.

No step here ever fabricates a ``qualified`` verdict: that outcome can only
come from ``qualify_deck_asset`` actually returning a ``QualifiedDeckAsset``,
which itself only happens after a real completed, faultless CABT game.  Every
non-qualified candidate is recorded with the literal exception message that
explains why, straight from the real code path that raised it.

The per-candidate results are published as one canonical, self-verifying
report artifact under ``runs/meta-specialist-seed-qualification/`` (see
``seed_qualification_report_v1.py``).  This script performs no Kaggle
network calls, no submission, and touches nothing outside that directory
plus stdout.
"""

from __future__ import annotations

import argparse
import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from mage_ptcg.meta_specialist.cabt_legality_v1 import (  # noqa: E402
    DEFAULT_PROBE_SEED_V1,
    CabtProbeOutcomeV1,
    probe_deck_legality_v1,
)
from mage_ptcg.meta_specialist.decks import (  # noqa: E402
    ArchetypeRegistryError,
    DeckQualificationError,
    load_archetype_registry,
    qualify_deck_asset,
)
from mage_ptcg.meta_specialist.seed_qualification_report_v1 import (  # noqa: E402
    atomic_write_seed_qualification_report_v1,
    build_seed_qualification_report_v1,
)
from mage_ptcg.meta_specialist.seed_registry import (  # noqa: E402
    SeedCandidate,
    SeedRegistryError,
    acquire_seed_candidate_blob,
    build_deck_asset_input,
    load_seed_candidate_registry,
    materialize_seed_candidate,
    read_en_card_vocabulary,
)


DEFAULT_ARCHETYPES_PATH = ROOT / "configs/meta_specialist/archetypes_v1.json"
DEFAULT_SEED_CANDIDATES_PATH = ROOT / "configs/meta_specialist/seed_candidates_v1.json"
# Card DB is not checked into this worktree's data/ (see .gitignore); fall back
# to the main repository checkout, matching tests/meta_specialist/test_seed_registry.py.
_EN_CARD_DATABASE_CANDIDATES = (
    ROOT / "data/raw/EN_Card_Data.csv",
    ROOT.parent.parent / "pokemon-tcg-ai-battle/data/raw/EN_Card_Data.csv",
)
DEFAULT_MAIN_REPO_ROOT = Path("/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle")
DEFAULT_OUTPUT_DIR = ROOT / "runs/meta-specialist-seed-qualification"
PROBE_MAX_STEPS = 2000


class QualificationRunnerError(RuntimeError):
    """Raised when a required prerequisite (config, card DB, git) is missing."""


def _resolve_card_database(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise QualificationRunnerError(f"--card-database does not exist: {path}")
        return path
    for candidate in _EN_CARD_DATABASE_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise QualificationRunnerError(
        "could not locate EN_Card_Data.csv in the worktree or the main repository "
        f"(checked {', '.join(str(p) for p in _EN_CARD_DATABASE_CANDIDATES)})"
    )


def _make_git_blob_byte_provider(main_repo_root: Path):
    def byte_provider(source_ref: str, source_commit: str, source_path: str) -> bytes:
        del source_ref  # git addresses the blob by commit:path; ref is provenance-only.
        completed = subprocess.run(
            ["git", "-C", str(main_repo_root), "cat-file", "blob", f"{source_commit}:{source_path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return completed.stdout

    return byte_provider


def _empty_outcome_fields() -> dict[str, object]:
    return {"cabt_probe_status": None, "cabt_probe_evidence": None, "qualified_asset_id": None}


def _qualify_one(
    candidate: SeedCandidate,
    *,
    archetype,
    known_card_ids,
    byte_provider,
    materialized_dir: Path,
    card_database_version: str,
    probe_seed: int,
    probe_max_steps: int,
) -> dict[str, object]:
    base: dict[str, object] = {
        "runtime_id": candidate.runtime_id,
        "priority": candidate.priority,
        "deck_identity": candidate.deck_identity,
        "asset_class": candidate.asset_class,
        "materialization_status": candidate.materialization_status,
    }

    # Step 1: acquire exact provider bytes. This is the real gate: it refuses
    # meta-JSONL rows outright, and it refuses any materialized_git_blob
    # candidate that is not permission-approved for Git-blob acquisition --
    # neither refusal is something this script decides.
    try:
        acquired = acquire_seed_candidate_blob(candidate, byte_provider=byte_provider)
    except SeedRegistryError as exc:
        return {**base, "outcome": "not_run", "reason": str(exc), **_empty_outcome_fields()}

    # Step 2: materialize to disk and build the deck asset input.
    materialized_path = (
        materialized_dir / f"{candidate.runtime_id}-p{candidate.priority}-{candidate.deck_identity}.csv"
    )
    try:
        materialize_seed_candidate(candidate, materialized_path, acquired_blob=acquired)
        asset = build_deck_asset_input(
            candidate,
            materialized_path=materialized_path,
            card_database_version=card_database_version,
        )
    except SeedRegistryError as exc:
        return {
            **base,
            "outcome": "failed",
            "reason": f"materialization/asset-build failed: {exc}",
            **_empty_outcome_fields(),
        }

    # Step 3: run exactly one real CABT probe game. The callback captures the
    # full CabtProbeOutcomeV1 so the report always carries the true evidence,
    # even when qualify_deck_asset's own exception text is generic.
    captured: dict[str, CabtProbeOutcomeV1] = {}

    def legality(card_ids: tuple[int, ...]) -> tuple[bool, str]:
        outcome = probe_deck_legality_v1(card_ids, seed=probe_seed, max_steps=probe_max_steps)
        captured["outcome"] = outcome
        return outcome.legal, outcome.evidence

    try:
        qualified = qualify_deck_asset(
            asset, archetype, known_card_ids=known_card_ids, cabt_legality=legality,
        )
    except DeckQualificationError as exc:
        outcome = captured.get("outcome")
        return {
            **base,
            "outcome": "failed",
            "reason": str(exc),
            "cabt_probe_status": outcome.status if outcome is not None else None,
            "cabt_probe_evidence": outcome.evidence if outcome is not None else None,
            "qualified_asset_id": None,
        }

    outcome = captured["outcome"]
    return {
        **base,
        "outcome": "qualified",
        "reason": None,
        "cabt_probe_status": outcome.status,
        "cabt_probe_evidence": outcome.evidence,
        "qualified_asset_id": qualified.asset_id,
    }


def run_qualification(
    *,
    seed_candidates_path: Path,
    archetypes_path: Path,
    card_database_path: Path,
    main_repo_root: Path,
    output_dir: Path,
    probe_seed: int = DEFAULT_PROBE_SEED_V1,
    probe_max_steps: int = PROBE_MAX_STEPS,
) -> tuple[dict[str, object], Path]:
    """Run the real qualification pass and publish the report. Returns (report, path)."""
    vocabulary = read_en_card_vocabulary(card_database_path)
    try:
        archetypes = load_archetype_registry(archetypes_path)
    except ArchetypeRegistryError as exc:
        raise QualificationRunnerError(f"could not load archetype registry: {exc}") from exc
    registry = load_seed_candidate_registry(
        seed_candidates_path, archetypes=archetypes, card_vocabulary=vocabulary,
    )

    card_database_version = f"EN_Card_Data-sha256-{registry.card_database_sha256}"
    byte_provider = _make_git_blob_byte_provider(main_repo_root)
    materialized_dir = output_dir / "materialized"
    materialized_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for candidate in registry.candidates:
        archetype = archetypes.archetypes[candidate.runtime_id]
        record = _qualify_one(
            candidate,
            archetype=archetype,
            known_card_ids=vocabulary.card_ids,
            byte_provider=byte_provider,
            materialized_dir=materialized_dir,
            card_database_version=card_database_version,
            probe_seed=probe_seed,
            probe_max_steps=probe_max_steps,
        )
        records.append(record)
        outcome = record["outcome"]
        print(
            f"[{outcome.upper():9s}] {candidate.runtime_id} p{candidate.priority} "
            f"{candidate.deck_identity}"
            + ("" if outcome == "qualified" else f" -- {record['reason']}"),
            file=sys.stderr,
        )

    report = build_seed_qualification_report_v1(
        registry_content_sha256=registry.content_sha256,
        card_database_sha256=registry.card_database_sha256,
        card_vocabulary_sha256=registry.card_vocabulary_sha256,
        archetype_registry_schema_version="meta-specialist-archetypes-v1",
        cabt_probe_seed=probe_seed,
        cabt_probe_max_steps=probe_max_steps,
        generated_time_utc=datetime.datetime.now(datetime.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        candidates=records,
    )
    report_path = atomic_write_seed_qualification_report_v1(
        output_dir / "seed_qualification_report_v1.json", report,
    )
    return report, report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed-candidates", default=str(DEFAULT_SEED_CANDIDATES_PATH))
    parser.add_argument("--archetypes", default=str(DEFAULT_ARCHETYPES_PATH))
    parser.add_argument("--card-database", default=None, help="Override the EN_Card_Data.csv path.")
    parser.add_argument(
        "--main-repo-root", default=str(DEFAULT_MAIN_REPO_ROOT),
        help="Main repository checkout used as the Git-blob byte provider source.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--probe-seed", type=int, default=DEFAULT_PROBE_SEED_V1)
    parser.add_argument("--probe-max-steps", type=int, default=PROBE_MAX_STEPS)
    args = parser.parse_args(argv)

    try:
        card_database_path = _resolve_card_database(args.card_database)
        report, report_path = run_qualification(
            seed_candidates_path=Path(args.seed_candidates),
            archetypes_path=Path(args.archetypes),
            card_database_path=card_database_path,
            main_repo_root=Path(args.main_repo_root),
            output_dir=Path(args.output_dir),
            probe_seed=args.probe_seed,
            probe_max_steps=args.probe_max_steps,
        )
    except (QualificationRunnerError, SeedRegistryError, ArchetypeRegistryError) as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    print(
        f"\n{report['qualified_count']} qualified, {report['failed_count']} failed, "
        f"{report['not_run_count']} not_run out of {report['candidate_count']}",
        file=sys.stderr,
    )
    print(f"report: {report_path}", file=sys.stderr)
    print(f"content_hash: {report['content_hash']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
