# cg-lethal P1 週末性能スプリント実装計画

## 目的

`cg-lethal-target-v1` と root deck を不変の research parent として保持し、既存の合法手・fallback・非公開情報境界を変更せずに、実 CABT 勝率を目的とした parameterized policy、同一観測 shadow telemetry、再開可能な CEM runner を研究専用の新規ファイルとして追加する。既存 submission branch、Champion、既存 artifact は変更しない。

## 設計

- P1 source SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` を immutable parent として検証する。
- 実在する P1 score surface から有限な整数パラメータを抽出し、default config では既存 P1 と action parity を保証する。
- shadow は一つの実ゲームで behavior action だけを実行し、同じ immutable observation を P1/candidate に渡して public projection と候補 score を記録する。shadow action は実行しない。
- META_TRAIN/DEV/FINAL は既存 pool/manifest に hash-bind し、候補自身を評価相手から除外する。DEV/FINAL の過去全履歴未使用は推測せず、campaign 境界と exposure metadata を明記する。
- CEM は外部依存を追加せず、deterministic sampling、fault-inclusive ranking、P1 control、atomic checkpoint、resume を持つ。ResourceGovernor が許可した場合だけ coordinator が heavy run を起動する。

## 実装タスク

1. **P1 parameterization**
   - failing parity/config/identity tests を先に追加する。
   - source renderer、config validation、candidate package materializer を実装する。
   - default package の compile と fallback/action parity smoke を実行する。

2. **Weekend split contract**
   - P1/pool/META manifest/evaluator/source SHA を含む新規 split config を作る。
   - disjointness、candidate-self exclusion、weight、permission、exposure metadata を検証する。

3. **Same-observation shadow telemetry**
   - behavior wrapper と side-effect-free shadow evaluator を実装する。
   - 既存 public telemetry normalizer と forbidden-field scan を再利用し、raw private observation を保存しない。
   - decision divergence、legal semantic digest、score breakdown、fault を検証する。

4. **CEM runner**
   - deterministic population sampling、elite update、objective/penalty、P1 control、generation manifest、atomic resume state を実装する。
   - 既存 arena/evaluator と ResourceGovernor を薄く接続する。候補 package の policy/deck/archive/manifest/config/split SHA を実行 metadata に束縛する。
   - CLI の dry-run/smoke と one-generation pilot を実行する。

5. **Pilot / campaign gate**
   - pilot の parity、fault=0、resume、resource state、artifact non-overwrite を確認する。
   - healthy な場合だけ coordinator が full campaign を継続する。失敗時は kill 条件と再現 artifact を記録し、P1 を rollback control として維持する。

## 検証条件

- 新規 tests が対象 module の import、config bounds、default parity、split disjointness、public-only telemetry、CEM determinism/resume を通る。
- `git diff --check` が clean で、既存差分を上書きしない。
- heavy run の結果は研究 artifact にのみ保存し、commit/push/Champion変更/Kaggle提出は行わない。

## 実行結果（2026-08-15）

- parameterization、split、same-observation shadow、CEM、resume/identity の実装と対象テストを完了した。
- rotating block の CEM g03 は META_DEV −4.48ptだった。DEV shadow で `ATTACK→ATTACH` / `EVOLVE→ATTACH` の score-order flip を確認し、guarded hypothesis は DEV −0.13ptまで回復したが TRAIN/FINAL は負だった。
- META_TRAIN 全12参照を使う robust CEM を6世代完走した。g01 は DEV の独立3 seedで正（+7.38/+8.22/+0.68pt）となり、3 seed pooled でも TRAIN/DEV/FINAL がそれぞれ +1.82/+5.56/+3.13ptだった。ただし seed A/B の TRAIN/FINALには負の揺れが残るため、research-only P2候補に留めた。g03 は全splitで棄却した。
- P2-fixed deck screen は `1123` の1枚置換4候補を各192局で評価した。`1123→1086` は一方のseedで +7.29ptだったが、独立seedで −12.50ptへ反転し、残り3候補も負だった。追加384局、policy phase、deck変更、Champion変更、提出は行わない。
- g01 は P1 と同じ `cg` runtime closure を含む deterministic `submission.tar.gz` と hash-bound manifest を research-only package として封印し、通常 interpreter の archive extraction/self-play smoke（1局、DONE/DONE、fault 0）を通過した。isolated `python -I` smoke は環境依存で未実施、公式提出検証・外部送信は行わない。

## 残リスク

robust g01 は3 seed pooledで全splitが正だが、個別seedではTRAIN/FINALが反転するため、より広いseedでの安定性とroot deckを超えるデッキ置換は未確認である。したがって本計画の実行結論は「g01を凍結した研究P2候補として保持し、P1/現root deckを運用基準のまま維持する」とする。

## 追加実行（P2→P3 loop、2026-08-15）

- g01/P2をcontrol兼初期中心にした Campaign 2 を3世代実行した。META_TRAIN screen 上位は大きな正差を示したが、P2未使用meta確認とDEVで再現しなかったため、P3へ昇格しなかった。
- CEM runnerを親control、初期config、独立再評価elite選抜に対応させた。Campaign 3では各世代のscreen上位6を独立96局で再評価し、その値で分布を更新した。screen +16.56ptが再評価 −1.54ptへ反転する例を捕捉できた。
- Campaign 3最終centerはMETA_DEV +7.60ptだったが、未使用meta seed D +4.17pt（seat gap 10.42%）と seed E −1.04ptへ反転した。P3、384/768拡大、deck phase、Champion変更、提出は行わない。
- g02-c07の未使用meta seed B +3.125ptも seed C −6.25ptへ反転した。single-seed positiveを採用しない再現性 gate は機能している。

## 現在の loop 状態

`P1 → P2(g01, research parent) → Campaign 2/3 → holdout` までを自律実行した。P2は次の探索親として保存するが、BestKnown/Champion/提出可能基準は `P1 + root deck` のまま。次回は独立再評価を標準にしたCEMをP2近傍で継続するか、探索面を変更する。deck探索はpolicy候補が未使用meta gateを通過するまで開始しない。

## Campaign 4/5 の更新（2026-08-15）

- Campaign 4 は `--reeval-for-update --reeval-repeats 2 --all-train-refs` で2世代実行した。generation-1 c18/c15は複数再評価では正差だったが、未使用meta seed H/I/Jでそれぞれ seat gapまたは負差により `NOT_PROMOTABLE` となった。
- `scripts/run_cg_p1_cem_v1.py` に repeatごとの固有 block/seed、resume identity、`--include-dev-refs` を追加した。focused tests 22件、docs validator、py_compile、diff-check はPASS。
- Campaign 5 は META_TRAIN＋META_DEV（18参照）で更新し、META_FINALを検証へ残した。2世代の screen 3,600局、再評価4,032局は fault=0だったが、generation-1再評価は全候補負差、center META_FINALは seat collapseだった。
- generation-0上位 c20 を、既存 splitとCampaign 4 holdoutに非重複の fresh holdout v2 で確認したが `39W vs 41W`（−2.083pt、seat gap 6.25%）となった。P1/root deck、BestKnown/Champion/提出物は不変。

したがって現在の再開条件は、同候補のblind retryではなく risk-aware CEM objective または新しい fresh-meta protocolを先に固定し、その証跡を得てから次の policy loopへ進むこととする。

## Campaign 6〜8 の追試と停止条件更新

- Campaign 6 は `--risk-aware-update` で2反復の最悪ブロックをelite更新へ使った。gen1 centerは META_FINAL −9.50pt、risk-aware上位 c01のv3診断は +1.04ptだが control seat gap 8.33%で停止した。
- 各blockの候補seat gap≤5%を硬く要求するCampaign 7は valid elite不足（1<6）でfail-closed停止した。これは小標本での完全除外が探索を止める実測である。
- seat gapの5%超過分をobjective penaltyへ変更したCampaign 8は gen1 META_DEV −2.55pt。gen0 c02のv3診断も −10.42ptで、v3未使用metaの安定優位は得られなかった。
- `configs/meta_specialist/cg_unused_meta_holdout_v3.json` はsplit/v1/v2と非重複の24 refsとして固定したが、c01/c02の診断後に選抜へ再利用していない。現BestKnown/Champion/production/submissionはP1＋root deckのまま。

次に再開する場合は、既評価c01/c02のblind retryをせず、別のpolicy surfaceまたは新しい未使用meta sourceを事前固定し、複数seed・両seat・fault=0・candidate/control seat gap gateを含むprotocolを先に作る。deck mutationはそのgate通過後だけ許可する。

### Campaign 8 audit correction / Campaign 9

Campaign 8後の監査で、safe seat gap時にpenaltyが負になる境界バグを検出した。安全域はpenalty=0、超過分のみ減点する修正をTDDで入れ、focused suite 26件をPASSした。修正版Campaign 9は META_DEV +1.83pt（24W vs 22W）だったが control seat gap 12.50%で不採用。未使用meta gateを通った候補はなく、P1＋root deckを維持する。

## Campaign 9 c11 residual panel と次の探索 surface

- c11 (`cg-p1-cem-g01-c11-76b754ba9dcb`) を、既存 split/holdout/internal-sourceを除く残存 public 3 opponentで、P2 robust g01 control と確認した。
- seed R は candidate/control `6W vs 6W / 96`、delta 0.00pt、candidate/control seat gap 0.00%/0.00%。seed S は `10W-1D vs 2W / 96`、delta +8.8542ptだが candidate seat gap 7.2917%。両方 `DONE=192/192`、fault=0、`NOT_PROMOTABLE`。
- Sの正差が特定 opponentへ偏り、tomatomatoでは同点だったため、CEM集計へ `opponent_seat_rates` を追加し、独立再評価の相手×seat gap 5%超過分を lower-tail objectiveへ減点する実装をTDDで追加した。focused CEM core/runner 21件、py_compile、docs validator、diff-checkを実行する。
- fresh public holdoutが無い間は同panelのblind retryをせず、P1＋root deck、BestKnown/Champion、submissionを変更しない。次 campaignはこの risk surface を使う research-only探索として、未使用 holdout確保後に再開する。

## Campaign 10 と control binding 修正

- P2 robust g01 configを中心に opponent×seat lower-tail CEMを2世代実行した。screen各1,200局、再評価各1,344局、gen1 META_DEVは全て DONE/fault0だった。
- gen1 centerは META_DEV `23W vs 14W / 96`（+9.7729pt）だが candidate seat gap 10.4167%で gate外。これは P3/BestKnown更新の根拠ではなく、fresh holdoutがないため residual panelにも追加投入しない。
- 監査で、shared controlを最初のeliteだけに生成する既存設計により、非先頭eliteのrepeat deltaへ空control objective `-1.0` が混入することを再現した。`_bind_repeat_control` を追加し、shared control aggregateを各eliteへ結合してからdeltaを計算するようにした。
- 新規テストを含む focused CEM 31件、py_compile、docs validator、diff-checkをPASS。Campaign 10旧resultsは不変、以降のheavy runは修正済みrunnerのみを使う。
