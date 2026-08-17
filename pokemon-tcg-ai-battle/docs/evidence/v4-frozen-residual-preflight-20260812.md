# Frozen Wave6 residual learning preflight — 2026-08-12

## 判定

これは provenance、known-domain、loss-mask、tiny-overfit 実行境界を閉じた**preflight時点の証跡**である。後続の bounded sidecar-only tiny integration は別証跡 [v4-frozen-residual-tiny-integration-20260812.md](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/v4-frozen-residual-tiny-integration-20260812.md) に分離している。本ファイル単体の生成時点では JSONL の読み取り・canonical public context/action hash 集計・descriptor 検証だけで、V4 model load、optimizer、trainer、CABT、longrun は起動していなかった。

成果物:

- schema/loader/mask: [frozen_residual_preflight_v1.py](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/src/mage_ptcg/meta_specialist/frozen_residual_preflight_v1.py)
- actual builder: [build_frozen_residual_preflight_manifest_v1.py](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/scripts/build_frozen_residual_preflight_manifest_v1.py)
- fail-closed dry-run / bounded integration runner: [run_frozen_residual_tiny_overfit_v1.py](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/scripts/run_frozen_residual_tiny_overfit_v1.py)
- focused tests: [test_frozen_residual_preflight_v1.py](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/meta_specialist/test_frozen_residual_preflight_v1.py)、[test_run_frozen_residual_tiny_overfit_v1.py](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/tests/meta_specialist/test_run_frozen_residual_tiny_overfit_v1.py)
- actual manifest: `runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json`

## Provenance schema

`Wave6ProvenanceV1` は seed 0/1 ごとに次を必須化する。

- Wave6 closed checkpoint path、file SHA-256、tensor-state SHA-256
- sealed screen JSON path/SHA-256
- sealed transition JSONL path/SHA-256
- subject deck SHA-256
- known domain source partition（`train` のみ）

top-level `FrozenResidualPreflightManifestV1` は seed 0 と seed 1 をこの順でちょうど2つ含む。subject deck は両seedで一致し、transition source SHA はseed間でdistinctでなければならない。`promotion_authority`、`longrun_allowed`、`training_permitted` は常に `false` であり、loader は open schema、duplicate seed/source、SHA不整合、authority付与を拒否する。

`verify_files=True` を指定した loader は checkpoint/screen/transitions の regular non-symlink file SHA を再計算して照合する。tensor-state SHA は既存 closed Wave6 run manifest の provenance として保持し、今回のpreflightでV4 loaderを起動して再計算していない。

## 実データ known context/action manifest

Builder command は次のとおり。`parse_transition_payload_v4` と `build_residual_context_v1` だけを使い、model/optimizer/trainer/CABTは呼ばない。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python scripts/build_frozen_residual_preflight_manifest_v1.py \
  --run-manifest runs/meta-specialist-v4-archaludon-longrun-wave6-current/run-manifest.json \
  --seed0-screen runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.json \
  --seed0-transitions runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.transitions.jsonl \
  --seed1-screen runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.json \
  --seed1-transitions runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.transitions.jsonl \
  --subject-deck-sha256 42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e \
  --output runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json
