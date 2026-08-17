# Self-owned Rule v0 public outcome common24 rollout v1

## 結論

Rule v0 自身を subject とする実対戦を、common24 opponent pool × 両 seat × 2 repetition の 96 局で収集した。96/96 局が `DONE`、fault 0、結果は 11 win / 85 loss だった。公開 trajectory projection と action/state digest のみを保存し、private state・raw observation・teacher label は保存していない。

収集した action-type outcome diagnostic は `PLAY`、`ATTACH`、`EVOLVE`、`ABILITY`、`ATTACK`、`END` の全てで平均 outcome が負（delta は約 −95〜−104）になった。符号が全 action type で同一のため `usable_signal=false` と判定し、candidate screen / 384 拡張 / longrun は起動しなかった。これは「table が弱い」ことの証拠であり、Rule v0 の native baseline 自体の性能評価を置き換えるものではない。

## 再現コマンド

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_self_owned_public_outcome_rollout_v1.py \
  --mode common24-rollout \
  --output runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1 \
  --games-per-seat 2 --base-seed 14900000
```

4 局接続確認（性能根拠ではない）は次で再現できる。

```bash
PYTHONPATH=.:src .venv/bin/python \
  scripts/run_self_owned_public_outcome_rollout_v1.py \
  --mode smoke \
  --output runs/final-sprint-autonomous/self-owned-public-outcome-rollout-smoke-v2 \
  --games 4 --base-seed 14900000
```

## 一次 artifact と SHA

| artifact | SHA-256 |
|---|---|
| common24 `source-manifest.json` | `3e56a3911367cbcc53436c883371d6f1ff1ba169c8ecd1dc3162c6570b31e388` |
| common24 `public-outcome-records.json` | `c78e5666acd697482dcdafa1bb59b814a9cecd99c80e24d76c83d22d56d221b2` |
| common24 `rollout-summary.json` | `1b9b1e603d90b7f885e141cb666299a6c74042b4c83322e232cc8e4d33b6075c` |
| common24 `action-outcome-table.json` bytes | `105b3f7924f86ad2fc48eaf270ff1de817bd0b0b60ebd0ceb5bbbe91db385ad2` |
| table embedded `table_sha256` | `822eda2d50a66119dd6cf07d7ef318cd430fce350643340ababce14e97f4e7f0` |
| rollout public evidence root | `runs/final-sprint-autonomous/self-owned-public-outcome-common24-rollout-v1/public-evidence/` |

Source identity は root policy `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`、root deck `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、pool manifest `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`、evaluator `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84` に bind されている。engine seed は検証済み `ENGINE_SEED_UNSUPPORTED` である。

## 実装・検証

- `src/mage_ptcg/meta_specialist/self_owned_public_outcome_v1.py` は native-first overlay、public-only digest rows、authority false、table/source SHA 検証を提供する。
- `scripts/run_self_owned_public_outcome_rollout_v1.py` は smoke / common24 rollout / screen の再現 CLI である。smoke table は common24 provenance がないため screen へ渡せず、common24 table も usable signal がない場合に fail-closed する。
- 実 rollout 中に確認された `select.option[*].toolIndex` は、公開 projection の非転送 allowlistへ追加した。回帰テストを先に RED で確認し、projection/evidence/privacy suite 35 passed、self-owned + projection suite 25 passed、py_compile passed、`git diff --check` passed。

全 authority は false。training / promotion / submission / CABT longrun は起動していない。
