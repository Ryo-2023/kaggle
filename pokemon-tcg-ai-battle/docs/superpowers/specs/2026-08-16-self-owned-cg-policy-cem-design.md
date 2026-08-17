# self-owned CG deck＋policy CEM 設計（2026-08-16）

## 目的

公式カードデータだけから生成した `parent_deck=null` の self-owned deck と、P1 `cg-lethal-target-v1` の公開状態 policy overlay を同一candidateへ束ね、実CABTの勝率を目的に探索できる研究専用経路を追加する。既存 `run_cg_p1_cem_v1.py` のroot-deck固定契約は変更せず、BestKnown、Champion、production、submissionへ自動反映しない。

## 制約と受入条件

- candidate packageの `self_owned_cg_package_manifest.json` を検証できる。
- `parent_deck=null`、`public_parent_read=false`、training／promotion／submission／longrun authority falseを維持する。
- policy overlayはP1の15パラメータ面だけを使い、合法手・選択数・fallback・CABT evaluatorを変更しない。
- candidateとcontrolは同一のself-owned deck、opponent、seat、seed、repetition strataで比較する。
- `META_TRAIN`のみを探索に使い、`META_DEV`／`META_FINAL`は未接触のまま保持できる。
- fault 0、positive delta、seat gap 5%以下、opponent×seat gap 5%以下を独立再評価で確認できない候補は更新しない。
- 既存P1 CEMのartifact、split、pool、root deckを上書きしない。

## 構成

### self-owned parameterized package materializer

P1 base sourceを検証し、`P1ParameterConfig`からoverlayをrenderする。deck artifact packageの `deck.csv` をcandidateへコピーし、P1の `ROOT_DECK` fallback constantを同じ60枚へ束ねる。`cg/` runtimeはP1 packageから正規ファイルだけをコピーする。最後にself-owned package manifestをcandidate policy/deck/runtime hashへ再束縛する。

### 専用CEM runner

既存CEM coreの `sample_population`、`aggregate_candidate_rows`、`update_distribution`、`build_paired_games`を再利用する。candidate packageは上記materializerで生成し、controlは同じself-owned deckへ束ねたP1 policy packageとする。CEM runnerはscreen、独立再評価、risk-aware positive gate、generation checkpointを保存するが、`cg_bestknown_loop_v1.py`やproduction packageを呼び出さない。

### split binding

meta source poolのhash-bound splitを読み、候補deck SHAを別のcandidate bindingとしてmanifestへ記録する。splitのopponent source identityとcandidate deck identityを混同しない。DEV／FINALはrunnerのsource listに含めず、validation stageで初めて読む。

## 失敗時の扱い

- package／manifest／deck／policy hash不一致はCABT前にfail-closed。
- static compileまたはdeck fallback smokeが失敗したcandidateは評価せず、generation artifactへ理由を残す。
- CABT fault、seat collapse、opponent×seat collapse、独立負差はcenter保持として記録する。
- source smokeでDEV／FINALを使用した場合は、未使用holdoutとして主張しない。

## 検証

1. materializerのmanifest再束縛、deck constant、policy config、no-clobberを失敗テスト先行で確認する。
2. 専用runnerのdry-runでcandidate/controlのpair strataと未使用split境界を確認する。
3. 新self-owned source epochをTRAIN-only bounded smokeで確認する。
4. independent re-evaluation、未使用DEV、未使用FINALを順に実行し、全gateを満たす場合だけBestKnown loopへの接続候補として記録する。

この設計の性能結果が出るまで、現行P1＋root deckのBestKnown、Champion、production、submissionは不変とする。
