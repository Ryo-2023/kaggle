# Water Box runtime-safe meta v1 implementation plan

1. `tests/test_waterbox_runtime_safe_meta_v1.py` に variant、変換、fail-closed、sealed 12-row split の失敗条件を先に書く。
2. `src/mage_ptcg/opponent_ingest/waterbox_runtime_safe_meta_v1.py` を追加する。base asset hash、deck hash、static findings、artifact freshness、pool/meta/split/fresh manifests を一つの seal 関数で生成する。
3. `scripts/generate_waterbox_runtime_safe_meta_v1.py` と明示的な JSON config を追加する。
4. focused test、seal、TRAIN smoke、P1 fixed CEM と independent re-eval を実行する。
5. 結果を evidence/status/handoff/context pack に記録し、P1 promotion gate は別途明示的に維持する。
