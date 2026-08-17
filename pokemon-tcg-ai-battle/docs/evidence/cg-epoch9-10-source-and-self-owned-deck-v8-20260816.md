# epoch9／epoch10 source CEM と self-owned deck v8 — 2026-08-16

## 結論

epoch9／epoch10で、P1 parameter surfaceから新しい self-owned robust source pool を生成し、source側の fresh validation と P1対の bounded smoke を完了した。epoch10 poolを P1 policy CEMへ接続したが、META_TRAIN-only と META_TRAIN＋META_DEV のいずれも独立再評価の lower-tail／opponent-seat gate を満たさず、incumbent centerを保持した。今回の判定は `SOURCE_GENERATION_PASS / FRESH_ROBUST_SOURCE_POOL / POLICY_CEM_NO_UPDATE` である。

同時に、公式カードCSVと self-owned spec v8 だけから4つの60枚 deck＋policy packageを生成した。public canonical collisionは0、全packageは静的検査と通常interpreter smokeを通過したが、epoch10 META_TRAINとの matched screen は4件すべて P1 root deck control より負差だった。v8 recipeは `SELF_OWNED_DECK_V8_SCREEN_NEGATIVE` として停止し、同recipeのblind retryは行わない。

P1、root deck、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py` の昇格状態は不変である。commit、push、Kaggle submissionは行っていない。

## epoch9 source CEM

artifact rootは `runs/cg-robust-adversarial-source-cem-20260816-epoch9-local-neighborhood2/`。centerは `configs/meta_specialist/robust_source_local_neighborhood_center2_20260816.json`（SHA `344153a96ae6115bf77a1620c065b29677830250cc93db5b18903822a6a95064`）、seed `2026088001`、population／elite `12／3`、screen 576局、elite validation 96局である。screenとvalidationの全CABT rowは `DONE`、fault 0だった。

validation上位は次の通りである。

| candidate | fresh mean | worst reference | max seat gap | gate |
|---|---:|---:|---:|---|
| `robust-source-g00-c02-1f7064f080ab` | 58.3333% | 53.1250% | 12.50% | PASS／promoted |
| `robust-source-g00-c01-953e91c5f4c5` | 51.0417% | 37.5000% | 25.00% | PASS |
| `robust-source-g00-c05-d19bee8656ca` | 44.7917% | 34.3750% | 18.75% | FAIL（mean） |

promoted sourceは `robust-source-g00-c02-1f7064f080ab`。campaign result SHAは `a933777b20f0546ce4da42c20d8571d9c7ce442e8a4e9c1c5df8e130bb593804`、promoted pool／fresh meta／smoke summary SHAはそれぞれ `0d2ea563e6e21d94b779d78f6f485b391318bf48096d47171ae41df6a2b8e4c1`／`5d13297a0257598fbe232637a0ae9dc253e82fd5982e290e60a21d767a29386d`／`53a5297eeeee00c0cff3ff5885230f0e18f4436afb64da246c8c64378cf47b0c` である。

epoch9 sourceのpromoteは source供給経路の検証であり、policy性能の昇格やBestKnown更新を意味しない。

## epoch10 source CEM（c04 centered）

artifact rootは `runs/cg-robust-adversarial-source-cem-20260816-epoch10-c04-centered/`。centerは epoch8-c04 の config、seed `2026089101`、population／elite `12／3`、screen 576局、elite validation 192局である。screen／validationは全て `DONE`、fault 0だった。

screen上位のsource候補は c11 mean `64.5833%`／worst `56.25%`／max seat gap `12.50%`、c01 mean `66.6667%`／worst `43.75%`／gap `12.50%`、c09 mean `60.4167%`／worst `50.00%`／gap `25.00%`。fresh validationは次の結果だった。

| candidate | fresh mean | worst reference | max seat gap | gate |
|---|---:|---:|---:|---|
| `robust-source-g00-c11-8e36de867293` | 55.2083% | 42.1875% | 9.375% | PASS／promoted |
| `robust-source-g00-c01-4594ffe11d1f` | 58.8542% | 51.5625% | 15.625% | PASS |
| `robust-source-g00-c09-615d61960ff3` | 57.2917% | 45.3125% | 21.875% | PASS |

promoted sourceは `robust-source-g00-c11-8e36de867293`。campaign result SHAは `fb2b3f8a39bb21a37d6e9f5866cb65cf47817f546ec980c1ebbbec24ce347e93`、promoted pool／fresh meta／smoke summary SHAは `bfd670d25a2f8a995955e0880709c8077f9ece1f0c65f19cb09d675d923be033`／`889213c9c21696bff56b39540d3938fddcc1ebf1b4daba3f64e09eef76d091fb`／`964b72c55995a26ad31eab70f8a6e8c0027eb008e1c88d67da15dc4b31083368` である。

epoch10 poolは次の6 sourceをP1 smoke 12/12、fault 0で封印した。

- `META_TRAIN`: `epoch10-c11`、`epoch10-c01`、`epoch10-c09`、`epoch8-c04`
- `META_DEV`: `epoch8-c11`
- `META_FINAL`: `epoch8-c12`

split SHAは `9e16db82307dc2cc2510d22b5575ef55a7b95c250fb30ed0ac7a3bd3abb7ec53`、pool manifest SHAは `14b1926e3dc46d50e14e364d01c99085b606d0f0b2428d09e91c4e60acddbd85`、fresh meta manifest SHAは `f6eb16e9753a8f89606771be1a87c33e18e5bb5d793b90240c50f1753c60f1c0` である。`META_DEV`／`META_FINAL` は policy CEMの更新参照には含めていない。

## epoch10 P1 policy CEM

### META_TRAIN-only

`runs/cg-p1-cem-robust-source-weekend-20260816-epoch10-v1/` は seed `2026089202`、population／elite `12／3`、2世代、各世代 screen 208局、elite 3候補×3独立block（各candidate 96局）で完走した。全CABT rowは `DONE`、fault 0、`META_TRAIN_ALL` のままである。manifest SHAは `c0d1e6fb9bb00394a2d3158ee17cbfd6e7df11ded4cb0f53969268d8ca1a9234`。

generation 0の最良独立候補は mean delta `−3.125pt`、repeats `−3.125 / 0 / −6.25pt`。generation 1の最良は mean `−1.0417pt`、repeats `−9.375 / +12.5 / −6.25pt`で、lower-tailとopponent／seat-safeを満たさなかった。両世代とも `incumbent-center`×3を保持した。gen1の diagnostic DEVは incumbent同士の比較で candidate `10/16` 対 control `8/16`（`+12.5pt`）だが、候補昇格の根拠にはしていない。

### META_TRAIN＋META_DEV 探索

`runs/cg-p1-cem-robust-source-weekend-20260816-epoch10-dev-expanded-v1/` は seed `2026089301`、同じ `12／3`、2世代、各世代 screen 260局、elite 3候補×3独立block（各candidate 120局）で完走した。manifest SHAは `a9ecebb2e0ac0a99ca7ed72f4f4d647699de9e6284de7826ce89a6088fed36bd`。search modeは `META_TRAIN_PLUS_DEV`、`include_dev_refs=true`、全CABT rowは `DONE`、fault 0である。

generation 0の最良 c04は mean `+12.5pt`、repeats `+2.5 / +7.5 / +27.5pt`だが、opponent／seat-safeが不成立。generation 1の最良 c09は mean `+5.833pt`、repeats `−17.5 / +12.5 / +22.5pt`でlower-tailが不成立だった。両世代とも `incumbent-center`×3を保持した。blind `META_FINAL` diagnosticは incumbent candidate `8/16` 対 control `7/16`（`+6.25pt`）だが、CEM更新や昇格には使用していない。

## self-owned deck v8 screen

公式 `data/raw/EN_Card_Data.csv` と `configs/meta_specialist/self_owned_cg_deck_spec_v8_tempo_energy.json` だけから、`fighting-lucario-tempo-energy-v10` の4候補を生成した。各 candidateは60枚、`parent_deck=null`、`public_parent_read=false`、public canonical collision 0、authority全falseである。代表候補の canonical deck SHAは `506df19e4a8fc100a5e5fc2f5dd9dd8249c01ef620c45269a442354adc98497a`、spec SHAは `8236f90bc3ac48d3ea5148af87afc6b230c18eb861dd6b62e84b4546df9c6f45`。

epoch10 META_TRAIN（4 source、各opponent×seat 4局、candidate/control各32局、合計64局）との matched screen は全件 fault 0だった。

| ordinal | candidate score | P1 control score | delta |
|---:|---:|---:|---:|
| 00 | 13/32 | 19/32 | −18.75pt |
| 01 | 12/32 | 15/32 | −9.375pt |
| 02 | 15/32 | 18/32 | −9.375pt |
| 03 | 13/32 | 19/32 | −18.75pt |

候補は `runs/cg-self-owned-deck-generation-v8-20260816-00/`〜`-03/`、screenは `runs/cg-self-owned-deck-screen-v8-20260816-train/`、`...-01-train/`、`...-02-train/`、`...-03-train/` に保存した。4件とも独立確認へ進めず、v8 recipeの追加seed／ordinal blind retryも停止する。

## P2 `c83df...` の扱いと `ono-` の出典

`cg-p1-cem-incumbent-g01-c83df4408b24` は、`runs/final-sprint-autonomous/cg-p1-robust-g01-submission-package-v1/candidate_manifest.json` に封印された research-only packageであり、config SHAは `c83df4408b247cb2418f684e2557d69dcde4626c8d81330bb1e9890ee022a9eb`、policy SHAは `4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4`、root deck raw SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` である。

