"""Real cabt adapters for the O2 sidecar.

This module only adapts existing factories and ``scripts.test_sim.run_match``;
it does not emulate cabt or silently substitute a fixture backend.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Mapping

from main import make_random_agent, make_rule_agent, make_student_agent, read_deck_csv
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256

from .core import DeckEntry, MatchSpec, O2ContractError, OpponentEntry


_ALLOWED_FACTORIES = {
    "rule_v0": "main.make_rule_agent",
    "random_legal": "main.make_random_agent",
    "student_v0": "main.make_student_agent",
}


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _factory_source(repository_root: Path, kind: str) -> Path:
    mapping = {
        "rule_v0": repository_root / "agents" / "rule_agent.py",
        "random_legal": repository_root / "main.py",
        "student_v0": repository_root / "src" / "mage_ptcg" / "student" / "runtime.py",
    }
    try:
        return mapping[kind]
    except KeyError as exc:
        raise O2ContractError(f"unsupported real agent kind {kind!r}") from exc


def resolve_real_agent(entry: OpponentEntry, *, repository_root: str | Path) -> Callable[[list[int], int], object]:
    """Resolve one allowlisted real factory and validate its implementation."""
    root = Path(repository_root)
    expected = _ALLOWED_FACTORIES.get(entry.agent_kind)
    if expected is None or entry.agent_factory != expected:
        raise O2ContractError(f"factory is not allowlisted for {entry.opponent_id!r}")
    actual_hash = _sha256_file(_factory_source(root, entry.agent_kind))
    if actual_hash != entry.implementation_hash:
        raise O2ContractError(f"implementation hash mismatch for {entry.opponent_id!r}")
    if entry.agent_kind == "rule_v0":
        return lambda deck, seed: make_rule_agent(deck=deck, seed=seed)
    if entry.agent_kind == "random_legal":
        return lambda deck, seed: make_random_agent(deck=deck, seed=seed)
    artifact = entry.artifact_reference
    if not artifact:
        raise O2ContractError("Student artifact_reference is required")
    model = Path(artifact)
    if not model.is_file():
        raise O2ContractError("Student artifact_reference does not exist")
    return lambda deck, _seed: make_student_agent(deck=deck, model_path=model)


def resolve_real_deck(entry: DeckEntry, *, repository_root: str | Path) -> tuple[Path, str]:
    """Verify the registered deck against the existing loader and content hash."""
    root = Path(repository_root)
    prefix = "repository:"
    if not entry.source.startswith(prefix):
        raise O2ContractError("real deck source must be a repository-relative path")
    relative = Path(entry.source[len(prefix):])
    if relative.is_absolute() or ".." in relative.parts:
        raise O2ContractError("deck source path is unsafe")
    path = root / relative
    deck = read_deck_csv(path)
    # ``deck_hash`` is canonicalized by the pool; this repeat check catches a
    # changed source file without making its location part of the identity.
    from .core import deck_content_hash
    if deck_content_hash(deck) != entry.deck_hash:
        raise O2ContractError(f"deck source content does not match {entry.deck_id!r}")
    return path, canonical_deck_sha256(deck)


def cabt_backend(
    *, decks: Mapping[str, DeckEntry], opponents: Mapping[str, OpponentEntry], repository_root: str | Path,
    output_root: str | Path, max_steps: int = 10_000,
) -> Callable[[MatchSpec], Mapping[str, object]]:
    """Return a real-cabt backend callback suitable for ``execute_match_plan``."""
    root, destination = Path(repository_root), Path(output_root)
    try:
        from scripts.cabt_capability import diagnose_cabt_capability
        capability = dict(diagnose_cabt_capability())
    except (ImportError, OSError) as exc:
        raise O2ContractError("cabt capability probe failed") from exc
    if capability.get("status") != "READY":
        raise O2ContractError("cabt_capability_unavailable")
    from scripts.test_sim import run_match

    def execute(spec: MatchSpec) -> Mapping[str, object]:
        try:
            deck_a, deck_a_hash = resolve_real_deck(decks[spec.player_a_deck], repository_root=root)
            deck_b, deck_b_hash = resolve_real_deck(decks[spec.player_b_deck], repository_root=root)
            factory_a = resolve_real_agent(opponents[spec.player_a_agent], repository_root=root)
            factory_b = resolve_real_agent(opponents[spec.player_b_agent], repository_root=root)
        except KeyError as exc:
            raise O2ContractError(f"plan refers to unregistered entry {exc.args[0]!r}") from exc
        raw = run_match(
            deck_a_path=deck_a, deck_b_path=deck_b, agent_a_name=opponents[spec.player_a_agent].agent_kind,
            agent_b_name=opponents[spec.player_b_agent].agent_kind, seed=spec.seed, max_steps=max_steps,
            output_dir=destination / spec.match_id / "raw", save_html=True, save_result=True,
            agent_a_factory=factory_a, agent_b_factory=factory_b,
        )
        # Only copy controlled, public result fields.  Paths and raw exception
        # text stay in the raw artifact, never in the O2 normalized record.
        return {
            "status": raw.get("status"), "winner": raw.get("winner"), "elapsed_seconds": raw.get("elapsed_seconds"),
            "termination_reason": raw.get("terminal_reason"), "engine_version": "cabt",
            "engine_seed_supported": capability.get("engine_seed_supported"), "deck_a_hash": deck_a_hash,
            "deck_b_hash": deck_b_hash, "fallback_events": [],
        }
    return execute


__all__ = ["cabt_backend", "resolve_real_agent", "resolve_real_deck"]
