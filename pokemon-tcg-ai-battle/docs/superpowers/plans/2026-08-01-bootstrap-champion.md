# Bootstrap Champion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 利用可能なデッキ・エージェントの中から最強の組を固定ベンチマークで選び、その方策を反映した R2D3 step 0 checkpoint から新しい継続学習を開始できるようにする。

**Architecture:** 既存の source intake、submitted snapshot、CABT 実行、BenchmarkManifest、R2D3 learner を再利用し、その前段に `bootstrap_champion` package を追加する。この package が資産の同一性、deck-policy 互換性、予備/最終選抜、Champion manifest、教師 dataset、step 0 重みを管理する。`continuous_league.py learn` は step 0 重みを「resume 状態」ではなく「初期重み」として読み、新しい optimizer、Replay、学習 step で開始する。

**Tech Stack:** Python 3.12、PyTorch 2.x、pytest、CABT、tqdm、既存 `mage_ptcg.continuous_league` / `mage_ptcg.policy_learning.r2d3` / `mage_ptcg.opponent_ingest` API。

**Design Canon:** `docs/superpowers/specs/2026-08-01-bootstrap-champion-design.md`

## Global Constraints

- タスクごとに red-green-refactor を行い、先に失敗するテストを作る。
- 長時間の 256/1,024 局実験は実装テストで起動しない。スケジュールと集計は合成データ、E2E は 4〜8 局で検証する。
- Kaggle 提出、Kaggle Replay 行動の模倣、非公開データ取得を行わない。
- remote branch は fetch/read/snapshot のみとし、remote branch へ commit、push、force update しない。
- `main.py`、`deck.csv`、他メンバーの agent source を上書きしない。
- 今回の実装の commit / push は、その時点でユーザーが明示的に依頼した場合だけ行う。
- 現在の dirty worktree にある他作業の差分を削除、整形、巻き戻ししない。
- actor-visible 境界、Stable ActionKey、60 枚デッキ、CABT 合法手判定は fail-closed とする。
- Bootstrap 用 benchmark、学習用 opponent、checkpoint 採用 benchmark の seed namespace と artifact identity を共用しない。

---

### Task 1: Bootstrap artifact と同一性の契約

**Files:**

- Create: `src/mage_ptcg/bootstrap_champion/__init__.py`
- Create: `src/mage_ptcg/bootstrap_champion/contracts.py`
- Create: `tests/test_bootstrap_champion_contracts.py`

**Interfaces:**

```python
class BootstrapContractError(ValueError): ...

class InitializationMode(str, Enum):
    DIRECT_CHECKPOINT = "DIRECT_CHECKPOINT"
    TEACHER_DISTILLATION = "TEACHER_DISTILLATION"

class DeckCompatibility(str, Enum):
    EXACT_DECK = "EXACT_DECK"
    ARBITRARY_LEGAL_DECK = "ARBITRARY_LEGAL_DECK"

@dataclass(frozen=True, slots=True)
class DeckAsset:
    deck_id: str
    deck_hash: str
    snapshot_path: str
    source_id: str
    source_hash: str

@dataclass(frozen=True, slots=True)
class PolicyAsset:
    policy_id: str
    policy_hash: str
    policy_kind: str
    runtime_path: str
    adapter_hash: str
    runtime_config_hash: str
    compatibility: DeckCompatibility
    exact_deck_hash: str | None
    source_id: str
    source_hash: str

@dataclass(frozen=True, slots=True)
class JointCandidate:
    deck: DeckAsset
    policy: PolicyAsset
    simulator_contract_hash: str

    @property
    def candidate_id(self) -> str: ...
```

