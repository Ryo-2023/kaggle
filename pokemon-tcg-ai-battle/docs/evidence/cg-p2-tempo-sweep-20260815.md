# P2 damaged-active tempo surface screen — 2026-08-15

## 結論

P2 robust g01（policy SHA `4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）へ、新しい公開状態軸 `damaged_active_threat_attack_bonus` を追加してscreenした。条件は「自分の可視activeが満HP未満、相手の可視activeがenergy 2以上、選択肢がATTACK」の同時成立時だけである。

META_TRAIN 12 opponent、候補3点＋共有controlの合計192局は全て `DONE` / fault 0 だったが、3点すべて負差だった。正差候補がないため独立確認は起動していない。P2/P3、BestKnown、Champion、production、deck phase、submissionは不変である。

## 仮説の根拠と設計境界

P1公開telemetryのMAIN局面で、ATTACK選択肢が存在する行を条件集計した。`self active damaged && opponent active energies_count >= 2` は144行、うち96行がATTACK選択（66.7%）だった。この観測を「攻撃可能なtempo局面で、既存P2 scoreが攻撃を過小評価している可能性」としてboundedに検証した。

既存のnear-lethal、単純な相手energy、bench満杯の3軸はゼロ固定し、候補値だけを`+6000/+12000/+24000`にした。private hand/deck、future RNG、deck、legal action、fallback、非ATTACK選択肢は変更していない。`P2ContextConfig`の4軸目としてhash-bound packageへ反映し、既存の共有control／ResourceGovernor／authority falseを再利用した。

## 再現コマンド

```bash
PYTHONPATH=src:. python scripts/run_cg_p2_tempo_sweep_v1.py \
  --output runs/final-sprint-autonomous/cg-p2-tempo-sweep-v1-20260815 \
  --base-seed 48496000 --repetitions 2 \
  --values 6000,12000,24000
```

## Screen結果

Artifactは `runs/final-sprint-autonomous/cg-p2-tempo-sweep-v1-20260815/`。共有controlは48局、各candidateも48局で、合計192局すべて `DONE` / fault 0。control objectiveは `0.1687010`。

| bonus | candidate | control | delta | candidate seat gap | 判定 |
|---:|---:|---:|---:|---:|---|
| 6,000 | 5W-0D-43L / objective 0.1026672 | 8W-0D-40L / 0.1687010 | −6.6034pt | 4.1667% | STOP |
| 12,000 | 6W-0D-42L / objective 0.1349892 | 8W-0D-40L / 0.1687010 | −3.3712pt | 0.0000% | STOP |
| 24,000 | 7W-0D-41L / objective 0.1577175 | 8W-0D-40L / 0.1687010 | −1.0984pt | 12.5000% | STOP |

summary SHA `d05cf7f04c20995df48e8ae7c5c56319d3d1d5bb4b3c6f6d127a36a42fbfa9bd`、complete manifest SHA `d63a4298313ff560f3bf33aae366e4799bf11a8e24a26a0ab6cdaf32e5d8bdb7`、tempo manifest SHA `98fed573f10286b9a75aebe1141bcdc81a9d054596072d60802d8acf67bdde35`。

## 判定と次の再開条件

正差候補がないため、独立seed確認、384/768拡大、CEM update、P3、deck mutationは行わない。+24000は負差に加えてseat gap gate外であり、blind retryしない。今回のscreenは再利用META_TRAINであり、fresh/unused metaの昇格根拠にもならない。

現在のproduction/Champion/BestKnownはP1 `cg-lethal-target-v1`＋root deck。現ローカルpoolにはfresh・unused・smoke-ready public metaがないため、次の性能昇格には新しいmeta sourceと、新しい正差policy surfaceの両方が必要である。

## 実装SHAと権限

- surface module SHA `20cd8f716dc21952a1dc867e741485b4fcdbf5248d046e5bf52657c3f47ffe99`
- tempo runner SHA `ea1bd1b6bec5bf5a0d19e15462bccd9573c842651f09daae2d7fb4e15b14f2c5`
- tempo test SHA `5c8b72104d3280ad410f374b4b0f8f7d1d49379c162eabea2188052088d199ea`
- existing surface test SHA `10b412330956de6eaef8c193c782397c0a532ecb0f481b2842df5eaf33f19991`
- runnerはresearch-only、promotion/training/longrun/submission authorityはfalse。
- commit、push、Champion変更、Kaggle提出は行っていない。

## 追試: 符号反転面と独立seed診断

正方向が全て負差だったため、同じ条件で「攻撃を抑える」符号面を新規候補として`-6000/-12000/-24000`でscreenした。共有controlを含む192局は全てDONE/fault0だった。

| bonus | candidate | control | delta | candidate seat gap | 判定 |
|---:|---:|---:|---:|---:|---|
| −6,000 | 9W-0D-39L / objective 0.19432096 | 6W-0D-42L / 0.13686184 | **+5.7459pt** | 4.1667% | independent confirmation |
| −12,000 | 5W-0D-43L / 0.11229789 | 6W-0D-42L / 0.13686184 | −2.4564pt | 12.5000% | STOP |
| −24,000 | 3W-0D-45L / 0.06623623 | 6W-0D-42L / 0.13686184 | −7.0626pt | 4.1667% | STOP |

`−6000`候補`cg-p2-context-g00-c00-27c845188120`をbase seed `48516000`、candidate/control各384局で独立確認した。candidate `51W-0D-333L`（objective `0.13934364`）対control `50W-0D-334L`（`0.13585263`）で、差`+0.3491pt`、candidate seat gap`4.6875%`、全768局DONE/fault0だった。判定は`NOT_PROMOTABLE_REUSED_META`。screenからの差は縮小しており、fresh/unused metaでもないためP2/P3またはBestKnownへの更新は行わない。

signed screen summary SHA `a2c9e434be9859e107b8d5067b7c04e9e08c0fc580f6615d51fd831c6ec63ef0`、complete manifest SHA `96017f48fa5cc9a284f66e39bd3dc6d67295cfdcd0347562d62698b0ac95a436`、tempo manifest SHA `b3d8c59fee66dd9b0e3e095c19e6b2147efe740cef0c2a1a42b0377715c5f6e2`。confirmation summary SHA `d32bdff25acba49d924e4e89292a1c90c62e84a99f802eb7ed45ff287b99edef`、complete manifest SHA `729a5f326052d391b6862e67951fc45654092ceead2c713024d5db4365bf4f6`。

これは符号反転が「候補面としては弱い正差を一度再現した」ことを示す診断であり、未使用metaでの再現性を示さない。追加の同面blind retry、CEM更新、deck phase、Champion変更、Kaggle提出は停止する。
