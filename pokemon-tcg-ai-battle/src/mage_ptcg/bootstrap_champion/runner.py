"""Execute a frozen Bootstrap schedule against the normal CABT engine."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from main import make_rule_agent, make_rule_agent_v1, read_deck_csv
from mage_ptcg.continuous_league.cabt import _submitted_snapshot_spec
from mage_ptcg.continuous_league.candidate_runtime import load_runtime_policy
from mage_ptcg.continuous_league.catalog import CatalogSnapshot
from mage_ptcg.continuous_league.contracts import (
    LeagueContractError,
    append_jsonl_once,
    atomic_write_json,
    load_json,
)
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.policy_learning.r2d3.semantic_action import encode_legal_action
from mage_ptcg.policy_learning.r2d3.semantic_state import encode_public_state
from mage_ptcg.policy_learning.submitted_runtime import SubmittedAgentWorker

from .contracts import JointCandidate
from .pipeline import load_candidates_artifact
from .teacher import BootstrapTeacherExample, outcome_weight


def _factory_for_policy(policy: Any, deck: list[int], *, game_key: str, seat: int, scratch_root: Path, workers: list[SubmittedAgentWorker]) -> Callable[[list[int], int], Any]:
    if policy.policy_kind == "rule_v0":
        return lambda cards, seed: make_rule_agent(deck=cards, seed=seed)
    if policy.policy_kind == "rule_v1":
        return lambda cards, seed: make_rule_agent_v1(deck=cards, seed=seed)
    if policy.policy_kind == "submitted_snapshot":
        spec = _submitted_snapshot_spec(Path(policy.runtime_path).resolve())
        def create(_cards: list[int], _seed: int) -> SubmittedAgentWorker:
            worker = SubmittedAgentWorker(spec, scratch_root=scratch_root)
            workers.append(worker)
            return worker
        return create
    if policy.policy_kind == "runtime_policy":
        runtime = load_runtime_policy(Path(policy.runtime_path))
        if runtime.deck != deck:
            raise LeagueContractError("Bootstrap runtime policy deck differs from candidate deck")
        return lambda _cards, _seed: runtime.create(game_id=game_key, seat=seat)
    raise LeagueContractError(f"unsupported Bootstrap policy kind: {policy.policy_kind}")


def _existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LeagueContractError(f"corrupt Bootstrap result {path}:{number}") from exc
            key = row.get("game_key")
            if not isinstance(key, str) or key in rows:
                raise LeagueContractError("duplicate or missing Bootstrap game key")
            rows[key] = row
    return rows


class _TeacherTraceAgent:
    """Record only actor-visible, exact-one-action candidate decisions."""

    def __init__(self, agent: Any, *, candidate_id: str, state_size: int, action_size: int) -> None:
        self.agent = agent
        self.candidate_id = candidate_id
        self.state_size = state_size
        self.action_size = action_size
        self.drafts: list[dict[str, Any]] = []
        self.skipped_multi_select_decisions = 0

    def __call__(self, observation: object, configuration: object = None) -> Any:
        # CABT calls agents with one observation argument.  The wrapped
        # built-in rule policies intentionally have that same one-argument
        # contract; do not inject the wrapper's optional compatibility arg.
        del configuration
        choice = self.agent(observation)
        if not isinstance(observation, Mapping) or not isinstance(observation.get("select"), Mapping):
            return choice
        select = observation["select"]
        if int(select.get("minCount", -1)) != 1 or int(select.get("maxCount", -1)) != 1:
            self.skipped_multi_select_decisions += 1
            return choice
        if not isinstance(choice, (list, tuple)) or len(choice) != 1 or type(choice[0]) is not int:
            return choice
        state = build_decision_state(dict(observation))
        matching = [action for action in state.legal_actions if action.option_index == choice[0]]
        if len(matching) != 1:
            return choice
        selected = state.legal_actions.index(matching[0])
        keys = tuple(action.action_key.digest for action in state.legal_actions)
        semantic_actions = tuple(
            tuple(
                encode_legal_action(
                    {
                        "digest": action.action_key.digest,
                        "action_type": action.action_key.selection_type,
                        "card_id": action.action_key.card_id,
                        "source_zone": action.action_key.source_entity_key,
                        "target_zone": action.action_key.target_entity_key,
                        "target_card": action.action_key.target_entity_key,
                        "amount": None,
                        "selection_order": action.option_index,
                        "phase": action.action_key.context,
                        "optional": False,
                        "semantic_role": action.action_key.semantic_operation,
                    },
                    dimension=self.action_size,
                )
            )
            for action in state.legal_actions
        )
        self.drafts.append(
            {
                "decision_index": len(self.drafts),
                "public_state": state.actor_view.public_state,
                "own_private_state": state.actor_view.own_private_state,
                "visible_history": state.actor_view.visible_history,
                "legal_action_keys": keys,
                "selected_action_key": keys[selected],
                "encoded_state": tuple(encode_public_state(state.actor_view.public_state, dimension=self.state_size)),
                "encoded_actions": semantic_actions,
                "selected_action": selected,
            }
        )
        return choice

    def complete(self, *, game_id: str, outcome: str) -> tuple[BootstrapTeacherExample, ...]:
        return tuple(
            BootstrapTeacherExample(
                game_id=game_id,
                decision_index=int(draft["decision_index"]),
                public_state=draft["public_state"],
                own_private_state=draft["own_private_state"],
                visible_history=tuple(draft["visible_history"]),
                legal_action_keys=tuple(draft["legal_action_keys"]),
                selected_action_key=str(draft["selected_action_key"]),
                outcome=outcome,
                behavior_weight=outcome_weight(outcome),
                teacher_candidate_id=self.candidate_id,
                encoded_state=tuple(draft["encoded_state"]),
                encoded_actions=tuple(draft["encoded_actions"]),
                selected_action=int(draft["selected_action"]),
            )
            for draft in self.drafts
        )


def _trace_path(root: Path, game_key: str) -> Path:
    return Path(root) / "games" / f"{game_key}.json"


def run_schedule(
    *, candidate_registry: Path, catalog: CatalogSnapshot, schedule_path: Path,
    output: Path, scratch_root: Path, max_steps: int = 10_000,
    teacher_output: Path | None = None, teacher_state_size: int = 128,
    teacher_action_size: int = 64,
) -> dict[str, Any]:
    """Resume a schedule by game key; retain faults as selection-disqualifying evidence."""

    _registry_id, candidates = load_candidates_artifact(candidate_registry)
    by_candidate = {candidate.candidate_id: candidate for candidate in candidates}
    schedule = load_json(schedule_path)
    if not isinstance(schedule, Mapping) or schedule.get("schema_version") != "bootstrap-tournament-schedule-v1":
        raise LeagueContractError("unsupported Bootstrap schedule")
    matches = [dict(item) for item in schedule.get("matches", [])]
    if not matches:
        raise LeagueContractError("Bootstrap schedule is empty")
    output = Path(output)
    existing = _existing(output)
    expected = {str(item["game_key"]): item for item in matches}
    if not set(existing).issubset(expected):
        raise LeagueContractError("Bootstrap output contains a game outside schedule")
    if teacher_output is not None:
        if teacher_state_size < 32 or teacher_action_size < 24:
            raise LeagueContractError("Bootstrap teacher encoder dimensions are too small")
        missing = [key for key in existing if not _trace_path(teacher_output, key).is_file()]
        if missing:
            raise LeagueContractError("completed Bootstrap game lacks its teacher trace")
    progress: Any | None = None
    if sys.stderr.isatty():
        try:
            from tqdm import tqdm
            progress = tqdm(total=len(matches), initial=len(existing), desc="bootstrap tournament", unit="game", dynamic_ncols=True)
        except ImportError:
            pass
    started = time.monotonic()
    faults = sum(bool(row.get("fault")) for row in existing.values())
    try:
        for match in matches:
            key = str(match["game_key"])
            if key in existing:
                continue
            candidate = by_candidate.get(str(match["candidate_id"]))
            if candidate is None:
                raise LeagueContractError("Bootstrap schedule references unknown candidate")
            opponent = catalog.get_instance(str(match["opponent_instance_id"]))
            candidate_first = match["seat"] == "subject_first"
            workers: list[SubmittedAgentWorker] = []
            tracer: _TeacherTraceAgent | None = None
            try:
                from scripts.test_sim import run_match
                candidate_deck = list(read_deck_csv(candidate.deck.snapshot_path))
                candidate_factory = _factory_for_policy(candidate.policy, candidate_deck, game_key=key, seat=0 if candidate_first else 1, scratch_root=scratch_root, workers=workers)
                if teacher_output is not None:
                    original_factory = candidate_factory

                    def candidate_factory(cards: list[int], seed: int) -> _TeacherTraceAgent:
                        nonlocal tracer
                        tracer = _TeacherTraceAgent(
                            original_factory(cards, seed),
                            candidate_id=candidate.candidate_id,
                            state_size=teacher_state_size,
                            action_size=teacher_action_size,
                        )
                        return tracer
                # CatalogEntry has the same runtime fields needed by the adapter.
                opponent_policy = type("CatalogPolicy", (), {
                    "policy_kind": opponent.policy_kind,
                    "runtime_path": opponent.runtime_path,
                })()
                opponent_deck = list(read_deck_csv(opponent.deck_path))
                opponent_factory = _factory_for_policy(opponent_policy, opponent_deck, game_key=key, seat=1 if candidate_first else 0, scratch_root=scratch_root, workers=workers)
                result = run_match(
                    deck_a_path=candidate.deck.snapshot_path if candidate_first else opponent.deck_path,
                    deck_b_path=opponent.deck_path if candidate_first else candidate.deck.snapshot_path,
                    agent_a_name="bootstrap" if candidate_first else opponent.policy_kind,
                    agent_b_name=opponent.policy_kind if candidate_first else "bootstrap",
                    agent_a_factory=candidate_factory if candidate_first else opponent_factory,
                    agent_b_factory=opponent_factory if candidate_first else candidate_factory,
                    seed=int(match["env_seed"]), max_steps=max_steps, output_dir=scratch_root / key,
                    save_html="failures", save_result=True,
                )
                side = 0 if candidate_first else 1
                winner = result.get("winner")
                if result.get("status") != "DONE":
                    raise LeagueContractError(f"CABT status {result.get('status')}")
                outcome = "win" if winner == side else "draw" if winner == 2 else "loss" if winner in (0, 1) else None
                if outcome is None:
                    raise LeagueContractError("CABT returned an invalid winner")
                row = {**match, "outcome": outcome, "duration_seconds": result.get("elapsed_seconds"), "fault": None}
            except (OSError, RuntimeError, ValueError, LeagueContractError) as exc:
                faults += 1
                row = {**match, "outcome": "loss", "duration_seconds": None, "fault": f"{type(exc).__name__}: {exc}"}
            finally:
                for worker in workers:
                    worker.close()
            if teacher_output is not None:
                trace = {
                    "schema_version": "bootstrap-teacher-trace-game-v1",
                    "game_id": key,
                    "candidate_id": candidate.candidate_id,
                    "status": "DONE" if row["fault"] is None else "FAULT",
                    "skipped_multi_select_decisions": (
                        tracer.skipped_multi_select_decisions if tracer is not None else 0
                    ),
                    "examples": (
                        [example.to_dict() for example in tracer.complete(game_id=key, outcome=str(row["outcome"]))]
                        if tracer is not None and row["fault"] is None else []
                    ),
                }
                # Trace first: after an interruption, a rerun may safely repeat
                # this deterministic game, but a completed result never exists
                # without its privacy-checked trace record.
                atomic_write_json(_trace_path(teacher_output, key), trace)
            append_jsonl_once(output, row, "game_key")
            if progress is not None:
                progress.update(1); progress.set_postfix(faults=faults, refresh=False)
    finally:
        if progress is not None:
            progress.close()
    rows = _existing(output)
    return {"schedule_id": schedule["schedule_id"], "completed_games": len(rows), "total_games": len(matches), "fault_count": sum(bool(row.get("fault")) for row in rows.values()), "elapsed_seconds": time.monotonic() - started, "results": str(output)}
