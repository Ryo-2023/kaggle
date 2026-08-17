---
project: MAGE-PTCG
document_status: canonical
canonical_source: git
initial_source: git
initial_sync_date: 2026-07-14
language: ja
notion_page_id: 39dfefac-d260-8174-8ea0-e73dc7e4fdc7
notion_url: https://app.notion.com/p/39dfefacd26081748ea0e73dc7e4fdc7
title: 04｜Kaggle実戦適応・共同最適化｜実装
---

# Kaggle実戦適応・デッキ方策共同最適化 実装計画書

## 1. 目的

本書は、Kaggle Episode/Replay/Logの取得、正規化、Submission Registry、Deck/Policy Fingerprint、時変メタ推定、Regret Mining、Opponent Surrogate、Deck-Policy共同最適化、Champion/Challenger運用を実装するための仕様です。

### 1.1 C2b｜Competition ProbeとRaw Archive（critical path先行実装、2026-07-14改訂）

提出critical pathではC2bだけを必須とし、§6以降のReplay正規化・Fingerprint・Meta・Surrogate・共同最適化は、Replayを実際に取得できた場合（O1〜O2）だけ追加する（設計は[../design/04_kaggle_competition_intelligence_and_joint_optimization_plan.md](../design/04_kaggle_competition_intelligence_and_joint_optimization_plan.md)の§1.1）。**2026-07-17までにmodeを確定**し、mode未決定のまま調査を継続しない。

**構成**

```text
src/competition/
├── capability.py
├── clients/
│   ├── kaggle_cli.py
│   └── episode_api.py
├── raw_archive.py
├── schema_detector.py
└── redaction.py
```

**Client（架空CLI commandを作らない）**

```python
class CompetitionClient(Protocol):
    def list_own_submissions(self) -> RawResponse: ...
    def leaderboard(self) -> RawResponse: ...
    def list_episodes_for_submission(self, submission_id) -> RawResponse: ...
    def list_episodes_for_team(self, team_id) -> RawResponse: ...
    def get_episode_replay(self, episode_id) -> RawResponse: ...
```

CLIはsubmissions／leaderboard、Episode clientはListEpisodes／GetEpisodeReplayを担当する。

**Capability Report（§3の`KaggleCapabilityReport`のC2b版。実測後に§3と統一する）**

```python
@dataclass(frozen=True)
class CompetitionCapabilityReport:
    competition_id: str
    probe_time: str
    auth_scope: str
    can_list_own_submissions: bool
    can_list_leaderboard: bool
    can_list_episodes_by_submission: bool
    can_list_episodes_by_team: bool
    can_get_replay: bool
    legal_options_in_replay: bool | None
    per_agent_observations: bool | None
    private_fields_detected: tuple[str, ...]
    own_agent_logs_available: bool | None
    public_agent_logs_available: bool | None
    replay_schema_hash: str | None
    error_samples: Mapping[str, str]
```

**Probe手順**

1. submissions
2. episode list
3. replay 1件
4. keys／types抽出
5. legal／hand／deck／prize／private field検出
6. per-agent observation比較
7. raw保存
8. mode決定（`FULL_REPLAY`／`REPLAY_WITHOUT_LEGAL_OPTIONS`／`PUBLIC_ARTIFACTS_ONLY`／`LOCAL_ONLY`）

取得不能でもerrorをArtifact化する。

**Raw Archive**

```text
artifacts/competition/raw/<snapshot_id>/
├── manifest.json
├── capability.json
├── submissions/
├── leaderboard/
├── episode_lists/
├── replays/
└── redaction_report.json
```

credential、cookie、tokenを保存しない。

**Tests（C2b）**：auth failure、timeout／retry、API shape、rate limit、archive idempotence、schema variation、legal options absent、private detection、invalid raw action、secret scan。

**完了条件（C2b）**：2026-07-17までのmode決定、rawまたはerrorの保存、Replay不可時は`PUBLIC_ARTIFACTS_ONLY`または`LOCAL_ONLY`へ確定、critical pipelineを止めない。

