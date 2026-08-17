# V4 portable closure read-only audit (2026-08-14)

## 結論

V4 seed-1 checkpoint と Archaludon deck の identity は整合するが、portable package としては未成立である。production entrypoint へ接続せず、提出可能性を発行していない。したがって本記録は性能結果ではなく、V4 を次の評価へ進めるための closure blocker の read-only 証跡である。

## 固定した入力

- checkpoint: `runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt`
- checkpoint file SHA: `ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319`
- checkpoint tensor-state SHA: `17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244`
- subject deck: `opponents/public_archaludon_cinderace_r7/deck.csv`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- existing audit command: `scripts/build_performance_submission_bundle_v1.py audit-wave6`

The existing audit returns `coherent_pair=true` and `submission_ready=false` with these blockers:

1. `production_entrypoint_not_connected`
2. `production_card_vocabulary_gate`
3. `runtime_dependency_closure_unvendored`

## Read-only import-closure probe

The transitive AST walk (including relative imports) started at `scripts/run_meta_specialist_v4_actor_smoke.py` and `src/mage_ptcg/meta_specialist/actor_pool_v1.py`. It found 60 repository-local Python files (1,202,251 bytes) and these host imports:

- `torch`
- `numpy`
- `kaggle_environments`

The walk found no missing Python module names, but that is not a package proof: V4 also resolves runtime data and registry files dynamically. A temporary staging probe copied the 60 local files plus the checkpoint, deck, registries, card CSV, and pool manifest. `python -I` isolated import passed, then the actor-pool registry load failed closed at the first absent opponent asset (`aman_crustleaware_fighting/deck.csv`). The actor-pool path loads the production card-vocabulary registry and `data/raw/EN_Card_Data.csv`, rebuilds archetype qualification, reads `opponents/pool_manifest.json`, and validates every registered opponent asset. These are outside the checkpoint/deck pair and are not vendored by the current V4 package route. The external opponent policy path additionally relies on the repository's shared `cg`/`vendor_opponent_pilots` assets when non-mirror opponents are selected.

The existing one-game V4 actor smoke remains a research-only runtime check. It does not convert the current source tree into an isolated submission archive, and it does not connect the checkpoint to `main._DEFAULT_AGENT`.

## Gate and next action

No V4 package, production edit, CABT campaign, training run, or submission was started by this audit. The route remains `STATIC_BLOCKED`. To reopen it, a separate research-only materializer must explicitly bind the local Python closure, data/registry closure, host dependency contract, production entrypoint, card vocabulary, checkpoint/deck SHAs, and an isolated import/smoke result. Until those facts are sealed, the submission-compatible P0 remains Rule v0 plus the root deck; independent performance screens use `workers=12` and `recycle=16` by default.

## Reproduction

```bash
PYTHONPATH=.:src .venv/bin/python scripts/build_performance_submission_bundle_v1.py \
  audit-wave6 \
  --checkpoint runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --deck opponents/public_archaludon_cinderace_r7/deck.csv
```

The AST closure probe was run as a read-only Python process over the same two entry files. No repository production file or prior run root was modified.
