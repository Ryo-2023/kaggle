# Comfey behavior-family meta source / fresh CEM（2026-08-15）

## 結論

Starmie専用だったbehavior-family source generatorを、別deck／別behavior familyであるComfey library-out系へ一般化した。許可済みinternal snapshotから、同一deckを保ったままvisible-state priority tableを4種類へ固定変換し、fresh meta manifest、custom split、P1 control固定のrisk-aware CEM、未使用META_FINAL確認まで完了した。

train smokeと全CEM blockはfault-freeだったが、改善候補は昇格条件を満たさなかった。fresh DEVの一時的な`+12.50pt`はP1 centerをcandidate armとcontrol armで同じpolicyとして再生したRNG差であり、policy gainとは扱わない。gen1 candidate-03の未使用FINALは`0pt`、candidate seat gap`12.50%`で`NOT_PROMOTABLE`。P1＋root deck、BestKnown、Champion、production、submissionは不変である。

このepochも`local_eval_only`の相関proxyであり、public/native性能やnative上位72%到達の証拠ではない。

## source generation

`src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py` と `scripts/generate_starmie_behavior_family_meta_v1.py` の`--family comfey`経路を使用した。baseは許可済み`internal_nihei-MegaLopunny_19fd36050805`（branch `agents/nihei-MegaLopunny`、commit `19fd360508056e4eb7512a16ec91e27e07a4c6cb`、source policy SHA `e5018c9f2945c82ae0aedb62f63cf76f48d6049eaf309489d825df75824e3258`）で、4変換は次の通りである。

- `DECKOUT_AGGRESSIVE`: visible self-deck reserve `4 → 2`
- `DECKOUT_CONSERVATIVE`: visible self-deck reserve `4 → 8`
- `COMFEY_SETUP_FIRST`: Comfey priorityをMawileより先行
- `LITWICK_SETUP_FIRST`: Comfey priorityをLitwickより先行

各候補は同一canonical deck SHA `da3bb5b4851037d9c2ad0c379a25ad097a3e2c5f8778b8f52745f361edb5f432`、`visible_state_only`、`local_eval_only`を保持し、任意rewrite、deck変更、import実行、network、提出bundle生成を許可していない。4件のpolicy identityはすべて新規で、生成rootは `runs/cg-comfey-behavior-family-meta-20260815-g/` である。

pool SHAは`65c134872b3f2cb656ed49f787502d3bab7ae971de8a8443b77da3524d806252`、fresh meta SHAは`7b0f6bf515527a79d46ecca844781f34acb38efecd2bb8810d7857a917242d84`、split SHAは`c5378d2efee9c2220da4cfd00a9c0455736db919eb606715479c7702df8ca1aa`である。splitは`META_TRAIN=DECKOUT_AGGRESSIVE/DECKOUT_CONSERVATIVE`、`META_DEV=COMFEY_SETUP_FIRST`、`META_FINAL=LITWICK_SETUP_FIRST`とした。

## smokeとCEM

train 2 variantだけを両seat・各2反復（8局）でsmokeし、8/8 `DONE`、fault0、P1は2勝6敗だった。DEV/FINALはsmokeから除外した。

P1 `cg-lethal-target-v1`＋root deckをcontrol/parentに固定し、population 8、elite 2、2世代、initial scale 5%、independent re-evaluation 2回、positive-delta gate、risk-aware updateでCEMを実行した。screen 144局、独立再評価96局、fresh DEV32局、合計272局は全て`DONE`・fault0だった。

- gen0は独立再評価でrobust positive candidateがなく、centerを保持した。
- gen1 candidate-03とcandidate-07はscreenで各`+25.00pt`だったが、独立再評価はsafe-positiveを満たさず、centerを保持した。CEMの更新先はP1のままである。
- fresh `META_DEV`ではCEM center（P1 policy）candidate `8W-0D-8L` 対 control `6W-0D-10L`、見かけの差`+12.50pt`だった。しかしcandidate armとcontrol armは同一P1 centerであり、candidate seat ratesは`50.00%/50.00%`、controlは`25.00%/50.00%`だったため、policy改善ではなく同一policyのseed noiseとして扱い、昇格根拠にしなかった。

CEM manifest SHAは`f2e129b8da26818e671042873c40667c754e06ae3f06ec68dbb646a17099bc75`、generation results SHAは`4757073583d532763ce1b3dba8dd99c1f05ed107fda6300f9c9d4f4aaf00cb1b` / `877062ffa0244a70ef6d862c2ed3c59d106d2412791bfd87626443c8893356ba`である。

## fresh FINAL

gen1 candidate-03 `cg-p1-cem-g01-c03-772cde17f57e`を未使用`META_FINAL`へ8 games/opponent/seat（各arm16局）確認した。candidate `9W-0D-7L`（56.25%）、control `9W-0D-7L`（56.25%）、差`0pt`、candidate seat gap`12.50%`、fault0、判定`NOT_PROMOTABLE`。candidate policy SHAは`8949be86a31142297aa92b10dd52988a447598877418e34ea4b16c2570a0658f`、summary SHAは`dcb156b4013c0b351901cf954e0f6e824bd95db949476233d439a22b21c5ba8d3`である。

## 判定と次の扱い

このepochは、異なるdeck／behavior familyで「新しいmeta sourceの生成→freshness seal→CEM→fresh FINAL」を安全に接続できることを示した。一方、CEM center更新もBestKnown更新も成立していない。Comfey proxyのblind retry、P2/P3昇格、deck mutation、`cg_bestknown_loop_v1.py`のpolicy→deck→policy実行、training、longrun、Champion変更、production変更、commit、push、Kaggle submitは行わない。

次は、同一Comfey proxyを再利用せず、permission済みの別source／別behavior generatorを新epochとして固定する。候補がscreen後の独立複数blockでpositive・seat-safe・fault0を満たした場合のみ、fresh DEV/FINALを経てBestKnown loopへ渡す。
