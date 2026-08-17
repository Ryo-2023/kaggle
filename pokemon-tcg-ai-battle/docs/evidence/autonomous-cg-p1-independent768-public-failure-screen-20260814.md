# cg P1 independent 768 / public failure screen — 2026-08-14

## 結論

P1 `cg-lethal-target-v1` と P0 `root-cg-self-owned-v1` を root deck 固定・META_TRAIN top24・同一 opponent/seat/repetition/seed strata で独立 768 局ずつ評価した。P1 は 151/768、P0 は 138/768 で、差は +1.6276pt。全 1,536 局が `DONE`、fault 0、P1 の seat gap は 2.8646pt だった。これは P1 parent の再現性確認であり、Champion や SubmissionEligible の昇格ではない。

既存 P1 公開 telemetry 4,077 decision 行を terminal WDL と結合すると、state bucket は 3,298、support 6 以上で競合する bucket は 1、mixed-sign bucket も 1 に留まった。因果的な action label として扱える十分な signal がないため、strict analyzer は `ready_for_candidate_screen=false` とした。

P0 telemetry は同一 96 strata、同一 base seed 40400000、workers 12/recycle16 で追加収集した。P1 は 4,077 decision 行、P0 は 3,584 decision 行、両方 fault 0。P0 の最初の stdin spawn 試行は multiprocessing の `<stdin>` 起動制約で partial root になったが、v2 の実ファイル wrapperで再実行し、partial rootは性能値へ算入していない。

独立 768 の負け寄り public active-id cluster から、lethal bonus を対象 family だけ抑制する P2 を最大3件、P1 control と workers12/recycle16 の weighted48 で評価した。

| candidate | candidate | P1 control | delta | 判定 |
|---|---:|---:|---:|---|
| `cg-p1-public-suppress-dragapult-lethal-v1` | 15W-1D-80L | 24W-0D-72L | −8.8542pt | STOP |
| `cg-p1-public-suppress-grimmsnarl-lethal-v1` | 17W-0D-79L | 17W-0D-79L | 0.0000pt | STOP |
| `cg-p1-public-suppress-lucario-lethal-v1` | 17W-0D-79L | 17W-0D-79L | 0.0000pt | STOP |

3 screen は計 576 局、全 `DONE`/fault0、両 seat、paired strata gate PASS。positive candidate がないため common24/384/768、alternating promotion、training、teacher、longrun は起動していない。P1 parent、root deck、Champion、submission packageは不変である。

## Identity / artifacts

- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- P0 policy SHA: `617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- population schedule SHA: `d9b59a3ed3cb07f3845a5b32999ec86898d7fdec07b2e7bbb6a728948e25c7c3`
- independent 768 summary: `runs/final-sprint-autonomous/cg-p1-independent-768-20260814-v1/summary.json` SHA `cd0bcda15839bb89fa3df6a7f060a1cd30bca7c397fd49ab51cf587df947d9ed`
- independent 768 manifest: SHA `03d372f2affbad6f220c6f79b4547658caf398f2759ff35872a411728adc7569`
- P1 telemetry summary SHA `fabdd3fcc49432bf058f33bb2673904c7c194aebe480163558900a5171fc2f1f`
- P0 telemetry v2 summary SHA `62487bef091167a6d2782263b86cfbd0cb0e076626514e88b54dae7aa54abd42`
- hypothesis module SHA `654222bca0e549d86fce546189945c43d54ba53c8ffd5919fe134025838f6d1d`
- candidate module SHA `846716f294235867266face2d11d6782f10899ea41f73a838a3d16c1c160b560`
- screen runner SHA `0149fe13327b164da4a4346b4aad5051b35f6573a864066c13d1d123c204e255`

## Verification

```text
pytest tests/meta_specialist/test_cg_p1_public_hypothesis_v1.py tests/meta_specialist/test_cg_p1_public_failure_candidates_v1.py
6 passed
py_compile: PASS
all evaluation rows: DONE, faults=0
authority.training/promotion/submission/longrun/teacher: false
```

これは research-only evidence であり、native local_eval_only behavior、teacher label、training data、submission permissionを使用していない。
