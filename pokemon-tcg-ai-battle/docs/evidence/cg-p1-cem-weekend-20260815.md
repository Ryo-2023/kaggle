# cg-lethal P1 CEM 週末スプリント証拠

確認日: 2026-08-15 (Asia/Tokyo)

## 固定 parent と実装

- P1 package: `runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package`
- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- new implementation: `src/mage_ptcg/meta_specialist/cg_p1_parameterization_v1.py`, `cg_weekend_split_v1.py`, `cg_p1_shadow_telemetry_v1.py`, `cg_p1_cem_v1.py`
- runner: `scripts/run_cg_p1_cem_v1.py`, `scripts/run_cg_p1_p2_validation_v1.py`
- split: `configs/meta_specialist/cg_weekend_splits_v1.json` (config SHA `ae7d0a28fc903b78039de3853c438df811a9c26835178fdc960e35052f13ca42`)

P1 source は変更せず、15個の有限整数パラメータを append renderer で追加した。default config の fallback/clean-room parity、candidate identity、split identity、shadow public-only、CEM determinism/resume は新規 tests 16件で確認した。

## Campaign

最終 campaign artifact: `runs/final-sprint-autonomous/cg-p1-cem-weekend-v3/`

- one-generation pilot: generation-0000 evaluation 1200局、top-6 independent 672局、すべて `DONE`, fault=0。
- full CEM: generation 0〜5、各 generation の評価・reevaluation、g1/g3/g5 の META_DEV、checkpoint-g0000〜g0005 を完了。
- ResourceGovernor: 全 heavy block で `normal`, recommended workers 12, recycle 16, GPU compute process 0。
- 途中の v1/v2 は schema binding と小標本 seat guard の実装不備で fail-closed 停止した。artifact は保持し、v3 へ修正後に再実行した。

generation-3 incumbent を P2 validation 候補として固定した。

- candidate id: `cg-p1-cem-incumbent-g03-c2684e0b7079`
- config SHA: `c2684e0b7079fcf210c76b758d411877383cdac6b51aba53f6c23f188320f886`
- candidate policy SHA: `8f9e364f4f4c12348bbd9a545cd70e0d1d51e7f3e7429352372f2ed476bc8a68`
- package: `runs/final-sprint-autonomous/cg-p1-cem-weekend-v3/generation-0003/incumbent/package`

## Fresh validation

Artifact: `runs/final-sprint-autonomous/cg-p1-p2-validation-g03-v1/`

| stage | candidate | P1 control | candidate - control | fault |
|---|---:|---:|---:|---:|
| META_TRAIN 384 | 0.1718 (63W/1D) | 0.1385 (52W) | +3.33pt | 0 |
| META_DEV 96 | 0.2179 (21W) | 0.2626 (25W) | −4.48pt | 0 |
| META_FINAL 96 | 0.0847 (8W) | 0.0520 (5W) | +3.27pt | 0 |

DEV が fresh control を下回ったため、P2 は「研究候補」に留める。Champion変更、P2-fixed deck optimization、submission は実施していない。

## Shadow smoke

Artifact: `runs/final-sprint-autonomous/cg-p1-shadow-smoke-g03-v1/`

P1 behavior のみを実行する実 CABT 1局で21 decision rowsを記録し、同一観測 shadow、public-only projection、shadow fault=0、behavior-only execution を確認した。この1局では actions differ=0であり、性能因果や candidate superiority の証拠とは扱わない。

## Same-observation shadow の原因診断

Artifact: `runs/final-sprint-autonomous/cg-p1-shadow-dev-diagnostic-g03-96-v1/summary.json`

DEV 96局の同一観測 shadow は 4,508 decision rows、98 action diffs（2.1739%）、shadow fault=0、`same_observation_all=true` だった。主な遷移は `ATTACK→ATTACH` 43件、`EVOLVE→ATTACH` 19件であり、candidate の attack/evolve score が P1 より低下する一方、Mega attach score が上回る score-order flip が観測された。これは DEV drop の機序仮説を支持するが、shadow telemetry 自体は結果 outcome を実行していないため、因果の単独証拠とは扱わない。

main-action/attack/lethal/evolve/attach/ability を P1 値へ戻し、setup/search/retreat だけを g03 から残した guarded candidate（`runs/final-sprint-autonomous/cg-p1-guarded-g03-validation-v1/`）では、DEV が P1 比 −0.13ptまで回復した。ただし TRAIN −1.72pt、FINAL −1.58ptであり、P2 には採用しなかった。

## Robust CEM と P2 候補

Artifact: `runs/final-sprint-autonomous/cg-p1-cem-robust-v1/`

rotating 4-opponent block ではなく META_TRAIN 全12参照（各2反復）を使う `META_TRAIN_ALL` CEM を6世代完走した。g01 は CEM DEV で P1 比 +3.97pt、g03 は +2.62pt、g05 は −5.60ptだった。独立した fresh validation は次のとおり。

