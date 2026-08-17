# V4 評価ノイズ・CABT seed・recurrent ablation 監査

確認日: 2026-08-12  
担当: Codex subagent `/root/eval_noise_recurrence`  
目的: ChatGPT Pro レビューの最優先項目である「評価ノイズの分離」「CABT の真の seed/pairing 可否」「normal carry / reset every complete action / reset every turn の接続点」を、既存実装を変更せずに再監査する。

## 結論

1. 現在の公式 CABT (`kaggle-environments==1.32.0`) には、既存の Python runner から利用できる engine RNG setter がない。`run_match(seed=...)` の `seed` は両 agent factory に渡されるだけで、CABT engine の `BattleStart` へは渡されない。
2. 同じ deck、同じ deterministic agents、同じ `seed=123456` を同一プロセスで繰り返しても、完全な replay digest と episode 長が毎回変わった。したがって現在の比較は game-level paired evaluation ではなく、同一スケジュールの独立・層化評価である。
3. McNemar、paired bootstrap、同一 seed だから同じ初期 deck/shuffle だったという解釈は現行 engine では無効である。`base-seed` は agent 側の Python/NumPy randomness と provenance を変えるだけで、engine の hidden randomness を固定しない。
4. V4 の recurrent state は `SpecialistNeuralPolicyV4._recurrent_state` に保持され、`begin_decision()` が incoming hidden を読み、complete action の `session.commit()` で next hidden を保存する。通常の policy はこれが game 内で carry され、policy object は `make_agent()` ごとに fresh なので game 間では reset される。
5. recurrent ablation は `MetaSpecialistRuntime.reset()` を turn ごとに呼んではいけない。同メソッドは recurrent state だけでなく `_registered` と terminal/trace counters を初期化するため、game 中に呼ぶと次の decision が deck 未登録として失敗する。必要なのは policy 内の recurrent state だけを reset する research-only hook である。

## 1. CABT seed setter / pairing のコード監査

### 1.1 runner から engine へ seed は伝播しない

`scripts/test_sim.py:208-224` の `run_match` は `seed` を受け取るが、`scripts/test_sim.py:252-260` で agent factory に渡すだけである。環境生成は `scripts/test_sim.py:263-268` の次の呼び出しで、configuration は deck と `episodeSteps` のみである。

```python
env = make(
    "cabt",
    configuration={"decks": [deck_a, deck_b], "episodeSteps": max_steps},
)
episode = env.run([agent_a, agent_b])
```

`configuration={"seed": seed, ...}` へ変更した既存実装はなく、現行 CABT schema にも seed property はない。

### 1.2 Python binding の ABI に seed 引数がない

`kaggle_environments/envs/cabt/cg/game.py:31-40` は二つの 60 枚 deck を一つの `ctypes.c_int` 配列にして `lib.BattleStart(arg)` を呼ぶ。`cg/sim.py:36-37` の ctypes declaration も `BattleStart.argtypes = [ctypes.POINTER(ctypes.c_int)]` だけであり、engine seed setter の binding は存在しない。

`cabt.py:121-134` の interpreter も deck を取り出して `battle_start(state[0].action, state[1].action)` を呼ぶだけである。ゲーム開始時の engine randomness を Python の `random.seed`、NumPy seed、PyTorch seedで制御する経路はない。

### 1.3 native library symbol 監査

確認した library:

```text
.venv/lib/python3.12/site-packages/kaggle_environments/envs/cabt/cg/libcg.so
SHA-256: 7acbfc7bc61d4f8233515c63debcfa454b8f804f138a6c395c599decc3dd17d0
```

公開 T symbol は `GameInitialize`, `BattleStart`, `BattleFinish`, `GetBattleData`, `Select`, `VisualizeData` 等で、`Seed`, `SetSeed`, `Rng`, `Random` の setter symbol はない。`strings` では `std::random_device`、`std::shuffle`、`uniform_int_distribution` を確認した。

