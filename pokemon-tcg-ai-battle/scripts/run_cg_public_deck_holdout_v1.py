"""Research-only CABT holdout over fresh public replay decklists.

The source pool contains public replay decklists, not the original teams'
policies.  This runner therefore uses the repository's generic local pilot and
labels every result as a ``public deck holdout proxy``.  It never changes the
canonical opponent pool, CEM data, Champion, or submission package.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Iterable, Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]

from mage_ptcg.meta_specialist.cg_alternating_runtime_v1 import (  # noqa: E402
    CgAlternatingRuntimeError,
    CgPackageSpecV1,
)
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from scripts import run_root_cg_candidate_arena_v1 as arena  # noqa: E402
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    _game_from_payload,
    evaluation_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.test_sim import run_match  # noqa: E402


SCHEMA = "meta-specialist-cg-public-deck-holdout-v1"
SOURCE_SCHEMA = "r2d3-deck-opponent-pool-v1"
RUNNER_REF = f"scripts.run_cg_public_deck_holdout_v1:run_public_deck_holdout_game_v1"
DEFAULT_SOURCE_POOL = _ROOT / "data/opponent_deck_pool_20260730/opponent_deck_pool.json"
DEFAULT_CURRENT_POOL = _ROOT / "opponents"
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/cg_public_deck_holdout_v1.json"
DEFAULT_PILOT_MAIN = _ROOT / "opponents/medal_0004_01501d64/main.py"
DEFAULT_CANDIDATE_PACKAGE = _ROOT / "runs/final-sprint-autonomous/cg-p10-candidate-campaign9-g01-c11-residual-package-v1/package"
DEFAULT_PARENT_PACKAGE = _ROOT / "runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/package"
DEFAULT_INCUMBENT_PACKAGE = _ROOT / "runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package"
DEFAULT_MAX_WORKERS = 12
DEFAULT_WORKER_RECYCLE_GAMES = 16
DEFAULT_REPETITIONS = 8
SEAT_GAP_LIMIT = 0.05

# These are fixed from the 2026-07-29 public replay snapshot.  Rank-298 is
# deliberately absent because its aliases include TEAM_REMOTE_REF entries.
EXPECTED_FRESH_PUBLIC_DECK_IDS = (
    "rule-v0-deck-704270a2922dabe9",
    "rule-v0-deck-ca42a47ab1c33580",
    "rule-v0-deck-7c6399e18e86ec1f",
    "rule-v0-deck-c960f71296a4ca79",
    "rule-v0-deck-b7e42041a0618476",
    "rule-v0-deck-dbc315744a48d35b",
    "rule-v0-deck-849255b36a7691d2",
)
HOLDOUT_ARM_IDS = ("candidate_c11", "parent_p2", "incumbent_p1")
AUTHORITY_FALSE = {
    "training_allowed": False,
    "promotion_allowed": False,
    "submission_allowed": False,
    "longrun_allowed": False,
}


class PublicDeckHoldoutError(ValueError):
    """Raised when a public deck holdout cannot be proven closed and fresh."""


@dataclass(frozen=True, slots=True)
class PublicDeckEntry:
    opponent_id: str
    deck_hash: str
    deck_cards: tuple[int, ...]
    source_kind: str
    source_id: str
    rank: int
    team_name: str
    alias_source_kinds: tuple[str, ...]


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise PublicDeckHoldoutError(f"regular file required: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _semantic_sha(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _runtime_deck_sha256(deck_cards: Sequence[int]) -> str:
    """Return the byte identity emitted by the CABT opponent loader.

    Source replay rows store the order-independent canonical hash, whereas
    evaluator ledgers record the deck.csv byte hash.  Both identities must be
    checked when proving that a public decklist is genuinely unused.
    """

    deck_bytes = ("\n".join(str(card) for card in deck_cards) + "\n").encode("utf-8")
    return hashlib.sha256(deck_bytes).hexdigest()


def _require_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise PublicDeckHoldoutError(f"{label} must be an object")
    return value


def _entry_from_row(row: Mapping[str, object]) -> PublicDeckEntry:
    opponent_id = str(row.get("opponent_id", ""))
    deck_hash = str(row.get("deck_hash", ""))
    source_kind = str(row.get("source_kind", ""))
    source_id = str(row.get("source_id", ""))
    try:
        rank = int(row["rank"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PublicDeckHoldoutError(f"{opponent_id or '<unknown>'}: rank is required") from exc
    team_name = str(row.get("team_name", ""))
    cards_raw = row.get("deck_cards")
    if not isinstance(cards_raw, list) or len(cards_raw) != 60:
        raise PublicDeckHoldoutError(f"{opponent_id}: deck_cards must contain exactly 60 cards")
    try:
        cards = tuple(int(card) for card in cards_raw)
    except (TypeError, ValueError) as exc:
        raise PublicDeckHoldoutError(f"{opponent_id}: deck_cards must be integers") from exc
    if not opponent_id or len(deck_hash) != 64 or not source_id:
        raise PublicDeckHoldoutError(f"invalid public deck entry identity: {opponent_id!r}")
    aliases_raw = row.get("aliases")
    if not isinstance(aliases_raw, list) or not aliases_raw:
        raise PublicDeckHoldoutError(f"{opponent_id}: aliases are required")
    alias_source_kinds: list[str] = []
    for alias in aliases_raw:
        alias_map = _require_mapping(alias, f"{opponent_id} alias")
        alias_source_kinds.append(str(alias_map.get("source_kind", "")))
    return PublicDeckEntry(
        opponent_id=opponent_id,
        deck_hash=deck_hash,
        deck_cards=cards,
        source_kind=source_kind,
        source_id=source_id,
        rank=rank,
        team_name=team_name,
        alias_source_kinds=tuple(alias_source_kinds),
    )


def select_public_holdout_entries(
    payload: Mapping[str, object],
    *,
    current_deck_hashes: Iterable[str],
    seen_deck_hashes: Iterable[str],
    expected_ids: Sequence[str] = EXPECTED_FRESH_PUBLIC_DECK_IDS,
) -> tuple[PublicDeckEntry, ...]:
    """Select exactly the frozen public decklist-only holdout set.

    A hash that is already in the canonical pool or any prior ledger is a
    hard failure, rather than a silently smaller holdout.  This keeps a later
    rerun from accidentally changing the protocol while appearing successful.
    """

    if payload.get("schema") != SOURCE_SCHEMA:
        raise PublicDeckHoldoutError("public deck source schema mismatch")
    if payload.get("policy_binding") != "RULE_V0_DECK_ONLY":
        raise PublicDeckHoldoutError("unexpected source policy binding")
    rows = payload.get("entries")
    if not isinstance(rows, list):
        raise PublicDeckHoldoutError("public deck source has no entries")
    expected = tuple(str(item) for item in expected_ids)
    if len(expected) != len(set(expected)) or not expected:
        raise PublicDeckHoldoutError("expected public deck IDs must be unique")
    by_id: dict[str, PublicDeckEntry] = {}
    for raw in rows:
        row = _require_mapping(raw, "public deck row")
        # The snapshot also contains internal-only and non-ranked rows.  They
        # are outside this frozen public selection and must not be parsed as if
        # their optional rank field were required.
        if str(row.get("opponent_id", "")) not in expected:
            continue
        entry = _entry_from_row(row)
        if entry.opponent_id in by_id:
            raise PublicDeckHoldoutError(f"duplicate public deck id: {entry.opponent_id}")
        by_id[entry.opponent_id] = entry
    current = {str(item) for item in current_deck_hashes}
    seen = {str(item) for item in seen_deck_hashes}
    selected: list[PublicDeckEntry] = []
    for opponent_id in expected:
        entry = by_id.get(opponent_id)
        if entry is None:
            raise PublicDeckHoldoutError(f"frozen public deck is missing: {opponent_id}")
        if entry.source_kind != "KAGGLE_PUBLIC_REPLAY":
            raise PublicDeckHoldoutError(f"{opponent_id}: source is not a public replay")
        if not entry.alias_source_kinds or any(kind != "KAGGLE_PUBLIC_REPLAY" for kind in entry.alias_source_kinds):
            raise PublicDeckHoldoutError(f"{opponent_id}: aliases cross the public-only boundary")
        if entry.deck_hash in current:
            raise PublicDeckHoldoutError(f"{opponent_id}: deck hash is already in the canonical pool")
        if entry.deck_hash in seen or _runtime_deck_sha256(entry.deck_cards) in seen:
            raise PublicDeckHoldoutError(f"{opponent_id}: deck hash was already used by a prior ledger")
        selected.append(entry)
    return tuple(selected)


def _isolated_pilot_bytes(source: Path) -> bytes:
    """Make the existing generic pilot importable from an isolated pool.

    The source file remains the provenance reference.  The generated wrapper
    adds an absolute, repository-local vendor path so ``opponent_pool_v1`` can
    load a pool located below ``runs/`` without copying or mutating the main
    ``opponents`` tree.
    """

    text = source.read_text(encoding="utf-8")
    marker = "from agents.generic_agent import make_agent"
    if marker not in text:
        raise PublicDeckHoldoutError(f"pilot source is not the expected generic pilot: {source}")
    vendor_root = (_ROOT / "vendor_opponent_pilots").resolve()
    injected = (
        "import sys\n"
        f"sys.path.insert(0, {str(vendor_root)!r})\n"
        f"{marker}"
    )
    return text.replace(marker, injected, 1).encode("utf-8")


def materialize_public_holdout_pool(
    entries: Sequence[PublicDeckEntry],
    *,
    output_root: Path | str,
    pilot_main_source: Path | str = DEFAULT_PILOT_MAIN,
) -> Path:
    """Materialize an isolated, generic-pilot pool without touching ``opponents``."""

    if not entries:
        raise PublicDeckHoldoutError("cannot materialize an empty public holdout")
    root = Path(output_root).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"public holdout pool output is not empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    source = Path(pilot_main_source).resolve()
    if source.is_symlink() or not source.is_file():
        raise PublicDeckHoldoutError(f"pilot source is missing: {source}")
    pilot_bytes = _isolated_pilot_bytes(source)
    policy_sha = hashlib.sha256(pilot_bytes).hexdigest()
    manifest: list[dict[str, object]] = []
    for entry in entries:
        opponent_root = root / entry.opponent_id
        opponent_root.mkdir(parents=False, exist_ok=False)
        deck_bytes = ("\n".join(str(card) for card in entry.deck_cards) + "\n").encode("utf-8")
        # The source's canonical identity is order-independent; the engine
        # file must retain the replay's order.  Verify the canonical sorted
        # representation while recording the actual file hash in the pool
        # instance only through its bytes.
        canonical_bytes = ("\n".join(str(card) for card in sorted(entry.deck_cards)) + "\n").encode("utf-8")
        if hashlib.sha256(canonical_bytes).hexdigest() != entry.deck_hash:
            raise PublicDeckHoldoutError(f"{entry.opponent_id}: canonical deck hash does not match source cards")
        (opponent_root / "deck.csv").write_bytes(deck_bytes)
        (opponent_root / "main.py").write_bytes(pilot_bytes)
        manifest.append(
            {
                "id": entry.opponent_id,
                "canonical_deck_hash": entry.deck_hash,
                "policy_hash": policy_sha,
                "smoke_ok": True,
                "source": "public_replay_decklist_generic_pilot",
                "usage_boundary": "local_eval_only",
                "source_kind": entry.source_kind,
                "source_id": entry.source_id,
                "rank": entry.rank,
                "team_name": entry.team_name,
                "decklist_only": True,
            }
        )
    (root / "pool_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    metadata = {
        "schema_version": f"{SCHEMA}-pool",
        "research_only": True,
        "decklist_only_public_proxy": True,
        "pilot_source_path": str(source),
        "pilot_source_sha256": _sha256(source),
        "generated_policy_sha256": policy_sha,
        "entries": [entry.opponent_id for entry in entries],
    }
    (root / "pool_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    load_opponent_pool_v1(root)
    return root


def _annotate_games(
    games: Sequence[EvaluationGameV1],
    *,
    arm_id: str,
    pool_root: Path,
    base_seed: int,
) -> tuple[EvaluationGameV1, ...]:
    annotated: list[EvaluationGameV1] = []
    for game in games:
        metadata = {
            **dict(game.metadata),
            "public_deck_holdout_schema": SCHEMA,
            "holdout_arm": arm_id,
            "holdout_seed": base_seed,
            "public_deck_holdout_pool_root": str(pool_root),
            "research_only": True,
            "decklist_only_public_proxy": True,
            "authority": dict(AUTHORITY_FALSE),
        }
        annotated.append(replace(game, runner_ref=RUNNER_REF, metadata=metadata))
    return tuple(annotated)


def build_holdout_games(
    *,
    packages: Mapping[str, Path | str],
    reference_ids: Sequence[str],
    pool_root: Path | str,
    base_seeds: Sequence[int],
    repetitions: int = DEFAULT_REPETITIONS,
) -> tuple[EvaluationGameV1, ...]:
    """Build three-arm, both-seat, seed-stratified public holdout games."""

    if set(packages) != set(HOLDOUT_ARM_IDS):
        raise PublicDeckHoldoutError(f"packages must use arm IDs {HOLDOUT_ARM_IDS}")
    refs = tuple(str(item) for item in reference_ids)
    if not refs or len(refs) != len(set(refs)):
        raise PublicDeckHoldoutError("reference_ids must be unique and non-empty")
    if type(repetitions) is not int or repetitions <= 0:
        raise PublicDeckHoldoutError("repetitions must be a positive integer")
    seeds = tuple(int(seed) for seed in base_seeds)
    if not seeds or len(seeds) != len(set(seeds)) or any(seed < 0 for seed in seeds):
        raise PublicDeckHoldoutError("base_seeds must be unique nonnegative integers")
    pool = Path(pool_root).resolve()
    loaded_pool = load_opponent_pool_v1(pool)
    for ref in refs:
        if ref not in loaded_pool:
            raise PublicDeckHoldoutError(f"holdout reference is not in the isolated pool: {ref}")
    specs = {arm_id: CgPackageSpecV1.from_package(Path(packages[arm_id]).resolve()) for arm_id in HOLDOUT_ARM_IDS}
    deck_hashes = {spec.deck_sha256 for spec in specs.values()}
    if len(deck_hashes) != 1:
        raise PublicDeckHoldoutError("all holdout arms must use the same subject deck")
    all_games: list[EvaluationGameV1] = []
    for base_seed in seeds:
        per_arm: dict[str, tuple[EvaluationGameV1, ...]] = {}
        for arm_id in HOLDOUT_ARM_IDS:
            spec = specs[arm_id]
            arm = arena.ArenaArm(
                arm_id=f"public-holdout-{arm_id}",
                policy_id=spec.candidate_id,
                policy_sha256=spec.policy_sha256,
                arm_kind="root_cg",
                candidate_package_root=spec.package_root,
            )
            raw = arena._build_games(
                arm=arm,
                refs=refs,
                pool_root=pool,
                base_seed=base_seed,
                games_per_opponent_seat=repetitions,
                block_id=f"{SCHEMA}-{base_seed}-{arm_id}",
            )
            per_arm[arm_id] = _annotate_games(raw, arm_id=arm_id, pool_root=pool, base_seed=base_seed)
        key_sets = {
            arm_id: {(str(game.metadata["pair_key"]), game.seed) for game in games}
            for arm_id, games in per_arm.items()
        }
        if not (key_sets[HOLDOUT_ARM_IDS[0]] == key_sets[HOLDOUT_ARM_IDS[1]] == key_sets[HOLDOUT_ARM_IDS[2]]):
            raise PublicDeckHoldoutError("holdout arms do not share opponent/seat/seed strata")
        for arm_id in HOLDOUT_ARM_IDS:
            all_games.extend(per_arm[arm_id])
    return tuple(all_games)


def run_public_deck_holdout_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Spawn-safe worker for one packaged arm against one generic-pilot deck."""

    game = _game_from_payload(payload)
    metadata = game.metadata
    if metadata.get("public_deck_holdout_schema") != SCHEMA or metadata.get("authority") != AUTHORITY_FALSE:
        raise PublicDeckHoldoutError("game is not bound to the public deck holdout schema")
    if metadata.get("research_only") is not True or metadata.get("decklist_only_public_proxy") is not True:
        raise PublicDeckHoldoutError("public holdout game is missing research-only binding")
    subject_deck = Path(game.subject_deck_path).resolve()
    opponent_deck = Path(game.opponent_deck_path).resolve()
    if _sha256(subject_deck) != game.deck_sha256 or _sha256(opponent_deck) != game.opponent_deck_sha256:
        raise PublicDeckHoldoutError("deck identity changed after game construction")
    package_root = Path(str(metadata.get("candidate_package_root", ""))).resolve()
    if _sha256(package_root / "main.py") != game.policy_sha256:
        raise PublicDeckHoldoutError("candidate policy identity changed")
    subject_factory = arena._candidate_policy_factory(package_root)
    pool_root = Path(str(metadata.get("public_deck_holdout_pool_root", ""))).resolve()
    pool = load_opponent_pool_v1(pool_root)
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=str(subject_deck))
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    subject_first = game.seat == 0
    return run_match(
        deck_a_path=subject_deck if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else subject_deck,
        agent_a_name=game.policy_id if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else game.policy_id,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=f"/tmp/cg-public-deck-holdout-worker/{game.game_id}",
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )


def _seat_gap(summary: Mapping[str, object]) -> float:
    seat = summary.get("seat")
    if not isinstance(seat, Mapping):
        return 1.0
    left = seat.get("0")
    right = seat.get("1")
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return 1.0
    left_rate = left.get("score_rate")
    right_rate = right.get("score_rate")
    if type(left_rate) not in (int, float) or type(right_rate) not in (int, float):
        return 1.0
    return abs(float(left_rate) - float(right_rate))


def summarize_holdout_rows(rows: Sequence[Mapping[str, object]], *, games_per_arm: int) -> dict[str, object]:
    """Summarize all seeds and require the candidate to win each seed stratum."""

    if type(games_per_arm) is not int or games_per_arm <= 0:
        raise PublicDeckHoldoutError("games_per_arm must be a positive integer")
    by_arm = {
        arm_id: [row for row in rows if row.get("metadata", {}).get("holdout_arm") == arm_id]
        for arm_id in HOLDOUT_ARM_IDS
    }
    if any(len(arm_rows) != games_per_arm for arm_rows in by_arm.values()):
        raise PublicDeckHoldoutError("holdout arms do not cover the requested games")
    strata = {
        arm_id: {(str(row.get("metadata", {}).get("pair_key")), row.get("seed")) for row in arm_rows}
        for arm_id, arm_rows in by_arm.items()
    }
    if not (strata[HOLDOUT_ARM_IDS[0]] == strata[HOLDOUT_ARM_IDS[1]] == strata[HOLDOUT_ARM_IDS[2]]):
        raise PublicDeckHoldoutError("holdout summary strata differ")
    def holdout_seed(row: Mapping[str, object]) -> object:
        metadata = row.get("metadata", {})
        return metadata.get("holdout_seed") if isinstance(metadata, Mapping) else None

    seeds = tuple(sorted({holdout_seed(row) for row in by_arm[HOLDOUT_ARM_IDS[0]]}))
    if not seeds or any(type(seed) is not int for seed in seeds):
        raise PublicDeckHoldoutError("holdout rows have no seed strata")
    by_seed: dict[str, object] = {}
    seed_decisions: list[bool] = []
    all_faults = 0
    for seed in seeds:
        seed_arms: dict[str, object] = {}
        seed_scores: dict[str, float] = {}
        seed_gaps: dict[str, float] = {}
        seed_faults = 0
        for arm_id, arm_rows in by_arm.items():
            selected = [row for row in arm_rows if holdout_seed(row) == seed]
            if not selected:
                raise PublicDeckHoldoutError(f"seed {seed} is missing from arm {arm_id}")
            aggregate = arena._aggregate(selected)
            seed_arms[arm_id] = aggregate
            seed_scores[arm_id] = float(aggregate.get("score_rate") or 0.0)
            seed_gaps[arm_id] = _seat_gap(aggregate)
            seed_faults += int(aggregate.get("faults", 0))
        all_faults += seed_faults
        candidate_score = seed_scores[HOLDOUT_ARM_IDS[0]]
        positive = (
            seed_faults == 0
            and candidate_score > seed_scores[HOLDOUT_ARM_IDS[1]]
            and candidate_score > seed_scores[HOLDOUT_ARM_IDS[2]]
            and all(gap <= SEAT_GAP_LIMIT for gap in seed_gaps.values())
        )
        seed_decisions.append(positive)
        by_seed[str(seed)] = {
            "arms": seed_arms,
            "candidate_delta_points_vs_parent": (candidate_score - seed_scores[HOLDOUT_ARM_IDS[1]]) * 100.0,
            "candidate_delta_points_vs_incumbent": (candidate_score - seed_scores[HOLDOUT_ARM_IDS[2]]) * 100.0,
            "seat_gaps": seed_gaps,
            "faults": seed_faults,
            "positive": positive,
        }
    aggregate_arms = {arm_id: arena._aggregate(arm_rows) for arm_id, arm_rows in by_arm.items()}
    candidate_score = float(aggregate_arms[HOLDOUT_ARM_IDS[0]].get("score_rate") or 0.0)
    decision = "POSITIVE_SIGNAL" if all(seed_decisions) and all_faults == 0 else "NOT_PROMOTABLE"
    payload: dict[str, object] = {
        "schema_version": SCHEMA,
        "arms": aggregate_arms,
        "by_seed": by_seed,
        "seed_count": len(seeds),
        "games_per_arm": games_per_arm,
        "candidate_delta_points_vs_parent": (candidate_score - float(aggregate_arms[HOLDOUT_ARM_IDS[1]].get("score_rate") or 0.0)) * 100.0,
        "candidate_delta_points_vs_incumbent": (candidate_score - float(aggregate_arms[HOLDOUT_ARM_IDS[2]].get("score_rate") or 0.0)) * 100.0,
        "candidate_seat_gap": _seat_gap(aggregate_arms[HOLDOUT_ARM_IDS[0]]),
        "faults": all_faults,
        "decision": decision,
        "promotion_authority": False,
        "research_only": True,
        "decklist_only_public_proxy": True,
        "authority": dict(AUTHORITY_FALSE),
    }
    payload["summary_sha256"] = _semantic_sha(payload)
    return payload


