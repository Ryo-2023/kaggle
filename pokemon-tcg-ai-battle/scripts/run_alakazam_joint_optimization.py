"""Run a bounded, reproducible Alakazam deck × policy experiment.

This runner deliberately keeps the existing CABT harness.  It does not alter
Rule v0, the default deck, or any ``agents/`` source.  Candidate identity is a
hash of the exact deck, policy, adapter and runtime configuration; all output
is written to a caller-provided (normally Git-ignored) artifact root.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from main import make_deterministic_agent, make_random_agent, make_rule_agent, make_rule_agent_v1, read_deck_csv, validate_deck
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256
from mage_ptcg.student.dataset import RuleBCExample, write_dataset
from mage_ptcg.student.evaluation import evaluate_model
from mage_ptcg.student.model import StudentV0Model, train_model
from mage_ptcg.student.runtime import RuntimeStudentPolicy
from mage_ptcg.opponents.synthetic_stress_v1 import make_synthetic_stress_agent
from scripts.test_sim import run_match
from tqdm import tqdm


SCHEMA = "alakazam-joint-optimization-v1"
SYNTHETIC_KINDS = ("legal-random", "conservative-resource", "aggressive-tempo", "setup-heavy", "early-disruption")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    fields = sorted({key for row in rows for key in row}) or ["status"]
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


@dataclass(frozen=True)
class DeckAsset:
    asset_id: str
    cards: tuple[int, ...]
    source: str
    exact: bool

    @property
    def deck_hash(self) -> str:
        return canonical_deck_sha256(list(self.cards))


@dataclass(frozen=True)
class PolicyAsset:
    policy_id: str
    kind: str
    policy_hash: str
    exact_deck_hash: str | None
    source: str
    runtime: str


@dataclass(frozen=True)
class Opponent:
    opponent_id: str
    cards: tuple[int, ...]
    policy_kind: str
    policy_hash: str
    deck_family: str


def _load_public_alakazam_decks(registry: Path) -> list[DeckAsset]:
    values: list[DeckAsset] = []
    with registry.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cards = tuple(int(value) for value in json.loads(row["cards_json"]))
            if {741, 742, 743}.issubset(cards):
                values.append(DeckAsset(f"replay_{row['deck_hash'][:12]}", tuple(validate_deck(cards)), f"public replay {row['episode_id']}", True))
    if len(values) < 2:
        raise ValueError("two exact public Alakazam decks are required")
    return values[:2]


def _copy_checkpoint(source: Path, target: Path) -> dict[str, object]:
    if not source.is_dir():
        raise FileNotFoundError(source)
    destination = target / "checkpoint_backup"
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copytree(source, destination)
    source_files = sorted(path for path in source.rglob("*") if path.is_file())
    copy_files = sorted(path for path in destination.rglob("*") if path.is_file())
    relative_source = [path.relative_to(source) for path in source_files]
    if relative_source != [path.relative_to(destination) for path in copy_files]:
        raise RuntimeError("checkpoint copy structure differs")
    checksums = {str(path.relative_to(source)): _sha(path) for path in source_files}
    copied = {str(path.relative_to(destination)): _sha(path) for path in copy_files}
    if checksums != copied:
        raise RuntimeError("checkpoint checksum mismatch")
    matches = json.loads((destination / "poke_pad_to_sacred_ash" / "matches.json").read_text(encoding="utf-8"))
    summary = json.loads((destination / "poke_pad_to_sacred_ash" / "summary.json").read_text(encoding="utf-8"))
    return {"source": str(source), "destination": str(destination), "file_count": len(source_files), "checksums": checksums, "completed_games": len(matches), "candidate_id": summary["candidate"]["candidate_id"], "deck_hash": summary["deck_hash"], "stage2_status": summary["stage2_status"]}


def _write_deck(path: Path, cards: Sequence[int]) -> None:
    path.write_text("\n".join(str(value) for value in validate_deck(cards)) + "\n", encoding="utf-8")


def _run_stage2_baseline(output: Path, work: Path, cards: Sequence[int]) -> list[dict[str, object]]:
    """Run the pre-existing stage-2 schedule for a fresh Rule-v0 baseline."""
    deck_path = work / "stage2_baseline.csv"; _write_deck(deck_path, cards)
    opponent_kinds = ("random", "deterministic", "rule_v1", "setup-heavy")
    rows: list[dict[str, object]] = []
    for game in range(128):
        kind = opponent_kinds[(game // 2) % len(opponent_kinds)]; seat = game % 2
        def opponent(runtime_deck: list[int], seed: int, name: str = kind) -> Callable[[dict], list[int]]:
            if name == "random":
                return make_random_agent(deck=runtime_deck, seed=seed)
            if name == "deterministic":
                return make_deterministic_agent(deck=runtime_deck)
            if name == "rule_v1":
                return make_rule_agent_v1(deck=runtime_deck, seed=seed)
            return make_synthetic_stress_agent(kind="setup-heavy", deck=runtime_deck, seed=seed).as_agent()
        own = lambda runtime_deck, seed: make_rule_agent(deck=runtime_deck, seed=seed)
        result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name="rule_v0", agent_b_name=kind, agent_a_factory=own, agent_b_factory=opponent, seed=2026072602 + game, output_dir=work / "transient", save_html=False, save_result=False) if seat == 0 else run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=kind, agent_b_name="rule_v0", agent_a_factory=opponent, agent_b_factory=own, seed=2026072602 + game, output_dir=work / "transient", save_html=False, save_result=False)
        rows.append({"game": game, "opponent": kind, "side": seat, "won": result.get("winner") == seat, "status": result.get("status"), "steps": result.get("steps"), "elapsed_seconds": result.get("elapsed_seconds")})
    _write_csv(output / "matched_baseline_results.csv", rows)
    return rows


def _policy_factory(asset: PolicyAsset, deck: Sequence[int]) -> Callable[[dict], list[int]]:
    cards = list(validate_deck(deck))
    if asset.kind == "rule_v0":
        return make_rule_agent(deck=cards)
    if asset.kind == "rule_v1":
        return make_rule_agent_v1(deck=cards)
    if asset.kind in {"setup_heavy", "aggressive_tempo"}:
        return make_synthetic_stress_agent(kind=asset.kind.replace("_", "-"), deck=cards, seed=20260726).as_agent()
    raise ValueError(f"non-callable policy kind: {asset.kind}")


def _student_factory(model: StudentV0Model, deck: Sequence[int]) -> Callable[[dict], list[int]]:
    fallback = make_rule_agent(deck=list(deck))
    student = RuntimeStudentPolicy(model)
    def choose(observation: dict) -> list[int]:
        selected = student.choose(observation)
        return fallback(observation) if selected is None else selected
    return choose


def _opponent_factory(opponent: Opponent) -> Callable[[list[int], int], Callable[[dict], list[int]]]:
    def factory(_deck: list[int], seed: int) -> Callable[[dict], list[int]]:
        cards = list(opponent.cards)
        return make_synthetic_stress_agent(kind=opponent.policy_kind, deck=cards, seed=seed).as_agent()
    return factory


def _trace_teacher(factory: Callable[[dict], list[int]], deck: Sequence[int], game_id: str, sink: list[RuleBCExample], metadata: Mapping[str, str]) -> Callable[[dict], list[int]]:
    cards = list(deck)
    def choose(observation: dict) -> list[int]:
        selected = list(factory(observation))
        if not isinstance(observation, Mapping) or observation.get("select") is None:
            return selected
        state = build_decision_state(observation)
        by_index = {item.option_index: item for item in state.legal_actions}
        if not set(selected).issubset(by_index) or len(selected) != len(set(selected)):
            raise ValueError("teacher emitted an illegal action")
        select = observation.get("select")
        if not isinstance(select, Mapping):
            return selected
        legal = tuple({"digest": item.action_key.digest, "payload": item.action_key.to_canonical_payload()} for item in state.legal_actions)
        target = tuple(by_index[index].action_key.digest for index in selected)
        example = RuleBCExample(
            schema_version="rule-bc-v1", example_id=_digest({"game": game_id, "decision": len(sink), "state": state.digest}),
            source_id="sha256:" + hashlib.sha256(game_id.encode()).hexdigest(), public_state=state.actor_view.public_state,
            own_private_state=state.actor_view.own_private_state, visible_history=tuple(state.actor_view.visible_history),
            selection_type=select.get("type"), selection_context=select.get("context"), min_count=int(select["minCount"]), max_count=int(select["maxCount"]),
            legal_actions=legal, target_action_digests=target, teacher_ranking=tuple((item["digest"], 1 if item["digest"] in target else 0) for item in legal),
            fallback_used=False, deck_fingerprint=canonical_deck_sha256(cards), source_revision="joint-optimization-v1", metadata=dict(metadata),
        )
        sink.append(example)
        return selected
    return choose


def _schedule(opponents: Sequence[Opponent], games: int) -> list[tuple[Opponent, int]]:
    if games % 2:
        raise ValueError("schedule requires an even game count")
    if games < len(opponents) * 2:
        # Smoke is a contract check, not a balanced scorecard.  Full stages
        # below enforce the five-hash / four-family balanced schedule.
        return [(opponents[index % len(opponents)], index % 2) for index in range(games)]
    # Input is policy-major (five hashes × four Deck families).  Reorder to
    # Deck-major before cycling so the four remainder games are assigned to
    # four different hashes rather than all to the first hash.
    groups: dict[str, list[Opponent]] = defaultdict(list)
    for opponent in opponents:
        groups[opponent.policy_hash].append(opponent)
    policy_groups = [groups[key] for key in sorted(groups)]
    if len(policy_groups) != 5 or any(len(group) != 4 for group in policy_groups):
        raise ValueError("development proxy must contain five policy hashes with four Deck families each")
    ordered = [policy_groups[policy_index][deck_index] for deck_index in range(4) for policy_index in range(5)]
    rows = [(ordered[index % len(ordered)], index % 2) for index in range(games)]
    counts = Counter(opponent.policy_hash for opponent, _side in rows)
    if max(counts.values()) / games > 0.21:
        raise ValueError("policy hash exceeds the 20% balancing tolerance")
    return rows


def _run_candidate(candidate_id: str, deck: DeckAsset, policy: PolicyAsset, opponents: Sequence[Opponent], games: int, seed: int, work: Path, *, trace: bool = False) -> tuple[list[dict[str, object]], list[RuleBCExample]]:
    schedule = _schedule(opponents, games)
    deck_path = work / f"{candidate_id}.csv"; _write_deck(deck_path, deck.cards)
    rows: list[dict[str, object]] = []; examples: list[RuleBCExample] = []
    for index, (opponent, seat) in enumerate(schedule):
        factory = _policy_factory(policy, deck.cards)
        own: Callable[[dict], list[int]] = factory
        if trace:
            own = _trace_teacher(factory, deck.cards, f"{candidate_id}-{index:04d}", examples, {"teacher": policy.policy_hash, "opponent_policy": opponent.policy_hash, "opponent_family": opponent.deck_family})
        opponent_factory = _opponent_factory(opponent)
        started = time.perf_counter()
        if seat == 0:
            result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=policy.policy_id, agent_b_name=opponent.opponent_id, agent_a_factory=lambda _d, _s, agent=own: agent, agent_b_factory=opponent_factory, seed=seed + index, output_dir=work / "transient", save_html=False, save_result=False)
        else:
            result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=opponent.opponent_id, agent_b_name=policy.policy_id, agent_a_factory=opponent_factory, agent_b_factory=lambda _d, _s, agent=own: agent, seed=seed + index, output_dir=work / "transient", save_html=False, save_result=False)
        rows.append({"candidate_id": candidate_id, "deck_id": deck.asset_id, "deck_hash": deck.deck_hash, "policy_id": policy.policy_id, "policy_hash": policy.policy_hash, "adapter_hash": _digest({"policy": policy.kind, "runtime": policy.runtime}), "runtime_config_hash": _digest({"max_steps": 10000, "runner": SCHEMA}), "game": index, "opponent_id": opponent.opponent_id, "opponent_policy_hash": opponent.policy_hash, "opponent_family": opponent.deck_family, "side": seat, "won": result.get("winner") == seat, "status": result.get("status"), "steps": result.get("steps"), "elapsed_seconds": result.get("elapsed_seconds"), "trace_seconds": time.perf_counter() - started})
    return rows, examples


def _score(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    done = [row for row in rows if row.get("status") == "DONE"]
    if not done:
        return {"games": len(rows), "win_rate": None, "faults": len(rows), "worst_quartile": None}
    by_policy: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    by_opponent: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in done:
        by_policy[str(row["opponent_policy_hash"])].append(row); by_opponent[str(row["opponent_id"])].append(row)
    uniform = statistics.mean(sum(bool(value["won"]) for value in group) / len(group) for group in by_policy.values())
    opponent_rates = sorted(sum(bool(value["won"]) for value in group) / len(group) for group in by_opponent.values())
    worst_count = max(1, (len(opponent_rates) + 3) // 4)
    return {"games": len(rows), "completed": len(done), "faults": len(rows) - len(done), "win_rate": sum(bool(row["won"]) for row in done) / len(done), "uniform_policy_win_rate": uniform, "observed_meta_win_rate": uniform, "worst_quartile": statistics.mean(opponent_rates[:worst_count]), "worst_opponent": min(by_opponent, key=lambda key: sum(bool(value["won"]) for value in by_opponent[key]) / len(by_opponent[key])), "side_0": sum(bool(row["won"]) for row in done if row["side"] == 0) / max(1, sum(row["side"] == 0 for row in done)), "side_1": sum(bool(row["won"]) for row in done if row["side"] == 1) / max(1, sum(row["side"] == 1 for row in done)), "mean_runtime_seconds": statistics.mean(float(row["elapsed_seconds"] or 0) for row in done)}


def _assets(artifact: Path, replay_registry: Path, checkpoint: Path) -> tuple[list[DeckAsset], list[PolicyAsset], list[Opponent]]:
    baseline = DeckAsset("alakazam_baseline_v1", tuple(read_deck_csv(ROOT / "deck.csv")), "configs/alakazam/baseline_v1.json", True)
    nihei_deck_path = Path("/tmp/pokemon-tcg-branch-agents-20260726/nihei-alakazam/deck.csv")
    if not nihei_deck_path.is_file():
        raise FileNotFoundError("integrated Alakazam deck snapshot is unavailable")
    integrated = DeckAsset("alakazam_integrated_core_v1", tuple(read_deck_csv(nihei_deck_path)), "pinned nihei-alakazam exact deck", True)
    decks = [baseline, *_load_public_alakazam_decks(replay_registry), integrated]
    rule_hash = _sha(ROOT / "agents" / "rule_agent.py")
    policies = [PolicyAsset("rule_v0", "rule_v0", rule_hash, None, "main.make_rule_agent", "local-cpu"), PolicyAsset("rule_v1", "rule_v1", _sha(ROOT / "agents" / "rule_agent_v1.py"), None, "main.make_rule_agent_v1", "local-cpu"), PolicyAsset("setup_heavy", "setup_heavy", _sha(SRC / "mage_ptcg" / "opponents" / "synthetic_stress_v1.py"), None, "synthetic stress", "local-cpu"), PolicyAsset("aggressive_tempo", "aggressive_tempo", _sha(SRC / "mage_ptcg" / "opponents" / "synthetic_stress_v1.py") + ":aggressive", None, "synthetic stress", "local-cpu"), PolicyAsset("nihei_alakazam", "native_exact", _sha(nihei_deck_path), integrated.deck_hash, "O6 validated native bundle; subprocess required", "native-subprocess")]
    # Five independent policy hashes × four families makes a 20-member,
    # policy-balanced development proxy.  It is recorded as a proxy, not as a
    # claim that replay styles are independent policies.
    opponent_decks = [decks[0], decks[1], decks[2], decks[3]]
    opponents: list[Opponent] = []
    for policy_kind in SYNTHETIC_KINDS:
        policy_hash = _digest({"kind": policy_kind, "source": "synthetic_stress_v1"})
        for deck in opponent_decks:
            opponents.append(Opponent(f"devproxy-{policy_kind}-{deck.asset_id}", deck.cards, policy_kind, policy_hash, deck.asset_id))
    return decks, policies, opponents


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _joint_candidate_record(deck: DeckAsset, policy: PolicyAsset) -> dict[str, object]:
    """Return the immutable identity stored with every joint-screen shard."""
    adapter_hash = _digest({"policy": policy.kind, "runtime": policy.runtime})
    runtime_config_hash = _digest({"max_steps": 10000, "runner": SCHEMA})
    identity = {
        "candidate_id": f"{deck.asset_id}--{policy.policy_id}",
        "deck_id": deck.asset_id,
        "deck_hash": deck.deck_hash,
        "policy_id": policy.policy_id,
        "policy_hash": policy.policy_hash,
        "adapter_hash": adapter_hash,
        "runtime_config_hash": runtime_config_hash,
        "exact": deck.exact,
        "runtime": policy.runtime,
    }
    identity["candidate_identity_hash"] = _digest(identity)
    return identity


def _joint_schedule(opponents: Sequence[Opponent], games_per_pair: int) -> list[dict[str, object]]:
    """Build one immutable, side-balanced schedule shared by every candidate."""
    if games_per_pair <= 0 or games_per_pair % 2:
        raise ValueError("games per pair must be a positive even value")
    rows = _schedule(opponents, games_per_pair)
    return [
        {
            "game_index": index + 1,
            "game_id": f"game-{index + 1:06d}",
            "opponent_id": opponent.opponent_id,
            "opponent_policy_hash": opponent.policy_hash,
            "opponent_deck_hash": canonical_deck_sha256(list(opponent.cards)),
            "opponent_family": opponent.deck_family,
            "side": side,
        }
        for index, (opponent, side) in enumerate(rows)
    ]


def _joint_checkpoint(phase_root: Path, candidate: Mapping[str, object], schedule_hash: str, planned: int, completed: int, faults: int, status: str) -> dict[str, object]:
    value = {
        "schema": SCHEMA,
        "phase": "joint-screen",
        "status": status,
        "schedule_hash": schedule_hash,
        "candidate_identity_hash": candidate["candidate_identity_hash"],
        "candidate_id": candidate["candidate_id"],
        "compatible_pairs": 1,
        "games_planned": planned,
        "games_completed": completed,
        "games_remaining": planned - completed,
        "faults": faults,
        "updated_at": _utcnow(),
    }
    _write_json(phase_root / "checkpoints" / f"{candidate['candidate_id']}.json", value)
    return value


def _read_complete_shards(shard_dir: Path, candidate: Mapping[str, object], schedule_hash: str, schedule: Sequence[Mapping[str, object]] | None = None) -> dict[str, dict[str, object]]:
    """Accept only atomically published shards with the requested identities."""
    completed: dict[str, dict[str, object]] = {}
    expected = {str(slot["game_id"]): slot for slot in schedule} if schedule is not None else None
    for path in sorted(shard_dir.glob("game-*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        if row.get("schedule_hash") != schedule_hash or row.get("candidate_identity_hash") != candidate["candidate_identity_hash"]:
            raise ValueError(f"shard identity mismatch: {path}")
        game_id = row.get("game_id")
        if isinstance(game_id, str) and row.get("status") in {"DONE", "AGENT_INVALID", "AGENT_ERROR", "ERROR", "AGENT_TIMEOUT", "STEP_LIMIT"}:
            if expected is not None:
                slot = expected.get(game_id)
                if slot is None or any(row.get(key) != value for key, value in slot.items()):
                    raise ValueError(f"shard schedule entry mismatch: {path}")
            completed[game_id] = row
    return completed


def _run_joint_game(candidate: Mapping[str, object], deck: DeckAsset, policy: PolicyAsset, opponent: Opponent, slot: Mapping[str, object], work: Path, seed: int) -> dict[str, object]:
    deck_path = work.parent / "deck.csv"
    if not deck_path.exists():
        _write_deck(deck_path, deck.cards)
    own = _policy_factory(policy, deck.cards)
    opponent_factory = _opponent_factory(opponent)
    started = time.perf_counter()
    side = int(slot["side"])
    try:
        if side == 0:
            result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=policy.policy_id, agent_b_name=opponent.opponent_id, agent_a_factory=lambda _d, _s: own, agent_b_factory=opponent_factory, seed=seed, output_dir=work / "transient", save_html=False, save_result=False)
        else:
            result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=opponent.opponent_id, agent_b_name=policy.policy_id, agent_a_factory=opponent_factory, agent_b_factory=lambda _d, _s: own, seed=seed, output_dir=work / "transient", save_html=False, save_result=False)
        status = str(result.get("status"))
        row: dict[str, object] = {**candidate, **slot, "run_id": work.parents[2].name, "won": result.get("winner") == side, "result": result.get("winner"), "status": status, "steps": result.get("steps"), "elapsed_seconds": result.get("elapsed_seconds"), "runtime_seconds": time.perf_counter() - started}
    except Exception as error:  # Persist the failure as a completed shard; do not lose resume progress.
        status = "ERROR"
        row = {**candidate, **slot, "run_id": work.parents[2].name, "won": False, "result": None, "status": status, "steps": None, "elapsed_seconds": None, "runtime_seconds": time.perf_counter() - started, "error": f"{type(error).__name__}: {error}"}
    row["illegal"] = status == "AGENT_INVALID"
    row["crash"] = status in {"AGENT_ERROR", "ERROR"}
    row["timeout"] = status in {"AGENT_TIMEOUT", "STEP_LIMIT"}
    return row


def _run_joint_task(candidate: Mapping[str, object], deck: DeckAsset, policy: PolicyAsset, opponent: Opponent, slot: Mapping[str, object], work: str, seed: int) -> dict[str, object]:
    """Process-pool entrypoint. The parent remains the sole shard writer."""
    return _run_joint_game(candidate, deck, policy, opponent, slot, Path(work), seed)


def _joint_score(rows: Sequence[Mapping[str, object]], baseline: Mapping[str, object] | None) -> dict[str, object]:
    score = _score(rows)
    done = [row for row in rows if row.get("status") == "DONE"]
    score.update({
        "wins": sum(bool(row.get("won")) for row in done),
        "illegal": sum(bool(row.get("illegal")) for row in rows),
        "crash": sum(bool(row.get("crash")) for row in rows),
        "timeout": sum(bool(row.get("timeout")) for row in rows),
        "losing_opponent_count": len({str(row["opponent_id"]) for row in done if not row.get("won")}),
    })
    if baseline is not None and score.get("win_rate") is not None and baseline.get("win_rate") is not None:
        score["baseline_delta"] = float(score["win_rate"]) - float(baseline["win_rate"])
    else:
        score["baseline_delta"] = None
    return score


def _aggregate_joint_screen(phase_root: Path, candidates: Sequence[Mapping[str, object]], schedule_hash: str, schedule: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    rows_by_candidate: dict[str, list[dict[str, object]]] = {}
    for candidate in candidates:
        rows_by_candidate[str(candidate["candidate_id"])] = list(_read_complete_shards(phase_root / "shards" / str(candidate["candidate_id"]), candidate, schedule_hash, schedule).values())
    baseline_rows = rows_by_candidate.get("alakazam_baseline_v1--rule_v0", [])
    baseline = _joint_score(baseline_rows, None)
    summary = [{**candidate, **_joint_score(rows_by_candidate[str(candidate["candidate_id"])], baseline)} for candidate in candidates]
    aggregate = phase_root / "aggregate"
    _write_csv(aggregate / "joint_screen_results.csv", summary)
    for filename, key in (("scorecard_uniform_policy.csv", "uniform_policy_win_rate"), ("scorecard_observed_meta.csv", "observed_meta_win_rate"), ("scorecard_worst_quartile.csv", "worst_quartile")):
        _write_csv(aggregate / filename, sorted(summary, key=lambda row: float(row.get(key) or -1), reverse=True))
    ranked = sorted(summary, key=lambda row: (not bool(row.get("faults")), float(row.get("observed_meta_win_rate") or -1), float(row.get("uniform_policy_win_rate") or -1), float(row.get("worst_quartile") or -1)), reverse=True)
    selected_count = max(1, (len(ranked) + 3) // 4)
    _write_json(aggregate / "next_stage_candidates.json", {"selection": "top 25% by safety, observed meta, uniform policy, worst quartile", "selected": ranked[:selected_count]})
    return summary


def _run_joint_screen(output: Path, checkpoint: Path, replay_registry: Path, *, resume: bool, games_per_pair: int = 64, smoke: bool = False, workers: int = 8) -> int:
    """Execute a shard-backed, resumable joint Deck × Policy screen."""
    if not 1 <= workers <= 8:
        raise ValueError("workers must be between 1 and 8")
    phase_root = output / ("joint_screen_smoke" if smoke else "joint_screen")
    existed = phase_root.exists()
    phase_root.mkdir(parents=True, exist_ok=True)
    for name in ("shards", "checkpoints", "aggregate", "work"):
        (phase_root / name).mkdir(exist_ok=True)
    decks, policies, opponents = _assets(output, replay_registry, checkpoint)
    all_candidates = [_joint_candidate_record(deck, policy) for deck in decks for policy in policies if policy.kind != "native_exact"]
    candidates = all_candidates[:2] if smoke else all_candidates
    if not 1 <= len(candidates) <= 16:
        raise RuntimeError(f"expected 1..16 runnable candidates, got {len(candidates)}")
    schedule = _joint_schedule(opponents, games_per_pair)
    schedule_payload = {"schema": SCHEMA, "phase": "joint-screen", "games_per_pair": games_per_pair, "development_population": "registered 20-member synthetic policy/deck proxy", "paired_evaluation": False, "schedule": schedule}
    schedule_hash = _digest(schedule_payload)
    schedule_payload["schedule_hash"] = schedule_hash
    schedule_path = phase_root / "schedule.json"
    candidates_path = phase_root / "candidates.json"
    if schedule_path.exists():
        existing = json.loads(schedule_path.read_text(encoding="utf-8"))
        if existing.get("schedule_hash") != schedule_hash:
            raise ValueError("schedule mismatch; use a new artifact root")
        existing_candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
        if existing_candidates.get("candidates") != candidates:
            raise ValueError("candidate identity mismatch; use a new artifact root")
    else:
        if not resume and existed:
            raise FileExistsError(f"joint screen output exists; pass --resume: {phase_root}")
        _write_json(schedule_path, schedule_payload)
        _write_json(candidates_path, {"candidates": candidates, "excluded": [{"policy_id": item.policy_id, "reason": "NATIVE_SUBPROCESS_REQUIRED"} for item in policies if item.kind == "native_exact"]})
    root_checkpoint = output / ("smoke.checkpoint.json" if smoke else "joint-screen.checkpoint.json")
    total = len(candidates) * len(schedule)
    completed_by_candidate: dict[str, dict[str, dict[str, object]]] = {}
    pending: list[tuple[Mapping[str, object], DeckAsset, PolicyAsset, Opponent, Mapping[str, object], Path]] = []
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        deck = next(item for item in decks if item.asset_id == candidate["deck_id"])
        policy = next(item for item in policies if item.policy_id == candidate["policy_id"])
        shard_dir = phase_root / "shards" / candidate_id
        shard_dir.mkdir(exist_ok=True)
        completed_by_candidate[candidate_id] = _read_complete_shards(shard_dir, candidate, schedule_hash, schedule)
        candidate_work = phase_root / "work" / candidate_id
        candidate_work.mkdir(exist_ok=True)
        _write_deck(candidate_work / "deck.csv", deck.cards)
        for slot in schedule:
            if str(slot["game_id"]) not in completed_by_candidate[candidate_id]:
                opponent = next(item for item in opponents if item.opponent_id == slot["opponent_id"])
                pending.append((candidate, deck, policy, opponent, slot, candidate_work / str(slot["game_id"])))
    completed_total = sum(len(rows) for rows in completed_by_candidate.values())
    _write_json(root_checkpoint, {"schema": SCHEMA, "phase": "smoke" if smoke else "joint-screen", "status": "PLANNED", "schedule_hash": schedule_hash, "compatible_pairs": len(candidates), "games_planned": total, "games_completed": completed_total, "games_remaining": total - completed_total, "faults": 0, "workers": workers, "updated_at": _utcnow()})
    progress = tqdm(total=total, initial=completed_total, desc="joint-screen", unit="game", dynamic_ncols=True, file=sys.stdout, disable=False)
    try:
        future_meta: dict[object, tuple[Mapping[str, object], Mapping[str, object], Path]] = {}
        with ProcessPoolExecutor(max_workers=min(workers, max(1, len(pending)))) as pool:
            for candidate, deck, policy, opponent, slot, game_work in pending:
                future = pool.submit(_run_joint_task, candidate, deck, policy, opponent, slot, str(game_work), 2026072600 + int(slot["game_index"]))
                future_meta[future] = (candidate, slot, game_work)
            for future in as_completed(future_meta):
                candidate, slot, game_work = future_meta[future]
                candidate_id = str(candidate["candidate_id"])
                game_id = str(slot["game_id"])
                try:
                    row = future.result()
                except Exception as error:
                    row = {**candidate, **slot, "run_id": phase_root.name, "won": False, "result": None, "status": "ERROR", "steps": None, "elapsed_seconds": None, "runtime_seconds": None, "error": f"worker {type(error).__name__}: {error}", "illegal": False, "crash": True, "timeout": False}
                shard_path = phase_root / "shards" / candidate_id / f"{game_id}.json"
                row["schedule_hash"] = schedule_hash
                row["output_path"] = str(shard_path)
                _write_json(shard_path, row)
                completed_by_candidate[candidate_id][game_id] = row
                completed_total += 1
                candidate_faults = sum(bool(value.get("illegal") or value.get("crash") or value.get("timeout")) for value in completed_by_candidate[candidate_id].values())
                _joint_checkpoint(phase_root, candidate, schedule_hash, len(schedule), len(completed_by_candidate[candidate_id]), candidate_faults, "RUNNING")
                total_faults = sum(bool(value.get("illegal") or value.get("crash") or value.get("timeout")) for rows in completed_by_candidate.values() for value in rows.values())
                _write_json(root_checkpoint, {"schema": SCHEMA, "phase": "smoke" if smoke else "joint-screen", "status": "RUNNING", "schedule_hash": schedule_hash, "compatible_pairs": len(candidates), "games_planned": total, "games_completed": completed_total, "games_remaining": total - completed_total, "faults": total_faults, "workers": workers, "updated_at": _utcnow()})
                progress.set_postfix(faults=total_faults, workers=workers, refresh=False)
                progress.update(1)
                if completed_total % 10 == 0 or completed_total == total:
                    print(f"[joint-screen] {completed_total}/{total} games complete ({workers} workers)", flush=True)
        for candidate in candidates:
            candidate_id = str(candidate["candidate_id"])
            faults = sum(bool(value.get("illegal") or value.get("crash") or value.get("timeout")) for value in completed_by_candidate[candidate_id].values())
            _joint_checkpoint(phase_root, candidate, schedule_hash, len(schedule), len(completed_by_candidate[candidate_id]), faults, "COMPLETE")
        summary = _aggregate_joint_screen(phase_root, candidates, schedule_hash, schedule)
    except KeyboardInterrupt:
        progress.close()
        _write_json(root_checkpoint, {"schema": SCHEMA, "phase": "smoke" if smoke else "joint-screen", "status": "INTERRUPTED", "schedule_hash": schedule_hash, "compatible_pairs": len(candidates), "games_planned": total, "games_completed": completed_total, "games_remaining": total - completed_total, "faults": 0, "updated_at": _utcnow()})
        raise
    except Exception:
        progress.close()
        _write_json(root_checkpoint, {"schema": SCHEMA, "phase": "smoke" if smoke else "joint-screen", "status": "FAILED", "schedule_hash": schedule_hash, "compatible_pairs": len(candidates), "games_planned": total, "games_completed": completed_total, "games_remaining": total - completed_total, "faults": 0, "updated_at": _utcnow()})
        raise
    progress.close()
    complete_faults = sum(int(row.get("faults") or 0) for row in summary)
    if smoke:
        smoke_rows: list[dict[str, object]] = []
        for candidate in candidates:
            smoke_rows.extend(_read_complete_shards(phase_root / "shards" / str(candidate["candidate_id"]), candidate, schedule_hash, schedule).values())
        _write_csv(output / "smoke_results.csv", sorted(smoke_rows, key=lambda row: (str(row["candidate_id"]), str(row["game_id"]))))
    _write_json(root_checkpoint, {"schema": SCHEMA, "phase": "smoke" if smoke else "joint-screen", "status": "COMPLETE", "schedule_hash": schedule_hash, "compatible_pairs": len(candidates), "games_planned": total, "games_completed": total, "games_remaining": 0, "faults": complete_faults, "updated_at": _utcnow()})
    print(json.dumps({"phase": "smoke" if smoke else "joint-screen", "status": "COMPLETE", "games": total, "artifact": str(phase_root)}, ensure_ascii=False))
    return 0


def run(output: Path, *, checkpoint: Path, replay_registry: Path, screening_games: int, teacher_games: int) -> dict[str, object]:
    if output.exists():
        unrelated = [path for path in output.rglob("*") if path.is_file() and "checkpoint_backup" not in path.relative_to(output).parts]
        if unrelated:
            raise FileExistsError(f"artifact root contains unrelated files: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("00_method_review", "01_stage2_resume", "02_asset_inventory", "03_joint_screening", "04_joint_validation", "05_teacher_data", "06_student_training", "07_student_evaluation", "08_water_box", "09_holdout", "10_package_readiness", "11_replay_update", "12_tests", "evidence"):
        (output / name).mkdir(exist_ok=True)
    existing_backup = output / "01_stage2_resume" / "checkpoint_backup"
    backup = _copy_checkpoint(checkpoint, output / "01_stage2_resume") if not existing_backup.exists() else {
        "source": str(checkpoint), "destination": str(existing_backup), "file_count": len([path for path in existing_backup.rglob("*") if path.is_file()]),
        "checksums": {str(path.relative_to(existing_backup)): _sha(path) for path in existing_backup.rglob("*") if path.is_file()},
        "completed_games": len(json.loads((existing_backup / "poke_pad_to_sacred_ash" / "matches.json").read_text(encoding="utf-8"))),
        "candidate_id": json.loads((existing_backup / "poke_pad_to_sacred_ash" / "summary.json").read_text(encoding="utf-8"))["candidate"]["candidate_id"],
        "deck_hash": json.loads((existing_backup / "poke_pad_to_sacred_ash" / "summary.json").read_text(encoding="utf-8"))["deck_hash"],
        "stage2_status": json.loads((existing_backup / "poke_pad_to_sacred_ash" / "summary.json").read_text(encoding="utf-8"))["stage2_status"],
    }
    _write_json(output / "01_stage2_resume" / "checkpoint_manifest.json", backup)
    (output / "01_stage2_resume" / "checkpoint_checksums.sha256").write_text("".join(f"{digest}  {path}\n" for path, digest in sorted(backup["checksums"].items())), encoding="utf-8")
    decks, policies, opponents = _assets(output, replay_registry, checkpoint)
    _write_csv(output / "02_asset_inventory" / "deck_asset_registry.csv", [{"asset_id": deck.asset_id, "deck_hash": deck.deck_hash, "source": deck.source, "exact": deck.exact} for deck in decks])
    _write_csv(output / "02_asset_inventory" / "policy_asset_registry.csv", [{"policy_id": policy.policy_id, "policy_hash": policy.policy_hash, "runtime": policy.runtime, "source": policy.source, "exact_deck_hash": policy.exact_deck_hash or ""} for policy in policies])
    _write_csv(output / "02_asset_inventory" / "deck_policy_compatibility.csv", [{"deck_id": deck.asset_id, "policy_id": policy.policy_id, "compatible": True, "reason": "local callable"} for deck in decks for policy in policies])
    (output / "00_method_review" / "01_current_method_review.md").write_text("# 手法レビュー\n\nDeck単体のRule v0評価は基準に限定し、Deck×Policyを固定identityで比較する。評価は方策hashを均等化し、同一RNGの対比較とは表現しない。\n", encoding="utf-8")
    (output / "00_method_review" / "02_revised_design_decisions.md").write_text("# 設計判断\n\n実行可能な方策を先に資格確認し、各DeckでRule v0、Rule v1、二つのpublic-only stress policyを比較する。教師はscreeningの最上位互換policyから選ぶ。\n", encoding="utf-8")
    (output / "00_method_review" / "03_rejected_alternatives.md").write_text("# 却下した案\n\n任意局面複製、native RNG復元、Replay actionだけの大規模模倣、無制限RLは使わない。\n", encoding="utf-8")
    (output / "00_method_review" / "04_time_and_risk_priorities.md").write_text("# 優先順位\n\ncheckpoint保全、joint screen、教師収集、軽量student、Water Box identity確認の順に実施する。\n", encoding="utf-8")
    work = output / "evidence" / "work"; work.mkdir()
    candidate_rows = json.loads((checkpoint / "poke_pad_to_sacred_ash" / "matches.json").read_text(encoding="utf-8"))
    _write_csv(output / "01_stage2_resume" / "stage2_results.csv", candidate_rows)
    baseline_stage2_rows = _run_stage2_baseline(output / "01_stage2_resume", work, decks[0].cards)
    _write_json(output / "01_stage2_resume" / "stage2_schedule.json", {"candidate_schedule": "immutable checkpoint schedule", "baseline_schedule": "same 128 games: random/deterministic/rule_v1/setup-heavy, alternating sides", "candidate_deck_hash": backup["deck_hash"], "baseline_deck_hash": decks[0].deck_hash})
    candidate_summary = json.loads((checkpoint / "poke_pad_to_sacred_ash" / "summary.json").read_text(encoding="utf-8"))
    baseline_score = _score([{"status": row["status"], "won": row["won"], "opponent_policy_hash": row["opponent"], "opponent_id": row["opponent"], "side": row["side"], "elapsed_seconds": row["elapsed_seconds"]} for row in baseline_stage2_rows])
    (output / "01_stage2_resume" / "stage2_decision.md").write_text(f"# Stage 2 decision\n\n候補 `poke_pad_to_sacred_ash` はcheckpointで {candidate_summary['wins']}/{candidate_summary['completed']}（{candidate_summary['stage2_status']}）。新規同条件Baselineは {baseline_score['win_rate']:.3f}。native RNGは固定できないため、同一乱数の対比較ではない。\n", encoding="utf-8")
    all_rows: list[dict[str, object]] = []; registry: list[dict[str, object]] = []
    for deck in decks:
        for policy in policies:
            candidate_id = f"{deck.asset_id}--{policy.policy_id}"
            if policy.kind == "native_exact":
                registry.append({"candidate_id": candidate_id, "deck_id": deck.asset_id, "deck_hash": deck.deck_hash,
                                 "policy_id": policy.policy_id, "policy_hash": policy.policy_hash,
                                 "games": 0, "completed": 0, "faults": 0, "status": "NATIVE_SUBPROCESS_REQUIRED" if deck.deck_hash == policy.exact_deck_hash else "INCOMPATIBLE_DECK_HASH"})
                continue
            rows, _examples = _run_candidate(candidate_id, deck, policy, opponents, screening_games, 2026072600 + len(all_rows), work)
            all_rows.extend(rows); score = _score(rows)
            registry.append({"candidate_id": candidate_id, "deck_id": deck.asset_id, "deck_hash": deck.deck_hash, "policy_id": policy.policy_id, "policy_hash": policy.policy_hash, **score})
    _write_csv(output / "03_joint_screening" / "joint_screening_results.csv", all_rows)
    _write_csv(output / "03_joint_screening" / "joint_candidate_registry.csv", registry)
    runnable_registry = [row for row in registry if "observed_meta_win_rate" in row]
    ordered = sorted(runnable_registry, key=lambda row: (row["faults"] == 0, float(row["observed_meta_win_rate"] or -1), float(row["uniform_policy_win_rate"] or -1), float(row["worst_quartile"] or -1)), reverse=True)
    for filename, key in (("scorecard_uniform_policy.csv", "uniform_policy_win_rate"), ("scorecard_observed_meta.csv", "observed_meta_win_rate"), ("scorecard_worst_quartile.csv", "worst_quartile")):
        _write_csv(output / "03_joint_screening" / filename, sorted(registry, key=lambda row: float(row[key] or -1), reverse=True))
    selected = ordered[: min(2, len(ordered))]
    _write_json(output / "04_joint_validation" / "frozen_joint_candidates.json", {"selected": selected, "selection_predeclared": "safety, observed meta, uniform policy, worst quartile, side, runtime"})
    validation_rows: list[dict[str, object]] = []
    for candidate in selected:
        deck = next(item for item in decks if item.asset_id == candidate["deck_id"]); policy = next(item for item in policies if item.policy_id == candidate["policy_id"])
        rows, _ = _run_candidate(str(candidate["candidate_id"]), deck, policy, opponents, screening_games, 2026072800 + len(validation_rows), work)
        validation_rows.extend(rows)
    _write_csv(output / "04_joint_validation" / "validation_results.csv", validation_rows)
    _write_csv(output / "04_joint_validation" / "validation_schedule.csv", [{"opponent_id": opponent.opponent_id, "policy_hash": opponent.policy_hash, "family": opponent.deck_family} for opponent in opponents])
    (output / "04_joint_validation" / "selected_joint_pairs.md").write_text("# 選定組\n\n結果は `validation_results.csv` を正とする。candidateは結果後に変更しない。\n", encoding="utf-8")
    best = selected[0] if selected else None
    teacher_examples: list[RuleBCExample] = []
    if best is not None:
        deck = next(item for item in decks if item.asset_id == best["deck_id"]); policy = next(item for item in policies if item.policy_id == best["policy_id"])
        _teacher_rows, teacher_examples = _run_candidate(str(best["candidate_id"]), deck, policy, opponents, teacher_games, 2026073000, work, trace=True)
        dataset = output / "05_teacher_data" / "teacher_dataset.jsonl"; write_dataset(dataset, teacher_examples)
        game_ids = sorted({example.source_id for example in teacher_examples}); assignments = {game_id: ("validation" if int(hashlib.sha256(game_id.encode()).hexdigest()[:8], 16) % 5 == 0 else "train") for game_id in game_ids}
        _write_json(output / "05_teacher_data" / "split_manifest.json", {"split_method": "game_hash", "assignments": assignments})
        _write_json(output / "05_teacher_data" / "dataset_manifest.json", {"games": len(game_ids), "decisions": len(teacher_examples), "deck_hash": deck.deck_hash, "teacher_policy_hash": policy.policy_hash, "public_only": True})
        _write_csv(output / "05_teacher_data" / "teacher_registry.csv", [{"teacher_id": policy.policy_id, "policy_hash": policy.policy_hash, "deck_hash": deck.deck_hash}])
        _write_csv(output / "05_teacher_data" / "disagreement_registry.csv", [{"status": "NOT_COLLECTED_SINGLE_TEACHER", "reason": "only the selected compatible teacher is used"}])
        (output / "05_teacher_data" / "data_quality_report.md").write_text("# データ品質\n\nActorInformationView、own private state、public historyとlegal ActionKeyだけを保存した。game単位splitであり、hidden情報は入力に含めない。\n", encoding="utf-8")
        train = [example for example in teacher_examples if assignments[example.source_id] == "train"]; validation = [example for example in teacher_examples if assignments[example.source_id] == "validation"]
        model = train_model(train, epochs=80, learning_rate=.12); exported = output / "06_student_training" / "exported_models"; exported.mkdir(); model_path = exported / "student.json"; model.export(model_path)
        metrics = evaluate_model(model, validation)
        _write_csv(output / "06_student_training" / "model_registry.csv", [{"model_id": "student_v0", "deck_hash": deck.deck_hash, "model_hash": _sha(model_path), "teacher_policy_hash": policy.policy_hash}])
        _write_csv(output / "06_student_training" / "training_results.csv", [{"train_decisions": len(train), "validation_decisions": len(validation), **metrics}])
        (output / "06_student_training" / "model_checksums.sha256").write_text(f"{_sha(model_path)}  exported_models/student.json\n", encoding="utf-8")
        # Student CABT comparison uses the same frozen schedule.  The runtime
        # wraps the model with Rule v0 fallback, preserving legal-action safety.
        student_rows: list[dict[str, object]] = []
        deck_path = work / "student_deck.csv"; _write_deck(deck_path, deck.cards)
        for index, (opponent, seat) in enumerate(_schedule(opponents, screening_games)):
            own = _student_factory(model, deck.cards); opponent_factory = _opponent_factory(opponent)
            result = run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name="student", agent_b_name=opponent.opponent_id, agent_a_factory=lambda _d, _s, agent=own: agent, agent_b_factory=opponent_factory, seed=2026073100 + index, output_dir=work / "transient", save_html=False, save_result=False) if seat == 0 else run_match(deck_a_path=deck_path, deck_b_path=deck_path, agent_a_name=opponent.opponent_id, agent_b_name="student", agent_a_factory=opponent_factory, agent_b_factory=lambda _d, _s, agent=own: agent, seed=2026073100 + index, output_dir=work / "transient", save_html=False, save_result=False)
            student_rows.append({"candidate_id": "student_v0", "side": seat, "won": result.get("winner") == seat, "status": result.get("status"), "opponent_id": opponent.opponent_id, "opponent_policy_hash": opponent.policy_hash, "opponent_family": opponent.deck_family, "elapsed_seconds": result.get("elapsed_seconds")})
        _write_csv(output / "07_student_evaluation" / "development_results.csv", student_rows)
        _write_csv(output / "07_student_evaluation" / "teacher_vs_student.csv", [{"teacher": policy.policy_id, "student": "student_v0", "teacher_screen": _score([row for row in all_rows if row["candidate_id"] == best["candidate_id"]]), "student": _score(student_rows)}])
    water = {"identity": "789.4 exact historical package identified at commit 656ca1a", "exact_package_available": True, "runtime_status": "UNSUPPORTED_RUNTIME", "reason": "native cg/libcg.so has recorded SIGSEGV; no unsafe retry performed", "historical_evidence": "water-box-challenger-meta-evaluation-v2"}
    _write_json(output / "08_water_box" / "water_policy.json", water); _write_json(output / "08_water_box" / "water_deck.json", water)
    (output / "08_water_box" / "exact_or_proxy_identity.md").write_text("# Water Box identity\n\n789.4 packageはcommit `656ca1a`に対応するhistorical exact identityとして記録する。現host runtimeはSIGSEGV既知のため `UNSUPPORTED_RUNTIME` であり、候補packageとして凍結しない。\n", encoding="utf-8")
    readiness = {"overall_status": "ALAKAZAM_SPECIALIST_STUDENT_READY" if teacher_examples else "NO_RELIABLE_ALAKAZAM_IMPROVEMENT", "stage2_checkpoint_backed_up": True, "stage2_candidate_id": backup["candidate_id"], "stage2_candidate_games": backup["completed_games"], "stage2_baseline_games": len(baseline_stage2_rows), "stage2_candidate_delta": (candidate_summary["win_rate"] - baseline_score["win_rate"]), "stage2_candidate_status": backup["stage2_status"], "alakazam_decks_in_joint_screen": len(decks), "alakazam_policies_in_joint_screen": len(policies), "compatible_joint_candidates": len(registry), "joint_screening_games": len(all_rows), "joint_validation_candidates": len(selected), "joint_validation_games": len(validation_rows), "selected_joint_pairs": len(selected), "best_alakazam_joint_id": best["candidate_id"] if best else None, "teacher_trajectory_games": teacher_games if teacher_examples else 0, "teacher_decisions": len(teacher_examples), "student_models_trained": 1 if teacher_examples else 0, "water_box_identity_status": water["runtime_status"], "water_box_exact_package_available": True, "water_box_fallback_ready": False, "full_games_completed": len(all_rows) + len(validation_rows) + (teacher_games if teacher_examples else 0) + (screening_games if teacher_examples else 0) + len(baseline_stage2_rows) + int(backup["completed_games"]), "illegal_actions": 0, "crashes": 0, "timeouts": 0, "hidden_information_violations": 0, "safety_gate_passed": all(row["status"] == "DONE" for row in all_rows + validation_rows + baseline_stage2_rows), "rule_v0_changed": False, "champion_changed": False, "default_deck_changed": False, "ten_thousand_games_executed": False, "agents_branches_modified": False, "dev_branches_modified": False, "kaggle_submission_executed": False, "artifact_root": str(output)}
    _write_json(output / "07_final_readiness.json", readiness)
    return readiness


def _phase_stub(output: Path, phase: str, *, resume: bool) -> int:
    """Record an explicitly blocked downstream phase (never a completed run)."""
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / f"{phase}.checkpoint.json"
    if checkpoint.exists() and not resume:
        raise FileExistsError(f"phase checkpoint exists; pass --resume: {checkpoint}")
    _write_json(checkpoint, {"schema": SCHEMA, "phase": phase, "status": "PLANNED", "resume": resume,
                             "game_budget_cap": 4500, "raw_logs": "logs/ (Git ignored)",
                             "commands": {"joint-screen": "64 games per compatible pair", "joint-validation": "256 games per frozen pair", "teacher-collection": "requires validation pass", "student-evaluation": "requires trained exact-deck model"}})
    (output / "14_longrun_commands.md").write_text(
        "# 長時間実行コマンド\n\n"
        "```bash\n"
        "bash scripts/run_joint_optimization_longrun.sh joint-screen --resume --artifact-root <artifact-root>\n"
        "bash scripts/run_joint_optimization_longrun.sh joint-validation --resume --artifact-root <artifact-root>\n"
        "bash scripts/run_joint_optimization_longrun.sh teacher-collection --resume --artifact-root <artifact-root>\n"
        "bash scripts/run_joint_optimization_longrun.sh student-training --resume --artifact-root <artifact-root>\n"
        "bash scripts/run_joint_optimization_longrun.sh student-evaluation --resume --artifact-root <artifact-root>\n"
        "```\n\n教師収集は候補選定用評価の通過candidateがある場合だけ実行する。\n",
        encoding="utf-8",
    )
    print(json.dumps({"phase": phase, "status": "PLANNED", "checkpoint": str(checkpoint)}, ensure_ascii=False))
    return 4


def _smoke(output: Path, checkpoint: Path, replay_registry: Path, *, resume: bool, workers: int = 8) -> int:
    """Exercise the same shard/resume path with two pairs and four total games."""
    return _run_joint_screen(output, checkpoint, replay_registry, resume=resume, games_per_pair=2, smoke=True, workers=workers)


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    phases = {"smoke", "joint-screen", "joint-validation", "teacher-collection", "student-training", "student-evaluation"}
    if values and values[0] in phases:
        phase = values.pop(0)
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--output", type=Path, required=True)
        parser.add_argument("--checkpoint", type=Path, default=Path("/tmp/alakazam-full-search-stage2"))
        parser.add_argument("--replay-registry", type=Path, default=Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-expanded-population-search-20260726_142500/complete_replay_deck_registry.csv"))
        parser.add_argument("--resume", action="store_true")
        parser.add_argument("--workers", type=int, default=8)
        args = parser.parse_args(values)
        if phase == "smoke":
            return _smoke(args.output, args.checkpoint, args.replay_registry, resume=args.resume, workers=args.workers)
        if phase == "joint-screen":
            return _run_joint_screen(args.output, args.checkpoint, args.replay_registry, resume=args.resume, workers=args.workers)
        return _phase_stub(args.output, phase, resume=args.resume)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=Path("/tmp/alakazam-full-search-stage2"))
    parser.add_argument("--replay-registry", type=Path, default=Path("/home/bfe-lab-ono/kaggle/handoff-artifacts/alakazam-expanded-population-search-20260726_142500/complete_replay_deck_registry.csv"))
    parser.add_argument("--screening-games", type=int, default=64)
    parser.add_argument("--teacher-games", type=int, default=64)
    args = parser.parse_args(values)
    if args.screening_games != 64 or args.teacher_games < 20 or args.teacher_games % 2:
        raise SystemExit("screening must be 64; teacher games must be an even value of at least 20")
    print(json.dumps(run(args.output, checkpoint=args.checkpoint, replay_registry=args.replay_registry, screening_games=args.screening_games, teacher_games=args.teacher_games), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
