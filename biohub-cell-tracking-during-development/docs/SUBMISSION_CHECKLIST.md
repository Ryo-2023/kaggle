# Biohub Submission Checklist

> **用途:** Kaggle に提出する前の最終確認。  
> コンペ仕様は [`COMPETITION_GUIDE.md`](COMPETITION_GUIDE.md)、実験規約は [`EXPERIMENT_PLAYBOOK.md`](EXPERIMENT_PLAYBOOK.md) を参照する。

---

## 0. 重要

このチェックリストは **提出事故を防ぐためのゲート**。

モデルの local score が高くても、以下のどれかを満たさなければ提出候補にしない。

- hidden test で最後まで動く見込みがある
- submission schema が正しい
- graph が構造的に壊れていない
- offline execution が成立する
-使用した model / dependency / data がルール上許可されている
- local validation の provenance が残っている

Kaggle の Rules / Code Requirements は開催中に変更され得るため、**最終提出時は必ず公式ページを再確認する。**

---

# A. Competition / Rules

- [ ] Kaggle の **Overview** を再確認した
- [ ] Kaggle の **Evaluation** を再確認した
- [ ] Kaggle の **Rules** を再確認した
- [ ] Kaggle の **Code Requirements** を再確認した
- [ ] organizer の pinned Discussion / announcement を確認した
- [ ] official evaluator / baseline repository に更新がないか確認した
- [ ] 使用する external data / pretrained weights がルール上許可されている
- [ ] internet disabled 環境で必要ファイルを取得できる構成になっている
- [ ] submission deadline を UTC と日本時間の両方で確認した

---

# B. Candidate provenance

- [ ] candidate の experiment ID がある
- [ ] Git commit SHA を記録した
- [ ] branch を記録した
- [ ] config を保存した
- [ ] train / validation split を保存した
- [ ] seed を保存した
- [ ] checkpoint path / model artifact を保存した
- [ ] inference threshold / post-processing parameter を保存した
- [ ] evaluator revision を記録した
- [ ] 実行コマンドを保存した

提出後に「どのモデルだったか分からない」は不可。

---

# C. Local validation

- [ ] official-compatible evaluator を使用した
- [ ] final score を保存した
- [ ] adjusted edge Jaccard を保存した
- [ ] edge TP / FP / FN を保存した
- [ ] division Jaccard を保存した
- [ ] division TP / FP / FN を保存した
- [ ] predicted node count を保存した
- [ ] predicted edge count を保存した
- [ ] predicted fork count を保存した
- [ ] dataset 別の結果を確認した
- [ ] baseline との差を確認した
- [ ] 特定 sample だけの改善ではないことを確認した
- [ ] 小差なら seed / repeat 依存を確認した

---

# D. Sparse-GT sanity checks

- [ ] unannotated cell を一律 negative として扱う処理が入っていない
- [ ] sparse GT 前提を壊す post-processing を導入していない
- [ ] local metric が `GTに無いprediction = FP` と単純化されていない
- [ ] node-count adjustment を確認している
- [ ] prediction node 数が異常に膨張していない

---

# E. Coordinate / geometry checks

- [ ] image axis を `(T, Z, Y, X)` として扱っている
- [ ] `z, y, x` の順序を submission まで維持している
- [ ] voxel coordinate と physical coordinate を混同していない
- [ ] spatial scale を正しく扱っている
- [ ] crop / resize / padding 後の座標逆変換を確認した
- [ ] augmentation の座標変換を逆に戻せている
- [ ] coordinate が image bounds 内にある
- [ ] `t` が dataset の time range 内にある
- [ ] NaN / Inf coordinate がない

---

# F. Graph structural checks

各 dataset の prediction graph で確認する。

- [ ] node ID が一意
- [ ] edge source が存在する node を参照
- [ ] edge target が存在する node を参照
- [ ] self-loop がない
- [ ] 不正な backward-time edge がない
- [ ] 基本的に temporal direction が `t → t+1` の意味を保つ
- [ ] duplicated edge がない
- [ ] 意図しない merge / shared child が大量発生していない
- [ ] predicted fork が異常に大量発生していない
- [ ] empty graph の dataset が意図せず存在しない
- [ ] graph serialization / deserialization 後も node / edge 数が一致する

---

# G. Submission CSV schema

Header:

```csv
id,dataset,row_type,node_id,t,z,y,x,source_id,target_id
```

