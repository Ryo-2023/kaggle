# cg submission closure と policy-first screen — 2026-08-14

## 結論

cg の self-owned policy + root deck は、リポジトリ同梱の sample submission 契約に対して local contract PASS になった。archive の安全な形、60 枚 deck、`agent` entrypoint、`cg` runtime の sample parity、clean-room CABT smoke を確認した。ただし Kaggle の remote Submit verifier と提出 API はこの repository に同梱されていないため、分類は `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED` とし、`SUBMISSION_READY_CANDIDATE` や提出完了とは扱わない。

policy-first の候補では、lethal target +120 のみが cg P0 control に対して common24 と seed-disjoint confirmation の両方で正だった。retreat damage は common24 で差がなく停止した。lethal target の 768/arm confirmation は candidate 161W/768、control 106W/768、+7.1615pt、全1536局 `DONE`/fault0 だったが、これは研究用 policy screen の結果であり、Rule v0 Champion の変更・submission・longrun の許可を意味しない。

## 1. local contract verification

- sample contract source: `data/raw/sample_submission/sample_submission/`
- engine reference: `data/raw/ptcg_engine/ptcgProgram 22/README.md`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- Rule closure SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- local verifier: `scripts/verify_root_cg_submission_candidate_v1.py` SHA `18904f2789cab2859a36a9f32d1f63252c0d58f2386117d9d44208f8009be637`
- report: `runs/final-sprint-autonomous/root-cg-contract-verification-v1-20260814/report.json` SHA `86b8371a97b7bd5c0d1a7fc46867f1852e2b8fe2d35a072ecbf8b2df0175e39a`

P0 package の archive SHA は `278438be73b73d1be385810530dadf6d3679711cd218b78b9847c48d15ca1bb5`。新しい policy variants は同じ 7 core members（`main.py`, `deck.csv`, `cg/__init__.py`, `cg/api.py`, `cg/sim.py`, `cg/utils.py`, `cg/libcg.so`）を保持し、sample の `cg` runtime SHA と完全一致する。

retry-safe4 package の検証は各 4 games、両 seat を含み、両候補とも `DONE=4`, `faults=0`, `illegal_actions=0`, archive shape PASS, deck 60, `cg_runtime_parity=PASS` だった。

