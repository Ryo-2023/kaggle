# P2→P3 BestKnown更新ループ継続 — 2026-08-15

## 結論

P1＋root deckを運用BestKnown／Champion／productionとして維持したまま、P2の独立seed再確認、P2 context CEM、P1 parameter CEMの高精度再評価を続けた。全heavy blockは `DONE`・fault 0 で完走したが、fresh/unused metaはローカルに存在せず、安定したpolicy更新候補は得られなかった。Champion、submission package、deckは変更していない。

## 公開telemetry v2

固定P1 `cg-lethal-target-v1`で base seed `49300000` の96局を追加収集した。decision 3,837行、deck registration 96行、projection fault 0、全96ファイルが `DONE` である。v1と結合した7,914 decision行を strict public-hypothesis analyzerへ通した。

`min_support=2` では候補が1件だけ出たが、state supportは候補ATTACH 2件／参照PLAY 4件で、ATTACHは2/2敗、PLAYも1/4勝だった。候補側・参照側の独立mixed-sign supportを満たさず、screenへ昇格しなかった。`min_support>=4` では bounded hypothesis 0件、`ready_for_candidate_screen=false` と扱う。artifact summary SHAは `1cd6c53757d349bdfe7a5fa274654c2b1180c2511300a50406ad8aaf1619e0b1`。

## P2 context Campaign 3

P2 c06 config（`-6114,-8020,-12769,-15294`）を親に、population 12、2世代、screen repetition 2、各世代2独立block、各block上位2候補、workers 12で実行した。各世代screenは624局、独立blockは候補あたり144局、全て fault 0 だった。

- generation 0: screen最大 `+7.0286pt` だが seat-unsafe。robust対象なし。
- generation 1: screen上位 c00 `+7.1483pt`、c03 `+6.4977pt`。独立blockは c00 が `+5.3418pt / -9.0150pt`、c03 が `-5.7892pt / -12.8140pt` へ反転。
- 2世代とも `CENTER_HELD_NOT_ENOUGH_ROBUST_POSITIVE_ELITES`。final centerは初期c06から不変。

summary SHAは `bd1a6718b9592ca0be77c172189a4473a608911637eb0265501f13f04e1a8278`、manifest-complete SHAは `369c9ad69b866f91ed48bed16fc0bf653ff281a141e74202db7a530c7da08b2a`。fresh meta provenanceは `BLOCKED_NO_LOCAL_UNUSED_META`。

## P2 fresh-seed再確認

P2 `cg-p1-cem-incumbent-g01-c83df4408b24` とP1 controlを base seed `49366000` で固定比較した。candidate/control各stageは全て fault 0 である。

| stage | candidate | control | 差 |
|---|---:|---:|---:|
| META_TRAIN 384 | 72W | 59W | +3.4426pt |
| META_DEV 96 | 19W | 23W | −5.1529pt |
| META_FINAL 96 | 7W | 8W | −1.0662pt |

DEV/FINALで正差が再現せず、P2のresearch-parent昇格条件は未達。manifest SHAは `131082a60c9dbbaf7e14163e9b10fbbdb8038ef55c0917ab78021bceba7376ae`。

## P1 parameter Campaign 12 と c05確認

P2 c83を初期centerに、P1 parameter CEMを `META_TRAIN_ALL`・population 24・1世代でscreenした。screen 1,200局、上位6候補の独立再評価は3 block、合計2,016局、全て fault 0。risk-aware lower-tailで6枠のpositive gateを満たさず、centerは保持された。

唯一、独立3 blockすべて正だった c05（screen `+9.208pt`、独立 `+4.538/+5.061/+0.781pt`）を固定候補として別seed validationへ送った。しかし fresh base seed `49426000` では次の通り全stage負差となった。

| stage | candidate | P1 control | 差 |
|---|---:|---:|---:|
| META_TRAIN 384 | 59W | 62W | −0.614pt |
| META_DEV 96 | 22W | 24W | −1.632pt |
| META_FINAL 96 | 5W | 10W | −4.944pt |

Campaign manifest SHAは `ca8f584807e13d9ca209962c369105ac9cd89bc5302958cf28fc28a378b2e6a4`、generation results SHAは `5652b916f703d79ecdd78d0866d512a4474cc19d65da7f18659d54c406381f69`、c05 validation manifest SHAは `29e3f161f7ae441bf6c931646b74d82b499ca12f17184f9c285b16b86f5bb409`。

## 判定と次条件

- P1 `cg-lethal-target-v1`＋root deckをBestKnown／Champion／productionに保持する。
- P2 c83、P2 context c06、c05はいずれもfresh DEV/FINAL優位を示さず、P3・BestKnown更新へ進めない。
- fresh・unused・smoke-ready public metaは0件。未使用metaが戻るまで、既評価候補のblind retryとdeck mutationは行わない。
- 次の更新は、新しいmeta sourceまたは未評価policy surfaceを固定し、screen→独立複数block→fresh DEV/FINALの順で実施する。single-seed positiveは昇格根拠にしない。

## 検証・権限

今回のheavy runは全て workers=12、fault 0、research-onlyで、promotion/training/longrun/submission authorityは false。commit、push、Champion変更、Kaggle submissionは未実施。既存focused suite、py_compile、docs validator、git diff --checkは別途再実行して最終handoffへ記録する。
