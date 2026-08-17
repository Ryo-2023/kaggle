# Autonomous Rule v0 root deck Dusk surface — 2026-08-14

## 結論

提出互換の Rule v0 + root `deck.csv` に対し、`1102 Dusk Ball` を `135 Bloodmoon Ursaluna` または `1225 Hilda` へ1枚置換する新規候補を、runtime smoke → weighted48 → common24 → seed-disjoint 384 の順で評価した。両候補は common24 では一時的に +4.1667pt だったが、宣言優先順位1位の Bloodmoon を384局で再確認すると親を −0.9115pt 下回った。したがって両候補は `candidate_only` とし、768・longrun・promotion・submissionへ進めない。同じ候補の再実行もしない。

## 実験契約

- 親: Rule v0 closure SHA `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。
- pool: `opponents/pool_manifest.json` SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`。
- common24 config SHA `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`、evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`。
- META_TRAIN weighted subset SHA `09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed`。heldout training exposureは0。
- すべて research-only、execution/training/promotion/submission/longrun authorityはfalse。workers=12、weighted/common24はrecycle16、384はrecycle64。

## 結果

| 段階 | 親 | Bloodmoon | Hilda | 判定 |
|---|---:|---:|---:|---|
| runtime smoke（各2局） | — | 2/2 DONE, fault0 | 2/2 DONE, fault0 | 実行可 |
| weighted48 | 3/48 | 3/48 (+0.1896pt) | 3/48 (+0.1896pt) | common24 eligible |
| common24（各96） | 8/96 | 12/96 (+4.1667pt) | 12/96 (+4.1667pt) | Bloodmoonを384へ |
| confirmation384 | 41W-1D-342L | 38W-0D-346L (−0.9115pt) | 未実施 | STOP |

weighted root: `runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-weighted48-20260814-retry-v2/`。summary SHA `3c3dd00841bb13a3587758b92f7902c27c87f51168a2782489318a77c8ea7665`、manifest SHA `9b2216aad853596d695ea5ede9a3be6c38826741c3fea9d4b7c26a8cfcb40d2e`、runtime smoke SHA `fbe6089dc948131851780aa27ee7257922aab54b59fd08ab3f6bc33027b7d75e`。

common24 root: `runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-common24-20260814-v1/`。manifest SHA `4122383f5a3d46e9d08ce8cd7ce803d67b78781b3be467a920c75079f5c3dec3`、summary SHA `d54039aec1dc01ad6a45b3e8bea1a9c9334845523588aefb5480f0987fc92b77`、summary MD SHA `694ad2cd0a6ecb91dbe42a18cf7d356210244f50320e0ba8c8b76dad427153de`。全288局DONE/fault0、24 opponent、両seat、paired seed/GID gate PASS。

confirmation384 root: `runs/final-sprint-autonomous/rule-v0-root-deck-dusk-v10-confirmation384-20260814-v1/`。manifest SHA `8fc4c5deecc35f0fbb9f0bd48e482c851cd6b407f18e0d2128d952274488b54d`、summary SHA `b5f186e5979b18f6ec79c293f36f6109b77b8ca569be4beade37da92d9da5d57`、summary MD SHA `2d99861331dce42761d3aff4c1847027e973f387624ee683a7a69778f2cbe712`。全768局DONE/fault0、各arm seat192/192、24 opponent×16、same-strata/seed/GID gate PASS。parent scoreは `(41 + 0.5 draw)/384 = 10.8073%`、candidateは `38/384 = 9.8958%`。

## 実装・検証

- runner: `scripts/run_rule_v0_root_deck_novel_v10.py` SHA `58d9e64e694ae3ce0d1c8fb528c9996c1f97e98f001c07d1b52975e0f3ceda9c`。
- common24 wrapper: `scripts/run_rule_v0_root_deck_dusk_common24_v1.py` SHA `3ff46f97396e334c7f6ca19f78eae861764091403e5d109b08f4a7d12705b41d`。
- confirmation wrapper: `scripts/run_rule_v0_root_deck_dusk_confirmation384_v1.py` SHA `11768bcdd75af658864f1bfa80eada28be5ee3893c6138ddc69cf830d514b252`。
- ResourceGovernorは、WSLで`nvidia-smi`がOSによりブロックされてもCPU/memoryが健全ならCPU-only normalとして扱うよう修正した。GPUを要求する経路は`gpu_count=0`でdenyされ、GPU権限を偽装しない。module SHA `2e73dd5c5901955d074774fbcd988d42bdcecfd394acd34d05882833e9cc6e38`、回帰テスト SHA `26be635364b52fad96821013eadd4cd2ea651df48e920d9e363369fd85755a7c`。
- focused tests: `tests/test_rule_v0_root_deck_novel_v10.py` 5 passed、resource governorを含む関連 suite 12 passed。py_compile、docs validator（13 canonical documents）、`git diff --check`を実施済み。

## 取り扱い

この結果はroot deckの新規1-card mutationが384で再現しなかったことを示すcandidate-only evidenceであり、native local_eval_only policyの提出・教師化・behavior収集を意味しない。ChampionはRule v0 + root deckから変更していない。次のscreenはこの候補を再実行せず、別の新規deck/policy surfaceをruntime smoke後にworkers12で開始する。