---

## 2. ディレクトリ

```text
src/mage_ptcg/
├── competition/
│   ├── kaggle_cli.py
│   ├── capability.py
│   ├── ingestion.py
│   ├── replay_schema.py
│   ├── normalizer.py
│   ├── ledger.py
│   ├── submission_registry.py
│   ├── deck_fingerprint.py
│   ├── policy_fingerprint.py
│   ├── archetype_clustering.py
│   ├── meta_model.py
│   ├── matchup_model.py
│   ├── regret_mining.py
│   ├── surrogate.py
│   └── champion_challenger.py
├── decks/
│   ├── map_elites.py
│   ├── mutation.py
│   ├── surrogate_score.py
│   └── cooptimization.py
└── league/
    ├── psro.py
    └── external_population.py
```

---

## 3. 能力試験

```python
@dataclass
class KaggleCapabilityReport:
    can_list_submissions: bool
    can_list_episodes: bool
    can_download_replay: bool
    can_download_own_logs: bool
    can_list_leaderboard: bool
    can_list_team_submissions: bool
    can_download_public_logs: bool
    daily_dataset_available: bool
    replay_schema_hash: str | None
    rate_limit_observations: Mapping[str, float]
```

```python
class KaggleCapabilityProbe:
    def run(self, competition: str) -> KaggleCapabilityReport: ...
```

推測ではなく、実アカウント・参加済み状態で確認します。

---

## 4. Kaggle CLI wrapper

```python
class KaggleSimulationClient:
    def list_submissions(self) -> list[SubmissionRecord]: ...
    def list_episodes(self, submission_id: str) -> list[EpisodeRecord]: ...
    def download_replay(self, episode_id: str, target: Path) -> Path: ...
    def download_logs(self, episode_id: str, agent_index: int, target: Path) -> Path: ...
    def leaderboard(self) -> list[LeaderboardRecord]: ...
    def team_submissions(self, team_id: str) -> list[SubmissionRecord]: ...
    def download_daily_dataset(self, target: Path) -> DatasetSnapshot | None: ...
```

要件：

- subprocess timeout
- retry with exponential backoff
- rate-limit detection
- stdout/stderr保存
- CLI version記録
- idempotent download
- checksum検証

---

## 5. 生データ保存領域

```text
data/raw/kaggle/
├── capability/
├── submissions/YYYY-MM-DD/
├── leaderboard/YYYY-MM-DD-HH/
├── episodes/<submission_id>/
├── replays/<episode_id>.json
├── logs/<episode_id>/<agent_index>.json
└── daily_dataset/<snapshot_id>/
```

Rawは上書きせず、content hashとretrieved_atを付けます。

---

## 6. Normalized schema

### Episode

```python
@dataclass
class EpisodeRow:
    episode_id: str
    source: str
    created_at: str | None
    ended_at: str | None
    state: str
    submission_0: str | None
    submission_1: str | None
    team_0: str | None
    team_1: str | None
    winner: int | None
    termination_reason: str | None
    step_count: int
    first_player: int | None
    replay_hash: str
    schema_version: str
```

### Player Episode

```python
@dataclass
class PlayerEpisodeRow:
    episode_id: str
    player_index: int
    submission_id: str | None
    observed_cards: tuple[int, ...]
    deck_fingerprint_id: str | None
    archetype_posterior: Mapping[str, float]
    policy_cluster_posterior: Mapping[str, float]
    first_attack_turn: int | None
    first_prize_turn: int | None
    final_prizes: int | None
    timeout: bool
    invalid_action: bool
    exception: bool
```

### Decision

```python
@dataclass
class DecisionRow:
    episode_id: str
    step_index: int
    player_index: int
    turn: int
    phase: str
    select_type: str
    select_context: str | None
    legal_option_count: int
    selected_indices: tuple[int, ...]
    selected_card_ids: tuple[int, ...]
    public_state_hash: str
    actor_information_hash: str | None
    remaining_time_ms: int | None
    final_result: float
```

