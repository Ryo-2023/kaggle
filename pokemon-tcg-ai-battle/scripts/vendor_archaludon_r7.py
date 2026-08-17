"""公開 Archaludon R7 pilot を、単一ファイルの対戦相手として取り込む。

## なぜ 1 ファイルへ畳むのか

`opponent_pool_v1` の `policy_hash` は **`main.py` のバイト列だけ**を検証する。
上流の配布形は `main.py` (9 行の shim) + `archaludon_agent.py` +
`archaludon_bench_guard.py` + `empty_bench_guard.py` の 4 ファイルであり、その形の
まま登録すると、方策本体である `archaludon_agent.py` を書き換えても hash 検査を
通ってしまう。この相手だけ整合性検査が飾りになるため、既存 65 体と同じ
「自己完結した 1 つの `main.py`」へ畳む。

## 上流からの変更点 (方策には触れない)

1. 3 module を依存順に連結する。`empty_bench_guard.apply_bench_guard` は
   `archaludon_bench_guard.apply_bench_guard` と名前が衝突するため、前者を
   `_generic_apply_bench_guard` へ改名する。呼び出し側も同時に書き換える。
2. module 間の import (`from archaludon_bench_guard import ...` など) は、同一
   namespace になるため削除する。
3. 上流の `main.py` は sibling を `os.getcwd()` から解決する。Kaggle では提出
   ディレクトリが cwd なので成立するが、本リポジトリの harness では cwd が repo
   root であり成立しない。畳んだ結果 sibling import 自体が消えるので、この
   解決処理も不要になる。

方策の判断ロジック、カード ID、対面別ルール、bench guard の優先順位には一切
手を入れない。生成物は上流の固定 commit から決定的に再生成できる。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

UPSTREAM_REPO_V1 = "https://github.com/TomBombadyl/kaggle_pokemon"
UPSTREAM_COMMIT_V1 = "39545440b0cf4ab6175a45742e525d0628ca5e68"
EXPECTED_DECK_SHA256_V1 = (
    "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e"
)
OPPONENT_ID_V1 = "public_archaludon_cinderace_r7"


class VendorV1Error(RuntimeError):
    """Raised when the upstream tree is not the shape this vendoring assumes."""


def _strip_module_docstring(source: str) -> str:
    match = re.match(r'\s*(?:"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\')', source)
    return source[match.end():] if match else source


def _drop_future_imports(source: str) -> str:
    return "\n".join(
        line for line in source.splitlines()
        if not line.startswith("from __future__ import")
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise VendorV1Error(message)


_SIDECAR_DECK_HELPER_V1 = '''

def _sidecar_deck_path() -> str | None:
    """The deck.csv shipped next to this policy, if present.

    FROZEN-OPPONENT PATCH (bench only, applied by scripts/vendor_archaludon_r7.py):
    upstream resolves a cwd-relative "deck.csv" first, which is correct on Kaggle
    (cwd is the submission directory) and wrong in this repository's harness (cwd
    is the repo root, whose deck.csv is the *submission* deck).  Without this the
    pilot reasons about 60 cards that are not its own.
    """
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return None
    candidate = os.path.join(here, "deck.csv")
    return candidate if os.path.exists(candidate) else None

'''

_UPSTREAM_READ_DECK_V1 = '''def read_deck_csv():
    fp = "deck.csv"
    if not os.path.exists(fp):
        fp = "/kaggle_simulations/agent/deck.csv"
'''

_PATCHED_READ_DECK_V1 = '''def read_deck_csv():
    fp = _sidecar_deck_path() or "deck.csv"
    if not os.path.exists(fp):
        fp = "/kaggle_simulations/agent/deck.csv"
'''

_UPSTREAM_RESOLVE_V1 = '''    if os.path.exists("deck.csv"):
        return "deck.csv"
'''

_PATCHED_RESOLVE_V1 = '''    sidecar = _sidecar_deck_path()
    if sidecar:
        return sidecar
    if os.path.exists("deck.csv"):
        return "deck.csv"
'''


def _patch_deck_resolution_v1(agent_src: str) -> str:
    """Make both deck readers prefer the policy's own sidecar deck.csv."""
    _require(
        _UPSTREAM_READ_DECK_V1 in agent_src,
        "archaludon_agent.py read_deck_csv() no longer matches the expected upstream form",
    )
    _require(
        _UPSTREAM_RESOLVE_V1 in agent_src,
        "archaludon_agent.py _resolve_deck_path() no longer matches the expected upstream form",
    )
    # The helper must be defined before the first reader that uses it.
    anchor = "def read_deck_csv():"
    agent_src = agent_src.replace(
        anchor, _SIDECAR_DECK_HELPER_V1.strip("\n") + "\n\n\n" + anchor, 1
    )
    agent_src = agent_src.replace(_UPSTREAM_READ_DECK_V1, _PATCHED_READ_DECK_V1, 1)
    return agent_src.replace(_UPSTREAM_RESOLVE_V1, _PATCHED_RESOLVE_V1, 1)


