"""frozen population mixture から CABT experience chunk を生成する。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import shutil
import sys
import time
from typing import Any

from mage_ptcg.policy_learning.r2d3.online_collection import MixtureManifest
from mage_ptcg.policy_learning.r2d3.sequence import R2D3Transition, split_episode

from .benchmark import ScheduledGame
from .cabt import CabtMatchExecutor
from .catalog import CatalogSnapshot
from .contracts import LeagueContractError, atomic_write_json, content_id, load_json
from .experience import read_experience_chunk, sequence_record, write_experience_chunk


@dataclass(frozen=True, slots=True)
class CollectionRequest:
    population_epoch_id: str
    candidate_runtime_policy_id: str
    episodes: int
    base_seed: int
    subject_deck_id: str
    execution_block: str = "training"
    opponent_episode_quotas: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        if self.episodes < 1:
            raise LeagueContractError("collection episodes must be positive")
        if self.opponent_episode_quotas:
            quota_ids = [opponent_id for opponent_id, _count in self.opponent_episode_quotas]
            if len(quota_ids) != len(set(quota_ids)):
                raise LeagueContractError("collection opponent quotas must be unique")
            if any(count < 2 or count % 2 for _opponent_id, count in self.opponent_episode_quotas):
                raise LeagueContractError(
                    "each collection opponent quota must be a positive even number"
                )
            if sum(count for _opponent_id, count in self.opponent_episode_quotas) != self.episodes:
                raise LeagueContractError(
                    "collection episodes must equal the sum of opponent quotas"
                )

    def identity(self) -> dict[str, Any]:
        return {
            "population_epoch_id": self.population_epoch_id,
            "candidate_runtime_policy_id": self.candidate_runtime_policy_id,
            "episodes": self.episodes,
            "base_seed": self.base_seed,
            "subject_deck_id": self.subject_deck_id,
            "execution_block": self.execution_block,
            "opponent_episode_quotas": [list(item) for item in self.opponent_episode_quotas],
        }


@dataclass(frozen=True, slots=True)
class CollectionAssignment:
    episode_index: int
    member: Any
    seat: str


def build_collection_schedule(
    request: CollectionRequest, mixture: MixtureManifest
) -> tuple[CollectionAssignment, ...]:
    """quota 指定時は相手ごとの先後を厳密に揃える。"""

    if not request.opponent_episode_quotas:
        return tuple(
            CollectionAssignment(
                episode_index=episode_index,
                member=mixture.sample(seed=request.base_seed + episode_index),
                seat=("subject_first" if episode_index % 2 == 0 else "subject_second"),
            )
            for episode_index in range(request.episodes)
        )
    members = {member.opponent_policy_id: member for member in mixture.members}
    pairs: list[tuple[Any, str, str]] = []
    for opponent_id, count in sorted(request.opponent_episode_quotas):
        member = members.get(opponent_id)
        if member is None:
            raise LeagueContractError(
                "collection quota opponent is absent from the population mixture: "
                f"{opponent_id}"
            )
        pairs.extend((member, "subject_first", "subject_second") for _ in range(count // 2))
    randomizer = random.Random(request.base_seed)
    randomizer.shuffle(pairs)
    assignments: list[CollectionAssignment] = []
    for member, first, second in pairs:
        assignments.extend(
            (
                CollectionAssignment(len(assignments), member, first),
                CollectionAssignment(len(assignments) + 1, member, second),
            )
        )
    return tuple(assignments)


class _CollectionProgress:
    """TTY では一つの bar、非 TTY では集約 snapshot だけを表示する。"""

    def __init__(self, *, total: int, initial: int) -> None:
        self.total = total
        self.completed = initial
        self.initial = initial
        self.started = time.monotonic()
        self.last_snapshot = self.started
        self.bar: Any | None = None
        if sys.stderr.isatty():
            from tqdm import tqdm

            self.bar = tqdm(
                total=total,
                initial=initial,
                desc="CABT replay collection",
                unit="game",
                dynamic_ncols=True,
            )

    def update(self, *, sequences: int, faults: int) -> None:
        self.completed += 1
        if self.bar is not None:
            self.bar.update(1)
            self.bar.set_postfix(sequences=sequences, faults=faults, refresh=False)
            return
        now = time.monotonic()
        if now - self.last_snapshot >= 10.0 or self.completed == self.total:
            elapsed = max(now - self.started, 1e-9)
            rate = (self.completed - self.initial) / elapsed if self.completed > self.initial else 0.0
            remaining = self.total - self.completed
            snapshot = {
                "stage": "collect",
                "completed": self.completed,
                "total": self.total,
                "games_per_second": round(rate, 4),
                "eta_seconds": round(remaining / rate, 1) if rate else None,
                "sequences": sequences,
                "faults": faults,
            }
            print(snapshot, file=sys.stderr, flush=True)
            self.last_snapshot = now

    def close(self) -> None:
        if self.bar is not None:
            self.bar.close()


def _collection_identity(
    request: CollectionRequest, mixture: MixtureManifest
) -> dict[str, Any]:
    return {"request": request.identity(), "mixture_hash": mixture.mixture_hash}


def _load_finalized_collection(
    *,
    output_root: Path,
    identity: dict[str, Any],
) -> dict[str, Any] | None:
    final_path = output_root / "collection_manifest.json"
    if not final_path.exists():
        return None
    finalized = load_json(final_path)
    if finalized.get("identity") != identity or finalized.get("status") != "COMPLETE":
        raise LeagueContractError("collection output belongs to a different request")
    chunk_path = Path(str(finalized.get("manifest_path", "")))
    if not chunk_path.is_file():
        raise LeagueContractError("finalized collection chunk manifest is missing")
    chunk, _records = read_experience_chunk(chunk_path)
    if chunk.get("experience_chunk_id") != finalized.get("experience_chunk_id"):
        raise LeagueContractError("finalized collection chunk identity mismatch")
    return finalized


def _load_completed_game(path: Path, expected_identity: dict[str, Any]) -> dict[str, Any]:
    payload = load_json(path)
    if payload.get("game_identity") != expected_identity:
        raise LeagueContractError(f"collection staging identity mismatch: {path}")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise LeagueContractError(f"collection staging has no records: {path}")
    return payload


def _trace_sequences(
    traces: list[dict[str, Any]],
    *,
    prefix: str,
    outcome_reward: float,
    candidate_runtime_policy_id: str,
    opponent_policy_hash: str,
    opponent_deck_hash: str,
    opponent_source_lineage: str,
    opponent_family: str,
    own_deck_hash: str,
) -> tuple[list[Any], int]:
    """multi-select 境界を跨がず、学習可能な single action segment を作る。"""

    segments: list[list[dict[str, Any]]] = [[]]
    skipped = 0
    for trace in traces:
        if not trace.get("trainable_single_action", True):
            skipped += 1
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(trace)
    sequences = []
    nonempty = [segment for segment in segments if segment]
    for segment_index, segment in enumerate(nonempty):
        transitions = []
        terminal_segment = segment_index == len(nonempty) - 1
        for offset, trace in enumerate(segment):
            terminal = terminal_segment and offset == len(segment) - 1
            transitions.append(
                R2D3Transition(
                    public_state=tuple(trace["state"]),
                    legal_actions=tuple(tuple(action) for action in trace["actions"]),
                    selected_action=int(trace["selected_action"]),
                    reward=outcome_reward if terminal else 0.0,
                    discount=0.0 if terminal else 0.99,
                    terminal=terminal,
                    behavior_policy_version=candidate_runtime_policy_id,
                    behavior_source="continuous_cabt_online",
                    opponent_policy_hash=opponent_policy_hash,
                    opponent_deck_hash=opponent_deck_hash,
                    opponent_source_lineage=opponent_source_lineage,
                    opponent_family=opponent_family,
                    own_deck_hash=own_deck_hash,
                    demonstration=False,
                )
            )
        sequences.extend(
            split_episode(
                transitions,
                burn_in=8,
                unroll=20,
                stride=20,
                prefix=f"{prefix}-segment-{segment_index}",
                n_step_lookahead=5,
            )
        )
    return sequences, skipped


def collect_experience(
    *,
    request: CollectionRequest,
    mixture: MixtureManifest,
    catalog: CatalogSnapshot,
    executor: CabtMatchExecutor,
    output_root: Path,
) -> dict[str, Any]:
    output_root = Path(output_root)
    identity = _collection_identity(request, mixture)
    finalized = _load_finalized_collection(output_root=output_root, identity=identity)
    if finalized is not None:
        return finalized

    staging_root = output_root / "staging"
    games_root = staging_root / "games"
    atomic_write_json(
        staging_root / "request.json",
        {"schema_version": 1, "status": "COLLECTING", "identity": identity},
    )
    scheduled: list[tuple[int, dict[str, Any], ScheduledGame, Any]] = []
    for assignment in build_collection_schedule(request, mixture):
        episode_index = assignment.episode_index
        member = assignment.member
        try:
            entry = catalog.get_instance(member.opponent_policy_id)
        except LeagueContractError as exc:
            raise LeagueContractError(
                "mixture opponent_policy_id must be an opponent_instance_id"
            ) from exc
        seat = assignment.seat
        game_identity = {
            "population_epoch_id": request.population_epoch_id,
            "candidate_runtime_policy_id": request.candidate_runtime_policy_id,
            "opponent_instance_id": entry.opponent_instance_id,
            "episode_index": episode_index,
            "seat": seat,
            "execution_block": request.execution_block,
        }
        game_id = content_id("collection-game-v1", game_identity)
        game = ScheduledGame(
            benchmark_id=content_id("collection-benchmark-v1", request.population_epoch_id),
            runtime_policy_id=request.candidate_runtime_policy_id,
            subject_deck_id=request.subject_deck_id,
            opponent_instance_id=entry.opponent_instance_id,
            seat=seat,
            repetition_index=episode_index,
            execution_block=request.execution_block,
            env_seed=request.base_seed + episode_index,
            game_key=game_id,
        )
        scheduled.append((episode_index, game_identity, game, entry))

    existing: dict[str, dict[str, Any]] = {}
    for _episode_index, game_identity, game, _entry in scheduled:
        staged_path = games_root / f"{game.game_key}.json"
        if staged_path.exists():
            existing[game.game_key] = _load_completed_game(staged_path, game_identity)
    progress = _CollectionProgress(total=request.episodes, initial=len(existing))
    fault_count = 0
    try:
        for _episode_index, game_identity, game, entry in scheduled:
            staged_path = games_root / f"{game.game_key}.json"
            if game.game_key in existing:
                continue
            result, policy = executor.execute(game, entry)
            reward = {"win": 1.0, "draw": 0.0, "loss": -1.0}[result["outcome"]]
            sequences, skipped = _trace_sequences(
                policy.traces,
                prefix=game.game_key,
                outcome_reward=reward,
                candidate_runtime_policy_id=request.candidate_runtime_policy_id,
                opponent_policy_hash=entry.policy_hash,
                opponent_deck_hash=entry.deck_hash,
                opponent_source_lineage=entry.source_id,
                opponent_family=entry.effective_archetype_id,
                own_deck_hash=policy.manifest["deck_hash"]
                if hasattr(policy, "manifest")
                else executor.runtime_policy.manifest["deck_hash"],
            )
            if not sequences:
                raise LeagueContractError(
                    f"collection game {game.game_key} produced no trainable decisions"
                )
            records = []
            for sequence in sequences:
                record = sequence_record(
                    game_id=game.game_key,
                    sequence=sequence,
                    candidate_runtime_policy_id=request.candidate_runtime_policy_id,
                    opponent_instance_id=entry.opponent_instance_id,
                    population_epoch_id=request.population_epoch_id,
                    candidate_seat=result["candidate_side"],
                    result=result["outcome"],
                )
                record["sampling_probability"] = member.probability
                record["meta_strategy_hash"] = mixture.mixture_hash
                records.append(record)
            payload = {
                "schema_version": 1,
                "game_identity": game_identity,
                "result": result,
                "records": records,
                "skipped_multi_select_decisions": skipped,
            }
            atomic_write_json(staged_path, payload)
            existing[game.game_key] = payload
            # 成功した CABT の詳細ログは staged record に不要なので即時に片付ける。
            match_root = getattr(executor, "output_root", None)
            if match_root is not None:
                match_dir = Path(match_root) / game.game_key
                if match_dir.is_dir():
                    shutil.rmtree(match_dir)
            progress.update(
                sequences=sum(len(item["records"]) for item in existing.values()),
                faults=fault_count,
            )
    finally:
        progress.close()

    if len(existing) != request.episodes:
        raise LeagueContractError("collection staging does not cover every scheduled game")
    ordered_games = [existing[game.game_key] for _, _, game, _ in scheduled]
    all_records = [
        record
        for payload in ordered_games
        for record in payload["records"]
    ]
    skipped_multi_select = sum(
        int(payload["skipped_multi_select_decisions"]) for payload in ordered_games
    )
    collector_id = content_id("experience-collector-v1", identity)
    manifest = write_experience_chunk(
        output_root=output_root / "chunks",
        records=all_records,
        collector_id=collector_id,
    )
    outcomes = {outcome: 0 for outcome in ("win", "loss", "draw")}
    for payload in ordered_games:
        outcomes[payload["result"]["outcome"]] += 1
    opponent_seat_counts: dict[str, dict[str, int]] = {}
    for _episode_index, _identity, game, entry in scheduled:
        counts = opponent_seat_counts.setdefault(
            entry.opponent_instance_id,
            {"subject_first": 0, "subject_second": 0},
        )
        counts[game.seat] += 1
    if request.opponent_episode_quotas:
        for opponent_id, count in request.opponent_episode_quotas:
            counts = opponent_seat_counts.get(opponent_id, {})
            if (
                counts.get("subject_first") != count // 2
                or counts.get("subject_second") != count // 2
            ):
                raise LeagueContractError(
                    "collection quota schedule did not preserve both seats"
                )
    finalized = {
        "schema_version": 1,
        "status": "COMPLETE",
        "identity": identity,
        "experience_chunk_id": manifest["experience_chunk_id"],
        "manifest_path": str(
            output_root / "chunks" / manifest["experience_chunk_id"] / "manifest.json"
        ),
        "games": request.episodes,
        "sequences": len(all_records),
        "outcomes": outcomes,
        "opponent_seat_counts": opponent_seat_counts,
        "skipped_multi_select_decisions": skipped_multi_select,
    }
    atomic_write_json(output_root / "collection_manifest.json", finalized)
    # chunk と final manifest が確定してからだけ staging を消す。中断時は残して再開する。
    if staging_root.is_dir():
        shutil.rmtree(staging_root)
    return finalized
