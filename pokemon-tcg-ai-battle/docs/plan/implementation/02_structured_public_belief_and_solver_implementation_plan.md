---
project: MAGE-PTCG
document_status: canonical
canonical_source: git
initial_source: git
initial_sync_date: 2026-07-14
language: ja
notion_page_id: 39dfefac-d260-8107-94dc-ca2501151908
notion_url: https://app.notion.com/p/39dfefacd260810794dcca2501151908
title: 02｜構造化Belief・探索｜実装
---

# 構造化公開信念・探索 実装計画書

## 1. 目的

本書は、情報漏洩を防いだ状態型、Structured Belief、SMC、動的Action Abstraction、Public Tree、External-Sampling MCCFR、safe re-solving、transition cache、Runtime Budgetを実装するための正典です。

### 1.1 改訂スコープ（2026-07-14 第三者レビュー反映）

提出critical pathはC1（Public Belief監査・統合）とC3（bounded search）である。§7 SMC以降のL2機能、§13 ES-MCCFR、§15 safe re-solvingはOptional段階（[../design/02_structured_public_belief_and_solver_plan.md](../design/02_structured_public_belief_and_solver_plan.md)の§1.1）として維持する。

**C1構成（実装済みpatchの監査・統合対象）**

```text
src/contracts/
├── observation.py
├── events.py
└── actions.py

src/belief/
├── decision_state.py
├── public_belief.py
├── event_ledger.py
└── exact_constraints.py
```

**Stable ActionKeyテスト（C1）**

- index→key→indexの往復
- legal option順序のshuffle不変性
- ephemeral entity ID変化への安定性
- option type 12
- duplicate semantic action
- canonical payload serialization

**Public Belief v0監査項目（C1）**

- hidden truth importがない
- actor切替でstate leakがない
- episode resetが完全である
- exception後に状態が破損しない
- trace再生で同じhashになる
- Rule v0出力が非回帰である
- Rule v1非昇格結果（105–95）を再現できる

**C3構成**

```text
src/domain/
├── damage.py
├── prize_route.py
└── energy_flow.py

src/solver/
├── bounded_search.py
├── priors.py
├── transition.py
└── reliability.py
```

```python
@dataclass(frozen=True)
class BoundedSearchConfig:
    max_depth: int
    max_engine_calls: int
    hard_deadline_margin_ms: int
    prior_floor: float
    primitive_exploration_fraction: float
```

```python
@dataclass(frozen=True)
class BoundedSearchResult:
    action_keys: tuple[str, ...]
    action_values: tuple[float, ...]
    selected_action_key: str
    engine_calls: int
    action_coverage: float
    primitive_coverage: float
    guided_unguided_gap: float | None
    elapsed_ms: float
    truncated: bool
```

**Guided／Unguided比較**：guidedはRule／Knowledge prior、unguidedはuniformまたはDomain exactを使い、同一root・seed・budgetで比較する。priorでcandidateを削除しない。guidedだけが改善する場合はself-confirmationを調査する。

**Budget優先順位**：deadline safety > legal response > exact update > Rule／Book > bounded search > advanced resolving。

**C3完了条件**：legality invariant、primitive coverage 100%、p95 latency budget内、Rule v0に対するroot regretまたはpaired改善、counterexample Artifact。upliftがなければruntime searchを無効化し、offline Teacher用途だけ残す。

---

## 2. ディレクトリ

```text
src/mage_ptcg/
├── belief/
│   ├── actor_view.py
│   ├── exact_constraints.py
│   ├── deck_range.py
│   ├── particles.py
│   ├── smc.py
│   ├── recovery.py
│   ├── player_model.py
│   └── tactical_events.py
├── macros/
│   ├── action.py
│   ├── generators.py
│   ├── snapshot.py
│   ├── planner.py
│   ├── executor.py
│   └── cache.py
└── solver/
    ├── public_tree.py
    ├── infoset.py
    ├── external_sampling.py
    ├── leaf_value.py
    ├── safe_resolving.py
    ├── transition_kernel.py
    └── budget.py
```

---

## 3. 情報漏洩防止型

