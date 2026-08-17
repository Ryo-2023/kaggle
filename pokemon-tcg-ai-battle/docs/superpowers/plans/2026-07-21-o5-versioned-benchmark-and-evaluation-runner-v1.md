# O5 Versioned Benchmark & Evaluation Runner v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the already-canonical O5 Deck Archetype Registry / Policy Pack / Opponent Factory (`o5_registry.py`, `o5_activation.py`) with a versioned, immutable Benchmark manifest envelope, a real deterministic Adversarial/Safety opponent agent family, and a resumable multi-opponent Evaluation Runner that can execute and aggregate real cabt matches today — without fabricating the still-blocked multi-archetype population.

**Architecture:** Reuse, don't duplicate. `o5_activation.build_benchmark_manifest()` already computes the population-gated `sets`/`status`; a new `o5_benchmark.py` wraps it with the run-identifying/versioning envelope the spec requires (benchmark_id, seeds, game_count, commit, manifest_hash, ...). A new `o5_adversarial_agents.py` supplies real callables for the `safety` set's currently-unimplemented labels (`exception_agent`, `slow_agent`, `invalid_artifact`, `unknown_selection`), reusing `main.make_random_agent`/`choose_rule_indices` where possible. A new `o5_evaluation.py` is a thin multi-opponent orchestrator over the **existing**, already-tested `league.actual_runner.run_actual_league` (one `ActualLeagueConfig` per opponent member, results merged and Wilson-CI aggregated with the existing `offline_training_v1_support.statistics.wilson_score_interval`). `scripts/run_o5_benchmark.py` is the CLI. No change to `main.py`, `deck.csv`, Rule Agent v0/v1, or the Champion/default agent.

**Tech Stack:** Python 3.11, existing `mage_ptcg.competition_intelligence` package conventions (frozen dataclasses, `digest()`-based content hashing, atomic JSON I/O), `kaggle_environments==1.32.0` cabt via `scripts/test_sim.py::run_match`, pytest.

## Global Constraints

- Do not edit canonical `feature/belief-guided-search` directly; all work happens in worktree `pokemon-tcg-ai-battle-o5-opponent-benchmark-v1` / branch `feature/o5-opponent-population-benchmark-v1` (base `514a56a`, confirmed HEAD == `origin/feature/belief-guided-search`, divergence `0 0`).
- Do not fabricate Rules attestation (`VERIFIED`), Team permission manifests, or archetype/deck data. Current O5 state is genuinely `BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION` (0 active decks, 0 verified links) and stays that way in this slice — `current_meta`/archetype-`adversarial` sets remain empty and must be reported as `0 games / BLOCKED`, never padded.
- Reuse existing contracts: `o5_activation.build_benchmark_manifest`, `o5_activation.OpponentInstanceSpec`, `o5_activation.GenericArchetypeAgent`, `league.actual_runner.{ActualLeagueConfig, run_actual_league}`, `offline_training_v1_support.statistics.wilson_score_interval`, `scripts/test_sim.run_match` (via `agent_a_factory`/`agent_b_factory` injection — never re-implement environment construction).
- `main.py`, `deck.csv`, `agents/rule_agent.py`, `agents/rule_agent_v1.py`, Promotion Gate code are protected files: byte-identical before/after (verify via `git diff` on these paths).
- Champion/default stays Rule Agent v0. Promotion decision authority stays out of scope (report facts only).
- No network calls, no Kaggle CLI/API use, no `kaggle.json`/credentials access.
- New adversarial opponent agents are local test doubles only (never candidates, never submitted); they must never be reachable from `main.py`.
- All new modules follow existing `competition_intelligence` package house style: frozen `slots=True` dataclasses, `digest(..., domain=...)` content hashing, explicit `__all__`, fail-closed on malformed input (raise a typed `*Error`, never a bare `except`).
- Every new artifact-producing function must be deterministic given identical inputs (no wall-clock reads inside hashed payloads; timestamps are passed in as parameters).
- Test command: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q <paths>` (repo convention; use `env -u PYTHONPATH .venv/bin/python` for real-cabt runs per `docs/evidence/cabt-capability-recovery.md`).

---

### Task 1: Versioned Benchmark Manifest v2

**Files:**
- Create: `src/mage_ptcg/competition_intelligence/o5_benchmark.py`
- Test: `tests/competition_intelligence/test_o5_benchmark.py`

**Interfaces:**
- Consumes: `o5_activation.build_benchmark_manifest(population, *, active_exact_decks, runnable_families, verified_links) -> dict` (existing, unmodified), `o5_activation.OpponentInstanceSpec` (existing), `.canonical.digest(value, *, domain: str) -> str` (existing).
- Produces: `VersionedBenchmarkManifest` frozen dataclass with fields `schema_version, benchmark_id, benchmark_version, created_at, source_snapshot_ids, deck_registry_version, policy_pack_version, agent_family_versions, ruleset_version, cabt_version, seed_set, seat_swap_policy, game_count, logical_pair_count, time_budget_seconds, candidate_artifact_id, baseline_artifact_ids, environment, commit, status, sets, requirements, config_hash, manifest_hash`, plus `build_versioned_benchmark_manifest(...) -> VersionedBenchmarkManifest` and `O5BenchmarkError`. `.as_dict()` returns a plain-JSON-safe dict for `atomic_write_json`. Task 4 (Evaluation Runner) consumes `manifest.sets[set_name]`, `manifest.seed_set`, `manifest.seat_swap_policy`, `manifest.status`, `manifest.manifest_hash`.

- [ ] **Step 1: Write failing tests for construction, validation, and determinism**

```python
# tests/competition_intelligence/test_o5_benchmark.py
from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.o5_benchmark import (
    O5BenchmarkError, O5_BENCHMARK_MANIFEST_SCHEMA_VERSION,
    build_versioned_benchmark_manifest,
)


