# Public kernel re-export intake and wrapper contract fix (2026-08-16)

## 結論

公開 Kaggle kernel `res1235/rule-based-agent-mega-lucario-ex-deck-very-simple` は、source の `main.py` が `from agent import agent` で payload の callable を再公開していたため、従来の entrypoint gate では `missing_agent_entrypoint` になっていた。source 本体は改変せず、明示的な `agent` import alias を entrypoint として認識する gate を追加した。

さらに、生成 wrapper が一引数 payload に `configuration` を二引数で転送していた契約バグを修正した。payload の signature が二引数を bind できる場合だけ configuration を転送し、それ以外は observation 一引数で呼ぶ。旧 artifact は上書きせず、wrapper 修正版を別 intake root へ封印した。

## provenance と封印

| 項目 | 値 |
|---|---|
| kernel ref | `res1235/rule-based-agent-mega-lucario-ex-deck-very-simple` |
| source URL | `https://www.kaggle.com/code/res1235/rule-based-agent-mega-lucario-ex-deck-very-simple` |
| raw tar SHA-256 | `9b5dee3801e7ee4dff40af94fd08476849bbd08cbc19cd49f254283c197d0bea` |
| raw `main.py` SHA-256 | `dab324f833cf14f63540392e1e1c4cf788ee27fcbfb54e669e43919f675e4795` |
| canonical deck SHA-256 | `282bbb43e78cd05d63c1bf2e680202537bdc5ad680966ead77e8dc8400f65cce` |
| intake config | `configs/meta_specialist/cg_kaggle_kernel_meta_reexport_wrapperfix_epoch9_20260816.json` |
| intake root | `runs/cg-kaggle-kernel-meta-intake-public-reexport-wrapperfix-epoch9-20260816` |
| intake report SHA-256 | `97080975757a7f31e1b212b0e41b5120079663770c3a10a0ea63367ba3327058` |
| fresh meta SHA-256 | `bde52f78b9897b0751f27439f2e8bd81c986fff8ba4f8623c4fbafaac0a59103` |
| sealed pool SHA-256 | `d91a0810ba4aa6f6663dd802bd957ce3ca5a1b18893d3ed83ac3c84d82423a70` |
| generated wrapper SHA-256 | `be74996cfb949205f3dc3c59814b23c649b4400c25c931df76e9df7ca0af74d2` |

The source remains `local_eval_only`; the intake report keeps all training, promotion, long-run, and submission authority false. This is a re-check of a previously unexecuted source, not a claim of a new public source lineage.

## static and runtime gates

- raw tar SHA check: PASS; no network or imports during intake.
- exact 60-card deck: PASS; local official catalog reports exactly one ACE SPEC (`1159`).
- static source scan: PASS; no network, subprocess, dynamic execution, environment-secret, or filesystem-write finding.
- entrypoint: PASS after explicit `from agent import agent` recognition.
- wrapper unit contract: PASS; one-argument payload does not receive configuration, two-argument payload does.
- bounded CABT smoke: `runs/cg-kaggle-kernel-meta-smoke-public-reexport-wrapperfix-epoch9-20260816-ordered-2x-stable`.
  - pool: existing `official_random` only;
  - both seats, 2 games/seat, 4 requested;
  - `4 DONE`, `0 fault`, `4W-0D-0L`, score rate `100%`;
  - smoke summary SHA-256: `4442257cdadba8a8522febeca66e9cf0ddc11f1fbf12b2581f4f792787f92669`;
  - completion manifest SHA-256: `a988456d5318e176656127f7798c5c81c8ab9222501c017f3412ccbb47260e73`.

The first pool-bound attempt before the wrapper/order fixes aborted with native `buffer full` and is retained as a failed diagnostic artifact. It is not performance evidence and is not used for promotion.

## decision

The source is **smoke-qualified for evaluation-only use**. It is not added to the production `opponents/` pool, not included in META_TRAIN/DEV/FINAL, and not eligible for CEM, promotion, or submission until an explicit unused-meta schedule and a multi-opponent independent gate are created. The next source-generation step is to reuse this safe wrapper/order protocol for genuinely new, non-reused public source snapshots rather than re-running the same lineage.
