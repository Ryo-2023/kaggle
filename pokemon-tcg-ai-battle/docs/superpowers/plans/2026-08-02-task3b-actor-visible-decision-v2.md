# Task 3B Actor-Visible Decision V2 Implementation Plan

> **For implementation agents:** Use strict test-driven development for every
> behavior change.  Do not commit, stage, push, submit, or delete unrelated
> artifacts.  Use `apply_patch` for edits.

**Goal:** Add the actor-visible, privacy-separated decision and local-training
boundary needed by the specialist policy.  Preserve the existing C1 v1 and C5
v1 bytes and public-action rules, while allowing normal gameplay decisions with
duplicate public projections to train and run through private local identities.

**Why this precedes Task 4:** On the pinned 936-decision telemetry corpus, C1 v1
accepts 936/936 decisions but C5 v1 represents only 339.  The other 597 (63.8%)
contain legitimate many-private-to-one-public action projections.  A public-only
training/runtime path would discard most real decisions and force avoidable
fallbacks.

**Primary evidence:**

- CABT API:
  `/home/bfe-lab-ono/kaggle/handoff-artifacts/canonical-champion-rebaseline-v1/_work/main_archive/cg/api.py`
- API SHA-256:
  `593f1298e52a635f90f8f505a52113e9af114f444c293404e37906f18ee06ced`
- Telemetry:
  `/home/bfe-lab-ono/kaggle/handoff-artifacts/family-agent-activation-remediation-v1/artifacts/turn_telemetry.jsonl`
- Telemetry SHA-256:
  `de6091a5724334e431d7e3858c9bdc27b046001911ebf912b2a25c34f92e14be`

## Non-negotiable boundaries

- C1 v1, ActionKey v2, `canonical-decision-v1`, and public `DecisionEnvelope`
  remain backward compatible.  Do not widen or relabel a v1 payload.
- A complete C5 v1 record is **actor-private local audit data** because it
  contains `own_private_state.hand_card_ids`.  Only
  `DecisionState.to_trace_payload()` is the truly public v1 trace projection.
- Actor hand, `select.deck`, `current.looking`, `contextCard`, `effect`, raw
  ActionKey actor payloads, private IDs, serials, and execution indices never
  enter public traces or submission telemetry.
- The official raw `Pokemon` type has no required `playerIndex`.  Pokémon owner
  is derived from its containing player/zone.  If a future/telemetry extension
  supplies `playerIndex`, validate it against the derived owner but accept its
  absence.  Base `Card` values still require `id`, `serial`, `playerIndex`.
  Because frozen C1 v1 preserves that optional wire extension when present,
  C1v2 also retains an explicit non-learned **presence bit** so its pure v1
  adapter can reproduce historic bytes; the extension value never supplies the
  semantic owner.
- Unknown raw observation fields are ignored without traversal; all emitted
  v2 payloads are exact closed shapes.  Opponent hand and prize contents are
  never traversed.
- Runtime candidates may share a public action ID, but private action IDs and
  ActionKey digests must be globally unique inside one decision.  Option ordinal
  is never used to manufacture identity.
- `(selection_type=5, context=34)` is the sole ordered schema.  Every other
  frozen schema is unordered.

## Frozen names and domains

- C1 schema version: integer `2`
- Actor-visible selection schema:
  `actor-visible-selection-v1`
- Actor-visible binding schema:
  `actor-visible-action-binding-v1`
- Local record schema:
  `canonical-specialist-decision-v2-local`
- Local dataset manifest schema:
  `canonical-specialist-dataset-v2-local`
- Source-permission manifest schema:
  `specialist-source-permission-v1`
- Specialist model manifest schema:
  `specialist-v2-model-manifest-v1`
- Learned feature domain:
  `actor-visible-action-v1`
- Card-vocabulary schema: `specialist-card-vocabulary-v1`
- Model-input schema: `specialist-model-input-v1`
- Step-input schema: `specialist-step-input-v1`
- Semantic-action schema: `specialist-semantic-action-v1`

Every new hash uses the same closed primitive:

```text
H(domain, core) = lowercase_hex(
  SHA256(UTF8(domain) || 0x00 || canonical_json_utf8(core))
)
```

`canonical_json_utf8` means `ensure_ascii=false`, `allow_nan=false`, sorted
object keys, separators `(',', ':')`, UTF-8, no BOM.  Parsers reject duplicate
object keys and non-finite constants before canonicalization.  A hash core never
contains the hash being derived or any later-derived ID.  No v1 domain is reused.

The following derivation order and domains are frozen.  “Exact section” means
the closed serializer defined in Task 5; unknown keys are not silently omitted.

| ID | Domain | Exact core / exclusions |
|---|---|---|
| `action_key_digest` | existing ActionKey v2 domain | Frozen existing implementation; never redefined here. |
| `public_action_id` | existing public-action v1 domain | Frozen public ActionKey payload; many-to-one is legal. |
| `local_action_id` | `mage_ptcg:actor-visible-local-action:v1` | `{action_key_digest, binding_core}`; `binding_core` contains no derived ID. |
| `information_state_id` | `mage_ptcg:specialist-information-state:v1` | Exact local `information_state` and `selection`; excludes only `observed_result` and derived identity paths (`local_action_id`, `action_key_digest`, `public_action_id`, decision/model/record/content/source/provenance IDs). Semantic gameplay IDs such as `card_id`, attack ID, Skill card ID, vocabulary token/card ID remain in the core. |
| `model_input_id` | `mage_ptcg:specialist-model-input:v1` | `{feature_domain, feature_schema_hash, model_input}` from the shared extractor; no local-ID lookup map. |
| `decision_id` | `mage_ptcg:specialist-decision:v2` | `{information_state_id, selection_contract, sorted_local_action_ids}`. |
| `complete_action_id` | `mage_ptcg:specialist-complete-action:v1` | `{decision_id, order_semantics, selection}`; ordered selection is a sequence, unordered selection is sorted unique IDs. |
| `near_duplicate_id` | `mage_ptcg:specialist-near-duplicate:v1` | `{feature_domain, feature_schema_hash, model_input}`; therefore excludes serial, locators, local/action digests, episode/source/provenance and labels. |
| `episode_id_hash` | `mage_ptcg:specialist-episode:v1` | Source-qualified episode core; raw episode/game IDs are never persisted. |
| `record_id` | `mage_ptcg:specialist-record:v2` | `{decision_id, episode_id_hash, decision_index}`; excludes source row order, `record_id`, and `content_hash`. |
| record `content_hash` | `mage_ptcg:specialist-record-content:v2` | Entire canonical record after `record_id` is set, with only `content_hash` omitted. |
| `manifest_id` | `mage_ptcg:specialist-dataset-manifest:v2` | Entire canonical manifest with `manifest_id` and manifest `content_hash` omitted. |
| manifest `content_hash` | `mage_ptcg:specialist-dataset-manifest-content:v2` | Entire canonical manifest after `manifest_id` is set, with only manifest `content_hash` omitted. |
| `permission_manifest_id` | `mage_ptcg:specialist-source-permission:v1` | Entire exact permission manifest with `permission_manifest_id` and `content_hash` omitted. |
| permission `content_hash` | `mage_ptcg:specialist-source-permission-content:v1` | Entire exact permission manifest after `permission_manifest_id` is set, with only `content_hash` omitted. |
| `model_architecture_hash` | `mage_ptcg:specialist-model-architecture:v1` | The repository-compiled exact architecture descriptor selected by the allowlisted `model_architecture_schema`; the submitted model manifest is not the descriptor or the trust source. |

