---
project: MAGE-PTCG
evidence_type: student-v0
as_of: 2026-07-15
---

# C4 Student v0

## 結論

C4の最小offline基盤を実装した。Rule Agent v0を教師とする可変長合法手のBehavior Cloning dataset、候補別score model、JSON export、Rule v0 fallback、評価CLI、Student専用clean-room artifactを追加した。実cabt trace／runtimeはこのworktreeにないため、Studentをsubmission defaultへ昇格しない。判定は **NO-GO（runtime promotion）／GO（offline distillation foundation）** とする。

## 既存契約と設計

- cabtの`select.option`だけを候補集合とし、既存の`build_decision_state`とStable `ActionKey`を候補identityへ用いる。候補配列の固定位置をfeatureにしない。
- stateはActorInformationView由来のpublic state、actor自身のprivate hand、最大64個のpublic history digestだけである。相手hand／prize内容、`logs`、`search_begin_input`、将来engine stateはdatasetへ保存しない。
- dataset schemaは`rule-bc-v1`で、schema version、public/own-private/history、selection context/bounds、candidateごとのActionKey payload/digest、Rule v0 target、取得可能時のscore ranking、deck fingerprint、source revision、redaction済みsource metadataを保存する。
- source/episode group hashでtrain/validationを分離する。source IDはSHA-256へredactし、同一traceを分割しない。near duplicateの完全分離は実traceが得られた時の追加Gateであり、fixtureだけでは未検証である。
- runtimeはschema mismatch、モデル未配置・load失敗、unknown/malformed observation、NaN、空score、非合法候補をすべて`None`として返し、factoryが決定的なRule Agent v0へfallbackする。

## Modelとruntime

Student v0はvalue headなしの候補別linear scorerである。state hash feature 32次元とActionKey feature 64次元を連結し、各legal candidateをscoreしてlegal set内softmaxでcross-entropyを最小化する。parameter数はweight 96＋bias 1の計97である。学習は依存追加なしのdeterministic full-batch Python float計算、runtimeは標準ライブラリJSONのみである（NumPy、PyTorch、Knowledge Packを必要としない）。

同点は`(-score, ActionKey.digest, cabt option index)`で決める。indexは最後の合法候補tie-breakだけであり、model featureには候補配列位置を入れない。既存ActionKeyが意味的に同一digestを与える未解決option shapeだけは、最終index tie-breakでcabt出力へ戻す。

runtimeは永続化しない`last_decision_trace`へ、既存のpublic-only trace projection、選択ActionKeyの**public digest**、またはfallback理由の分類だけを残す。public digestは`ActionKey.to_public_trace_payload()`（`card_id`を`None`へredact）を`sort_keys=True`・compact JSONへ正規化してSHA-256した値であり、private ActionKey core digestを記録しない。N1として、card ID候補を列挙してprivate core digestを再計算する辞書攻撃が選択traceからcard identityを識別できないことをfocused testで固定した。own private handやraw observationはtraceへ残さない。

`main.make_student_agent(..., model_path=...)`が任意のStudent factoryである。`main.agent`と既存`build_submission.py`はChampion Rule v0のままである。Student artifactは次の明示的な別入口で作る。

```bash
python scripts/build_student_submission.py --model /path/student-v0.json --output-dir /path/artifact
```

artifactにはmodel、Student runtime、既存ActionKey/Rule v0だけを含める。Knowledge Pack、training scripts、NumPy、C3コードは含めない。

## 実測（fixture contract only）

実cabtの観測traceは未配置のため、full actor observationを模した12件のRule v0 fixtureでschema、学習、exportを確認した。12 source groupのsplitはtrain 10、validation 2である。同一種類のfixtureを複製した小標本であり、汎化・対戦性能を示さない。

