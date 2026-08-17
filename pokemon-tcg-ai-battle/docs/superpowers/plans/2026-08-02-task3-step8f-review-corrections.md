# Task 3 Step 8F Review Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development for every behavior change. This delegated task is executed inline and must not commit, stage, or push.

**Goal:** Correct Task 3 against the frozen `kaggle-environments==1.32.0` agent-JSON contract and close the independent ActionKey, adapter, enumeration, and probability findings.

**Architecture:** Treat agent JSON enums as a checked-in versioned contract consumed by both `DecisionState` and complete-action adaptation. Keep actor ActionKey v2 identity verifiable and separate from a recursively validated public projection; legacy v1 artifacts enter only through explicit feature-reader boundaries. Existing callers that lack full public-board state return a tested neutral/fail-closed result for ToolCard.

**Tech Stack:** Python 3.11 standard library, pytest, existing `mage_ptcg.decision_state` contracts.

## Global Constraints

- Strict RED→GREEN for every production behavior change.
- Agent JSON SkillOrder is `(5,34)` and SpecialCondition is select type `10`.
- Maximum legal option count is 60.
- No Task 4+ edits, no commit/stage/push, and no R2D3 integration.
- Use `apply_patch` for edits.

---

### Task 1: Frozen Agent-JSON Order Contract

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cabt_json_contract_v1.py`
- Modify: `src/mage_ptcg/meta_specialist/actions.py`
- Modify: `src/mage_ptcg/decision_state.py`
- Modify: `tests/meta_specialist/test_cabt_action_identity.py`
- Modify: `tests/meta_specialist/test_action_properties.py`

**Interfaces:**
- Produces `resolve_order_semantics(type, context)` for all allowed JSON pairs.
- `DecisionEnvelope.from_decision_state(..., order_semantics=None)` derives semantics and treats a supplied value as an assertion only.

- [x] Add table-driven tests for `(0,0)`, ranges through `(10,48)`, ordered `(5,34)`, unknown rejection, and caller downgrade rejection.
- [x] Run the two test files and verify failures show the current 6/11/native mapping and caller-controlled mode.
- [x] Add the versioned mapping and change Skill/SpecialCondition validation to JSON types 5/10.
- [x] Derive semantics inside `from_decision_state` and rerun the focused tests green.

### Task 2: Verifiable ActionKey v2 and Explicit v1 Feature Boundary

**Files:**
- Modify: `src/mage_ptcg/decision_state.py`
- Modify: `src/mage_ptcg/student/model.py`
- Modify: `src/mage_ptcg/policy_learning/data.py`
- Modify: `tests/meta_specialist/test_cabt_action_identity.py`
- Modify: affected student/policy tests.

**Interfaces:**
- Every v2 `ActionKey` serializes an explicit version and verifies its domain-separated digest from the actor payload.
- Direct public payload injection recursively rejects raw/actor/digest/index keys and malformed typed shapes.
- Legacy readers explicitly construct feature-only v1 keys; v1 keys are rejected by `DecisionState` and `DecisionEnvelope`.

- [x] Add direct-constructor injection tests for nested `cardId`, `serial`, actor payload, digest, and option-index aliases; add bad digest/version and mixed-v1/v2 tests.
- [x] Run RED and confirm the current unchecked constructor/fallback is exercised.
- [x] Centralize public-payload validation and v2 digest recomputation in `ActionKey.__post_init__`; add explicit legacy feature constructors/readers.
- [x] Rerun identity, student, and policy tests green.

### Task 3: ToolCard Caller Compatibility

**Files:**
- Modify: `src/mage_ptcg/observability/cabt_trace.py`
- Modify: `src/mage_ptcg/knowledge/adapter.py`
- Modify: `src/mage_ptcg/distillation/actor_visible_attestation.py`
- Modify: relevant observability, knowledge, and distillation tests.

**Interfaces:**
- Full observations use verified `DecisionState` public ActionKeys where available.
- Callers without enough board state return an explicit neutral/fail-closed result for option type 4 and never call unresolved ToolCard construction.

- [x] Add one type-4 regression per caller that exercises its public boundary and asserts no exception/private fallback identity.
- [x] Run RED and verify each fails at `ToolCard requires a verified public host locator`.
- [x] Route or neutralize each caller with the smallest compatible change.
- [x] Rerun all affected adapter suites green.

### Task 4: Bounded Enumeration and Positive Finite-Logit Support

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/actions.py`
- Modify: `tests/meta_specialist/test_actions.py`