Validation recomputes each ID in that order.  Within a decision,
`local_action_id` and ActionKey digest are each separately unique;
`public_action_id` is explicitly many-to-one.

---

### Task 1: Exact C1 v2 value types and safe builder

**Files:**

- Create: `src/mage_ptcg/meta_specialist/actor_visible_v2.py`
- Create: `tests/meta_specialist/test_actor_visible_v2.py`
- Modify: `src/mage_ptcg/meta_specialist/__init__.py` only if a lazy export is
  needed; do not introduce import-time dependency cycles.

**Required value types:**

- `CardRefV2(card_id, serial, player_index)` for official base Cards.
- `PokemonRefV2(card_id, serial, legacy_player_index_extension_present)` for
  the official identity plus the non-semantic v1 compatibility bit.
- `BoundCardRefV1(card_id, serial, player_index)` for a resolver result whose
  owner is either explicit on Card or derived from the containing Pokémon zone.
- Exact public Pokémon/player/board/selection/state dataclasses.
- `ActorVisibleSelectionViewV1(context_card, effect, deck_reveal, looking)`.
- `ActorPrivateStateV2(own_hand, selection_view, visibility_basis)`.
- `ActorInformationViewV2` and `ActorVisibleDecisionStateV2` with redacted reprs.

**Builder behavior:**

- Before the frozen v1 builder, perform one non-converting budget preflight on
  allowlisted container topology only: exactly two players; option length at
  most 512; stadium/active at most one; every hand/deck/prize/reveal/looking/
  discard/bench/nested attachment collection at most 60.  Length-check before
  touching any element.  For opponent prize, read only the container length;
  opponent hand/prize elements, logs, search input, and unknown fields are not
  traversed even by preflight.  Only after this bounded preflight call the
  frozen v1 builder; retain no raw observation.
- Parse only allowlisted official fields into new immutable values.
- Require strict non-bool integer domains, `firstPlayer in {-1,0,1}`, exact
  stadium list length 0/1, active length 0/1 with optional null, non-null
  bench/discard, bounded nested lists, and EnergyType 0..11.
- Require acting hand to be an exact ordered Card list whose length equals
  `handCount`; require opponent hand to be `None` without traversing it.
- Use only prize length.  Ignore logs and `search_begin_input` without walking
  their values.
- Include strict nonnegative `remainDamageCounter` and `remainEnergyCost`.
- Parse `contextCard`, `effect`, `select.deck`, and `current.looking` into
  actor-private allowlisted values, each list bounded at 60.
- Keep card collections at 60, but accept `select.option` up to the separate
  frozen `MAX_LEGAL_CANDIDATES_V2=512`.  The pinned corpus contains legitimate
  counts 61, 64, and 67; a shared 60 bound would reject valid Main/Attach play.
- When `select.deck` is non-null, require its length to equal the acting
  player's `deckCount` and every Card owner to equal the actor.  This is true
  for all 86 such decisions in the pinned 1.32.0 corpus and prevents treating a
  partial or foreign list as the actor's revealed deck.
- Reject duplicate ActionKey digests before bindings/envelopes are built.
- Give private classes `repr=False` and explicit redacted `__repr__`; provide
  explicit local serializers rather than generic `asdict` persistence.

**Legacy projection:**

- Implement pure `project_c1v2_to_c1v1_public_state()` and
  `project_c1v2_to_c1v1_own_private_state()` functions.
- The public projection must reproduce the complete historic v1 topology,
  including `{fields: ...}` card wrappers and nested-list counts—not merely
  stadium/counter changes.
- On the same raw observation, require byte-equivalent canonical v1 state,
  sorted legacy hand-ID state, v1 public digest, public ActionKey payloads and
  public action IDs.  Never reuse the v2 private decision digest as a v1 digest.
- Task 3B C1 v2 deliberately supports only the default C1 trace context:
  visible history is the exact empty tuple and belief summary is exactly null.
  Neither is a dataclass field, builder argument, update method, or local JSON
  extension, so direct construction/`replace` cannot inject them.  General
  nonempty-history/non-null-belief behavior remains available only in frozen C1
  v1 until a separately typed, bounded public v2 belief/history schema is
  designed; it is not silently dropped by a purported v2 adapter.
- `to_public_trace_payload()` reconstructs every call from typed public state
  and public ActionKey payloads, emits the invariant empty history/null belief,
  and recomputes public state/action-set/trace digests with the frozen v1
  domains.  It never deserializes a retained legacy trace/state/private JSON
  blob.  If legacy bytes are kept only as a parity oracle, construction verifies
  canonical-byte equality and the bytes are not a serialization source.  The
  parity claim is exactly against `build_decision_state(observation)` with its
  default trace context, not a v1 state later updated with belief/history.

**TDD gates:**

- Official Pokémon without `playerIndex` is accepted and bound to the containing
  player; a present mismatched extension is rejected.
- Present-versus-absent matching extensions produce the corresponding exact v1
  public projection while yielding identical semantic model features.
- Base Card without owner, bad stadium/card/Pokémon shapes, bool integers,
  overlong lists, hand count mismatch, and opponent hand contents fail closed.
- Sentinels placed in opponent hand/prize/log/search input are never touched.
- Private repr/exceptions/public trace contain none of the private sentinels.
- Shared fixtures produce exact v1 projection parity.
- A 513-option or over-cap collection with touch sentinels at its elements is
  rejected by topology preflight before v1/action parsing touches an element.
  Direct construction/`replace` cannot inject legacy JSON into the public trace;
  normal trace bytes equal frozen default-context v1 across the 936 gate.  A
  caller attempting to supply nonempty history or non-null belief to the C1 v2
  builder/update surface gets no accepted API rather than silent truncation.

---

### Task 2: Total 17-variant resolver and private candidate identity

**Files:**

