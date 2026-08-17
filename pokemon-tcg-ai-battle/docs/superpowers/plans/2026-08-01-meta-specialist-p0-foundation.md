# Meta Specialist P0 Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the isolated, testable P0 foundation for one fixed-deck/one-policy Kaggle specialist: official runtime contracts, immutable deck/lineage qualification, complete multi-select actions, a CPU-only CABT runtime boundary, and a deterministic specialist submission bundle.

**Architecture:** Add a new `mage_ptcg.meta_specialist` package without modifying the dirty R2D3/continuous-league implementation. Reuse the stable public `DecisionState`/`ActionKey` projection and content-addressed JSON helpers through narrow adapters; introduce a new complete-action schema because existing R2D3 traces discard multi-select decisions. Keep the specialist submission builder separate from the deliberately fixed Rule-v0 and Student-v0 builders.

**Tech Stack:** Python 3.11+, standard library dataclasses/argparse/tarfile/hashlib/json, existing `mage_ptcg.decision_state` and `mage_ptcg.continuous_league.contracts`, pytest, optional `kaggle_environments` only in marked integration tests.

## Global Constraints

- Canonical package path is `src/mage_ptcg/meta_specialist/`; do not create a top-level `rl/` package.
- One submission contains exactly one top-level `main.py`, exactly one top-level `deck.csv`, and one immutable policy identity.
- Archive size limit is exactly 202,400 KiB (207,257,600 bytes).
- Submission qualification is CPU-only under 2 vCPU, 12,815,744 KiB RAM, 12,388,608 KiB agent disk, with the bundle rooted at `/kaggle_simulations/agent/`; GPU and network are not required.
- Competition deadline is `2026-08-16T23:59:00Z` (`2026-08-17T08:59:00+09:00`); default safe upload target is `2026-08-15T23:59:00Z`.
- Kaggle submission is never automated. Lifecycle code only records manually supplied evidence.
- Gold/Silver/Bronze and live rating are never runtime model selectors.
- Candidate classes are exactly `checkpointed_specialist` and `static_rule_bundle`.
- A checkpointed specialist receives `policy_lineage_id` only after `DeckLockDecision`. Changing the deck creates a new lineage root.
- One complete selection is one RL/environment action. Multi-select decisions are not converted to independent top-k actions and are not silently dropped.
- Stable action identity and selection are invariant to CABT option enumeration order; final emitted integers are current CABT option indices.
- Raw observations, opponent private state, future RNG, and raw own-hand identities are absent from persisted decision traces.
- Do not import or patch `policy_learning.r2d3.candidate`, `continuous_league.collector`, `optimization.deck_specialist`, or the fixed Rule-v0/Student-v0 builders in this plan.
- Work in an isolated worktree. Preserve the user-owned dirty checkout at branch `feature/belief-guided-search`, HEAD `ed9d82bf718d230afbca0d6c406aa73a8c7cdc05`, status SHA-256 `cfd388608b2417dab4ca1ad3ae77a864bead84de92fb5693f6561a26eb0403a6`.
- Do not delete o6 or other user artifacts in this plan.
- Do not commit, push, open a PR, or submit to Kaggle; the repository requires separate explicit user authorization.
- Follow strict TDD: add one behavior test, run it and observe the expected failure, add the minimum implementation, run it green, then refactor.

---

## File Structure

- `src/mage_ptcg/meta_specialist/contracts.py` — official ladder/runtime constants and content-addressed manifest publication.
- `src/mage_ptcg/meta_specialist/lifecycle.py` — manual submission lifecycle state machine.
- `src/mage_ptcg/meta_specialist/decks.py` — exact deck parsing, identity, provenance qualification, duplicate detection, deck lock, lineage rules.
- `src/mage_ptcg/meta_specialist/actions.py` — complete-action enumeration, canonical autoregressive probability, greedy decode, and complete-action Q argmax.
- `src/mage_ptcg/meta_specialist/runtime.py` — CABT registration/decision boundary and privacy-safe complete-action trace.
- `src/mage_ptcg/meta_specialist/package.py` — deterministic safe specialist `.tar.gz` builder and verifier.
- `src/mage_ptcg/meta_specialist/cli.py` / `__main__.py` — JSON-output local commands; no network calls or submissions.
- `configs/meta_specialist/archetypes_v1.json` — five-lane registry and fixed P0 priority/replacement order.
- `tests/meta_specialist/` — focused unit/integration tests for this package.
- `docs/runbooks/meta-specialist-p0-foundation.md` — exact local commands and manual Kaggle lifecycle procedure.

### Task 1: Official Runtime and Manual Lifecycle Contracts

**Files:**
- Create: `src/mage_ptcg/meta_specialist/__init__.py`
- Create: `src/mage_ptcg/meta_specialist/contracts.py`
- Create: `src/mage_ptcg/meta_specialist/lifecycle.py`
- Create: `tests/meta_specialist/test_meta_specialist_contracts.py`
- Create: `tests/meta_specialist/test_lifecycle.py`

**Interfaces:**
- Consumes: `mage_ptcg.continuous_league.contracts.publish_content_addressed_json` and `require_sha256`.
- Produces: `ladder_mechanics_payload(checked_at_utc: str) -> dict[str, object]`, `publish_ladder_mechanics(root: Path, *, checked_at_utc: str) -> tuple[str, Path]`, `SubmissionLifecycleRecord`, and `advance_lifecycle(record, target, evidence)`.

- [ ] **Step 1: Write the failing official-contract test**

```python
from mage_ptcg.meta_specialist.contracts import ladder_mechanics_payload

def test_ladder_mechanics_v1_has_exact_official_limits() -> None:
    payload = ladder_mechanics_payload(checked_at_utc="2026-08-01T00:00:00Z")
    assert payload["schema_version"] == "ladder-mechanics-v1"
    assert payload["archive_format"] == ".tar.gz"
    assert payload["required_top_level_files"] == ["main.py", "deck.csv"]
    assert payload["bundle_size_limit_kib"] == 202_400
    assert payload["bundle_size_limit_bytes"] == 207_257_600
    assert payload["max_daily_submissions"] == 5
    assert payload["active_submission_limit"] == 2
    assert payload["final_selection_limit"] == 2
    assert payload["initial_mu"] == 600.0
    assert payload["cpu_limit_percent"] == 200
    assert payload["ram_limit_kib"] == 12_815_744
    assert payload["agent_disk_limit_kib"] == 12_388_608
    assert payload["agent_root"] == "/kaggle_simulations/agent/"
    assert payload["gpu_required"] is False
    assert payload["network_required"] is False
    assert payload["deadline_utc"] == "2026-08-16T23:59:00Z"
    assert payload["deadline_jst"] == "2026-08-17T08:59:00+09:00"
    assert payload["target_safe_upload_at_utc"] == "2026-08-15T23:59:00Z"
```

- [ ] **Step 2: Run the test and verify the missing-package failure**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_meta_specialist_contracts.py::test_ladder_mechanics_v1_has_exact_official_limits -q`
Expected: FAIL during collection with `ModuleNotFoundError: No module named 'mage_ptcg.meta_specialist'`.

- [ ] **Step 3: Add the minimum official payload and publisher**

```python
# contracts.py
from hashlib import sha256
from pathlib import Path
from mage_ptcg.continuous_league.contracts import file_sha256
from mage_ptcg.knowledge.model import deck_identity_from_card_ids
from typing import Any

from mage_ptcg.continuous_league.contracts import (
    publish_content_addressed_json,
)

BUNDLE_SIZE_LIMIT_KIB = 202_400
BUNDLE_SIZE_LIMIT_BYTES = BUNDLE_SIZE_LIMIT_KIB * 1024

def ladder_mechanics_payload(*, checked_at_utc: str) -> dict[str, object]:
    if not checked_at_utc.endswith("Z"):
        raise ValueError("checked_at_utc must be an explicit UTC timestamp ending in Z")
    return {
        "schema_version": "ladder-mechanics-v1",
        "checked_at_utc": checked_at_utc,
        "official_source": "https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/overview/description",
        "archive_format": ".tar.gz",
        "required_top_level_files": ["main.py", "deck.csv"],
        "bundle_size_limit_kib": BUNDLE_SIZE_LIMIT_KIB,
        "bundle_size_limit_bytes": BUNDLE_SIZE_LIMIT_BYTES,
        "max_daily_submissions": 5,
        "active_submission_limit": 2,
        "final_selection_limit": 2,
        "initial_mu": 600.0,
        "cpu_limit_percent": 200,
        "ram_limit_kib": 12_815_744,
        "agent_disk_limit_kib": 12_388_608,
        "agent_root": "/kaggle_simulations/agent/",
        "gpu_required": False,
        "network_required": False,
        "deadline_utc": "2026-08-16T23:59:00Z",
        "deadline_jst": "2026-08-17T08:59:00+09:00",
        "target_safe_upload_at_utc": "2026-08-15T23:59:00Z",
    }

def publish_ladder_mechanics(
    root: Path, *, checked_at_utc: str
) -> tuple[str, Path]:
    return publish_content_addressed_json(
        root,
        domain="meta-specialist-ladder-mechanics-v1",
        payload=ladder_mechanics_payload(checked_at_utc=checked_at_utc),
        id_field="ladder_mechanics_id",
    )
```

`__init__.py` exports no runtime-heavy modules; it contains only `"""Meta-driven fixed-deck specialist contracts and runtime."""`.

- [ ] **Step 4: Run the official-contract tests green**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_meta_specialist_contracts.py -q`
Expected: PASS, including a second test that publishes the same payload twice to the same content-addressed path and rejects a same-ID/different-content collision through the existing contract helper.

- [ ] **Step 5: Write failing lifecycle transition tests**

```python
import pytest
from mage_ptcg.meta_specialist.lifecycle import (
    LifecycleError,
    SubmissionLifecycleRecord,
    SubmissionState,
    advance_lifecycle,
)

def test_manual_submission_lifecycle_requires_external_evidence() -> None:
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="a" * 64,
        active_slot_intent="primary",
    )
    with pytest.raises(LifecycleError, match="submission_id"):
        advance_lifecycle(draft, SubmissionState.SUBMITTED, {})

    submitted = advance_lifecycle(
        draft,
        SubmissionState.SUBMITTED,
        {
            "submission_id": "54812345",
            "submitted_at_utc": "2026-08-10T01:00:00Z",
            "daily_slot_number": 1,
            "recorded_by": "human",
        },
    )
    with pytest.raises(LifecycleError, match="validation_log"):
        advance_lifecycle(submitted, SubmissionState.VALIDATION_PASSED, {})

def test_local_code_cannot_skip_to_active_or_final_selected() -> None:
    draft = SubmissionLifecycleRecord.draft(
        bundle_sha256="b" * 64,
        active_slot_intent="backup",
    )
    with pytest.raises(LifecycleError, match="invalid lifecycle transition"):
        advance_lifecycle(
            draft,
            SubmissionState.ACTIVE_CONFIRMED,
            {"active_checked_at_utc": "2026-08-10T02:00:00Z"},
        )
```

- [ ] **Step 6: Run lifecycle tests red, implement the state machine, and run green**

The implementation uses:

```python
class SubmissionState(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    VALIDATION_PASSED = "validation_passed"
    VALIDATION_FAILED = "validation_failed"
    ACTIVE_CONFIRMED = "active_confirmed"
    FINAL_SELECTED = "final_selected"

_ALLOWED = {
    SubmissionState.DRAFT: {SubmissionState.SUBMITTED},
    SubmissionState.SUBMITTED: {
        SubmissionState.VALIDATION_PASSED,
        SubmissionState.VALIDATION_FAILED,
    },
    SubmissionState.VALIDATION_PASSED: {SubmissionState.ACTIVE_CONFIRMED},
    SubmissionState.ACTIVE_CONFIRMED: {SubmissionState.FINAL_SELECTED},
    SubmissionState.VALIDATION_FAILED: set(),
    SubmissionState.FINAL_SELECTED: set(),
}
```

Evidence fields are exact: `SUBMITTED` requires `submission_id`, `submitted_at_utc`, `daily_slot_number` in 1..5, and `recorded_by`; `VALIDATION_PASSED` requires nonempty `validation_log` and `validated_at_utc`; `VALIDATION_FAILED` requires nonempty `validation_log` and `validated_at_utc`; `ACTIVE_CONFIRMED` requires `active_checked_at_utc` and `active_submission_ids` of length at most 2 containing this submission; `FINAL_SELECTED` requires `final_selected_at_utc` and `final_submission_ids` of length at most 2 containing this submission.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_meta_specialist_contracts.py tests/meta_specialist/test_lifecycle.py -q`
Expected: PASS.

- [ ] **Step 7: Record task evidence without committing**

Run: `git status --short -- src/mage_ptcg/meta_specialist tests/meta_specialist` and append the RED/GREEN commands and outputs to the SDD task report. Do not run `git add` or `git commit`.

### Task 2: Five-Lane Deck Registry, Qualification, Deck Lock, and Lineage

**Files:**
- Create: `configs/meta_specialist/archetypes_v1.json`
- Create: `src/mage_ptcg/meta_specialist/decks.py`
- Create: `tests/meta_specialist/test_decks.py`
- Create: `tests/meta_specialist/test_deck_lock.py`

**Interfaces:**
- Consumes: `main.validate_deck`, `main.read_deck_csv`, `mage_ptcg.knowledge.loader.read_deck_card_ids`, `mage_ptcg.knowledge.model.deck_identity_from_card_ids`, `file_sha256`, and `content_id`.
- Produces: `load_archetype_registry(path) -> ArchetypeRegistry`, `qualify_deck_asset(asset, archetype, *, known_card_ids, cabt_legality) -> QualifiedDeckAsset`, `reject_duplicate_seed_decks(assets)`, `create_deck_lock(...) -> DeckLockDecision`, and `require_lineage_deck(lock, deck_identity)`.

- [ ] **Step 1: Add the failing five-lane registry test**

```python
from pathlib import Path
from mage_ptcg.meta_specialist.decks import load_archetype_registry

ROOT = Path(__file__).resolve().parents[2]

def test_registry_has_exact_five_lanes_and_priority_order() -> None:
    registry = load_archetype_registry(
        ROOT / "configs/meta_specialist/archetypes_v1.json"
    )
    assert tuple(registry.archetypes) == (
        "alakazam",
        "grimmsnarl_froslass_munkidori",
        "crustle_mega_kangaskhan",
        "rocket_mewtwo_spidops",
        "archaludon",
    )
    assert registry.primary_order == (
        "grimmsnarl_froslass_munkidori",
        "alakazam",
        "crustle_mega_kangaskhan",
    )
    assert registry.replacement_order == (
        "rocket_mewtwo_spidops",
        "archaludon",
    )
    assert registry.archetypes["alakazam"].core_card_ids == (741, 742, 743)
    assert registry.archetypes["archaludon"].core_card_ids == (169, 190)
```

- [ ] **Step 2: Run red and add the exact registry JSON**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_decks.py::test_registry_has_exact_five_lanes_and_priority_order -q`
Expected: FAIL because `decks.py` and the config do not exist.

The JSON has `schema_version = "meta-specialist-archetypes-v1"`, the exact primary/replacement arrays above, and five records in runtime-ID order. Exact canonical (ascending) core arrays are `[741,742,743]`, `[104,112,646,647,648,860]`, `[344,345,756]`, `[400,401,431]`, and `[169,190]`. Only Grimmsnarl has alias `grimmsnarl_froslass`. Every record has `candidate_status = "registered_unqualified"`.

- [ ] **Step 3: Implement strict config parsing and run the registry test green**

`ArchetypeSpec` and `ArchetypeRegistry` are frozen dataclasses. Reject unknown top-level/record keys, duplicate runtime IDs or aliases, bool-as-int card IDs, unsorted duplicate core IDs, overlap between priority lists, and any priority ID absent from `archetypes`.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_decks.py::test_registry_has_exact_five_lanes_and_priority_order -q`
Expected: PASS.

- [ ] **Step 4: Add failing deck identity and fail-closed qualification tests**

```python
from dataclasses import replace
from pathlib import Path
import pytest

from mage_ptcg.meta_specialist.decks import (
    DeckAssetInput,
    DeckQualificationError,
    qualify_deck_asset,
    reject_duplicate_seed_decks,
)

def test_qualification_records_multiset_and_exact_file_identity(
    tmp_path: Path, alakazam_spec
) -> None:
    cards = [741, 742, 743] + list(range(1000, 1057))
    deck = tmp_path / "deck.csv"
    deck.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    asset = DeckAssetInput.from_path(
        asset_id="alakazam-seed-a",
        archetype_id="alakazam",
        path=deck,
        source_ref="origin/agents/nihei-alakazam:deck.csv",
        source_commit="a" * 40,
        asset_class="deck_only",
        usage_boundary="local_eval_only",
        policy_compatibility="deck_only",
        card_database_version="fixture-v1",
    )
    qualified = qualify_deck_asset(
        asset,
        alakazam_spec,
        known_card_ids=set(cards),
        cabt_legality=lambda values: (True, "fixture-cabt-pass"),
    )
    assert qualified.card_count == 60
    assert qualified.deck_identity.startswith("deck-")
    assert len(qualified.deck_file_sha256) == 64
    assert qualified.cabt_legality_status == "passed"
    assert qualified.cabt_legality_evidence == "fixture-cabt-pass"

def test_production_qualification_requires_operational_legality(
    tmp_path: Path, alakazam_asset, alakazam_spec
) -> None:
    with pytest.raises(DeckQualificationError, match="CABT legality"):
        qualify_deck_asset(
            alakazam_asset,
            alakazam_spec,
            known_card_ids=set(alakazam_asset.card_ids),
            cabt_legality=None,
        )

def test_duplicate_canonical_decks_are_one_seed(qualified_asset) -> None:
    duplicate = replace(qualified_asset, asset_id="same-bytes-new-source")
    with pytest.raises(DeckQualificationError, match="duplicate canonical deck"):
        reject_duplicate_seed_decks((qualified_asset, duplicate))
```

- [ ] **Step 5: Run the deck tests red and implement exact qualification**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_decks.py -q`
Expected: FAIL on missing deck qualification symbols.

`DeckAssetInput.from_path` reads explicit paths only; there is no default to repository-root `deck.csv`. It stores the exact bytes SHA-256 and canonical multiset identity. `qualify_deck_asset` requires exactly 60 positive non-bool integers, known-card membership, all core IDs present, immutable provenance (`source_commit` is 40 lowercase hex), allowed asset classes `deck_only`/`runnable_rule`/`checkpoint_teacher`, allowed boundaries `local_eval_only`/`teacher_only`/`bundle_allowed`, and a CABT callback returning `(True, nonempty_evidence)`.

- [ ] **Step 6: Run deck qualification green**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_decks.py -q`
Expected: PASS for valid identity and failures for 59/61 cards, bool, zero/negative/unknown card, missing core, mutable source, absent CABT evidence, and duplicate canonical decks.

- [ ] **Step 7: Add failing deck-lock/lineage tests**

```python
import pytest
from mage_ptcg.meta_specialist.decks import (
    DeckLineageError,
    create_deck_lock,
    require_lineage_deck,
)

def test_deck_lock_issues_lineage_only_after_fair_short_race() -> None:
    lock = create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=(
            "deck-" + "a" * 20,
            "deck-" + "b" * 20,
        ),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=100_000,
    )
    assert len(lock.deck_lock_id) == 64
    assert len(lock.policy_lineage_id) == 64
    require_lineage_deck(lock, "deck-" + "a" * 20)
    with pytest.raises(DeckLineageError, match="new branch"):
        require_lineage_deck(lock, "deck-" + "b" * 20)

def test_paths_and_timestamps_do_not_change_deck_lock_identity() -> None:
    first = create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=("deck-" + "a" * 20,),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=100_000,
    )
    second = create_deck_lock(
        archetype_id="alakazam",
        selected_deck_identity="deck-" + "a" * 20,
        compared_deck_identities=("deck-" + "a" * 20,),
        foundation_init_id="f" * 64,
        joint_race_schedule_id="e" * 64,
        equal_transition_budget=100_000,
    )
    assert first == second
```

- [ ] **Step 8: Implement deterministic deck lock and run all Task 2 tests green**

`create_deck_lock` sorts/deduplicates compared identities, requires the selected identity among them, requires at least one comparison entry, rejects nonpositive/bool budgets, computes `deck_lock_id = content_id("meta-specialist-deck-lock-v1", identity_payload)`, and computes `policy_lineage_id = content_id("meta-specialist-policy-lineage-v1", {"deck_lock_id": deck_lock_id})`. Absolute paths, hosts, and timestamps are not parameters.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_decks.py tests/meta_specialist/test_deck_lock.py -q`
Expected: PASS.

- [ ] **Step 9: Record task evidence without committing**

Record the focused test output and `git diff --stat -- configs/meta_specialist src/mage_ptcg/meta_specialist/decks.py tests/meta_specialist/test_decks.py tests/meta_specialist/test_deck_lock.py` in the task report.

### Task 3: Complete-Action Contract and Shuffle-Invariant Probability

**Files:**
- Create: `src/mage_ptcg/meta_specialist/actions.py`
- Create: `tests/meta_specialist/test_actions.py`
- Create: `tests/meta_specialist/test_action_properties.py`
- Modify: `src/mage_ptcg/decision_state.py`
- Create: `tests/meta_specialist/test_cabt_action_identity.py`

**Interfaces:**
- Consumes: `mage_ptcg.decision_state.DecisionState` and `LegalAction`.
- Produces: `DecisionEnvelope.from_decision_state`, `CompleteAction`, `enumerate_complete_actions`, `legal_next_tokens`, `complete_action_log_probability`, `complete_action_distribution`, `greedy_decode`, and `q_argmax`.

- [x] **Step 1: Write failing mandatory/optional enumeration tests**

```python
from mage_ptcg.meta_specialist.actions import (
    Candidate,
    DecisionEnvelope,
    enumerate_complete_actions,
)

def _envelope(minimum: int, maximum: int) -> DecisionEnvelope:
    key_a, key_b, key_c = "1" * 64, "2" * 64, "3" * 64
    return DecisionEnvelope(
        selection_type=0,
        decision_digest="d" * 64,
        action_set_digest="e" * 64,
        candidates=(
            Candidate(key_c, 2),
            Candidate(key_a, 0),
            Candidate(key_b, 1),
        ),
        min_count=minimum,
        max_count=maximum,
        order_semantics="unordered_set",
    )

def test_complete_action_enumerates_every_legal_unordered_set() -> None:
    key_a, key_b, key_c = "1" * 64, "2" * 64, "3" * 64
    actions = enumerate_complete_actions(_envelope(1, 2), limit=32)
    assert [action.keys for action in actions] == [
        (key_a,), (key_b,), (key_c,),
        (key_a, key_b), (key_a, key_c), (key_b, key_c),
    ]

def test_optional_selection_includes_empty_action_once() -> None:
    actions = enumerate_complete_actions(_envelope(0, 2), limit=32)
    assert actions[0].keys == ()
    assert sum(action.keys == () for action in actions) == 1
```

