# Autonomous Meta-Fine-Tuning: submission permission / package closure audit (2026-08-13)

## 結論

**現時点の Strong Asset native pair は提出不可、ただし指定済み teacher の
許可済み trajectory から作る自前 student は、外部 native code を混入させず、
別途 bundle-allowed の自前 deck と実行時閉包を満たせば提出候補にできる。**

従って判定は次のとおりである。

| 経路 | 許諾判定 | package 判定 | 総合 |
|---|---|---|---|
| tomato / Lucifer / plamen native `main.py` をそのまま同梱 | 不可 | 不可 | **NO-GO** |
| pool asset を local arena の opponent として実行 | 可 | 該当なし | **GO (local eval only)** |
| tomato / Lucifer / plamen の許可済み行動 record をローカル学習に使う | 可（限定） | 該当なし | **GO (training-local)** |
| 上記3 teacher の行動から導出された自前 θ0 / student weight | 判断記録上は可、source code 非同梱が条件 | V4 route は未閉鎖 | **CONDITIONAL GO** |
| tomato / Lucifer / plamen / R7 の外部 deck.csv を提出 deck にそのまま使う | 未許可 | `bundle_allowed` gate 不通過 | **NO-GO / 外部判断待ち** |
| root `deck.csv` + Rule v0 | 可 | archive-only smoke 済み | **PACKAGE GO（性能GOではない）** |
| Strong Asset teacher 起点・自前student + 自前 deck | 条件付き可 | 下記の閉包作業が必要 | **最短提出経路** |

この文書は提出を行わない read-only 監査である。Kaggle API/CLI、archive build、
CABT、学習、`main.py`、`deck.csv` は変更していない。

## 1. 根拠の優先順位と対象

評価対象は `opponents/pool_manifest.json`、各 asset の `SOURCE.md`、teacher
dataset manifest、判断記録、submission builder/entrypoint、既存 self-trained
checkpoint と package evidence である。source の利用境界と提出形式が矛盾する場合、
以下を優先する。

1. 各 asset の `SOURCE.md` と `opponents/pool_manifest.json` の `usage_boundary`。
2. 導出重みについての明示的な人間判断
   [`docs/decisions/2026-08-05-archaludon-teacher-derivation.md`](../decisions/2026-08-05-archaludon-teacher-derivation.md)。
3. bundle gate の実装（`src/mage_ptcg/meta_specialist/package.py`、
   `src/mage_ptcg/meta_specialist/runtime.py`）と既存 package evidence。
4. Kaggle 提出契約の未確認事項
   [`docs/evidence/kaggle-submission-contract.md`](kaggle-submission-contract.md)。

`pool_manifest.json` は 102 asset をすべて `local_eval_only` としている。selected
asset の現在の identity は次のとおりである。