| candidate | policy source SHA | archive SHA | contract report SHA |
|---|---|---|---|
| `cg-lethal-target-v1` | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` | `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02` | `e563f523575efb2e5f681267b4a8d070b051bae0be9f2f3fb7d5b80d420f4fd0` |
| `cg-retreat-damage-v1` | `6f41c58dc65969fb5a2597c6d7fb37520df77e920210ad2675739396199559c0` | `0b42d8d8df16ded441400a6105f05c9bd73f42886a62cb3154ed14609f3cd6c5` | `6a2dc681f0fddf0c9bbfbaf64cd21b180ed81df98f7feefee0b7c9eec79e3278` |

remote verifier、正式 archive schema、host Python/ABI/dependency/timeout の最終値は remote 側で確認する必要がある。local report の `submission_ready_candidate=false` はこの未確認境界を反映する。

## 2. packaged P0 parity screen

既存 self-owned cg P0 package と Rule v0 control を、同じ root deck と broad common24 に投入した fresh screen は次の通りだった。

- root: `runs/final-sprint-autonomous/root-cg-packaged-common24-contract-v1-20260814/`
- candidate: 17W/0D/79L = 17.7083%
- Rule control: 9W/0D/87L = 9.3750%
- delta: +8.3333pt
- 192/192 `DONE`, fault0, evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- summary SHA `2b7f63e14aa5a20d33d45e705b84f92d0a05bb1ae1c4b6c8aaa419735c4c90dc`
- manifest-complete SHA `d2cbabb202a205811d41789dec29b06797c7f4af28e2d9d5e05797f5bdd81f34`

これは package と同一 policy/deck の parity を確認する local research result である。

## 3. policy hypothesis and gates

immutable root deck 上で最大 2 surface を screen した。

- `cg-lethal-target-v1`: public visible active の `hp <= attack damage` の ATTACK に +12000。狙いは lethal を確実に優先すること。private hand/prize/deck、teacher label、future RNG は使わない。
- `cg-retreat-damage-v1`: visible active damage >=100 かつ bench に energy>=2 がある RETREAT に +12000。狙いは damaged active の交換を促すこと。

追加 source の最後の callable が Kaggle loader の entrypoint に誤選択される問題を clean-room で再現したため、variant 末尾に明示的 `agent()` wrapper を置いた。未型 `Struct`、`obs.select` 欠落、base score 例外は score 化せず 0/fallback とする。初回 retry roots (`cg-policy-screen-v1-20260814`, `retry-safe`, `retry-safe2`, `retry-safe3`) は smoke fault の diagnostic/INVALID として保全し、性能値には算入しない。

## 4. fresh policy screens

全 screen は cg policy candidate 対 cg P0 control、同一 broad 24 IDs、同一 seat/repetition/seed strata、workers=12、authority 全 false で実行した。

| stage | candidate | control | delta | 判定 |
|---|---:|---:|---:|---|
| common24 lethal, 192 games | 19W/0D/77L (19.7917%) | 15W/0D/81L (15.6250%) | +4.1667pt | 384へ進行 |
| common24 retreat, 192 games | 16W/0D/80L (16.6667%) | 16W/0D/80L (16.6667%) | 0.0000pt | STOP |
| lethal confirmation384, 768 games | 64W/1D/319L (16.7969%) | 59W/0D/325L (15.3646%) | +1.4323pt | 768へ進行 |
| lethal confirmation768, 1536 games | 161W/0D/607L (20.9635%) | 106W/0D/662L (13.8021%) | +7.1615pt | candidate-only; longrun/submitなし |

Artifacts:

- materialization root: `runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/materialization.json` SHA `449560a532c89bb144446d5b312deaa7431c7958b79a11cd2ad04e31084ac208`
- lethal common24 summary SHA `bad85f9b8ac359ba447c5e9b35ab83c86ecdd34f13d8d6483e4ef97b30c1801a`
- retreat common24 summary SHA `5a76af61ef69f79dc68e73ed08434d4bd4f2f7a8398f48f423f0c883719c2c97`
- lethal 384 summary SHA `379365219aed71332a7048495d74a6011f825b049ff71a808aeecabd9deb7707`
- lethal 768 summary SHA `d613e70f04c2b476ed2a9582c3fbd91136f0993d7603dc55b8901e953363f537`
- lethal 768 manifest-complete SHA `2ad0f3be495d325e2d3db35c63ed3757abf9253189d1acda0d56e69bad134974`

The 768 run used seed-disjoint `base_seed=40240000`, 16 games per opponent/seat, `recycle=64`, and completed 1536/1536 with fault0. Runtime total was 564.165431 seconds (~2.72 games/s aggregate); this run is intentionally not a longrun or promotion claim.

## 5. authority and next action

All artifacts are `research_only`; training, behavior/teacher labels, promotion, submission, and longrun authority are false. No Kaggle API or Submit UI was called. Rule v0 Champion and production `main.py`/`deck.csv` were not changed.

The current research candidate is `cg-lethal-target-v1` + root deck, but it remains `LOCAL_CONTRACT_PASS / REMOTE_CONFIRMATION_REQUIRED`. Before any external submission, a human must confirm the official remote archive contract and verifier. Further policy search should use a genuinely new bounded public surface on the immutable root deck; retreat and the failed/INVALID variants are not to be retried. Deck mutation remains paused until cg policy/contract closure is resolved.

Source SHAs after this lane: `src/mage_ptcg/meta_specialist/cg_policy_candidate_v1.py` `fa964ce17f89f8829c1be2ebd0dcf524568707d14a60c61da3a8d2165a0bb09c`, `scripts/run_root_cg_policy_screen_v1.py` `8e4e042ad0a49b6f1203f347723afa9a40b483c03aef5ab9da48c7b634112b4a`, `tests/test_cg_policy_candidate_v1.py` `d1b250f23e0bbe38c7fbfdc19bafee095475b080a606212a3443c3f48dd5d80e`, and focused tests `7 passed` (contract verifier included).
