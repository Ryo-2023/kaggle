# V5 SetContext bounded pilot evidence (2026-08-12)

## 結論

V4 checkpointへ転送した研究専用V5 SetContext sidecarは、strict transfer、zero-head parity、実CABT adapter smoke、2 seed学習、fixed-six評価まで完走した。validation NLLは両seedで低下したが、実戦ゲートは不合格である。seed0がWave6を3勝下回り、seed1は2勝上回っただけで、seat1も4/12へ悪化した。従ってshadow-B、長時間学習、Champion変更、Kaggle提出へは進めない。

## 目的と固定条件

- 目的: 候補集合のmean/count contextとcandidate residualが、同じV4 base・同じsealed snapshotで実戦性能を安定化するかを分離評価する。
- 変更範囲: V4 model/loader、V4 policy、actor pool、submission runtimeは変更しない。V5はresearch-only sidecar。
- subject deck: `opponents/lucifer19_battlecore/deck.csv`
- subject deck SHA-256: `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- snapshot root: `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48/`
- snapshot index SHA-256: `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3`
- snapshot shard SHA-256: `e63c8b5db91fd7cf8a16c3f013a11053f82d54d00cb8b02f43a4e2a4084f937e`
- dataset snapshot root SHA-256: `5064542ae045054ee9864bc67fd11f62cc8bc3a16d019190743fca00d4bb45b2`
- split: train 1,928 records / development 436 / test 426（testは未使用）
- sequence projection: train 2,179 steps / validation 501 steps / train 35 episodes / validation 7 episodes
- objective SHA-256: `9f159bfd5640a9e63fbc5ecc15e85b1042eec94e1ac515234704d1a59781c6ad`
- training: 1 epoch、patience 0、learning rate `1e-4`、TBPTT 8、burn-in 1、35 optimizer updates/seed、`cuda:0`、torch threads 2、uniform research objective
- evaluation: fixed-six `EVAL_HELD_OUT_V1`、両seat、2 games/opponent×seat = 24 games/seed、base seed `10100000`、max steps `2000`
- protocol SHA-256: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- CABT engineにはseed setter/APIがなく、同じbase seedはgame-level paired evaluationを保証しない。結果は独立層化比較で扱う。

## 実装とidentity

追加した研究用ファイルは次の通り。

- `src/mage_ptcg/meta_specialist/neural_model_v5.py`: V4 encoder/GRUを転送し、valid candidate mean/count contextとcandidate residualを追加。head最終層はzero-init。STOPはV4 base globalから計算し、duplicate maskはpoolとlogitへ適用。
- `src/mage_ptcg/meta_specialist/neural_policy_v5.py`: actor-visible V1入力、fresh policy、decisionごとのGRU一回、commit/abort/reset、strict artifact identityを実装。
- `src/mage_ptcg/meta_specialist/recurrent_bc_v5.py`: V4 sequence/objectiveを再利用するV5専用trainer。V4 exact-type trainerを呼ばず、base provenanceをrun config/checkpoint descriptorへ保存。
- `scripts/measure_v5_set_context_checkpoint_strength.py`: V4 actor-pool kindを変更せず、deck binding→V5 policy factory→`runtime.make_agent`を接続する研究用evaluator。
- `scripts/run_v5_set_context_pilot.py`: Wave6/V4 checkpoint binding、V5 sidecar transfer、2 seed単独runner、atomic progress/reportを実装。

V5 descriptorは、V4 base file/tensor SHA、V4 transfer allowlist SHA、V5 implementation digest、V5 tensor SHA、head configを閉じ込める。既存adapter smoke用sidecarとLucifer19 BC用sidecarはbase file SHAが異なるため別ファイル名へ分離し、既存artifactを上書きしていない。

## parity / adapter smoke

実V4 checkpointからのzero-head転送について、seed0/seed1ともV4とV5のsemantic logits、base global token、STOP logitsのallclose parityを確認した。V5 evaluatorを各seed 1 game/opponent×seat（12 games/seed）で起動し、両seed fault 0、runtime factory/commit経路が実CABTで動作することを確認した。これはadapter smokeであり、性能証拠ではない。

## 学習artifact

| seed | V4 base file SHA | V5 sidecar SHA | V5 best file SHA | V5 tensor SHA | init NLL → best NLL | elapsed |
|---:|---|---|---|---|---:|---:|
| 0 | `9058fd71fed68f9c0eaec2ed4a64fae16b0ece201279696900ee544a0dcaefa6` | `437a08b11f713617d0c4c69bf7bd2a67d3c32fd7488b0dada60b50923b451af2` | `4fdc30147d71e50740fd206641b11c1cff4f9ff14935a847cd9c6af381636c26` | `9393a0ef6345b7dd7aab5b792a4148f82ea8d7095b91a1275356de3b27a2855a` | 0.457705 → 0.444270 | 143.2s |
| 1 | `b57e76cf29199d4a9f058273002dd4deafc8535abccccffbc5fef94bcbcb25a0` | `d846b8f5eed8b7307e3071a01a350e99f83c87e15a6f78b3c5c741a3d7e87e55` | `7c5ce8282686f91e65b21f39e168b4669343251549bc41e326ebbe043d006270` | `6d08a8e9c54d3d2cf1a3ffd4bd41138887866cd8b997e5640183d57c1a333ac2` | 0.480082 → 0.451917 | 142.7s |

学習reportは `runs/meta-specialist-v5-set-context-lucifer19-48-pilot-20260812/seed-{0,1}/report.json`。V5 model implementation digestは `8a6558579337447cc140ce98441e4bc90c55c26908eace502ab35655a475bfc4`、runner source SHAは `e71973ba43f9a3276a76b704f3979eebc45e892ee94c4e33be151ca0ceba1552`、trainer source SHAは `2b90aa0bbc2f5e9b74602d52e0c6d541f0a1b6fefb64aa23c707b6b0a1dc34a0`。

## fixed-six結果

評価JSONは `runs/meta-specialist-v5-set-context-lucifer19-48-pilot-20260812/seed-{0,1}/fixed-six-screen-24.json`。両seedとも24/24局完走、fault 0。

| arm | seed0 | seed1 | aggregate | seat内訳 |
|---|---:|---:|---:|---|
| Wave6 baseline | 15/24 | 10/24 | 25/48 | seed0 9/12,6/12; seed1 5/12,5/12 |
| V5 SetContext | 12/24 | 12/24 | 24/48 | seed0 6/12,6/12; seed1 8/12,4/12 |
| V5 − Wave6 | -3勝 | +2勝 | -1勝 / -2.08pt | seed1 seat1は4/12でbaseline 5/12より悪化 |

V5のaggregateは、少数局のscreenとしてWave6より1勝少ない。より重要なのはseed0が対応baselineを下回り、seed1もseat1が悪化した点で、事前ゲート「seed0/seed1それぞれbaseline以上、両seat非悪化、fault 0」を満たさない。V5 candidateの評価JSON全体SHAはseed0 `33926562b8247eee852390c7585bcdea480664caf0c725aa08f44623ac203af4`、seed1 `4a3d0d99dc46053953a562960b6b0800777244a19bd3af5c6b04bf1083fe8f06` である。

## 判定と次の方針

1. V5 SetContext architectureは「実行可能・provenance閉鎖・NLL改善」までは確認できた。
2. fixed-sixの事前ゲートは不合格。shadow-Bへ拡大しない。
3. V5 headの長時間化、head magnitude sweep、seat別後追い調整は行わない。
4. Rule v0 alpha=1、outcome weighting、STOP扱い、qualified-teacher V4 short armでseed反転が繰り返されているため、同じV4 BC局所探索の期待値は低い。
5. 次の性能主線は、現行permissionで training-local が許可された teacher のfresh collectionを、V4 topologyへ正しく投影できるか確認したうえで matched BCするか、public-only value/search targetを作ること。R7は `local_eval_only` / `smoke_ok=false` なので使用しない。
6. root `deck.csv`（Mega Lucario/Hariyama系）とLucifer19 Archaludon subject deckは別identityであり、このscreenを提出性能へ転記しない。

## 再現コマンド

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_neural_model_v5.py \
  tests/meta_specialist/test_neural_policy_v5.py \
  tests/meta_specialist/test_recurrent_bc_v5.py \
  tests/meta_specialist/test_measure_v5_set_context_checkpoint_strength.py \
  tests/meta_specialist/test_run_v5_set_context_pilot.py
```