def _kwargs(**overrides):
    base = dict(
        benchmark_id="o5-benchmark-core-v1",
        benchmark_version="1.0.0",
        created_at="2026-07-21T00:00:00Z",
        source_snapshot_ids=("registry-snapshot-1",),
        deck_registry_version="o5-deck-archetype-registry-v1",
        policy_pack_version="o5-activation-opponent-factory-v1",
        agent_family_versions={"rule_v0": "1", "random_legal": "1"},
        ruleset_version="unknown",
        cabt_version="1.32.0",
        seed_set=(9000, 9001),
        seat_swap_policy="ALWAYS_SWAP",
        game_count=8,
        time_budget_seconds=600.0,
        candidate_artifact_id="rule_v0",
        baseline_artifact_ids=("random_legal",),
        environment="local",
        commit="0" * 40,
        active_exact_decks=0,
        runnable_families=0,
        verified_links=0,
    )
    base.update(overrides)
    return base


def test_builds_expected_schema_and_carries_blocked_status():
    manifest = build_versioned_benchmark_manifest((), **_kwargs())
    assert manifest.schema_version == O5_BENCHMARK_MANIFEST_SCHEMA_VERSION
    assert manifest.status == "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"
    assert manifest.sets["current_meta"] == ()
    assert manifest.logical_pair_count == 4
    assert manifest.manifest_hash and manifest.config_hash


def test_manifest_hash_is_deterministic_and_content_addressed():
    a = build_versioned_benchmark_manifest((), **_kwargs())
    b = build_versioned_benchmark_manifest((), **_kwargs())
    assert a.manifest_hash == b.manifest_hash
    c = build_versioned_benchmark_manifest((), **_kwargs(game_count=16))
    assert c.manifest_hash != a.manifest_hash
    assert c.config_hash != a.config_hash


def test_rejects_odd_game_count_when_seat_swap_is_always_swap():
    with pytest.raises(O5BenchmarkError, match="even"):
        build_versioned_benchmark_manifest((), **_kwargs(game_count=7))


def test_rejects_empty_seed_set():
    with pytest.raises(O5BenchmarkError, match="seed_set"):
        build_versioned_benchmark_manifest((), **_kwargs(seed_set=()))


def test_rejects_blank_benchmark_id_or_version():
    with pytest.raises(O5BenchmarkError):
        build_versioned_benchmark_manifest((), **_kwargs(benchmark_id=""))
    with pytest.raises(O5BenchmarkError):
        build_versioned_benchmark_manifest((), **_kwargs(benchmark_version=""))


def test_as_dict_round_trips_through_json(tmp_path):
    import json
    manifest = build_versioned_benchmark_manifest((), **_kwargs())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest.as_dict(), sort_keys=True), encoding="utf-8")
    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["manifest_hash"] == manifest.manifest_hash
```

- [ ] **Step 2: Run tests to verify they fail with `ModuleNotFoundError`**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_benchmark.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'mage_ptcg.competition_intelligence.o5_benchmark'`

- [ ] **Step 3: Implement `o5_benchmark.py`**

