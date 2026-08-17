# P1 local CEM と Carmine tempo surface — 2026-08-15

## 結論

P1 `cg-lethal-target-v1`＋root deckを固定し、P2 c83近傍の狭幅CEMと、未評価だった公開 `turn`／`supporterPlayed`／Carmine play surfaceを実CABTで確認した。全完走したCABT blockは `DONE`・fault 0 だったが、BestKnownを更新できる候補は得られなかった。P1、root deck、Champion、production、submissionは不変である。

## Campaign 13: P2 c83 local CEM

各P1 parameter spanの5%を初期Gaussian scaleにする `--initial-scale-fraction 0.05` を追加し、P2 c83 config（SHA `c83df4408b247cb2418f684e2557d69dcde4626c8d81330bb1e9890ee022a9eb`）をcenterにした。`META_TRAIN_ALL`、population 24、1世代、screen 1,200局、top6×3独立blockの再評価2,016局を完了した。

- screen上位は c14 `+9.355pt`、c13 `+8.472pt`、c12 `+7.102pt`、c22 `+5.046pt`。
- 独立block差は c14 `+4.309/−2.169/−0.172pt`、c13 `+2.729/−4.346/+1.319pt`、c12 `−4.378/−3.068/+0.773pt`、c22 `−3.015/−8.071/−1.037pt`。
- 全候補が3 block positiveを満たさず、`independent_reeval_x3_positive_delta_gate_preserve_center`。new centerはP2 c83から不変。

manifest SHAは `e4493d0be285a6bfd663d78b7ca61b993d57ec1858abd158432cc6d303b2cfc4`、generation results SHAは `280aa66bfef236c4fbc312a2f58d4d315ba6bed0f0779c7eee011224164fb8c2`。

## Carmine tempo surface

P1の公開観測値だけで、`turn <= 2`、`supporterPlayed=false`、合法なCarmine PLAYを対象に、+6000（v1）と+12000（v2）を同じ96局/arm screenした。候補・controlは同一24 opponent、両seat、同一seed `49540000` である。

| candidate | result | delta | fault |
|---|---:|---:|---:|
| `cg-p1-carmine-tempo-v1` (+6000) | 19W / 96 vs 15W / 96 | `+4.1667pt` | 0 |
| `cg-p1-carmine-tempo-v2` (+12000) | 18W / 96 vs 24W / 96 | `−6.25pt` | 0 |

弱いv1だけを独立base seed `49550000`、8 games/opponent/seat（384局/arm、合計768局）で確認した。candidate `68W/384` 対 control `71W/384`、差 `−0.78125pt`、全局DONE/fault0、seat ratesはcandidate `0.21875/0.13542`、control `0.16667/0.20313`。screen差は再現せず、v2とv1はいずれも停止した。

screen summary SHAは v1 `71479e60597b9b8c4c83c78aa5f9f81e86a873685b50e6e8b4640ba5aedd31c9`、v2 `e39eb12800de1fbf4d9711561c4f8ad5e40df982774276ef65d49edd98ad335c`、独立確認 `dedde25afdffdecf5a5ca8023830b35d103bc91be1108cb15d05d40ef1cedc07`。

## 実装・検証上の記録

- `run_cg_p1_cem_v1.py` に、parameter span比率で初期探索幅を固定する `--initial-scale-fraction` を追加した。既定値を省略した既存経路は変更していない。
- v2 policy variant adapterを追加し、base runnerへ8/16 games/opponent/seatの独立確認 budgetを追加した。CLIの直接起動、candidate package materialization、通常smokeはPASS。
- 最初のstdin起動はmultiprocessing spawnが `<stdin>` を再importできず失敗したため、診断artifactとして保持し、判定にはCLI完走artifactだけを使用した。
- `fresh・unused・smoke-ready public meta` は引き続き0件。今回のscreen/confirmationは既存poolの再利用metaであり、fresh promotion evidenceではない。
- Kaggle safety verifierは現Python環境に `kaggle-environments` package metadataがなくruntime probeで停止した。これは提出準備の未完了として記録し、CABT性能判定とは分離する。

## 判定

P1＋root deckをBestKnown／Champion／productionとして保持する。Carmine surface、P2 c83 local CEM、P2 c83、c05はいずれもfresh DEV/FINALの昇格条件を満たしていない。次は新しい未使用meta sourceが利用可能になった場合、または別の未評価public surfaceを固定した場合だけ、screen→独立block→fresh DEV/FINALを再開する。既評価候補のblind retry、deck mutation、Champion変更、commit、push、Kaggle提出は行わない。

## 提出closureの再確認

