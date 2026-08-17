# O6 Public Trajectory Privacy/Integrity Final Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (this plan is executed **inline, in this session, by the same author who designed it** — the schema/privacy/integrity design is too interlocking to safely fragment across fresh subagents with no shared context. Do not dispatch per-task subagents for this plan.)
>
> **Granularity note:** given the scope (privacy schema + two independent implementations + multi-level integrity chain + a new 60-game league run), test steps bundle several related cases per task instead of one-test-per-step. Still test-first per task. Full code is given for every hard part (schema, projection allow-lists, digest/chain math); trivial glue code (argparse plumbing, docstrings) is described rather than fully inlined.

**Goal:** Close O6-AUD-002-PRIVACY-001, PRIVACY-002, INTEGRITY-001 by replacing raw-observation trajectory persistence with a strict allow-list public projection, giving the independent verifier its own schema/privacy/integrity-chain checks, anchoring the evidence root outside the run directory, invalidating the tainted legacy run as a tombstone, and re-running the 60-game league on the new pipeline.

**Architecture:** A new `public_trajectory_projection.py` builds `PUBLIC_TRAJECTORY_PROJECTION_V1` events via recursive allow-list (fail-closed on any unrecognized key), validated against a shared JSON Schema artifact. `public_trajectory_evidence.py` (replacing `raw_trajectory_evidence.py`) is the only writer and never touches disk with a raw observation. `independent_trajectory_verifier.py` is rewritten with its own privacy scan, its own schema conformance check, and full integrity-chain verification (game hashes → trajectory/game manifests → run manifest/summary → run root → externally-anchored trusted root registry), and gains a `--trusted-root-registry`/`--expected-root-sha256`-gated "full" mode. `league_integrity_chain.py` (runtime-only) builds the multi-level manifest/hash chain and registers the trusted root. `scripts/run_o6_team_league.py` is updated to orchestrate the new write→verify→finalize sequence and produce `o6-team-league-<hash>-public-v2`.

**Tech Stack:** Python 3, `jsonschema` (already a pinned dependency — `requirements.txt: jsonschema==4.26.0`), `gzip`/`hashlib`/`json` stdlib, existing `mage_ptcg.competition_intelligence.canonical` (runtime side only), pytest.

## Global Constraints

- No new branch/worktree. All work happens in `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1` on `feature/o6-opponent-intelligence-platform-v1`.
- No merge, push, rebase, Champion change, default-agent change, Kaggle submission, protected-file change, Public Agent activation, Public Population publish.
- Never write a raw observation to disk. Only `PUBLIC_TRAJECTORY_PROJECTION_V1` events may be persisted.
- Unknown fields at any nesting level fail the whole game's evidence generation (`PUBLIC_SCHEMA_UNKNOWN_FIELD`) — never silently dropped, never bucketed into a named-only `unknown_fields` list.
- The independent verifier must never import the runtime projection builder, the runtime digest function, or the League runner. Sharing the JSON Schema *artifact* (data) and third-party `jsonschema` (generic library) is fine; sharing *logic* is not — the verifier keeps its own canonical-JSON/digest/privacy-scan implementation, per existing precedent in this module.
- `git commit`/`push`/Kaggle submission only on explicit instruction — this task authorizes commits (§19 of the task spec) but not push/submit.
- Existing raw evidence at `docs/evidence/o6-opponent-intelligence-v3/` must never be copied into new runs, added to shared artifacts, or treated as valid evidence again.
- Preserve `NativeAgentWorker.__init__`'s `Path(source_root).resolve()` fix (`league_runtime.py:80`) and its regression test untouched.
- Japanese for commit messages and any new/edited Japanese docs; code/identifiers in English matching existing style.

---

## File Structure

New files:
- `src/mage_ptcg/opponents/public_trajectory_schema_v1.json` — shared JSON Schema artifact (draft 2020-12), `additionalProperties: false` recursively.
- `src/mage_ptcg/opponents/public_trajectory_projection.py` — runtime-only allow-list projection builder.
- `src/mage_ptcg/opponents/public_trajectory_evidence.py` — runtime writer (replaces `raw_trajectory_evidence.py`, which is deleted).
- `src/mage_ptcg/opponents/league_integrity_chain.py` — runtime-only multi-level manifest/hash-chain builder + trusted-root registry helpers.
- `docs/evidence/o6-trusted-league-roots.json` — external trust anchor registry (git-tracked).
- `docs/evidence/o6-opponent-intelligence-v3-TOMBSTONE.json` — tombstone metadata for the invalidated legacy run.
- `tests/opponents/test_public_trajectory_projection.py`
- `tests/opponents/test_public_trajectory_evidence.py` (replaces `test_raw_trajectory_evidence.py`, which is deleted)
- `tests/opponents/test_league_integrity_chain.py`

Modified files:
- `src/mage_ptcg/opponents/independent_trajectory_verifier.py` — rewritten: own privacy scan, own schema check, full chain verification, two-mode CLI.
- `src/mage_ptcg/opponents/privacy_gate.py` — kept as the runtime-side defense-in-depth scanner (used only inside the writer), left largely as-is (still a useful secondary net) but no longer imported by the verifier.
- `scripts/run_o6_team_league.py` — new write→verify→finalize sequence, new default evidence root (`docs/evidence/o6-opponent-intelligence-v4`), trusted-root registration, new `league_run_id` pattern (`...-public-v2`).
- `tests/opponents/test_independent_trajectory_verifier.py` — extended with chain/anchor/tamper coverage.
- `tests/opponents/test_privacy_gate.py` — extended with renamed/nested-unknown-field regression cases (still useful as the writer's defense-in-depth layer).
- `tests/test_run_o6_team_league.py` — updated for new evidence root/manifest shape.
- `.gitignore` — add `docs/evidence/o6-opponent-intelligence-v3/`.
- `docs/status/current_status.md`, `docs/status/handoff.md`, `docs/evidence/o6-opponent-intelligence-v1.md` — Phase C section updated to describe the invalidated legacy run and the new remediation.

Deleted (git-tracked removal, not disk deletion) from git tracking:
- `docs/evidence/o6-opponent-intelligence-v3/` (kept on local disk as restricted quarantine).

---

## Schema Reference (authoritative — all code below implements exactly this)

Grounded in `docs/evidence/cabt-observation-schema.md` (already-audited raw key inventory from 8 episodes / 474 observations / 3986 options) — raw key allow-lists below are exactly that document's structural findings, not new guesses. Fields whose zone semantics that document itself flags unverified (`option.area`, `option.index`, `option.inPlayArea`, `option.inPlayIndex`, `option.energyIndex`, `current.looking`, non-null `current.stadium` shape) are recognized-but-intentionally-excluded from the public projection rather than guessed at.

### Top-level event

```json
{
  "schema_version": "o6-public-trajectory-v1",
  "event_type": "INITIAL_PUBLIC_STATE | PUBLIC_ACTION | TERMINAL_PUBLIC_STATE",
  "step_index": 0,
  "seat_direction": "SEAT_0 | SEAT_1 | null",
  "public_payload": {
    "players": [ <player_projection>, <player_projection> ],
    "board": <board_projection>,
    "result": null,
    "action": null
  }
}
```

`seat_direction` is `"SEAT_0"`/`"SEAT_1"` when `public_payload.action` is non-null (the acting seat), else `null`. `event_type` is `INITIAL_PUBLIC_STATE` for `step_index == 0`, `TERMINAL_PUBLIC_STATE` for the last step, `PUBLIC_ACTION` otherwise (same partition scheme the current code already uses, renamed).

### `player_projection` (list index 0/1 = engine player index, from raw `current.players[i]`)

Raw recognized keys: `active, asleep, bench, benchMax, burned, confused, deckCount, discard, hand, handCount, paralyzed, poisoned, prize`. Any other key on a player object → `PUBLIC_SCHEMA_UNKNOWN_FIELD`.

```json
{
  "hand_count": 0,
  "deck_count": 0,
  "prize_count": 0,
  "bench_max": 5,
  "active": [ <card_projection_or_null>, ... ],
  "bench": [ <card_projection_or_null>, ... ],
  "discard": [ <card_projection>, ... ],
  "status": {"poisoned": false, "burned": false, "asleep": false, "paralyzed": false, "confused": false}
}
```

`hand`/`prize` are recognized raw keys whose **length only** is forwarded (`hand_count`, `prize_count`); their contents never participate. `active`/`bench`/`discard` contents are forwarded through `card_projection` below.

### `card_projection` (element of active/bench/discard; `null` = empty slot)

Raw recognized card keys: `id, serial, playerIndex, hp, maxHp, appearThisTurn, energies, energyCards, tools, preEvolution`. Any other key → `PUBLIC_SCHEMA_UNKNOWN_FIELD`.

```json
{
  "card_id": 0,
  "serial": null,
  "player_index": null,
  "current_hp": null,
  "max_hp": null,
  "appear_this_turn": null,
  "attached_energy_count": 0,
  "tool_count": 0,
  "evolution_depth": 0
}
```

`attached_energy_count = len(energyCards)` if present else `len(energies)` if present else `0`. `tool_count = len(tools)`. `evolution_depth = len(preEvolution)`.

### `board_projection` (from raw `current.{stadium,stadiumPlayed,supporterPlayed,energyAttached,retreated}`)

```json
{
  "stadium": {"stadium_id": 0} | null,
  "stadium_played": false,
  "supporter_played": false,
  "energy_attached": false,
  "retreated": false
}
```

Non-null `stadium` must be exactly `{"id": <int>}` in the raw observation (per the doc's own defensive note that no other shape has ever been observed) — anything else → `PUBLIC_SCHEMA_UNKNOWN_FIELD`.

### `action_projection` (from the acting seat's `select.options[selected_index]`, `null` when no seat acted this step)

Raw recognized option-field keys (the `fields` mapping inside the chosen option): `index, area, inPlayArea, inPlayIndex, playerIndex, energyIndex, count, number, attackId`, plus the option's own `type`. Any other key → `PUBLIC_SCHEMA_UNKNOWN_FIELD`. `index, area, inPlayArea, inPlayIndex, energyIndex` are recognized but **not forwarded** (zone semantics unverified — see Global Constraints).

```json
{
  "option_type": 14,
  "option_type_name": "PLAY | ATTACH | EVOLVE | ABILITY | ATTACK | END | null",
  "player_index": null,
  "attack_id": null,
  "count": null,
  "number": null
}
```

`option_type_name` mapping (from `main.py`'s existing `_OPTION_TYPE_NAMES`): `7→PLAY, 8→ATTACH, 9→EVOLVE, 10→ABILITY, 13→ATTACK, 14→END`; any other `type` value keeps `option_type` but `option_type_name: null` (a recognized-but-unnamed enum value is fine; this is not an unknown *field*).

---

## Task 1: JSON Schema artifact

**Files:**
- Create: `src/mage_ptcg/opponents/public_trajectory_schema_v1.json`
- Test: `tests/opponents/test_public_trajectory_projection.py` (schema-loading portion)

**Interfaces:**
- Produces: a JSON Schema (draft 2020-12) file loadable via `json.loads`, referenced by both `public_trajectory_projection.py` (construction-time validation) and `independent_trajectory_verifier.py` (independent conformance check) via `jsonschema.validate`.

- [ ] **Step 1: Write the schema file**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://mage-ptcg.local/o6/public_trajectory_schema_v1.json",
  "title": "o6-public-trajectory-v1",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "event_type", "step_index", "seat_direction", "public_payload"],
  "properties": {
    "schema_version": {"const": "o6-public-trajectory-v1"},
    "event_type": {"enum": ["INITIAL_PUBLIC_STATE", "PUBLIC_ACTION", "TERMINAL_PUBLIC_STATE"]},
    "step_index": {"type": "integer", "minimum": 0},
    "seat_direction": {"enum": ["SEAT_0", "SEAT_1", null]},
    "public_payload": {"$ref": "#/$defs/public_payload"}
  },
  "$defs": {
    "public_payload": {
      "type": "object",
      "additionalProperties": false,
      "required": ["players", "board", "result", "action"],
      "properties": {
        "players": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"$ref": "#/$defs/player_projection"}},
        "board": {"$ref": "#/$defs/board_projection"},
        "result": {"type": ["integer", "null"]},
        "action": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/action_projection"}]}
      }
    },
    "status_flags": {
      "type": "object",
      "additionalProperties": false,
      "required": ["poisoned", "burned", "asleep", "paralyzed", "confused"],
      "properties": {
        "poisoned": {"type": "boolean"}, "burned": {"type": "boolean"}, "asleep": {"type": "boolean"},
        "paralyzed": {"type": "boolean"}, "confused": {"type": "boolean"}
      }
    },
    "card_projection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["card_id", "serial", "player_index", "current_hp", "max_hp", "appear_this_turn", "attached_energy_count", "tool_count", "evolution_depth"],
      "properties": {
        "card_id": {"type": "integer"},
        "serial": {"type": ["integer", "null"]},
        "player_index": {"type": ["integer", "null"], "enum": [0, 1, null]},
        "current_hp": {"type": ["integer", "null"]},
        "max_hp": {"type": ["integer", "null"]},
        "appear_this_turn": {"type": ["boolean", "null"]},
        "attached_energy_count": {"type": "integer", "minimum": 0, "maximum": 256},
        "tool_count": {"type": "integer", "minimum": 0, "maximum": 256},
        "evolution_depth": {"type": "integer", "minimum": 0, "maximum": 256}
      }
    },
    "card_slot_list": {"type": "array", "maxItems": 256, "items": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/card_projection"}]}},
    "player_projection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["hand_count", "deck_count", "prize_count", "bench_max", "active", "bench", "discard", "status"],
      "properties": {
        "hand_count": {"type": "integer", "minimum": 0, "maximum": 4096},
        "deck_count": {"type": "integer", "minimum": 0, "maximum": 4096},
        "prize_count": {"type": "integer", "minimum": 0, "maximum": 4096},
        "bench_max": {"type": "integer", "minimum": 0, "maximum": 256},
        "active": {"$ref": "#/$defs/card_slot_list"},
        "bench": {"$ref": "#/$defs/card_slot_list"},
        "discard": {"$ref": "#/$defs/card_slot_list"},
        "status": {"$ref": "#/$defs/status_flags"}
      }
    },
    "board_projection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["stadium", "stadium_played", "supporter_played", "energy_attached", "retreated"],
      "properties": {
        "stadium": {"anyOf": [{"type": "null"}, {"type": "object", "additionalProperties": false, "required": ["stadium_id"], "properties": {"stadium_id": {"type": "integer"}}}]},
        "stadium_played": {"type": "boolean"},
        "supporter_played": {"type": "boolean"},
        "energy_attached": {"type": "boolean"},
        "retreated": {"type": "boolean"}
      }
    },
    "action_projection": {
      "type": "object",
      "additionalProperties": false,
      "required": ["option_type", "option_type_name", "player_index", "attack_id", "count", "number"],
      "properties": {
        "option_type": {"type": "integer"},
        "option_type_name": {"enum": ["PLAY", "ATTACH", "EVOLVE", "ABILITY", "ATTACK", "END", null]},
        "player_index": {"type": ["integer", "null"], "enum": [0, 1, null]},
        "attack_id": {"type": ["integer", "null"]},
        "count": {"type": ["integer", "null"]},
        "number": {"type": ["integer", "null"]}
      }
    }
  }
}
```

- [ ] **Step 2: Verify it parses and is internally referenceable**

Run: `PYTHONPATH=src .venv/bin/python -c "import json, jsonschema; s=json.load(open('src/mage_ptcg/opponents/public_trajectory_schema_v1.json')); jsonschema.Draft202012Validator.check_schema(s); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit is deferred to Task 3's commit** (schema + projection builder land together; a schema with no consumer is not independently testable).