- Modify: `src/mage_ptcg/meta_specialist/actor_visible_v2.py`
- Modify: `tests/meta_specialist/test_actor_visible_v2.py`
- Create: `tests/meta_specialist/fixtures/actor_visible_resolver_v1.json` only if
  a literal table fixture materially improves auditability.

**Resolver table:**

Define one frozen row for each option type 0..16.  Each row states source owner
(`option.playerIndex`, actor, or unavailable), legal source areas, exact source
list, target owner/area, and whether unresolved is legal.  The implementation
must not infer beyond this table.

- CARD/Tool/Energy variants use explicit `playerIndex`; opponent HAND never
  indexes `own_hand`.
- PLAY uses actor hand.
- For ATTACH/EVOLVE/ABILITY/DISCARD, use implicit actor ownership only where the
  official Option semantics establish it; otherwise emit `hidden-unresolved`.
- DECK and LOOKING use the actor-visible reveal lists; PRIZE is always unresolved.
- Active/bench Pokémon owner comes from the containing player zone.
- Tool/Energy attachment bindings resolve the exact nested Card and public host.
- Skill `(cardId, serial)` may resolve against the bounded typed registry, but
  this never upgrades its public ActionKey locator.  Card ID 0 remains explicit
  non-card/special-condition handling.
- Out-of-range locators into visible lists fail; intentionally hidden locators
  stay legal with an explicit missing marker.

**Binding and ID:**

- The authoritative identity value is the exact closed
  `ActorVisibleActionBindingCoreV1`; it contains precisely
  `schema_version`, `source`, `target`, and `host`.  Each endpoint contains
  precisely `resolution_kind`, `owner_player_index`, `semantic_zone`,
  `bound_card`, and `missing_reason`.  `bound_card` is null or exactly
  `{card_id, serial, player_index}`.  `host` has the same endpoint shape.
- `resolution_kind` is one of `not-applicable`, `actor-visible`,
  `public-visible`, `owner-resolved`, `hidden-unresolved`,
  `special-condition`; `semantic_zone` is one of `not-applicable`, `deck`,
  `deck-reveal`, `hand`, `discard`, `active`, `bench`, `prize`, `stadium`,
  `energy`, `tool`, `pre-evolution`, `player`, `looking`, `active-tool`,
  `bench-tool`, `active-energy`, `bench-energy`, `context-card`, `effect`, or
  `hidden`.  `owner-resolved` is used only for a card-less AreaType.PLAYER
  endpoint: non-null owner, null card, null missing reason.  `missing_reason`
  is null unless unresolved, then one of
  `hidden-zone`, `not-addressable`, `card-id-zero`, or `ambiguous-registry`.
  Null/resolution/zone combinations are validated by the literal 17-row table,
  not accepted as arbitrary cross products.
- `ActorVisibleActionBindingV1` wraps this core plus separately recomputed
  `action_key_digest`, `public_action_id`, and `local_action_id`.  The derived
  IDs are outside the core; deserialization rejects a stored self-ID or binding
  whose recomputed values differ.
- `local_action_id` is exactly
  `H('mage_ptcg:actor-visible-local-action:v1',
  {action_key_digest, binding_core})`.  It never hashes the CABT option ordinal,
  its own stored value, or `public_action_id`.  Serial may distinguish current
  physical cards but is excluded from learned semantic features.
- The code contains one literal, immutable `OPTION_RESOLVER_TABLE_V1` with 17
  rows (types 0..16).  Each row fixes operation, owner rule, source/target/host
  resolver, legal AreaType values, and legal missing reason.  Tests assert the
  literal row contents; prose or fall-through inference is not authoritative.
- AreaType values 1..12 retain their semantic area even when the exact card is
  unaddressable: DECK/ENERGY/TOOL/PRE_EVOLUTION use their named zone, and
  PLAYER resolves the explicit owner without fabricating a card.  For
  ABILITY/DISCARD, ACTIVE/BENCH derive actor ownership from the containing
  zone, while STADIUM requires index 0 and derives owner from the singleton
  stadium Card's `playerIndex`; actor ownership is never assumed for stadium.
- `resolution_kind` records the channel by which the actor obtained the value;
  `owner_player_index` records ownership.  They are independent.  Actor hand
  and full `select.deck` are `actor-visible` and every Card in those two
  collections must be actor-owned.  A non-null `current.looking`,
  `contextCard`, or `effect` Card is `actor-visible` whether actor- or
  opponent-owned; when an option also declares an owner, it must equal that
  Card owner but does not change visibility.  Active, bench, discard, stadium,
  Pokémon, and attachments are `public-visible` regardless of owner.  A zone
  is hidden only when its contents were not presented through either channel.
- Endpoint invariants are disjoint and exhaustive: `not-applicable` is the
  exact all-null endpoint; `special-condition` is its sole exact special form;
  actor/public-visible require a bound Card whose owner matches; hidden requires
  null Card plus legal missing reason; `player` is the sole cardless
  `owner-resolved` form.
- Export one state-aware `validate/rebuild_binding_v1(action_key, typed_view)`
  path.  It parses the typed actor ActionKey union, reruns the authoritative
  resolver against the current view, and returns the rebuilt core.  A candidate
  is valid only when stored and rebuilt core canonical bytes match; recomputing
  a local ID around a forged core never authenticates it.
- All ActionKey/local IDs are unique in a decision.  Repeated public IDs are
  legal and recorded as collision groups.

**TDD gates:**

- Cover all 17 variants and all AreaType values 1..12.
- Option-list permutation preserves private IDs, bindings, and semantic meaning
  while execution indices remap.
- Reordering a reveal/hand list and updating its semantic locator may change
  execution/private binding IDs, but must not change learned semantic features.
- The real two-Tool SkillOrder shape yields two private candidates and two legal
  permutations while both public Skill sources remain redacted.
- Option permutation leaves `local_action_id` unchanged; changing any binding
  core field changes it.  Two serial-distinct but semantically equal bindings
  have distinct local IDs and equal learned features.  Tampering a stored ID or
  inserting any derived ID into the core fails closed.
- Opponent-owned but actor-presented LOOKING/context/effect fixtures remain
  actor-visible, while the same owner in an unpresented hand/deck is hidden.
  Foreign-owned Cards in the acting hand or full `select.deck` are rejected.
  A forged bound Card/core with a freshly recomputed local ID is rejected by the
  state-aware rebuild comparison.

---

### Task 3: New ordinal/serial-free learned feature domain

**Files:**

- Create: `src/mage_ptcg/meta_specialist/actor_visible_features_v1.py`
- Create: `tests/meta_specialist/test_actor_visible_features_v1.py`

**Contract:**

