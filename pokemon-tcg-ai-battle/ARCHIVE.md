# アーカイブ情報

Kaggle「The Pokemon Company - PTCG AI Battle Challenge Simulation」の作業期間は **2026年8月17日に終了した**。本リポジトリは以後アーカイブとして扱い、新規の開発・評価・提出は行わない。

最初に読む文書は [プロジェクト総括報告書](docs/postmortems/2026-08-17-project-final-report.md) とする。目的、方針転換の理由、実験結果、反省点、再利用可能な資産がまとまっている。

## 最終状態の要約

| 項目 | 内容 |
|---|---|
| 研究上の最良基準 | `cg-lethal-target-v1`（P1）＋ 終盤に固定した基準デッキ |
| 比較対象の自己所有基準 | `root-cg-self-owned-v1`（P0） |
| 後続候補（P2） | 独立評価で改善が再現せず、いずれも非昇格 |
| 長時間学習 | 開始条件を満たさず未開始 |
| ローカル提出検証 | `PASS / READY_TO_SUBMIT` の記録あり |
| 実際の Kaggle 提出・最終順位 | **未確認**。報告書 §18.3 を参照 |

数値の解釈上の注意は報告書 §1.2 に従う。異なる時期・異なる相手群の勝率を直接比較してはならない。

## ディレクトリ構成

```text
.
├── ARCHIVE.md                     # 本ファイル
├── main.py, deck.csv              # 提出エントリポイントとデッキ
├── archive/final-baseline-p1/     # 最終研究基準 P1 の退避（下記参照）
├── docs/
│   ├── postmortems/               # 総括報告書
│   ├── evidence/                  # 評価・スクリーニング記録（438 件）
│   ├── status/                    # 終了時点の状態・引き継ぎ
│   └── plan/                      # 設計正典（当時の計画。現行計画ではない）
├── src/mage_ptcg/                 # 実装本体
├── scripts/                       # 評価・提出・補助スクリプト
│   └── archive/prune_runs.sh      # runs/ 軽量化スクリプト
├── tests/                         # テスト
├── experiments/                   # 採用判断に使った実験記録
├── runs/                          # 実験成果物（Git 管理外・軽量化済み）
├── local-artifacts/               # 統合した外部成果物（Git 管理外）
├── cg/                            # 配布エンジン（Git 管理外・再配布不可）
└── data/                          # カードデータ等（Git 管理外・再配布不可）
```

## `archive/final-baseline-p1/`

報告書 §17.3 が「整理して保管すべきもの」とした最終研究基準 P1 の中核を、`runs/` の軽量化で失われない位置へ退避したもの。

| パス | 内容 |
|---|---|
| `package/main.py` | P1 の方策ソース |
| `package/deck.csv` | P1 が使った 60 枚デッキ |
| `evidence/cg-p1-independent-768-20260814-v1/` | 独立768局の評価証拠。勝敗、座席別、故障、SHA-256、相手一覧を含む |
| `evidence/cg-p1-public-telemetry-96-20260814-v1/` | 公開観測ブロックのマニフェスト |
| `telemetry/*.jsonl` | P1 の公開観測 96 局・4,173 行（判断 4,077 行 ＋ デッキ登録 96 行） |

エンジンバイナリ（`libcg.so` 等）は退避対象に含めない。正本はリポジトリ直下の `cg/` にあり、AGENTS.md の再配布禁止規則によりチーム外へ持ち出さない。

## Git 管理から除外しているもの

次はリポジトリに含まれず、GitHub へも送られない。復元には元の配布物または再実行が必要になる。

- `cg/` — 配布エンジン（再配布不可）
- `data/` — カードデータ等（再配布不可）
- `runs/`, `local-artifacts/`, `worktree-archives/`, `dist/` — 実験成果物
- `.venv/` — Python 仮想環境
- `kaggle.json`, `.env` — 認証情報

## 復元手順

### Python 環境

`.venv/` はアーカイブ時に削除した。必要になったら再生成する。

```bash
uv venv --python 3.12
```

```bash
uv pip install -r requirements.txt
```

### 実験成果物

`runs/` は `scripts/archive/prune_runs.sh` により約 133GB から約 5GB へ縮小してある。削除したのは次の生データで、いずれも再実行しない限り復元できない。

- 対局ごとの生記録（`games/` 配下）
- 教師データ・学習コーパス（`game-*.jsonl`, `dataset-*.jsonl`, `snapshot-*.json`, `rule_bc*.jsonl`）
- 学習済みチェックポイント（`*.pt`, `*.pth`）
- run ごとに複製されたエンジンバイナリ
- キャッシュとワーカーログ

残したのは評価証拠（`summary.json`, `manifest*.json`, `run_summary.json`, `ledger.jsonl`, telemetry）と方策ソースであり、報告書が引用する数値はすべてこれらから追跡できる。

## 公開ミラー

本リポジトリ（チーム所有・非公開）の一部を、個人の公開リポジトリ
`Ryo-2023/kaggle` の `pokemon-tcg-ai-battle/` へミラーしている。**公開ミラーは意図的に部分的である。**

第三者の成果物を公開再配布しないため、次はミラーから除外している（297 ファイル）。

| 除外対象 | 理由 |
|---|---|
| `opponents/` | 他コンペ参加者およびチームメンバーの方策 104 体。取り込み時の権限はローカル評価等に限られ、公開再配布は別権限である |
| `quarantine/` | 隔離した外部提出物アーカイブを含む |
| `vendor_opponent_pilots/` | 外部由来の試行コード |
| `artifacts/team-knowledge-mining/`, `artifacts/team-knowledge-curated/` | チームの非公開ブランチから採掘した知識と出所情報 |
| `configs/meta_specialist/*kaggle_kernel*` | 公開カーネル由来の相手メタ設定 48 件 |

`cg/`（配布エンジン）と `data/`（カードデータ）は元から Git 管理外であり、ミラーにも含まれない。

このため公開ミラーだけでは評価を再実行できない。再現には非公開のチームリポジトリと、権限を確認した対戦相手集団が必要になる。

## 再利用するときの注意

報告書 §19.1 の 10 原則を先に読むことを勧める。特に次の 3 点は、本プロジェクトが実験を通じて得た結論である。

1. 96 局は候補を証明する局数ではなく、壊れた候補を早く捨てる局数である。
2. 弱い基準に勝っても、現在の最良基準に勝たなければ更新ではない。
3. オフラインの正解率・損失の改善は、実対戦勝率の改善を意味しない。

本リポジトリの学習・探索コード（行動模倣、AWR、PPO、V-trace、DAgger、PSRO、PIMC、ExIt、CEM 等）は競技中に昇格しなかったが、実装としては動作する。再利用する場合は、教師品質・行動表現・独立評価を先に固めること。
