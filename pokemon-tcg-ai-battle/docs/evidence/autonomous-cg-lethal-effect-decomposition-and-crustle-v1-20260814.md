# cg-lethal 効果分解と Crustle-aware policy 候補 — 2026-08-14

## 結論

最新 canonical directive の `P1 = cg-lethal-target-v1 + root deck` を基準に、既存 ledger を read-only 集計した。既存 evaluator の ledger は terminal WDL、seat、opponent、repetition、seed、`cabt_turn`、steps だけを保存しており、decision ごとの public state、lethal 条件、実際の action 変更、target HP/damage、resource 状態は保存していない。そのため、lethal の因果効果を state-level に断定できる証拠は現時点でない。今回の最小分解で確定できるのは、paired WDL の opponent/seat 分布と、既存 ledger の観測範囲・欠落範囲である。

Crustle-aware 候補 `cg-crustle-wall-v1` は P0 には正だったが、P1 lethal に対する seed-disjoint 384 では `68W-0D-316L` 対 P1 `74W-1D-309L`（score delta `-1.6927pt`）となった。したがって P1 を更新せず、候補は research-only / candidate-only で停止する。P0 に対する 768 の `+3.3854pt` を根拠に `P2` や longrun を名乗らない。

## 1. 参照した不変 identity

- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- cg P0 source SHA: `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`
- cg-lethal P1 source SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- candidate `cg-crustle-wall-v1` source SHA: `90232bcbad524633bdde619d59beea8f9b0ad1897a5f0d417cade130073cd89f`
- candidate archive SHA: `a4e2b10f19e13ac134f3d505c3c73b1ad6f607997bf5a24a8acf1394b40340b0`
- candidate manifest SHA: `e5edcf74c54a74383739482ff9a15ebea8d00b01eafecec43c8ef5d7c11c9387`

候補は public visible opponent active が Crustle (`345`) のとき、visible non-ex attack IDs `{976,977,978,979,980,981}` に `+24000`、visible ex attack IDs `{982,983}` に `-24000` を加える bounded overlay である。unsupported、malformed、例外、非対象状態は P0 exact fallback とした。root deck、private hand/prize/deck、teacher、native behavior は変更・利用していない。clean-room smoke は `2/2 DONE`, fault0, illegal0 だった。

## 2. P1 lethal の read-only outcome decomposition

既存 P1 ledger は次の三 block を対象にした。各 block は candidate/control の同一 opponent×seat×repetition strata を持ち、全行 `DONE`、fault0 である。

| block | candidate | control | paired score delta |
|---|---:|---:|---:|
| common24 / 96 per arm | 19/96 | 15/96 | +4.1667pt |
| seed-disjoint 384 per arm | 64W-1D/384 | 59W/384 | +1.4323pt |
| seed-disjoint 768 per arm | 161/768 | 106/768 | +7.1615pt |

768 block の paired opponent 分解（win=1, draw=0.5 の候補−control）は、上位が `medal_0001_77a53ffc +10/32`、`itsuki9180_lucario_jp +8/32`、`naoto714_kangaskhan +8/32`、`naoto714_slowking +7/32`、`naoto714_ursaluna +6/32`。負側は `aristophanivan_multiply -2/32`、`rauffauzanrambe_advanced -3/32`、`kojimar_lucario -1/32`、`harukiharada_crustle -1/32` で、その他は同値または中立だった。seat 集計は candidate が seat0 +31/384、seat1 +24/384 で、どちらか一方だけに依存する差ではない。

384 block では上位が `medal_0001_77a53ffc +8/16`、`ferozahmedds_solution +4/16`、`kokinnwakashuu_lucario_search +4/16`。負側は `kiyotah_abomasnow -4/16`、`naoto714_kangaskhan -3/16`、`biohack44_crustlecounter2 -2/16`、`itsuki9180_lucario_jp -2/16`。この block の seat 差は candidate seat0 `+1.5`、seat1 `+4`（score points）である。

`cabt_turn` と `steps` の平均は outcome と同じ terminal row にあるが、lethal 発火や action変更を表すものではない。したがって「lethal が発火した勝ち」を勝因とみなす推論は行わない。

## 3. Crustle-aware candidate の実評価

同一 root deck、同一 public opponent pool、同一 paired strata、authority 全 false で実施した。

