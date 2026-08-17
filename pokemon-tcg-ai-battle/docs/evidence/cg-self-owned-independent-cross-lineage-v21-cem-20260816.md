# self-owned independent cross-archetype v21／P1 CEM（2026-08-16）

## 結論

v20のTRAIN 2 sourceによる高分散を避けるため、Grass sourceを除外し、Fire／Dark／Lightning／Fighting／Water／Psychicの6件を新seedで生成した。promotion前の強化runtime gate（各source×seat 4局）は48/48 `DONE`・fault0だった。P1固定CEMを2世代、META_TRAIN 4 sourceで実行したが、独立再評価のlower-tail gateを満たす候補はなく、P1 centerと現行BestKnownを保持した。META_DEVはgen1の自動診断で32局読まれたがfault0、META_FINALは未読である。

## source生成とpromotion

- plan: `configs/meta_specialist/self_owned_cg_independent_policy_family_v21_train4_cross_archetype.json`（SHA `0fdae75c697b7d6b75c626c27da55ad50a54c6ae52dee8b667daa9af0d8d2ce4`）。
- root runtime: `runs/final-sprint-autonomous/root-cg-dusk-bloodmoon-package-v2-20260814/package`。immutable root `main.py` SHA `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`、`cg/`同梱。
- generation root: `runs/cg-self-owned-independent-cross-lineage-v21-20260816/`。6 source、authority全false、research-only。factorial manifest SHA `93b12dce149c7fbeb618411313b3f15abc8d65fe7ffaf8b343347a2079f4ecf1`、staged pool SHA `aa4965ed5431550496d9df87efbbd060e633bcebbd6cea4a859bb5a836d148dc`。
- P1 smoke: P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、各source×seat 4局、48/48 `DONE`、fault0、32勝16敗。summary SHA `b5b69ddf310a0647eb77d081896dafc1675f9266d65e3d22ad538827bf1962cc`。
- promotion: promoted pool SHA `8d07d74fb3940e8f4c09f1078084cea0fbda473fcbcc4dc194f591e79b6500cc`、fresh meta SHA `9c47dbdf08cce03abffca1b387a66b0e1d3c73c99351ef9b7c4b4be039c6f5e9`、meta manifest SHA `fd60a17bba01dd50f5fb177f450655663774f549d2bb0228b93c6d1045a93337`。
- split: `META_TRAIN=4 / META_DEV=1 / META_FINAL=1`、SHA `8b9dc51ca0b05aa1bc6ead3b6d1c4b0543110b0e4e2adad7a5494100aa774bba`。split封印時のtraining exposureは0である。

## P1 CEM

実行rootは `runs/cg-self-owned-independent-cross-lineage-v21-20260816/p1-cem/`。campaign seed `2026082121`、population／elite `8／2`、META_TRAIN_ALL、screen各2局／opponent／seat、独立再評価2 block、各2局、positive-delta gate、risk-aware update、evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`である。

generation 0はscreen 144/144、独立再評価96/96をfault0で完走した。c02はscreen delta `+12.5pt`、独立平均 `+15.625pt`だったが、blockは`−6.25 / +37.5pt`でlower-tail gate不通過。c04も独立平均`−6.25pt`だった。選択は`incumbent-center`×2、P1 center保持。results SHAは `bb74a69ac52147994d29f6d13ed093a5c0f261b2d95679f2d27c7ed38d35af20`。

同一campaignをresumeしてgeneration 1まで実行した。screen 144/144、独立再評価96/96をfault0で完走し、独立平均deltaは最良でも`−6.25pt`（c01）、c03は`−12.5pt`だった。全候補がpositive-delta／risk-aware gate不通過で、`incumbent-center`×2を継続した。results SHAは `a4753ea019e0a068cf027ff7fffa9e907ee3fe3b234e82fcb2ee19a9430e3760`、campaign manifest SHAは `27c073990cc2bfc9d48313fc86e1a18f00a08e7b7b6f601f4afedbf844b6c07b`、`champion_changed=false`である。

generation 1のrunnerが自動的にMETA_DEVのincumbent／control診断を行い、32/32 `DONE`・fault0、26勝6敗だった。DEV summary SHAは `ba8beb8f07169124ce205702e484f135d489cbd1df8954a5d9e9b2bdf318b164`。これはcandidate昇格の根拠にはしていない。META_FINAL、deck phase、`cg_bestknown_loop_v1.py`接続は未実施である。

## 判定と次の条件

判定は`SOURCE_GENERATION_PASS / PROMOTION_PASS / RUNTIME_SMOKE_4X_PASS / CEM_FAULT0 / POLICY_CEM_NO_UPDATE / DEV_DIAGNOSTIC_PASS / FINAL_UNREAD / BESTKNOWN_UNCHANGED`。v21 pool、seed、候補は性能使用済みとして同一条件のblind retryを行わない。独立-root policy surfaceの2世代・TRAIN 4 sourceでもstrict lower-tail positiveが得られなかったため、次は同じsurfaceの微調整ではなく、別の明示的 renderer lineageまたはpolicy→deck再結合方式を新seedで生成する。strict independent positive、seat-safe、opponent×seat-safeを満たしたcandidateだけをFINALへ進める。

