# self-owned robust adversarial source CEM（2026-08-16）

## 追補 — distinct source再検証とP1 CEM接続（2026-08-16 JST）

epoch4〜epoch7の追加source CEMを実行した。epoch4〜6はscreen上の上振れがfresh validationでseat-collapseまたはscreen gate不通過となり、epoch7（population 24、elite 6、screen 576局、validation 2候補×48局）も全row `DONE`・fault 0だがpromotion 0件だった。epoch7は同一portfolio（P1／Rule v0／balanced-independent）のblind retryをこれ以上続けない根拠として記録する。epoch7 `campaign_result.json` SHAは `faced4d8c31186177d666af1f27b155563515f6a5504850251b80b734570856b` である。

過去epochのscreen gate通過かつ未使用のdistinct candidate 8件を、新seed namespace `robust-source-independent-validation-20260820`、各reference／seat 8局で再検証した。384局は全て `DONE`・fault 0で、4件がscreen＋fresh validation＋seat-safe gateを通過した。

| candidate | fresh mean | worst reference | max seat gap | gate |
|---|---:|---:|---:|---|
| `epoch2-c01` | 64.5833% | 50.0% | 25.0% | PASS |
| `epoch2-c03` | 70.8333% | 62.5% | 12.5% | PASS |
| `epoch4-c06` | 56.25% | 50.0% | 25.0% | PASS |
| `epoch7-c19` | 63.5417% | 53.125% | 18.75% | PASS |

`epoch3-c04`／`epoch3-c05`は独立 validationでseat gap不通過、`epoch2-c07`もgap不通過だった。screen meanが0.5未満だった `epoch2-c00`／`epoch5-c05`／`epoch7-c12`はholdout候補として別seedで診断し、`epoch7-c12`はvalidation自体はpositiveだったがscreen gateを満たさないため昇格しなかった。新validatorは `scripts/validate_robust_source_candidates_v1.py`、結果SHAは `d26fae4df69fbd111f7ab4a5fd09cb72b326d582e3b6e6efc02978e74e6f8a6e`（manifest）／`98fe3c8b8b0f011633103efea8b82725b6af028530c4c7c45c08a6aef9fa3b59`（result）である。

4件を `scripts/seal_robust_source_weekend_pool_v1.py` で別rootへ再封印し、P1対の両seat source smoke 8/8 `DONE`・fault 0を確認した。未使用性能holdoutを維持するため、splitは `META_TRAIN=(epoch2-c01, epoch2-c03)`、`META_DEV=(epoch4-c06)`、`META_FINAL=(epoch7-c19)` とした。pool／fresh／meta／split SHAはそれぞれ `920880c7bac47ef7f0d69b3d895176981bdb809a93d1fb7fbf8cb5873c5afa0c`／`ceeee4148fdd8ca205838208cd303c8ad6690b0c6c3951ec4167c8cb736ec29b`／`672d2831725ee61d060be8deb8e335fac9b16eccc6ab113c0707fedbad14a1fc`／`7e7499cc59c1ee1b92041ee89222e29d1557cfc8b69c56d24247af6292f4ad23` である。

このsplitをP1固定policy CEMへ接続した。`runs/cg-p1-cem-robust-source-weekend-20260816-v1/` は seed `2026084002`、population／elite `8／2`、`META_TRAIN_ALL`、screen 72局、independent re-evaluation 96局（各elite 32局＋shared control block）を全て `DONE`・fault 0で完走した。screen上位 `cg-p1-cem-g00-c01-44d0f082ae20` は +25.0ptだったが、独立差は `−12.5pt`（repeat `−18.75pt / −6.25pt`）でseat/opponent-seat-safe不成立。もう1候補も独立 `−15.625pt`、risk-aware minimum `−18.75pt`だった。positive／risk-aware／seat-safe gate不成立のため `incumbent-center`×2、P1 centerを保持した。DEV／FINALは未読である。campaign／generation／result SHAは `1e3d99a698d5a8159e9d72fd9b104063a7dd9baaeefaec2881828754fffe1bb0`／`9c1f2c050fe528bc03d53cd8971bdc6808907185c81c77f99c426e7f49c572eb`／`fba482da45928c2d8070c7eb7db0603b58b954d4af666b49198acf08dccd973e` である。