```

生成 artifact SHA-256 は `7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689` である。seedごとの集計は次のとおり。

同時刻に別プロセスで生成された `runs/meta-specialist-v4-frozen-residual-preflight-20260812/wave6-residual-preflight-manifest-v1.json`（SHA `58733323ba94621a31dfdfdf5864eed68b59f2b97b1bc79ebda2646b376edfdb`）は、builderへSTOP keyを追加する前の旧artifactである。seedのcontext/prefix件数は同じだが、action countがseed0=1000、seed1=1063であり、今回の推奨artifactはSTOP keyを含むseed0=1001、seed1=1064の方である。旧artifactをtiny-overfit入力へ使わない。

| seed | train transitions | prefix rows | unique public context IDs | unique semantic/STOP action keys |
|---:|---:|---:|---:|---:|
| 0 | 3,678 | 7,784 | 7,706 | 1,001 |
| 1 | 3,892 | 8,259 | 8,191 | 1,064 |

Action key集合には semantic canonical bytes由来のキーに加えて、STOPが合法なprefixで使用する `STOP_ACTION_KEY_V1` を含めた。出力へは opponent ID、seat、game/component identity、policy identity、physical serial/local action IDを含めていない。context IDは public model input + step input の canonical bytes、action keyは semantic canonical bytesのSHA-256である。

対応 Wave6 checkpoint provenance:

| seed | checkpoint file SHA-256 | tensor-state SHA-256 | transition source SHA-256 |
|---:|---|---|---|
| 0 | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` | `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` |
| 1 | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` | `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26` |

## Effective denominator / mask semantics

`ResidualMaskRowV1` は各 recurrent prefixについて、`eligible`、`supervision_weight`、`recurrent_context`を検証する。

- すべてのrowは `recurrent_context=true` でなければならない。
- `eligible=true` のrowだけが loss-bearing で、weightは `(0,1]`。
- `eligible=false` は context-only で、weightは必ず `0`。
- `denominator_rows == loss_bearing_rows`、`effective_loss_mass == sum(supervision_weight)`。
- context-only rowは hidden state のsequence contextを進めるが、loss denominatorへ入らない。

TDD fixtureでは、3行（context-only 1、eligible weight 0.5/1.0）から `effective_loss_mass=1.5`、`denominator_rows=2`、loss terms `(100,2,3)` から weighted sum `4.0` を確認した。context-onlyへ正のweightを付ける場合、またはrecurrent contextを無効化する場合は fail-closed である。

## Tiny-overfit runner 境界

`run_frozen_residual_tiny_overfit_v1.py` はデフォルト実行を拒否し、`--dry-run` または明示的な `--execute` が必要である。dry-run時は manifest を load して descriptor を出すだけであり、execute時も対応する closed Wave6 と少数 sealed train prefix に対する sidecar-only bounded update に限定される。execute結果の解釈は別 evidence に記録し、性能証拠にはしない。

```json
{
  "execution": "DRY_RUN_NOT_STARTED",
  "training_permitted": false,
  "cabt_permitted": false,
  "longrun_allowed": false,
  "promotion_authority": false,
  "optimizer_updates": 0,
  "epochs": 0
}
```

runnerは production actor pool、CABT、V4 BC trainer を起動しない。preflight生成時点では model/optimizer 接続を行っていなかったが、後続の sidecar-only trainer は既存 Wave6 checkpointを strict load するため、その実行結果は [tiny integration evidence](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/docs/evidence/v4-frozen-residual-tiny-integration-20260812.md) に分離している。

実データmanifestへdry-runを適用した最新descriptorは `runs/meta-specialist-frozen-residual-preflight-20260812/tiny-overfit-dry-run-descriptor-v1.json`（SHA `57be37541c4803b8e1a866046e21980a2658f60babdadb68577cb1b82ba99616`）である。stdout JSONも同一SHAで、execution=`DRY_RUN_NOT_STARTED`、optimizer_updates=0、training/CABT/longrun/promotion全てfalse、`SELF_IMITATION_INTEGRATION_ONLY`、target kind/seed別target manifest SHAを確認した。

## 検証結果

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test PYTHONPATH=.:src \
  .venv/bin/pytest -q -s \
  tests/meta_specialist/test_frozen_residual_v1.py \
  tests/meta_specialist/test_frozen_residual_preflight_v1.py \
  tests/meta_specialist/test_run_frozen_residual_tiny_overfit_v1.py \
  tests/meta_specialist/test_research_logit_ensemble_v1.py
```

preflight/runner 7 tests、既存 ensemble + residual 12 testsを通過（最終一括実行は後続で再確認）。各新規 module/script/test の `py_compile` と `git diff --check` も実行済み。docs validator は既存 canonical docs 13件を通過した。

## 未実施・残リスク

- この preflight snapshot の時点では residual optimizer接続、tiny-overfit実学習、checkpoint生成は未実施。後続実施分は別 evidence に記録。
- known context/action coverageはtrain由来であり、validation/shadow-Cを含まない。既知集合を勝率後に変更してはならない。
- seed0/1でcontext/action集合が異なるため、sidecarはseed対応manifestを混同しないこと。
- tensor-state SHAの再計算、variable legal-domain batch、ordered/soft-action mass、teacher correctness、outcome valueは未監査。
- same-checkpoint CABT noise（seed0 SD約2.62pt、seed1 SD約7.51pt）を超える改善証拠が得られるまで長時間学習・promotion・提出は禁止。
