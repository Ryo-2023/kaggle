# Rule v0/root deck 2-card coordinated package screen（2026-08-14）

## 結論

提出互換の Rule v0 と root `deck.csv` を固定し、1-card hill-climb ではなく2-card coordinated packageを2件生成した。`[1123, 1142] → [1086, 3]` の候補 `8de3e32b1ed3f3c229c418412a722d99384b3986b28797a0a8d7d6eb15f5a057` は weighted48、common24、seed-disjoint 384、768 まで全て fault0 で正の差を示したが、差は 384 の +3.385pt から 768 の +1.432pt へ縮小した。したがって candidate-only とし、Champion、submission package、longrun、1536、Kaggle submission は変更・起動しない。

もう1件の package `ad5b284c34d6167bb91ec79ee60ac9bd67fb3c8f12f3d3798e70c5f3234d32c6`（`[1102, 1227] → [1086, 1086]`）は weighted48 で negative のため common24 へ進めなかった。

## 固定資産と境界

- policy: Rule v0 root closure SHA `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- parent deck: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/deck.csv`（既存 root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）
- parent/candidate evaluationは同一 broad pool、同一 evaluator、同一 seat/opponent/repetition strata、seed pairingで実施した。
- 全artifactは research-only、authority flags全false、heldout training exposure=0。production `main.py`/`agents`、Champion、submission package、既存artifactは変更していない。
- `AGENT_INVALID` と fault は勝率へ変換せず、今回の採用armは全て `DONE`/fault0 とした。

## 段階結果

### runtime smoke / weighted48

weighted root `runs/final-sprint-autonomous/rule-v0-root-deck-package-v1-20260814` で親＋2候補を各2局（両seat）の runtime smoke に通し、6/6 `DONE`/fault0。その後 workers=12、recycle=16、META_TRAIN weighted48（各arm48、計144局）を実施した。

| arm | weighted result | 判定 |
|---|---:|---|
| parent | 4-0-44 | — |
| `8de3…` (`[1123,1142]→[1086,3]`) | weighted 0.0969716、+0.9655pt | common24へ |
| `ad5b…` (`[1102,1227]→[1086,1086]`) | weighted 0.0707446、−1.6572pt | STOP |

weighted manifest SHA `86bc468305cc21175e755dfff60d7a3e9decee5e294ca12fb549503d10dc12d2`、summary SHA `9249d00f9f655a6c1080677f19955fcbb47ee1cba510ed1b68b7e30cde68a688`、runtime smoke SHA `da3e6410108d48160d53f726bf752218870632f744d1ff05ad91d315cab61e44`。

### common24

common24 root `runs/final-sprint-autonomous/rule-v0-root-deck-package-common24-v1-20260814` は parent/candidate 各96局、計192局、workers=12/recycle=16、全DONE/fault0。親7/96（7.2917%）に対して `8de3…` は12/96（12.5000%）、+5.2083pt。paired strata、seed、seat、GID、heldout exposure=0 のgateを通過したため、候補1件だけを384へ送った。

common24 manifest SHA `a0e7acbc3692a1cfe17454b7061845e8791209a21fcd962f23d828bf668a124a`、summary SHA `fd3e02e49bb7ed4871c4fff8bf7637ef2d456d759dd983eb62512abd70b27b39`、summary Markdown SHA `a38e34aaa893791d99a2e109e588e5b1d49aa8968bbda035d249375d4ff4f65f`。

### seed-disjoint 384

confirmation root `runs/final-sprint-autonomous/rule-v0-root-deck-package-confirmation384-v1-20260814` は parent/candidate 各384局、計768局、base seed `23683000`、workers=12/recycle=64、全DONE/fault0。親42/384（10.9375%）に対し候補55/384（14.3229%）、+13勝/+3.3854pt。両seat192/192、24 opponents×16、paired seed/GID gate、ResourceGovernor normalを確認した。