**Interfaces:**
- Envelopes reject more than 60 legal candidates.
- Counting stops at `limit + 1` without calculating later terms.
- Distribution raises `CompleteActionProbabilityError` if any finite-logit legal action exponentiates to zero.

- [x] Add 60-candidate combination/permutation tests with `limit=1`, a 61-candidate envelope rejection, and a finite extreme-logit underflow test.
- [x] Run RED against exact counting/unbounded candidates/zero support.
- [x] Implement capped counting and explicit underflow rejection.
- [x] Rerun the action tests green.

### Task 5: Fixture, Provenance, and Final Evidence

**Files:**
- Create: `tests/meta_specialist/fixtures/cabt_1_32_0_skill_order.json`
- Create: `docs/evidence/cabt-agent-json-selection-contract-v1.md`
- Modify: `.superpowers/sdd/2026-08-01-meta-specialist-p0-foundation/task-3-report.md`

**Interfaces:**
- Fixture records the captured `(type=5, context=34, option.type=15)` shape.
- Evidence records the official API URL and local two-game `1.32.0` probe facts without claiming unmeasured behavior.

- [x] Check in the literal fixture and make the runtime mapping test consume it.
- [x] Record provenance, agent/native boundary correction, and all Step 8F RED→GREEN outputs.
- [x] Run the previous 206 tests plus observability/knowledge/distillation suites, compileall, and targeted `git diff --check`.

### Task 6: R2 Privacy and Resource-Bound Review Closure

**Files:**
- Modify: `src/mage_ptcg/decision_state.py`
- Modify: `src/mage_ptcg/distillation/contracts.py`
- Modify: `src/mage_ptcg/meta_specialist/actions.py`
- Modify: `src/mage_ptcg/student/model.py`
- Modify: `tests/meta_specialist/test_cabt_action_identity.py`
- Modify: `tests/meta_specialist/test_actions.py`
- Modify: `tests/test_targeted_distillation_v0.py`

**Interfaces:**
- ActionKey v2 binds its canonical public projection as well as exact actor identity.
- C5 accepts/validates only public ActionKey v2 projections.
- Generic persisted fields are a closed per-option typed union.
- Every complete-action enumeration entry point enforces a hard `65,536` ceiling.

- [x] Add RED regressions for direct/deserialized official actor forgery, bool metadata, unchanged-digest public tampering, schema-version bool aliasing, generic alias/per-option field injection, unknown option types, rehashed C5 injection, v1 C5 promotion, and all enumeration entry points.
- [x] Confirm the expanded suite is RED for the intended reasons: `35 failed, 82 passed`.
- [x] Include canonical `public_identity_payload` in the v2 hash core and validate exact Skill/SpecialCondition/ToolCard actor unions on every constructor path.
- [x] Replace arbitrary generic public fields with a per-option allowlist, reject unknown types, and keep recursive raw/private/digest/current-index denial.
- [x] Remove the C5 v1/schema-less persistence branch and validate every candidate public payload before recalculating its public ID.
- [x] Enforce a positive non-bool enumeration limit no larger than `65,536` before counting/materialization in enumeration, distribution, and Q entry points.
- [x] Remove the unused Student `_action_key` compatibility reader and imports.
- [x] Run focused, compatibility, clean-room, compile, and whitespace gates and refresh the Task 3 evidence report.

### Task 7: R4 Public Identity, C5 Context, and Closed Envelope Closure

**Files:**
- Modify: `src/mage_ptcg/decision_state.py`
- Modify: `src/mage_ptcg/distillation/contracts.py`
- Modify: `src/mage_ptcg/meta_specialist/actions.py`
- Modify: `src/mage_ptcg/student/features.py`
- Modify: `src/mage_ptcg/knowledge/adapter.py`
- Modify: Task 3 / evidence documents and focused regressions

**Interfaces:**
- Exported ActionKey construction derives public identity itself; only the private
  decision-state builder may pass a raw-board-resolved locator.
- C4 feature readers are structural and non-persistable.  C5 persistence validates
  non-redacted locator membership against the exact C1 public observation.
- `canonical-decision-v1` is a closed 20-field envelope with exact nested shapes.

- [x] Capture RED for caller-selected public identity, rehashed generic value substitution,
  non-redacted serialized Skill without public resolution, open C5 containers/cross-field
  mismatches/duplicate IDs, and zero-option trace persistence.
- [x] Remove the public builder injection path; bind generic public fields to actor identity;
  add private resolved construction and exact Skill `card_ref` / Tool host membership checks.
- [x] Split structural feature validation from `validate_persistable_public_action_payload`;
  preserve C4/C5 feature-vector compatibility without treating either as origin evidence.