- [ ] `DeckAsset` が非 60 枚の snapshot、不正 hash、実体と不一致の hash を拒否するテストを書く。
- [ ] `EXACT_DECK` policy に `exact_deck_hash` がない場合と、`ARBITRARY_LEGAL_DECK` policy に不必要な拘束がある場合を拒否するテストを書く。
- [ ] フィールド順や入力順によらず、同一の組が同じ `candidate_id` を作るテストを書く。
- [ ] 上記テストを実行し、module が存在しないため失敗することを確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_contracts.py
```

- [ ] 既存 `continuous_league.contracts.content_id` / `file_sha256` / `atomic_write_json` を再利用し、上記 dataclass と enum を実装する。
- [ ] `JointCandidate` の content identity へ `deck_hash`, `policy_hash`, `adapter_hash`, `runtime_config_hash`, `simulator_contract_hash` を含める。path や timestamp は identity へ含めない。
- [ ] `BootstrapChampionManifest` と `BootstrapCheckpointManifest` の `build()`, `to_dict()`, `from_dict()` を実装し、再計算した ID と不一致な JSON を拒否する。
- [ ] 全 manifest が atomic write され、再実行で同一 artifact なら no-op、異なる artifact なら fail-closed となるテストを追加する。
- [ ] focused test を通す。

---

### Task 2: Read-only source intake と deck-policy 候補生成

**Files:**

- Create: `src/mage_ptcg/bootstrap_champion/intake.py`
- Create: `src/mage_ptcg/bootstrap_champion/candidates.py`
- Modify: `src/mage_ptcg/continuous_league/source_intake.py`
- Modify: `src/mage_ptcg/continuous_league/catalog.py`
- Create: `tests/test_bootstrap_champion_intake.py`

**Interfaces:**

```python
def build_bootstrap_asset_registry(
    *,
    repo: Path,
    submitted_catalog: CatalogSnapshot,
    deck_asset_registry: Path | None,
    compatible_checkpoints: Sequence[Path],
    output: Path,
) -> BootstrapAssetRegistry: ...

def build_joint_candidates(
    registry: BootstrapAssetRegistry,
    *,
    simulator_contract_hash: str,
) -> tuple[JointCandidate, ...]: ...

def is_compatible(deck: DeckAsset, policy: PolicyAsset) -> bool: ...
```

- [ ] 2 つの deck、任意 deck 対応 policy、exact-deck policy から、3 組だけが生成される失敗テストを書く。
- [ ] `CatalogEntry.from_submitted_asset()` で固定された remote snapshot を Policy/Deck Asset へ変換し、source commit と hash が保存されるテストを書く。
- [ ] Kaggle 系の deck-only entry が Policy Asset を自動生成しないテストを書く。
- [ ] checkpoint の model config / Stable ActionKey schema / deck binding が不明な場合、`DIRECT_CHECKPOINT` 候補にならないテストを書く。
- [ ] focused test を実行し、新 API がないため失敗することを確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_intake.py
```

- [ ] `build_qualified_submitted_catalog()` が生成する pinned snapshot を複製せず、その manifest を参照する adapter を実装する。
- [ ] 公開 deck registry は `deck_asset_registry.jsonl` の provenance、exact/degraded 区分を読み、exact で合法性を確認できる deck だけを候補にする。
- [ ] 互換 R2D3 checkpoint はファイルの存在、SHA-256、schema、model config、action schema、deck binding を読み、registry へ保存する。不明なフィールドは既定値で補わない。
- [ ] `EXACT_DECK` は exact hash とのみ、`ARBITRARY_LEGAL_DECK` は合法な全 deck と組み合わせる。duplicate `candidate_id` は一つに正規化する。
- [ ] asset registry と candidate registry を内容 hash 付き JSON で固定する。
- [ ] `refresh_sources()` の remote 操作が `git fetch --prune <remote>` 以外を発行しないことを fake command runner で検査する。
- [ ] focused test と既存 source intake test を通す。

---

### Task 3: qualification smoke と固定トーナメント

**Files:**

- Create: `src/mage_ptcg/bootstrap_champion/tournament.py`
- Modify: `src/mage_ptcg/continuous_league/benchmark.py`
- Modify: `src/mage_ptcg/continuous_league/evaluation.py`
- Create: `tests/test_bootstrap_champion_tournament.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class BootstrapTournamentSpec:
    screen_games_per_candidate: int = 256
    validation_games_per_candidate: int = 1024
    finalists: int = 4
    screen_seed_namespace: str = "bootstrap-screen-v1"
    validation_seed_namespace: str = "bootstrap-validation-v1"

def build_candidate_schedule(
    *, candidate_ids: Sequence[str], benchmark: BenchmarkManifest,
    games_per_candidate: int, seed_namespace: str,
) -> tuple[BootstrapMatch, ...]: ...

def summarize_candidate(rows: Sequence[EvaluationRow]) -> BootstrapScore: ...

def rank_candidates(scores: Sequence[BootstrapScore]) -> tuple[BootstrapScore, ...]: ...
```

