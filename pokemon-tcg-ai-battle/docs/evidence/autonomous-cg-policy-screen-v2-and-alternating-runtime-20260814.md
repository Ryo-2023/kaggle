# Autonomous cg policy screen v2 と alternating runtime — 2026-08-14

## 結論

root deck（SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）を固定し、既存の lethal-target / retreat-damage surface を再実行せず、cg policy の新規 public MAIN surface を2件だけ評価した。両候補は package closure と clean-room contract を通過したが、同一 cg P0 control との weighted48 で負差となったため、common24 / 384 / 768 へ進めず candidate-only とした。

## 新規 policy surface

| candidate | 仮説 | candidate | cg P0 control | delta | 判定 |
|---|---|---:|---:|---:|---|
| `cg-attach-threshold-v1` | Mega Lucario が energy 1 のとき Fighting attach を選び、attack threshold 到達を優先 | 17/96 | 19/96 | −2.0833pt | STOP |
| `cg-overkill-conservation-v1` | visible active の lethal overkill が大きい attack に bounded penalty を適用 | 12/96 | 21/96 | −9.3750pt | STOP |

各 screen は broad24、両seat、同一 evaluator/identity 契約、workers=12、recycle=16、192局（candidate/control 各96）で実行した。全192局ずつ `DONE`、fault 0、draw 0、両seatの記録あり。既存の lethal/retreat screen および deck surface は再実行していない。

## package contract

両候補は7-file cg runtime closure、60枚 deck、sample `cg` runtime parity、4局 clean-room smokeを PASSした。

- attach archive SHA: `21ad35e1f2715d338d75a24e9113f1c4b4fc3b367116a2876a36d3066559cc01`
- overkill archive SHA: `c89a2f8a5a6dad285fad39fed79e3b429d9a57bf537de13379c3bbd897e07b2d`
- attach verifier report SHA: `d3e7312a89ea6e712d07b8ef56b838f2897a606ae7f6c36460f19f0b2ad726d6`
- overkill verifier report SHA: `b925d5e6aa4537402b1fcea20a77359509eedb4bc2c7b5b9b485c5825a8392f5`

repo内に公式リモート Submit verifier と archive contract はないため、分類は `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED`、`submission_ready=false` のままである。Kaggleへの送信は行っていない。

## cg deck↔policy alternating runtime

native candidate factory専用だった既存 outcome-only runtimeとは別に、package identityを直接束縛する research-only adapterを追加した。

- module: `src/mage_ptcg/meta_specialist/cg_alternating_runtime_v1.py` SHA `03d0fe298745755478b2f837b52cdebf07988d4f8c43232fda295ec26815276b`
- CLI: `scripts/run_cg_alternating_runtime_v1.py` SHA `6d9b0ece162a0f5fa3eb8503842699d048985ee7482828a0f29bc07bbdb1213c`
- tests: `tests/meta_specialist/test_cg_alternating_runtime_v1.py` SHA `a716c40ccfc73728f256ba11e28f1dfc035d1b216dbe41e44760fe740208ec55`、`tests/meta_specialist/test_run_cg_alternating_runtime_v1.py` SHA `bc86ceb12ec50d40d8394feb52085800e75001c7e3beb88508815d18555300cf`

契約は次の通り。

- `POLICY_FIXED_SHORT`: candidate/control の policy SHAを一致させ、deck SHAだけを変える。
- `DECK_FIXED_LONG`: deck SHAを一致させ、policy SHAだけを変える。
- stageは `96 → 384 → 768 → 1536`。positive、fault0、両seat support、seat gap ≤5pt のときだけ次 stageを記録する。
- candidate/controlは同じ opponent×seat×repetition×seed strataを共有し、pair keyを再検証する。
- workers=12、96はrecycle16、384以上はrecycle64。authorityは全falseで、無限loop・training・promotion・submissionは起動しない。

実 package（Hilda deckを deck phase、attach policyを frozen Hilda deckへ接続）で dry-run を作成し、evaluatorを起動せずに strict reload を通過した。

- iteration SHA: `75b9a932a4e709505702154f5b357e5f217da3225329f1dd8ad1cb08ef41d3ca`
- dry-run stage manifest SHA: `35fbe6df150bc2c7d7ce0320cb9e3d5a386abe8be2328425cf8ca779cae5d23a`
- status: `DRY_RUN`、stage `POLICY_FIXED_SHORT`、96 games、workers12、evaluation未起動

この adapter は長時間学習を自動許可するものではなく、次の新規 policy/deck candidateが確定した場合にのみ、同一固定契約でbounded evaluationへ渡すための実行可能な骨格である。

## authority / next gate

既存の cg lethal candidate は768で +7.1615ptを示すが、384では +1.4323ptへ縮小している。新規 v2 surfaceは両方負差だったため、現在は candidate-only。VerifiedSubmissionEligibleBestKnown は Rule v0＋root deck（11/96、fault0）のまま。remote contractの人手確認がない限り提出を行わず、既評価 surfaceの blind retryも行わない。

