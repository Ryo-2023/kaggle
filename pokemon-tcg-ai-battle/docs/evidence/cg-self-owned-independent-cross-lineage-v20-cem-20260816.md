# self-owned independent cross-lineage v20／P1 CEM（2026-08-16）

## 結論

公式カードCSVから新しいseed namespaceで4件の self-owned deck × independent-root policy sourceを生成し、promotion前のP1対pool smokeを通過させた。P1固定CEMを2世代実行したが、独立再評価のlower-tail／risk-aware gateを満たす候補はなく、P1 centerと現行BestKnownを保持した。generation 1の自動DEV診断ではGrass sourceに2件の`STEP_LIMIT`が出たため、DEVも完全未使用ではなくなった。META_FINAL、deck phase、`cg_bestknown_loop_v1.py`接続、BestKnown／Champion／production／submission変更は行っていない。

## source生成と契約

- plan: `configs/meta_specialist/self_owned_cg_independent_policy_family_v20_fresh_cross_lineage.json`（SHA `74efdd113c313b9bf08cc82c340970a8bcf5dbf12d127ab94556caaa7f3c8791`）。
- card database: `data/raw/EN_Card_Data.csv`。deck recipeはFire、Dark、Lightning、Grassの4件で、各seed／canonical deck／policy SHAはfactorial manifestに固定した。
- root runtimeは `root-cg-dusk-bloodmoon-package-v2-20260814/package` を使用した。先に指定したv1 pathは`main.py` SHAは一致したが`cg/`が欠けていたため、生成器が`BLOCKED`で停止し、artifactを作らなかった。採用runtimeの`main.py` SHAは `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`。
- generation root: `runs/cg-self-owned-independent-cross-lineage-v20-20260816/`。4 source、authority全false、research-only。factorial manifest SHA `c576f98845bb252b9fd8ddc708d59ab5df4bf9fb0582794a534269d6e414ad0e`、staged pool SHA `8e857acc7d133fca8837452b606b1605a9d57fd470ce2ce4d0efc3ca4cf6b334`。

## runtime smoke／promotion

P1 package（policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`）を4 source、両seat、各2局で評価した。16/16が`DONE`、fault 0、12勝4敗で、summary SHAは `0848fae698b35f73a110f5498fbcc94d2a77aca19535fc63ceca992df25c03d4`。

`seal_self_owned_cg_meta_source_v1.py --execute --promote --batch`でlocal-eval-onlyへpromotionした。promoted pool SHAは `24e081f98eac76ed0ff33795e2b2d32f896e1aab57adf111c8d3a24dcd2aa3df`、fresh meta SHAは `82f1a3b84c028266126b009e8024c511791d60f25f86d1bf35a93327d96c8d68`、meta manifest SHAは `c4f9b93b604410cf7a39b7b2831b0b994b4db4f85ea2c2f2844029366b9f43fe`。splitは`META_TRAIN=2 / META_DEV=1 / META_FINAL=1`、SHA `bcc3571028651a4e5c859df6f06e032819e39a1af25ae70637a20b8269a47982`である。split作成時点のTRAIN／DEV／FINAL exposureは0だった。

## P1 CEM

実行rootは `runs/cg-self-owned-independent-cross-lineage-v20-20260816/p1-cem/`。campaign seed `2026081621`、population／elite `8／2`、META_TRAIN_ALL、screen各2局／opponent／seat、独立再評価2 block、各2局、positive-delta gate、risk-aware updateである。evaluator SHAは `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`。

generation 0はscreen 72/72、独立再評価48/48をfault 0で完走した。screen上位c05はdelta `+25.0pt`だったが独立blockは`0.0 / 0.0pt`、c07は平均`+12.5pt`でもblockは`−12.5 / +37.5pt`で、risk-aware lower-tail gateを満たさなかった。選択は`incumbent-center`×2、P1 center保持。results SHAは `088aa684a7a919c20cfb1f71852365252c921fd174d0739b54cf2a33d8cbcd64`。

同一campaignをresumeしてgeneration 1まで進めた。screen 72/72、独立再評価48/48はfault 0だったが、c00の独立平均deltaは`−6.25pt`、c07は`−12.5pt`で、再び`incumbent-center`×2となった。generation 1 results SHAは `6ee6171336a66adea9a0b9a770989412a3617ba932059c975fa0c383fea3035d`、campaign manifest SHAは `37e339e770d5d0587e6c5be5ab3d7870a4d1a499d400eed6a44f918c412196b7`。`champion_changed=false`である。

generation 1のrunnerは奇数世代の自動診断としてMETA_DEVのGrass sourceをincumbent／controlへ読み込んだ。32局中30局`DONE`、2局`STEP_LIMIT`、fault率6.25%（DEV summary SHA `001d8585f1f66d6f43ab23328b63ff65cd1cce55059e16bdcd1903f65deb45a1`）だった。これはcandidate選抜やBestKnown更新には使っていないが、META_DEVは以後「完全未使用」とは表記しない。META_FINALは未読である。

## 判定と次の条件

判定は`SOURCE_GENERATION_PASS / PROMOTION_PASS / RUNTIME_SMOKE_PASS / CEM_FAULT0_TRAIN / POLICY_CEM_NO_UPDATE / DEV_DIAGNOSTIC_FAULT / BESTKNOWN_UNCHANGED`。v20 pool、seed、候補は性能使用済みとして同一条件のblind retryを行わない。Grass sourceはruntime安全性の再確認が必要であり、次epochではTRAIN source数とruntime gateを増やした別recipeを生成する。候補がstrict independent positive、seat-safe、opponent×seat-safeを満たすまでDEV／FINALの評価結果を昇格根拠にしない。

