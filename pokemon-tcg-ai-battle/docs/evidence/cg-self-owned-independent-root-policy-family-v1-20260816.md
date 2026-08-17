# self-owned independent root policy family v1（2026-08-16）

## 結論

「新しい meta source を自分で生成し、既存の BestKnown loop へ渡せる形にする」経路は実装・実行できた。ただし、今回の independent policy lineage は P1 を上回らず、BestKnown 更新候補にはならなかった。8 variant の META_TRAIN matched screen はすべて fault 0 だったが、P1 control に対する差分は `-8.3333pt`〜`-41.6667pt`。したがって判定は `SOURCE_GENERATION_PASS / POLICY_LINEAGE_NO_UPDATE / BESTKNOWN_UNCHANGED` とする。

`ono-` はこの source の作者名ではない。policy parent はローカルに封印された public root policy SHA `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef` と明記し、P1 SHA `1c505b2b...` は renderer の親に含めていない。

## 独立 lineage と生成境界

- plan: [`self_owned_cg_independent_policy_family_v1.json`](../../configs/meta_specialist/self_owned_cg_independent_policy_family_v1.json)、SHA-256 `0809a6e335ea1b14433074f06c70ce405fced7f6d05795e31a131a02adda1f89`
- source epoch: `self_owned_independent_root_policy_v1_20260816`
- input: `data/raw/EN_Card_Data.csv`、`self_owned_cg_deck_spec_v5_broad_support.json`、既存 artifact は canonical hash collision 監査のみ
- parent policy: `root_cg_submission_agent_v1.py`、SHA-256 `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`
- renderer: [`cg_independent_policy_renderer_v1.py`](../../src/mage_ptcg/meta_specialist/cg_independent_policy_renderer_v1.py)、SHA-256 `2180555626f3967087f42ce75eceaaf2ccc2fbe407d07202f561dd8bdddf8cf1`
- package materializer: [`self_owned_cg_independent_package_v1.py`](../../src/mage_ptcg/meta_specialist/self_owned_cg_independent_package_v1.py)、SHA-256 `22f674721dff1b6b840f4f1be77e8c26000f7f717308d35f25823f758912e8c6`
- generator: [`generate_self_owned_cg_independent_policy_meta_v1.py`](../../scripts/generate_self_owned_cg_independent_policy_meta_v1.py)、SHA-256 `bfacfeeb8c74d83fbc6317c0958efb975c8201a4c206163743cb614c08a29541`

renderer は root の public `cg.api` observation、合法手 cardinality、exception fallback を保持し、lethal／non-lethal attack、low-hand ability/supporter、damaged-active retreat、energy reserve、search-before-evolve、bench threat の別スコア面だけを追加する。生成 source には `RESEARCH_INDEPENDENT_LINEAGE: root-cg-public-state-v1` を埋め、`parent_deck=null`、`public_parent_read=false`、authority 全 false とした。

8 件の deck／policy identity は相互に distinct。factorial manifest は [`factorial_manifest.json`](../../runs/cg-self-owned-independent-root-policy-family-v1-20260816/factorial_manifest.json)、SHA-256 `39822b0ba64445cd25c2b9f614b34c8c9cdbc5c70597a4cc080de5f564d1daf4`。

## runtime smoke と source seal

8 source × `aristophanivan_multiply` × 両 seat × 1 repetition を 1 worker で実行し、16/16 `DONE`、fault 0。採用した summary は [`smoke-v4/smoke_summary.json`](../../runs/cg-self-owned-independent-root-policy-family-v1-20260816/smoke-v4/smoke_summary.json)、SHA-256 `4a72ff4a84c7cbbe39ae918a6fb6d6766ffdfa8c2d65d0cc0afdb393a314882f`。

12 worker および 4 worker の同一 smoke は `libcg` の `buffer full (capacity:7)` で ledger 未完了となったため、性能結果・promotion 根拠から除外した。partial roots は保全するが、成功扱いにしない。以後この source epoch の smoke gate は worker 1 とする。stdin multiprocessing の試行も同様に証拠へ算入しない。

fault-free smoke 後に [`runs/cg-self-owned-independent-root-policy-family-v1-20260816-promoted/`](../../runs/cg-self-owned-independent-root-policy-family-v1-20260816-promoted/) へ promote した。

- pool SHA-256 `5ebfe26de43e858db37d52dcab43509c49f6495899df9159b1076d36944fa1a7`
- fresh meta SHA-256 `a8d1ec399345d154a105fc1c0ababf219e8659793656ccd83e1fda78b9f0e2bc`
- meta manifest SHA-256 `6a7a2a4d0fc7abbe46260dae51315e554627082ad152ed156ec9b5b5ccb68916`
- split SHA-256 `2766a71abbca3caa8e5d06cac7fca8a72232666ba709ca939525d2796b5a555b`
- split: `META_TRAIN=6 / META_DEV=1 / META_FINAL=1`
- promoted source kind: `self_owned_official_card_data_deck_with_independent_root_policy`

## matched performance screen

各 variant について同一 deck の P1 control package を作り、promoted pool の META_TRAIN 6 source、両 seat、1 repetition で比較した。すべて fault 0 だが、positive／seat-safe gate を満たすものはない。

| variant | independent | P1 control | delta |
|---|---:|---:|---:|
| ability | 0.1667 | 0.5833 | −41.6667pt |
| balanced | 0.5833 | 0.6667 | −8.3333pt |
| bench | 0.3333 | 0.6667 | −33.3333pt |
| conservative | 0.4167 | 0.5000 | −8.3333pt |
| lethal | 0.3333 | 0.7500 | −41.6667pt |
| reserve | 0.5000 | 0.6667 | −16.6667pt |
| retreat | 0.5000 | 0.6667 | −16.6667pt |
| search | 0.5833 | 0.6667 | −8.3333pt |

balanced の fresh pool 8 source screen（16局／arm）は independent `0.375`、P1 `0.500`、差 `−12.5pt`、fault 0（summary SHA-256 `5d329fb635ebc75d17c1503c7a6f087ada1265cd12cdf024a8afdbcbfc39df12`）だった。META_DEV／META_FINAL は variant 選別には使用していない。

## 判定と次の使用条件

この epoch は「公式データだけから、P1から独立した policy lineage を生成し、fresh meta／split／matched CABT へ接続できる」ことの実証として保全する。一方、policy performance は明確な no-go なので、同じ independent surface の CEM や blind retry は開始しない。fresh pool はこの screen で META_TRAIN exposure 済みとして扱い、`cg_bestknown_loop_v1.py` の promotion input へ直接渡さない。

次に性能研究を再開する条件は、(1) P1または同等以上の親を明示した別 lineage、(2) source generation と holdout の exposure ledger、(3) worker 1 を含む再現可能な CABT runner、(4) independent seed／未使用 DEV／未使用 FINAL の順を別 epoch で確保すること。BestKnown、Champion、production、submission、commit、push は変更していない。