```text
nm -D --defined-only .../libcg.so | rg -i 'seed|random|battle|game'
0000000000007680 T BattleFinish
000000000000ea70 T BattleStart
000000000000f020 T GameInitialize
000000000000b700 T GetBattleData
0000000000010b00 W _ZNSt13random_deviceC1Ev
0000000000010b00 W _ZNSt13random_deviceC2Ev
```

これは「seed setter をまだ見つけられていない」という状態ではなく、現在配布されている Linux native CABT ABI に setter がないという証拠である。

### 1.4 capability report の既存契約

`scripts/cabt_capability.py` は engine seed capability を明示的に `False` としており、`tests/test_cabt_capability.py::test_ready_and_version_mismatch_are_distinct` も READY 時に `engine_seed_supported is False` を要求する。

実環境での確認結果:

```text
package: kaggle-environments==1.32.0
status: READY
actual_execution_allowed: true
engine_seed_supported: false
python: 3.12.3
```

### 1.5 同じ seed の replay digest 実証

実行した条件:

- deck: `opponents/tomatomato_archaludon/deck.csv` を両 player に使用
- agent: `make_deterministic_agent` を両 player に使用
- `seed_agent_randomness_v1(123456)` を各 replay の直前に実行
- `make("cabt", configuration={"decks": [deck, deck], "episodeSteps": 2000})`
- 同一 Python process で 5 回実行
- `env.steps` を一時的に canonical JSON 化して SHA-256 を計算（private replay 本体は保存していない）

結果:

| repeat | replay digest | episode steps |
|---:|---|---:|
| 0 | `08a4da16966c17418fe47626c12308d579df4cee19b012d90f79a3cbb86a65a0` | 108 |
| 1 | `c0b0c602067641dc0892484fdee42dbd54bf6f0fedc104960b8efdc671f05b88` | 83 |
| 2 | `6b133437b1213f4ededd438d95191d76fb96717c8a7abc880baf32b5745e5d1c` | 122 |
| 3 | `553cd19b6e4bc36e95be4d9da79c32f9ca6a7abbcc27fc20e9bd2bb6404663a1` | 152 |
| 4 | `4a2df53a442d767a02e1d835c95803c1b951e82234389f51fe6692681e34659f` | 96 |

全 5 replay の digest は一致せず、episode 長も一致しない。従って同じ `seed` は engine initial state / draw / coin / shuffle を再現していない。agent 側が deterministic でもこの差は残った。

この実証は競技性能の勝率推定ではなく、pairing capability の診断である。private replay の中身は artifact として保存していない。

## 2. 現行評価の解釈と安全な反復契約

### 2.1 paired と呼べる条件

game-level paired evaluation と呼ぶには、少なくとも次が同時に必要である。

1. engine RNG setter が存在し、同一初期 deck/order/hidden rolls を再現できる。
2. candidate と baseline が同一 engine random stream を使う。
3. game-level ledger に candidate/baseline の対応 pair と replay/state/action digest がある。
4. engine seed capability と replay verification が report に attested される。

現行 CABT は 1 を満たさないため、同じ `base-seed` を使った candidate と baseline でも paired ではない。現状の `strict-paired` 等の名称は training data/seed 対応を表すだけで、統計的な game pairing を表さない。

### 2.2 最小の反復評価 runner（既存 evaluator の安全な利用法）

新しい実装を急いで追加せず、既存の hash-bound evaluator を repetition ごとに別 output へ呼び出す。

対象 command:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v4_checkpoint_strength.py \
  --checkpoint <CHECKPOINT.pt> \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon \
  --opponent-count 6 \
  --games-per-seat 8 \
  --base-seed <BLOCK_BASE_SEED> \
  --max-steps 2000 \
  --output runs/v4-eval-noise/<checkpoint-id>/block-<block>.json
