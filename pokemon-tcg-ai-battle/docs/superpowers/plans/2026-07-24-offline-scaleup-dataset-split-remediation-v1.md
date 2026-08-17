# Offline Scale-up Dataset Split Remediation v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regenerate the offline scale-up Student v1 dataset from the existing, already-Gate-PASS `stability-1000` 900-game run so it has 5 non-overlapping, deterministic, gate-satisfying cohorts (train/validation/test/opponent_holdout/deck_holdout), instead of the current 1/1 validation/test episodes.

**Architecture:** Add pure split-selection and manifest-building functions to `mage_ptcg.offline_scaleup.pipeline` (same module that already owns dataset export), reusing the existing per-record builder via a safe refactor. A new `export-dataset-v2-split` CLI subcommand joins `game_results.jsonl` with the population snapshot to recover each episode's real opponent identity/type/deck fingerprint (the old dataset only stored a one-way hash of the opponent id, so it cannot be used as the source — this task reads directly from `runs/stability-1000/game_results.jsonl`, which is already-computed CABT output, not a new CABT execution). Deterministic SHA-256 hashing (matching the project's existing `_digest` convention) selects the 1 opponent holdout and 1 deck holdout, then stratifies the remainder 80/10/10 per (opponent, side) cell. `05_train_student_v1.sh` is parametrized to accept a dataset path instead of hard-coding `stability-1000.jsonl`, and gains a pre-flight split-gate check.

**Tech Stack:** Python 3.12, stdlib only (`hashlib`, `json`, `concurrent.futures.ProcessPoolExecutor`, `argparse`), `tqdm` (already a pinned dependency) for progress bars, `pytest` for tests.

## Global Constraints

- Branch: stay on `local/offline-scaleup-v2`. Local commits only. No `git push`, no upstream, no remote branch, no PR, no merge to any canonical branch.
- Do not modify or stage existing untracked files (`.codex/hooks.json`, `.codex/hooks/`, `generate_audit_artifacts.py`, `o6_continue_after_team_permission.md*`, `pokemon_team_agents_internal_v1.yaml*`, `scripts/build_o6_taxonomy.py`).
- Protected files — never touch: `main.py`, `deck.csv`, `agents/rule_agent.py`, `agents/rule_agent_v1.py`, `src/mage_ptcg/evaluation/promotion.py`.
- Do not re-run CABT and do not delete/modify the existing `datasets/stability-1000.jsonl`, its summary, or anything under `runs/stability-1000/`.
- Every new artifact is written under `/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/` at the exact paths the spec names.
- No hard-coded opponent ID or deck fingerprint anywhere in the selection code — both must come from deterministic hashing over the population entries actually present in the run.
- Minimum split gate: train ≥ 500, validation ≥ 50, test ≥ 50, opponent_holdout ≥ 50, deck_holdout ≥ 50 episodes; 0 leakage; 0 illegal selected actions; 0 quarantined teacher records; 0 provenance gaps.
- Commit messages follow `AGENTS.md`'s `<type>(<scope>): <summary>` convention, in Japanese, no emoji.

---

## Investigation findings (context for every task below)

Read directly from `runs/stability-1000/`:
- `game_results.jsonl`: 900 rows, schema `offline-scaleup-result-v2`. Each row has `game_id`, `candidate` (always `"rule-v0-current-deck"`), `opponent` (an `opponent_id` string), `candidate_side` (0/1), `status`, `legal`, `candidate_fault`, `mapping_valid`, `score_identity_valid`, `teacher_samples` (list of `RuleBCExample` dicts).
- `schedule.json.population_digest` = `c2cb029f9ebeedbca75f3ffb4d9e97e4793fff3b6472cce9a0d61ec4a510c6d2`, which equals `artifacts/expanded_population_snapshot.json["semantic_population_digest"]`. That snapshot is the population used for this run (37 entries: 31 `RULE_V0_DECK`, 3 `FAMILY_SPECIFIC`, 3 `TEAM_NATIVE`).
- Distinct opponents actually exercised in the 900 games (9 total, 100 games each, 50/50 split by `candidate_side`): `rule-v0-current-deck`, `rule-v0-deck-0a996cf541e0d1cf`, `rule-v0-deck-113d5e366c62c387` (all `RULE_V0_DECK`); `family-alakazam-deck-74d86ec36fd144b9`, `family-mega_abomasnow_ex-deck-2e7428b334577cbe`, `family-mega_lucario_ex-deck-0ec8de046577ad94` (all `FAMILY_SPECIFIC`); `team-native-03d3839995b4c5e9`, `team-native-9144af0d5cde8d11`, `team-native-973619b52534bae9` (all `TEAM_NATIVE`).
- `candidate` is `"rule-v0-current-deck"` for all 900 games — every teacher record's `teacher_identity`/`teacher_type`/`teacher_trust` in the dataset is Rule v0/TRUSTED. This is a fact to report, not an error (per spec's 教師分布の確認 section).
- The **existing** dataset (`datasets/stability-1000.jsonl`) records `opponent_fingerprint` as `_digest({"opponent": game["opponent"]}, "opponent")` — a one-way hash, not the raw `opponent_id`. It cannot be joined back to the population, so it cannot be reused as the source for split selection. The new export must re-read `run_dir/game_results.jsonl` directly (already-computed CABT output — re-reading is not re-running CABT).
- `src/mage_ptcg/offline_scaleup/pipeline.py:593-645` (`export_dataset`) is the existing exporter. Its episode/split bug: it always reserves exactly the first 1 or 2 hash-ranked episodes as test/validation regardless of total episode count (`pipeline.py:627-632`). This function stays untouched (existing test `tests/test_offline_scaleup_pipeline.py::test_dataset_student_v1_and_holdout_smoke` depends on its exact behavior); only its record-building inner loop is extracted into a shared helper with **identical** output, verified by that existing test still passing unmodified.
- `train_student_v1`/`evaluate_holdout` (`pipeline.py:659-684`) already filter dataset rows by `row["split"] == "train"/"validation"/"test"` and by `row["schema_version"] == DATASET_SCHEMA`. Reusing the same `schema_version` value for v2 records means these two functions work unmodified against the new dataset — no changes needed there.
- `scripts/offline_scaleup/05_train_student_v1.sh` currently hard-codes `--dataset "$ARTIFACT_ROOT/datasets/stability-1000.jsonl"`. Must be parametrized.
- No GPU-bound step exists anywhere in this pipeline (`student/model.py` is a pure-Python/NumPy linear ranking model; `training_summary` already states `"device": "CPU (GPU optional external trainer is not required for this model)"`). GPU scaling from the session-level ask is therefore not applicable and will be noted as such, not fabricated.
- `tqdm==4.68.4` is already pinned in `requirements.txt` — safe to use for progress bars with no new dependency.
- Host has 28 CPUs and 31 GiB RAM (`os.sched_getaffinity(0)` → 28). A CPU-based default-worker heuristic (`max(1, floor(affinity_count * 0.8))`) is a safe, dependency-free approximation; there's no `psutil` in `requirements.txt`, so memory-based capping is approximated by process count only (documented, not silently invented).

---

## Task 1: Add deterministic holdout selection + episode index to pipeline.py

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py`
- Test: `tests/test_offline_scaleup_dataset_split.py` (new file)

**Interfaces:**
- Produces: `_population_entries_by_id(population: Mapping[str, Any]) -> dict[str, dict[str, Any]]`
- Produces: `select_opponent_holdout(population: Mapping[str, Any], present_opponent_ids: set[str]) -> str`
- Produces: `select_deck_holdout(population: Mapping[str, Any], present_opponent_ids: set[str], opponent_holdout_id: str) -> str`
- Consumes: existing `_digest(value, domain) -> str`, `ContractError`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_offline_scaleup_dataset_split.py`:

```python
"""Contracts for the deterministic 5-cohort dataset split remediation."""
from __future__ import annotations

from mage_ptcg.offline_scaleup.pipeline import (
    ContractError,
    select_deck_holdout,
    select_opponent_holdout,
)


def _population(entries: list[dict[str, object]]) -> dict[str, object]:
    return {"schema_version": "offline-scaleup-population-v2", "entries": entries,
            "semantic_population_digest": "d" * 64}


def _entry(opponent_id: str, opponent_type: str, deck_fingerprint: str) -> dict[str, object]:
    return {"opponent_id": opponent_id, "opponent_type": opponent_type, "deck_fingerprint": deck_fingerprint}


_POPULATION = _population([
    _entry("rule-v0-current-deck", "RULE_V0_DECK", "fp-current"),
    _entry("rule-v0-deck-a", "RULE_V0_DECK", "fp-a"),
    _entry("rule-v0-deck-b", "RULE_V0_DECK", "fp-b"),
    _entry("family-x", "FAMILY_SPECIFIC", "fp-family-x"),
    _entry("family-y", "FAMILY_SPECIFIC", "fp-family-y"),
    _entry("team-native-p", "TEAM_NATIVE", "fp-team-p"),
    _entry("team-native-q", "TEAM_NATIVE", "fp-team-q"),
])
_PRESENT = {"rule-v0-current-deck", "rule-v0-deck-a", "rule-v0-deck-b", "family-x", "family-y", "team-native-p", "team-native-q"}


def test_opponent_holdout_is_deterministic_and_from_team_native_or_family() -> None:
    first = select_opponent_holdout(_POPULATION, _PRESENT)
    second = select_opponent_holdout(_POPULATION, _PRESENT)
    assert first == second
    entries = {e["opponent_id"]: e for e in _POPULATION["entries"]}
    assert entries[first]["opponent_type"] in {"TEAM_NATIVE", "FAMILY_SPECIFIC"}


def test_opponent_holdout_rejects_when_no_team_native_or_family_present() -> None:
    rule_only = {"rule-v0-current-deck", "rule-v0-deck-a", "rule-v0-deck-b"}
    try:
        select_opponent_holdout(_POPULATION, rule_only)
    except ContractError:
        return
    raise AssertionError("expected ContractError")


def test_deck_holdout_is_deterministic_and_from_rule_v0_and_excludes_opponent_holdout_deck() -> None:
    opponent_holdout = select_opponent_holdout(_POPULATION, _PRESENT)
    first = select_deck_holdout(_POPULATION, _PRESENT, opponent_holdout)
    second = select_deck_holdout(_POPULATION, _PRESENT, opponent_holdout)
    assert first == second
    entries_by_fp = {e["deck_fingerprint"]: e for e in _POPULATION["entries"]}
    assert entries_by_fp[first]["opponent_type"] == "RULE_V0_DECK"
    holdout_fp = next(e["deck_fingerprint"] for e in _POPULATION["entries"] if e["opponent_id"] == opponent_holdout)
    assert first != holdout_fp


def test_deck_holdout_rejects_when_no_rule_v0_deck_present() -> None:
    non_rule = {"family-x", "family-y", "team-native-p", "team-native-q"}
    try:
        select_deck_holdout(_POPULATION, non_rule, "family-x")
    except ContractError:
        return
    raise AssertionError("expected ContractError")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_opponent_holdout'`

- [ ] **Step 3: Implement in pipeline.py**

Insert after `_load_v1_examples` (after line 656, before `def train_student_v1`) in `src/mage_ptcg/offline_scaleup/pipeline.py`:

```python
def _population_entries_by_id(population: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["opponent_id"]: item for item in population["entries"]}


def select_opponent_holdout(population: Mapping[str, Any], present_opponent_ids: set[str]) -> str:
    """Deterministically pick 1 TEAM_NATIVE/FAMILY_SPECIFIC opponent id actually present in the run."""
    entries = _population_entries_by_id(population)
    candidates = sorted(
        opponent_id for opponent_id in present_opponent_ids
        if entries.get(opponent_id, {}).get("opponent_type") in {"TEAM_NATIVE", "FAMILY_SPECIFIC"}
    )
    if not candidates:
        raise ContractError("no TEAM_NATIVE or FAMILY_SPECIFIC opponent is present in this run for opponent-holdout selection")
    return min(candidates, key=lambda opponent_id: _digest(opponent_id, "opponent-holdout-selection"))


def select_deck_holdout(population: Mapping[str, Any], present_opponent_ids: set[str], opponent_holdout_id: str) -> str:
    """Deterministically pick 1 RULE_V0_DECK deck fingerprint, excluding the opponent-holdout's own deck."""
    entries = _population_entries_by_id(population)
    excluded_fingerprint = entries[opponent_holdout_id]["deck_fingerprint"]
    candidates = sorted({
        entries[opponent_id]["deck_fingerprint"] for opponent_id in present_opponent_ids
        if entries.get(opponent_id, {}).get("opponent_type") == "RULE_V0_DECK"
        and entries[opponent_id]["deck_fingerprint"] != excluded_fingerprint
    })
    if not candidates:
        raise ContractError("no RULE_V0_DECK opponent deck fingerprint is available for deck-holdout selection")
    return min(candidates, key=lambda fingerprint: _digest(fingerprint, "deck-holdout-selection"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py tests/test_offline_scaleup_dataset_split.py
git commit -m "feat(offline-scaleup): 決定論的なopponent／deck holdout選択を追加

- population entriesから実在するopponentのみを候補にhash選択する"
```

---

## Task 2: Refactor `export_dataset`'s per-record builder into a shared helper (no behavior change)

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py:593-645`
- Test: `tests/test_offline_scaleup_pipeline.py` (existing — must keep passing unmodified)

**Interfaces:**
- Produces: `_teacher_dataset_record(game: Mapping[str, Any], sample: Mapping[str, Any], population_digest: str) -> dict[str, Any]`
- Consumes: `RuleBCExample`, `ContractError`, `_digest`, `_contains_forbidden`, `DATASET_SCHEMA`

This is a pure extraction: the existing test `test_dataset_student_v1_and_holdout_smoke` must pass byte-for-byte identically before and after.

- [ ] **Step 1: Run the existing test to record the current-passing baseline**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_pipeline.py::test_dataset_student_v1_and_holdout_smoke -v`
Expected: PASS (baseline, before refactor)

- [ ] **Step 2: Extract the helper and rewrite `export_dataset` to call it**

Replace `src/mage_ptcg/offline_scaleup/pipeline.py:593-621` (the `def export_dataset` header through the `records.append(record)` line, i.e. everything up to but not including `if not records:`) with:

```python
def _teacher_dataset_record(game: Mapping[str, Any], sample: Mapping[str, Any], population_digest: str) -> dict[str, Any]:
    example = RuleBCExample.from_dict(sample)
    record = {"schema_version": DATASET_SCHEMA, "episode_id": game["game_id"], "game_id": game["game_id"], "turn": 0, "phase": "OBSERVED",
              "state_fingerprint": example.example_id, "deck_fingerprint": example.deck_fingerprint,
              "opponent_fingerprint": _digest({"opponent": game["opponent"]}, "opponent"), "candidate_side": game["candidate_side"],
              "teacher_identity": game["candidate"], "teacher_type": "RULE_V0_DECK", "teacher_trust": "TRUSTED",
              "runtime_fingerprint": _digest({"candidate": game["candidate"]}, "runtime"), "legal_action_candidates": list(example.legal_actions),
              "selected_action": list(example.target_action_digests), "selected_action_key": list(example.target_action_digests),
              "state_features": {"public_state": example.public_state, "own_private_state": example.own_private_state, "visible_history": list(example.visible_history)},
              "action_features": list(example.legal_actions), "family": None, "strategy": "rule", "variant": None, "rule_score": list(example.teacher_ranking),
              "terminal_result": game["status"], "fault_class": game["fault"]["kind"], "legality": True,
              "provenance": {"population_digest": population_digest, "source_revision": example.source_revision},
              "rule_bc_example": example.to_dict()}
    if _contains_forbidden(record):
        raise ContractError("privacy violation in dataset materialization")
    legal = {item["digest"] for item in example.legal_actions}
    if not set(example.target_action_digests).issubset(legal):
        raise ContractError("selected action is not legal")
    return record


def _valid_terminal_games(run_dir: Path) -> list[dict[str, Any]]:
    rows = list(_jsonl(run_dir / "game_results.jsonl"))
    return [game for game in rows if game.get("status") == "DONE" and game.get("legal") is True
            and not game.get("candidate_fault") and game.get("mapping_valid") and game.get("score_identity_valid")]


def export_dataset(*, run_dir: Path, output: Path) -> dict[str, Any]:
    population_digest = _read_json(run_dir / "schedule.json")["population_digest"]
    records: list[dict[str, Any]] = []
    for game in _valid_terminal_games(run_dir):
        for sample in game.get("teacher_samples", []):
            records.append(_teacher_dataset_record(game, sample, population_digest))
```