### Game Event

```python
@dataclass
class GameEventRow:
    episode_id: str
    step_index: int
    event_type: str
    actor: int | None
    card_id: int | None
    source_zone: str | None
    destination_zone: str | None
    target_id: str | None
    numeric_value: float | None
    raw_log_hash: str
```

---

## 7. リプレイ正規化器

```python
class ReplayNormalizer:
    def normalize(self, raw: Mapping[str, Any], source: ReplaySource) -> NormalizedReplay: ...
```

手順：

1. schema detector
2. player/submission mapping
3. step iteration
4. turn/phase reconstruction
5. log parser
6. card location ledger replay
7. decision extraction
8. termination extraction
9. invariant validation
10. normalized parquet output

### Invariants

- zone countが負にならない
- public card duplicationがない
- selected indexがlegal range内
- winnerとterminationが整合
- ledger replayが最後まで通る

不整合Replayはquarantineへ送ります。

---

## 8. 提出レジストリ

```python
@dataclass
class SubmissionProfile:
    submission_id: str
    team_id: str | None
    first_seen: str
    last_seen: str
    active: bool
    public_score_history: tuple[ScorePoint, ...]
    suspected_parent_id: str | None
    joint_strategy_fingerprint_id: str | None
    sample_count: int
    visibility: str
```

Lineage推定：

- submission date
- deck fingerprint similarity
- policy fingerprint similarity
- team identity
- score continuity

推定lineageにはconfidenceを付け、断定しません。

---

## 9. Deck Fingerprint

```python
@dataclass
class DeckFingerprint:
    observed_card_counts: Mapping[CardId, float]
    evolution_edges: Mapping[tuple[CardId, CardId], float]
    energy_profile: Mapping[str, float]
    trainer_role_profile: Mapping[str, float]
    attack_usage: Mapping[str, float]
    opening_sequence_embedding: np.ndarray
    prize_pattern_embedding: np.ndarray
    posterior_confidence: float
```

複数Episode統合：

```python
class DeckFingerprintEstimator:
    def update(
        self,
        previous: DeckPosterior | None,
        player_episode: NormalizedPlayerEpisode,
        deck_grammar: DeckGrammar,
    ) -> DeckPosterior: ...
```

Card count上限、進化整合性、observed card必須を制約にします。

---

## 10. Policy Fingerprint

```python
@dataclass
class PolicyFingerprint:
    macro_distribution: Mapping[str, float]
    bench_expansion: float
    resource_conservation: float
    gust_priority: Mapping[str, float]
    disruption_timing: Mapping[str, float]
    strategy_mode_transition: np.ndarray
    decision_time_profile: Mapping[str, float]
    confidence: float
```

private availabilityの影響を除くため、Belief-conditioned likelihoodで推定します。

```python
class PolicyClusterModel:
    def infer(
        self,
        decisions: Sequence[DecisionRow],
        reconstructed_beliefs: Sequence[BeliefSummary],
    ) -> PolicyClusterPosterior: ...
```

---

## 11. アーキタイプクラスタリング

特徴：

- Deck fingerprint
- Card role co-occurrence
- evolution/energy graph
- opening sequence
- prize pattern

手順：

1. known catalogへのposterior
2. known variant判定
3. residual embedding clustering
4. minimum supportとstability検査
5. unknown archetype登録

Cluster IDはsnapshotごとに再割当せず、matchingで継承します。

---

## 12. 時変メタモデル

```python
class MetaPosteriorModel:
    def update(
        self,
        previous: MetaPosterior,
        observations: Sequence[WeightedClusterObservation],
        timestamp: datetime,
    ) -> MetaPosterior: ...
```

```python
@dataclass
class MetaPosterior:
    timestamp: str
    mean: Mapping[str, float]
    credible_intervals: Mapping[str, tuple[float, float]]
    source_composition: Mapping[str, float]
    uncertainty_scenarios: tuple[Mapping[str, float], ...]
```

忘却係数、source weight、duplicate discountをconfig化します。