| candidate | split | candidate | P1 control | delta | fault |
|---|---|---:|---:|---:|---:|
| robust g01 | META_TRAIN 384, seed A | 0.1559 (57W) | 0.1383 (50W) | +1.76pt | 0 |
| robust g01 | META_DEV 96, seed A | 0.2648 (25W) | 0.1911 (18W) | +7.38pt | 0 |
| robust g01 | META_FINAL 96, seed A | 0.1326 (14W) | 0.1096 (11W) | +2.30pt | 0 |
| robust g01 | META_TRAIN 384, seed B | 0.1436 (54W) | 0.1546 (57W) | −1.10pt | 0 |
| robust g01 | META_DEV 96, seed B | 0.2736 (26W) | 0.1913 (18W) | +8.22pt | 0 |
| robust g01 | META_FINAL 96, seed B | 0.1011 (10W) | 0.1012 (10W) | −0.02pt | 0 |
| robust g01 | META_TRAIN 384, seed C | 0.1453 (53W/1D) | 0.1358 (50W/2D) | +0.95pt | 0 |
| robust g01 | META_DEV 96, seed C | 0.2507 (24W) | 0.2439 (23W) | +0.68pt | 0 |
| robust g01 | META_FINAL 96, seed C | 0.1348 (14W) | 0.1088 (11W) | +2.60pt | 0 |
| robust g03 | META_TRAIN 384 | 0.1704 (62W/1D) | 0.1717 (63W) | −0.12pt | 0 |
| robust g03 | META_DEV 96 | 0.1893 (18W) | 0.2034 (19W) | −1.41pt | 0 |
| robust g03 | META_FINAL 96 | 0.0783 (8W) | 0.0817 (8W) | −0.33pt | 0 |

g01 は DEV の独立3 seedすべてで正であり、3 seed pooled でも TRAIN +1.82pt（164W vs 157W）、DEV +5.56pt（75W vs 59W）、FINAL +3.13pt（38W vs 32W）となった。一方、seed A/BのTRAIN/FINALには負の揺れもあり、完全なseed-invariant superiorityとは扱わない。したがって g01 を週末時点の research P2 候補として固定し、Champion/提出には昇格しない。g03 は棄却した。

## P2-fixed deck alternating screen

Artifact root: `runs/final-sprint-autonomous/cg-p2-deck-alternating-v1/candidates.json`

P2 robust g01 policy と root deck を固定し、root deck から `1123` を1枚だけ置換した4候補を、同一 `performance_first_broad_pool_v1` 24参照・192局（candidate/control各96）で一件ずつ評価した。全評価が `DONE`、fault=0で、candidate の昇格や policy phase 開始はなかった。

| candidate mutation | short delta | candidate seat gap | decision |
|---|---:|---:|---|
| `1123 → 3` | −11.46pt | 0.00% | NOT_PROMOTABLE |
| `1123 → 1086` | +7.29pt (seed A), −12.50pt (seed B) | 8.33%, 6.25% | NOT_PROMOTABLE |
| `1123 → 5` | −1.04pt | 6.25% | NOT_PROMOTABLE |
| `1123 → 1097` | −7.29pt | 6.25% | NOT_PROMOTABLE |

`1123 → 1086` は1 seedだけ正だったが、独立 seedで反転し、2 seed pooled でも安定優位を示さないため追加の 384局確認は行わず停止した。deck alternating は現 root deckを維持する結論であり、P2 policy の採用・Champion変更・Kaggle提出は行っていない。

## P2 package closure

g01 は `runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/` に、P1と同じ `cg` runtime closure、parameterized `main.py`、root `deck.csv`、deterministic `submission.tar.gz`、hash-bound `candidate_manifest.json` を持つ research-only package として封印した。`CgPackageSpecV1.from_package` と通常 interpreter での archive extraction/self-play 1局（DONE/DONE、fault=0、104 steps）を通過した。strict `python -I` smoke は、このworkspaceで `kaggle_environments` がisolated interpreterから見えないため未実施であり、これを公式提出検証の代替とは扱わない。`submission_ready=false`、外部送信なし。

## P2 parent Campaign 2 と未使用meta確認

g01 を初期中心・control とし、P1 source を不変の renderer とする Campaign 2 を実行した。

- artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign2-v1/`
- 3 generations、各 generation の `META_TRAIN_ALL` screen 1,200局、fault=0
- generation-2 screen 上位 `cg-p1-cem-g02-c07-a01f78663f60` は P2 比 +19.04pt（13W vs 4W）だったが、generation-1 incumbent の DEV は −7.81ptであり、screen 値だけで採用しない。
- g01 自身を P1 control とした独立未使用meta holdout（`runs/final-sprint-autonomous/cg-p2-unused-meta-policy-v1-seedA/`）は P1 と同点（23W vs 23W、0.00pt）だった。g01 は BestKnown/Champion へ昇格しない。

g02-c07 を package 化し、P2 control と同じ root deck で未使用metaを2 seed確認した。

| artifact | candidate | P2 control | delta | seat gap | decision |
|---|---:|---:|---:|---:|---|
| `cg-p3-unused-meta-policy-v1-g02-c07-seedB` | 27W/96 | 24W/96 | +3.125pt | 10.42% | NOT_PROMOTABLE |
| `cg-p3-unused-meta-policy-v1-g02-c07-seedC` | 21W/96 | 27W/96 | −6.25pt | 2.08% | NOT_PROMOTABLE |

1 seedのpositiveは再現せず、P3には進めなかった。

## 再評価でelite選抜を安定化した Campaign 3

Campaign 2 の小標本noiseを抑えるため、`scripts/run_cg_p1_cem_v1.py` に `--control-package`、`--initial-config-json`、`--reeval-for-update` を追加した。`--reeval-for-update` は screen 上位6候補を独立96局で再評価し、その目的値でCEM分布を更新する。対象テストは7件、すべてPASS。

artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign3-reeval-v1/`

- generation-0: screen 最大候補 +16.56ptが独立再評価 −1.54ptへ反転した。screenと再評価の順位乖離を確認できた。
- generation-1: screenでは負の候補も含めて再評価値で選抜し、最終center `cg-p1-cem-incumbent-g01-8c2668d97d1e` の META_DEV は P2 比 +7.60pt（23W vs 16W）。
- candidate package: `runs/final-sprint-autonomous/cg-p3-candidate-campaign3-g01-package-v1/`（policy SHA `403466d6bd014f231e1ddd34008ef9d0e38754b846bafefc20108d50d52c77d3`、root deck SHA は不変）。

同 candidate を未使用metaで独立確認した結果は次のとおり。

| artifact | candidate | P2 control | delta | seat gap | decision |
|---|---:|---:|---:|---:|---|
| `cg-p3-unused-meta-policy-v1-campaign3-g01-seedD` | 31W/96 | 27W/96 | +4.167pt | 10.42% | NOT_PROMOTABLE |
| `cg-p3-unused-meta-policy-v1-campaign3-g01-seedE` | 22W/96 | 23W/96 | −1.042pt | 4.17% | NOT_PROMOTABLE |

seed D のpositiveはseed Eで再現せず、P3昇格、384/768拡大、policy→deck phaseは停止した。現時点の研究状態は「P2を次の探索parentとして保持するが、BestKnown/Champion/提出はP1＋root deckのまま」である。

## 再現性に関する評価上の注意

parallel CABT evaluator artifact では `engine_seed_supported=false` が記録されている。したがって同一 `pair_key` とseedは同一ゲーム乱数を保証せず、今回の比較は独立stratified process drawsである。seed D/E の反転はこの制約と整合し、single-seed positiveを昇格根拠にしない。すべての上記 holdout は fault=0、192 games（candidate/control各96）、未使用24 opponent、両seatで完了している。

## Campaign 3 c01 candidate の未使用meta再確認

Campaign 3 generation-1 の独立96局再評価で最大だった実候補 c01 を package 化し、P2 control・root deck固定で未使用metaを2 seed確認した。

- package: `runs/final-sprint-autonomous/cg-p3-candidate-campaign3-g01-c01-package-v1/`
- candidate id: `cg-p3-cem-g01-c01-47562102fb24`
- policy SHA: `babe87ec17658ef88316351ebcc616818709ed966fc19f4b62280e08ac35ce92`
- archive SHA: `11b374acfb0ec0e94379f9224b57c5e946e2b2845fefd2282eac5c5a51cbe6ea`

| artifact | candidate | P2 control | delta | candidate seat gap | decision |
|---|---:|---:|---:|---:|---|
| `cg-p3-unused-meta-policy-v1-campaign3-g01-c01-seedF` | 29W/96 | 26W/96 | +3.125pt | 6.25% | NOT_PROMOTABLE |
| `cg-p3-unused-meta-policy-v1-campaign3-g01-c01-seedG` | 23W/96 | 24W/96 | −1.042pt | 6.25% | NOT_PROMOTABLE |

両 holdout は candidate/control 各96局、未使用24 opponent、両seat、192/192 DONE、fault=0 だった。seed F の正差は seed G で再現せず、c01 の P3昇格、384/768拡大、deck mutation は行わない。single-seed の screen/re-evaluation positive を昇格根拠にしない。

## 次のCEM選抜改善