Leave everything from `if not records:` (original line 622) through the end of the function (`return summary`, original line 645) exactly as-is.

- [ ] **Step 3: Run the existing test again to confirm identical behavior**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_pipeline.py -v`
Expected: all pass, same as baseline in Step 1

- [ ] **Step 4: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py
git commit -m "refactor(offline-scaleup): export_dataset のレコード構築を共有ヘルパーへ抽出

- 挙動は変更せず、v2 split exportとの重複を避けるための準備"
```

---

## Task 3: Build the split manifest (episode index + holdout + stratified 80/10/10) and gate check

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py`
- Test: `tests/test_offline_scaleup_dataset_split.py`

**Interfaces:**
- Consumes: `select_opponent_holdout`, `select_deck_holdout`, `_population_entries_by_id`, `_valid_terminal_games`, `_read_json`, `_digest`, `ContractError`
- Produces: `MIN_SPLIT_EPISODES: dict[str, int]`
- Produces: `build_split_manifest(*, run_dir: Path, population_path: Path) -> dict[str, Any]` — keys: `schema_version`, `run_id`, `population_digest`, `opponent_holdout_id`, `deck_holdout_fingerprint`, `episode_count`, `split_counts` (dict split→int), `episode_assignment` (dict episode_id→split name), `episode_opponent` (dict episode_id→opponent_id), `episode_side` (dict episode_id→int)
- Produces: `validate_split_gate(manifest: Mapping[str, Any]) -> dict[str, Any]` — keys: `schema_version`, `counts`, `failures` (list[str]), `gate` (`"PASS"`/`"BLOCKED"`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_offline_scaleup_dataset_split.py`:

```python
import json
from pathlib import Path

from mage_ptcg.offline_scaleup.pipeline import (
    RESULT_SCHEMA,
    _write_jsonl_once,
    MIN_SPLIT_EPISODES,
    build_schedule,
    build_split_manifest,
    validate_split_gate,
)
from mage_ptcg.student.dataset import build_rule_bc_example


def _card(card_id: int) -> dict[str, object]:
    return {"id": card_id, "serial": 0, "playerIndex": 0, "hp": 100, "maxHp": 100, "appearThisTurn": False, "energies": [], "energyCards": [], "tools": [], "preEvolution": []}


def _observation() -> dict[str, object]:
    player = lambda card: {"active": [], "asleep": False, "bench": [], "benchMax": 5, "burned": False, "confused": False, "deckCount": 53, "discard": [], "hand": [_card(card)], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [object() for _ in range(6)]}
    return {"current": {"energyAttached": False, "firstPlayer": 0, "players": [player(100), player(700)], "result": -1, "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False, "turn": 2, "turnActionCount": 3, "yourIndex": 0}, "select": {"context": 0, "maxCount": 1, "minCount": 1, "option": [{"type": 14}, {"type": 13, "attackId": 1}], "type": 0}, "step": 7}


def _build_run(tmp_path: Path, *, games_per_opponent: int) -> tuple[Path, Path]:
    entries = [
        {"opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": "x", "deck_id": "current-deck", "deck_fingerprint": "fp-current", "runtime_id": "r", "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
        {"opponent_id": "rule-v0-deck-a", "opponent_type": "RULE_V0_DECK", "source_path": "x", "deck_id": "deck-a", "deck_fingerprint": "fp-a", "runtime_id": "r", "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
        {"opponent_id": "family-x", "opponent_type": "FAMILY_SPECIFIC", "source_path": "x", "deck_id": "deck-x", "deck_fingerprint": "fp-family-x", "runtime_id": "r", "runtime_fingerprint": "b" * 64, "agent_digest": "b" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED", "quarantine_reason": None, "family_id": "X", "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
        {"opponent_id": "team-native-p", "opponent_type": "TEAM_NATIVE", "source_path": "x", "deck_id": "deck-p", "deck_fingerprint": "fp-team-p", "runtime_id": "r", "runtime_fingerprint": "c" * 64, "agent_digest": "c" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "LIMITED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
    ]
    population = {"schema_version": "offline-scaleup-population-v2", "entries": entries, "semantic_population_digest": "d" * 64, "alias_count": 0, "created_by": "test", "population_id": "population-test"}
    population_path = tmp_path / "population.json"
    population_path.write_text(json.dumps(population), encoding="utf-8")
    schedule = build_schedule(population, candidate="rule-v0-current-deck",
                               opponents=["rule-v0-current-deck", "rule-v0-deck-a", "family-x", "team-native-p"],
                               games=games_per_opponent, base_seed=41)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    for game in schedule["games"]:
        _write_jsonl_once(run_dir / "game_results.jsonl", {"schema_version": RESULT_SCHEMA, **game, "status": "DONE", "legal": True,
                           "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True,
                           "teacher_samples": [example.to_dict()], "fault": {"kind": "COMPLETED"}})
    from mage_ptcg.offline_scaleup.pipeline import summarize_run
    summarize_run(run_dir)
    return run_dir, population_path


def test_split_manifest_is_deterministic_covers_every_episode_and_isolates_holdouts(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=20)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    repeat = build_split_manifest(run_dir=run_dir, population_path=population_path)
    assert manifest["episode_assignment"] == repeat["episode_assignment"]
    assert manifest["opponent_holdout_id"] == repeat["opponent_holdout_id"]
    assert manifest["deck_holdout_fingerprint"] == repeat["deck_holdout_fingerprint"]
    assert set(manifest["episode_assignment"]) == set(manifest["episode_opponent"])
    assert manifest["opponent_holdout_id"] in {"family-x", "team-native-p"}
    holdout_episodes = [ep for ep, split in manifest["episode_assignment"].items() if split == "opponent_holdout"]
    assert all(manifest["episode_opponent"][ep] == manifest["opponent_holdout_id"] for ep in holdout_episodes)
    assert {manifest["episode_side"][ep] for ep in holdout_episodes} == {0, 1}
    deck_episodes = [ep for ep, split in manifest["episode_assignment"].items() if split == "deck_holdout"]
    assert deck_episodes, "deck holdout must not be empty"
    assert not (set(holdout_episodes) & set(deck_episodes))


def test_split_gate_reports_failures_below_minimum(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=4)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    assert gate["gate"] == "BLOCKED"
    assert any(name.startswith("train") for name in gate["failures"])


def test_split_gate_passes_when_minimums_met(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    assert gate["gate"] == "PASS", gate["failures"]
    for name, minimum in MIN_SPLIT_EPISODES.items():
        assert manifest["split_counts"].get(name, 0) >= minimum
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_split_manifest'`

- [ ] **Step 3: Implement `build_split_manifest` and `validate_split_gate`**

