# Signed residual normalization preflight — 2026-08-12

## 判定

ChatGPT Proレビューのprefix/episode過重化指摘に対し、実学習へ接続しない研究専用のnormalization契約を追加した。これは現在のsigned trainerを置き換えず、次のFrozen Residual v2で事前に選ぶべき2方式を合成fixtureで定義するためのものだ。

## 契約

`src/mage_ptcg/meta_specialist/signed_residual_normalization_v1.py` の入力は、sealed target join済みの`SignedPrefixWeightV1(episode_id, record_id, prefix_index, signed_weight)`だけである。recordのprefix indexは0から連続し、recordが複数episodeへ跨ぐ入力、非有限値、`[-1,1]`外のweight、未知modeはfail-closedする。

- `record_normalized`: physical recordのprefix targetを平均して1つのrecord weightにし、prefix数で割って配分する。したがってcomplete-action log probabilityをprefix合計で計算する場合、同じrecordの総abs contributionはprefix数に依存しない。
- `episode_normalized`: record weightをepisode内の総abs record weightで正規化し、各episodeの総abs contributionを1（全recordがzeroでない場合）にする。その後record内prefixへ均等配分する。

このmoduleはlogits計算、optimizer、V4 runtime、CABT、checkpoint、promotion authorityを持たない。出力は常に`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`、`performance_evidence=false`である。

## 合成検証

focused testは3 passed。

1. 1 prefixと4 prefixの同じrecordを比較し、`record_normalized`のrecord総abs massが一致すること。
2. prefix長が異なる2 episodeで、`episode_normalized`のepisode総abs massがそれぞれ1になること。
3. 非連続prefix indexと未知modeを拒否すること。

これはgradient invarianceの必要条件を閉じるpreflightであり、実data学習がprefix過重化を解消したことを示すものではない。次のtrainer実装では、各physical recordの全prefix logitsを一つのcomplete-action log probabilityへ集約し、このnormalization結果を一度だけ適用する必要がある。現行signed trainerの結果と混ぜず、`record_normalized`と`episode_normalized`を固定2 armとして比較する。

## 未実施

- 実Wave6 materializationへの接続
- sidecar optimizerへの接続
- coarse bucket gateとの結合
- CABT評価、96局block、shadow-C、longrun、Champion変更、Kaggle提出

上記を行う前に、同一seed binding・同一update budget・同一target source SHA・coarse coverage telemetryをmanifestへ固定する。
