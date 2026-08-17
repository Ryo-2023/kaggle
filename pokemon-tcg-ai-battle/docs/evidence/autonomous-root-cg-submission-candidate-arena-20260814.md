# Self-owned root `cg` submission candidate — performance arena (2026-08-14)

## 結論

既存 native agent を教師・提出物として流用せず、root Rule-v0 deck に束縛した self-owned `cg.api` policy を isolated package 化した。package archive は clean-room CABT smoke を通過し、broad 24-opponent arena では既存 Rule-v0 control を 96/384/768/1536 段階で上回った。最終 1536/arm は candidate `267W-1D-1268L-0F`（score 17.4154%）対 Rule-v0 `184W-1D-1350L-1F`（12.0117%）で、差は **+5.4036pt**。candidate 自身は fault=0 だったが control に1 faultが残るため、これは `candidate-only / research-only` の性能証拠であり、Champion更新・longrun・training・promotion・Kaggle submission は行わない。

## 固定資産と境界

- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`（60枚）。
- Rule-v0 control closure SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`。
- candidate policy source SHA: `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`。
- candidate archive SHA: `278438be73b73d1be385810530dadf6d3679711cd218b78b9847c48d15ca1bb5`。
- runtime closure は `main.py`, `deck.csv`, `cg/__init__.py`, `cg/api.py`, `cg/sim.py`, `cg/utils.py`, `cg/libcg.so` の7 regular files。unknown third-party import=0、blocked=0、`libcg.so` の dlopen traceを確認した。
- package manifest は `submission_ready=false` とし、公式 Rule/Student verifierは異なる package schema のため `not_applicable_different_cg_runtime_shape` と明示した。これは提出成功を意味しない。
- authority は training/promotion/submission/longrun/teacher 全false。native poolはlocal_eval_onlyで、candidate policyはnative code copyではない。

## Clean-room package smoke

`runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/` の `submission.tar.gz` を archive から抽出し、repository source を除いた subprocess で2局実行した。

- `DONE=2/2`, `faults=0`, `illegal_actions=0`, `steps_total=73`。
- runtime contract は Python 3.12 / `kaggle-environments==1.32.0` host contract。CABT engine はbundle外のhost提供で、欠落時はfail-closed。
- builder SHA `6d3b9798662dca5b7ea6af981978169fb869ad84c7a95c83f09b3167aa8279b`、candidate archive manifest SHA `282be186acd0466b083c7948e1465196a80ea2885e3339e845470b9b3f594fa0`。

## Performance stages

同一 broad 24 IDs、同一 root deck、同一 opponent/seat/repetition strata、seed-disjoint block で candidate/control を比較した。全段階 workers=12、weighted/common24 は recycle16、384/768/1536 は recycle64。

| stage | self-owned candidate | Rule-v0 control | delta | integrity |
|---|---:|---:|---:|---|
| common24 (96/arm) | 11/96 = 11.4583% | 7/96 = 7.2917% | +4.1667pt | 192/192 DONE, F0 |
| confirmation384 retry (384/arm) | 60/384 = 15.6250% | 34/384 = 8.8542% | +6.7708pt | 768/768 DONE, F0 |
| confirmation768 (768/arm) | 123/768 = 16.0156% | 92/768 = 11.9792% | +4.0365pt | 1536/1536 DONE, F0 |
| longrun1536 (1536/arm) | 267W-1D-1268L-0F = 17.4154% | 184W-1D-1350L-1F = 12.0117% | +5.4036pt | 3071/3072 DONE, one control F |

The first 384 block had one candidate pre-CABT identity fault (`deck identity changed`) while the same game reproduced as `DONE` in a direct isolated call. It was not used as promotion evidence; one targeted disjoint 384 retry was run after adding detailed SHA diagnostics, and that retry was 768/768 DONE/fault0. The longrun control-only fault similarly occurred before CABT on a transient empty read of the repository deck; candidate fault count remained zero. Both caveats remain in the ledger and are not converted into wins.

Longrun opponent-level deltas were positive on 17/24 opponents, negative on 6/24, with one control fault. Largest positives were `naoto714_kangaskhan` +39.1pt, `official_random` +26.6pt, `medal_0001_77a53ffc` +17.2pt, `kokinnwakashuu_lucario_search` +10.9pt; notable regressions were `itsuki9180_lucario_jp` −7.8pt, `rauffauzanrambe_advanced` −7.8pt, `naoto714_slowking` −6.2pt, and `naoto714_ursaluna` −5.5pt. This is a broad-meta overfit signal, not a universal superiority claim.

## Artifact hashes

- common24 summary: `db9959ce8678446a39bc46bc5c7fbe6442316f4575cb6424f33b855b5df4de2a`.
- 384 retry summary: `c2b34e9a7081a446a66b3fc5597643b6b8b11310800a4e184a73806c6ff20f01`.
- 768 summary: `7923651287a2178af72c10a6d002231b43b84434b81064359a1c0ca53bcb850a`.
- 1536 summary: `82526a6621853feac9e36f86bdc12a4cb1e6afa3a2a06f3feaa152302f6a58ea`.
- runner SHA: `a429e85a669e944e38e961969edcfdb218b761033c9e38210e70bc3646bddb1b`.

Focused bridge tests: 3 passed; py_compile and `git diff --check` passed. Production `main.py`, `agents/`, Champion, root `deck.csv`, and native artifacts were not edited by this route. No commit, push, training, promotion, or submission was performed.