Add to `src/mage_ptcg/offline_scaleup/pipeline.py` right after `select_deck_holdout` (from Task 1):

```python
MIN_SPLIT_EPISODES = {"train": 500, "validation": 50, "test": 50, "opponent_holdout": 50, "deck_holdout": 50}


def _stratified_remainder_assignment(cells: Mapping[tuple[str, int], list[str]]) -> dict[str, str]:
    """Deterministically split each (opponent, side) cell 80/10/10; rounding remainder goes to test."""
    assignment: dict[str, str] = {}
    for key in sorted(cells):
        episodes = sorted(cells[key], key=lambda episode_id: _digest((key, episode_id), "remaining-split-order"))
        total = len(episodes)
        train_n = int(total * 0.8)
        validation_n = int(total * 0.1)
        for episode_id in episodes[:train_n]:
            assignment[episode_id] = "train"
        for episode_id in episodes[train_n:train_n + validation_n]:
            assignment[episode_id] = "validation"
        for episode_id in episodes[train_n + validation_n:]:
            assignment[episode_id] = "test"
    return assignment


def build_split_manifest(*, run_dir: Path, population_path: Path) -> dict[str, Any]:
    """Compute the deterministic 5-cohort split for one already-completed, Gate-PASS league run."""
    run_summary = _read_json(run_dir / "run_summary.json")
    if run_summary.get("gate") != "PASS":
        raise ContractError("run gate must PASS before dataset split remediation")
    schedule = _read_json(run_dir / "schedule.json")
    population = _read_json(population_path)
    if schedule.get("population_digest") != population.get("semantic_population_digest"):
        raise ContractError("population snapshot does not match the run schedule's population digest")
    entries = _population_entries_by_id(population)
    valid_games = _valid_terminal_games(run_dir)
    if len(valid_games) != run_summary["completed"]:
        raise ContractError("run contains non-valid games; dataset split remediation requires a fully valid run")
    episode_opponent: dict[str, str] = {}
    episode_side: dict[str, int] = {}
    for game in valid_games:
        opponent_id = str(game["opponent"])
        if opponent_id not in entries:
            raise ContractError(f"opponent {opponent_id} is absent from the supplied population snapshot")
        episode_id = str(game["game_id"])
        episode_opponent[episode_id] = opponent_id
        episode_side[episode_id] = int(game["candidate_side"])
    present_ids = set(episode_opponent.values())
    opponent_holdout_id = select_opponent_holdout(population, present_ids)
    deck_holdout_fingerprint = select_deck_holdout(population, present_ids, opponent_holdout_id)
    opponent_holdout_episodes = {ep for ep, opp in episode_opponent.items() if opp == opponent_holdout_id}
    deck_holdout_episodes = {
        ep for ep, opp in episode_opponent.items()
        if entries[opp]["opponent_type"] == "RULE_V0_DECK"
        and entries[opp]["deck_fingerprint"] == deck_holdout_fingerprint
        and ep not in opponent_holdout_episodes
    }
    reserved = opponent_holdout_episodes | deck_holdout_episodes
    cells: dict[tuple[str, int], list[str]] = defaultdict(list)
    for episode_id, opponent_id in episode_opponent.items():
        if episode_id in reserved:
            continue
        cells[(opponent_id, episode_side[episode_id])].append(episode_id)
    assignment = _stratified_remainder_assignment(cells)
    for episode_id in opponent_holdout_episodes:
        assignment[episode_id] = "opponent_holdout"
    for episode_id in deck_holdout_episodes:
        assignment[episode_id] = "deck_holdout"
    if set(assignment) != set(episode_opponent):
        raise ContractError("split assignment left an episode unassigned")
    return {"schema_version": "offline-scaleup-dataset-split-manifest-v2", "run_id": run_dir.name,
            "population_digest": population["semantic_population_digest"],
            "opponent_holdout_id": opponent_holdout_id, "deck_holdout_fingerprint": deck_holdout_fingerprint,
            "episode_count": len(episode_opponent), "split_counts": dict(sorted(Counter(assignment.values()).items())),
            "episode_assignment": assignment, "episode_opponent": episode_opponent, "episode_side": episode_side}


def validate_split_gate(manifest: Mapping[str, Any]) -> dict[str, Any]:
    counts = manifest["split_counts"]
    failures = [f"{name}<{minimum} (actual={counts.get(name, 0)})" for name, minimum in MIN_SPLIT_EPISODES.items() if counts.get(name, 0) < minimum]
    return {"schema_version": "offline-scaleup-split-gate-v1", "counts": counts, "failures": failures,
            "gate": "PASS" if not failures else "BLOCKED"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py tests/test_offline_scaleup_dataset_split.py
git commit -m "feat(offline-scaleup): 5 cohort split manifestとminimum Gateを追加

- episode単位でholdoutと80/10/10層化split を決定論的に割り当てる"
```

---

## Task 4: `export_dataset_v2` — build the split dataset + all 6 artifacts, with progress bar and parallel workers

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py`
- Test: `tests/test_offline_scaleup_dataset_split.py`

**Interfaces:**
- Consumes: `build_split_manifest`, `validate_split_gate`, `_teacher_dataset_record`, `_valid_terminal_games`, `_population_entries_by_id`, `_atomic_json`, `_write_jsonl_once`, `MIN_SPLIT_EPISODES`
- Produces: `default_worker_count() -> int`
- Produces: `export_dataset_v2(*, run_dir: Path, population_path: Path, artifact_root: Path, workers: int | None = None, show_progress: bool = True) -> dict[str, Any]` — writes all 7 files named in the spec under `artifact_root` and returns a summary dict with `dataset`, `manifest`, `gate`, `records`, `episodes`, `split_counts`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_offline_scaleup_dataset_split.py`:

```python
from mage_ptcg.offline_scaleup.pipeline import DATASET_SCHEMA, default_worker_count, export_dataset_v2


def test_default_worker_count_is_positive_and_bounded_by_cpu() -> None:
    import os
    workers = default_worker_count()
    assert 1 <= workers <= (os.cpu_count() or 1)


def test_export_dataset_v2_writes_five_cohorts_and_all_artifacts(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    artifact_root = tmp_path / "artifacts_root"
    result = export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2, show_progress=False)
    assert result["gate"] == "PASS"
    dataset_path = artifact_root / "datasets" / "stability-900-split-v2.jsonl"
    assert dataset_path.exists()
    rows = [json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines()]
    assert rows and all(row["schema_version"] == DATASET_SCHEMA for row in rows)
    splits_seen = {row["split"] for row in rows}
    assert splits_seen == {"train", "validation", "test", "opponent_holdout", "deck_holdout"}
    for name in ("dataset_split_manifest_v2.json", "dataset_composition_v2.json", "dataset_teacher_distribution_v2.json",
                 "dataset_leakage_check_v2.json", "dataset_quality_report_v2.json", "dataset_split_remediation_verdict.json"):
        assert (artifact_root / "artifacts" / name).exists(), name
    teacher = json.loads((artifact_root / "artifacts" / "dataset_teacher_distribution_v2.json").read_text(encoding="utf-8"))
    assert teacher["single_teacher_finding"]["all_teachers_rule_v0"] is True
    leakage = json.loads((artifact_root / "artifacts" / "dataset_leakage_check_v2.json").read_text(encoding="utf-8"))
    assert leakage["episode_leakage"] == 0 and leakage["opponent_holdout_leakage"] == 0 and leakage["deck_holdout_leakage"] == 0
    verdict = json.loads((artifact_root / "artifacts" / "dataset_split_remediation_verdict.json").read_text(encoding="utf-8"))
    assert verdict["verdict"] == "READY_FOR_STUDENT_V1_TRAINING"


def test_export_dataset_v2_rejects_second_write_to_same_output(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    artifact_root = tmp_path / "artifacts_root"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    try:
        export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    except ContractError:
        return
    raise AssertionError("expected ContractError on duplicate dataset output")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: FAIL with `ImportError: cannot import name 'default_worker_count'`

- [ ] **Step 3: Implement**

Add `import tqdm` to the top-level imports in `src/mage_ptcg/offline_scaleup/pipeline.py` (after the `from mage_ptcg.student.model import ...` line):

```python
from tqdm import tqdm
```

Add near the top of the file, right after the module-level constants (after `FAMILY_LOADER = "family_specific_external_v1"`):

```python
def default_worker_count() -> int:
    """CPU-affinity-based default: ~80% of usable cores, minimum 1.

    No psutil dependency is pinned in this project, so this is a CPU-only
    approximation; there is no GPU-bound step anywhere in this pipeline.
    """
    try:
        cpu = len(os.sched_getaffinity(0))
    except AttributeError:
        cpu = os.cpu_count() or 1
    return max(1, int(cpu * 0.8))
