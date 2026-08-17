"""Self-owned Rule v0 public-outcome smoke and common24 screen.

The runner is deliberately research-only.  It collects real Rule-v0 games
against the registered local-evaluation pool, persists only the audited public
trajectory projection, derives a bounded *action-type outcome diagnostic*, and
can then screen a native-first overlay against the exact common24 universe.
``main.py``, ``agents/rule_agent.py`` and the production evaluator are not
modified by this file.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from main import make_rule_agent  # noqa: E402
from mage_ptcg.meta_specialist.opponent_pool_v1 import (  # noqa: E402
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)
from mage_ptcg.meta_specialist.self_owned_public_outcome_v1 import (  # noqa: E402
    build_bounded_action_overlay_v1,
    build_overlay_agent_v1,
    capture_rule_v0_rollout_v1,
    load_overlay_table_v1,
    save_overlay_table_v1,
)
from scripts.parallel_cabt_evaluator_v1 import (  # noqa: E402
    EvaluationGameV1,
    _game_from_payload,
    evaluator_implementation_sha256_v1,
    run_parallel_cabt_evaluation,
)
from scripts.run_performance_first_arena_v1 import (  # noqa: E402
    ROOT_DECK,
    root_policy_sha256,
)
from scripts.test_sim import run_match  # noqa: E402


SCREEN_SCHEMA_V1 = "self-owned-rule-v0-public-outcome-screen-v1"
RUNNER_REF_V1 = "scripts.run_self_owned_rule_v0_public_outcome_screen_v1:run_screen_game_v1"
DEFAULT_CONFIG = _ROOT / "configs/meta_specialist/performance_first_broad_pool_v1.json"
DEFAULT_POOL_ROOT = _ROOT / "opponents"
_SHA_HEX = frozenset("0123456789abcdef")


class SelfOwnedScreenError(ValueError):
    """Raised when a research-only screen artifact is not closed."""


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256(path: Path | str) -> str:
    return _sha256_bytes(Path(path).read_bytes())


def _require_sha(value: object, name: str) -> str:
    if type(value) is not str or len(value) != 64 or any(c not in _SHA_HEX for c in value):
        raise SelfOwnedScreenError(f"{name} must be lowercase SHA-256")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _load_common24(path: Path) -> tuple[str, ...]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SelfOwnedScreenError(f"cannot load broad config: {path}") from exc
    ids = payload.get("opponent_ids") if isinstance(payload, Mapping) else None
    if not isinstance(ids, list) or len(ids) != 24 or len(set(ids)) != 24:
        raise SelfOwnedScreenError("broad config must contain exactly 24 unique opponent_ids")
    if any(type(item) is not str or not item for item in ids):
        raise SelfOwnedScreenError("broad config opponent ids are malformed")
    return tuple(ids)


def _ensure_fresh(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise FileExistsError(f"output is not empty: {path}")
    path.mkdir(parents=True, exist_ok=True)


def build_public_outcome_records_v1(rollouts: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Convert capture summaries to the table builder's public digest schema."""
    records: list[dict[str, object]] = []
    for rollout in rollouts:
        if not isinstance(rollout, Mapping):
            raise SelfOwnedScreenError("rollout summary must be an object")
        rows = rollout.get("action_rows")
        if not isinstance(rows, list):
            raise SelfOwnedScreenError("rollout summary action_rows must be a list")
        actions: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise SelfOwnedScreenError("action row must be an object")
            # The table builder accepts only the public digest tuple.  Do not
            # carry outcome/opponent metadata into each action row, and never
            # accept a private field from a captured payload.
            actions.append({
                "step_index": row.get("step_index"),
                "seat": row.get("seat"),
                "action_type": row.get("action_type"),
                "state_digest": row.get("state_digest"),
                "action_digest": row.get("action_digest"),
            })
        records.append({
            "game_id": rollout.get("game_id"),
            "candidate_side": rollout.get("subject_side"),
            "outcome": rollout.get("outcome"),
            "opponent_id": rollout.get("opponent_id"),
            "subject_policy_sha256": rollout.get("subject_policy_sha256"),
            "subject_deck_sha256": rollout.get("subject_deck_sha256"),
            "actions": actions,
        })
    return records


