"""正典違反を書いた瞬間に落ちる regression guard。

`test_canon_conformance.py` が §22 の受入条件を条項単位で見るのに対し、この
ファイルは実装が過去に正典から逸脱した**具体的な失敗モード**を検知する。

## この形になった経緯 (重要)

初版の 1 件目は ``len(_OPPONENT_KINDS_V1) > 1`` を、3 件目は
``neural_model_v1`` のソースに provenance を示す文字列が現れるかを検査して
いた。どちらも「正典が要求する性質」ではなく「その性質があれば付随して現れる
であろう表面的な形」を見ていたため、次の 2 つで通ってしまった。

- 閉じた enum に名前を 17 個足す。ただし ``agent_b_factory=None`` のままなので
  **どの名前を選んでも engine 内蔵の rule agent が対局する**。相手の deck しか
  変わらない。
- ``FOUNDATION_INIT_PROVENANCE_V1`` という dict を追加する。キーがテストの
  grep する文字列そのもので、値はハードコードされた ``False``/``None``、
  参照箇所はゼロ。

したがって現在のテストは**振る舞い**を検査する。名前の数やソース中の文字列で
はなく、「相手を変えると実際に別の方策が対局するか」「未登録の相手が
self-mirror へ落ちずに失敗するか」を実際に走らせて確かめる。

表面的な形を検査するテストは、それを満たすだけの dead code を誘発する。
"""

from __future__ import annotations

import ast
from pathlib import Path
import subprocess

import pytest


pytestmark = pytest.mark.canon_conformance

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_META_SPECIALIST = _REPO_ROOT / "src" / "mage_ptcg" / "meta_specialist"
_ACTOR_POOL_PATH = _SRC_META_SPECIALIST / "actor_pool_v1.py"
_SUBJECT_DECK = _REPO_ROOT / "opponents" / "tomatomato_archaludon" / "deck.csv"


def _actor_pool_ast() -> ast.Module:
    return ast.parse(_ACTOR_POOL_PATH.read_text(encoding="utf-8"), filename=str(_ACTOR_POOL_PATH))


def _run_one_game(opponent_kind: str, tmp_path: Path, *, seed: int = 777):
    from mage_ptcg.meta_specialist.actor_pool_v1 import (
        ActorJobConfigV1,
        rule_agent_behavior_identity_v1,
        run_one_actor_game_v1,
    )

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=_REPO_ROOT
    ).stdout.strip()
    job = ActorJobConfigV1(
        job_id=f"anti-canon-{opponent_kind}",
        archetype_id="archaludon",
        deck_csv_path=str(_SUBJECT_DECK),
        source_commit=commit,
        env_seed=seed,
        seat=0,
        behavior_kind="rule_agent",
        behavior_identity=rule_agent_behavior_identity_v1(),
        opponent_kind=opponent_kind,
    )
    return run_one_actor_game_v1(job=job, output_dir=tmp_path / opponent_kind)


# ---------------------------------------------------------------------------
# 1. 相手を変えると実際に別の方策が対局すること
# ---------------------------------------------------------------------------


def test_anti_canon_naming_a_different_opponent_changes_which_policy_plays(tmp_path: Path) -> None:
    """正典 §13 が要求する opponent league が実効を持つこと。

    正典 §13 は opponent instance を「deck hash、policy implementation / hash、
    policy type、source rank band、local strength band、sampling weight」を持つ
    独立した実体と定める。相手の deck だけが変わり policy が常に同一なら、
    strength band の分離も §14 の promotion gate も意味を持たない。

    名前の一覧ではなく、**同じ subject・同じ seed で相手だけを変えたときに
    ``opponent_version`` が実際に分かれるか**を検査する。過去の実装は
    ``agent_b_factory=None`` を渡し ``opponent_version`` に常に repo root の
    ``main.py`` の hash を入れていたため、この検査で落ちる。
    """
    results = {
        kind: _run_one_game(kind, tmp_path)
        for kind in ("cabt_rule_agent_v0", "nihei_megalopunny", "itsuki9180_lucario_jp")
    }
    for kind, result in results.items():
        assert result.status == "completed", f"{kind}: {result.fault}"

    versions = {kind: result.opponent_version for kind, result in results.items()}
    assert len(set(versions.values())) == len(versions), (
        "different opponent kinds recorded the same opponent_version "
        f"{versions!r}: naming a different opponent did not change which policy "
        "actually played. This is the exact failure mode where an expanded "
        "opponent name list is decorative because agent_b_factory stays None."
    )