判定は `SOURCE_GENERATION_PASS / FRESH_DISTINCT_SOURCE_POOL_SEALED / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。このepochはsource供給方法とP1 CEM接続を実証したが、P2／P3、deck phase、`cg_bestknown_loop_v1.py`のBestKnown loop接続、Champion／production／submission変更は行っていない。次は同じsource portfolioのblind retryではなく、別policy lineageまたは別deck recipeを含むfresh source epochを生成し、少なくとも独立 positive・seat-safe・unused DEV／FINALを満たした場合だけpolicy→deckへ進める。

## 結論

P1 parameter surfaceから、固定policyのaction trace・private field・teacher labelを使わず、terminal WDLだけで複数のself-owned referenceに対して頑健なmeta sourceを生成する経路を追加した。screen上位1件だけをfresh validationへ送る旧方式は独立seedのseat noiseを拾ったため、全screen eliteを検証し、最初にscreen＋fresh validation＋seat-safe gateを満たした候補だけをsource poolへ封印する方式へ修正した。

epoch3ではこの方式が昇格ゲートを通過した。候補 `robust-source-g00-c05-acb3f0d8e32e` は、self-owned reference portfolio（P1、Rule v0、balanced-independent）に対し、screen 24局／referenceの最弱score 25.0%・fault 0、fresh validation 48局／平均56.25%・最弱reference 50.0%・最大seat gap 25.0%・fault 0、source smoke 2/2 `DONE` を満たした。fresh meta manifest と pool manifest は生成済みで、`build_fresh_meta_batch_v1` の再検証もPASSした。

これはP1の性能改善やBestKnown更新を意味しない。現時点でP1、root deck、BestKnown、Champion、production、submissionは不変であり、policy CEM／deck phaseはまだ接続していない。

## 実装

- 集約とgate: `src/mage_ptcg/opponent_ingest/robust_adversarial_source_cem_v1.py`
  - reference集合を厳密に固定し、referenceごとのmean／worst score、fault、seat gapを集約する。
  - `robust_objective = (mean + worst) / 2 - fault_rate`。
  - 全referenceのfault-free、seat-safe、両seat観測を必須とする。
- runner: `scripts/run_robust_adversarial_source_cem_v1.py`
  - immutable P1 sourceからparameterized candidateを生成する。
  - 固定portfolioをscreenし、全eliteを独立seedで再評価する。
  - promotion時はcandidate packageを別rootへcopyし、pool／freshness evidence／fresh meta manifestをno-clobberでsealする。
- downstream bridge: `scripts/validate_robust_source_candidates_v1.py` と `scripts/seal_robust_source_weekend_pool_v1.py`
  - source-CEM screen候補を別seedで再検証し、policy/deck CEMへ未使用のまま渡せる distinct candidateだけを選ぶ。
  - P1両seat smoke、pool／meta／fresh／weekend splitのhash bindingを別rootへsealする。
- regression: `tests/test_robust_adversarial_source_cem_v1.py`（6件pass）。

生成時の固定参照は次の3件である。いずれも今回のepochではlocal-eval-onlyの対戦相手としてのみ使い、公開kernelを生成元にはしていない。

| ID | policy SHA | canonical deck SHA |
|---|---|---|
| `balanced-independent-v1` | `340cc50dbdffb6533dd3ba57e92a2ce2d62d3c8084fdf2a3f9408fa032ce4aad` | `882011b389ef4496f1cdc70460ea2407ac9f9d5fcf84669b9ff82505844cdc71` |
| `p1-reference-v1` | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` | `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7` |
| `rule-v0-reference-v1` | `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7` | `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7` |

## 実験記録

### epoch1（public referenceを含む試行）