現 repo の canonical fresh-holdout記録は、P2がP1に対して `188W-1D-190L-5F / 384` 対 `200W-0D-180L-4F / 384`、差 `−2.9948pt`、fault 9件で `NOT_PROMOTABLE` と判定している。したがって、別資料にある `TRAIN +1.82pt / DEV +5.56pt / FINAL +3.13pt` は、現在の worktreeで対応する一次artifact path／manifestを確認できない未照合値として扱い、BestKnown更新やP2昇格の根拠には記録しない。新しいrunの完全artifactが提示されるまで、現行判定はP1＋root deckのままである。

資料中の `ono-` は公開kernel作者名や外部sourceではない。ローカルGitの identity／branch／封印commitに由来する識別子である。

- Git identity: `bfe-lab-ono <ono.ryosuke.36t@st.kyoto-u.ac.jp>`
- branch: `agents/ono-cg-lethal-v1`
- sealing commit: `1965b42b028f10960d08ccb4980be5b76946f98b`
- commit subject: `feat(submission): cg lethal提出候補を封印`
- remote: `origin = git@github.com:NiheiRyunosuke/pokemon-tcg-ai-battle.git`

従って `ono-` は「このローカル作業者が封印したcg-lethal系artifact」を短く示す prefixであり、deckやpolicyを外部からコピーした出典を示すものではない。root deckのraw SHAは複数の公開snapshotとも一致するため、単一の外部原作者をこのSHAだけから断定しない。

## 次の再開条件

次の heavy runは、同じ epoch10 source poolやv8 recipeのblind retryではなく、(1) source／author／policy／deck identityを先にledgerへ予約し、(2) TRAIN／DEV／FINALをCABT前に分離し、(3) source smokeを通し、(4) P1対の独立 multi-blockでpositive・fault0・両seat／opponent-safeを確認できる場合だけ開始する。policy候補が昇格しない限り deck phaseへ進めない。