# ---------------------------------------------------------------------------
# 2. 未登録の相手が self-mirror へ無言 fallback しないこと
# ---------------------------------------------------------------------------


def test_anti_canon_unregistered_opponent_fails_closed_instead_of_mirroring(tmp_path: Path) -> None:
    """正典 §5 の「不足した runtime ID は起動時に失敗させ、推測で補完しない」。

    過去の実装は相手の deck をディスク上で見つけられないと subject 自身の deck
    へ無言で fallback した。その結果、実体を持たない相手を名指しした job が
    self-mirror を回しながら、その相手と対戦したかのように記録されていた。
    """
    from mage_ptcg.meta_specialist.opponent_pool_v1 import OpponentPoolV1Error

    with pytest.raises(OpponentPoolV1Error):
        _run_one_game("an_opponent_that_is_not_registered", tmp_path)


def test_anti_canon_opponent_deck_is_never_defaulted_to_the_subject_deck() -> None:
    """相手 deck を subject deck へ束縛する式が復活していないこと。

    ``opp_deck = resolved or subject_deck`` の形は、実体が無いときに黙って
    self-mirror へ落ちる。

    検出するのは **availability fallback** だけである。すなわち
    ``x or subject`` と ``x if x else subject`` の形。``a if subject_first else b``
    のような席入れ替えは、test が分岐の値そのものではない別の bool なので
    除外する。両者を区別しないと、正当な seat swap を誤検出する。
    """
    tree = _actor_pool_ast()
    subject_names = {"deck_path_str", "deck_csv_path"}
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        fallback = None
        if isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
            fallback = value.values[-1]
        elif isinstance(value, ast.IfExp):
            # Only a truthiness fallback: the tested expression is the same
            # name as the "kept" branch (`x if x else subject`).
            test, body = value.test, value.body
            if isinstance(test, ast.Name) and isinstance(body, ast.Name) and test.id == body.id:
                fallback = value.orelse
        if isinstance(fallback, ast.Name) and fallback.id in subject_names:
            targets = ", ".join(t.id for t in node.targets if isinstance(t, ast.Name))
            violations.append(
                f"{_ACTOR_POOL_PATH.name}:{node.lineno}: {targets} falls back to {fallback.id}"
            )
    assert not violations, (
        "an opponent value silently defaults to the subject's own deck: " + "; ".join(violations)
    )


# ---------------------------------------------------------------------------
# 3. FoundationInit の provenance が rule v0 でないこと
# ---------------------------------------------------------------------------


