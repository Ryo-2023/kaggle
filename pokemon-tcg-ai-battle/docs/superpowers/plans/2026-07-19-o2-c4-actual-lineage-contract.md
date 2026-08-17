# O2 → C4 Actual Lineage Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This plan is executed **inline in the same session that wrote it** (author has full investigation context); no subagent dispatch is required.

**Goal:** Let O2's Rule-vs-Random match plan (`match_id`, seat, opponent, agent/deck lineage) flow, backward-compatibly, into the existing C4 collector's private binding, so `O2 match plan → cabt execution → C4 private binding → C4 episode → decisions → dataset records → NEURAL_ACTUAL_TRAINED artifact → evaluation` is mechanically traceable end to end.

**Architecture:** `collect_actual_dataset()` gains an *optional* `episode_lineage_inputs` parameter (list of a new frozen `ActualEpisodeLineageInput` DTO, one per game) plus `opponent_deck_path`/`opponent_agent_factory`. When absent, behavior is byte-for-byte identical to today (legacy Rule-self-play). When present, the existing per-game loop uses O2's `match_id` as `episode_group_id` (instead of `f"{run_id}-g{i}"`), O2's `requested_seed` as the game seed (instead of `base_seed+game_index`), and drives one seat with the existing Rule-capturing wrapper and the other seat with a caller-supplied plain (non-capturing) delegate — so gameplay is genuinely Rule-vs-Random, not relabeled self-play. Lineage is merged into the *existing* private binding dict at commit time (no new binding class) and, in aggregate (`match_id`, `plan_hash` only — already public via O2's own `match_plan.json`), into the *existing* public `dataset_manifest.json`/`public_summary.json`. A new adapter module in `o2_training_loop` builds the match plan (reusing `build_match_matrix`, `resolve_real_agent`, `resolve_real_deck` unmodified) and calls `collect_actual_dataset` in O2 mode. The rest of the Offline Training v1 pipeline (`build_dataset`, `neural.train`, `export`, `package`, `evaluate`) is reused unmodified by driving `Pipeline` phase methods directly from a new script, with the `collect` phase pre-seeded and marked complete instead of re-run.

**Tech Stack:** Python 3, existing `mage_ptcg.dataops`, `mage_ptcg.o2_training_loop`, `mage_ptcg.offline_training` modules. No new dependencies.

## Global Constraints

- No new branch/worktree. Work stays on `feature/o2-minimum-training-loop` in `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o2-mvtl`.
- Legacy `collect_actual_dataset()` call signature and behavior (no new kwargs passed) must be byte-for-byte unchanged: same `episode_group_id` format, same binding keys, same config_hash inputs.
- No new private binding class/schema, no new parallel episode schema. Extend the existing binding `dict` and `RuleBCExample.metadata` only.
- No new Opponent Pool entries, no new decks, no Bounded Search, no learner change, no Promotion Gate change, no Champion change.
- `run_id`, timestamps, filesystem paths must never enter semantic/episode identity. In O2 mode, `episode_group_id = match_id` (a content hash), not `run_id`-derived.
- Public artifacts may carry `match_id` and `plan_hash` only (both already public via O2's own `match_plan.json`/`batch_manifest.json`). Seat, player_side, own/opponent implementation_hash, own/opponent deck_hash, match_spec_hash, requested_seed, pair_id stay in the private binding only.
- All new/changed code must pass `scripts/docs/validate_docs.py`, the full `pytest -q`, and a secret scan before commit.

---

### Task 1: `ActualEpisodeLineageInput` DTO + validation, backward-compatible

**Files:**
- Modify: `src/mage_ptcg/dataops/collector.py`
- Modify: `src/mage_ptcg/dataops/__init__.py` (export the new dataclass)
- Test: `tests/test_c4_data_ops.py`

**Interfaces:**
- Produces: `ActualEpisodeLineageInput` frozen dataclass with fields `match_id: str`, `plan_hash: str`, `match_spec_hash: str`, `backend_kind: str`, `requested_seed: int`, `engine_seed_supported: bool`, `seat_index: int`, `player_side: str`, `own_agent_id: str`, `opponent_agent_id: str`, `own_implementation_hash: str`, `opponent_implementation_hash: str`, `own_deck_hash: str`, `opponent_deck_hash: str`, `pair_id: str | None = None`.
- Produces: `class LineageValidationError(DataOpsError)` — raised for every O2-mode rejection reason (message states the reason, e.g. `"lineage_count_mismatch"`, `"duplicate_match_id"`, `"missing_match_id"`, `"seat_mismatch"`, `"opponent_mismatch"`, `"own_agent_mismatch"`, `"own_implementation_hash_mismatch"`, `"opponent_implementation_hash_mismatch"`, `"own_deck_hash_mismatch"`, `"opponent_deck_hash_mismatch"`, `"fixture_backend_rejected"`).
- Consumes (Task 2): nothing yet — this task only adds the type and a pure validator function `_validate_episode_lineage_inputs(inputs: Sequence[ActualEpisodeLineageInput], *, games: int, own_deck_fingerprint: str, opponent_deck_fingerprint: str) -> None`.

- [ ] **Step 1: Write failing tests for the DTO and validator**

```python
# tests/test_c4_data_ops.py (append)
from mage_ptcg.dataops.collector import ActualEpisodeLineageInput, LineageValidationError, _validate_episode_lineage_inputs


def _lineage(**overrides):
    base = dict(
        match_id="match_abc123", plan_hash="planhash1", match_spec_hash="specHash1",
        backend_kind="cabt", requested_seed=1000, engine_seed_supported=False,
        seat_index=0, player_side="A", own_agent_id="rule-agent-v0",
        opponent_agent_id="random-legal-v0",
        own_implementation_hash="f" * 64, opponent_implementation_hash="a" * 64,
        own_deck_hash="OWNDECKHASH", opponent_deck_hash="OPPDECKHASH", pair_id="pair1",
    )
    base.update(overrides)
    return ActualEpisodeLineageInput(**base)


def test_lineage_validator_accepts_a_consistent_batch():
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", pair_id="pair2"),
    ]
    _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_count_mismatch():
    entries = [_lineage(match_id="m1")]
    with pytest.raises(LineageValidationError, match="lineage_count_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_duplicate_match_id():
    entries = [_lineage(match_id="m1"), _lineage(match_id="m1", seat_index=1, player_side="B")]
    with pytest.raises(LineageValidationError, match="duplicate_match_id"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_missing_match_id():
    entries = [_lineage(match_id=""), _lineage(match_id="m2", seat_index=1, player_side="B")]
    with pytest.raises(LineageValidationError, match="missing_match_id"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_seat_mismatch():
    entries = [_lineage(match_id="m1", seat_index=0, player_side="B")]
    with pytest.raises(LineageValidationError, match="seat_mismatch"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_opponent_mismatch_across_batch():
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", opponent_agent_id="student-v0"),
    ]
    with pytest.raises(LineageValidationError, match="opponent_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_own_implementation_hash_mismatch():
    entries = [
        _lineage(match_id="m1", seat_index=0, player_side="A"),
        _lineage(match_id="m2", seat_index=1, player_side="B", own_implementation_hash="0" * 64),
    ]
    with pytest.raises(LineageValidationError, match="own_implementation_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=2, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_own_deck_hash_mismatch():
    entries = [_lineage(match_id="m1", seat_index=0, player_side="A")]
    with pytest.raises(LineageValidationError, match="own_deck_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="DIFFERENT", opponent_deck_fingerprint="OPPDECKHASH")


def test_lineage_validator_rejects_opponent_deck_hash_mismatch():
    entries = [_lineage(match_id="m1", seat_index=0, player_side="A")]
    with pytest.raises(LineageValidationError, match="opponent_deck_hash_mismatch"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="DIFFERENT")


def test_lineage_validator_rejects_fixture_backend():
    entries = [_lineage(match_id="m1", seat_index=0, player_side="A", backend_kind="fixture_backend")]
    with pytest.raises(LineageValidationError, match="fixture_backend_rejected"):
        _validate_episode_lineage_inputs(entries, games=1, own_deck_fingerprint="OWNDECKHASH", opponent_deck_fingerprint="OPPDECKHASH")
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_c4_data_ops.py -k lineage_validator -v`
Expected: FAIL (`ImportError` — names do not exist yet).

- [ ] **Step 3: Implement `ActualEpisodeLineageInput` and `_validate_episode_lineage_inputs` in `collector.py`**

Add near the top of `src/mage_ptcg/dataops/collector.py`, after `class DataOpsError` (collector.py:93-94):

```python
class LineageValidationError(DataOpsError):
    """Raised when O2 lineage inputs are missing, inconsistent, or unsafe."""


@dataclass(frozen=True, slots=True)
class ActualEpisodeLineageInput:
    """One O2 match's lineage, merged into the existing private binding at commit time.

    This is an adapter input DTO only; it is never persisted as its own schema.
    """

    match_id: str
    plan_hash: str
    match_spec_hash: str
    backend_kind: str
    requested_seed: int
    engine_seed_supported: bool
    seat_index: int
    player_side: str
    own_agent_id: str
    opponent_agent_id: str
    own_implementation_hash: str
    opponent_implementation_hash: str
    own_deck_hash: str
    opponent_deck_hash: str
    pair_id: str | None = None


_SEAT_TO_SIDE = {0: "A", 1: "B"}


def _validate_episode_lineage_inputs(
    inputs: Sequence[ActualEpisodeLineageInput],
    *,
    games: int,
    own_deck_fingerprint: str,
    opponent_deck_fingerprint: str,
) -> None:
    if len(inputs) != games:
        raise LineageValidationError("lineage_count_mismatch")
    seen_match_ids: set[str] = set()
    own_agent_ids: set[str] = set()
    opponent_agent_ids: set[str] = set()
    own_impl_hashes: set[str] = set()
    opponent_impl_hashes: set[str] = set()
    own_deck_hashes: set[str] = set()
    opponent_deck_hashes: set[str] = set()
    for entry in inputs:
        if not isinstance(entry.match_id, str) or not entry.match_id:
            raise LineageValidationError("missing_match_id")
        if entry.match_id in seen_match_ids:
            raise LineageValidationError("duplicate_match_id")
        seen_match_ids.add(entry.match_id)
        if entry.backend_kind != "cabt":
            raise LineageValidationError("fixture_backend_rejected")
        if entry.seat_index not in (0, 1) or entry.player_side != _SEAT_TO_SIDE[entry.seat_index]:
            raise LineageValidationError("seat_mismatch")
        own_agent_ids.add(entry.own_agent_id)
        opponent_agent_ids.add(entry.opponent_agent_id)
        own_impl_hashes.add(entry.own_implementation_hash)
        opponent_impl_hashes.add(entry.opponent_implementation_hash)
        own_deck_hashes.add(entry.own_deck_hash)
        opponent_deck_hashes.add(entry.opponent_deck_hash)
    if len(own_agent_ids) > 1:
        raise LineageValidationError("own_agent_mismatch")
    if len(opponent_agent_ids) > 1:
        raise LineageValidationError("opponent_mismatch")
    if len(own_impl_hashes) > 1:
        raise LineageValidationError("own_implementation_hash_mismatch")
    if len(opponent_impl_hashes) > 1:
        raise LineageValidationError("opponent_implementation_hash_mismatch")
    if own_deck_hashes != {own_deck_fingerprint}:
        raise LineageValidationError("own_deck_hash_mismatch")
    if opponent_deck_hashes != {opponent_deck_fingerprint}:
        raise LineageValidationError("opponent_deck_hash_mismatch")
```

- [ ] **Step 4: Export from `src/mage_ptcg/dataops/__init__.py`**

Read the current file first, then add `ActualEpisodeLineageInput` and `LineageValidationError` to whatever import/`__all__` pattern it already uses for `DataOpsError`/`collect_actual_dataset`.

- [ ] **Step 5: Run tests, confirm pass**

Run: `python -m pytest tests/test_c4_data_ops.py -k lineage_validator -v`
Expected: 11 passed.

- [ ] **Step 6: Full focused regression, then commit**

Run: `python -m pytest tests/test_c4_data_ops.py -q`
Expected: all prior 23 + 11 new pass.

```bash
git add src/mage_ptcg/dataops/collector.py src/mage_ptcg/dataops/__init__.py tests/test_c4_data_ops.py
git commit -m "$(cat <<'EOF'
feat(dataops): add O2 lineage input DTO and batch validator

- ActualEpisodeLineageInput is an adapter DTO only; no new persisted schema
- _validate_episode_lineage_inputs fails closed on count/seat/opponent/hash
  mismatches and fixture backend_kind
EOF
)"
```

---

### Task 2: Wire O2 mode into `collect_actual_dataset`'s execution loop

**Files:**
- Modify: `src/mage_ptcg/dataops/collector.py` (`collect_actual_dataset`, `_finalize_run`)
- Test: `tests/test_c4_data_ops.py`

**Interfaces:**
- Consumes: `ActualEpisodeLineageInput`, `_validate_episode_lineage_inputs`, `LineageValidationError` (Task 1).
- Produces: `collect_actual_dataset(..., episode_lineage_inputs=None, opponent_deck_path=None, opponent_agent_factory=None)`. When `episode_lineage_inputs` is `None` (default), behavior is unchanged from today. When provided, `opponent_deck_path` and `opponent_agent_factory` become required (else `LineageValidationError("o2_mode_requires_opponent_wiring")`).
- Produces: binding dict gains an optional `"o2_lineage"` key (full private detail, see field list in Task 1) only in O2 mode; `RuleBCExample.metadata` gains `"o2_match_id"` and `"o2_pair_id"` only in O2 mode. Legacy binding/metadata keys are untouched.
- Produces: public `dataset_manifest`/`public_summary` gain `"o2_lineage_present": bool`, and when `True`, `"o2_plan_hashes": list[str]` and `"o2_match_ids": list[str]` (both already public via O2's own `match_plan.json`).

- [ ] **Step 1: Write failing tests — O2 mode drives a real Rule-vs-alt-agent game and legacy mode is unchanged**

Read `tests/test_c4_data_ops.py` fully first to match its existing fixture/match_runner-stub style (it stubs `match_runner` — do not require real cabt). Add:

```python
def _stub_opponent_factory(deck, seed):
    def agent(observation):
        select = observation.get("select")
        if not isinstance(select, dict):
            return []
        options = select.get("option") or []
        return [0] if options else []
    return agent


def test_legacy_collect_actual_dataset_is_unchanged_without_lineage_inputs(tmp_path, ...):
    # Reuse the existing fixture match_runner/deck fixtures already in this file
    # (see test_collection_smoke_produces_rows_bindings_and_chosen_targets for the pattern).
    # Assert: episode_group_id in the binding is f"{run_id}-g{i}" (unchanged),
    # and no binding contains an "o2_lineage" key.
    ...


def test_o2_lineage_mode_tags_bindings_and_uses_match_id_as_episode_group(tmp_path, ...):
    # Build 2 ActualEpisodeLineageInput entries (seat 0 / seat 1, distinct match_id,
    # shared own/opponent agent+hash), call collect_actual_dataset with
    # episode_lineage_inputs=..., opponent_deck_path=..., opponent_agent_factory=_stub_opponent_factory.
    # Assert: every binding's episode_group_id equals its lineage entry's match_id;
    # every binding has an "o2_lineage" dict with match_id/plan_hash/seat_index/... ;
    # dataset_manifest["o2_lineage_present"] is True and o2_plan_hashes/o2_match_ids are populated;
    # public_summary mirrors the same two fields.
    ...


def test_o2_lineage_mode_rejects_count_mismatch(tmp_path, ...):
    # games=2 but only 1 lineage entry -> DataOpsError/LineageValidationError.


def test_o2_lineage_mode_requires_opponent_wiring(tmp_path, ...):
    # episode_lineage_inputs given, opponent_deck_path/opponent_agent_factory omitted -> LineageValidationError.
```

Fill in the fixture/tmp_path/deck plumbing by copying the exact pattern already used in `test_collection_smoke_produces_rows_bindings_and_chosen_targets` in this file (same `deck_path`, `canonical_base_sha`, `repository_root`, `capability_report` fixtures) — do not invent a new fixture style.

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_c4_data_ops.py -k o2_lineage -v`
Expected: FAIL (new kwargs not accepted yet / AssertionError).

- [ ] **Step 3: Implement the loop changes in `collect_actual_dataset`**

In `src/mage_ptcg/dataops/collector.py`, extend the signature (collector.py:470-485):

```python
def collect_actual_dataset(
    *,
    run_id: str,
    games: int,
    base_seed: int,
    output_root: str | Path,
    canonical_base_sha: str,
    deck_path: str | Path,
    repository_root: str | Path,
    max_steps: int = 10_000,
    validation_percent: int = 20,
    split_seed: int = 0,
    match_runner: Callable[..., Mapping[str, object]] | None = None,
    capability_report: Mapping[str, object] | None = None,
    source_revision: str | None = None,
    episode_lineage_inputs: Sequence[ActualEpisodeLineageInput] | None = None,
    opponent_deck_path: str | Path | None = None,
    opponent_agent_factory: Callable[[Sequence[int], int], Callable[[dict], list[int]]] | None = None,
) -> dict[str, object]:
```

Right after `deck = read_deck_csv(deck_path); deck_fingerprint = canonical_deck_sha256(deck)` (collector.py:516-517), add:

```python
    opponent_deck_fingerprint: str | None = None
    if episode_lineage_inputs is not None:
        if opponent_deck_path is None or opponent_agent_factory is None:
            raise LineageValidationError("o2_mode_requires_opponent_wiring")
        opponent_deck = read_deck_csv(opponent_deck_path)
        opponent_deck_fingerprint = canonical_deck_sha256(opponent_deck)
        _validate_episode_lineage_inputs(
            episode_lineage_inputs,
            games=games,
            own_deck_fingerprint=deck_fingerprint,
            opponent_deck_fingerprint=opponent_deck_fingerprint,
        )
```

Inside the `for game_index in range(games):` loop (collector.py:582), replace the `seed = base_seed + game_index` / `episode_group_id = f"{run_id}-g{game_index}"` / `trace_provenance_hash = ...` block with:

```python
        lineage_entry = episode_lineage_inputs[game_index] if episode_lineage_inputs is not None else None
        seed = lineage_entry.requested_seed if lineage_entry is not None else base_seed + game_index
        episode_group_id = lineage_entry.match_id if lineage_entry is not None else f"{run_id}-g{game_index}"
        if lineage_entry is not None:
            trace_provenance_hash = digest(
                {"match_spec_hash": lineage_entry.match_spec_hash, "plan_hash": lineage_entry.plan_hash, "environment": "cabt"},
                domain="c4-data-ops-trace-v0",
            )
        else:
            trace_provenance_hash = digest(
                {"config_hash": config_hash, "game_index": game_index, "seed": seed, "environment": "cabt"},
                domain="c4-data-ops-trace-v0",
            )
```

Replace the `raw = match_runner(...)` call (collector.py:615-627) with a branch that, in O2 mode, assigns the capturing factory to `lineage_entry.seat_index` and the plain `opponent_agent_factory` to the other seat, with per-seat deck paths:

```python
        if lineage_entry is not None:
            own_seat = lineage_entry.seat_index
            opp_seat = 1 - own_seat
            deck_by_seat = {own_seat: deck_path, opp_seat: opponent_deck_path}
            factory_by_seat = {own_seat: make_factory(own_seat), opp_seat: opponent_agent_factory}
            raw = match_runner(
                deck_a_path=deck_by_seat[0],
                deck_b_path=deck_by_seat[1],
                agent_a_name=lineage_entry.own_agent_id if own_seat == 0 else lineage_entry.opponent_agent_id,
                agent_b_name=lineage_entry.opponent_agent_id if own_seat == 0 else lineage_entry.own_agent_id,
                seed=seed,
                max_steps=max_steps,
                output_dir=run_dir / ".transient",
                save_html=False,
                save_result=False,
                agent_a_factory=factory_by_seat[0],
                agent_b_factory=factory_by_seat[1],
            )
        else:
            raw = match_runner(
                deck_a_path=deck_path,
                deck_b_path=deck_path,
                agent_a_name="rule",
                agent_b_name="rule",
                seed=seed,
                max_steps=max_steps,
                output_dir=run_dir / ".transient",
                save_html=False,
                save_result=False,
                agent_a_factory=make_factory(0),
                agent_b_factory=make_factory(1),
            )
```

Replace the "assign final decision index" block (collector.py:634-643) so lineage is merged in at commit time only:

```python
        rows: list[dict[str, object]] = []
        binds: list[dict[str, object]] = []
        for decision_index, artifacts in enumerate(collected):
            metadata = {**artifacts.example.metadata, "decision_index": str(decision_index)}
            binding = {**artifacts.binding, "decision_index": decision_index}
            if lineage_entry is not None:
                metadata["o2_match_id"] = lineage_entry.match_id
                metadata["o2_pair_id"] = lineage_entry.pair_id or ""
                binding["o2_lineage"] = {
                    "match_id": lineage_entry.match_id,
                    "plan_hash": lineage_entry.plan_hash,
                    "match_spec_hash": lineage_entry.match_spec_hash,
                    "backend_kind": lineage_entry.backend_kind,
                    "requested_seed": lineage_entry.requested_seed,
                    "engine_seed_supported": lineage_entry.engine_seed_supported,
                    "seat_index": lineage_entry.seat_index,
                    "player_side": lineage_entry.player_side,
                    "own_agent_id": lineage_entry.own_agent_id,
                    "opponent_agent_id": lineage_entry.opponent_agent_id,
                    "own_implementation_hash": lineage_entry.own_implementation_hash,
                    "opponent_implementation_hash": lineage_entry.opponent_implementation_hash,
                    "own_deck_hash": lineage_entry.own_deck_hash,
                    "opponent_deck_hash": lineage_entry.opponent_deck_hash,
                    "pair_id": lineage_entry.pair_id,
                }
            example = replace(artifacts.example, metadata=metadata)
            rows.append(example.to_dict())
            binds.append(binding)
```

(Keep everything below — `_write_jsonl`, `completed.add`, `atomic_write_json` of `collection_state.json` — unchanged.)

Note: `_PUBLIC_FORBIDDEN_KEYS`/`scan_public_artifact` never sees `private_dataset/*.jsonl` (those files are not passed to `scan_public_artifact`, per collector.py:856), so the private-only `o2_lineage`/`o2_pair_id`/`o2_match_id` fields are safe by construction — confirm this by reading collector.py:855-856 again before editing to make sure no call site starts scanning the private files.

- [ ] **Step 4: Propagate the public-safe subset into `_finalize_run`**

In `_finalize_run` (collector.py:674+), after `all_binds` is loaded back from disk (collector.py:691-699) and before building `dataset_manifest` (collector.py:820), add:

```python
    o2_entries = [
        bind["o2_lineage"] for bind in all_binds
        if isinstance(bind, Mapping) and isinstance(bind.get("o2_lineage"), Mapping)
    ]
    o2_lineage_present = bool(o2_entries)
    o2_plan_hashes = sorted({str(entry["plan_hash"]) for entry in o2_entries}) if o2_entries else []
    o2_match_ids = sorted({str(entry["match_id"]) for entry in o2_entries}) if o2_entries else []
```

Add to `dataset_manifest` dict literal (collector.py:820-853), right before the closing `**schema, "engineering_gate": engineering_gate,`:

```python
        "o2_lineage_present": o2_lineage_present,
        "o2_plan_hashes": o2_plan_hashes,
        "o2_match_ids": o2_match_ids,
```

Add the same three keys to `public_summary` (collector.py:877-918), in the same relative position (after `"split": {...}` block, before `"compute": compute`).

- [ ] **Step 5: Run tests, confirm pass**

Run: `python -m pytest tests/test_c4_data_ops.py -v`
Expected: all pass, including the new O2-mode tests and every pre-existing test in this file (byte-for-byte legacy behavior confirmed).

- [ ] **Step 6: Run the full pre-existing regression suite touched by this file's contract**

Run: `python -m pytest tests/test_c4_data_ops.py tests/test_c4_actual_training_bundle.py tests/test_offline_training_v1.py -q`
Expected: all pass (these are the tests enumerated in the investigation report §12 that must stay green).

- [ ] **Step 7: Commit**

```bash
git add src/mage_ptcg/dataops/collector.py tests/test_c4_data_ops.py
git commit -m "$(cat <<'EOF'
feat(dataops): drive O2 Rule-vs-opponent games through collect_actual_dataset

- episode_group_id becomes O2's content-hash match_id in O2 mode; legacy
  self-play keeps its existing run_id-derived id unchanged
- opponent seat is played by the caller-supplied opponent_agent_factory,
  not a second Rule capture, so the game is genuinely Rule-vs-opponent
- private binding and RuleBCExample.metadata carry full lineage; public
  dataset_manifest/public_summary carry only match_id/plan_hash, both
  already public via O2's own match_plan.json
EOF
)"
```

---

### Task 3: O2 match-plan adapter (`o2_training_loop/c4_bridge.py`)

**Files:**
- Create: `src/mage_ptcg/o2_training_loop/c4_bridge.py`
- Test: `tests/test_o2_c4_bridge.py` (new)

**Interfaces:**
- Consumes: `mage_ptcg.o2_training_loop.core.{DeckEntry, OpponentEntry, MatchSpec, build_match_matrix}` (unmodified), `mage_ptcg.o2_training_loop.cabt.{resolve_real_agent, resolve_real_deck}` (unmodified), `mage_ptcg.dataops.collector.{ActualEpisodeLineageInput, collect_actual_dataset}` (Task 1/2).
- Produces: `build_episode_lineage_inputs(specs: Sequence[MatchSpec], *, challenger_id: str, opponents: Mapping[str, OpponentEntry], decks: Mapping[str, DeckEntry], repository_root: str | Path) -> list[ActualEpisodeLineageInput]` — pure, no execution, one entry per spec, `requested_seed=spec.seed`, `backend_kind="cabt"`, hashes/ids resolved from `opponents`/`decks` via seat mapping identical to `build_match_matrix`'s own convention (`spec.first_player` is the challenger's seat).
- Produces: `run_o2_actual_collection(*, specs: Sequence[MatchSpec], challenger_id: str, opponents: Mapping[str, OpponentEntry], decks: Mapping[str, DeckEntry], repository_root: str | Path, output_root: str | Path, run_id: str, base_seed: int, canonical_base_sha: str, max_steps: int = 10_000, validation_percent: int = 20, split_seed: int = 0) -> dict[str, object]` — resolves the single own deck/agent and single opponent deck/agent (MVP: one challenger, one opponent, one deck each, matching `configs/competition/opponent_pool_o2_v1.yaml`/`deck_pool_o2_v1.yaml`), builds lineage inputs, and calls `collect_actual_dataset(..., episode_lineage_inputs=..., opponent_deck_path=..., opponent_agent_factory=...)`. Raises `O2ContractError` if `specs` reference more than one distinct own or opponent identity (out of MVP scope — fail closed rather than silently generalize).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_o2_c4_bridge.py
import json
from pathlib import Path

import pytest

from mage_ptcg.o2_training_loop.core import build_match_matrix, load_deck_pool, load_opponent_pool
from mage_ptcg.o2_training_loop.c4_bridge import build_episode_lineage_inputs, run_o2_actual_collection
from mage_ptcg.dataops.collector import DataOpsError

REPO_ROOT = Path(__file__).resolve().parents[1]


def _plan():
    decks = load_deck_pool(REPO_ROOT / "configs/competition/deck_pool_o2_v1.yaml")
    opponents = load_opponent_pool(REPO_ROOT / "configs/competition/opponent_pool_o2_v1.yaml", deck_ids=decks)
    specs = build_match_matrix(
        decks=decks, opponents=opponents, challenger_id="rule-agent-v0",
        opponent_ids=["random-legal-v0"], seeds=[9300, 9301], engine_version="cabt",
        created_from_manifest="o2-training-loop-v1",
    )
    return decks, opponents, specs


def test_build_episode_lineage_inputs_maps_seat_and_hashes_from_match_spec():
    decks, opponents, specs = _plan()
    entries = build_episode_lineage_inputs(
        specs, challenger_id="rule-agent-v0", opponents=opponents, decks=decks, repository_root=REPO_ROOT,
    )
    assert len(entries) == len(specs) == 4
    assert {entry.match_id for entry in entries} == {spec.match_id for spec in specs}
    for entry, spec in zip(entries, specs):
        assert entry.match_id == spec.match_id
        assert entry.plan_hash == spec.plan_hash
        assert entry.backend_kind == "cabt"
        assert entry.requested_seed == spec.seed
        assert entry.seat_index == spec.first_player
        assert entry.own_agent_id == "rule-agent-v0"
        assert entry.opponent_agent_id == "random-legal-v0"
        assert entry.own_implementation_hash == opponents["rule-agent-v0"].implementation_hash
        assert entry.opponent_implementation_hash == opponents["random-legal-v0"].implementation_hash
        assert entry.pair_id == spec.pair_id


def test_build_episode_lineage_inputs_rejects_multiple_own_or_opponent_identities():
    decks, opponents, specs = _plan()
    # Fabricate a mixed batch referencing two distinct challengers to hit the guard.
    other = build_match_matrix(
        decks=decks, opponents=opponents, challenger_id="random-legal-v0",
        opponent_ids=["rule-agent-v0"], seeds=[1], engine_version="cabt",
        created_from_manifest="o2-training-loop-v1",
    )
    with pytest.raises(Exception):
        build_episode_lineage_inputs(
            list(specs) + list(other), challenger_id="rule-agent-v0", opponents=opponents, decks=decks,
            repository_root=REPO_ROOT,
        )
```

Do not test `run_o2_actual_collection` with a real cabt call in this task (that needs the real engine and is covered by the actual collection run in Task 5/8). Instead add one more unit test that stubs `mage_ptcg.dataops.collector.collect_actual_dataset` via monkeypatch to assert `run_o2_actual_collection` calls it with the right `games`, `episode_lineage_inputs` length, and non-`None` `opponent_deck_path`/`opponent_agent_factory`.

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_o2_c4_bridge.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `c4_bridge.py`**

```python
"""Adapter: O2's match plan drives C4's actual collector, unmodified on both sides.

This module builds no new persisted schema.  It maps an already-built
``MatchSpec`` list onto ``dataops.collector.ActualEpisodeLineageInput`` and
calls the existing collector; O2's own ``build_match_matrix`` and cabt
resolvers are reused verbatim.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from mage_ptcg.dataops.collector import ActualEpisodeLineageInput, collect_actual_dataset

from .cabt import resolve_real_agent, resolve_real_deck
from .core import DeckEntry, MatchSpec, O2ContractError, OpponentEntry


def build_episode_lineage_inputs(
    specs: Sequence[MatchSpec],
    *,
    challenger_id: str,
    opponents: Mapping[str, OpponentEntry],
    decks: Mapping[str, DeckEntry],
    repository_root: str | Path,
) -> list[ActualEpisodeLineageInput]:
    entries: list[ActualEpisodeLineageInput] = []
    for spec in specs:
        own_seat = spec.first_player
        own_agent_id, opponent_agent_id = (
            (spec.player_a_agent, spec.player_b_agent) if own_seat == 0 else (spec.player_b_agent, spec.player_a_agent)
        )
        if own_agent_id != challenger_id:
            raise O2ContractError(f"spec {spec.match_id!r} does not place the challenger in its own declared seat")
        own_deck_id, opponent_deck_id = (
            (spec.player_a_deck, spec.player_b_deck) if own_seat == 0 else (spec.player_b_deck, spec.player_a_deck)
        )
        _, own_deck_hash = resolve_real_deck(decks[own_deck_id], repository_root=repository_root)
        _, opponent_deck_hash = resolve_real_deck(decks[opponent_deck_id], repository_root=repository_root)
        entries.append(
            ActualEpisodeLineageInput(
                match_id=spec.match_id,
                plan_hash=spec.plan_hash,
                match_spec_hash=_spec_hash(spec),
                backend_kind="cabt",
                requested_seed=spec.seed,
                engine_seed_supported=False,
                seat_index=own_seat,
                player_side="A" if own_seat == 0 else "B",
                own_agent_id=own_agent_id,
                opponent_agent_id=opponent_agent_id,
                own_implementation_hash=opponents[own_agent_id].implementation_hash,
                opponent_implementation_hash=opponents[opponent_agent_id].implementation_hash,
                own_deck_hash=own_deck_hash,
                opponent_deck_hash=opponent_deck_hash,
                pair_id=spec.pair_id,
            )
        )
    own_ids = {entry.own_agent_id for entry in entries}
    opponent_ids = {entry.opponent_agent_id for entry in entries}
    if len(own_ids) > 1 or len(opponent_ids) > 1:
        raise O2ContractError("bridge MVP supports exactly one own agent and one opponent per collection run")
    return entries


def _spec_hash(spec: MatchSpec) -> str:
    from mage_ptcg.competition_intelligence.canonical import digest

    return digest(spec.to_dict(), domain="o2-match-spec-c4-lineage-v0")


def run_o2_actual_collection(
    *,
    specs: Sequence[MatchSpec],
    challenger_id: str,
    opponents: Mapping[str, OpponentEntry],
    decks: Mapping[str, DeckEntry],
    repository_root: str | Path,
    output_root: str | Path,
    run_id: str,
    base_seed: int,
    canonical_base_sha: str,
    max_steps: int = 10_000,
    validation_percent: int = 20,
    split_seed: int = 0,
) -> dict[str, object]:
    entries = build_episode_lineage_inputs(
        specs, challenger_id=challenger_id, opponents=opponents, decks=decks, repository_root=repository_root,
    )
    own_deck_id = decks[next(iter({e for e in decks if decks[e].deck_id == decks[e].deck_id}))].deck_id  # placeholder, replaced below
    # Resolve the single own/opponent deck paths and factories referenced by entries[0].
    first_spec = specs[0]
    own_deck_id = first_spec.player_a_deck if first_spec.first_player == 0 else first_spec.player_b_deck
    opponent_deck_id = first_spec.player_b_deck if first_spec.first_player == 0 else first_spec.player_a_deck
    own_deck_path, _ = resolve_real_deck(decks[own_deck_id], repository_root=repository_root)
    opponent_deck_path, _ = resolve_real_deck(decks[opponent_deck_id], repository_root=repository_root)
    opponent_agent_factory = resolve_real_agent(opponents[entries[0].opponent_agent_id], repository_root=repository_root)
    return collect_actual_dataset(
        run_id=run_id,
        games=len(specs),
        base_seed=base_seed,
        output_root=output_root,
        canonical_base_sha=canonical_base_sha,
        deck_path=own_deck_path,
        repository_root=repository_root,
        max_steps=max_steps,
        validation_percent=validation_percent,
        split_seed=split_seed,
        episode_lineage_inputs=entries,
        opponent_deck_path=opponent_deck_path,
        opponent_agent_factory=opponent_agent_factory,
    )


__all__ = ["build_episode_lineage_inputs", "run_o2_actual_collection"]
```

Delete the placeholder `own_deck_id = decks[next(...)]` line above before committing — it was left in as a thinking artifact; the two lines right below it (`first_spec = ...`) already compute `own_deck_id`/`opponent_deck_id` correctly. Re-read the function once written to make sure there is exactly one assignment to each name.

- [ ] **Step 4: Run tests, fix until green**

Run: `python -m pytest tests/test_o2_c4_bridge.py -v`
Expected: pass.

- [ ] **Step 5: Full regression + commit**

Run: `python -m pytest tests/test_o2_training_loop.py tests/test_o2_c4_bridge.py tests/test_c4_data_ops.py -q`

```bash
git add src/mage_ptcg/o2_training_loop/c4_bridge.py tests/test_o2_c4_bridge.py
git commit -m "$(cat <<'EOF'
feat(o2): add O2 match-plan adapter for C4 actual lineage collection

- build_episode_lineage_inputs maps MatchSpec -> ActualEpisodeLineageInput
  by reusing build_match_matrix/resolve_real_agent/resolve_real_deck as-is
- run_o2_actual_collection is the single call site that drives real
  Rule-vs-opponent cabt games through collect_actual_dataset's O2 mode
EOF
)"
```

---

### Task 4: Bidirectional lookup tests + dataset/artifact lineage propagation

**Files:**
- Modify: `src/mage_ptcg/offline_training/dataset.py` (`build_dataset`)
- Modify: `src/mage_ptcg/offline_training/cli.py` (`phase_build_dataset`, one line)
- Test: `tests/test_offline_training_v1.py`, `tests/test_c4_data_ops.py`

**Interfaces:**
- Produces: `build_dataset(..., source_plan_hash: str = "NONE")` — new optional kwarg, stored as `manifest["source_plan_hash"]`, included in `manifest_hash`. Default `"NONE"` preserves today's manifest content/hash for every existing caller that does not pass it.
- Produces: `deterministic_episode_split` split-grouping is strengthened using `RuleBCExample.metadata.get("o2_pair_id")` when present (seat-swap pair leakage guard) — see Step 3.

- [ ] **Step 1: Write failing tests**

In `tests/test_c4_data_ops.py`, add a lookup test using the O2-mode fixture from Task 2:

```python
def test_o2_match_id_and_c4_episode_id_are_the_same_value(tmp_path, ...):
    summary = ...  # call collect_actual_dataset with episode_lineage_inputs as in Task 2
    run_dir = ...
    binds = _load_jsonl_helper(run_dir / "private_dataset" / "private_bindings.jsonl")
    rows = _load_jsonl_helper(run_dir / "private_dataset" / "rule-bc-v1.jsonl")
    match_ids = {entry["match_id"] for entry in lineage_inputs}
    assert {b["episode_group_id"] for b in binds} == match_ids  # O2 match_id -> C4 episode ID
    assert {r["metadata"]["episode_group_id"] for r in rows} == match_ids  # decision -> match_id
    assert {r["metadata"]["o2_match_id"] for r in rows} == match_ids
    assert set(summary["o2_match_ids"]) == match_ids  # dataset_manifest/public_summary -> match_ids


def test_public_summary_never_carries_seat_or_implementation_hash(tmp_path, ...):
    summary = ...  # same O2-mode run
    dumped = json.dumps(summary)
    assert "own_implementation_hash" not in dumped
    assert "opponent_implementation_hash" not in dumped
    assert "seat_index" not in dumped
    assert "own_deck_hash" not in dumped
```

In `tests/test_offline_training_v1.py`, add:

```python
def test_build_dataset_records_source_plan_hash_when_given(tmp_path):
    ...
    manifest = build_dataset(..., source_plan_hash="planhash123")
    assert manifest["source_plan_hash"] == "planhash123"


def test_build_dataset_defaults_source_plan_hash_to_none_and_matches_prior_hash(tmp_path):
    manifest_without = build_dataset(...)  # no source_plan_hash kwarg
    assert manifest_without["source_plan_hash"] == "NONE"


def test_deterministic_split_keeps_o2_pair_together(tmp_path):
    # Two source_ids whose examples share metadata["o2_pair_id"] must land in the
    # same split even though their raw source_id hashes would otherwise separate them.
    ...
```

Also add, in `tests/test_c4_data_ops.py`, the leakage audit test named by the task spec:

```python
def test_o2_seat_swapped_pair_members_are_never_split_across_train_and_validation(tmp_path, ...):
    # Build >=4 lineage entries as two seat-swapped pairs (shared pair_id per pair),
    # run collect_actual_dataset, then run split_by_episode_group with the collector's
    # own pair-aware grouping and assert no pair_id appears in both train_ids and validation_ids.
```

- [ ] **Step 2: Run to confirm failure**

Run: `python -m pytest tests/test_offline_training_v1.py tests/test_c4_data_ops.py -k "plan_hash or pair or lookup or never_carries" -v`
Expected: FAIL.

- [ ] **Step 3: Implement `source_plan_hash` propagation and pair-aware split grouping**

In `src/mage_ptcg/offline_training/dataset.py`, add `source_plan_hash: str = "NONE"` to `build_dataset`'s signature (dataset.py:136-148) and `"source_plan_hash": source_plan_hash,` next to the existing `"source_collection_hash": source_collection_hash,` line (dataset.py:312).

For pair-aware grouping, change the split call site (dataset.py:182-189). Read `deterministic_episode_split`'s current body (dataset.py:56-95) first. Replace the `episode_ids = sorted({example.source_id for example in clean})` line with a grouped-id computation:

```python
    group_by_source_id: dict[str, str] = {}
    for example in clean:
        pair_id = example.metadata.get("o2_pair_id")
        group_by_source_id[example.source_id] = pair_id if pair_id else example.source_id
    episode_ids = sorted(group_by_source_id)
    assignment_by_group = deterministic_episode_split(
        sorted(set(group_by_source_id.values())),
        split_seed=split_seed, train_fraction=train_fraction,
        validation_fraction=validation_fraction, test_fraction=test_fraction,
    )
    assignment = {source_id: assignment_by_group[group] for source_id, group in group_by_source_id.items()}
```

Remove the old direct call to `deterministic_episode_split(episode_ids, ...)` immediately below (the one currently at dataset.py:183-189) since it is replaced by the block above. Leave `deterministic_episode_split` itself unchanged — it already operates correctly on whatever id list (raw source_id or pair_id) it is given; no internal change needed there. Re-run the "Leakage assertion" block (dataset.py:280-286) unchanged — it still holds because every `source_id` maps to exactly one `assignment[source_id]`.

Similarly, in `src/mage_ptcg/dataops/collector.py`'s `split_by_episode_group` (collector.py:301-357), add an equivalent optional `group_key_by_source_id: Mapping[str, str] | None = None` parameter (default `None` preserves today's behavior exactly), and if provided, group/rank by the override value instead of the raw `example.source_id`, expanding back to per-example assignment the same way. Call it from `_finalize_run` (collector.py:736) by building `group_key_by_source_id` from `example.metadata.get("o2_pair_id")` when present, mirroring the `offline_training/dataset.py` change. Keep the function's public contract (`train`/`validation`/`train_ids`/`validation_ids`/`manifest`) identical.

In `src/mage_ptcg/offline_training/cli.py`, change line 175 area (`phase_build_dataset`) to also pass:

```python
            source_plan_hash=summary.get("o2_plan_hashes", ["NONE"])[0] if summary.get("o2_plan_hashes") else "NONE",
```

- [ ] **Step 4: Run tests, fix until green**

Run: `python -m pytest tests/test_offline_training_v1.py tests/test_c4_data_ops.py -q`
Expected: all pass, including every test enumerated in the investigation report §12.

- [ ] **Step 5: Full repository regression**

Run: `python -m pytest -q`
Expected: all pass (compare pass count against the Task 0 baseline captured before any edits — see the top-level report step).

- [ ] **Step 6: Commit**

```bash
git add src/mage_ptcg/offline_training/dataset.py src/mage_ptcg/offline_training/cli.py src/mage_ptcg/dataops/collector.py tests/test_offline_training_v1.py tests/test_c4_data_ops.py
git commit -m "$(cat <<'EOF'
feat(offline-training): propagate O2 plan hash and guard seat-swap leakage

- build_dataset records source_plan_hash (default NONE, backward compatible)
  so export/package manifests remain reachable to their O2 match plan via
  dataset_manifest.json
- deterministic_episode_split and split_by_episode_group now honor an
  O2 pair_id group override when present, keeping seat-swapped match pairs
  in the same split; both default to today's per-episode grouping otherwise
EOF
)"
```

---

### Task 5: O2 actual-lineage collection + pipeline script

**Files:**
- Create: `scripts/run_o2_actual_lineage_pipeline.py`
- Test: none (this is an operational script exercised for real in Task 8; keep it small and readable, argument-parsing only, delegating to Task 3/4 functions and to `mage_ptcg.offline_training.cli.Pipeline`)

**Interfaces:**
- Consumes: `mage_ptcg.o2_training_loop.core.{load_deck_pool, load_opponent_pool, build_match_matrix}`, `mage_ptcg.o2_training_loop.c4_bridge.run_o2_actual_collection`, `mage_ptcg.offline_training.cli.Pipeline`, `mage_ptcg.offline_training.config.load_config`, `mage_ptcg.offline_training.runstate`.
- Produces: a CLI that (1) builds the O2 match plan, (2) runs the O2-lineage actual collection directly into `<run-dir>/collection/cabt`, (3) writes `<run-dir>/collection/summary.json`, (4) opens a `Pipeline` against the same `<run-dir>` with an Offline Training v1 config, marks the `collect` phase complete without re-invoking it, and (5) runs `phase_build_dataset` → `phase_train` → `phase_export` → `phase_evaluate` → `phase_screen` → `phase_package` → `phase_verify` in order, saving state after each — i.e., it reuses `Pipeline`'s phase methods verbatim; it does not reimplement gate/train/export/package logic.

- [ ] **Step 1: Read `src/mage_ptcg/offline_training/runstate.py`'s public surface**

Read the file fully first (`RunPaths`, `load_or_create`, `RunState.set_phase`, `STATUS_COMPLETE`, `run_lock`) so the script's manual "mark collect complete" step matches the exact API `Pipeline.phase_collect` itself uses (cli.py:125-149) — do not guess field names.

- [ ] **Step 2: Implement the script**

```python
"""Run O2's Rule-vs-Random actual match plan through the full C4/Offline
Training v1 pipeline with O2 lineage attached, reusing Pipeline's phase
methods unmodified for build-dataset/train/export/evaluate/screen/package/verify.

Never re-implements collection, gating, training, export, or packaging.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
for _p in (REPOSITORY_ROOT, REPOSITORY_ROOT / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from mage_ptcg.o2_training_loop.core import build_match_matrix, load_deck_pool, load_opponent_pool  # noqa: E402
from mage_ptcg.o2_training_loop.c4_bridge import run_o2_actual_collection  # noqa: E402
from mage_ptcg.offline_training import runstate  # noqa: E402
from mage_ptcg.offline_training.cli import Pipeline  # noqa: E402
from mage_ptcg.offline_training.config import load_config  # noqa: E402


def _git_head() -> str:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck-pool", type=Path, default=REPOSITORY_ROOT / "configs/competition/deck_pool_o2_v1.yaml")
    parser.add_argument("--opponent-pool", type=Path, default=REPOSITORY_ROOT / "configs/competition/opponent_pool_o2_v1.yaml")
    parser.add_argument("--challenger", default="rule-agent-v0")
    parser.add_argument("--opponent", default="random-legal-v0")
    parser.add_argument("--seeds", type=int, nargs="+", required=True)
    parser.add_argument("--offline-training-config", type=Path, default=REPOSITORY_ROOT / "configs/competition/o2_actual_smoke_training.json")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="o2-actual-lineage")
    parser.add_argument("--max-steps", type=int, default=10_000)
    parser.add_argument("--validation-percent", type=int, default=25)
    parser.add_argument("--split-seed", type=int, default=93)
    parser.add_argument("--base-seed", type=int, default=0)
    args = parser.parse_args(argv)

    decks = load_deck_pool(args.deck_pool)
    opponents = load_opponent_pool(args.opponent_pool, deck_ids=decks)
    specs = build_match_matrix(
        decks=decks, opponents=opponents, challenger_id=args.challenger,
        opponent_ids=[args.opponent], seeds=args.seeds, engine_version="cabt",
        created_from_manifest="o2-training-loop-v1",
    )
    run_dir = args.run_dir.resolve()
    collection_root = run_dir / "collection"
    summary = run_o2_actual_collection(
        specs=specs, challenger_id=args.challenger, opponents=opponents, decks=decks,
        repository_root=REPOSITORY_ROOT, output_root=collection_root, run_id="cabt",
        base_seed=args.base_seed, canonical_base_sha=_git_head(), max_steps=args.max_steps,
        validation_percent=args.validation_percent, split_seed=args.split_seed,
    )
    summary = dict(summary)
    summary["collection_source"] = "actual"
    summary["actual_cabt"] = "ACTUAL_CABT_RUN"
    (collection_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")

    config = load_config(args.offline_training_config)
    pipeline = Pipeline(config, run_dir)
    with runstate.run_lock(runstate.RunPaths(run_dir), args.run_id):
        pipeline.open(run_id=args.run_id, resume=False)
        pipeline.state.set_phase("collect", runstate.STATUS_COMPLETE)
        results = [
            {"phase": "collect", "status": "COMPLETE", "episodes": summary.get("episode_count"),
             "decisions": summary.get("decision_count"), "o2_match_ids": summary.get("o2_match_ids"),
             "o2_plan_hashes": summary.get("o2_plan_hashes")},
            pipeline.phase_build_dataset(force=False),
            pipeline.phase_train(force=False),
            pipeline.phase_export(force=False),
            pipeline.phase_evaluate(force=False),
            pipeline.phase_screen(force=False),
            pipeline.phase_package(force=False),
            pipeline.phase_verify(force=False),
        ]
        pipeline.state.save()
    print(json.dumps({"run_dir": str(run_dir), "phases": results}, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Adjust names (`pipeline.state` vs `pipeline._s`, `runstate.STATUS_COMPLETE`, `runstate.run_lock` signature) to whatever Step 1's reading found — the sketch above must be corrected to match the real API before running.

- [ ] **Step 3: Dry-run against the fixture path first (no real games)**

Before spending real cabt time, sanity-check argument wiring with `--seeds 1` against a `/tmp` run-dir and confirm it fails *only* at the point where it needs real cabt capability (or succeeds, if cabt is available) rather than on a wiring bug (`TypeError`, `AttributeError`, wrong path).

Run: `python scripts/run_o2_actual_lineage_pipeline.py --seeds 1 --run-dir /tmp/o2-lineage-dryrun --run-id dryrun 2>&1 | tail -60`

Fix any wiring bugs found (do not fix by weakening validation — fix the script).

- [ ] **Step 4: Commit the script once the dry run's failure/success mode is understood and correct**

```bash
git add scripts/run_o2_actual_lineage_pipeline.py
git commit -m "$(cat <<'EOF'
feat(scripts): add O2 actual-lineage collection + pipeline runner

Builds the O2 Rule-vs-Random match plan, runs it through
collect_actual_dataset's O2 lineage mode, then reuses Pipeline's existing
build-dataset/train/export/evaluate/screen/package/verify phases unmodified.
EOF
)"
```

---

### Task 6: Run the real actual-lineage collection and regenerate the artifact

**No file changes — evidence generation.** Run from `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o2-mvtl`:

- [ ] **Step 1: Choose seeds for ~40 games.** `build_match_matrix` with 1 opponent, paired seat-swap, N seeds yields `2N` matches. Use 20 seeds (e.g. `9300..9319`) for 40 matches, matching the prior self-play collection's scale.

- [ ] **Step 2: Run the real collection + pipeline**

Run (this executes real cabt games; expect it to take a while — do not run in the background silently, monitor output):
```bash
python scripts/run_o2_actual_lineage_pipeline.py \
  --seeds 9300 9301 9302 9303 9304 9305 9306 9307 9308 9309 9310 9311 9312 9313 9314 9315 9316 9317 9318 9319 \
  --run-dir runs/o2-real-cabt/o2-actual-lineage-v0 \
  --run-id o2-actual-lineage-v0 \
  --base-seed 9300 --validation-percent 25 --split-seed 93
```

- [ ] **Step 3: Record every number the task's report section requires** from the printed JSON and from `runs/o2-real-cabt/o2-actual-lineage-v0/collection/summary.json`, `.../dataset/canonical/dataset_manifest.json`, `.../package/neural-student-v1/manifest.json`, `.../package/clean_room.json`: match count, episode count, decisions, candidates, dataset records, train/validation/test episode counts, O2 match ID count, C4 episode ID count, binding count, duplicate count, mismatch count, exclusion reasons, artifact class, dataset hash, source match IDs/plan hash reachability, clean-room legal/illegal counts.

- [ ] **Step 4: If collection fails at the engineering gate (too few episodes/decisions for `ACTUAL_TRAINING`/`NEURAL_ACTUAL_TRAINED`)**, do not weaken the gate. Increase `--seeds` count and re-run (idempotent — `collect_actual_dataset`'s per-game resume means a re-run with a longer seed list plus the same run-dir extends rather than repeats completed games, provided `games` in `run_o2_actual_collection` equals the new, larger `len(specs)`; delete and restart the run-dir if seed-list composition changes rather than just its length, to avoid `game_index` reassignment across differently-ordered specs).

---

### Task 7: Evaluation smoke on the new artifact

**Files:** none (evidence generation), reusing `scripts/run_actual_agent_viability.py` exactly as the prior 16-game seat-balanced smoke did (investigation report §14 item 4).

- [ ] **Step 1: Re-read `scripts/run_actual_agent_viability.py --help`** to confirm current flags before invoking (do not assume the flag list from the investigation report is still exact without checking).

- [ ] **Step 2: Run a seat-balanced smoke against the new package**, pointing `--package-path` (or equivalent) at `runs/o2-real-cabt/o2-actual-lineage-v0/package/neural-student-v1`, with a games count similar to the prior 16-game smoke.

- [ ] **Step 3: Record** evaluation match ID, artifact hash, training plan hash / training source collection (from the export/package manifest), cabt backend, legality, fallback, timeout, crash, privacy, model selection, latency. Confirm the printed/produced metadata includes `engine_seed_supported: false`, `pairing_mode: seat_matched_unseeded`, `exact_paired_inference: false`, `promotion_eligible: false` — if the script does not already emit these fields for this artifact class, do not fabricate them into evidence docs; report the gap instead of inventing values.

- [ ] **Step 4: Confirm Promotion stays `INSUFFICIENT_EVIDENCE`** (or equivalent) — do not invoke or alter the Promotion Gate.

---

### Task 8: Docs, secret/privacy/docs-validation, final regression, evidence, commit, push

- [ ] **Step 1: Full repository regression**

Run: `python -m pytest -q`
Compare pass/fail counts against the Task-0 baseline recorded before any edits in this session.

- [ ] **Step 2: Secret scan and privacy scan**

Locate the exact standalone invocation forms confirmed in the investigation (§15): `secret_scan`/`privacy_scan`/`absolute_path_scan` are *not* standalone — they only run inside `scripts/collect_offline_training_v1_evidence.py`'s plan-JSON runner. For a repo-wide/session-wide secret scan appropriate to "前回未実施だったsecret scan", use the same self-scan primitives directly:

```bash
python - <<'EOF'
import sys
sys.path.insert(0, "src")
from mage_ptcg.competition.redaction import secret_scan
from mage_ptcg.dataops.collector import scan_public_artifact
import json, pathlib
# Scan every new/changed public-intended artifact this session touched.
for path in [
    "runs/o2-real-cabt/o2-actual-lineage-v0/collection/summary.json",
    "runs/o2-real-cabt/o2-actual-lineage-v0/dataset/canonical/dataset_manifest.json",
    "runs/o2-real-cabt/o2-actual-lineage-v0/package/neural-student-v1/manifest.json",
]:
    doc = json.loads(pathlib.Path(path).read_text())
    print(path, scan_public_artifact(doc))
EOF
```

Also run `git diff --check` and `git status --short` to look for accidentally staged `kaggle.json`/`.env`/absolute-path leakage, per AGENTS.md.

- [ ] **Step 3: Docs validation**

Run: `python scripts/docs/validate_docs.py`
Expected: `Validated N canonical documents.` (0 errors). Fix only if this task's own doc edits broke it.

- [ ] **Step 4: Update evidence doc**

Edit `docs/evidence/o2-minimum-training-loop-v0.md` per its own file's `docs/plan/AGENTS.md` writing rules (conclusion-first, one point per paragraph, tables for 3+ parallel items, explicit `TODO:`/`(要検証)` for anything unverified). Add a new dated section reporting Task 6/7's real numbers, referencing this plan's commit range. Do not restate figures from the prior sections; link/refer instead.

- [ ] **Step 5: Update `docs/status/current_status.md` / `docs/status/handoff.md`** with the completion of "O2→C4 Actual Lineage Contract" per AGENTS.md's "意味のある実装・評価・統合の後は...更新する" rule. Keep changes minimal and evidence-backed.

- [ ] **Step 6: Final full regression + validation, then commit docs**

```bash
git add docs/evidence/o2-minimum-training-loop-v0.md docs/status/current_status.md docs/status/handoff.md
git commit -m "$(cat <<'EOF'
docs(eval): record O2->C4 actual lineage contract evidence

Experiment: docs/evidence/o2-minimum-training-loop-v0.md
EOF
)"
```

- [ ] **Step 7: Push to the existing remote branch (not main, not a new branch)**

```bash
git push origin feature/o2-minimum-training-loop
```

- [ ] **Step 8: Report** per AGENTS.md's 完了報告 format and per the user's 29-point final-report checklist, including `git status --short` and origin divergence (`git rev-list --left-right --count HEAD...origin/feature/o2-minimum-training-loop`).