```

Add the following after `validate_split_gate` (end of Task 3's additions), still in `pipeline.py`. This needs a module-level (picklable) worker function for the process pool:

```python
def _build_episode_records(args: tuple[dict[str, Any], str]) -> list[dict[str, Any]]:
    game, population_digest = args
    return [_teacher_dataset_record(game, sample, population_digest) for sample in game.get("teacher_samples", [])]


def _composition_report(records: list[dict[str, Any]], manifest: Mapping[str, Any]) -> dict[str, Any]:
    per_split: dict[str, dict[str, Any]] = {}
    for split_name in ("train", "validation", "test", "opponent_holdout", "deck_holdout"):
        split_records = [r for r in records if r["split"] == split_name]
        episodes = {r["episode_id"] for r in split_records}
        per_split[split_name] = {
            "episodes": len(episodes), "records": len(split_records),
            "unique_opponents": len({manifest["episode_opponent"][str(e)] for e in episodes}),
            "unique_decks": len({r["opponent_deck_fingerprint"] for r in split_records}),
            "sides": dict(sorted(Counter(manifest["episode_side"][str(e)] for e in episodes).items())),
        }
    return {"schema_version": "offline-scaleup-dataset-composition-v2", "records_total": len(records),
            "episodes_total": manifest["episode_count"], "splits": per_split}


def _teacher_distribution_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    def _by(field: str) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for value, group in _group(records, field).items():
            out[str(value)] = {"episodes": len({r["episode_id"] for r in group}), "records": len(group)}
        return out
    identities = {r["teacher_identity"] for r in records}
    types = {r["teacher_type"] for r in records}
    trusts = {r["teacher_trust"] for r in records}
    all_rule_v0 = identities == {"rule-v0-current-deck"} and types == {"RULE_V0_DECK"} and trusts == {"TRUSTED"}
    return {"schema_version": "offline-scaleup-dataset-teacher-distribution-v2",
            "teacher_identity": _by("teacher_identity"), "teacher_type": _by("teacher_type"), "teacher_trust": _by("teacher_trust"),
            "opponent_type": _by("opponent_type"), "opponent_id": _by("opponent_id"), "candidate_side": _by("candidate_side"),
            "deck_fingerprint": _by("opponent_deck_fingerprint"), "split": _by("split"),
            "single_teacher_finding": {
                "all_teachers_rule_v0": all_rule_v0,
                "statement": ("全教師記録の teacher_identity/teacher_type/teacher_trust は rule-v0-current-deck / RULE_V0_DECK / TRUSTED のみである。"
                              "これは実データを集計した事実でありエラーではない。Student v1 は Rule v0 の Behavior Cloning baseline である。"
                              "FAMILY_SPECIFIC / TEAM_NATIVE の opponent は状態分布を多様化する対戦相手であり、教師方策ではない。") if all_rule_v0 else
                             "教師分布が単一ではない。teacher_identity/teacher_type別の内訳を確認すること。"}}


