# Festival behavior-family meta source / fresh CEM（2026-08-15）

## 結論

既使用のFestival snapshotをそのまま再利用せず、visible-state priority tableだけを4種類へ固定変換する別deck behavior-family sourceを生成した。freshness／canonical deck／static securityをsealし、P1 control固定のrisk-aware CEM、fresh DEV、未使用META_FINALまで接続できた。

全CABTは`DONE`・fault0だったが、BestKnown更新は成立しなかった。CEMの独立再評価でscreen陽性候補がP1 controlを下回り、fresh DEVのcenter見かけ差もseat gate外だった。未使用FINALのgen1 candidate-05は`+6.25pt`だったもののcandidate seat gap`12.50%`で`NOT_PROMOTABLE`。P1＋root deck、BestKnown、Champion、production、submissionは不変である。

このepochはinternalの相関proxyであり、public/native性能やnative上位72%到達の証拠ではない。

## source generation

`src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py`へFestival用のexact transformerを追加し、CLIの`--family festival`から呼び出せるようにした。baseは許可済み`internal_nihei-festival-lead_7e1398e6ad54`（branch `agents/nihei-festival-lead`、commit `7e1398e6ad5439ffa9efb9713244769fc79f8e13`、source policy SHA `78b27625cf7e3a3378d95c4a58761fae063bffe266a7191cf878e377148129e6`）である。

固定変換は次の4つで、各候補は同一canonical deck SHA `62ac60931cb5a15918003d6519bad43a7ae74c1dbe23bd0bacb6029c675ed0b4`、`visible_state_only`、`local_eval_only`を保持する。

- `ALAKAZAM_FIRST`: Alakazam／Dunsparceのpokemon priorityを反転
- `DUNSPARCE_FIRST`: Dunsparce系とDudunsparceを優先するpriorityへ変更
- `SHAYMIN_SETUP_FIRST`: setup activeでShayminを先行
- `POFFIN_DUNSPARCE_FIRST`: Poffin searchでDunsparce系を先行

生成rootは `runs/cg-festival-behavior-family-meta-20260815-h/`。policyは4件とも新規SHAで、pool SHAは`6f29a032fcb79ce904992efd264c462c8b464500a539c3a10da6def24ca4e4df`、fresh meta SHAは`22244c4529380a5b73ada3441cf75569ab3fda2c24df35a626a3e15daf3b41af`、split SHAは`fc343031962e282210614c028797b28f6486f14bddba4de50ddec6ec5396f97c`である。splitは`META_TRAIN=ALAKAZAM_FIRST/DUNSPARCE_FIRST`、`META_DEV=SHAYMIN_SETUP_FIRST`、`META_FINAL=POFFIN_DUNSPARCE_FIRST`とした。

## smokeとCEM

train 2 variantを両seat・各2反復（8局）smokeし、8/8 `DONE`・fault0、P1は6勝2敗だった。DEV/FINALはsmokeから除外した。

P1 `cg-lethal-target-v1`＋root deckをcontrol/parentに固定し、population 8、elite 2、2世代、initial scale 5%、独立再評価2回、positive-delta gate、risk-aware updateでCEMを実行した。screen144局、独立再評価96局、fresh DEV32局、合計272局を全て`DONE`・fault0で完了した。

- gen0 candidate-00／candidate-03はscreen各`+25.00pt`だったが、独立再評価でcontrolを下回り、robust positiveなし。
- gen1 candidate-00／candidate-05もscreen各`+25.00pt`だったが、独立再評価でcontrolを下回り、centerはP1のまま。
- fresh `META_DEV`のcenterはcandidate `13W-0D-3L` 対 control `9W-0D-7L`（見かけの`+25.00pt`）だったが、candidate seat gap`12.50%`でgate外。center parameter packageの確認値であり、BestKnown更新には使わない。

CEM manifest SHAは`21085ba442cdadf2fb908044b5525f04baeac39e1a4e16762efd263080ca4fe1`、generation results SHAは`ce85b8712b1cdad7c0222683578e736ac160294d5b285e1dba777eef25733c74` / `5d4189da5de87397953458761066191ea9eaf7e96b0c15da0ae569f6f9a39f7c`である。

## fresh FINAL

gen1 screen上位 candidate-05 `cg-p1-cem-g01-c05-5943d78e4e24`を未使用`META_FINAL`へ8 games/opponent/seat（各arm16局）確認した。candidate `9W-0D-7L`（56.25%）、control `8W-0D-8L`（50.00%）、差`+6.25pt`、candidate seat gap`12.50%`、fault0、判定`NOT_PROMOTABLE`。candidate policy SHAは`9d44c3aa664e7bd021ffed510d3c1ce2272dc9fc38cc6d3017fbf17cc20d16ad`、summary SHAは`e9ebfc3f7d2918797a984f27c33ac943ec0f3bf1788d2c821e39d7c0bf684d0e`である。

## 判定と次の扱い

Festival epochは、既使用snapshotから別deck behavior-familyを安全に生成し、fresh split→CEM→fresh FINALへ接続する方法を追加で検証した。しかし独立seedとseat gateを満たす候補はなく、CEM center、P1、BestKnown、deck、Champion、production、submissionは不変である。Festival proxyのblind retry、P2/P3、deck mutation、`cg_bestknown_loop_v1.py`のpolicy→deck→policy、training、longrun、commit、push、Kaggle submitは行わない。

次は同じFestival proxyを再利用せず、別deck／別sourceのbehavior-familyまたは新しい許可済みsourceを固定する。screen後の独立複数blockでpositive・seat-safe・fault0を満たした場合のみ、fresh DEV/FINALを経てBestKnown loopへ渡す。