- [ ] qualification smoke が各候補 4 局、両 seat 2 局ずつを作り、fault/timeout/illegal が 1 件でもあれば候補を失格させるテストを書く。
- [ ] 256 局 schedule が opponent × seat を均等に含み、候補の列挙順を変えても同じ対局 identity になるテストを書く。
- [ ] 1,024 局 schedule が予備選抜と別 namespace であり、候補間で opponent/seat/seed cell を共有するテストを書く。
- [ ] 任意の結果行から opponent-equal score、worst-opponent score、overall Wilson lower bound、seat 別 score、fault を集計するテストを書く。
- [ ] 最高 opponent-equal score から 1 point 以内の候補集合を一度だけ作り、その中で worst-opponent、Wilson 下限、p95、`candidate_id` の順に決定するテストを書く。
- [ ] focused test を実行し、必要 API 不足による失敗を確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_tournament.py
```

- [ ] `BenchmarkManifest` の opponent instance と既存 `build_schedule()` を再利用し、candidate 側の seat と seed namespace を追加した固定 schedule を実装する。
- [ ] 最終選抜開始時に finalist の `candidate_id` と全 hash を freeze manifest へ書き、途中の source refresh の影響を受けなくする。
- [ ] TTY は単一の `tqdm` bar、非 TTY は約10秒ごとの集約行と atomic `progress_summary.json` に限定する。局ごとのログは出さない。
- [ ] 欠落 cell、duplicate result、schedule 外 result、fault がある最終選抜では Champion manifest を作らない。
- [ ] 上位候補と最終勝者の選抜および `BootstrapChampionManifest` 出力を実装する。
- [ ] focused test、`tests/test_continuous_league_contracts.py`、`tests/test_continuous_league_cabt.py` を通す。

---

### Task 4: actor-visible 教師 dataset の収集と seal

**Files:**

- Create: `src/mage_ptcg/bootstrap_champion/teacher.py`
- Modify: `src/mage_ptcg/continuous_league/cabt.py`
- Modify: `src/mage_ptcg/policy_learning/r2d3/sequence.py`
- Create: `tests/test_bootstrap_champion_teacher.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class BootstrapTeacherExample:
    game_id: str
    decision_index: int
    public_state: Mapping[str, object]
    own_private_state: Mapping[str, object]
    visible_history: tuple[Mapping[str, object], ...]
    legal_action_keys: tuple[str, ...]
    selected_action_keys: tuple[str, ...]
    outcome: str
    behavior_weight: float
    teacher_candidate_id: str

def collect_teacher_dataset(...) -> TeacherDatasetManifest: ...
def validate_actor_visible_example(example: BootstrapTeacherExample) -> None: ...
def split_games(game_ids: Sequence[str], *, seed: int) -> tuple[set[str], set[str]]: ...
```

- [ ] 教師が選んだ単一 option index が Stable ActionKey へ変換され、選択外/重複 index は失敗するテストを書く。
- [ ] 複数選択 decision は現行 R2D3 transition へ無理に変換せず、`skipped_multi_select_decisions` へ計数して dataset から外すテストを書く。
- [ ] hidden opponent hand、deck order、future random を示す key が 1 つでも example へ入った場合に seal が失敗するテストを書く。
- [ ] win/draw/loss が 1.0/0.5/0.25 の `behavior_weight` になるテストを書く。
- [ ] 1 局内の全 decision が train または validation の同じ側に入り、game ID の並び順に依存しない 80/20 分割のテストを書く。
- [ ] fault を含む game の example が 1 件も seal されないテストを書く。
- [ ] focused test を実行し、未実装のため失敗することを確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_teacher.py
```