---

## 13. Matchup Model

階層logistic modelをPyMC/NumPyroまたは独自variationalで実装します。

```python
class MatchupModel:
    def fit(self, games: MatchupDataset, prior: MatchupPrior) -> MatchupPosterior: ...
    def predict(self, strategy_a: str, strategy_b: str, first_player: int) -> WinPosterior: ...
```

Kaggle観測ではjoint strategy単位。Deck/Policy分解targetはlocal cross-playだけから生成します。

---

## 14. 後悔分析

```python
class ReplayRegretMiner:
    def reconstruct_information_state(self, replay: NormalizedReplay, decision: DecisionRow) -> ReconstructedState: ...
    def solve_information_set(self, state: ReconstructedState, budget: TeacherBudget) -> ISRegretResult: ...
    def solve_oracle(self, state: ReconstructedState, full_truth: FullState | None) -> OracleRegretResult | None: ...
```

```python
@dataclass
class ISRegretResult:
    played_action: str
    best_action: str
    action_values: Mapping[str, float]
    regret: float
    failure_category: str
    confidence: float
```

Oracle resultは別table・別Artifact typeへ保存します。

---

## 15. 相手代理モデル

```python
@dataclass
class SurrogateSpec:
    surrogate_id: str
    deck_posterior_id: str
    policy_cluster_id: str
    model_id: str
    uncertainty: float
    source_submission_ids: tuple[str, ...]
```

Training：

- observed action likelihood
- belief-conditioned behavior cloning
- entropy floor
- unseen action smoothing
- Teacher quality weighting

Evaluation：

- action distribution KL
- macro confusion
- heldout Episode likelihood
- local matchup reproduction

Surrogate uncertaintyが高い場合、League weightを下げます。

---

## 16. MAP-Elites Deck Search

```python
class DeckMapElites:
    def initialize(self, seeds: Sequence[LegalDeck]) -> Archive: ...
    def ask(self, n: int) -> list[DeckCandidate]: ...
    def tell(self, results: Sequence[DeckEvaluation]) -> None: ...
```

Behavior descriptor：

```python
@dataclass(frozen=True)
class DeckBehavior:
    setup_speed_bin: int
    prize_style_bin: int
    control_bin: int
    energy_complexity_bin: int
    branching_bin: int
```

Candidate評価段階：

1. legality/grammar
2. cheap consistency metrics
3. learned surrogate score
4. small tournament
5. full paired tournament

---

## 17. PSRO Co-optimization

```python
class JointStrategyPSRO:
    def solve_meta(self, payoff_matrix: np.ndarray) -> np.ndarray: ...
    def train_best_response(self, meta_mixture: np.ndarray, role: str) -> PopulationMember: ...
    def add_member(self, member: PopulationMember) -> None: ...
```

Best responseはDeck-only、Policy-only、Jointの3種類を交互に生成します。

---

## 18. Champion/Challenger Registry

```python
@dataclass
class ChallengerExperiment:
    experiment_id: str
    champion_submission_id: str
    candidate_artifact_id: str
    changed_factor: str
    hypothesis: str
    local_eval_id: str
    kaggle_submission_id: str | None
    decision: str | None
```

Promotion API：

```python
class PromotionGate:
    def decide(
        self,
        local_result: EvalPosterior,
        live_adjusted: LiveRatingEstimate | None,
        risk: RiskReport,
    ) -> PromotionDecision: ...
```

---

## 19. スケジューラ

```yaml
competition_scheduler:
  ingest_interval_minutes: 60
  leaderboard_interval_hours: 4
  meta_update_hours: 24
  regret_mining_daily_roots: 500
  surrogate_update_days: 1
  deck_search_days: 2
```

重い処理はqueueへ送り、ingestionをブロックしません。

---

## 20. データ利用マニフェスト

```yaml
artifact_id:
source:
visibility:
retrieved_at:
allowed_use:
contains_private_postgame_info:
contains_logs:
quarantined:
reason:
```