def build_single_file_policy_v1(agent_dir: Path, *, commit: str) -> str:
    """Concatenate the three upstream modules into one importable policy."""
    agent_src = (agent_dir / "archaludon_agent.py").read_text(encoding="utf-8")
    bench_src = (agent_dir / "archaludon_bench_guard.py").read_text(encoding="utf-8")
    empty_src = (agent_dir / "empty_bench_guard.py").read_text(encoding="utf-8")

    # 1. Rename the generic guard so the archetype-specific wrapper can keep the
    #    public name the agent calls.
    _require(
        "def apply_bench_guard(" in empty_src,
        "empty_bench_guard.py no longer defines apply_bench_guard",
    )
    empty_src = empty_src.replace(
        "def apply_bench_guard(", "def _generic_apply_bench_guard("
    )
    _require(
        "apply_bench_guard" not in empty_src.replace("_generic_apply_bench_guard", ""),
        "empty_bench_guard.py still references apply_bench_guard after renaming; "
        "an internal call would now resolve to the archetype wrapper instead",
    )

    # 2. The archetype wrapper's own import of the generic guard is now a
    #    same-namespace reference.
    _require(
        "_BENCH_PRIORITY = (169, 57)" in bench_src,
        "archaludon_bench_guard.py bench priority changed; re-read it before vendoring",
    )
    bench_body = (
        "_BENCH_PRIORITY = (169, 57)\n\n\n"
        "def apply_bench_guard(obs_dict: dict, selection: list) -> list:\n"
        "    return _generic_apply_bench_guard(obs_dict, selection, _BENCH_PRIORITY)\n"
    )

    # 3. The agent's late import of the wrapper is likewise unnecessary.
    late_import = (
        "try:\n"
        "    from agent.archaludon_bench_guard import apply_bench_guard\n"
        "except ImportError:\n"
        "    from archaludon_bench_guard import apply_bench_guard\n"
    )
    _require(
        late_import in agent_src,
        "archaludon_agent.py no longer contains the expected bench-guard import block",
    )
    agent_src = agent_src.replace(late_import, "")
    _require(
        "def agent(obs_dict: dict)" in agent_src,
        "archaludon_agent.py no longer defines agent(obs_dict)",
    )

    # 4. Deck resolution: upstream prefers a cwd-relative `deck.csv`, which is
    #    right on Kaggle (cwd is the submission directory) and wrong here (cwd is
    #    the repository root, holding the *submission* deck).  Measured: the
    #    unpatched policy read `<repo>/deck.csv` and reasoned about 60 cards that
    #    were not its own for every game.  Resolve next to this file first --
    #    the same "FROZEN-OPPONENT PATCH" the pooled ozawa/nihei seeds already
    #    carry, for the same reason.
    agent_src = _patch_deck_resolution_v1(agent_src)
    # The cwd branch legitimately survives as a *later* fallback; what must change
    # is the order, so assert the sidecar check precedes it in both readers.
    sidecar_at = agent_src.find("sidecar = _sidecar_deck_path()")
    cwd_at = agent_src.find('if os.path.exists("deck.csv"):\n        return "deck.csv"')
    _require(
        0 <= sidecar_at < cwd_at,
        "_resolve_deck_path still consults cwd before the policy's own deck.csv",
    )
    _require(
        '_sidecar_deck_path() or "deck.csv"' in agent_src,
        "read_deck_csv still resolves deck.csv from cwd first",
    )

    header = f'''"""Archaludon ex / Cinderace — community v5 pilot + R7 empty-bench guard.

Vendored verbatim from {UPSTREAM_REPO_V1}
at commit {commit}, then folded into one file by
scripts/vendor_archaludon_r7.py.  See that script for the three mechanical
changes made (name collision, inter-module imports, cwd-based path resolution)
and for why a multi-file opponent would defeat this pool's policy_hash check.

Usage boundary: local_eval_only.  This is an opponent and a possible teacher;
it is never packaged into a submission bundle.
"""
from __future__ import annotations
'''
    parts = [
        header,
        f"\n# ---- vendored: agent/empty_bench_guard.py @ {commit[:12]} ----\n",
        _drop_future_imports(_strip_module_docstring(empty_src)),
        f"\n\n# ---- vendored: agent/archaludon_bench_guard.py @ {commit[:12]} ----\n",
        bench_body,
        f"\n\n# ---- vendored: agent/archaludon_agent.py @ {commit[:12]} ----\n",
        _drop_future_imports(_strip_module_docstring(agent_src)),
    ]
    return "".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-checkout", required=True,
                        help="固定 commit を checkout 済みの上流リポジトリ")
    parser.add_argument("--opponent-id", default=OPPONENT_ID_V1)
    parser.add_argument("--opponents-root", default=str(_ROOT / "opponents"))
    args = parser.parse_args()

    upstream = Path(args.upstream_checkout).resolve()
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=upstream, text=True
    ).strip()
    if head != UPSTREAM_COMMIT_V1:
        raise SystemExit(
            f"upstream checkout is at {head}, not the pinned {UPSTREAM_COMMIT_V1}. "
            "Later revisions (R8-R12) measured worse on the public ladder; this "
            "vendoring is pinned deliberately."
        )

    agent_dir = upstream / "agent"
    deck_src = upstream / "agent_decks" / "archaludon_ex_cinderace.csv"
    for path in (agent_dir / "archaludon_agent.py", agent_dir / "archaludon_bench_guard.py",
                 agent_dir / "empty_bench_guard.py", deck_src):
        if not path.is_file():
            raise SystemExit(f"upstream file missing: {path}")

    deck_bytes = deck_src.read_bytes()
    deck_hash = hashlib.sha256(deck_bytes).hexdigest()
    if deck_hash != EXPECTED_DECK_SHA256_V1:
        raise SystemExit(
            f"upstream deck hash {deck_hash} != expected {EXPECTED_DECK_SHA256_V1}"
        )
    card_ids = [line for line in deck_bytes.decode("utf-8").splitlines() if line.strip()]
    if len(card_ids) != 60:
        raise SystemExit(f"deck must be 60 cards, got {len(card_ids)}")

    policy = build_single_file_policy_v1(agent_dir, commit=head)

    out_dir = Path(args.opponents_root) / args.opponent_id
    if out_dir.exists():
        raise SystemExit(
            f"{out_dir} already exists; refusing to overwrite a registered opponent. "
            "Remove it deliberately or choose another --opponent-id."
        )
    out_dir.mkdir(parents=True)
    (out_dir / "main.py").write_text(policy, encoding="utf-8")
    shutil.copyfile(deck_src, out_dir / "deck.csv")
    (out_dir / "SOURCE.md").write_text(
        f"""# {args.opponent_id}

| 項目 | 値 |
|---|---|
| upstream | {UPSTREAM_REPO_V1} |
| commit | `{head}` |
| deck | `agent_decks/archaludon_ex_cinderace.csv` |
| deck sha256 | `{deck_hash}` |
| usage boundary | `local_eval_only` |

`agent/archaludon_agent.py` + `agent/archaludon_bench_guard.py` +
`agent/empty_bench_guard.py` を `scripts/vendor_archaludon_r7.py` で 1 ファイルへ
畳んだもの。変更点は同スクリプトの docstring を正とする。方策の判断ロジックには
手を入れていない。

`opponents/tomatomato_archaludon` とは **60 枚デッキが同一 (同じ sha256) だが別の
エージェント**である。後者には R7 の bench guard (`ARCHALUDON_BENCH_GUARD`,
`apply_bench_guard`, `_legal_fallback`, `_is_legal`) が存在しない。名前や成績を
混同しないこと。

上流の R8〜R12 系は公開ラダーで R7 を下回ったと報告されているため、commit を
固定している。最新版へ追従しない。
""",
        encoding="utf-8",
    )

    policy_hash = hashlib.sha256((out_dir / "main.py").read_bytes()).hexdigest()
    print(json.dumps({
        "opponent_id": args.opponent_id,
        "path": str(out_dir),
        "upstream_commit": head,
        "policy_hash": policy_hash,
        "canonical_deck_hash": deck_hash,
        "policy_lines": len(policy.splitlines()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