---

## Task 2: Public trajectory projection builder (runtime-only, fail-closed allow-list)

**Files:**
- Create: `src/mage_ptcg/opponents/public_trajectory_projection.py`
- Test: `tests/opponents/test_public_trajectory_projection.py`

**Interfaces:**
- Consumes: `canonical_steps` in the exact shape `trajectory.canonical_step_seat` already produces (list of steps, each step a list of 2 per-seat `{"observation", "action", "status"}` dicts) — same input `raw_trajectory_evidence.build_raw_events` took.
- Produces: `build_public_trajectory_events(canonical_steps) -> list[dict]` (events matching the schema above) and `PublicSchemaUnknownFieldError` (raised on any unrecognized key at any depth). Consumed by Task 3's writer.

- [ ] **Step 1: Write the failing tests**

```python
# tests/opponents/test_public_trajectory_projection.py
from __future__ import annotations

import copy
import pytest

from mage_ptcg.opponents.public_trajectory_projection import (
    PublicSchemaUnknownFieldError,
    build_public_trajectory_events,
)

def _player(**overrides):
    base = {
        "active": [None, None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
        "confused": False, "deckCount": 52, "discard": [], "hand": [{"id": 1}, {"id": 2}], "handCount": 2,
        "paralyzed": False, "poisoned": False, "prize": [{"id": 9}, {"id": 10}, {"id": 11}, {"id": 12}, {"id": 13}, {"id": 14}],
    }
    base.update(overrides)
    return base

def _observation(*, your_index=0, players=None, result=None, select=None, extra_current=None, extra_top=None):
    current = {
        "yourIndex": your_index, "players": players or [_player(), _player()],
        "energyAttached": False, "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False,
    }
    if result is not None:
        current["result"] = result
    if extra_current:
        current.update(extra_current)
    obs = {"current": current, "logs": [], "search_begin_input": "opaque-token", "select": select, "step": 1}
    if extra_top:
        obs.update(extra_top)
    return obs

def _step(seat0_action=None, seat0_select=None, seat1_action=None, seat1_select=None, **kw):
    return [
        {"observation": _observation(your_index=0, select=seat0_select, **kw), "action": seat0_action, "status": "ACTIVE"},
        {"observation": _observation(your_index=1, select=seat1_select, **kw), "action": seat1_action, "status": "ACTIVE"},
    ]

def _end_option():
    return {"type": 14, "fields": {"index": 0}}

def test_three_step_game_produces_initial_action_terminal_events():
    steps = [
        _step(seat0_select={"type": 0, "options": [_end_option()]}),
        _step(seat0_action=[0], seat0_select={"type": 0, "options": [_end_option()]}),
        _step(seat0_action=[0], seat0_select={"type": 0, "options": [_end_option()]}, result=0),
    ]
    events = build_public_trajectory_events(steps)
    assert [e["event_type"] for e in events] == ["INITIAL_PUBLIC_STATE", "PUBLIC_ACTION", "TERMINAL_PUBLIC_STATE"]
    assert [e["step_index"] for e in events] == [0, 1, 2]
    assert events[2]["public_payload"]["result"] == 0

def test_action_event_projects_selected_option_and_seat_direction():
    steps = [_step(), _step(seat0_action=[0], seat0_select={"type": 0, "options": [_end_option()]})]
    events = build_public_trajectory_events(steps)
    action_event = events[-1]
    assert action_event["seat_direction"] == "SEAT_0"
    assert action_event["public_payload"]["action"] == {
        "option_type": 14, "option_type_name": "END", "player_index": None, "attack_id": None, "count": None, "number": None,
    }

def test_no_action_event_has_null_seat_direction_and_null_action():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    assert events[0]["seat_direction"] is None
    assert events[0]["public_payload"]["action"] is None

def test_hand_contents_never_appear_only_count():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    import json
    blob = json.dumps(events)
    assert '"id": 1' not in blob and '"id": 2' not in blob  # hand card ids from _player()
    assert events[0]["public_payload"]["players"][0]["hand_count"] == 2

def test_prize_contents_never_appear_only_count():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    assert events[0]["public_payload"]["players"][0]["prize_count"] == 6
    import json
    assert '"id": 9' not in json.dumps(events)

def test_logs_and_search_begin_input_never_appear():
    steps = [_step(), _step()]
    events = build_public_trajectory_events(steps)
    import json
    blob = json.dumps(events)
    assert "opaque-token" not in blob and '"logs"' not in blob and '"search_begin_input"' not in blob

def test_active_card_projects_known_fields_only():
    card = {"id": 5, "serial": 7, "playerIndex": 0, "hp": 60, "maxHp": 60, "appearThisTurn": True,
            "energyCards": [{"id": 1}], "tools": [], "preEvolution": [{"id": 2}]}
    players = [_player(active=[card, None]), _player()]
    steps = [_step(players=players), _step(players=players)]
    events = build_public_trajectory_events(steps)
    projected = events[0]["public_payload"]["players"][0]["active"][0]
    assert projected == {
        "card_id": 5, "serial": 7, "player_index": 0, "current_hp": 60, "max_hp": 60, "appear_this_turn": True,
        "attached_energy_count": 1, "tool_count": 0, "evolution_depth": 1,
    }

def test_unknown_top_level_observation_field_fails_closed():
    steps = [_step(extra_top={"totally_new_field": 1}), _step()]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)

def test_unknown_nested_player_field_fails_closed():
    players = [_player(newly_added_field="x"), _player()]
    steps = [_step(players=players), _step(players=players)]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)

def test_renamed_unknown_nested_field_under_card_fails_closed():
    card = {"id": 5, "hidden_engine_blob": "should not exist"}
    players = [_player(active=[card, None]), _player()]
    steps = [_step(players=players), _step(players=players)]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)

def test_unknown_option_field_fails_closed():
    option = {"type": 14, "fields": {"index": 0, "mystery_field": 1}}
    steps = [_step(), _step(seat0_action=[0], seat0_select={"type": 0, "options": [option]})]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)

def test_non_null_stadium_with_unexpected_shape_fails_closed():
    steps = [_step(extra_current={"stadium": {"unexpected": True}}), _step()]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)

def test_non_null_looking_fails_closed():
    steps = [_step(extra_current={"looking": {"anything": 1}}), _step()]
    with pytest.raises(PublicSchemaUnknownFieldError):
        build_public_trajectory_events(steps)

def test_empty_steps_rejected():
    from mage_ptcg.opponents.errors import OpponentError
    with pytest.raises(OpponentError):
        build_public_trajectory_events([])

def test_events_validate_against_shared_json_schema():
    import json
    from pathlib import Path
    import jsonschema
    schema = json.loads((Path("src/mage_ptcg/opponents/public_trajectory_schema_v1.json")).read_text())
    steps = [_step(), _step(seat0_action=[0], seat0_select={"type": 0, "options": [_end_option()]}, result=0)]
    events = build_public_trajectory_events(steps)
    validator = jsonschema.Draft202012Validator(schema)
    for event in events:
        validator.validate(event)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_public_trajectory_projection.py`
Expected: FAIL (`ModuleNotFoundError: mage_ptcg.opponents.public_trajectory_projection`)

- [ ] **Step 3: Implement the projection builder**

```python
# src/mage_ptcg/opponents/public_trajectory_projection.py
"""Strict allow-list public trajectory projection (O6-AUD-002 final remediation).

Builds ``PUBLIC_TRAJECTORY_PROJECTION_V1`` events by constructing a brand-new
dict from only recognized, individually-vetted-safe keys -- never by copying
the raw observation and deleting/denying keys afterward. Any raw key this
module does not explicitly recognize at any nesting depth raises
:class:`PublicSchemaUnknownFieldError` and aborts the whole game's evidence
generation (fail-closed): a partially-redacted trajectory is worse than none.

The recognized raw key inventory (player/card/option field names) is exactly
the structural findings audited in ``docs/evidence/cabt-observation-schema.md``
(8 episodes, 474 observations, 3986 options) -- not a new guess. Fields that
document itself flags as zone-semantics-unverified (``option.area``,
``option.index``, ``option.inPlayArea``, ``option.inPlayIndex``,
``option.energyIndex``, ``current.looking``, non-null ``current.stadium``
shapes other than ``{"id": int}``) are recognized (so they don't trigger a
false "unknown field" failure by mere presence with an expected shape) but
deliberately never forwarded into the public payload, and a *non-conforming*
value for any of them (e.g. non-null ``looking``) still fails closed, because
this module has no verified basis for judging its public/private status.

This module is imported only by the runtime writer
(:mod:`mage_ptcg.opponents.public_trajectory_evidence`). The independent
verifier must not import it; see that module's docstring.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .errors import OpponentError

PUBLIC_TRAJECTORY_SCHEMA_VERSION = "o6-public-trajectory-v1"
EVENT_INITIAL = "INITIAL_PUBLIC_STATE"
EVENT_ACTION = "PUBLIC_ACTION"
EVENT_TERMINAL = "TERMINAL_PUBLIC_STATE"

_OPTION_TYPE_NAMES = {7: "PLAY", 8: "ATTACH", 9: "EVOLVE", 10: "ABILITY", 13: "ATTACK", 14: "END"}

_PLAYER_KEYS = {"active", "asleep", "bench", "benchMax", "burned", "confused", "deckCount", "discard", "hand", "handCount", "paralyzed", "poisoned", "prize"}
_CARD_KEYS = {"id", "serial", "playerIndex", "hp", "maxHp", "appearThisTurn", "energies", "energyCards", "tools", "preEvolution"}
_CURRENT_KEYS = {"yourIndex", "turn", "turnActionCount", "firstPlayer", "result", "players", "energyAttached", "retreated", "stadium", "stadiumPlayed", "supporterPlayed", "looking"}
_OBSERVATION_KEYS = {"current", "logs", "search_begin_input", "remainingOverageTime", "select", "step"}
_SELECT_KEYS = {"context", "contextCard", "deck", "effect", "maxCount", "minCount", "option", "options", "remainDamageCounter", "remainEnergyCost", "type"}
_OPTION_KEYS = {"type", "fields"}
_OPTION_FIELD_KEYS = {"index", "area", "inPlayArea", "inPlayIndex", "playerIndex", "energyIndex", "count", "number", "attackId"}
_OPTION_FIELD_FORWARDED = {"playerIndex": "player_index", "attackId": "attack_id", "count": "count", "number": "number"}
_STADIUM_KEYS = {"id"}


class PublicSchemaUnknownFieldError(OpponentError):
    """A raw field this module does not recognize was present; evidence generation fails closed."""


def _reject(path: str, key: str) -> None:
    raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: unrecognized key {key!r} at {path}")


def _require_dict(value: Any, *, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: expected object at {path}, got {type(value).__name__}")
    return value


def _project_card(card: Any, *, path: str) -> dict[str, Any] | None:
    if card is None:
        return None
    card = _require_dict(card, path=path)
    for key in card:
        if key not in _CARD_KEYS:
            _reject(path, key)
    energy_cards = card.get("energyCards")
    energies = card.get("energies")
    attached_energy_count = len(energy_cards) if isinstance(energy_cards, list) else (len(energies) if isinstance(energies, list) else 0)
    tools = card.get("tools")
    pre_evolution = card.get("preEvolution")
    return {
        "card_id": card.get("id"),
        "serial": card.get("serial"),
        "player_index": card.get("playerIndex"),
        "current_hp": card.get("hp"),
        "max_hp": card.get("maxHp"),
        "appear_this_turn": card.get("appearThisTurn"),
        "attached_energy_count": attached_energy_count,
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "evolution_depth": len(pre_evolution) if isinstance(pre_evolution, list) else 0,
    }


def _project_card_list(cards: Any, *, path: str) -> list[dict[str, Any] | None]:
    if not isinstance(cards, list):
        _reject(path, "<non-list card container>")
    return [_project_card(card, path=f"{path}[{index}]") for index, card in enumerate(cards)]


def _project_status(player: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "poisoned": bool(player.get("poisoned")), "burned": bool(player.get("burned")), "asleep": bool(player.get("asleep")),
        "paralyzed": bool(player.get("paralyzed")), "confused": bool(player.get("confused")),
    }


def _project_player(player: Any, *, path: str) -> dict[str, Any]:
    player = _require_dict(player, path=path)
    for key in player:
        if key not in _PLAYER_KEYS:
            _reject(path, key)
    hand = player.get("hand")
    prize = player.get("prize")
    return {
        "hand_count": player.get("handCount", len(hand) if isinstance(hand, list) else 0),
        "deck_count": player.get("deckCount"),
        "prize_count": player.get("prize_count") if "prize_count" in player else (len(prize) if isinstance(prize, list) else player.get("prizeCount")),
        "bench_max": player.get("benchMax"),
        "active": _project_card_list(player.get("active"), path=f"{path}.active"),
        "bench": _project_card_list(player.get("bench"), path=f"{path}.bench"),
        "discard": _project_card_list(player.get("discard"), path=f"{path}.discard"),
        "status": _project_status(player),
    }


def _project_stadium(stadium: Any, *, path: str) -> dict[str, int] | None:
    if stadium is None:
        return None
    stadium = _require_dict(stadium, path=path)
    for key in stadium:
        if key not in _STADIUM_KEYS:
            _reject(path, key)
    stadium_id = stadium.get("id")
    if not isinstance(stadium_id, int):
        _reject(path, "id")
    return {"stadium_id": stadium_id}


def _project_board(current: Mapping[str, Any], *, path: str) -> dict[str, Any]:
    return {
        "stadium": _project_stadium(current.get("stadium"), path=f"{path}.stadium"),
        "stadium_played": bool(current.get("stadiumPlayed")),
        "supporter_played": bool(current.get("supporterPlayed")),
        "energy_attached": bool(current.get("energyAttached")),
        "retreated": bool(current.get("retreated")),
    }


def _project_action(select: Any, action: Any, *, path: str) -> dict[str, Any] | None:
    if not action:
        return None
    select = _require_dict(select, path=path)
    for key in select:
        if key not in _SELECT_KEYS:
            _reject(path, key)
    options = select.get("options") or ([select["option"]] if select.get("option") is not None else [])
    if not isinstance(options, list) or not options:
        raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: action recorded but no options at {path}")
    index = action[0] if isinstance(action, list) and action else action
    if not isinstance(index, int) or not (0 <= index < len(options)):
        raise PublicSchemaUnknownFieldError(f"PUBLIC_SCHEMA_UNKNOWN_FIELD: action index out of range at {path}")
    option = _require_dict(options[index], path=f"{path}.options[{index}]")
    for key in option:
        if key not in _OPTION_KEYS:
            _reject(f"{path}.options[{index}]", key)
    fields = option.get("fields") or {}
    fields = _require_dict(fields, path=f"{path}.options[{index}].fields")
    for key in fields:
        if key not in _OPTION_FIELD_KEYS:
            _reject(f"{path}.options[{index}].fields", key)
    projected = {"option_type": option.get("type"), "option_type_name": _OPTION_TYPE_NAMES.get(option.get("type"))}
    for raw_key, out_key in _OPTION_FIELD_FORWARDED.items():
        projected[out_key] = fields.get(raw_key)
    return projected


def _project_step(step: Sequence[Mapping[str, Any]], *, acting_seat: int | None) -> dict[str, Any]:
    source_seat = acting_seat if acting_seat is not None else next((i for i, seat in enumerate(step) if isinstance(seat.get("observation"), Mapping) and seat["observation"].get("current")), 0)
    observation = step[source_seat].get("observation")
    observation = _require_dict(observation, path=f"$.step[{source_seat}].observation")
    for key in observation:
        if key not in _OBSERVATION_KEYS:
            _reject(f"$.step[{source_seat}].observation", key)
    current = observation.get("current")
    current = _require_dict(current, path=f"$.step[{source_seat}].observation.current")
    for key in current:
        if key not in _CURRENT_KEYS:
            _reject(f"$.step[{source_seat}].observation.current", key)
    if current.get("looking") is not None:
        _reject(f"$.step[{source_seat}].observation.current", "looking")
    players_raw = current.get("players")
    if not isinstance(players_raw, list) or len(players_raw) != 2:
        raise PublicSchemaUnknownFieldError("PUBLIC_SCHEMA_UNKNOWN_FIELD: current.players must have exactly 2 entries")
    players = [_project_player(p, path=f"$.step[{source_seat}].observation.current.players[{i}]") for i, p in enumerate(players_raw)]
    board = _project_board(current, path=f"$.step[{source_seat}].observation.current")
    action = None
    if acting_seat is not None:
        acting_observation = _require_dict(step[acting_seat].get("observation"), path=f"$.step[{acting_seat}].observation")
        action = _project_action(acting_observation.get("select"), step[acting_seat].get("action"), path=f"$.step[{acting_seat}].observation.select")
    return {"players": players, "board": board, "result": current.get("result"), "action": action}


def _acting_seat(step: Sequence[Mapping[str, Any]]) -> int | None:
    for index, seat in enumerate(step):
        if seat.get("action"):
            return index
    return None


def build_public_trajectory_events(canonical_steps: Sequence[Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    if not canonical_steps:
        raise OpponentError("cannot build public trajectory events from empty steps")
    events: list[dict[str, Any]] = []
    last = len(canonical_steps) - 1
    for step_index, step in enumerate(canonical_steps):
        event_type = EVENT_INITIAL if step_index == 0 else EVENT_TERMINAL if step_index == last else EVENT_ACTION
        acting_seat = _acting_seat(step)
        public_payload = _project_step(step, acting_seat=acting_seat)
        events.append({
            "schema_version": PUBLIC_TRAJECTORY_SCHEMA_VERSION,
            "event_type": event_type,
            "step_index": step_index,
            "seat_direction": (f"SEAT_{acting_seat}" if acting_seat is not None else None),
            "public_payload": public_payload,
        })
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_public_trajectory_projection.py -v`
Expected: all PASS. If `test_events_validate_against_shared_json_schema` fails on a field mismatch, fix the mismatch between this module's output keys and Task 1's schema (not the other way around — the schema is the spec).

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/opponents/public_trajectory_schema_v1.json src/mage_ptcg/opponents/public_trajectory_projection.py tests/opponents/test_public_trajectory_projection.py
git commit -m "$(cat <<'EOF'
fix(o6): replace raw league observations with public projections

