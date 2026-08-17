# cg historical meta source epoch e / risk-aware CEM / fresh FINAL

## 結論

first-parent historical intakeで新しいsource identityを3件得て、訓練用sourceだけを先にsmokeし、未使用DEV/FINALを分離したままP1 policy CEMへ接続した。CEMは全152局を`DONE`・fault 0で完了したが、独立再評価のpositive gateを満たさずcenterはP1を保持した。gen0で最も有望だったcandidate-05を未使用META_FINALへ確認した結果も`−12.50pt`であり、BestKnown更新・deck phase・`cg_bestknown_loop_v1.py`接続は行わない。

これは同一Starmie deck上の履歴policyを使ったlocal-eval-only診断であり、public/native性能の証拠ではない。

## source intake

`runs/cg-historical-internal-meta-20260815-e/`へ3件をsealした。

| id | source commit | commit message | staged policy SHA |
|---|---|---|---|
| `internal_ozawa-starmie_6309a5f59f6d` | `6309a5f59f6d42412fe4e80d765e236996fd8bfb` | v1 ベンチ結果 | `1f426c0149f67d9e46d9c5bcb72ce16b6f4b2c5cb41b9a368fb511a6ac1fc4a4` |
| `internal_ozawa-starmie_66b0053163ff` | `66b0053163ff83c09680b75a6bd72d9fb844bb68` | Phase 4 実装 | `74fd800455359010367b74b3e2f8a45038542042ddfc3cec16eaa2cb6ff56dcf` |
| `internal_ozawa-starmie_78d8b10eabe9` | `78d8b10eabe93cdcb8071b9ee9fdf2ab655c067d` | v2 deck修正 | `dedef779ebd3346e62b1755c7bda4526b3041c1d56e21fe9715245780e473483` |

3件は同一canonical deck SHA `c69a18eccd20b925ae9e26818fb86f0eee3404bee94cffbdf52a08b6e3b10ce4`で、static findingsは0、`STARMIE_DEBUG`以外の動的書込みは検出されず、usage boundaryは`local_eval_only`である。pool/fresh batch SHAはそれぞれ `16bf897907e9c116c831ab479639b90ad91cc9de9f8c0a6cf71a192830192776` / `2372f2c714df4d6a701444cd95604abf61d7796ddcf8c9f6af1724e7775c9a3c`。

splitは `META_TRAIN=6309a5f59f6d`、`META_DEV=66b0053163ff`、`META_FINAL=78d8b10eabe9` とし、split SHAは `baa2317f2c595fe187d1686ade77e305b6badd05321e8e8b73d5a3739d45f57d`。訓練sourceだけを `run_historical_meta_smoke_v1.py --reference-id` で4局（両seat・各2反復）smokeし、4/4 DONE・fault0を確認した。DEV/FINALはこの時点で未使用だった。

## CEM

P1 `cg-lethal-target-v1`＋root deckをcandidate/controlのparentに固定し、population 8、elite 2、2世代、`--all-train-refs --reeval-for-update --reeval-repeats 2 --positive-delta-gate --risk-aware-update`、初期scale 5%、campaign seed `20260876`で実行した。gen0/1のscreen各36局、独立再評価各24局、gen1のDEV各16局、合計152局は全てDONE・fault0である。

- gen0 candidate-05はscreen `+50.00pt`、独立2 blockは各`+25.00pt`で両seat-safeだった。
- ただしpositive gateは独立positive候補がelite数2件に満たない場合centerを保持するため、gen0 centerはP1のまま。
- gen1も独立再評価でworst-case positiveかつseat-safeな2候補を得られず、centerはP1のまま。
- fresh `META_DEV`（center、16局/arm）はcandidate `6W-0D-10L`、control `6W-0D-10L`、差`0pt`、fault0で、正差なし。

CEM manifest SHAは `bff8ba0ce00269b74c5bb9e7212f26bab5b5bd8f474db8c8558f317f1e5c7da1`、generation results SHAは `bc3c716e19fe0471bcbc52b5819b188fd9f2df2170f2d507d1e3207366fbe08d` / `4797746c8199def9bb0281dfc4fc1f91b86d3219f81bfb674f433c28c1cbdac4`。

## 未使用FINAL確認

gen0 candidate-05を選抜後の診断対象として、未使用 `META_FINAL` 1件へ8 games/opponent/seat（各arm 16局）を実行した。candidate `2W-0D-14L`（12.50%）、control `4W-0D-12L`（25.00%）、差`−12.50pt`、candidate seat gap 0%、fault0、判定`NOT_PROMOTABLE`。summary SHAは `b4902fc2c621cd9151314357de84896161f1a0955651467e49d75d280de8aca0`。

## 状態と次の再開条件

現BestKnown／Champion／productionはP1 `cg-lethal-target-v1`＋root deckのまま。P2/P3昇格、deck mutation、`cg_bestknown_loop_v1.py`のpolicy→deck→policy実行、commit、push、Kaggle提出は行っていない。今回のe epochは、sourceを安全に生成し、訓練sourceとfresh holdoutを分離してCEMへ渡せることを確認したが、性能更新には失敗した。

次は同一Starmie履歴のblind retryをせず、異なるbehavior familyを持つpermission済みsourceまたは明示的な新generatorを別epochとして作り、`P1 → risk-aware CEM → fresh DEV → fresh FINAL →（positive時のみ）deck → policy`へ進める。