screen +16.56pt が再評価 −1.54ptへ反転し、c01 も独立再評価 +7.59ptから未使用metaで反転したため、次 campaign では screen elite を複数の独立 META_TRAIN block で再評価してから CEM update する。`scripts/run_cg_p1_cem_v1.py` に任意の `--reeval-repeats` と repeat ごとの固有 `block_id`/seed を追加し、既定値1で既存artifactとの互換性を保つ。次のheavy runは P2 control、root deck固定、`--reeval-for-update --reeval-repeats 2 --all-train-refs` の2世代 research-only campaignとし、package昇格・deck探索は holdout gate 通過後に限定する。

## Campaign 4: 複数再評価の実測

P2 package (`cg-p1-robust-g01-submission-package-v1`) を control、root deckを固定し、`META_TRAIN_ALL`・2世代・screen elite各2 independent block (`--reeval-for-update --reeval-repeats 2`) を実行した。

- artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign4-multiseed-reeval-v1/`（manifest SHA `8f6f8accc002ecd84714e90501678e8eaec4bb990a5eef725d6260c57d05f738`）
- generation-0: screen 1,200局、再評価1,344局、generation-1 center更新
- generation-1: screen 1,200局、再評価1,344局、META_DEV 96局
- 全 heavy block は workers=12、`DONE`、fault=0

generation-1 の再評価上位実候補を package 化した。

| candidate | pooled re-eval | holdout | delta | seat gap | decision |
|---|---:|---:|---:|---:|---|
| c18 `cg-p4-cem-g01-c18-802af89966f3` | 34W vs 25W / 192 (+4.69pt) | seed H: 25W vs 20W / 96 | +5.208pt | 14.58% | NOT_PROMOTABLE |
| c18 | 同上 | seed I: 20W vs 32W / 96 | −12.500pt | 0.00% | NOT_PROMOTABLE |
| c15 `cg-p4-cem-g01-c15-7e3fcad2042c` | 31W vs 25W / 192 (+3.26pt) | seed J: 25W vs 27W / 96 | −2.083pt | 6.25% | NOT_PROMOTABLE |

c18 は seed H の正差が seat gap gate（5%以下）を満たさず、seed I で大きく反転した。c15 は複数再評価では正だったが未使用metaで負差かつ seat gap gate 外だった。holdout はすべて candidate/control 各96局、両seat、192/192 DONE、fault=0であり、P3・384/768拡大・deck mutation・Champion変更は行わない。

## Campaign 5: META_TRAIN＋DEV の探索面拡張

META_TRAINだけへの適合を抑えるため、runnerへ `--include-dev-refs` を追加し、CEM更新の探索参照を META_TRAIN＋META_DEV（18 opponent）へ拡張した。検証は META_FINAL に切り替え、既存 META_FINAL を更新には使わない。search-plan、manifest/resume identity、repeat block、validation split のテストを含む focused 22件がPASSした。

- artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign5-trainplusdev-v1/`（manifest SHA `ccf36c8569e321569da1d3fc3fd05bebfa26d20154af2627033e132c4695bcec`）
- 2世代、screen 3,600局、再評価 4,032局、全て fault=0
- generation-0 の再評価上位 c20 `cg-p1-cem-g00-c20-d00b5178f239` は 55W vs 49W / 192（+2.54pt）
- generation-1 は再評価6候補すべて負差（最良でも −1.81pt）、centerの META_FINAL は 6W vs 12W / 96、seat collapse、`valid=false`

c20 を package 化し、既存 train/dev/final と Campaign 4 までの holdout v1 に重ならない fresh holdout v2（24 opponent）で確認した。

- package: `runs/final-sprint-autonomous/cg-p4-candidate-campaign5-g00-c20-package-v1/`
- policy SHA `4020447abdb31af3cc1c33363392640b505d5acfc4bdf71e951bf395dac396fb`
- archive SHA `512e9a6031f56de372c785cb3d540f6e57ce27f6d5a1d70f7735021b9cd7ee41`
- holdout config: `configs/meta_specialist/cg_unused_meta_holdout_v2.json`
- seed K: 39W vs 41W / 96（−2.083pt、candidate seat gap 6.25%、`NOT_PROMOTABLE`、192/192 DONE、fault=0）

探索面を広げても fresh meta で正差を得られなかったため、Campaign 5 候補の policy→deck phase は開始しない。現 BestKnown/Champion/production/submission は P1＋root deckのままとし、次の CEM は新しい risk-aware surface または fresh meta protocolを固定してから再開する。単なる同候補の blind retry は行わない。

## Campaign 6: lower-tail risk-aware CEM

Campaign 4/5 の「screen・再評価は正でも未使用metaで反転」を受け、`--risk-aware-update` を追加した。elite候補の独立2ブロックを個別集計し、CEM更新には pooled 平均ではなく最悪ブロックの objective を使う。fault、zero-seat collapse、candidate/controlの不整合は従来どおり fail-closed とした。

- artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign6-risk-aware-v1/`（manifest SHA `b55ec59df80f67c7bc11122a82000fff0e1bf954b84d121796932bae4ed825c5`）
- P2/root deck control、META_TRAIN＋META_DEV（18 refs）を探索、META_FINALを検証に保持
- 2世代、screen 3,600局、独立再評価4,032局、gen1 META_FINAL 192局、全 `DONE`/fault=0
- gen1 center `cg-p1-cem-incumbent-g01-f80e482fae4e` は META_FINAL `6W vs 15W / 96`、−9.50pt。探索中心は昇格しない。

gen1 risk-aware上位 c01 (`cg-p1-cem-g01-c01-6b544d2b0cb6`) は再評価2ブロックが `+0.2285/+0.2476` objective だった。P2 parentから package化して fresh v3 panelで一度だけ診断したが、`51W vs 50W / 96`（+1.04pt、candidate seat gap 2.08%、fault=0）に留まり、control seat gap 8.33%のため `NOT_PROMOTABLE` だった。package manifest SHAは `711832c389f3e41572307c5320c4b0ca8a588f22ba07bde439736ba35ad7e37e`、holdout run SHAは `6e24fb693bb68e3660d1927b22ec2d8ac343a516e40a35ff70de7146b65c1706`。

fresh v3 config `configs/meta_specialist/cg_unused_meta_holdout_v3.json`（SHA `b7f8eb68d4b3f9dc7c714b5d484e6fd805acaf07ac398459e9b63aab43e29630`）は weekend split/v1/v2と非重複の24 refsで、c01の診断後は選抜へ再利用していない。

## Campaign 7 STOP と Campaign 8: seat-gap penalty

各再評価ブロックの候補 seat gap ≤5%を硬い `valid` 条件にする試行（Campaign 7）は、gen0で valid elite が `1 < 6` となり fail-closed 停止した（stop SHA `cbcc86423ee1e4499bbc0e799753715a49477d6e8fa62655952659f0fcdacf48`）。これは小標本のseat varianceを完全除外すると更新不能になる実測である。

そこで `RISK_AWARE_SEAT_GAP_LIMIT=0.05` を超えた差だけを risk-aware objective から減点し、fault/zero-seat collapse以外は候補として残す形へ変更した。focused suiteは25件PASS。

- artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign8-risk-aware-seatpenalty-v1/`（manifest SHA `49e52610db9c9aa0a5f6f4a8fef3f2f50f1b7a16de57797f1b6c6e5031b62ff8`）
- P2/root deck control、META_TRAIN_ALL、2世代、screen 2,400局、再評価2,688局、gen1 META_DEV 192局、全 fault=0
- gen1 centerは META_DEV `19W vs 21W / 96`（−2.55pt）。P3/Championへは進めない。
- gen0 risk-aware上位 c02 (`cg-p1-cem-g00-c02-c797f937dfd3`) は2ブロックとも positiveかつ seat-safeだったため、事前固定した診断としてv3 panelを一度だけ実行した。結果は `45W vs 55W / 96`（−10.42pt、candidate seat gap 6.25%、fault=0、control seat gap 10.42%）で `NOT_PROMOTABLE`。package manifest SHA `6c17a4be31f498d18cac9bb6ff1db64f0200aebc5df59b3c19e5b5655e334623`、holdout run SHA `bdfd5d466c1a4d03b916834616ae4c5b6b26be9d14b9b2fb8a74f311ad6a5dbb`。

Campaign 6〜8 は、探索側の lower-tail と seat variance を扱っても v3未使用metaで安定優位を得られないことを示した。したがって現 BestKnown/Champion/production/submission は P1 `cg-lethal-target-v1`＋root deckのまま、P2/P3昇格、deck mutation、384/768拡大、Kaggle送信は行わない。同じ候補のblind retryは停止し、次の再開には新しい policy surface または新しい未使用meta sourceと、事前固定した複数seed gateが必要である。

## Campaign 8 audit correction and Campaign 9 fixed rerun

Campaign 8後のコード監査で、seat-gap penaltyが全blockのgap≤5%時に負値となり、safe candidateへ誤ってbonusを与える実装バグを検出した。Campaign 8のCEM選抜値は有効なrisk-aware証拠として扱わず、holdoutの観測値だけを診断ログとして保持する。安全域で penalty=0、超過分だけ減点する回帰テストを追加し、focused suite 26件をPASSした。

修正版 Campaign 9:

- artifact: `runs/final-sprint-autonomous/cg-p2-cem-campaign9-risk-aware-seatpenalty-fixed-v1/`（manifest SHA `49e52610db9c9aa0a5f6f4a8fef3f2f50f1b7a16de57797f1b6c6e5031b62ff8`、gen1 results SHA `c8127d0f2a3acf69b4510311c09e0b410eff4c33083e404fa3ba3dc4ee03d542`）
- runner SHA `7acda70ac64a560b6e4202c94282c590dabec2ae14c98f7ee16d455cf134984a`、focused test SHA `f7b8a479a93ecfb98aa0ced3c50c0d1c768e82d86900f187175fe7038e1954c1`
- P2/root deck control、META_TRAIN_ALL、2世代、screen 2,400局、再評価2,688局、gen1 META_DEV 192局、全 `DONE`/fault=0
- gen1 center `cg-p1-cem-incumbent-g01-8d68a4684d23` は META_DEV `24W vs 22W / 96`（+1.83pt、candidate seat gap 4.17%）だったが、control seat gap 12.50%で promotion gate外。P3/Championへは進めない。
- risk-aware gen1上位には両block seat-safeな c11 (`cg-p1-cem-g01-c11-76b754ba9dcb`, objective `0.1486/0.1366`) があったが、v3 holdoutは既にc01/c02の診断で使用済みのため、新たな未使用meta positive確認へは進めていない。

したがってCampaign 9のMETA_DEV positiveは候補生成の信号に留まり、未使用meta gateを通ったBestKnown更新ではない。現在の運用基準は引き続きP1＋root deckであり、新しい未使用meta sourceなしに同panelで候補を増やさない。

## Campaign 9 c11 の residual public panel 確認

Campaign 9 risk-aware elite の c11 (`cg-p1-cem-g01-c11-76b754ba9dcb`) を package 化し、既存 split、holdout v1/v2/v3、internal-source opponentを除いた残り3件の public `smoke_ok` meta (`rauffauzanrambe_advanced`, `tomatomato_archaludon`, `yaminh_agent`) だけで、P2 robust g01 control と同一 root deckを比較した。これは選抜用ではなく、残存 public panel に対する事後の再現性確認であり、authority は全て false、candidate 自身は pool に存在しない。

| seed | candidate | P2 control | delta | candidate seat gap | control seat gap | 判定 |
|---|---:|---:|---:|---:|---:|---|
| R (`180260815`) | 6W/96 | 6W/96 | +0.00pt | 0.00% | 0.00% | NOT_PROMOTABLE |
| S (`190260815`) | 10W-1D/96 | 2W/96 | +8.8542pt | 7.2917% | 4.1667% | NOT_PROMOTABLE |

両 seed とも candidate/control 各96局、合計192局、両seat、`DONE=192/192`、fault=0だった。S の正差は candidate seat-gap gate（5%以下）を満たさず、R では差が再現しない。したがって c11 は P3、BestKnown、Champion、deck phase、384/768 拡大へ進めない。R/S summary の内部 hash はそれぞれ `65590ec62bd7468f35f8b2210690e7b95af228f4ecec526f3f7a1ece6e428e4f` / `ad8c19d47a84dfd4fb07c86b0f3a67bdbcb03edce7c1d4f533de5e0fa03bfef2`、protocol hash は `6d2fc697b46674c6a789a977f461f86d2248f575f722f9953b74cf290c59ab13` / `91e22e924dc997cbfa1f7c76d5ab692363b0672f56917033bf7b7ab991fdbbe9` である。

相手別集計では、S の改善は `rauffauzanrambe_advanced` に集中し、`tomatomato_archaludon` は P2 と同点、`yaminh_agent` は小幅改善だった。candidate の相手×seat gap は pooled residual で安定せず、全体 seat gap だけでは見えない lower-tail が残った。

## 次の CEM surface: opponent×seat lower-tail penalty

上記の失敗を再利用可能な実装契約へ落とすため、`aggregate_candidate_rows` が `opponent_seat_rates` を保存し、`_risk_aware_reevaluation` が各 independent block の相手×seat gap（閾値5%超過分のみ）を objective へ減点するようにした。従来の全体 seat-gap penalty は維持し、safe gap に bonus は与えない。CEM core/runner focused tests は21件を通過した。

この変更は次の research-only CEM で候補分布を安定化するためのものであり、今回の residual panelの再利用や c11 の blind retryを意味しない。fresh public holdout が追加で確保されるまで、BestKnown/Champion/production/submission は P1 `cg-lethal-target-v1`＋root deckを維持する。

## Campaign 10: opponent×seat lower-tail CEM（探索専用）

Campaign 10 は P2 robust g01 config（initial config SHA `c83df4408b247cb2418f684e2557d69dcde4626c8d81330bb1e9890ee022a9eb`）を中心に、P2 package を control として `META_TRAIN_ALL`・2世代・`--reeval-for-update --reeval-repeats 2 --risk-aware-update` で実行した。artifact root は `runs/final-sprint-autonomous/cg-p3-cem-campaign10-opponent-seat-v1/`、manifest SHA は `49e52610db9c9aa0a5f6f4a8fef3f2f50f1b7a16de57797f1b6c6e5031b62ff8` である。generation 0/1 の screen は各1,200局、再評価は各1,344局、全て `DONE`、fault=0。ResourceGovernor は normal、12 workers、killなしだった。

