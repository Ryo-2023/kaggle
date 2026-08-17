# 自己所有 Tomato デッキ Rule v0 → Student 実ループ（2026-08-14）

## 結論

Tomato native deck (`opponents/tomatomato_archaludon/deck.csv`) を subject に固定し、提出互換の Rule v0 の自己軌跡を収集して outcome-weighted Student v0 を学習した。96局 screen では weighted Student が Rule v0 を +2勝（+2.0833pt）上回ったが、別seedの384局確認では +2勝（+0.5208pt）に縮小した。fault は全て0だったため実装・評価経路は成立したが、改善幅は昇格根拠として不十分であり、Student/Hybrid は candidate-only、384追加・768・longrun・promotion・submission は停止する。

## 実験境界

- 研究専用。`research_only=true`、training/behavior/promotion/submission authority は全て `false`。
- subject deck は Tomato native deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`。
- opponent pool は `opponents/pool_manifest.json` SHA `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`。
- broad config は `configs/meta_specialist/performance_first_broad_pool_v1.json` SHA `832273ff656280d2556c9df09a9a3db9f2564a181be78a3e658509d3b396209b`。
- 全性能評価は24 opponent × 両seat × repetitionで、通常のscreenは96局、確認は384局。
- 独立armはユーザー指示どおり `workers=12` を使用した。96局screenは `worker_recycle_games=16`、384確認は12 workerの一斉recycle停滞を避けるため fresh retryで `worker_recycle_games=64` とした。
- native opponent の行動、teacher label、private stateはStudent学習へ流用していない。収集したのはsubject側のactor-visible RuleBCExampleとterminal WDLだけである。

## Rule v0 自己軌跡の収集

収集root:

`runs/final-sprint-autonomous/self-owned-tomato-rule-bc-v1-20260814/`

実行条件:

```text
scripts/collect_self_owned_rule_bc_v1.py
--subject-deck opponents/tomatomato_archaludon/deck.csv
--games-per-seat 2 --base-seed 20269000 --workers 12 --max-steps 2000
```

結果は96/96 DONE、fault=0、draw=0、3377 decision examples。collection manifestは `READY_FOR_WEIGHTED_TRAINING`、authority全false。

- manifest SHA: `e69364c922d2f6dad8add3d3a148068a4be0ca5dc620a3cad1f915f413399dad`
- JSONL dataset SHA: `81e5cb97815263092a6e1be10126fac039a9f7c07486db0c41ac8941b861f29a`
- source collector SHA: `40cad3071571e2992fec25f1ffcd2838d8db5acdfcb2df7f6fee5e82377b9f31`

## Student 学習

現行 Student v0 は ordered Skill selection `(selection_type=5, context=34)` を表現しないため、最初の3学習は意図的にfail-closedした。学習器へ無断で順序ラベルをunordered化せず、明示的な `--exclude-unsupported` を追加し、7 ordered examples を除外した。未知のselection schemaは引き続き拒否する。学習対象は3370例、train=2898、validation=472。

学習コマンドは以下の3 variantを並列実行した。

```text
scripts/train_outcome_weighted_student_v1.py
--collection-root runs/final-sprint-autonomous/self-owned-tomato-rule-bc-v1-20260814
--epochs 80 --learning-rate 0.08 --exclude-unsupported
```

| variant | win/draw/loss weight | model SHA | validation top-1 |
|---|---:|---|---:|
| weighted | 1.5 / 1.0 / 0.5 | `8d9686df9900bb09b862b8ec5fda8a04d3c7e6d61de921588bde506715b8a282` | 0.7775 |
| plain | 1.0 / 1.0 / 1.0 | `d390b2058933f53e1163f3506968a4d933a3766bb6df055789d1f2a35a2aa402` | 0.7775 |
| heavy | 3.0 / 1.0 / 0.1 | `350b314fbef178079cdcc98aecfcae05d0e5e027341e6fb7b174cda8f621ef91` | 0.7818 |

weighted training manifest SHAは `54d87940ea3cd3a32c7057f04c7a50026eb1c467641fecd9613e446f05806571`。全model artifactは `READY_FOR_LOCAL_EVALUATION` だが、promotion authorityは持たない。

実装変更:

- `scripts/train_outcome_weighted_student_v1.py` SHA `0b1251e8594ef2e26e59f333b49a8266634bb3ccf49f6aecf632c419eabde4fc`
- `tests/test_train_outcome_weighted_student_v1.py` SHA `267d6df6e7682ab30f13c3bdd1ee7fe60f20670a7cb3573b606eb5a89b9b0d04`
- ordered Skillを除外する partition回帰テストを追加。focused testは2 passed。

## 同一seed 96局 screen

同じTomato deck、same broad24、base seed `20269000`、各96局。これは同一 evaluator 入口内の比較である。Rule v0基準も同じ入口から再計測した。

| policy | W-D-L | score | seat0 / seat1 | 判定 |
|---|---:|---:|---:|---|
| Rule v0 | 13-0-83 | 13.5417% | 6 / 7 | control |
| Student weighted | 15-0-81 | 15.6250% | 7 / 8 | +2勝、+2.0833pt |
| Student plain | 12-0-84 | 12.5000% | 4 / 8 | negative |
| Student heavy | 13-0-83 | 13.5417% | 9 / 4 | score同等、seat imbalance |
| Hybrid weighted | 12-0-84 | 12.5000% | 8 / 4 | negative |

全arm 96/96 DONE、fault=0、draw=0。screen summary SHA:

- weighted `7eb6283d60442a593511808fdcfcd6c0681f35230f2f7dcba6d0b0f35886af6c`
- plain `80dfc763edd36ec955cc4784411769b6f87afee21155eb133fb8819dfc5436f2`
- heavy `c4a8893cac2f4d5d48f86f063be0e21e11b593f6537b76000cdcf804e4359e18`
- hybrid `ec8aebc42739cb6bb6378e806eb2acf77200768b52289f7dc224034213196cb2`
- Rule v0 control `385eaddc083ea51d80c23958a3ed0fab057638ba945860f53c1950d96adfa5e3`

## 384局 confirmation

96局で唯一positiveだった weighted Studentだけを、未使用のbase seed `20270000`、同一384セルでRule v0と並列評価した。初回 `worker_recycle_games=16` は各arm192局まで進んだ後、12 worker一斉recycle後のspawn停滞が発生したため、部分rootは不採用・保全し、同じ条件を上書きせず `worker_recycle_games=64` のfresh retryを実施した。retryは384/384 DONE、fault=0、draw=0で完了した。

| policy | W-D-L | score | seat0 / seat1 |
|---|---:|---:|---:|
| Rule v0 control | 50-0-334 | 13.0208% | 30 / 20 |
| Student weighted | 52-0-332 | 13.5417% | 29 / 23 |

同一384 scheduleの差は +2勝、+0.5208pt。96局の+2.0833ptを再現しなかったため、768やlongrunへは進めない。

- Student retry summary SHA: `0d8cc46a574de2e3cbb1885d238c7548011ffcac67dcff26cfd4b0a19497cc7d`
- Rule retry summary SHA: `b636bd94ca8425e76ce684c7b023359c01d1ec2b6bcdada227c464e3b357b81e`
- Student retry manifest SHA: `02bcc14b701fdfd53f8339c2838dffdbee4767d2825361792465fc3f14f7f10a`
- Rule retry manifest SHA: `4dd80963b1366a9f69a39512baba9566a0f7d5e8b8e412eeb60e14562c45c20a`

## Student evaluator / deck screen 接続修正

Tomato deckをRule v0 KnowledgePool screenへ渡せるよう、`run_screen(subject_deck=...)` とCLI `--subject-deck` をTDDで追加した。従来はscreen関数がROOT_DECKに固定され、明示subject deckが無視されていた。focused `tests/test_rule_v0_knowledge_pool_screen_v1.py` は9 passed。

- screen script SHA: `6b2e00c64ebbf6b3fe5eeb18dc5539ca4052c664f7e089f0a8d1fa16938a0ee0`
- screen test SHA: `534cd0a81b130a426ba494bc564249e16e4bc54bef02797ea4fa4cf995f6a93c`

## 最終判断

1. Tomato deckはRule v0 subjectとしてroot deckより96局の局所値が高いが、policy Student化の改善は384局で+0.52ptに留まる。
2. weighted Studentは研究用candidateとして保存するが、Rule v0 Champion、既定agent、deck、提出packageは変更しない。
3. このStudent経路は現時点で promotion/longrun/submission に進めない。次の性能投資は、別のsubmission-compatible deck/policy surfaceまたは新しい公開target overlayへ移る。
4. ordered Skillの7例は未学習であり、Skill順序を推測補完していない。将来pointer-headを実装する場合のみ別責務として再評価する。

## 検証

- `PYTHONPATH=.:src TMPDIR=/tmp .venv/bin/pytest -q -s tests/test_train_outcome_weighted_student_v1.py`: 2 passed
- `PYTHONPATH=.:src TMPDIR=/tmp .venv/bin/pytest -q -s tests/test_rule_v0_knowledge_pool_screen_v1.py`: 9 passed
- 96/384 performance roots: all selected roots DONE/fault0/draw0
- py_compile: PASS（変更script/tests）
- `python scripts/docs/validate_docs.py`: `Validated 13 canonical documents.`
- `git diff --check`: PASS
- Kaggle submission、commit、push、Champion変更: 未実施

## 384局データでの再学習拡張

96局収集だけでは学習信号が薄いため、同じTomato deck・Rule v0・broad24で追加384局をworkers=12で収集した。base seedは `20271000`、全384/384 DONE、fault=0、draw=0、examples=14542。ordered Skill 7例を除いた14535例を3 variantへ入力した。

- collection manifest SHA: `e3d3d077082e1071a5866104ffdd9a95bf26963470d0455b0f698b65b779662a`
- dataset SHA: `5b3dada0d65ac47a1880dad47a096cd23582a28edee45b9eaef332e24c5fc745`
- weighted model SHA: `abf37436c82977eb38eb70549e42e9761b62ba56e693e9750d4384653927d155`
- plain model SHA: `4686f256d01fe2e4ed1c49a721d3750f96f8381a6ed91adf71ee41f008890561`
- heavy model SHA: `9d914648bab08ff2ce1c15746fe4676da79de81a7e78987f6b04337d1dd5ac73`

未使用のbase seed `20272000`で、3 StudentとRule v0を各384局、workers=12 / worker_recycle_games=64で並列評価した。

| policy | W-D-L | score | seat0 / seat1 |
|---|---:|---:|---:|
| Rule v0 | 50-0-334 | 13.0208% | 26 / 24 |
| weighted (1.5/1/0.5) | 49-0-335 | 12.7604% | 27 / 22 |
| plain (1/1/1) | 50-0-334 | 13.0208% | 26 / 24 |
| heavy (3/1/0.1) | 54-0-330 | 14.0625% | 30 / 24 |

heavyだけが+4勝（+1.0417pt）だが、既存の+3pt昇格基準には届かず、seed差に対して小さい。weightedはむしろ−1勝、plainは同率。よって384-data Studentも768/longrun/promotion/submissionへは進めない。

再学習の実行は3 variantを並列化した。評価も4 armをworkers=12で並列化し、全選択rootでDONE=384/fault=0/draw=0を確認した。前段の384確認でrecycle16が一斉再生成境界に停滞したため、今回はrecycle64をmanifestへ固定した。

384-data evaluation summary SHA:

- weighted `8c1940d749ce23fb3a476f07fb3dfd1600c1824ec6a42d7c27baa0caa245d587`
- plain `c51b51a5a9c2c6375988b0838cf618bbacc40f3ff1e89e3fa47f598a89abfa73`
- heavy `a37a0779c825cb99b0960d3bcce502caeb622742c6f8a86b35da8db85e30c328`
- Rule v0 `c51b51a5a9c2c6375988b0838cf618bbacc40f3ff1e89e3fa47f598a89abfa73`

この拡張で、Tomato deck固定のRule v0→Student loopは「学習・提出互換評価・workers12並列実行」まで接続済みだが、性能改善は再現性のある昇格水準に達していない、という判断を確定する。

## 最終更新

384-data再学習を含む最終判断は、heavy Studentの一時的な+1.0417ptを保存するが、weighted/plainで再現しないため `candidate-only / NO-GO` とする。次の探索はこのStudent weight sweepを繰り返さず、未評価のdeck surfaceまたは別の公開target surfaceへ移す。