def _group(records: list[dict[str, Any]], field: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        out[record[field]].append(record)
    return out


def _leakage_report(manifest: Mapping[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    assignment = manifest["episode_assignment"]
    episode_to_splits: dict[str, set[str]] = defaultdict(set)
    for record in records:
        episode_to_splits[str(record["episode_id"])].add(record["split"])
    duplicate_split_episodes = sum(1 for splits in episode_to_splits.values() if len(splits) > 1)
    unassigned = sum(1 for episode_id in manifest["episode_opponent"] if episode_id not in assignment)
    opponent_holdout_leakage = sum(1 for r in records if r["split"] != "opponent_holdout" and r["opponent_id"] == manifest["opponent_holdout_id"])
    deck_holdout_leakage = sum(1 for r in records if r["split"] not in {"deck_holdout", "opponent_holdout"} and r["opponent_deck_fingerprint"] == manifest["deck_holdout_fingerprint"] and r["opponent_type"] == "RULE_V0_DECK")
    episode_leakage = sum(1 for splits in episode_to_splits.values() if len(splits) > 1)
    return {"schema_version": "offline-scaleup-dataset-leakage-check-v2", "episode_leakage": episode_leakage,
            "opponent_holdout_leakage": opponent_holdout_leakage, "deck_holdout_leakage": deck_holdout_leakage,
            "unassigned_episodes": unassigned, "duplicate_split_episodes": duplicate_split_episodes}


def _quality_report(records: list[dict[str, Any]], gate: Mapping[str, Any]) -> dict[str, Any]:
    illegal = 0
    for record in records:
        legal_digests = {item["digest"] for item in record["legal_action_candidates"]}
        if not set(record["selected_action"]).issubset(legal_digests):
            illegal += 1
    quarantined = sum(1 for r in records if r["teacher_trust"] not in {"TRUSTED", "LIMITED"})
    provenance_missing = sum(1 for r in records if not r.get("provenance", {}).get("population_digest") or not r.get("provenance", {}).get("source_revision"))
    return {"schema_version": "offline-scaleup-dataset-quality-report-v2", "parse_valid": True, "records": len(records),
            "illegal_selected_actions": illegal, "quarantined_teacher_records": quarantined,
            "provenance_missing": provenance_missing, "split_gate": gate}


def export_dataset_v2(*, run_dir: Path, population_path: Path, artifact_root: Path, workers: int | None = None, show_progress: bool = True) -> dict[str, Any]:
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    population = _read_json(population_path)
    entries = _population_entries_by_id(population)
    dataset_path = artifact_root / "datasets" / "stability-900-split-v2.jsonl"
    if dataset_path.exists():
        raise ContractError("dataset output already exists")
    valid_games = _valid_terminal_games(run_dir)
    jobs = [(game, manifest["population_digest"]) for game in valid_games]
    resolved_workers = workers if workers is not None else default_worker_count()
    from concurrent.futures import ProcessPoolExecutor
    per_game_records: list[list[dict[str, Any]]]
    progress = tqdm(total=len(jobs), desc="offline-scaleup: building dataset records", disable=not show_progress)
    if resolved_workers <= 1 or len(jobs) < 2:
        per_game_records = []
        for job in jobs:
            per_game_records.append(_build_episode_records(job))
            progress.update(1)
    else:
        per_game_records = [None] * len(jobs)  # type: ignore[list-item]
        with ProcessPoolExecutor(max_workers=resolved_workers) as pool:
            for index, result in zip(range(len(jobs)), pool.map(_build_episode_records, jobs)):
                per_game_records[index] = result
                progress.update(1)
    progress.close()
    records: list[dict[str, Any]] = []
    for game, game_records in zip(valid_games, per_game_records):
        episode_id = str(game["game_id"])
        opponent_id = manifest["episode_opponent"][episode_id]
        entry = entries[opponent_id]
        split = manifest["episode_assignment"][episode_id]
        for record in game_records:
            record["split"] = split
            record["opponent_id"] = opponent_id
            record["opponent_type"] = entry["opponent_type"]
            record["opponent_deck_fingerprint"] = entry["deck_fingerprint"]
            record["family_id"] = entry.get("family_id")
            records.append(record)
    if not records:
        raise ContractError("no valid teacher decisions available for split dataset")
    for record in tqdm(records, desc="offline-scaleup: writing dataset", disable=not show_progress):
        _write_jsonl_once(dataset_path, record)
    composition = _composition_report(records, manifest)
    teacher_distribution = _teacher_distribution_report(records)
    leakage = _leakage_report(manifest, records)
    quality = _quality_report(records, gate)
    reasons = list(gate["failures"])
    if leakage["episode_leakage"] or leakage["opponent_holdout_leakage"] or leakage["deck_holdout_leakage"] or leakage["unassigned_episodes"]:
        reasons.append("leakage_detected")
    if quality["illegal_selected_actions"] or quality["provenance_missing"]:
        reasons.append("quality_check_failed")
    verdict_value = "READY_FOR_STUDENT_V1_TRAINING" if gate["gate"] == "PASS" and not reasons else \
        ("READY_AFTER_LIMITED_DATASET_FIX" if gate["gate"] == "PASS" else "DATASET_SPLIT_REWORK_REQUIRED")
    verdict = {"schema_version": "offline-scaleup-dataset-split-remediation-verdict-v1", "verdict": verdict_value,
               "reasons": reasons, "split_counts": manifest["split_counts"], "gate_minimums": MIN_SPLIT_EPISODES,
               "opponent_holdout_id": manifest["opponent_holdout_id"], "deck_holdout_fingerprint": manifest["deck_holdout_fingerprint"],
               "cabt_rerun": 0, "dataset": str(dataset_path)}
    _atomic_json(artifact_root / "artifacts" / "dataset_split_manifest_v2.json", manifest)
    _atomic_json(artifact_root / "artifacts" / "dataset_composition_v2.json", composition)
    _atomic_json(artifact_root / "artifacts" / "dataset_teacher_distribution_v2.json", teacher_distribution)
    _atomic_json(artifact_root / "artifacts" / "dataset_leakage_check_v2.json", leakage)
    _atomic_json(artifact_root / "artifacts" / "dataset_quality_report_v2.json", quality)
    _atomic_json(artifact_root / "artifacts" / "dataset_split_remediation_verdict.json", verdict)
    return {"dataset": str(dataset_path), "manifest": str(artifact_root / "artifacts" / "dataset_split_manifest_v2.json"),
            "gate": gate["gate"], "verdict": verdict_value, "records": len(records), "episodes": manifest["episode_count"],
            "split_counts": manifest["split_counts"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: all pass (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py tests/test_offline_scaleup_dataset_split.py
git commit -m "feat(offline-scaleup): export_dataset_v2 で5 cohort Dataset と6成果物を生成

- ProcessPoolExecutorとtqdmで並列化・進捗表示に対応
- workers未指定時はCPU affinityの80%を既定値にする"
```

---

## Task 5: Wire `export-dataset-v2-split` into the CLI

**Files:**
- Modify: `src/mage_ptcg/offline_scaleup/pipeline.py:687-725` (`_parser`, `main`)

**Interfaces:**
- Consumes: `export_dataset_v2`

- [ ] **Step 1: Add the subcommand to `_parser()`**

In `src/mage_ptcg/offline_scaleup/pipeline.py`, right after the `export = sub.add_parser("export-dataset"); ...` line, add:

```python
    export_v2 = sub.add_parser("export-dataset-v2-split")
    export_v2.add_argument("--run-dir", type=Path, required=True)
    export_v2.add_argument("--population", type=Path, required=True)
    export_v2.add_argument("--artifact-root", type=Path, required=True)
    export_v2.add_argument("--workers", type=int, default=None)
    export_v2.add_argument("--no-progress", action="store_true")
```

- [ ] **Step 2: Handle it in `main()`**

In `src/mage_ptcg/offline_scaleup/pipeline.py`, right after the `elif args.command == "export-dataset": ...` line, add:

```python
        elif args.command == "export-dataset-v2-split":
            result = export_dataset_v2(run_dir=args.run_dir, population_path=args.population, artifact_root=args.artifact_root, workers=args.workers, show_progress=not args.no_progress)
            return 0 if result["gate"] == "PASS" else 2
```

- [ ] **Step 3: Smoke-test the CLI help resolves without error**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m mage_ptcg.offline_scaleup export-dataset-v2-split --help`
Expected: argparse help text listing `--run-dir`, `--population`, `--artifact-root`, `--workers`, `--no-progress`, exit code 0

- [ ] **Step 4: Commit**

```bash
git add src/mage_ptcg/offline_scaleup/pipeline.py
git commit -m "feat(offline-scaleup): export-dataset-v2-split CLIサブコマンドを追加"
```

---

## Task 6: Regenerate the real dataset from the `stability-1000` run

**Files:**
- No file changes — this runs the CLI built in Tasks 1-5 against the real artifact root.

- [ ] **Step 1: Confirm the target dataset directory has no pre-existing v2 output**

Run: `ls /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/datasets/`
Expected: only `stability-1000.jsonl` and `stability-1000.summary.json` present (no `stability-900-split-v2.jsonl`)

- [ ] **Step 2: Run the real export against the `stability-1000` run**

Run:
```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONPATH=.:src python3 -m mage_ptcg.offline_scaleup export-dataset-v2-split \
  --run-dir /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/runs/stability-1000 \
  --population /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/artifacts/expanded_population_snapshot.json \
  --artifact-root /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1
```
Expected: exit code 0, printed JSON with `"gate":"PASS"`. This reads only already-computed `game_results.jsonl` — it does not invoke CABT or any subprocess game execution.

- [ ] **Step 3: Verify the existing dataset is untouched**

Run:
```bash
python3 -c "
import hashlib, sys
path = '/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/datasets/stability-1000.jsonl'
with open(path, 'rb') as f:
    print(hashlib.sha256(f.read()).hexdigest())
"
```
Expected: identical hash to a hash taken before this task started (record it in Step 1 of Task 6 before running the export, e.g. `sha256sum stability-1000.jsonl` captured beforehand — compare both values in the final report).

- [ ] **Step 4: Summarize the 6 new artifacts with a small aggregation script (do not paste full JSON into chat)**

Run:
```bash
python3 -c "
import json
root = '/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/artifacts'
for name in ('dataset_split_manifest_v2.json', 'dataset_composition_v2.json', 'dataset_teacher_distribution_v2.json', 'dataset_leakage_check_v2.json', 'dataset_quality_report_v2.json', 'dataset_split_remediation_verdict.json'):
    data = json.load(open(f'{root}/{name}'))
    print(name, len(json.dumps(data)), 'bytes')
verdict = json.load(open(f'{root}/dataset_split_remediation_verdict.json'))
print(json.dumps({k: v for k, v in verdict.items() if k != 'reasons' or v}, ensure_ascii=False, indent=2))
"
```
Expected: verdict `"READY_FOR_STUDENT_V1_TRAINING"`, all split counts ≥ their `MIN_SPLIT_EPISODES` minimum. Record the printed summary for the final report — this is the 20KB-or-under summary the task requires.

- [ ] **Step 5: No commit in this task** (no tracked files changed; the new files live under `handoff-artifacts/`, outside the git repository root under version control — confirm with `git status --short` that nothing changed)

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && git status --short`
Expected: no new lines beyond what was already present before this task (the artifact root is outside the repo).

---

## Task 7: Parametrize `05_train_student_v1.sh` with a dataset argument and pre-flight split-gate check

**Files:**
- Modify: `scripts/offline_scaleup/05_train_student_v1.sh`
- Test: manual CLI smoke test (shell scripts have no pytest harness in this repo)

- [ ] **Step 1: Read current script and design the parametrization**

Current (`scripts/offline_scaleup/05_train_student_v1.sh`):
```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/05_train_student_v1.log"
printf 'phase=train-student-v1 workers=%s\n' "$WORKERS"
python3 -m mage_ptcg.offline_scaleup train-student-v1 --dataset "$ARTIFACT_ROOT/datasets/stability-1000.jsonl" --model-dir "$ARTIFACT_ROOT/models/student-v1" >"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --phase training --model-dir "$ARTIFACT_ROOT/models/student-v1"
printf 'completed=1 planned=1 valid=1 fault_count=0 throughput=n/a summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_training_summary.json" "$ROOT/scripts/offline_scaleup/06_evaluate_holdout.sh $ARTIFACT_ROOT $WORKERS"
```

- [ ] **Step 2: Write the new version**

Overwrite `scripts/offline_scaleup/05_train_student_v1.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; ARTIFACT_ROOT="${1:-/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-opponent-league-training-v2}"
WORKERS="${2:-2}"; DATASET="${3:-$ARTIFACT_ROOT/datasets/stability-900-split-v2.jsonl}"
export PYTHONPATH="$ROOT:$ROOT/src"; LOG="$ARTIFACT_ROOT/logs/05_train_student_v1.log"
if [ ! -f "$DATASET" ]; then
  echo "dataset not found: $DATASET (pass the dataset path as the 3rd argument)" >&2
  exit 3
fi
printf 'phase=train-student-v1 workers=%s dataset=%s\n' "$WORKERS" "$DATASET"
python3 -c '
import json, sys
from collections import Counter
minimums = {"train": 500, "validation": 50, "test": 50, "opponent_holdout": 50, "deck_holdout": 50}
episodes = {}
with open(sys.argv[1], encoding="utf-8") as handle:
    for line in handle:
        row = json.loads(line)
        episodes[row["episode_id"]] = row["split"]
counts = Counter(episodes.values())
failures = [f"{name}<{minimum} (actual={counts.get(name, 0)})" for name, minimum in minimums.items() if counts.get(name, 0) < minimum]
if failures:
    print("SPLIT_GATE_BLOCKED: " + "; ".join(failures), file=sys.stderr)
    sys.exit(4)
print("SPLIT_GATE_PASS: " + json.dumps(dict(sorted(counts.items()))))
' "$DATASET"
python3 -m mage_ptcg.offline_scaleup train-student-v1 --dataset "$DATASET" --model-dir "$ARTIFACT_ROOT/models/student-v1" >"$LOG" 2>&1
python3 "$ROOT/scripts/offline_scaleup/summarize_run.py" --artifact-root "$ARTIFACT_ROOT" --phase training --model-dir "$ARTIFACT_ROOT/models/student-v1"
printf 'completed=1 planned=1 valid=1 fault_count=0 throughput=n/a summary=%s next_command=%s\n' "$ARTIFACT_ROOT/summaries/latest_training_summary.json" "$ROOT/scripts/offline_scaleup/06_evaluate_holdout.sh $ARTIFACT_ROOT $WORKERS $DATASET"
```

Note: `evaluate-holdout` in `pipeline.py` already takes `--dataset`; `06_evaluate_holdout.sh` should be checked in Step 3 — if it also hard-codes `stability-1000.jsonl`, apply the same 3rd-argument pattern there for consistency (read the file first).

- [ ] **Step 3: Check and, if needed, parametrize `06_evaluate_holdout.sh` the same way**

Run: `cat /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/scripts/offline_scaleup/06_evaluate_holdout.sh`

If it hard-codes `$ARTIFACT_ROOT/datasets/stability-1000.jsonl`, replace that with `DATASET="${3:-$ARTIFACT_ROOT/datasets/stability-900-split-v2.jsonl}"` and use `"$DATASET"` in the `evaluate-holdout --dataset` call, following the same pattern as Step 2. Preserve everything else in the file unchanged.

- [ ] **Step 4: Smoke-test the split-gate failure path**

Run:
```bash
mkdir -p /tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/gate-smoke
printf '%s\n' '{"episode_id":"e1","split":"train"}' > /tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/gate-smoke/tiny.jsonl
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
bash scripts/offline_scaleup/05_train_student_v1.sh /tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/gate-smoke 1 /tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/gate-smoke/tiny.jsonl; echo "exit=$?"
```
Expected: stderr contains `SPLIT_GATE_BLOCKED`, `exit=4`

- [ ] **Step 5: Smoke-test the missing-dataset path**

Run: `bash /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/scripts/offline_scaleup/05_train_student_v1.sh /tmp/claude-1000/-home-bfe-lab-ono-kaggle-pokemon-tcg-ai-battle/4ef254b4-13ab-45b0-bc38-701e3cd3704e/scratchpad/gate-smoke 1 /nonexistent/dataset.jsonl; echo "exit=$?"`
Expected: stderr `dataset not found: ...`, `exit=3`

- [ ] **Step 6: Commit**

```bash
git add scripts/offline_scaleup/05_train_student_v1.sh scripts/offline_scaleup/06_evaluate_holdout.sh
git commit -m "fix(offline-scaleup): Student training scriptがv2 Datasetを選択可能にし、split Gateを事前検証する

- Dataset pathを第3引数化しhard-codeを解消
- train/validation/test/opponent_holdout/deck_holdout の最低件数を満たさない場合はexit非0で停止"
```

(If `06_evaluate_holdout.sh` required no change, drop it from the `git add`/commit and note that in the final report.)

---

## Task 8: Remaining split-integrity tests (episode atomicity, side preservation, empty-holdout rejection, manifest round-trip, real-run regression)

**Files:**
- Modify: `tests/test_offline_scaleup_dataset_split.py`

- [ ] **Step 1: Write the remaining failing/new tests**

Append to `tests/test_offline_scaleup_dataset_split.py`:

```python
def test_episode_atomicity_every_episode_records_share_one_split(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    artifact_root = tmp_path / "atomicity"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2, show_progress=False)
    rows = [json.loads(line) for line in (artifact_root / "datasets" / "stability-900-split-v2.jsonl").read_text(encoding="utf-8").splitlines()]
    by_episode: dict[str, set[str]] = {}
    for row in rows:
        by_episode.setdefault(row["episode_id"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in by_episode.values())


def test_side_preservation_holdouts_contain_both_candidate_sides(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    artifact_root = tmp_path / "sides"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=2, show_progress=False)
    rows = [json.loads(line) for line in (artifact_root / "datasets" / "stability-900-split-v2.jsonl").read_text(encoding="utf-8").splitlines()]
    for split_name in ("opponent_holdout", "deck_holdout"):
        sides = {row["candidate_side"] for row in rows if row["split"] == split_name}
        assert sides == {0, 1}, f"{split_name} missing a side: {sides}"


def test_empty_holdout_pool_is_rejected(tmp_path: Path) -> None:
    entries = [
        {"opponent_id": "rule-v0-current-deck", "opponent_type": "RULE_V0_DECK", "source_path": "x", "deck_id": "current-deck", "deck_fingerprint": "fp-current", "runtime_id": "r", "runtime_fingerprint": "a" * 64, "agent_digest": "a" * 64, "validation_status": "VALIDATED", "availability_status": "AVAILABLE", "evaluation_eligibility": "ALLOWED", "training_eligibility": "ALLOWED_FOR_VALID_FAULT_FREE_GAMES", "teacher_trust": "TRUSTED", "quarantine_reason": None, "family_id": None, "strategy_tags": [], "variant_tags": [], "evidence_paths": []},
    ]
    population = {"schema_version": "offline-scaleup-population-v2", "entries": entries, "semantic_population_digest": "e" * 64, "alias_count": 0, "created_by": "test", "population_id": "population-test"}
    population_path = tmp_path / "population.json"
    population_path.write_text(json.dumps(population), encoding="utf-8")
    schedule = build_schedule(population, candidate="rule-v0-current-deck", opponents=["rule-v0-current-deck"], games=6, base_seed=7)
    run_dir = tmp_path / "run"; run_dir.mkdir()
    (run_dir / "schedule.json").write_text(json.dumps(schedule), encoding="utf-8")
    example = build_rule_bc_example(_observation(), deck=[1] * 60, source_id="fixture", source_revision="test")
    for game in schedule["games"]:
        _write_jsonl_once(run_dir / "game_results.jsonl", {"schema_version": RESULT_SCHEMA, **game, "status": "DONE", "legal": True,
                           "candidate_fault": False, "mapping_valid": True, "score_identity_valid": True,
                           "teacher_samples": [example.to_dict()], "fault": {"kind": "COMPLETED"}})
    from mage_ptcg.offline_scaleup.pipeline import summarize_run
    summarize_run(run_dir)
    try:
        build_split_manifest(run_dir=run_dir, population_path=population_path)
    except ContractError as exc:
        assert "opponent-holdout" in str(exc)
        return
    raise AssertionError("expected ContractError for empty opponent-holdout pool")


def test_insufficient_split_is_rejected_by_gate_not_by_silent_pass(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=4)
    manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
    gate = validate_split_gate(manifest)
    assert gate["gate"] == "BLOCKED"


def test_split_manifest_round_trip_through_disk(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    artifact_root = tmp_path / "roundtrip"
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    manifest_path = artifact_root / "artifacts" / "dataset_split_manifest_v2.json"
    reloaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_rows = [json.loads(line) for line in (artifact_root / "datasets" / "stability-900-split-v2.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in dataset_rows:
        assert reloaded["episode_assignment"][row["episode_id"]] == row["split"]


def test_old_dataset_file_is_never_touched_by_v2_export(tmp_path: Path) -> None:
    run_dir, population_path = _build_run(tmp_path, games_per_opponent=200)
    artifact_root = tmp_path / "old-dataset-guard"
    old_dataset = artifact_root / "datasets" / "stability-1000.jsonl"
    old_dataset.parent.mkdir(parents=True)
    old_dataset.write_text('{"marker": "do-not-touch"}\n', encoding="utf-8")
    before = old_dataset.read_bytes()
    export_dataset_v2(run_dir=run_dir, population_path=population_path, artifact_root=artifact_root, workers=1, show_progress=False)
    assert old_dataset.read_bytes() == before
```

- [ ] **Step 2: Run the full new test module**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_dataset_split.py -v`
Expected: all pass. If any fail due to a real gap (not a typo), fix the implementation in `pipeline.py`, not the test — these tests encode the spec's explicit minimum test list.

- [ ] **Step 3: Run the real 900-game run through the new gate/manifest functions as a regression check (no re-export, read-only)**

Run:
```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONPATH=.:src python3 -c "
from pathlib import Path
from mage_ptcg.offline_scaleup.pipeline import build_split_manifest, validate_split_gate
run_dir = Path('/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/runs/stability-1000')
population_path = Path('/home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/artifacts/expanded_population_snapshot.json')
manifest = build_split_manifest(run_dir=run_dir, population_path=population_path)
gate = validate_split_gate(manifest)
print(gate['gate'], manifest['split_counts'], manifest['opponent_holdout_id'], manifest['deck_holdout_fingerprint'][:16])
"
```
Expected: `PASS {'deck_holdout': .., 'opponent_holdout': .., 'test': .., 'train': .., 'validation': ..} <opponent-id> <fp-prefix>` — must match the counts already produced by Task 6's real export exactly (same deterministic function, called twice, must agree; confirms the 900-game run itself is unchanged and idempotent).

- [ ] **Step 4: Run the full existing offline-scaleup test suite to confirm no regression**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && PYTHONPATH=.:src python3 -m pytest tests/test_offline_scaleup_pipeline.py tests/test_offline_scaleup_worker_contract.py tests/test_offline_scaleup_dataset_split.py -v`
Expected: all pass, 0 failures

- [ ] **Step 5: Commit**

```bash
git add tests/test_offline_scaleup_dataset_split.py
git commit -m "test(offline-scaleup): split remediationのepisode atomicity／leakage／round-tripを網羅"
```

---

## Task 9: Final verdict, status docs, and handoff report

**Files:**
- Modify: `docs/status/current_status.md`, `docs/status/handoff.md` (only if these files exist and already track offline-scaleup progress — read them first; append, don't restructure)
- No code changes

- [ ] **Step 1: Read the current status/handoff docs to find the offline-scaleup section**

Run: `grep -n "offline.scaleup\|stability-1000\|Student v1" /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/status/current_status.md /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/status/handoff.md 2>&1 | head -40`

- [ ] **Step 2: Append a dated entry to both files** (format: match whatever heading/date convention the existing file already uses; do not reformat unrelated sections). Content: dataset split remediation done, verdict, split counts, holdout IDs, next command for Student v1 training, evidence path pointing at `artifacts/dataset_split_remediation_verdict.json`.

- [ ] **Step 3: Verify no protected file and no untracked file was touched**

Run: `cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && git status --short`
Expected: only the files this plan intentionally modified/created appear; `main.py`, `deck.csv`, `agents/rule_agent.py`, `agents/rule_agent_v1.py`, `src/mage_ptcg/evaluation/promotion.py` absent; the pre-existing untracked files from session start (`.codex/hooks.json`, `.codex/hooks/`, `generate_audit_artifacts.py`, `o6_continue_after_team_permission.md*`, `pokemon_team_agents_internal_v1.yaml*`, `scripts/build_o6_taxonomy.py`) still show as untracked and unstaged, unmodified.

- [ ] **Step 4: Commit**

```bash
git add docs/status/current_status.md docs/status/handoff.md
git commit -m "docs(offline-scaleup): dataset split remediation v2の状態を記録"
```

- [ ] **Step 5: Compose the final report** (to the user, not a file) covering exactly the 8 points the spec requires: root cause, split algorithm, per-split episode/record counts, holdout selections, teacher distribution finding, leakage results, local commit list (`git log --oneline` since this task's first commit), and the exact next Student v1 training command:
```
bash scripts/offline_scaleup/05_train_student_v1.sh /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1 <workers> /home/bfe-lab-ono/kaggle/handoff-artifacts/offline-scaleup-population-diversity-expansion-v1/datasets/stability-900-split-v2.jsonl
```
End the report with the literal line `OFFLINE_SCALEUP_DATASET_SPLIT_REMEDIATION_READY` only if the verdict from Task 6/8 was `READY_FOR_STUDENT_V1_TRAINING`; otherwise state which verdict was actually reached and why, and do not print that marker.

---

## Self-review notes

- Spec coverage: 5 cohorts (Task 3/4), deterministic hash holdouts with no hard-coding (Task 1), teacher distribution honesty (Task 4's `_teacher_distribution_report`), leakage/quality/composition/manifest/verdict artifacts (Task 4), dataset regeneration without CABT re-run (Task 6, reads only `game_results.jsonl`), training script parametrization + pre-flight gate (Task 7), full test list from the spec (episode atomicity, min gates, holdout exclusivity, side preservation, empty/insufficient rejection, manifest round-trip, old-dataset-unchanged, 900-game-run-unchanged — Task 8), final verdict + report (Task 9).
- No placeholders: every step has literal code or literal commands with expected output.
- Type consistency checked: `build_split_manifest` returns `episode_assignment`/`episode_opponent`/`episode_side` used identically by `export_dataset_v2`, `_composition_report`, `_leakage_report`; `default_worker_count()` name matches between Task 4's definition and Task 4/7 usage; `MIN_SPLIT_EPISODES` defined once in Task 3, imported by name in Task 4 and duplicated intentionally (as a plain literal, not an import) inside the shell one-liner in Task 7 since shell can't import Python module constants — flagged inline in that step so the two lists are kept in sync by a human reader.