def test_anti_canon_foundation_init_records_where_its_weights_came_from() -> None:
    """正典 §1 / §9.3 が要求する Foundation θ0 の出自記録。

    正典 §1 は curriculum の起点を Foundation θ0 と定め、§9.3 は teacher を
    manifest で固定することを求める。AGENTS.md は Rule Agent v0 を Promotion
    Gate なしに Champion へ昇格させないと定める。初期重みがどこから来たかを
    記録する仕組みが無ければ、「rule v0 の模倣に戻っていないこと」を検査でき
    ない。

    **この検査はソース中の文字列も、公開名の一覧も見ない。** 初版はソース中の
    文字列を見ていたため、キーが grep 対象の文字列そのもので参照ゼロの dict を
    追加するだけで通ってしまった。公開名の一覧を見るだけでも同じ穴が残る
    (適当な名前の定数を 1 つ足せば通る)。

    したがってここでは **checkpoint を実際に書いて読み戻し**、provenance が
    payload に載って往復すること、載っていない checkpoint が拒否されること、
    rule v0 を teacher に指定した θ0 が構築できないことを確かめる。
    """
    torch = pytest.importorskip("torch")

    from mage_ptcg.meta_specialist.foundation_init_v1 import (
        DERIVATION_QUALIFIED_V1,
        FoundationInitProvenanceV1,
        FoundationInitV1Error,
        INIT_KIND_BC_DISTILLED_V1,
        RULE_AGENT_V0_TEACHER_ID_V1,
        TeacherRefV1,
        assert_primary_teacher_is_not_rule_v0_v1,
        random_init_provenance_v1,
    )
    from mage_ptcg.meta_specialist.neural_checkpoint_v1 import (
        NeuralCheckpointV1Error,
        build_checkpoint_payload_v1,
        build_training_identity_v1,
    )
    from mage_ptcg.meta_specialist.neural_model_v1 import (
        SpecialistModelConfigV1,
        build_specialist_policy_model_v1,
    )

    config = SpecialistModelConfigV1(
        card_vocabulary_size=8, hidden_dim=8, card_dim=4, symbol_dim=2
    )
    model = build_specialist_policy_model_v1(config, seed=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
    recipe = {"kind": "anti-canon-probe"}
    identity = build_training_identity_v1(
        snapshot_id="anti-canon", config=config, recipe=recipe, seed=0
    )

    # 1. provenance が実際に payload へ載ること。
    payload = build_checkpoint_payload_v1(
        model=model, optimizer=optimizer, scheduler=None, identity=identity,
        recipe=recipe, step=0, sampler_cursor=0,
        foundation_init=random_init_provenance_v1(notes="anti-canon probe"),
    )
    stored = payload["metadata"]["foundation_init"]
    assert stored["init_kind"] == "random" and stored["teachers"] == [], (
        "the checkpoint did not record how θ0 was initialized: " f"{stored!r}"
    )

    # 2. provenance を省いた checkpoint は構築できないこと。省略できるなら、
    #    出自不明の重みが再び流通しうる。
    with pytest.raises(TypeError):
        build_checkpoint_payload_v1(  # type: ignore[call-arg]
            model=model, optimizer=optimizer, scheduler=None, identity=identity,
            recipe=recipe, step=0, sampler_cursor=0,
        )
    assert not isinstance(NeuralCheckpointV1Error, str)  # module import sanity

    # 3. 派生資格が未決の teacher からは θ0 を構築できないこと。
    unqualified = TeacherRefV1(
        teacher_id="some_pooled_agent", teacher_kind="external_submission_agent",
        policy_hash="0" * 64, usage_boundary="local_eval_only",
        derivation_boundary="derivation_unqualified", decision_ref="",
    )
    with pytest.raises(FoundationInitV1Error):
        FoundationInitProvenanceV1(
            init_kind=INIT_KIND_BC_DISTILLED_V1, teachers=(unqualified,),
            parent_checkpoint_sha256="", notes="",
        )

    # 4. Rule Agent v0 を teacher にした θ0 が拒否されること。
    rule_v0 = TeacherRefV1(
        teacher_id=RULE_AGENT_V0_TEACHER_ID_V1, teacher_kind="cabt_rule_agent",
        policy_hash="1" * 64, usage_boundary="internal_mirror",
        derivation_boundary=DERIVATION_QUALIFIED_V1, decision_ref="test",
    )
    with pytest.raises(FoundationInitV1Error):
        assert_primary_teacher_is_not_rule_v0_v1(
            FoundationInitProvenanceV1(
                init_kind=INIT_KIND_BC_DISTILLED_V1, teachers=(rule_v0,),
                parent_checkpoint_sha256="", notes="",
            )
        )


# ---------------------------------------------------------------------------
# 4. import されるだけで呼ばれないモジュールを作らないこと
# ---------------------------------------------------------------------------


def test_anti_canon_no_module_is_imported_without_being_called() -> None:
    """「配線した」を import 文の存在で主張できないこと。

    以前、孤立モジュール (importer 0 件) を解消したという報告があった。実際に
    行われていたのは ``import mage_ptcg.meta_specialist.census_v1 as census_v1``
    のような**未使用 import の追加**であり、``census_v1.<何か>`` を呼ぶコードは
    1 行も無かった。importer を数える検査は、この形で満たせてしまう。

    ここでは AST で「import しているのに、その名前で属性アクセスも呼び出しも
    しないモジュール」を検出する。テスト・スクリプト・``__init__`` は対象外で、
    ``src/mage_ptcg/meta_specialist`` の本番モジュール同士の関係だけを見る。
    """
    offenders: list[str] = []
    for path in sorted(_SRC_META_SPECIALIST.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        bound: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("mage_ptcg.meta_specialist."):
                        bound[alias.asname or alias.name.split(".")[-1]] = alias.name
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("mage_ptcg.meta_specialist"):
                    # `from x import name` binds the names themselves; those are
                    # used directly and are covered by ordinary name resolution.
                    continue

        if not bound:
            continue
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                used.add(node.value.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                used.add(node.func.id)
        for local_name, module in sorted(bound.items()):
            if local_name not in used:
                offenders.append(f"{path.name} imports {module} but never uses {local_name}")

    assert not offenders, (
        "these modules are imported without being called, which makes an "
        "'importers: 0' check pass without any real wiring:\n  "
        + "\n  ".join(offenders)
    )