- [ ] `CabtMatchExecutor` の decision hook が原始 observation 全体を保存せず、`build_decision_state()` で構築した actor view と Stable ActionKey だけを sink へ渡すようにする。
- [ ] 同じ Champion、deck、opponent schedule、seed、trace schema から同じ dataset ID が得られるようにする。
- [ ] example を game 単位で JSONL chunk に書き、manifest へ game count、decision count、outcome count、weight count、`skipped_multi_select_decisions`、train/validation ID、source provenance を記録する。
- [ ] seal 前に actor-visible validation、action legality、fault-free game、deck hash、Champion ID を再検査する。
- [ ] focused test と `tests/test_continuous_league_cabt.py` を通す。

---

### Task 5: R2D3 用の教師事前学習

**Files:**

- Create: `src/mage_ptcg/bootstrap_champion/distillation.py`
- Modify: `src/mage_ptcg/policy_learning/r2d3/learner.py`
- Modify: `src/mage_ptcg/policy_learning/r2d3/model.py`
- Create: `tests/test_bootstrap_champion_distillation.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class DistillationConfig:
    learning_rate: float
    batch_size: int
    max_epochs: int
    patience_epochs: int
    gradient_clip: float
    seed: int

def behavior_cloning_loss(
    q_values, legal_mask, selected_mask, behavior_weight
): ...

def distill_bootstrap_policy(
    *, model, dataset, config: DistillationConfig, output: Path
) -> DistillationResult: ...
```

- [ ] illegal action の Q 値が大きくても loss へ入らない masked cross entropy のテストを書く。
- [ ] 単一の target Stable ActionKey が合法候補に存在しない場合は失敗し、複数選択 example は learner 入力前に拒否されるテストを書く。
- [ ] behavior weight 0.25 の sample の勾配寄与が weight 1.0 より小さいテストを書く。
- [ ] 2〜3 batch の合成 dataset で train loss が下がり、モデル重みが更新されるテストを書く。
- [ ] validation の最良 epoch の重みが保存され、最終 epoch の重みで上書きされないテストを書く。
- [ ] focused test の失敗を確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_distillation.py
```

- [ ] 既存 R2D3 model の `output["q"]` を合法行動 logit として使う。新しい提出時専用 head は追加しない。
- [ ] behavior-only update は TD、target network bootstrap、PER、demonstration margin を実行せず、weighted masked cross entropy と必要最小限の既存 auxiliary loss のみを使う。
- [ ] seed を Python / NumPy / PyTorch / CUDA に設定し、dataset order、epoch metrics、best epoch、validation top-1 一致率、loss を manifest へ保存する。
- [ ] `patience_epochs` による early stopping と non-finite loss/gradient の fail-closed を実装する。
- [ ] 長時間学習用表示は TTY の単一 progress bar または非 TTY の集約 snapshot だけにする。
- [ ] focused test と既存 `tests/test_submitted_opponents_r2d3.py` の learner test を通す。

---

### Task 6: 直接 checkpoint 転送と step 0 weight bundle

**Files:**

- Create: `src/mage_ptcg/bootstrap_champion/initializer.py`
- Modify: `src/mage_ptcg/policy_learning/r2d3/checkpoint.py`
- Create: `tests/test_bootstrap_champion_initializer.py`

**Interfaces:**

```python
def initialize_from_checkpoint(
    *, source_checkpoint: Path, champion: BootstrapChampionManifest,
    model_config: R2D3ModelConfig, output: Path,
) -> BootstrapCheckpointManifest: ...

def initialize_from_distillation(
    *, distilled_weights: Path, champion: BootstrapChampionManifest,
    model_config: R2D3ModelConfig, teacher_dataset_id: str, output: Path,
) -> BootstrapCheckpointManifest: ...

def load_bootstrap_weights(
    path: Path, *, model, target, expected_manifest: BootstrapCheckpointManifest
) -> None: ...
```

`bootstrap-checkpoint-v1` は、通常の `r2d3-checkpoint-v3` resume payload とは別契約にする。まだ Replay/population に結び付いていない重み転送 artifact であり、`load_checkpoint()` では読めない。

- [ ] source checkpoint の model config、action schema、deck binding、weight key/shape のどれかが違うと直接初期化を拒否するテストを書く。
- [ ] 直接初期化で online と target が source online と等しくなり、source target、optimizer、scheduler、Replay priority、step を引き継がないテストを書く。
- [ ] 模倣経路で teacher dataset ID が必須、直接経路で source checkpoint ID が必須になるテストを書く。
- [ ] 改変された weight file を SHA-256 の不一致で拒否するテストを書く。
- [ ] `load_checkpoint()` が `bootstrap-checkpoint-v1` を resume checkpoint として受け入れない回帰テストを書く。
- [ ] focused test の失敗を確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_initializer.py
```