```python
"""Versioned, immutable Benchmark manifest envelope for O5.

o5_activation.build_benchmark_manifest() already decides the population-gated
sets/status; this module only adds the run-identifying, reproducibility, and
provenance envelope a *versioned* Benchmark needs (id, seeds, game budget,
commit, content-addressed hash). Definition and results stay separate: this
module builds the manifest only, o5_evaluation.py consumes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .canonical import digest
from .o5_activation import OpponentInstanceSpec, build_benchmark_manifest

O5_BENCHMARK_MANIFEST_SCHEMA_VERSION = "o5-versioned-benchmark-manifest-v1"


class O5BenchmarkError(ValueError):
    """Raised for a malformed versioned benchmark manifest input."""


def _non_blank(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise O5BenchmarkError(f"{name} must be a non-blank string")
    return value


@dataclass(frozen=True, slots=True)
class VersionedBenchmarkManifest:
    schema_version: str
    benchmark_id: str
    benchmark_version: str
    created_at: str
    source_snapshot_ids: tuple[str, ...]
    deck_registry_version: str
    policy_pack_version: str
    agent_family_versions: Mapping[str, str]
    ruleset_version: str
    cabt_version: str
    seed_set: tuple[int, ...]
    seat_swap_policy: str
    game_count: int
    logical_pair_count: int
    time_budget_seconds: float
    candidate_artifact_id: str
    baseline_artifact_ids: tuple[str, ...]
    environment: str
    commit: str
    status: str
    sets: Mapping[str, tuple[str, ...]]
    requirements: Mapping[str, int]
    config_hash: str = field(init=False)
    manifest_hash: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "config_hash", digest(self._config_payload(), domain="o5-versioned-benchmark-config"))
        object.__setattr__(self, "manifest_hash", digest(self.as_dict(), domain="o5-versioned-benchmark-manifest"))

    def _config_payload(self) -> dict[str, object]:
        return {
            "benchmark_id": self.benchmark_id, "benchmark_version": self.benchmark_version,
            "deck_registry_version": self.deck_registry_version, "policy_pack_version": self.policy_pack_version,
            "agent_family_versions": dict(sorted(self.agent_family_versions.items())),
            "ruleset_version": self.ruleset_version, "cabt_version": self.cabt_version,
            "seed_set": list(self.seed_set), "seat_swap_policy": self.seat_swap_policy,
            "game_count": self.game_count, "time_budget_seconds": self.time_budget_seconds,
            "candidate_artifact_id": self.candidate_artifact_id,
            "baseline_artifact_ids": sorted(self.baseline_artifact_ids), "environment": self.environment,
            "commit": self.commit,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version, "benchmark_id": self.benchmark_id,
            "benchmark_version": self.benchmark_version, "created_at": self.created_at,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "deck_registry_version": self.deck_registry_version, "policy_pack_version": self.policy_pack_version,
            "agent_family_versions": dict(sorted(self.agent_family_versions.items())),
            "ruleset_version": self.ruleset_version, "cabt_version": self.cabt_version,
            "seed_set": list(self.seed_set), "seat_swap_policy": self.seat_swap_policy,
            "game_count": self.game_count, "logical_pair_count": self.logical_pair_count,
            "time_budget_seconds": self.time_budget_seconds,
            "candidate_artifact_id": self.candidate_artifact_id,
            "baseline_artifact_ids": list(self.baseline_artifact_ids), "environment": self.environment,
            "commit": self.commit, "status": self.status,
            "sets": {key: list(value) for key, value in self.sets.items()},
            "requirements": dict(self.requirements), "config_hash": self.config_hash,
        }


def build_versioned_benchmark_manifest(
    population: Sequence[OpponentInstanceSpec], *, benchmark_id: str, benchmark_version: str, created_at: str,
    source_snapshot_ids: Sequence[str], deck_registry_version: str, policy_pack_version: str,
    agent_family_versions: Mapping[str, str], ruleset_version: str, cabt_version: str, seed_set: Sequence[int],
    seat_swap_policy: str, game_count: int, time_budget_seconds: float, candidate_artifact_id: str,
    baseline_artifact_ids: Sequence[str], environment: str, commit: str, active_exact_decks: int,
    runnable_families: int, verified_links: int,
) -> VersionedBenchmarkManifest:
    _non_blank(benchmark_id, "benchmark_id")
    _non_blank(benchmark_version, "benchmark_version")
    _non_blank(candidate_artifact_id, "candidate_artifact_id")
    if seat_swap_policy not in {"ALWAYS_SWAP", "NO_SWAP"}:
        raise O5BenchmarkError("seat_swap_policy must be ALWAYS_SWAP or NO_SWAP")
    if not seed_set or any(type(seed) is not int for seed in seed_set):
        raise O5BenchmarkError("seed_set must be a non-empty tuple of ints")
    if type(game_count) is not int or game_count <= 0:
        raise O5BenchmarkError("game_count must be a positive integer")
    if seat_swap_policy == "ALWAYS_SWAP" and game_count % 2:
        raise O5BenchmarkError("game_count must be even when seat_swap_policy is ALWAYS_SWAP")
    base = build_benchmark_manifest(population, active_exact_decks=active_exact_decks, runnable_families=runnable_families, verified_links=verified_links)
    return VersionedBenchmarkManifest(
        schema_version=O5_BENCHMARK_MANIFEST_SCHEMA_VERSION, benchmark_id=benchmark_id, benchmark_version=benchmark_version,
        created_at=created_at, source_snapshot_ids=tuple(source_snapshot_ids), deck_registry_version=deck_registry_version,
        policy_pack_version=policy_pack_version, agent_family_versions=dict(agent_family_versions), ruleset_version=ruleset_version,
        cabt_version=cabt_version, seed_set=tuple(seed_set), seat_swap_policy=seat_swap_policy, game_count=game_count,
        logical_pair_count=game_count // 2, time_budget_seconds=time_budget_seconds, candidate_artifact_id=candidate_artifact_id,
        baseline_artifact_ids=tuple(baseline_artifact_ids), environment=environment, commit=commit, status=base["status"],
        sets={key: tuple(value) for key, value in base["sets"].items()}, requirements=dict(base["requirements"]),
    )


__all__ = ["O5BenchmarkError", "O5_BENCHMARK_MANIFEST_SCHEMA_VERSION", "VersionedBenchmarkManifest", "build_versioned_benchmark_manifest"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_benchmark.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/competition_intelligence/o5_benchmark.py tests/competition_intelligence/test_o5_benchmark.py
git commit -m "feat(o5): add versioned immutable benchmark manifest envelope"
```