```python
@dataclass(frozen=True)
class ActorInformationView:
    actor: int
    public_state: PublicState
    own_hand: tuple[CardId, ...]
    own_known_prizes: Mapping[int, CardId]
    own_known_deck_top: tuple[CardId, ...]
    own_known_deck_bottom: tuple[CardId, ...]
    private_observation_history: tuple[PrivateObservation, ...]
    legal_options: tuple[LegalOption, ...]
```

`ActorInformationView`は相手手札、真の相手山札順、真の相手サイドをfieldに持ちません。

この型は[00_overall_implementation_plan.md](00_overall_implementation_plan.md)の§4.1にある共通契約（`public_state`／`own_private_state`／`limited_knowledge`／`visible_history`／`action_snapshot`）のfield粒度の詳細版であり、C1統合時に共通契約と整合させます。

```python
@dataclass
class FullState:
    engine_snapshot: OpaqueEngineSnapshot
    private_truth: PrivateTruth
```

`FullState`はsolver simulator内部だけで使用し、Macro Planner、Policy Modelのactor pathへ渡しません。

---

## 4. 厳密制約

```python
@dataclass
class ExactConstraintState:
    self_decklist: Counter[CardId]
    self_known_hand: Counter[CardId]
    self_known_field: Counter[CardId]
    self_known_discard: Counter[CardId]
    self_known_prize_positions: dict[int, CardId]
    self_known_deck_top: tuple[CardId, ...]
    self_known_deck_bottom: tuple[CardId, ...]
    opponent_public_cards: Counter[CardId]
    opponent_hand_count: int
    opponent_deck_count: int
    opponent_prize_count: int
    revealed_sets: tuple[RevealedSet, ...]
    setup_events: tuple[SetupEvent, ...]
    linear_constraints: tuple[CardCountConstraint, ...]
```

Update API：

```python
class ExactConstraintTracker:
    def update(
        self,
        previous: ExactConstraintState,
        observation: Observation,
        normalized_events: Sequence[GameEvent],
    ) -> ExactConstraintState: ...
```

---

## 5. Deck Range・Particle

```python
@dataclass(frozen=True)
class DeckHypothesis:
    deck_id: str
    card_counts: Mapping[CardId, int]
    source: str
    prior_weight: float

@dataclass
class DeckRange:
    hypotheses: list[DeckHypothesis]
    open_world_mass: float
    posterior_weights: np.ndarray
```

```python
@dataclass
class PrivateParticle:
    self_prizes: tuple[CardId, ...]
    opponent_hand: tuple[CardId, ...]
    opponent_prizes: tuple[CardId, ...]
    opponent_deck_order: tuple[CardId, ...]
    own_unknown_deck_order: tuple[CardId, ...]
    opponent_deck_id: str
    log_weight: float
```

山札全体を常に具体化せず、`ExchangeableDeckSegment`を利用可能にします。

```python
@dataclass
class ExchangeableDeckSegment:
    known_top: tuple[CardId, ...]
    unknown_multiset: Counter[CardId]
    known_bottom: tuple[CardId, ...]
```

順序が必要な効果に遭遇した時だけlazy realizationします。

---

## 6. 信念バンドル

```python
@dataclass
class GameBelief:
    exact: ExactConstraintState
    deck_range: DeckRange
    particles: list[PrivateParticle]
    normalized_weights: np.ndarray
    ess: float
    degraded: bool
    schema_version: str

@dataclass
class OpponentPlayerModel:
    cluster_posterior: dict[str, float]
    macro_temperature: float
    risk_profile: dict[str, float]
    confidence: float
    evidence_count: int

@dataclass
class BeliefBundle:
    policy_agnostic: GameBelief
    behavior_conditioned: GameBelief
    player_model: OpponentPlayerModel
    sensitivity: float
```

---

## 7. SMC

```python
class SMCBeliefUpdater:
    def update(
        self,
        belief: GameBelief,
        observation: Observation,
        events: Sequence[GameEvent],
        action_likelihood: ActionLikelihoodModel,
        budget: BeliefBudget,
    ) -> GameBelief: ...
```

擬似コード：

