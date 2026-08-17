# ChatGPT引き渡し用 current context pack — cg BestKnown loop（2026-08-16）

## 最上段追補（2026-08-16、portfolio source factory v22-v24／strict gate fail）

`portfolio-hard-negative source factory v1` の実装前診断として、v22 cross-archetype 4 source、v23 Lucario-core 4 source、v24 Lucario-core 5 sourceを生成した。v24はruntime smoke 40/40、最終source-side validation 960/960を全て`DONE`・fault0で完走したが、3 reference×両seatのstrict gate（max seat gap `<=5%`）で`selected_ids=[]`となった。v22 fast independentは64局・fault0・7.8125%、v23 8×4は256局・fault0・19.140625%、v24 32×3は960局・fault0・23.5417%だった。

判定は`SOURCE_FACTORY_SCREEN_PASS / SOURCE_SIDE_STRICT_GATE_FAIL / BESTKNOWN_UNCHANGED`。P1 CEM、DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py`へは接続していない。v24 final objective artifactは`runs/cg-portfolio-lucario-core-source-v4-20260816/source-validation-32x3-final/source_side_objective.json`（SHA `a105212969e171693675654f06c5d9ce63bc747ed3848a271f173e09db88160f`）で、全5 sourceがeligible=falseである。中断したv22 26/128 runは性能証拠に使わない。

次は既存固定configのretryではなく、新epoch／新seedでdeck-bound source-side CEMを設計する。各deck recipeに複数P1 parameter configurationを束ね、terminal WDL・seat・opponent identityだけでscreenし、source-side strict gateを4 source以上が通過した場合だけTRAIN-only P1 CEMへ進める。実装は未開始であり、BestKnown／Champion／production／submissionは不変である。

`ono-`の出典は公開kernel名ではない。local Git identity `bfe-lab-ono <ono.ryosuke.36t@st.kyoto-u.ac.jp>`、sealed branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`（`feat(submission): cg lethal提出候補を封印`）というローカルGit一次情報に由来する。root deckの単一公開元を意味しない。詳細は[`cg-portfolio-hard-negative-v22-v24-source-factory-20260816`](../evidence/cg-portfolio-hard-negative-v22-v24-source-factory-20260816.md)。

## 最上段追補（2026-08-16、次の meta source 生成方式を選定）

v21までの independent-root policy surfaceは、TRAIN 4 source・2世代のrisk-aware CEMでもstrict lower-tail positiveを得られなかった。公開source intake、action-conditioned、deck-adaptive、cross-lineage、source-side adversarial CEMは既存exposure済みで、同じpool／seed／candidateのblind retryは停止する。

次の方式は `portfolio-hard-negative source factory v1`。公式カードCSV＋新規role specificationから4 deck family以上を生成し、bounded policy configurationをdeck-boundで組み合わせ、複数referenceのmean／worst terminal-WDL objectiveでsource-side hard-negativeを選ぶ。source×seat 4局以上のfault0、独立seed validation、policy/deck/config hash重複禁止を必須とし、META_DEV／META_FINALは生成時点で隔離する。P1 CEMはTRAIN-only、strict independent positive／seat-safe／opponent×seat-safe通過後のみ未使用DEV→FINAL、さらにその後だけ`cg_bestknown_loop_v1.py`へ接続する。

この時点では方式選定のみで、source plan生成、CABT、deck phase、BestKnown／Champion／production／submission変更は未実施。一次記録は[`cg-next-meta-source-generation-contract-v1-20260816`](../evidence/cg-next-meta-source-generation-contract-v1-20260816.md)。

## 最上段追補（2026-08-16、self-owned independent cross-archetype v21／P1 CEM no-update）

v20のGrass runtime faultとTRAIN 2 sourceの高分散を避け、Grassを除外してFire／Dark／Lightning／Fighting／Water／Psychicの6 sourceを新seedで生成した。promotion前の強化runtime gate（各source×seat 4局、計48局）は48/48`DONE`・fault0（32W-16L）。staged pool SHAは`aa4965ed5431550496d9df87efbbd060e633bcebbd6cea4a859bb5a836d148dc`、promoted pool／fresh／meta／split SHAは`8d07d74fb3940e8f4c09f1078084cea0fbda473fcbcc4dc194f591e79b6500cc`／`9c47dbdf08cce03abffca1b387a66b0e1d3c73c99351ef9b7c4b4be039c6f5e9`／`fd60a17bba01dd50f5fb177f450655663774f549d2bb0228b93c6d1045a93337`／`8b9dc51ca0b05aa1bc6ead3b6d1c4b0543110b0e4e2adad7a5494100aa774bba`。splitは`META_TRAIN=4 / META_DEV=1 / META_FINAL=1`、authority全false、local-eval-onlyである。

P1固定CEM `runs/cg-self-owned-independent-cross-lineage-v21-20260816/p1-cem/`（seed`2026082121`、population／elite`8／2`、2世代、META_TRAIN_ALL）は、各世代screen144局＋独立再評価96局を全てfault0で完走した。gen0 c02はscreen`+12.5pt`、独立平均`+15.625pt`でもblock`−6.25/+37.5pt`でlower-tail gate不通過。gen1の独立平均最良は`−6.25pt`。両世代`incumbent-center`×2、P1 center保持、`champion_changed=false`。gen1自動DEV診断は32/32`DONE`・fault0、META_FINALは未読である。

### CANONICAL OVERRIDE（v21最新）

現行BestKnownはself-authored P1 policy＋common/public root deck。v21はsource生成・promotion・TRAIN/DEV診断までで、strict independent positive／seat-safe／opponent×seat-safeを満たさず、BestKnown loopへ接続しない。v21 pool・seed・候補のblind retry、deck phase、BestKnown／Champion変更、commit、push、Kaggle提出は行わない。次は同じindependent-root surfaceの微調整ではなく、別renderer lineageまたはpolicy→deck再結合方式を新seedで生成する。一次evidenceは[`cg-self-owned-independent-cross-lineage-v21-cem-20260816`](../evidence/cg-self-owned-independent-cross-lineage-v21-cem-20260816.md)。

## 最上段追補（2026-08-16、self-owned independent cross-lineage v20／P1 CEM no-update）

公式カードCSVと新しいseed namespaceから、Fire／Dark／Lightning／Grassの4件を生成する self-owned independent cross-lineage v20 sourceを作成した。先に指定したroot package v1は`main.py` SHA一致でも`cg/`が欠けていたため生成器がfail-closedし、同じimmutable `main.py` SHAと`cg/`を持つv2 packageへ切り替えた。factorial manifest SHAは`c576f98845bb252b9fd8ddc708d59ab5df4bf9fb0582794a534269d6e414ad0e`、staged pool SHAは`8e857acc7d133fca8837452b606b1605a9d57fd470ce2ce4d0efc3ca4cf6b334`。全sourceはauthority全false、research-onlyである。

P1対v20 sourceのpromotion前smoke（4 source、両seat、各2局）は16/16`DONE`・fault0（12W-4L）。promoted pool／fresh／meta／split SHAは`24e081f98eac76ed0ff33795e2b2d32f896e1aab57adf111c8d3a24dcd2aa3df`／`82f1a3b84c028266126b009e8024c511791d60f25f86d1bf35a93327d96c8d68`／`c4f9b93b604410cf7a39b7b2831b0b994b4db4f85ea2c2f2844029366b9f43fe`／`bcc3571028651a4e5c859df6f06e032819e39a1af25ae70637a20b8269a47982`。splitは`META_TRAIN=2 / META_DEV=1 / META_FINAL=1`である。

P1固定CEM `runs/cg-self-owned-independent-cross-lineage-v20-20260816/p1-cem/`（seed`2026081621`、population／elite`8／2`、2世代、META_TRAIN_ALL、独立re-evaluation 2 block）は、gen0／gen1ともscreen72局＋独立48局をfault0で完走した。gen0 c05は独立差`0.0/0.0pt`、c07は`−12.5/+37.5pt`、gen1 c00は平均`−6.25pt`、c07は`−12.5pt`で、risk-aware gate不通過。`incumbent-center`×2、P1 center保持、`champion_changed=false`となった。

gen1の自動DEV診断はincumbent／controlで32局中30`DONE`・2`STEP_LIMIT`（fault率6.25%）となったため、META_DEVは完全未使用とは扱わない。META_FINALは未読。現行BestKnownはself-authored P1 policy＋common/public root deckのままで、P1／root deck／Champion／production／submission／`cg_bestknown_loop_v1.py`は不変。v20 pool／seed／候補のblind retry、deck phase、BestKnown／Champion変更、commit、push、Kaggle提出は行わない。

### CANONICAL OVERRIDE（v20最新）

最終目標はself-ownedかつ確実に提出可能なdeck＋policyで、実CABT勝率を主指標に`policy → deck → policy`を回し、独立seed・未使用metaでBestKnownを更新し続けること。v20はsource生成・promotion・TRAIN CEMまでで、strict independent positive／seat-safe／opponent×seat-safeを満たさず、BestKnown loopへ接続しない。次はGrass runtime faultとTRAIN 2 sourceの分散を踏まえ、別recipe／別seedで3件以上のTRAIN sourceを作り、promotion前4 games/source/seat以上のruntime gateを通す。一次evidenceは[`cg-self-owned-independent-cross-lineage-v20-cem-20260816`](../evidence/cg-self-owned-independent-cross-lineage-v20-cem-20260816.md)。

## 最上段追補（2026-08-16、action-conditioned v2 source／raw-control CEM bridge）

公式カードCSVからP1互換Lucario deck 6件とpublic-state-only action-conditioned renderer（12係数）を生成した。v2 promoted pool／fresh／split SHAは`1f925cdda22e20e84234f4186686535991f0cf69440cf0bb7f72cba37b2154a5`／`f7512c5b2f46466418c4937401991e40eafd9331af00ea504eee16a267a8c378`／`007121171a07829a94b0926b1a137992b254f411f2f38e66a26d92bf54d94d9b`。source smoke 48/48、candidate runtime、raw P1 same-deck controlのTRAIN/DEV/FINAL screenは全て`DONE`・fault0である。

promoted/staged source directoryをcandidate seatへ直渡ししたnative`buffer full`は、opponent pool用に`cg/`を省略するsource ingest仕様が原因だった。候補をgenerator `packages/`、opponentを`promoted/`へ分離し、旧parameterized controlの差分はpair invariant不成立として性能証拠から除外した。raw-control screenはsplit間で符号が揺れ、3 splitすべてpositiveかつseat-safeの候補は無かった。

固定v5 self-owned deckのaction-conditioned CEM 1世代（population/elite`6/2`、train 96局fault0）はbest c05 train`+25.0pt`、拡張DEV`+18.75pt`、FINAL`−6.25pt`、DEV seat gap`0.625`。CEM bridge接続は確認したが、BestKnown／Champion／production／submission／`cg_bestknown_loop_v1.py`は不変。v2 pool／seedは使用済みとしてblind retryせず、次は相関の低い新source epochへ進む。一次evidenceは[`cg-self-owned-action-conditioned-v1-v2-20260816`](../evidence/cg-self-owned-action-conditioned-v1-v2-20260816.md)。

## 最上段追補（2026-08-16、epoch v19 runtime-safe cross-lineage／P1 CEM no-update）

v18のGrass/Venusaur系が4 games/source/seat未満の初期smokeを通過してしまい、追加診断で`STEP_LIMIT`を検出したため、Grass系を除外し、Fire/Charizard・Dark/Gengar・Lightning/Manectricの3 archetype×P1 lineage 3件＋independent lineage 3件を新規生成した。v19 plan SHAはP1 `1c4afea8b1f4465cef6da73573c811f6b03ac27aa95ea9746562d21b9f074bbd`、independent `665bb0d629ece2928577cb922073e024b642dc22b0b1d4636065661246e66d51`。promoted pool／fresh／split SHAは`1855ffb53e2a3fe389d430a6741a7de859410174e6b31c6a011b0fc54db28a72`／`ec3614665bf4251fe2268b173732cf04a1e10d7a1a0ef3d9f1520ed8e741e8a6`／`19a6b2362baa3c18aea70fbe453587d8679684de6b680cebec74c594cd7a852a`。既存poolとのpolicy／deck hash衝突は0件、authority全false、local-eval-onlyである。

promotion前の強化履歴smoke（6 source、各source×seat 4局、計48局）は`48/48 DONE`・fault0（29W-19L）で通過した。P1固定CEM `runs/cg-p1-cem-self-owned-runtime-safe-v19-20260816/`（seed `2026081623`、population／elite `8／2`、screen144、独立re-eval192局、各elite 2 repeat）は全件`DONE`・fault0。screen上位c00は`+12.5pt`だが独立repeat `−12.5/+18.75pt`（mean `+3.125pt`、min `−12.5pt`、subject seat-safeはtrue、opponent-seat-safeはfalse）、c06はscreen`+6.25pt`・独立`0/+3.125pt`（mean `+1.5625pt`、seat-safe false）。risk-aware gateで incumbent-center×2となり、`champion_changed=false`、P1／BestKnown／DEV／FINALは不変・未読。判定は`SOURCE_GENERATION_PASS / RUNTIME_SMOKE_4X_PASS / CEM_FAULT0 / CEM_POSITIVE_BUT_RISK_OR_OPPONENT_SEAT_UNSAFE / BESTKNOWN_UNCHANGED`。次はP1／independent rootの単純な派生ではなく、action-conditioned renderer lineageを明示的に分けたsource生成方法を設計する。一次evidenceは[`cg-self-owned-runtime-safe-v19-20260816`](../evidence/cg-self-owned-runtime-safe-v19-20260816.md)。

## 最上段追補（2026-08-16、epoch v18 cross-lineage／Grass runtime faultで停止）

P1系4件＋independent系4件のFire/Charizard、Dark/Gengar、Lightning/Manectric、Grass/Venusaur sourceを生成した。初期smokeは各source×seat 1局の16/16`DONE`・fault0だったが、これはpromotion gateとして不十分だった。Grass系だけを各4局へ拡大した診断では16局中12`DONE`・4`STEP_LIMIT`（fault率25%、summary SHA `3d82486645ac3cdf3d09a01e3ddd8547ae52b84fcc25ea51b9ffd6fe78f9cdb0`）となり、v18 P1 CEMもGrass opponentを含むscreenで`STEP_LIMIT; cabt terminal result unavailable`を17件検出した。fail-closedによりeliteを空にし、`champion_changed=false`、P1／BestKnown不変。

v18 merged staged／promoted pool／fresh／meta／split SHAは`822dbf3c8af9f2fcf693e36fade9e20c1826ec8e90e5fbfafce639daf9c41d53`／`b497c89f0c887d449b9fe651ead5b5da501a3bdd65f93313313c9d2dc5907b52`／`14705c380d8a109b37ee66e5a16d64b40b678ea2e88d6e9372d101abadd299d0`／`461d39f19da417656917cbc27876504274cd70c21cc0cd79e1e31cfd8f72d128`／`dac15378f9c145e976fedfa146e467bada78be837560eeb4f63a8631a41b1264`。判定は`SOURCE_GENERATION_PASS / INITIAL_SMOKE_INSUFFICIENT / CEM_FAULT_INCLUSIVE / RUNTIME_UNSAFE_GRASS_SOURCE / CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。以後のpromotion runtime gateは最低4 games/source/seatとする。一次evidenceは[`cg-self-owned-cross-lineage-v18-20260816`](../evidence/cg-self-owned-cross-lineage-v18-20260816.md)。

## 最上段追補（2026-08-16、epoch v17 cross-element independent source／CEM positive but seat-unsafe）

公式カードCSVから Fighting/Zygarde、Water/Starmie、Psychic/Gardevoir の3 archetypeを使い、P1とは独立したroot policy rendererで8 sourceを生成した。deck／policy planは既存poolのpolicy hash／canonical deck hashと衝突0。staged pool SHA `25404f4c4bbe140ca468722f0d8f245fd320809ae3a5b516e83af72b82d609dd`、promoted pool／fresh SHAは `325b0c33bec126928f588f04d15ce978b4db855489ca1f72f92f3f781d3e6aaa`／`0b2bf2fb491f46b53baac12999307b640bb9f2c0d1e4f08d3a2ee84bd5c37a64`。用途はlocal-eval-only、authority全false。

P1 subject対source opponentのbounded smokeは両seat各1局×8 sourceの16/16 `DONE`・fault0（12W-4L）。splitは`META_TRAIN=4 / META_DEV=2 / META_FINAL=2`、split SHA `bba92ad1cd182ee8c05cad2d5122b70c90432ca76464bead62344c2c5b2922ce`。P1固定CEM `runs/cg-p1-cem-self-owned-independent-cross-element-v2-20260816/` はpopulation／elite `8／2`、screen144＋独立再評価48局を全てfault0で完走した。

screen上位c00／c05はcontrol比`+18.75pt`／`+12.5pt`、独立repeatは`+50/+50pt`／`+37.5/+37.5pt`だった。しかし双方`seat_safe=false`・`opponent_seat_safe=false`で、campaign `champion_changed=false`、P1 center／BestKnownは不変。DEV／FINALは未読である。判定は`SOURCE_GENERATION_PASS / HASH_COLLISION_PASS / PROMOTION_PASS / RUNTIME_SMOKE_PASS / CEM_POSITIVE_BUT_SEAT_UNSAFE / BESTKNOWN_UNCHANGED`。v17 pool／seed／候補はblind retryせず、次はpolicy familyとdeck familyを同時に変える低相関sourceまたはsource数を増やす新epochへ進む。詳細は[`cg-self-owned-independent-cross-element-v2-20260816`](../evidence/cg-self-owned-independent-cross-element-v2-20260816.md)。

## 最上段追補（2026-08-16、epoch20 v16 hybrid-support source／P1 CEM no-update）

公式カードIDだけから4件の self-owned hybrid-support deck＋P1 policyを生成した。deck spec SHA `7165a812050b6556d2ff9381dda927485e6774dc8c8090cdfad739c79964697d`、policy plan SHA `289f37211e8fbad6f7aee9d579c08f3f6e6559522e0ec40c132405a5afebbccf`、promoted pool／fresh／split SHAは `eb13f0848da593d85266902ed93fb596c2b1609ffc7a4c86b52f3e7f6dfcaae4`／`494ff6584d164b86217990a9b8fa2f5aba2e17827ddb69679d61830a6502ad70`／`3a32fd100ebbdbcc43001fa319ec9b9e096fd335618b83a1ee9fdc33a57dc8fb`。authority全false、local-eval-onlyである。

sourceをsubjectにする独立source smoke runnerはnative `buffer full. capacity:7`で既存v15でも再現したためcandidate rejectionには使わなかった。CEMの意図する向き（P1 subject、v16 source opponent）の履歴smokeは8/8 `DONE`・fault0だった。

P1固定CEM `runs/cg-p1-cem-self-owned-cg-policy-family-v16-hybrid-support-20260816/`（seed `2026081616`、population／elite `8／2`、META_TRAIN 2 source）はscreen72＋独立再評価24をfault0で完走した。screen最大deltaは`0.0pt`。elite c01は独立mean `−0.125`（repeat `−0.25/0.0`）、c06は`0.0`（`0.0/0.0`）で、双方seat-safe不成立。`incumbent-center`×2、P1 center保持、META_DEV／META_FINAL未読。判定は`SOURCE_GENERATION_PASS / PROMOTION_PASS / RUNTIME_SMOKE_PASS / CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v16 pool／seedのblind retry、BestKnown／Champion／production／submission変更、`cg_bestknown_loop_v1.py`接続は行わない。詳細は[`cg-self-owned-cg-policy-family-v16-hybrid-support-20260816`](../evidence/cg-self-owned-cg-policy-family-v16-hybrid-support-20260816.md)。

## 最上段追補（2026-08-16、epoch17–19 meta source generation／P1 CEM no-update）

epoch17のremote first-parent履歴監査は32世代・615 snapshotを検査し、accepted 0（intake report SHA `0d837a29f6ed41a1843aca25099c5178fdcb3da15f4a1a73837dfa0dafaad950`）だった。既存artifact identity、consumed ledger、source commit、runtime accessの再利用をfail-closedしたため、同じGit履歴のblind retryは停止した。

epoch18はepoch16bで唯一freshだったComfey parent（policy SHA `1cdf325ccfc7f9723c62d34f402c9a7daed6e672b7f3cff38cf979e2215928d4`、deck bytes SHA `27ae00f17af0b187033e7e558a041e139ac63d81ed84ad3150a000796e443157`）からbehavior factorial 4件を生成した。promoted pool／fresh／split SHAは`74af83aad260f6abf1bc849d551e21656f69e804cee874f781a5d03f2d6270f9`／`c1adbfd7d761f948a8b8949b1feedb92c9da1b2b9e669ef91e942a2b351b2ea8`／`0f6222aa4bb73fd27458c03ecfc0efc7a847d17cea7e84be891d98ac1f51ca71`。P1 CEMはscreen72＋再評価24をfault0で完走したが、c00独立差`+50/+25pt`のseat-safe不成立でcenter保持した。

epoch19は同じparentに別recipe `self-owned-meta-adapter-v1`（同一option type内の決定的action perturbation、rate `0.04/0.08/0.12/0.18`）を適用した。4 variantは各2局smokeをfault0で通過し、統合pool／fresh／split SHAは`8c88578a7c7558f6c718aa767cd824132f1508729172041ccee735c278a0d071`／`7ceee2cf9867e1c5af13ea97b1b911dfe58963253b7f1bde6a7c5c1f571ccc5a`／`7017036dd9b2bfe738ff916018867458efcb8f5b3bd0ea7e357f616aea2cde75`。P1 CEM `runs/cg-p1-cem-internal-comfey-adapter-epoch19-20260816/`はscreen72＋独立再評価24をfault0で完走した。c03はscreen／独立TRAINとも+25.0ptだが再評価`+50.0/0.0pt`、c07は`0.0/−25.0pt`で、seat-safe／opponent-seat-safe不成立。`incumbent-center`×2、P1 centerを保持し、DEV／FINALは未読である。

判定は`SOURCE_GENERATION_PASS / BOUNDED_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。epoch19は同一parentからの相関派生であり、独立作者lineageやnative性能の証拠ではない。現行BestKnownはself-authored P1 policy＋common/public root deckのまま。epoch19 pool／seed／候補のblind retry、`cg_bestknown_loop_v1.py`接続、deck phase、BestKnown／Champion変更、commit、push、Kaggle提出は行わない。次は相関を下げる新しいpermission済みmeta sourceまたは別generator recipeを生成し、strict gate通過後のみloopへ接続する。一次evidenceは[`cg-internal-comfey-adapter-epoch19-cem-20260816`](../evidence/cg-internal-comfey-adapter-epoch19-cem-20260816.md)。

## 最上段追補（2026-08-16、epoch14 historical internal source／P1 CEM no-update）

first-parent historical snapshot intakeで、現行pool／artifact identityと重複しない内部Git source 12件（Cynthias 3、Rocket 7、Starmie 2）をlocal-eval-onlyで封印した。pool／fresh meta SHAは `590ee18351ec4e4dc2fabb4a3d17857ecf9089f86ef70a1146dffe30e97c9525`／`7b708d3dab394947d77abb0e68de416333b0c3958249df89e2adb94b1f11d610`。P1 package両seat各1局 smokeは24/24 `DONE`・fault0（5W-19L、20.8333%、性能根拠ではない）。

splitは `META_TRAIN=8 / META_DEV=2 / META_FINAL=2`、split SHA `bf24b182f8ef85af3faea9dd9202144bdc19fa97cc5962e9b2cab45c19868ec9`。P1＋root deck固定CEM `runs/cg-p1-cem-internal-historical-epoch14-20260816/`（population／elite `8／2`、1世代、META_TRAIN_ALL）はscreen288＋独立96局を全て`DONE`・fault0で完走した。screen上位c07は+15.625ptだったが、独立は`+6.25pt / −18.75pt`（mean −6.25pt、minimum −18.75pt）、seat／opponent-seat-safe不成立。c01も`−18.75pt / −18.75pt`で、`incumbent-center`×2、P1 center保持。DEV／FINALは未読である。

判定は `SOURCE_GENERATION_PASS / BOUNDED_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。このpool／seedのblind retryは行わない。現BestKnown／P1／root deck／Champion／production／submission／`cg_bestknown_loop_v1.py`は不変。一次evidenceは [`cg-internal-historical-epoch14-p1-cem-20260816`](../evidence/cg-internal-historical-epoch14-p1-cem-20260816.md)。

## 最上段追補（2026-08-16、epoch11–13 source／root-deck P1 CEMとdeck/fallback契約診断）

epoch11–13で生成・独立検証した4件の self-owned robust sourceを、P1＋root deck固定の正規 CEMへ接続した。pool manifest SHAは `0191e4cd4bbd481abdfe95ea84310562dd43c57ba375e902b1e11f3527c06ed7`、split SHAは `17150416b386fb70a1b370265f7fe9e892957af8838690db3dbf3621ab9c5ed3`。`META_TRAIN`は c07／c01、`META_DEV`は c06、`META_FINAL`は c09で、DEV／FINALは未読のまま保全した。

root-deck P1 CEM `runs/cg-p1-cem-robust-source-epoch11-13-root-20260816/`（seed `2026090412`、population／elite `8／2`、1 generation、screen72＋独立48局、positive／risk-aware gate）は120/120 `DONE`・fault0で完走した。screen上位 c05は候補 `6W-0D-2L` 対 control `3W-0D-5L`（`+37.5pt`）だったが、独立2 blockは `+37.5pt / −12.5pt`、risk-aware mean／minimum deltaは `+12.5pt / −12.5pt`、seat-safe／opponent-seat-safe不成立。`incumbent-center`×2でP1 centerを保持し、P1／root deck／BestKnown／Champion／production／submission／`cg_bestknown_loop_v1.py`は不変。campaign／generation／results SHAは `1c96441cf7d55e194b1fe35a72b14b2c902f9dcc915d75997ce7c054f1dc98e9`／`a31f8f75f1aa152fdb0d292c02a452fc598a5de94dbbc7f08ff5c10f7dcc303f`／`aace24041382031e503f8645813f5fc8a206162c07470a602cc689a916c9cd73`。

先行 self-owned kieran policy CEMは、candidate deck SHA `c82f8ccda501d9396e0eca9f6f7e0d8aebdeeefbd0f0bde631c5231158d6e2fd`とP1 root control/split SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`を混在させたため、CABT前の static smokeで `candidate failed the P1 deck/fallback contract`となった。既存 deck-bound回帰テストは2/2 PASSであり、契約を緩める修正はしていない。self-owned deck policy CEMは、同一deckに再束縛した source／control／splitを別fresh epochとして封印してから再開する。判定は `SOURCE_GENERATION_PASS / STATIC_CONTRACT_DIAGNOSTIC / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。詳細は [`cg-robust-source-epoch11-13-root-cem-20260816`](../evidence/cg-robust-source-epoch11-13-root-cem-20260816.md)。

