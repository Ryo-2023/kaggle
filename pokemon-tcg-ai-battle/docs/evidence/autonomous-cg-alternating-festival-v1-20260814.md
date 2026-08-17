# cg alternating Festival Grounds interaction — 2026-08-14

## 結論

`Gravity Mountain (1252) → Festival Grounds (1245)` を、cg P0 policy と lethal-target policy の交互最適化契約で評価した。384局ではデッキ固定で +1.0417pt、lethal policy固定で +4.5573ptだったが、768局のデッキ固定確認は 114/768 対 control 120/768（−0.78125pt）へ反転した。したがって本候補は candidate-only / STOP とし、1536局、longrun、promotion、submissionへ進めない。

これは root deck の機械的昇格ではない。root deck、production `main.py`、Champion、提出 package は不変で、全 authority flag は false である。

## 提出契約の境界

cg P0 package は sample submission と engine README に対する local verifier では archive shape、60枚 deck、`agent`、sample `cg` runtime parity、clean-room smoke を通過している。report SHA は `86b8371a97b7bd5c0d1a7fc46867f1852e2b8fe2d35a072ecbf8b2df0175e39a`、P0 archive SHA は `278438be73b73d1be385810530dadf6d3679711cd218b78b9847c48d15ca1bb5` である。

一方、repo の標準 `scripts/verify_kaggle_submission.py` を cg 7-file package に直接適用すると `ValueError: kaggle package manifest must be a regular file` で `BLOCKED` となる。また `scripts/probe_kaggle_contract.py --competition pokemon-tcg-ai-battle` は `AUTH_MISSING`（archive type、submission method、competition access、rules acceptance は UNKNOWN）を返した。よって状態は `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED`、`submission_ready=false` のままであり、Kaggle送信は行っていない。

## 固定した identities

- root control package: `runs/final-sprint-autonomous/root-cg-submission-candidate-v1-20260814/package`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- cg P0 policy/source SHA: `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`
- Festival deck SHA: `d034887232321f6466b69c4b5c23580d05b4e169539582df60634be20f980f2e`
- Festival P0 package archive SHA: `07bdf39ebd0a9f2e6f31de1135abfba4fb6035bdb8289f6a46d5a7aac74bdea5`
- Festival lethal policy/source SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- Festival lethal package archive SHA: `3823b96979dec694f3049b55decbaea74d8396d6c160579e47176dce3a5b8a17`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- alternating runtime module SHA: `03d0fe298745755478b2f837b52cdebf07988d4f8c43232fda295ec26815276b`
- alternating runtime CLI SHA: `6d9b0ece162a0f5fa3eb8503842699d048985ee7482828a0f29bc07bbdb1213c`

仮説は、Gravity Mountain を1枚減らして Festival Grounds に置換することで、Crustle / tool / stadium 系の hard-negative matchup で stadium interaction を改善できるかを、P0 policy と lethal policy を分離して測ることだった。policy/deck source は package builder の `source_agent` 契約で別々に hash-bind した。

## 実測

全ステージは同一 broad opponent pool、両 seat、paired opponent×seat×repetition×seed strataで実施した。通常 stage は workers=12、96局は recycle=16、384/768局は recycle=64、全局 `DONE`、fault=0、authority=false である。

| stage | phase | candidate | control | delta | 判定 |
|---|---|---:|---:|---:|---|
| 96 | `POLICY_FIXED_SHORT` | 18/96 | 17/96 | +1.0417pt | `NOT_PROMOTABLE`（control seat gap 6.25%） |
| 384 | `POLICY_FIXED_SHORT` | 63/384 | 59/384 | +1.0417pt | `POSITIVE_CONTINUE` |
| 384 | `DECK_FIXED_LONG` | 82/384 | 64W-1D/384 | +4.5573pt | `POSITIVE_CONTINUE` |
| 768 | `POLICY_FIXED_SHORT` | 114/768 | 120/768 | −0.78125pt | `NOT_PROMOTABLE` / STOP |

96局の iteration SHA は `cfc33c1aff05919a297de7b80e0daf64d2947130c39c4e2e2b020ee267907c09`、384局は `7aa4672944a73e116625411346674ac2bd8437a43f188f35ba9b3e05049cffd1`、768局は `4993dc8b1614da7f081cbe3c210ba3bc756b520283b9dcee17e073a0199ec0e0` である。

stage manifest / summary:

- 96 deck phase manifest `3a713714c79fb22ae8390f1ad0007fd8ee4241182c2cdb602973c81b20df9db4`, summary `3c2e0f31973654414e3177839380c6dd55a5c093287b1c41a709f3902e904f0d`
- 384 deck phase manifest `902863b5233a1da78563d62e193f5fe54dceac1a6aecd3fac818899c5abd15f1`, summary `6475153307974f6843b7f16cd2a64384ff4a541d890533314e2c5e4072ba8a4e`
- 384 policy phase manifest `675c2d0744e846ccd073fc6847b3e518706d3ffef0a968a6929e1ed78e1d3d55`, summary `23806801071513b765079a1b49bd926ecb0b10c4309066babe96910eb1fe0c20`
- 768 deck phase manifest `b0f92aece1d244299cc46097c04d563afd2e6dc180482310b94ae8c1f9dfb426`, summary `7edf156aa8c001277b0075311ebed2600cb3f32275b8b556a2fc8d262a52318f`

全ステージの output root は `runs/final-sprint-autonomous/cg-alternating-festival-v1-20260814/` 配下に保存し、strict reloadで package identity、phase、stage、fault、authorityを再確認した。現在 active process はない。

## 判定と次の境界

384局の一時的な正差は768局で再現しなかったため、Festival deckは BestKnown、SubmissionEligible、Champion のいずれにも昇格しない。lethal policyとの相互作用も384局の研究信号に留め、longrunへ外挿しない。次に実行する場合は、remote contract確認または明示的に異なる public policy hypothesis が必要であり、同候補の blind retry は行わない。

## 検証

- local cg contract verifier: PASS（report上記）
- repo標準 Kaggle verifier: `BLOCKED`（cg packageに regular `kaggle-package-manifest.json` がない）
- Kaggle contract probe: `AUTH_MISSING`
- package smoke: P0 / lethal 各 2/2 `DONE`, fault0, illegal0
- 交互 runtime: 96/384/768 の実行局は全て `DONE`, fault0
- docs validator: 13 canonical documents
- `git diff --check`: PASS

本記録は research-only の性能・提出契約境界の証跡であり、公式提出成功や remote package compatibility を主張しない。