- [ ] source checkpoint から online weights だけを抽出する safe reader を追加する。pickle payload の schema と全必須 field を検査し、未知 schema は拒否する。
- [ ] `bootstrap-checkpoint-v1` に model weights、model config hash、action schema hash、deck hash、Champion ID、initialization mode、source provenance を保存する。optimizer/replay/RNG resume state は格納しない。
- [ ] load 時に online model へ重みを入れた後、target model へ online state をコピーし、全 target parameter を `requires_grad=False` にする。
- [ ] atomic save、fsync、weight hash、manifest hash、同一 output 再実行の内容一致を実装する。
- [ ] focused test と既存 checkpoint round-trip test を通す。

---

### Task 7: CLI orchestration、resume、progress 表示

**Files:**

- Modify: `src/mage_ptcg/continuous_league/cli.py`
- Modify: `src/mage_ptcg/continuous_league/learner_service.py`
- Create: `src/mage_ptcg/bootstrap_champion/pipeline.py`
- Modify: `tests/test_continuous_league_cli.py`
- Create: `tests/test_bootstrap_champion_pipeline.py`

**CLI:**

```text
continuous_league.py bootstrap-build-candidates
continuous_league.py bootstrap-screen
continuous_league.py bootstrap-validate
continuous_league.py bootstrap-collect-teacher
continuous_league.py bootstrap-initialize
continuous_league.py bootstrap-status
continuous_league.py learn --bootstrap-checkpoint ... --bootstrap-manifest ...
```

- [ ] 各 bootstrap command の required argument、mutually exclusive argument、output path が parser test で固定されるようにする。
- [ ] `bootstrap-initialize` が Champion の initialization mode に応じ、source checkpoint または teacher dataset の片方だけを求めるテストを書く。
- [ ] `learn` で `--resume` と `--bootstrap-checkpoint` の同時指定を拒否するテストを書く。
- [ ] bootstrap 学習で model/target だけが初期化され、optimizer、scheduler、Replay priority、RNG、learner step は新規になるテストを書く。
- [ ] CLI に渡した `--deck` と Champion/BootstrapCheckpoint manifest の `deck_hash` が違う場合に学習開始前に失敗するテストを書く。
- [ ] stage 完了 manifest がある場合の resume で完了 stage を再実行せず、中途な result は schedule identity 一致時のみ再利用するテストを書く。
- [ ] focused test の失敗を確認する。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_continuous_league_cli.py \
  tests/test_bootstrap_champion_pipeline.py
```

- [ ] 6 command を `continuous_league.cli` へ追加し、それぞれの input/output manifest を明示的にする。ワンコマンドの大きな暗黙 pipeline は作らない。
- [ ] `pipeline.py` は stage state と resume 判定だけを担い、CABT、ランキング、学習の実装は各 module へ委譲する。
- [ ] `learn --bootstrap-checkpoint` は `load_bootstrap_weights()` 後に新 optimizer/scheduler/replay を構築し、`learner.steps=0` で開始する。`--resume` は従来通り全状態を復元する。
- [ ] 最初の通常 checkpoint 保存時に `bootstrap_champion_id` / `bootstrap_checkpoint_id` / `deck_hash` を training identity に含める。
- [ ] TTY の単一 progress bar に `stage`, `done/total`, `games/s` または `updates/s`, `ETA`, `faults` を表示する。非 TTY は約10秒ごとの同じ集約値と atomic `progress_summary.json` にする。
- [ ] focused test、`tests/test_continuous_league_learning.py`、`tests/test_continuous_league_cli.py` を通す。

---

### Task 8: 少数局 E2E、正典文書、検証

**Files:**

- Create: `configs/continuous_league/bootstrap_champion.example.yaml`
- Modify: `docs/runbooks/continuous-league.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Create: `tests/test_bootstrap_champion_e2e.py`