`runs/cg-robust-adversarial-source-cem-20260816-epoch1/` は192局、fault 0で完走した。しかし `public-reference-v1`（tetsutani Grimmsnarl）のsource scoreが0〜12.5%に集中し、全8候補が同referenceでseat-collapseした。elite 0、validation 0、promotion 0である。これは候補の性能証拠ではなく、強すぎる／deck archetypeの異なるpublic referenceを生成時portfolioへ直接入れると探索が空になる失敗モードである。

### epoch2（self-owned reference portfolio）

`runs/cg-robust-adversarial-source-cem-20260816-epoch2-selfowned-portfolio/` は192局、fault 0で完走した。screenでは2 elite（最良mean 75.0%、worst 50.0%）を得たが、最良候補のfresh validation 48局はmean 60.4167%でも最大seat gap 62.5%となり、promotion gate不合格だった。screen上位1件のみを検証する方式の弱点を確認した。

### epoch3（multi-elite validation）

実行:

```text
PYTHONPATH=src:. python scripts/run_robust_adversarial_source_cem_v1.py \
  --output runs/cg-robust-adversarial-source-cem-20260816-epoch3-selfowned-multielite \
  --population-size 8 --elite-count 2 \
  --games-per-reference-seat 4 \
  --validation-games-per-reference-seat 8 \
  --campaign-seed 2026081694 --workers 4
```

- screen: 192局、96W-96L、fault 0、全row `DONE`。
- elite 1（`robust-source-g00-c05-acb3f0d8e32e`）: screen mean 54.1667%、worst 25.0%、seat-safe。
- fresh validation: 48局、26W-22L、fault 0、mean 56.25%、worst 50.0%、最大seat gap 25.0%、seat-safe。
- elite 2もfresh validation gateを通過したが、順位1のcandidateを選択した。
- source smoke: 2局、fault 0、`smoke_ok=true`。
- promotion result: `runs/cg-robust-adversarial-source-cem-20260816-epoch3-selfowned-multielite/promoted_source_pool/`。

主要artifactのSHA-256:

| artifact | SHA-256 |
|---|---|
| `campaign_result.json` | `9de5893e821f1fa3e7784507c6539307ee40ff4454893271e3b188082aee80c8` |
| `generation-0000/manifest.json` | `768607864a1d2920b4329847b2c1fdb3f6dd1bc20a3685f85d29cd84b386dab6` |
| promoted `pool_manifest.json` | `fbe73d49c918d4d13c0d2670941f38b507826d8efe96873e13c4f80abf14c3c5` |
| `fresh_meta_manifest.json` | `671bff318a6b2ff0479d6ee96868faae80a9c51cc79cd4c19cfbb56d945ee707` |
| `freshness-evidence.json` | `af26d54580d62087659780c6fb00bd6dc5abec610de22510beb326dbbcdb23aa` |
| `promoted_source_smoke_summary.json` | `505df10a2bd705d6115cd03ee30d1929dc3c7524896b69a66f91e3b184424cf8` |

生成sourceのpolicy SHAは `0e7d8bdc5efbeb2b27ead35e1591c2b09aad6910ec8b87e4f3dc97d7a71c1c57`、P1 root deckのcanonical SHAは `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7` である。

## 判定と次の接続条件

判定は `SOURCE_GENERATION_PASS / SELF_OWNED_ROBUST_SOURCE_PROMOTED / BESTKNOWN_UNCHANGED`。fresh batchのloader再検証はPASSしたが、現fresh batchはreference 1件であり、既存の12-source weekend splitを自動的に置換するものではない。次はこのsourceを過去性能使用済みpoolへ混ぜず、別seed・別source epochを追加して未使用TRAIN／DEV／FINALをCABT前に分離する。その後、candidate runnerを注入した `cg_bestknown_loop_v1.py` のpolicy phaseへ接続し、positive・fault0・seat gap≤5%のstrict gateを通過した場合だけdeck phaseへ進む。

Champion変更、production変更、commit、push、Kaggle提出は行っていない。