- Freeze a closed `specialist-semantic-action-v1` view with selection type,
  context, option type, operation, source, target, and parameters.  Source and
  target contain only visibility, actor-relative owner role, semantic zone,
  card ID, host card ID, and an optional serial-free Pokémon snapshot.  Exact
  parameters cover number, attack ID, special-condition type, energy count, and
  Skill card ID; inapplicable values are null.
- Define a closed tensor-ready `actor-visible-action-v1` model input, not a
  generic recursive hash vector.  The state scalar block is exactly the
  following ordered 41 fields; active/bench occupancy is represented by the
  Pokémon multiset, so the four zone counts are unambiguously
  hand/deck/prize/discard, while bench capacity remains explicit:

  ```text
   0 first_player_role        1 step                     2 turn
   3 turn_action_count        4 selection_type           5 selection_context
   6 min_count                7 max_count                 8 option_count
   9 remain_damage_counter   10 remain_energy_cost       11 stadium_played
  12 supporter_played       13 energy_attached          14 retreated
  15 self_hand_count        16 self_deck_count           17 self_prize_count
  18 self_discard_count     19 opponent_hand_count       20 opponent_deck_count
  21 opponent_prize_count   22 opponent_discard_count    23 self_poisoned
  24 self_burned            25 self_asleep               26 self_paralyzed
  27 self_confused          28 opponent_poisoned         29 opponent_burned
  30 opponent_asleep        31 opponent_paralyzed        32 opponent_confused
  33 deck_reveal_available  34 looking_available         35 looking_hidden_count
  36 context_card_present   37 effect_present            38 stadium_present
  39 self_bench_max         40 opponent_bench_max
  ```

  `first_player_role` is categorical `0=unknown, 1=self, 2=opponent` and is
  derived actor-relatively.  `selection_type` is 0..10 and context is 0..48
  under the frozen CABT schema.  Flags/presence are strict bool encoded 0/1.
  Count values remain strict nonnegative integers in canonical model input; the
  collator emits float32 `log1p(min(value, cap))/log1p(cap)` using caps
  `step=4095`, `turn=255`, `turn_action_count=255`,
  `remain_damage_counter=255`, 512 for `option_count`/`min_count`/`max_count`,
  and 60 for every card-zone/bench-capacity/looking count.  The two categorical
  values and `first_player_role` are emitted as separate int64 indices, not
  normalized continuous values.  Saturation counters are exposed only as
  aggregate diagnostics, not extra model inputs.  Absolute actor index,
  `observed_result`, and step-availability provenance are not model scalars.
- The exact top-level `SpecialistModelInputV1` keys are `schema_version`,
  `feature_domain`, `feature_schema_hash`, `state_scalars`, `single_card_ids`,
  `card_bags`, `pokemon_entities`, and `candidate_rows`.  `single_card_ids` has
  exactly `stadium`, `context`, `effect`.  `card_bags` has exactly the five
  sorted multisets `own_hand`, `deck_reveal`, `looking_visible`, `self_discard`,
  `opponent_discard`; duplicates are retained and each bag is capped/padded at
  60 with a separate mask.
- `pokemon_entities` is a canonically sorted **multiset**, never a set.  It has
  at most 122 rows (two players times one active plus 60 bench).  A row contains
  exactly `owner_role`, `zone`, `card_id`, `hp`, `max_hp`,
  `appear_this_turn`, a length-12 `energy_type_counts`, and the three sorted
  card-ID multisets `energy_cards`, `tools`, `pre_evolution` (each capped at
  60).  It contains no serial, slot, or list ordinal.  Duplicate equal rows are
  retained with equal features.
- There are at most 512 `candidate_rows`.  Each closed row contains exactly
  selection type/context, option type/operation, source/target/host semantic
  endpoints, and parameters `number`, `attack_id`, `special_condition`,
  `energy_count`, `skill_card_id`.  A semantic endpoint contains only
  visibility, actor-relative owner role, semantic zone, card ID, host card ID,
  and an optional serial-free Pokémon snapshot.  Inapplicable fields are null.
  Rows are sorted by canonical semantic bytes then by local ID only in the
  non-learned lookup; the local-ID tie-break is not serialized in
  `model_input`.  Collation uses ragged logical length `option_count` and pads
  only to the maximum candidate count in that batch (never a global 512 tensor
  unless the batch requires it), with an exact mask.  Candidate-count buckets
  keep the rare 61+ cases from slowing ordinary batches.
- Card IDs remain typed integer tokens for a learned embedding; do not hash them
  into an opaque feature bucket.  A sealed `specialist-card-vocabulary-v1`
  manifest fixes `PAD=0`, `UNK=1`, and recognized official card ID `k` to token
  `k+1`; null optional-card fields use PAD, while an unrecognized non-null ID
  uses UNK.  The manifest binds source SHA, recognized ID interval/set, mapping
  rule, environment version, permission/usage decision, and its own schema
  hash.  The currently available EN card table has SHA-256
  `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`
  and 1,267 unique contiguous IDs.  Until asset qualification says
  `bundle_allowed`, production packaging fails closed; unit tests use an
  explicit test-only vocabulary and cannot silently promote it.
- Use card IDs, card multiplicities, public/actor zone, owner relation,
  resolution kind, selection schema/counters, public board/count/status/turn
  values, semantic action family, and non-locator option semantics.
- Aggregate hand/deck/looking/public-zone cards as bounded multisets.  Do not
  emit sequence positions for these collections.
- Never emit serial, CABT option index, hand/deck/looking ordinal, raw ActionKey
  locator, private ID, or actor-payload digest as a learned feature.
- Do not feed terminal outcome/value targets, provenance, split identity, or
  teacher/student labels into the decision encoder.  `observed_result` remains
  validated state for audit, not a predictive input.
- Do not feature-hash the current `visible_history` SHA-256 strings: they are
  opaque identifiers, not semantic history.  A later history model may consume
  only a separately versioned, bounded typed event projection.
- Current `student.features` domains are incompatible and must not be silently
  reused.  New model manifests must bind the new domain and schema hash.
- One shared extractor is used by local-record construction, training loaders,
  and runtime.  Stored features are validated against that extractor.

**Autoregressive semantic-class step interface:**

- `SpecialistModelInputV1` is accompanied in memory by a non-serialized
  `local_action_id -> candidate_row_index` lookup.  The lookup is used only for
  legality/masking/tie-breaking and is excluded from model features and
  `model_input_id`.
- At each prefix the shared v2 primitive groups currently allowed local
  candidates by exact canonical semantic-row bytes.  One group is a
  `SemanticActionClassV1` with exactly `semantic_row` and
  `allowed_alias_count`; local IDs and serials stay only in a non-learned
  class-to-current-alias lookup.