- [x] **Step 2: Run red and implement immutable candidates/envelopes/enumeration**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_actions.py -q`
Expected: FAIL because `actions.py` does not exist.

`Candidate` requires a 64-hex `stable_key` in production, but tests may use short fixture keys only through `DecisionEnvelope.for_test`; the public constructor enforces nonempty unique keys, current nonnegative non-bool option indices, `0 <= min_count <= max_count <= candidate_count`, and `order_semantics` in `unordered_set`/`ordered_sequence`. `enumerate_complete_actions` uses combinations for unordered sets and permutations for ordered sequences, returns deterministic key order, and raises `CompleteActionEnumerationError` before materializing more than `limit` actions.

- [x] **Step 3: Run enumeration tests green and add limit/error cases**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_actions.py -q`
Expected: PASS, including max-count zero, duplicate keys, duplicate current indices, inconsistent bounds, and an explicit enumeration-limit failure.

- [x] **Step 4: Write the failing normalized autoregressive distribution test**

```python
import math
from mage_ptcg.meta_specialist.actions import complete_action_distribution

def test_canonical_autoregressive_set_distribution_sums_to_one() -> None:
    envelope = _envelope(1, 2)
    key_a, key_b, key_c = "1" * 64, "2" * 64, "3" * 64

    def logits(prefix: tuple[str, ...], allowed: tuple[str, ...]):
        return {token: 0.0 for token in allowed}

    distribution = complete_action_distribution(
        envelope,
        step_logits=logits,
        enumeration_limit=32,
    )
    assert set(action.keys for action in distribution) == {
        (key_a,), (key_b,), (key_c,),
        (key_a, key_b), (key_a, key_c), (key_b, key_c),
    }
    assert math.isclose(sum(distribution.values()), 1.0, abs_tol=1e-12)
    assert all(probability > 0.0 for probability in distribution.values())
```

- [x] **Step 5: Implement canonical next-token masking and exact log probability**

Use `STOP_TOKEN = "__STOP__"`. For an unordered prefix, candidate tokens must have stable key greater than the last selected key. Mask any token that would make `min_count` unreachable with remaining greater keys. `STOP_TOKEN` is legal only after `min_count` and before/at `max_count`; at `max_count` stop is forced with log-probability 0. Use a stable log-sum-exp and reject missing, extra, bool, NaN, or infinite logits.

`complete_action_log_probability` validates the complete action then accumulates the selected-token and final-stop log probabilities. `complete_action_distribution` enumerates all legal complete actions and exponentiates their log probabilities. It raises if the total differs from 1 by more than `1e-10`.

- [x] **Step 6: Run probability tests green**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_actions.py -q`
Expected: PASS for mandatory, optional, min=max, zero-max, and nonfinite-logit cases.

- [x] **Step 7: Add failing shuffle-invariance and Q tests**

```python
from mage_ptcg.meta_specialist.actions import (
    DecisionEnvelope,
    complete_action_distribution,
    greedy_decode,
    q_argmax,
)

def test_candidate_shuffle_preserves_distribution_decode_and_q_target(
    decision_state_factory,
) -> None:
    original = DecisionEnvelope.from_decision_state(
        decision_state_factory(option_order=(0, 1, 2)),
        min_count=1,
        max_count=2,
        order_semantics="unordered_set",
    )
    shuffled = DecisionEnvelope.from_decision_state(
        decision_state_factory(option_order=(2, 0, 1)),
        min_count=1,
        max_count=2,
        order_semantics="unordered_set",
    )

    def logits(prefix, allowed):
        return {
            token: float(int(token[:8], 16) % 17) if token != "__STOP__" else -0.25
            for token in allowed
        }

    left = {action.keys: p for action, p in complete_action_distribution(original, step_logits=logits, enumeration_limit=32).items()}
    right = {action.keys: p for action, p in complete_action_distribution(shuffled, step_logits=logits, enumeration_limit=32).items()}
    assert left == right
    assert greedy_decode(original, step_logits=logits).keys == greedy_decode(shuffled, step_logits=logits).keys

    q = {keys: float(index) for index, keys in enumerate(sorted(left))}
    assert q_argmax(original, q, enumeration_limit=32).keys == q_argmax(shuffled, q, enumeration_limit=32).keys
```

- [x] **Step 8: Implement the DecisionState adapter, greedy decode, Q argmax, and run green**

`DecisionEnvelope.from_decision_state` derives stable keys from `LegalAction.action_key.digest` and current engine indices from `LegalAction.option_index`. It rejects duplicate stable keys because ambiguous ActionKey-to-index mapping is not safe. `greedy_decode` chooses the maximum finite logit at each canonical step and tie-breaks by token string. `q_argmax` requires an exact finite Q value for every enumerated complete action and tie-breaks by complete-action key tuple.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_actions.py tests/meta_specialist/test_action_properties.py -q`
Expected: PASS with the option-shuffle property.

- [x] **Step 8A: Apply the pre-implementation safety audit (authoritative over earlier shorthand)**

The following requirements close the independent pre-implementation audit and are part of Task 3 acceptance:

1. `DecisionEnvelope.from_decision_state` treats `decision.normalized_public_observation["select"]` as the authoritative source of `min_count`, `max_count`, selection type, and `option_count`. If compatibility `min_count`/`max_count` arguments remain, they are strict non-bool assertion values that must equal the authoritative values. Require `option_count == len(decision.legal_actions)`; mismatches fail closed.
2. `CompleteAction` is immutable and contains canonical stable `keys` plus envelope-current `option_indices`. It validates cardinality, unique known keys, unique non-bool current indices, exact key/index correspondence, and the originating decision/envelope identity. For `unordered_set`, keys are ascending and CABT execution indices are numerically sorted; stable equality/Q identity never uses ephemeral indices. Stale actions from another envelope are rejected.
3. `legal_next_tokens(envelope, prefix) -> tuple[str, ...]` returns deterministic eligible keys followed by `STOP_TOKEN` only when legal; STOP is forced at max count. `DecisionEnvelope.for_test` is explicitly test-only, accepts short fixture keys plus candidate indices/bounds/semantics, and performs the same structural invariants as production except 64-hex key validation.
4. For `ordered_sequence`, every unselected key is eligible in deterministic key order, STOP is legal after the minimum and forced at the maximum, each permutation has one generation path, and emitted option indices preserve semantic sequence order. Add the `n=2,min=1,max=2` enumeration/normalization test containing both `(a,b)` and `(b,a)`.
5. Add a privacy-safe persisted projection, `DecisionEnvelope.to_public_trace_payload(action)` (or an equivalently named interface). It is constructed only from `DecisionState.to_trace_payload()` and `ActionKey.to_public_trace_payload()` projections. It must omit raw observations, own-private state, actor-private `card_id`, production `ActionKey.digest`, `DecisionState.digest`, private action-set digest, and ephemeral option indices. `Candidate`, `DecisionEnvelope`, and `CompleteAction` use redacted reprs and are never persisted with `dataclasses.asdict`.
6. Strengthen shuffle tests by retaining both complete actions, mapping each emitted current index through that source `DecisionState.legal_actions` back to its digest, and asserting equal selected digest set/order semantics and cardinality. Every emitted index must be current, legal, unique, non-bool, in range, and in the execution order defined above; raw index tuples may differ after shuffle.
7. Add explicit equal-logit and equal-Q tie tests proving tie-breaks use stable ActionKey strings / complete key tuples rather than option indices. Add greedy and Q cases whose optimum has minimum and maximum legal cardinality.
8. Logit and Q mappings require exact domains and finite non-bool numeric values. Test missing/extra keys, bool, NaN, positive infinity, and negative infinity for both interfaces. Q output must be a mapping over exactly all enumerated complete-action key tuples.
9. `actions.py` may depend only on `mage_ptcg.decision_state` and standard-library utilities. It must not import or adapt current R2D3, `continuous_league.collector`, or the single-index `R2D3Transition`, and Task 3 does not modify those modules.
10. The property-test file defines its own `decision_state_factory` fixture. Add fail-closed tests for duplicate/unknown/noncanonical keys, wrong cardinality, stale envelope identity, mismatched CABT bounds/count, direct token masks, and a private-hand/raw-observation sentinel absent from serialized public trace and repr output.

- [x] **Step 8B: Make the persisted complete-action trace positive and unambiguous**

These requirements close the second independent audit and are authoritative:

1. `DecisionEnvelope.from_decision_state` retains an immutable per-candidate public projection derived at adaptation time from that candidate's `ActionKey.to_public_trace_payload()`, together with public decision metadata from `DecisionState.to_trace_payload()`. It fails closed if two production candidates have indistinguishable redacted public projections.
2. `to_public_trace_payload(action)` emits a schema version, public decision identity, authoritative `selection_type`, `min_count`, `max_count`, `order_semantics`, `selected_count`, and the selected candidates' public projections. Unordered selections use canonical key order; ordered selections use semantic order. It never emits production stable digests, private decision/action-set digests, raw observations/private state, or current option indices.
3. Two distinguishable public selections must yield different trace payloads. The same stable selection remapped through a permuted option list must yield the same serialized payload. Tests assert the selected cardinality and exact redacted public action projections are positively present, not merely that private fields are absent.
4. Public tracing is supported only for envelopes adapted with `from_decision_state`; `for_test`/synthetic envelopes without explicit safe projections raise a clear non-persistable-envelope error.
5. `DecisionEnvelope` has an immutable authoritative `selection_type` field populated from `normalized_public_observation["select"]["type"]`. Every source `LegalAction.action_key.selection_type` must match it, and the adapter test asserts this invariant.
6. For `unordered_set`, `option_indices` is execution-only and not positionally aligned with `keys`; validate `set(option_indices) == {envelope.index_for_key(key) for key in keys}`. For `ordered_sequence`, keys and indices are positionally aligned and indices preserve key order.
7. Privacy tests capture the actual `ActionKey.digest`, `DecisionState.digest`, private `action_set_digest`, and selected option-index text, then assert those concrete values are absent from every runtime `repr` and the serialized public trace.

- [x] **Step 8C: Remove private-derived ordering and use structural privacy oracles**

These final audit clarifications are authoritative:

1. Runtime `CompleteAction.keys` remains ordered by production stable key. Persisted unordered selected public projections are independently sorted by canonical serialization of the redacted public projections, never by a production stable key; ordered-sequence traces preserve semantic order.
2. `public_decision_identity` is a domain-separated digest of the stable public subset `{schema_version, public_state_digest, public_action_set_digest, selection_type, min_count, max_count, order_semantics}`. It must not use `DecisionState.to_trace_payload()["trace_digest"]` or the current `action_keys` list order.
3. Add a fixture where changing only a private card ID changes/reorders production digests but leaves the redacted public selection equivalent; persisted unordered projection ordering and public decision identity must remain unchanged. Retain byte-equivalence under raw option permutation.
4. Option-index privacy tests are structural: recursively reject fields named `option_index`, `option_indices`, or equivalent current-index fields in persisted trace; reject `option_index=`/`option_indices=` and the exact internal mapping/tuple representation in runtime reprs. Do not assert that a bare digit such as `"1"` is absent, because counts and schema versions legitimately contain it. Continue exact-value absence checks for full private 64-hex digests.

