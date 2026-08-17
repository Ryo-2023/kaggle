---
project: MAGE-PTCG
document_status: evidence
canonical_source: git
language: ja
title: 2026-08-13 self-owned Rule v0 play-minus 384 confirmation
---

# self-owned Rule v0 play-minus 384 confirmation

## 結論

96局serialで一時的に+5.729ptだったKnowledgePack `play-minus` を、同一
common24/base14900000、両seat、repetition 8（384局）へ延長した。baseline controlは
43W/341L、candidateは41W/343Lで、candidateは -0.5208pt。faultは両arm 0、seat差も
baseline 22/21、candidate 21/20で崩壊なしだが、native controlを上回らず、+3pt gateを
満たさない。`play-minus` は `NOT_PROMOTABLE` とし、384→768/1536およびlongrunへ
進めない。

## 実行条件

- run root: `runs/final-sprint-autonomous/rule-v0-knowledge-pool-screen-v1-play-minus-384-14900000-serial-v1/`
- command: `/tmp/run_play_minus_384_serial.py`（同一common24、base14900000、games_per_seat=8）
- evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`
- workers: `1`
- worker recycle: `32`
- requested games: 384/arm, baseline + play-minus = 768
- root policy closure SHA: `750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b`
- deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- broad config SHA: `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`

## 結果

| arm | wins | draws | losses | score rate | faults | seat0 / seat1 wins |
|---|---:|---:|---:|---:|---:|---:|
| baseline-no-pack | 43 | 0 | 341 | 11.198% | 0 | 22 / 21 |
| play-minus | 41 | 0 | 343 | 10.677% | 0 | 21 / 20 |

同一セルのpaired outcome matrix（384 cells）:

- loss→loss: 311
- loss→win: 30
- win→loss: 32
- win→win: 11

従って candidate netは -2 wins、score delta `-0.5208pt`。opponent supportは
candidate wins > baseline winsが6/24、regressionが8/24、equalが10/24だった。
主な差分は `official_random` 8→15の改善に対して、`harukiharada_crustle` 4→1、
`naoto714_kangaskhan` 7→3が悪化しており、単一familyへの過適応を示唆する。

## Artifact SHA

- overall summary: `d79aba2e9b8237813cdc5a4306da83e519fe42533f077a6ddaa1398e870ea05d`
- baseline manifest: `92906410bf6dabfe53c92f62b9d4a2847d1876a39682cba97ba87b579d1091b6`
- baseline evaluator manifest: `96303acbacd6444b0bcc02fd11d6be83af90168d1cde8bbdbf9c1c3606468d32`
- baseline evaluator summary: `2366c40f4866fe8ca347a139390db72fb9c7da3e7752f577d93833d86d3fb1b9`
- play-minus manifest: `538f8e7e3a7fb3b8c541a0c031e7d83da94ca42ab9fd30c69623c06fa681c02f`
- play-minus evaluator manifest: `63335ec1ffc574c498d0fbef18d25e0385686bcb3d46d165e175229e277464b0`
- play-minus evaluator summary: `763ed138a8353a52540f7ff6a969080e29dfe0cfb0e39039a280824f74888403`

## 判定

`NOT_PROMOTABLE / NO-GO`。96局の上振れは384局で再現せず、hard-label/同型tie-break
の追加sweepは停止する。native assetはteacher label/behavior sourceとして使わず、
production Rule v0、Champion、training、submission、longrunは変更・起動していない。