## 最上段追補（2026-08-16、公開 re-export source intake／wrapper contract fix）

未性能使用の `res1235/rule-based-agent-mega-lucario-ex-deck-very-simple` は、root `main.py` の `from agent import agent` re-export を entrypoint gateが認識できず、従来は `missing_agent_entrypoint` で停止していた。source本体は変更せず、明示的 import aliasを受理する静的判定を追加した。

generated wrapperの一／二引数契約も修正した。payloadが一引数なら configurationを渡さず、signatureが二引数をbindできる場合だけ渡す。raw tar SHA `9b5dee3801e7ee4dff40af94fd08476849bbd08cbc19cd49f254283c197d0bea`、canonical deck SHA `282bbb43e78cd05d63c1bf2e680202537bdc5ad680966ead77e8dc8400f65cce`、wrapper SHA `be74996cfb949205f3dc3c59814b23c649b4400c25c931df76e9df7ca0af74d2`を固定した。

修正版 intake `runs/cg-kaggle-kernel-meta-intake-public-reexport-wrapperfix-epoch9-20260816/` は accepted 1、exact 60、ACE SPEC 1、static findings 0、network/import実行なし。fresh meta SHA `bde52f78b9897b0751f27439f2e8bd81c986fff8ba4f8623c4fbafaac0a59103`、pool SHA `d91a0810ba4aa6f6663dd802bd957ce3ca5a1b18893d3ed83ac3c84d82423a70`。

source smoke runnerは pool opponentを先にbindして source importを遅延し、stable artifact `runs/cg-kaggle-kernel-meta-smoke-public-reexport-wrapperfix-epoch9-20260816-ordered-2x-stable/`で `official_random`、両seat、各2局の4局を `DONE 4 / fault0 / 4W-0D-0L` で完走した。summary SHA `4442257cdadba8a8522febeca66e9cf0ddc11f1fbf12b2581f4f792787f92669`、completion manifest SHA `a988456d5318e176656127f7798c5c81c8ab9222501c017f3412ccbb47260e73`。これは local-eval-only の bounded smokeであり、public score、性能改善、META_TRAIN／DEV／FINAL、CEM、promotionの根拠ではない。現BestKnown／P1＋root deck／P2／Champion／production／submission／`cg_bestknown_loop_v1.py`は不変。詳細は [`cg-kaggle-kernel-meta-reexport-wrapperfix-20260816`](../evidence/cg-kaggle-kernel-meta-reexport-wrapperfix-20260816.md)。

## 0. 位置づけ

このpackは、現行worktreeで確認できる self-owned cg P1、P2 holdout、freshness、提出closure、BestKnown loopの最新状態をChatGPTへ渡すための短い正本補助資料である。履歴の詳細は `docs/status/chatgpt_full_context_pack_native_meta_overfit_2026-08-13.md`、実行時系列は `docs/status/current_status.md` と `docs/status/handoff.md` を参照する。推測で過去方針を再開せず、このpackと最新artifactを優先する。

## 最上段追補（2026-08-16、epoch9／epoch10 source CEM・self-owned v8 deck）

epoch9／epoch10 source CEMは、P1 parameter surfaceから新しいself-owned robust sourceを生成する経路として完走した。epoch9はscreen 576＋fresh validation 96、epoch10はscreen 576＋fresh validation 192を全て`DONE`・fault0で完了し、epoch9-c02とepoch10-c11をpromoteした。epoch10 poolは6 sourceを `META_TRAIN=4 / META_DEV=1 / META_FINAL=1` に分離し、split SHAは `9e16db82307dc2cc2510d22b5575ef55a7b95c250fb30ed0ac7a3bd3abb7ec53`。

epoch10 poolをP1 policy CEMへ渡したが、META_TRAIN-only（seed `2026089202`）とMETA_TRAIN＋META_DEV（seed `2026089301`）の両方で、2世代・独立3 blockを全てfault0完走したものの、lower-tail／opponent-seat gateを満たす候補はなく、`incumbent-center`を保持した。判定は `SOURCE_GENERATION_PASS / FRESH_ROBUST_SOURCE_POOL / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。

公式カードCSV＋self-owned spec v8から4つの60枚 deck＋policyを生成した。public canonical collision 0、parent deck参照なし、authority全falseである。epoch10 META_TRAIN matched screenは全候補 fault0だが、P1 control比で `−18.75 / −9.375 / −9.375 / −18.75pt`。v8 recipeはhard-negativeとして停止し、deck phaseへ進めない。

P2 `cg-p1-cem-incumbent-g01-c83df4408b24`は、現repoのfresh holdout一次記録ではP1比 `−2.9948pt`（P2 `188W-1D-190L-5F`、P1 `200W-0D-180L-4F`、P2 fault 9）で `NOT_PROMOTABLE`。別資料の `+1.82/+5.56/+3.13` は現worktreeで対応artifactが確認できないため未照合値として扱う。

`ono-`は公開kernel作者名ではなく、Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／封印commit `1965b42b028f10960d08ccb4980be5b76946f98b`由来のlocal prefixである。詳細なartifact、SHA、次の再開条件は [`cg-epoch9-10-source-and-self-owned-deck-v8-20260816`](../evidence/cg-epoch9-10-source-and-self-owned-deck-v8-20260816.md) を参照する。

## 最新追補（2026-08-16、robust source pool再検証／P1 CEM no-update）

同一portfolioのrobust source CEM epoch4〜7を完走した。epoch7（population 24、screen 576局、elite validation 96局）は全row `DONE`・fault0だがfresh promotion 0件。`campaign_result.json` SHAは `faced4d8c31186177d666af1f27b155563515f6a5504850251b80b734570856b`。同じportfolioのblind retryは停止する。

過去screen gate通過かつ未使用のdistinct candidate 8件を新seedで384局再検証し、`epoch2-c01`、`epoch2-c03`、`epoch4-c06`、`epoch7-c19`の4件を選定した。fresh meanは64.5833%、70.8333%、56.25%、63.5417%、worst referenceは50.0%、62.5%、50.0%、53.125%、max seat gapは25.0%、12.5%、25.0%、18.75%。全row fault0。validator result SHAは `98fe3c8b8b0f011633103efea8b82725b6af028530c4c7c45c08a6aef9fa3b59`。

4件を `runs/cg-robust-source-weekend-pool-20260816-v1/` に再封印し、P1 source smoke 8/8 `DONE`・fault0、split `META_TRAIN=(epoch2-c01, epoch2-c03)`／`META_DEV=(epoch4-c06)`／`META_FINAL=(epoch7-c19)`を作成した。pool／fresh／meta／split SHAは `920880c7bac47ef7f0d69b3d895176981bdb809a93d1fb7fbf8cb5873c5afa0c`／`ceeee4148fdd8ca205838208cd303c8ad6690b0c6c3951ec4167c8cb736ec29b`／`672d2831725ee61d060be8deb8e335fac9b16eccc6ab113c0707fedbad14a1fc`／`7e7499cc59c1ee1b92041ee89222e29d1557cfc8b69c56d24247af6292f4ad23`。`load_weekend_split(..., verify_sources=True)` PASS。

P1固定 policy CEM `runs/cg-p1-cem-robust-source-weekend-20260816-v1/`（seed `2026084002`、population／elite `8／2`、META_TRAIN_ALL、screen 72局＋独立re-eval 96局（各elite 32局＋shared control block））は全row `DONE`・fault0。screen上位は+25.0ptだったが独立は−12.5pt（repeat −18.75／−6.25pt）、もう1候補も独立−15.625ptで、positive／risk-aware／seat-safe gate不成立。`incumbent-center`×2、P1 center保持。DEV／FINALは未読。result SHA `fba482da45928c2d8070c7eb7db0603b58b954d4af666b49198acf08dccd973e`。

判定は `SOURCE_GENERATION_PASS / FRESH_DISTINCT_SOURCE_POOL_SEALED / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P1／root deck／BestKnown／Champion／production／submission、`cg_bestknown_loop_v1.py`昇格状態、commit／pushは不変。次は同じsource portfolioを再試行せず、別policy lineageまたは別deck recipeのfresh source epochを作り、独立 positive・seat-safe・unused DEV／FINALを通過した候補だけを次のpolicy→deck phaseへ渡す。一次evidenceは `docs/evidence/cg-robust-adversarial-source-cem-20260816.md`。

## 最新追補（2026-08-16、self-owned robust adversarial source CEM epoch3）

新しいmeta source生成方法として、P1 parameter surfaceからcandidate policyを生成し、P1／Rule v0／self-owned independent policyの固定portfolioに対するterminal WDLのみでmean／worst scoreを最適化する `meta-specialist-robust-adversarial-source-cem-v1` を追加した。公開kernelを生成元には使わず、action trace・private field・teacher labelは不使用である。screen上位1件だけでなく全eliteをfresh seedで検証する方式へ修正した。

epoch3（seed `2026081694`、population／elite `8／2`）はscreen 192局、elite validation 2候補×48局、source smoke 2局を全て`DONE`・fault0で完走した。`robust-source-g00-c05-acb3f0d8e32e` はscreen mean `54.1667%`・worst `25.0%`、fresh validation mean `56.25%`・worst `50.0%`・最大seat gap `25.0%`でpromotion gateを通過し、fresh meta manifestまで生成した。promoted pool SHA `fbe73d49c918d4d13c0d2670941f38b507826d8efe96873e13c4f80abf14c3c5`、fresh meta SHA `671bff318a6b2ff0479d6ee96868faae80a9c51cc79cd4c19cfbb56d945ee707`。`build_fresh_meta_batch_v1`再検証PASS。

epoch1 public reference混成は全候補seat-collapse、epoch2 self-owned portfolioはfresh validation seat gap `62.5%`で不合格だった。epoch3は全elite fresh validationで選抜noiseを抑えた。判定は `SOURCE_GENERATION_PASS / SELF_OWNED_ROBUST_SOURCE_PROMOTED / BESTKNOWN_UNCHANGED`。fresh batchは現状reference 1件なので、既存weekend splitを置換せず、別seed／別source epochの未使用TRAIN／DEV／FINALを先に追加分離する。P1、root deck、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変。一次evidenceは [`cg-robust-adversarial-source-cem-20260816`](../evidence/cg-robust-adversarial-source-cem-20260816.md)。

## 最新追補（2026-08-16、public-state mix epoch1 の TRAIN-only screen）

公式カード CSV＋P1 parentから6 distinct self-owned public-state sourceを生成し、promoted pool `runs/cg-p1-public-state-mix-epoch1-20260816-promoted/`（pool SHA `d55d2d2d92e3514352c7dc7a7cb1b01ecba0735fa17b889f92dfcf702cdfeda3`、fresh SHA `78c7a64fd2675b6fe16e324c5f71a4df4b97af735477b374a088c69c19f6b568`）へ封印した。exposure ledger（SHA `8320d94d1b58c0818f3741777791155c04dbe1211ebfe515cc2d263a0b46f7c5`）で `META_TRAIN=4 / META_DEV=1 / META_FINAL=1` を予約し、DEV／FINALは性能探索から隔離した。

P1 policy＋root deck固定 CEM `runs/cg-p1-public-state-mix-epoch1-20260816-cem/` は seed `20260816801`、population／elite `4／2`、screen80＋独立48局、計128局を fault0で完走したが、risk-aware／seat-safe gate不成立で `incumbent-center`を保持した。6 public-state packageのTRAIN screenは96局 fault0で、paired screen topは `ahead-lethal-conserve +25.0pt`。上位4候補の別seed `20260816803`・各opponent×seat 4局の独立再評価（256局、fault0）は `−3.125 / −25.0 / −12.5 / −18.75pt` で全件不採用。P2／BestKnown／`cg_bestknown_loop_v1.py`は不変、META_DEV／META_FINALは未読である。一次evidenceは `docs/evidence/cg-p1-public-state-mix-epoch1-20260816.md`。

`ono-` は公開kernel作者名ではない。local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`（self-authored P1 policyを封印）に由来するローカル識別子である。現行BestKnownの正確なラベルは「self-authored P1 policy＋common/public root deck」で、root deckの単一公開元は同一bytesが複数あるためrepo証拠だけでは特定できない。

## 最新追補（2026-08-16、epoch9 raw timeout／runtime-safe Metal family）

未使用の許可済みGit履歴から `agents/ozawa-metal-psychic-search` の4 snapshotをstatic-only intakeした。4件は同一branch・同一canonical deck SHA `e32f681f9ca9505b17bcd1a48acab223d0ae63b0b40e169cf18d926333781c1f`の時系列で、raw P1 smokeは8局中6局`parent_timeout`だった。

既存Metal runtime-safe generatorを新snapshotの`PRINPLUP`なしpriority tableへexact対応させ、4 variantを`runs/cg-internal-source-epoch9-metal-runtime-safe-20260816/`へsealした。pool／fresh／split／meta manifest SHAは`016a18aeff4d3a707fd4e907851acfc5dfb46d461fddd0646397b3b5c07867f6`／`cb6a82456aa2d170973f6b288230468fe071179ee4d0c4b3062d5e21a12a3e31`／`afa414b826f290d1903f7f0993004193e0899ea2f38afcc675598f4998208d0`／`75975df8da9b1e348761e61c89fff749988f08a6ec6648d83e9a86cd19a646db`。P1対の8局bounded smokeは8/8`DONE`・fault0（5W-3L）。

これはsource生成／runtime gateの検証であり、同一lineageの派生4件を独立sourceとして扱わない。CEM、DEV／FINAL、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変。一次evidenceは`docs/evidence/cg-internal-source-epoch9-runtime-safe-20260816.md`。

## 最新追補（2026-08-16、v11未使用holdout／新規multi-author intake）

v8 c06をv11 promoted rootの未使用`META_DEV`／`META_FINAL`へ独立評価した。candidate／control各32局、合計128局は全て`DONE`・fault0。DEVはcandidate 18W-14L対control 17W-15L（+3.125pt）、FINALは22W-10L対14W-18L（+25.0pt）だった。ただしcandidate seat gapはDEV 12.5pt、FINAL 25.0ptで、strict `seat_gap <= 5pt` および opponent×seat-safe を満たさない。平均転移は確認したが昇格不可であり、P2／P3、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変。一次evidenceは`docs/evidence/cg-v11-unused-holdout-and-public-multiauthor-intake-20260816.md`、artifactは`runs/cg-v8-c06-v11-unused-holdout-20260816/`。

次の優先課題として、作者系譜を分散し、tar SHA・source policy・canonical deckを先に固定する`MULTIAUTHOR_EXPOSURE_FIRST_INTAKE_V1`を構成した。config `configs/meta_specialist/cg_kaggle_kernel_meta_public_fresh_epoch8_multiauthor_20260816.json`（SHA `f24672fefccb97a84170b8c9558205b0d7bf01c4309cbcef307daa2aad29edea`）で Emanuellcs／Nursrijan／Res1235 の3件をintakeしたが、accepted 0件（deck欠落／invalid deck／agent entrypoint欠落）。intake reportは`runs/cg-kaggle-kernel-meta-intake-public-fresh-epoch8-multiauthor-20260816/intake_report.json`（SHA `94fdd656e29cdf805cb207d3f054463a3381f9d781c051855f53697762a5295e`）。これは性能失敗ではなく、legality／static source gateのfail-closedであり、smoke・CEM・holdoutへ接続しない。

したがって、既評価sourceのblind retryではなく、exposure ledgerでauthor／source branch／policy SHA／canonical deck SHAを予約し、TRAIN／DEV／FINALをCABT前に分離した新規permission済みsource snapshotを獲得する。3件以上が`legality → static safety → bounded fault0`を通過するまでheavy CABTを起動しない。`ono-`は公開kernel作者名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42b028f10960d08ccb4980be5b76946f98b`由来のローカル識別子である。現行BestKnownはself-authored P1 policy＋common/public root deckのまま。

## 最新追補（2026-08-16、cross-lineage epoch7／c05 holdout）

公開kernelの未使用lineageからpolicy parentとdeck parentを分離して交差再構成する `CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1` を実行した。yaminh／samrishb／Sushanth Emboar／Sushanth Zacianを組み合わせた7 sourceを生成し、P1 smoke 14/14 `DONE`・fault0（8W-6L）でpromotionした。promoted pool／fresh／meta／split SHAは `aa5a01b6a6bcfa12b2468c305c54810d02d5b5fc7e3fa359648455052569ff58`／`ad5b80d9d5db4258f11958c167e2dda286ec30c1013fe45ce4e3252da4e582f5`／`c32822ba9ac8b8384a0e58ac8d9353a482ab94197bdd1933c984a34c8cd2b70e`／`75c6262ce42d27a0e4e9ef4177b28a460a6f981e6f422b7692f0b95afaa46a88`。TRAIN5／DEV1／FINAL1で、DEV／FINAL policy lineageはTRAINから分離した。このepochは公開lineage再構成であり、self-owned sourceとは分類しない。

`runs/cg-cross-lineage-epoch7-cem-20260816/` のP1固定CEM（seed `202608985`、population／elite `8／2`、1世代、独立2回）はscreen180＋独立60局を全て`DONE`・fault0で完走した。screen最大差は0pt、上位c05の独立差は`+20pt / 0pt`（worst 0pt）、c06は`−5pt`。risk-aware positive gate不成立でP1 center保持、BestKnown変更なし。

未使用DEV／FINALでc05を候補／P1 control各32局確認した結果はcandidate `3W-0D-29L` 対 control `0W-0D-32L`、差 `+9.375pt`、fault0。ただし2 sourceだけの低絶対勝率holdoutであり、転移シグナルに留めて昇格しない。holdout summary SHAは `91332017fa989c85560b4d6c35e86b9a6b39da26aee6c77114cda27cb76b35a6`。詳細は `docs/evidence/cg-cross-lineage-epoch7-cem-20260816.md`。

## 最新追補（2026-08-16、self-owned v14/v15 と root-deck CEM no-update）

v14 behavior-spreadは公式カードCSVから8 self-owned sourceを生成し、P1 smoke 32/32 `DONE`・fault0（15W-17L）を通過した。CEM screen216・独立72もfault0だったが、上位c01の独立mean/minは`−16.667/−41.667pt`、c06は`0/−25pt`でgate不成立。v14 pool／fresh／meta／split SHAは`01aa3179e1bb7e1a68a646b315574bda758b1afd876ff21ea0ab41c216758d3d`／`2c3bd8082a95eee45e2791293f6acd014c96959ce1bf433bdcdcfbfbe670b6eb`／`5ac26b79f05e18ae0e963b5c71fb7917f650443ed47a61a629fa28ff0480c1d2`／`06ba48f3db5075be5278088bd576f2bbd381bf49bf68c2871f6172854668efd0`。

v15初回はspec v1の既存public canonical deck衝突でfail-closed。spec v2・別seedのretry1を採用し、8 self-owned sourceをP1 smoke 32/32 `DONE`・fault0（17W-15L）でpromoteした。epoch6g public 2 sourceと混成した10-source poolでは、v15 8件をMETA_TRAIN、public 2件をMETA_DEV／META_FINALに分離した。正典P1＋root deck（root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）のpool／fresh／meta／split SHAは`7b27d98dbb546d37eabc6869aeca88474da8d17e84bdce3e9d5d8a084ab7d58c`／`c40aa72dca9925f62857262f84b807685fc5f8322a0e185ce9f8f23334be2aa6`／`f6df1830fdb7c871ea6f65de0c211768c4514f37331eba731a196774e4ba7464`／`e25e01b5af15deef75fa20ff9bf84b2cf82dedbdebc373cc1018110ccd622cbf`。

root-deck CEM `runs/cg-self-owned-public-mixed-cg-cem-v15-rootdeck-20260816/`はP2 config `c83df4408b24`、seed `202608982`、population／elite `8／2`、1 generation、独立再評価2回、positive／risk-aware gateで実行した。screen288局はfault0でc05 `+12.5pt`、c06 `+9.375pt`まで上がったが、独立c05はmean/min `−3.125/−6.25pt`、c06は`−6.25/−18.75pt`、両者seat/opponent-safe false。`incumbent-center`×2でP2を保持し、DEV／FINAL、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変。判定は`SOURCE_GENERATION_PASS / ROOT_DECK_BOUND / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。一次evidenceは`docs/evidence/cg-self-owned-v14-v15-rootdeck-cem-20260816.md`。

self-owned batch promotion後のsmoke／promoted pool SHA不一致を修正し、回帰テスト6件をpassした。v14／v15 source、P2 config、seed、候補は性能使用済みとしてblind retryしない。次はsource lineage相関を下げ、holdoutを生成時点で分離し、screen上位へ独立seedを重点配分する新しいmeta生成方法を別epochで設計する。

## 最新追補（2026-08-16、epoch6e/6f/6g と self-owned v13 CEM no-update）

epoch6e／6fの公開kernel候補は各0 accepted／8 rejected（重複、違法ACE、dynamic execution、entrypoint不足）。epoch6gでは `tetsutani/grimmsnarl-ex-damage-transfer-control` と `samrishb/unified-ptcg-framework-v2` の2件を受理し、pool SHA `8dd9ceb8aa43058da20d6a21b18b15d2b787fdbc878586a967c823559aa96a9d`、fresh meta SHA `e1c0e6e11a36d1898dc69a2856833bcc60d3bceed870d1e14a97e2bc07ce1797`を封印した。legalizer補正系は8/8 `AGENT_INVALID`、adapter retryは同一artifact identity reuseで停止した。

公式カードCSVから self-owned v13（4 deck recipe×4 policy variant）を生成し、P1 smokeは16/16 `DONE`・fault0。promoted pool／fresh／meta／split SHAは `7ad55492b60622c5271999b4944a3fb91ded28198d9da66dfbf77160468d39a9`／`d0f158f01926acab0c8ba34842acf0bf70da738930d9fa486eae918e60390549`／`02feb58669de34c4f9c7030438043ded63447625e915e9d53fc1a38305b9033f`／`c6773e48f9031426b2395503b9ee53eee498eb2d92b158951843c104899ab9b5`。4 packageは `ROOT_DECK` と `deck.csv` が一致する。

v13 CEM初回は、CEMの候補materializerが immutable `p1-source-core`の旧`ROOT_DECK`をそのままコピーし、同じc06 `deck.csv`を持つ`p1-core-control`のfallbackへ再束縛していなかったため、CABT前のstatic smokeで停止した。既存self-owned materializerをCEMへ接続し、contract-only candidateで60枚の`ROOT_DECK == deck.csv`、static smoke PASS、P1 parent SHA保持を確認した。

修正後のCEM `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816-retry2/`は未使用seed`202608977`、population／elite`4／2`、1世代、`META_TRAIN_ALL`でscreen40＋独立24局を全て`DONE`・fault0で完走した。screen c03は`+62.5pt`だったが、独立平均`−37.5pt`・minimum`−50.0pt`、c00は平均`−25.0pt`・minimum`−50.0pt`。positive gate不成立で`new_center=c06`、elitesは`incumbent-center`×2。DEV／FINAL、BestKnown／Champion／production／submission／`cg_bestknown_loop_v1.py`接続は不変。判定は `SOURCE_GENERATION_PASS / CEM_CONTRACT_FIXED / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。一次evidenceは `docs/evidence/cg-fresh-source-epoch6e6g-v13-contract-20260816.md`。

再開条件は、修復済みdeck／fallback contractを維持しつつ、v13の同一seed・候補はblind retryせず、別の未使用source epochまたは別policy surfaceを生成してCEMへ渡すこと。

## 最新追補（2026-08-16、公開kernel fresh epoch6d／c06 CEM no-update）

epoch6cとは別の未性能使用公開kernel 8件（dicer992 1、Naoto 5、Maximim 2）をintakeし、8 accepted／0 rejected。intake pool／fresh SHAは`210b53a5cac15da0e57186ebe6308b1acbe707a18f61f27cbfaf307f96b4c08`／`21fa7a02bd40f185d5f10bfd95d7c9789436a3bfaef70813f3fc817c09f58355`。P1対の32局smokeはseed`202608971`で32/32`DONE`・fault0、promoted pool／fresh SHAは`a2d68d1565678d84f01ae814804e2b1a1b1985c82786aa01ac9692e209b87e59`／`ba3a08e6e78bd73a96d3cf7030a85893c215b6a0fb0d9b5b92d8badfd01d1027`。