```bash
PYTHONPATH=.:src .venv/bin/python scripts/measure_v5_set_context_checkpoint_strength.py \
  --checkpoint runs/meta-specialist-v5-set-context-lucifer19-48-pilot-20260812/seed-0/best-recurrent-bc-v5.pt \
  --subject-deck-csv opponents/lucifer19_battlecore/deck.csv \
  --subject-archetype-id archaludon --games-per-seat 2 --base-seed 10100000 \
  --max-steps 2000 --output /tmp/v5-seed0-fixed-six.json
```

## 再検証済み項目

- [x] 評価JSON全体SHAを `sha256sum` で再計算し、上記へ記録した。
- [ ] V5 pilotをshadow-Bへ進めないことをcurrent status/handoffへ反映する。
- [ ] 次のteacher/search armを開始する前に、permission・deck/policy SHA・test exclusionを再確認する。

## 追加訂正 — Wave6 baseからのarchitecture isolation（正式判定）

上記の初回V5 runはLucifer19 V4-BC checkpointを初期値にしていたため、V5 architecture単独の比較ではない。初回runは別の有効な診断armとして保持し、以下を正式なarchitecture isolationとする。

Wave6対応checkpointからV5へstrict transferし、同じLucifer19 snapshot、1 epoch、lr `1e-4`、TBPTT8、burn-in1、35 updates、`cuda:0`、同じfixed-six protocolでseed0/1を学習・評価した。

