# 公開Kaggle kernel meta intake v1（2026-08-15）

## 判定

このepochは`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`である。公開kernelをshared `cg` engineへ隔離するsource生成・freshness・loader smoke契約は成立したが、P1固定CEMの全候補が新metaに対して有効な勝ちを得られず、P1をBestKnownとして保持した。P1、root deck、Champion、production、submission authority、`opponents/pool_manifest.json`は不変である。

## 目的と境界

既存internal snapshotのidentityが消費済みになったため、公開Kaggle kernelのローカル取得済みtarを新しいmeta sourceとして使えるかを検証した。取得と検証は読み取り専用のローカル実験であり、Kaggle提出、外部再配布、teacher label利用、submission bundleへの混入は行っていない。すべての生成物は`local_eval_only`で、training/promotion/submission/longrun権限はfalseである。

## 実装

- `src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py`
  - tar SHA、絶対経路・`..`・symlink/hardlink・非regular member・容量上限を検証する。
  - bundled `cg/`、cache、pyc、submission archive、notebookをpayloadから除外する。
  - retained Python全件へAST static scanを行い、network、subprocess、dynamic import/execution、filesystem write/delete、secret literalをfail-closedで拒否する。
  - candidate固有wrapperがpayload moduleを隔離し、祖先からrepoのshared `cg`を解決する。wrapper call中は候補rootをcwdにしてdeckの相対読み込みを閉じる。
  - `pool_manifest.json`、`fresh_meta.json`、source evidence、`local_eval_only` authorityをhashでsealする。
- `scripts/generate_kaggle_kernel_meta_v1.py`
  - network取得を持たず、既存tarのSHA一致だけを確認するCLIである。
- `configs/meta_specialist/cg_kaggle_kernel_meta_v1.json`
  - 5 kernel、3/1/1（META_TRAIN/DEV/FINAL）のsourceとsplitを固定する。

初回wrapperはpool rootが`runs/`配下にある場合の既存loaderのrepo root推定と衝突した。wrapperへ祖先探索を追加し、修正版を`runs/cg-kaggle-kernel-meta-intake-v1b-20260815/`へ再sealした。v1b以外のrootは実験途中の監査用であり、CEM sourceには使っていない。

## source batch

| candidate | kernel | tar SHA-256 | source policy SHA-256 | canonical deck SHA-256 | staged wrapper SHA-256 |
|---|---|---|---|---|---|
| `kaggle_tetsutani_grimmsnarl_20260815` | `tetsutani/grimmsnarl-ex-damage-transfer-control` | `04f9779b77d17417570189d06a1b7ff5b0016797639a2a45f4b53bc02e945712` | `c61e540bcb45aa2e8184ae912e7e17efaa900dba3df4536468da41899b09dcd8` | `cafa7652a6349be806d8ac2b9abfdb6c72ca3821f368e0d912e2d989f3b54cdd` | `daeea1889d89e686a2bfd1cdbe26023667332bb91bf4aecc2dc50a2c709b6ef2` |
| `kaggle_jazivxt_alakazam_20260815` | `jazivxt/codex-sol-eclipse-alakazam` | `78dde4d68910a7c841a4c989a7e39fe8ae4ec15b0ba278f28b7ba43cdec5476b` | `f31eba2e819ee2b3d46765b4195ea7dab8f32d0b5d09cafd39b3823661f6b5aa` | `e656740ab5d19a958fe1a2d05ca05d49bea09b273a5cb593de5e1d4d9cbb8340` | `ae48ad9f70bde38db7b9559cbb0c7caacc6ea9e8cd4ed93fc3a8e6914fae3ce9` |
| `kaggle_jazivxt_crustle_20260815` | `jazivxt/crustle-counter-al220-v29-agents-only` | `c342491c38afe44941efb366dbb212825381f3de64b286170029f7db1e795a16` | `5efff0b1c51d86adff8d9c134fdf45cb05c3cc0d5b344510b7e35f42ce1db70b` | `6b466aa49f6c5722bd7d9915c118abc6bb9d4983444ee34fbcf34e9a959dab1b` | `a7f769f461796a263cf3ec52be1cd3df4e7932bc7eaba42ed8281b43869109e8` |
| `kaggle_jazivxt_garchomp_20260815` | `jazivxt/garchomp-gpu-v28-agents-only` | `1197f40380ad59a2a80963f0d2da8ddca2c02a3040a71194ab4cd9c310d5e79a` | `e99465c757231679cd038a9ebb401b3c1e9228bf58e3efcd386ab681b5ca6fbc` | `39fb18fd9ff204e86299a92ac22092fdd41fb6111a48febb82aabd2039d01ef` | `568da4eefc836cb8b316f1075c180842ec7a908065ae328857f5b36e5e69d645` |
| `kaggle_prvsiyan_grimmsnarl_v21_20260815` | `prvsiyan/ptcg-ai-battle-visible-grim-belief-alakazam-v21` | `6bec348a7ed0191f45d2f49a5ff7c4b9cdbd7aa6172f11dc96eaea84e27b22e4` | `26430640c7670d6de1bf5d8e0818d18ce04c3c510402634539a2c555478242cc` | `606a775392ffe25e058b19c17801d58a4bf30f7cd8c62782388d3de7e7eb5283` | `084de53f1c5b4a229b37af1e1def253c7a77f29b2b21622daa96db4058004769` |