promoted epoch6dをTRAIN4／DEV2／FINAL2（split SHA`282057daff86fe5c4bca2ca272072968a76ac342c35f429d3e5a4ddb69373f32`）に固定し、`runs/cg-self-owned-cg-policy-cem-epoch6d-c06-g01-20260816/`でc06近傍CEMをseed`202608972`、population／elite`8／2`、1世代実行した。screen144局＋独立96局は全て`DONE`・fault0。独立上位候補のmean deltaは`−17.1875pt`／`−9.3750pt`、opponent/seat-safeは両方false。positive gate不成立のためc06 center保持、DEV／FINAL未読、BestKnown／Champion／production／submission／`cg_bestknown_loop_v1.py`接続は不変。判定は`SOURCE_GENERATION_PASS / CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。詳細は`docs/evidence/cg-kaggle-public-more-epoch6d-cem-20260816.md`。

epoch6d source／candidate／seedは性能使用済みとしてblind retryしない。次は同一作者テーマの追加ではなく、未使用作者系譜・deck archetype・runtime strategyを分散させた新source epochを作り、source smoke→promotion→TRAIN-only CEM→fresh DEV→fresh FINALの順で再開する。

## 最新追補（2026-08-16、公開kernel fresh epoch6c／新meta供給とP1 CEM）

Kaggle公開kernel outputをローカル取得し、tarまたは取得済み`main.py`＋`deck.csv`をSHA固定して、static safety・合法性・過去exposure identity・bounded runtime smokeへ通す新しいsource獲得経路を実行した。8候補中6件（Ravi BattleCore 1件、Naoto戦略5件）を受理し、Bronzong／Empoleonは`invalid_ace_spec_count`で除外した。epoch6c intake pool／fresh SHAは`e546951d8e51f78f4bcaaecff23cde229253f4696b15722545170756751db498`／`20f3e2b0493e92ca3d18f56b7f5540367466b17f8ec189162dcb9662fdb1f6ae`、promoted pool／fresh／meta／split SHAは`0b940f87cd3d073ee42ffab717f2842d08d8f54582ba2a62347c435fd11485a3`／`8dfd26927121511e62c62a9fa41de1a04bd513d1081a7354b9c67f241df0b3d5`／`46d15a44baf3458ce0104174ff99f3f117e024aa1113995ca5fb452ca418dbac`／`d854f72c7541d709dbff0386a9de7ad255ef55670dc6487d5d81ec6205d7e6e6`。P1対の24局smokeは24/24`DONE`・fault0、splitはTRAIN4／DEV1／FINAL1、全行training exposure 0である。

`runs/cg-self-owned-cg-policy-cem-epoch6c-g01-20260816/`のP1 CEMはseed`202608970`、population／elite`8／2`、1世代、screen144＋独立再評価を全row fault0で完走した。screen c01はcontrol比`+25.0pt`だったが、独立差`−12.5pt / +6.25pt`（mean`−3.125pt`、min`−12.5pt`）。c04はscreen`+12.5pt`から独立`−3.125pt / 0pt`へ反転した。selectionは`risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`、elitesは`incumbent-center`×2、P1 center保持。独立positive／seat-safe／opponent-seat-safe候補が無いためDEV／FINALは読んでいない。BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変で、判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。一次evidenceは`docs/evidence/cg-kaggle-public-more-epoch6c-cem-20260816.md`。

epoch6c TRAIN／候補／seedは性能使用済みとしてblind retry禁止。次は別作者・別policy lineageのfresh source、または構造的に異なるself-owned rendererを新epochで生成し、strict gateを最初から実行する。なお、runs全体（約130GB）をscanする試行は自プロセス停止し、epoch5と同じevidence rootだけをfreshness scan対象へ戻した。途中rootは削除せず性能根拠にも使わない。

## 最新追補（2026-08-16、self-owned meta batch v2 / P1 CEM）

公式`data/raw/EN_Card_Data.csv`＋`configs/meta_specialist/self_owned_cg_deck_spec_v2.json`から、canonical deck SHAが異なる3候補を生成し、P1 policyを各deckへ再束縛した。生成rootは`runs/cg-self-owned-deck-generation-v2-20260816-00/`〜`-02/`、promoted poolは`runs/cg-self-owned-cg-meta-batch-v2-20260816-promoted/`。source sealは`src/mage_ptcg/opponent_ingest/self_owned_cg_meta_source_v1.py`／`scripts/seal_self_owned_cg_meta_source_v1.py`。public canonical collisionは0、authorityは全false。

promoted hashes: pool `a6d48cd9d5335bc349867dc91320e9154f92530f3e408b1023fc95ba0b55ef57`、fresh `5468ddc0773ace25ca9306c6e7b064562ddba16dfddb4d6e66b95138cc278d66`、split `45c72b42b380fa58d3570c9d97ddca33352f2991a2dd3255e4a208e8ceeb0451`。3 sourceを含むruntime smokeは24/24 DONE・fault0だったが、3 sourceすべてをsmokeで使ったためMETA_FINALはCEM選抜未使用でも完全未接触ではない。

P1固定CEM `runs/cg-self-owned-cg-meta-batch-v2-20260816-cem/` はpopulation4、2世代、独立re-evaluation、positive gateで完走。generation 1候補`cg-p1-cem-g01-c02-d566ed140e60`はMETA_TRAIN独立で`+12.5pt`だったが、META_DEVではcandidate `7W-1D-8L (46.875%)`対control `9W-0D-7L (56.25%)`、`-9.375pt`。判定は`POLICY_CEM_NO_UPDATE`で、P1＋root deck、BestKnown、Champion、production、submissionは不変。一次evidenceは`docs/evidence/cg-self-owned-cg-meta-batch-v2-cem-20260816.md`。

focused source tests 5 passed、py_compile/docs validation PASS、active heavy processなし。v2 deck proxyのblind retryは停止し、次は相関の低いself-owned policy familyまたは別公式deck archetypeを新epochで作る。

## 最新追補（2026-08-16、self-owned deck lineage）

公開root deckの単一元を特定できない問題への対処として、公式`data/raw/EN_Card_Data.csv`と`configs/meta_specialist/self_owned_cg_deck_spec_v1.json`だけから60枚scratch deckを決定的に生成する経路を追加した。candidate `fighting-lucario-scratch-v1-s20260816-o0000-c60e368cad31`のcanonical SHAは`c60e368cad31e90192afb820db02ac9528177ae495945a904dbfd9f0fe75ac0c`、deck-file SHAは`b144ff9909a33d39c467c74a876bac71128f9ff2d9951297db8db3390f22c0db`、package policy SHAは`fd59353369da8a28e8944170e25d0886dc5d6646edb2e65f2096b4489a23c0ab`。manifestは`parent_deck=null`、`public_parent_read=false`、authority全false、public canonical collision 0である。生成rootは`runs/cg-self-owned-deck-generation-v1-20260816/`。

candidate対P1 root-deck controlを`aristophanivan_multiply`両seat各1局でsmokeし、candidate `0W-0D-2L`、control `0W-0D-2L`、全4局`DONE`・fault0・delta`0.0pt`だった。1 opponent×1 repetitionのruntime smokeであり、新meta source、独立DEV/FINAL、CEM、BestKnown更新の根拠ではない。詳細は`docs/evidence/cg-self-owned-deck-generation-and-smoke-20260816.md`。現行BestKnown、Champion、production、submission、commit、pushは不変である。

## 1. 最終目標

self-ownedかつ確実に提出可能なdeck＋policyについて、実CABT勝率を主指標に
`policy → deck → policy` の改善ループを自律的に回し、独立seed・未使用metaで再現性を保ちながらBestKnownを更新し続ける。最終的にはnative上位72%級を安定して超える提出モデルを完成させる。

現時点ではこの最終目標は未達であり、P1を運用基準として保持している。

## 2. 作業状態

- branch: `feature/belief-guided-search`
- HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16` (`chore: ignore local CABT engine artifact`)
- worktree: このpackと同時に行った文書更新後で、977 entriesの大量未コミット差分（今回追加したevidence／packを含む）。ユーザー／過去研究の差分として保全し、整理・削除・上書きしない。
- active heavy process: なし。
- commit、push、Champion変更、Kaggle submit: なし。

## 3. 現行BestKnownと提出closure

Current research/production referenceは self-owned cg P1 `cg-lethal-target-v1`＋root deck。

