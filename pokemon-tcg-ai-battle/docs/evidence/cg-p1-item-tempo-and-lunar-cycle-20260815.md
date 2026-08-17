# P1 item-tempo / Lunar Cycle policy surfaces — 2026-08-15

## 結論

P1 `cg-lethal-target-v1` と root deck を固定し、未評価だった actor-visible policy surface を実CABTで検証した。v4 item-tempo 3面と v5 Lunar Cycle 3面は全て `DONE`・fault 0・candidate/control paired strata一致だったが、独立seedまたは holdoutで再現する BestKnown更新候補は得られなかった。P1、root deck、BestKnown、Champion、production、submissionは不変である。

今回のscreenでは、Gravity と Switch に一時的な正差が出た。Gravityは独立384局で `−1.0417pt` に反転し、Switchは独立384局で `+6.5104pt` だったが、予約holdout v1（他laneで既に使用済みのmeta）では `0.0000pt` だった。したがってSwitchを転移・昇格候補とは扱わない。Lunar Cycle lowhand4もscreen `+5.2083pt` から独立384局 `−0.1302pt` へ戻った。

## 不変 identity

- P1 policy/source SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- v4 module: `src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v4.py`
- v5 module: `src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v5.py`
- 全screenは workers=12、worker recycle=16（独立384はrecycle=64）、両seat、candidate/control同一pair key＋seed strata、authority falseで実行した。

## v4 item-tempo screen

候補はP1 sourceからhash-boundにmaterializeし、公開情報だけを参照した。

| candidate | 条件 | screen 96局/arm | delta | 独立/holdout | 判定 |
|---|---|---:|---:|---:|---|
| `cg-p1-gravity-stage2-lethal-v1` | visible opponent activeの`preEvolution`が2以上、Gravityの+30でlegal attackが到達 | 18W-0D-78L vs 14W-0D-82L | +4.1667pt | 独立384: 74W vs 78W, **−1.0417pt** | STOP |
| `cg-p1-premium-power-lethal-v1` | Premium Powerの+30でlegal attackがvisible HPに到達 | 18W-1D-77L vs 19W-0D-77L | −0.5208pt | 未実施 | STOP |
| `cg-p1-switch-powered-bench-v1` | damaged activeかつattached energy 2以上のbenchが存在 | 20W-0D-76L vs 15W-0D-81L | +5.2083pt | 独立384: 89W vs 64W, **+6.5104pt**。holdout v1: 24W vs 24W, **0pt** | candidate-only / STOP |

v4 screen artifact root:
`runs/final-sprint-autonomous/cg-p1-item-tempo-surface-screen-20260815/`

各armのcandidate/controlはscreen 96局ずつ、独立確認は384局ずつで、全ledgerがfault 0、paired strata 96または384だった。Switch holdout v1も192局全てDONE/fault0だったが、`configs/meta_specialist/cg_unused_meta_holdout_v1.json`の24 IDは過去run artifactに出現しており、fresh-unused metaとは呼ばない。

## v5 Lunar Cycle screen

Lunatone（ID 675）の `Lunar Cycle`を、Solrock（ID 676）がvisibleで actor hand countが閾値以下のときだけbounded bonusした。これは過去のRule v0 generic ABILITY screenとは別のP1-specific overlayである。

| candidate | bonus / hand condition | screen 96局/arm | delta | 独立384 |
|---|---|---:|---:|---:|
| `cg-p1-lunar-cycle-lowhand3-v1` | +12000 / hand≤3 | 16W vs 20W | −4.1667pt | 未実施 |
| `cg-p1-lunar-cycle-lowhand4-v1` | +8000 / hand≤4 | 19W vs 14W | +5.2083pt | 70W vs 70W+1D, **−0.1302pt** |
| `cg-p1-lunar-cycle-lowhand5-v1` | +6000 / hand≤5 | 20W vs 22W | −2.0833pt | 未実施 |

v5 screen artifact root:
`runs/final-sprint-autonomous/cg-p1-lunar-cycle-surface-screen-20260815/`

## 判定

- fresh・unused・smoke-ready public metaは依然0件。pool全体の実run artifact監査でも、予約holdout v1のIDを含めて未使用sourceは確認できなかった。
- positive screenだけで独立確認を省略せず、Gravity、Switch、lowhand4を独立seedで確認した。
- Switchは広域24面では2 block positiveだが、別holdoutで0ptのため、未使用metaでの再現性証拠がない。
- lowhand4は独立384で中立、Gravityは独立384で負差。Premium、lowhand3、lowhand5はscreen段階で停止した。
- P2/P3昇格、deck mutation、CEM update、Champion/production変更、longrun、training、Kaggle提出は行わない。

## 検証

```text
TMPDIR=/tmp PYTHONPATH=src pytest v4 tests: 5 passed
TMPDIR=/tmp PYTHONPATH=src pytest v5 tests: 5 passed
py_compile v4/v5 source, adapters, tests: PASS
all v4/v5 evaluation blocks: DONE, faults=0
candidate/control paired strata: PASS
```

次の再開条件は、真に未評価のmeta sourceが固定できること、または新しいactor-visible policy surfaceを事前登録して、screen→独立複数block→fresh DEV/FINALの順で再現差を確認できることである。同じSwitch、Gravity、Lunar Cycle候補のblind retryは行わない。