- The only policy interface is
  `SpecialistStepLogitPolicyV1.logits(model_input, step_input)`.  Its closed
  `SpecialistStepInputV1` has exactly `schema_version`, `order_semantics`,
  `semantic_prefix`, `allowed_semantic_classes`, and `stop_available`.
  It returns one finite logit per semantic class plus, when available, one
  dedicated STOP logit.  STOP is token kind 1 in its own row; semantic classes
  are token kind 0, and neither STOP nor class identity is a candidate/local
  action ID.  Class rows are ragged up to 512 and batch-padded with a mask.
- For unordered selections, `semantic_prefix` is a canonically sorted multiset
  of candidate semantic rows; for `(5,34)` it is the selected semantic-row
  sequence.  Duplicates are retained.  Prefix length is at most 512 and has its
  own mask.  `allowed_alias_count` lets the model observe legitimate
  multiplicity without seeing physical identity.
- Loss, complete-action log probability, greedy decode, beam search, and
  sampling all consume these same class logits.  They choose the semantic class
  first; only then does the runtime choose the lexicographically smallest
  currently legal local ID in that class as the deterministic physical alias.
  Removing that alias decrements the class count, so the same semantic class
  can be selected again when cardinality permits.
- Unordered semantic-prefix canonical order is a separate type from the sorted
  local-ID set used for persisted complete-action identity.  Private v1-style
  key ordering and `legal_next_tokens` are not silently reused for v2 class
  factorization; both are implemented by one shared v2 legality primitive.
- If STOP is the sole legal token, runtime appends STOP without calling the
  model and training emits no loss row.  Zero-option decisions use the same
  forced-STOP rule.  Ordered SkillOrder retains semantic prefix order; local ID
  remains only the post-class alias tie-break and is never embedded.
- Runtime, JSONL loader, teacher-target builder, and model collator must call
  this same extractor/step builder.  A manifest binds the canonical schema
  descriptor bytes and expected schema hash; unit tests recompute the literal
  expected hash rather than accepting a self-declared value.

**TDD gates:**

- Exact dimension/hash and finite numeric values.
- Option-list permutation, serial-only changes, and semantically equivalent
  reveal reordering yield equal per-candidate features.
- Source card ID/zone, target card ID/zone, counters, multiplicity, and allowed
  semantic scalars change the expected features.
- Recursive token/key scan proves forbidden ordinal/serial/private fields are
  absent.
- Existing public/private/legacy Student domains are rejected as mismatches.
- Static model input and every prefix/allowed/STOP step input are byte-equal
  between runtime and training.  Unordered prefix permutations are equal;
  SkillOrder permutations are not; forced STOP never invokes the model.
- A class A with two aliases and probability 0.6 beats singleton class B at
  0.4 in both loss and every decoder; tests also cover ordered duplicate
  aliases, alias removal/reselection, and exact reported complete-action log
  probability.

---

### Task 4: Private runtime complete-action envelope

**Files:**

- Create: `src/mage_ptcg/meta_specialist/runtime_actions_v2.py`
- Create: `tests/meta_specialist/test_runtime_actions_v2.py`
- Reuse the audited counting/selection primitives in
  `src/mage_ptcg/meta_specialist/actions.py` through composition; do not weaken
  public `DecisionEnvelope.from_decision_state()`.

**Contract:**

- `RuntimeDecisionEnvelope` is built only from a validated C1 v2 state.  It
  preserves private local IDs/current indices for legality and execution but
  exposes only semantic classes to the v2 policy.
- It accepts duplicate public projections and binds the full actor-private v2
  decision digest.
- It accepts up to `MAX_LEGAL_CANDIDATES_V2=512` while the frozen public v1
  `DecisionEnvelope` remains unchanged at 60.  The 65,536 cap applies only to
  exact enumeration/materialized teacher rows, not greedy/beam/sample
  autoregressive inference over a larger combinatorial space.  Exact
  min/max/zero-option behavior and `(5,34)`-only sequence semantics remain.
- `RuntimeCompleteAction` rejects stale/cross-envelope keys and decodes exact
  current indices.
- No public serializer exists; an attempted public serialization raises a
  constant-message private-envelope error.
- Unique-public cases can explicitly convert to the frozen public envelope and
  selected action, with byte-equivalent public trace output.
- Collision telemetry emits only status, selected count, and collision-group
  sizes—never private/public candidate IDs, actor payload, serial, reveal card,
  or option index.

**TDD gates:**

- Duplicate public candidates score/decode without fallback; public v1 envelope
  still rejects the same decision.
- Legitimate 61/64/67-candidate Main decisions decode privately without
  fallback; public conversion is attempted only when the frozen public-v1
  envelope's own limits and uniqueness rules hold.
- Unique-public conversion equals direct v1 bytes.
- Ordered SkillOrder preserves both permutations; unordered outputs canonicalize.
- Cap, stale-action, logit-domain, zero-option, and safe telemetry regressions.

---

### Task 5: Closed local record, manifest, and training loader

**Files:**

- Create: `src/mage_ptcg/meta_specialist/local_dataset_v2.py`
- Create: `tests/meta_specialist/test_local_dataset_v2.py`

**Top-level record keys:**

`schema_version`, `record_id`, `content_hash`, `decision_id`, `model_input_id`,
`episode_id_hash`, `decision_index`, `information_state`, `selection`,
`legal_actions`, `behavior`, `teacher`, `student`, `source`, `provenance`,
`privacy`, `public_audit`, `near_duplicate_id`.

Every nested section must have an exact field set and strict scalar/list bounds.
Document and test exact ID/hash prefixes and which fields each hash excludes.

`source` is one exact common shape with keys `kind`, `artifact_sha256`,
`synthetic`, `synthetic_fields`, `training_eligible`, `usage_class`, and
`permission_manifest_id`.  `synthetic_fields` is a sorted unique bounded string
list; `usage_class` is exactly `audit_only_unqualified` or
`qualified_training`; `training_eligible=true` requires `synthetic=false`, an
empty synthetic field list, and `qualified_training`.  The pinned corpus uses
`kind='pinned-telemetry-audit'`, `synthetic=true`, `['step']`, false,
`audit_only_unqualified`, and null permission manifest.  Raw source/game IDs
are not stored.  `provenance.source_record_ordinal` is a strict nonnegative
audit locator only: it is excluded from `record_id` and split identity, may
change on resharding, but remains covered by record `content_hash`.

**Candidate contract:**

- Each exact candidate contains `local_action_id`, `action_key_digest`,
  `action_key_payload`, `public_action_id`, `public_payload`, `actor_binding`,
  `semantic_action`, and the validated model `features`.
