# cg Kaggle public kernel meta intake v2–v4 — 2026-08-15

## 結論

公開 Kaggle kernel から、既存 artifact／pool／source identity と重複しない
`local_eval_only` の新しい meta source を取得・生成する経路を実 CABT へ接続した。
v2c は 5 件を seal し、TRAIN-only smoke は 10/10 `DONE`・fault 0 だった。
P1 固定 CEM は 60/60 `DONE`・fault 0 で完走したが、4 candidate 全てが pilot の
seat-collapse gate により無効となり、center は P1 のまま保持した。v3/v4 ではさらに
3 件を fresh 候補として sealし、TRAIN-only接続smokeで v3 は2/4 fault、v4 は2/2 DONE
だったため、CEMへはまだ投入していない。
P1、BestKnown、Champion、production、submission は変更していない。

## Source generation

新しい取得方法は `tar SHA → safe member 展開 → bundled cg 除外 → Python AST
安全性監査 → candidate wrapper／shared cg loader → exact 60-card／canonical deck
検証 → freshness evidence／seed namespace seal` の順である。既存 pool／artifact の
policy identity は `policy_hash`、`policy_sha256`、`staged_policy_sha256` として比較し、
legacy pool の policy hash も source identity として扱う。

### v2c（TRAIN-only smoke 済み）

config は `configs/meta_specialist/cg_kaggle_kernel_meta_v2.json`、root は
`runs/cg-kaggle-kernel-meta-intake-v2c-20260815/` である。受理した 5 件は次の通り。

- `kaggle_dashimaki360_crustle_20260815`
- `kaggle_jazivxt_alakazam_rising_tide_20260815`
- `kaggle_pixiux_lucario_20260815`
- `kaggle_plamen06_steel_20260815`
- `kaggle_prvsiyan_meta_router_20260815`

pool manifest SHA は
`fd3755e7f7be013d289b0f464c0770523d31b9756e370a40441fe90f9ecb25d9`、
fresh meta SHA は
`a7156f85d196b17f7212e0a7e1e02519268b8453a74e1d24295bc2021249ecde`、
split SHA は
`614211b79c1c801b8d866312570c3fe8f0452b1a5e4ee8c5d232b56b92aa38da` である。
TRAIN 3／DEV 1／FINAL 1 の split を作成し、smoke は TRAIN 3 reference、両 seat、
各 1 局の 10 game を実行した。`DONE=10/10`、fault 0、W/D/L は `1/0/9` であり、
勝率の根拠ではなく plumbing 確認である。

### v3/v4（fresh holdout 予約）

v3 は `llccqq624` と `lucifer19` の 2 件、v4 は `koushikrudra` の 1 件を受理した。
それぞれの pool／fresh meta SHA は次の通りである。

| epoch | root | accepted | pool SHA | fresh meta SHA |
|---|---|---:|---|---|
| v3 | `runs/cg-kaggle-kernel-meta-intake-v3-20260815/` | 2 | `2b976fd734b9d2ad90967685da680e7147930d0483b3c7af87d43fcae6a53664` | `631083cd131f7a767913bd29e859ce820d6049cd19bcb1a82c5c861a98cb669a` |
| v4 | `runs/cg-kaggle-kernel-meta-intake-v4-20260815/` | 1 | `297bd57cfefbb7128691ff68ae6618e032bf99d7b0acf3b0f4e2116971135658` | `34209a640b522e9946bf5e53b2f3f1f3968a23281c63af0d729896c9abec1a54` |