| arm | seed0 | seed1 | aggregate | seat |
|---|---:|---:|---:|---|
| Wave6 baseline | 15/24 | 10/24 | 25/48 | seed0 9/12,6/12; seed1 5/12,5/12 |
| V5 from Wave6 | 12/24 | 15/24 | 27/48 | seed0 5/12,7/12; seed1 9/12,6/12 |

V5のcandidate評価JSON全体SHAはseed0 `f30d1465c5ae001beb64bdec97d133fc419fc9dd16686ce0c5420384004f014c`、seed1 `4591dac90ffcc4b2fedc7bdb91c5f70f8c4bc5e6060c81561c77f3aeeb777981`。学習report全体SHAはseed0 `2b5e650a0d8ca716940976a36e93b22ae7cfc3d6fbcf0b612309761d9a3249bf`、seed1 `e589a2c2627de28430d84d316d2f147c5d5b5ae6fbbc208ce5069b68fe38a68e`。checkpoint file SHAはseed0 `f3ecdb31f389f0cd7ccfc4959f1486f67d35956a3655ff12b69bd2f263b8c44b`、seed1 `46750a0069a9f2c25b1ad6181e25508104bee91334cd3012f00c3298c65af46f`。tensor SHAはseed0 `ff8ea6a3d045d8cf6fb869f32c41ee97326bb23d5427fe830c469ffe6e90c409`、seed1 `b3b769352e2fcf89e5a95e475c2ebd3c2819a82cf29db2e39f5e39dec620d6d5`。

判定は不合格である。seed0の対応baseline比 `-3勝`、seed0 seat0の `5/12` 対 `9/12` の悪化があるため、seed1の `+5勝` は再現性を示さない。shadow-B、V5長時間化、head sweep、Champion変更、Kaggle提出は行わない。
