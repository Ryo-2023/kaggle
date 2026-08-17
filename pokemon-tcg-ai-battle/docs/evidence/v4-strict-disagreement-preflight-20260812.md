---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
---

# V4 strict-disagreement preflight（2026-08-12）

## 結論

GPU access の停止原因は Codex sandbox の `/dev/dxg` 非公開であり、sandbox 外の承認済み実行では復旧した。strict overlay は、complete episode を context として通しつつ、eligible な disagreement prefix だけを `supervision_weight=1`、その他を `0` として trainer の loss から除外する実装へ変更した。seed0/seed1 の CPU report と、teacher-target-only / 対称 filter / confidence threshold の比較を生成済みである。

新規 strict-disagreement pilot を開始するためのデータ面の preflight は完了した。ただし、この文書は pilot の勝率・Champion変更・提出を承認するものではない。

## strict loss semantics の修正

以前の実装では、91ゲーム等の complete episode を選択しても、trainer に loss mask が渡っていなかった。これでは selected episode 内の全 prefix が loss-bearing になり、`effective_loss_mass` は選択メタデータに過ぎなかった。

今回の修正:

- `RecurrentBCStepV4.supervision_weight` を追加（既定値 `1.0`、許容範囲 `[0,1]`）。
- strict disagreement の mismatch prefix だけを `1.0` にし、同じ episode の他 prefix は hidden-state context-only の `0.0` にした。
- trainer の train/evaluation/positive STOP metrics が `supervision_weight` を参照する。
- objective hash と DAgger record hash に mask を含め、mask の変更を同一実験 identity とみなさないようにした。
- `supervised_prefix_count` を report へ追加し、selection metadata の effective mass と照合できるようにした。

検証結果:

```text
tests/meta_specialist/test_run_meta_specialist_v4_dagger_bc.py
tests/meta_specialist/test_recurrent_bc_v4.py
tests/meta_specialist/test_dagger_v4.py
tests/meta_specialist/test_recurrent_dataset_v4.py
60 passed, 1 skipped
```

追加回帰テストでは、context-only prefix を評価 NLL と mask denominator へ寄与させないことを確認した。

## seed別 screen / strict report

| seed | screen transitions | broad disagreement prefix | full non-forced effective mass | teacher-target-only `-0.2` mass | selected games | mask prefix count | mass/full |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 4,763 | 3,076 | 4,498 | 851 | 88 | 851 | 18.92% |
| 1 | 5,590 | 3,707 | 5,357 | 985 | 91 | 985 | 18.39% |

`full non-forced effective mass` は selected game だけの合計ではなく、screen 全体の prefix mass を分母にしている。詳細な hash、opponent/seat、domain、prefix、confidence 配列は [preflight JSON](../../runs/meta-specialist-v4-strict-disagreement-preflight-20260812.json) と seed別 broad/action report を正とする。

seed0 でも strict report を生成でき、seed1 だけに依存する overlay ではない。ただし strict prefix mass は seed0 851、seed1 985 と異なり、同じ data-policy seed 再現を意味しない。

## action-type filter の対称比較

target set は `{9,13,14}`（EVOLVE/ATTACK/END）。各 prefix の disagreement を集計した結果:

| seed | filter | `-0.2` prefix | `-0.5` prefix | `-1.0` prefix |
|---:|---|---:|---:|---:|
| 0 | teacher target only | 851 | 106 | 0 |
| 0 | student **or** teacher target | 867 | 106 | 0 |
| 1 | teacher target only | 985 | 110 | 0 |
| 1 | student **or** teacher target | 990 | 110 | 0 |

対称化の増分は `-0.2` で seed0 +16、seed1 +5 に留まる。現時点では filter を勝率で再探索せず、pilot の arm identity として一度固定する。`-0.5` は mass が急減し、`-1.0` はゼロになるため、confidence threshold は単なる「低信頼」名ではなくデータ量をほぼ決める制御である。

## disagreement confusion

| seed | false negative（teacherが対象、studentが対象外） | false positive（studentが対象、teacherが対象外） | within-type | unrelated |
|---:|---:|---:|---:|---:|
| 0 | 1,424 | 32 | 390 | 1,230 |
| 1 | 1,737 | 28 | 482 | 1,460 |

teacher-target-only は false negative / within-type を主に拾い、false positive をほぼ対象外にする。対称 filter はこの非対称性を部分的に補うが、増分は小さい。なお teacher は `UniformLegalPolicyFactory` であり、target logits の top1 margin は対象 prefix で 0（tie-break による選択）だった。従って teacher target への agreement は teacher correctness の証明ではない。

## 固定六 opponent の seed内訳

既存 strict-paired evaluation JSON は opponent別の周辺集計と seat別集計を保存している。opponent×seat の同時セルは ledger に残っていないため、そこは未取得として扱い、推測で補わない。

opponent別（`wins / 16 games`、baseline → candidate）:

| opponent | seed0 | seed1 |
|---|---:|---:|
| kiyotah_lucario | 10→12（+2） | 9→11（+2） |
| sue124_alakazam | 6→8（+2） | 12→5（−7） |
| skarin_dragapult | 6→6（0） | 3→6（+3） |
| ozawa_crustle_v2 | 6→3（−3） | 6→9（+3） |
| nihei_megalopunny | 8→11（+3） | 10→10（0） |
| yaroslav_crustleaware_lucario | 7→10（+3） | 10→10（0） |

seat別（`wins / 48 games`、baseline → candidate）:

- seed0: seat0 `24→26（+2）`、seat1 `19→24（+5）`
- seed1: seat0 `26→24（−2）`、seat1 `24→27（+3）`

seed0 と seed1 で opponent/seat の改善位置が反転しており、合計 +4.17pt は再現性の証拠ではない。

## shadow-B freeze

既存 shadow-A は新規 strict arm の選択に使われたため、promotion-untouched ではなく development-external diagnostic として扱う。shadow-B は以下6件を固定した。

`biohack44_crustlecounter2`, `harukiharada_crustle`, `kiyotah_iono`, `naoto714_ursaluna`, `pilkwang_lucario_alakazam`, `prvsiyan_grimmsnarl`

manifest: [shadow-B manifest](../../runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json)

manifest SHA-256: `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`

fixed-six と shadow-A の deck/policy SHA、shadow-B cohort 内の deck/policy SHA は重複しない。各 candidate の `SOURCE.md`、`deck.csv`、`main.py` と SHA-256、および freeze 時点の V4 JSON/Markdown への ID 非出現を検証した。CABT、fault 0、速度、強度は未測定であり、学習中の arm 選択には使わない。

## 次の許可境界

GPU access は復旧しているが、次に許可されるのは seed0/seed1 対応 screen からの fixed-budget strict-disagreement pilot だけである。長時間学習、best-seed選択、Rule v0との promotion 判断、Champion変更、Kaggle提出はこの preflight からは導かない。