| item | SHA / 判定 |
|---|---|
| P1 policy `main.py` | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` |
| P1/root deck `deck.csv` | `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` |
| P1 archive | `e724b48059ac16a43ea28030647fea8b516caf7f9c1ac1331659e04caf369f02` |
| local wrapper | CG parity PASS、8-game clean-room PASS、fault 0、illegal 0 |
| remote submission | `submission_ready_candidate=false`。remote Submit verifier／契約未確認 |

root `main.py`（Rule-v0 lane）は SHA `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7`で、P1 package policyとは分離して扱う。current root `deck.csv`は60 cards、P1 deckと同一bytesで、composition SHAは `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7`。

## 4. P2/P3判定

P2 `cg-p1-cem-incumbent-g01-c83df4408b24` は、fresh medal 24件でP1比 `−2.9948pt`（P2 `188W-1D-190L-5F`、P1 `200W-0D-180L-4F`）、reserve 10件で `−0.9375pt`（P2 `76W-1D-83L`、P1 `78W-0D-82L`、P2 seat gap 14.3750%）だった。24件側のfaultは`medal_0019_df6f7443`に集中するが、reserve 10件でもfault0・負差・seat unsafeだったため、faultだけを理由とする再評価はしない。

判定は両方`NOT_PROMOTABLE`。P2をCEM center、P3、deck phase、Championへ昇格していない。

## 5. meta freshnessと禁止経路

pool manifest SHAは `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`。102 rows（public 71、internal 31）、smoke-ready 101、全て`local_eval_only`。public smoke-ready 70 IDsは既存artifactへ全件出現済みで、fresh・unused・smoke-ready public metaは0件である。

R7 (`public_archaludon_cinderace_r7`) は smoke false、raw SHAをcanonical deck identityにしている不整合、過去使用済みのため再利用不可。internal slow/quarantine assets、既存holdout、既評価候補のblind retryをfresh証拠として扱わない。

Rule v0新規探索、Student/AWR/BC、native teacher、deck blind sweep、既評価policy surfaceのblind retryは再開しない。未使用metaが無い状態でheavy CABTを追加消費しない。

## 6. 実装済みの再開契約

`src/mage_ptcg/meta_specialist/cg_bestknown_loop_v1.py` は以下を研究専用で実装する。

1. fresh manifest、pool実体、policy SHA、canonical deck SHA、未使用証跡、seed namespace/planをCABT前に検証。
2. `DECK_FIXED_LONG`（policy改善）→`POLICY_FIXED_SHORT`（deck改善）→policyを最大8 cycleで交互実行。
3. fault 0、正delta、candidate seat gap≤5%の`POSITIVE_CONTINUE`のみ研究parentへ渡す。
4. 各cycleをno-clobber checkpointへ保存し、CABT起動、training、Champion、submission権限は持たない。

focused loop/alternating/population/runner、fresh-intake、derived-source、historical-source、behavior-family、CEM隣接の今回の統合確認は54 tests PASS。py_compile、docs validator、diff-checkもPASSしている。これは性能改善結果ではなく、fresh source到着後の誤昇格・meta再利用を防ぐ継続実行契約である。

## 7. 再開条件と次の行動

新しいmeta sourceが供給されたら、freshness evidence file、canonical identity、独立seed planを固定し、P1をcontrolにして `policy CEM → 独立複数block → deck phase → policy phase → fresh DEV/FINAL` の順に実行する。candidateが通過してもlocal package closureとremote Submit契約を別gateで確認し、確認前に提出しない。

詳細な現状証跡: `docs/evidence/cg-current-state-report-20260815.md`。

## 8. 新しいinternal meta source intake（2026-08-15追記）

公開sourceが`UNVERIFIED_RULES_CONSTRAINT`でarchive-onlyのため、許可済み`origin/agents/*` branch snapshotからfresh local-eval sourceを生成するread-only intakeを実装した。`scripts/discover_fresh_internal_meta_v1.py`は同一commitのroot `main.py`＋`deck.csv`を読み、current pool／artifact／consumed identity、canonical deck hash、static security、local-only permissionを固定して、`cg_bestknown_loop_v1.py`に渡せるfresh-meta manifestを別staged rootへ出力する。checkout、import、network、current `opponents/` mutation、Champion、submissionは行わない。

実repo14 refsの結果は1件accepted、13件rejected。acceptedは`internal_ozawa-rocket-rule_de797c3646e9`で、staged root `runs/cg-fresh-internal-meta-intake-20260815-f/`、source commit `de797c3646e935157618be3edea17615430ccfec`、source policy SHA `8025ae95503ef10cc82a433518e81ba61554ce1547846eecc582610a85ae6c7f`、staged policy SHA `159a5d61ce7d1d12cf955a5d2bf99845b25d3d32eedc3904ee46e21143be053e`、canonical deck SHA `d61230a21f488d4e78b28b37187c6a468168c0a2fff7842025e6c0409da3614a`、pool SHA `99942d1081ce1105bde2e2f19007986a866073aeb92e30528520732a4c982513`、fresh meta SHA `ae0c3f3606565556cbe7b4dc95553005bb4c1cde79852fa1b89f56deb8213438`である。提出環境のcwd相対deck読み込みは`LOCAL_DECK_SIDECAR_V1`でcandidate横のdeckへ固定し、raw／staged policy identityを別管理する。

staged pool loader、fresh-meta builder、candidateのdeck read/import smoke、root ruleとの両seat CABT plumbing smoke（seed `20260815`／`20260816`、2/2 DONE、fault/invalid/timeoutなし）はPASSした。ただしこれは性能証拠ではない。`ozawa-grimmsnarl-rule+RL`は任意path telemetry appendを検出してfilesystem-write quarantine、既存refはsource／artifact identity再利用またはself-owned除外である。次はstaged poolをcustom runnerへ渡し、P1 control対policy CEM→独立seed→deck→policyを、fault0・seat gap≤5%・未使用seed gate付きで実行する。一次evidenceは`docs/evidence/cg-fresh-internal-source-intake-20260815.md`。
既存`scripts/run_cg_p1_cem_v1.py`へ`--pool-root`を追加し、staged poolを現行`opponents/`へコピーせず注入できる。read-only `build_paired_games` preflightでは候補IDを4 game payloadへ解決できたが、CEM本体・独立性能評価は未起動である。一次evidenceは`docs/evidence/cg-fresh-internal-source-intake-20260815.md`。

## 9. derived source generation / CEM result（2026-08-15追記）

remote internal refの新規候補が1件に留まるため、accepted Rocket snapshotに含まれる`_THETA_LUCMIX`、`_THETA_A09_MERGED`、`_THETA_A07_MERGED`、`_THETA_ABOMASNOW_R2`を、`_THETA_GENERAL`初期化の1箇所へ明示的に選ぶderived generatorを追加した。これは相関したlocal-eval proxyであり、public/native metaの代替ではない。base＋4 variantを `runs/cg-derived-internal-meta-20260815-a/` へsealし、fresh batch、pool、custom splitをhash-boundに固定した。

`run_root_cg_candidate_arena_v1.py`へstaged `pool_root`とmanifest SHAをworker metadataへ渡す修正、CEMへ`population_size`/`elite_count` override、valid候補不足時のcenter保持を追加した。risk-aware CEM（2世代、population 24、elite 2、independent repeats 2、624局、全fault0）はworst independent positive候補0件で、P1 center保持となった。

未使用`META_FINAL` 2 refsでcenterを64局fresh holdoutした結果は`+6.25pt`だがcandidate seat gap `12.50%`で`NOT_PROMOTABLE`。したがってP1＋root deck、BestKnown、Champion、deck phase、submissionは不変。derived poolを用いた結果をnative 72%到達の証拠として扱わない。一次evidenceは`docs/evidence/cg-derived-meta-source-and-cem-20260815.md`。

## 10. first-parent historical meta source / CEM（2026-08-15追記）

remote branch headが既存pool／artifact identityと重複し、public sourceも未解禁のため、fresh intakeへfirst-parent historical snapshot取得を追加した。`--history-depth`／`--include-ref`は明示opt-inであり、同一commitのroot `main.py`＋`deck.csv`、current pool／artifact／consumed identity、static security、canonical deck、batch内policy重複を検査する。checkout、import、network、current pool mutationは行わない。

Festival、Rocket、Starmieの3系統から9件を`runs/cg-historical-internal-meta-20260815-b/`へsealした。pool SHAは`b09c9239c35af2a12afd52835bb8171882d8a762a1d9fb68e126d5fb30f9b071`、fresh meta SHAは`c261783d3dd232ace34903a0528a50f93aaaeb62c5a72c40fe6e0b159cf8a541`、split SHAは`e4bf12e666abb50607a6977782256276c07098a82f903a64dc7c37b59665bc00`。`cg_bestknown_loop_v1.build_fresh_meta_batch_v1`／split verification PASS、P1 subjectの18局smokeはDONE 18/18・fault0だった。

P1 control固定のrisk-aware CEM（2世代、population8、elite2、独立re-evaluation 2回）を実行し、screen216＋re-evaluation144＋DEV96＝456局を全てDONE/fault0で完了した。gen0 screen陽性は独立lower-tailで最大+8.33ptに留まり、gen1はpositive gateでcenter保持。META_DEVはcandidate `12W-0D-36L` 対 control `13W-0D-35L`、差−2.0833pt、candidate seat gap0%で`NOT_PROMOTABLE`。META_FINALはCEM選抜・DEV判定には使っていないが、全9件を含む18局smokeで実行済みのため、fresh holdout用には未使用ではない。

historical intakeは「新source identityを安全に得てCEMへ接続する」方法としては機能したが、同一branch履歴の相関があり、public/native性能の証拠ではない。P1＋root deck、BestKnown、Champion、production、submission、deck phaseは不変。次はhistorical blind retryではなく、permission済み新sourceまたは異なるbehavior-family generatorを別source epochで作り、P1→risk-aware policy CEM→fresh DEV→fresh FINALへ進む。詳細は`docs/evidence/cg-historical-meta-source-cem-20260815.md`。

## 11. historical meta source epoch e / strict fresh split CEM（2026-08-15追記）

first-parent intakeで同一Starmie deck上の履歴policy 3件を `runs/cg-historical-internal-meta-20260815-e/` へsealした。3件のstatic findingsは0、canonical deck SHAは`c69a18eccd20b925ae9e26818fb86f0eee3404bee94cffbdf52a08b6e3b10ce4`で一致する。pool SHAは`16bf897907e9c116c831ab479639b90ad91cc9de9f8c0a6cf71a192830192776`、fresh meta SHAは`2372f2c714df4d6a701444cd95604abf61d7796ddcf8c9f6af1724e7775c9a3c`、split SHAは`baa2317f2c595fe187d1686ade77e305b6badd05321e8e8b73d5a3739d45f57d`である。

`META_TRAIN=6309a5f59f6d`だけを4局smokeし、4/4 DONE・fault0を確認した。`META_DEV=66b0053163ff`と`META_FINAL=78d8b10eabe9`は未使用のまま分離した。P1 control固定のrisk-aware CEM（population8／elite2／2世代）はscreen72、独立再評価48、fresh DEV32、計152局をDONE/fault0で完了した。gen0 candidate-05はscreen `+50pt`、独立2 block各`+25pt`だったが、robust positive候補数不足でcenterはP1を保持。gen1も更新条件を満たさず、fresh DEVはcandidate/controlとも`6W-0D-10L`、差`0pt`だった。

未使用META_FINALでcandidate-05を確認した結果はcandidate `2W-0D-14L` 対 control `4W-0D-12L`、差`−12.50pt`、seat gap 0%、fault0、`NOT_PROMOTABLE`。P1＋root deck、BestKnown、Champion、production、submissionは不変。P2/P3、deck phase、`cg_bestknown_loop_v1.py`のpolicy→deck→policy、commit、push、Kaggle submitは行っていない。詳細は`docs/evidence/cg-historical-meta-source-epoch-e-20260815.md`。次は同一Starmie履歴のblind retryではなく、異なるbehavior familyの新sourceまたはgeneratorを別epochで固定する。

## 12. Starmie behavior-family source generator / fresh CEM（2026-08-15追記）

同一履歴Starmie policyのvisible-state priority tableだけを固定変換する4つの相関proxy（Supporter draw-first／Hilda-first／basic evolution-first／Poffin Snorunt-first）をsealするgeneratorを追加した。4件は新規policy SHA、同一canonical deck、static findings 0、`local_eval_only`。生成rootは`runs/cg-behavior-family-meta-20260815-f/`、pool SHA `22e71e2dde96925afbab49004ed7fd3eb35fa725f1df0bfb045d4dee2dbd3258`、fresh meta SHA `08c1296e4354cbb2972892e529ae0cec48dfc6e6c86230e2f8e03faf5695e238`、split SHA `fdb3bcf6a98496a754cea973b6848d2477900d2119178a796fe72e061b485e97`である。

train 2 variantだけを8局smokeし、8/8 DONE・fault0。P1 control固定のrisk-aware CEM（population8／elite2／2世代）はscreen144、独立再評価96、fresh DEV32、計272局をDONE/fault0で完了した。fresh DEVのP1 centerは`+6.25pt`だったがcandidate seat gap`12.50%`でgate外。gen1 candidate-04は独立平均`+6.25pt`・worst`0pt`・seat unsafe、未使用FINALでは`−6.25pt`・seat gap`12.50%`で`NOT_PROMOTABLE`。P1＋root deck、BestKnown、Champion、production、submissionは不変である。

初回の`runs/`全体scanは約1.2GB RSSで中断し、partial rootを`runs/cg-behavior-family-meta-20260815-f-incomplete/`へ移動した。既知のhistorical/CEM/config rootにscan範囲を限定して再sealした。詳細は`docs/evidence/cg-behavior-family-meta-cem-20260815.md`。次は同一Starmie相関proxyのblind retryではなく、異なるbehavior familyのpermission済みsourceまたは別generatorを新epochで固定する。

## 13. Comfey behavior-family source generator / fresh CEM（2026-08-15追記）

Starmie専用だったbehavior-family generatorを、別deck／別behavior familyのComfey library-out系へ一般化した。許可済み`internal_nihei-MegaLopunny_19fd36050805` snapshotから、`DECKOUT_AGGRESSIVE`、`DECKOUT_CONSERVATIVE`、`COMFEY_SETUP_FIRST`、`LITWICK_SETUP_FIRST`の4 policyをvisible-state-onlyの固定変換で生成した。4件は新規policy SHA、同一canonical deck SHA、static findings 0、`local_eval_only`である。生成rootは`runs/cg-comfey-behavior-family-meta-20260815-g/`、pool SHA `65c134872b3f2cb656ed49f787502d3bab7ae971de8a8443b77da3524d806252`、fresh meta SHA `7b0f6bf515527a79d46ecca844781f34acb38efecd2bb8810d7857a917242d84`、split SHA `c5378d2efee9c2220da4cfd00a9c0455736db919eb606715479c7702df8ca1aa`である。

train 2 variantの8局smokeは8/8 DONE・fault0。P1 control固定のrisk-aware CEM（population8／elite2／2世代）はscreen144、独立再評価96、fresh DEV32、計272局をDONE/fault0で完了した。gen0はrobust positiveなし、gen1 candidate-03/07はscreen各`+25.00pt`でも独立positive gateを満たさずcenter保持。fresh DEVの見かけの`+12.50pt`はcandidate/controlが同一P1 centerだったため同一policyのRNG差として扱い、policy gainとは認めない。未使用META_FINALでcandidate-03はcandidate/control各`9W-0D-7L`、差`0pt`、candidate seat gap`12.50%`、fault0、`NOT_PROMOTABLE`となった。

P1＋root deck、BestKnown、Champion、production、submissionは不変。Comfey proxyをnative/public evidenceとして扱わず、blind retry、P2/P3、deck phase、`cg_bestknown_loop_v1.py`のpolicy→deck→policy、training、longrun、commit、push、Kaggle submitは行っていない。一次evidenceは`docs/evidence/cg-comfey-behavior-family-meta-cem-20260815.md`、CEM manifest SHAは`f2e129b8da26818e671042873c40667c754e06ae3f06ec68dbb646a17099bc75`、FINAL summary SHAは`dcb156b4013c0b351901cf954e0f6e824bd95db949476233d439a22b21c5ba8d3`である。

次の再開条件は、同一Comfey proxyを再利用せず、permission済み別source／別generatorを新epochとしてsealし、screen→独立複数block→fresh DEV/FINALで正差・seat-safe・fault0を確認すること。通過時だけBestKnown loopへ接続する。

## 14. Festival behavior-family source generator / fresh CEM（2026-08-15追記）

既使用のFestival snapshotをそのまま再利用せず、visible-state priority tableを4種類へ固定変換するbehavior-family generatorを追加した。`ALAKAZAM_FIRST`、`DUNSPARCE_FIRST`、`SHAYMIN_SETUP_FIRST`、`POFFIN_DUNSPARCE_FIRST`の4 policyは新規SHA、同一canonical deck、static findings 0、`local_eval_only`である。生成rootは`runs/cg-festival-behavior-family-meta-20260815-h/`、pool SHA `6f29a032fcb79ce904992efd264c462c8b464500a539c3a10da6def24ca4e4df`、fresh meta SHA `22244c4529380a5b73ada3441cf75569ab3fda2c24df35a626a3e15daf3b41af`、split SHA `fc343031962e282210614c028797b28f6486f14bddba4de50ddec6ec5396f97c`である。

train smokeは8/8 DONE・fault0、P1 control固定のrisk-aware CEM（population8／elite2／2世代）はscreen144＋独立再評価96＋fresh DEV32＝272局をDONE/fault0で完了した。gen0/1のscreen上位は独立再評価でcontrolを下回りcenter保持。fresh DEV centerの見かけの`+25.00pt`はcandidate seat gap`12.50%`でgate外、未使用META_FINALのcandidate-05は`9W-0D-7L`対`8W-0D-8L`、差`+6.25pt`、seat gap`12.50%`、fault0、`NOT_PROMOTABLE`となった。

P1＋root deck、BestKnown、Champion、production、submissionは不変。Festival proxyをnative/public evidenceとして扱わず、blind retry、P2/P3、deck phase、`cg_bestknown_loop_v1.py`のpolicy→deck→policy、training、longrun、commit、push、Kaggle submitは行っていない。一次evidenceは`docs/evidence/cg-festival-behavior-family-meta-cem-20260815.md`、CEM manifest SHAは`21085ba442cdadf2fb908044b5525f04baeac39e1a4e16762efd263080ca4fe1`、FINAL summary SHAは`e9ebfc3f7d2918797a984f27c33ac943ec0f3bf1788d2c821e39d7c0bf684d0e`である。

次の再開条件は、同じFestival proxyを再利用せず、別deck／別sourceのbehavior-familyまたは新しいpermission済みsourceを新epochとしてsealし、screen→独立複数block→fresh DEV/FINALで正差・seat-safe・fault0を確認すること。通過時だけBestKnown loopへ接続する。

## 15. Metal/Psychic behavior-family source epoch（runtime hard-negative、2026-08-15追記）

許可済み `agents/ozawa-metal-psychic-search` の historical snapshot（commit `3f5f71d4ff5923ffafe355a9f2e57fd0b88aa675`）から、visible-state priority tableを `PIPLUP_FIRST`、`METAGROSS_FIRST`、`RECEIVER_FIRST`、`LUCARIO_PLAN_FIRST` へ固定変換する4件を `runs/cg-metal-behavior-family-meta-20260815-i/` へsealした。全件新規 policy SHA、同一 canonical deck SHA `dfdfd61d32d84ee2c181890e79ecea29a280f5636de84d3d8a418e026b5171ef`、static findings 0、`visible_state_only`、`local_eval_only`。pool／fresh／split SHAは `9cf7c7646ba8aeab4d1fb0165658d08041337df0f4a615bba66eaa656051b58d`／`686a7bb53815b45d93bc1a941e04d0dcbf1d4d22c35e5826ec2e8d26422ec27e`／`8a21f9a24f4cd6eee18df84d1a7e74b359638f58324b1028c0049ccde4a0b930` である。fresh batch／split verificationはPASSした。

P1＋root deckの train smoke は、既定環境8局で `1 DONE / 7 fault`、sourceが許可している `SEARCH_LOCAL_FIXED_BUDGET=0.1` でも `6 DONE / 2 fault`、budget `0.0` の4局確認でも `0 DONE / 4 fault`。faultは全て `parent watchdog exceeded game timeout grace` で、元の未変換 Metal/Psychic snapshotにも同じ timeout がある。したがって sourceのsearch runtimeがbounded gateを満たさず、CEM／fresh DEV/FINALへ接続しない。

P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submissionは不変。Metal/Psychic epoch iは runtime-safety hard-negative として停止し、同一sourceのblind retryはしない。次は search実行量を構造的に上限化した別generatorまたは新しい許可済みsourceを新epochでsealし、短い両seat smokeをfault0で通した後だけ loopへ接続する。一次evidenceは `docs/evidence/cg-metal-behavior-family-meta-20260815.md`。

## 16. Metal/Psychic runtime-safe behavior-family epoch / CEM（2026-08-15追記）

epoch iのtimeoutを受け、同じ Metal/Psychic source identityから `SEARCH_NUM_WORLDS = 0`、search budget default `0.0` をexact replacementし、priority差分も残す4件を新epoch jとして `runs/cg-metal-runtime-safe-meta-20260815-j/` にsealした。pool／fresh／split SHAは `a4fcee67b39c6abd9f2fca881355544f5a757d82bff294bc2afac902dbfc0019`／`7947e1c4f95639e92a4cf678a482d6cc6ccf43883597d5517de39dfe4238058e`／`12e0285708d298bbe6a6e37b4721c32c1ad2f8c06f240b65786672f559e721bf`。fresh batch／split verificationはPASSした。

P1＋root deckのtrain smokeは8/8 DONE・fault0（5W-0D-3L、約5.66秒）。P1 control固定のrisk-aware CEMはscreen144＋独立再評価96＋fresh DEV32＝272局をDONE/fault0で完了した。gen0 screen top `+12.50pt`は独立`−37.50pt`／`0pt`、gen1 screen top `+37.50pt`のcandidate-02も独立`0pt`／`+25.00pt`、worst0、seat gap12.50%で、両世代とも`incumbent-center`を保持。fresh DEV centerは差`−6.25pt`。

未使用 META_FINAL の candidate-02 は `6W-0D-10L` 対 control `11W-0D-5L`、差`−31.25pt`、seat gap0%、fault0、`NOT_PROMOTABLE`。P1＋root deck、BestKnown、Champion、production、submissionは不変。次は同じMetal/Psychic proxyのblind retryをせず、別deck／別sourceまたは複数runtime-safe source familyを生成し、fault0→独立positive→seat-safe後だけBestKnown loopへ接続する。一次evidenceは `docs/evidence/cg-metal-runtime-safe-meta-cem-20260815.md`。

## 17. historical source epoch k / cross-source confirmation（2026-08-15追記）

first-parent historical snapshotを複数remote refから読み出すsource-acquisition laneで、`runs/cg-source-audit-20260815-k4/` に22件をaccepted、158件をrejectedとしてsealした。pool SHAは`aa3dc3f3e6c3eab8a95aa9a6b0f67c958f245865cf9753cbe35b35a877441ce8`、fresh meta SHAは`2692d8301bb752f0c78190f04142d9519745f37b0e753c810754d5470acb7e55`、split SHAは`a644cedc468dabf75d17243953127beb281002f54e0cc7b6b9573f22ad748513`。train smoke 8/8、fault0。

P1 control固定のCEM（population8／elite2／2世代、独立再評価2回、risk-aware gate）は全block DONE・fault0。gen1 candidate-03はscreen`+18.75pt`、独立`+3.125pt`、META_DEV差0ptでcenter保持。汎用paired fresh confirmationでは、Cynthia/Alakazam FINALが`+3.125pt`・seat gap0%で`PROMISING_CONFIRMATION`、Hydreigon/Comfeyが`+9.375pt`・seat gap6.25%、Psychicが`+4.6875pt`・seat gap9.375%で`NOT_PROMOTABLE`だった。単一FINALの正差をBestKnown更新とは扱わない。詳細は`docs/evidence/cg-historical-source-epoch-k-and-crosssource-20260815.md`。

## 18. behavior-family epochs m–p / source audit l（2026-08-15追記）

Alakazam、Psychic、Hydreigon/Comfeyへvisible-state priority変換を一般化し、4 variantずつの新規policy SHAをsealした。全heavy blockはDONE・fault0だが、m FINALは`−3.125pt`、p FINALは`−6.25pt`、n/oのcross-source正差はseat gap gate外で、P1を更新するcandidateは未成立。新remote head audit lは`BLOCKED_NO_SAFE_CANDIDATES`（0 accepted／133 rejected）で、artifact identity再利用とfilesystem-write quarantineを検出した。次はblind retryではなく、別系統のpermission済みsourceまたは相関を明示した新recipeをsealし、複数未使用FINALで正差・seat-safe・fault0を同時に確認する。詳細は`docs/evidence/cg-behavior-family-alakazam-psychic-hydreigon-20260815.md`。

## 19. factorial behavior-family v2（epochs t / u、最新）

### 実装

新しいmeta sourceの獲得・生成方法を優先課題として、可視状態の独立優先度軸を直積にするfactorial recipeを実装した。`src/mage_ptcg/opponent_ingest/behavior_factorial_meta_v1.py` はAlakazam／Comfeyの2 familyに対応し、既存のexact replacementを2回だけ合成する。`scripts/generate_factorial_behavior_family_meta_v1.py --family alakazam|comfey` はfresh pool／fresh meta／custom splitとseed namespaceをhash-boundにsealする。static scan、visible-state-only、local-eval-only、current-pool identity検査を維持し、candidate／Champion／submission権限は持たない。

### Alakazam epoch t

base `internal_nihei-cynthias-garchomp_3818c21f59b6`。4 recipeを新規policy SHAでsealした。pool `5e6a753faae421e0c72242611a3cc1244781a02928ef3fddf35ae4506806a42b`、fresh `3618179ac14a1e804731463cfaddbffe7fd8bbb997fe8e94381cfc6bbac9611b`、split `a018ad5a0ce173dfb2bf0300a62950fc050e53051009379bbd9a849f0632f402`。smoke 8/8 DONE・fault0、CEM 272/272 DONE・fault0、両世代P1 center保持、META_DEV center差`−3.125pt`。昇格なし。

### Comfey epoch u

base `internal_nihei-hydreigon-deckout_c8430334ca23`。4 recipeをsealした。pool `ea7909050ec3bfcbea7384d10658f9e6b5bf48d18f2ef0c8706dc29acbe7042e`、fresh `e9cc9e53d17a7f07f458adba2b1bcf8d56b0f6fab7fa29cbea5a7c6eae87a4a9`、split `d4c77561a731b97abf577a57da57940548c97107f5cd63ec784121e9608768cc`。smoke 8/8 DONE・fault0、CEM 272/272 DONE・fault0。generation-1 candidate `cg-p1-cem-g01-c05-796b8f2986f4` は独立2 block各`+25.00pt`だったがopponent seat gap 25–50%で`seat_safe=false`。未使用META_FINAL 64局はcandidate `13W-0D-19L`、P1 `17W-0D-15L`、差`−12.50pt`、fault0、`NOT_PROMOTABLE`。P1を保持した。

### source供給と次の優先順位

remote source audit `r` は0/200、`s` は0/48 acceptedで、いずれも`BLOCKED_NO_SAFE_CANDIDATES`（artifact／source commit再利用など）だった。factorial proxyはpublic/native性能の証拠ではない。次に再開するのは同じvariantのblind retryではなく、(1)既存identityと重複しない許可済み新snapshot、または(2)複数runtime-safe source familyを構造的に生成する方法である。sourceごとのseat gap・lower-tailを分離し、fault0→独立positive→seat-safe（≤5%）→fresh DEV/FINALを満たした候補だけを`cg_bestknown_loop_v1.py`へ渡す。

最終目標（self-owned deck＋policyで実CABT勝率を主指標にBestKnownを更新し続け、独立seed・未使用metaで再現性を保ち、native上位72%級へ到達・安定超過する提出モデルを完成する）は未達。現行BestKnown、Champion、production、submission、commit、pushは不変である。詳細は`docs/evidence/cg-factorial-behavior-family-20260815.md`。

## 20. cross-snapshot behavior meta source / CEM（2026-08-15追記、最新）

新しいmeta source獲得・生成方法として、異なるsealed snapshotから各1件だけを取り、既存のexact visible-state transformを1回適用する `cross_snapshot_behavior_meta_v1` generator／CLIを追加した。4 base重複禁止、source commit 3種類以上、unknown family拒否、current pool/artifact policy identity重複拒否、static scan、fresh split、authority全falseを契約化している。

specは`configs/meta_specialist/cg_cross_snapshot_behavior_v1.json`。Alakazam 3件（`ABRA_FIRST`／`DUNSPARCE_FIRST`／`FEZANDIPITI_DRAW_FIRST`）とHydreigon/Comfey factorial 1件（`DECKOUT_AGGRESSIVE_COMFEY`）を、k4の異なる4 source commitから生成した。rootは`runs/cg-cross-snapshot-behavior-meta-20260815-w/`、pool SHA `7e61cd8df139d3bb3da4dbedc54b68d14d8ec06608a7b5a991c6cc8b87638bcb`、fresh SHA `d4a6600270a1c5fe69313f95ddc6a9052854732e511b5cffc4f8c9a4c424a788`、split SHA `2dd76b22ce06b5ad747f1b1070c3a240e86246203c7a953cad71d5f284cad030`。4件とも新規policy SHA、static findings 0、60枚、`local_eval_only`である。

META_TRAIN両seat smoke（base seed `20260946`、8局）は8/8 DONE・fault0。P1 control固定CEM（campaign seed `20260947`、population8、elite2、2世代、独立re-eval 2回）はscreen144＋re-eval96＋fresh DEV32＝272局を全てDONE・fault0で完走した。しかしgen0/1とも独立lower-tail／opponent×seat safe gate（≤5%）を満たさず、eliteは`incumbent-center`、P1 centerを保持した。gen1 fresh DEVの見かけの`+18.75pt`は同一P1 centerのRNG差であり、policy gainではない。META_FINALはCEM選抜・診断に使っておらず、未使用のまま保持している。

判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。現行P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submissionは不変である。次は同じtransformのblind retryではなく、重複しない許可済みsnapshot、構造的にboundedな別source family、またはfamily別lower-tailを推定できる混合poolを新epochで生成し、fault0→独立positive→seat-safe→fresh DEV/FINALを満たした候補だけを`cg_bestknown_loop_v1.py`へ渡す。一次evidenceは`docs/evidence/cg-cross-snapshot-behavior-meta-20260815.md`。commit、push、Kaggle submitなし。

## 21. stratified behavior meta v2 / CEM（2026-08-15追記、最新）

cross-snapshot v1のsplit偏りを修正するため、既存のexact visible-state transformをsource commit・base snapshot・derived policy SHAで重複禁止にし、splitとfamily coverageをspecへ明示する`stratified_behavior_meta_v2` generator／CLIを追加した。実装は`src/mage_ptcg/opponent_ingest/stratified_behavior_meta_v2.py`、CLIは`scripts/generate_stratified_behavior_meta_v2.py`、specは`configs/meta_specialist/cg_stratified_behavior_v2.json`、testは`tests/test_stratified_behavior_meta_v2.py`である。Metal runtime-safe variantは既使用だったためfreshness gateを緩めずHydreigon/Festival transformへ差し替えた。

sealed rootは`runs/cg-stratified-behavior-meta-20260815-v2/`。12件、12 distinct source commit／base candidate／policy SHA、TRAIN 8（Alakazam 2、Comfey 2、Festival 3、Psychic 1）、DEV 2、FINAL 2。pool／fresh／split／meta SHAは`f3655e62b24b9b1f4651f285c155d2eb30fa1b21b1b1b67b8759444a986954b4`／`e6e6cb22febe585e4380e9697e66cbc7272d899d9a3107e29151a1ec792fab8a`／`1736d834a0da9fdfa64176cd5587bbb66a5930574af50f94205d86e3fe05a65d`／`41ce070bdad79e9a897bc98a857f2927ac05f561aa48cc362ec36aea2f5a76dc`。

短い接続smokeは96/96 DONE・fault0。P1 control固定のcheap CEM（seed `20260962`、population8、elite2、2世代、独立re-eval 2 block）はscreen 288＋288、re-eval 192＋192を全てDONE・fault0で完了したが、両世代ともrobust positive／seat-safe candidate 0件でP1 centerを保持した。gen1 META_DEV診断は`14W-0D-18L`対`20W-0D-12L`、差`−18.75pt`、fault0。META_FINALはCEM中にidentity hit 0件で未使用のまま保持し、fresh FINAL confirmationは起動していない。

判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。現行P1 policy、root deck、BestKnown、Champion、production、submission authorityは不変。これはsource generationを安全にCEMへ接続できることの証拠であり、native/public性能の証拠ではない。一次evidenceは`docs/evidence/cg-stratified-behavior-meta-cem-20260815.md`。次は同poolのblind retryではなく、許可済み新snapshotまたはfamily別lower-tailを安定推定できる別compositionを新epochで生成し、通過候補だけを`cg_bestknown_loop_v1.py`へ渡す。

## 22. stratified behavior meta v2b / CEM（2026-08-15追記、最新）

v2のsource-generation契約を別compositionへ接続するため、`configs/meta_specialist/cg_stratified_behavior_v2_epoch_b.json`を追加した。Alakazam／Comfey／Festival／Psychicの12件を`runs/cg-stratified-behavior-meta-20260815-v2b/`へsealし、source commit／base candidate／derived policy SHAは各12 distinct、TRAIN 8／DEV 2／FINAL 2、各split 2 family以上、authority全falseである。pool／fresh／split／meta SHAは`e3474b0864b5d55302f7efea7f3b1c09ce7772f966c2d45a14f10ed53a304550`／`91f8a6ad8fc7d6bab8ae65e8b970c3f9e06c37d37c9d0000ae7374f336237dd9`／`867062ff515f028dd282d266f2d710abc5a9b5fbcab67cdd75b7c5fdf10faede`／`5a737a55751f1dadb1f9d25d3b3c0e4431310376c45f00245ba7818d4705dc07`である。

全12 referenceの両seat 24局smokeはDONE 24/24・fault0だったが、全pool指定のためMETA_FINAL 2件もsmoke投入済みとなり、fresh holdoutとしては無効化した。CEMはMETA_TRAINだけを使用し、FINAL identity hitは0件、FINAL confirmationは未起動である。次回はsmokeもTRAIN限定にする。

P1 control固定のcheap CEM（campaign seed `20260963`、population8／elite2／2世代、独立re-evaluation 2回、risk-aware／positive gate）はscreen 288＋288、独立192＋192を全てDONE・fault0で完了した。gen1 screen上位は`+25.00pt`／`+28.125pt`だったが、独立で`+18.75pt / −18.75pt`、`+12.50pt / −15.625pt`へ反転。robust positive／opponent×seat safe候補は0件、両世代ともP1 centerを保持した。gen1 DEV診断はcenter同士`14W-0D-18L`対`14W-0D-18L`、差0pt、fault0である。

判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submissionは不変。P2/P3、deck phase、`cg_bestknown_loop_v1.py`接続、commit、push、Kaggle submitは行っていない。一次evidenceは`docs/evidence/cg-stratified-behavior-meta-cem-20260815-v2b.md`。次は同一proxyのblind retryではなく、fresh holdoutをsmokeから分離した新しいpermission済みsnapshotまたは別recipeを生成する。

## 23. Rocket Theta Behavior Meta v2 / TRAIN-only CEM（2026-08-15追記、最新）

新しいmeta source生成方法として、受理済み `internal_ozawa-rocket-rule_de797c3646e9` の5 theta table全体へbounded numeric transformを適用する `rocket_theta_behavior_meta_v2` を実装した。12件を `runs/cg-rocket-theta-behavior-meta-20260815-a/` にsealし、pool SHA `cbb89bc59cfc500a5484c7007c876a8e53672ebd2397f1c128a4400077e44741`、fresh SHA `f89f830803c658387b94571029f109b2f2a6a272422a43b0c7953cd7adbc6d7b`、split SHA `029196b14f3d6338b2cf81d9c9aa3311478d571809edc3f409ce91ba79a37830`、meta SHA `74e4a329bbd52610bcc7a1f85cace5061ae7e9498c301ae4e5133a42cced9072`。TRAIN 8／DEV 2／FINAL 2、authority全false、P1／root deck／BestKnown不変である。

TRAIN-only smokeは16/16 DONE・fault0。P1 control固定CEM generation 0（population16、elite2、campaign seed `20260882`、independent re-evaluation 2回）はscreen544／independent192をDONE・fault0で完走したが、screen上位`+12.50pt`／`+9.375pt`は独立`−9.375pt`／`−1.5625pt`、worst`−15.625pt`／`−3.125pt`へ反転し、seat-safe／opponent×seat-safe false。positive gateを満たさずcenter保持。DEV/FINAL未使用、generation 1未実行。判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。一次evidenceは `docs/evidence/cg-rocket-theta-behavior-meta-v2-20260815.md`。次は同一Rocket proxyのblind retryではなく、許可済み新snapshotまたはfamily別lower-tailを推定できる別compositionを新epochで生成する。

## 24. Rocket Specialist Route Meta v1 / TRAIN-only CEM（2026-08-15追記、最新）

Rocket Theta v2のnumeric transformを再試行せず、同じ受理済みRocket sourceのspecialist dispatch route tokenだけを厳密に再配置する `rocket_specialist_route_meta_v1` を実装した。実装は`src/mage_ptcg/opponent_ingest/rocket_specialist_route_meta_v1.py`、CLIは`scripts/generate_rocket_specialist_route_meta_v1.py`、configは`configs/meta_specialist/cg_rocket_specialist_route_v1.json`。12件（TRAIN 8／DEV 2／FINAL 2）を`runs/cg-rocket-specialist-route-meta-20260815-b/`へsealし、pool／fresh／split／meta SHAは`dcab93e7b948a6449a48c5e33b8b9836bf3356bd0869fc828095649fce632289`／`db1c41c7a86bb018ef74597e68767622fc648a1f25ef17fcb5ec8528838765dd`／`a32662471c51718146ac0eee838a05ecafd8e5cbee72af398df73b7661be19b1`／`946701cd718f02b252ce5fe5790ba244f7568ac1ff5462ce6f63bce26015a6f1`。authority全false、static／compile／loader／focused testsはPASSである。

TRAIN 8件だけのP1 smokeは16/16 DONE・fault0・draw0（2W-0D-14L）。P1 control固定CEM generation 0（population16、elite2、campaign seed `20260884`、独立再評価2回）はscreen544／independent192をDONE・fault0で完走したが、screen上位`+12.50pt`／`+9.375pt`は独立re-evalで`+3.125pt / 0pt`、`+9.375pt / 0pt`へ反転し、lower-tail 0、seat-safe／opponent×seat-safe false。positive gateを満たさずP1 center保持。DEV／FINAL／generation 1未使用。判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。一次evidenceは`docs/evidence/cg-rocket-specialist-route-meta-v1-20260815.md`。次は同一Rocket proxyのblind retryではなく、相関を下げた許可済み新snapshotまたは別family compositionを新epochで生成し、通過候補だけを`cg_bestknown_loop_v1.py`へ渡す。

## 25. Rocket Dispatch Classifier Meta v1 / TRAIN-only CEM（2026-08-15追記、最新）

Rocket Theta／route tokenの再配置ではなく、受理済みRocket sourceの公開card ID classifier `_TIER_A_TO_GROUP` の既存13 keyのfamily valueだけを変える12 variantを生成した。実装は`src/mage_ptcg/opponent_ingest/rocket_dispatch_classifier_meta_v1.py`、CLIは`scripts/generate_rocket_dispatch_classifier_meta_v1.py`、configは`configs/meta_specialist/cg_rocket_dispatch_classifier_v1.json`。rootは`runs/cg-rocket-dispatch-classifier-meta-20260815-c/`、pool／fresh／split／meta SHAは`b3ccdec6e68bfebe78ba55d1b859432d022f1aa17c5dc21320d47355c549664d`／`294e2157f7407d16d18785a6ed865bbc050b4fd8adf08da96b9b9ccaa5112e51`／`9749aa51b6c1941ad81c53642b7e716117ced5f15b96362329d1d39ef3bdd482`／`cdcf280c151895c9aceacb568a4f31f1a0aac15b4bbf75c9a189eaceed58733a`。TRAIN 8／DEV 2／FINAL 2、authority全false、static／compile／loader／split PASSである。

TRAIN-only smokeは16/16 DONE・fault0・draw0（1W-0D-15L）。P1 control固定CEM generation 0（population16、elite2、campaign seed `20260886`、independent re-evaluation 2回）はscreen544／independent192をDONE・fault0で完走した。screen上位c04の`+12.50pt`は独立平均`−15.625pt`へ反転し、c10は独立平均`+1.5625pt`でもworst block `0pt`、opponent×seat gap `75pt`。robust positive／seat-safe／opponent×seat-safe候補0件、P1 center保持。DEV／FINAL／generation 1未使用。判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。一次evidenceは`docs/evidence/cg-rocket-dispatch-classifier-meta-v1-20260815.md`。次は同一Rocket proxyのblind retryではなく、相関を下げた許可済みsnapshotまたは複数runtime-safe familyの別compositionを生成し、通過候補だけを`cg_bestknown_loop_v1.py`へ渡す。

## 26. Rocket Dispatch Confidence Meta v1 / TRAIN-only CEM（2026-08-15追記、最新）

Rocket sourceのdispatch commit条件へ、公開family evidenceのturn履歴・multi-card確認を加える12 variantを `runs/cg-rocket-dispatch-confidence-meta-20260815-d/` にsealした。pool／fresh／split SHAは`78b2118fbc2d537f4cc3c7e7f65a3657878dc6495491f23056ea0394c9cefdd0`／`0aace92bd1be10270e5fb59355a936069de1d06c5ae1d74e9e4837960a1d4850`／`01099af99abcb77e6b7922eea382410763b9bb605201613b9afb64ccc90fe09f`。TRAIN 8／DEV 2／FINAL 2、全件static／compile／loader PASS、authority全falseである。

TRAIN smokeは16/16 DONE・fault0（2W-0D-14L）。P1 control固定CEM（population16、elite2、campaign seed `20260889`、independent re-evaluation 2回）はscreen544／independent192をDONE・fault0で完走した。screen elite c01/c09は各`+3.125pt`だったが、独立で`−1.5625pt`／`−4.6875pt`へ反転し、robust positive／seat-safe候補0件。P1 center保持、DEV／FINAL未使用。一次evidenceは`docs/evidence/cg-rocket-dispatch-confidence-meta-v1-20260815.md`。

## 27. Water Box runtime-safe Meta v1 / TRAIN-only CEM（2026-08-15追記、最新）

slow/quarantineだった`opponents/waterbox_search_v3`を、探索停止・極小予算・周期gateだけのresearch opponentへ変換した。初回probe eは周期variantの実行時間測定、fは既使用hashのsplit再配置として採用せず、gで新規予算帯hash群をsealした。rootは`runs/cg-waterbox-runtime-safe-meta-20260815-g/`、pool／fresh／split SHAは`1179ac28d253f892be3acf651c9f802575794b74f98e156a83a67006c76281ed`／`54c84a50f65f834ae2a92f5027b106b6134c0c7c8dbfed7904cc7031ff4f4be5`／`9acebe5e9431e3a7ad9770377242c670097f9e91d7c870dbe201ba475e2553b2`。TRAIN 8／DEV 2／FINAL 2、static／compile／loader PASS、authority全falseである。

TRAIN smokeは16/16 DONE・fault0（4W-0D-12L、runtime46.64秒）。P1 control固定CEM（population4、elite1、campaign seed `20260912`、independent re-evaluation 2 block）はscreen160／independent64をDONE・fault0で完走した。screen top c02の`+21.875pt`は独立`+6.25pt`／`0pt`（mean`+3.125pt`、worst`0pt`）へ縮小し、seat-safe=falseのためP1 center保持。DEV／FINAL未使用。一次evidenceは`docs/evidence/cg-waterbox-runtime-safe-meta-v1-20260815.md`。

この2系統はいずれも`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submissionは不変。次のmeta sourceは同一Rocket／Water Box proxyのblind retryではなく、相関の低いpermission済み別sourceまたは複数runtime-safe familyの混合poolを生成し、fault0→独立positive→seat-safe→fresh DEV/FINALの順で評価する。`cg_bestknown_loop_v1.py`へはgate通過候補だけを接続する。

## 28. 公開Kaggle kernel source intake discovery（2026-08-15）

既存poolとinternal remote/historyのsource identityがほぼ消費済みのため、公開Kaggle kernelを読み取り専用で取得してlocal-eval meta sourceへ隔離する方式を調査した。未収載候補 `tetsutani/grimmsnarl-ex-damage-transfer-control` の `submission.tar.gz` を `runs/cg-kaggle-kernel-intake-20260815-tetsutani-a/raw/` に保存し、tar SHA `04f9779b77d17417570189d06a1b7ff5b0016797639a2a45f4b53bc02e945712`、元 `main.py` SHA `c61e540bcb45aa2e8184ae912e7e17efaa900dba3df4536468da41899b09dcd8`、元 `deck.csv` SHA `92b92bac9f9163ecff933b3dc39294d2cc154c8684f3c8497877661419ebc59d`、canonical deck SHA `cafa7652a6349be806d8ac2b9abfdb6c72ca3821f368e0d912e2d989f3b54cdd` を固定した。現行102-row pool／既存artifactとのexact identityはread-only照合で未検出である。

tarにはpolicy payload、model binary、`EN_Card_Data.csv`、bundled `cg/`が含まれる。shared engine parityのため`cg/`と提出archiveを除外し、候補横wrapperからpayloadを隔離importする設計が必要である。payload全Pythonの静的監査では、bundled `cg`由来の`ctypes`以外にnetwork/subprocess/dynamic import/filesystem writeは検出されなかった（`list.remove`等のコンテナ操作は誤検出除外）。

まだpool/fresh/split seal、両seat CABT smoke、CEM、DEV/FINALは未実施であり、P1、BestKnown、Champion、production、submissionは不変である。公開kernelを`local_eval_only`の研究sourceとして明示許可した場合だけ、`tar取得→safe member展開→bundled cg除外→wrapper/AST safety→fault0 smoke→fresh 8/2/2 split→P1 CEM`へ進む。training、public/native teacher、submission bundle、Kaggle送信、外部再配布には使わない。一次evidenceは`docs/evidence/cg-kaggle-kernel-intake-discovery-20260815.md`。

## 29. Kaggle public kernel meta intake v1 実行結果（2026-08-15追記、最新）

公開kernel sourceを取得・生成する方法を実装し、上記discoveryを実CABT入力へ接続した。実装は`src/mage_ptcg/opponent_ingest/kaggle_kernel_meta_v1.py`、CLIは`scripts/generate_kaggle_kernel_meta_v1.py`、configは`configs/meta_specialist/cg_kaggle_kernel_meta_v1.json`、focused testは`tests/test_kaggle_kernel_meta_v1.py`である。tar SHA／path・link・容量／AST safety／module隔離／shared `cg` ancestor resolution／fresh identityを固定し、bundled `cg`は除外した。

5件（tetsutani Grimmsnarl、jazivxt Alakazam/Crustle/Garchomp、prvsiyan Grimmsnarl v21）を`runs/cg-kaggle-kernel-meta-intake-v1b-20260815/`へsealした。pool SHA `0de2046dac59b826faf314a9a8a3012fa388cdff6922488221a8908c39074f99`、fresh SHA `92a110c3412f3b6d7dfde8ea0e4674560028ff9be9ee2853d4487c0fd49ff788`、historical meta SHA `dfef2207809fe6ebbcf2df8c2cda82f6bde43ccf83a601a8a0f391e42db51000`、split SHA `2570d31b37614a8a94a6195cbd8507f88336eb9d2ec336d1f3173f09d3255e31`。splitはTRAIN 3／DEV 1／FINAL 1で、5件ともstatic／exact 60／loader preflight PASS、authority全false、`local_eval_only`である。

P1（policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）をTRAIN 3件へ両seat各1局でsmokeした。6/6 DONE、fault0、draw0、P1 0W-0D-6L。DEV／FINALはsmokeにも未投入である。

P1固定CEMは`runs/cg-kaggle-kernel-meta-cem-v1b-20260815/`、population4、META_TRAIN_ALL 3件、2 repetition/seat、campaign seed `202608151`、positive-delta gateを使用した。screen 60/60 DONE・fault0、candidate 4件は0W-0D-48L、controlは1W-0D-11L。valid elite 0件で`not enough valid candidates for elite update: 0 < 1`により停止し、独立re-evaluation、DEV／FINAL、generation 1は起動していない。判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。P1、BestKnown、Champion、production、submissionは不変である。

この5件はP1に対する強いhard-negative sourceとして有用だが、公開kernel scoreやnative/public性能の証拠ではない。同じ5件のblind retryはせず、次は相関の低い安全なpublic kernelを追加してdeck/policy familyを広げ、P1 baseline separation→fault0→独立positive→seat-safe→fresh DEV/FINALの順で`cg_bestknown_loop_v1.py`へ接続する。一次evidenceは`docs/evidence/cg-kaggle-kernel-meta-intake-v1-20260815.md`。

## 30. Kaggle public kernel meta intake v2c–v4 / CEM no-update（2026-08-15最新）

公開kernel sourceを `tar SHA → safe extraction → bundled cg除外 → AST safety → wrapper/loader → exact 60-card/canonical deck → freshness evidence` の順に隔離する方式を拡張した。v2cは `kaggle_dashimaki360_crustle_20260815`、`kaggle_jazivxt_alakazam_rising_tide_20260815`、`kaggle_pixiux_lucario_20260815`、`kaggle_plamen06_steel_20260815`、`kaggle_prvsiyan_meta_router_20260815` の5件をsealした。pool／fresh／split SHAは `fd3755e7f7be013d289b0f464c0770523d31b9756e370a40441fe90f9ecb25d9`／`a7156f85d196b17f7212e0a7e1e02519268b8453a74e1d24295bc2021249ecde`／`614211b79c1c801b8d866312570c3fe8f0452b1a5e4ee8c5d232b56b92aa38da`。TRAIN-only smokeは10/10 DONE・fault0だった。v3はTRAIN接続smoke 4局中2局DONE・2局AGENT_ERRORでbatch quarantine、v4は2/2 DONE・fault0。v3/v4ともCEM／DEV／FINAL未実施で、未使用holdoutを保全している。

v2c P1固定CEM（population4、elite1、campaign seed `202608152`）は60/60 DONE・fault0。小標本で4 candidate全てがseat-collapse gateによりinvalidとなったが、runnerのno-update修正後retryは `COMPLETE`、`valid_screen_candidates=0`、elite空、P1 center保持、Champion／submission不変として `results.json` とcheckpointを封印した。初回の `rank_valid_results` 例外停止は再現しない。これは性能改善ではなく、source→CEMのfail-closed接続証拠である。

取り込みのfreshness検査はlegacy `policy_hash`、過去intake artifact root、policyとdeck identityの混同を修正済み。次の実行はv3/v4 TRAIN-only smokeから開始し、fault0→独立positive→seat-safe→未使用DEV/FINALを満たす candidate のみ `cg_bestknown_loop_v1.py` へ渡す。一次evidenceは `docs/evidence/cg-kaggle-kernel-meta-intake-v2-v4-20260815.md`。P1、BestKnown、Champion、production、submission、commit、pushは不変である。

## 31. Kaggle public kernel meta intake v5–v6 / merged CEM（2026-08-15最新）

新しいmeta source供給を継続し、v5は3件、v6は2件を受理した。v5 smokeは6/6、v6 smokeは4/4 `DONE`・fault 0。入力sealed poolを上書きしない `promote_historical_meta_smoke_v1.py` と、複数batchをsource ID重複検査付きで束ねる `merge_historical_meta_smoke_v1.py` を追加し、helper testsは4 passed。これはsource→CABTの境界を壊さず、freshnessとsmoke証跡を別hashで保持するための実装である。

merged rootは `runs/cg-kaggle-kernel-meta-merged-fg-smoke3-20260815/`。pool／fresh／split／meta SHAは `2820e5d58ad97de9b6a590c342af015c724d248515c64482ac1b816b1e6efac5`／`839b42fadeff241f6eaba4be0712882ef66592386d7494a81c2d452517b83e63`／`95bc2bd113b8260df44620e7bd4b7a21963bd57d882e19739268a01fb78efd02`／`498cc9c6a53bfba5eb9ad553350dd3fcda86107a774e13aad0eeb641a10aae7e`。TRAINはPixiux／Ryota／Skarin、DEVはYaroslav、FINALはZoli。FINALはCEM探索には読んでいないが、Zoliはv6 source smoke済みなので、真のsmoke-untouched holdoutではない。

P1固定CEM（population4、elite1、1世代、campaign seed `202608157`、all train refs、positive delta gate、risk-aware update、独立re-evaluation 2回）は60/60 `DONE`・fault 0、W/D/L `5/0/55`。candidate 4件は全て`seat_collapse=true`でinvalid、elite空、P1 center保持。したがって判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL` であり、P2/P3、deck phase、BestKnown update、Champion変更は行っていない。META_DEV／META_FINALは未使用のまま保全している。

次の研究主線は、同一公開kernelのblind retryではなく、相関の低い許可済みsourceの追加取得・生成である。次batchではTRAIN-only smokeを先に行い、DEV/FINALはsmokeから分離する。source単位のfault0 smoke、独立seed複数block、seat-safe、未使用DEV/FINALを順に通過した候補だけを `cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` に渡す。最終目標（self-owned deck＋policyで実CABT勝率を主指標にBestKnownを更新し続け、native上位72%級へ到達・安定超過する提出モデル）は未達。P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変である。一次evidenceは `docs/evidence/cg-kaggle-kernel-meta-intake-v5-v6-20260815.md`。

## 32. Kaggle public kernel meta intake v7–v9 / source-generation priority（2026-08-15最新）

v7–v9で公開kernel sourceの追加取得を実施した。v7はRaunak advanced heuristicだけがruntime-safe subsetとして昇格し、Mega EmboarはEnergy Search Pro（ACE SPEC）4枚で`AGENT_INVALID`。v8はFaheem Dragapult／Prvsiyan Alakazam v10／v9を6/6 `DONE`・fault0でsealed、v9はPrvsiyan control v11を2/2 `DONE`・fault0でsealedした。v8 CEM v8bはscreen 144、independent 48、DEV/FINAL診断32、合計224局をfault0で完走したが、独立positive／seat-safe gate未達でP1 center保持。詳細は `docs/evidence/cg-kaggle-kernel-meta-intake-v7-v9-20260815.md`。

v8 FINALはすでに診断へ投入済みであり、新campaignの未使用FINALではない。v7 Raunak＋v8 3件＋v9 controlをmergeした5-reference poolは存在するが、v8 3件は性能評価済み、v7/v9はsmoke済みである。全5件を未使用metaとして再利用してはならない。

intakeにはローカル公式card catalogのACE SPEC枚数ゲートを追加した。catalogが利用できる場合はexactly one ACE SPECを要求し、違反を`invalid_ace_spec_count`でCABT前に拒否する。source intake test 11 passed、promotion subset 3 passed、CEM focused 30 passed。

## 35. 現状報告と次のmeta-source設計候補（2026-08-15追記）

現行BestKnownはself-owned cg P1＋root deckで不変。P1 policy SHAは`1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは`2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。branch／HEADは`feature/belief-guided-search`／`30cade0e5d349d6ea545f019fc411e9d53288f16`、確認時点でactive heavy processはない。current opponent poolは102 rows（public 71／internal 31）、smoke-readyは101 rowsである。作業ツリーには既存の未コミット差分があるが、今回の報告で整理・削除していない。

直近の`CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1`と`FAILURE_CONDITIONED_PUBLIC_COUNTERPRESSURE_V1`は、いずれも静的合法性とbounded CABT接続を通過した一方、独立positive／seat-safe／opponent×seat-safeを満たさずP1を保持した。公開kernel v7 Raunak、v9 Prvsiyan control、v4 Koushikrudraは未CEM候補として残る。v8の3件、過去のRocket／behavior-family transform、同一P1-base adapterは既に性能使用または診断済みで、新しい未使用holdoutとは扱わない。詳細は`docs/evidence/cg-current-state-report-20260815-b.md`。

次の設計候補は、v4／v7／v9の未CEM親policyを、turn・公開active／bench card ID・stadium等の決定的なactor-visible bucketで切り替える`actor-visible routed ensemble source`である。expert/action label、相手の非公開情報、future RNGは使わず、親payloadのstatic scan、exact 60、ACE SPEC exactly one、runtime budget、parent SHA／deck SHA／routing recipe、freshness evidenceをhash-boundに封印する。生成poolは初期`smoke_ok=false`とし、TRAIN-only bounded smoke→独立複数block→seat-safe／opponent×seat-safe→未使用DEV→未使用FINALを通過した候補だけ`cg_bestknown_loop_v1.py`へ渡す。

この設計の実装、CEM、DEV／FINAL測定、BestKnown loop接続はまだ開始していない。P1、BestKnown、Champion、production、submission、`opponents/`、commit、pushは不変である。設計承認前にheavy runを再開しない。

## 36. Actor-visible routed ensemble source / CEM（2026-08-15追記）

前節の設計候補を実装へ進め、`ACTOR_VISIBLE_ROUTED_ENSEMBLE_V1`を実証した。未CEMの公開kernel parent v4 Koushikrudra、v7 Raunak、v9 Prvsiyan controlから、公開状態（turn、yourIndex、active／bench card ID、stadium、selection context）だけを使う4つのrouted policy×deck candidateを生成した。expert/action label、相手の非公開情報、future RNG、network accessは使っていない。実装／CLI／再bind／testsは`src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`、`scripts/generate_routed_ensemble_meta_v1.py`、`scripts/rebind_routed_ensemble_split_v1.py`、`tests/test_routed_ensemble_meta_v1.py`。

generated root `runs/cg-routed-ensemble-meta-20260815-a/` のpool SHAは`aae831cd7c12904499e097e4d9e729dccd4470442f7133b30255fede0e79b403`、fresh SHAは`6e058fdea6a90fb0807dc046d2d1df9d629c09aeb4c0cfcd95528c7f088846d7`。P1両seat smokeは8/8 `DONE`・fault0、promoted pool SHAは`e9aa6b129964e41afb6125311db891efaddd0d3e80af8ab61d94a08127218d93`、rebound split SHAは`ff22d2efe41bda990456a8ec7c9680bb83bf61b116fa5520692a4800bc4f66e5`。ただし4候補すべてをruntime smokeしたため、FINALは性能未使用だがsmoke-untouchedではない。

P1固定CEM `runs/cg-routed-ensemble-cem-20260815-a/` はcampaign seed`20261002`、2世代、population／elite`8／2`、META_TRAIN 2件、独立2 block、positive／risk-aware gateでscreen `72+72`、独立`48+48`、DEV`32`、合計272 rowsをfault0で完了した。gen0独立差は正でもseat-safe／opponent×seat-safe false、gen1は`−12.50pt / −12.50pt`へ反転したため、両世代ともP1 centerを保持。fresh DEV center差は`−18.75pt`。robust positive candidateは0件で、FINAL performance confirmation、`cg_bestknown_loop_v1.py`、deck phase、BestKnown変更は行っていない。

判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。次は同じv4/v7/v9 parent・routing recipeのblind retryをせず、相関の低い新parent source、またはruntime smokeと性能holdoutを完全に分離できる新compositionを生成する。詳細evidenceは`docs/evidence/cg-routed-ensemble-meta-cem-20260815.md`。

### CANONICAL OVERRIDE

このpackと実repoの最新一次artifactを正典とする。古いRule v0探索、Student/AWR/BC、deck blind sweep、既評価policy surfaceのblind retryは再開しない。次の最優先は新しいmeta sourceの獲得・生成方法そのものの設計であり、新recipeが `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe → unused DEV → unused FINAL` を通過するまで `cg_bestknown_loop_v1.py` のheavy policy→deck→policy loopを起動しない。

第一候補は、P1 CABTのactor-visible stateとterminal outcomeからhard-negative stateを抽出し、private情報を使わないfailure-conditioned self-owned adversarial policy adapterを生成すること。ただし、CABT outcomeをexpert labelとして直接学習せず、recipe／lineage／fresh split／hashを先にsealed artifactへ固定する。source recipeが通過した場合だけ、P1を親に `policy CEM → fresh validation → deck → policy` を再開する。P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変である。

## 33. Cross-lineage meta source recipe / CEM v1（2026-08-15最新追記）

新しいmeta source生成方式 `CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1` を実装した。smoke済みsealed candidateからpolicy parentとdeck parentを別々に選び、同一lineageを除外し、repository-owned wrapperを新candidate IDへ再生成する。exact 60、公式card ID、ACE SPEC exactly one、payload static safetyを先に検証し、生成poolは`smoke_ok=false`、bounded CABT smoke → promote → split rebind後にのみCEMへ接続する。実装は `src/mage_ptcg/opponent_ingest/cross_lineage_meta_v1.py`、CLIは `scripts/generate_cross_lineage_meta_v1.py`、再bindは `scripts/rebind_cross_lineage_split_v1.py`。

v1: generated root `runs/cg-cross-lineage-meta-v1-20260815/`、promoted root `runs/cg-cross-lineage-meta-promoted-v1-20260815/`。generated/promoted pool SHAは `e8a94ae352df0b4a0506b6e79f1b81c412cb7c4ee54570363e409f32a7ee7bdb`／`611b3e1bd2ccbffc655dea39a6c9ed16cc3842010c03caff98100cf8362c8a5f`、promoted fresh meta SHA `7284a36278cf3d8ff2d888a5966cfe54ee5fab6897869cdc9c5232d1e211985f`、rebound split SHA `a29482721e319fd55a40de9c199eb61cfb6bc55e204451bb8a689a79c4234742`。4候補、両seat smoke 8/8 DONE、fault0。Faheem deck parentはv8 CEMで既に性能使用済みなので該当候補は新しいpair identityとしてのみ扱い、parent deck未使用とは主張しない。

`runs/cg-cross-lineage-cem-v1-20260815/` で P1 control固定、META_TRAIN 2／DEV 1／FINAL 1、population12、2世代、independent re-evaluation 2 block、positive/risk-aware gateを実行した。304 rowsは全てDONE・fault0。gen0 screen上位 `+37.50pt` → independent `−12.50pt`／worst `−25.00pt`、gen1上位 → independent `−25.00pt`、最良独立候補はmean/worst `0pt`。robust positive・seat-safe候補0件、P1 center保持、FINAL未使用。判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`、P1／root deck／BestKnown／Champion／production／submission不変。一次evidenceは `docs/evidence/cg-cross-lineage-meta-cem-20260815.md`。

次の実行で旧方針へ戻らない。cross-lineageの同一pair blind retryを避け、未性能使用policy parentを優先した新batch、またはactor-visible failure-conditioned self-owned adapterとの混合sourceを設計する。再開ゲートは `legality → static → bounded fault0 → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL → cg_bestknown_loop_v1`。

## 34. Actor-visible failure-conditioned self-owned adapter / CEM v1（2026-08-15最新）

新しいmeta sourceの獲得・生成方法として、P1 CABTのaggregate terminal outcomeから、private情報やexpert/action labelを使わずに相手側challenge policyを生成するrecipeを実装した。正典recipeは`FAILURE_CONDITIONED_PUBLIC_COUNTERPRESSURE_V1`、実装は`src/mage_ptcg/opponent_ingest/self_owned_failure_adapter_v1.py`、CLIは`scripts/generate_failure_adapter_meta_v1.py`、smoke後のsplit再bindは`scripts/rebind_failure_adapter_split_v1.py`。4件のpublic-state adapter（KO finish、survival retreat、自己active damage counterpressure、相手active damage tempo）はsealed P1 scorerへ委譲し、各々新しいpolicy SHAを持つ。同じroot deckを使うため、親未使用ではなくpair-level fresh identityとして扱う。

generated root `runs/cg-failure-adapter-meta-v1-20260815/` は4件、exact 60、公式card ID、ACE SPEC exactly one、static findings 0、authority全false、初期 `smoke_ok=false`。pool SHA `fa01fb4882f6bbd4e9569a262430b8cdf4def47eef69421c68e374d2c58bfd28`、fresh meta SHA `9bc0213edadd941d9c348b9cc758bc8151b6862209b89a1215e2c24d0427ff80`。P1対4候補を両seat各1局smokeし、8/8 DONE・fault0・draw0（P1 6W-2L）。promote後pool SHA `369daf3ff9db77361734e52fb41dab9ec45daffd8f73e30c853882e9b6c91892`、rebound split SHA `f2bd6deadea48ab0e91e6aa642f135b2780a67f5ceedcc321a94c71a1146944a`。

`runs/cg-failure-adapter-cem-v1-20260815/` でP1 fixed CEMを実行した。META_TRAIN 2／DEV 1／FINAL 1、population12、elite3、2 generations、seed `20260901`、screen各opponent/seat 2局、independent re-evaluation 2 block、positive/risk-aware gate。gen0 136局＋gen1 168局＝304/304をfault0で完走（gen1 evaluation draw 1）。screen topは独立seedで反転するかseat/opponent×seat gate外で、robust positive・seat-safe候補0件。P1 center保持、META_DEV center差`−12.50pt`・seat gap`12.50%`、META_FINALは未使用のまま保持した。判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。

### CANONICAL OVERRIDE

このpackと実repoの最新一次artifactを正典とする。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価policy surfaceや同一P1-base adapterのblind retryは再開しない。最終目標は、self-ownedかつ確実に提出可能なdeck＋policyで、実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、最終的にnative上位72%級を安定して超える提出モデルを完成させることである。

次の最優先は、未性能使用policy parentを含む新source、複数runtime-safe familyを相関管理した混合pool、または新規permission済みsourceの獲得・生成である。recipeが`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を通過するまで、`cg_bestknown_loop_v1.py`のheavy policy→deck→policy loopを再開しない。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submission、commit、pushは不変である。詳細evidenceは`docs/evidence/cg-self-owned-failure-adapter-cem-20260815.md`。

## 35. Actor-visible semantic routing repair / CEM（2026-08-15最新追記）

v4／v7／v9 parentを公開状態のdamage、visible bench size／appear、threat contextで切り替える3 recipeを追加した初回epoch `runs/cg-routed-ensemble-meta-20260815-b/` は、P1 smoke 8局中6局`AGENT_ERROR`でquarantineした。CABT公開状態で根因を再現した結果、empty bench listが`or ()`でtupleになりactive listと連結できないwrapper bugだった。parent policy／engineの性能証拠には昇格しない。

tuple正規化とempty-bench回帰テストを入れた修正版 `runs/cg-routed-ensemble-meta-20260815-c-fix/` はpool／fresh／split SHAが `487db2fd945096cddf990fa8bcce88c4ff781082e2b9381a30876723d7a1659b`／`48f8f8ff5783bf62417d7fcae8aabf3f3e54eabe09129561dc3462ad29ee065e`／`73aee9f4a2a2b8bc9d35320048b98391c935bcdfa2ba119f32935ba8ece17f6d`。P1 smoke（base seed `20261011`、8局）は`DONE=8/8`、fault0、draw0、P1 `1W-0D-7L`。promoted pool／rebound split SHAは `8597484b9e85ab31834a0c322d0a334ecda0a44a2a6f14769296509eba9fc4bd`／`2dcb4a8690d44e4a511fab2cf2cfa6aae13c2c53e2d1983b20a8a42f6ed45081`。

修正版poolのP1固定CEM `runs/cg-routed-ensemble-cem-20260815-c-fix/` はseed `20261012`、1世代、population／elite `8／2`、META_TRAIN 2件、screen 72局をDONE・fault0で完了したが、valid candidate `0/8`、elite空、P1 center保持。独立re-evaluation、DEV、FINAL、`cg_bestknown_loop_v1.py`接続、deck phase、BestKnown変更は未実行である。判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。詳細evidenceは`docs/evidence/cg-routed-ensemble-meta-cem-20260815.md`。P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変。

### CANONICAL OVERRIDE（最新）

上記c-fix結果を最新一次artifactとして扱う。同じv4／v7／v9親の再組合せを繰り返さず、次の最優先は相関の低い新parentまたは新規permission済みmeta sourceの獲得・生成である。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全て通過するまで、`cg_bestknown_loop_v1.py`のheavy policy→deck→policy loopを起動しない。

## 37. 新規 source acquisition：adversarial source CEM と同一deck routed pair（2026-08-15最新）

前節以降、次の meta source 生成方法を実測した。P1／root deck／BestKnown／Champion／production／submission／commit／pushは不変である。

- self-owned adversarial source CEM `runs/cg-adversarial-source-cem-20260815-b/`: screen 64、独立 validation 16、全てDONE・fault0。validationは`10W-0D-6L`、seat0 `75%`／seat1 `50%`、seat gap `25%`で`seat_safe=false`、promoted sourceなし。P1 surfaceの相手化だけでは安定したseat-safe meta sourceを得られない。
- 異種deck routed epoch a `runs/cg-adversarial-route-meta-20260815-a/`: 8/8 fault。Faheem payloadがimport時に自分の`deck.csv`を探す`StopIteration`であり、heterogeneous parentと単一wrapper deckの契約不整合だった。性能結果へ昇格しない。
- 同一deck routed epoch b: Prvsiyan Alakazam v10／control v11（canonical deck SHA `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb`）から4候補を生成。promoted pool `runs/cg-adversarial-route-promoted-20260815-b/`のpool SHAは`3768e6faea58c81b39ec9ffe9e9c393162ec7c4d1d01f1ee8c003abd04cf9b`、rebound split SHAは`575cf0fbe6c70cdfd508141caa52aea5c1fbbb7a859ccbb49600eef62f8b6d2f`。P1 smokeは8/8 DONE・fault0、P1 `0W-0D-8L`。
- 独立384局確認 `runs/cg-adversarial-route-confirm-20260815-b/`: DONE 384/384、fault0、P1 `21W-0D-363L`（5.46875%）。新metaがP1に対して強いchallenge sourceであることは再現したが、4候補全て確認済みで同pool内のDEV／FINALは未使用holdoutではない。
- P1 policy CEM `runs/cg-adversarial-route-cem-20260815-b/`: screen 40/40 DONE・fault0。4 candidate全て`seat_collapse=true`／`valid=false`、positive gateでP1 center保持。独立re-evaluation、DEV、FINAL、`cg_bestknown_loop_v1.py`接続、deck phaseは未実行。

実装・検証の一次evidenceは`docs/evidence/cg-adversarial-route-meta-source-20260815.md`。次の正しい起動条件は、今回のpoolをholdoutへ再利用せず、別の未使用policy lineageまたは新しいdeck-conditioned sourceを生成して、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を通過することである。通過前にheavy policy→deck→policy loopを再開しない。

### CANONICAL OVERRIDE（source acquisition最新）

最終目標は、self-ownedかつ確実に提出可能なdeck＋policyについて、実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、最終的にnative上位72%級を安定して超える提出モデルを完成させることである。現時点のBestKnownはP1＋root deckのままであり、今回のrouted poolは強いchallenge sourceだがP2／BestKnown candidateではない。異種deck parentを同じwrapper deckへ混ぜず、全ゲート通過前に`cg_bestknown_loop_v1.py`を起動しない。

## 38. 別deck-family routed source e-fix / P1 CEM（2026-08-15最新）

次のmeta source生成方法として、Skarin／Zoli Dragapultの同一 canonical deck parent pairをactor-visible routed ensembleへ接続した。初回 `runs/cg-adversarial-route-meta-20260815-e/` は8/8 faultだったが、原因は親payloadがimport時に要求する`deck.csv`を隔離wrapperへコピーしていなかった生成器のasset contract defectである。異なるcanonical deckの組合せを生成前に拒否し、親deckを`parent_a/`／`parent_b/`へ同梱する修正をTDDで追加した。旧artifactはquarantineし、削除・改変していない。

修正版 `runs/cg-adversarial-route-meta-20260815-e-fix2/` は4候補をsealし、generated pool／fresh／split SHAは `888f2325a80b91a1dde54cf83ca613007bea032f74d75f5f5040e544aafc8291`／`b0b0562aca82d35de57dda3d6154121585c8a84b0cbdef91f0a99e530b9abafb`／`747f20f4174b60509a25f88d4cb6fe84eec153db0b95a1b92d605017a97d1647`。P1両seat smokeは8/8 `DONE`・fault0・draw0・`2W-0D-6L`。promoted pool／fresh／rebound split SHAは `ea71e13bc89dcc9cc634c8bc520f4e05c8895aebeef0c1614eae1554761e2e0d`／`4e0cdee6bf9dd24607c8d336f59cd2c657c2eb497cc215428e18f7197d56db90`／`d3cd0221f4c3f7dc34811cb5fea495fce8495ba002fd4b507e6b6fa0dbade761`。splitはTRAIN 2／DEV 1／FINAL 1、FINALは未使用で保持した。

P1固定CEM `runs/cg-adversarial-route-cem-20260815-e-fix2/` はcampaign seed `20260882`、population／elite `4／1`、2世代、META_TRAIN_ALL、独立再評価1 block、positive-delta gateで全row fault0。gen0の候補 `cg-p1-cem-g00-c02-d892b7a55419` は独立 `6W-0D-10L` vs control `4W-0D-12L`（差 `+12.5pt`）だったが、gen1 DEVでは同centerが `3W-0D-13L` vs control `5W-0D-11L`（差 `-12.5pt`）へ反転した。gen1独立positive gateも満たさず、P1 center保持。CEM manifest／generation results SHAは `07bdf5b6104cdcc2fb78de51ce6ef6e94bf041c1fea16ac0d7dee6e0db895c74`／`aff3bb1500535a7028c4fccf51b7f62f6bf36dad182a3cc3f92ab5d13303d7a5`／`34239b80f1afa2b4e46c75e7affaaecceef8e7f863092c4700d80399b4e95c69`。判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。

現行BestKnownはself-owned cg P1＋root deckで不変。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、branch／HEAD `feature/belief-guided-search`／`30cade0e5d349d6ea545f019fc411e9d53288f16`、current pool 102 rows（public 71／internal 31）、smoke-ready 101 rows、active heavy processなし。commit／push／Champion変更／Kaggle submissionは未実施である。

### CANONICAL OVERRIDE（最新）

最終目標は、self-ownedかつ確実に提出可能なdeck＋policyで実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、最終的にnative上位72%級を安定して超える提出モデルを完成させること。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価source／同一Skarin-Zoli pairのblind retryは再開しない。次の最優先は相関の低い新meta sourceの獲得・生成方法であり、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全て通過するまで`cg_bestknown_loop_v1.py`のheavy policy→deck→policy loopを起動しない。詳細evidenceは`docs/evidence/cg-adversarial-route-meta-source-e-fix-20260815.md`。

## 39. Self-owned routed direct-main repair / CEM（2026-08-15最新追記）

前回のself-owned failure-adapter routed sourceが8/8 faultになった原因を、parent package入口がsealed直下`main.py`なのにwrapperが`payload/original_main.py`だけを探していたingest contract defectと切り分けた。`src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`を2種類のentrypointへ対応させ、direct entrypoint自身のstatic scan、parent main／deckの隔離コピー、entrypoint形式のcandidate/source SHA bindingを追加した。`tests/test_routed_ensemble_meta_v1.py`は既存wrapperとdirect-main回帰を含め`6 passed`。旧fault rootはquarantineし、途中の全`runs/`走査rootは性能artifactに昇格していない。

同じ4 self-owned failure-adapterを再封印した `runs/cg-selfowned-adapter-route-meta-20260815-c/` は、generated pool／fresh／split SHAが `b782c8466e0d3293cdd5a60f5a0b35492a55408a66da3098d44c8d16228ddfdf`／`335e36630ce6190f3804f4d696539300254dd051e8733a92c34930b6ab55f871`／`16d684f2152706e97a4c8e3e7770628ba86829bd89dc530b23579d85ffc4e8b2`。P1両seat smoke（seed `20260913`）は`DONE=8/8`、fault0、draw0、`4W-0D-4L`。promoted pool／fresh／rebound split SHAは `d3b0672ecf21ab505764e2aa5e5d4566c2af98c935a5f697d19b95eec2b36577`／`3555e8743a3c4532a4d75f38fbdb217d2eea55dd11cbd0dfe5fabb12b1d17477`／`c9b6c286ab21a676706f7676214e22b48a57a413940f01201b411091a18eb25a`である。splitはTRAIN 2／DEV 1／FINAL 1、FINALは未使用で保持した。

P1固定CEM `runs/cg-selfowned-adapter-route-cem-20260815-c/`（seed `20260924`、META_TRAIN_ALL、population／elite `4／1`、2世代、独立re-eval 1 block、positive gate）は全screen／re-evaluation／DEV rowをfault0で完了した。gen0 screen上位は独立`6W-0D-10L`対control`11W-0D-5L`（`-31.25pt`）へ反転、gen1 centerは独立`10W-0D-6L`対control`10W-0D-6L`（`0pt`）、DEVは`8W-0D-8L`対`9W-0D-7L`（`-6.25pt`）。positive gateでP1 centerを保持し、P2/P3、BestKnown loop、deck phase、FINAL performance confirmationは行っていない。判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`である。

### CANONICAL OVERRIDE（最新）

最終目標は、self-ownedかつ提出可能なdeck＋policyで実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、native上位72%級を安定して超えることである。現行BestKnownはP1＋root deckのまま。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価policy surface、同一P1-base adapter／同一route recipeのblind retryは再開しない。次の最優先は、未性能使用policy lineageまたは新規permission済みsourceを含む相関の低い複数runtime-safe familyの混合poolを獲得・生成すること。新recipeは`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全て通過した場合だけ`cg_bestknown_loop_v1.py`へ接続する。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submission、commit、pushは不変である。詳細evidenceは`docs/evidence/cg-selfowned-routed-direct-main-20260815.md`。

## 40. Actor-visible action-level mixer meta / P1 CEM（2026-08-15最新）

新しいmeta source生成方式として、同一canonical deckの4直接policy lineage（Koushik rear-card、Prvsiyan control、Prvsiyan Alakazam、Prvsiyan meta-router）をaction-levelで混合する`ACTOR_VISIBLE_ACTION_LEVEL_MIX_V1`を実装した。各decisionで2 parentへ同じ観測を渡し、parentが返す合法index集合のどちらか一方を、公開状態だけで決定的に選ぶ。indexを発明・mergeせず、相手private情報は読まない。4 recipe（KO／tempo／setup／hash）から12候補を生成した。

generated root `runs/cg-action-level-mixer-meta-20260815-c/` のpool／fresh／split／meta SHAは `c863bb81ba03360a2b82188eaada3dc1d66fc4e5cf80c4dda31a922dd60cb411`／`ddb1a661ad35f3ef496f572f4ebb73f74f6bef2a589f4ab91cbc50b1b0f61a28`／`dc6e8c6e5a12c6ecb968b91d6e4699f2081514e0715b6ff0f612a48f8a2d9f51`／`b123fa45a34eb69265c2bcbeb757ee29da5ec9f0286cad0437bc8a69f6bac443`。全24 smoke gameはDONE・fault0・draw0（P1 `1W-0D-23L`）。promoted root `runs/cg-action-level-mixer-promoted-20260815-c/` のpool／fresh／split SHAは `0563fea8a48f712819aa7577133614ea06f0994dbd28d034fbbc44588b2a2c70`／`0f9db8e11f09094f8ed77cb2de192b9b838960e5d644185985f49e7080a03db3`／`c995d521939494cf1aecdc00a954544e886c2d62608bc80b302f1eb2fabe1b54`。splitはTRAIN10／DEV1／FINAL1で、fresh-meta loaderはPASS。

P1固定CEM `runs/cg-action-level-mixer-cem-20260815-c/` はseed `20260817`、META_TRAIN_ALL、population／elite `4／1`、1世代、screen 200＋独立re-evaluation 80、fault0で完了した。screen上位は`+10.0pt`だったが、独立2 blockでは`−10.0pt`／`−5.0pt`へ反転し、seat-collapseも残った。positive/risk-aware gateによりP1 centerを保持し、DEV／FINAL、BestKnown loop接続、deck phaseは未実施。4候補版の小pool診断も全候補valid=falseだったため、12候補化は統計的識別力を上げるための別compositionであり、同一sourceのblind retryではない。

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P1／root deck／BestKnown／Champion／production／submission／commit／pushは不変である。次は同じ4 lineage／recipeを繰り返さず、未性能使用policy lineageを含む相関管理済み混合pool、またはsmokeと性能holdoutを分離できる新規permission済みsourceを優先する。全ゲート（`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`）通過後だけ、`cg_bestknown_loop_v1.py`の`P1 → policy CEM → fresh validation → deck → policy`へ接続する。一次evidenceは`docs/evidence/cg-action-level-mixer-meta-cem-20260815.md`。

### CANONICAL OVERRIDE（action-level mixer最新）

このpackと実repoの最新一次artifactを正典とする。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価policy surface、同一parent／同一action recipeのblind retryは再開しない。最終目標はself-ownedかつ確実に提出可能なdeck＋policyで実CABT勝率を主指標にBestKnownを更新し続け、native上位72%級を安定して超えることである。現行BestKnownはP1＋root deckのまま。新sourceが全ゲートを通過するまでheavy policy→deck→policy loop、Champion変更、commit、push、Kaggle提出を行わない。

## 41. TRAIN-only difficulty-calibrated heterogeneous meta pool / P1 CEM（2026-08-15最新）

新しいmeta source生成方法として、P1とのTRAIN-only calibration ledgerで候補の難易度を校正し、異なるruntime-safe familyをpool levelで混合する `TRAIN_ONLY_DIFFICULTY_CALIBRATED_HETEROGENEOUS_POOL_V1` を実装した。実装は `src/mage_ptcg/opponent_ingest/calibrated_meta_pool_v1.py`、CLIは `scripts/build_calibrated_meta_pool_v1.py`。最終poolは Rocket dispatch classifier／confidence／specialist route／theta behavior と Water Box runtime-safeの5 family、12候補である。metal familyはparent timeoutを含んだため除外した。2局候補ごとのcalibration scoreは0／0.5／1に量子化されるため、性能証明ではなくcomposition校正だけに使った。

generated root `runs/cg-calibrated-heterogeneous-meta-20260815-c/` のpool／fresh／初期split SHAは `07d18f75a787bdcaddaa5c7c1adfdcad49bef7039d5629981fe82ec7032ca564`／`3e8818b72c30da9bc12b310b71bcd88e9ae1e0e4143249c6ab648cb5d67889cd`／`aa4749d903d4e77fa8dc8b0dfab95a24c8e2b0efc703bd37deee8afe50958f60`。P1両seat各1局の公式CLI smokeは24/24 `DONE`・fault0・draw0（5W-0D-19L）。promoted root `runs/cg-calibrated-heterogeneous-promoted-20260815-c/` のpool／fresh／meta／rebound split SHAは `b5e4417d38855f8821baf7ef1d494aff5075ac88310ee4a8a89734306dfea095`／`32879d9ecb13ea25962368124469693dad9f150cdd226ce5ea1af2fb872f7297`／`8ca1457feacc1880e8cde10d1c2e2e316a74b987ff6706e0611ac9c9bb043a59`／`d6d3b05c4f574e434dfb8ed50b12ea2af2b65844ae49587fc3af7fb12d7c4383`。splitはTRAIN10／DEV1／FINAL1で、全候補をruntime smokeしたためFINALはCEM性能未使用だがsmoke-untouchedではない。

P1 fixed CEM `runs/cg-calibrated-heterogeneous-cem-20260815-c/`（seed `20260858`、META_TRAIN_ALL、population／elite `4／1`、1世代、positive／risk-aware gate）はscreen 200/200、独立re-evaluation 80/80を全て`DONE`・fault0で完了した。screen candidateは最良6/40（15.0%）対control8/40（20.0%）で、screen elite `cg-p1-cem-g00-c03-fd74f19c63b0`は独立0/40対control7/40となった。独立positive／seat-safeを満たさず、`incumbent-center`でP1 centerを保持した。DEV／FINALはCEM性能選定へ読んでいない。判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。

### CANONICAL OVERRIDE（calibrated heterogeneous pool最新）

このpackと実repoの最新一次artifactを正典とする。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価source／同一5-family calibration poolのblind retryは再開しない。最終目標はself-ownedかつ確実に提出可能なdeck＋policyで実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、native上位72%級を安定して超えることである。現行BestKnownはP1＋root deckのまま。次はruntime smoke用候補と性能holdoutを分離し、未性能使用policy lineageまたは新規permission済みsourceを含む相関の低いpoolを生成する。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`の全ゲート通過前に`cg_bestknown_loop_v1.py`のheavy loop、Champion変更、commit、push、Kaggle提出を行わない。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submission、commit、pushは不変である。詳細evidenceは `docs/evidence/cg-calibrated-heterogeneous-meta-pool-cem-20260815.md`。

## 42. 同一deck action-consensus meta source / P1 CEM（2026-08-15最新）

未使用の同一 canonical deck parent pair `kaggle_kokinnwakashuu_lucario_20260815`（policy `e976b40df254a78de160e82d8b0b390e582b0a67cad0e77001004a2f7863799a`）と `kaggle_yaroslav_lucario_crustle_20260815`（policy `fb0209dc9f1e9309524be88e02c02fb54f042f40d04baba49b66885ae6e42145`）を使い、`ACTION_LEVEL_CONSENSUS_MIX_V1`、`ACTION_LEVEL_CONSENSUS_HASH_V1`、`ACTION_LEVEL_CONSENSUS_KO_V1` を追加した。共通合法 action index集合を `minCount/maxCount` 内で優先し、共通集合がないときだけ公開 score／hash／KO fallbackを使う。private情報、future RNG、network、index発明はない。Kokinn追加公式 smokeは2/2 DONE・fault0・draw0である。

generated root `runs/cg-action-consensus-meta-20260815-b/` は6候補、pool／fresh／split SHA `4866a112434535549b2db03cc149271a40eb6fee2bbb9243c6148ea454643fa6`／`75b292c9d5bbd3278415b82907efc0b7e6d6ae7c47b1da6e2f76b22b5679d48d`／`5c734e326bf4f497390e9f54adaefb0e2f9eb38f9f43fd8f164adca2183ddf58`。P1両seat smokeは12/12 DONE・fault0・draw0。promoted root `runs/cg-action-consensus-promoted-20260815-b/` は pool／fresh／meta／rebound split SHA `d11d09e4320cc769240a28cec555a72389530b5d1d073a7e4a0c40e614440859`／`6b53b0336d324a9ee8670100375fceb5c3228728bfa315fbc03f157abf87dccc`／`3a92c79557d9028c166d472a60fc4fdad140e4bc29338cb957629b4b7f38926c`／`4cfe2b3696225a1f847d001ac35aa06da25e850db85816c45036484b1b22600b`。splitはTRAIN4／DEV1／FINAL1で、全候補はruntime smoke済みだがDEV／FINALはCEM性能未使用である。

P1固定CEM `runs/cg-action-consensus-cem-20260815-b/`（seed `20260862`、META_TRAIN_ALL、population／elite 6／2、1世代、独立re-evaluation 2回、positive／risk-aware gate）は screen 112/112、独立48/48をfault0で完了した。screen最良は3/16対control4/16（−6.25pt）。独立上位のrepeat deltaは0/+37.5ptと−12.5/+25.0ptで、seat gap／worst gate不通過。`elites=["incumbent-center", "incumbent-center"]`でP1 centerを保持し、BestKnown／Champion／root deckは不変である。

screen上位 candidate `cg-p1-cem-g00-c01-3dd7cdcee94c` の staged-pool fresh validation（base seed `20260863`）は、TRAIN128で+7.8125pt、DEV16で0pt、FINAL16で0pt、全てfault0だった。DEV／FINAL再現性はなく、採用・BestKnown更新には使っていない。validation rootは `runs/cg-action-consensus-fresh-validation-20260815-b/`、一次evidenceは `docs/evidence/cg-action-consensus-meta-cem-20260815.md`。

### CANONICAL OVERRIDE（action-consensus最新）

この節と上記evidenceを最新一次artifactとして扱う。同じ Kokinn/Yaroslav pair、同じconsensus recipe、同じCEM seedのblind retryはしない。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価policy surfaceは再開しない。現行BestKnownはself-owned P1＋root deckのまま。次は未性能使用policy lineageまたは新規permission済みsourceを含む相関の低い混合poolを、runtime smoke候補とperformance holdoutを分けて生成する。全ゲート `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` 通過前に `cg_bestknown_loop_v1.py` のheavy loop、deck phase、Champion変更、commit、push、Kaggle提出を行わない。

## 43. Self-owned same-option-type action adapter source / P1 CEM（2026-08-15最新）

新しいsource生成法として、Feroz public policyをself-containedに埋め込み、各decisionでbase actionと同じ`option.type`を持つ未選択合法候補がある場合だけ、観測＋saltのSHA-256で決定的に置換する`self-owned-meta-adapter-v1`を追加した。indexの発明、重複、`minCount/maxCount`違反、network、future RNG、private情報の追加はない。実装は`src/mage_ptcg/opponents/self_owned_action_adapter_v1.py`、CLIは`scripts/generate_self_owned_adapter_meta_v1.py`／`scripts/seal_self_owned_adapter_meta_v1.py`、契約テストは6 passed、生成policy AST scan findingsは空である。

generated policy SHAは`7f51c35ee3d12357a74e35397f80a7fb9a74b449734c62b4b1393c0b4c5d4405`。promoted source `runs/cg-self-owned-adapter-promoted-v2-20260815/`はpool SHA `7f76f36343a5e557e3fbfca9f441a9a882488f681d70ca7146be6891b0228a0f`、fresh SHA `8bd24558399aee0a2078fd239f6ca67a6231256e6b011df7ddf1870dcb1900de`。P1両seat smokeは2/2`DONE`、fault0、draw0で、freshness evidenceを含む`build_fresh_meta_batch_v1`検証もPASSした。

Feroz、Prvsiyan v23、generated adapterの3 referenceを`runs/cg-public-selfowned-merged-meta-v2-20260815/`へmergeし、pool/fresh/meta/split SHAはそれぞれ`90efe8f91164d08ad4720de9cf7f5ad27675dce6c9c4af2192a8700a5af7dc68`／`c6c281ba16177fae41a0f9a8eef3f20658f02552adc25b6eb0ec8096ac86fd2c`／`65b1a6548a8b521b34be61ddbd73559e512cae6d6d8fbb879725dda65e0903b0`／`5521bd4684ef606fa36bb25d5535daa2e9d25842bdd7038f26d9938c7ef71442`。splitは`META_TRAIN=Feroz`、`META_DEV=Prvsiyan v23`、`META_FINAL=generated adapter`で、CEMの性能選抜にFINALは投入していない。

P1 fixed CEM pilot `runs/cg-public-selfowned-cem-v1-20260815/`（1世代、population4、screen20局、独立re-evaluation4局、全row fault0）は`incumbent-center`を保持した。1 TRAIN referenceの小標本であり、fresh DEV/FINAL validation、deck phase、BestKnown更新は行わない。判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。詳細は`docs/evidence/cg-self-owned-action-adapter-meta-cem-20260815.md`。

### CANONICAL OVERRIDE（self-owned adapter最新）

最終目標は、self-ownedかつ確実に提出可能なdeck＋policyについて、実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、native上位72%級を安定して超えることである。現行BestKnownはP1＋root deck、Champion、production、submission、commit、pushは不変。generated adapterは独立policy lineageの水増しに使わない。次は相関の低い複数policy/deck familyと未使用性能holdoutを追加し、全ゲート`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を通過した候補だけを`cg_bestknown_loop_v1.py`へ接続する。旧Rule v0探索、Student/AWR/BC、deck blind sweep、同一adapterのblind retryは再開しない。

## 44. 公開kernel intake v17 / Lucario cross-lineage CEM（2026-08-15最新）

Sushanth公開kernel 7件を`configs/meta_specialist/cg_kaggle_kernel_meta_v17.json`からintakeした。exact 60、ACE SPEC exactly one、agent entrypoint、static safetyを通過したのはLucario-Garchomp 1件のみ。Gardevoir／Hydreigon／Gouging Fire／Dragapult v3は`invalid_ace_spec_count`、Venusaurは`missing_agent_entrypoint`、Palafinは`invalid_deck`でrejectした。LucarioはP1 smoke 2/2 DONE・fault0でpartial promotionした。

Lucario policyをKoushikrudra／Raunak／Prvsiyan visible-grim v23の異なる合法deckへ`CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1`で組み合わせ、3候補をsealした。generated pool SHA `5255d1f62116bae9fbc32bf916da730e8d763829a261558d516f8a54a8155a73`、promoted pool SHA `6b3d8b771f10f45e4f2ac457d325299f1e8ff0f00fc174e99699b9abf11e3edc`、fresh SHA `ca8a9e281491c62cfadd9c004c94f41c4442540c5c2b58863c0e8a9f60d92324`、split SHA `88a87babe0c7553023d1e806158fa505791e321c2f796312a18e9ec092508996`。P1 smokeは12/12 DONE・fault0である。

`runs/cg-cross-lineage-cem-v2-lucario-20260815/`はP1固定、seed `202608153`、population／elite `8／2`、2世代、META_TRAIN＋META_DEV search、独立再評価、positive-delta gateで全row fault0。gen0は独立反転でP1 centerを保持。gen1 centerは独立TRAIN `6/8 対 5/8`、未使用META_FINAL `10/16 対 9/16`。独立seedの拡大FINAL holdout（64局）はcandidate `44/64`、control `36/64`、差`+12.50pt`だったが、candidate seat rate `0.78125/0.59375`、gap`18.75%`で`NOT_PROMOTABLE`。

判定は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変。同一Lucario policy×deck pairのblind retryはしない。次はACE SPEC／entrypoint reject sourceを元policy非改変で合法deckへ変換するdeck-repair adapter、または新policy lineageを含む別public batchを新epoch・新seedで生成する。一次evidenceは`docs/evidence/cg-kaggle-kernel-intake-v17-cross-lineage-cem-20260815.md`。

### CANONICAL OVERRIDE（v17最新）

最終目標は、self-ownedかつ確実に提出可能なdeck＋policyで実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、native上位72%級を安定して超えること。現行BestKnownはP1＋root deckのまま。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価source、同一Lucario cross-lineage pairのblind retryは再開しない。新sourceは`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全て通過した場合だけ`cg_bestknown_loop_v1.py`へ接続する。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submission、commit、pushは不変である。

## 45. 公開kernel deck-repair source / P1 CEM fresh FINAL（2026-08-15最新）

Sushanth公開kernelの元policyを改変せず、ACE SPEC／deck legalityのみを明示位置置換する `EXPLICIT_POSITION_REPLACEMENT_V1` を生成器 `src/mage_ptcg/opponent_ingest/legalized_public_meta_v1.py` とCLI `scripts/generate_legalized_public_meta_v1.py` に実装した。Gardevoir index9（5→1158）、Hydreigon index19（7→1088）、Dragapult v3 index11–13（13→1184）、Gouging Fire index25（1088→1227）を処理し、exact 60・公式ID・ACE SPEC exactly one・static scanを通した。初回binary tar decode failureはquarantineし、v2で再生成した。

v2 smokeは8局中6 DONE・2 AGENT_ERROR。Dragapult／Gardevoir／Hydreigonのみをpartial promotionした。promoted pool／fresh／split SHAは `93ebd7a6090afcbf7361576821281aeb665da3ee9e4ef91eb7df2e110d2b2479`／`d1d5c07db9dc3ce1f59272c79142d5d17eaeeaff5f666d9d33e12dcce5d2fb9b`／`74e4c2c8bd29c201b79b3cc1cef08191bee282720873a21deadec00d843b12cd`。splitは`META_TRAIN=Dragapult`、`META_DEV=Gardevoir`、`META_FINAL=Hydreigon`。Gouging Fireは`DECK_ON_INITIAL_SELECT_NONE_SINGLE_ARG_V1` adapter後も公開policyの`prize_count(None)`で4/4 fault、quarantineした。

P1固定CEM `runs/cg-legalized-public-meta-cem-v1-20260815/`（seed `20260895`、2世代、population／elite `8／2`、独立re-evaluation 2回、positive／risk-aware gate）は全row fault0で完走。gen0 screen上位は`+25.0pt`だが独立mean `0pt`／worst `−25.0pt`、gen1 c05は独立mean／worst `+12.5pt`だがseat gap `25%`を含み、両世代とも`incumbent-center`でP1を保持した。gen1 c05は candidate policy SHA `b7616fe97f8d151b37af2bee94f0a7b858017912e397095b5b6a9e6bc9798cc9`。

gen1 c05の未使用META_FINAL確認 `runs/cg-legalized-public-meta-cem-fresh-confirmation-v1-20260815/` はcandidate `21/32`（65.625%）対P1 `27/32`（84.375%）、差`−18.75pt`、fault0、`NOT_PROMOTABLE`。Hydreigon FINALは今回の確認で使用済みで、次campaignのblind holdoutには戻さない。判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。P1 policy／root deck／BestKnown／Champion／production／submission／commit／pushは不変。詳細evidenceは `docs/evidence/cg-legalized-public-meta-repair-cem-20260815.md`。

### CANONICAL OVERRIDE（deck-repair source最新）

この節と上記evidence、実repoのsealed artifactを正典とする。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価source、同一Sushanth deck-repair recipe／同一CEM campaignのblind retryは再開しない。最終目標はself-ownedかつ確実に提出可能なdeck＋policyで実CABT勝率を主指標にBestKnownを更新し続け、native上位72%級を安定して超えること。次は未性能使用policy lineageまたは新規permission済みsourceを含む相関の低いpoolを、runtime smoke候補とperformance holdoutを分けて生成し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` の全ゲート通過候補だけを`cg_bestknown_loop_v1.py`へ接続する。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、BestKnown、Champion、production、submission、commit、pushは不変である。

## 46. 公開kernel union5 / P1 CEM（2026-08-16）

未性能使用policy snapshotとしてRmy alias、Aristophanivan、Kityugin、Aman、Penguinの5件をintakeし、各2局のbounded smokeを10/10 `DONE`・fault0で完了した。sealed rootは`runs/cg-kaggle-kernel-meta-promoted-union5-20260816/`、pool／fresh／split／meta SHAは`b0e4ffb937c1468180cd378d4b1e4d115bb6a2cf3396e99d03d46394908aa4b3`／`81730c60e8b882f64dd09e5f2741fc2114eb75bb88275c2a890ba9150714b4c2`／`0a2d42dce9c8c1bad3035d1f0102e25e682de1a0fb47bd870f38611849e01a4a`／`b9c087fc4fe82cf2dafc0b99a623f1e4f68f2266b6500d7f6d20d5be70ec47cd`。P1固定CEM `runs/cg-p1-cem-union5-20260816-g01/`は合計680 rows fault0だが、独立repeatで安定転移せず、g00/g01とも`incumbent-center`を保持した。union5は性能使用済みであり、同じpoolのblind retryはしない。

## 47. Makthanithin × Aman routed ensemble / P1 CEM no-update（2026-08-16）

Makthanithin公開policy（raw SHA `a81eab3eb761af95da2ddf70a67d6078897a2cd698dae4a7b6ea92de070fad2b`、staged SHA `cdcf8329f5c091f994584ff5f987dd2de1e615679e838ecb74470f9cf2f89b04`）とAman policyをactor-visible routingで12候補化した。promoted rootは`runs/cg-makthanithin-aman-route-promoted-20260816/`、pool／fresh／meta／split SHAは`287c78324a869e7724f8d6eedbfeb4317ab868a4c5a85de7ad945d380249cd80`／`c911d8e14b027a025329c314ab5376ab05f30174b3666701fad867344dfdacbb`／`e4992f55667b68e107d3619592b5dd2b0493034202b5afc62ac7adb64fc8dccc`／`bdf7dd77ae1a18d420145144ac2b0f632982c7bf60aaebb2ad1747201a3fe564`。P1 CEM `runs/cg-p1-cem-makthanithin-aman-route-20260816-g01/`はscreen416＋独立256をfault0で完了したが、独立positive／seat-safe候補0件でP1 centerを保持した。Mak/Aman routeは性能使用済みであり、同じpairをblind retryしない。

## 48. Mak direct gap / self-owned turn-planner transfer（2026-08-16最新）

Mak staged policyをP1と直接比較した診断`runs/cg-mak-direct-vs-p1-diagnostic-20260816-v3/`では、同じMak/Aman-derived panelでMak `12W-0D-20L / 37.50%` 対P1 `0W-0D-32L / 0.00%`、64/64 `DONE`・fault0だった。ただしMakの`search_begin(obs, your_deck=yd)`は現行API契約に合わずheuristic fallbackへ戻るため、search APIの効果とは断定しない。公開コードを提出policyへコピーせず、P1からself-owned `src/mage_ptcg/meta_specialist/cg_p1_turn_planner_v1.py`（6 knob、search/private fieldなし）を実装した。candidate policy SHAは`075dc82bed4565c68c8f2a6b96eefc2b92ef3d8a7f17ecd61aa89ea2380fccbf`、focused testは4 passed。同じpanelでは`+3.125pt`だがseat gap`6.25%`でgate外、broad META_TRAINでは`−2.6662pt`・seat gap`12.5%`で転移しなかった。

### CANONICAL OVERRIDE（2026-08-16 Mak diagnostic最新）

このpackと実repoの最新一次artifactを正典とする。`ono-`は公開source名ではなく、local branch `agents/ono-cg-lethal-v1`／commit author `bfe-lab-ono`に由来する識別子である。Mak source、Mak/Aman route、turn-plannerは研究artifact／local-eval-onlyであり、self-owned BestKnownではない。現行BestKnownはself-owned P1＋root deckのまま。Mak/Aman-derived panel、同じturn-planner config、同じCEMのblind retryは再開しない。次はsmoke候補と性能holdoutを分離した新しいpermission済みpolicy lineageまたは相関の低いmeta source生成を行い、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たす候補だけを`cg_bestknown_loop_v1.py`へ接続する。BestKnown、Champion、production、submission、commit、pushは不変である。詳細evidenceは`docs/evidence/cg-mak-source-and-selfowned-turn-planner-20260816.md`。
## 49. 公開kernel Marnie base static variant intake（2026-08-16最新）

`llccqq624/ptcg-alakazam-marniebelief-0723-a` の提出rootは `importlib` 依存で静的境界外だったため採用せず、archive内 `base_main.py`＋`marnie_belief.json`を選択した。v1の一引数 `agent` と共通CABT wrapperの二引数契約差を、policy decision pathを変えないcompatibility adapterで修復し、v2を別hashでsealした。source SHA `5079eca56c00edc5b510e1caa901e457c00e94dfa63d37bb53a0cb4e7377c296`、promoted wrapper SHA `ba9af9aacbb68fcf7e3bfde3f88de50e3a259cf233e8d0be0e571e6dddade380`、tar SHA `513d6858f78c26bc3c6aec2920f638eaa44b9790459d40bc8bbfe0f346616f15`、deck/canonical SHA `0598646548d081832ec311c15fdc369b32c6f5e63175b0cfd1904d21fd082451`／`606a775392ffe25e058b19c17801d58a4bf30f7cd8c62782388d3de7e7eb5283`である。

promoted root `runs/cg-kaggle-kernel-meta-promoted-marnie-base-static-v2-20260816/` は static findings 0、exact 60、ACE SPEC 1、loader PASS。v2 smokeは4/4 `DONE`・fault0・draw0（3W-1L、各seat 2局）。pool／fresh SHAは `ef9aafdcabc62e7dc624bf1b6447a6d2fb65e801aa0b0c26fc4bb6b9dfe1db50`／`887c604d0b27706ed0f709bedfb9704fb7555bef85f2f378806fe6020a00bfd6`。ただし新規参照は1件だけで、独立 `META_TRAIN`／`META_DEV`／`META_FINAL` split、P1 CEM、fresh validation、deck phase、BestKnown loop接続は未実施である。判定は `SOURCE_GENERATION_PASS / PERFORMANCE_NOT_STARTED`。

### CANONICAL OVERRIDE（Marnie static intake最新）

最終目標はself-ownedかつ確実に提出可能なdeck＋policyで実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、native上位72%級を安定して超えること。現行BestKnownはself-authored policy＋common/public root deckであり、self-owned deck＋policyとはまだ呼ばない。Marnie static variantは公開sourceから生成したlocal-eval-only snapshotであり、独立作者lineageや性能証拠ではない。次は少なくとも2件の未性能使用policy lineageまたは明示的self-owned generation variantに加え、root deck自体のself-owned lineageを確立または再生成し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を満たしたsource poolだけを `cg_bestknown_loop_v1.py`へ接続する。旧Rule v0探索、Student/AWR/BC、deck blind sweep、既評価sourceのblind retry、Champion変更、commit、push、Kaggle提出は行わない。P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`は不変である。`ono-` は公開source名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42b028f10960d08ccb4980be5b76946f98b`由来のローカル識別子である。root deckがAman、Makthanithin、Kojimar、Aristophanivan 2 snapshotとraw SHA一致する事実と単一元kernelを特定できない制約は、上記evidenceへ記録する。

## 50. self-owned deck + P1 policy CEM pilot（2026-08-16最新）

公式カードCSV `data/raw/EN_Card_Data.csv` と `configs/meta_specialist/self_owned_cg_deck_spec_v2.json`だけからseed `20260840..20260845`の6 scratch deckを生成した。公開canonical hashとの衝突は0、6件のcanonical deck hashは相互に異なり、各packageは`parent_deck=null`、`public_parent_read=false`、authority全false。sourceを各4局（両seat）smokeし、24/24 `DONE`・fault0でpromoteした。promoted rootは`runs/cg-self-owned-cg-meta-batch-v3-20260816-promoted/`、pool／fresh／split SHAは`99a28828d0adaa215f048ce35ecc5b59445be670efe1a9973a4b6fd0d769f5ec`／`de31609f0b9d9f51c0a7a3c39f35d9e6c9a88e8ae2beb6d0c12b5d31becfdc28`／`3eab6dc1b3ef61e84e28f680a44fc6abfb49f58ebc98201d77f0aaf7dd43372d`。META_TRAIN=4、META_DEV=1、META_FINAL=1で、CEM中DEV／FINALは未使用である。

P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`を同じscratch deck（deck file SHA `5610c2e0b9210c22885edcf59160212270fdfc53c90b6f61df588dcdbf8ffde2`、canonical SHA `b6dc5a6a3f3e00545df881fa3c6981e1cf8ee418c39794504bed90d052ddfcbf`）へ再bindするmaterializer `src/mage_ptcg/meta_specialist/self_owned_cg_parameterized_package_v1.py`を追加した。既存P1 CEM coreへ接続するresearch-only runnerは`scripts/run_self_owned_cg_policy_cem_v1.py`。

`runs/cg-self-owned-cg-policy-cem-v1-20260816-pilot/`をcampaign seed `2026084601`、population／elite `8／2`、META_TRAIN_ALL、独立re-evaluation 2 block、positive／risk-aware gateで実行した。screen 144局、独立96局、合計240局は全て`DONE`・fault0。screen最大はcontrol同率だった。独立では候補c05がrepeatで`+18.75pt`／`+25.00pt`（平均`+21.875pt`、最悪`+18.75pt`）まで出たがopponent／seat安全条件外、c03も最悪delta `0pt`で安全条件外だった。`risk_aware_independent_train96_x2_valid_candidates_below_elite_count_preserve_center`によりP1 centerを保持した。

### CANONICAL OVERRIDE（self-owned deck CEM最新）

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。このv3 source epochはCEM性能使用済みであり、同じpoolのblind retryはしない。DEV／FINAL、`cg_bestknown_loop_v1.py`、deck phase、BestKnown／Champion／production／submission、commit／pushは不変。次は別deck recipeまたは新しいpermission済みpolicy lineageを新fresh epochとして生成し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たしたcandidateだけをBestKnown loopへ接続する。`ono-`は公開source作者名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42b...`に由来するローカル識別子であり、現行BestKnownはself-authored policy＋common/public root deckである。

## 51. self-owned alternate deck epoch + P1 policy CEM v2（2026-08-16最新）

公式カードCSV `data/raw/EN_Card_Data.csv`（SHA `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`）と `configs/meta_specialist/self_owned_cg_deck_spec_v3.json`（SHA `b09d04996e02689bf78bdd0d40c596efc028a98ca264a095112859663e863423`）だけから alternate count 構成の6 self-owned sourceを生成した。seed／ordinalは`20260850/0`、`20260851/1`、`20260853/3`、`20260854/4`、`20260855/5`、`20260856/6`、exact 60、公開canonical hash衝突0、相互distinct。`20260852/2`は合法deck retry bound失敗で除外し、artifactは保全した。promoted pool／fresh／meta／split SHAは `4215effb998e0fc6fa3e4b70c52f456ba29351ed4e01fd608bb488366f69607b`／`d81837eb6e605e1fc3ab72f46dcd8d1b137a08d15ff50bf0c1f2336504c7911a`／`ee9f1da95be5951378a35946942577a9cb316d7f321741a232b55a93c3247d49`／`b1380c9b29ebc5a9c24c8bc5ba567a15054312e7ad22a8b3b8087e08e2f5a63d`。splitは`META_TRAIN=4 / META_DEV=1 / META_FINAL=1`でsource verification PASS。

6 sourceをP1 root controlと両seat各1局smokeし、24/24 `DONE`・fault0（summary SHA `01ff46535698d2d6cdfe5baf10f315e0a34b8e14e8905091f858831612b10cf0`）。P1固定CEM `runs/cg-self-owned-cg-policy-cem-v2-20260816-pilot/`（seed `2026084607`、population／elite `8／2`、screen 144局、独立96局、全row fault0）はscreen c03 `+12.50pt`が独立`−6.25pt / +12.50pt`、c01が`−25.00pt / +25.00pt`へ揺れ、positive／seat-safe／opponent×seat-safe候補0件、`incumbent-center`保持となった。campaign／results SHAは `167f57307e00ba29277132aaecd1aea534efd99f931bb84553c72b3518196f3a`／`f59a2f05293d4e675b6e4eaf738faa9b79dc0aefa4351f669f4b1e92338ab80d`。

### CANONICAL OVERRIDE（self-owned alternate deck CEM最新）

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v4 source epochはscreen・独立再評価で性能使用済みのため、同じpoolのblind retryは行わない。META_DEV／META_FINAL、`cg_bestknown_loop_v1.py`、deck phase、BestKnown／Champion／production／submission、commit／pushは不変。次はsmoke候補と性能holdoutを分離した別deck recipeまたは別policy lineageを生成し、全ゲートを通過した候補だけをBestKnown loopへ接続する。現行BestKnownの正確なラベルは「self-authored P1 policy＋common/public root deck」であり、deckまでself-ownedとは呼ばない。`ono-`は公開source作者名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42b028f10960d08ccb4980be5b76946f98b`に由来するローカル識別子である。詳細evidenceは `docs/evidence/cg-self-owned-cg-policy-cem-v2-20260816.md`。

## 52. self-owned deck × policy factorial source / P1 CEM（2026-08-16最新）

公式カードCSVだけを入力に、既存public canonical deckとSHAが衝突しない8件のscratch deckを生成し、P1の15 knob parameter configurationを8通りのpolicy variantとして各deckへ1対1で結合した。実装は `configs/meta_specialist/self_owned_cg_policy_factorial_v1.json`、`scripts/generate_self_owned_cg_policy_meta_v1.py`、`scripts/build_self_owned_cg_policy_factorial_split_v1.py`。最初のv1 recipeはcanonical deck collisionで停止・隔離し、v2 recipeへ切り替えたretry1だけを採用対象とする。

retry1のstaged poolは `fe5ff9269e34f5e943df427706eebba3712b4dcf92ccdb614ba7609a4d39a60c`、batchは `33ce59baa2ba4337c74b02ab4e7da5d1e12a64ef76d23e41a7645e82a50ff9ce`、promoted poolは `505e77becc5b342db958f9fbe08ec967f3c9c3252c5de5e1fc1f2336504c7911a`、fresh metaは `bf4db869e61440a4ae9ab409a60a876f0b987acb6f33335f31d4085049768f3a`。splitは `0bc8a3462cb6a83c4c4277808c80bfe641349f6636be4d997c83f0f8705d1f98`で、META_TRAIN=6 / META_DEV=1 / META_FINAL=1。DEV／FINALはCEM選定に未使用である。

P1固定CEMはscreen 216件、独立再評価144件をすべて `DONE / fault=0` で完走した。screen上位c04は独立再評価で−2.08pt、c06は+16.67ptだったが、opponent×seat-safe gateを満たさず、valid elite=0。したがってcenterはP1のまま、P2/P3への昇格、BestKnown、deck phase、`cg_bestknown_loop_v1.py`、META_DEV／META_FINALは不変である。小規模smokeの勝率差は性能根拠に使わない。

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。性能使用済みfactorial poolへのblind retryはしない。次は未使用metaを保った別policy lineageまたは相関を下げたdeck familyを生成し、smokeと性能holdoutを分離したうえで、opponent×seat-safe・fresh validationを通過した候補だけをBestKnown loopへ接続する。詳細は `docs/evidence/cg-self-owned-policy-factorial-meta-cem-20260816.md`。

### CANONICAL OVERRIDE（factorial source最新）

最終目標は、self-ownedかつ確実に提出可能なdeck＋policyについて、実CABT勝率を主指標にpolicy→deck→policyの改善ループを自律的に回し、独立seed・未使用metaで再現性を保ちながらBestKnownを更新し続けること。現行BestKnownは「self-authored P1 policy＋common/public root deck」であり、今回の8件は新規meta sourceとして昇格済みだが、P1を置換する性能候補ではない。旧Rule v0／Student・AWR・BC／過去deck blind sweep／性能使用済みpoolのblind retryは再開しない。

## 53. 公開未使用policy lineage pool / P1 CEM（2026-08-16最新）

CEM未使用で個別bounded smoke済みのRaunak／Prvsiyan／Koushikrudra／Marnie static variantを`runs/cg-kaggle-public-unused-lineages-v1-20260816/`へ統合した。pool／fresh／meta／split SHAは`3b53afa3aed3e4a25494c34dc3aa855efb903a72d3c710c75aada17624065e25`／`83d72a55548b9bb7887b0e2b9c8b0138d9dfa806e3e620a2d98db976dc74e456`／`aa4ea9bed4d1eef6b2a18fb740627407ac7634bba743f2f0af63e383b2675d4e`／`3d0eebe7b5389e96119a43bfffd2cca1a8809b53104bbde8046271e75e61974f`。splitはMETA_TRAIN=Raunak＋Prvsiyan、META_DEV=Koushikrudra、META_FINAL=Marnieで、`load_weekend_split(..., verify_sources=True)` PASS。

self-owned v4 deckのP1 CEM`runs/cg-self-owned-cg-policy-cem-public-unused-lineages-v1-20260816/`はscreen72/72`DONE`・fault0、valid screen candidate 1件（差0pt）、elite0件、P1 center保持。最初のscratch-deck bridge契約不一致rootはfail-closed隔離し、性能結果には数えない。独立re-evaluation、DEV／FINAL、BestKnown loopは未起動である。

別の未使用4-source pool（Jazi Archaludon／Kaiwalya／Yaminh staged／Jazi rank1）をroot-deck P1 CEM`runs/cg-p1-cem-public-new4-v1-20260816/`へ接続した。screen72/72`DONE`・fault0、valid candidate 1件（差0pt）、elite0件、P1 center保持。DEV=Yaminh staged、FINAL=Jazi rank1は未使用のまま保全した。

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。各epochのTRAIN rowsだけが性能使用済みで、DEV／FINALは未使用である。同じpoolのblind retryやelite数だけを変えた再実行はしない。詳細evidenceは`docs/evidence/cg-public-unused-lineages-cem-20260816.md`。

### CANONICAL OVERRIDE（public unused lineage最新）

現行BestKnownはself-authored P1 policy＋common/public root deckのまま。今回の公開lineage poolはmeta sourceとして実行可能性を示したが、P1を置換する性能候補は得ていない。次は別の未性能使用policy lineageまたは相関低減familyをsmoke候補と性能holdoutに分離して生成し、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たした候補だけを`cg_bestknown_loop_v1.py`へ接続する。旧Rule v0／Student・AWR・BC／過去poolのblind retry／BestKnown・Champion・production・submission変更は行わない。

## 54. 公開未使用 snapshot epoch v3 / P1 CEM（2026-08-16最新）

holdout exposureを再監査し、Yaminh stagedが別の`public-new4` CEMのDEV baseline診断へ投入済みだったため、先行v2 rootは未使用DEVとして採用せず保全した。正しいv3 epochは、TRAIN=Jazi Garchomp＋Prvsiyan visible-grim v21、DEV=Jazi rank1、FINAL=Marnie base static v2で、root `runs/cg-kaggle-unused-public-epoch-v3-20260816/`にsealした。pool／fresh／meta／split SHAは`5b13783671d77c66397287a8c1ff57a50177fce07fab17d7064816bdb5b9b1a6`／`20979a75471a2372f2554d6b248c684b12d070679737b5f48c735233c8c63ebe`／`9cbe500826e54606bdf260d932d22311cff9cc95fc7d85d8c6168e09a11bdd1a`／`25b4a48138925bd6aba909240f249ada1c97b8d03b20fc6a3cb6a51a7ba1d21c`。TRAIN-only smokeは4/4`DONE`・fault0、authorityは全false・`local_eval_only`である。

P1固定CEM `runs/cg-p1-cem-unused-public-epoch-v3-20260816/`はseed`2026084634`、population／elite`8／2`、1世代、`META_TRAIN_ALL`、screen2 games/opponent×seat、独立2 block×2 games/opponent×seat、positive／risk-aware gateで実行した。screen72局＋独立48局の全120局が`DONE`・fault0。screen validはc02=`3/8`対control`0/8`、c03=`2/8`対`0/8`だったが、独立TRAINは両者とも`1/16`対control`3/16`、差`−12.5pt`、candidate seat rates`0.125/0.0`でseat-collapse。`risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`により`incumbent-center`×2、P1 center保持。DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py`、BestKnown／Champion／production／submissionは不変である。

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。同じv3 source／seed／c02／c03のblind retryは行わず、次はsource ID／policy SHA単位のholdout exposure ledgerを自動監査し、相関の低い新規permission済みlineageまたはself-owned policy familyを新epochで生成する。一次evidenceは`docs/evidence/cg-unused-public-epoch-v3-cem-20260816.md`。

### CANONICAL OVERRIDE（public unused snapshot epoch v3最新）

この節と上記evidence、実repoのsealed artifactを正典とする。現行BestKnownはself-authored P1 policy＋common/public root deck。旧Rule v0／Student・AWR・BC／性能使用済みpoolのblind retryは再開しない。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`の全ゲート通過前にheavy policy→deck→policy loop、Champion変更、commit、push、Kaggle提出を行わない。

## 55. self-owned role-separated v4 source / P1 CEM（2026-08-16最新）

公式`data/raw/EN_Card_Data.csv`と`configs/meta_specialist/self_owned_cg_deck_spec_v4.json`だけから、role-default／pressure／setup／retreatの4 self-owned deck＋policy sourceを生成した。promoted rootは`runs/cg-self-owned-cg-policy-factorial-v2-20260816-promoted/`、pool／fresh／split SHAは`344134f98c87d9becf1cedf4fdf8726ac3564a4c07bb0a3bb14cb08704007ea0`／`aedac5f9251c4f4959b2d3556dfb387b07dc60c87cd541fb9cf2bde4b99e8d18`／`cf0baeea04f7fef6e5f76b899df77f5fde55bfbbdfed0b9791324fc0e8f7a5fd`。4 sourceは`parent_deck=null`、`public_parent_read=false`、authority全false。P1対のbounded smokeは8/8`DONE`・fault0・5W-3L、CEM splitはTRAIN2／DEV1／FINAL1でDEV／FINALは未使用である。

P1固定CEMの通常12-worker起動は、parent static native import後のworker境界で`buffer full`が発生した不完全artifactとして隔離した。compile-only static smoke＋1 workerのbounded retry `runs/cg-self-owned-cg-policy-factorial-v2-20260816-cem-lowworkers-retry4/`はscreen40/40、独立re-evaluation16/16を全てfault0で完了。screen c00は+12.5ptだったが独立は0pt、`incumbent-center`を保持した。判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P2／BestKnown、`cg_bestknown_loop_v1.py`、deck phase、Champion／production／submission、commit／pushは不変である。

candidate/shared `cg` module isolation loaderを修正し、関連suiteは13 passed、generation／package suiteは17 passed。Sushanth Zacian snapshotは別epochで2/2`DONE`・fault0・1W-1Lのpartial sourceとして保全したが、1 sourceのみのためCEMへ接続していない。

### CANONICAL OVERRIDE（self-owned role-separated v4最新）

最終目標は、self-ownedかつ確実に提出可能なdeck＋policyについて、実CABT勝率を主指標に`policy → deck → policy`を自律反復し、独立seed・未使用metaでBestKnownを更新し続け、native上位72%級を安定して超えることである。現行BestKnownの正確なラベルは「self-authored P1 policy＋common/public root deck」であり、deckまでself-ownedとはまだ呼ばない。旧Rule v0／Student・AWR・BC／既評価policy surface／性能使用済みpoolのblind retryは再開しない。次はsmoke候補と性能holdoutを分離した新しいself-owned policy family、またはparent native importを別subprocessへ隔離したCEM runnerを作り、全ゲート通過候補だけを`cg_bestknown_loop_v1.py`へ接続する。

`ono-`は公開kernel作者名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42b028f10960d08ccb4980be5b76946f98b`由来のローカル識別子である。root deck bytesはAman、Makthanithin、Kojimar、Aristophanivan（2 snapshot）と一致し、単一の元kernelはrepo証拠だけでは特定できない。詳細evidenceは`docs/evidence/cg-self-owned-role-separated-v4-meta-cem-20260816.md`。

## 58. self-owned policy family v7 broad-support / deck-bound policy CEM（2026-08-16最新）

v1〜v6と別の生成方法として、公式`data/raw/EN_Card_Data.csv`と新規role spec `configs/meta_specialist/self_owned_cg_deck_spec_v5_broad_support.json`だけから8件の`broad-support-v7` deck＋P1-derived policy overlayを生成した。plan SHAは`9426e937cc089afc5e575c7c7d9ed8df390f8129e763dcc9f619d3b29171b298`、promoted rootは`runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-promoted/`、pool／fresh／meta／通常split SHAは`c70cb2906b7e9e7f3084d11a1ced052b946fa5c4c9baccb5e47eb92fc19810e9`／`17326c7267b7163e09544ce46c941acddeaea05649ddd7b5d961bfbdd336ffd0`／`62a6ae44fda0c9c4aad14ed03f54eb745c708eccdfe8234a043df83c2107d28a`／`40630d7525d313b7e70a1172ad69e880833080c3432e0ed2f4bea772c5b10e9b`。8 sourceはdeck／policy SHAとも相互distinct、`parent_deck=null`、`public_parent_read=false`、authority全false。両seat matched smokeは32/32`DONE`・fault0、splitは`META_TRAIN=6 / META_DEV=1 / META_FINAL=1`である。

policyとdeckの差を混ぜないため、v7 scratch deck（deck SHA `c771040e8d77921402de738f1c20dcebab088e4468202795fb0d84090cb902b0`）へP1 default controlを再bindした。CEM coreのsource/control deck bindingに合わせたdeck-bound split `runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-promoted/cg_self_owned_cem_split_v1.json` SHAは`bc38707e45c34234e33da3d1060ec3ac9c42951796ff0ecf7b2b5582d12dc847`である。

`runs/cg-self-owned-cg-policy-family-v7-broad-support-20260816-cem/`をcampaign seed`20261301`、population／elite`8／2`、1 generation、META_TRAIN_ALL、独立2 block×2 games/opponent×seat、positive／risk-aware gateで実行した。screen216局＋独立144局の全360局が`DONE`・fault0。screen上位c04（`cg-p1-cem-g00-c04-51fa620f2e8b`）は`17W-0D-7L`対control`14W-0D-10L`、`+12.5pt`。独立差は`+10.4167pt / +16.6667pt`（mean`+13.5417pt`、min`+10.4167pt`）だが、`seat_safe=false`、`opponent_seat_safe=false`。selectionは`risk_aware_independent_train96_x2_valid_candidates_below_elite_count_preserve_center`で、P1 center保持となった。DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py`、BestKnown／Champion／production／submission、commit／pushは不変である。

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v7は新meta source生成とdeck-bound policy-only CEMの実行可能性を確認したが、P2候補は得ていない。v7 poolのblind retryは行わず、次は相関の低い新policy lineageまたはdeck recipeをfresh epochとして生成し、全ゲート通過候補だけをBestKnown loopへ接続する。一次evidenceは`docs/evidence/cg-self-owned-policy-family-v7-broad-support-cem-20260816.md`。

## 56. self-owned policy family v5 / CEM（2026-08-16最新）

公式`data/raw/EN_Card_Data.csv`と`configs/meta_specialist/self_owned_cg_deck_spec_v4.json`だけから、8種類のself-owned deckへP1 parameter overlayを1対1で束ねる新source epochを生成した。plan SHAは`77afc8e46b7d6ca3b0b15d5b3f9e647f3f7f4d587481d6833ce5fc450dd2e9fc`、promoted rootは`runs/cg-self-owned-cg-policy-family-v5-20260816-promoted/`、pool／fresh／meta／split SHAは`509bb5b7b08a2af8b876fd4ed578c5ad64ca8a16ef6a7ddbdf5024ef2f7871a6`／`57a842a68aa3e7d0a500d68a253903b912c71d6103dd3c243a45381415f0621b`／`b8d263e22bf9b73925bf1ad4fd4becda14c1d44e08cf44066d132bd5ce755951`／`d7064695eeea863689c3c821fb0c69179dfa49e34a05a656e522fd8920931d`。splitはTRAIN4／DEV2／FINAL2、authorityは全false、`parent_deck=null`、`public_parent_read=false`である。

P1両seat smokeは16/16 `DONE`・fault0・11W-5L。P1 fixed CEM `runs/cg-self-owned-cg-policy-family-v5-20260816-cem/`はscreen144＋独立96を全て`DONE`・fault0で完走した。screen c07はcontrol比`+37.5pt`だったが、独立差は`[+31.25pt, 0pt]`、`seat_safe=false`、`opponent_seat_safe=false`。fresh fixed validationではTRAIN `−4.6875pt`、DEV `−3.125pt`、FINAL `0pt`だった。P1 center、BestKnown、Champion、production、submission、deck phase、`cg_bestknown_loop_v1.py`接続は不変である。

## 57. cross-archetype policy family v6 / CEM（2026-08-16最新）

v2／v3／v4の異なるdeck specを混ぜる`configs/meta_specialist/self_owned_cg_policy_family_v6_cross_archetype.json`（SHA `84b8d67e158cb701df82df398ea1a6a73258837c416ea93a0c8c9d69a3f8cf56`）を生成方法として追加した。初回`runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-retry1/`はcanonical collisionでfail-closed quarantineし、v3 recipeのseed／ordinalを変更したretry2を正とした。promoted rootは`runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-promoted/`、pool／fresh／meta／split SHAは`ca1c7c8124ffd3f40d88618b2b86b751423e732589e59e560a6aa4431740a0cd`／`d6ac59c615f06d438f9b0f5fb6ce5e01ecb4e1f1d380faf86f075faf3910c726`／`207a17049f71c482f5575c43fb31e9b41325436d2ae3556720c7338f2dd3ca24`／`11b41b9995b736e3ad7fd1074c2353cf78afd435e93eb8cfc845d6dc6928092b`。splitはTRAIN4／DEV2／FINAL2、P1両seat smokeは16/16 `DONE`・fault0・9W-7L。

P1 fixed CEM `runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-cem/`はscreen144＋独立96を全て`DONE`・fault0で完走した。screen c03はcontrol比`+50.0pt`だったが、独立差は`[−18.75pt, −12.5pt]`。c05も`[−25.0pt, −18.75pt]`で、positive／risk-aware／seat-safe gateを満たす候補は0件、P1 centerを保持した。判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`である。

### CANONICAL OVERRIDE（v5/v6 CEM最新）

現行BestKnownはself-authored P1 policy＋common/public root deckで不変。v5/v6はself-owned deck＋P1-derived policyのmeta source生成方法を検証したepochであり、提出候補のdeckをself-ownedへ置換した成果物ではない。次は同じpoolのblind retryを行わず、相関の異なるsource recipeまたはpolicy lineageを新epochで生成する。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たす候補だけを`cg_bestknown_loop_v1.py`へ接続する。旧Rule v0／Student・AWR・BC／deck blind sweep／Champion変更／commit／push／Kaggle提出は行わない。

CEM parentのnative `cg` importによる`buffer full`は、`scripts/run_cg_static_smoke_v1.py`専用subprocessへ隔離した。関連suiteは32 passed、通常12 workerのv5/v6 CEMは全row fault0で完走した。一次evidenceは`docs/evidence/cg-self-owned-policy-family-v5-v6-cem-20260816.md`。

## 59. independent root policy lineage source epoch（2026-08-16最新）

P1-derived overlayと混ざらない新しいsource generation方法を追加した。親は公開root policy `root_cg_submission_agent_v1.py` SHA `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`だけで、P1 SHA `1c505…`は親に含めていない。renderer／package／generatorは`src/mage_ptcg/meta_specialist/cg_independent_policy_renderer_v1.py`、`self_owned_cg_independent_package_v1.py`、`scripts/generate_self_owned_cg_independent_policy_meta_v1.py`。plan `configs/meta_specialist/self_owned_cg_independent_policy_family_v1.json` SHA `0809a6e335ea1b14433074f06c70ce405fced7f6d05795e31a131a02adda1f89`。

公式カード CSV＋新規role specから8 sourceを生成し、parent_deck=null、public_parent_read=false、authority全false、source kind `self_owned_official_card_data_deck_with_independent_root_policy`でsealした。staged rootは`runs/cg-self-owned-independent-root-policy-family-v1-20260816/`、promoted rootは`runs/cg-self-owned-independent-root-policy-family-v1-20260816-promoted/`。1 worker source smokeは16/16`DONE`・fault0。pool／fresh／meta／split SHAは`5ebfe26de43e858db37d52dcab43509c49f6495899df9159b1076d36944fa1a7`／`a8d1ec399345d154a105fc1c0ababf219e8659793656ccd83e1fda78b9f0e2bc`／`6a7a2a4d0fc7abbe46260dae51315e554627082ad152ed156ec9b5b5ccb68916`／`2766a71abbca3caa8e5d06cac7fca8a72232666ba709ca939525d2796b5a555b`。splitはMETA_TRAIN=6／DEV=1／FINAL=1。

12／4 workerの同一source smokeはlibcg `buffer full (capacity:7)`で不完全ledgerとなったため性能・promotion根拠から除外し、このepochはworker1を固定する。stdin spawn trialも不採用。成功artifactは`smoke-v4`のみ。

promoted poolのMETA_TRAIN 6 sourceで、8 independent variantを同一deckのP1 controlとmatched screenした結果は全row fault0だが、delta `−8.3333pt`〜`−41.6667pt`。balancedの別fresh 8 source screenも`−12.5pt`。positive／seat-safe候補は0件で、CEM、DEV／FINAL、`cg_bestknown_loop_v1.py`、BestKnown／Champion／production／submissionは不変。fresh poolはMETA_TRAIN exposure済みとして再利用しない。

判定は`SOURCE_GENERATION_PASS / POLICY_LINEAGE_NO_UPDATE / BESTKNOWN_UNCHANGED`。`ono-`は作者名ではなくlocal Git identity／branch由来の識別子である。一次evidenceは`docs/evidence/cg-self-owned-independent-root-policy-family-v1-20260816.md`。次は同じsurfaceのblind retryではなく、別の明示されたpolicy lineageまたはsource generation epochを作り、exposure ledgerと独立holdoutを先に固定する。

## 60. self-owned policy family v8 stability / CEM / 未使用holdout（2026-08-16最新）

公式`data/raw/EN_Card_Data.csv`と`configs/meta_specialist/self_owned_cg_deck_spec_v6_stability.json`だけから、8件の`stability-v8` self-owned deck＋P1 parameter overlayを生成した。plan SHAは`cad29a9c58f8509e912a797c72a5ba56d7eedc8438c4bcab283b890a2a479a18`、promoted rootは`runs/cg-self-owned-cg-policy-family-v8-stability-20260816-promoted/`、pool／fresh／meta／split SHAは`deacc8f685e9d78ac2b196df2adb719c730e43c336d39901d1e1c19eae393245`／`f36ea1945b9d23c3b6a6cc2631e57300b523d14955bfaa788f9d02539bdd3d75`／`9e8a10747f9ae84992e36dd88960fad4070efa19ea854ca50d31360972d1a0e2`／`5c193e70dc0e0c57f73e7277ab3367f251b5de531dcb4645bf0849f11bc88058`。full package smokeは16/16`DONE`・fault0。staged intermediate rootでの`buffer full`は`cg` runtime欠落のため除外した。

balanced-v8 deck-bound split SHAは`3cbfc30f4c72ed0c8f2dff0412de4683f0e5803a4a0830789ba1f619d249377a`。P1 fixed CEM `runs/cg-self-owned-cg-policy-family-v8-stability-20260816-cem/`はseed`20261401`、population／elite`8／2`、1世代、screen216＋独立144、全row`DONE`・fault0。c06（`cg-p1-cem-g00-c06-5eaa501e4f94`）はscreen`+8.3333pt`、独立`+33.3333pt / +12.5pt`（mean`+22.9167pt`）だが`seat_safe=false`、`opponent_seat_safe=false`であった。CEM centerとP1は不変。

CEM選定後、未使用META_DEV／META_FINALを各stage candidate/control 64局で固定holdoutした。両方ともcandidate36/64、control33/64、差`+4.6875pt`・fault0だったが、candidate seat gapはDEV`0.0625`、FINAL`0.125`で厳格な`0.05` gateを超えた。したがって判定は`SOURCE_GENERATION_PASS / POLICY_IMPROVEMENT_REPRODUCED_BUT_STABILITY_GATE_FAIL / BESTKNOWN_UNCHANGED`。P2／P3、deck phase、`cg_bestknown_loop_v1.py`接続、BestKnown／Champion／production／submissionは不変である。

`v8`のMETA_DEV／META_FINALは性能使用済みとして再利用せず、同じsource／seed／c06のblind retryもしない。次はc06近傍を狭い初期分布で探索する別seedの新meta epochを生成し、全gate（`legality → static safety → bounded fault0 → TRAIN-only CEM → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`）を再実行する。一次evidenceは`docs/evidence/cg-self-owned-policy-family-v8-stability-cem-20260816.md`。

## CANONICAL OVERRIDE — v9〜v11 source/CEM・deck診断（2026-08-16）

v9 c06-neighborhood、v10 broad-support、v11 heterogeneousの3 self-owned source epochを生成・promoteした。採用したsource smoke／CEMは全てfault0だが、v9 c01/c02、v10 c06、v11 small／wide候補はstrict opponent×seat-safe gate外で、selectionはincumbent-center。v9 c01の96局/opponent/seat拡大は`−2.691pt`へ反転、v10 c06の同拡大は`+3.2552pt`だがopponent×seat gaps`0.0833〜0.1771`、v11 wide c07は独立`+12.5/+6.25pt`だがopponent×seat-safe falseである。

v11 core deckの完全package `runs/cg-self-owned-cg-policy-family-v11-heterogeneous-20260816/packages/core-v11-00`とv8 balanced controlを同じc06 policy surfaceで比較した320-row TRAIN-only診断は全row DONE・fault0、candidate objective`0.5500`対control`0.490625`（差`+5.9375pt`）。candidate seat gap`0.1750`で昇格不可。promoted source-only directory直渡しの`buffer full`は`cg/` runtime欠落による不完全試行であり、性能結果に算入しない。v10／v11 DEV／FINALは未使用のまま保全する。

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。現行BestKnownはself-authored P1 policy＋common/public root deckで不変。次は完全package runtimeをsource promotion前にsealedし、opponent×seat相関の低い新しいmeta source／deck recipeを生成する。同じpoolのblind retry、DEV／FINAL読出し、`cg_bestknown_loop_v1.py`接続、BestKnown／Champion変更は行わない。詳細evidenceは`docs/evidence/cg-self-owned-policy-family-v9-v11-cem-20260816.md`。

## 63. 公開kernel fresh union4 / root deck固定 P1 CEM（2026-08-16最新）

新しい公開 source acquisition epochとして、`sgzk001` engineering、`sushanth` Alakazam、`prvsiyan` static tusk、`sushanth` Lightning の4 snapshotを fail-closed intakeで受理した。各2局の runtime smoke は全て `DONE`・fault0。avikdas strategy、Grimmsnarlex／Mega Emboar、Dragapult v2／PalafinのACE SPEC不正、および既使用 source/artifact identity は除外した。

sealed union rootは `runs/cg-kaggle-kernel-meta-promoted-fresh-union4-rootdeck-v2-20260816/`、pool／fresh／meta／split SHAは `f82aedcadce8a807bcbbcc3821e2b9fb7180dc6be0bc44da5d3fb9d9b8682e72`／`28be3f56df6d6326dce656ff463f466a952fd746f176238cc6048d5ad5ed41b5`／`851c10a78c74c08d3febf2fd72e0c7bb775dc52ec8d0bd1a58f064132590b85f`／`5c078f66e566726627be0036aceb761657cdaf30c75a71dce6f256d676f781a9`。splitは `META_TRAIN=2 / META_DEV=1 / META_FINAL=1`。CEM中はTRAINの2 sourceだけを読んだ。

root deck SHA `2a541d7b...`を固定した P1 CEM `runs/cg-p1-cem-fresh-public-union4-rootdeck-v3-20260816/` は seed `202608961`、population／elite `8／2`、1世代、screen 72/72 rowを fault0で完走したが、8候補全てが `seat_collapse=true`・`valid=false`、elite 0件となった。従って独立再評価、DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py`は未実施で、P1／BestKnown／Champion／production／submissionは不変である。今回の union TRAIN と candidate は性能使用済みとし、同じ source／seed／候補の blind retryはしない。

### CANONICAL OVERRIDE（public fresh union4最新）

現行BestKnownは self-authored P1 policy＋common/public root deck。`ono-`は公開kernel作者名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42...`由来のローカル識別子である。次は source ID／policy SHAのexposure ledgerを守り、runtime smokeと性能 holdoutを分離した相関低減source epochを生成する。全strict gate通過前に `cg_bestknown_loop_v1.py`、BestKnown／Champion変更、commit、push、Kaggle提出を行わない。詳細evidenceは `docs/evidence/cg-kaggle-kernel-meta-fresh-public-union4-cem-20260816.md`。

## 64. 公開kernel cross-lineage epoch5／self-owned CEM再確認（2026-08-16最新）

公開kernel intake epoch5dで、Prvsiyan Alakazam v12、Sushanth Mega Emboar、Sushanth Zacianの3 sourceを受理した。Samrishは`source_identity_reused`、Siddharajは`dynamic_execution`、Sushanth Greninjaは`'Card'`／`invalid_deck`で除外した。final fresh SHAは`56d41011cb5d1ab1defe7ca5e96b716598a832c65af2631c9c31b3acf382b98f`である。

3 policy parent×3 deck parentの非対角6組を`CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1`で生成した。cross poolをP1と両seat各2局でsmokeし、24/24`DONE`・fault0・21W-3L。promoted rootは`runs/cg-cross-lineage-meta-promoted-public-fresh-epoch5-p1-20260816/`、pool／fresh／meta／split SHAは`fa22538880d29ce7cd9e322991cf9a94d93e03b44d45acdfa4bc14a5f3244f08`／`9ca211e8a5f00460c79a96596e232d4e1e8c24cb26aa3397455dd3f5e22f3494`／`b83513fdb3b6bae89c88156e7c7a3f1dcbc746736b0743f8806bcff25f3fa052`／`cb55300b15dc8cf8c7d23521977705bb25570ffd3c0e386fd719bad827a3c844`。splitはTRAIN4／DEV1／FINAL1、全行`training_exposure=0`・`local_eval_only`である。

`runs/cg-self-owned-cg-policy-cem-cross-lineage-epoch5-g01-20260816/`でself-owned deck-bound P1 CEMをseed`202608965`、population／elite`8／2`、1世代、screen144＋独立96局で実行した。全rowは`DONE`・fault0。screen c01はcontrol比`+31.25pt`だったが、独立2反復は`+12.5pt / −6.25pt`、risk-aware min delta`−6.25pt`、seat／opponent-seat safe falseで、`incumbent-center`を保持した。c01を新seed`202608967`、8局／opponent／seatで64局／armへ拡大確認すると、候補48勝・control54勝、delta`−9.375pt`だった。screen上の改善は再現していない。

判定は`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py`、BestKnown／Champion／production／submissionは不変。同じcross pool／c01／seedのblind retryは禁止する。次はpolicy SHA・deck SHA・generator lineageの相関を下げた新source epochを生成し、source smokeとexposure ledgerを先に固定する。一次evidenceは`docs/evidence/cg-kaggle-cross-lineage-epoch5-cem-20260816.md`。現行BestKnownのラベルは「self-authored P1 policy＋common/public root deck」であり、deckまでself-ownedとは扱わない。

### CANONICAL OVERRIDE（cross-lineage epoch5最新）

最終目標はself-ownedかつ提出可能なdeck＋policyで、実CABT勝率を主指標に`policy → deck → policy`を回し、独立seed・未使用metaでBestKnownを更新し続けること。source generationが成功しただけではP2とみなさない。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全通過した候補だけを`cg_bestknown_loop_v1.py`へ接続する。旧Rule v0、Student/AWR/BC、性能使用済みpoolのblind retry、Champion変更、commit、push、Kaggle提出は行わない。

## CANONICAL OVERRIDE（2026-08-16 v11 holdout／epoch8 intake最新）

v8 c06のv11未使用DEV／FINAL transferは平均positiveだったが、seat gap 12.5pt／25.0ptで昇格不可。現行BestKnown、P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、Champion、production、submissionは不変である。

新しい主線は`MULTIAUTHOR_EXPOSURE_FIRST_INTAKE_V1`。Emanuellcs／Nursrijan／Res1235のepoch8 intakeは全件fail-closed（accepted 0）であり、smoke／CEM／holdoutへ接続しない。次は既評価sourceのblind retryではなく、新規permission済みsource snapshotを少なくとも3件、exposure ledgerでTRAIN／DEV／FINALを予約してから`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を実行する。3件に満たない間はheavy CABTを起動しない。旧Rule v0、Student/AWR/BC、deck blind sweep、Champion変更、commit、push、Kaggle提出は行わない。

`ono-`は公開kernel作者名ではなく、local Git identity `bfe-lab-ono`／branch `agents/ono-cg-lethal-v1`／commit `1965b42b028f10960d08ccb4980be5b76946f98b`由来のローカル識別子である。根拠は`docs/evidence/cg-v11-unused-holdout-and-public-multiauthor-intake-20260816.md`と実repoのsealed artifactを優先する。

## 65. self-owned deck-adaptive source v2 / P1 CEM（2026-08-16最新）

新しい source generation 経路として、公式 `data/raw/EN_Card_Data.csv` と repo 内 self-owned deck specから6件の deckを生成し、公開状態だけを読む generic `cg-deck-adaptive-public-state-v1` rendererで各policyを生成した。過去 kernel policyを親にしていない。planは `configs/meta_specialist/self_owned_cg_deck_adaptive_family_v2.json`、SHA `ac65f796f66bf34284a439d74eed9fa187922284e2dcee2e81e6af9c6d263a5c`。v1 grass variantはSTEP_LIMIT faultが再現したためquarantineし、v2はfire／dark／lightning／fighting／water／psychicへ限定した。

promoted rootは `runs/cg-self-owned-deck-adaptive-v2-20260816/promoted/`。pool／fresh／meta／split SHAは `96525ece441063ad37c3236f275ea2d66c00949dd977bf3ad33f6f2008f7e568`／`4a5256a120c763acb8cbf172dc26a0f50803b4bab813fee7efa9d4a8acab8259`／`dc38ce1266ceed5148eba6884539ab2c6dba336ccbc8612b58d1e80d31f81465`／`a8768957b28dc382cf00d63f68a843f6cb1fc2d53d2e8b5c0b371001ca2abd8e`。source smokeはworker1/recycle1、192/192 `DONE`、fault0、draw0、`10W-0D-182L`。これはsourceをopponentとしてP1と対戦したruntime smokeであり、BestKnown性能ではない。

P1 fixed CEM `runs/cg-p1-cem-deck-adaptive-v2-20260816/` は seed `2030862901`、population／elite `8／3`、2世代、META_TRAIN_ALL、独立re-evaluation 2回、positive-delta gate。screen各108局、独立各96局、DEV64局を全てfault0で完走したが、独立positive／seat-safe候補は0件、selectionは`incumbent-center`。g01 DEVのcenter `25/32` 対 control `24/32`は同じP1 policyのunpaired参考値で、P2とは認めない。

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。BestKnownはself-authored P1 policy＋common/public root deckのまま。今回のpoolは性能使用済みで再利用しない。次は相関の低い新source lineageを新seed namespaceで作り、`legality → static safety → bounded fault0 → TRAIN-only CEM → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たした候補だけを`cg_bestknown_loop_v1.py`へ接続する。`ono-`は公開kernel作者名ではなく、local Git identity／branch／commit由来のローカル識別子である。一次evidenceは `docs/evidence/cg-self-owned-deck-adaptive-v2-cem-20260816.md`。

## 66. seat-conditioned self-owned source と robust-source pool v2（2026-08-16最新）

P1の公開`yourIndex`×action-family offsetを探索する renderer／CEM／generatorを追加した。公式カードCSV＋self-owned deck specから6 sourceを生成し、bounded smoke 192/192 `DONE`・fault0でpromotionしたが、seat-conditioned CEMのscreen best `+12.5pt`は独立`−6.25/+15.625pt`へ揺れ、seat/opponent×seat gate不成立だった。`research_gate_pass=false`、BestKnown不変。pool／fresh／split SHAは `065e1e64d4551305b1ec4ce472f2248a51fdb8c571d3ad86a5453252f6c9d5df`／`4cd12f028d10bc99d4ed8a394bcb074d5d45b30194f17683ebc7ddba6a76e808`／`6dced5d2b9178fd8b6bcb6e27fe2adf63236f2a6112396bc6f0f4e7d71ce1b37`。

robust-source epoch 9/11/12/13の未downstream使用4 sourceも別rootへ封印し、P1 source smoke 8/8 `DONE`・fault0を確認した。新poolのP1 CEM（seed `2026089702`、screen72＋独立96）はscreen `−12.5〜−25pt`、initial top独立`+25/0pt`でrisk-aware／seat gate不成立、`incumbent-center`×2。pool／fresh／split SHAは `a9b9f724b32ffa4c2aa91d28abc60f6fe10c0b4861d18f760d623783c513bd0f`／`0afca0d2fea1fbaf6dc09f383ff095736aedb968f3d252ca20f661e3784ed592`／`070d97736e9af155fdb8247583f49e4a01fb0a12bcb632bc4f5af1c2c29b4adc`。

判定は `SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。両poolは性能使用済みとしてblind retryせず、DEV／FINALは未読のまま保全する。P1 policy SHA `1c505b2b...`、root deck SHA `2a541d7b...`、Champion、production、submission、`cg_bestknown_loop_v1.py`、commit、pushは不変。一次evidenceは `docs/evidence/cg-seat-conditioned-and-robust-source-cem-20260816.md`。

## 67. deck-conditioned adversarial self-owned source と P1 CEM（2026-08-16最新）

公式 `data/raw/EN_Card_Data.csv` と repo 内 self-owned deck recipeから6 deckを生成し、P1 parameterized policyを各deckへ再結合する `self-owned-cg-deck-conditioned-adversarial-source-v1` を実装した。generatorは `scripts/generate_self_owned_cg_deck_conditioned_adversarial_meta_v1.py`、planは `configs/meta_specialist/self_owned_cg_deck_conditioned_adversarial_family_v1.json`（SHA `591cfee66de6f1964e0d54a6c8b390d47202980ec5f7d69e61318db07e53007d`）。promoted pool／fresh／meta／split SHAは `f8c2e4fe3735730665bd8234ef48c628809373e22cce895c74043add7b7233aa`／`786d1d8b186e060b3c664ab2e3375c0a58b04c63ff20dbc21b86eaa57c67f9d9`／`f3de7856cb0b4bbe72552ba1f3795c5c622bf4171a99f7b5bb505c29f2d6d6f5`／`2e16dffed86c89982e380d9d0d76a25653a716ebae15af8c265372553a1c983c`。splitはMETA_TRAIN 4／DEV 1／FINAL 1、DEV／FINALはCEM中未読である。

bounded source smokeは192/192 `DONE`、fault0、draw0、source側`41W-0D-151L`。P1固定CEM `runs/cg-p1-cem-deck-conditioned-adversarial-v1-20260816/` はseed `2026089801`、population／elite `8/2`、2世代、独立re-evaluation 2回、positive／risk-aware gateで全row fault0を完走した。gen0 screen best c05はcontrol比`+18.75pt`だったが、独立2 blockは`−18.75pt / −6.25pt`（mean `−12.5pt`）へ反転。gen1 screenは全候補負差分、DEVのincumbent centerはcandidate `6/16` 対 control `9/16`（`−18.75pt`）で、両世代の選択は`incumbent-center`となった。

### CANONICAL OVERRIDE（deck-conditioned adversarial source最新）

判定は `SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。現行BestKnownはself-authored P1 policy＋common/public root deck。今回のpool・candidateは性能使用済みとしてblind retryしない。candidateがstrict gateを通過しなかったため、META_FINAL、deck phase、`cg_bestknown_loop_v1.py`接続、BestKnown／Champion変更、commit、push、Kaggle提出は行っていない。次は相関の低い別policy/deck lineageの未使用metaを生成し、`legality → static safety → bounded fault0 → TRAIN-only CEM → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を通過した候補だけをloopへ接続する。詳細evidenceは `docs/evidence/cg-self-owned-deck-conditioned-adversarial-cem-20260816.md`。

## CANONICAL OVERRIDE（2026-08-16 margin-gated source最新）

`cg-p1-margin-gated-v1`は、sealed P1のscore差が`score_margin`以内の合法選択だけへactor-visible補正を加える新しい未使用source generation経路である。公式カードCSV＋新seed namespaceから6 self-owned deck＋policyを生成し、promoted pool／fresh／meta／split SHAは`4cd6f198a15c4bd4f1121b5c72e782c9ac0bac759b712fb20d703eb8603e7489`／`cb9da86d6762fbef87d877ced8691f4cb7d1fbd11819641685392c26f83670cb`／`dbdeee18d06c74bd557c08267d0c5be45a5c62ce9dac27d4c657c006c96a91e9`／`9d5e942a2bc85be4df155b809aaf1efd3f3da1e4289371ff7cb6db9a7e4353f5`。splitはMETA_TRAIN 4／DEV 1／FINAL 1、全行training exposure 0である。source smokeは192/192 DONE・fault0。

corrected P1/root-deck CEMは`runs/cg-self-owned-margin-gated-cem-v1-corrected-20260816/`、seed`2026081801`、population／elite`8／2`、2世代、screen各128、独立各128、META_TRAIN only。全row fault0だが、gen0独立は`−18.75/+6.25pt`、`+6.25/−12.5pt`、gen1独立は`+6.25/+37.5pt`、`+6.25/0pt`でstrict positive＋seat≤5%＋opponent×seat≤5%を満たさず、accepted 0・center保持となった。初回fast runはscreen control stratum混入を検出して破棄し、block限定回帰後のcorrected runだけを採用する。

判定は`SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。META_DEV／META_FINAL、deck phase、`cg_bestknown_loop_v1.py`、BestKnown／Champion、production／submission、commit／pushは不変。今回のpool・candidateは性能使用済みでblind retryしない。次は相関の低い別lineageの未使用metaを生成し、全strict gate通過候補だけをBestKnown loopへ接続する。`ono-`は外部作者名ではなく、local Git identity／repo path由来の識別子である。詳細evidenceは`docs/evidence/cg-self-owned-margin-gated-cem-20260816.md`。