| 指標 | 結果 | 条件 |
|---|---:|---|
| holdout loss | 0.02779 | validation 2 fixture |
| teacher top-1 / top-3 | 1.0 / 1.0 | validation 2 fixture |
| legal action rate | 1.0 | validation 2 fixture |
| selection type 0 fidelity | 1.0 | validation 2 fixture |
| fallback rate | 0.0 | valid exported modelのoffline evaluation |
| p50 / p95 latency | 68.27 / 69.08 µs | 20 repeats、local CPU、fixture |
| model size | 1,086 bytes | 97 parameter JSON |
| Student tar.gz size | 20,744 bytes | fixture modelを含むclean artifact |

候補順序入替testは、同じPLAY ActionKeyを異なるoption orderで選ぶことを確認した。model欠落test、schema mismatch／NaN拒否、Rule fallback、JSON round-trip、group split、clean-room importもfocused testsへ含めた。

## 検証

- `python -m pytest -q tests/test_student_v0.py tests/test_submission_artifact.py tests/test_rule_agent.py`: 35 passed、external cabt testsは依存未配置のためskip。
- `python -m pytest -q`: 459 passed、3 warnings（既存のPydantic Field非推奨warning）。
- `python scripts/docs/validate_docs.py`: 12 canonical documentsを検証。
- fixture build/train/evaluate/build artifact: validation 2件で上表、Student artifact tar SHA-256 `b2b5fa3e9b273e805345dbca99d3c3de8d27160cfa734350958ae31d2d2f5065`。artifactをtarballだけから`python -I`でimportし、Student出力とmodel欠落時のRule v0 fallbackを確認した。
- 実cabt paired evaluation: **NOT RUN（未実施）**。`kaggle_environments`のcabt plugin、配布data、実traceがこのworktreeにない。synthetic勝率で代用していない。

## GO / NO-GOと再開条件

fixture contractでは合法性、fallback、順序不変性、exportを満たすが、holdout fidelityは実traceではなく、Rule v0とのpaired non-inferiorityも未測定である。そのためStudentをChampion・submission defaultへ昇格しない。

次の再開条件は、正当なcabt environmentでRule v0 callbackからprivacy-safe BC sourceを収集し、episode/near-duplicate/OOD context group holdout、selection type別fidelity、actual p95 latency、Rule v0 paired evaluationを記録することである。C3 outputは品質Gate済みの任意teacher inputとしてのみ後続追加し、C4 v0 runtimeの必須依存へはしない。

## 正典ブランチ統合検証

`feature/student-v0`の`55242022179fb84b8fce08f3165a2672e7b98ed8`を、C3統合済みの`feature/belief-guided-search`へ通常のno-ff mergeで統合した（merge commit: `db174d0`）。`main.py`はC3の`make_bounded_search_agent`とC4の`make_student_agent`を併存させ、`main.agent`／`_DEFAULT_AGENT`はRule Agent v0のままである。C3はEngineAdapter未指定でRule v0へfallbackし、C4はmodel未配置・破損・非有限score等でRule v0へfallbackする。pack未指定のC3／Rule v0はKnowledge moduleをimportしない。

- C3/C4/Rule/Knowledge focused integration: 106 passed、3 warnings。
- repository: 484 passed、3 warnings。
- fixture dataset build/train/evaluate: 12 source group、train 10／validation 2、top-1 1.0、legal action rate 1.0。これは契約検証だけであり、汎化性能・実cabt性能ではない。
- Student artifact: build／verifyとtarballだけの`python -I` clean-roomをpass。identityは`student-v0-rule-v0-fallback`であり、Student runtime promotionはNO-GOのままである。
- Rule v0 artifact: 2回buildのtarball bytesが一致した。content hashは`cb778ba0ab31aabc74eca7b763cebea80b3df85f78df5ad9025f8948f91361f8`、tar.gz SHA-256は`c26b98d0d7ed80eb288a36c924babcf96ada7405aa3a965a54714d67295b8f6b`。`main.py`へC3/C4の任意factoryを追加したためruntime file hashはC3単独統合時から変化するが、defaultのRule v0選択規則は変更していない。Rule artifact verifyとtarballだけの`python -I` clean-roomをpassした。
- actual cabt paired evaluation: **NOT RUN**。配布runtime／dataとpublic arbitrary-state forward contractがないためであり、synthetic勝率で代用していない。
