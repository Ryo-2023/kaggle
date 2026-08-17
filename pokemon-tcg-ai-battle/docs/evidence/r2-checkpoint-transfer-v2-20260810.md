# R2 v1 checkpoint の representation v2 warm-start transfer 検証

## 結論

`checkpoint-cf5c974fc70b9…` は、現行 `representation_version=2` の
`SpecialistPolicyModelV1` へ**研究用の BC fine-tune 初期値候補としてのみ**移せる。
これは旧 checkpoint の強さ・CABT 成績・V-trace 改善を継承する証拠ではない。旧 checkpoint
の strict resume は引き続き不可だが、移送後は新規 optimizer/identity/recipe を持つ公式
`specialist-neural-checkpoint-v1` bootstrap として既存 runtime/BC loader へ接続できる。

## 一次確認

- source: `runs/from-worktree/meta-specialist-canonical/meta-specialist-training/t3-r8-alakazam/checkpoints/checkpoint-cf5c974fc70b909ae43b3bcc633a4003189e03b80e2c77e1597071bc2e8af371.pt`
- source SHA-256: `cf5c974fc70b909ae43b3bcc633a4003189e03b80e2c77e1597071bc2e8af371`
- checkpoint `training_identity.snapshot_id`: `a4e6475255ff7ac56469f87cfd0ca6214de749af`
- 旧モデルの確認: `git show a4e6475255ff7ac56469f87cfd0ca6214de749af:src/mage_ptcg/meta_specialist/neural_model_v1.py`
- 旧モデル schema: `specialist-neural-model-v1`。現行 target は
  `representation_version=2` / `specialist-neural-model-v2`。

## 移送規則

| 対象 | 扱い | 根拠 |
|---|---|---|
| 35 個の shape 完全一致 tensor | 明示 allowlist で完全コピー | 埋め込み、bag/single-card、candidate/query/value 等は input 意味と shape が一致 |
| `pokemon_encoder.weight` | 列名付き offset map で card ID と既存 6 scalar のみコピー。zone・energy type・attachment 由来列は 0 | v2 は card の後に zone を挿入するため prefix copy は誤対応 |
| `endpoint_encoder.weight` | card/host/zone/visibility/owner の 161 列だけコピー。nested Pokémon 128 列と presence flag は 0 | v2 だけが endpoint 内の Pokémon snapshot を読む |
| 上記 2 encoder の bias | コピー | 新規入力列を 0 にしたとき旧 affine 出力を保つ |
| `scalar_encoder.{weight,bias}` | target 初期値を保持（コピーなし） | v1 は全 41 scalar に `log1p`、v2 は categorical/flag を raw にするため、同じ列位置でも係数の意味が非線形に異なる |
| `pokemon_count_encoder.*`, `opponent_value_embedding.weight` | target 初期値を保持 | v2 専用 branch。source に対応 state はない |

入力 checkpoint は SHA-256、旧 top-level/metadata/training identity/model config の closed schema、
snapshot ID、全 state_dict key、各 tensor の exact shape/dtype/有限値を検査してからのみ受理する。
`strict=False`、key prefix、または shape prefix によるコピーは用いていない。

## 既存 pipeline 互換の bootstrap 発行

`publish_transferred_v2_bootstrap_checkpoint()` は transfer 後の v2 model に対して、新しい
AdamW state、step/sampler cursor = 0、v2 `model_config`、次の専用 recipe を載せて
`publish_checkpoint_v1()` で content-addressed publish する。旧 checkpoint の recipe、optimizer、
scheduler、RNG、training identity は流用しない。

```text
objective: research_legacy_v1_to_v2_transfer_bootstrap
transfer_schema_version: r2-checkpoint-transfer-v2
legacy_source_sha256: cf5c…
legacy_source_snapshot_id: a4e647…
column_map_version: r2-v1-to-v2-semantic-column-map-v1
```

`foundation_init` は `warm_start` とし、親 checkpoint SHA は legacy cf5c の SHA、teacher
provenance は legacy metadata を再検証して継承する。これにより BC 側が bootstrap をさらに
warm-start するときの親 SHA は、この新しい published bootstrap の SHA になる。