```

この設定は 6 opponents × 2 seats × 8 repetitions = 96 games/block である。block ごとに output を新規 path へ書き、既存 artifact を overwrite しない。`base-seed` は block identity と agent-side stream を分けるために `30100000`, `30200000`, `30300000` のように disjoint にする。ただし、この値を engine seed と解釈してはいけない。

各 report で必ず確認する項目:

- `checkpoint.file_sha256` と `checkpoint.tensor_state_sha256` が全 block で一致。
- `subject_deck_file_sha256`, `evaluation_protocol_sha256`, `evaluation_implementation_sha256` が一致。
- opponent IDs と opponent fingerprints が一致。
- `requested_games == games_played`、`faults == 0`、`comparison_status == "valid"`。
- seat/opponent 別 wins/draws/losses を保持し、block 間のばらつきを計算。

推定は candidate/baseline の勝敗を同一 pair として処理せず、`block × opponent × seat` 層の独立評価として行う。平均差、block SD、opponent/seat variance を報告する。engine seed が導入されない限り McNemar/paired bootstrap は実行しない。

### 2.3 反復対象 checkpoint と局数

最初に固定 six で評価する順序:

| 優先 | checkpoint | 目的 | 1 block | 推奨 block 数 |
|---:|---|---|---:|---:|
| 1 | Wave6 seed0 best | baseline 自身の evaluation noise | 96 | 3 |
| 2 | Wave6 seed1 best | training seed 間の差と noise の分離 | 96 | 3 |
| 3 | tomatomato-96 candidate seed0 best | 既存 full-fine-tune candidate の再現性 | 96 | 3 |
| 4 | tomatomato-96 candidate seed1 best | 同上 | 96 | 3 |

最大で 4 checkpoints × 3 blocks × 96 = 1,152 games だが、長時間学習ではない。第一段階は Wave6 seed0/seed1 の 2 checkpoints × 3 blocks = 576 games とし、within-checkpoint SD を先に得る。その後 candidate 2 checkpoints を同じ設計で測る。48 games/block（`--games-per-seat 4`）は exploratory pilot、96 games/block は確認用である。

この診断が終わるまで shadow-C、longrun、Champion 変更、Kaggle 提出は行わない。

## 3. recurrent ablation の実装接続点

### 3.1 normal carry（現行）

`src/mage_ptcg/meta_specialist/neural_policy_v4.py:240-257` で、policy object ごとに `_recurrent_state: Tensor | None` を持つ。

- `begin_decision()` は現在の `_recurrent_state` を `SpecialistNeuralDecisionSessionV4` に渡す。
- session は一つの complete action の全 prefix を同じ incoming hidden から評価する。
- `session.commit()` の callback が `next_hidden` を policy の `_recurrent_state` に保存する。
- `SpecialistNeuralPolicyV4Factory.new_policy()` は game ごとに fresh policy を返すため、game 間の hidden leakage はない。

これが `normal carry` の正確な定義である。prefix ごとの hidden update は行われず、complete action commit ごとに一度だけ更新される。

### 3.2 reset every complete action

安全な ablation は policy recurrent state だけを `begin_decision()` 前に `None` にすることである。

研究用の最小 hook の意味:

```python
class ResetEveryActionPolicy:
    def begin_decision(self):
        self.inner.reset_recurrent_state_only()
        return self.inner.begin_decision()
