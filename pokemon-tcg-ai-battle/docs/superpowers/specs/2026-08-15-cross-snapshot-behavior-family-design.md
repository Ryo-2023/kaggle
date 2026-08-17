# Cross-snapshot behavior-family meta source 設計

## 目的

単一base snapshotに複数のpriority変換を適用した既存factorial epochは、policy差を作れる一方、source-family相関を十分に下げられなかった。複数の許可済みhistorical snapshotから各1 variantだけを生成し、source commitとbase candidateを分離したfresh poolを作ることで、CEMの独立評価が特定snapshotへ過適合していないかを測定する。

このlaneは研究専用であり、native/public meta、Champion、production、submissionを変更しない。P1 `cg-lethal-target-v1`とroot deckをpolicy parent／controlに固定する。

## 採用方式

1. 入力specは4件以上の `{base_root, family, variant, label}` を受ける。
2. 各baseは既存の`_read_base_source`で読み、static findings、deck canonical hash、source policy identityを検証する。
3. `family=alakazam|comfey`に応じて、既存のexact behavior transformまたはfactorial transformを1回だけ適用する。
4. 同一base candidateから複数variantを許さず、source commitは最低3種類を要求する。これで同じsnapshotのvariant増加をsource diversityと誤認しない。
5. policy SHAがcurrent pool、artifact scan、同一batch内で重複した場合はfail-closedする。
6. 生成された各candidateの`SOURCE.md`とevidenceには、base candidate、source commit、base policy、derived policy、deck、recipe、familyを固定する。
7. 最初の2件をMETA_TRAIN、3件目をMETA_DEV、残りをMETA_FINALへhash-boundにsplitする。CEMはTRAINだけを読み、DEV/FINALは選抜後にのみ読む。

## エラーと安全境界

- base rootが通常ファイル、deck不整合、static finding、transform不適合の場合は出力rootを完成扱いにしない。
- 4件未満、source commit 3種類未満、同一baseの重複、未知family／variantは拒否する。
- すべてのauthority flagはfalse、`research_only=true`、`usage_boundary=local_eval_only`とする。
- CABT smokeでfault、illegal、timeoutがあればCEMを起動しない。
- CEMでは独立re-evaluationのworst delta、opponent×seat gap≤5%、fault0を同時に要求する。単一source familyの正差は昇格根拠にしない。

## 成功条件

実装成功はsource poolの安全な生成とfresh splitの検証まで。性能成功は、P1 control比で独立複数blockがpositive、source familyごとのseat gap≤5%、fresh DEV/FINALでもpositiveであること。未達ならP1を保持し、source-generation recipeの診断結果として記録する。
