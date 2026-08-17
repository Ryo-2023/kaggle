"""Capture a privacy-safe JSONL trace of official cabt agent decisions.

Runs the official cabt environment directly (not scripts/test_sim.py's
run_match, which does not expose per-agent wrapping) and reuses the existing
main.py agents and deck loader. Game rules are never reproduced here.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

SRC_ROOT = REPOSITORY_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from main import (  # noqa: E402
    Agent,
    Deck,
    DeckValidationError,
    make_deterministic_agent,
    make_random_agent,
    make_rule_agent,
    make_rule_agent_v1,
    read_deck_csv,
)
from mage_ptcg.observability.cabt_trace import TraceWriter, make_traced_agent  # noqa: E402


class TraceDependencyError(RuntimeError):
    """Raised when the official Kaggle cabt runtime is unavailable."""


class TraceExecutionError(RuntimeError):
    """Raised when a traced episode does not complete successfully."""


class TraceOutputExistsError(RuntimeError):
    """Raised when the output path already exists and --overwrite was not given."""


# Hidden prefix (dot-file) so a leftover temporary trace from a hard process
# kill is never mistaken for a completed destination.
TEMP_FILE_PREFIX = ".cabt-trace-tmp-"
TEMP_FILE_SUFFIX = ".jsonl.part"


def _fsync_file(path: Path) -> None:
    """Force the OS to flush a closed file's contents to disk."""
    file_descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(file_descriptor)
    finally:
        os.close(file_descriptor)


def _git_revision() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value if len(value) == 40 else None