| stage | candidate | control | delta | 判定 |
|---|---:|---:|---:|---|
| P0 comparison / 96 per arm | 22/96 | 16/96 | +6.2500pt | screen positive; seat gap のため昇格根拠ではない |
| P0 comparison / 384 per arm | 75/384 | 60/384 | +3.9063pt | P0 に対して positive |
| P0 comparison / 768 per arm | 159/768 | 133/768 | +3.3854pt | P0 に対して positive |
| **P1 lethal comparison / 384 per arm** | **68/384** | **74W-1D/384** | **−1.6927pt** | **P1 未達、STOP** |

P1 比較は `runs/final-sprint-autonomous/cg-crustle-wall-screen-v1-retry-v3-20260814/vs-lethal-384/`、768 total rows、DONE768/fault0、candidate seat0/1 は `38/30`、P1 seat0/1 は `33W+1D/41W` だった。P0 に対する改善は P1 を超えないため、最新 directive の `P1 → P2` 親更新条件を満たさない。1536、longrun、submission、Champion、production default変更は行っていない。

主要 artifact:

- candidate P0-384 ledger SHA: `e324a2f9a22186e38f6fa7c83736d43f087142eb392f5ae4431056d28d5f76f8`
- candidate P0-768 ledger SHA: `7003e0d0650e550e6cea58fb75cf31da0b3ff9d9736756e49133248c1e5f87d6`
- P1 comparison ledger SHA: `df4cd69e2699e8288c19ad2ccdb78a521b20b99268de716b805e35c3f2ceca12`
- P1 comparison summary SHA: `667c1a907e68f036bfe132fbd9e06e0a5af677dcebb1165d5c7421b6c34bbb95`

## 4. 分析限界と次の実行条件

現行 evaluator は action/state trace を保存しないため、directive の A〜D のうち、今回確定できたのは terminal outcome、opponent、seat、seed、pairing、turn/steps の範囲だけである。lethal coverage、実 action change、target HP/damage bucket、energy/resource、変更後 outcome の因果 linkage は `UNMEASURED` として扱う。次の候補を作る前に、P1 の public decision/action telemetry を保存できる最小 wrapper を先に確認する必要がある。大規模な analysis framework、private trace、teacher label、native behavior collection は追加しない。

現時点の research parent は `cg-lethal-target-v1 + root deck` のまま。ただし remote package contract 未確認のため `submission_ready=false`、Champion/SubmissionEligibleBestKnown は不変である。

## 5. standard package closure probe

最新 directive の schema 確認として、既存 P1 archive を fresh root `runs/final-sprint-autonomous/cg-lethal-standard-closure-probe-v1-20260814/package/` へコピーし、repo 標準の `kaggle-agent-package-v1` sidecar を追加した。sidecar 自体は regular file として読み込まれ、`competition_slug=pokemon-tcg-ai-battle`、`entrypoint=main.py`、`private_artifacts_included=false`、`contract.submission_method=UNKNOWN` の shape を通過した。しかし次段の `scripts/build_submission.py::validate_artifact` が inner `manifest.json` の cg schema (`meta-specialist-root-cg-submission-candidate-v1`) を Rule v0 の `artifact_schema_version` として認めず、`ArtifactValidationError: unsupported artifact schema version` で `BLOCKED` になった。

これは standard sidecar の追加で閉じる問題ではなく、repo 標準 verifier が `main.py`、`agents/`、Rule v0 runtime closure を前提にしていることを示す。cg の 7-file runtime closure を Rule v0 manifest に偽装して通すことはしない。cg archive の local verifier は引き続き PASS、repo 標準 verifier は `BLOCKED`、remote contract probe は `AUTH_MISSING`、従って `submission_ready=false` を維持する。

- sidecar SHA: `50753e3c3dcf704eeb658a0c13af36eea0b5f4cf312c70cb06dace75fef19551`
- inner manifest SHA: `ca2d5d8c8d1bd6d30272514a47c94d1b8d0266d51bb862dc1001c3a2e925a875`
- probe command: `PYTHONPATH=.:src .venv/bin/python scripts/verify_kaggle_submission.py --artifact runs/final-sprint-autonomous/cg-lethal-standard-closure-probe-v1-20260814/package --stress-games 1`