---

### Task 2: Adversarial / Safety Opponent Agent Family v1

**Files:**
- Create: `src/mage_ptcg/competition_intelligence/o5_adversarial_agents.py`
- Test: `tests/competition_intelligence/test_o5_adversarial_agents.py`

**Interfaces:**
- Consumes: `main.Deck` (a `list[int]`), `agents.rule_agent.choose_rule_indices(obs_dict) -> list[int] | None` (existing), `main._selection_contract` is NOT imported (private to `main.py`) — reimplement a minimal local read of `obs_dict["select"]["option"]` length only, matching the same public fields `main.py:105-127` already treats as the allowlisted contract (`select.option`, `select.minCount`, `select.maxCount`), never any other observation field.
- Produces: `make_exception_agent(deck, seed) -> Agent`, `make_slow_agent(deck, seed, *, delay_seconds=0.05) -> Agent`, `make_invalid_artifact_agent(deck, seed) -> Agent`, `make_unknown_selection_agent(deck, seed) -> Agent`, and a registry `ADVERSARIAL_AGENT_FACTORIES: Mapping[str, Callable[[Sequence[int], int], Agent]]` keyed by the exact labels already present in `o5_activation.build_benchmark_manifest`'s `sets.safety` (`"exception_agent"`, `"slow_agent"`, `"invalid_artifact"`, `"unknown_selection"`). Task 4 imports `ADVERSARIAL_AGENT_FACTORIES` to resolve opponent member ids.

- [ ] **Step 1: Write failing tests**

```python
# tests/competition_intelligence/test_o5_adversarial_agents.py
from __future__ import annotations

import pytest

from mage_ptcg.competition_intelligence.o5_adversarial_agents import (
    ADVERSARIAL_AGENT_FACTORIES, make_exception_agent, make_invalid_artifact_agent,
    make_slow_agent, make_unknown_selection_agent,
)

_DECK = list(range(1, 61))
_OBS = {"select": {"type": 0, "minCount": 1, "maxCount": 1, "option": [{"type": 14}]}}
_NO_SELECT_OBS = {"select": None}


def test_registry_has_every_safety_label():
    assert set(ADVERSARIAL_AGENT_FACTORIES) == {"exception_agent", "slow_agent", "invalid_artifact", "unknown_selection"}


def test_exception_agent_eventually_raises_deterministically():
    agent = make_exception_agent(deck=_DECK, seed=1)
    agent(_OBS)
    agent(_OBS)
    with pytest.raises(RuntimeError):
        agent(_OBS)


def test_exception_agent_is_a_noop_when_there_is_no_selection():
    agent = make_exception_agent(deck=_DECK, seed=1)
    assert agent(_NO_SELECT_OBS) == []


def test_slow_agent_sleeps_a_bounded_deterministic_amount(monkeypatch):
    calls = []
    monkeypatch.setattr("time.sleep", lambda seconds: calls.append(seconds))
    agent = make_slow_agent(deck=_DECK, seed=1, delay_seconds=0.01)
    result = agent(_OBS)
    assert calls == [0.01]
    assert result == [0]


def test_invalid_artifact_agent_returns_out_of_range_index():
    agent = make_invalid_artifact_agent(deck=_DECK, seed=1)
    selection = agent(_OBS)
    assert selection == [999999]


def test_unknown_selection_agent_returns_well_formed_but_unmapped_index():
    agent = make_unknown_selection_agent(deck=_DECK, seed=1)
    selection = agent(_OBS)
    assert selection == [len(_OBS["select"]["option"])]


@pytest.mark.parametrize("factory", ADVERSARIAL_AGENT_FACTORIES.values())
def test_every_adversarial_factory_is_seed_reproducible(factory):
    first = factory(_DECK, 7)(_OBS)
    second = factory(_DECK, 7)(_OBS)
    assert first == second
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_adversarial_agents.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `o5_adversarial_agents.py`**

```python
"""Deterministic local opponent agents for O5's Benchmark `safety` set.

These are never candidates and never reachable from ``main.py``. They exist
only so the Evaluation Runner can exercise real cabt fault paths (exception,
timeout-adjacent latency, invalid action, unmapped selection) against a real
candidate (e.g. Rule Agent v0) and confirm the harness classifies each
correctly, instead of leaving ``sets.safety`` as unimplemented labels.
"""

from __future__ import annotations

