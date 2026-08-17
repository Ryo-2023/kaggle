# 同一 deck action-consensus meta source / P1 CEM evidence（2026-08-15）

## 結論

未使用の同一 canonical deck parent pair（Kokinn Lucario／Yaroslav Lucario）から、両 parent が同じ観測で返した合法 action index の共通集合を優先する `ACTION_LEVEL_CONSENSUS_*` source を生成し、通常 interpreter の runtime smoke、promotion、P1固定 CEM、staged pool上の fresh validation まで完了した。全実行は research-only、authority は false、fault 0 だったが、独立 re-evaluation の positive／seat-safe／opponent×seat-safe gateを満たさず、P1 centerを保持した。P1 policy、root deck、BestKnown、Champion、production、submissionは不変である。

## Parent audit と fresh source

同一 canonical deck hash `282bbb43e78cd05d63c1bf2e680202537bdc5ad680966ead77e8dc8400f65cce` の2 parentを使用した。

- `kaggle_kokinnwakashuu_lucario_20260815`: policy hash `e976b40df254a78de160e82d8b0b390e582b0a67cad0e77001004a2f7863799a`。未smoke intakeから公式CLIで両seat 2局を再確認し、`2/2 DONE`、fault 0、draw 0、P1は `0W-0D-2L`。promoted parent rootは `runs/cg-kokinn-parent-promoted-20260815-a/`。
- `kaggle_yaroslav_lucario_crustle_20260815`: policy hash `fb0209dc9f1e9309524be88e02c02fb54f042f40d04baba49b66885ae6e42145`。既存の別smoke-promoted root `runs/cg-kaggle-kernel-meta-intake-v5-smoke-20260815/`を使用した。
- Kokinn parent smoke summary SHA: `cb4d47a9fbd5ea9f0f09108d1550c46aeb468e10cb449180d515a39337a28b4e`。
- Kokinn promoted pool SHA: `db72927a50e892370e0f002a0bb6187607f2b54ab4d4027ebaff2415b460d7d3`。

親の source identity と canonical deck identity は既存 artifact の使用履歴を照合し、今回の pair/wrapper は新しい policy＋deck pair として `unused_before_run=true` で記録した。異なる deck hash の parent は混合していない。

## 新しい source method

実装は `src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`、CLIは既存の `scripts/generate_routed_ensemble_meta_v1.py`、回帰は `tests/test_routed_ensemble_meta_v1.py` である。

- `ACTION_LEVEL_CONSENSUS_MIX_V1`: 共通合法集合が selection の `minCount/maxCount` を満たせばそれを返し、満たさなければ既存の公開 action score と deterministic hash tie-breakerへフォールバックする。
- `ACTION_LEVEL_CONSENSUS_HASH_V1`: 共通集合を優先し、共通集合がない場合は公開 snapshot hashで parent setを選ぶ。
- `ACTION_LEVEL_CONSENSUS_KO_V1`: 共通集合を優先し、共通集合がない場合は公開 opponent HPからの KO scoreを優先する。
- parentが返した index以外を発明せず、index集合をmergeしていない。片側invalidなら他方、両側invalidなら fail-closed である。
- 読み取るのは `turn`、`yourIndex`、visible active/bench card ID、stadium、selection contextだけで、hand、prize、deck、discard、future RNG、networkは使わない。
- 各decisionで同一 observationを両parentへ渡すため runtime cost multiplier は2。wrapperは各parentの隔離 `deck.csv` と entrypointを保持する。

fresh validationを staged poolへ接続できるよう、`scripts/run_cg_p1_p2_validation_v1.py` に `--pool-root` を追加した。これにより `opponents/`へコピーせず、pool manifest SHAを検証したまま validationできる。

## Pool seal / smoke / promotion

6候補（3 consensus fallback variant × parent order A/B）を生成した。

generated root `runs/cg-action-consensus-meta-20260815-b/`:

- pool SHA: `4866a112434535549b2db03cc149271a40eb6fee2bbb9243c6148ea454643fa6`
- fresh meta SHA: `75b292c9d5bbd3278415b82907efc0b7e6d6ae7c47b1da6e2f76b22b5679d48d`
- initial split SHA: `5c734e326bf4f497390e9f54adaefb0e2f9eb38f9f43fd8f164adca2183ddf58`
- meta manifest SHA: `3a92c79557d9028c166d472a60fc4fdad140e4bc29338cb957629b4b7f38926c`