generation-1 center `cg-p1-cem-incumbent-g01-b56ae24d9436` は META_DEV で `23W vs 14W / 96`（+9.7729pt）だったが、candidate seat rates は `0.1875/0.2917`（seat gap 10.4167%）で gate外だった。相手別 gap も `kiyotah_lucario` 0.375、`nihei_megalopunny` 0.25、`sue124_alakazam` 0.25 など高く、研究 parent／P3／BestKnownへは昇格しない。fresh holdoutは増えていないため、residual panelでの追加評価や deck phaseは行わない。

### 再評価 control binding の監査修正

Campaign 10 artifactの監査で、各 repeat の control game は最初の elite にだけ生成され、他 elite の candidate block内には control row がないことを確認した。そのため元の `repeat_results` では空 control の fault-inclusive objective `-1.0` が delta に混入し、非先頭 eliteの `risk_aware.repeat_deltas` が見かけ上 +1pt超になっていた。candidate objective と pooled independent delta は正常だが、この delta fieldはそのまま採用根拠にできない。

根本原因を修正し、各 repeatで一度だけ実行した共有 control aggregateを全 eliteの repeat resultへ束縛してから deltaを計算する `_bind_repeat_control` を追加した。新規再現テストを含む focused suite は32件PASS。既存 Campaign 10 ledgerを再集計した post-hoc 検証では、全 elite の corrected repeat delta が `[-0.0874, +0.0879]` の範囲に収まり、各 control aggregate は96局で空でないことを確認した。旧 results.json は不変であり、Campaign 10 の risk delta は修正後の値へ置換していない。以降の heavy run は修正済み runner のみを使用する。

以上から Campaign 10 は「相手×seat penalty が安定 candidate を生んだ」という証拠ではなく、META_DEV の単発正差と高い seat variance、および control binding 契約の監査結果として扱う。P1＋root deck、BestKnown/Champion/production/submissionは不変である。

## Fresh public decklist holdout: c11 / P2 / P1

現行 `opponents/pool_manifest.json` と全既存 `runs/**/ledger.jsonl` をdeck hashで照合し、未登録・未使用かつ全aliasが `KAGGLE_PUBLIC_REPLAY` の7件を固定した。rank-298のdeckは `TEAM_REMOTE_REF` aliasを含むため除外した。この資産は公開replayの**decklistのみ**であり、元チームのnative policyを再現しない。評価では既存のgeneric local pilotをisolated poolへmaterializeし、結果を `public deck holdout proxy` として扱う。

- source: `data/opponent_deck_pool_20260730/opponent_deck_pool.json`（source SHA `90d24ea18e80f65ec10c9d71d1db6b5241ea581aca48161150972f3752163e96`、pool hash `53845668ada8fa9be78631061d44ae92945e7e7b1706b5dd023e697046780eef`）
- protocol: 7 decklists、両seat、独立 base-seed strata `480260000` / `480261000`、各deck/seat/seed 8反復、3 arm、672局、workers=12/recycle=16、fault-inclusive
- arms: c11 `cg-p10-cem-g01-c11-76b754ba9dcb`、research parent P2 `cg-p1-cem-incumbent-g01-c83df4408b24`、production incumbent P1 `cg-lethal-target-v1`
- artifact: `runs/final-sprint-autonomous/cg-public-deck-holdout-v1-20260815-reviewed/`（元ledgerは `.../cg-public-deck-holdout-v1-20260815/evaluation/ledger.jsonl` として不変保持）

| arm | W-D-L | score rate | seat gap (pooled) |
|---|---:|---:|---:|
| c11 candidate | 105-2-117 / 224 | 47.3214% | 0.8929% |
| P2 parent | 118-2-104 / 224 | 53.1250% | 8.0357% |
| P1 incumbent | 109-1-114 / 224 | 48.8839% | 11.1607% |

全672局は `DONE`、fault=0だったが、c11はP2に −5.8036pt、P1に −1.5625ptであり、seed `480261000` ではP2比 −12.0536ptへ反転した。seedごとのcandidate seat gapも5.3571% / 7.1429%で、事前固定gate（各seedでP2/P1を上回り、全arm seat gap≤5%）を満たさない。したがってこのproxyは c11 のP3/BestKnown昇格、CEM更新、deck phase、Champion変更、提出を許可しない。P1＋root deckをproduction基準、P2を次のresearch parent候補として保持する。