accidental secret検出時は自動quarantineします。

---

## 21. テスト

### Ingestion

- duplicate episode
- partial download
- CLI timeout
- rate limit
- schema version change

### Normalization

- ledger invariant
- missing log
- unexpected select type
- corrupted replay

### Modeling

- temporal holdout
- cluster stability
- posterior calibration
- joint-strategy non-identifiability test

### Regret

- IS reconstructionにpostgame truthが混入しない
- Oracle Artifactがtraining loaderから除外される

### Operations

- Champion rollback
- Submission lineage
- scheduler idempotency

---

## 22. 完了の定義

- 新Episodeだけ差分取得
- raw/normalizedのhash追跡
- Replay schema変更にcanary test
- Meta Posteriorがcredible intervalを返す
- Regret MiningがIS/Oracleを型とstorageで分離
- Surrogate uncertainty付き
- MAP-Elitesが常に合法デッキを評価
- Promotion Gateがraw ratingだけで昇格しない
- data-use manifestが全Competition Artifactに存在

---

## 23. Competition Intelligence Sidecar 実装（O1, 2026-07-18）

本節は、[設計書§20](../design/04_kaggle_competition_intelligence_and_joint_optimization_plan.md)のデータガバナンス層を実装した範囲を記録する。§1–22の収集・解析仕様自体は変更しない。作業はcanonical worktree（`feature/belief-guided-search`、`6782e68`）を変更しない検証済みdetached worktree（`pokemon-tcg-ai-battle-o1-intelligence`）で行った。

### 23.1 モジュール配置と既存機能の再利用

新規パッケージ`src/mage_ptcg/competition_intelligence/`を追加し、重複実装を避けるため既存機能を次のとおり再利用する。

| 責務 | 再利用元 | 備考 |
|---|---|---|
| Capability probe／raw archive／redaction／schema fingerprint | `mage_ptcg.competition`（C2b、既存） | `secret_scan`を直接importして再利用。`CompetitionMode`／`ProbeRunner`は変更しない |
| Stable ActionKey／ActorInformationView／Public Trace | `mage_ptcg.decision_state`、`mage_ptcg.public_belief`（既存） | `DecisionRecord.actor_information_view`は`ActorInformationView`の契約に従う想定。相手非公開情報を含めない |
| Wilson信頼区間 | `mage_ptcg.offline_training_v1_support.statistics.wilson_score_interval`（既存） | 本セッションでは未使用（Matchup解析はO1-2以降）。実装時に直接importする方針のみ記録 |
| Episode-group split手法 | `mage_ptcg.dataops.collector.split_by_episode_group`（既存） | O1-4 Snapshot群split実装時に同様のhash方式を踏襲する方針（未実装） |
| canonical JSON／digest／atomic write | 独自実装（`competition_intelligence/canonical.py`、`atomic_io.py`） | リポジトリに共有utilityが存在しない（`knowledge.model`、`distillation.contracts`等が個別実装する既存慣習と同型）ため、sidecar専用のdomain-prefixed実装を追加した。cross-cutting util化はスコープ外として見送った |

### 23.2 実装範囲（本セッション：Slice 0–2、O1-0とO1-1の一部）

