# P2 near-lethal bonus sweep / independent confirmation — 2026-08-15

## 結論

P2 robust g01（policy SHA `4261870c855d68abfbb96df029b5e66c6f019f398471701ceaac03f72f2b03c4`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）を固定し、既確認の `near_lethal_attack_bonus=12000` を除いた強度グリッドを新規screenした。`+24000`だけがscreenで正方向だったが、独立確認ではP2 controlに `-1.3455pt` へ反転した。したがって新しいBestKnown、P3、deck phase、Champion、production、提出物は生成していない。

全screen 288局と確認768局は `DONE`、fault 0 で、候補のseat collapseは確認されなかった。しかし確認は再利用 `META_TRAIN` であり、fresh・unused metaではない。今回の結果は性能診断として封印し、昇格根拠にはしない。

## 実装と再現コマンド

新規runnerは、既確認点をblind retryしない bounded strength sweepとして実装した。

- `scripts/run_cg_p2_near_sweep_v1.py`
  - 候補は `(4000, 8000, 16000, 20000, 24000)`。
  - `12000` を入力すると fail-closed で拒否する。
  - 候補ごとにP2 controlを共有し、research-only／promotion authority falseをmanifestへ固定する。
- `tests/meta_specialist/test_run_cg_p2_near_sweep_v1.py`
  - グリッドの順序、他の2軸をゼロに固定すること、既確認点の除外を検証する。

screenの再現コマンド:

```bash
PYTHONPATH=src:. python scripts/run_cg_p2_near_sweep_v1.py \
  --output runs/final-sprint-autonomous/cg-p2-near-sweep-v1-20260815 \
  --base-seed 48416000 --repetitions 2 \
  --values 4000,8000,16000,20000,24000
```

独立確認は、screenで最大の正差だった `cg-p2-context-g00-c04-217aa3465683` を、base seed `48486000`、候補/control各384局（各opponent×seat 16反復）で実施した。

## Strength sweep

Artifactは `runs/final-sprint-autonomous/cg-p2-near-sweep-v1-20260815/`。共有controlは48局、各候補も48局で、全体288局が `DONE` / fault 0 だった。control objectiveは `0.1718341431`。

| near-lethal bonus | candidate objective | control objective | delta | candidate seat gap | 判定 |
|---:|---:|---:|---:|---:|---|
| 4,000 | 0.1281853 | 0.1718341 | −4.3649pt | 0.00% | STOP |
| 8,000 | 0.1491655 | 0.1718341 | −2.2669pt | 4.1667% | STOP |
| 16,000 | 0.1251136 | 0.1718341 | −4.6721pt | 16.6667% | STOP |
| 20,000 | 0.1676548 | 0.1718341 | −0.4179pt | 8.3333% | STOP |
| 24,000 | 0.2133247 | 0.1718341 | **+4.1491pt** | 12.5000% | confirmation only |

screen summary SHA `4e3b6895defd57fb47e664955219322318a6191212850990ba1f075edea7453d`、complete manifest SHA `8138b8523ddb1bbe222d63889e704dbdb00da95195e4ad5f3b378edc20e8a74f`、sweep manifest SHA `05f1dfdd3fc1c57c8ff9874702bf66521aac14c266c5b194b7e142a4539b96ff`。

## Independent confirmation of +24000

Artifactは `runs/final-sprint-autonomous/cg-p2-near-sweep-c04-confirmation-seed48486000-v1/`。candidate/control各384局、合計768局、両seat、全 `DONE` / fault 0 だった。

- candidate `48W-0D-336L`、objective `0.1346655583`、seat rates `14.0625% / 10.9375%`、seat gap `3.125%`。
- P2 control `55W-0D-329L`、objective `0.1481205173`、seat rates `15.1042% / 13.5417%`。
- delta `−1.3454959pt`。candidateはseat-safeだが、性能差が負のため `NOT_PROMOTABLE`。
- meta provenanceは `reused_meta_train`。fresh/unused gateは満たさない。

candidate config SHA `217aa346568344c2d09bb87f391c75522b902f9c24e858f2430ada44eedc2387`。confirmation summary SHA `e5b45cd54bad8b93daa554a97367b5ed054eda6b52d2ab245a553721eb8caf2a`、complete manifest SHA `b6e1c3a2ee0b97cb30bb4607c385bce76ed4686afe0ab0833cde8fca0bd6f84d`。

## Gateと次の境界

現在のproduction/ChampionはP1 `cg-lethal-target-v1`＋root deckのままである。P2 robust g01はresearch parent候補として保存するが、今回の近傍強度面は採用しない。既確認 `+12000` のblind retryも行わない。

現ローカルpoolには、過去ledgerで使用済みでない、smoke-readyなfresh public metaがない。`cg_unused_meta_holdout_v1〜v3` と residualは再利用不可、`water_box_search` / `waterbox_search_v3` は internal `local_eval_only` / quarantineである。したがって次の昇格に必要なのは、(1)新しい未使用meta source、(2)同一candidate/controlの独立seed、(3)fault 0・両seat・正差の再現、である。

## 実装SHAと権限

- runner SHA `ab35d62f3287612d1090f6174d021ab6ec8b857424eea5056f07da158ec5ee5f`
- test SHA `7801b0eb32f39c893805c3b9286d8f1d4571a39c44fe356c6cec5dd4d7d925c8`
- screen / confirmation は research-only。promotion、training、longrun、submission authorityは全て false。
- commit、push、Champion変更、Kaggle提出は行っていない。