P1 package SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`。公式 smoke は6候補×両seat＝12局で、`12/12 DONE`、fault 0、draw 0、P1は `3W-0D-9L` だった。candidate別のP1結果は、hash-ab `1W-0D-1L`、hash-ba `0W-0D-2L`、KO-ab `1W-0D-1L`、KO-ba `1W-0D-1L`、mix-ab `0W-0D-2L`、mix-ba `1W-0D-1L` である。これは runtime safety の証拠であり、性能採用根拠ではない。

promoted root `runs/cg-action-consensus-promoted-20260815-b/`:

- pool SHA: `d11d09e4320cc769240a28cec555a72389530b5d1d073a7e4a0c40e614440859`
- fresh meta SHA: `6b53b0336d324a9ee8670100375fceb5c3228728bfa315fbc03f157abf87dccc`
- meta manifest SHA: `3a92c79557d9028c166d472a60fc4fdad140e4bc29338cb957629b4b7f38926c`
- rebound split SHA: `4cfe2b3696225a1f847d001ac35aa06da25e850db85816c45036484b1b22600b`
- promoted smoke summary SHA: `37b7e4fb8256251b525502e4d14615d9c4925f64fae8be6c2be7d98ff1380257`
- split: `META_TRAIN=4 / META_DEV=1 / META_FINAL=1`

6候補は全て runtime smoke 済みだが、CEM performance selectionには `META_TRAIN` 4件だけを使用した。従って DEV/FINALは performance-unused holdout であり、smoke-untouched holdoutではない。この区別を次のsource設計でも維持する。

## P1 fixed CEM

campaign root `runs/cg-action-consensus-cem-20260815-b/`:

- campaign seed `20260862`、`META_TRAIN_ALL`、4 opponent refs、population/elite `6/2`、1 generation。
- `--reeval-for-update --reeval-repeats 2 --reeval-games-per-opponent-seat 1 --positive-delta-gate --risk-aware-update`。
- screen `112/112 DONE`、fault 0。independent re-evaluation `48/48 DONE`、fault 0。
- campaign manifest SHA: `ab588897e967f4a25f5592e13f9d3e171b147331496ac1559b5b7b6f234157e4`
- generation results SHA: `ee9bcdbf1f7c0f8235a4bc396734f5ca464fadeab5e12dc60eed79661807a0ae`
- re-evaluation summary SHA: `4ca2a0b89c95b59ded5cb5ce2e468e207ae65552be4bc81ef5f40705ca69e996`

screenは次の通りだった（各 candidate/control 16局、fault 0）。

| candidate | candidate | control | delta |
|---|---:|---:|---:|
| `g00-c00` | 1/16 | 4/16 | −18.75pt |
| `g00-c01` | 3/16 | 4/16 | −6.25pt |
| `g00-c02` | 2/16 | 4/16 | −12.50pt |
| `g00-c03` | 1/16 | 4/16 | −18.75pt |
| `g00-c04` | 2/16 | 4/16 | −12.50pt |
| `g00-c05` | 2/16 | 4/16 | −12.50pt |

screen上位 `g00-c01` は独立2 blockの delta が `0.00pt / +37.50pt` で mean `+18.75pt` だったが、両blockの seat gap は25pt、opponent×seat safeではなく、risk-aware deltaは `0pt`。`g00-c02` は独立 delta `−12.50pt / +25.00pt`、seat gap `0pt / 50pt`、worst `−12.50pt` だった。robust positive、seat-safe、opponent×seat-safeを満たす候補は0件で、`elites=["incumbent-center", "incumbent-center"]`、`champion_changed=false`、P1 identity center保持となった。

## Fresh validation（選定後の診断）

screen上位の `cg-p1-cem-g00-c01-3dd7cdcee94c` を固定し、staged promoted poolに対して `base_seed=20260863` で `META_TRAIN_384 / META_DEV_96 / META_FINAL_96` を実行した。splitが4/1/1件なので実際の局数は各 `128 / 16 / 16`（candidate＋controlの両seatを含む）である。validation rootは `runs/cg-action-consensus-fresh-validation-20260815-b/`。

| stage | candidate | control | delta | faults |
|---|---:|---:|---:|---:|
| META_TRAIN（128局） | 18W-0D-110L | 8W-0D-120L | +7.8125pt | 0/0 |
| META_DEV（16局） | 1W-0D-15L | 1W-0D-15L | 0.00pt | 0/0 |
| META_FINAL（16局） | 1W-0D-15L | 1W-0D-15L | 0.00pt | 0/0 |

DEV/FINALで改善は再現せず、CEM gate後に読む診断結果としてのみ記録した。FINALを次の探索で未使用 holdout として再利用してはならない。

validation manifest SHA: `16930602e2e3a9e44d6948545d9385df01c62a8bf3387abe4c85ea77d2920d82`。各stage summary SHAは TRAIN `8a4ddf00d9dc52dd9684bc33bd242ccc309fa29de92d85aa1580c3a1dfd57cb0`、DEV `53e7a1ca77cbbeb03fddc32949c33b051a9492883b43183297a2dd967069d5ce`、FINAL `b22e7aba3a9202bd6100a8756fcf76a8f85de70feb453552cb310d4d3808253e`。

## 判定と次の方針

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。この方法は「同じ observationに対する2つの合法 action候補の合意」を runtime-safe に作れることを示したが、今回の小標本での policy performance gain は確認できなかった。旧 Rule v0、Student/AWR/BC、既評価 policy surface、同じ Kokinn/Yaroslav pair の blind retry、deck search、BestKnown loop再開は行わない。

次は、未性能使用 policy lineageまたは新規 permission済み sourceを含む相関の低い parent familyを増やし、runtime smoke用候補と performance holdoutを明示的に分けた compositionを設計する。全ゲート `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を通過した場合だけ、`cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` に接続する。

## 変更と検証

- `routed_ensemble_meta_v1.py`、`run_cg_p1_p2_validation_v1.py`、関連テストは未コミットの作業差分として保持している。
- focused suite（action-consensus、calibrated pool、split、historical smoke、CEM、failure adapter）は後段でまとめて実行する。
- commit、push、Champion変更、production変更、Kaggle submissionは行っていない。