- 新規 public_trajectory_projection.py が再帰的 allow-list で
  PUBLIC_TRAJECTORY_PROJECTION_V1 イベントを構築する
- 未知フィールドは深さを問わず PublicSchemaUnknownFieldError で fail-closed
- 許可リストは docs/evidence/cabt-observation-schema.md の監査済み構造に基づく
EOF
)"
```

---

## Task 3: Public trajectory evidence writer (replaces `raw_trajectory_evidence.py`)

**Files:**
- Create: `src/mage_ptcg/opponents/public_trajectory_evidence.py`
- Delete: `src/mage_ptcg/opponents/raw_trajectory_evidence.py`
- Delete: `tests/opponents/test_raw_trajectory_evidence.py`
- Create: `tests/opponents/test_public_trajectory_evidence.py`

**Interfaces:**
- Consumes: `build_public_trajectory_events` (Task 2), `assert_public_only` (existing `privacy_gate.py`, kept as defense-in-depth), `canonical_json_bytes`/`sha256_hex` from `mage_ptcg.competition_intelligence.canonical` (runtime side is explicitly allowed to reuse this).
- Produces: `persist_game_evidence(evidence_root, game_dir_id, *, canonical_steps, runtime_digests, metadata) -> dict` (same call signature as the old `raw_trajectory_evidence.persist_game_evidence`, so `scripts/run_o6_team_league.py`'s call site in Task 8 only needs an import-line change), `write_immutable_json`, `compute_checksums_file`, `ImmutableEvidenceConflict`, `TRAJECTORY_MANIFEST_SCHEMA_VERSION = "o6-public-trajectory-manifest-v1"`, `CANONICALIZATION_VERSION = "o6-canonical-json-v1"`, `EVIDENCE_FORMAT_VERSION = "o6-evidence-format-v1"`. File on disk is named `public_projection_trajectory.jsonl.gz` (not `public_trajectory.jsonl.gz` — the old name is one of the banned names' near-misses; use the exact name from the task spec) and `runtime_digest.txt` (renamed from `trajectory_digest.txt`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/opponents/test_public_trajectory_evidence.py
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from mage_ptcg.opponents.public_trajectory_evidence import (
    ImmutableEvidenceConflict,
    compute_checksums_file,
    persist_game_evidence,
)
from mage_ptcg.opponents.public_trajectory_projection import PublicSchemaUnknownFieldError


def _canonical_steps():
    def player():
        return {"active": [None] * 1, "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False,
                "confused": False, "deckCount": 52, "discard": [], "hand": [{"id": 1}], "handCount": 1,
                "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}

    def obs(your_index, select=None, result=None):
        current = {"yourIndex": your_index, "players": [player(), player()], "energyAttached": False,
                   "retreated": False, "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
        if result is not None:
            current["result"] = result
        return {"current": current, "logs": [], "search_begin_input": "tok", "select": select, "step": 1}

    select = {"type": 0, "options": [{"type": 14, "fields": {"index": 0}}]}
    return [
        [{"observation": obs(0, select=select), "action": None, "status": "ACTIVE"},
         {"observation": obs(1, select=select), "action": None, "status": "ACTIVE"}],
        [{"observation": obs(0, select=select), "action": [0], "status": "ACTIVE"},
         {"observation": obs(1, select=select), "action": None, "status": "ACTIVE"}],
        [{"observation": obs(0, select=select, result=0), "action": None, "status": "DONE"},
         {"observation": obs(1, select=select, result=0), "action": None, "status": "DONE"}],
    ]


def _digests():
    return {"initial_observation_digest": "a", "action_trace_digest": "b", "terminal_observation_digest": "c", "complete_trajectory_digest": "d"}


def test_persist_writes_expected_files(tmp_path: Path):
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    game_dir = tmp_path / "games" / "pair__match0"
    assert (game_dir / "public_projection_trajectory.jsonl.gz").exists()
    assert (game_dir / "trajectory_manifest.json").exists()
    assert (game_dir / "runtime_digest.txt").exists()
    assert (game_dir / "game_metadata.json").exists()
    with gzip.open(game_dir / "public_projection_trajectory.jsonl.gz", "rt") as handle:
        lines = [json.loads(line) for line in handle if line.strip()]
    assert len(lines) == 3
    assert lines[0]["schema_version"] == "o6-public-trajectory-v1"
    blob = json.dumps(lines)
    assert '"id": 1' not in blob and "tok" not in blob and '"logs"' not in blob


def test_privacy_violation_still_blocks_persist_as_defense_in_depth(tmp_path: Path, monkeypatch):
    from mage_ptcg.opponents import privacy_gate
    monkeypatch.setattr(privacy_gate, "scan_public_only", lambda value: {"schema_version": "x", "status": "REJECTED", "violation": {"path": "$", "reason": "forced"}})
    with pytest.raises(privacy_gate.PrivacyViolation):
        persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    assert not (tmp_path / "games" / "pair__match0" / "public_projection_trajectory.jsonl.gz").exists()


def test_unknown_field_in_canonical_steps_blocks_persist(tmp_path: Path):
    steps = _canonical_steps()
    steps[0][0]["observation"]["current"]["players"][0]["a_brand_new_key"] = 1
    with pytest.raises(PublicSchemaUnknownFieldError):
        persist_game_evidence(tmp_path, "pair__match0", canonical_steps=steps, runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    assert not (tmp_path / "games" / "pair__match0").exists() or not list((tmp_path / "games" / "pair__match0").iterdir())


def test_immutable_write_idempotent_then_rejects_tamper(tmp_path: Path):
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    with pytest.raises(ImmutableEvidenceConflict):
        persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0-different"})


def test_checksums_file_covers_all_files_and_is_sha256sum_verifiable(tmp_path: Path):
    persist_game_evidence(tmp_path, "pair__match0", canonical_steps=_canonical_steps(), runtime_digests=_digests(), metadata={"game_id": "pair#0"})
    compute_checksums_file(tmp_path, tmp_path / "checksums.sha256")
    import subprocess
    result = subprocess.run(["sha256sum", "-c", "checksums.sha256"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_public_trajectory_evidence.py`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement the writer**

