# Autonomous root-cg Dusk deck arena — 2026-08-14

## 結論

self-owned `cg.api` policyを固定し、Dusk Ball 1102を置換したBloodmoon Ursaluna 135とHilda 1225の2 deck candidateを、Rule v0 controlと同一opponent・seat・seed strataで評価した。Bloodmoonはcommon24で負、Hildaは384では正だったが768で差が+0.9115ptまで縮小した。したがってHildaを含む両candidateはcandidate-only / research-onlyであり、longrun・promotion・submissionへ進めない。現SubmissionEligibleBestKnownはRule v0＋root deck 11/96（11.4583%、fault0）のまま。

## Candidate package / smoke

候補policyはroot deck向けself-owned `cg.api` policyで、policy source SHAは`617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`。候補packageはそれぞれ同じ7-file runtime closure（`main.py`、`deck.csv`、`cg/__init__.py`、`cg/api.py`、`cg/sim.py`、`cg/utils.py`、`cg/libcg.so`）を持ち、clean-room smokeは各2/2 DONE、fault0、illegal_actions0。公式Rule/student verifierとはruntime shapeが異なるため、両manifestの`submission_ready=false`を維持する。

| candidate | deck SHA | package manifest SHA | smoke |
| --- | --- | --- | --- |
| Dusk→Bloodmoon | `ce7e51d84ab02d85a2ddcafcdd4d1d17fec3692d53f0c78444cd048db929706d` | `03543ee03d71a577b3983213f42965d01ec7c8c794d3d1616ac99bf9462a9db3` | 2/2 DONE |
| Dusk→Hilda | `bcae6d8e12ec118da52ad84ac38ea58a3c747e436f3403d8b6425aeda1c2dbc4` | `db273287f2a57e7ca1c8c89ffab649c23fe9439bc394f97ec071164ed540f328` | 2/2 DONE |

## Evaluation

全てworkers=12、candidate/control同一policy/deck identity、同一24 opponent broad pool、両seat、fault-inclusive denominatorで実施した。weighted相当は12 opponent×両seat×2 repetition（48/arm）、common24は24 opponent×両seat×2（96/arm）、384/768は同一strataでrepetitionを増やした。

| stage | Bloodmoon candidate/control | Hilda candidate/control | 判定 |
| --- | --- | --- | --- |
| weighted48 | 5/48 vs 4/48, +2.0833pt | 8/48 vs 6/48, +4.1667pt | 両方 smoke/fault0 |
| common24 96 | 13/96 vs 18/96, **−5.2083pt** | 12/96 vs 10/96, **+2.0833pt** | Bloodmoon停止、Hildaのみ継続 |
| confirmation384 | 未実施 | 66/384 vs 45/384, **+5.4688pt** | Hilda継続 |
| confirmation768 | 未実施 | 109/768 vs 102/768, **+0.9115pt** | 縮小、candidate-only |

Bloodmoonのweighted/common24 summary SHAはそれぞれ`c299645a526de16b46f355497a9d6aa11e791f42788135e47f409b7e5c15ef92`、`c912263826dd3767315a898957b216dea91be7ae5d13008cbabbc77ea3ea7585`。Hildaのweighted/common24/384/768 summary SHAは`5778bec21605e70e4735fa36f2f268ce2308ca36640e8fedd7f4573c3b715019`、`aa2ecc7d1c4a72e84eb7a857eb979f4f5d776a13d22c7354166c44ea60a960aa`、`e5c4fe3bf9ebc0dcba2bc43267a8e11f356f5fe48203cec61b0d97006b572677`、`d00b45c09467a7c9bf6140d76c1eb173c104c29dcafb1646e627e28fd7315a71`。

768は全1536局DONE/fault0、candidate seat0=52/384・seat1=57/384、control seat0=56/384・seat1=46/384でseat collapseなし。Hilda 384のpositiveは768でほぼ消えたため、longrunを開始しない。Bloodmoonのcommon24 negativeも再実行しない。

## Resource / authority / reproducibility

ResourceGovernorの通常方針に従い、weighted/common24はworkers=12/recycle=16、384/768はworkers=12/recycle=64で実行した。authorityは全段階でtraining/promotion/submission/longrun/teacher=false、native opponentはlocal_eval_only、既存production/Champion/root Rule v0 artifactは不変。candidate/controlは同一opponent・seat・seed strataとunique GIDを使用し、faultを勝利へ変換していない。

## Next

同じDusk/Bloodmoon/Hilda surfaceのblind retryはしない。次はself-owned policyを固定した新規novel deck/packageをruntime smoke→weighted48→common24で選別する。common24で明確に再現したcandidateだけ384へ進め、768で差が縮小した場合はcandidate-onlyへ戻す。公式提出packageは別途verifier/runtime contract接続が成立するまで`submission_ready=false`を維持する。
