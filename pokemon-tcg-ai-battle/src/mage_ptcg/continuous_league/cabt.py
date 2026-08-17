"""公式 CABT を使う evaluation/collection 共通 match executor。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from main import make_rule_agent, make_rule_agent_v1, read_deck_csv
from mage_ptcg.policy_learning.submitted_runtime import (
    SubmittedAgentWorker,
    spec_from_manifest,
)

from .benchmark import ScheduledGame, SubjectDeck
from .candidate_runtime import RuntimePolicyFactory, load_runtime_policy
from .catalog import CatalogEntry
from .contracts import LeagueContractError, load_json


def _submitted_snapshot_spec(manifest_path: Path):
    """Resolve a portable snapshot manifest before the child changes CWD."""
    manifest = dict(load_json(manifest_path))
    snapshot_root = manifest_path.parent.resolve()
    recorded_root = Path(str(manifest["snapshot_root"]))
    recorded_deck = Path(str(manifest["deck_path"]))
    if recorded_root.is_absolute():
        if recorded_root.resolve() != snapshot_root:
            raise LeagueContractError(
                "submitted snapshot manifest path differs from its recorded root"
            )
        if not recorded_deck.is_absolute():
            raise LeagueContractError(
                "submitted snapshot manifest mixes absolute root and relative deck"
            )
        try:
            deck_relative = recorded_deck.resolve().relative_to(snapshot_root)
        except ValueError as exc:
            raise LeagueContractError(
                "submitted snapshot deck is outside its recorded root"
            ) from exc
    else:
        if recorded_deck.is_absolute():
            raise LeagueContractError(
                "submitted snapshot manifest mixes relative root and absolute deck"
            )
        try:
            deck_relative = recorded_deck.relative_to(recorded_root)
        except ValueError as exc:
            raise LeagueContractError(
                "submitted snapshot deck does not belong to its recorded root"
            ) from exc
    deck_path = (snapshot_root / deck_relative).resolve()
    if not deck_path.is_relative_to(snapshot_root) or not deck_path.is_file():
        raise LeagueContractError("submitted snapshot deck is missing from manifest root")
    manifest["snapshot_root"] = str(snapshot_root)
    manifest["deck_path"] = str(deck_path)
    return spec_from_manifest(manifest)


class CabtMatchExecutor:
    def __init__(
        self,
        *,
        runtime_policy: RuntimePolicyFactory,
        subject_decks: tuple[SubjectDeck, ...],
        output_root: Path,
        scratch_root: Path,
        max_steps: int = 10_000,
        save_failures_html: bool = True,
    ) -> None:
        self.runtime_policy = runtime_policy
        self.subject_decks = {
            deck.deck_id: deck for deck in subject_decks
        }
        self.output_root = Path(output_root)
        self.scratch_root = Path(scratch_root)
        self.max_steps = max_steps
        self.save_failures_html = save_failures_html
        self._runtime_opponents: dict[str, RuntimePolicyFactory] = {}

    def _opponent_factory(
        self,
        game: ScheduledGame,
        entry: CatalogEntry,
        workers: list[SubmittedAgentWorker],
    ) -> Callable[[list[int], int], Any]:
        if entry.policy_kind == "rule_v0":
            return lambda deck, seed: make_rule_agent(deck=deck, seed=seed)
        if entry.policy_kind == "rule_v1":
            return lambda deck, seed: make_rule_agent_v1(deck=deck, seed=seed)
        if entry.policy_kind == "submitted_snapshot":
            manifest_path = Path(entry.runtime_path)
            if manifest_path.is_dir():
                manifest_path = manifest_path / ".submitted_snapshot_manifest.json"
            spec = _submitted_snapshot_spec(manifest_path.resolve())

            def create(_deck: list[int], _seed: int) -> SubmittedAgentWorker:
                worker = SubmittedAgentWorker(spec, scratch_root=self.scratch_root)
                workers.append(worker)
                return worker

            return create
        if entry.policy_kind == "runtime_policy":
            runtime_path = Path(entry.runtime_path)
            if runtime_path.is_file():
                runtime_path = runtime_path.parent
            runtime_key = str(runtime_path.resolve())
            opponent_runtime = self._runtime_opponents.get(runtime_key)
            if opponent_runtime is None:
                opponent_runtime = load_runtime_policy(runtime_path)
                self._runtime_opponents[runtime_key] = opponent_runtime
            if opponent_runtime.runtime_policy_id != entry.policy_hash:
                raise LeagueContractError(
                    "runtime opponent policy hash differs from catalog entry"
                )
            catalog_deck = list(read_deck_csv(entry.deck_path))
            if opponent_runtime.deck != catalog_deck:
                raise LeagueContractError(
                    "runtime opponent deck differs from catalog entry"
                )
            opponent_seat = 1 if game.seat == "subject_first" else 0

            def create(_deck: list[int], _seed: int) -> Any:
                return opponent_runtime.create(
                    game_id=game.game_key,
                    seat=opponent_seat,
                )

            return create
        raise LeagueContractError(
            f"unsupported opponent runtime kind: {entry.policy_kind}"
        )

    def execute(
        self, game: ScheduledGame, entry: CatalogEntry
    ) -> tuple[dict[str, Any], Any]:
        from scripts.test_sim import run_match

        subject_deck = self.subject_decks.get(game.subject_deck_id)
        if subject_deck is None:
            raise LeagueContractError(
                f"runtime has no subject deck {game.subject_deck_id}"
            )
        deck = list(read_deck_csv(subject_deck.deck_path))
        if deck != self.runtime_policy.deck:
            raise LeagueContractError(
                "benchmark subject deck differs from RuntimePolicy deck"
            )
        candidate_policies = []
        workers: list[SubmittedAgentWorker] = []

        def candidate_factory(_deck: list[int], _seed: int) -> Any:
            policy = self.runtime_policy.create(
                game_id=game.game_key,
                seat=0 if game.seat == "subject_first" else 1,
            )
            candidate_policies.append(policy)
            return policy

        opponent_factory = self._opponent_factory(game, entry, workers)
        subject_first = game.seat == "subject_first"
        try:
            result = run_match(
                deck_a_path=(
                    subject_deck.deck_path if subject_first else entry.deck_path
                ),
                deck_b_path=(
                    entry.deck_path if subject_first else subject_deck.deck_path
                ),
                agent_a_name="runtime_policy" if subject_first else entry.policy_kind,
                agent_b_name=entry.policy_kind if subject_first else "runtime_policy",
                seed=game.env_seed,
                max_steps=self.max_steps,
                output_dir=self.output_root / game.game_key,
                save_html="failures" if self.save_failures_html else False,
                save_result=True,
                agent_a_factory=candidate_factory if subject_first else opponent_factory,
                agent_b_factory=opponent_factory if subject_first else candidate_factory,
            )
        finally:
            for worker in workers:
                worker.close()
        if result.get("status") != "DONE":
            raise LeagueContractError(
                f"CABT match failed: {result.get('status')} "
                f"{result.get('terminal_reason')}"
            )
        if len(candidate_policies) != 1:
            raise LeagueContractError("candidate runtime was not instantiated exactly once")
        candidate_side = 0 if subject_first else 1
        winner = result.get("winner")
        if winner == candidate_side:
            outcome = "win"
        elif winner == 2:
            outcome = "draw"
        elif winner in (0, 1):
            outcome = "loss"
        else:
            raise LeagueContractError(f"CABT returned invalid winner: {winner}")
        return (
            {
                "outcome": outcome,
                "duration_seconds": result.get("elapsed_seconds"),
                "steps": result.get("steps"),
                "winner": winner,
                "candidate_side": candidate_side,
            },
            candidate_policies[0],
        )

    def __call__(
        self, game: ScheduledGame, entry: CatalogEntry
    ) -> dict[str, Any]:
        result, _policy = self.execute(game, entry)
        return result
