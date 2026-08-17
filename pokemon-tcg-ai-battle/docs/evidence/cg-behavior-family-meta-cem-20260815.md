# Starmie behavior-family meta source / CEM（2026-08-15）

## 結論

同一履歴Starmie policyをそのまま再利用せず、可視状態だけを参照する4つの固定priority-table変換を新しいlocal-eval-only source-generation laneとして実装した。4候補はstatic検査とtrain-only smokeを通過し、P1 policy CEMへ接続できた。fresh DEVではP1 centerが一時的に`+6.25pt`となったがcandidate seat gap `12.50%`でgate外、gen1の有望candidateも未使用FINALで`−6.25pt`へ反転した。BestKnown、Champion、deck phaseは不変である。

これは同一deck・同一source policyからの相関proxyであり、public/native性能の証拠ではない。

## generator

`src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py` と `scripts/generate_starmie_behavior_family_meta_v1.py` を追加した。transformは次の4つに限定し、任意のrewrite、deck変更、import実行、network、提出bundle生成を許可しない。

- `SUPPORTER_DRAW_FIRST`: Supporter優先をLillie/Judge先行へ変更
- `SUPPORTER_HILDA_FIRST`: Hilda/Boss先行へ変更
- `BASIC_EVOLUTION_FIRST`: Staryu/SnoruntをBudewより先行
- `POFFIN_SNORUNT_FIRST`: Poffin理想数をStaryu 1 / Snorunt 2へ変更

baseは `internal_ozawa-starmie_66b0053163ff`。4つのpolicy SHAは全て新規で、同一canonical deck SHA `c69a18eccd20b925ae9e26818fb86f0eee3404bee94cffbdf52a08b6e3b10ce4`を保持する。生成rootは `runs/cg-behavior-family-meta-20260815-f/`、pool SHA `22e71e2dde96925afbab49004ed7fd3eb35fa725f1df0bfb045d4dee2dbd3258`、fresh meta SHA `08c1296e4354cbb2972892e529ae0cec48dfc6e6c86230e2f8e03faf5695e238`、split SHA `fdb3bcf6a98496a754cea973b6848d2477900d2119178a796fe72e061b485e97`である。

初回に`runs/`全体をartifact scan rootへ渡した試行は、ファイル列挙が大きすぎて約1.2GB RSS・I/O待ちとなったため中断し、partial rootを `runs/cg-behavior-family-meta-20260815-f-incomplete/` へ移した。既知のhistorical/CEM/config rootだけへscan範囲を限定して再sealし、no-clobberで完了した。この制限は性能結果ではなく、source intakeの運用上の残課題である。

## smokeとCEM

splitは`META_TRAIN`をSupporter Draw/Hildaの2 variant、`META_DEV`をBasic Evolution、`META_FINAL`をPoffin Snoruntへ割り当てた。訓練2 variantだけを両seat・各2反復（8局）smokeし、8/8 DONE・fault0、P1は4勝4敗だった。DEV/FINALはsmokeから除外した。

P1 `cg-lethal-target-v1`＋root deckをcontrol/parentに固定し、population 8、elite 2、2世代、初期scale 5%、independent re-evaluation 2回、positive-delta gate、risk-aware updateでCEMを実行した。gen0/1のscreenは各72局、独立再評価は各48局、gen1のfresh DEVはcandidate/control各16局、合計272局を全てDONE・fault0で完了した。

- gen0はscreen正差が独立blockで反転またはworst負となり、robust positive候補0件。
- gen1 candidate-04は独立block `+12.50pt` / `0pt`（平均`+6.25pt`、worst `0pt`）だがseat-safeではなく、positive gateを満たさなかった。
- fresh META_DEVのP1 centerはcandidate `5W-0D-11L`（31.25%）、control `4W-0D-12L`（25.00%）、差`+6.25pt`。candidate seat ratesは25.00%/37.50%、controlは12.50%/37.50%で、candidate seat gap `12.50%`のため`NOT_PROMOTABLE`相当。

CEM manifest SHAは `0ca82e993e0f1ce6d79ad18aaf89f00bb04f4c51d2767294b872df0117406787`、generation results SHAは `125c9d34e9fea58f3133d835e8d81cf70a0d1a32e35a81876f50dd553e57ffb0` / `c3b63d3e7b4e1b1855d9615482e9548d6ce1169847bd058f58ef364e29e5b9b4`。

## fresh FINAL

gen1 candidate-04を未使用META_FINALへ8 games/opponent/seat（各arm16局）確認した。candidate `5W-0D-11L`（31.25%）、control `6W-0D-10L`（37.50%）、差`−6.25pt`、candidate seat gap `12.50%`、fault0、判定`NOT_PROMOTABLE`。summary SHAは `f970a6d03650bc193605d90c5d69df3095c5b0d9f2fd80716275111528c0b9ba`。

## 状態

P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submissionは不変。P2/P3昇格、deck mutation、`cg_bestknown_loop_v1.py`のpolicy→deck→policy、commit、push、Kaggle提出は行っていない。次はこのStarmie相関proxyのblind retryをせず、permission済みの異なるbehavior-family sourceまたは別generatorを新epochとして固定する。