- [ ] `submission.csv` が存在する
- [ ] ファイル名が正しい
- [ ] 列名が正しい
- [ ] 列順が正しい
- [ ] `id` が存在する
- [ ] `dataset` が test dataset 名と一致する
- [ ] `row_type` は `node` または `edge`
- [ ] node row の `node_id,t,z,y,x` が有効
- [ ] node row の `source_id,target_id` が sentinel 値
- [ ] edge row の `source_id,target_id` が有効
- [ ] edge row の node-only fields が sentinel 値
- [ ] node coordinate の integer conversion が official conversion と整合する
- [ ] duplicate row が意図せずない
- [ ] CSV が空でない

---

# H. CSV round-trip

可能なら必ず行う。

```text
prediction graph / GEFF
      ↓
geffs_to_csv
      ↓
submission.csv
      ↓
csv_to_geffs
      ↓
reconstructed graph
```

- [ ] 元 graph と reconstructed graph の dataset 集合が一致する
- [ ] node 数が一致する
- [ ] edge 数が一致する
- [ ] node ID / edge reference が壊れていない
- [ ] coordinate rounding が想定どおり
- [ ] reconstructed graph を evaluator に通せる

**提出するのは元GEFFではなくCSVなので、最終評価対象に最も近いCSV round-tripで検証する。**

---

# I. Test dataset coverage

- [ ] test dataset の一覧を取得した
- [ ] 全 test dataset に prediction がある
- [ ] dataset 名の typo がない
- [ ] train dataset を誤って submission に混ぜていない
- [ ] output file が sample の一部だけになっていない
- [ ] dataset order に依存したバグがない

---

# J. Runtime / memory

- [ ] local または Kaggle 上で end-to-end runtime を測定した
- [ ] preprocessing を含めて測定した
- [ ] model load を含めて測定した
- [ ] Zarr I/O を含めて測定した
- [ ] graph generation を含めて測定した
- [ ] CSV generation を含めて測定した
- [ ] official runtime limit に十分な余裕がある
- [ ] peak RAM を確認した
- [ ] GPU 使用時は peak VRAM を確認した
- [ ] hidden test が public test より大きくても多少耐えられる余裕がある

目標は「制限ぎりぎり」ではなく、**I/O variation や hidden data 増加を吸収できる headroom を持つこと**。

---

# K. Offline Kaggle Notebook

- [ ] Notebook の internet を OFF にして通る
- [ ] pip / git / wget / curl で外部取得しない
- [ ] model weights が Kaggle input として利用可能
- [ ] custom wheel / source が必要なら offline input に含まれている
- [ ] hard-coded local path がない
- [ ] `/workspace` や Mac 固有 path を参照していない
- [ ] Kaggle input / working path の違いを吸収している
- [ ] credentials を必要としない
- [ ] secrets / tokens が Notebook に入っていない
- [ ] Notebook を **Restart / Run All 相当の clean state** で完走できる

---

# L. Failure handling

- [ ] dataset load failure を黙って skip しない
- [ ] model load failure を空 prediction に置換しない
- [ ] NaN / Inf を黙って0にしない
- [ ] graph generation failure を握りつぶさない
- [ ] exception時に壊れた `submission.csv` を success として残さない
- [ ] final assertion / validation がある

本番では **fail loudly > silent invalid submission**。

---

# M. Final notebook rehearsal

最終候補は、提出直前に最低1回は最初から最後まで通す。

```text
clean Kaggle session
      ↓
load all inputs
      ↓
preprocess
      ↓
inference
      ↓
graph construction
      ↓
submission.csv
      ↓
schema + coverage validation
      ↓
finish
```

- [ ] manual cell execution に依存しない
- [ ] cell order 依存がない
- [ ] stale variable に依存しない
- [ ] debug subset flag が残っていない
- [ ] `break` / `continue` / early return のdebug変更が残っていない
- [ ] test subset だけ処理する環境変数が残っていない
- [ ] verbose debug output がdiskを圧迫しない

---

# N. 提出直前の最終5項目

以下は全部 YES でない限り提出しない。

- [ ] **このCSVは全test datasetを含む**
- [ ] **このCSVをround-trip検証した**
- [ ] **このNotebookはclean/offlineで完走した**
- [ ] **このcandidateがどのcommit/config/checkpointか特定できる**
- [ ] **Kaggleの最新Rules / Code Requirementsを確認した**

---

## 提出記録テンプレート

```markdown
# Submission <name>

- submitted_at:
- git_commit:
- experiment_id:
- checkpoint:
- config:
- local_final_score:
- local_adjusted_edge_jaccard:
- local_division_jaccard:
- runtime:
- notebook_version:
- kaggle_submission_id:
- public_score:
- notes:
```

---

## 原則

> **提出は「submission.csv が作れた」時点では完了ではない。再現可能な候補を、cleanなKaggle環境で、全test dataに対して、offlineで、制限内に、構造的に正しいCSVとして生成できた時点で初めて提出可能。**