def candidate_policy_sha256_v1(root_policy_sha256: str, table_sha256: str) -> str:
    root = _require_sha(root_policy_sha256, "root_policy_sha256")
    table = _require_sha(table_sha256, "table_sha256")
    return _sha256_bytes(b"self-owned-public-action-overlay-v1\0" + root.encode() + b"\0" + table.encode())


def build_screen_games_v1(
    *,
    arm: str,
    table_path: Path,
    opponent_ids: Sequence[str],
    output_dir: Path,
    games_per_seat: int = 2,
    base_seed: int = 14_900_000,
    max_steps: int = 2_000,
) -> tuple[EvaluationGameV1, ...]:
    """Build the same common24 cells for native baseline or overlay candidate."""
    if arm not in {"baseline", "candidate"}:
        raise SelfOwnedScreenError("arm must be baseline or candidate")
    if type(games_per_seat) is not int or games_per_seat <= 0:
        raise SelfOwnedScreenError("games_per_seat must be positive")
    table = load_overlay_table_v1(table_path)
    root_sha = root_policy_sha256()
    deck_sha = _sha256(ROOT_DECK)
    table_sha = _sha256(table_path)
    policy_sha = root_sha if arm == "baseline" else candidate_policy_sha256_v1(root_sha, table_sha)
    pool = load_opponent_pool_v1(DEFAULT_POOL_ROOT)
    telemetry_root = output_dir / "telemetry" / arm
    games: list[EvaluationGameV1] = []
    ordinal = 0
    for opponent_id in opponent_ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(ROOT_DECK))
        opponent_deck_sha = _sha256(opponent.deck_csv_path)
        opponent_policy_sha = _sha256(opponent.policy_path) if opponent.policy_path else "0" * 64
        for seat in (0, 1):
            for repetition in range(games_per_seat):
                game_id = f"common24-{opponent_id}-seat{seat}-g{repetition:04d}"
                games.append(EvaluationGameV1(
                    game_id=game_id,
                    block_id=f"{SCREEN_SCHEMA_V1}-{arm}-96",
                    policy_id=f"self-owned-rule-v0-{arm}",
                    policy_sha256=policy_sha,
                    deck_id="root-deck-current-worktree",
                    deck_sha256=deck_sha,
                    opponent_id=opponent_id,
                    opponent_identity={
                        "policy_sha256": opponent_policy_sha,
                        "deck_sha256": opponent_deck_sha,
                        "usage_boundary": opponent.usage_boundary,
                        "source": opponent.source,
                    },
                    opponent_deck_sha256=opponent_deck_sha,
                    seat=seat,
                    seed=base_seed + ordinal,
                    max_steps=max_steps,
                    subject_deck_path=str(ROOT_DECK),
                    opponent_deck_path=str(opponent.deck_csv_path),
                    policy_agent_name=f"self-owned-rule-v0-{arm}",
                    opponent_agent_name=opponent_id,
                    runner_ref=RUNNER_REF_V1,
                    metadata={
                        "schema_version": SCREEN_SCHEMA_V1,
                        "arm": arm,
                        "table_path": str(table_path.resolve()),
                        "table_sha256": table_sha,
                        "table_config_sha256": table["config_sha256"],
                        "baseline_policy_sha256": root_sha,
                        "candidate_policy_sha256": policy_sha,
                        "telemetry_root": str(telemetry_root.resolve()),
                        "source_pool_manifest_sha256": _sha256(DEFAULT_POOL_ROOT / "pool_manifest.json"),
                        "authority": {
                            "training_authority": False,
                            "promotion_authority": False,
                            "submission_authority": False,
                        },
                        "research_only": True,
                        "private_state_used": False,
                        "teacher_labels_used": False,
                        "repetition": repetition,
                    },
                ))
                ordinal += 1
    if not games:
        raise SelfOwnedScreenError("no screen games were built")
    return tuple(games)