- `local_action_id` is the binding-aware private ID and is unique;
  `public_action_id` may repeat.  The existing ActionKey digest remains a
  separately verified identity and is not silently redefined.
- Exact typed 17-variant actor payload and typed actor binding are local-only.
- Public payload validates through the frozen v1 public ActionKey validator.
- Stored model feature vector/domain/hash validates through Task 3's extractor.
- Legal candidates and score rows sort by local ID.  Selected IDs retain order
  only for `(5,34)` and canonicalize for unordered schemas.
- Legal candidate rows are capped at 512, independently of every 60-card
  collection bound.

The persisted binding is the exact `ActorVisibleActionBindingCoreV1`; derived
IDs live beside it, never inside its hash core.  The loader has one mandatory
state-aware validation path for every candidate before exposing a row:

1. Parse the record's exact local `information_state` and `selection` into the
   immutable typed C1 v2 view and run all cross-field validation.
2. Parse the exact typed 17-variant actor ActionKey payload, then call Task 2's
   authoritative `validate/rebuild_binding_v1(action_key, typed_view)` resolver
   against that reconstructed state.
3. Require canonical-byte equality between the rebuilt binding core and the
   stored `ActorVisibleActionBindingCoreV1`.
4. Derive the ActionKey digest, public ID, local ID, semantic action, and shared
   features from the typed ActionKey plus **rebuilt** core, then compare every
   stored value.

Re-hashing a forged stored core is never authentication.  Public-ID uniqueness
is not a validation condition; ActionKey and local-ID uniqueness are independent
gates.

**Label contracts:**

- Behavior, Student, and Teacher use explicit closed availability/tagged-union
  states; absent scores are marked unavailable rather than fabricated.
- Behavior distinguishes `on_policy`, `action_only`, and `unavailable`.  Public
  replay or telemetry selections enter as `action_only` and are never silently
  promoted to expert/teacher labels.
- Teacher is the following exact tagged union.  Unavailable has exactly
  `{status:'unavailable', reason}`.  Available has exactly `status`,
  `teacher_id`, `teacher_revision`, `input_id`, `target_kind`, `quality_weight`,
  `value_target`, and `mass_rows`.  `input_id` must equal the record's recomputed
  `model_input_id`; `teacher_id`/revision are nonempty bounded strings;
  `quality_weight` is finite in `(0,1]`; `value_target` is null or finite in
  `[-1,1]`.  `target_kind` is one of `hard_selection`, `visit_count`,
  `probability_mass`.
- Every mass row has exactly `{complete_action_id, selection, weight}` and rows
  are canonically sorted by complete-action ID.  `selection` is a legal local-ID
  sequence for ordered `(5,34)`, otherwise a sorted unique list; zero-option is
  `[]`.  The ID is recomputed from the frozen hash table.  Duplicate selections
  or IDs are rejected.  Row count is at most the exact legal complete-action
  count and 65,536; canonical record size is capped at 16 MiB.
- `hard_selection` has exactly one row and `weight` exactly integer `1`.
  `visit_count` weights are strict non-bool nonnegative integers with at least
  one positive.  `probability_mass` weights are finite nonnegative JSON numbers
  and `math.fsum(weights)` must be within absolute tolerance `1e-12` of 1.0.
  Sparse support is permitted for all three variants; omitted legal complete
  actions have zero teacher mass.  Rows of zero weight validate but are dropped
  before loss construction.
- Teacher therefore represents a distribution over legal **complete actions**,
  not independent candidate scores.  Hard selection, root visit counts, and
  probability mass share this contract; telemetry without a ranking remains
  Behavior `action_only` and does not fabricate a Teacher.
- Student fallback has empty selection/scores plus a nonempty reason;
  nonfallback has a legal selection and exact score coverage.
- Student decode rows cover the exact allowed token/logit domain and recompute
  the selected complete action/log probability.  Ordered disagreement compares
  tuples; unordered disagreement compares sets.
- The telemetry converter may build a selected-label record without inventing a
  full ranking that was not observed.

**Unique complete-action-to-step loss:**

1. Revalidate each private complete action with the same runtime
   `legal_next_tokens` implementation and convert visit counts to probability
   with `math.fsum`; apply `quality_weight` only to the final example loss.
2. Map each selected local ID to its semantic candidate row.  For unordered
   schemas the semantic complete action/prefix is a sorted multiset; for
   `(5,34)` it is a sequence.  Sum private complete-action mass into identical
   semantic complete-action classes.  This push-forward deliberately removes
   serial/local-ID distinctions the model is forbidden to observe.
3. For every semantic prefix with positive reach mass, sum the reachable class
   mass by next semantic token, using the dedicated STOP token for completed
   actions, then divide by prefix reach mass.  Prefixes with zero reach are not
   examples; a sole forced STOP creates no loss row.
4. Candidate aliases with the same semantic row form one target class.  The
   model emits one logit directly for that class (with allowed alias count as
   input), not one logit per private alias; STOP is its own class.
   Cross-entropy and every runtime decoder normalize the identical class-logit
   domain, so a serial-indistinguishable alias is never punished by a
   private-ID one-hot target and train/inference argmax cannot disagree.
   Runtime chooses a local ID only after class selection.

The target builder, runtime prefix builder, and collator call the same legality
and semantic-equivalence functions.  Tests exhaustively compare the induced
autoregressive distribution to the supplied complete-action distribution for
small unordered, ordered SkillOrder, alias, min/max, and zero-option domains.

**Public audit:**

- Classify the action projection as `representable` or
  `duplicate-public-identity` and record deterministic collision sizes.
- A C5 v1 record ID is populated only when a complete honest C5 record was
  actually emitted; identity representability alone must not fabricate scores.
- C5 v1 records remain actor-private local audit artifacts.  A truly public
  trace uses the existing redacted trace projection.

**Manifest/loader:**

- Exact local manifest binds record schema/hash, C1 version, ActionKey version,
  feature domain/hash/dimension, environment version, deck fingerprint, source
  artifact hashes, and usage rights; `export_allowed` is always false.
- A sealed source-permission manifest has exactly the keys
  `schema_version`, `permission_manifest_id`, `content_hash`,
  `artifact_sha256`, `source_kind`, `allowed_usages`, `revision`, `issuer`,
  `valid_from_utc`, and `expires_at_utc`.  `schema_version` is exactly
  `specialist-source-permission-v1`; both hashes are lowercase 64-hex;
  `allowed_usages` is a sorted unique nonempty subset of `audit-local`,
  `training-local`, and `submission-bundle`; revision and issuer are nonempty
  bounded strings; the two times are null or canonical RFC3339 UTC strings,
  with `valid_from_utc < expires_at_utc` when both exist.  Its two IDs are
  recomputed in the frozen order/table above.
