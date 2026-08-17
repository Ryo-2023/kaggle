---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
---

# V4 Wave3 post-run audit — 2026-08-12

## 結論

Wave3 targeted DAgger は学習途中で止まったのではなく、2 seed とも学習・checkpoint 保存・report finalization まで完了している。旧 context pack に残る `status=running` は stale である。学習損失と artifact integrity は正常だが、同一評価条件で Wave6 を下回り、targeted intervention は不採用とする。

Rule Agent v0 を同一 Archaludon deck、固定6相手、両 seat、96局で直接測定した結果は 12/96 (12.50%, fault 0) だった。現在の student は Rule v0 を大幅に上回っているため、次の本質的課題は「Rule v0 への追随」ではなく、Wave6 からの安定した増分改善と評価 selection bias の解消である。

## 実行状態と identity

- branch: `feature/belief-guided-search`
- HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16`
- working tree: tracked 24件 + untracked 217件（うち本 audit note が1件。既存差分を revert していない）
- Wave3 report: `runs/meta-specialist-v4-archaludon-dagger-wave3-targeted-balanced/bc.json`
- progress: `bc.progress.json` は `status=complete`, `stage=complete`, `seeds_completed=2`
- best checkpoint は seed0/seed1 とも存在し、`last-recurrent-bc-v4.pt` も存在する。run root に一時ファイルは残っていない。
- 現在の shell から Wave3 training process は見えず、`nvidia-smi` は `GPU access blocked by the operating system`。これは現在の診断環境の制約であり、完了済み run の失敗証拠ではない。

Wave3 の閉じた入力 identity は次の通り。

| artifact | SHA-256 |
|---|---|
| init checkpoint (Wave6 seed0) | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` |
| Wave3 seed0 best checkpoint | `6993f07fa73bcc22e1f79a051b7d4265910b41c05e288a5dd6ec0912f30a5228` |
| Wave3 seed0 tensor state | `366f19b85ff45938de6d99b09bd0626fa6eb52a32f57c915024add36a96eee89` |
| Wave3 seed1 best checkpoint | `f6e816f2d8ad324bed947934dde14aec23565e210cfe944966845abf730eec55` |
| Wave3 seed1 tensor state | `c6c379a4f9ef25958646dc193e44172ce3520783d3de4c6a20dbd57744d3ac72` |
| subject deck `public_archaludon_cinderace_r7/deck.csv` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| selection manifest | `b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc` |
| Wave6 screen | `9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3` |
| DAgger transitions | `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` |
| trainer implementation closure | `3441cc5241c80e82baf2168647eff9fc56e5236f05e50a9d44c84a6b5edc17e0` |
| held-out evaluation protocol | `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba` |

`trainer_implementation_sha256_v4()` の live 値は report と一致し、両 best checkpoint は descriptor の file/tensor SHA と一致した。V4 strict loader による implementation/live-callable closure 検証も seed0/seed1 とも PASS した。

runner の `train_recurrent_bc_v4` は train sequence 1件につき optimizer step 1件を実行する。Wave3 report の各 seed は `epochs_completed=3`, 各 epoch `optimizer_updates=96`, 累積 `optimizer_updates_completed=288` であり、`96 × 3 = 288` は training loop 完了と整合する。各 epoch の best/last checkpoint 保存後、最終 report と progress が atomic に complete 化されている。

## 比較結果

以下の Wave3/Wave6 は、同一 protocol SHA、同一 subject deck SHA、同一 opponent fingerprints、固定6相手、両 seat、各 seed 96局で比較した。`v4-fixed-heldout-*`（別 evaluator 世代の 89/192）はこの表へ混ぜていない。

| arm | seed 0 | seed 1 | 合計 | fault |
|---|---:|---:|---:|---:|
| Wave6 current evaluator | 47/96 | 51/96 | 98/192 (51.04%) | 0 |
| Wave3 targeted balanced | 52/96 | 41/96 | 93/192 (48.44%) | 0 |
| Wave3 − Wave6 | +5 | −10 | −5 (−2.60pt) | — |
| Rule Agent v0 direct audit | — | — | 12/96 (12.50%) | 0 |

Rule v0 direct audit は `main.py` の entrypoint SHA `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7` を記録し、同じ protocol/deck/base seed (`10000000`)/max steps (`2000`)で実行した。生の集計は `/tmp/rule-v0-heldout-20260811.json` に保存した。既存の `rule-v0-archaludon-fixed6-seed9700000-96.json`（8/96）は protocol/deck/opponent fingerprint を持たないため、直接比較の根拠には採用していない。

