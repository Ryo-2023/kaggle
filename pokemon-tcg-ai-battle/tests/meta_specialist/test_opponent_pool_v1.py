"""登録された相手を実体としてロードする契約 (正典 §13, §5)。

以前の実装は相手を ``frozenset({"cabt_rule_agent_v0"})`` に固定し、相手デッキを
subject 自身のデッキへ束縛していた。名前を増やしても ``agent_b_factory=None`` の
ままだったため、どの相手を選んでも engine 内蔵の rule agent が対局していた。
ここで検査するのはその再発防止である。
"""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sys

import pytest

from mage_ptcg.meta_specialist.opponent_pool_v1 import (
    MIRROR_OPPONENT_ID_V1,
    OpponentPoolV1Error,
    build_opponent_agent_factory_v1,
    load_opponent_pool_v1,
    opponent_version_v1,
    resolve_opponent_v1,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_POOL_ROOT = _REPO_ROOT / "opponents"

pytestmark = pytest.mark.skipif(
    not (_POOL_ROOT / "pool_manifest.json").is_file(),
    reason="the opponent pool is not materialized in this checkout",
)


def _pool():
    return load_opponent_pool_v1(_POOL_ROOT)


def test_every_registered_opponent_loads_a_real_policy() -> None:
    """全登録相手が実際に読めること.

    ここが落ちる状態は「相手プールに名前だけがある」状態であり、curriculum も
    calibration も意味を失う。
    """
    pool = _pool()
    assert pool, "the pool manifest registered no opponents"
    for opponent_id, instance in pool.items():
        agent = build_opponent_agent_factory_v1(instance)(None, 0)
        assert callable(agent), f"{opponent_id} did not expose a callable agent"


def test_loading_opponents_preserves_the_engine_and_rule_agent_modules() -> None:
    """相手の import が engine / Rule Agent v0 の module を置き換えないこと.

    これは実際に踏んだ順序依存バグの回帰テストである。リポジトリ直下には
    ``main.py`` と ``agents/`` があり、pooled な ``meta_*`` 提出は ozawa branch の
    ``agents.generic_agent`` を必要とする。engine を先に import して
    ``agents`` が ``sys.modules`` へ載った状態だと、vendored な pilot が影に隠れて
    相手のロードが失敗していた。逆に相手のロード後に vendored 版が残ると、
    リポジトリ自身の Rule Agent v0 の import 先が黙って変わる。
    """
    from scripts.test_sim import run_match  # noqa: F401  -- caches `main` and `agents`
    import agents as repo_agents

    before_main = sys.modules.get("main")
    before_agents = sys.modules.get("agents")
    before_path = list(sys.path)

    pool = _pool()
    for instance in pool.values():
        build_opponent_agent_factory_v1(instance)(None, 0)

    assert sys.modules.get("main") is before_main
    assert sys.modules.get("agents") is before_agents
    assert sys.path == before_path
    assert hasattr(repo_agents, "choose_rule_indices"), (
        "the repository's own Rule Agent v0 package was replaced by a vendored pilot"
    )


def test_loading_cg_game_source_does_not_reuse_candidate_cg_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source importing ``cg.game`` must not see a candidate's partial ``cg`` package.

    Candidate packages legitimately carry only ``cg.api``/``cg.sim``.  If the
    candidate is imported before an opponent, that partial top-level ``cg``
    module must not shadow the shared engine package required by a legacy
    source that imports ``cg.game``.
    """
    runs_root = tmp_path / "runs"
    pool_root = runs_root / "pool"
    source_root = pool_root / "cg-game-source"
    # Historical pools are materialized below ``runs/`` while the shared
    # engine remains at the repository (or checkout) root.
    engine_root = tmp_path / "cg"
    candidate_root = tmp_path / "candidate"
    source_root.mkdir(parents=True)
    engine_root.mkdir(parents=True)
    (candidate_root / "cg").mkdir(parents=True)
    (candidate_root / "cg/__init__.py").write_text("\n", encoding="utf-8")
    (candidate_root / "cg/api.py").write_text("MARKER = 'candidate'\n", encoding="utf-8")
    (engine_root / "__init__.py").write_text("\n", encoding="utf-8")
    (engine_root / "game.py").write_text(
        "def battle_start(*args): return None\n"
        "def battle_finish(*args): return None\n"
        "def battle_select(*args): return None\n",
        encoding="utf-8",
    )
    (source_root / "main.py").write_text(
        "from cg.game import battle_start\n"
        "def agent(observation):\n"
        "    return []\n",
        encoding="utf-8",
    )
    (source_root / "deck.csv").write_text("\n".join(["1"] * 60) + "\n", encoding="utf-8")
    policy_hash = hashlib.sha256((source_root / "main.py").read_bytes()).hexdigest()
    (pool_root / "pool_manifest.json").write_text(
        json.dumps([
            {
                "id": "cg-game-source",
                "canonical_deck_hash": "",
                "policy_hash": policy_hash,
                "usage_boundary": "local_eval_only",
                "source": "test",
                "smoke_ok": False,
                "mean_decision_ms": None,
            }
        ]),
        encoding="utf-8",
    )

    original_cg_modules = {
        name: module
        for name, module in sys.modules.items()
        if name == "cg" or name.startswith("cg.")
    }
    try:
        for name in list(sys.modules):
            if name == "cg" or name.startswith("cg."):
                sys.modules.pop(name, None)
        monkeypatch.syspath_prepend(str(candidate_root))
        import cg.api as candidate_cg_api

        candidate_cg_module = sys.modules["cg"]
        assert candidate_cg_api.MARKER == "candidate"
        instance = next(iter(load_opponent_pool_v1(pool_root).values()))
        factory = build_opponent_agent_factory_v1(instance)
        assert callable(factory(None, 0))
        # The loader must restore the candidate's already-cached module after
        # importing the opponent.  The opponent's shared-engine ``cg.game``
        # module must not leak into the caller's module cache.
        assert sys.modules.get("cg") is candidate_cg_module
        assert "cg.game" not in sys.modules
    finally:
        for name in list(sys.modules):
            if name == "cg" or name.startswith("cg."):
                sys.modules.pop(name, None)
        sys.modules.update(original_cg_modules)


def test_an_unregistered_opponent_is_refused_rather_than_mirrored() -> None:
    """正典 §5: 不足した runtime ID は起動時に失敗させ、推測で補完しない."""
    pool = _pool()
    with pytest.raises(OpponentPoolV1Error):
        resolve_opponent_v1(pool, "not_a_registered_opponent", subject_deck_csv_path="deck.csv")


def test_the_mirror_instance_is_selectable_only_by_name() -> None:
    """self-mirror は明示的に選んだときだけ成立すること.

    curriculum の self-play 相手として正当な選択肢だが、実体の見つからない相手から
    落ちてくる先であってはならない。
    """
    pool = _pool()
    mirror = resolve_opponent_v1(
        pool, MIRROR_OPPONENT_ID_V1, subject_deck_csv_path="my-deck.csv"
    )
    assert mirror.is_mirror
    assert mirror.deck_csv_path == "my-deck.csv"
    assert mirror.policy_path == ""
    with pytest.raises(OpponentPoolV1Error):
        build_opponent_agent_factory_v1(mirror)


def test_distinct_opponents_report_distinct_versions() -> None:
    """相手ごとに version が分かれること.

    以前は ``opponent_kind`` が何であれ repo root の ``main.py`` の hash を返して
    いたため、異なる相手の結果が同一 version として集計されえた。
    """
    pool = _pool()
    external = [i for i in pool.values() if not i.is_mirror][:5]
    versions = {opponent_version_v1(i, mirror_version="M") for i in external}
    assert len(versions) == len(external)


def test_a_manifest_entry_whose_policy_bytes_changed_is_refused(tmp_path: Path) -> None:
    """manifest の policy_hash とディスクの実体が食い違えば失敗すること."""
    import json
    import shutil

    pool = _pool()
    victim = next(i for i in pool.values() if not i.is_mirror)
    root = tmp_path / "opponents"
    (root / victim.opponent_id).mkdir(parents=True)
    shutil.copy(victim.deck_csv_path, root / victim.opponent_id / "deck.csv")
    (root / victim.opponent_id / "main.py").write_text("def agent(obs):\n    return []\n")
    (root / "pool_manifest.json").write_text(
        json.dumps(
            [{
                "id": victim.opponent_id,
                "canonical_deck_hash": victim.canonical_deck_hash,
                "policy_hash": victim.policy_hash,  # stale: does not match the bytes above
                "usage_boundary": "local_eval_only",
                "source": "internal",
                "smoke_ok": True,
                "mean_decision_ms": None,
            }]
        )
    )
    with pytest.raises(OpponentPoolV1Error):
        load_opponent_pool_v1(root)
