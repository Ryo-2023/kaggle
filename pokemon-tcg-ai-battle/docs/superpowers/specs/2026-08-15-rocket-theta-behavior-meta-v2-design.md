# Rocket Theta Behavior Meta v2 設計

## 目的

受理済みの `internal_ozawa-rocket-rule_de797c3646e9` を起点に、既存のRocket theta選択proxyとは異なる、5つのspecialist theta table全体を変換するlocal-eval-only meta source familyを生成する。生成物はP1 policy CEMの対戦相手としてだけ使用し、BestKnown、Champion、production package、Kaggle提出物は変更しない。

## 背景と範囲

旧meta sourceの未使用コミットは重複または採用根拠不足で、新しいbehavior familyを構成できない。受理済みRocket sourceは `_THETA_GENERAL`、`_THETA_LUCMIX`、`_THETA_A09_MERGED`、`_THETA_A07_MERGED`、`_THETA_ABOMASNOW_R2` の5表と、可視相手情報に限定したdispatchを持つ。過去の `ROCKET_THETA_SELECTION_V1` は初期化表の選択だけを変え、CEMで昇格できなかったため、同じproxyを再実行しない。

本設計の範囲は、固定recipeによるsource materialization、hash-bound freshness、明示split、TRAIN-only smoke、P1 CEM接続までである。thetaの自由探索、deck変更、公開meta化、training authority、submission、Champion変更は範囲外とする。

## 設計

### 1. 入力と出力

- 入力source: `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9`
- source commit: `de797c3646e935157618be3edea17615430ccfec`
- source policy SHA: `8025ae95503ef10cc82a433518e81ba61554ce1547846eecc582610a85ae6c7f`
- staged policy SHA: `159a5d61ce7d1d12cf955a5d2bf99845b25d3d32eedc3904ee46e21143be053e`
- canonical deck SHA: `d61230a21f488d4e78b28b37187c6a468168c0a2fff7842025e6c0409da3614a`
- 出力root: `runs/cg-rocket-theta-behavior-meta-20260815-a/`
- 予定variant: 12件（TRAIN 8、DEV 2、FINAL 2）

各variantは元の `main.py` と `deck.csv` を材料にし、`SOURCE.md`、pool manifest、fresh meta、custom split、個別freshness evidenceを出力する。現行 `opponents/` は変更しない。

### 2. 変換境界

変換器はUTF-8 sourceを厳密に解析し、5つのtheta dictionaryが各27キーを持つことを確認する。辞書値のtoken spanだけを置換し、sourceのdispatch、`_apply_theta`、可視情報抽出、RUSH mode、deck、環境変数、import、その他のコードは変更しない。

初期campaignではbool値を変換せず、数値軸を次のbounded recipeとしてmaterializeする。各recipeは5表すべてへ同じ式を適用し、specialist間の元の差分を保持する。

| 軸 | 対象 | 変換 | 意図 |
|---|---|---|---|
| `SETUP_SHRINK` | `a_place_*` | `clip(0.85*x, -1.2, 1.2)` | 初期配置を平均へ寄せる |
| `SETUP_EXPAND` | `a_place_*` | `clip(1.15*x, -1.2, 1.2)` | 初期配置の偏りを強める |
| `BOARD_WIDE` | `a_ideal_wanaida`, `a_ideal_freezer` | 各々+1、上限4/2 | 盤面展開を厚くする |
| `BOARD_LEAN` | `a_ideal_wanaida`, `a_ideal_freezer` | 各々-1、下限1 | 盤面要求を軽くする |
| `SUPPORTER_FLATTEN` | `b_sup_*` | `clip(0.9*x+50, 0, 1000)` | supporter順位差を平坦化する |
| `SUPPORTER_CONCENTRATE` | `b_sup_*` | `clip(500+1.1*(x-500), 0, 1000)` | supporter順位差を強める |
| `ATTACK_SHRINK` | `c_tr_mewtwo`, `c_grass_core`, `c_grass_wana2`, `c_tr_yami` | `clip(0.9*x, -1.25, 1.4)`、divisorは`clip(0.9*x, 1.0, 5.0)` | 攻撃評価を保守化する |
| `ATTACK_EXPAND` | `c_tr_mewtwo`, `c_grass_core`, `c_grass_wana2`, `c_tr_yami`, `c_mewtwo_notready_div` | 指数係数は`clip(1.1*x, -1.4, 1.4)`、divisorは`clip(1.1*x, 1.0, 5.0)` | 攻撃評価を強調する |
| `GUARD_CONSERVATIVE` | `d_deckout_guard`, `d_hand_thin`, `d_mewtwo_tr_reserve`, `d_safe_prize` | 順に+2、+1、+1、+1を適用し、clip範囲は`[1,24]`, `[0,6]`, `[0,3]`, `[0,5]` | 枯渇・安全余力を厚くする |
| `GUARD_AGGRESSIVE` | `d_deckout_guard`, `d_hand_thin`, `d_mewtwo_tr_reserve`, `d_safe_prize` | 順に-2、-1、-1、-1を適用し、clip範囲`[1,24]`, `[0,6]`, `[0,3]`, `[0,5]`へ収める | 早いレースを許容する |