- [x] **Step 8D: Add the source-backed CABT identity/order prerequisite for runtime**

This prerequisite is part of Task 3 and must pass before Task 4 starts:

1. Modify `mage_ptcg.decision_state` so official multiple Skill (`cardId`, `serial`), SpecialCondition (`specialConditionType`), and ToolCard (`toolIndex`) options receive distinct stable ActionKeys without using ephemeral option index as a uniqueness fallback. Add strict fixtures with at least two options of each relevant kind and prove key uniqueness and option-permutation stability.
2. Preserve the Step 8A–8C privacy contract: actor-private source IDs/serials may participate in runtime identity only through an explicitly classified private component and must be absent from public trace/repr. Publicly observable in-play identity may be projected only through a verified public-board entity mapping; ambiguous redacted public actions fail closed.
3. Add `resolve_order_semantics(selection_type, selection_context) -> Literal["unordered_set", "ordered_sequence"]` from an explicit source-backed contract. At the agent JSON boundary, CABT `(SelectType.SKILL=5, SelectContext.SKILL_ORDER=34)` is `ordered_sequence`; official option types are Skill=15, SpecialCondition=16, and ToolCard=4. C++ internal enum values are not agent JSON values. Recognized non-order-sensitive schemas are explicitly enumerated as `unordered_set`; unknown/unclassified schemas fail closed. Do not infer order from min/max or option indices.
4. Add native-contract regression evidence showing CABT preserves returned index order for SkillOrder and its engine consumes that order semantically. Task 4 consumes this resolver and never globally sorts ordered indices.
5. Freeze the B3 identity rules in this repository. Skill `(select.type=5, context=34, option.type=15)` requires strict non-bool integer `cardId` and `serial`; the raw pair is actor/transient identity only. Its public identity is a locator only when that pair resolves exactly once in the allowlisted public registry (both players' active, bench, discard, stadium, plus public Pokémon `energyCards`, `tools`, and `preEvolution`); zero or multiple matches produce the exact redacted source and never persist the pair or its digest. SpecialCondition `(select.type=10, option.type=16, context in {47,48})` requires enum 0..4 and persists only `POISON`/`BURN`/`SLEEP`/`PARALYZE`/`CONFUSE`. ToolCard `(option.type=4)` requires strict `(area,index,playerIndex,toolIndex)`, resolves only a public active/bench host plus in-range attachment slot, and persists that locator without raw child identity. Hand, prize, deck, looking, log, and search payloads are never traversed.
6. Exact duplicate actor payloads for Skill pair, SpecialCondition value, or Tool full tuple fail closed; option ordinal/index is never a uniqueness rescue. Two candidates whose safe public projections collide after redaction also fail at envelope adaptation. Required fixtures are: two public Skills with permutation-stable keys/locators and ordered execution; hidden Skill byte-invariant redaction plus collision failure; ambiguous public Skill mapping; two SpecialConditions plus bool/missing/out-of-range rejection; two ToolCards plus host/area/slot/bool rejection; exact duplicate payloads for all three types; and v1/v2 hash-domain migration with structural denial of actor payload, production digest, and option indices.

- [x] **Step 8F: Apply the independent code-review corrections and JSON-boundary oracle**

1. The frozen `kaggle-environments==1.32.0` agent JSON schema is: `(0,0)` Main; `(1,1..25)` Card; `(2,26..28)` AttachedCard; `(3,29)` CardOrAttachedCard; `(4,30..33)` Energy; `(5,34)` SkillOrder; `(6,35..36)` Attack; `(7,37)` Evolve; `(8,38..40)` Count; `(9,41..46)` YesNo; `(10,47..48)` SpecialCondition. Only `(5,34)` is `ordered_sequence`; every other listed pair is `unordered_set`; all other pairs fail closed. Check in the exact versioned mapping and a captured `(5,34)` observation fixture, with provenance to the official cabt API reference and a local 1.32.0 two-game probe. Do not use the native C++ one-based SelectType/SelectContext bytes as JSON values.
2. `DecisionEnvelope.from_decision_state` derives order semantics from authoritative type/context itself. If a compatibility `order_semantics` argument remains, it is only a strict assertion equal to `resolve_order_semantics`; passing `unordered_set` for SkillOrder or any mismatch fails closed.
3. Every `ActionKey` construction path recursively validates its stored public projection and rejects forbidden/raw identity keys. Direct construction cannot inject `cardId`, `serial`, actor payload, digest, or option-index fields. Serialized action payloads carry an explicit schema version. Production v2 digests are recomputed/verified from both the exact typed actor payload and canonical public projection; Skill, SpecialCondition, and ToolCard actor unions are exact on direct and deserialized paths. v1 feature artifacts require an explicit exact-integer-v1 reader and cannot enter a v2 `DecisionState`/`DecisionEnvelope` or C5 public record; v1/v2 mixed sets fail closed.
4. Existing observability, knowledge, and distillation consumers must not call ToolCard identity construction without the public-board resolver. Route full observations through the verified `DecisionState` path, or explicitly fail closed at that caller with a tested neutral result. Add type-4 regressions for every affected public adapter and keep existing non-Tool behavior compatible.
5. Complete-action counting uses a source-backed maximum of 60 legal options, accumulates combinations/permutations only until `limit + 1`, and short-circuits immediately on excess. Every exact-enumeration entry point rejects a nonpositive/bool limit and enforces the hard maximum `65,536` before counting or materialization. Add large-n/small-limit tests for both order semantics. Finite logits that would underflow a legal complete action to zero must raise a probability-domain error rather than silently deleting support.
6. Generic public action fields use a closed per-option CABT allowlist with exact non-bool integer values; unknown option types and raw/private/digest/current-index aliases fail closed. Public projection tampering cannot retain the same v2 digest.
7. C5 public conversion accepts only authenticated v2 ActionKeys. `validate_record` validates every candidate with `validate_public_action_feature_payload` before recomputing its public ID, so rehashed raw-field injection is rejected.

- [x] **Step 8E: Freeze the cross-task public trace schema**

`DecisionEnvelope` stores authoritative `selection_context` as well as `selection_type`, and every source `ActionKey.context`/`selection_type` must match. `to_public_trace_payload(action)` has exactly these keys:

`schema_version`, `public_decision_identity`, `public_state_digest`, `public_action_set_digest`, `selection_type`, `selection_context`, `min_count`, `max_count`, `order_semantics`, `selected_count`, `selected_public_actions`.

`public_decision_identity` is a domain-separated digest of the stable public subset `{schema_version, public_state_digest, public_action_set_digest, selection_type, selection_context, min_count, max_count, order_semantics}`. Add tests that changing only context changes the identity, while raw option permutation does not. Task 4 may append policy metadata and log probability but must not reconstruct missing public fields from private/runtime envelope internals.

- [x] **Step 9: Record Task 3 evidence without committing**

The report records each RED failure, final focused test count, and `git diff --check -- src/mage_ptcg/meta_specialist/actions.py tests/meta_specialist/test_actions.py tests/meta_specialist/test_action_properties.py`.

### Task 4: CPU-Only CABT Runtime Boundary and Privacy-Safe Trace

**Files:**
- Create: `src/mage_ptcg/meta_specialist/runtime.py`
- Create: `tests/meta_specialist/test_runtime.py`
- Create: `tests/meta_specialist/test_runtime_privacy.py`
- Create: `tests/meta_specialist/test_runtime_cabt.py`

**Interfaces:**
- Consumes: `build_decision_state`, `DecisionEnvelope`, `greedy_decode`, and an injected `StepLogitPolicy`.
- Produces: `MetaSpecialistRuntime`, `RuntimeDecisionTrace`, `PackageTelemetry`, `RuntimeConstraintManifest`, and `make_agent`.

- [ ] **Step 1: Write failing registration and malformed-selection tests**

```python
import pytest
from mage_ptcg.meta_specialist.runtime import MetaSpecialistRuntime, RuntimeContractError

def test_runtime_delivers_exact_deck_once(runtime_factory, qualified_deck_asset) -> None:
    runtime = runtime_factory()
    assert runtime({"select": None}) == list(qualified_deck_asset.card_ids)
    assert runtime({"select": None}) == []

def test_runtime_fails_closed_on_malformed_selection(runtime_factory) -> None:
    runtime = runtime_factory()
    runtime({"select": None})
    with pytest.raises(RuntimeContractError):
        runtime({"select": {"option": [], "minCount": 1, "maxCount": 1}})
```

- [ ] **Step 2: Run red and implement registration/reset behavior**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_runtime.py -q`
Expected: FAIL because `runtime.py` does not exist.

`MetaSpecialistRuntime` validates the exact card tuple of its `QualifiedDeckAsset` and its complete `DeckLockDecision` binding during construction, stores no network/GPU object, calls `policy.reset()` on runtime reset, returns the deck only on the first `select is None` callback, and returns `[]` for later registration callbacks.

- [ ] **Step 3: Add failing complete-selection runtime test**

```python
def test_one_callback_emits_one_legal_complete_action(
    cabt_observation_factory,
    runtime_factory,
) -> None:
    observation = cabt_observation_factory(min_count=1, max_count=2, option_order=(2, 0, 1))
    runtime = runtime_factory()
    runtime({"select": None})
    chosen = runtime(observation)
    assert 1 <= len(chosen) <= 2
    assert len(chosen) == len(set(chosen))
    assert all(0 <= index < 3 for index in chosen)
    assert runtime.environment_action_count == 1
    assert len(runtime.traces) == 1
    assert runtime.traces[0].to_payload()["selected_count"] == len(chosen)
```

- [ ] **Step 4: Implement decision projection and one-action accounting**

For a real decision, call `build_decision_state(observation)` once, derive the authoritative envelope and source-backed order semantics, open one `DecisionSession`, and call `greedy_decode` once through its cached pure scorer. Revalidate the complete action and public trace, then follow the exact commit/abort transaction in Step 7C. Unordered execution uses the envelope's execution order; ordered execution preserves semantic selection order. Never update recurrent/environment state per selected element.

- [ ] **Step 5: Write the failing privacy test**

```python
def test_trace_contains_only_public_digests_and_no_raw_cards(
    cabt_observation_factory,
    runtime_factory,
) -> None:
    observation = cabt_observation_factory(
        min_count=1,
        max_count=2,
        own_hand_ids=(741, 742),
        opponent_hidden_marker="SECRET-OPPONENT-CARD",
    )
    runtime = runtime_factory()
    runtime({"select": None})
    runtime(observation)
    payload = runtime.traces[0].to_payload()
    serialized = json.dumps(payload, sort_keys=True)
    assert "SECRET-OPPONENT-CARD" not in serialized
    assert "observation" not in payload
    assert set(payload) == {
        "schema_version",
        "public_decision_identity",
        "public_state_digest",
        "public_action_set_digest",
        "policy_identity",
        "candidate_class",
        "selection_type",
        "selection_context",
        "min_count",
        "max_count",
        "order_semantics",
        "selected_count",
        "selected_public_actions",
        "complete_action_log_probability",
    }
```

- [ ] **Step 6: Implement the frozen trace and run runtime tests green**

`RuntimeDecisionTrace.to_payload` stores only the listed fields, sourced from the Task 3 public complete-action projection plus validated policy metadata and cached log probability. Its `schema_version` is the exact string `meta-specialist-runtime-decision-trace-v1`; the consumed Task 3 projection must have exact integer `schema_version == 1`, and mismatches fail closed. Production ActionKey/DecisionState digests, stable keys, option indices, raw observations, and actor-private payloads are absent.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_runtime.py tests/meta_specialist/test_runtime_privacy.py -q`
Expected: PASS.

- [ ] **Step 7: Add and run the option-order property at the runtime boundary**

Construct semantically identical observations with permuted `select.option`. With the same digest-based policy, map each emitted current index back through that observation's `DecisionState` and assert equal selected digest sets while current indices may differ.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_runtime.py::test_runtime_is_invariant_to_option_enumeration_order -q`
Expected: PASS.

- [ ] **Step 7A: Apply the independent CABT runtime audit (authoritative over earlier shorthand)**

Task 4 begins only after Task 3 Steps 8D, 8E, and 8F pass, including `tests/meta_specialist/test_cabt_action_identity.py`, the JSON-boundary oracle/adapter regressions, and the Task 3 focused action/property suite. The following requirements define the production runtime boundary:

1. `MetaSpecialistRuntime.__call__(observation, configuration=None)` is a strict state machine. A mapping with an explicit `select is None` returns a fresh exact deck copy only on the first callback and `[]` thereafter (including terminal callback). Missing `select`, nonmapping observation/select, action before deck delivery, non-list options, bool/non-int/negative/inconsistent bounds fail with `RuntimeContractError`. `options=[]`, `min=max=0` is one valid complete action and commits one trace/count.
2. Consume Task 3 `resolve_order_semantics`. At the agent JSON boundary CABT `(selection_type=5, context=34)` SkillOrder preserves selected semantic order and is never numerically sorted. Only `unordered_set` emission is normalized according to the validated envelope. Unknown selection contracts fail closed.
3. Replace mutable per-step policy APIs with `StepLogitPolicy.reset()` and `begin_decision(decision) -> DecisionSession`; the session exposes a pure `scorer` whose `step_logits(prefix, allowed)` is cached by `(prefix, allowed)`. Decode and log probability use the same cache with no second inference/recurrent update. Commit/abort semantics are authoritative in Step 7C.
4. Validate exact built-in-string lowercase SHA-256 `policy_identity`, candidate class in `checkpointed_specialist`/`static_rule_bundle`, and an exact locked/qualified deck binding. Recompute canonical card multiset identity, require 60 positive non-bool immutable card IDs, match `DeckLockDecision`, and return mutation-safe copies. `make_agent` creates fresh runtime/policy per game/seat; explicit `reset()` clears delivery/count/bounded trace and calls policy reset once.
5. A decision is transactional: validate callback/select; build `DecisionState` once; build `DecisionEnvelope` once; begin policy once; decode once; revalidate action against the current envelope; compute log probability and Task 3 public trace from cached scores; only then atomically append trace/increment success count and return indices. Any failure leaves environment/success counters, trace, and recurrent committed state unchanged; typed diagnostic failure counters follow Step 7C.3. Log probability is finite and `<= 0`.
6. Runtime persistence derives only from `DecisionEnvelope.to_public_trace_payload(action)`. `RuntimeDecisionTrace` is frozen/slots/redacted and contains exactly schema/public decision and action-set identities, policy identity/class, authoritative type/context/bounds/semantics, selected count/public projections, and complete-action log probability. It contains no raw observation/private state, production decision/action digests, stable keys, private action-set digest, or option indices. `traces` exposes an immutable tuple snapshot, storage capacity is exactly 4,096, and overflow increments a dropped-trace count.
7. `RuntimeConstraintManifest` requires the explicit strict built-in positive integer fields frozen in Step 7C.4 and is bounded by official resources. No production defaults are invented; missing manifest fails closed. Runtime measures with an injected monotonic clock and reports post-return telemetry only. Task 5 enforces true subprocess hard timeout/RSS. Timing never enters deterministic trace identity.
8. Add privacy regressions using actual private card IDs/serials and their full digests, structural forbidden-field checks, public-equivalent private-hand changes, redacted repr/exceptions, unordered canonical trace and ordered semantic trace. Do not persist or assert option indices.
9. Add spies proving one state build/envelope build/begin/decode/score-per-prefix/transactional commit, including empty action and failure rollback. Cover optional, zero-max, fixed-cardinality, ordered and unordered selections; repeated null returns fresh lists; runtime/policy instance isolation.
10. Task 4 adds a source-runtime CABT smoke in both seats against Rule-v0 using a genuinely qualified legal deck and fresh factories. Require both statuses `DONE`, successful registration, no invalid/crash/timeout, and no max-step truncation. This is protocol safety, not strength evidence. If CABT dependency is unavailable, report explicit blocked dependency rather than pass.
11. Runtime imports are restricted by an AST closure test to approved local pure modules and stdlib; source code has no direct network/GPU/subprocess client. Task 5 provides the behavioral GPU-hidden/network-denied/archive-only proof. Regex absence alone is not qualification.

- [ ] **Step 7B: Freeze package telemetry and conservative local qualification limits**

1. Task 4 owns `PackageTelemetry` and `MetaSpecialistRuntime.package_telemetry()`. The exact payload keys are `schema_version="meta-specialist-package-telemetry-v1"`, `candidate_class`, `expected_policy_identity`, `loaded_policy_identity`, `model_loaded`, `checkpoint_lineage_id`, `checkpoint_lineage_reason`, `fallback_count`, `invalid_count`, `crash_count`, `timeout_count`, `legal_decision_count`, and `legal_action_count`. Counter values are nonnegative strict ints. The runtime validates this data from the injected policy; it never copies `loaded_policy_identity` from a bundle claim.
2. `checkpointed_specialist` requires `model_loaded is True`, exact expected/loaded policy identity equality, one SHA-256 checkpoint lineage ID, and null lineage reason. `static_rule_bundle` requires `model_loaded is False`, matching recomputed static-policy identity, null lineage ID, and exact reason `not_applicable_static_policy`.
3. Task 5 owns the real specialist archive entrypoint template and bootstrap implementation, not only a synthetic test file. Packaged top-level `main.py` exposes module-level `package_telemetry()` before its final `agent` binding, builds the runtime from the archived qualified-deck/DeckLock/policy/constraint assets, and delegates telemetry to that runtime snapshot. Task 5 archive-only tests build from this repository-owned entrypoint and require counters after actual decisions; missing/malformed/unused/different policy telemetry is never ready.
4. Runtime-constraints v1 uses explicit conservative local qualification values (not claimed as official Kaggle action limits): Python `3.11.11`; verifier-only `kaggle-environments==1.32.0`; archived-agent direct host dependency allowlist empty (stdlib-only); decision p95 target 100 ms; p99 target 250 ms; per-decision hard bound 1,000 ms; per-game hard bound 300,000 ms; peak RSS 8,388,608 KiB; trace capacity 4,096. Task 5 outer two-seat timeout is 900,000 ms and final readiness requires 100 archive-only stress games, balanced 50/50 by seat. A newer verified host dependency or bound requires a new contract version.

- [ ] **Step 7C: Freeze rollback, production construction, counters, and smoke schedules**

1. Replace the policy transaction with `StepLogitPolicy.begin_decision(decision) -> DecisionSession`. A session exposes a pure `scorer`, `commit(action)`, and idempotent no-throw `abort()`. `begin_decision` never mutates committed recurrent state. Pre-session validation failures cannot and do not call abort. After a session exists, any failure before commit calls `abort` exactly once; after all validation/trace/time work, runtime materializes the next bounded trace tuple and counters first, calls no-throw atomic `commit` exactly once, then installs the prebuilt runtime state. Commit and abort never both occur. Spy tests compare hidden state before/after abort.
2. Production constructor is exact: `MetaSpecialistRuntime(*, deck_asset: QualifiedDeckAsset, deck_lock: DeckLockDecision, policy: StepLogitPolicy, expected_policy_identity: str, constraints: RuntimeConstraintManifest, monotonic=time.monotonic)`. It revalidates 60 cards, canonical deck identity, SHA fields, qualification status/evidence, full DeckLock integrity/lineage, policy identity/class/telemetry, and the exact constraints ID. Unit tests use real Task 2 factories/objects through a `runtime_factory` fixture; no public tuple-only constructor or production bypass exists.
3. `StepLogitPolicy.policy_telemetry()` returns a strict frozen `PolicyTelemetrySnapshot` with loaded `policy_identity`, `candidate_class`, `model_loaded`, conditional lineage fields, and nonnegative `fallback_count`. Runtime owns `invalid_count`, `crash_count`, `timeout_count`, `legal_decision_count`, and `legal_action_count`. Both legal counters mean successfully committed complete-action callbacks (not selected tokens) and are equal in P0. Diagnostic counters increment after classification/rethrow without changing success count, trace, or recurrent state. Fallback increments only on an explicit typed policy outcome; implicit fallback is forbidden. Reset clears per-game runtime counters/trace and policy state; Task 5 aggregates immutable game telemetry snapshots.
4. `RuntimeConstraintManifest` exact v1 payload keys are `schema_version`, `python_version`, `verifier_dependency`, `host_dependencies`, `decision_p95_target_ms`, `decision_p99_target_ms`, `decision_hard_timeout_ms`, `game_hard_timeout_ms`, `peak_rss_limit_kib`, `trace_capacity`, and `runtime_constraints_id`. Exact values are `meta-specialist-runtime-constraints-v1`, `3.11.11`, `kaggle-environments==1.32.0`, an empty JSON array, 100, 250, 1,000, 300,000, 8,388,608, and 4,096. `runtime_constraints_id = SHA256(b"meta-specialist-runtime-constraints-v1\\0" + canonical_json(payload_without_runtime_constraints_id).encode("utf-8"))`, where canonical JSON uses UTF-8, sorted keys, compact separators, and `allow_nan=False`. All integer fields are exact built-in positive ints, `p95 <= p99 <= decision_hard <= game_hard`, RSS is below official RAM, and unknown/missing/bool/float values fail. Task 4 computes this ID; Task 5 independently recomputes the same frozen contract rather than trusting Task 4's helper.
5. Production traces contain no digest/index. Executor/property tests may privately map current returned indices through their source `DecisionState`: unordered compares stable sets; ordered compares stable sequences. Those runtime-only assertions are required and never serialized.
6. Exact duplicate Skill `(cardId,serial)`, SpecialCondition value, or Tool `(area,index,playerIndex,toolIndex)` options fail closed without ordinal/index rescue. Public locator/redaction behavior and all seven fixture groups are exactly those frozen in repository Task 3 Step 8D; no `/tmp` artifact is authoritative.
7. Source smoke is exactly two fresh games, specialist seat A once and seat B once. Archive readiness is 100 fresh specialist-vs-Rule-v0 games, A=50/B=50, executed as ten balanced 10-game batches; each game uses 300,000 ms, each batch a 900,000 ms watchdog, and the suite a 9,000,000 ms watchdog. Max-step truncation fails. External qualification aggregates p50/p95/p99/max decision latency, game duration, peak RSS, legal counts, and all fault counters; deterministic traces exclude timing.
8. Integration status is `PASS` or `BLOCKED_DEPENDENCY`. Blocked is never counted as a passing test, Task completion, P0 runtime qualification, or submission readiness; it propagates to Task 5/6.

- [ ] **Step 7D: Freeze the packaged policy factory and single-game binding**

This section resolves the Task 5 R3 lifecycle audit and is authoritative over the
earlier shorthand that `make_agent` merely creates a fresh policy.

1. `runtime.py` exports a runtime-checkable `StepLogitPolicyFactory` protocol with the
   sole method `new_policy() -> StepLogitPolicy`.  It also exports the frozen/slots,
   redacted `PackagedAgentBinding` whose only public data attributes are `agent` and
   `package_telemetry`; both are zero-ambient-state callables, and
   `package_telemetry()` takes no arguments and returns a fresh exact Task 4 telemetry
   payload.
2. The exact factory surface is
   `make_agent(*, deck_asset: QualifiedDeckAsset, deck_lock: DeckLockDecision,
   policy_factory: StepLogitPolicyFactory, expected_policy_identity: str,
   constraints: RuntimeConstraintManifest, monotonic=time.monotonic) -> PackagedAgentBinding`.
   It calls `policy_factory.new_policy()` exactly once, rejects exceptions, `None`,
   wrong types, or a policy whose strict telemetry does not match the expected identity,
   candidate class, model-loaded state, and DeckLock lineage, then constructs exactly
   one `MetaSpecialistRuntime`.
3. One binding has **single-game, single-seat lifetime**.  It never guesses game
   boundaries and is not reusable for a second registration.  Every source smoke game
   calls `make_agent` again.  Task 5 loads the archived module in a new isolated process
   for every game/seat, so each load reconstructs a fresh loader/factory, policy,
   runtime, trace, and counters.  The Task 5 verifier, not the binding, aggregates
   immutable per-game telemetry snapshots.
4. `MetaSpecialistRuntime.reset()` remains an explicit unit-test/diagnostic API but is
   not called implicitly by the binding and is not a substitute for a fresh archive
   load.  It clears one runtime and invokes that policy's `reset()` exactly once.
5. Add spies proving two `make_agent` calls from one factory return distinct policy and
   runtime identities, no recurrent state/counter/trace crosses bindings, the factory
   is called once per binding, and a second-game registration on the same binding is
   rejected/returns terminal `[]` according to the already-frozen state machine rather
   than silently resetting.  The source two-seat smoke uses two distinct bindings.

- [ ] **Step 8: Record Task 4 evidence without committing**

Record focused tests and confirm `rg -n 'torch|cuda|requests|urllib|socket' src/mage_ptcg/meta_specialist/runtime.py` returns no matches.

### Task 5: Deterministic Specialist Submission Builder and Verifier

**Files:**
- Create: `src/mage_ptcg/meta_specialist/package.py`
- Create: `src/mage_ptcg/meta_specialist/entrypoint.py`
- Create: `templates/meta_specialist/main.py`
- Create: `templates/meta_specialist/policy_loader.py`
- Create: `configs/meta_specialist/entrypoint_contract_v1.json`
- Create: `tests/meta_specialist/test_package.py`
- Create: `tests/meta_specialist/test_package_security.py`
- Create: `tests/meta_specialist/test_entrypoint.py`
- Create: `scripts/build_meta_specialist_submission.py`
- Create: `scripts/verify_meta_specialist_submission.py`
- Create: `tests/meta_specialist/test_submission_scripts.py`

**Interfaces:**
- Consumes: `BUNDLE_SIZE_LIMIT_BYTES`, explicit source-root files, deck/policy identities.
- Produces: `BundleSpec`, `DependencyContractIds`, `EntrypointContract`,
  `stage_repository_specialist`, `build_specialist_archive`,
  `verify_specialist_archive`, `verify_specialist_submission`,
  `extract_verified_archive`, and `package_command_outcome`.

- [ ] **Step 1: Write the failing deterministic archive test**

```python
from hashlib import sha256
from pathlib import Path
from mage_ptcg.meta_specialist.package import (
    BundleSpec,
    build_specialist_archive,
    verify_specialist_archive,
)

def test_specialist_archive_is_deterministic_and_has_required_top_level_files(
    tmp_path: Path,
    staged_specialist_source,
    staged_specialist_members,
    qualified_deck_asset,
    deck_lock,
    runtime_constraints_manifest,
    ladder_mechanics_snapshot,
    dependency_contract_ids,
) -> None:
    source = staged_specialist_source
    cards = qualified_deck_asset.card_ids
    deck_path = source / "deck.csv"
    deck_path.write_text("\n".join(map(str, cards)) + "\n", encoding="utf-8")
    (source / "weights.bin").write_bytes(b"weights")
    spec = BundleSpec(
        source_root=source,
        members=tuple(sorted((*staged_specialist_members, "deck.csv", "weights.bin"))),
        deck_member="deck.csv",
        qualified_deck_asset=qualified_deck_asset,
        deck_lock=deck_lock,
        runtime_constraints=runtime_constraints_manifest,
        ladder_mechanics=ladder_mechanics_snapshot,
        dependency_contract_ids=dependency_contract_ids,
        policy_entrypoint_member="policy_loader.py",
        policy_members=("weights.bin",),
        model_member="weights.bin",
        policy_identity=sha256(b"weights").hexdigest(),
        candidate_class="checkpointed_specialist",
        checkpoint_lineage_id=deck_lock.policy_lineage_id,
        checkpoint_lineage_reason=None,
    )
    first = build_specialist_archive(spec, tmp_path / "first.tar.gz")
    second = build_specialist_archive(spec, tmp_path / "second.tar.gz")
    assert first.archive_sha256 == second.archive_sha256
    assert (tmp_path / "first.tar.gz").read_bytes() == (tmp_path / "second.tar.gz").read_bytes()
    report = verify_specialist_archive(tmp_path / "first.tar.gz")
    assert report.required_top_level_files == ("main.py", "deck.csv")
    assert report.policy_identity == sha256(b"weights").hexdigest()
```

- [ ] **Step 2: Run red and implement canonical tar/gzip generation**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_package.py -q`
Expected: FAIL because `package.py` does not exist.

The builder requires relative POSIX member names, sorts names, rejects duplicates, symlinks, non-files, absolute paths, `..`, control characters, and output paths inside `source_root`. It injects top-level `meta_specialist_bundle.json` containing schema version, deck identity, policy identity, candidate class, member SHA-256/size records, CPU-only runtime constraints, and a content hash. Use USTAR metadata mode `0o644`, uid/gid/mtime `0`, empty uname/gname, and gzip `mtime=0`.

- [ ] **Step 3: Run deterministic tests green and verify exact size check**

`build_specialist_archive` writes to an exclusive sibling temporary file, checks compressed bytes are `<= 207_257_600`, fsyncs, verifies, and publishes collision-safely: an identical existing target is idempotent and a different/nonregular target is preserved and rejected. `verify_specialist_archive` checks the compressed size before reading members and rejects any metadata or manifest mismatch.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_package.py -q`
Expected: PASS, including tests that monkeypatch the private contract ceiling to exercise exact-boundary acceptance and limit-plus-one rejection without exposing a production bypass parameter.

- [ ] **Step 4: Write failing archive security tests**

```python
import io
import tarfile
from dataclasses import replace
import pytest
from mage_ptcg.meta_specialist.package import BundleSecurityError, verify_specialist_archive

@pytest.mark.parametrize("name", ["/abs.py", "../escape.py", "nested/../../escape.py"])
def test_verifier_rejects_unsafe_member_paths(tmp_path, name) -> None:
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(BundleSecurityError, match="unsafe"):
        verify_specialist_archive(archive_path)

def test_builder_rejects_secret_marker(valid_bundle_spec) -> None:
    spec = valid_bundle_spec
    (spec.source_root / "main.py").write_text("KAGGLE_KEY = 'secret'\n", encoding="utf-8")
    with pytest.raises(BundleSecurityError, match="secret"):
        spec.validate()

def test_builder_rejects_member_and_ancestor_symlinks(tmp_path, valid_bundle_spec) -> None:
    spec = valid_bundle_spec
    link = spec.source_root / "link.py"
    link.symlink_to(spec.source_root / "main.py")
    with pytest.raises(BundleSecurityError, match="symlink"):
        replace(spec, members=tuple(sorted((*spec.members, "link.py")))).validate()

    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(spec.source_root, target_is_directory=True)
    with pytest.raises(BundleSecurityError, match="symlink"):
        replace(spec, source_root=linked_root).validate()
```

- [ ] **Step 5: Implement fail-closed verification and run security tests green**

Reject non-regular tar members, duplicate names, noncanonical metadata, missing/duplicate required top-level files, nested substitutes such as `agent/main.py`, secret/private markers (`KAGGLE_KEY`, `KAGGLE_USERNAME`, `AWS_SECRET`, `PRIVATE KEY`), unexpected manifest fields, file hash/size mismatch, and a bundle manifest that requests GPU or network.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_package.py tests/meta_specialist/test_package_security.py -q`
Expected: PASS.

- [ ] **Step 6: Add an archive-only isolated callable smoke**

The test extracts only the verified archive to an empty temporary directory, changes CWD to a different empty directory, clears `PYTHONPATH`, sets `PYTHONNOUSERSITE=1`, invokes `python -I`, loads top-level `main.py` with `runpy.run_path`, and verifies `agent` is callable. It must not import any file from the repository checkout.

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_package.py::test_archive_only_main_is_callable_outside_repository -q`
Expected: PASS.

- [ ] **Step 6A: Apply the independent submission-safety audit (authoritative over earlier shorthand)**

Task 5 also creates `scripts/build_meta_specialist_submission.py`, `scripts/verify_meta_specialist_submission.py`, and `tests/meta_specialist/test_submission_scripts.py`. `package.py` is the primitive; the specialist verifier is the submission-readiness surface.

1. `BundleSpec` binds canonical `deck_identity`, exact `deck_file_sha256`, `candidate_class` (`checkpointed_specialist` or `static_rule_bundle`), explicit policy member(s), and `policy_identity` recomputed from policy bytes. A checkpointed specialist has exactly one valid `checkpoint_lineage_id`; a static rule has none and records `not_applicable_static_policy`. Builder and verifier independently parse exactly 60 positive non-bool deck IDs and recompute both deck identities. The archived runtime exposes immutable telemetry proving it loaded the bound policy identity; an unused weights file cannot satisfy the gate.
2. Freeze an exact recursive manifest schema. Reject duplicate JSON keys, unknown/missing fields, non-finite constants, and noncanonical JSON bytes. `content_hash` is a domain-separated SHA-256 over canonical manifest data excluding only itself. Sorted unique member records exclude the manifest and contain exactly `path`, `sha256`, and `size`; archive names equal the declared set plus exactly one top-level manifest.
3. The manifest pins the exact ladder contract: 202,400 KiB / 207,257,600 bytes, 200% CPU (2 vCPU), 12,815,744 KiB RAM, 12,388,608 KiB agent disk, `/kaggle_simulations/agent/`, CPU-only, no required network/GPU, exact host-dependency allowlist, and decision/game deadlines. External reports carry archive SHA-256 and compressed size.
4. Snapshot each regular source file once and use the same bytes for validation, hashes, and tar output. Reject symlinked roots/ancestors, resolved escapes, non-files, and output inside the source root. Publication uses an `O_EXCL` sibling temp, fsync, verification, then collision-safe atomic publish: existing identical is idempotent; existing different/nonregular fails without overwrite.
5. Verifier checks compressed size before `tarfile.open`, freezes one regular nonsymlink archive snapshot for verify+extract, and bounds member count, each member, and cumulative uncompressed bytes to no more than agent disk. Stream/count bytes; reject trailing data, concatenated gzip, and extraction bombs. `extract_verified_archive` accepts only absent/empty nonsymlink destinations, never follows pre-existing symlinks, never calls `extractall`, and cleans partial extraction on failure.
6. Enforce canonical single-stream gzip and USTAR: safe normalized POSIX names, exact deterministic member order, `REGTYPE`, mode `0644`, uid/gid/mtime zero, empty uname/gname, exact size, no absolute/dot/dotdot/backslash/empty/control/NUL/USTAR-invalid names, gzip mtime zero and no filename/comment/extra/trailing payload. Add independent negative tests for traversal, link types, device/FIFO/directory, duplicates, metadata/order/header/appended bytes.
7. Scan builder and verifier bytes case-insensitively for `KAGGLE_KEY`, `KAGGLE_USERNAME`, `AWS_SECRET`, `PRIVATE KEY`, authorization/cookie/home-path markers, and reject sensitive member names (`kaggle.json`, `.env`, `.git`, PEM/SSH/history/cache/tests/docs/data/report/experiments) unless a frozen narrow allowlist explicitly permits one. Test each guard independently.
8. Implement a specialist-specific AST local-import closure and exact host-dependency allowlist. Reject missing local modules and unknown third-party imports. The archive-only subprocess uses an empty CWD, cleared `PYTHONPATH`, `PYTHONNOUSERSITE=1`, `python -I`, `CUDA_VISIBLE_DEVICES=""`; audit first-party module `__file__` and candidate file/dynamic-library reads so repository/editable-install/sidecar/external reads fail.
9. The verifier resolves archived top-level `main.py` with `kaggle_environments.agent.get_last_callable`, proves first `select is None` returns the exact ordered archived deck, exercises complete-action legality, and runs archive-only CABT from both seats with explicit decision/game timeouts. Both statuses must be `DONE` with no crash/invalid/timeout and telemetry must be present. Missing/incompatible CABT may yield explicit `BLOCKED_DEPENDENCY`, never submission-ready.
10. Specialist package/scripts must not import/call fixed Rule-v0/Student-v0 builders, private helpers, or Student local-import closure. Add AST and monkeypatch independence tests; reject legacy fixed identities/paths while allowing a properly identified specialist `static_rule_bundle`.
11. Tests compare archive bytes (including reversed source member order), exact size boundary pass and +1 fail using private monkeypatched limits, identity/model/deck/candidate/lineage tampering, source/archive TOCTOU, poisoned workspace/sidecars, source deletion, import closure, dynamic external reads, both-seat telemetry, partial cleanup, idempotence and collision preservation. A callable-only smoke remains useful but is never reported as Kaggle `validation_passed` or submission-ready.

- [ ] **Step 6B: Freeze policy closure, telemetry, scripts, and verifier resources**

These decisions close the second package audit:

1. `BundleSpec` always uses sorted, unique, nonempty `policy_members`. For `checkpointed_specialist`, it also has exactly one non-null `model_member`, requires `policy_members == (model_member,)`, defines `policy_identity = SHA256(model bytes)`, requires one lowercase SHA-256 `checkpoint_lineage_id`, and has null `checkpoint_lineage_reason`. For `static_rule_bundle`, `model_member` is null, `policy_members` is the exact AST/file-read closure of behavior-affecting rule code/config excluding `deck.csv` and the bundle manifest, and `policy_identity = SHA256(b"meta-specialist-static-policy-v1\\0" + canonical_json(member_records))`; lineage ID is null and reason is exactly `not_applicable_static_policy`. The manifest always carries these exact conditional fields (including nulls). Helper/config mutation changes static identity; deck-only mutation does not masquerade as the same deck-policy bundle.
2. Task 4 owns `PackageTelemetry`; archived `main.py` must expose module-level `package_telemetry()` before the final `agent` binding. The specialist verifier calls it after real decisions and validates the exact Task 4 schema. It independently recomputes loaded checkpoint/static bytes and requires telemetry expected/loaded/bundle identities to agree. Correct but unused weights, different loaded bytes, missing/malformed telemetry, or missing counters are non-ready.
3. `BundleSpec.from_payload` / `load_bundle_spec` accepts one strict duplicate-key-free JSON object with exact local fields (`source_root`, sorted `members`, `deck_asset`, `deck_lock`, `runtime_constraints`, `policy_entrypoint_member`, identities/conditional policy fields, and dependency contract IDs) and rejects unknown/missing/noncanonical values. The three nested objects use the exact Task 2/4 public payloads and are independently reconstructed/revalidated; local paths never enter the archive manifest/content identity.
4. Script interfaces are exact: `python scripts/build_meta_specialist_submission.py --bundle-spec PATH --output PATH` and `python scripts/verify_meta_specialist_submission.py --archive PATH`. Success writes one JSON object to stdout; failure writes one sanitized JSON object to stderr. Exit codes are 0 success, 2 contract/security failure, 3 `BLOCKED_DEPENDENCY`, and 4 `BLOCKED_RESOURCE`. Neither script submits/uploads. Build can report `structurally_verified`; only verifier can report `submission_ready`.
5. Runtime contract values are imported from Task 4 v1: Python 3.11.11; verifier-only `kaggle-environments==1.32.0`; stdlib-only archived agent; p95 100 ms, p99 250 ms, decision hard 1,000 ms, game hard 300,000 ms, peak RSS 8,388,608 KiB, trace 4,096, outer two-seat 900,000 ms, and 100 final stress games balanced by seat. These are conservative local gates, not misreported official deadlines.
6. Archive resource parsing enforces the minimum of official disk, a private 1,073,741,824-byte local expanded-data ceiling, and current free space minus a 536,870,912-byte reserve; member count is at most 4,096 and each member at most the same private expanded ceiling. Host insufficiency/timeout is `BLOCKED_RESOURCE`, never ready. Hostile parsing/extraction runs in a bounded subprocess and tests monkeypatch small private constants rather than constructing huge fixtures.
7. Canonical archive order is exactly `tuple(sorted((*declared_member_paths, "meta_specialist_bundle.json")))`. The sensitive-path exception constant is an immutable empty set for P0.

- [ ] **Step 6C: Author and exercise the real packaged specialist entrypoint**

1. `templates/meta_specialist/main.py` is the repository-owned top-level archive entrypoint; synthetic hand-written `main.py` fixtures may test hostile inputs but cannot establish readiness. Its only bootstrap import is the archived `mage_ptcg.meta_specialist.entrypoint`. It obtains one `PackagedAgentBinding`, defines module-level `package_telemetry()` as a zero-argument delegate to that binding, and makes `agent = binding.agent` the final executable binding.
2. `src/mage_ptcg/meta_specialist/entrypoint.py` exposes `bootstrap_packaged_agent(root: Path) -> PackagedAgentBinding`. It reads only snapshotted files under the verified archive root, strictly reconstructs the archived `QualifiedDeckAsset`, `DeckLockDecision`, Task 4 `RuntimeConstraintManifest`, and policy loader, then calls Task 4 `make_agent`; no repository defaults, environment discovery, network, GPU, or external sidecar reads are allowed.
3. Local `BundleSpec` fields are exact typed `deck_asset: QualifiedDeckAsset`, `deck_lock: DeckLockDecision`, `runtime_constraints: RuntimeConstraintManifest`, and relative `policy_entrypoint_member: str`; the exact manifest schema carries their canonical `qualified_deck_asset`, `deck_lock`, and `runtime_constraints` payloads. The qualified asset includes every Task 2 field and must match `deck.csv`; the lock includes every Task 2 field and must select that exact deck; constraints independently match the Task 4 v1 payload/ID. `policy_entrypoint_member` is an archived regular Python member whose sole public loader is `load_policy(root, manifest) -> StepLogitPolicy`; its import/file closure is included in the declared archive and content manifest. Checkpoint identity remains the raw model hash; static policy identity covers the complete behavior-affecting closure as frozen in Step 6B.1.
4. Archive-only tests build from the real template and bootstrap for both candidate classes. They delete/poison the source checkout, load with `python -I`, require exact deck registration, at least one committed complete action, exact module telemetry from the policy actually used, and both-seat CABT evidence. Tamper each qualified-deck, lock, constraints, loader, and model/rule binding independently. A synthetic callable-only `main.py` can be structurally verified but is never `submission_ready`.

- [ ] **Step 6D: Close the R3 package/runtime/schema audit**

The complete literal Step 6D contract in
`.superpowers/sdd/2026-08-01-meta-specialist-p0-foundation/task-5-brief.md` is
incorporated here by reference and is authoritative over earlier shorthand.  Task 5
does not start until all of the following are true:

1. Task 2A removes the top-level `main` import through stdlib-only explicit deck I/O;
   every package boundary requires `usage_boundary == "bundle_allowed"`, sanitized
   bounded provenance, full qualified-deck/DeckLock revalidation, and checkpoint lineage
   exactly equal to `deck_lock.policy_lineage_id`.
2. The repository-owned loader returns Task 4 `StepLogitPolicyFactory` from an immutable
   `PackagedFileSnapshot`; Task 4 `make_agent` returns a single-game/seat
   `PackagedAgentBinding`, and every readiness game uses a fresh extracted tree/process,
   factory, policy, and runtime.
3. `stage_repository_specialist` and the checked-in
   `meta-specialist-entrypoint-contract-v1` bind exact template/loader/runtime/transitive
   closure bytes; full readiness, unlike structural verification, requires that trusted
   contract ID.
4. Local `meta-specialist-bundle-spec-v1` and archived
   `meta-specialist-bundle-manifest-v1` use the exact key sets, nested Task 1/2/4
   payloads, four named dependency IDs, canonical bytes, conditional nulls, member
   records, and domain-separated hashes frozen in Task 5 Step 6D.4--6D.5.  Relative
   source roots resolve against the spec file, never CWD.  Python `BundleSpec` uses the
   identical semantic names (`qualified_deck_asset`, no redundant standalone
   `entrypoint_contract_id`).  The CABT dependency preimage is the literal three-key
   payload with 49 lexicographically sorted `[type,context]` arrays and ordered
   `[[5,34]]`.
5. Structural `verify_specialist_archive` and operational
   `verify_specialist_submission` are separate.  Their exact reports and the sole
   `package_command_outcome` mapper use the schemas/statuses/streams and exit codes
   frozen in Step 6D.6; the Task 5 scripts and Task 6 delegate to that mapper without
   duplicating status logic.  Synthetic callability can never yield
   `submission_ready`, and blocked dependency/resource is never completion.
6. Every manifest carries the self-described entrypoint contract derived from its
   actual archive subset.  Structural verification proves that self-consistency; full
   readiness additionally compares the whole object with the trusted checked-in v1
   contract.  Thus a synthetic callable can be structural without falsely claiming the
   trusted ID.
7. Step 6D.7 freezes 100 ordered per-game evidence records, exact A/B batch schedule,
   trace-chain/evidence hash domains, nearest-rank integer quantiles, max-RSS aggregation,
   and byte oracles for all five command outcomes.  Reports contain archive hash/size,
   never a local path.  Step 6D.8 freezes variable static-policy identity versus trusted
   entrypoint identity and requires snapshot-backed transitive execution.

- [ ] **Step 7: Record Task 5 evidence without committing**

Record byte-identical archive hashes, strict manifest/deck/model/policy/lineage tamper tests, canonical tar/gzip and resource-bound security tests, import/file closure plus poisoned workspace/sidecar/source-deletion checks, script JSON/exit behavior, both-seat telemetry/readiness and 100-game gate evidence (or explicit blocked dependency/resource), legacy independence, collision/idempotence/partial cleanup, full Task 5 test count, `git diff --check`, and `git status --short`. Never label local evidence as Kaggle `validation_passed`.

### Task 6: Local JSON CLI and P0 Foundation Runbook

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cli.py`
- Create: `src/mage_ptcg/meta_specialist/__main__.py`
- Create: `tests/meta_specialist/test_cli.py`
- Create: `docs/runbooks/meta-specialist-p0-foundation.md`

**Interfaces:**
- Consumes: Tasks 1–5 public functions.
- Produces commands `show-ladder-contract`, `validate-deck`, `qualify-deck`, `lock-deck`, `build-submission`, and `verify-submission`.

- [ ] **Step 1: Write the failing CLI contract tests**

```python
import json
import os
import subprocess
import sys

def _run(*args: str):
    env = dict(os.environ, PYTHONPATH="src")
    return subprocess.run(
        [sys.executable, "-m", "mage_ptcg.meta_specialist", *args],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

def test_show_ladder_contract_emits_one_json_object() -> None:
    result = _run(
        "show-ladder-contract",
        "--checked-at-utc", "2026-08-01T00:00:00Z",
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["bundle_size_limit_kib"] == 202_400
    assert result.stderr == ""

def test_cli_has_no_submit_command() -> None:
    result = _run("--help")
    assert result.returncode == 0
    assert "submit-kaggle" not in result.stdout.lower()
```

- [ ] **Step 2: Run red and implement argparse routing**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_cli.py -q`
Expected: FAIL because `__main__.py`/`cli.py` do not exist.

Operational-command success writes exactly one canonical JSON object plus one newline to stdout; `--help` and subcommand help are the sole human-readable exit-0 exceptions. For `show-ladder-contract`, `validate-deck`, `qualify-deck`, and `lock-deck`, exit 2 writes exactly `{"status":"ERROR","error_type":...,"message":...}` to stderr; `error_type` is one of `ARGUMENT_ERROR`, `INPUT_ERROR`, `CONTRACT_ERROR`, or `SECURITY_ERROR`. `build-submission` and `verify-submission` instead delegate every success/error/block to Task 5 `package_command_outcome` and preserve its exact `meta-specialist-command-outcome-v1` bytes, stream, and exit code without rewrapping. Stdout is empty on every nonzero exit, stderr is empty on success, messages are one sanitized bounded line, and no traceback/raw bytes/environment/credentials are emitted. No command accesses the network, Git remote, or Kaggle submission API.

- [ ] **Step 3: Add command-specific tests and minimum handlers**

Exact inputs:

- `validate-deck --deck PATH --known-card-ids PATH` reads a strict newline-delimited known-ID fixture and emits deck identity, file SHA-256, and card count; it reports `operational_legality = "not_run"` and never calls this a production qualification.
- `qualify-deck --asset-json PATH --registry PATH --known-card-ids PATH --cabt-evidence-json PATH` consumes the exact deck-bound evidence contract in Step 3A and emits the qualified payload.
- `lock-deck --decision-json PATH` accepts only the exact fields of `create_deck_lock` and emits IDs.
- `build-submission --bundle-spec PATH --output PATH` builds locally and emits only the exact Task 5 structural report (archive hash and compressed size, never a local path).
- `verify-submission --archive PATH` verifies locally and emits the report.

- [ ] **Step 3A: Freeze strict inputs, evidence binding, and exact operational outputs**

1. The common JSON reader accepts one strict UTF-8 top-level object, rejects duplicate keys, NaN/Infinity, unknown/missing recursive fields, and wrong exact types, and never reads stdin/environment/repository defaults. The writer uses `sort_keys=True`, compact separators, `ensure_ascii=False`, `allow_nan=False`, and exactly one trailing LF. A custom parser converts unknown command, missing/extra argument, and invalid choice to the exit-2 JSON contract without usage text.
2. `asset-json` exact keys are `asset_id`, `archetype_id`, `path`, `source_ref`, `source_commit`, `asset_class`, `usage_boundary`, `policy_compatibility`, and `card_database_version`. The archetype is a canonical runtime ID, never silently alias-normalized. Its relative `path` resolves against the asset JSON parent; CLI option paths resolve against process CWD. No expanduser, environment expansion, glob, or repository fallback is allowed.
3. CABT evidence exact v1 keys are `schema_version="meta-specialist-cabt-deck-evidence-v1"`, exact `passed is True`, `deck_identity`, `deck_file_sha256`, `card_database_version`, `cabt_runtime_version="kaggle-environments==1.32.0"`, and nonempty `evidence`. Before qualification, the CLI snapshot/recomputes the asset's canonical deck identity and raw SHA once and matches all evidence bindings. The success payload includes `cabt_evidence_sha256`, computed over the accepted canonical evidence bytes.
4. Known-card files are strict UTF-8 newline-delimited canonical unsigned positive decimal integers: no blank/whitespace-padded/signed/duplicate/zero/bool-like/invalid tokens. Their raw file SHA-256 is persisted as `known_card_ids_file_sha256` in validate/qualify output.
5. Exact success schemas: `validate-deck` has `schema_version="meta-specialist-validate-deck-cli-v1"`, `status="STRUCTURAL_VALIDATION_ONLY"`, `deck_identity`, `deck_file_sha256`, `card_count`, `known_card_ids_file_sha256`, and `operational_legality="not_run"`. `qualify-deck` has `schema_version="meta-specialist-qualify-deck-cli-v1"`, `status="QUALIFIED"`, every `QualifiedDeckAsset` public field including the 60-card array, plus `cabt_evidence_sha256` and `known_card_ids_file_sha256`; local path is absent. `lock-deck` input is exactly the six `create_deck_lock` arguments and output has `schema_version="meta-specialist-lock-deck-cli-v1"`, `status="LOCKED"`, those six canonical fields, `deck_lock_id`, and `policy_lineage_id`.
6. `show-ladder-contract` returns the exact Task 1 payload without an envelope, validates strict RFC 3339 UTC syntax, and documents that `checked_at_utc` is caller-recorded provenance rather than a live network check. Build success is at most `structurally_verified`; only the full Task 5 verifier may emit `submission_ready`, and neither may emit Kaggle `validation_passed`.

- [ ] **Step 4: Run CLI tests green**

Run: `PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist/test_cli.py -q`
Expected: PASS for exact schemas; malformed UTF-8/JSON; duplicate/unknown/missing fields; NaN/Infinity; wrong strict types; missing/nonregular/symlink inputs; CABT deck/hash/card-DB/runtime mismatch; absent evidence; argparse errors; and absence of any submission command.

- [ ] **Step 4A: Preserve Task 5 outcomes and prove side-effect boundaries**

`build-submission` calls `build_specialist_archive` once and maps the resulting
`StructuralVerificationReport` with the exact public
`package_command_outcome("build-submission", result_or_error)`. `verify-submission`
calls `verify_specialist_submission` once and maps its report/block/error with the same
function and command string. They never invoke `verify_specialist_archive` a second
time, assemble a shell/subprocess invocation, duplicate status logic, or call
private/legacy builders. For the same fixture they are byte-equivalent to the standalone
scripts in canonical payload bytes, stdout/stderr placement, and exit code: 0 success,
2 contract/security/argument/internal failure, 3 blocked dependency, 4 blocked
resource. The exact payload has only `schema_version`, `command`, `status`, `report`,
and `error` as frozen in Task 5 Step 6D.6. Blocked is correctly reported by a passing
unit test but is never counted as foundation readiness. AST closure and fail-on-call
spies cover socket/network, Kaggle client/upload, Git remote, subprocess, deletion, and
legacy Rule/Student builders for all commands and error paths. Task 5 owns archive
snapshot/TOCTOU/collision/temp cleanup and its required internal isolated subprocesses;
Task 6 neither blocks those internals nor weakens them with a second read or overwrite.

- [ ] **Step 5: Write the exact runbook**

The runbook starts with the exact design invariant “Gold/Silver/Bronze are source/curriculum labels, never runtime selectors,” names the five exact runtime IDs and primary/replacement order, shows all six commands with concrete non-user fixture paths, and includes minimal strict contents for asset, known-ID, CABT evidence, lock decision, and bundle-spec inputs. It separates the 2026-08-01 official snapshot (source URL, UTC/JST deadline, bundle/CPU/RAM/disk/root/slot limits) from conservative local Python/CABT/latency/RSS/100-game gates, and tells the human to recheck official UI before upload. It documents exit 0/2/3/4, stdout/stderr, `structurally_verified`, `submission_ready`, blocked outcomes, and the manual-only lifecycle:

```text
draft -> submitted -> validation_passed -> active_confirmed -> final_selected
                 \\-> validation_failed
```

It explicitly says `show-ladder-contract` is not a live query; local smoke or 100 games cannot create `validation_passed`; active IDs and final IDs are separate arrays capped at two; P0 has no lifecycle-mutation command; and upload/active/final selection is performed by a human who records Task 1 evidence outside this CLI. It states this is only the six-command P0 subset, reads no credentials, consumes no daily slot, guesses no submission ID, and never deletes o6/dirty-worktree/remote-branch/user artifacts or performs commit/push/PR/submission.

- [ ] **Step 6: Run the full foundation suite and static checks**

Run:

```bash
PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m pytest tests/meta_specialist -q
PYTHONPATH=src /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python -m mage_ptcg.meta_specialist --help
rg -n 'rl\\.deck_finetune|target-tier|Bronze model|Silver model|Gold model' src/mage_ptcg/meta_specialist configs/meta_specialist docs/runbooks/meta-specialist-p0-foundation.md
git diff --check
```

Expected: all tests PASS; help exits 0; `rg` returns no matches; `git diff --check` returns no whitespace errors.

- [ ] **Step 7: Record Task 6 evidence without committing**

Record the full command, exit code, exact small stdout/stderr JSON or its SHA-256, test count, deterministic fixture archive hash, standalone-script equivalence, blocked status if any, and current `git status --short` in the SDD report. Do not label this Kaggle validation/submission evidence. Do not commit, push, create a PR, delete artifacts, or submit to Kaggle.

## Plan Boundary and Next Plans

This plan ends when the fixed-deck specialist foundation is locally verified. It deliberately does not claim a trained policy or statistical promotion. The next implementation plans consume these exact interfaces in this order:

1. `2026-08-01-meta-specialist-census-calibration.md` — sealed census, fixed reference panel, cross-play matrix, `ambiguous` band, and `pool_epoch`.
2. `2026-08-01-meta-specialist-vtrace-curriculum.md` — complete-action trajectories, Rule-BC foundation, recurrent V-trace, resume, DeckLock-bound lineage, and exposure-matched curriculum.
3. `2026-08-01-meta-specialist-global-evaluation.md` — `SealedScenarioBank`, `CandidateSetManifest` binding, the unique sequential/Holm alpha plan, band safety, Global Race, and primary/backup decision.
