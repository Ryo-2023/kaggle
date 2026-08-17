# 公開kernel Marnie base static variant source intake（2026-08-16）

## 結論

`llccqq624/ptcg-alakazam-marniebelief-0723-a` から、提出rootの動的import wrapperではなく、同梱 `base_main.py` を選んだ静的variantを研究用meta sourceとして封印した。初回は共通CABT intake wrapperと一引数 `agent` の契約不一致で失敗したが、policy decision pathを変更しない二引数compatibility adapterを局所追加した v2 は bounded smoke を fault なく完了した。これは `SOURCE_GENERATION_PASS / PERFORMANCE_NOT_STARTED` であり、P1、root deck、BestKnown、Champion、production、submissionは不変である。

## 出典とidentity

- Kaggle kernel: `llccqq624/ptcg-alakazam-marniebelief-0723-a`
- URL: `https://www.kaggle.com/code/llccqq624/ptcg-alakazam-marniebelief-0723-a`
- 取得時刻: `2026-08-15T17:46:00Z`
- 元 `main.py` SHA-256: `567ea9bec00f60204510e7b507833de06d6ced9b5f25e3e1d2f01ddf647ae749`
- 選択した `base_main.py` SHA-256: `570eacb2c5d0362816acb60b59855d44e665bea19e90274d6907fda07beb1ef0`
- staged source SHA-256（v2前）: `5079eca56c00edc5b510e1caa901e457c00e94dfa63d37bb53a0cb4e7377c296`
- v2 tar SHA-256: `513d6858f78c26bc3c6aec2920f638eaa44b9790459d40bc8bbfe0f346616f15`
- v2 promoted wrapper `main.py` SHA-256: `ba9af9aacbb68fcf7e3bfde3f88de50e3a259cf233e8d0be0e571e6dddade380`
- `deck.csv` bytes SHA-256: `0598646548d081832ec311c15fdc369b32c6f5e63175b0cfd1904d21fd082451`
- canonical deck SHA-256: `606a775392ffe25e058b19c17801d58a4bf30f7cd8c62782388d3de7e7eb5283`
- belief artifact SHA-256: `860014d0e19693607a819dcd2951fa8d186e6e95464cc2406095ffb582518a7f`

提出rootの `main.py` と `strongguard_main.py`／`dailyprior_main.py` は `importlib` 依存のため静的境界で採用しなかった。採用したのは公開archive内の `base_main.py` と `marnie_belief.json` だけである。v2の変更は `_policy_agent(obs_dict)` を呼ぶ二引数 `agent(obs_dict, configuration=None)` adapterの追加に限定し、source provenanceにも「独立作者系譜ではない静的variant」と明記した。

## intake／runtime gate

- intake config: `configs/meta_specialist/cg_kaggle_kernel_meta_marnie_base_static_v2_20260816.json`
- intake root: `runs/cg-kaggle-kernel-meta-intake-marnie-base-static-v2-20260816/`
- promoted root: `runs/cg-kaggle-kernel-meta-promoted-marnie-base-static-v2-20260816/`
- pool SHA（promoted）: `ef9aafdcabc62e7dc624bf1b6447a6d2fb65e801aa0b0c26fc4bb6b9dfe1db50`
- fresh meta SHA（promoted）: `887c604d0b27706ed0f709bedfb9704fb7555bef85f2f378806fe6020a00bfd6`
- intake fresh meta SHA: `af78f47b66fd100b5939329edb6eb40aa28c172db41b014bc67226acbf86748c`
- static findings: `[]`
- exact-60 / ACE SPEC: PASS（ACE SPEC 1枚）
- smoke: `runs/cg-kaggle-kernel-meta-smoke-marnie-base-static-v2-20260816/`
- smoke: 4/4 `DONE`, fault 0, draw 0, 3W-1L, score 75.0%（各seat 2局）
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

初回 v1 `runs/cg-kaggle-kernel-meta-smoke-marnie-base-static-20260816/` は4/4 `AGENT_ERROR`だった。原因は policy logic ではなく `agent(obs)` に対して wrapper が `(observation, configuration)` を渡す契約差で、直接のエラーは `TypeError: agent() takes 1 positional argument but 2 were given`。v1は診断artifactとして保持し、v2を別hashで封印した。

## 未実施と次のゲート

新規参照はこの1件だけであり、`build_historical_meta_split_v1.py` の重複禁止条件を満たす `META_TRAIN`／`META_DEV`／`META_FINAL` の三分割を作れない。したがって、P1固定CEM、独立seed validation、deck phase、`cg_bestknown_loop_v1.py` 接続は未実施である。次は、この公開snapshotを独立作者系譜と水増しせず、別の未性能使用 policy lineageまたは明示的な self-owned source-generation variantを少なくとも2件追加し、runtime smoke候補と性能holdoutを分離してからCEMへ進める。

## `ono-` の出典に関する訂正

`ono-` はこの公開kernelの作者名ではない。local Git の `bfe-lab-ono` identityと branch `agents/ono-cg-lethal-v1` に由来する識別子である。一次根拠は commit `1965b42b028f10960d08ccb4980be5b76946f98b`（parent `235d2a874d023d2ab58eef16d36f74b4b8276beb`）の author/committer と、そのcommitに記録された self-owned P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` である。公開sourceの系譜と local self-owned packageの系譜は別欄で管理する。

## Authority

`local_eval_only`, `research_only`; training／promotion／submission／longrunは全て禁止。commit、push、Kaggle提出は行っていない。

## 追加 provenance 監査（`ono-` と root deck の分離）

上記の「self-owned」は policy と package branch の識別子を指し、root deck の独自性を意味しない。root deck SHA `2a541d7b...` は、次の local opponent snapshot の `deck.csv` と raw bytes が完全一致する。

| local snapshot | 記録された公開kernel |
|---|---|
| `opponents/aman_crustleaware_fighting/deck.csv` | [`aman5153684/a-crustle-aware-fighting-agent`](https://www.kaggle.com/code/aman5153684/a-crustle-aware-fighting-agent) |
| `opponents/makthanithin_baseline1084/deck.csv` | [`makthanithin/pokemon-tcg-ai-battle-1084-5-baseline`](https://www.kaggle.com/code/makthanithin/pokemon-tcg-ai-battle-1084-5-baseline) |
| `opponents/kojimar_lucario/deck.csv` | [`kojimar/simple-baseline-matchup-tests`](https://www.kaggle.com/code/kojimar/simple-baseline-matchup-tests) |
| `opponents/aristophanivan_probabilistic/deck.csv` | [`aristophanivan/improved-probabilistic-agent`](https://www.kaggle.com/code/aristophanivan/improved-probabilistic-agent) |
| `opponents/aristophanivan_multiply/deck.csv` | [`aristophanivan/multiply-agent-best-940-lb`](https://www.kaggle.com/code/aristophanivan/multiply-agent-best-940-lb) |

したがって、現時点で正確な表現は「`ono-` branchで封印した self-authored policy と、複数公開snapshotと同一の common/public root deck」である。5件が同じdeck bytesなので、repoの証拠だけから単一の元kernelを特定することはできない。P1 policy `main.py` は commit `1965b42...` で parent の汎用random stubから置換され、上記5件の公開 `main.py` SHA（`67549f...`、`44187c...`、`202ae8...`、`8f108c...`）とは一致しない。ただし、Git差分だけでアルゴリズムの完全な独自性まで証明したものではない。