v3/v4 の `fresh_meta.json` は `unused_before_run=true` のまま保全している。TRAIN接続
smoke artifact は v3 `runs/cg-kaggle-kernel-meta-smoke-v3-20260815/smoke_summary.json`
(SHA `c8a37f2b400a6e2ca8e9d72a75dec4d1e02e1c9e4cd8bd9e1b3f96cbd1e5d2ad`) と v4
`runs/cg-kaggle-kernel-meta-smoke-v4-20260815/smoke_summary.json` (SHA
`efc8dbc12f30e1a387dc24265e7674b692003b15dd81b1f2453f369156b3d4ed`) に分離封印した。
smokeは
TRAIN接続診断だけに使い、v3は `kaggle_lucifer19_battlecore_20260815` の両seatが
`AGENT_ERROR` となったため source batch を quarantine、v4は fault 0 だが CEM未実行で
ある。DEV／FINALは消費していない。

## Freshness bug fix

取り込み中に、過去 source と同じ policy を誤って受理する可能性を三つ切り分けた。

1. legacy pool の `policy_hash` を source identity として照合していなかった。
2. 過去 intake artifact root を scan root に含めていなかった。
3. policy が新規でも、過去に使った deck と同じだけで重複扱いしていた。

1/2 は source policy identity の重複として拒否し、3 は policy identity と deck family を
分離して、新規 policy が既存 deck を使う場合は許可するよう修正した。追加した回帰テストは
`tests/test_kaggle_kernel_meta_v1.py` の 3 件である。誤受理対象は v2c でそれぞれ
`source_identity_reused` または `invalid_deck`／`filesystem_write` として排除された。

## CEM 接続と fail-closed 修正

v2c の P1 固定 CEM は次の設定で実行した。

```text
population=4, elite=1, generations=1
META_TRAIN 3 refs, all train refs, 2 repetitions/seat
campaign_seed=202608152, initial_scale_fraction=0.05
positive_delta_gate=true, risk_aware_update=true, reeval_repeats=2
```

初回実行は全 60 game が `DONE`・fault 0 だったが、valid elite が 0 件のとき
`rank_valid_results` の例外で `results.json` を書く前に停止した。runner に
`_select_initial_elites` を追加し、screen row が存在しても全候補が `valid=false`
（seat-collapse 等）の場合は、`screen_valid_candidates_below_elite_count_preserve_center`
として P1 center／scale を保持し、valid 数と候補診断を no-clobber artifact に記録する。

修正後の retry root は
`runs/cg-kaggle-kernel-meta-cem-v2c-retry-20260815/` である。manifest SHA は
`ce789e3097f87d2170ead3643b44743114b6df889475fea6b49f6688c084361a`、generation
results SHA は `ad34a9cb8a74a1ee076429e8de64de084bfeac63399f16ac399f7449656f9538`、
checkpoint SHA は `6154bb4208899733db0fd5caebc9e5afad855b77ff63c07c7386122f8ade42d5`。
結果は `status=COMPLETE`、`valid_screen_candidates=0`、elite 空、P1 center 保持、
`champion_changed=false`、`submission_sent=false` である。4 candidate の screen は
`0/12`、`0/12`、`1/12`、`0/12` wins で、全て seat-collapse により invalid だった。
この結果は性能改善ではなく、source→CEM の安全な no-update 接続の証拠である。

## Verification と次の条件

- `tests/test_kaggle_kernel_meta_v1.py`: 10 passed
- `tests/meta_specialist/test_run_cg_p1_cem_v1.py`: 23 passed
- v2c smoke: 10/10 DONE、fault 0
- v3 TRAIN smoke: 4 requested / 2 DONE / 2 AGENT_ERROR（CEM投入なし）
- v4 TRAIN smoke: 2/2 DONE、fault 0（CEM投入なし）
- v2c CEM retry: 60/60 DONE、fault 0、COMPLETE、P1 unchanged
- py_compile／docs validator（13 canonical documents）／diff-check: PASS

次は v3 の fault source を再試行せず、v4 の未使用 DEV／FINAL もすぐには消費しない。
新しい source batch は TRAIN-only smokeをsource単位で完了させ、fault 0・seat-safe・独立
positive が成立した場合だけ未使用 DEV／FINAL を使う。その candidate だけを
`cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` に渡す。
commit、push、Champion 変更、Kaggle submission は行わない。