import time
from typing import Callable, Mapping, Sequence


Agent = Callable[[dict], list[int]]


def _option_count(obs_dict: dict) -> int | None:
    select = obs_dict.get("select")
    if not isinstance(select, Mapping):
        return None
    options = select.get("option")
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        return None
    return len(options)


def make_exception_agent(deck: Sequence[int], seed: int) -> Agent:
    """Plays two legal END-priority-style turns, then raises deterministically."""
    calls = {"n": 0}

    def agent(obs_dict: dict) -> list[int]:
        count = _option_count(obs_dict)
        if count is None:
            return []
        calls["n"] += 1
        if calls["n"] > 2:
            raise RuntimeError("o5-adversarial-exception-agent: deterministic fault injection")
        return [0]

    agent.__name__ = "o5_exception_agent"
    return agent


def make_slow_agent(deck: Sequence[int], seed: int, *, delay_seconds: float = 0.05) -> Agent:
    """Sleeps a fixed, small, deterministic amount before each legal choice."""
    if delay_seconds < 0:
        raise ValueError("delay_seconds must be non-negative")

    def agent(obs_dict: dict) -> list[int]:
        count = _option_count(obs_dict)
        if count is None:
            return []
        time.sleep(delay_seconds)
        return [0]

    agent.__name__ = "o5_slow_agent"
    return agent


def make_invalid_artifact_agent(deck: Sequence[int], seed: int) -> Agent:
    """Always returns a well-typed but out-of-range selection index."""

    def agent(obs_dict: dict) -> list[int]:
        if _option_count(obs_dict) is None:
            return []
        return [999999]

    agent.__name__ = "o5_invalid_artifact_agent"
    return agent


def make_unknown_selection_agent(deck: Sequence[int], seed: int) -> Agent:
    """Returns a syntactically valid index that never matches a real option."""

    def agent(obs_dict: dict) -> list[int]:
        count = _option_count(obs_dict)
        if count is None:
            return []
        return [count]

    agent.__name__ = "o5_unknown_selection_agent"
    return agent


ADVERSARIAL_AGENT_FACTORIES: Mapping[str, Callable[[Sequence[int], int], Agent]] = {
    "exception_agent": make_exception_agent,
    "slow_agent": make_slow_agent,
    "invalid_artifact": make_invalid_artifact_agent,
    "unknown_selection": make_unknown_selection_agent,
}


__all__ = [
    "ADVERSARIAL_AGENT_FACTORIES", "make_exception_agent", "make_invalid_artifact_agent",
    "make_slow_agent", "make_unknown_selection_agent",
]
```

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_adversarial_agents.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/competition_intelligence/o5_adversarial_agents.py tests/competition_intelligence/test_o5_adversarial_agents.py
git commit -m "feat(o5): implement real safety/adversarial opponent agent family"
```

---

### Task 3: Experimental Pilot Profiles (additive)

**Files:**
- Modify: `src/mage_ptcg/competition_intelligence/o5_activation.py` (add `EXPERIMENTAL_PILOTS`, do not touch `DEFAULT_PILOTS`)
- Test: `tests/competition_intelligence/test_o5_activation.py` (append)

**Interfaces:**
- Consumes: existing `PilotProfile` dataclass (unchanged).
- Produces: `EXPERIMENTAL_PILOTS: tuple[PilotProfile, ...]` containing `SETUP_FIRST` and `DISRUPTION_FIRST`, exported via `__all__`. `DEFAULT_PILOTS` (3 entries) is unchanged so `build_opponent_population`'s default and existing tests are unaffected.

- [ ] **Step 1: Write failing test**

```python
def test_experimental_pilots_are_additive_and_do_not_change_default_pilots():
    from mage_ptcg.competition_intelligence.o5_activation import DEFAULT_PILOTS, EXPERIMENTAL_PILOTS
    assert {p.pilot_id for p in DEFAULT_PILOTS} == {"BALANCED", "AGGRESSIVE", "CONSERVATIVE"}
    assert {p.pilot_id for p in EXPERIMENTAL_PILOTS} == {"SETUP_FIRST", "DISRUPTION_FIRST"}
    assert len(DEFAULT_PILOTS) == 3
```

- [ ] **Step 2: Run to verify failure** (ImportError on `EXPERIMENTAL_PILOTS`)

- [ ] **Step 3: Add to `o5_activation.py`** immediately after the existing `DEFAULT_PILOTS` tuple:

```python
EXPERIMENTAL_PILOTS = (
    PilotProfile("SETUP_FIRST", .3, .7, .8, .2, .3),
    PilotProfile("DISRUPTION_FIRST", .55, .45, .4, .45, .85),
)
```

Add `"EXPERIMENTAL_PILOTS"` to the module's `__all__` list (alphabetical position after `DEFAULT_PILOTS`... actual list is alphabetized already — insert after `DEFAULT_PILOTS` string entry).

