# cg P1 observed-failure public-state screen（2026-08-14）

## 結論

P1 `cg-lethal-target-v1 + root deck`をcontrolに固定し、公開 decision telemetryから高重み負け相手を狙う bounded overlayを評価した。`cg-p1-ursaluna-pressure-v1` は96局で一時的に+10.4167ptだったが、seed-disjoint 384確認では **−2.9948pt**（candidate 72W-1D-311L 対 control 84W-0D-300L）へ反転した。全768局はDONE/fault0で、candidate-only/STOP。768、longrun、training、teacher、promotion、submissionは起動していない。

## 観測根拠

P1公開telemetry root `runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260814-v1` はbroad24、両seat、96/96 DONE/fault0、4,077 decision rows、96 redacted deck-registration rows、private/opaque key scan 0件だった。P1 384 population-bound ledgerで高重み負けが目立った `kiyotah_abomasnow`（visible active IDs 721/722/723）と `naoto714_ursaluna`（65/135/1073/1074）を、相手の公開 active IDだけで識別する仮説に限定した。hand/prize/deck_count、opponent policy identity、teacher/native actionは候補入力に使っていない。

## 候補と結果

| candidate | public change | 96 candidate | 96 P1 control | 96 delta | 384 candidate | 384 P1 control | 384 delta | 判定 |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `cg-p1-heavy-active-attack-v1` | visible `maxHp >= 300` の非致死 ATTACK +12000 | 14/96 | 19/96 | −5.2083pt | — | — | — | STOP |
| `cg-p1-very-heavy-active-attack-v1` | visible `maxHp >= 350` の非致死 ATTACK +12000 | 18/96 | 25/96 | −7.2917pt | — | — | — | STOP |
| `cg-p1-heavy-active-conserve-v1` | 同条件の非致死 ATTACK −12000（対立仮説） | 17/96 | 18/96 | −1.0417pt | — | — | — | STOP |
| `cg-p1-abomasnow-pressure-v1` | active ID ∈ {721,722,723} の非致死 ATTACK +12000 | 15/96 | 22/96 | −7.2917pt | — | — | — | STOP |
| `cg-p1-ursaluna-pressure-v1` | active ID ∈ {65,135,1073,1074} の非致死 ATTACK +12000 | 22/96 | 12/96 | **+10.4167pt** | 72W-1D/384 | 84W/384 | **−2.9948pt** | candidate-only/STOP |

96局は各candidate/control 96局、同一24 opponent×seat×repetition×seed strata、workers=12/recycle=16、全192局DONE/fault0。Ursaluna 384確認は各arm 384局、24 opponent×両seat×rep8、workers=12/recycle=64、全768局DONE/fault0、GID 768 unique、candidate/control paired key equal、各seat 192局、各arm opponent 24件である。candidateは72W-1D-311L、controlは84W-0D-300L、score差は−2.9947917pt。candidate seat0=40W-1D-151L、seat1=32W-160L、control seat0=39W-153L、seat1=45W-147L。

## 実装・境界

- module `src/mage_ptcg/meta_specialist/cg_p1_observed_failure_v1.py` SHA `e973d05c5f598e6467b0a5157fe35598fc5bf2ea620305c1a788cddcb1a78940`
- 96 runner `scripts/run_cg_p1_observed_failure_screen_v1.py` SHA `9ce437b326295e97d6e4b2e1b8632fa79b6518988a475e2e4a041f0b3588e7cf`
- 384 runner `scripts/run_cg_p1_observed_failure_confirmation_v1.py` SHA `4a9806031f415a6320df538ab115f16aebe88e5b0f9801d1282c94027be7ccd6`
- module tests SHA `5318c1c054b08c756eaa29e8f922dbb6d3e053837a954218233f6b0f300fa25b`
- confirmation tests SHA `c952371ba348a896160184ea056ad24025b04fe0c30af974bf15db4783a4b1bd`
- immutable P1 base source SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

96 screen summary SHAはheavy `49bbc998be6347c0f42d8d48897a69af4b512907b37d447dccdd29bb249aa585`、very-heavy `8b552ce5483afc469a50dc349c3bc27e55658f013e7d8c093ab4aa87f74b3a25`、conserve `031bc5a9386690fd34b33fe1d009a3868cf7a15f49d1641204b427320c1ef322`、Abomasnow `4633a488f45bec6e4c407167c97b0720c41929a77ac0cf728532204ab631df5a`、Ursaluna `22ac92c8aad7fe4a31a07f39bfd54e8dabc99b53c5d34eacf40495e56619d1dd`。384 confirmation summary SHAは `4aac32a8d3b4779869e34667c64aa47a3406ebcbdf2af2ce430914367a592c37`、manifest-complete SHAは `8cad2dbbba4ba6f03352b3ad4b3497390b933be76ae63c220902a2b6064454fa`。

authorityはtraining/teacher/promotion/submission/longrun全false、native/local_eval_only poolを教師として利用していない。P1 parent、Champion、SubmissionEligibleBestKnown、production `main.py`、root deckは不変。remote contract確認やKaggle提出は行っていない。

## 判定と次の境界

公開 active IDに基づく一時的な96局改善は384で再現しなかったため、候補をChampion/BestKnown/longrunへ昇格しない。同じ5候補と同じseedのblind retryもしない。P1 controlでの次のscreenは、別の観測事実と bounded change が揃う場合だけ再開する。現時点で実験プロセスはない。