- [x] Close C5 top-level/nested envelope shapes, selection/candidate/rule/student/C3/provenance
  cross-field checks, and duplicate public action rejection; reject Python `bool` where it could
  compare equal to an integer.
- [x] Add attachment/stadium Skill redaction and zero-option trace regressions; verify focused,
  compatibility, clean-room, compile, and whitespace gates before recording R4 hashes.

### Task 8: R5 Ordered Labels, Feature Domains, and Non-Persistable Readers

**Files:**
- Modify: `src/mage_ptcg/meta_specialist/cabt_json_contract_v1.py`
- Modify: `src/mage_ptcg/decision_state.py`
- Modify: `src/mage_ptcg/distillation/contracts.py`
- Modify: `src/mage_ptcg/distillation/selection.py`
- Modify: `src/mage_ptcg/student/dataset.py`
- Modify: `src/mage_ptcg/student/features.py`
- Modify: `src/mage_ptcg/student/model.py`
- Modify: `src/mage_ptcg/student/runtime.py`
- Modify: `src/mage_ptcg/student/artifact.py`
- Modify: focused Task 3 regressions only as required

**Interfaces and invariants:**
- One frozen helper identifies whether an exact `(selection_type, context)` is
  ordered.  C5 preserves selected-ID sequence for `(5,34)` and canonicalizes
  selected IDs for every unordered schema.
- `legal_actions` and score-ranking rows have no raw option-order semantics and
  are stored in canonical `action_id` order.  This does not alter the selected
  sequence of an ordered action.
- Legacy candidate-wise C4 conversion/training rejects ordered SkillOrder
  examples because it cannot represent a permutation label.  The new specialist
  complete-action model is the supported ordered-action consumer.
- A Student fallback is an exact tagged union: fallback carries empty selection
  and scores; a non-fallback carries a legal selection and exact score coverage.
- Model artifacts bind a feature domain.  C5-trained public models use the same
  public action payload and public action ID at runtime; legacy private models
  remain explicitly private-domain or are rejected when the domain is absent or
  incompatible.
- A context-free serialized v2 feature reader never returns a persistable
  `ActionKey`.  It returns a distinct immutable feature view or directly emits a
  vector, and cannot enter `DecisionState`, `DecisionEnvelope`, or trace APIs.
- Serialized generic v2 actor fields are closed to the builder-reachable union
  and both entity keys are recomputed exactly.  Official typed unions remain
  exact.  Legacy v1 feature serialization round-trips through its explicit
  feature-only reader.
- C1 and C5 public observations use exact scalar types: card IDs/serials/counts/
  HP values and temporal counters are strict non-bool integers in their valid
  ranges; `playerIndex`, result, and board/card booleans use their exact domains.
- A non-redacted Skill locator is persistable only when its `(id, serial)` pair
  occurs exactly once across the public active/bench/discard projection.
- C5 rejects all POSIX absolute paths, Windows drive-rooted paths, backslash path
  separators, and `file://` values while accepting ordinary revision IDs.

- [x] Add RED regressions for reversed ordered `[B,A]` C1→C4→C5 preservation,
  altered-order rejection, unordered canonicalization, example-ID uniqueness,
  and sequence-aware selection disagreement.
- [x] Add RED regressions for canonical legal/ranking order and for the exact
  Student fallback/non-fallback union.
- [x] Add RED regressions proving public C5 feature vectors equal live runtime
  vectors for generic, Skill, ToolCard, and redacted Skill identities, and that
  mixed/unknown feature domains fail closed.
- [x] Add RED regressions proving a context-free v2 feature artifact cannot be
  inserted into `DecisionState`/`DecisionEnvelope` or emit a public trace.
- [x] Add RED regressions for generic actor-field injection, forged entity keys,
  ambiguous Skill locators, strict C1/C5 scalar types, path variants, and legacy
  v1 round-trip.
- [x] Replace the v2 feature-reader capability bypass with a distinct
  non-persistable feature representation; close generic actor identity and
  rederive entity metadata.
- [x] Implement order-aware C5 labels, canonical candidate/ranking storage,
  tagged Student fallback, strict public schemas, and global unique Skill
  membership.
- [x] Bind Student model/artifact/runtime feature domains and reject ordered C5
  conversion at the legacy candidate-wise boundary.
- [x] Replace the flaky numeric-substring privacy test with recursive exact
  scalar/key assertions so timing/digest text cannot trigger false failures.
- [x] Run focused, broad compatibility, clean-room, compileall, and diff-check
  gates.  Record exact commands, counts, and hashes before requesting a fresh
  independent review.
