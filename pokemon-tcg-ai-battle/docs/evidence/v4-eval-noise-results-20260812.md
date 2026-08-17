# V4 同一checkpoint評価noiseの実測

確認日: 2026-08-12

## 目的

ChatGPT Proレビューの最優先診断として、学習seed差とCABT評価noiseを分離する。現行CABTにはengine seed setterがなく、block間は独立・層化評価である。同一checkpointを同じprotocol・subject deck・held-out sixへ96局ずつ3 block投入し、勝率のwithin-checkpoint分散を測った。

## 固定条件

- evaluator: `scripts/measure_v4_checkpoint_strength.py`
- subject deck: `opponents/tomatomato_archaludon/deck.csv`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- held-out: `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`, `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario`
- 6 opponents × 2 seats × 8 games/opponent-seat = 96 games/block
- protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- evaluator SHA: `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835`
- faults: 全6 blockで0
- engine pairing: 不可。base-seedはagent側seed/block identityでありCABT engine RNGを固定しない。

## 結果

### Wave6 seed0

checkpoint file SHA `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`、tensor SHA `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a`。

| block seed | wins | losses | score | seat0 | seat1 | JSON SHA |
|---:|---:|---:|---:|---:|---:|---|
| 30100000 | 44 | 52 | 45.83% | 23/48 | 21/48 | `503c1d9562becbbdc15d231291793b61555f7aa55a18531524cd034e46859675` |
| 30200000 | 49 | 47 | 51.04% | 26/48 | 23/48 | `3778244ae0ee08a7c2f1ecac714d4443178450cb40b6956d8aeb46da985a2505` |
| 30300000 | 46 | 50 | 47.92% | 25/48 | 21/48 | `c99236a570321921187eb85e534c17c612eb99082b683e9271db5f31372e1055` |

平均 `48.26%`、sample SD `2.62pt`、range `5.21pt`。

### Wave6 seed1

checkpoint file SHA `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6`、tensor SHA `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a`。

| block seed | wins | losses | score | seat0 | seat1 | JSON SHA |
|---:|---:|---:|---:|---:|---:|---|
| 31100000 | 42 | 54 | 43.75% | 20/48 | 22/48 | `c2ad9bffa4db6fd13393eb4f26f06ded2e1212b14b71b2da62087ae902b61fe8` |
| 31200000 | 46 | 50 | 47.92% | 25/48 | 21/48 | `b99e336108cb3af92ca3d10adae6f84e7e8235b5a3662e54379c2f9d73eb57be` |
| 31300000 | 56 | 40 | 58.33% | 24/48 | 32/48 | `3f6621d3fabf6870fff67ee433878318f02a8fc6d7a9fb6e8889985a29c72c7a` |

平均 `50.00%`、sample SD `7.51pt`、range `14.58pt`。

## 解釈

1. Wave6 seed0でも同一checkpointの96局blockが45.83〜51.04%へ動き、24局screenの1〜数勝差は評価noiseと同程度になり得る。
2. Wave6 seed1は3 blockで43.75〜58.33%まで動いた。seat1がblock 31300000で32/48となるなど、seat/opponent層の偏りが大きい。
3. seed1 checkpointの平均50.00%がseed0平均48.26%より高いという差は、今回3 blockだけではtraining seed効果と評価noiseを分離できない。
4. 既存の24局 fixed-sixで「seed0が下がりseed1が上がった」candidateを、直ちに学習seed instabilityと確定してはいけない。ただしpolicy drift smokeと合わせると、評価noiseだけでなく実際のpolicy差も併存している。
5. 同一checkpointのblock間差を上回る改善をlongrun gateへ要求する根拠が得られた。現時点のpublic OOD aggregate差0ptや従来の+1〜2勝は、このnoise floorを下回る。

## 未実施

- action-reset / turn-resetの本格ablationは未実施（2局接続smokeのみ）。
- tomatomato-96 candidate seed0/1の3 block反復は未実施。
- 反復結果を用いた階層モデル・meta-weighted Elo推定は未実施。

## 再現コマンド例

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v4_checkpoint_strength.py \
  --checkpoint <closed-v4-checkpoint> \
  --subject-deck-csv opponents/tomatomato_archaludon/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 \
  --games-per-seat 8 --base-seed <disjoint-block-seed> \
  --max-steps 2000 --output <new-json>
```

本資料は評価noise診断であり、promotion、Champion変更、longrun、Kaggle提出の根拠ではない。
