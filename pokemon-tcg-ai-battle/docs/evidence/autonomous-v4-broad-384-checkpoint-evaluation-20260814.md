# V4 checkpoint broad384 実性能評価（2026-08-14）

## 結論

既存の自己所有・提出互換候補の上限を見誤らないため、閉じた V4 checkpoint 2種を同一 broad24 arena で各384局へ拡張した。両runとも `workers=12`、`worker_recycle_games=16`、24 opponent × 両seat × repetition8、fault-inclusive denominator、fresh rootで実行し、全768局が `DONE` / fault0 / draw0 だった。最良は Archaludon longrun Wave4 seed1 の **224/384 = 58.3333%**、Lucifer19 outcome-weighted BC seed0 は **221/384 = 57.5521%**。96局で観測した 61.4583% / 59.3750% より低く、native Tomato の約72% benchmarkとの差も残るため、いずれも BestKnown超越・promotion・submission・longrun continuation の根拠にはしない。

## 共通条件

- evaluator: `scripts/run_performance_first_arena_v1.py`（implementation SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`）
- opponent IDs: `configs/meta_specialist/performance_first_broad_pool_v1.json`（SHA `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`）の24 ID
- subject deck: `opponents/public_archaludon_cinderace_r7/deck.csv`（SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）
- repetition: 各 opponent・seat につき8局（合計384）
- worker: `12`、recycle `16`、timeout `600s`
- authority: evaluation-only。training / promotion / submission / Kaggle送信はfalse
- pool permission: `local_eval_only`。native action、teacher label、private opponent stateは学習入力へ流用していない

## 結果

| checkpoint | W-D-L-F | score | seat0 / 192 | seat1 / 192 | runtime | 判定 |
|---|---:|---:|---:|---:|---:|---|
| Archaludon longrun Wave4 seed1 | 224-0-160-0 | 58.3333% | 113 | 111 | 839.41s | candidate-only |
| Lucifer19 outcome-weighted BC seed0 | 221-0-163-0 | 57.5521% | 115 | 106 | 826.14s | candidate-only |

長run Wave4 seed1の opponent別勝数（各16局）は、aman 10、aristophanivan_multiply 6、aristophanivan_probabilistic 5、biohack44 14、dashimaki360 13、ferozahmedds 15、harukiharada 10、itsuki9180 12、kiyotah_abomasnow 13、kiyotah_dragapult 8、kiyotah_iono 3、kojimar 6、kokinnwakashuu 10、lucifer19 6、masamikobayashi 11、medal 14、naoto_kangaskhan 10、naoto_slowking 14、naoto_ursaluna 12、official_random 15、pilkwang 3、plamen 3、prvsiyan 2、rauffauzanrambe 9。Lucifer19 seed0は同じ順で10、7、5、12、12、12、10、11、12、9、7、9、8、1、5、15、14、16、10、15、4、4、7、6だった。

## 入力 checkpoint と成果物

### Archaludon longrun Wave4 seed1

- checkpoint: `runs/meta-specialist-v4-archaludon-longrun-wave4/archaludon-training-checkpoints/seed-1/best-recurrent-bc-v4.pt`
- checkpoint file SHA: `df1e38753946463f12ed582efc0e7d949e913d24849f60cd15fc2ab1c7692af4`
- run root: `runs/final-sprint-autonomous/v4-archaludon-longrun-wave4-broad-384-20260814-seed1-v1/`
- manifest SHA: `8049f21569ef4ae3b0db77c1b95297778413fe7334bad2907468e839d2e10ca6`
- summary SHA: `63aee06d34835f2442341a414e3efef3264e97246f4198e8d88928b28440efe5`
- ledger SHA: `ed1638f26f3642e8cfa5ce13569c73ac521302b3f1790c34eead0adee9821d4e`
- progress SHA: `06236c778403a94154b43f4fafb178d27f93c50dabcafb82fd03c9597c31e5b4`

### Lucifer19 outcome-weighted BC seed0

- checkpoint: `runs/meta-specialist-v4-qualified-lucifer19-48-outcome-weighted-bc-20260812/seed-0/best-recurrent-bc-v4.pt`
- checkpoint file SHA: `24c3b82e40282e68050a7ab20832bf8a88cc0cbec4a60c63d57630b89b249a65`
- run root: `runs/final-sprint-autonomous/v4-lucifer19-bc-broad-384-20260814-seed0-v1/`
- manifest SHA: `8049f21569ef4ae3b0db77c1b95297778413fe7334bad2907468e839d2e10ca6`
- summary SHA: `49dde46ecc927c6fb662c58bea6d5a188d340626ff3bf04f53d18a21576c95af`
- ledger SHA: `54800c6e6b0424b73f10890c73155374153e0a2c0553219971d1a88a3af0867f`
- progress SHA: `3bee5dabf726744831fc4a691bc69f26e0a026e03c11c157135308b66196f051`

## 解釈と次の扱い

この評価は、96局の局所上振れを384局へ持ち込んだときの再現性を確認するためのものだった。両checkpointともfault-freeで実行できたが、native BestKnown benchmarkには届かず、既存のRule v0 submission-compatible baselineも置換しない。特に長run checkpointをそのまま R2D3/PSRO の初期 checkpointへ流用するAPIはなく、R2D3旧production artifactも現ホストに存在しないため、推測resumeは行わない。

次の性能作業は、(1) self-owned Rule v0 outcome loopのnegative結果を保持したまま、(2) 既存 R2D3/PSRO の新規 cold-start bridge、または (3) candidate deck/policyの新しい実在・提出互換 surface のいずれかを選び、同一 broad protocol・workers12で測定する。現時点で本artifactから自動的に学習・昇格・提出へ進めない。

## 検証

- 2 run: `DONE=384 / fault=0 / draw=0`
- seed/seat/opponentのledgerは各run内で一意、24 opponent × seat × repetition8
- 既存 production `main.py` / `agents/` / Champion / permission / submission artifact は不変
- commit / push / Kaggle submission は未実施