def run_screen_game_v1(payload: Mapping[str, object]) -> Mapping[str, object]:
    """Spawn-safe worker: native first, bounded overlay only on public options."""
    game = _game_from_payload(payload)
    metadata = game.metadata
    arm = metadata.get("arm")
    if arm not in {"baseline", "candidate"}:
        raise SelfOwnedScreenError("worker arm is malformed")
    pool = load_opponent_pool_v1(DEFAULT_POOL_ROOT)
    opponent = resolve_opponent_v1(pool, game.opponent_id, subject_deck_csv_path=game.subject_deck_path)
    opponent_factory = build_opponent_agent_factory_v1(opponent)
    holder: dict[str, object] = {}
    if arm == "candidate":
        table_path = Path(str(metadata["table_path"]))
        table = load_overlay_table_v1(table_path)
        if _sha256(table_path) != metadata.get("table_sha256"):
            raise SelfOwnedScreenError("worker table SHA mismatch")
        expected_policy = candidate_policy_sha256_v1(str(metadata["baseline_policy_sha256"]), str(metadata["table_sha256"]))
        if expected_policy != game.policy_sha256 or expected_policy != metadata.get("candidate_policy_sha256"):
            raise SelfOwnedScreenError("worker candidate policy identity mismatch")

        def subject_factory(deck: object, _seed: int):
            agent = build_overlay_agent_v1(
                deck=deck, table=table,
                baseline_policy_sha256=str(metadata["baseline_policy_sha256"]),
                candidate_config_sha256=str(metadata["table_config_sha256"]),
                deck_sha256=game.deck_sha256,
                seed=_seed,
            )
            holder["agent"] = agent
            return agent
    else:
        def subject_factory(deck: object, seed: int):
            agent = make_rule_agent(deck=deck, seed=seed)
            holder["agent"] = agent
            return agent

    subject_first = game.seat == 0
    result = run_match(
        deck_a_path=game.subject_deck_path if subject_first else opponent.deck_csv_path,
        deck_b_path=opponent.deck_csv_path if subject_first else game.subject_deck_path,
        agent_a_name=game.policy_agent_name if subject_first else game.opponent_id,
        agent_b_name=game.opponent_id if subject_first else game.policy_agent_name,
        seed=game.seed,
        max_steps=game.max_steps,
        output_dir=str(_ROOT / "runs" / "self-owned-public-screen-worker" / str(arm) / game.game_id),
        save_html=False,
        save_result=False,
        agent_a_factory=subject_factory if subject_first else opponent_factory,
        agent_b_factory=opponent_factory if subject_first else subject_factory,
    )
    telemetry = getattr(holder.get("agent"), "telemetry", None)
    if callable(telemetry):
        telemetry_path = Path(str(metadata["telemetry_root"])) / f"{game.game_id}.json"
        telemetry_path.parent.mkdir(parents=True, exist_ok=True)
        telemetry_path.write_text(json.dumps(telemetry(), sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def _summary_from_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    outcomes = Counter(str(row.get("outcome", "fault")) for row in rows)
    requested = len(rows)
    wins, draws, losses, faults = (outcomes.get(k, 0) for k in ("win", "draw", "loss", "fault"))
    return {
        "requested_games": requested,
        "completed_games": wins + draws + losses,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "faults": faults,
        "fault_rate": faults / requested if requested else None,
        "score_rate": (wins + 0.5 * draws) / requested if requested else None,
        "outcome_distribution": dict(sorted(outcomes.items())),
    }


def run_rollout_smoke_v1(
    *,
    output_dir: Path,
    opponent_id: str = "tomatomato_archaludon",
    games: int = 4,
    base_seed: int = 14_900_000,
    max_steps: int = 2_000,
) -> dict[str, object]:
    """Collect real Rule-v0 public rows and materialize one bounded table."""
    if games <= 0:
        raise SelfOwnedScreenError("games must be positive")
    _ensure_fresh(output_dir)
    pool = load_opponent_pool_v1(DEFAULT_POOL_ROOT)
    opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(ROOT_DECK))
    policy_sha = root_policy_sha256()
    deck_sha = _sha256(ROOT_DECK)
    evaluator_sha = evaluator_implementation_sha256_v1()
    evidence_root = output_dir / "public-evidence"
    rollouts: list[dict[str, object]] = []
    for ordinal in range(games):
        subject_side = ordinal % 2
        game_id = f"rollout-smoke-{opponent_id}-seat{subject_side}-g{ordinal:04d}"
        rollout = capture_rule_v0_rollout_v1(
            game_id=game_id,
            subject_deck_path=ROOT_DECK,
            opponent_deck_path=opponent.deck_csv_path,
            subject_factory=lambda deck, seed: make_rule_agent(deck=deck, seed=seed),
            opponent_factory=build_opponent_agent_factory_v1(opponent),
            subject_side=subject_side,
            seed=base_seed + ordinal,
            opponent_id=opponent_id,
            subject_policy_sha256=policy_sha,
            subject_deck_sha256=deck_sha,
            evaluator_sha256=evaluator_sha,
            evidence_root=evidence_root,
            max_steps=max_steps,
        )
        rollouts.append(rollout)
    records = build_public_outcome_records_v1(rollouts)
    table = build_bounded_action_overlay_v1(
        records, source_policy_sha256=policy_sha, source_deck_sha256=deck_sha,
        max_abs_delta=120.0, minimum_observations=1,
    )
    table_path = output_dir / "action-outcome-table.json"
    table_file_sha = save_overlay_table_v1(table_path, table)
    manifest = {
        "schema_version": SCREEN_SCHEMA_V1,
        "mode": "real-rollout-smoke",
        "rollout_games": games,
        "opponent_id": opponent_id,
        "source_policy_sha256": policy_sha,
        "source_deck_sha256": deck_sha,
        "evaluator_implementation_sha256": evaluator_sha,
        "pool_manifest_sha256": _sha256(DEFAULT_POOL_ROOT / "pool_manifest.json"),
        "table_sha256": table_file_sha,
        "table_path": str(table_path.resolve()),
        "records": records,
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
        "research_only": True,
        "private_state_used": False,
        "teacher_labels_used": False,
        "ready_for_evaluation": False,
        "note": "bounded action-type overlay/public outcome diagnostic; not full state-action advantage",
    }
    (output_dir / "rollout-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    summary = {
        "schema_version": SCREEN_SCHEMA_V1,
        "mode": "real-rollout-smoke",
        "rollouts": [_public_rollout_summary(row) for row in rollouts],
        "table_path": str(table_path.resolve()),
        "table_sha256": table_file_sha,
        "table_action_types": table["action_types"],
        "ready_for_evaluation": False,
    }
    (output_dir / "rollout-summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def run_common24_rollout_v1(
    *,
    output_dir: Path,
    broad_config: Path = DEFAULT_CONFIG,
    games_per_seat: int = 2,
    base_seed: int = 14_900_000,
    max_steps: int = 2_000,
) -> dict[str, object]:
    """Collect the real common24 META_TRAIN public rollout source.

    This is intentionally separate from the four-game Tomato smoke: only this
    schedule may produce a table accepted by ``run_common24_screen_v1``.
    """
    if type(games_per_seat) is not int or games_per_seat <= 0:
        raise SelfOwnedScreenError("games_per_seat must be positive")
    _ensure_fresh(output_dir)
    ids = _load_common24(broad_config)
    pool = load_opponent_pool_v1(DEFAULT_POOL_ROOT)
    policy_sha = root_policy_sha256()
    deck_sha = _sha256(ROOT_DECK)
    evaluator_sha = evaluator_implementation_sha256_v1()
    pool_sha = _sha256(DEFAULT_POOL_ROOT / "pool_manifest.json")
    evidence_root = output_dir / "public-evidence"
    rollouts: list[dict[str, object]] = []
    ordinal = 0
    for opponent_id in ids:
        opponent = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path=str(ROOT_DECK))
        opponent_factory = build_opponent_agent_factory_v1(opponent)
        for subject_side in (0, 1):
            for repetition in range(games_per_seat):
                game_id = f"common24-rollout-{opponent_id}-seat{subject_side}-g{repetition:04d}"
                rollout = capture_rule_v0_rollout_v1(
                    game_id=game_id,
                    subject_deck_path=ROOT_DECK,
                    opponent_deck_path=opponent.deck_csv_path,
                    subject_factory=lambda deck, seed: make_rule_agent(deck=deck, seed=seed),
                    opponent_factory=opponent_factory,
                    subject_side=subject_side,
                    seed=base_seed + ordinal,
                    opponent_id=opponent_id,
                    subject_policy_sha256=policy_sha,
                    subject_deck_sha256=deck_sha,
                    evaluator_sha256=evaluator_sha,
                    evidence_root=evidence_root,
                    max_steps=max_steps,
                )
                rollouts.append(rollout)
                ordinal += 1
    records = build_public_outcome_records_v1(rollouts)
    records_path = output_dir / "public-outcome-records.json"
    records_raw = _canonical(records) + b"\n"
    records_path.write_bytes(records_raw)
    records_sha = _sha256(records_path)
    statuses = Counter(str(item.get("status")) for item in rollouts)
    outcomes = Counter(str(item.get("outcome")) for item in rollouts)
    source_manifest = {
        "schema_version": "self-owned-public-rollout-source-v1",
        "common24_ids": list(ids),
        "games_per_cell": games_per_seat,
        "base_seed": base_seed,
        "requested_games": len(ids) * 2 * games_per_seat,
        "completed_games": len(rollouts),
        "status_distribution": dict(sorted(statuses.items())),
        "outcome_distribution": dict(sorted(outcomes.items())),
        "record_count": len(records),
        "records_sha256": records_sha,
        "evaluator_sha256": evaluator_sha,
        "pool_manifest_sha256": pool_sha,
        "source_policy_sha256": policy_sha,
        "source_deck_sha256": deck_sha,
        "engine_seed_support": "ENGINE_SEED_UNSUPPORTED",
        "authority": {
            "training_authority": False,
            "promotion_authority": False,
            "submission_authority": False,
        },
        "research_only": True,
        "private_state_used": False,
        "teacher_labels_used": False,
    }
    source_manifest_path = output_dir / "source-manifest.json"
    source_manifest_path.write_bytes(_canonical(source_manifest) + b"\n")
    source_manifest_sha = _sha256(source_manifest_path)
    provenance = {
        "schema_version": "self-owned-public-rollout-source-v1",
        "common24_ids": list(ids),
        "games_per_cell": games_per_seat,
        "base_seed": base_seed,
        "evaluator_sha256": evaluator_sha,
        "rollout_manifest_sha256": source_manifest_sha,
        "record_count": len(records),
        "engine_seed_support": "ENGINE_SEED_UNSUPPORTED",
        "authority": dict(source_manifest["authority"]),
    }
    table = build_bounded_action_overlay_v1(
        records,
        source_policy_sha256=policy_sha,
        source_deck_sha256=deck_sha,
        max_abs_delta=120.0,
        minimum_observations=2,
        source_provenance=provenance,
    )
    table_path = output_dir / "action-outcome-table.json"
    table_sha = save_overlay_table_v1(table_path, table)
    eligible = [
        float(row["delta"])
        for row in table["action_types"].values()
        if row.get("eligible")
    ]
    signs = {1 if value > 0 else -1 if value < 0 else 0 for value in eligible}
    usable_signal = len(eligible) >= 2 and len(signs) > 1
    summary = {
        "schema_version": SCREEN_SCHEMA_V1,
        "mode": "real-common24-rollout",
        "common24_ids": list(ids),
        "games_per_cell": games_per_seat,
        "requested_games": len(ids) * 2 * games_per_seat,
        "completed_games": len(rollouts),
        "faults": sum(1 for item in rollouts if item.get("status") != "DONE"),
        "outcome_distribution": dict(sorted(outcomes.items())),
        "records_path": str(records_path.resolve()),
        "records_sha256": records_sha,
        "source_manifest_path": str(source_manifest_path.resolve()),
        "source_manifest_sha256": source_manifest_sha,
        "table_path": str(table_path.resolve()),
        "table_sha256": table_sha,
        "table_action_types": table["action_types"],
        "engine_seed_support": "ENGINE_SEED_UNSUPPORTED",
        "usable_signal": usable_signal,
        "ready_for_screen": usable_signal and len(rollouts) == len(ids) * 2 * games_per_seat and not any(item.get("status") != "DONE" for item in rollouts),
        "authority": dict(source_manifest["authority"]),
        "research_only": True,
        "note": "real self-owned Rule-v0 public rollouts; table is bounded action-type outcome diagnostic, not full state-action advantage",
    }
    (output_dir / "rollout-summary.json").write_bytes(_canonical(summary) + b"\n")
    return summary