- [ ] **Step 4: Run full `test_o5_activation.py` to verify pass and no regression**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_activation.py -v`
Expected: all passed (existing 4 + new 1)

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/competition_intelligence/o5_activation.py tests/competition_intelligence/test_o5_activation.py
git commit -m "feat(o5): add experimental setup-first and disruption-first pilot profiles"
```

---

### Task 4: Evaluation Runner v1

**Files:**
- Create: `src/mage_ptcg/competition_intelligence/o5_evaluation.py`
- Test: `tests/competition_intelligence/test_o5_evaluation.py`

**Interfaces:**
- Consumes: `o5_benchmark.VersionedBenchmarkManifest` (Task 1), `o5_adversarial_agents.ADVERSARIAL_AGENT_FACTORIES` (Task 2), `league.actual_runner.{ActualLeagueConfig, run_actual_league}` (existing, unmodified), `offline_training_v1_support.statistics.wilson_score_interval` (existing, unmodified), `main.{make_random_agent, make_deterministic_agent, make_rule_agent, make_rule_agent_v1}` (existing), `o5_activation.GenericArchetypeAgent` (existing, for future non-empty `current_meta`).
- Produces: `KNOWN_AGENT_FACTORIES: Mapping[str, Callable[[Sequence[int], int], Agent]]` (candidate + baseline names: `rule_v0`, `rule_v1`, `random_legal`, `deterministic`, plus the four adversarial names from Task 2), `class O5EvaluationError(RuntimeError)`, `run_o5_benchmark(manifest, *, candidate_agent_id, deck_path, output_dir, run_match, opponent_agent_resolver=None) -> dict` — runs every member of `manifest.sets["core_regression"]`, `["current_meta"]`, `["adversarial"]`, `["safety"]` (skipping duplicates across sets) as a challenger against the fixed candidate, one resumable `run_actual_league` artifact per `(set_name, member_id)` under `output_dir`, then returns a merged report with Wilson CI per member and overall via `aggregate_o5_report(reports: Mapping[str, dict], *, confidence=0.95) -> dict`.

- [ ] **Step 1: Write failing tests using a fake `run_match`**

```python
# tests/competition_intelligence/test_o5_evaluation.py
from __future__ import annotations

import json

import pytest

from mage_ptcg.competition_intelligence.o5_benchmark import build_versioned_benchmark_manifest
from mage_ptcg.competition_intelligence.o5_evaluation import (
    KNOWN_AGENT_FACTORIES, O5EvaluationError, aggregate_o5_report, run_o5_benchmark,
)


def _manifest(**overrides):
    kwargs = dict(
        benchmark_id="o5-benchmark-core-v1", benchmark_version="1.0.0", created_at="2026-07-21T00:00:00Z",
        source_snapshot_ids=(), deck_registry_version="v1", policy_pack_version="v1",
        agent_family_versions={"rule_v0": "1"}, ruleset_version="unknown", cabt_version="1.32.0",
        seed_set=(9000,), seat_swap_policy="ALWAYS_SWAP", game_count=4, time_budget_seconds=60.0,
        candidate_artifact_id="rule_v0", baseline_artifact_ids=("random_legal",), environment="local",
        commit="0" * 40, active_exact_decks=0, runnable_families=0, verified_links=0,
    )
    kwargs.update(overrides)
    return build_versioned_benchmark_manifest((), **kwargs)


def _fake_run_match(schedule_item):
    return {"status": "DONE", "winner_agent": "champion" if schedule_item["match_index"] % 2 == 0 else "challenger", "elapsed_seconds": 0.01, "fallback_count": 0}


def test_known_agent_factories_cover_core_regression_and_safety_labels():
    manifest = _manifest()
    for member_id in (*manifest.sets["core_regression"], *manifest.sets["safety"]):
        assert member_id in KNOWN_AGENT_FACTORIES


def test_run_o5_benchmark_executes_core_regression_and_safety_and_records_blocked_current_meta(tmp_path):
    manifest = _manifest()
    result = run_o5_benchmark(
        manifest, candidate_agent_id="rule_v0", deck_path="deck.csv",
        output_dir=tmp_path, run_match=_fake_run_match,
    )
    assert result["current_meta"]["status"] == "BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION"
    assert result["current_meta"]["games"] == 0
    core = result["core_regression"]["members"]
    assert set(core) == set(manifest.sets["core_regression"])
    for member_report in core.values():
        assert member_report["games"] == manifest.game_count
        assert "wilson_ci_95" in member_report


def test_run_o5_benchmark_is_resumable_and_idempotent(tmp_path):
    manifest = _manifest()
    first = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_path="deck.csv", output_dir=tmp_path, run_match=_fake_run_match)
    second = run_o5_benchmark(manifest, candidate_agent_id="rule_v0", deck_path="deck.csv", output_dir=tmp_path, run_match=_fake_run_match)
    assert first == second


def test_unknown_candidate_agent_id_is_rejected():
    with pytest.raises(O5EvaluationError):
        run_o5_benchmark(_manifest(), candidate_agent_id="not_a_real_agent", deck_path="deck.csv", output_dir="/tmp/unused", run_match=_fake_run_match)


def test_aggregate_o5_report_computes_wilson_ci_and_overall_win_rate():
    reports = {
        "core_regression::random_legal": {"games": 10, "wins": 7, "losses": 3, "draws": 0, "seat_wld": {}, "invalid_actions": 0, "crashes": 0, "timeouts": 0, "match_latency_seconds": {"p50": 0.1, "p95": 0.2, "max": 0.3}},
    }
    aggregate = aggregate_o5_report(reports)
    assert aggregate["overall"]["games"] == 10
    assert 0.0 <= aggregate["overall"]["wilson_ci_95"][0] <= aggregate["overall"]["win_rate"] <= aggregate["overall"]["wilson_ci_95"][1] <= 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_evaluation.py`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `o5_evaluation.py`**