confirmation manifest SHA `e2d8b39f06e08551faed368ad142b0b060cea66479222f3cf8f1adce78559e6c`、summary SHA `d0dbedfd426415a54c031ad26eb676e28575d70a795205b566641a94fbd5993c`、summary Markdown SHA `d967d6b618f9a9726b84bc5015001ec0c436b41fe096382dd3f7eee72a63a7dd`、final SHA `4e409d23c63971c447c7b1324e3e75bf2b15119b752781b49551dba2bd19e722`。

### seed-disjoint 768

confirmation root `runs/final-sprint-autonomous/rule-v0-root-deck-package-confirmation768-v1-20260814` は parent/candidate 各768局、計1536局、base seed `23684000`、workers=12/recycle=64、全DONE/fault0。親71/768（9.2448%）に対し候補82/768（10.6771%）、+11勝/+1.4323pt。両seat384/384、24 opponents×32、paired seed/GID gate、ResourceGovernor normal、restart/killなしを確認した。384の差は再現したが縮小しており、強い昇格根拠とは扱わない。

768 manifest SHA `6f924bfadc7f4638fa46757618ff5779abe31330fa0d9855972590ab7c1fa242`、summary SHA `0704284fdf75b26f7e830d462b28e14782784a93215ecab615e26d880172033c`、summary Markdown SHA `697491a91480b86ed79c518309c80960f6a31a9e4b4a72cce1c4df86b27fae8a`、final SHA `0fdb1a8ccbcf965050d050d60c3943adeee4f1ee5c9a8eedaf8a7635d5abd27a`。

generic confirmation runnerの出力ファイル名は互換性のため `confirmation384_summary.*` のままだが、768 wrapperが `GAMES_PER_SEAT=16` と confirmation768 schemaを束縛している。数値は768 runの実artifactから再導出した。

## 次の判断

この package は現時点のSubmissionEligibleBestKnownを置き換えない。768で+1.4323ptに縮小したため、同候補の1536、longrun、blind retry、policy training、native teacher collection、promotion、submissionは起動しない。次の性能ループは既評価multisetを除外した新しい2-card packageまたは別の明確なsubmission-compatible policy surfaceを、runtime smoke→workers12 weighted48→common24の順で選ぶ。

検証: package/confirmation focused tests 10 passed、py_compile PASS、実行中processなし。docs validatorと`git diff --check`はcontext pack更新後に再実行する。

## 継続 v2（別generator seed、common24で停止）

v1 packageの同一candidateを再実行せず、generator seed `23685000`で別packageを2件生成した。`39d0ce…` は `[1141,1182]→[3,1097]`、`cdb179…` は `[1142,1152]→[3,5]`。runtime smokeは6/6 DONE/fault0、weighted48（144局、workers12/recycle16）では親4/48、候補4/48（+0.4089pt）、4-1-43（+0.7816pt）だった。common24（各96、計288、workers12/recycle16）は親17/96に対して候補11/96（−6.25pt）、12/96（−5.2083pt）となり、両方hard-negativeとして384へ進めなかった。

v2 weighted manifest SHA `9fe2e0507cc7fe7d53ee14126ed663b55300963e97d1ef255b3a5ce367ff35bb`、weighted summary SHA `bb2fffb6feae417586952f47a53e732db49c041337e919981d96a0f9724ff2fe`、common24 manifest SHA `672fe5f8edc71d36638b681873168c6273332ea212fa8771f2a25eb0f015b4af`、common24 summary SHA `48a2e83f5c68073bbea0bae05effaf7127c9f9658c1f043fdb697073876e913d`。

## 継続 v3（common24 positiveの384反転）

別generator seed `23688000`で `75719f…`（`[1102,1102]→[1219,3]`）と `94f110…`（`[1141,1252]→[3,1198]`）を生成した。runtime smokeは全6局DONE/fault0。weighted48（144局、workers12/recycle16）は親5/48、`75719f…` 2/48（−7.1233pt）、`94f110…` 6/48（+0.8530pt）だった。

