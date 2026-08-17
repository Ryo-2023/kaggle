# Derived Teacher Actor-Visible Value + AWR Sidecar v1

## 結論

旧catalogに登録されていた6件の derivation-qualified teacher snapshot をすべて再検証し、37,615 decision に対する `record_id -> V(s), A=G-V(s), AWR weight, filtered-BC eligibility` sidecarを計算した。しかし生成直後の上位監査で、旧collectorのlegacy omissionを完全復元できず、旧snapshotを正式な学習入力にできないことが確定した。

**現artifactのstatusは `LEGACY_SOURCE_NO_GO / AUDIT_ONLY` である。learnerへ接続してはならない。** fresh collector v2で全6件を再収集・再封印し、新catalog file/semantic SHAへ切り替えた後、別run rootへ再生成する。以下の数値とSHAは旧sourceに対してvalue/AWR計算経路が動いたことを追跡するため残すもので、正式training dataの性能証拠ではない。

本成果の有効部分はコード、TDD、feature/fold/weight/schema contractである。学習開始・性能改善・promotion・submissionは一切意味しない。

価値特徴は **strict public-only ではない**。対戦相手の非公開情報は含まないが、自分の手札など own-private state を含み得るため、本artifactでは一貫して `actor-visible` と呼ぶ。

## 一次 artifact

| artifact | path | file SHA-256 | semantic/self SHA-256 |
|---|---|---|---|
| manifest | `runs/final-sprint-autonomous/derived-teacher-actor-visible-awr-v1/manifest.json` | `a991220e2cd2edc4ad8dfee204dbec460917a7216d54c087dac2a9149aaa9dda` | `8711b39a6a974bedc72365cd065c085e6b023129eaf8d9e2b023c656ac5983cb` |
| weight sidecar | `runs/final-sprint-autonomous/derived-teacher-actor-visible-awr-v1/weights.jsonl` | `1e2e853922f1a74c425d55d032c3a3cb82ff4e3a2125a21f92edf17bd5123ab8` | 該当なし |
| source catalog | `runs/final-sprint-autonomous/derived-teacher-catalog-v1/catalog.json` | `d5216ebc83c8bbcdc3129f647201868a45a96f0ccbcd946c37a200a8a074d263` | `cbd485635efee7b24344d8210cec132b3101ab91cbbeae84d9578f31477396f0` |
| permission decision | `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` | `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` | 該当なし |

## 入力 source binding