Student用Safety Gateの環境不足を切り分けるため、Python 3.11.15＋`kaggle-environments==1.32.0`の一時venvを用いた。既存の別lane artifact `dist/kaggle/neural-student-v1/submission.tar.gz` は、Safety Gate G1〜G6を全てPASSし、20局で `crashes=0`、`invalid_actions=0`、`timeouts=0`、`external_files_read=[]`、`local_submission_ready=true` となった。ただしこれは `NEURAL_FIXTURE_SMOKE` のRule v0 fallback packageであり、現在のcg P1 policyの性能・提出候補を意味しない。検証JSONは `runs/final-sprint-autonomous/kaggle-safety-gate-neural-student-v1-20260815.json`（archive SHA `7ee7113e20b5a4bbf1f66b191e41c646986a0566877429a33859c9f569428f41`）。

現BestKnownのcg P1 archive `runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/submission.tar.gz` は、cg専用のローカル契約verifierで、sample submission runtime parity、60枚deck、agent import、4局clean-room smokeを全てPASSした。archive SHAは `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02`、policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、deck SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。検証JSONは `runs/final-sprint-autonomous/kaggle-cg-safety-gate-p1-20260815.json`。ただしremote Kaggle Submit verifier/契約はrepoに同梱されず、verifier自身も `submission_ready_candidate=false` としているため、これはローカル提出候補であってKaggle Validation済みではない。外部送信は行っていない。

## fresh-meta exhaustive audit / 8-game clean-room smoke

`opponents/pool_manifest.json` の public・`smoke_ok=true` 70 IDを、`runs/` と `configs/` のJSON/JSONL実artifactへ固定文字列で照合した。70/70が既存artifactへ現れ、未出現IDは0件だった。したがって現poolのfresh・unused・smoke-ready public metaは0件であり、再利用metaによる追加CABTを昇格根拠にしない。

P1 archiveを一時Python 3.11.15＋`kaggle-environments==1.32.0`環境のcg専用verifierで8局へ拡張し、archive shape、sample cg runtime parity、60枚deck、agent import、8/8 DONE、fault 0、illegal 0をPASSした。検証JSONは `runs/final-sprint-autonomous/kaggle-cg-safety-gate-p1-8games-20260815.json`、archive SHAは `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02`。remote Submit verifier/契約はrepoにないため、`submission_ready_candidate=false`、外部送信なしを維持する。

## Supporter 3面 screen — 2026-08-15

前回の未評価Supporter面を、P1＋root deck固定・`performance_first_broad_pool_v1`・同一base seed `49620000`・両seat・24 opponent・candidate/control各96局でscreenした。各ledgerはcandidate/control各96行、pair key＋seed 96組一致、全192局`DONE`、fault 0だった。今回のmetaは既存poolの再利用であり、fresh・unused gateは0件のままである。

| candidate | policy SHA | candidate | control | delta | seat gap | 判定 |
|---|---|---:|---:|---:|---:|---|
| `cg-p1-lillie-early-v1` | `ba056350a203ab90f9ebb89c8cc237a9443ddda11cd590bcb798071040854304` | 15W-81L | 16W-80L | −1.0417pt | 2.0833% / 0% | STOP |
| `cg-p1-boss-ko-v1` | `5fe20b84097c52b7140947e5744dbc69be4fab2610c54cb9bbfbee8ecee5878a` | 20W-76L | 20W-76L | 0pt | 8.3333% / 8.3333% | STOP |
| `cg-p1-carmine-lowhand-v1` | `11915bc8d5e5670744909b8f718fbec2a218fa0f46421702269795e04e26da73` | 16W-80L | 19W-77L | −3.1250pt | 0% / 2.0833% | STOP |

Candidate package manifest/summary SHAは順に、Lillie `8384246ea65597d08b97fc25c4a9438ab2c5539fcef71ade83b58e00e4a82183` / `77a0ae01b3a13660ba510003c040c8b37095e343ee92f9ecb82b7f7e11815b03`、Boss `aa57c73ca70c24480da96fdc2cd0d71962531729b7193a65b2bb3498d861d4aa` / `69312bf6abd3aa85cdae9ddc1f8767adb9c1a23bb51e2e49bb33508a0891f612`、Carmine `48c30e92383d839a445f2917351e90e8b738f7935d9828cc7c15de7c545bde78` / `eb11753341a8d4d1f14bfbba31bd17ed4e13dd01a14b8f8892a07f72dcbea4e3`。唯一の0pt候補もseat gap gate外で、positiveかつseat-safeな候補は0/3だったため、独立確認・CEM update・P3・deck phase・Champion変更・提出は起動しない。
