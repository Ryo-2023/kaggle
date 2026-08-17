# Task 4 C1 v2 Runtime Correction Plan

> **Status:** Authoritative over Task 4 in
> `2026-08-01-meta-specialist-p0-foundation.md` wherever that plan assumes the
> frozen public-v1 `DecisionEnvelope` is the production decision boundary.

**Goal:** Execute all validated C1 v2 decisions, including the 597/936 pinned
decisions with duplicate public identities, through one CPU-only,
transactional, complete-action runtime without exposing local action IDs,
card serials, private digests, or current CABT indices in persisted telemetry.

**Architecture:** Build the observation exactly once as
`ActorVisibleDecisionStateV2`, extract the frozen serial-free model input once,
and wrap both in `RuntimeDecisionEnvelope`.  The policy scores semantic action
classes through `SpecialistStepLogitPolicyV1`; physical aliases are chosen only
after class selection.  One complete selection is one environment action and
one recurrent commit.  Public-v1 conversion is an optional trace projection,
never the execution prerequisite.

## Files

- Create: `src/mage_ptcg/meta_specialist/runtime.py`
- Create: `tests/meta_specialist/test_runtime.py`
- Create: `tests/meta_specialist/test_runtime_privacy.py`
- Create: `tests/meta_specialist/test_runtime_cabt.py`
- Modify only if a failing test proves it necessary:
  `src/mage_ptcg/meta_specialist/runtime_actions_v2.py`

## Frozen dependencies

- `build_actor_visible_decision_state_v2`
- `RuntimeDecisionEnvelope`
- `greedy_decode_runtime_action_v2`
- `semantic_runtime_complete_action_from_runtime_action_v2`
- `runtime_semantic_complete_action_log_probability_v2`
- `CardVocabularyV1`
- Task 2 `QualifiedDeckAsset` and `DeckLockDecision`

The production runtime must not import R2D3, o6, `continuous_league.collector`,
`optimization.deck_specialist`, torch, CUDA, network clients, subprocess, or a
single-index action adapter.

## 1. Policy transaction

Define runtime-checkable protocols:

```python
class SpecialistDecisionPolicyV2(Protocol):
    def reset(self) -> None: ...
    def begin_decision(self) -> SpecialistDecisionSessionV2: ...
    def policy_telemetry(self) -> PolicyTelemetrySnapshot: ...

class SpecialistDecisionSessionV2(SpecialistStepLogitPolicyV1, Protocol):
    def commit(self, outcome: CommittedSemanticDecisionV2) -> None: ...
    def abort(self) -> None: ...
```

- `begin_decision` may read committed recurrent state but cannot mutate it.  It
  takes no envelope/state argument: the session receives the frozen serial-free
  `SpecialistModelInputV1` only through its `logits` protocol call, so the
  policy never receives the private runtime envelope.
- `logits(model_input, step_input)` is pure and cached by the exact canonical
  model-input ID plus canonical step-input bytes.  Greedy decode and semantic
  log-probability replay must reuse the same cached values; a spy must observe
  one inference per distinct prefix.
- `commit` is no-throw and atomic, called exactly once after every other
  runtime validation and state allocation succeeds.  `abort` is idempotent and
  no-throw.  Once a session exists, every pre-commit failure calls `abort`
  exactly once.  Commit and abort never both occur.
- `CommittedSemanticDecisionV2` is frozen, serial-free, and contains only the
  semantic complete action, its finite nonpositive log probability, and the
  next recurrent-state token owned by the policy.  It contains no envelope,
  local ID, ActionKey digest, card serial, or CABT index.

## 2. Strict runtime state machine

`MetaSpecialistRuntime.__call__(observation, configuration=None)` has these
states and no ambient game-boundary inference:

1. Before registration, an exact mapping with explicit `select is None`
   returns a fresh copy of the locked 60-card deck.
2. A second or terminal null callback returns a fresh `[]` and never redelivers
   or resets the deck.
3. A decision before registration fails closed.
4. Missing `select`, nonmapping observation/select, non-list options, bool or
   non-int type/context/bounds, negative/inconsistent bounds, and an unknown
   selection schema fail with `RuntimeContractError`.
5. `options=[]`, `min=max=0` is one legal complete action, produces `[]`, and
   commits exactly one decision/trace.

Construction is keyword-only and accepts an exact qualified deck, its exact
DeckLock decision, an immutable card vocabulary, one policy, expected policy
identity, and exact runtime constraints.  It recomputes deck/file/multiset and
lineage bindings rather than trusting a caller claim.

## 3. Per-decision transaction

For each non-null callback:

1. Validate callback structure without opening a policy session.
2. Build `ActorVisibleDecisionStateV2` exactly once.
3. Build `RuntimeDecisionEnvelope` exactly once with the locked vocabulary.
4. Open one policy session.  The decoder supplies the envelope's frozen
   serial-free model input through the session's `logits` call; runtime code
   does not expose the private envelope or reach into its private fields.
