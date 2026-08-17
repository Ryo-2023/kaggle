---
project: MAGE-PTCG
evidence_type: submission-hardening
as_of: 2026-07-15
---

# P0 Continuous Submission Baseline Hardening v0

## 結論

Rule Agent v0をsubmission defaultに固定し、必要runtimeだけを含むstandalone artifactと、最終提出用の決定的`submission.tar.gz`を生成・検証できるようにした。v0のscore、優先順位、情報境界は変更していない。

## 目的と判断

- 深度はR2。提出surface、完全性検証、隔離importをまたぐが、戦略・評価・提出APIの設計変更は含めない。
- 成功条件は、Rule v0だけをartifact tarballからimportし、deck登録と代表selectionをrepository外で決定的に実行でき、manifest／archive改竄を検出することである。
- 反証条件は、artifactがrepositoryの`agents/`や`deck.csv`に依存すること、manifestとruntime hashが不一致でも通ること、またはdefaultがv0以外であることとした。
- 代替案の「`main.py`単体へv0 policyを複製」は、score規則の二重管理になるため採用しなかった。artifactには既存の`agents/rule_agent.py`をそのまま含める。
- tarballへmanifestを含める案は、manifestがtarball自身のSHA-256を記録する自己参照になるため採用しなかった。manifestは同一artifact directoryのsidecarとし、tarballにはruntimeだけを含める。

## Base、Champion、調査結果

- 初回hardening base commit: `2f64fa13ac29a096c4802668ae467ccb7211b1bb`
- archive拡張開始HEAD: `48d2065ce6887c75dfb22c2a6300cfef349bddea`
- Champion: Rule Agent v0。Rule v1はChallengerのままとした。
- 既に成立: v0はstatelessかつdeterministic、deckは60枚、`choose_rule_indices`はmandatory／optionalと範囲・重複を処理し、hidden payloadを読まない。
- 実ブロッカー: `main.agent`のdefaultがrandom agent、submission artifact生成入口、manifest／tamper検出、clean-room検証が未実装だった。
- 今回不要: Rule v1、Knowledge Pack、探索、C2a／C2b／C3、requirements／lockfile、deck内容、v0 scoreの変更。

## Selection／option type監査

既存の[Rule Agent v0判断メモ](rule-agent-v0.md)の実cabt観測では、selection typeは`0`、`1`、`8`、`9`、contextは`0`、`1`、`2`、`4`、`7`、`22`、`38`、`41`であった。main selectionのoption typeは`7`（PLAY）、`8`（ATTACH）、`9`（EVOLVE）、`12`（意味未確定）、`13`（ATTACK）、`14`（END）、補助selectionでは`0`、`1`、`2`、`3`を確認済みである。

type 12は既存の実cabt観測に存在するが、現在のworktreeには配布cabt data、runtime、type定義、replay fixtureがないため、ゲーム意味は追加で確定できなかった。v0は既にtype 12へ名前・scoreを与えず、未知値としてstable fallbackする。今回、mandatoryのtype 12で`minCount`個の重複なし範囲内index、optionalのtype 12で空選択、unknown typeで同じfallbackをartifact側testで固定した。意味を推測して戦略変更はしていない。

## Artifact構成、archive tree、再現性

生成入口は次である。

```bash
python scripts/build_submission.py --output-dir artifacts/submission/rule-v0
```

最終提出物は`artifacts/submission/rule-v0/submission.tar.gz`である。tarballのrootには次だけを置く。

```text
main.py
deck.csv
agents/__init__.py
agents/rule_agent.py
```

artifact directoryには上記runtimeの検証用コピー、`submission.tar.gz`、`manifest.json`を置く。manifestにはsource HEAD、agent identity（`rule-agent-v0`）、deck hash、順序固定のfile listと各file hash、content hash、builder/schema version、最終tar.gz SHA-256を記録する。

tarballは展開済みarchiveを再梱包せず、runtime fileから直接生成する。member orderとpathは`RUNTIME_PATHS`で固定し、modeを`0644`、uid/gidを`0`、uname/gnameを空、tar mtimeとgzip header timestampを`0`へ正規化する。source commitとdirectory manifestのbuild timestampはprovenance専用であり、tarball bytesとcontent hashへ含めない。

既存artifactの検証は次で再実行できる。

```bash
python scripts/build_submission.py --verify-dir artifacts/submission/rule-v0
```

## 検証