def _run_parallel_with_progress(
    games: Sequence[EvaluationGameV1],
    *,
    output_dir: Path,
    workers: int,
    worker_recycle_games: int,
) -> dict[str, object]:
    """Run CABT with one TTY-owned bar or bounded non-TTY snapshots."""

    bar = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm

            bar = tqdm(total=len(games), desc="public deck holdout", unit="game", dynamic_ncols=True)
        except Exception:  # pragma: no cover - only exercised without tqdm
            bar = None
    state = {"completed": 0, "faults": 0, "last_emit": 0.0}

    def progress(row: Mapping[str, object]) -> None:
        state["completed"] += 1
        if str(row.get("outcome", "fault")) == "fault":
            state["faults"] += 1
        if bar is not None:
            bar.update(1)
            bar.set_postfix(faults=state["faults"])
            return
        now = time.monotonic()
        if now - state["last_emit"] >= 10.0 or state["completed"] == len(games):
            print(
                json.dumps(
                    {
                        "stage": "public_deck_holdout",
                        "completed": state["completed"],
                        "requested": len(games),
                        "faults": state["faults"],
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            state["last_emit"] = now

    try:
        return run_parallel_cabt_evaluation(
            games,
            output_dir=output_dir,
            max_workers=workers,
            worker_recycle_games=worker_recycle_games,
            overwrite=False,
            progress=progress,
        )
    finally:
        if bar is not None:
            bar.close()


def _load_config(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicDeckHoldoutError(f"invalid public deck holdout config: {path}") from exc
    config = _require_mapping(payload, "public deck holdout config")
    schema = config.get("schema_version")
    expected_ids = EXPECTED_FRESH_PUBLIC_DECK_IDS if str(schema) == "cg-public-deck-holdout-v1" else None
    if expected_ids is None:
        raise PublicDeckHoldoutError("unsupported public deck holdout config schema")
    ids = config.get("entry_ids")
    if tuple(ids or ()) != expected_ids:
        raise PublicDeckHoldoutError("config entry_ids do not match the frozen fresh public set")
    seeds = config.get("base_seeds")
    if not isinstance(seeds, list) or len(seeds) != 2 or len(set(seeds)) != 2:
        raise PublicDeckHoldoutError("config must contain exactly two independent base_seeds")
    if config.get("repetitions_per_opponent_seat") != DEFAULT_REPETITIONS:
        raise PublicDeckHoldoutError("public holdout is sealed to eight repetitions per seat")
    return config


def reconcile_completed_holdout(
    source_root: Path | str,
    *,
    output_root: Path | str | None = None,
) -> dict[str, object]:
    """Re-seal a completed ledger after a summary-only implementation fix.

    The source directory is never modified.  This is intentionally separate
    from the execution path so a post-hoc accounting correction cannot be
    mistaken for a new CABT sample.
    """

    source = Path(source_root).resolve()
    manifest_path = source / "manifest-complete.json"
    ledger_path = source / "evaluation/ledger.jsonl"
    if not manifest_path.is_file() or not ledger_path.is_file():
        raise PublicDeckHoldoutError(f"completed holdout ledger is missing: {source}")
    manifest = _require_mapping(json.loads(manifest_path.read_text(encoding="utf-8")), "completed holdout manifest")
    rows = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    games_per_arm = int(manifest.get("games_per_arm", 0))
    summary = summarize_holdout_rows(rows, games_per_arm=games_per_arm)
    summary.update(
        {
            "protocol_sha256": manifest.get("protocol_sha256"),
            "evaluator_summary": json.loads((source / "evaluation/summary.json").read_text(encoding="utf-8")),
            "reconciled_from": str(source),
            "reconciliation_reason": "group rows by metadata.holdout_seed rather than engine seed",
        }
    )
    summary["summary_sha256"] = _semantic_sha({key: value for key, value in summary.items() if key != "summary_sha256"})
    destination = Path(output_root).resolve() if output_root is not None else source.with_name(f"{source.name}-reviewed")
    if destination.exists():
        raise FileExistsError(f"reconciled holdout output already exists: {destination}")
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("pool", "evaluation"):
        shutil.copytree(source / name, destination / name)
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    reviewed_manifest = {
        **dict(manifest),
        "status": "RECONCILED",
        "source_output_root": str(source),
        "source_manifest_complete_sha256": _sha256(manifest_path),
        "summary_sha256": _sha256(destination / "summary.json"),
        "reconciliation_runner_sha256": _sha256(Path(__file__).resolve()),
        "decision": summary["decision"],
        "faults": summary["faults"],
    }
    (destination / "manifest.json").write_text(json.dumps(reviewed_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (destination / "manifest-complete.json").write_text(json.dumps(reviewed_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "RECONCILED", "output_root": str(destination), "summary": summary, "manifest": reviewed_manifest}


def collect_seen_deck_hashes(runs_root: Path | str) -> frozenset[str]:
    """Read only opponent deck identities already represented in run ledgers."""

    seen: set[str] = set()
    root = Path(runs_root)
    if not root.is_dir():
        return frozenset()
    for ledger in root.rglob("ledger.jsonl"):
        try:
            lines = ledger.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, Mapping):
                continue
            for key in ("opponent_deck_sha256", "deck_sha256"):
                value = row.get(key)
                if isinstance(value, str) and len(value) == 64:
                    seen.add(value)
            identity = row.get("opponent_identity")
            if isinstance(identity, Mapping):
                value = identity.get("deck_sha256")
                if isinstance(value, str) and len(value) == 64:
                    seen.add(value)
    return frozenset(seen)


def _current_deck_hashes(pool_root: Path) -> frozenset[str]:
    manifest = json.loads((pool_root / "pool_manifest.json").read_text(encoding="utf-8"))
    rows = manifest if isinstance(manifest, list) else manifest.get("opponents", [])
    if not isinstance(rows, list):
        raise PublicDeckHoldoutError("current opponent pool manifest has no rows")
    return frozenset(str(row.get("canonical_deck_hash")) for row in rows if isinstance(row, Mapping))


def run_public_deck_holdout(
    *,
    candidate_package: Path | str = DEFAULT_CANDIDATE_PACKAGE,
    parent_package: Path | str = DEFAULT_PARENT_PACKAGE,
    incumbent_package: Path | str = DEFAULT_INCUMBENT_PACKAGE,
    source_pool: Path | str = DEFAULT_SOURCE_POOL,
    current_pool: Path | str = DEFAULT_CURRENT_POOL,
    config_path: Path | str = DEFAULT_CONFIG,
    output_root: Path | str,
    workers: int = DEFAULT_MAX_WORKERS,
    worker_recycle_games: int = DEFAULT_WORKER_RECYCLE_GAMES,
    execute: bool = False,
) -> dict[str, object]:
    if workers != DEFAULT_MAX_WORKERS or worker_recycle_games != DEFAULT_WORKER_RECYCLE_GAMES:
        raise PublicDeckHoldoutError("public holdout is sealed to workers=12/recycle=16")
    config_path = Path(config_path).resolve()
    config = _load_config(config_path)
    source_path = Path(source_pool).resolve()
    current_root = Path(current_pool).resolve()
    source_payload = _require_mapping(json.loads(source_path.read_text(encoding="utf-8")), "public deck source")
    entries = select_public_holdout_entries(
        source_payload,
        current_deck_hashes=_current_deck_hashes(current_root),
        seen_deck_hashes=collect_seen_deck_hashes(_ROOT / "runs"),
        expected_ids=tuple(str(item) for item in config["entry_ids"]),
    )
    root = Path(output_root).resolve()
    if root.exists():
        raise FileExistsError(f"public holdout output already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    pool_root = materialize_public_holdout_pool(entries, output_root=root / "pool")
    packages = {
        HOLDOUT_ARM_IDS[0]: Path(candidate_package).resolve(),
        HOLDOUT_ARM_IDS[1]: Path(parent_package).resolve(),
        HOLDOUT_ARM_IDS[2]: Path(incumbent_package).resolve(),
    }
    seeds = tuple(int(item) for item in config["base_seeds"])
    repetitions = int(config["repetitions_per_opponent_seat"])
    games = build_holdout_games(
        packages=packages,
        reference_ids=tuple(entry.opponent_id for entry in entries),
        pool_root=pool_root,
        base_seeds=seeds,
        repetitions=repetitions,
    )
    specs = {arm_id: CgPackageSpecV1.from_package(path) for arm_id, path in packages.items()}
    protocol = _semantic_sha(
        {
            "schema_version": SCHEMA,
            "source_pool_sha256": _sha256(source_path),
            "source_pool_hash": source_payload.get("pool_hash"),
            "selected_entry_ids": [entry.opponent_id for entry in entries],
            "selected_deck_hashes": [entry.deck_hash for entry in entries],
            "base_seeds": list(seeds),
            "repetitions": repetitions,
            "packages": {arm_id: spec.policy_sha256 for arm_id, spec in specs.items()},
            "evaluator_sha256": evaluation_implementation_sha256_v1(),
        }
    )
    manifest: dict[str, object] = {
        "schema_version": SCHEMA,
        "status": "EXECUTING" if execute else "DRY_RUN",
        "research_only": True,
        "decklist_only_public_proxy": True,
        "authority": dict(AUTHORITY_FALSE),
        "source_pool_path": str(source_path),
        "source_pool_sha256": _sha256(source_path),
        "source_pool_hash": source_payload.get("pool_hash"),
        "current_pool_manifest_sha256": _sha256(current_root / "pool_manifest.json"),
        "selected_entries": [entry.opponent_id for entry in entries],
        "selected_deck_hashes": [entry.deck_hash for entry in entries],
        "base_seeds": list(seeds),
        "repetitions_per_opponent_seat": repetitions,
        "requested_games": len(games),
        "games_per_arm": len(games) // len(HOLDOUT_ARM_IDS),
        "pool_root": str(pool_root),
        "packages": {arm_id: spec.to_dict() for arm_id, spec in specs.items()},
        "protocol_sha256": protocol,
        "evaluator_implementation_sha256": evaluation_implementation_sha256_v1(),
        "runner_sha256": _sha256(Path(__file__).resolve()),
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "holdout_promotion_rule": "candidate must beat P2 and P1 on every seed; all arms fault0; seat gaps <=5%; no automatic promotion",
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    if not execute:
        return {"status": "DRY_RUN", "output_root": str(root), "manifest": manifest}
    evaluation = _run_parallel_with_progress(
        games,
        output_dir=root / "evaluation",
        workers=workers,
        worker_recycle_games=worker_recycle_games,
    )
    summary = summarize_holdout_rows(evaluation["rows"], games_per_arm=len(games) // len(HOLDOUT_ARM_IDS))
    summary["protocol_sha256"] = protocol
    summary["evaluator_summary"] = evaluation["summary"]
    summary_path = root / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    manifest.update({"status": "COMPLETE", "summary_sha256": _sha256(summary_path), "decision": summary["decision"], "faults": summary["faults"]})
    (root / "manifest-complete.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "COMPLETE", "output_root": str(root), "summary": summary, "manifest": manifest}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-package", type=Path, default=DEFAULT_CANDIDATE_PACKAGE)
    parser.add_argument("--parent-package", type=Path, default=DEFAULT_PARENT_PACKAGE)
    parser.add_argument("--incumbent-package", type=Path, default=DEFAULT_INCUMBENT_PACKAGE)
    parser.add_argument("--source-pool", type=Path, default=DEFAULT_SOURCE_POOL)
    parser.add_argument("--current-pool", type=Path, default=DEFAULT_CURRENT_POOL)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--worker-recycle-games", type=int, default=DEFAULT_WORKER_RECYCLE_GAMES)
    parser.add_argument("--execute", action="store_true", help="required acknowledgement for CABT execution")
    args = parser.parse_args(argv)
    if not args.execute:
        raise SystemExit("refusing public deck holdout CABT run without --execute")
    try:
        result = run_public_deck_holdout(
            candidate_package=args.candidate_package,
            parent_package=args.parent_package,
            incumbent_package=args.incumbent_package,
            source_pool=args.source_pool,
            current_pool=args.current_pool,
            config_path=args.config,
            output_root=args.output,
            workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
            execute=True,
        )
    except (PublicDeckHoldoutError, CgAlternatingRuntimeError, FileExistsError, OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({key: value for key, value in result.items() if key != "manifest"}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