初回artifactのsummaryはCABT engine seed（`seed=base_seed+ordinal`）を独立base-seedと誤認していたため、ledgerを変更せず `metadata.holdout_seed` で2 strataへ再集約したreviewed artifactを正とする。`engine_seed_supported=false` のため、base-seedはagent-side/protocol stratumの独立性を示し、同一CABT初期局面の完全再現を意味しない。

## Campaign 11: independent-seed CEM と候補確認

P2 config SHA `c83df4408b247cb2418f684e2557d69dcde4626c8d81330bb1e9890ee022a9eb` を中心に、P2 packageをcontrolとして、未使用 campaign seed `480262000` の `META_TRAIN_ALL` 2世代を実行した。artifactは `runs/final-sprint-autonomous/cg-p2-cem-campaign11-independent-seed-v1/`（manifest SHA `5d07cefcee73e2ce693a9ef9cce20835e5305356dd91b088b458dbd464f75f15`）である。各世代はscreen 1,200局、独立再評価1,344局、gen1 DEV 192局、全ブロック `DONE`/fault=0だった。

gen1の再評価では c13 (`cg-p1-cem-g01-c13-b69214284aa4`) が P2 比 +3.39pt、c02 (`cg-p1-cem-g01-c02-4aeec2c0296f`) が +2.34ptだった。しかしこれは再評価内の選抜信号に留め、次の未使用seed確認を必須にした。

| candidate | confirmation seed | candidate | P2 control | delta | seat gap | 判定 |
|---|---:|---:|---:|---:|---:|---|
| c13 | `480962000` | 54W / 384 | 58W / 384 | −1.2545pt | 0.00% | NOT_PROMOTABLE |
| c02 | `481062000` | 48W / 384 | 55W / 384 | −2.2506pt | 5.2083% | NOT_PROMOTABLE |

c13 confirmation artifactは `runs/final-sprint-autonomous/cg-p2-cem-campaign11-c13-confirmation-v2/`（summary SHA `231e636ee71d58b602780ef916206c2df924b566a23efa4e47fe8e13ea09bc49`）、c02は `runs/final-sprint-autonomous/cg-p2-cem-campaign11-c02-confirmation-v1/`（summary SHA `f81037eb661c352948f9581d45c3bb1c5bc266491f87b5cbfb36c7457fba9cb6`）である。両確認とも candidate/control 合計768局、全 `DONE`、fault=0だった。stdinをmain moduleに使った一回限り起動はspawn不能で停止したが、`scripts/run_cg_candidate_confirmation_v1.py` のfile-backed runnerへ切り替えて再現可能にした。

## Campaign 12: 高精度再評価による現行surfaceの判定

Campaign 11の反転を受け、同じP2起点で candidateごとの独立再評価を各 `META_TRAIN` opponent・seat 16局、2 repeat（candidate/control各768局）へ増やした。artifactは `runs/final-sprint-autonomous/cg-p2-cem-campaign12-highprecision-v1/`（manifest SHA `5c2a2cba4eec7ab5d2af2c5cf35395a7c5cb5cb0331696861a8e0a41b736c9d5`、seed `482162000`）で、screen 1,200局＋再評価5,376局を全て `DONE`/fault=0で完了した。

再評価6候補は全てP2に対して非正だった。

| candidate | re-eval candidate objective | P2 objective | delta |
|---|---:|---:|---:|
| c03 | 0.16853 | 0.16899 | −0.05pt |
| c10 | 0.16143 | 0.16899 | −0.76pt |
| c20 | 0.15633 | 0.16899 | −1.27pt |
| c17 | 0.13047 | 0.16899 | −3.85pt |
| c11 | 0.12291 | 0.16899 | −4.61pt |
| c15 | 0.12229 | 0.16899 | −4.67pt |

したがって Campaign 12 からP3/BestKnownへ進む候補は無く、Campaign 12が生成した新centerは次のparentへ採用しない。現行15パラメータsurfaceは、少なくともこの高精度 `META_TRAIN_ALL` 条件ではP2を安定して上回る証拠を示さなかった。

この反転を次回以降の自動loopで安全に扱うため、CEM runnerへ `--reeval-games-per-opponent-seat` と `--positive-delta-gate` を追加した。後者は独立再評価でcontrolを上回る候補がelite数に満たない場合、分布を更新せず現在centerを保持するfail-closed gateである。confirmation runnerを含む focused suiteは25件PASS、両runnerのpy_compileもPASSした。

現時点のBestKnown/Champion/production/submissionは P1 `cg-lethal-target-v1`＋root deckのまま。P2は研究parent候補として保持するが、P3昇格、deck phase、package promotion、Champion変更、Kaggle送信は行っていない。次の再開条件は、positive-delta gateを有効にした新しいpolicy surfaceまたは新しい未使用meta sourceを事前固定することであり、Campaign 11/12候補のblind retryではない。
