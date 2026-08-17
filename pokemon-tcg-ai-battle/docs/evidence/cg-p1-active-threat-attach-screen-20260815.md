# P1 active-threat attachment surface screen（2026-08-15）

## 結論

公開P1 telemetryから、既評価のnear-lethal／damaged-active／attack-cooldownとは異なる「健全なactiveがenergy 1で、相手activeがenergy 2以上のとき、activeへのFighting Energy attachmentを少し優先する」surfaceを1本だけ作り、同一24相手・両seat・paired strataのCABT screenを実行した。candidateは `17W-0D-79L/96`、P1 controlは `18W-0D-78L/96`、差は `-1.0417pt`、全192局 `DONE`・fault 0だった。正差でないため、このsurfaceはここでSTOPする。

P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submission authorityは変更していない。再利用metaのscreenであり、fresh/unused metaの昇格証拠にはしない。

## 仮説の根拠と境界

入力は `runs/final-sprint-autonomous/cg-p1-public-telemetry-96-20260815-v2/` の公開projectionだけである。MAINの2,507行を再集計し、`self active hp == maxHp`、`self active energies_count <= 1`、`opponent active energies_count >= 2`、ATTACK option合法の行は171行だった。そこでP1が選んだ操作は `END 85`、`PLAY 45`、`ATTACH 32`、`ATTACK 3` などで、操作と勝敗の同時出現は因果的なaction valueではない。この集計は候補の優先順位を決めるためだけに使い、training label・teacher label・promotion evidenceには使っていない。

candidateのoverlayは、Fighting Energyを可視activeそのものへ付けるoptionだけを対象にし、activeが満HP、active energyがちょうど1、相手active energyが2以上、当ターンのenergy未添付という条件で `+6000` する。それ以外、malformed state、bench target、非Fighting optionはP1のexact scorerへ戻る。P1 sourceとdeckはhash-boundである。

## 実行契約

| 項目 | 値 |
|---|---|
| candidate | `cg-p1-active-threat-attach-v1` |
| control | `cg-lethal-target-v1` |
| base seed | `50310000` |
| opponent refs | `performance_first_broad_pool_v1.json` の24件 |
| seat / repetition | 両seat、各2反復（candidate/control各96局） |
| workers / recycle | 12 / 16 |
| evaluator | `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08` |
| pool manifest SHA | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| broad config SHA | `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b` |
| authority | training / promotion / longrun / submission 全て false |

candidate/controlのpair key＋seed strataはrunnerのfinalizeで一致を検証し、smokeも `DONE` だった。全192局のstatusは `DONE`、fault 0、draw 0である。candidate seat scoreはseat0 `9/48`、seat1 `8/48`、controlは各 `9/48` で、candidate seat gapは2.0833ptだった。

## 判定

`candidate_delta_points = -1.0417` のため `STOP_NEGATIVE_REUSED_META`。独立384/768、CEM更新、P2/P3昇格、deck search、training、longrun、Champion変更、Kaggle提出は起動しない。現poolのpublic・smoke-ready 70件は既存artifactへ出現済みでfresh/unused sourceがないため、screenの負差をfresh gateへ拡張できない。

## artifactとSHA

- artifact root: `runs/final-sprint-autonomous/cg-p1-active-threat-attach-screen-20260815-v1/`
- `manifest-complete.json`: `ae34bbfc4e302c021f6854d5c1261e11bb04389f9484008753f5fb483718fb93`
- `summary.json`: `85c61bd6090a5e00268e7c364e6b02483babeba9a0c8a5cd1a22bfd282e9375a`
- `evaluation/ledger.jsonl`: `9169701d581a550ad961f9a1005e7cde7d951aa8efb7f7a534bd72bf5ec95bf0`
- candidate policy: `78d8aaf573555c2e1875a820db033d8d7b89722326ae932d3d1d15aa95180b84`
- candidate/root deck: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- surface module: `835f809e730d3b729499f8ce2fe90390c546360741c0331821d6725d2d38dab2`
- screen wrapper: `33f54d3ea0ddfd9e4397d722ce5209457f0f04b34429c92ac4099e2871c7cb93`
- focused test: `c44bcb79b8533c42cf66de197869b6cdb4bba7743d81d9931e8373bc64ea883d`

## 検証

- `TMPDIR=/tmp PYTHONPATH=.:src pytest -q tests/meta_specialist/test_cg_p1_active_threat_attach_v1.py` → `3 passed`
- `PYTHONPATH=.:src python -m py_compile src/mage_ptcg/meta_specialist/cg_p1_active_threat_attach_v1.py scripts/run_cg_p1_active_threat_attach_screen_v1.py` → PASS
- paired CABT screen → `192/192 DONE`, fault 0、smoke `DONE`
- `git diff --check` と既存docs validatorは、status/evidence追記後に再実行する