Reuse `run_actual_league`/`ActualLeagueConfig` per opponent member (one resumable artifact file per `f"{set_name}__{member_id}.json"` under `output_dir`); skip a member already fully recorded (same technique `run_actual_league` itself already uses via `config_hash` — just call it again, it is idempotent by construction). Candidate agent factory and opponent agent factory are both resolved through `KNOWN_AGENT_FACTORIES` and injected into `run_match` via a small local closure that adapts `(deck, seed) -> Agent` into the `run_actual_league`-compatible `run_match(schedule_item) -> dict` signature using `scripts.test_sim.run_match`'s `agent_a_factory`/`agent_b_factory` when the real adapter is passed in (tests use the fake `run_match` directly, bypassing `test_sim` entirely — `run_o5_benchmark` never imports `scripts.test_sim` itself; the CLI in Task 5 is responsible for building the real `run_match` closure and passing it in). Build `aggregate_o5_report` using `wilson_score_interval(wins, losses, draws)` for the `overall` roll-up and per-member roll-up, keyed exactly like the `_summary()` shape already produced by `run_actual_league` so no new record schema is invented.

Write the full module now (implementer note: keep `run_o5_benchmark` under ~80 lines by delegating everything possible to `run_actual_league`; do not reimplement scheduling, resumption, or seat-swap logic — those already exist and are already tested).

- [ ] **Step 4: Run to verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/test_o5_evaluation.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/competition_intelligence/o5_evaluation.py tests/competition_intelligence/test_o5_evaluation.py
git commit -m "feat(o5): add resumable multi-opponent evaluation runner with wilson CI aggregation"
```

---

### Task 5: CLI + real cabt smoke and initial evaluation run

**Files:**
- Create: `scripts/run_o5_benchmark.py`
- Test: `tests/test_run_o5_benchmark_cli.py` (argument parsing + dry-run only; real cabt execution is exercised manually per Task 5 Step 4, not in the unit test suite, matching the existing `scripts/run_actual_league.py` convention)

**Interfaces:**
- Consumes: `o5_benchmark.build_versioned_benchmark_manifest`, `o5_evaluation.run_o5_benchmark`, `scripts.test_sim.run_match`, `main.read_deck_csv`.
- Produces: a CLI with `--benchmark-id`, `--benchmark-version`, `--candidate-agent-id`, `--deck-path`, `--seeds` (repeatable), `--games-per-member`, `--output-dir`, `--max-steps`, `--dry-run`. Writes `<output-dir>/versioned_benchmark_manifest.json` and per-member league artifacts plus `<output-dir>/o5_benchmark_report.json` (the `aggregate_o5_report` output). Mirrors `scripts/run_actual_league.py`'s existing wiring style (build a `play(schedule_item)` closure over `scripts.test_sim.run_match`).

- [ ] **Step 1-4: TDD the CLI argument parsing and manifest-writing path (dry-run, no real cabt), following the same Step 1/2/3/4/5 pattern as Tasks 1-4** — write failing test asserting `--dry-run` writes `versioned_benchmark_manifest.json` with the expected `benchmark_id` and does not call `run_match`; implement; verify pass; commit (`git commit -m "feat(o5): add versioned benchmark CLI with dry-run manifest generation"`).

- [ ] **Step 5: Real cabt smoke (2 games, Core Regression only)**

Run (repo convention for real cabt, host `PYTHONPATH` overlay excluded):

```bash
env -u PYTHONPATH .venv/bin/python scripts/run_o5_benchmark.py \
  --benchmark-id o5-benchmark-core-v1 --benchmark-version 1.0.0 \
  --candidate-agent-id rule_v0 --deck-path deck.csv \
  --seeds 90000 --games-per-member 2 \
  --output-dir /tmp/o5-benchmark-smoke --max-steps 10000
