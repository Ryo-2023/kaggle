# meta-specialist オフライン V-trace 1周目（rule agent コーパス 250 step）

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-08-04 22:00 JST |
| 担当 | agent (Claude Opus 5) |
| 種別 | local experiment |
| commit | `acadc711` |
| branch | `codex/meta-specialist-p0-foundation` |
| model provenance | claude-opus-5 / Anthropic / Claude Code 2.1.221 |
| simulator / data | CABT via `scripts/test_sim.py`、engine sha256 `b9201330…`、収集コーパス `runs/meta-specialist-actor-pool/p0-rule-agent-2000`（4,270局 / 87,258 transition、subject_behavior_version `b89ca316…` 単一） |

## 目的と反証条件

- **問い**: rule agent v0 が収集した固定コーパスに対するオフライン V-trace 学習で、`alakazam` レーンの勝率が rule agent v0 を上回るか。
- **仮説**: value head + entropy + BC 錨を備えた V-trace が、収集された行動の再評価だけで rule agent を上回る方策を学習する。
- **反証条件**: 300局評価で勝率の信頼区間が rule agent の点推定（12.27%）を含む場合、この条件では改善を示せなかったと判断する。
- **変更点（baseline = rule agent v0 そのもの）**: 学習した neural policy を subject に置き換えた 1 点のみ。
- **固定条件**: deck は同一の qualified `alakazam` 実体、対戦相手は `cabt_rule_agent_v0`、seat balanced、`--max-steps 250`、`--trajectories-per-step 64`、AdamW lr 1e-3、`value_coefficient` 0.5、`entropy_coefficient` 0.01、`bc_coefficient` 0.1、`rho_bar`=`c_bar`=1.0、device cpu。

## 再現

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-p0
PY=/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/.venv/bin/python

PYTHONPATH=.:src $PY scripts/train_meta_specialist_from_trajectories.py \
  --collection-run-dir runs/meta-specialist-actor-pool/p0-rule-agent-2000 \
  --run-name p0-train-round1 --trajectories-per-step 64 \
  --max-steps 250 --checkpoint-interval-steps 25 --device cpu