```python
def update(...):
    predicted = transition_particles(belief.particles, events)
    survivors = [p for p in predicted if satisfies(p, new_exact)]
    if not survivors:
        return recovery.rebuild(...)

    log_w = [
        p.log_weight
        + observation_log_likelihood(p, observation)
        + tempered_action_log_likelihood(p, action_likelihood)
        for p in survivors
    ]
    weights = normalize_log_weights(log_w)
    ess = 1.0 / np.sum(weights ** 2)

    if ess < budget.ess_threshold:
        survivors = systematic_resample(survivors, weights)
        survivors = mcmc_rejuvenate(survivors, new_exact, budget)
        weights = uniform_weights(len(survivors))

    return GameBelief(...)
```

policy-agnostic版ではaction likelihoodを平坦化します。

---

## 8. 崩壊からの復旧

```python
class BeliefRecovery:
    def recover(
        self,
        exact: ExactConstraintState,
        history: Sequence[BeliefEvent],
        budget: RecoveryBudget,
    ) -> GameBelief: ...
```

手順：

1. catalog + grammar + open-worldでDeck Range再構築
2. exact constraintsを満たすcompletion生成
3. target粒子数まで制約付きsample
4. belief-relevant eventを時系列再生
5. minimum未満なら`degraded=True`
6. telemetryへcollapse fixture保存

```yaml
recovery:
  runtime_target_particles: 32
  runtime_minimum_particles: 8
  max_action_budget_fraction: 0.10
  max_attempts: 1
```

Degraded時：

- Player Model exploitation無効
- safe resolving無効
- Student + Domain Macroへfallback
- event probabilityを保守的区間で出力

---

## 9. マクロの種類

```python
@dataclass(frozen=True)
class MacroAction:
    action_key: ActionKey
    family: str
    intent: str
    parameters: Mapping[str, JsonValue]
    information_set_signature: str
    estimated_cost: float
    source: str
```

```python
@dataclass(frozen=True)
class ActionSetSnapshot:
    infoset_key: InformationSetKey
    abstraction_version: str
    action_keys: tuple[ActionKey, ...]
    created_at_epoch: int
```

Snapshotはimmutableです。

---

## 10. マクロのライフサイクル

```python
class MacroGenerator(Protocol):
    def generate(
        self,
        view: ActorInformationView,
        belief_summary: BeliefSummary,
        deck_profile: DeckProfile,
    ) -> Sequence[MacroAction]: ...
```

4 generatorのunionをcanonicalizeします。

```python
def build_snapshot(...):
    actions = union(
        expert_generator.generate(...),
        deck_generator.generate(...),
        learned_generator.generate(...),
        primitive_escape_generator.generate(...),
    )
    actions = canonicalize_and_deduplicate(actions)
    if not actions:
        actions = legal_fallback_actions(view)
    return ActionSetSnapshot(...)
```

探索中に新Actionを発見したら`AbstractionRefinementRequest`を発行し、次epochで追加します。

---

## 11. マクロ計画器／実行器

```python
class MacroPlanner(Protocol):
    def plan(
        self,
        view: ActorInformationView,
        action: MacroAction,
    ) -> MacroPolicyTemplate: ...

class MacroExecutor(Protocol):
    def realize(
        self,
        template: MacroPolicyTemplate,
        full_state: OpaqueSimulatorHandle,
        rng: ControlledRng,
    ) -> MacroExecutionResult: ...
```

PlannerはFullStateを受け取りません。

### Cache key

```text
Plan:
(actor_information_hash, action_key, abstraction_version, compiler_version)

Realization:
(full_state_hash, template_id, rng_trace_hash, engine_version)

Transition Kernel:
(public_node_key, infoset_key, action_key, kernel_version)
```

---

## 12. Public Tree

```python
@dataclass
class InfoSetTable:
    snapshot: ActionSetSnapshot
    cumulative_regret: np.ndarray
    average_strategy_sum: np.ndarray
    visit_count: int = 0

@dataclass
class PublicTreeNode:
    key: PublicNodeKey
    public_state: PublicState
    acting_player: int | ChancePlayer
    boundary_type: BoundaryType
    infoset_tables: dict[InformationSetKey, InfoSetTable]
    transition_index: dict[TransitionKey, TransitionKernel]
    leaf_value_cache: dict[str, CFVResult]
```

Public Nodeに共有action vectorを置きません。

---

## 13. External-Sampling MCCFR