Copy the structure of the old `raw_trajectory_evidence.py` (`write_immutable_json`, `_write_gzip_jsonl`, `compute_checksums_file` are unchanged verbatim — they're generic and not the privacy-sensitive part) but replace `build_raw_events` with a call into `public_trajectory_projection.build_public_trajectory_events`, rename the jsonl filename and digest-file name, and add `canonicalization_version`/`evidence_format_version` to the manifest for Task 6's digest-must-include-schema-version requirement:

```python
# src/mage_ptcg/opponents/public_trajectory_evidence.py
"""Privacy-gated, canonical public trajectory persistence (O6-AUD-002 final remediation).

This is the *runtime write* side: raw observations are never written to
disk. Every event a game produces is a strict allow-list projection (see
:mod:`mage_ptcg.opponents.public_trajectory_projection`), independently
privacy-scanned again here as defense-in-depth (see
:mod:`mage_ptcg.opponents.privacy_gate`) before a single byte is written.
The independent verifier (:mod:`mage_ptcg.opponents.independent_trajectory_verifier`)
must not share code with this module; see its docstring.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from mage_ptcg.competition_intelligence.canonical import canonical_json_bytes, sha256_hex

from .errors import OpponentError
from .privacy_gate import assert_public_only
from .public_trajectory_projection import build_public_trajectory_events

TRAJECTORY_MANIFEST_SCHEMA_VERSION = "o6-public-trajectory-manifest-v1"
CANONICALIZATION_VERSION = "o6-canonical-json-v1"
EVIDENCE_FORMAT_VERSION = "o6-evidence-format-v1"


class ImmutableEvidenceConflict(OpponentError):
    """Attempted to overwrite already-persisted evidence with different content."""


def write_immutable_json(path: Path, value: Any) -> None:
    path = Path(path)
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if canonical_json_bytes(existing) != canonical_json_bytes(value):
            raise ImmutableEvidenceConflict(f"refusing to overwrite immutable evidence with different content: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _write_gzip_jsonl(path: Path, events: list[dict[str, Any]]) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for event in events) + "\n"
    raw_bytes = lines.encode("utf-8")
    if path.exists():
        with gzip.open(path, "rb") as handle:
            existing = handle.read()
        if existing != raw_bytes:
            raise ImmutableEvidenceConflict(f"refusing to overwrite immutable public trajectory with different content: {path}")
        return path.read_bytes()
    with open(path, "wb") as fileobj:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fileobj, mtime=0) as handle:
            handle.write(raw_bytes)
    return path.read_bytes()


def persist_game_evidence(evidence_root: Path, game_dir_id: str, *, canonical_steps: Sequence[Sequence[Mapping[str, Any]]],
                           runtime_digests: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Fail-closed persist of one game's public trajectory projection evidence.

    Projection (allow-list, may raise ``PublicSchemaUnknownFieldError``) runs
    before privacy scanning, which runs before anything is written: a single
    rejected event blocks the whole game's evidence, not just that event.
    """
    events = build_public_trajectory_events(canonical_steps)
    for event in events:
        assert_public_only(event)
    game_dir = Path(evidence_root) / "games" / game_dir_id
    jsonl_gz_path = game_dir / "public_projection_trajectory.jsonl.gz"
    compressed = _write_gzip_jsonl(jsonl_gz_path, events)
    manifest = {
        "schema_version": TRAJECTORY_MANIFEST_SCHEMA_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "evidence_format_version": EVIDENCE_FORMAT_VERSION,
        "game_dir_id": game_dir_id,
        "event_count": len(events),
        "runtime_digests": dict(runtime_digests),
        "privacy_validation": {"status": "PASS", "events_checked": len(events)},
    }
    write_immutable_json(game_dir / "trajectory_manifest.json", manifest)
    write_immutable_json(game_dir / "game_metadata.json", dict(metadata))
    digest_text_path = game_dir / "runtime_digest.txt"
    digest_text = json.dumps(dict(runtime_digests), sort_keys=True) + "\n"
    if digest_text_path.exists() and digest_text_path.read_text(encoding="utf-8") != digest_text:
        raise ImmutableEvidenceConflict(f"refusing to overwrite immutable evidence with different content: {digest_text_path}")
    digest_text_path.write_text(digest_text, encoding="utf-8")
    hashes = {
        "schema_version": "o6-public-evidence-hashes-v1",
        "files": {
            "public_projection_trajectory.jsonl.gz": sha256_hex(compressed),
            "trajectory_manifest.json": sha256_hex((game_dir / "trajectory_manifest.json").read_bytes()),
            "game_metadata.json": sha256_hex((game_dir / "game_metadata.json").read_bytes()),
            "runtime_digest.txt": sha256_hex((game_dir / "runtime_digest.txt").read_bytes()),
        },
    }
    write_immutable_json(game_dir / "hashes.json", hashes)
    return manifest


def compute_checksums_file(root: Path, checksums_path: Path) -> None:
    root = Path(root)
    checksums_path = Path(checksums_path)
    files = sorted(
        (path for path in root.rglob("*") if path.is_file() and path.resolve() != checksums_path.resolve()),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}" for path in files]
    checksums_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
```

Then delete the old files:

```bash
git rm src/mage_ptcg/opponents/raw_trajectory_evidence.py tests/opponents/test_raw_trajectory_evidence.py
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_public_trajectory_evidence.py -v`
Expected: all PASS

- [ ] **Step 5: Fix now-broken imports elsewhere**

`grep -rn raw_trajectory_evidence src/ scripts/ tests/` and update each hit (`scripts/run_o6_team_league.py` is handled fully in Task 8; for now just confirm no other file imports it — if `tests/opponents/test_independent_trajectory_verifier.py` imports the old module for its end-to-end fixture, update it to `public_trajectory_evidence` here too so the suite doesn't bit-rot mid-plan).

- [ ] **Step 6: Commit**

```bash
git add src/mage_ptcg/opponents/public_trajectory_evidence.py tests/opponents/test_public_trajectory_evidence.py
git add -u src/mage_ptcg/opponents/raw_trajectory_evidence.py tests/opponents/test_raw_trajectory_evidence.py
git commit -m "$(cat <<'EOF'
fix(o6): replace raw league observations with public projections

- raw_trajectory_evidence.py を廃止し public_trajectory_evidence.py に置換
- 生データは一切ディスクへ書かず、allow-list projection のみを永続化
- ファイル名を public_projection_trajectory.jsonl.gz / runtime_digest.txt に変更
EOF
)"
```

---

## Task 4: Privacy gate regression coverage (renamed/nested unknown fields)

**Files:**
- Modify: `tests/opponents/test_privacy_gate.py`

**Interfaces:** none new; `privacy_gate.py` itself is unchanged (it remains the writer's defense-in-depth denylist scanner — the *allow-list* enforcement now lives in Task 2's projection builder, which is the actual fix for PRIVACY-002; this task only adds the regression tests the re-audit specifically called out as missing).

- [ ] **Step 1: Add the failing tests**

Append to `tests/opponents/test_privacy_gate.py`:

```python
def test_renamed_nested_unknown_sensitive_field_still_caught_by_pattern():
    # A key that is semantically "hidden state" but under a name the denylist regex matches
    # even after renaming (credential_bundle -> auth_credential_bundle): still rejected.
    result = scan_public_only({"observation": {"nested": {"auth_credential_bundle": "value"}}})
    assert result["status"] == "REJECTED"


def test_genuinely_novel_unrecognized_key_name_is_not_caught_by_denylist_alone():
    # Documents the known limitation this defense-in-depth layer has: a key name with no
    # denylist-matching substring and a value with no denylist-matching pattern passes here.
    # This is why PRIVACY-002's actual fix is the allow-list projection builder (Task 2),
    # not a stronger denylist -- this test pins the *documented* boundary of this layer.
    result = scan_public_only({"totally_novel_unrelated_key_name": "ordinary looking value"})
    assert result["status"] == "PASS"
```

(Adjust the import line at the top of the file if `scan_public_only` isn't already imported.)

- [ ] **Step 2: Run and confirm both pass as expected**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_privacy_gate.py -v`
Expected: all PASS (the second test passing is intentional — it documents the layer's known boundary, not a bug; the real fix is Task 2's allow-list, which this test file cannot exercise since it tests `privacy_gate` in isolation)

- [ ] **Step 3: Commit**

```bash
git add tests/opponents/test_privacy_gate.py
git commit -m "$(cat <<'EOF'
test(o6): pin privacy_gate denylist boundary and rename-evasion coverage

- privacy_gate は writer 側の defense-in-depth 層として維持する
- 実際の unknown-field fail-closed は public_trajectory_projection の allow-list が担う
EOF
)"
```

---

## Task 5: Independent verifier — own privacy scan + own schema conformance check

**Files:**
- Modify: `src/mage_ptcg/opponents/independent_trajectory_verifier.py`
- Modify: `tests/opponents/test_independent_trajectory_verifier.py`

**Interfaces:**
- Consumes: `jsonschema` (third-party, generic), the shared `public_trajectory_schema_v1.json` (data artifact, allowed), stdlib only otherwise. Must still pass the existing AST-based import-boundary test — do **not** import `public_trajectory_projection`, `public_trajectory_evidence`, `trajectory`, `league_runtime`, `mage_ptcg.league.actual_runner`, or `mage_ptcg.competition_intelligence.canonical`.
- Produces: `verify_game(game_dir) -> dict` gains `schema_valid`/`schema_errors`, `privacy_valid` now computed by this module's **own** recursive scan (not imported from `privacy_gate`), and `event_type`/`step_index` sequencing checks are now schema-driven (fixes the schema_audit.json gap noted in the survey — the old code checked only first/last `event_type` string and `step_index` gaps, never validated the full event shape).

- [ ] **Step 1: Extend the tests**

Add to `tests/opponents/test_independent_trajectory_verifier.py`:

```python
def test_verifier_source_does_not_import_projection_or_writer_modules():
    import ast
    from pathlib import Path
    source = Path("src/mage_ptcg/opponents/independent_trajectory_verifier.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = {"public_trajectory_projection", "public_trajectory_evidence", "trajectory", "league_runtime",
                 "mage_ptcg.league.actual_runner", "mage_ptcg.competition_intelligence.canonical"}
    hit = {name for name in imported if any(name == f or name.endswith("." + f) for f in forbidden)}
    assert not hit, f"independent verifier imports runtime modules: {hit}"


def test_own_privacy_scan_rejects_opponent_hand_leak():
    from mage_ptcg.opponents.independent_trajectory_verifier import independent_privacy_scan
    event = {"public_payload": {"players": [{"hand": [{"id": 1}]}, {}], "board": {}, "result": None, "action": None}}
    result = independent_privacy_scan(event)
    assert result["status"] == "REJECTED"


def test_own_privacy_scan_passes_clean_projection():
    from mage_ptcg.opponents.independent_trajectory_verifier import independent_privacy_scan
    event = {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": 0,
              "seat_direction": None, "public_payload": {"players": [{"hand_count": 1}, {"hand_count": 1}], "board": {}, "result": None, "action": None}}
    assert independent_privacy_scan(event)["status"] == "PASS"


def test_schema_conformance_rejects_additional_property(tmp_path):
    from mage_ptcg.opponents.independent_trajectory_verifier import validate_event_schema
    bad = {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": 0,
           "seat_direction": None, "public_payload": {"players": [{}, {}], "board": {}, "result": None, "action": None},
           "unexpected_extra_key": 1}
    errors = validate_event_schema(bad)
    assert errors


def test_schema_conformance_rejects_bool_int_confusion():
    from mage_ptcg.opponents.independent_trajectory_verifier import validate_event_schema
    bad = {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": True,
           "seat_direction": None, "public_payload": {"players": [{}, {}], "board": {}, "result": None, "action": None}}
    errors = validate_event_schema(bad)
    assert errors
```

- [ ] **Step 2: Run to verify failures**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_independent_trajectory_verifier.py -k "own_privacy or schema_conformance or does_not_import_projection"`
Expected: FAIL (`independent_privacy_scan`/`validate_event_schema` not defined; import-boundary test passes vacuously until the new module exists, harmless)

- [ ] **Step 3: Implement**

Add to `independent_trajectory_verifier.py` (near the top, after the existing `_DOMAIN_PREFIX` definition), and wire `verify_game` to call both:

```python
import re as _re
import jsonschema as _jsonschema

_PUBLIC_TRAJECTORY_SCHEMA = json.loads((Path(__file__).parent / "public_trajectory_schema_v1.json").read_text(encoding="utf-8"))
_SCHEMA_VALIDATOR = _jsonschema.Draft202012Validator(_PUBLIC_TRAJECTORY_SCHEMA)

# Independently-written denylist: intentionally NOT the same implementation as
# mage_ptcg.opponents.privacy_gate (which the runtime writer uses) -- a bug shared
# between writer and verifier privacy logic must not be able to "confirm" itself.
_INDEPENDENT_FORBIDDEN_KEY_PATTERN = _re.compile(
    r"(hidden|secret|private|credential|password|token|api[_-]?key|rng[_-]?state|random[_-]?state|"
    r"engine[_-]?internal|hostname|username|\bpid\b|process[_-]?id|environ|env[_-]?var|debug[_-]?dump|"
    r"internal[_-]?state|memory[_-]?address|^logs$|^search_begin_input$|^hand$|^deck$)",
    re.IGNORECASE,
)
_INDEPENDENT_FORBIDDEN_VALUE_PATTERN = re.compile(
    r"(object at 0x[0-9a-fA-F]+|/home/[^\s\"']+|/Users/[^\s\"']+|/root/[^\s\"']+|/tmp/[^\s\"']+)"
)


def _independent_privacy_walk(value: Any, *, path: str) -> tuple[str, str] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                return (path, "non-string key")
            if _INDEPENDENT_FORBIDDEN_KEY_PATTERN.search(key):
                return (f"{path}.{key}", f"forbidden field name pattern: {key!r}")
            if key == "hand" and child is not None:
                return (f"{path}.{key}", "raw hand contents present")
            found = _independent_privacy_walk(child, path=f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(value, list):
        for index, child in enumerate(value):
            found = _independent_privacy_walk(child, path=f"{path}[{index}]")
            if found:
                return found
        return None
    if isinstance(value, str) and _INDEPENDENT_FORBIDDEN_VALUE_PATTERN.search(value):
        return (path, f"forbidden value pattern: {value[:80]!r}")
    return None


def independent_privacy_scan(event: Any) -> dict[str, Any]:
    """Verifier's own recursive privacy scan -- does not import ``privacy_gate``."""
    violation = _independent_privacy_walk(event, path="$")
    if violation is None:
        return {"status": "PASS", "violation": None}
    path, reason = violation
    return {"status": "REJECTED", "violation": {"path": path, "reason": reason}}


def validate_event_schema(event: Any) -> list[str]:
    """Independent JSON-Schema conformance check against the shared schema artifact."""
    return [f"{'.'.join(str(p) for p in error.path)}: {error.message}" for error in _SCHEMA_VALIDATOR.iter_errors(event)]
```

Then update `verify_game`'s event loop:

```python
        events = parse_public_trajectory_jsonl_gz(jsonl_path)
        schema_errors: list[str] = []
        for event in events:
            schema_errors.extend(validate_event_schema(event))
            if independent_privacy_scan(event)["status"] != "PASS":
                result["privacy_valid"] = False
        if schema_errors:
            result["schema_valid"] = False
            result["schema_errors"] = schema_errors[:20]
        else:
            result["schema_valid"] = True
```

Rename `parse_public_trajectory_jsonl_gz`'s hardcoded filename usage (it currently takes a `path`; the call site changes from `game_dir / "public_trajectory.jsonl.gz"` to `game_dir / "public_projection_trajectory.jsonl.gz"`), and update `reconstruct_canonical_steps`'s `event_type` string checks from `INITIAL_PUBLIC_OBSERVATION`/`TERMINAL_PUBLIC_OBSERVATION` to `INITIAL_PUBLIC_STATE`/`TERMINAL_PUBLIC_STATE`, and switch `canonical_steps` reconstruction to read from `event["public_payload"]` instead of `event["public_step"]` (project the new event shape back into the `[seat0_like, seat1_like]` shape `recompute_digests`/`_action_trace` expect, or — simpler and safer — recompute digests directly over the new event list structure; see Step 3b). Also make `verify_game`'s overall pass/fail (`result["match"]`) additionally require `result["schema_valid"]`, so a schema violation blocks `match=True` even if digests happen to coincide.

**Step 3b (digest domain change):** since the payload shape changed, the digest inputs change too — this is intentional and required by Task 6 (schema-version-affects-digest). Recompute directly over the ordered event list's `public_payload` values instead of reconstructing an old-shape `canonical_steps`:

```python
def _independent_canonical_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(events, key=lambda e: e.get("step_index", -1))
    if [e.get("step_index") for e in ordered] != list(range(len(ordered))):
        raise MalformedTrajectoryError("step_index sequence has gaps or duplicates")
    if ordered[0].get("event_type") != "INITIAL_PUBLIC_STATE":
        raise MalformedTrajectoryError("first event is not INITIAL_PUBLIC_STATE")
    if ordered[-1].get("event_type") != "TERMINAL_PUBLIC_STATE":
        raise MalformedTrajectoryError("last event is not TERMINAL_PUBLIC_STATE (missing terminal)")
    return ordered


def recompute_digests(events: list[dict[str, Any]]) -> dict[str, Any]:
    action_trace = [{"step": e["step_index"], "seat_direction": e["seat_direction"], "action": e["public_payload"]["action"]}
                     for e in events if e["public_payload"]["action"] is not None]
    return {
        "schema_version_digest_input": events[0].get("schema_version"),
        "initial_observation_digest": independent_digest({"schema_version": events[0]["schema_version"], "payload": events[0]["public_payload"]}, domain="o6-trajectory-initial"),
        "terminal_observation_digest": independent_digest({"schema_version": events[-1]["schema_version"], "payload": events[-1]["public_payload"]}, domain="o6-trajectory-terminal"),
        "action_trace_digest": independent_digest({"schema_version": events[0]["schema_version"], "trace": action_trace}, domain="o6-trajectory-actions"),
        "complete_trajectory_digest": independent_digest({"schema_version": events[0]["schema_version"], "events": [{"event_type": e["event_type"], "step_index": e["step_index"], "seat_direction": e["seat_direction"], "public_payload": e["public_payload"]} for e in events]}, domain="o6-trajectory-complete"),
    }
```

Update `verify_game` and `reconstruct_canonical_steps` call sites accordingly (replace `reconstruct_canonical_steps(events)` with `_independent_canonical_events(events)`, drop the old `_action_trace` helper). Update `parse_public_trajectory_jsonl_gz` call sites' filename to `public_projection_trajectory.jsonl.gz`.

**Mirror this exact digest formula on the runtime side too** (Task 6 depends on it): update `trajectory.py::compute_trajectory_digests` to accept the new event list shape and include `schema_version` in every digest input the same way — this is covered explicitly in Task 6, don't do it here; for now just get `independent_trajectory_verifier.py`'s own tests green using its own recomputation (the runtime/independent comparison is re-wired in Task 6).

- [ ] **Step 4: Run and verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_independent_trajectory_verifier.py -v`
Expected: new tests PASS; pre-existing tests that assumed the old `public_step`/`INITIAL_PUBLIC_OBSERVATION` shape will now fail — update those fixtures in the same file to the new event shape (use Task 2's `_step`/`_observation` helper style, or call `public_trajectory_projection.build_public_trajectory_events` directly in the fixture setup — importing the projection builder from a *test* file is fine; only the verifier *module* itself must not import it).

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/opponents/independent_trajectory_verifier.py tests/opponents/test_independent_trajectory_verifier.py
git commit -m "$(cat <<'EOF'
fix(o6): give independent verifier its own privacy scan and schema check

- independent_privacy_scan / validate_event_schema を verifier 内で独自実装
- privacy_gate や projection builder を import しない（AST 境界テストで維持）
- digest 入力に schema_version を含め、public_payload 形状の変更を反映
EOF
)"
```

---

## Task 6: schema_version participates in the digest (runtime + independent)

**Files:**
- Modify: `src/mage_ptcg/opponents/trajectory.py`
- Modify: `tests/opponents/test_trajectory.py`

**Interfaces:**
- `compute_trajectory_digests` now takes the **event list** (Task 2's `build_public_trajectory_events` output), not raw `env.steps` — call sites in `league_runtime.py::play_game` update accordingly (Task 7).
- Produces digests using the identical formula Task 5 implemented independently (same domain strings, same `{"schema_version": ..., "payload"/"events"/"trace": ...}` wrapping) — this is deliberate parity (both sides must agree on what's *in* the digest; they must NOT share the *function*).

- [ ] **Step 1: Extend the failing test**

Add to `tests/opponents/test_trajectory.py`:

```python
def test_schema_version_change_changes_digest():
    from mage_ptcg.opponents.trajectory import compute_trajectory_digests
    events = [
        {"schema_version": "o6-public-trajectory-v1", "event_type": "INITIAL_PUBLIC_STATE", "step_index": 0, "seat_direction": None,
         "public_payload": {"players": [{}, {}], "board": {}, "result": None, "action": None}},
        {"schema_version": "o6-public-trajectory-v1", "event_type": "TERMINAL_PUBLIC_STATE", "step_index": 1, "seat_direction": None,
         "public_payload": {"players": [{}, {}], "board": {}, "result": 0, "action": None}},
    ]
    baseline = compute_trajectory_digests(events)
    bumped = [dict(e, schema_version="o6-public-trajectory-v2") for e in events]
    bumped_digests = compute_trajectory_digests(bumped)
    assert baseline["complete_trajectory_digest"] != bumped_digests["complete_trajectory_digest"]
```

- [ ] **Step 2: Run, verify it fails** (current `compute_trajectory_digests` takes raw steps, not events, and won't accept this shape / won't vary with schema_version)

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_trajectory.py -k schema_version_change`
Expected: FAIL

- [ ] **Step 3: Rewrite `compute_trajectory_digests`**

Replace the body in `trajectory.py` (keep the function name and `TRAJECTORY_DIGEST_SCHEMA_VERSION` constant; remove `canonical_step_seat`/`strip_volatile_observation`/`_VOLATILE_OBSERVATION_KEYS` usage from this function — those helpers can stay in the module since other code may still reference them, but this function no longer needs them because the new event projection already excludes `remainingOverageTime` upstream in Task 2's `_OBSERVATION_KEYS` allow-list, which never includes it):

```python
def compute_trajectory_digests(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compute a digest set from PUBLIC_TRAJECTORY_PROJECTION_V1 events (see
    :mod:`mage_ptcg.opponents.public_trajectory_projection`).

    Mirrors (but does not share code with)
    :func:`mage_ptcg.opponents.independent_trajectory_verifier.recompute_digests`
    exactly in *what* participates: every digest input is wrapped with the
    event's own ``schema_version`` so a schema bump changes every digest,
    even if the payload content is byte-identical.
    """
    if not events:
        raise OpponentError("cannot compute trajectory digests from empty events")
    ordered = sorted(events, key=lambda e: e["step_index"])
    schema_version = ordered[0]["schema_version"]
    action_trace = [{"step": e["step_index"], "seat_direction": e["seat_direction"], "action": e["public_payload"]["action"]}
                     for e in ordered if e["public_payload"]["action"] is not None]
    return {
        "schema_version": TRAJECTORY_DIGEST_SCHEMA_VERSION,
        "initial_observation_digest": digest({"schema_version": schema_version, "payload": ordered[0]["public_payload"]}, domain="o6-trajectory-initial"),
        "terminal_observation_digest": digest({"schema_version": schema_version, "payload": ordered[-1]["public_payload"]}, domain="o6-trajectory-terminal"),
        "action_trace_digest": digest({"schema_version": schema_version, "trace": action_trace}, domain="o6-trajectory-actions"),
        "complete_trajectory_digest": digest({"schema_version": schema_version, "events": [
            {"event_type": e["event_type"], "step_index": e["step_index"], "seat_direction": e["seat_direction"], "public_payload": e["public_payload"]} for e in ordered
        ]}, domain="o6-trajectory-complete"),
        "game_length": len(ordered),
        "raw_action_count": len(action_trace),
    }
```

- [ ] **Step 4: Run full `test_trajectory.py`, fix any other now-broken tests in that file**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_trajectory.py -v`
Expected: any pre-existing test that called `compute_trajectory_digests` with raw `env.steps`-shaped input must be updated to pass event-list fixtures instead (mirror the `_step`/event-building pattern from Task 2's test file, or call `public_trajectory_projection.build_public_trajectory_events` in the fixture). All PASS after updating.

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/opponents/trajectory.py tests/opponents/test_trajectory.py
git commit -m "$(cat <<'EOF'
fix(o6): include schema_version in trajectory digest inputs

- compute_trajectory_digests は生 env.steps でなく public trajectory event を入力とする
- schema_version の変更が全 digest に反映されることをテストで固定する
EOF
)"
```

---

## Task 7: `league_runtime.py::play_game` produces events, not raw canonical_steps

**Files:**
- Modify: `src/mage_ptcg/opponents/league_runtime.py`
- Modify: `tests/opponents/test_league_runtime.py`

**Interfaces:**
- `play_game(...)` return dict's `"canonical_steps"` key is renamed to `"public_trajectory_events"` (the events list from Task 2, computed once and reused for both digesting and persistence — no second raw-observation pass). `"trajectory"` key keeps computing via `trajectory.compute_trajectory_digests` (Task 6's new signature). **Do not touch `NativeAgentWorker.__init__`'s `Path(source_root).resolve()` line or its comment.**

- [ ] **Step 1: Update the failing test**

In `tests/opponents/test_league_runtime.py`, update `test_play_game_canonical_steps_and_digest_consistency` (or equivalent) to assert on `result["public_trajectory_events"]` instead of `result["canonical_steps"]`, and assert every event's `schema_version == "o6-public-trajectory-v1"`.

- [ ] **Step 2: Run, verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_league_runtime.py`
Expected: FAIL (KeyError on the old key, or missing key on the new one)

- [ ] **Step 3: Implement**

In `league_runtime.py::play_game`, replace:

```python
    from .trajectory import canonical_step_seat, compute_trajectory_digests, determine_engine_seed_capability

    def _canonical_steps_or_none(steps: Any) -> list[list[dict[str, Any]]] | None:
        if not steps:
            return None
        return [[canonical_step_seat(seat) for seat in step] for step in steps]
```

with:

```python
    from .public_trajectory_projection import PublicSchemaUnknownFieldError, build_public_trajectory_events
    from .trajectory import canonical_step_seat, compute_trajectory_digests, determine_engine_seed_capability

    def _public_events_or_none(steps: Any) -> list[dict[str, Any]] | None:
        if not steps:
            return None
        canonical_steps = [[canonical_step_seat(seat) for seat in step] for step in steps]
        try:
            return build_public_trajectory_events(canonical_steps)
        except PublicSchemaUnknownFieldError:
            return None
```

and every `_canonical_steps_or_none(...)` call site → `_public_events_or_none(...)`, storing into `"public_trajectory_events"` instead of `"canonical_steps"`; `trajectory = compute_trajectory_digests(environment.steps)` → `compute_trajectory_digests(public_events)` computed from the same `_public_events_or_none(...)` result (compute the events once, reuse for both `trajectory` and the returned key — do not call `build_public_trajectory_events` twice). Both the success path and the `except (TimeoutError, OpponentError)` fault path need this change; on the fault path, if `build_public_trajectory_events` itself raises `PublicSchemaUnknownFieldError` (e.g., a partial/corrupt observation on a crashed game), treat it the same as "no trajectory" (`trajectory = None`, `public_trajectory_events = None`) rather than letting the exception propagate out of `play_game` — a fault-path game with unprojectable observations should surface as "no evidence for this game," not crash the League script.

- [ ] **Step 4: Run and verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_league_runtime.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/opponents/league_runtime.py tests/opponents/test_league_runtime.py
git commit -m "$(cat <<'EOF'
fix(o6): play_game emits public trajectory events, not raw canonical steps

- canonical_steps キーを public_trajectory_events に置換
- NativeAgentWorker の絶対パス解決修正は変更しない
EOF
)"
```

---

## Task 8: Integrity chain module + trusted root registry

**Files:**
- Create: `src/mage_ptcg/opponents/league_integrity_chain.py`
- Create: `tests/opponents/test_league_integrity_chain.py`
- Create: `docs/evidence/o6-trusted-league-roots.json` (seeded empty `{"trusted_roots": []}`, populated by the real run in Task 10)

**Interfaces:**
- Produces: `RUN_MANIFEST_SCHEMA_VERSION`, `RUN_ROOT_SCHEMA_VERSION`, `compute_run_root_sha256(run_dir, *, exclude) -> str`, `build_run_manifest(*, run_id, sorted_game_ids, game_manifest_hashes, summary_hash, participant_ids, population_id, team_bundle_hashes, ruleset_version, cabt_version, evidence_format_version) -> dict`, `write_trusted_root_entry(registry_path, *, run_id, run_root_sha256, source_commit, population_id, evidence_schema, status="TRUSTED") -> None`, `load_trusted_root_entry(registry_path, run_id) -> dict | None`.
- Consumed by: `scripts/run_o6_team_league.py` (Task 9, orchestration), and independently re-derived (not imported) by `independent_trajectory_verifier.py`'s chain-verification additions (Task 9b).

- [ ] **Step 1: Write the failing tests**

```python
# tests/opponents/test_league_integrity_chain.py
from __future__ import annotations

import json
from pathlib import Path

from mage_ptcg.opponents.league_integrity_chain import (
    build_run_manifest,
    compute_run_root_sha256,
    load_trusted_root_entry,
    write_trusted_root_entry,
)


def test_compute_run_root_sha256_changes_on_any_file_change(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"x": 1}', encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.json").write_text('{"y": 2}', encoding="utf-8")
    before = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    (tmp_path / "sub" / "b.json").write_text('{"y": 3}', encoding="utf-8")
    after = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    assert before != after


def test_compute_run_root_sha256_changes_on_file_insertion_or_deletion(tmp_path: Path):
    (tmp_path / "a.json").write_text('{"x": 1}', encoding="utf-8")
    before = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    (tmp_path / "b.json").write_text('{"z": 1}', encoding="utf-8")
    after = compute_run_root_sha256(tmp_path, exclude={"run_root.sha256"})
    assert before != after


def test_run_manifest_contains_required_fields():
    manifest = build_run_manifest(
        run_id="run-1", sorted_game_ids=["g1", "g2"], game_manifest_hashes={"g1": "h1", "g2": "h2"},
        summary_hash="sh", participant_ids=["a", "b"], population_id="pop-1", team_bundle_hashes={"a": "bh"},
        ruleset_version="rv1", cabt_version="cv1", evidence_format_version="ev1",
    )
    for key in ("run_id", "schema_version", "canonicalization_version", "sorted_game_ids", "game_manifest_hashes",
                "summary_hash", "participant_ids", "population_id", "team_bundle_hashes", "ruleset_version",
                "cabt_version", "evidence_format_version"):
        assert key in manifest


def test_trusted_root_round_trip(tmp_path: Path):
    registry = tmp_path / "roots.json"
    write_trusted_root_entry(registry, run_id="run-1", run_root_sha256="abc", source_commit="deadbeef",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    entry = load_trusted_root_entry(registry, "run-1")
    assert entry is not None
    assert entry["run_root_sha256"] == "abc"
    assert entry["status"] == "TRUSTED"


def test_trusted_root_missing_run_id_returns_none(tmp_path: Path):
    registry = tmp_path / "roots.json"
    write_trusted_root_entry(registry, run_id="run-1", run_root_sha256="abc", source_commit="deadbeef",
                              population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    assert load_trusted_root_entry(registry, "run-does-not-exist") is None
```

- [ ] **Step 2: Run, verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_league_integrity_chain.py`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement**

```python
# src/mage_ptcg/opponents/league_integrity_chain.py
"""Multi-level evidence integrity chain and external trusted-root anchor (O6-AUD-002 INTEGRITY-001).

Runtime/orchestrator-only: builds ``run_manifest.json`` and computes
``run_root.sha256`` so tampering with any manifest/hashes/summary file --
not just the raw trajectory bytes -- is detectable. The independent verifier
re-derives its own expectation of these values from disk at verification
time (see ``independent_trajectory_verifier.py``); it does not import this
module, matching the same independence discipline as the rest of O6-AUD-002.

External anchoring: ``run_root.sha256`` living inside the run directory
cannot detect an attacker who rewrites the whole run directory (including
that file) consistently. ``docs/evidence/o6-trusted-league-roots.json`` is
committed to git *outside* the run directory; its own git history is the
actual external trust anchor -- a run's ``run_root_sha256`` cannot be
silently changed there without a new, reviewable git commit.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

RUN_MANIFEST_SCHEMA_VERSION = "o6-league-run-manifest-v2"
RUN_ROOT_CANONICALIZATION_VERSION = "o6-run-root-canonical-v1"
TRUSTED_ROOT_REGISTRY_SCHEMA_VERSION = "o6-trusted-league-roots-v1"


def compute_run_root_sha256(run_dir: Path, *, exclude: set[str] = frozenset()) -> str:
    """SHA-256 over a canonical, sorted ``{relative_path: file_sha256}`` mapping of every file under ``run_dir``.

    Any file content change, insertion, deletion, or rename under ``run_dir``
    changes this hash (each is a change to the mapping's keys and/or values).
    """
    run_dir = Path(run_dir)
    entries: dict[str, str] = {}
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        relpath = path.relative_to(run_dir).as_posix()
        if relpath in exclude:
            continue
        entries[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def build_run_manifest(*, run_id: str, sorted_game_ids: list[str], game_manifest_hashes: Mapping[str, str],
                        summary_hash: str, participant_ids: list[str], population_id: str,
                        team_bundle_hashes: Mapping[str, str], ruleset_version: str, cabt_version: str,
                        evidence_format_version: str) -> dict[str, Any]:
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "canonicalization_version": RUN_ROOT_CANONICALIZATION_VERSION,
        "run_id": run_id,
        "sorted_game_ids": sorted(sorted_game_ids),
        "game_manifest_hashes": dict(game_manifest_hashes),
        "summary_hash": summary_hash,
        "participant_ids": sorted(participant_ids),
        "population_id": population_id,
        "team_bundle_hashes": dict(team_bundle_hashes),
        "ruleset_version": ruleset_version,
        "cabt_version": cabt_version,
        "evidence_format_version": evidence_format_version,
    }


def write_trusted_root_entry(registry_path: Path, *, run_id: str, run_root_sha256: str, source_commit: str,
                              population_id: str, evidence_schema: str, status: str = "TRUSTED") -> None:
    registry_path = Path(registry_path)
    registry: dict[str, Any] = {"schema_version": TRUSTED_ROOT_REGISTRY_SCHEMA_VERSION, "trusted_roots": []}
    if registry_path.exists():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    entries = [e for e in registry.get("trusted_roots", []) if e.get("run_id") != run_id]
    entries.append({
        "run_id": run_id, "run_root_sha256": run_root_sha256, "source_commit": source_commit,
        "population_id": population_id, "evidence_schema": evidence_schema, "status": status,
    })
    registry["trusted_roots"] = sorted(entries, key=lambda e: e["run_id"])
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(json.dumps(registry, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def load_trusted_root_entry(registry_path: Path, run_id: str) -> dict[str, Any] | None:
    registry_path = Path(registry_path)
    if not registry_path.exists():
        return None
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for entry in registry.get("trusted_roots", []):
        if entry.get("run_id") == run_id:
            return entry
    return None
```

Seed `docs/evidence/o6-trusted-league-roots.json`:

```json
{
  "schema_version": "o6-trusted-league-roots-v1",
  "trusted_roots": []
}
```

- [ ] **Step 4: Run and verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_league_integrity_chain.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/opponents/league_integrity_chain.py tests/opponents/test_league_integrity_chain.py docs/evidence/o6-trusted-league-roots.json
git commit -m "$(cat <<'EOF'
feat(o6): anchor complete league evidence integrity chain

- run_manifest.json / run_root.sha256 の構築ロジックを追加
- docs/evidence/o6-trusted-league-roots.json を外部 anchor として新設
EOF
)"
```

---

## Task 9: Independent verifier — full chain mode (`--trusted-root-registry` / `--expected-root-sha256`)

**Files:**
- Modify: `src/mage_ptcg/opponents/independent_trajectory_verifier.py`
- Modify: `tests/opponents/test_independent_trajectory_verifier.py`
- Modify: `tests/opponents/test_cli_verify_league_trajectories.py`

**Interfaces:**
- Adds `verify_game_integrity(game_dir) -> dict` (hashes.json file-hash check, independent of trajectory content), `verify_run_chain(run_dir, *, trusted_root_registry=None, expected_root_sha256=None) -> dict` (top-level: run_manifest.json/run_summary.json/run_root.sha256/per-game trajectory+integrity, requires exactly one of the two anchor args or returns `{"status": "UNANCHORED_EVIDENCE", ...}`), and CLI gains `--mode {trajectory,full}` (default `trajectory`, backward-compatible with today's per-game-only usage), `--trusted-root-registry PATH`, `--expected-root-sha256 HEX`.
- **Does not** import `league_integrity_chain` (that module is runtime/orchestrator-only); re-derives its own run-root hash the same way `league_integrity_chain.compute_run_root_sha256` does, but as an independently-written function inside this module.

- [ ] **Step 1: Write the failing tests**

Add to `tests/opponents/test_independent_trajectory_verifier.py`:

```python
def test_full_mode_without_anchor_returns_unanchored_evidence(tmp_path):
    from mage_ptcg.opponents.independent_trajectory_verifier import verify_run_chain
    (tmp_path / "games").mkdir()
    result = verify_run_chain(tmp_path)
    assert result["status"] == "UNANCHORED_EVIDENCE"


def test_full_mode_wrong_expected_root_fails(tmp_path):
    from mage_ptcg.opponents.independent_trajectory_verifier import verify_run_chain
    (tmp_path / "games").mkdir()
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "run_summary.json").write_text("{}", encoding="utf-8")
    result = verify_run_chain(tmp_path, expected_root_sha256="0" * 64)
    assert result["status"] != "PASS"
    assert result["root_hash_match"] is False


def test_verifier_source_still_does_not_import_integrity_chain_module():
    import ast
    from pathlib import Path
    source = Path("src/mage_ptcg/opponents/independent_trajectory_verifier.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(name.endswith("league_integrity_chain") for name in imported)
```

Add to `tests/opponents/test_cli_verify_league_trajectories.py`: one test invoking the CLI subprocess with `--mode full` and no anchor flags, asserting nonzero exit and `"UNANCHORED_EVIDENCE"` in stdout/stderr JSON.

- [ ] **Step 2: Run, verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_independent_trajectory_verifier.py -k full_mode`
Expected: FAIL

- [ ] **Step 3: Implement**

Add to `independent_trajectory_verifier.py`:

```python
def _independent_run_root_sha256(run_dir: Path, *, exclude: set[str]) -> str:
    """Independently-written mirror of league_integrity_chain.compute_run_root_sha256 -- not imported."""
    entries: dict[str, str] = {}
    for path in Path(run_dir).rglob("*"):
        if not path.is_file():
            continue
        relpath = path.relative_to(run_dir).as_posix()
        if relpath in exclude:
            continue
        entries[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    canonical = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_game_integrity(game_dir: Path) -> dict[str, Any]:
    game_dir = Path(game_dir)
    result: dict[str, Any] = {"game_dir_id": game_dir.name, "hashes_valid": False}
    try:
        hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
        mismatches = []
        for relpath, expected in hashes.get("files", {}).items():
            target = game_dir / relpath
            actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if actual != expected:
                mismatches.append(relpath)
        result["hashes_valid"] = not mismatches
        result["mismatched_files"] = mismatches
    except (OSError, json.JSONDecodeError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def verify_run_chain(run_dir: Path, *, trusted_root_registry: Path | None = None, expected_root_sha256: str | None = None) -> dict[str, Any]:
    run_dir = Path(run_dir)
    if trusted_root_registry is None and expected_root_sha256 is None:
        return {"status": "UNANCHORED_EVIDENCE", "reason": "neither --trusted-root-registry nor --expected-root-sha256 was provided"}
    anchor_root_sha256 = expected_root_sha256
    anchor_source = "expected_root_sha256"
    if trusted_root_registry is not None:
        registry = json.loads(Path(trusted_root_registry).read_text(encoding="utf-8"))
        run_manifest_path = run_dir / "run_manifest.json"
        run_id = None
        if run_manifest_path.exists():
            try:
                run_id = json.loads(run_manifest_path.read_text(encoding="utf-8")).get("run_id")
            except json.JSONDecodeError:
                run_id = None
        entry = next((e for e in registry.get("trusted_roots", []) if e.get("run_id") == run_id), None)
        if entry is None or entry.get("status") != "TRUSTED":
            return {"status": "UNANCHORED_EVIDENCE", "reason": f"no TRUSTED entry for run_id={run_id!r} in {trusted_root_registry}"}
        anchor_root_sha256 = entry["run_root_sha256"]
        anchor_source = "trusted_root_registry"

    actual_root_sha256 = _independent_run_root_sha256(run_dir, exclude={"run_root.sha256"})
    root_hash_match = actual_root_sha256 == anchor_root_sha256
    base = verify_league_evidence(run_dir)
    per_game_integrity = {p.name: verify_game_integrity(p) for p in sorted((run_dir / "games").iterdir()) if p.is_dir()} if (run_dir / "games").exists() else {}
    hashes_all_valid = all(g["hashes_valid"] for g in per_game_integrity.values()) if per_game_integrity else False
    status = "PASS" if (root_hash_match and hashes_all_valid and base["digest_mismatches"] == 0 and base["malformed_trajectories"] == 0
                        and base["privacy_violations"] == 0 and base.get("schema_violations", 0) == 0) else "FAIL"
    return {
        "status": status, "anchor_source": anchor_source, "expected_root_sha256": anchor_root_sha256,
        "actual_root_sha256": actual_root_sha256, "root_hash_match": root_hash_match,
        "per_game_integrity": per_game_integrity, "trajectory_verification": base,
    }
```

Update `verify_league_evidence` to also surface a `schema_violations` count (sum of games with `schema_valid is False`), and update `main()`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mage_ptcg.opponents.independent_trajectory_verifier")
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mode", choices=["trajectory", "full"], default="trajectory")
    parser.add_argument("--trusted-root-registry", type=Path, default=None)
    parser.add_argument("--expected-root-sha256", default=None)
    args = parser.parse_args(argv)
    if args.mode == "trajectory":
        summary = verify_league_evidence(Path(args.evidence))
        ok = summary["digest_mismatches"] == 0 and summary["malformed_trajectories"] == 0 and summary["privacy_violations"] == 0 and summary.get("schema_violations", 0) == 0
    else:
        summary = verify_run_chain(Path(args.evidence), trusted_root_registry=args.trusted_root_registry, expected_root_sha256=args.expected_root_sha256)
        ok = summary["status"] == "PASS"
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    else:
        for key, value in summary.items():
            if key not in ("per_game", "per_game_integrity", "trajectory_verification"):
                print(f"{key}: {value}")
    return 0 if ok else 1
```

- [ ] **Step 4: Run and verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents/test_independent_trajectory_verifier.py tests/opponents/test_cli_verify_league_trajectories.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/mage_ptcg/opponents/independent_trajectory_verifier.py tests/opponents/test_independent_trajectory_verifier.py tests/opponents/test_cli_verify_league_trajectories.py
git commit -m "$(cat <<'EOF'
feat(o6): anchor complete league evidence integrity chain

- verify_run_chain / verify_game_integrity を追加し full mode を新設
- trusted-root-registry か expected-root-sha256 が無ければ UNANCHORED_EVIDENCE
EOF
)"
```

---

## Task 10: League runner orchestration rewrite + tamper test fixtures

**Files:**
- Modify: `scripts/run_o6_team_league.py`
- Modify: `tests/test_run_o6_team_league.py`

**Interfaces:**
- New default `--evidence-root` → `docs/evidence/o6-opponent-intelligence-v4`.
- New `league_run_id` pattern: `o6-team-league-{population_hash[:12]}-public-v2`.
- Orchestration order (per Task spec §8, §10, §12): play all pairs (writer never touches raw observations, per Task 3/7) → verifier subprocess `--mode trajectory` over `games/` → abort without writing anything final if any mismatch/malformed/privacy/schema violation → write `independent_digest.txt` per game from that pass's `independent_digests` → recompute each game's `hashes.json` to additionally cover `independent_digest.txt` → build `run_manifest.json` (via `league_integrity_chain.build_run_manifest`) and `run_summary.json` (the existing `league_summary.json` content, also duplicated/renamed to `run_summary.json` at the evidence root per the required file list) → compute `run_root.sha256` (via `league_integrity_chain.compute_run_root_sha256`, excluding `run_root.sha256` and `checksums.sha256` themselves) → `compute_checksums_file` (kept, external `sha256sum -c` convenience) → register trusted root in `docs/evidence/o6-trusted-league-roots.json` → final gate: re-invoke verifier subprocess `--mode full --trusted-root-registry docs/evidence/o6-trusted-league-roots.json`; abort if not `PASS`.

- [ ] **Step 1: Update `tests/test_run_o6_team_league.py`'s existing two tests**

Update `test_valid_run_produces_v3_summary_and_evidence_root_artifacts` (rename to `..._v4_summary_and_evidence_root_artifacts` if the test name embeds the version) to additionally assert: `run_manifest.json`, `run_summary.json`, `run_root.sha256` exist at the evidence root; every game directory has exactly `public_projection_trajectory.jsonl.gz, trajectory_manifest.json, runtime_digest.txt, independent_digest.txt, hashes.json, game_metadata.json`; `docs/evidence/o6-trusted-league-roots.json` (in the test's isolated tmp copy — see how the existing test isolates evidence roots, likely via `monkeypatch`/`tmp_path` fixtures already in that file) gains a `TRUSTED` entry for the run's `league_run_id`. Keep `test_digest_mismatch_aborts_without_writing_final_summary` structurally the same, just updated for new filenames.

- [ ] **Step 2: Run, verify failure**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_run_o6_team_league.py`
Expected: FAIL against current script behavior

- [ ] **Step 3: Rewrite `scripts/run_o6_team_league.py`**

Apply these changes to the existing file (structure preserved, `run_pair`/`_load_participants`/`_make_callable` untouched except the import line and the `canonical_steps`→`public_trajectory_events` key rename from Task 7):

```python
from mage_ptcg.opponents.public_trajectory_evidence import compute_checksums_file, persist_game_evidence, write_immutable_json  # noqa: E402
from mage_ptcg.opponents.league_integrity_chain import build_run_manifest, compute_run_root_sha256, write_trusted_root_entry  # noqa: E402
import subprocess as _subprocess  # already imported as subprocess at top; reuse existing import
```

In `run_pair`'s `play()` closure, change `canonical_steps = raw.get("canonical_steps")` → `public_events = raw.get("public_trajectory_events")` and the `persist_game_evidence(..., canonical_steps=canonical_steps, ...)` call → `persist_game_evidence(..., canonical_steps=..., ...)` still takes raw canonical_steps per Task 3's signature — **keep `persist_game_evidence`'s existing parameter name `canonical_steps`** (it internally calls `build_public_trajectory_events` itself); pass `raw.get("canonical_steps")` if `league_runtime.py` still also returns that key alongside `public_trajectory_events`, or pass the raw per-step data needed. To avoid double-computation ambiguity, have `play_game` (Task 7) return **both** `public_trajectory_events` (for the trajectory digest / return value) **and** keep a `canonical_steps` key too (revert Task 7's rename decision to an addition instead of a rename): `play_game` computes `canonical_steps` once, derives `public_trajectory_events` from it once via `build_public_trajectory_events`, and returns both keys. This keeps `persist_game_evidence`'s existing `canonical_steps=` call site in `run_o6_team_league.py` unchanged. **(Apply this correction retroactively to Task 7 Step 3: add `"canonical_steps": canonical_steps` back into `play_game`'s two return dicts alongside the new `"public_trajectory_events": public_events` key, so both Task 3's writer and Task 6's digesting have what they need without recomputing.)**

Add a `_finalize_evidence_root` function replacing the tail of `main()` from `print("[league] all pairs finished...")` onward:

```python
def _run_verifier_subprocess(evidence_root: Path, *, mode: str, extra_args: list[str] | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        [sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json", "--mode", mode, *(extra_args or [])],
        capture_output=True, text=True, cwd=REPOSITORY_ROOT,
        env={**os.environ, "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode not in (0, 1):
        raise SystemExit(f"independent verifier crashed: {completed.stderr[-2000:]}")
    return json.loads(completed.stdout)
```

Replace the old `_run_independent_verifier` calls with this, keeping the existing trajectory-mode abort logic (now also checking `schema_violations`), then after `compute_checksums_file`:

```python
    print("[league] all pairs finished; independently re-verifying public trajectory evidence...", file=sys.stderr, flush=True)
    verify_result = _run_verifier_subprocess(evidence_root, mode="trajectory")
    if (verify_result["digest_mismatches"] or verify_result["malformed_trajectories"] or verify_result["privacy_violations"]
            or verify_result.get("schema_violations", 0)):
        raise SystemExit(f"independent verification failed, refusing to write a final league summary: {verify_result}")
    if verify_result["game_count"] != len(all_trajectory_records):
        raise SystemExit(f"raw evidence game_count ({verify_result['game_count']}) does not match recorded trajectory records ({len(all_trajectory_records)})")

    per_game = verify_result["per_game"]
    verified_records: list[dict[str, Any]] = []
    game_manifest_hashes: dict[str, str] = {}
    for record in all_trajectory_records:
        game_dir_id = _game_dir_id(record["pair_id"], record["match_index"])
        game_result = per_game.get(game_dir_id)
        if game_result is None:
            raise SystemExit(f"no independent verification result for {game_dir_id}")
        verified = dict(record)
        verified.update(game_result["independent_digests"])
        verified_records.append(verified)
        game_dir = evidence_root / "games" / game_dir_id
        (game_dir / "independent_digest.txt").write_text(json.dumps(game_result["independent_digests"], sort_keys=True) + "\n", encoding="utf-8")
        hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
        hashes["files"]["independent_digest.txt"] = sha256_hex((game_dir / "independent_digest.txt").read_bytes())
        (game_dir / "hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        game_manifest_hashes[game_dir_id] = sha256_hex((game_dir / "trajectory_manifest.json").read_bytes())

    # ... (pair_statistics / bradley_terry / summary dict construction: unchanged from current script) ...

    (output_dir / "league_summary.json").write_text(..., encoding="utf-8")
    run_summary_bytes = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    (evidence_root / "run_summary.json").write_bytes(run_summary_bytes)

    league_run_id = f"o6-team-league-{(manifest.get('population_identity_hash') or 'unknown')[:12]}-public-v2"
    run_manifest = build_run_manifest(
        run_id=league_run_id, sorted_game_ids=sorted(game_manifest_hashes), game_manifest_hashes=game_manifest_hashes,
        summary_hash=sha256_hex(run_summary_bytes), participant_ids=sorted(participants), population_id=args.population,
        team_bundle_hashes={pid: p.get("bundle_hash", "unknown") for pid, p in participants.items() if p["kind"] == "native"},
        ruleset_version=manifest.get("ruleset_version", "unknown"), cabt_version=manifest.get("cabt_version", "unknown"),
        evidence_format_version="o6-evidence-format-v1",
    )
    write_immutable_json(evidence_root / "run_manifest.json", run_manifest)
    compute_checksums_file(evidence_root, evidence_root / "checksums.sha256")
    run_root_sha256 = compute_run_root_sha256(evidence_root, exclude={"run_root.sha256", "checksums.sha256"})
    (evidence_root / "run_root.sha256").write_text(run_root_sha256 + "\n", encoding="utf-8")

    trusted_root_registry = REPOSITORY_ROOT / "docs/evidence/o6-trusted-league-roots.json"
    source_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, capture_output=True, text=True).stdout.strip()
    write_trusted_root_entry(trusted_root_registry, run_id=league_run_id, run_root_sha256=run_root_sha256, source_commit=source_commit,
                              population_id=args.population, evidence_schema="o6-public-trajectory-v1")

    print("[league] final full-chain independent verification...", file=sys.stderr, flush=True)
    final_check = _run_verifier_subprocess(evidence_root, mode="full", extra_args=["--trusted-root-registry", str(trusted_root_registry)])
    if final_check["status"] != "PASS":
        raise SystemExit(f"final full-chain verification failed: {final_check}")
```

(`sha256_hex` needs importing from `mage_ptcg.competition_intelligence.canonical` at the top of the script.) Also rename `league_run_manifest.json`/`trajectory_summary.json` writes to align with the new `run_manifest.json`/`run_summary.json` names (drop the old duplicate names entirely — the required file list in the task spec names exactly `run_manifest.json`/`run_summary.json`, not both old and new).

- [ ] **Step 4: Run and verify pass**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_run_o6_team_league.py -v`
Expected: all PASS. Debug loop: if a test fails, check whether it's a fixture mismatch (adjust the test) or a real orchestration bug (fix the script) — do not loosen the test's assertions to match a bug.

- [ ] **Step 5: Commit**

```bash
git add scripts/run_o6_team_league.py tests/test_run_o6_team_league.py src/mage_ptcg/opponents/league_runtime.py tests/opponents/test_league_runtime.py
git commit -m "$(cat <<'EOF'
feat(o6): anchor complete league evidence integrity chain

- run_o6_team_league.py が run_manifest/run_summary/run_root/trusted-root を生成
- 最終ゲートを full-chain 独立検証(--mode full)に接続
- play_game は canonical_steps と public_trajectory_events の両方を返す
EOF
)"
```

---

## Task 11: Tamper test suite (temporary-copy based, covers every layer in §12)

**Files:**
- Create: `tests/test_o6_integrity_tamper.py`

**Interfaces:** exercises `independent_trajectory_verifier.verify_run_chain` (and `verify_game`) against a small synthetic evidence tree built with `public_trajectory_evidence.persist_game_evidence` + `league_integrity_chain` helpers — no dependency on a real league run, so this suite runs fast and standalone.

- [ ] **Step 1: Write the tests**

```python
# tests/test_o6_integrity_tamper.py
"""O6-AUD-002-INTEGRITY-001 closure: every layer of the evidence tree must be tamper-evident.

Builds one small synthetic run (1 game) with the real writer + integrity-chain
helpers, registers a trusted root, then mutates exactly one file/value per
test and asserts the independent verifier's full-chain mode fails. This is
the direct regression coverage for the re-audit's tamper_test_results.json
gap (manifest/hashes/summary tamper was previously invisible to the verifier).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"


def _build_run(tmp_path: Path) -> tuple[Path, Path, str]:
    sys.path.insert(0, str(SRC_ROOT))
    from mage_ptcg.competition_intelligence.canonical import sha256_hex
    from mage_ptcg.opponents.league_integrity_chain import build_run_manifest, compute_run_root_sha256, write_trusted_root_entry
    from mage_ptcg.opponents.public_trajectory_evidence import compute_checksums_file, persist_game_evidence, write_immutable_json

    def player():
        return {"active": [None], "asleep": False, "bench": [None] * 5, "benchMax": 5, "burned": False, "confused": False,
                "deckCount": 52, "discard": [], "hand": [{"id": 1}], "handCount": 1, "paralyzed": False, "poisoned": False, "prize": [{"id": 9}] * 6}

    def obs(your_index, result=None):
        current = {"yourIndex": your_index, "players": [player(), player()], "energyAttached": False, "retreated": False,
                   "stadium": None, "stadiumPlayed": False, "supporterPlayed": False}
        if result is not None:
            current["result"] = result
        select = {"type": 0, "options": [{"type": 14, "fields": {"index": 0}}]}
        return {"current": current, "logs": [], "search_begin_input": "t", "select": select, "step": 1}

    canonical_steps = [
        [{"observation": obs(0), "action": None, "status": "ACTIVE"}, {"observation": obs(1), "action": None, "status": "ACTIVE"}],
        [{"observation": obs(0, result=0), "action": [0], "status": "DONE"}, {"observation": obs(1, result=0), "action": None, "status": "DONE"}],
    ]
    evidence_root = tmp_path / "evidence"
    game_dir_id = "pairA__match0"
    persist_game_evidence(evidence_root, game_dir_id, canonical_steps=canonical_steps,
                           runtime_digests={"initial_observation_digest": "i", "action_trace_digest": "a", "terminal_observation_digest": "t", "complete_trajectory_digest": "c"},
                           metadata={"game_id": "pairA#0"})
    game_dir = evidence_root / "games" / game_dir_id
    completed = subprocess.run([sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json", "--mode", "trajectory"],
                                capture_output=True, text=True, cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    trajectory_result = json.loads(completed.stdout)
    independent_digests = trajectory_result["per_game"][game_dir_id]["independent_digests"]
    (game_dir / "independent_digest.txt").write_text(json.dumps(independent_digests, sort_keys=True) + "\n", encoding="utf-8")
    hashes = json.loads((game_dir / "hashes.json").read_text(encoding="utf-8"))
    hashes["files"]["independent_digest.txt"] = sha256_hex((game_dir / "independent_digest.txt").read_bytes())
    (game_dir / "hashes.json").write_text(json.dumps(hashes, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    run_summary_bytes = json.dumps({"pairs": {}}, sort_keys=True).encode() + b"\n"
    (evidence_root / "run_summary.json").write_bytes(run_summary_bytes)
    run_manifest = build_run_manifest(run_id="tamper-test-run-v1", sorted_game_ids=[game_dir_id],
                                       game_manifest_hashes={game_dir_id: sha256_hex((game_dir / "trajectory_manifest.json").read_bytes())},
                                       summary_hash=sha256_hex(run_summary_bytes), participant_ids=["a", "b"], population_id="pop-1",
                                       team_bundle_hashes={}, ruleset_version="v1", cabt_version="v1", evidence_format_version="v1")
    write_immutable_json(evidence_root / "run_manifest.json", run_manifest)
    compute_checksums_file(evidence_root, evidence_root / "checksums.sha256")
    root_hash = compute_run_root_sha256(evidence_root, exclude={"run_root.sha256", "checksums.sha256"})
    (evidence_root / "run_root.sha256").write_text(root_hash + "\n", encoding="utf-8")
    registry = tmp_path / "trusted_roots.json"
    write_trusted_root_entry(registry, run_id="tamper-test-run-v1", run_root_sha256=root_hash, source_commit="deadbeef", population_id="pop-1", evidence_schema="o6-public-trajectory-v1")
    return evidence_root, registry, game_dir_id


def _verify_full(evidence_root: Path, registry: Path) -> dict:
    completed = subprocess.run([sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json", "--mode", "full", "--trusted-root-registry", str(registry)],
                                capture_output=True, text=True, cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    return json.loads(completed.stdout)


def test_untampered_run_passes(tmp_path):
    evidence_root, registry, _ = _build_run(tmp_path)
    assert _verify_full(evidence_root, registry)["status"] == "PASS"


@pytest.mark.parametrize("mutate", [
    "trajectory_byte", "runtime_digest", "independent_digest", "hashes_json", "trajectory_manifest",
    "run_summary", "run_manifest", "run_root", "trusted_registry",
])
def test_single_layer_tamper_detected(tmp_path, mutate):
    evidence_root, registry, game_dir_id = _build_run(tmp_path)
    game_dir = evidence_root / "games" / game_dir_id
    if mutate == "trajectory_byte":
        path = game_dir / "public_projection_trajectory.jsonl.gz"
        data = bytearray(path.read_bytes()); data[-1] ^= 0xFF; path.write_bytes(bytes(data))
    elif mutate == "runtime_digest":
        (game_dir / "runtime_digest.txt").write_text('{"initial_observation_digest": "tampered"}\n', encoding="utf-8")
    elif mutate == "independent_digest":
        (game_dir / "independent_digest.txt").write_text('{"initial_observation_digest": "tampered"}\n', encoding="utf-8")
    elif mutate == "hashes_json":
        h = json.loads((game_dir / "hashes.json").read_text()); h["files"]["trajectory_manifest.json"] = "0" * 64
        (game_dir / "hashes.json").write_text(json.dumps(h), encoding="utf-8")
    elif mutate == "trajectory_manifest":
        m = json.loads((game_dir / "trajectory_manifest.json").read_text()); m["event_count"] = 999
        (game_dir / "trajectory_manifest.json").write_text(json.dumps(m), encoding="utf-8")
    elif mutate == "run_summary":
        (evidence_root / "run_summary.json").write_text('{"tampered": true}\n', encoding="utf-8")
    elif mutate == "run_manifest":
        rm = json.loads((evidence_root / "run_manifest.json").read_text()); rm["population_id"] = "tampered"
        (evidence_root / "run_manifest.json").write_text(json.dumps(rm), encoding="utf-8")
    elif mutate == "run_root":
        (evidence_root / "run_root.sha256").write_text("0" * 64 + "\n", encoding="utf-8")
    elif mutate == "trusted_registry":
        reg = json.loads(registry.read_text()); reg["trusted_roots"][0]["run_root_sha256"] = "0" * 64
        registry.write_text(json.dumps(reg), encoding="utf-8")

    result = _verify_full(evidence_root, registry)
    assert result["status"] != "PASS", f"tamper not detected for {mutate}: {result}"


def test_game_insertion_detected(tmp_path):
    evidence_root, registry, game_dir_id = _build_run(tmp_path)
    extra = evidence_root / "games" / "extra__match0"
    shutil.copytree(evidence_root / "games" / game_dir_id, extra)
    assert _verify_full(evidence_root, registry)["status"] != "PASS"


def test_game_deletion_detected(tmp_path):
    evidence_root, registry, game_dir_id = _build_run(tmp_path)
    shutil.rmtree(evidence_root / "games" / game_dir_id)
    assert _verify_full(evidence_root, registry)["status"] != "PASS"


def test_wrong_trusted_anchor_rejected(tmp_path):
    evidence_root, registry, _ = _build_run(tmp_path)
    reg = json.loads(registry.read_text())
    reg["trusted_roots"][0]["run_id"] = "different-run-id"
    registry.write_text(json.dumps(reg), encoding="utf-8")
    assert _verify_full(evidence_root, registry)["status"] == "UNANCHORED_EVIDENCE"


def test_missing_trusted_anchor_rejected(tmp_path):
    evidence_root, registry, _ = _build_run(tmp_path)
    completed = subprocess.run([sys.executable, "-m", "mage_ptcg.opponents.independent_trajectory_verifier", "--evidence", str(evidence_root), "--json", "--mode", "full"],
                                capture_output=True, text=True, cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(SRC_ROOT), "PYTHONDONTWRITEBYTECODE": "1"})
    assert json.loads(completed.stdout)["status"] == "UNANCHORED_EVIDENCE"


def test_same_run_id_different_content_detected(tmp_path):
    """Two separately-built runs sharing a run_id but different game content: registering the
    second run's root under the first run's trusted entry must not let it pass as the first."""
    evidence_root_a, registry, _ = _build_run(tmp_path / "a")
    evidence_root_b, _, _ = _build_run(tmp_path / "b")
    # swap b's games into a's directory while keeping a's manifest/registry (simulates a same-id substitution)
    shutil.rmtree(evidence_root_a / "games")
    shutil.copytree(evidence_root_b / "games", evidence_root_a / "games")
    assert _verify_full(evidence_root_a, registry)["status"] != "PASS"
```

- [ ] **Step 2: Run, verify they pass** (this suite is written test-last relative to Task 9's implementation since it's pure integration coverage over already-implemented pieces — still run it to catch integration bugs, treat any failure as a real bug to fix, not a test to weaken)

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_o6_integrity_tamper.py -v`
Expected: all PASS. If `test_single_layer_tamper_detected[hashes_json]` or similar fails, it means Task 9's `verify_run_chain` has a real gap — fix `verify_run_chain`/`verify_game_integrity`, not the test.

- [ ] **Step 3: Commit**

```bash
git add tests/test_o6_integrity_tamper.py
git commit -m "$(cat <<'EOF'
test(o6): cover public trajectory privacy and tamper boundaries

- 全 integrity chain 層（trajectory/digest/hashes/manifest/summary/root/anchor）の
  改ざんが full-chain 独立検証で検出されることを固定する
EOF
)"
```

---

## Task 12: Legacy run tombstone + `.gitignore`

**Files:**
- Create: `docs/evidence/o6-opponent-intelligence-v3-TOMBSTONE.json`
- Modify: `.gitignore`
- Git-untrack: `docs/evidence/o6-opponent-intelligence-v3/` (kept on local disk)

**Interfaces:** none (documentation/bookkeeping task), but must run before Task 14's docs update references the tombstone path.

- [ ] **Step 1: Capture pre-invalidation facts (before untracking)**

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1
FILE_COUNT=$(find docs/evidence/o6-opponent-intelligence-v3 -type f | wc -l)
ROOT_HASH=$(sha256sum docs/evidence/o6-opponent-intelligence-v3/checksums.sha256 | cut -d' ' -f1)
echo "file_count=$FILE_COUNT root_hash=$ROOT_HASH"
```

Record the printed values for Step 2.

- [ ] **Step 2: Write the tombstone**

```json
{
  "run_id": "o6-team-league-f4c8f9b87ae6-raw-v1",
  "status": "INVALID_PRIVATE_EVIDENCE",
  "status_flags": ["INVALID_PRIVATE_EVIDENCE", "PRIVACY_VALIDATION_FAILED", "NOT_EXPORTABLE", "NOT_MERGE_EVIDENCE"],
  "invalidation_reason": "O6-AUD-002 targeted re-audit found raw trajectory files retained logs, search_begin_input, full hand/prize contents (PRIVACY-001), the field gate accepted unknown/renamed fields fail-open (PRIVACY-002), and manifest/hashes/summary tampering was undetectable by the independent verifier (INTEGRITY-001).",
  "invalidated_finding_ids": ["O6-AUD-002-PRIVACY-001", "O6-AUD-002-PRIVACY-002", "O6-AUD-002-INTEGRITY-001"],
  "file_count": "<FILL IN FROM STEP 1>",
  "root_hash": "<FILL IN FROM STEP 1: sha256 of the run's own checksums.sha256, computed before untracking>",
  "created_commit": "fd08e04",
  "raw_payload_location": "intentionally omitted (restricted local quarantine on the audited worktree's disk, never git-tracked again)",
  "superseded_by": "o6-team-league-<population_hash[:12]>-public-v2 (see docs/evidence/o6-opponent-intelligence-v4/, populated in Task 15)"
}
```

- [ ] **Step 3: Untrack the legacy evidence directory (keep on local disk)**

```bash
git rm -r --cached docs/evidence/o6-opponent-intelligence-v3
```

Verify the files are still present on disk (quarantine, not deletion):

```bash
test -d docs/evidence/o6-opponent-intelligence-v3 && echo "quarantine intact"
```

- [ ] **Step 4: Update `.gitignore`**

Add a line:

```
docs/evidence/o6-opponent-intelligence-v3/
```

- [ ] **Step 5: Commit**

```bash
git add docs/evidence/o6-opponent-intelligence-v3-TOMBSTONE.json .gitignore
git add -u docs/evidence/o6-opponent-intelligence-v3
git commit -m "$(cat <<'EOF'
fix(o6): invalidate tainted raw league evidence as tombstone-only

- o6-opponent-intelligence-v3 を git 追跡から除外し、ローカル限定隔離へ変更
- INVALID_PRIVATE_EVIDENCE / NOT_MERGE_EVIDENCE のtombstoneのみを正典に残す
- raw payload は共有 artifact／docs／checksums の対象から除外する
EOF
)"
```

Confirm with `git status --short` that no sensitive file under `o6-opponent-intelligence-v3/` remains staged or tracked (`git ls-files docs/evidence/o6-opponent-intelligence-v3` should be empty).

---

## Task 13: Full pre-run test suite pass

**Files:** none (verification-only checkpoint before spending an actual league run).

- [ ] **Step 1: Run the full opponents suite**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents`
Expected: all PASS (this now includes every task's tests above). Fix any regression before proceeding — do not run the real league with a red test suite.

- [ ] **Step 2: Run the O5/actual-league/tamper suites**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence tests/test_run_o5_benchmark_cli.py tests/test_actual_league_runner.py tests/test_actual_league_cli.py tests/test_run_o6_team_league.py tests/test_o6_integrity_tamper.py`
Expected: all PASS

- [ ] **Step 3: No commit this task** (pure checkpoint).

---

## Task 14: Execute the new 60-game public League run

**Files:** produces `docs/evidence/o6-opponent-intelligence-v4/` (new, git-tracked).

- [ ] **Step 1: Confirm the artifact store / population inputs the old run used are still valid**

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1
cat docs/evidence/o6-opponent-intelligence-v3-TOMBSTONE.json | python3 -c "import json,sys; print(json.load(sys.stdin)['superseded_by'])"
grep -n "artifact-store\|--population" scripts/run_o6_team_league.py | head -5
```

Find the exact `--artifact-store`/`--population`/`--cache-dir` values the *previous* run used (check `docs/status/handoff.md`'s O6 Phase C section, or any wrapper/invocation script referenced there — do not invent paths).

- [ ] **Step 2: Run the league**

```bash
mkdir -p /tmp/o6-public-v2-output /tmp/o6-public-v2-cache
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_o6_team_league.py \
  --artifact-store <same as previous run — from Step 1> \
  --population <same population id as previous run> \
  --cache-dir /tmp/o6-public-v2-cache \
  --output-dir /tmp/o6-public-v2-output \
  --evidence-root docs/evidence/o6-opponent-intelligence-v4 \
  --games-per-pair 10 --base-seed 91000
```

(New `--base-seed` distinct from the invalidated run's `83000`/original `71000`, so this is unambiguously a fresh execution, not a replay.)

- [ ] **Step 2: Verify exit code 0 and inspect the printed summary JSON** for `pairs_played == 6`, `independently_verified_count == 60`.

- [ ] **Step 3: Verify raw-observation-file-count is zero and public-projection-file-count is 60**

```bash
find docs/evidence/o6-opponent-intelligence-v4/games -name "public_projection_trajectory.jsonl.gz" | wc -l   # expect 60
find docs/evidence/o6-opponent-intelligence-v4/games -iname "*raw*"                                            # expect empty
```

- [ ] **Step 4: Run the standalone full-chain re-verification exactly as an independent auditor would**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m mage_ptcg.opponents.independent_trajectory_verifier \
  --evidence docs/evidence/o6-opponent-intelligence-v4 --json --mode full \
  --trusted-root-registry docs/evidence/o6-trusted-league-roots.json | python3 -m json.tool | head -40
```

Expected: `"status": "PASS"`, `"root_hash_match": true`, all 60 games' `hashes_valid: true`.

- [ ] **Step 5: No commit yet** — Task 15 commits the evidence + docs together.

---

## Task 15: Commit evidence, statistics, and docs

**Files:**
- New: `docs/evidence/o6-opponent-intelligence-v4/` (evidence tree)
- Modify: `docs/status/current_status.md`, `docs/status/handoff.md`, `docs/evidence/o6-opponent-intelligence-v1.md` (Phase C section)

- [ ] **Step 1: Update docs' Phase C sections**

Replace any statement asserting the old run (`o6-team-league-f4c8f9b87ae6-raw-v1`) is validly public/independently-verified with: legacy run invalidated (link to the TOMBSTONE file and findings), new run `o6-team-league-<hash>-public-v2` (fill in the actual `population_identity_hash[:12]` printed by Task 14) is the current Evidence, with actual `raw_executions`/`unique_complete_trajectories`/`effective_independent_sample_size_total`/per-pair win counts pulled from the real `run_summary.json` — do not reuse the old 9–1 number; state whatever this run's `ozawa-crustle-rule` vs `rule-agent-v0` result actually is.

- [ ] **Step 2: Validate docs**

Run: `.venv/bin/python scripts/docs/validate_docs.py`
Expected: exit 0

- [ ] **Step 3: Commit**

```bash
git add docs/evidence/o6-opponent-intelligence-v4 docs/status/current_status.md docs/status/handoff.md docs/evidence/o6-opponent-intelligence-v1.md
git commit -m "$(cat <<'EOF'
docs(o6): record privacy-safe independently anchored evidence

- o6-opponent-intelligence-v4（public projection のみ、60 games）を追加
- Phase C の記述を無効化済み legacy run と新 run の実結果へ更新
EOF
)"
```

---

## Task 16: Final full verification pass + worktree cleanliness

**Files:** none.

- [ ] **Step 1: Run everything the task spec's §18 requires**

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-o6-opponents-v1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/opponents
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q tests/competition_intelligence tests/test_run_o5_benchmark_cli.py tests/test_actual_league_runner.py tests/test_actual_league_cli.py
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q
.venv/bin/python scripts/docs/validate_docs.py
git diff --check
```

Expected: all exit 0.

- [ ] **Step 2: `git status --short`** — expect clean (everything from this plan committed; any unrelated pre-existing worktree files noted in the final report, not silently touched).

- [ ] **Step 3: No commit** (pure verification).

---

## Self-Review Notes

- **Spec coverage:** §0 (constraints) → Global Constraints + every task's scope; §1 (legacy evidence) → Task 12; §2–§7 (naming/schema/exclusions) → Tasks 1–2; §8 (runtime writer) → Task 3; §9 (independent verifier) → Tasks 5, 9; §10–§11 (integrity chain/trust anchor) → Tasks 8–9; §12 (tamper coverage) → Task 11; §13 (schema_version in digest) → Task 6; §14–§15 (new run + statistics) → Task 14–15; §16 (required tests) → distributed across Tasks 2–11 (cross-check: privacy tests in Tasks 2/4/5, action tests in Task 2, schema tests in Tasks 1/2/9, integrity tests in Task 11, independence tests in Tasks 5/9); §17 (non-regression) → Task 16 + explicit non-touch notes on `NativeAgentWorker`/Champion/population identity throughout; §18 → Task 16; §19 → each task's commit step uses the exact suggested subject lines; §20 → final report step after this plan completes (not part of the plan itself, produced by the executing agent directly).
- **Type consistency check:** `persist_game_evidence`'s signature (`canonical_steps=`, `runtime_digests=`, `metadata=`) is unchanged from the old module across Tasks 3, 7, 10 — confirmed consistent. `build_public_trajectory_events` return shape (Task 2) is exactly what Task 3's writer, Task 5's verifier fixtures, and Task 6's digest function all consume — confirmed consistent (`schema_version`/`event_type`/`step_index`/`seat_direction`/`public_payload` used identically everywhere). `play_game`'s return dict carries **both** `canonical_steps` and `public_trajectory_events` after the Task 10 correction to Task 7 — flagged inline in Task 10 to avoid a silent drift between tasks.
- **Known limitation carried into the final report (§26 of the task's requested report):** `action_projection` intentionally omits `area`/`in_play_area`/`index`/`in_play_index`/`energy_index` because their zone semantics (hand vs. board vs. deck) are marked unverified in `docs/evidence/cabt-observation-schema.md` itself — this is a deliberate conservative gap, not an oversight, and should be reported as a remaining limitation rather than silently closed.