def _public_rollout_summary(row: Mapping[str, object]) -> dict[str, object]:
    """Drop action rows from the compact summary; rows remain in manifest records."""
    return {key: row.get(key) for key in ("game_id", "status", "outcome", "winner", "subject_side", "opponent_id", "public_event_count", "public_action_count", "engine_seed_support")}


def run_common24_screen_v1(
    *,
    output_dir: Path,
    table_path: Path,
    broad_config: Path = DEFAULT_CONFIG,
    games_per_seat: int = 2,
    base_seed: int = 14_900_000,
    workers: int = 12,
    worker_recycle_games: int = 16,
) -> dict[str, object]:
    """Evaluate baseline and candidate on the common24 universe."""
    _ensure_fresh(output_dir)
    table_path = table_path.resolve()
    table = load_overlay_table_v1(table_path)
    ids = _load_common24(broad_config)
    provenance = table.get("source_provenance")
    if not isinstance(provenance, Mapping):
        raise SelfOwnedScreenError("screen requires a common24 rollout table; diagnostic smoke table is not admissible")
    if tuple(provenance.get("common24_ids", ())) != ids:
        raise SelfOwnedScreenError("table common24 IDs do not match screen config")
    if provenance.get("games_per_cell") != games_per_seat:
        raise SelfOwnedScreenError("table games_per_cell does not match screen games_per_seat")
    if provenance.get("evaluator_sha256") != evaluator_implementation_sha256_v1():
        raise SelfOwnedScreenError("table evaluator SHA does not match current evaluator")
    if provenance.get("engine_seed_support") != "ENGINE_SEED_UNSUPPORTED":
        raise SelfOwnedScreenError("table seed capability is not the verified common24 value")
    if table.get("source_policy_sha256") != root_policy_sha256() or table.get("source_deck_sha256") != _sha256(ROOT_DECK):
        raise SelfOwnedScreenError("table source pair does not match current root policy/deck")
    eligible_deltas = [
        float(row["delta"])
        for row in table["action_types"].values()
        if isinstance(row, Mapping) and row.get("eligible")
    ]
    signs = {1 if value > 0 else -1 if value < 0 else 0 for value in eligible_deltas}
    if len(eligible_deltas) < 2 or len(signs) <= 1:
        raise SelfOwnedScreenError("common24 table has no usable action-type signal; candidate screen is not admissible")
    arms: dict[str, object] = {}
    for arm in ("baseline", "candidate"):
        games = build_screen_games_v1(
            arm=arm, table_path=table_path, opponent_ids=ids, output_dir=output_dir,
            games_per_seat=games_per_seat, base_seed=base_seed,
        )
        arm_root = output_dir / arm
        result = run_parallel_cabt_evaluation(
            games, output_dir=arm_root / "evaluation", max_workers=workers,
            worker_recycle_games=worker_recycle_games,
        )
        arms[arm] = {"summary": result["summary"], "rows": result["rows"]}
    baseline_rows = {str(row["game_id"]): row for row in arms["baseline"]["rows"]}
    candidate_rows = {str(row["game_id"]): row for row in arms["candidate"]["rows"]}
    paired = {"loss_to_win": 0, "win_to_loss": 0, "same": 0}
    for game_id, candidate_row in candidate_rows.items():
        baseline_row = baseline_rows.get(game_id)
        if not baseline_row:
            continue
        pair = (baseline_row.get("outcome"), candidate_row.get("outcome"))
        if pair == ("loss", "win"):
            paired["loss_to_win"] += 1
        elif pair == ("win", "loss"):
            paired["win_to_loss"] += 1
        else:
            paired["same"] += 1
    telemetry_files = sorted((output_dir / "telemetry" / "candidate").glob("*.json"))
    telemetry = {"games": len(telemetry_files), "override_attempts": 0, "override_applied": 0, "eligible": 0, "native_calls": 0}
    for path in telemetry_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for key in ("override_attempts", "override_applied", "eligible", "native_calls"):
            telemetry[key] += int(payload.get(key, 0))
    summary = {
        "schema_version": SCREEN_SCHEMA_V1,
        "mode": "common24-baseline-vs-overlay",
        "common24_ids": list(ids),
        "games_per_seat": games_per_seat,
        "requested_games_per_arm": len(ids) * 2 * games_per_seat,
        "table_path": str(table_path),
        "table_sha256": _sha256(table_path),
        "table_config_sha256": table["config_sha256"],
        "source_policy_sha256": root_policy_sha256(),
        "source_deck_sha256": _sha256(ROOT_DECK),
        "evaluator_implementation_sha256": evaluator_implementation_sha256_v1(),
        "pool_manifest_sha256": _sha256(DEFAULT_POOL_ROOT / "pool_manifest.json"),
        "arms": {arm: value["summary"] for arm, value in arms.items()},
        "paired_cells": paired,
        "candidate_telemetry": telemetry,
        "fault_free": all(value["summary"].get("faults", 0) == 0 for value in arms.values()),
        "authority": {"training_authority": False, "promotion_authority": False, "submission_authority": False},
        "research_only": True,
        "ready_for_longrun": False,
    }
    (output_dir / "screen-summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "screen96"), default="smoke")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--table", type=Path)
    parser.add_argument("--broad-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--opponent-id", default="tomatomato_archaludon")
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--games-per-seat", type=int, default=2)
    parser.add_argument("--base-seed", type=int, default=14_900_000)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--worker-recycle-games", type=int, default=16)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.mode == "smoke":
        result = run_rollout_smoke_v1(
            output_dir=args.output, opponent_id=args.opponent_id,
            games=args.games, base_seed=args.base_seed,
        )
    else:
        if args.table is None:
            raise SystemExit("--table is required for --mode screen96")
        result = run_common24_screen_v1(
            output_dir=args.output, table_path=args.table,
            broad_config=args.broad_config, games_per_seat=args.games_per_seat,
            base_seed=args.base_seed, workers=args.workers,
            worker_recycle_games=args.worker_recycle_games,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "RUNNER_REF_V1", "SCREEN_SCHEMA_V1", "SelfOwnedScreenError",
    "build_public_outcome_records_v1", "build_screen_games_v1",
    "candidate_policy_sha256_v1", "main", "run_common24_screen_v1",
    "run_rollout_smoke_v1", "run_screen_game_v1",
]