## seed / seat / opponent / target–guardrail

### seed と seat

- seed0: Wave3 52/96 (54.17%) vs Wave6 47/96 (48.96%), **+5局**
- seed1: Wave3 41/96 (42.71%) vs Wave6 51/96 (53.13%), **−10局**
- seat0: Wave3 42/96 vs Wave6 46/96, **−4.17pt**
- seat1: Wave3 51/96 vs Wave6 52/96, **−1.04pt**
- Rule v0: seat0 6/48, seat1 6/48

### opponent 別（各32局）

| opponent | Wave6 wins | Wave3 wins | 差 |
|---|---:|---:|---:|
| Kiyotah | 21 | 19 | −2 (−6.25pt) |
| Nihei | 20 | 16 | −4 (−12.50pt) |
| Ozawa | 14 | 11 | −3 (−9.38pt) |
| Skarin | 15 | 15 | 0 |
| Sue | 11 | 13 | +2 (+6.25pt) |
| Yaroslav | 17 | 19 | +2 (+6.25pt) |

focus 3相手を全 seat で集約すると Wave6 55/96 → Wave3 46/96（**−9局, −9.38pt**）。non-target guardrail 3相手は Wave6 43/96 → Wave3 47/96（**+4局, +4.17pt**）。相手×seat のセル別勝数は既存評価 report に保存されていないため、そこは未測定として扱う。

### DAgger overlay の実効寄与

Wave3 report は available 96 episode から DAgger 42 episode（実 fraction 0.3043478）を混ぜている。focus component ID は screen の全96 componentを含むため、priority prefix を再構成して selected overlay を集計した。

| group | selected episodes | transition records | eligible decoder steps | action-weighted mass |
|---|---:|---:|---:|---:|
| focus target (Kiyotah/Nihei/Ozawa, seat1) | 17 | 1,049 | 999 | 1,018.29 |
| non-target / other seats | 25 | 1,582 | 1,524 | 1,534.78 |
| 合計 | 42 | 2,631 | 2,523 | 2,553.06 |

従って overlay の約60%が非 target である。teacher relabel 後の action-weighted mass は ATTACK(type13) 1,344.49、CARD(type3) 717.80、END(type14) 239.51、ATTACH(type8) 167.51、type2 77.88、type0 5.88で、EVOLVE(type9) は0だった。これは「EVOLVE/ATTACK/ENDをfocusに指定した」ことがそのまま teacher target の勾配 mass になっていないことを示す。正確な全 selected objective identity は report の `selected_sequence_sha256=f761f2b8479e7482be251057b7dfc6fc65583d20db3fdce809a9d5692aa2f886` を正とし、上記は `/tmp/wave3-dagger-selected42-effective-mass-20260812.json` に保存した再構成診断集計である。

validation の action metric も、seed0/1 で ATTACK top1 `0.4369/0.4053`、END `0.5116/0.5233`、EVOLVE `0.6792/0.6415`に留まり、CABT勝率の改善を裏付けない。action metric と対戦性能は分離して評価する。

## 判断と次の最大3作業

1. **strict disagreement target の短期 arm を再設計する。** Wave6 seed1 checkpoint と同一 identity の新しい Screen を作り、teacher/student の完全 action disagreement、target action type、低い behavior probability を game 単位で抽出する。target episode 数だけでなく、teacher target の effective loss mass と opponent/seat coverage を事前に report する。
2. **fixed-six を development pool として扱い、shadow pool を固定する。** Kiyotah/Nihei/Ozawa は Wave2 結果を見て選んだため、fixed-sixでの非悪化を generalization evidence と呼ばない。現在 repo に同じ identity で sealed な untouched shadow 評価 artifact は見つからないため、候補選択後に別 qualified opponent cohort を freeze する。
3. **短期 arm のみを同一条件で再評価する。** 2 seed 各96局、fault 0、seed consistency、target aggregate、guardrail aggregate、worst-case harm を先に測る。既存 gate の +5pt は長時間化の実務 filter とし、統計的証明とは扱わない。

## 今回行っていないこと

Wave3 の再起動、同じ output root への上書き、Wave4/長時間 DAgger、RL longrun、Champion変更、commit、push、Kaggle提出は行っていない。今回の変更はこの evidence note と status/handoff の追記のみで、既存のコード・checkpoint・deck・submission は変更していない。