5. Decode one semantic complete action with
   `greedy_decode_runtime_action_v2`; P0 production has no exact complete-action
   enumeration.  An optional bounded beam belongs to offline evaluation and is
   not a hidden runtime default.
6. Revalidate issued-action provenance and current option indices.
7. Erase physical aliases into `SemanticRuntimeCompleteActionV2`.
8. Compute the semantic log probability with the same cached session; require
   finite and `<= 0`.
9. Build the privacy-safe trace and the complete next bounded runtime snapshot.
10. Commit the semantic outcome exactly once, then install counters/trace and
    return a fresh list of current indices.

Any error leaves success counters, trace, deck-delivery state, and committed
recurrent state unchanged.  A typed diagnostic counter may be incremented only
after classifying and rethrowing the error.  There is no implicit fallback;
only a typed policy result may increment `fallback_count`.

For unordered schemas, emitted indices use the envelope's validated numeric
execution order.  For `(selection_type=5, selection_context=34)`, emitted
indices preserve semantic SkillOrder.  Never sort ordered indices.

## 4. Trace projection with collisions

`RuntimeDecisionTraceV2` is frozen/slots/redacted and has one of three exact
closed variants:

- `public-v1-representable`: the explicit
  `RuntimeDecisionEnvelope.convert_to_public` path succeeds, and the trace
  contains the frozen public decision/action projection.
- `duplicate-public-identity`: public candidate identities collide; persist
  only authoritative selection schema/bounds/order, selected count, sorted
  collision-group sizes, policy metadata, and semantic complete-action log
  probability.
- `public-v1-option-limit-exceeded`: candidate count exceeds frozen public-v1
  limit 60; persist only the same aggregate fields plus candidate count.

The latter two variants must not persist selected semantic rows, own-hand card
IDs, ActionKey/local IDs, actor bindings, card serials, production/private
digests, raw observations, or option indices.  They must not fabricate a C5
record or public selection identity.  `representable` is not inferred merely
from collision absence: the explicit bridge must succeed.

All variants use schema `meta-specialist-runtime-decision-trace-v2`, exact
built-in scalar types, a lowercase SHA-256 policy identity, candidate class,
selected count, authoritative type/context/min/max/order semantics, finite
nonpositive semantic log probability, and no timing in trace identity.  Trace
capacity is exactly 4,096; overflow increments a dropped count.  `traces`
returns an immutable tuple snapshot.

Privacy tests must recursively reject key aliases for raw observation/private
state, card ID/serial, actor payload/binding, local/action/decision digest,
stable key, and option/current index.  Test actual private sentinel values and
full 64-hex digests, not bare digits.

## 5. Policy/package telemetry and constraints

Retain Task 4 Steps 7B-7D from the foundation plan with these corrections:

- `legal_decision_count == legal_action_count` counts committed complete
  selections, never selected elements.
- `checkpointed_specialist` requires model loaded, exact policy identity, and
  one DeckLock lineage.  `static_rule_bundle` has no checkpoint lineage and
  uses exact reason `not_applicable_static_policy`.
- Runtime constraints remain the frozen conservative v1 values and are
  independently recomputed by the package verifier.
- `make_agent` creates exactly one fresh policy/runtime binding per game/seat.
  No state crosses bindings; a second registration does not silently reset.
- Runtime code remains stdlib plus approved pure local modules and performs no
  network, subprocess, GPU, or filesystem discovery.

## 6. TDD acceptance sequence

1. RED registration/state-machine tests, then minimal locked-deck runtime.
2. RED zero-option, unordered multi-select, and ordered SkillOrder tests, then
   the C1 v2 envelope transaction.
3. RED session cache/commit/abort/rollback spies, then transactional policy
   integration.
4. RED trace-variant/privacy tests covering representable, duplicate-public,
   and 61/64/67-option decisions, then trace implementation.
5. RED option-permutation property: compare selected local semantics privately
   while allowing current indices to change; never serialize those oracles.
6. RED factory isolation and telemetry/constraint tests.
7. Real two-seat CABT protocol smoke with fresh bindings and a genuinely
   qualified legal deck.  Dependency absence is `BLOCKED_DEPENDENCY`, never a
   pass.
8. Run the C1/features/runtime-actions/local-dataset frozen suites, compileall,
   AST import closure, and `git diff --check`.
9. Require an independent adversarial privacy/correctness review before Task 5
   package readiness can consume this runtime.

## Acceptance gates

- All 936 pinned decisions remain executable at the C1 v2/runtime-action
  boundary; public collisions never trigger a policy fallback.
- The 61/64/67-option tail is accepted privately without exact enumeration.
- One callback equals one complete environment action and one recurrent commit.
- Greedy decode and logged probability share exactly the same class-logit
  domain and cached values.
- Every returned index is current/legal/unique; SkillOrder is preserved.
- No persisted trace or repr exposes private aliases, card serials, private
  card identities, raw observations, production digests, or current indices.
- Source smoke has two `DONE` games, balanced seats, zero invalid/crash/timeout,
  and no max-step truncation, or is explicitly blocked by a missing external
  dependency.
