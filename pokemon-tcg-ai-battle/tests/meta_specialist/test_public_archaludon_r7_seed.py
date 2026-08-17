"""R7 pilot と公開ノートブック原版を取り違えないことを固定する。

両者は **60 枚デッキが同一 (同じ sha256) で、エージェントが別物**である。名前や
デッキ hash が一致することを根拠に成績を読み替えると、16 相手・300 局で 5.0% と
測った個体の数字を、別の個体の実力として扱ってしまう。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    default_pool_root_v1,
    load_opponent_pool_v1,
    resolve_opponent_v1,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_R7_ID = "public_archaludon_cinderace_r7"
_ORIGINAL_ID = "tomatomato_archaludon"
_SHARED_DECK_SHA256 = "42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e"
# R7 の識別子。上流 `agent/archaludon_agent.py` と guard が持つもの。
_R7_MARKERS = ("ARCHALUDON_BENCH_GUARD", "apply_bench_guard", "_legal_fallback", "_is_legal")


def _pool():
    return load_opponent_pool_v1(default_pool_root_v1(_REPO_ROOT))


def _policy_source(opponent_id: str) -> str:
    instance = resolve_opponent_v1(_pool(), opponent_id, subject_deck_csv_path="x")
    return Path(instance.policy_path).read_text(encoding="utf-8")


def test_both_archaludon_seeds_are_registered_separately() -> None:
    pool = _pool()

    assert _R7_ID in pool
    assert _ORIGINAL_ID in pool
    assert pool[_R7_ID].policy_path != pool[_ORIGINAL_ID].policy_path


def test_the_two_seeds_share_a_deck_but_not_a_policy() -> None:
    pool = _pool()
    decks = {
        opponent_id: hashlib.sha256(
            Path(pool[opponent_id].deck_csv_path).read_bytes()
        ).hexdigest()
        for opponent_id in (_R7_ID, _ORIGINAL_ID)
    }

    assert decks[_R7_ID] == decks[_ORIGINAL_ID] == _SHARED_DECK_SHA256
    assert pool[_R7_ID].policy_hash != pool[_ORIGINAL_ID].policy_hash


def test_only_the_r7_seed_carries_the_bench_guard() -> None:
    r7_source = _policy_source(_R7_ID)
    original_source = _policy_source(_ORIGINAL_ID)

    for marker in _R7_MARKERS:
        assert marker in r7_source, f"R7 seed lost {marker!r}"
        assert marker not in original_source, (
            f"{_ORIGINAL_ID} now contains {marker!r}; the two seeds can no longer be "
            "told apart by this test and their measured results may be conflated"
        )


def test_the_r7_seed_is_evaluation_only() -> None:
    """提出 bundle へ入れてよい資産ではない。"""
    assert _pool()[_R7_ID].usage_boundary == "local_eval_only"


def test_the_vendored_policy_records_its_pinned_upstream_commit() -> None:
    source = _policy_source(_R7_ID)

    assert "39545440b0cf4ab6175a45742e525d0628ca5e68" in source, (
        "vendored policy no longer names the commit it came from; R8-R12 は公開"
        "ラダーで R7 を下回ったため、どの版かが追えないと採用根拠が消える"
    )


def test_the_folded_policy_has_no_leftover_sibling_imports() -> None:
    """畳んだ後に sibling import が残っていると、harness で ImportError になる。"""
    source = _policy_source(_R7_ID)

    for dangling in (
        "from archaludon_bench_guard import",
        "from agent.archaludon_bench_guard import",
        "from empty_bench_guard import",
        "from agent.empty_bench_guard import",
    ):
        assert dangling not in source, f"leftover sibling import: {dangling}"


def test_the_generic_guard_was_renamed_so_the_wrapper_keeps_the_public_name() -> None:
    source = _policy_source(_R7_ID)

    assert "def _generic_apply_bench_guard(" in source
    assert "def apply_bench_guard(" in source
    # 呼び出し側が改名後の名前を使っていること。
    assert "_generic_apply_bench_guard(obs_dict, selection, _BENCH_PRIORITY)" in source


def test_the_r7_policy_exposes_a_callable_agent() -> None:
    from mage_ptcg.meta_specialist.opponent_pool_v1 import load_opponent_agent_callable_v1

    instance = resolve_opponent_v1(_pool(), _R7_ID, subject_deck_csv_path="x")

    assert callable(load_opponent_agent_callable_v1(instance))