12variantは上記単軸と、`SETUP_SHRINK+SUPPORTER_FLATTEN`、`SETUP_EXPAND+ATTACK_EXPAND` の直交合成を使う。合成順は固定し、recipe名に完全列挙する。clipping後に元値と同一になった場合でも、variant全体のpolicy SHAが元と異なることを必須とする。

### 3. freshnessと権限

各policyについて、元source commit、source/staged policy SHA、recipe、policy SHA、deck SHA、splitをevidenceへ記録する。現行pool manifestと指定scan rootsにpolicy SHAが存在する場合はfail-closedする。variant間のpolicy SHA重複、base policyとの同一、出力rootの既存、未定義recipe、theta表の欠落・キー差分も拒否する。

生成manifestは `usage_boundary=local_eval_only`、`training_exposure=0`、authority（training/promotion/submission/longrun）を全てfalseとする。静的安全性、compile、exact 60-card、通常interpreter smokeの各failureは成果物を採用せず、ダミー行で補完しない。

### 4. splitと実験順序

splitは設定ファイルでvariantごとに明示し、TRAIN 8件、DEV 2件、FINAL 2件を予約する。smokeとCEMのコマンドはTRAIN IDだけを明示列挙し、pool全件を暗黙実行しない。DEV/FINALの参照がartifactへ出現した場合は、そのbatchをfresh holdoutとして無効扱いにする。

実験順序は次のとおりとする。

1. generator unit test、静的検査、compile、manifest/hash検証。
2. TRAIN IDだけのfault0 smoke。
3. P1をcontrol packageとして固定したCEM。screen後の独立再評価は全block positive、fault0、seat gap 5%以下を必須とする。
4. gateを通過したcandidateだけをDEVでfresh validationし、さらに独立seedで再確認する。
5. DEVがpositive transferを示した場合だけFINALを一度だけ実行する。FINALがcandidate選定に使われた場合は、別batchを新holdoutとして作る。
6. positive transferが成立した場合のみ `cg_bestknown_loop_v1.py` のpolicy→deckループへ渡す。未成立ならsource recipeまたはsource familyを変更し、同一proxyのblind retryはしない。

### 5. 検証

- 5表のkey集合一致、bool不変、dispatch文字列不変、recipeのdeterminismをunit testする。
- unknown recipe、theta表不足、duplicate policy SHA、既使用artifact、既存output、unsafe findingをfail-closedで検証する。
- 生成全variantの`py_compile`、package hash、exact deck、通常interpreter smokeを実行する。
- CEMはcontrol identity、split SHA、pool SHA、evaluator SHA、seed、局数、seat、faultをmanifestへ固定する。
- docs/evidenceには、source lineage、全SHA、split exposure、実行結果、昇格判断、未解決リスクを記録する。

## 成功条件と非成功条件

成功条件は、TRAIN-only exposureを守ったfresh poolが封印され、fault0でCEMが完走し、独立再評価とDEV/FINALが事前定義gateを通過して、P1を上回るself-owned policy候補を再現性付きで得ることである。source生成やsmokeが成功しても、性能gate未通過なら `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL` と記録し、BestKnownは変更しない。

## 既知の制約

全variantが同一source commit由来のlocal proxyであり、native/public metaの独立性は主張しない。Rocket sourceの環境変数とrandom importは受理済みbaseの既存挙動であり、generatorは新しい環境・ファイル・ネットワーク経路を追加しない。submission readinessは別途、package closureと`python -I` smokeを通過するまで未確定とする。