実生成物:

- path: `runs/meta-specialist-transfer-v2/alakazam/checkpoint-ad29d4f72ccd8cea5187bb8e8e88366ced0c22e740a182f02fe5a1f0eeb11338.pt`
- content SHA-256: `ad29d4f72ccd8cea5187bb8e8e88366ced0c22e740a182f02fe5a1f0eeb11338`
- target runtime snapshot: `a178366edf04886f5cef44442e14e1ec41110976`。未コミットの現行
  `src/mage_ptcg/meta_specialist/neural_model_v1.py` の Git blob SHA-1 であり、commit SHA と
  偽装していない。payload の model schema は `specialist-neural-model-v2`。

以前の `68c906…` artifact は `HEAD` を runtime snapshot として誤記したため、比較には用いない。
削除は行わず、正しい source snapshot を持つ上記 `ad29d4…` を唯一の候補とする。

## 実測した互換性 oracle

次のローカル実行で成功した。

```text
PYTHONPATH=src .venv/bin/python -c '<cf5c を transfer_v1_checkpoint_to_v2 へ渡す検証>'
```

- 35 tensor を exact-copy、4 tensor を意味 map、5 tensor を新規初期値として provenance 化。
- 移送後の公式 checkpoint を保存後、現行 v2 model の `load_state_dict(..., strict=True)` が成功。
- actor-visible の実 runtime fixture で `step_logits` を実行し、semantic/STOP logits が有限値。
- `load_checkpoint_for_inference_v1`、`load_specialist_neural_policy_from_checkpoint_v1`、
  `train_from_trajectories_v1._load_bootstrap_weights_v1` の3経路を実生成物 `ad29d4…` で通過。
  後者は published name/hash を検査し、weights-only strict load を実行する。

## BC fine-tune 候補としての判断

妥当なのは、同一 v2 training data・同一 seed protocol で scratch/v2smoke 初期化と
**held-out BC NLL**を比べる初期値候補としてである。評価には transferred/scratch の loss curve、
finite/fault、複数 seed を必要とする。現 runtime-compatible v2smoke は接続 screen の
2 opponent × 2 seat（4 局）で 1W3L=0.25、fresh rollout 2 局からの 1e-4 V-trace 1 update後も
1W3L=0.25 であり、いずれも採用や強さの証拠には不足する。

失われる情報は、scalar encoder が学んだ v1 の count/flag scale、v2 が新たに露出した
zone/energy composition/attachment identity、endpoint の nested Pokémon 表現、Pokémon 数、
opponent-conditioned value、ならびに全 optimizer/scheduler/RNG/training-run 状態である。

## 4局 held-out CABT screen（比較用のみ）

既存 `perf-sprint-connect-alakazam-4.json` の対象（`kiyotah_lucario`,
`sue124_alakazam`）、両 seat、各 1 rep、greedy decoding、`max_steps=2000` と同じ
`measure_opponent_strength.py` 経路で測定した。既存 JSON は base seed を保存していないため、
script の既定値である `9100000` を明示し、v2smoke baseline も同じ command で再測定した。

| checkpoint | W-D-L | faults | score | seat 0 / 1 | artifact |
|---|---:|---:|---:|---:|---|
| transfer `ad29d4…` | 2-0-2 | 0 | 0.50 | 1.00 / 0.00 | `runs/meta-specialist-strength/r2-checkpoint-transfer-v2-alakazam-ad29d4-4.json` |
| v2smoke `784917…`（同一 seed 再測定） | 2-0-2 | 0 | 0.50 | 1.00 / 0.00 | `runs/meta-specialist-strength/v2smoke-alakazam-784917-seed9100000-4.json` |

両方の 95% Wilson interval は `[0.150, 0.850]` で重なり、同一 screen では差を観測していない。
既存 artifact `perf-sprint-connect-alakazam-4.json` は 1-0-3 / 0.25 であるが、base seed が記録
されていないため、今回の seed 固定比較と混在させない。いずれも N=4 の接続確認であり、
transfer の強さ、BC improvement、採用を断定する証拠ではない。

