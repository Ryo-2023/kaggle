# cg historical source epoch k / cross-source confirmation — 2026-08-15

## 結論

first-parent historical snapshotを複数系統から読み出すsource-acquisition laneは、今回も新しい `local_eval_only` metaを安全にsealし、`cg_bestknown_loop_v1.py` 前段のpolicy CEMへ接続できた。ただし、k campaignの候補は単一の未使用FINALでは正差を示したものの、別sourceで座席安全性または差分の再現に失敗した。したがってP1をBestKnownとして保持し、候補をP2／deck phaseへ渡していない。

## source epoch k

`runs/cg-source-audit-20260815-k4/` は `--history-depth 40` と明示した複数remote refから22件をaccepted、158件をrejectedとしてsealした。acceptedはCynthia/Alakazam、Hydreigon/Comfey、Rocket+RLの履歴snapshotで、同一branch履歴に由来する相関sourceである。native/public metaの代替や提出性能の証拠ではない。

- intake status: `SEALED`（22 accepted / 158 rejected）
- pool SHA: `aa3dc3f3e6c3eab8a95aa9a6b0f67c958f245865cf9753cbe35b35a877441ce8`
- fresh meta SHA: `2692d8301bb752f0c78190f04142d9519745f37b0e753c810754d5470acb7e55`
- split SHA: `a644cedc468dabf75d17243953127beb281002f54e0cc7b6b9573f22ad748513`
- intake report SHA: `5a2988579c7b3359f38431b2ae1298f768d5c868dcb4b2e51349af36160f9c7d`

train smoke（META_TRAIN 4 refs、両seat、8局）は `DONE=8/8`、fault 0だった。P1 `cg-lethal-target-v1` の3勝5敗は接続確認であり、性能主張ではない。

## k policy CEM

P1をcontrolに固定し、population 8、elite 2、2世代、独立re-evaluation 2回、`positive_delta_gate`、`risk_aware_update`で実行した。screen／独立再評価／META_DEVの全局がDONE・fault0である。

- campaign root: `runs/cg-cynthia-historical-cem-20260815-k/`
- manifest SHA: `a1bc0d549cdad569625a4d01a3354f2a3955ed71cf8a816541269726f241b7c4`
- generation-0000 results SHA: `31e6bde010fb30e08f0086a738864961128501944277ec662eee1d239ecfb1a5`
- generation-0001 results SHA: `c48215e29f39a39ad99e1411864f2a54586c14655dced8722ae6a22c5dea0b7d`

gen0はscreen上位が独立評価で負差へ反転した。gen1 candidate-03/04はscreen各 `+18.75pt`、独立ではcandidate-03が `+3.125pt`、candidate-04が `0pt`で、robust eliteが不足したためcenter（P1）を保持した。META_DEVのcenterはcandidate/controlとも123W-149L、差0ptだった。

## fresh FINAL と cross-source

新規の汎用paired confirmation runner `scripts/run_cg_historical_fresh_confirmation_v1.py` を追加し、candidate/control package、fresh meta、split、seed、seat gapをhash-boundに検証してから実行するようにした。k candidate-03（policy SHA `a2c09154f282550ed6130b61b6ff1f5af0b92c71ecbbc264c3efab84f7044421`）の結果は次の通りである。

| fresh source | candidate | control | delta | seat gap | 判定 |
|---|---:|---:|---:|---:|---|
| Cynthia/Alakazam FINAL `3818c21f59b6` | 14W-0D-18L / 32 | 13W-0D-19L / 32 | +3.125pt | 0% | `PROMISING_CONFIRMATION` |
| Hydreigon/Comfey derived `litwick_setup_first` | 19W-0D-13L / 32 | 16W-0D-16L / 32 | +9.375pt | 6.25% | `NOT_PROMOTABLE` |
| Psychic derived `cheren_draw_first` | 10W-1D-21L / 32 | 9W-0D-23L / 32 | +4.6875pt | 9.375% | `NOT_PROMOTABLE` |

最初の1件だけなら有望だが、別sourceではseat gap gate（≤5%）を満たさない。従って「再現性のあるBestKnown更新」とは扱わない。一次artifactはそれぞれ `runs/cg-cynthia-historical-final-20260815-k5/`、`runs/cg-cynthia-crosssource-final-20260815-o/`、`runs/cg-cynthia-crosssource-final-20260815-n/` である。

## source acquisition audit l

新しいremote head（Double DQN、Crustle、Grimmsnarl、Rocket等）を対象にした `runs/cg-source-audit-20260815-l/` は `BLOCKED_NO_SAFE_CANDIDATES`（0 accepted / 133 rejected）だった。主な理由はartifact identity再利用とfilesystem-write quarantineである。これは重いCABTを追加で消費せず、sourceの安全性を先に閉じた結果である。

## 判定と次の条件

k campaignは「履歴から新identityを安全に作り、fresh FINALへ接続する」方法を実証したが、policy性能の再現性は未成立だった。P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submissionは不変である。次は現candidateのblind retryではなく、別系統のpermission済みsourceまたは新しいsource-generation recipeをsealし、複数の未使用FINALで正差・seat-safe・fault0を同時に確認してからloopへ渡す。