```

Expected: exits 0, `core_regression` and `safety` members each show `games: 2`, `status: DONE` for at least the `random_legal`/`deterministic` members; record actual observed behavior for `exception_agent`/`slow_agent`/`invalid_artifact`/`unknown_selection` (status classification, whether `slow_agent`'s sleep actually triggers `AGENT_TIMEOUT` is unconfirmed until observed — report exactly what happens, do not assume). `current_meta` and archetype-`adversarial` report `games: 0, status: BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION`.

- [ ] **Step 6: Full initial evaluation run**

Run the same command with `--seeds 90000 90001 90002 90003 90004 90005 --games-per-member 8` (targets ≥96 total games across `core_regression` + `safety` members at 2 members × 48 + adversarial family × ... — implementer computes exact seed/game count to reach ≥96 logical pairs within the members that are actually populated; document the real total in the run's output, do not pad or round up the report). Save the report under `docs/evidence/` per Task 6.

- [ ] **Step 7: Commit CLI + real smoke test file**

```bash
git add scripts/run_o5_benchmark.py tests/test_run_o5_benchmark_cli.py
git commit -m "test(o5): add versioned benchmark CLI dry-run coverage"
```

---

### Task 6: Docs, evidence, status/handoff

**Files:**
- Modify: `docs/status/current_status.md` (new dated entry, append-only style matching existing entries)
- Modify: `docs/status/handoff.md` (new dated entry)
- Modify: `docs/plan/implementation/05_evaluation_submission_and_strategy_implementation_plan.md` (new `## 23.1` subsection directly after `## 23`, following `docs/plan/AGENTS.md` rules: conclusion-first, one point per paragraph, link to evidence rather than duplicate detail)
- Create: `docs/evidence/o5-versioned-benchmark-evaluation-runner-v1.md` (+ optional `.json` machine-readable twin, following the existing evidence pairing convention)

**Interfaces:** none (documentation only). Must report, verbatim from the real Task 5 Step 6 run: total games executed, per-member win/loss/draw + Wilson CI, `current_meta`/archetype-`adversarial` = 0 games/BLOCKED with the exact blocking reason (`Rules attestation UNVERIFIED_RULES_CONSTRAINT`, `Team permission manifests: 0`), and the exact next unblocking step already on record in `docs/status/handoff.md` (human rules attestation + team permission manifest, then re-run `o5 acquire-environment-top-decks`).

- [ ] Write `docs/evidence/o5-versioned-benchmark-evaluation-runner-v1.md` with sections: 結論 / 実装 / 実行結果とGate / 検証 / 残課題, matching the two existing O5 evidence docs' skeleton.
- [ ] Append dated entries to `current_status.md` and `handoff.md` referencing the new evidence file; do not restate numbers already in the evidence file — link to it.
- [ ] Add `## 23.1 Versioned Benchmark Manifest and Evaluation Runner` to the implementation plan doc: 2-3 short paragraphs (what is new, what remains blocked and why, link to evidence). Do not renumber section 23 or anything after it.
- [ ] Commit: `git commit -m "docs(o5): record versioned benchmark and evaluation runner evidence"`

---

### Task 7: Full verification sweep

- [ ] Focused: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence/ tests/test_run_o5_benchmark_cli.py`
- [ ] Full regression: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q`
- [ ] Protected file diff: `git diff 514a56a -- main.py deck.csv agents/rule_agent.py agents/rule_agent_v1.py src/mage_ptcg/evaluation/promotion.py` — expect empty
- [ ] `git diff --check` — expect clean
- [ ] Conflict marker scan: `grep -rn "^<<<<<<<\|^=======$\|^>>>>>>>" --include=*.py --include=*.md .` — expect none introduced
- [ ] Secret scan (repo convention, reuse whatever `scripts/collect_offline_training_v1_evidence.py`'s privacy/secret scan invokes, or a targeted `grep -rniE "kaggle\.json|AKIA|BEGIN (RSA|OPENSSH) PRIVATE KEY"` over new files only)
- [ ] Docs validation: `python scripts/docs/validate_docs.py`
- [ ] `git status --short` on the worktree — expect clean after final commit

---

## Self-Review Notes (from plan authoring)

- Spec coverage: O5-A (Registry) and O5-B/O5-C (Policy Pack/Opponent Factory) are already implemented and canonical — this plan deliberately does not re-implement them, only extends the Benchmark (O5-D) and adds the Evaluation Runner (O5-E) plus evidence (O5-F). This is a narrower, honest scope than the original six-part spec because the multi-archetype population gate is a genuine, intentional, human-gated block, not an engineering gap.
- The plan does not implement scenario-level Adversarial cases (setup accidents, resource starvation, bench pressure) beyond agent-behavior faults, because cabt has no confirmed engine-level state-injection or seed control (`ENGINE_SEED_SUPPORTED = False`) — fabricating a scenario harness on unconfirmed engine capabilities would violate the project's no-guessing rule. This is recorded as a known limitation in Task 6, not silently dropped.
- Type/signature consistency check: `Agent = Callable[[dict], list[int]]` used consistently across Tasks 2 and 4; `run_o5_benchmark`'s `run_match` parameter matches `league.actual_runner.run_actual_league`'s `Callable[[Mapping[str, object]], Mapping[str, object]]` signature exactly, since Task 4 delegates to it per-member rather than reimplementing.
