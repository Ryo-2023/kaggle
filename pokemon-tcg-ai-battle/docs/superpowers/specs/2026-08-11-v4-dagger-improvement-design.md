# V4 DAgger 実戦改善設計

**作成日:** 2026-08-11

## 目的

Wave6 V4 は V2 より強いが、validation の模倣精度を下げ続けても実戦勝率が同じ割合で上がらない。次の研究 arm は、現在の V4 checkpoint が実戦中に訪れる actor-visible state を収集し、その state を同じ teacher policy で再ラベルして teacher-forcing の分布ずれを減らす。

成功条件は、既存 Wave6 を比較対象として、2 seed の短期 screen で次を同時に満たすことである。

- 24 局 screen が fault 0。
- validation の complete-action top-1 が Wave6 より悪化しない。
- END / EVOLVE / ATTACK の macro 指標のうち少なくとも2種類が改善し、STOP / PLAYを大きく悪化させない。
- 両seatで崩壊せず、fixed-six の4相手以上で非悪化。
- その後の 96 局確認で Wave6 の pooled score 46.4%を上回る。

## 非目標

- DAgger データを正式な teacher-quality READY authority として扱わない。
- current-R2 や既存 V4 checkpoint の topology を変更しない。
- private hand、prize、deck order、serial locator などを記録・特徴量化しない。
- Wave6 の既存 artifact を上書きしない。
- 短期 screen を理由に Kaggle 提出や Champion 変更を自動実行しない。

## アーキテクチャ

### 1. Runtime capture

既存 `run_one_actor_game_v1` の V4 actor-pool 経路を利用する。対象は fixed-six opponent、両seat、指定 seed で、checkpoint file SHA と tensor-state SHA を必須にする。返却された `ActorTrajectoryTransitionV1` の `model_input` と `prefix_steps` だけを収集し、ゲーム結果は provenance と診断に使う。faulted game は学習データへ入れない。

### 2. Teacher relabel

記録済みの `model_input` と各 `step_input` を Rule/teacher policy factoryへ渡し、legal semantic classes と STOP に対する安定化済み soft distribution を作る。teacher policy は各 decision の最初から作り直し、runtimeで使われた prefix の順序をそのまま維持する。teacher の private情報や別の再構成stateは参照しない。

### 3. V4 sequence conversion

各 game を一つの episode/component とし、SHA-256 で partition を決める。train/validation は game component 単位で完全分離する。各 prefix row は既存 `representation_v4_from_step_input_v1` で投影し、`reach_mass`、`target_masses`、`episode_start` を保存する。DAgger sequence は `research_only=True` とし、quality weight は明示的に1.0に固定する。これは性能実験用の重みであり、teacher-quality authorityではない。

### 4. Mixed training

base sealed V4 sequenceを変更せず、DAgger sequenceを新しいresearch-only overlayとして追加する。初回 screen は base 80% / DAgger 20% を目安にするが、実際の行数は episode/component 単位で決め、同一 episode の行を分割しない。既存 trainer の reach-weighted semantic+STOP loss、GRU carry、burn-in、TBPTT、deterministic shuffleを再利用する。

### 5. Evaluation gate

各 seed で initial、best、last の checkpoint を保持し、validation metrics と fixed-six CABT metricsを別artifactにする。NLLだけでbestを決めず、短期 gate は action-type metrics と実戦結果を併記する。DAgger armが不採用でも、base Wave6 artifactと収集データは保持する。

## 失敗時の扱い

- checkpoint SHA、source closure、opponent identity、deck SHA、seed、seat が一致しない場合は収集・学習を中止する。
- runtime fault、non-DONE、private field、空のteacher domain、non-finite logitsはそのrecordを捨て、原因と件数をartifactへ保存する。全体が設定したfault上限を超えたら arm を無効にする。
- train/validation component overlap、record duplicate、teacher targetとlegal domainの不一致は fail-closed にする。
- DAgger armが Wave6 を上回らない場合、同じBCのepoch延長へ自動移行しない。次は matchup/action-type別追加収集または別teacher/state特徴の診断へ戻す。

## 検証

- teacher logitsのsoftmax、STOP整列、duplicate除外、private field拒否、component splitを単体テストする。
- synthetic fixtureでruntime transitionからV4 sequenceを作り、既存 trainerの一epochが完走することを確認する。
- existing V4 model/representation/dataset/runtime testを回帰実行する。
- 実CABTは短期screenの明示コマンドでのみ実行し、既存評価artifactを上書きしない。
