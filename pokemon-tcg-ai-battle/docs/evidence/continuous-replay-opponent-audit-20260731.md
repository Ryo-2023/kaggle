# Continuous Replay opponent監査と補完収集

## 結論

V15の固定Replayは、Rule v0と既存Team Agentを使った初期データとして再利用できます。一方でRule v1対戦と履歴モデル対戦は含まれないため、V15だけでは計画した相手多様性を満たしません。V15を親Replayとして保持し、不足区分だけを追加収集する構成を採用しました。

## V15の実データ

確認対象は`/home/bfe-lab-ono/kaggle/handoff-artifacts/r2d3-sequence-psro-production-v15`です。

| 項目 | 確認値 |
|---|---:|
| 完了局 | 5,000 / 5,000 |
| 席順 | candidate first 2,500、candidate second 2,500 |
| Rule v0が相手の局 | 4,000 |
| Team Agentが相手の局 | 1,000 |
| Team Agentのpolicy hash | 12種類 |
| 相手deck hash | 32種類 |
| environment top deck | 24種類 |
| Rule v1が相手の局 | 0 |
| behavior policyとopponent policyが同一の局 | 0 |
| 固定Replay | 33,810 sequence |
| Replay SHA-256 | `ea07b3a5f4fa56a9312292b7b82c99c8e3561c4ca93091e9ea7274ed0b2a75ff` |

V15の1,000局に使われたTeam Agentは次の12件です。

- `agents/ozawa-crustle-rule`
- `agents/ozawa-crustle-rule+RL`
- `dev/ozawa_crustle_v2`
- `agents/nihei-alakazam`
- `agents/nihei-double_dqn_houdin`
- `agents/ozawa-starmie`
- `dev/ozawa_starmie_v3`
- `agents/nihei-festival-lead`
- `dev/sue124_alakazam`
- `dev/tomatomato_archaludon`
- `agents/ozawa-rocket-rule`
- `agents/ozawa-rocket-rule+RL`

V15の5,000局は、`ppo_submitted_rule` 2,000局、`environment_top_decks` 1,000局、`bc_recurrent` 1,000局、`family_alakazam` 1,000局です。後半のPSRO online collectionは生成されていません。Full Trainingの37,500 update checkpointは完了していますが、controllerは`development_validation`中に停止していました。

## 不足の判定

現行Catalogには49種類の学習用Rule v0 deckがあります。V15でRule v0と組み合わせた25 deckのうち現行Catalogとの重複は24種類で、現行側の25種類はV15にありませんでした。

| 区分 | V15での充足 | 補完方針 |
|---|---|---|
| Rule v0 × 外部deck | 一部充足 | 現行49 deckを均等抽選して1,000局追加 |
| Rule v1 | 不足 | 専用Mixtureで500局追加 |
| Team Agent | 12 policyを収録 | V15をそのまま利用 |
| 履歴モデル対戦 | 不足 | V15学習途中の5世代を相手に5,000局追加 |

履歴モデルは7,500、15,000、22,500、30,000、37,500 updateのcheckpointからRuntime Policyを生成しました。候補側は37,500 updateを固定し、5世代を均等抽選します。

## 実装と検証

- `CabtMatchExecutor`へ`runtime_policy` opponentを追加した。
- Runtime Policyのpolicy hashとdeckがCatalog entryに一致しない場合は対戦前に停止する。
- `build-runtime-catalog`で複数の学習済みRuntime PolicyをCatalogへ固定できる。
- `build-population --policy-kind`でRule v0、Rule v1、履歴モデルを別stratumとして収集できる。
- 6局のend-to-end smokeで3区分を各2局収集し、V15親Replayと結合した33,819 sequenceを保存・再読込した。
- focused testは追加CLI testを含む13件がPASSした。

## 本収集の確定結果

`continuous-replay-bootstrap-v2.service`は正常終了しました。

| 区分 | 局数 | sequence | candidate勝／敗 | fault |
|---|---:|---:|---:|---:|
| Rule v0補完 | 1,000 | 1,754 | 573 / 427 | 0 |
| Rule v1補完 | 500 | 726 | 218 / 282 | 0 |
| 履歴モデル対戦 | 5,000 | 5,794 | 2,376 / 2,624 | 0 |
| 追加合計 | 6,500 | 8,274 | 3,167 / 3,333 | 0 |

Rule v0は49 deckすべてを10〜33局、履歴モデルは5世代を各962〜1,036局抽選しました。追加分とV15親Replayを結合した最終Replayは次のとおりです。

| 項目 | 値 |
|---|---|
| Population epoch | `a757828c1c9873cadfd1174229eafbbe91aed668458d1f4c1f4940c309a3fa2d` |
| Replay dataset version | `ea98677fcc681e06520f844d2f8d1dfdbe8ae98580694304e8c4c58928997098` |
| sequence | 42,084 |
| Replay SHA-256 | `49c83ee623b490b32abb7fca1d1f1c49fd1cc9ec885fe96a7ea2ab7d2b0db213` |
| manifest | `runs/continuous-league-external-v1/bootstrap-v2/collection/replays/ea98677fcc681e06520f844d2f8d1dfdbe8ae98580694304e8c4c58928997098/manifest.json` |

最終Replayはchecksum照合と再読込を通過しました。GRU-256 learnerの1 updateも実行し、loss `4.792010`、gradient norm `0.497483`、TD error mean `0.245161`はすべて有限でした。schema v3 checkpointとRuntime Policyの発行も確認しました。検証用の約1.9 GBコピー、局別中間データ、完了ログ、stale PIDは削除済みです。