```

正式実装時は `SpecialistNeuralPolicyV4.reset()` 相当の state-only API を明示化するか、V4 evaluator 内の research-only wrapper に限定する。`MetaSpecialistRuntime.reset()` を代用してはいけない。

### 3.3 reset every turn

actor-visible C1 v2 には `turn` と `turn_action_count` が存在する。従って callback 受信時に前回 `turn` と比較し、turn が変わったときだけ policy recurrent state を reset できる。

必要な接続:

1. `MetaSpecialistRuntime.__call__` が validated actor-visible state の `turn` を policy の research-only `observe_turn(turn)` hook へ渡す。
2. policy wrapper は `last_turn` を保持し、値が変化したときだけ recurrent state を None にする。
3. `_registered`, `_terminal`, trace counters は保持する。

現行 `SpecialistDecisionPolicyV2.begin_decision()` に turn 引数はないため、外側の callback wrapperだけで完全に正しく実装することは難しい。policy の private `_recurrent_state` を直接触る暫定 monkeypatch は smoke 用に限り、正式な評価 arm では state-only hook を追加して hash-bound evaluator に含めるべきである。

### 3.4 2-game smoke（接続可否のみ）

Wave6 seed0 best、固定 six の先頭 opponent (`kiyotah_lucario`)、各 seat 1 game（計 2 games）で、既存 evaluator を in-memory monkeypatch して次を実行した。

- normal carry: 0/2, faults 0
- reset every complete action: 0/2, faults 0
- reset every turn: 2/2, faults 0

これは engine が同じ seedでも同じ replay を作らないため、性能差の証拠ではない。各 arm は同じ checkpoint/protocol で合法に完走することだけを確認した smoke である。reset-turn の 2/2 はこの 2 局の random draw による値であり、ablation の優劣を意味しない。

正式な ablation では各 mode を別 independent arm として扱い、同じ block schedule を記録する。normal/action/turn を同じ engine pair とみなして比較しない。

## 4. 固定 six での実行順序（推奨）

1. Wave6 seed0/seed1 の normal carry を 96 games/block × 3 blocks。
2. 同じ 2 checkpoints の reset-every-action を 96 games/block × 3 blocks。
3. 同じ 2 checkpoints の reset-every-turn を 96 games/block × 3 blocks。
4. 各 mode の within-checkpoint SD と block/opponent/seat 分散を計算。
5. `reset >= normal` が block 平均・両 seedで再現すれば recurrence の有害/不要仮説を支持し、full recurrent fine-tune を続けず短期 memory または明示履歴へ移る。
6. `normal > reset-every-action` かつ `normal >= reset-every-turn` なら recurrence を維持し、次の候補は TBPTT/anchor stability の監査。ただし GRU 拡大や長時間学習へ直行しない。
7. 評価 noise SD が candidate 改善幅と同程度以上なら、24-game screen の seed反転は性能結論に使わない。

この順序ではまず Wave6 自身の noise floor を測るため、candidate の一時的な勝率上昇を「学習改善」と誤認しにくい。shadow-C は recurrence mode と candidate の選択が終わった後に freeze し、最後まで untouched のまま保持する。

## 5. 実行していない項目 / 制約

- 96 games × 3 blocks の本評価は、Wave6 seed0/seed1について後続の専用結果資料 `docs/evidence/v4-eval-noise-results-20260812.md` で実行済みである。本資料の上記接続監査と併せて読むこと。action-reset / turn-resetの本格評価はまだ実行していない。
- CABT native engine を再ビルドしたり、`libcg.so` を patch して seed setter を追加する操作はしていない。競技 engine の ABI を改変すると現在の評価正典から外れるためである。
- fixed-six の既存 24/96 reports は、同じ base seed を記録していても game-level paired evidence には昇格させない。
- recurrence ablation の 2-game smoke は private replay を保存していない。評価結果を promotion evidence として使用してはならない。

## 6. 参照 artifact / hash

- evaluator: `scripts/measure_v4_checkpoint_strength.py`  
  evaluation implementation SHA（現行 report の値）: `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835`
- protocol: `heldout_protocol_sha256_v1()`  
  現行 fixed-six protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- CABT Python binding: `.venv/lib/python3.12/site-packages/kaggle_environments/envs/cabt/cg/game.py` SHA `c4699ecbe617013349895992ae493d17486de72fec85c798cdfabc06d7260e41`
- CABT ctypes binding: `.venv/lib/python3.12/site-packages/kaggle_environments/envs/cabt/cg/sim.py` SHA `a5aee75dfe3d70a9622a5e8369ff01b79b22d4b7d026ca44027143ce4672b048`
- CABT Python interpreter: `.venv/lib/python3.12/site-packages/kaggle_environments/envs/cabt/cabt.py` SHA `83966930d12da5d8f725ac70314bf1b58b842180277437dc4ea7dc6ceea0d176`
- runner: `scripts/test_sim.py` SHA `b920133088bb09aa0da10891e856b31ab6d8a51b27d083a3a5942e1319c379c5`
- capability diagnostic: `scripts/cabt_capability.py` SHA `8c6bfd4b53a49948552cb9d631c77324ab7c28eabc7d408af52b768d81633094`