**Example config:**

```yaml
schema_version: bootstrap-champion-config-v1
qualification_games_per_candidate: 4
screen_games_per_candidate: 256
screen_finalists: 4
validation_games_per_candidate: 1024
screen_seed_namespace: bootstrap-screen-v1
validation_seed_namespace: bootstrap-validation-v1
teacher_split:
  train: 0.8
  validation: 0.2
teacher_outcome_weights:
  win: 1.0
  draw: 0.5
  loss: 0.25
```

- [ ] fake submitted policy 2 件、deck 2 件、合成 benchmark を使い、candidate build → smoke → screen → validation → Champion → teacher dataset → step 0 → learner 1 update を少数データで通す E2E テストを書く。
- [ ] E2E で Champion の deck hash が teacher、bootstrap checkpoint、training identity まで一貫することを検証する。
- [ ] 別 deck または別 Stable ActionKey schema を途中で差し込んだ negative E2E が、RL update 前に失敗するようにする。
- [ ] 失敗 E2E が `deck.csv`、source snapshot、完了 artifact を変更しないことを検証する。
- [ ] E2E を先に実行し、未統合部分で失敗することを確認する。
- [ ] example config と runbook に、「外部資産取得」「候補固定」「予備選抜」「1,024 局最終選抜」「step 0 生成」「学習開始」のコピー可能な手順を追加する。
- [ ] runbook に `--resume` と `--bootstrap-checkpoint` の違い、既存 hard Grimmsnarl chunk の再利用判定、出力の確認方法を追加する。
- [ ] status 文書は実装済み/未実験を明確に分け、実際の 256/1,024 局を実行したと誤認させない。
- [ ] 次の順で検証し、1 つでも失敗したら完了としない。

```bash
PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_bootstrap_champion_contracts.py \
  tests/test_bootstrap_champion_intake.py \
  tests/test_bootstrap_champion_tournament.py \
  tests/test_bootstrap_champion_teacher.py \
  tests/test_bootstrap_champion_distillation.py \
  tests/test_bootstrap_champion_initializer.py \
  tests/test_bootstrap_champion_pipeline.py \
  tests/test_bootstrap_champion_e2e.py

PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/test_continuous_league_contracts.py \
  tests/test_continuous_league_cabt.py \
  tests/test_continuous_league_cli.py \
  tests/test_continuous_league_learning.py \
  tests/test_submitted_opponents_r2d3.py

.venv/bin/python scripts/docs/validate_docs.py
git diff --check
git status --short
```

- [ ] テスト後に 4〜8 局の CLI smoke を temporary root で実行し、実際の CABT 上で stage resume、manifest hash、teacher decision、step 0 load、learner 1 update を確認する。
- [ ] smoke 生成物は競技性能の根拠として使わず、temporary root のパスと pass/fail のみ報告する。
- [ ] `git diff --check` と `git status --short` で、今回分と先行する既存差分を分けて報告する。
- [ ] commit / push は行わず、ユーザーの次の明示指示を待つ。

## Implementation Completion Gate

実装作業を「完了」と報告してよいのは、次のすべてが確認できた場合だけとする。

| Gate | 合格条件 |
|---|---|
| Source | remote へ書かず、全 asset の source/hash を固定できる |
| Candidate | 互換性のある deck-policy 組だけが生成される |
| Tournament | 256/1,024 局の決定的な均等 schedule と ranking を合成テストで検証できる |
| Champion | 勝者の deck/policy/runtime/score/provenance が content-addressed manifest になる |
| Teacher | hidden information を持たず、合法 Stable ActionKey だけを教師にする |
| Step 0 | 直接転送と教師模倣の両方で online=target、fresh optimizer、step 0 になる |
| Learning | deck/manifest/weight identity を検査後、少なくとも 1 update が有限 loss で完了する |
| Regression | 対象 test、関連 continuous league/R2D3 test、docs validation、`git diff --check` がすべて pass |

1,024 局の実際の選抜実験と長時間 fine-tuning は、実装完了後の別タスクとする。
