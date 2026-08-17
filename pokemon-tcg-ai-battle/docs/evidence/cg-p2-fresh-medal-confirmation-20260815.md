# P2 fresh unused medal holdout confirmation — 2026-08-15

## 結論

P2 research parent `cg-p1-cem-incumbent-g01-c83df4408b24` を、P2 campaignで未使用だった公開 medal holdout 24件へ移し、同一 strata の P1 `cg-lethal-target-v1` と比較した。candidate/control 各384局、合計768局を実行したが、P2 は P1 より `−2.9948pt` であり、fault も9件発生したため、`NOT_PROMOTABLE` と判定する。

この結果で P2 を BestKnown、Champion、production、P3 parent、deck探索入力へ昇格しない。現BestKnown/Champion/production は P1 `cg-lethal-target-v1`＋root deck のまま不変である。今回の確認は research-only で、training、longrun、commit、push、Kaggle提出は行っていない。

## Fresh meta の固定

`runs/final-sprint-autonomous/meta-distribution-v1/manifest.json` の公開 medal は36件だった。`medal_0001_77a53ffc` と `medal_0004_01501d64` は過去の unused-meta dry run／seed A に出現済みのため除外し、次の24件を本runのfresh holdoutとして固定した。

```text
medal_0006_07bedfff       medal_0007_dd63244c
medal_0009_25393c12       medal_0010_4bf59ca5
medal_0014_f50fa3a2       medal_0015_5e60b8c7
medal_0016_706fa912       medal_0018_053b4950
medal_0019_df6f7443       medal_0020_d6c573dd
medal_0022_e40278fd       medal_0190_f06bd3d5
medal_0236_f7e1adfe       medal_0282_78fc59fb
medal_0312_a3079bb2       medal_0346_5b509bae
medal_0362_dae58a68       medal_0378_7bcec45f
medal_0427_3300b0c3       medal_0460_3e769b3b
medal_0509_203002de       medal_0590_ff157aaa
medal_2844_04dbbd93       medal_2845_67cf83ea
```

残りの10件（`medal_2849_bd32b8f7`、`medal_2850_952f9507`、`medal_2851_8543bee4`、`medal_2852_b31a602e`、`medal_2855_fba1f87c`、`medal_2856_458f87a5`、`medal_2857_0c1054dc`、`medal_2858_6644aa14`、`medal_2859_02ea57ae`、`medal_2862_65040fb4`）は後続確認用に予約した。freshness basis は「freeze時点で `cg-p2-*` artifact audit に出現していない」であり、training/submission には使用していない。

実体identityは次の通りである。

| 対象 | SHA256 |
|---|---|
| pool manifest | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| meta manifest | `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae` |
| P2 policy (`main.py`) | `4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4` |
| P1 policy (`main.py`) | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` |
| shared root deck | `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` |

## 実行契約

- runner: `scripts/run_cg_fresh_meta_confirmation_v1.py`
- base seed: `50100000`
- repetitions: `8`（各 opponent × seat）
- workers / recycle: `12 / 64`
- candidate/control: 各384局、同一 opponent・seat・repetition・CABT seed
- authority: `training=false`, `longrun=false`, `promotion=false`, `submission=false`
- artifact root: `runs/final-sprint-autonomous/cg-p2-fresh-medal-confirmation-20260815-v1/`

candidate/control の `(pair_key, seed)` 集合は各384件で完全一致した。したがって、差分は同一 strata に束縛されており、片側だけ別 seed を引いた結果ではない。

## 結果

| arm | W-D-L-F | score rate | seat 0 | seat 1 | seat gap |
|---|---:|---:|---:|---:|---:|
| P2 candidate | `188-1-190-5` | `49.0885%` | `94-0-97-1` (`48.9583%`) | `94-1-93-4` (`49.2188%`) | `0.2604%` |
| P1 control | `200-0-180-4` | `52.0833%` | `100-0-89-3` (`52.0833%`) | `100-0-91-1` (`52.0833%`) | `0.0000%` |

P2 − P1 は **`−2.9948pt`** だった。candidate seat gap は `0.002604`（0.2604%）でseat gate内だが、正差でなく、かつ fault 0 でもないため昇格条件を満たさない。

全体は `759 DONE`、`9 FAULT`（raw status はすべて `STEP_LIMIT`）だった。9 fault はすべて `medal_0019_df6f7443` に集中し、candidate 5件、control 4件である。各faultは `steps=1999 / max_steps=2000`、`STEP_LIMIT; cabt terminal result unavailable` で、両armに発生する共通 opponent/termination 事象である。これは P2 の負差を救済する根拠にはならず、faultを除外した都合のよい再集計も行わない。

判定は `NOT_PROMOTABLE`。summary の SHA は `71df165f4335a7ff76e40c86e73d9d9b5b2f378c9544060330aa567fbd0103c1`、complete manifest の SHA は `b426ba9a82ccc37eab864c85c1741b5e8777d5da33046448d5fe5e0e758002b8`、ledger の SHA は `3ad5514fb6ec9e1effb70a05debd32b4132fd86050e9de3044045ca9df984359` である。evaluator implementation SHA は `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`、runtime は `314.334316s` だった。

## 解釈と次の再開条件

今回の fresh meta でも P2 の平均優位は再現せず、CEM の screen／既存 split で得た一時的な正差を BestKnown 更新へ使えないことを確認した。`medal_0019_df6f7443` の共通 STEP_LIMIT は評価 harness 側の診断対象として記録するが、同じ holdout の blind retry はしない。

次の性能研究は、(1) 共通 opponent の termination 原因を再現可能な小さな診断で切り分ける、または (2) 予約済み10件を別base seed・同一paired契約で使う、のいずれかを先に固定してから行う。P2を親にしたCEM update、P3昇格、deck phaseは、fresh holdout で正差・seat-safe・fault0を同時に満たすまで起動しない。現時点で新しいBestKnown更新はない。

## 再現コマンド

```bash
TMPDIR=/tmp PYTHONPATH=.:src python scripts/run_cg_fresh_meta_confirmation_v1.py \
  --output runs/final-sprint-autonomous/cg-p2-fresh-medal-confirmation-20260815-v1 \
  --base-seed 50100000 --repetitions 8 --workers 12 \
  --worker-recycle-games 64 --execute
```

実行時runner SHA は `f4b8cfd4ce8a28d04f54a3cf775724346576338cd0336dc8d2872b0ec3c8750b`、実行時契約テスト `tests/meta_specialist/test_run_cg_fresh_meta_confirmation_v1.py` SHA は `6e9280d413cdd43f96415164fc4d719d5f6918b2ec7a9c08e8b395b787050a11` である。reserve batch対応の後続runner拡張は別artifactとして記録した。外部送信・commit・pushは行っていない。