再現コマンド（checkpoint だけを差し替えて両方に実行）:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/measure_opponent_strength.py \
  --subject-checkpoint <checkpoint> \
  --subject-deck-csv opponents/nihei_alakazam/deck.csv \
  --subject-archetype-id alakazam \
  --opponent-ids kiyotah_lucario,sue124_alakazam \
  --games-per-opponent-seat 1 --base-seed 9100000 \
  --output runs/meta-specialist-strength/<artifact>.json
```

## Gate 1 sealed 32 records の complete-action NLL（初期値指標のみ）

`gate1-input-alakazam.json` を `_read_gate_input_v3` / `_gate_steps_from_input_v3` で
再読込・再 materialize した。これにより snapshot/teacher/permission/vocabulary/split bytes を
再検証し、canonical `semantic_loss_rows_from_record_v2` を唯一の target とした。forced sole
STOP はこの loss-row contract の時点で除外される。split は train 26 / validation 6 records で、
今回の選択では各 record が 1 canonical loss row だったため complete-action NLL と token NLL は
同値である。

| checkpoint | train complete-action NLL (N=26) | validation complete-action NLL (N=6) | transfer − v2smoke (validation) |
|---|---:|---:|---:|
| transfer `ad29d4…` | 0.755460 | 0.769725 | −2.725953 |
| v2smoke `784917…` | 7.804211 | 3.495678 | — |

NLL が低い transfer は、同一 sealed target における **BC fine-tune 初期値候補としての弱い
選択指標**を満たす観測である。ただし validation は 6 records だけであり、seed repeat、より大きい
sealed corpus、fine-tune curve、fault と CABT は別途必要である。これを強さ、generalization、採用の
確定証拠として扱わない。

artifact: `runs/meta-specialist-strength/r2-checkpoint-transfer-v2-alakazam-gate1-nll.json`

実行では次の既存 Gate 1 helpers をそのまま使った（private API への薄い評価 wrapper のみで、
モデルや target 処理を変更していない）。

```bash
PYTHONPATH=.:src .venv/bin/python /tmp/r2_gate1_nll.py
```

wrapper は `representation_benchmark_v3._read_gate_input_v3`、`_gate_steps_from_input_v3`、
`_distribution`、`_soft_nll` と既存 checkpoint inference loader を呼び、checkpoint ごとに train/
validation の token losses を record 単位で和して平均する。

## 24局 held-out CABT screen

4局では差が出なかったため、同じ6 held-out opponent、両seat、各2反復へ拡大した。両checkpoint
とも `base_seed=9200000`、greedy decoding、`max_steps=2000` を固定した。

| checkpoint | W-D-L | score | faults | seat 0 / 1 |
|---|---:|---:|---:|---:|
| transfer `ad29d4…` | 8-0-16 | 0.333 | 0 | 0.250 / 0.417 |
| v2smoke `784917…` | 9-0-15 | 0.375 | 0 | 0.500 / 0.250 |

transfer は1勝分下回り、24局でも実勝率の優位を観測できなかった。従って現時点の実行baselineは
v2smokeを維持し、transferは sealed BC NLLの低い fine-tune初期値候補に限定する。標本誤差が大きく、
この結果だけでtransferが弱いとも断定しない。

- transfer artifact: `runs/meta-specialist-strength/r2-checkpoint-transfer-v2-alakazam-ad29d4-24.json`
- baseline artifact: `runs/meta-specialist-strength/v2smoke-alakazam-784917-seed9200000-24.json`

## 実装とテスト

- `src/mage_ptcg/meta_specialist/r2_checkpoint_transfer_v2.py`: research-only migrator、
  finite runtime probe、公式 bootstrap publisher。
- `tests/meta_specialist/test_r2_checkpoint_transfer_v2.py`: offset map、scalar 非コピー、
  SHA/schema fail-closed、finite forward、runtime/BC bootstrap load の計 6 テスト。
- 実行: `PYTHONPATH=src .venv/bin/python -m pytest tests/meta_specialist/test_r2_checkpoint_transfer_v2.py -q`
  → `6 passed`。