def _write_trace_manifest(
    *,
    path: Path,
    trace_path: Path,
    matches: int,
    base_seed: int,
    agent_a_name: str,
    agent_b_name: str,
) -> dict[str, object]:
    """Persist actual-runtime provenance separately from public decision JSONL."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        environment_version = version("kaggle-environments")
    except PackageNotFoundError:
        environment_version = None
    config = {
        "agent_a": agent_a_name,
        "agent_b": agent_b_name,
        "base_seed": base_seed,
        "matches": matches,
    }
    encoded_config = json.dumps(config, sort_keys=True, separators=(",", ":"))
    manifest = {
        "schema_version": "c5-actual-cabt-trace-manifest-v1",
        "actual": True,
        "environment_loader": "kaggle_environments.make",
        "environment_name": "cabt",
        "environment_version": environment_version,
        "commit": _git_revision(),
        "config": config,
        "config_hash": hashlib.sha256(encoded_config.encode("utf-8")).hexdigest(),
        "trace_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
    }
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return manifest


def _load_make() -> Callable[..., Any]:
    try:
        from kaggle_environments import make
    except ModuleNotFoundError as exc:
        raise TraceDependencyError(
            "kaggle-environments with the cabt plugin is required; "
            "install requirements.txt (kaggle-environments==1.32.0)"
        ) from exc
    return make


def _build_agent(name: str, deck: Deck, seed: int) -> Agent:
    if name == "random":
        return make_random_agent(deck=deck, seed=seed)
    if name == "deterministic":
        return make_deterministic_agent(deck=deck)
    if name == "rule":
        return make_rule_agent(deck=deck, seed=seed)
    if name == "rule_v1":
        return make_rule_agent_v1(deck=deck, seed=seed)
    raise ValueError(f"unknown agent: {name}")


def _episode_seat_assignment(
    episode_index: int,
    *,
    agent_a_name: str,
    agent_b_name: str,
    deck_a: Deck,
    deck_b: Deck,
) -> list[tuple[str, Deck]]:
    """Return [(agent_name, deck), ...] indexed by seat (0, 1).

    Seats alternate every episode by default: agent_a plays seat 0 on even
    episode indices and seat 1 on odd episode indices.
    """
    seats = [(agent_a_name, deck_a), (agent_b_name, deck_b)]
    if episode_index % 2 == 1:
        seats.reverse()
    return seats


def _state_status(state: Any) -> Any:
    if isinstance(state, Mapping):
        return state.get("status")
    return getattr(state, "status", None)


def run_trace(
    *,
    deck_a_path: str | Path,
    deck_b_path: str | Path,
    agent_a_name: str,
    agent_b_name: str,
    matches: int,
    base_seed: int,
    output_path: str | Path,
    overwrite: bool = False,
    manifest_path: str | Path | None = None,
    make_environment: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run ``matches`` official cabt episodes, writing one JSONL trace record
    per agent decision to a hidden temporary file in ``output_path``'s
    directory, then publishing it to ``output_path`` with a single atomic
    same-filesystem replace only after every episode has completed
    successfully.

    ``output_path`` itself is never modified, truncated, or deleted before
    that final publish step, so any handled failure (invalid deck, missing
    dependency, environment/agent error, a non-DONE episode) always leaves a
    preexisting destination byte-for-byte unchanged, and never leaves a
    partial trace under the requested final name.
    """
    if isinstance(matches, bool) or not isinstance(matches, int) or matches <= 0:
        raise ValueError("matches must be a positive integer")

    destination = Path(output_path)
    if destination.exists() and not overwrite:
        raise TraceOutputExistsError(
            f"output already exists: {destination} (pass --overwrite to replace it)"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)

    deck_a = read_deck_csv(deck_a_path)
    deck_b = read_deck_csv(deck_b_path)
    make = make_environment or _load_make()

    temp_descriptor, temp_name = tempfile.mkstemp(
        prefix=TEMP_FILE_PREFIX, suffix=TEMP_FILE_SUFFIX, dir=str(destination.parent)
    )
    os.close(temp_descriptor)
    temp_path = Path(temp_name)

    try:
        episode_results: list[dict[str, Any]] = []
        with TraceWriter(temp_path) as writer:
            for episode_index in range(matches):
                seats = _episode_seat_assignment(
                    episode_index,
                    agent_a_name=agent_a_name,
                    agent_b_name=agent_b_name,
                    deck_a=deck_a,
                    deck_b=deck_b,
                )
                decision_counter = itertools.count()
                seat_agents = []
                for seat_index, (agent_name, deck) in enumerate(seats):
                    seed = base_seed + episode_index * 2 + seat_index
                    raw_agent = _build_agent(agent_name, deck, seed)
                    traced = make_traced_agent(
                        raw_agent,
                        seat=seat_index,
                        episode_index=episode_index,
                        writer=writer,
                        decision_counter=decision_counter,
                    )
                    seat_agents.append(traced)

                env = make("cabt", configuration={"decks": [seats[0][1], seats[1][1]]})
                env.run(seat_agents)

                statuses = [_state_status(state) for state in env.state]
                if not env.done or statuses != ["DONE", "DONE"]:
                    raise TraceExecutionError(
                        f"episode {episode_index} did not complete successfully: statuses={statuses}"
                    )
                episode_results.append({"episode_index": episode_index, "statuses": statuses})

        _fsync_file(temp_path)

        # Re-check immediately before publication: a same-name file created
        # by another process while this run was in progress must not be
        # casually replaced when overwrite was not requested.
        if destination.exists() and not overwrite:
            raise TraceOutputExistsError(
                f"output already exists: {destination} (pass --overwrite to replace it)"
            )

        os.replace(temp_path, destination)
    except BaseException:
        if temp_path.exists():
            temp_path.unlink()
        raise

    result: dict[str, Any] = {
        "matches": matches,
        "output_path": str(destination.resolve()),
        "episodes": episode_results,
    }
    if manifest_path is not None:
        manifest_destination = Path(manifest_path)
        manifest_destination.parent.mkdir(parents=True, exist_ok=True)
        result["manifest"] = _write_trace_manifest(
            path=manifest_destination,
            trace_path=destination,
            matches=matches,
            base_seed=base_seed,
            agent_a_name=agent_a_name,
            agent_b_name=agent_b_name,
        )
    return result


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-a", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument("--deck-b", type=Path, default=REPOSITORY_ROOT / "deck.csv")
    parser.add_argument(
        "--agent-a", choices=("random", "deterministic", "rule", "rule_v1"), default="random"
    )
    parser.add_argument(
        "--agent-b",
        choices=("random", "deterministic", "rule", "rule_v1"),
        default="deterministic",
    )
    parser.add_argument("--matches", type=_positive_int, default=1)
    parser.add_argument("--base-seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_trace(
            deck_a_path=args.deck_a,
            deck_b_path=args.deck_b,
            agent_a_name=args.agent_a,
            agent_b_name=args.agent_b,
            matches=args.matches,
            base_seed=args.base_seed,
            output_path=args.output,
            overwrite=args.overwrite,
            manifest_path=args.manifest_output,
        )
    except (
        DeckValidationError,
        TraceDependencyError,
        TraceExecutionError,
        TraceOutputExistsError,
        ValueError,
    ) as exc:
        print(f"cabt trace failed: {exc}", file=sys.stderr)
        return 2

    print(f"wrote trace for {result['matches']} match(es) to {result['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
