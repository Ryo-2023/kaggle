# V4 coarse complete-action normalization preflight (2026-08-12)

## 結論

研究専用の合成 fixture に対して、prefix 単位の signed residual を物理
complete action 単位へ集約する正規化契約を実装し、4 件の focused test を
通過させた。これは現行 V4 trainer、residual factory、CABT evaluator へは
接続していない。したがって性能改善、学習許可、promotion、long-run の根拠
ではない。

## 目的

現在の signed residual v1 は prefix row をそのまま weight 付きで扱うため、
同じ physical action の prefix 数、record 長、episode 長が gradient mass と
交絡する可能性がある。この preflight では、次の二つを固定した。

* `record_normalized`: 一つの physical record に一つの signed target を割り当て、
  prefix 数に依存しない総絶対 mass へ分配する。
* `episode_normalized`: 上記に加え、episode 内の record の総絶対 mass を 1 に
  正規化する。

base logits は detached な sealed input とし、最小の coarse bucket/action
table だけを更新する。可変長 domain は row ごとに扱い、padding で別の合法手を
作らない。

## 実装

* `src/mage_ptcg/meta_specialist/signed_residual_normalization_v1.py`
  * `SignedPrefixWeightV1`
  * `normalize_signed_prefix_weights_v1`
  * closed schema、連続 prefix index、episode/record 整合、authority 全 false
* `src/mage_ptcg/meta_specialist/coarse_record_residual_trainer_v1.py`
  * `CoarsePrefixLogitRowV1`
  * `normalize_complete_action_rows_v1`
  * zero-init bounded residual table
  * record group ごとの signed complete-action loss、anchor KL、residual L2
  * residual parameter だけを SGD 更新

`CoarsePrefixLogitRowV1` は `episode_id`、`record_id`、`prefix_index`、
public bucket SHA、sorted semantic action SHA、base logits、target index、
signed weight を保持する。unknown bucket/action、非有限値、target domain
外、prefix index 欠落は fail-closed で拒否する。

## focused 実験

fixture は同一 physical record を prefix 1 件または 4 件へ展開し、同じ
signed target を与えた。`record_normalized` では prefix 数を変えても
record の総絶対 mass が一致することを確認した。別 episode を加えた
`episode_normalized` では各 episode の総絶対 mass が 1 になることを確認した。
zero-init table の bounded residual は 1 update 後も設定上限以下で、base
logit tensor は table の optimizer 対象外である。非有限 base logit と未知
bucket の fail-closed も確認した。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test-coarse PYTHONPATH=.:src \
  .venv/bin/pytest -q -s \
  tests/meta_specialist/test_coarse_record_residual_trainer_v1.py
```

結果: `4 passed`（pytest fixture 側の requires-grad scalar warning 1 件のみ）。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/coarse_record_residual_trainer_v1.py \
  src/mage_ptcg/meta_specialist/signed_residual_normalization_v1.py
```

結果: pass。`git diff --check`: pass。

## 未実施と次の接続条件

実データから coarse bucket ごとの base logits/complete-action target を
materialize していない。従って本 artifact の loss は実戦性能を表さない。
次に進むには、seed 対応の Wave6 checkpoint と actor-visible transition の
replay から、record group ごとの logits と cross-fitted target を同じ順序で
再生成し、train/validation/test 境界、reference bundle SHA、coarse gate
coverage、zero-init parity を固定する必要がある。さらに state-value baseline
を導入する場合、`G_t - V_hat_heldout(s_t)` を別 target kind として分離し、
global episode mean の旧 target と混同してはならない。

`promotion_authority=false`、`training_permitted=false`、
`longrun_allowed=false`、`performance_evidence=false` は本 preflight の
不変条件である。
