# P2 contextual attack surface screen / confirmation — 2026-08-15

## 結論

P2 `cg-p1-cem-incumbent-g01-c83df4408b24`（policy SHA `4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4`）を固定し、公開状態だけで判定する3条件の小さな attack-score surface を追加した。

screenでは `near_lethal_attack_bonus=12000` の単独候補だけが正方向だったが、独立 seed の384局/arm確認でも差は `+0.6003pt` に留まった。全768局は `DONE`、fault 0、candidate seat gap 1.3021% である。ただし確認に使える未使用metaがローカルに存在しないため、判定は `NOT_PROMOTABLE_REUSED_META` とする。P2/P3、BestKnown、Champion、production、deck phase、submissionは変更しない。

## 実装境界

- `src/mage_ptcg/meta_specialist/cg_p2_context_surface_v1.py`
  - P2親SHAとroot-deck SHAを検証する3次元config。
  - `near_lethal`（non-lethal gap 1–50）、相手active可視energy 2以上、own bench満杯だけを参照する。
  - legal action、fallback、deck、private hand/deck、将来乱数を変更しない。
  - self-owned runtime package、archive、通常interpreter clean-room smokeを生成し、authorityは全false。
- `scripts/run_cg_p2_context_screen_v1.py`
  - 8点gridをP2 controlと同一opponent/seat/seed strataでscreen。
  - control blockは共有し、評価分母にfaultを残す。
- `scripts/run_cg_p2_context_confirmation_v1.py`
  - 候補1件の384局/arm確認。
  - `fresh_unused` 以外のmeta provenanceではpositiveでも昇格不可。

実装SHAは surface `d1f32ede2fc3f3629199f878b5fbf0a3d2e707381fa27998c117d286547d64e7`、screen runner `251dfa3aa528558956805fdbf5fdccdaf25e67f6de9889b69159dc29cc8611a4`、confirmation runner `608bdfe05d80c981e002444302fdcb81a3ba2740a09b038505c7256f4564d391`。focused tests 7件、py_compile、`git diff --check`を実行した。

## Screen

Artifact: `runs/final-sprint-autonomous/cg-p2-context-screen-grid-v1-20260815/`

- META_TRAIN 12 opponent、各seat/rep 2、8候補＋共有control、合計432局。
- 全432局 `DONE` / fault 0。
- control: 10W/48、objective `0.2091052`。

| candidate | config (near, threat, bench) | delta | 判定 |
|---|---:|---:|---|
| c00 | (0, 0, 0) | −8.2541pt | STOP |
| c01 | (12000, 0, 0) | **+0.9083pt** | promising screen |
| c02 | (0, 12000, 0) | −0.8388pt | STOP |
| c03 | (0, 0, 12000) | −8.0613pt | STOP |
| c04 | (12000, 12000, 0) | −12.2639pt | STOP |
| c05 | (12000, 0, 12000) | −9.5642pt | STOP |
| c06 | (0, 12000, 12000) | −10.3429pt | STOP |
| c07 | (12000, 12000, 12000) | −7.8172pt | STOP |

screen summary SHA `6efbb3f3e8dcb2ae9f6cb0d6fc9421bf21965c3cdebf188955866b5f9ba553ea`、complete manifest SHA `747d7ce9ad5ff8db462fcec104371f249ec0cba19e45150cd59540fda7b0966c`。

## Independent-seed confirmation

Artifact: `runs/final-sprint-autonomous/cg-p2-context-c01-confirmation-seed48386000-v1/`

- candidate `cg-p2-context-g00-c01-bb78f91b9def`、config SHA `bb78f91b9def81e469ee1574fdfc7b0aebb285432328492273556d46306b7af9`。
- base seed `48386000`、META_TRAIN 12 opponent、各seat 16反復。
- candidate/control 各384局、合計768局、全 `DONE` / fault 0。
- candidate `55W-1D-328L`、objective `0.1499482`。
- control `53W-1D-330L`、objective `0.1439450`。
- delta `+0.6003pt`、candidate seat gap `1.3021%`。
- meta provenanceは `reused_meta_train`。したがって positiveでも `NOT_PROMOTABLE_REUSED_META`。

confirmation summary SHA `acab22bd488218bbb5ea77252b1708e08b00fcd5feb8335b07a5397231713af6`、complete manifest SHA `38f1b6a548f8caeb3a24080a5ba5143d31bec18abd70f270f228919933b45d77`。

c01 packageは通常interpreter smoke `1/1 DONE`、policy SHA `73c63b1a7b03442e7ca9ac137ba2617b0ba2d40fa22986c5a894dd1130798bf5`、deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、archive SHA `9f938ac0521e4653a00d08603e29b1443f4230c720305adbd78b65df6f38b13d`である。submission-ready flagはfalseのまま保持する。

## Meta provenance gate

`cg_unused_meta_holdout_v1〜v3` と residual configは過去ledgerで使用済みであり、未使用metaとして再利用しない。pool内で名前上残る `water_box_search` / `waterbox_search_v3` は internal `local_eval_only` の slow/quarantine assetで、提出モデルの未使用meta gateには使わない。現在、再現性確認に使える fresh・unused・smoke-ready public metaは0件である。

次の昇格条件は、(1) source/deck/policy/config/seedを束縛した新しい未使用meta、(2) 同一candidate/controlの独立seed、(3) fault 0・両seat・positive delta の再現、の3点である。新しいmetaが得られるまでは、c01のblind retry、P2/P3昇格、deck探索、Champion変更、Kaggle submissionを起動しない。

