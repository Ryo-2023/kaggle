# Rule v0 MAIN `ABILITY+120` telemetry weighted48 remeasurement

既存の初回48局 root は不変のまま、seed base `14910200` の fresh rootで telemetry runner を接続した candidate/control 各48局を実行した。v2 は runner export 不備で96/96 faultとなったため `SCREEN_INVALID` として除外し、v3のみを判定対象とする。

## 結果

| arm | W/D/L | score rate | fault | seat |
|---|---:|---:|---:|---|
| candidate | 5/0/43 | 10.4167% | 0/48 | 24/24 |
| control | 7/0/41 | 14.5833% | 0/48 | 24/24 |

paired keysは48/48一致。`loss→win=3`, `win→loss=5` で net `-2 wins`、candidate差は `-4.1667pt`。coverage telemetryはcandidate全48行で取得でき、集計値は `observations=2126`, `main_observations=1375`, `eligible_main_observations=147`, `override_attempts=1375`, `override_applied=44`, `fallback_count=0`。controlはbaselineのため overlay telemetry unavailableを明示した。

coverageは0ではなかったが、candidateがcontrolを下回ったため、この surface は即STOP。common24-96、384、deck child、promotion、training、submission、longrunへは進めない。

## 契約・再現

- fresh root: `runs/final-sprint-autonomous/rule-v0-main-ability-plus-120-telemetry-weighted48-20260814-v3/`
- seed base: `14910200`（初回screenとdisjoint）
- workers: `12`, worker recycle: `16`
- candidate: public MAIN `ABILITY` score delta `+120`, exact Rule v0 fallback
- opponent: META_TRAIN 20 IDs、heldout exposure `0`, local_eval_only, synthetic false
- all authority false; production main/agent/evaluator unchanged

## Artifact SHA

| artifact | SHA-256 |
|---|---|
| bridge `manifest.json` | `c8effcb72176eac44f2f4dad68fdebf025fcce416a525ca363eaf1efdcbbc04d` |
| bridge `games.json` | `e40ddc503e81704e5722a8c5e632017e507ca864b3d2896882441da669f415b6` |
| bridge screen SHA | `c062845c2dbb86227c2c4a7e8a2b6ddaa82594bb7c37a9aad7f7ec5e5bcdaaa1` |
| evaluation `ledger.jsonl` | `46eade78a1dc964ab6198cfde8eba7ba7860d41b27106b7fa425779b0593fbac` |
| evaluation `summary.json` | `93694a4340221ec9ecdec043139b1f85bb75393195bb1dbd3acdbe44ddbc1c433` |

## 判定

`ABILITY+120` は telemetry取得自体は成功したが、fresh weighted48で負差となったため局所 surface を棄却する。次候補は別の未評価 policy/deck surfaceを選び、同じ ABILITY candidate の再測定は行わない。