- `contracts.py`：`SourceEnvelope`、`EpisodeRecord`、`DecisionRecord`、`DeckObservation`、`KnowledgeClaim`、`IntelligenceSnapshot`の6契約。frozen dataclass + `__post_init__`検証（既存`knowledge.model.KnowledgePack`と同型の自己検証スタイル）。`IntelligenceSnapshot`は内容ハッシュ自己検証で、`build_intelligence_snapshot()`が唯一の構築経路（`snapshot_id`／`snapshot_sha256`を呼び出し側が指定することを拒否する）
- `permissions.py`：default deny、`intersect_allowed_uses`、`require_permission`、`PUBLIC_OTHER`のTRAINING既定拒否
- `provenance.py`：SourceEnvelope構築とmanifestシリアライズ。manifestファイル名は`source_id`のsha256から導出し、任意文字列によるpath traversalを構造的に防ぐ
- `archive.py`：content-addressed raw archive（`raw/sha256/<prefix>/<hash>`）とquarantine。`mage_ptcg.competition.redaction.secret_scan`を再利用してarchive前にscanする
- `runstate.py`：run manifest／single-writer lock（PID + `/proc`起動時刻マーカーによるstale lock判定。既存`offline_training.runstate`と同型の独自実装）
- `catalog.py`：SQLite rebuildable catalog（現時点は`sources`テーブルのみ。`episodes`／`decisions`テーブルはO1-2実装時に追加予定で、削除しても`source_manifests/*.json`から再構築できる契約を維持する）
- `config.py`：`CompetitionIntelligenceConfig`。`automation.auto_promote`／`automation.auto_submit`／`external.public_other_training_enabled`をv1で強制拒否（`__post_init__`が`ConfigError`を送出）
- `local_ingest.py`、`cli.py`：`doctor`／`ingest-local`／`rebuild-catalog`の3コマンドのみ実装。`normalize`／`analyze`／`import-knowledge`／`build-knowledge-snapshot`／`build-meta-snapshot`／`build-snapshot`／`export-offline-dataset`／`report`は未実装（§23.3に継続計画を記録し、stubとしては追加していない）

### 23.3 未実装範囲（継続計画）

O1-2（Replay正規化・解析）、O1-3（Knowledge Claim取込CLIとcontradiction registry）、O1-4（Snapshot builderとOffline Training adapter）、O1-5（Kaggle live／Team bundle adapter）、O1-6（Meta posterior／Opponent Surrogate／Promotion Report）は未着手である。継続順序案：

1. O1-2：local run readerが既存`offline_training`のrun manifest／shardを読み取り専用で走査し、`EpisodeRecord`/`DecisionRecord`へ正規化する。Stable ActionKeyは`decision_state.py`の契約をそのまま使う
2. O1-3：Claim Bundle（YAML/JSON）parserとlifecycle registry（`contracts.KnowledgeClaim`と`validate_claim_transition`は実装済みのため、取込CLIとdeterministic contradiction検出を追加する）
3. O1-4：cutoff／permission intersection／episode-group splitを適用したSnapshot builder。selection-only Offline Training adapterはlazy importで接続する
4. O1-5：`mage_ptcg.competition`の`ProbeRunner`を拡張し、Team bundle validator（permission manifest必須、path traversal／symlink拒否）を追加する
5. O1-6：Deck/Policy fingerprintのstdlib実装とDirichlet系meta posterior、empirical Opponent Surrogate

### 23.4 Runtime isolation

`mage_ptcg.competition_intelligence`は`main.py`から到達不能である。`tests/test_competition_intelligence_runtime_isolation.py`が、素の`import main`と既定Rule Agent v0経路（`main.make_rule_agent`／`main.make_student_agent`のRule v0 fallback）の両方について、cleanなsubprocess（`PYTHONPATH`除外）で`sys.modules`をscanし、sidecar・`mage_ptcg.dataops`・`sqlite3`・`pandas`・`sklearn`が現れないことを検証する。

### 23.5 CLIとテスト

```bash
python scripts/run_competition_intelligence.py doctor
python scripts/run_competition_intelligence.py ingest-local --run-dir runs/competition-intelligence/<run-id> --input <path>
python scripts/run_competition_intelligence.py rebuild-catalog --run-dir runs/competition-intelligence/<run-id>
python scripts/check_o1_protected_files.py baseline --output <path>
python scripts/check_o1_protected_files.py verify --baseline <path>
```

新規focused test 127件（`tests/competition_intelligence/`115件＋`tests/test_competition_intelligence_runtime_isolation.py`4件＋`tests/test_check_o1_protected_files.py`8件、内訳は[Evidence](../../evidence/o1-competition-intelligence-sidecar-slice0-1.md)）がPASS。詳細な実行ログとPromotion Gateへの非干渉確認は同Evidenceを正とする。