- The loader receives an immutable trusted permission set separately from the
  dataset payload.  Each trusted entry binds permission ID to exact manifest
  bytes and their SHA-256; the local dataset manifest lists exactly the
  permission ID, permission content hash, and trusted-byte SHA-256 values it
  used.  Loading reparses the closed manifest, recomputes both permission IDs,
  checks the dataset cross-reference, checks source `artifact_sha256` and
  `kind`, checks `training-local` against the requested use, and checks the
  manifest validity interval at the caller-supplied qualification time.  A
  record or dataset cannot add itself to the trusted set.
- `source.training_eligible` and `source.usage_class` are redundant checked
  projections, never authority.  The loader derives them from `synthetic`,
  `synthetic_fields`, the requested use, and the verified permission manifest:
  a nonsynthetic source with no synthetic fields and a trusted live
  `training-local` permission is exactly `(true, 'qualified_training')`;
  `audit_only_unqualified` with null permission is exactly false and produces
  no training row.  Missing, expired, tampered, mismatched, or untrusted
  permissions reject any eligibility claim rather than silently downgrading it.
- Define and recompute every ID in the frozen derivation table above.  Compute
  `record_id` before record `content_hash`, and `manifest_id` before manifest
  `content_hash`; no serializer accepts a self-referential hash field.  The
  near-duplicate core excludes serial, locator, private digest and episode data
  and is used with episode identity to keep connected components in one split.
- Atomic writer, streaming validator/loader, record count/hash, no mixed schema
  or feature domain, and existing path/private-key scans.
- Training loader returns only validated model inputs/labels, never raw local
  actor payloads as features.
- TDD includes a stored binding/Card mutation with a freshly recomputed local
  ID that still fails state-aware loading, and missing/tampered/untrusted/
  expired permission fixtures that cannot self-promote to training.  A trusted
  matching permission admits a qualified row; the pinned synthetic audit row
  remains valid for audit and yields zero default training examples.

---

### Task 6: Submission privacy enforcement

**Files:**

- Create: `src/mage_ptcg/meta_specialist/submission_privacy.py`
- Create: `src/mage_ptcg/meta_specialist/model_architectures_v1.py` before any
  production `specialist-v2` package profile is enabled.
- Create: `tests/meta_specialist/test_submission_privacy.py`
- Modify: `scripts/build_student_submission.py` only for generic extra-file
  hardening that preserves current supported package profiles.
- Task 4's specialist package builder must consume the same scanner.

**Contract:**

- Submission members use an explicit allowlist.  Arbitrary `extra_files` are
  not a bypass.
- The scanner receives a trusted builder-selected package profile plus an exact
  path-to-member-role map; payload content never selects its own role.
  `student-v0` is a separate closed legacy profile.  The `specialist-v2`
  profile requires exactly one `specialist-v2-model-manifest` role.
- Before tar creation and again after extraction, reject local-record schema
  markers, JSONL/training-dataset path classes, private action IDs, reveal
  artifacts, actor payload artifacts, and unallowlisted auxiliary files.
- Normalized JSON-key scanning also rejects physical/execution locators,
  including `serial`, `option_index`, `option_indices`, `execution_index`, and
  `execution_indices` under case or separator variants.  Public aggregate
  counts are allowed only through their explicit public schemas.
- Runtime source code may contain contract identifiers, so content checks must
  be type/path aware rather than a broad substring rule that rejects safe code.
- The specialist-v2 model manifest is a strict JSON object with exactly
  `schema_version`, `feature_domain`, `feature_schema_hash`,
  `c1_schema_version`, `card_vocabulary_manifest_sha256`,
  `model_architecture_schema`, `model_architecture_hash`, `weights_member`,
  `weights_sha256`, `deck_fingerprint`, `lane_id`, and
  `environment_version`.  `schema_version` is exactly
  `specialist-v2-model-manifest-v1`; it binds C1=2 and
  `actor-visible-action-v1`; all hash fields are lowercase 64-hex and
  `weights_member` is the profile's allowlisted
  binary weight path.  It contains no record, reveal, private candidate, or
  self-declared profile field.  The trusted specialist validator, not the JSON,
  supplies the expected lane/deck/environment bindings and performs all of the
  following exact checks:

  - `schema_version`, `feature_domain`, C1 version, and feature schema hash equal
    repository-compiled constants;
  - `weights_sha256` equals SHA-256 of the immutable snapshot bytes at
    `weights_member`;
  - `model_architecture_schema` selects one allowlisted repository-compiled
    closed architecture descriptor, whose canonical bytes are hashed with
    `mage_ptcg:specialist-model-architecture:v1` and must equal
    `model_architecture_hash`; and
  - `card_vocabulary_manifest_sha256` equals SHA-256 of the immutable bytes at
    the specialist profile's fixed `model/card-vocabulary-manifest.json` member,
    whose own exact schema/content hash/card mapping is also validated.

  Thus neither an inner hash edit nor a mutually consistent outer package hash
  can substitute unknown weights, architecture, vocabulary, or feature schema.
- The sole architecture trust anchor is the literal immutable
  `TRUSTED_MODEL_ARCHITECTURES_V1` map in
  `src/mage_ptcg/meta_specialist/model_architectures_v1.py`; its key is the
  submitted `model_architecture_schema`, never a payload-selected file or
  plugin.  Each mapped descriptor has exactly `schema_version`, `family`,
  `runtime_backend`, `weight_format`, `model_width`, `card_embedding_width`,
  `state_encoder_layers`, `set_encoder_layers`, `candidate_encoder_layers`,
  `prefix_decoder_layers`, `attention_heads`, `ffn_width`, `dropout_ppm`,
  `activation`, `normalization`, `candidate_scoring`, `stop_head`, `value_head`,
  `max_card_bag_size`, `max_pokemon_entities`, and `max_candidates`.
  Descriptor `schema_version` is exactly
  `specialist-model-architecture-descriptor-v1`; bounded strings are nonempty
  and at most 64 UTF-8 bytes; all numeric fields are strict non-bool integers;
  widths are 1..4096, layer counts 0..64, heads 1..64 with model width divisible
  by heads, FFN width 1..16384, dropout 0..1,000,000 ppm, and the three maxima
  are exactly 60, 122, and 512.  Runtime backend, weight format, activation,
  normalization, scoring, STOP head, and value head are closed enums declared
  beside the map.
- This Task 3B privacy foundation may leave the production registry empty; in
  that state every `specialist-v2` package build fails closed.  The later model
  implementation task must add each benchmark-selected architecture as a
  reviewed literal registry entry and pin its expected derived hash before it
  may enable the specialist package profile.  Placeholder descriptors,
  dynamically loaded registrations, and accepting an arbitrary descriptor from
  the model manifest are forbidden.  Task 6's production specialist builder is
  therefore explicitly downstream of that registry/model task; the generic
  legacy Student hardening can ship independently.