5/5件がstatic scan、exact 60、pool loader preflightを通過した。bundled `cg`は全件から除外し、shared repo `cg`だけを実行対象とした。

## sealed artifacts

- pool root: `runs/cg-kaggle-kernel-meta-intake-v1b-20260815/`
- pool manifest SHA-256: `0de2046dac59b826faf314a9a8a3012fa388cdff6922488221a8908c39074f99`
- fresh meta SHA-256: `92a110c3412f3b6d7dfde8ea0e4674560028ff9be9ee2853d4487c0fd49ff788`
- historical meta SHA-256: `dfef2207809fe6ebbcf2df8c2cda82f6bde43ccf83a601a8a0f391e42db51000`
- split SHA-256: `2570d31b37614a8a94a6195cbd8507f88336eb9d2ec336d1f3173f09d3255e31`
- split report SHA-256: `9c025839f3737f16290df011365dcef14befeab94081a9229af5d2e7b9b32fd2`
- split allocation: TRAIN 3（tetsutani / Alakazam / Crustle）、DEV 1（Garchomp）、FINAL 1（prvsiyan）
- all `fresh=true`, `unused_before_run=true`, `training_exposure=0`

## TRAIN-only smoke

P1 package SHAは`1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`である。base seed `20260890`、TRAIN 3 source、両seat、各source/seat 1局、workers 1、timeout 120秒で実行した。

- requested/completed: 6/6
- DONE: 6、fault: 0、draw: 0
- P1: 0W-0D-6L、score rate 0.0%
- smoke summary SHA-256: `9d74810edf31a044f2cd5e79582c1843372c967e18bf4039d9cad541b6e6ed8b`

この結果に基づき、v1b pool rowsの`smoke_ok`をtrueへsealした。DEV/FINALはこのsmokeには投入していない。

## P1-fixed CEM

P1をsource/controlの両方へ固定し、`META_TRAIN_ALL`のみを検索へ渡した。DEV/FINALはCEM選抜へ渡していない。

```text
PYTHONPATH=.:src python scripts/run_cg_p1_cem_v1.py \
  --output runs/cg-kaggle-kernel-meta-cem-v1b-20260815 \
  --split runs/cg-kaggle-kernel-meta-intake-v1b-20260815/cg_historical_split.json \
  --source-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package \
  --control-package runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package \
  --pool-root runs/cg-kaggle-kernel-meta-intake-v1b-20260815 \
  --generations 1 --all-train-refs --reeval-for-update \
  --reeval-games-per-opponent-seat 2 --positive-delta-gate \
  --campaign-seed 202608151 --population-size 4 --elite-count 1 --execute
```

generation 0はpopulation 4、search 48 candidate games＋12 control games、全60局DONE/fault0だった。候補4件は全48局で0W-0D-48L、controlは1W-0D-11L。したがって`seat_collapse`を含む有効eliteは0件で、独立re-evaluation、DEV、FINAL、generation 1は起動せず、runnerは次で停止した。

```text
ValueError: not enough valid candidates for elite update: 0 < 1
```

- CEM evaluation summary SHA-256: `a2eefb6e89b42d3231fc277ce008ec32cee39d6f267908a0fe76b77f011fbb8c`
- CEM manifest SHA-256: `2ecf5335f75d2c4c56741cb0a80ab18c24e8dc9572fbcd0e2a605af48cd69283`
- stop SHA-256: `c3b1aacc03fe21187c846d94422bf1649c91ea31aee55a4b400e5d05fd63b306`
- result: P1 center保持、P2/P3昇格なし、deck phaseなし

新sourceはP1に対して強いhard-negative poolとして機能した一方、今回の小規模CEM budgetではP1近傍に有効候補が出なかった。これは公開kernelのKaggle scoreやnative/public leaderboard性能を意味しない。

## 次の優先順位

1. 同一5件のblind retryはしない。まずsource batchを「強いhard-negative」として保持する。
2. 既存public kernelから、deck familyとpolicy familyの相関が低い安全候補を追加し、TRAIN source数を増やす。新候補も同じtar/AST/wrapper/fault0契約を通す。
3. `META_TRAIN`がP1を全敗させる場合は、CEM前のscreenでP1/controlのbaseline separationを記録し、候補有効性が0件なら自動停止する。
4. fresh DEV/FINALはCEM後の独立確認専用として未使用を維持する。positive、fault0、seat-safe、opponent×seat-safeを満たすcandidateだけを`cg_bestknown_loop_v1.py`へ渡す。