`94f110…`のみcommon24へ進め、親9/96対候補11/96（+2.0833pt、192局DONE/fault0）となった。seed-disjoint 384（base `23691000`、workers12/recycle64）は親48/384対候補44/384（−1.0417pt、768局DONE/fault0）へ反転したため、candidate-only/STOP。1536、longrun、同候補のblind retry、promotion、submissionは起動しない。途中の初回 wrapper 呼び出しは候補ID固定による fail-closed選別エラーで実験を開始せず、CLI `--candidate-id`束縛を追加して正しい候補を一度だけ実行した。

v3 weighted manifest SHA `02a1ec99529048a83cf8644bd2d489f46020afd297b2b4ae14e28797daddfbe2`、weighted summary SHA `79436c9c2f5590b68a7dd903e434d28787db12a5daa9cbe1a7c08f46da42cf4d`、common24 manifest SHA `08181ec951577833efce4fba9125f7b0fc1319a4c14f6a0b6a7a784dd463c0dc`、common24 summary SHA `1bc88a8f6be7e7991246a70878fb02ac1173224a51f1985c7e7a34d9f0482a15`、confirmation384 manifest SHA `1cbfaba79729b368dc724088794fc053987dee4e7807fd0958042a8d72293e2e`、confirmation384 summary SHA `bc99e66dd0439dcd9013aec00f6131a4ccb5726ac8b86e2ac128fcd713b0e921`。

## 継続 v4（common24で停止）

v1〜v3とnoveltyを分離した2-card packageをgenerator seed `23692000`で2件生成した。`06c7d58d…` は `[1142,1182]→[3,3]`、`651da340…` は `[1182,1192]→[3,5]`。runtime smokeは6/6 DONE/fault0、weighted48（親＋2候補、144局、workers12/recycle16）では親4/48、候補4/48（−0.0444pt）と6/48（+4.6449pt）だった。陽性候補だけcommon24（親・候補各96、192局）へ送り、親10/96対候補10/96（0.0pt、全DONE/fault0）へ反転したため384へ進めない。一次evidenceは `docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v4-20260814.md`。v4 weighted manifest `fed3db9cba03fe15ae7ebcc8f0d4c722ad758ee13062d3d63262917cd62acec6`、weighted summary `18693dd5ee2f7d0d5c5ab378ffb5054052b224dfb9c1f1c70d3953e3106c5292`、common24 manifest `e3667ae9598bc8bb9ccc9cd14ca9ef1b3dea35fdb1ad10ba67709af4b5d2f1b3`、common24 summary `18701430945549fbfaeafa7728727d11075fa7323b6961a901faacf85b306283`。1536/longrun/promotion/submission、同候補blind retryは起動しない。

## 継続 v5（weighted positiveがcommon24で停止）

generator seed `23695000`で、`0b49700b…`（`[1152,1182]→[3,3]`）と`fc0bfd8d…`（`[1141,1227]→[5,3]`）を生成した。runtime smoke 6/6、weighted48 144/144は全てDONE/fault0。weightedでは親2/48に対し候補5/48（+6.2545pt）、6/48（+8.9595pt）だったが、common24では親13/96、候補13/96（0.0pt）と候補11/96（−2.0833pt）へ反転した。v5はcandidate-only/hard-negativeとして停止し、384/768/longrun/promotion/submission、同候補blind retryは起動しない。一次evidenceは `docs/evidence/autonomous-rule-v0-root-deck-coordinated-package-v5-20260814.md`。v5 weighted manifest `ded751844988cb7500f4c4e13122994cf0158537783a375dc3bc542d1f744879`、weighted summary `7cd537df3bd822b5544ef2d5c78d2fe5a142c4f14eb7a2e65104c39a4e4b48f2`、common24 manifest `d7da287c87e8ab5b1e660898be3328d8d1811dc27d01cf4a9b6bb6e6458c5569`、common24 summary `6674c5df7f62c3a0d52a9d4b0e7970e886afc2ad1c46104e7e8b68e6758d3243`。