- Every source Path is read once into an immutable member-byte snapshot.  Those
  exact bytes are used for privacy validation, per-member SHA/size, root write,
  tar creation, and manifest creation; the tar writer never re-reads a source
  path.  Verification checks every regular tar member's exact path, size, and
  SHA against the trusted manifest before write/import, then re-reads and checks
  the extracted bytes after the privacy scan.
- The root `manifest.json` is itself parsed by the bounded duplicate-key and
  non-finite-rejecting scanner and has exactly `agent_identity`,
  `artifact_schema_version`, `archive_sha256`, and `files`; each file row has
  exactly `path`, `sha256`, and `size`.  Profile/identity is trusted builder
  input and must equal the expected value, never payload-selected.  After
  parsing, the artifact root is a closed inventory containing only
  `manifest.json`, the archive, and the exact regular files named by `files`;
  unmanifested files, directories, symlinks, and non-regular entries fail.
  Any Kaggle wrapper sidecar created after this inner build is separately
  strict-schema/privacy/hash validated and included in the wrapper's own closed
  inventory; verification may not hide or merely ignore such a sidecar.

**TDD gates:**

- Passing a v2-local JSONL through `extra_files` fails before output remains.
- A tampered archive/manifest containing the same payload fails post-extraction.
- Complete `canonical-decision-v1`, `c5-public-action-v1`,
  `own_private_state`, or `hand_card_ids` payloads fail at pre-tar, tar
  extraction, and post-write verification even under an otherwise allowed JSON
  filename.  The matching redacted `DecisionState.to_trace_payload()` fixture
  remains allowed as a public trace.  `public_audit.c5_record_id` is rejected
  from submission telemetry and is legal only inside a local record.
- JSON duplicate keys, NaN/Infinity constants, case/separator-obfuscated private
  keys, and auxiliary names such as `training-data.json` or
  `dataset_dump.json` fail closed under byte/pair/node limits.
- Existing supported Student package profiles still clean-room verify.
- Omitted/renamed v2 domain, JSON list/non-object, C1 v1, bad schema/hash,
  unexpected manifest key, absent required role, and a payload attempting to
  self-select specialist-v2 all fail; the legacy profile remains independently
  valid.
- The supported-profile full builder rejects `c5_record_id`, `c5RecordId`, and
  separator/case variants, serials, option locators, and execution locators and
  removes partial output.  Mutating a source after snapshot and independently
  tampering root, manifest, tar, or post-write bytes either has no effect on the
  immutable snapshot or fails verification.
- Full specialist-builder tests independently swap the weight bytes, rewrite
  only the inner weight hash, name an unknown architecture, alter its hash,
  alter the vocabulary bytes/hash, or supply an old feature hash; every case
  fails even after rebuilding a self-consistent outer manifest/archive.
- Tests recompute the architecture hash for every literal trusted-registry
  entry and compare it to a separately pinned expected value.  An empty
  production registry rejects specialist profile activation; once populated,
  unknown schemas are rejected both before and after model-manifest parsing.
- Outer-manifest unknown/duplicate/private keys, an unmanifested root file or
  directory, a symlink, and a valid-looking but unverified wrapper sidecar each
  fail closed; the exact clean closed inventory passes both direct and Kaggle
  wrapper verification.

---

### Task 7: Pinned 936-record audit and closure

**Files:**

- Create: `scripts/audit_actor_visible_v2_corpus.py`
- Create: `tests/meta_specialist/test_actor_visible_v2_corpus_audit.py` with a
  small checked-in fixture; keep the external pinned corpus gate explicit.
- Add Task 3B evidence to the SDD report directory.

**Audit behavior:**

- Require the pinned telemetry SHA-256 before reading.
- Read exactly 936 nonblank records.  For each raw `game_id`, derive
  `episode_id_hash = H('mage_ptcg:specialist-episode:v1',
  {source_sha256, game_id})`, persist only the hash, and assign zero-based
  `decision_index` in corpus order within that episode.  Provenance-only
  `source_record_ordinal` is the zero-based nonblank global row ordinal and is
  excluded from record identity.
- The archive does not contain the true outer Kaggle `step`; inject the constant
  integer `0` solely to exercise C1 v2, never a line/game ordinal.  Mark every
  resulting record exactly `source.synthetic=true`,
  `source.synthetic_fields=['step']`, `source.training_eligible=false`, and
  permission/usage `audit_only_unqualified`.  The default training loader must
  reject these records.  Training eligibility may change only if a separately
  qualified source supplies the true observed step; no availability mask is
  invented in this schema.
- Build C1 v2 and one audit-valid local selected-label record per decision.
- Compute public identity injectivity without fabricating rankings.
- Report selection/context, option variant, resolver kind/area, and collision
  coverage, plus exact validation error groups.

**Hard gates:**

- C1 v2: 936/936.
- Local v2 audit-valid selected-label records: 936/936; default-training-
  eligible records: 0/936 for this source.
- Public identity status: 339 representable, 597 duplicate-public-identity.
- No other validation error.
- Preserve these exact observed shape counts:
  `firstPlayer {-1:12, 0:924}`; stadium length `{0:378, 1:558}`;
  non-null deck reveal `86`; non-null looking `13`; non-null context card `49`;
  non-null effect `183`; remain damage `{0:936}`; remain energy
  `{0:916, 1:19, 2:1}`.  Report resolver branches that have zero real-corpus
  coverage.
- Preserve exact legal-option tail counts: three decisions exceed 60,
  `{61:1, 64:1, 67:1}`, maximum 67.  C1 v2/private runtime accepts all three;
  every card collection remains bounded at 60.  Preserve observed
  `OptionType.ABILITY + AreaType.STADIUM = 31` candidate coverage, including an
  adversarial opponent-owned stadium fixture.
- The collision report says exactly `339 representable`,
  `597 duplicate-public-identity`, `0 other errors`; it never says 339 C5
  records were exported because the telemetry has no complete score ranking.
- Public C1/C5 v1 focused regressions remain byte/behavior compatible.
- Run focused Task 3B tests, prior Task 3 suites, clean-room package tests,
  `compileall`, `git diff --check`, and an independent adversarial review before
  Task 4 begins.

## Task 3B completion criteria

Task 3B is complete only when duplicate public projections no longer force a
runtime fallback, all 936 pinned decisions produce validated local records,
the new feature domain is train/runtime identical and ordinal/serial-free, v1
public behavior is unchanged, actor-private values cannot reach public traces or
submission bundles, and independent review has no unresolved correctness or
privacy finding.