- focused: `python -m pytest -q tests/test_rule_agent.py tests/test_submission_artifact.py` は26 passed、2 skipped。2回buildのtarball bytes／SHA-256一致、archive hash改竄検出、path traversal、duplicate member、symlink、secret scan、tarball clean-roomを含む。
- repository全体: `python -m pytest -q` は373 passed、7 skipped（31.88秒）。`python scripts/docs/validate_docs.py`は12 canonical documentsを検証した。
- `git diff --check`はpass。今回のGit差分はsecret markerの検索で、test用の`KAGGLE_KEY=not-a-real-token`以外の検出0件だった。
- build済みruntime content hash: `6a8a7184804ba23889e64908d0c51df73d638256bd4543c53c9225de1ac2fc4b`。
- final tar.gz SHA-256: `c73585680bf79891f002308555d35dae919218d41f43211a16220a4ce084b589`。同一HEAD・同一runtimeからの2回buildでtarball bytesとこのhashが一致した。
- archive validation: absolute path、`..` traversal、duplicate member、非regular member（symlink/hardlink/directory）、非canonical owner/mode/mtime、想定外／不足fileを拒否する。
- clean-room: tarballだけを一時directoryへ安全に展開し、artifact外cwdから`python -I`でimportする。deck登録は60枚、unknown mandatory selectionは`[0, 1]`、optional unknown selectionは`[]`で、同一入力の出力は一致する。repository rootの`PYTHONPATH`混入とruntime source内のabsolute repository pathも検査する。
- cabt smoke: `SKIPPED_EXTERNAL_DEPENDENCY`。このworktreeには`kaggle_environments`／cabt pluginと配布dataがない。dummy simulatorで代用して実cabt成功とは扱わない。依存を用意した環境では`python -m pytest -q tests/test_rule_agent.py -k official_cabt`を実行する。
- secret scan: archiveとartifact全fileを検査する。検出対象はKaggle key／username assignment、Authorization、Cookie、email、`/home/<user>`であり、正常artifactは検出0件、test markerは検出する。

## Kaggle提出手順と制限

この作業ではKaggleへ送信しない。ローカル正典[competition.md](../competition.md)は、submission形式を`Submit`タブで確認するよう明示しており、現在の公式ページはJavaScript描画のため本作業環境から形式を再確認できなかった。directory artifactをzipへ推測変換しない。

人間が`Submit`タブで受理形式を確認した後、正典[kaggle_guide.md](../kaggle_guide.md)にあるCLI形を、実際の受理ファイルとメッセージへ具体化して使用する。

```bash
kaggle competitions submit pokemon-tcg-ai-battle -f <submission_file> -m "<message>"
```

提出後は、artifactの`content_hash`、submission ID、日時、受理結果を`experiments/`へ記録する。受理形式がdirectory以外なら、その形式を公式sampleと照合してから別途packagingを追加する。

## 残リスクと再開条件

- type 12のゲーム意味は未確定。配布cabt runtimeまたは公式fixtureを取得したら、意味を推測せず構造・合法性を確認する。
- actual cabt smokeとKaggle受理は未実施。`kaggle_environments`のcabt pluginとcompetition dataを使う環境、および人間のKaggle Submit画面確認が再開条件である。
- Kaggle Submit画面がtar.gzを受理するかは未確認である。実送信せず、受理形式を確認してから提出する。

## 独立再レビューのNon-blocking notes

判定は`APPROVED_WITH_NONBLOCKING_NOTES`であり、blocking findingはない。次の3点は既知の運用上の制約として記録し、P0の完了条件やartifact検証のpass/failを変更しない。

- N1: build sourceはGit objectではなく、build時点のworking treeのruntime filesである。manifestの`source_revision`はprovenanceであり、content hash／tarball bytesの入力ではない。
- N2: builderはdirty worktreeを現時点で拒否せず、manifestの`source_revision.dirty`へ記録する。release運用ではclean HEADからbuildする。
- N3: secret scanのemail regexは広く、通常のメール形式も検出する。正常artifactでの誤検知を避けるため、検出時は内容を確認して扱う。

## 正典ブランチ統合検証

`feature/submission-hardening-v0`（`b85f89e27d400b05d8e9b25d0d11da278e1d24f8`）を`feature/belief-guided-search`へ通常のno-ff mergeで統合した。C2a/C2b側のruntime進捗を保持したため、P0 HEADと比べて`main.py`、`agents/__init__.py`、`agents/rule_agent.py`は変化し、`deck.csv`は同一である。

- merge後のstandalone依存違反を検出し、Knowledge Packが未指定のRule v0ではknowledge moduleをimportしないよう遅延化した。packを指定するC2a経路は維持する。
- clean HEAD（`9752d9678c32d6267d571be225f264a5944e2d69`）から2回buildし、tarball bytesは一致した。content hashは`61243e8e3d210fae16907f95beb0594641669133278099e8f490e8011e58452b`、tar.gz SHA-256は`2fea32c175598fe071fcb8df6a7f25f9cab1ac196318f3c4f3c073b4c1b25cdd`である。
- builder verifyと、`/tmp`へtarballだけを展開した`python3 -I` clean-roomで、60枚deck、mandatory unknown selection `[0, 1]`、optional selection `[]`を確認した。
- focused submission/Rule/Knowledge testsは60 pass・2 skip、repository testsは445 pass・7 skip。docs validation、`git diff --check`、conflict marker scanはpassし、artifact secret scanは0件だった。