| teacher | archetype | rows | policy SHA-256 | deck SHA-256 |
|---|---|---:|---|---|
| `tomatomato_archaludon` | `archaludon` | 5,146 | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| `lucifer19_battlecore` | `archaludon` | 5,102 | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` |
| `plamen06_steel` | `archaludon` | 5,420 | `8a40be6825612ea927f5a6fd3396bb18e78a198d554e44cbb572acbab1b74ac3` | `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` |
| `ozawa_grimmsnarl_v2` | `grimmsnarl_froslass_munkidori` | 7,808 | `48621429950e717e8dbd2928fd58876ee73b6cd4eb397dc8f629899a41ce2014` | `92b92bac9f9163ecff933b3dc39294d2cc154c8684f3c8497877661419ebc59d` |
| `ozawa_rocket_v2` | `rocket_mewtwo_spidops` | 6,048 | `a3b9cc59b82ebb34afafed2fd52053f1769f85d7d55b9452fe21bcf0e791c83b` | `0c4a1f66c862ca1d2391b780c5622cbdf76a7845f89259d47290c05021384fbb` |
| `nihei_alakazam` | `alakazam` | 8,091 | `a502b37132b555fdd329a40337c2cc8a0b27098ed278b249b7c2222fd2df711` | `167d43335013f7b68441356d750dab335088171c1ab929e083deb85a2c79e5b1` |

完全な dataset manifest / snapshot index / corpus snapshot / shard SHA は一次manifestの `sources` に保存した。要点は次の通り。

| teacher | dataset manifest SHA | snapshot index SHA | corpus snapshot SHA |
|---|---|---|---|
| tomato | `b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff` | `b5cc75c82ee321cb7841b99f80d49fd6759e56d060af435200239a45b36bc72f` | `38a361ec571e2d8ba9546db333fd48f33ffb72d7d8526ba304f4be80235c559a` |
| Lucifer | `d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84` | `ea5275370d17bcc520d31aec3302ea0be054520eb92811cd5af2cdac54005ba4` | `ed0b61159e1f89f4aab4d04d0964632aa9965972a1ac99dc7afdadcf6ea8d309` |
| Plamen | `2084255352caba9fe8c7127010833ca84af1fa3c6efe75a766c36b4aa0a20348` | `10a10c2a95fb66fabe7177303c750cecf2fcb8061bc941d74c50b38db7543bd9` | `bd2141eb236edba4a50a6f0ea5c207643f64fb3a79f9caa650c247dd37a3b77d` |
| Grimmsnarl | `fb6fa69f42ae8877819321f9f5d9d806367cdcab6597341dfa857df6cae1e844` | `b907594b2a0927e511e7add4780e9d76e456f630830444457acc1c8fae6392b1` | `d2a7eebb24b633d61eb2b5ea0fb0b4da148f4aad3e1dd75db3e688e998d7a3b3` |
| Rocket | `271ecd0a41ca114b551c5f2be5572a883484cd68c50abfcd7186423ee1307d58` | `a88989193b817c737b4cf0d0712cc84ee9a55300e5085883b4fb47026f2a7f1a` | `d9d16e8ad022d6bd5a046eafae6c42399232c710ef0e7c880b470a79ea5dee4f` |
| Alakazam | `f5a0fafc0ff1cf389d51e35728f20a5b2f602944eb9e8657d9173cf181b727d2` | `ed47d0c0f3622465d4e43e64759082923e749fce11b66f8766566c369fe70df7` | `1bca01ba2f937de0363912276a8b8df9c251df3e421cd9aab9bd1211bb28b16a` |

旧internal 3件はcatalog ownershipが `team_internal_agent` なのに、snapshot内 `source_artifacts.kind` が `pooled_external_submission_agent` だった。この不一致も旧artifactの監査情報として保存されている。fresh collector v2ではinternal 3件のsource kindを `team_internal_agent` に修正する。現行hardened loaderはpolicy SHAだけでなくsource kindもcatalogとexact一致しなければ拒否するため、この旧catalogからの再生成はfail-closedする。

## Value / fold protocol

- feature schema: `actor-visible-specialist-state-41-scalars-step-structure-56-v1`
- feature dimension: 56
- value model: float64 ridge linear regression
- ridge lambda: 1.0
- target: episode terminal `value_target` (`G`)
- advantage: `A(s,a) = G - V(s)`
- fold unit: episode
- fold count: 5
- fold assignment: `sha256-domain-seed-episode-mod-v1`
- fold assignment SHA: `f6f09f647e6389a5c1ff6213301b4667d7579331052baf25120a989553be7043`
- fit可能split: `train`のみ
- fit禁止split: `development`, `validation`, `test`, `opponent_holdout`, `deck_holdout`
- train episode: 407
- 全episode: 576

各train rowは自分と同じfoldの全episodeを除外したmodelでscoreした。各foldの `fit_score_episode_intersection_count` は0。foldごとのscore episode/record数は以下。

| fold | fit episodes | score episodes | fit records | score records | coefficient SHA |
|---:|---:|---:|---:|---:|---|
| 0 | 324 | 83 | 20,980 | 5,447 | `bad15627bb4b66efb85b28a1aadc0f6596853b7beb8e78885a3ca6d29557664d` |
| 1 | 332 | 75 | 21,504 | 4,923 | `b24d9ec348a8c7638224f089f749cc25a10f4de8766ad766a66411a90b2d4a4a` |
| 2 | 335 | 72 | 21,959 | 4,468 | `ea8cd747617f933fd568b50b2570506024b2bc44b229533bcf9f3d5b5ef7d160` |
| 3 | 320 | 87 | 20,686 | 5,741 | `5a256085f939a199f9d59019a12f06f6e770b0e8e806ba6831954558e53ba392` |
| 4 | 317 | 90 | 20,579 | 5,848 | `a95aa0766bc8d581f36dbbedd13d2367d6137780e79492e1d6fc6cdf4d83560d` |

development/test rowは、outcomeを一切fitへ渡さず、train全体だけでfitしたmodel (`b43b514e22b04b1abbd3d8b98ba9f8bc6a07af9fa80847781550a5ad20b4b68b`) でscoreした。現sourceには `validation` / `opponent_holdout` / `deck_holdout` rowはないが、schemaとテストではこれらをfit禁止として固定している。

## AWR / filtered BC protocol

- beta: 1.0（事前固定）
- raw weight: `exp(A / beta)`
- raw upper clip: 20.0
- exponent lower clip: -50.0（underflow防止）
- normalization: train weightの平均が1になるbounded water-filling
- normalization scale: `0.7133676221010026`
- train normalized mean: `0.9999999999999999`
- final upper bound: 20.0
- effective weight: `snapshot example_quality_weight * normalized AWR weight`
- filtered BC: `A > 0` のstrict-positiveのみ eligible
- behavior probability required/used: `false / false`

importance sampling ratio、soft teacher distribution、teacher KLは使っていない。これは chosen action、terminal return、actor-visible valueだけで構成するreplay-only AWR/filtered BC sidecarである。

## 全体結果

| metric | value |
|---|---:|
| rows | 37,615 |
| train rows | 26,427 |
| heldout rows | 11,188 |
| positive advantage / filtered eligible | 23,857 |
| negative advantage | 13,758 |
| zero advantage | 0 |
| quality mass | 37,375.0000 |
| AWR mass | 37,526.8311 |
| effective mass | 37,302.2932 |
| ESS | 25,351.7260 |
| advantage mean | 0.0137218 |
| advantage min | -2.1783990 |
| advantage max | 2.4376926 |

trainだけではpositive 16,630、negative 9,797、effective mass 26,271.9646、ESS 17,524.3513。developmentはpositive 3,411 / negative 1,972、testはpositive 3,816 / negative 1,989だった。

## Teacher別 effective mass / ESS / 符号分布

| teacher | rows | positive | negative | effective mass | ESS |
|---|---:|---:|---:|---:|---:|
| Lucifer | 5,102 | 3,788 | 1,314 | 5,273.3900 | 3,967.2931 |
| Alakazam | 8,091 | 4,397 | 3,694 | 7,806.6160 | 4,841.7165 |
| Grimmsnarl | 7,808 | 5,080 | 2,728 | 7,398.4165 | 5,803.1928 |
| Rocket | 6,048 | 3,492 | 2,556 | 6,223.8845 | 3,708.9062 |
| Plamen | 5,420 | 3,799 | 1,621 | 5,516.7736 | 3,942.2484 |
| tomato | 5,146 | 3,301 | 1,845 | 5,083.2126 | 3,358.7315 |

## Action type別の主要群

全25 action type、および teacherとの直積で実在する91群について、row数、quality/AWR/effective mass、ESS、positive/zero/negativeのcountとeffective mass、advantage min/max/meanを一次manifestの `diagnostics.action_type` と `diagnostics.teacher_action_type` に全件保存した。effective mass上位は以下。

| action type | rows | positive | negative | effective mass | ESS |
|---|---:|---:|---:|---:|---:|
| `selection_type=0/selection_context=0` | 20,648 | 12,852 | 7,796 | 20,751.7443 | 13,675.0825 |
| `selection_type=1/selection_context=7` | 6,456 | 4,303 | 2,153 | 6,401.1259 | 4,545.7106 |
| `selection_type=1/selection_context=21` | 1,799 | 1,296 | 503 | 1,747.8233 | 1,361.9246 |
| `selection_type=9/selection_context=43` | 1,266 | 829 | 437 | 1,288.9147 | 893.0186 |
| `selection_type=1/selection_context=4` | 1,185 | 548 | 637 | 1,217.8228 | 691.9168 |
| `selection_type=1/selection_context=22` | 1,079 | 744 | 335 | 1,131.2270 | 756.0230 |

最大のteacher/action群は `nihei_alakazam × selection_type=0/selection_context=0` で、4,972 row、effective mass 4,834.2867、ESS 2,893.4805、positive 2,680 / negative 2,292。全91群はmanifestを正とする。

## Feature境界

価値特徴へ入るのは、封印snapshotの `model_input` と最初のcanonical decision stepだけである。

- 41 state scalars
- 最初のstepのeffective domain、STOP可否、prefix長
- actor-visible Pokémon entity数
- actor-visible card bag mask数（own handを含み得る）
- option type presence
- fixed bias

以下はvalue特徴へ入れない。

- `opponent_id`
- `seat`
- `teacher_id`
- policy SHA / deck SHA
- episode ID
- record provenance

これらはsource binding、episode fold、join metadataとしてのみ保持する。

## Authority

manifestは以下をすべて `false` に固定した。

- `training_authority`
- `promotion_authority`
- `submission_authority`
- `longrun_authority`

teacher codeとteacher deckをbundleへコピーしていない。sidecarの生成はderived weight利用許可を記録するだけで、learner実行、Champion変更、package採用、Kaggle submissionを許可しない。

## 実装と検証

新規実装:

- `src/mage_ptcg/meta_specialist/derived_teacher_actor_visible_awr_v1.py`
- `src/mage_ptcg/meta_specialist/derived_teacher_awr_artifact_v1.py`
- `scripts/build_derived_teacher_actor_visible_awr_v1.py`
- `tests/meta_specialist/test_derived_teacher_actor_visible_awr_v1.py`
- `tests/meta_specialist/test_derived_teacher_awr_artifact_v1.py`

TDDで固定した反証条件:

- 同一actor-visible stateのprovenance/teacher/opponent/seat/policy/deck metadataを変更してもfeature不変
- score対象foldのoutcomeを変更しても、そのfoldのbaseline/model SHA不変
- development/opponent_holdout/deck_holdout outcomeを変更しても全fit model SHA不変
- constant target / zero target varianceでもfinite、positive、上限内、train平均1
- sidecar byte tamperをSHAで拒否
- manifest reclassification/authority変更をself SHAまたはclosed authorityで拒否
- snapshot collection kindがcatalog ownership表記と異なってもpolicy SHAをexact identityとして維持し、actual kindをprovenanceとして保存

旧artifactを生成したコマンド（現在のhardened loaderでは旧internal source kind不一致を拒否するため、そのまま再生成はできない）:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_derived_teacher_actor_visible_awr_v1.py
```

## 解釈と残リスク

- **最優先 blocker:** legacy omissionを復元できない旧6 snapshotはtraining input NO-GO。fresh collector v2、新snapshot、新catalog SHA、新run rootが再開条件。
- 本結果はweight計算の成立を示す。policy性能改善はまだ測っていない。
- 線形ridgeは固定56特徴の小さいbaselineであり、value approximation errorは残る。AWR learnerのscreenではnative BestKnownをcontrolに含める必要がある。
- 現snapshotは各teacher 96局の既存収集であり、今後作る上位meta on-policy大量データそのものではない。次のcollectionで同一contractを再利用できる。
- positive比率やteacher別massはteacherの絶対強度ランキングではない。状態分布とvalue residualの結果である。
- heldout rowの `V(s)` はtrain全体fitであり、train rowのようなcross-fitted scoreではない。ただしheldout outcomeはfitへ一切使っていない。
- action type群の小さいものはESSも小さい。少数群を単独で性能判断しない。
- 実model ID/provider/effortはこのsubagent実行環境から取得できず未記録。artifact生成自体は固定入力・固定fold・float64 deterministic pipelineで行った。