CKPT=$(ls -t runs/meta-specialist-training/p0-train-round1/checkpoints/*.pt | head -1)
PYTHONPATH=.:src $PY -m mage_ptcg.meta_specialist collect-trajectories \
  --num-games 300 --base-seed 9400000 --run-name eval-round1 --lanes alakazam \
  --behavior-kind neural_specialist --neural-checkpoint-path "$CKPT" --workers 10
```

生成物（Git 管理外）:

| path | 内容 |
|---|---|
| `runs/meta-specialist-training/p0-train-round1/` | checkpoint `ceff8196543dd8bf0ff6a03ec5ba08b58bc6c6b2f0c2e7a31640fcda4ca9098a` |
| `runs/meta-specialist-actor-pool/eval-round1/` | 評価292局 |
| `runs/meta-specialist-actor-pool/eval-trained-clean/` | 80step checkpoint の評価290局 |

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| rule agent v0 (baseline) | base 2000000 | 1,728 | **12.27% ±1.55%** | 0 | 0 | — | 既存コーパスの `alakazam` 分 |
| trained 80 step | base 9300000 | 290 | 14.14% ±4.01% | 0 | 0 | 収集133s | fault 10/300 |
| trained 250 step (proposed) | base 9400000 | 292 | **11.99% ±3.73%** | 0 | 0 | 収集133s | fault 8/300 |

学習側の計測:

| 項目 | 値 |
|---|---|
| steps | 250/250 完走、skipped 0、scoring failure 0 |
| wall time | 1,527s（ロード466s + 準備295s + 学習756s、3.03 s/step） |
| `dlogp`（target − behavior） | **+1.4349** |
| mean log-prob | target −0.1995 / behavior −1.6344 |
| `dead_rho`（ρ<0.01） | **0.000** |
| `clip_hi`（ρ>ρ̄） | 0.879 |
| `V` / `ret` | −0.667 / −0.75 |
| grad norm | 0.484 |

- **sanity check**: 評価は seat balanced（146/146）。admitted 4,270/4,270、unreadable 0、dropped_stale 0。学習の消費 transition 326,955 = 87,258 × 約3.75 epoch。勝率は 3 条件とも 11〜15% の同一桁に収まり、桁落ちや集計欠損は無い。
- **負の所見**: proposed は baseline を **0.28pt 下回った**。80 step から 250 step で 2.15pt 低下しており、学習を延ばして改善する傾向は観測できていない。
- **不確実性**: 単一 seed 系列・単一レーン・単一学習実行。評価 n≈290 で信頼区間 ±約3.7pt、差の信頼区間は約 ±4.0pt。seed 間ばらつきは未測定。

## 解釈と判断

- **観測事実**: 3 条件の信頼区間はいずれも互いの点推定を含む。学習は数値的に健全（発散なし、critic 較正済み、fault 0）だが、勝率は動いていない。方策は収集行動へ約82%の確率を割り当てている（target log-prob −0.1995）。
- **解釈**: 方策は rule agent の鋭くした複製へ収束した。理由は 2 つ。(1) `bc_coefficient` 0.1 に対し |advantage| が 0.01〜0.04 で、勾配の約 3/4 が模倣項である。(2) より本質的に、固定コーパスのオフライン 1 パスであるため、方策は rule agent が実際に取った行動の並べ替えしかできず、探索が存在しない。勝率 12% の行動分布内で最善を選んでも上限は 12% 付近になる。代替説明として、学習ステップ数の不足（3.75 epoch）や lr / 係数の不適合も否定はできないが、80→250 step で改善が見えないことは (1)(2) と整合する。
- **判断**: **保留**。この条件では改善を示せなかった。棄却はしない（発散解消と実行経路の成立という前提条件は達成済み）。
- **言わないこと**: 「V-trace はこの課題に不適」とは言えない。試したのは固定 rule agent コーパスに対するオフライン 1 パスのみであり、設計本体の `collect → train → evaluate → promote` ループを 1 周も回していない。他レーン、他アルゴリズム、他係数についても何も主張しない。
- **次 action**:
  1. **2 周目の収集**（owner: user）。学習済み checkpoint を subject に 2,000 局収集し、そのコーパスで再学習する。停止条件: 2 周目評価で勝率が baseline を下回り続ける場合、オフライン反復ではなく報酬・対戦相手設計へ戻る。
  2. **`bc_coefficient` の再探索**（owner: agent）。0.02〜0.05 の範囲で発散せずに RL 項の寄与を上げられるか。停止条件: `dead_rho` が 0.5 を超えたら棄却。
  3. **収集データの勝率 12% 自体の見直し**（owner: 未定）。terminal が全 transition の約 5% しかなく、advantage が構造的に小さい。停止条件: 未設定。

## Kaggle 提出（該当時）

該当なし。本実験では提出を行っていない。

---

# 追記: 2周目と、レーン別に見た再評価（2026-08-05）

上の「改善を示せなかった」という判断は **alakazam レードのみを評価対象にしたことによる誤り**であった。3レーン全体で見ると有意な改善が存在する。

## 追加実測

2周目は round1 checkpoint 自身で 2,000 局収集（`p0-round2-neural`、1,970完走 / fault 30）し、そのコーパスで 250 step 再学習した（`p0-train-round2`、250/250完走・skipped 0・scoring failure 0）。

### レーン別: rule agent v0 と round1 方策の比較

round2 の収集そのものが round1 方策による 1,970 局であるため、レーン別の比較標本として使える。

| lane | rule agent v0 | round1 方策 | 差 | 判定 |
|---|---|---|---|---|
| alakazam | 12.27% ±1.55% (n=1728) | 15.07% ±2.78% (n=637) | +2.80% ±3.18% | 有意でない |
| **grimmsnarl_froslass_munkidori** | **4.96% ±1.22% (n=1210)** | **9.90% ±2.27% (n=667)** | **+4.94% ±2.58%** | **有意** |
| rocket_mewtwo_spidops | 20.12% ±2.15% (n=1332) | 16.97% ±2.85% (n=666) | −3.15% ±3.57% | 有意でない |

grimmsnarl は z=3.75。3レーンの Bonferroni 補正（閾値 z>2.39）後も有意である。

### alakazam の推移（標本を統合）

| condition | n | win rate |
|---|---:|---|
| rule agent v0 | 1,728 | 12.27% ±1.55% |
| round1 方策（eval + round2収集） | 929 | 14.10% ±2.24% |
| round2 方策 | 291 | 15.81% ±4.19% |

各段の差は個別には有意でないが、方向は単調である。

### 2周目の学習側計測

| 項目 | round1 | round2 |
|---|---|---|
| `dlogp` | +1.4349 | **+0.0215** |
| mean log-prob target / behavior | −0.1995 / −1.6344 | −0.0489 / −0.0705 |
| 実効 importance ratio（exp(dlogp)） | 約 4.2 | **約 1.022** |
| `clip_hi` | 0.879 | 0.838 |
| `dead_rho` | 0.000 | 0.000 |
| `V` / `ret` | −0.667 / −0.75 | −0.483 / −0.656 |
| admitted transitions | 87,258 | 47,369 |

## 解釈の訂正

- **観測事実**: 3レーン中1レーンで有意な改善（4.96%→9.90%）、1レーンで有意でない改善、1レーンで有意でない悪化。
- **解釈**: オフライン V-trace 学習は機能している。当初「改善なし」と判断したのは、評価レーンを alakazam 1本に絞ったためである。改善幅はレーン依存で、rule agent が既に強い rocket_mewtwo（20.12%）では悪化方向、弱い grimmsnarl（4.96%）で最も伸びた。これは「rule agent が取りこぼしている行動が多いレーンほど、収集行動の再評価で得られる余地が大きい」という説明と整合するが、単一実行の観測であり因果は未確立。
- **`clip_hi` の読み違い**: round2 の `clip_hi` 0.838 は round1 の 0.879 とほぼ同じに見えるが、実効 ratio は 4.2 から 1.022 へ下がっている。behavior が方策自身になり、ほぼ決定的（log-prob −0.07）になったため、わずかな変化でも ratio が 1 を超えて計上される。`clip_hi` 単体では off-policy 距離を判断できない。**`dlogp` から ratio を見るべきである。**
- **判断**: **保留を継続**。改善は存在するが、有意なのは 3 レーン中 1 レーンで、単一学習実行・単一 seed 系列である。採用判断には round2 方策の全レーン評価が要る。
- **言わないこと**: 「round2 は round1 より強い」とは言えない（alakazam 291局で ±4.19%）。レーン間の改善幅の違いに対する上記の説明は仮説であり検証していない。

## 次 action（更新）

1. **round2 方策を grimmsnarl と rocket_mewtwo で評価**（owner: user）。signal が最も強い grimmsnarl を含めないと round1→round2 の変化を判定できない。停止条件: grimmsnarl で round1（9.90%）を下回る場合、反復はレーン依存と結論し 3 へ移る。
2. **rocket_mewtwo の悪化の確認**（owner: user）。−3.15% は有意でないが方向が負である。標本を増やして実在するか確かめる。停止条件: 有意な悪化が確認されたら、全レーン一律の学習方針を見直す。
3. **`bc_coefficient` 再探索**（owner: agent）。0.02〜0.05。停止条件: `dead_rho` > 0.5 で棄却。