```python
def regret_matching(regret: np.ndarray) -> np.ndarray:
    positive = np.maximum(regret, 0.0)
    if positive.sum() <= 0:
        return np.full_like(positive, 1.0 / len(positive))
    return positive / positive.sum()
```

正典擬似コード：

```python
def traverse(node, full_state, traverser, ctx):
    if terminal(full_state):
        return utility(full_state, traverser)
    if depth_limited(node):
        return cfv_model.evaluate(node, full_state, traverser)

    if node.acting_player is Chance:
        outcome = sample_true_chance(node, full_state, ctx)
        return traverse(*transition(node, "CHANCE", outcome, full_state), traverser, ctx)

    actor = int(node.acting_player)
    view = make_actor_view(full_state, actor)
    infoset_key = information_set_key(view)
    table = get_or_create_table(node, view, ctx.epoch)
    strategy = regret_matching(table.cumulative_regret)

    if actor != traverser:
        update_average_strategy(table, strategy, ctx.iteration)
        idx = sample_once_per_infoset(infoset_key, strategy, ctx)
        child = transition(node, infoset_key, table.snapshot.action_keys[idx], full_state)
        return traverse(*child, traverser, ctx)

    values = []
    for action_key in table.snapshot.action_keys:
        child = transition(node, infoset_key, action_key, full_state)
        values.append(traverse(*child, traverser, ctx))

    values = np.asarray(values)
    node_value = float(strategy @ values)
    table.cumulative_regret += values - node_value
    return node_value
```

注意：sampleしたopponent/chance reachをregretへ再度掛けません。

Average strategy：

```python
def update_average_strategy(table, strategy, iteration):
    if iteration <= config.average_delay:
        return
    weight = iteration - config.average_delay if config.linear_average else 1.0
    table.average_strategy_sum += weight * strategy
```

---

## 14. 葉ノード価値インターフェース

```python
class CounterfactualValueModel(Protocol):
    def evaluate(
        self,
        public_state: PublicState,
        traverser_private: PrivateState,
        opponent_range: RangeEmbedding,
        actions: Sequence[ActionKey] | None = None,
    ) -> CFVResult: ...
```

CFV cache keyにmodel version、range hash、depth、action snapshot versionを含めます。

---

## 15. 安全な再解法

```python
@dataclass
class SafeResolveConfig:
    violation_quantile: float = 0.995
    minimum_cluster_samples: int = 1000
    sparse_cluster_backoff: str = "global"
```

Blueprint CFVとmarginをterminate optionとしてGadgetへ追加します。

Promotion条件：

- heldout outside-option violationがtarget以下
- abstraction/model変更時に再校正
- sparse clusterはglobal marginへbackoff

---

## 16. 実行時予算

```python
@dataclass(frozen=True)
class DecisionBudget:
    wall_time_ms: int
    particles: int
    max_nodes: int
    iterations: int
    max_engine_calls: int
    max_plan_misses: int
```

```python
class RuntimeBudgetController:
    def allocate(
        self,
        remaining_game_time_ms: int,
        state_features: BudgetFeatures,
        cache_state: CacheStats,
    ) -> DecisionBudget: ...
```

hard deadlineを超える前に`SearchBudgetExceeded`を発生させfallbackします。

---

## 17. テスト

### 情報漏洩

- 相手private truthだけを変え、ActorInformationViewが同じなら方策分布が同じ
- Macro PlannerがFullStateを型上受け取れない

### Solver

- Kuhn/Leduc収束
- OpenSpiel相当実装との比較
- reach二重計上版が回帰fixtureで失敗
- average updateがnon-traverserのみ
- epoch中action dimension不変

### Belief

- true state support recall
- forced collapse recovery
- open-world unknown card
- setup/mulligan event

### Cache

- hit/miss結果分布一致
- version変更で無効化
- memory cap

### Runtime

- engine call cap
- hard deadline fallback
- degraded beliefでexploit無効

---

## 18. 完了の定義

- 全public APIにtype hintとschema version
- Belief update/recoveryのdeterministic fixture
- ES-MCCFR小規模検証通過
- action snapshot invariant通過
- information-state invariance通過
- safe resolving calibration Artifact生成
- Runtime Budget超過時もlegal actionを返す