| asset | policy SHA-256 | raw deck SHA-256 | smoke | source / boundary |
|---|---|---|---|---|
| `tomatomato_archaludon` | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` | true | public / `local_eval_only` |
| `lucifer19_battlecore` | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | true | public / `local_eval_only` |
| `plamen06_steel` | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` | true | public / `local_eval_only` |
| `public_archaludon_cinderace_r7` | `c08588467c3faa2cbc748703acc8e7099c6362c32747c84cb2cec8131d6a4ca3` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` | false | public / `local_eval_only` |

R7 の source header はさらに明示的に「possible teacher / never packaged into a
submission bundle」と記載する。tomato/Lucifer/plamen の `SOURCE.md` も local
offline evaluation 専用、再配布なし、as-is 提出なしとする。従って native agent
source を提出 archive に含める解釈はない。

## 2. 利用種別ごとの判定

### 2.1 ローカル評価

可。`OpponentPoolV1` は pool asset を opponent としてロードする目的であり、同 module
の docstring は `bundle_allowed` 以外を bundle 経路へ渡さない責務を package 側に残す。
local arena、BestKnown ranking、meta distribution 構成はこの境界内である。

ただし `local_eval_only` は、teacher、behavior collection、training、submission を
自動的に許可しない。`configs/meta_specialist/autonomous_meta_distribution_v1.json` も
`training_authority=false`、`submission_authority=false` と固定している。

### 2.2 training labels / behavior trajectory

限定可。tomato の96局teacher dataset と Lucifer の48局/96局teacher dataset は、各
`teacher_dataset_manifest.json` 内の trusted permission manifest が
`allowed_usages=["training-local"]` であることを記録する。具体的な permission manifest
ID は tomato `441a6b83...42956ca0`、Lucifer `83074da0...ad74a70f` であり、issuer は
`docs/decisions/2026-08-05-archaludon-teacher-derivation.md` である。

これは chosen action と public-state record をローカル学習に利用できることを意味する。
native teacher の確率/logit、source code、外部 deck の提出利用を許可するものではない。
plamen については判断記録では同じ derivation-qualified 3 teacher に含まれるが、今回
監査で既存の plamen teacher dataset/permission manifest は確認できなかった。そのため
plamen の新規 behavior collection は、同判断記録を引用して permission manifest を
materialize してから開始する必要がある。

### 2.3 導出された自前modelの提出

**条件付きで可能。** 判断記録は tomato/Lucifer/plamen の「挙動を蒸留した重み」を
submission bundle に含まれる方策の初期値 θ0 に使うことを対象として明示的に許可する。
一方で、同じ判断記録は `main.py` またはその改変物を bundle に含めることを禁止し、
deck.csv の提出可否を別問題として残す。

`FoundationInitProvenanceV1` も `derivation_qualified` と decision ref が無い teacher を
fail-closed で拒否する。従って final student は最低限、以下を checkpoint/package
manifest に固定する必要がある。

- teacher policy SHA、teacher dataset/permission manifest SHA、decision ref。
- student/weight file SHA と tensor/state SHA（該当する形式の場合）。
- FoundationInit、warm-start lineage、training data split、meta schedule SHA。
- bundle に teacher `main.py`、teacher `SOURCE.md`、teacher deck.csv、trajectory/raw record
  を含めないことを allowlist と secret/privacy scan で証明すること。

この判断は **3指定teacherだけ** に限られる。102 asset 全体や、local_eval_only の opponent
pool 全体へ勝手に拡張してはならない。

### 2.4 native code inclusion

不可。pool manifest の全assetは local evaluation boundary であり、`SOURCE.md` の文面と
`src/mage_ptcg/meta_specialist/opponent_pool_v1.py` の利用境界により、提出 bundle への
混入を許さない。Team-internal policy
`configs/opponents/permissions/pokemon_team_agents_internal_v1.yaml` も source policy では
`submission_bundle: true` を明示的に prohibited とする。これは internal agent source
にも適用され、native codeコピーの抜け道にはならない。

### 2.5 deck list submission

現時点では native Strong Asset deck に **NO-GO**。bundle contract は
`QualifiedDeckAsset.usage_boundary == "bundle_allowed"`、60枚、CABT legality `passed`、
deck lock/lineage を要求する。tomato の seed registry entry は
`public_source_training_approved_local_only` で、blocker に
`cabt_legality_not_run`、`competition_legality_not_confirmed`、`runtime_not_qualified` を残す。

そのため「teacher起点の自前policy」は提出可能性を持ち得るが、「teacherのdeckをそのまま
提出する」ことは別途許可・qualificationなしにはできない。Lucifer/plamen deck も同様に
local-eval source であり、この監査で bundle approval は得られなかった。

## 3. 現在のself-trained assetと技術的package状態

### Rule v0 fallback

root `main.py` は `_DEFAULT_AGENT = make_rule_agent()` に固定されている。root `deck.csv`
（SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）との route は
`runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz` として
archive-only 2局 smoke 済みである。archive SHA は
`da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a`。これは fallback
package の有効性だけを示し、Strong Asset BestKnown より強いことは示さない。

### V4 checkpoint route

Wave6 V4 seed0 の checkpoint SHA は
`9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`、V4 public-state AWR
tomato seed0 は同一 architecture（vocabulary size 1267、hidden 128、embedding 64）を
用いる self-trained weight である。これらは teacher native code ではないが、現行
`scripts/build_performance_submission_bundle_v1.py` は V4を
`WAVE6_V4_NOT_SUBMISSION_READY` とする。

当時の vocabulary blocker は、現在は技術的には一部解消している。実行時に
`load_production_card_vocabulary_v1()` と
`require_production_card_vocabulary_v1()` を通し、1267 card、pinned database SHA
`a0ea63cf...0f373`、`usage_decision=permission_decision=bundle_allowed` を確認した。
しかし、V4 route は依然として提出不可である。理由は次のとおり。

1. production entrypoint は V4 checkpoint loader へ接続していない。
2. `neural_policy_v4.py` / model / representation / runtime と checkpoint を archive に
   閉じ込める allowlist が未実装・未検証である。
3. V4 runtime は `torch` を import するが、提出環境で torch が利用可能・許容される
   contract は確認されていない。repository `requirements.txt` の明示依存は numpy であり、
   Kaggle submission contract は package format/size/runtime制約も UNKNOWN とする。
4. V4 candidate の archive-only clean-room legality、latency、fallback telemetry、
   no-private-information audit が未実測である。
5. subject Archaludon deck は bundle_allowed でない。

### 既存 Student v0 package route

`scripts/build_student_submission.py` は pure-Python JSONの `StudentV0Model`、student専用
entrypoint、Rule v0 fallback、member allowlist、hash manifest、clean-room verify を持つ。
この形式は `torch` に依存せず、最短の**技術的** package closure である。

ただし現在の `dist/kaggle/neural-student-v1` は `NEURAL_FIXTURE_SMOKE` / stale deck の
fixtureであり、Strong Asset fine-tune結果でも提出候補でもない。既存package機構があることと、
新しいStrong Asset studentを表現・評価できることは別である。V4 checkpointをこの線形
Student v0 JSONへ無根拠に変換してはならない。

## 4. Strong Asset由来自前policyの最短package closure

以下は提出実行ではなく、提出可能候補を生成・検証できる状態へ至る最短順序である。

1. **deckを分離する。** root deck、または外部sourceをコピーしていない自前60枚deckを
   `bundle_allowed` として qualification/legality/lockする。Strong Asset deckを選ぶなら、
   source owner/Kaggle Rulesに基づく明示判断と再qualificationが先である。
2. **behavior provenanceを固定する。** tomato/Lucifer（必要なら同一の人間判断を
   materializeしたplamen）の `training-local` permission manifest と public-state record
   のみを入力にし、FoundationInit/teacher SHA/decision ref を candidate checkpointへ継承する。
3. **portable runtime形式を選ぶ。**
   - 最短技術route: Standard Libraryで推論できる Student v0同等の self policy JSON。
   - 強いV4を使うroute: checkpoint + V4 feature/representation/runtime の最小allowlistを
     vendoringし、torch availability/size/latencyが提出contractで許されることを先に確認する。
     これを確認できない限り、V4を標準ライブラリまたは許可済みruntime形式へ**parity検証
     付き** exportする必要がある。
4. **entrypointとfallbackを固定する。** package専用 `main.py` がhash-pinned self modelを
   読み、失敗時にはRule v0へ合法にfallbackする。repo root `main.py` とChampionは変更しない。
5. **package closureを検証する。** model/deck/runtime sourcesのみをallowlist化し、tar
   member、model/lineage/deck hash、card vocabulary registry、dependency inventory、secret/
   private-source scan を固定する。外部 `opponents/`、teacher record、training datasetを
   archiveへ入れない。
6. **clean roomを実測する。** archive展開後・repo path除外の subprocess で、import、60枚
   deck登録、model hash照合、fallback非発火、合法手、fault 0、timeout 0、p50/p95/p99 latency
   を測る。性能評価は別に、native EvaluationBestKnownを相手に96→384→768→1536局で行う。
7. **提出契約を最終確認する。** archive type/size、entrypoint、Rules acceptance、外部
   dependency可否を人間がKaggle Submit画面で確認し、contract evidenceを更新する。

この順序では、Strong Assetの動作を「見て学ぶ」ことと外部native sourceを「提出する」ことを
完全に切り離せる。最終pairの deck/policy はどちらも self-owned / bundle-allowedの
artifactとして manifest化される。

## 5. 真正の外部blocker

以下はコード修正・ローカル実験だけでは解除できない。

1. **外部deckの提出利用可否。** tomato/Lucifer/plamen/R7 deckをそのまま提出する場合、
   `local_eval_only` を `bundle_allowed` へ変更する根拠（source author licenceまたは
   competition Rulesへの適合確認）が必要である。現在のrepository owner承認は
   **導出weightに限る**ため、外部deckそのものの提出利用を含まない。
2. **Kaggle submission contract。** format/size、Python/runtime依存、torch可否、entrypoint、
   Rules acceptance は現正典で UNKNOWN / `CONTRACT_CONFIRMATION_REQUIRED` である。
3. **導出モデルの範囲外teacher。** 3指定teacher以外を行動label/θ0に使う場合、個別の
   derivation decision / permission manifestが必要である。

反対に、3指定teacherから既に記録済みの `training-local` trajectoryを用い、teacher source
を同梱せず、自前weightとしてそのprovenanceを保存することは、既存の判断記録によって
新たな外部author許諾待ちには分類しない。ただし、そのweightが強いこと、bundleが閉じること、
Kaggle contractに適合することは別の未達gateである。

## 6. 実務上のGO / NO-GO

- **GO now:** approved tomato/Lucifer behavior recordsを使う public-state value/AWR/filtered
  BCの研究、native BestKnownとのlocal ranking、self-owned/bundle-allowed deckの探索、portable
  student runtimeの設計・テスト。
- **NO-GO:** native agent codeの再利用・同梱、external `deck.csv` の無断package、pool全体を
  teacherとして扱うこと、V4 checkpointをroot entrypointへ未検証で接続すること、Kaggle提出。
- **CONDITIONAL GO:** 3指定teacher由来の自前student weightを submission candidateへ進める
  こと。ただし上記のdeck、runtime、clean-room、performance、Kaggle contract gatesが全て
  artifactで閉じた場合に限る。

## 7. 再現したread-only確認

```bash
jq '.[] | select(.id=="tomatomato_archaludon" or .id=="lucifer19_battlecore" or .id=="plamen06_steel" or .id=="public_archaludon_cinderace_r7")' opponents/pool_manifest.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python - <<'PY'
from mage_ptcg.meta_specialist.card_vocabulary_registry_v1 import load_production_card_vocabulary_v1
from mage_ptcg.meta_specialist.actor_visible_features_v1 import require_production_card_vocabulary_v1
vocabulary = load_production_card_vocabulary_v1()
require_production_card_vocabulary_v1(vocabulary)
print(len(vocabulary.recognized_card_ids), vocabulary.usage_decision, vocabulary.permission_decision)
PY

sha256sum deck.csv opponents/tomatomato_archaludon/deck.csv \\
  opponents/lucifer19_battlecore/deck.csv opponents/plamen06_steel/deck.csv
```

本監査で新規package build、submission、学習、CABTは実行していない。
