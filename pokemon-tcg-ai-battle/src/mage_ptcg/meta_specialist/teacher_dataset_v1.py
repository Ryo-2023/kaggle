"""外部 teacher の決定を、既存の local record schema の teacher section へ載せる。

正典 §9.3 (教師データの重み) に対応する。

## この module が足すもの / 足さないもの

``local_dataset_v2`` は既に正典 §9.3 の teacher record を完全に実装している。
``target_kind ∈ {hard_selection, visit_count, probability_mass}``、``(0,1]`` の
``quality_weight``、``[-1,1]`` の ``value_target``、``mass_rows`` が到達可能な
complete action であることの検証まで揃っている。**新しい dataset schema は作らない。**

欠けていたのは 1 点だけである。外部 teacher (提出 agent の形をした rule / search
agent) は engine へ **CABT option index** を返すのに対し、record は
``local_action_id`` の列を要求する。その逆写像がどこにも無かったため、
外部 teacher の決定を教師データにできなかった。

この module はその逆写像と、``hard_selection`` teacher payload の組み立てだけを
提供する。永続化・検証は ``build_local_record_v2`` に任せる。

## なぜ V-trace 軌跡として記録しないか

``TrajectoryPrefixStepV1`` は ``behavior_log_probability`` を持ち、
``local_dataset_v2`` の検証器はその値が記録済み logits から再計算した値と
``abs_tol=1e-12`` で一致することを要求する。rule / search teacher は我々の
action 空間上の分布を持たないため、この不変条件を満たす値を原理的に作れない。

0.0 を入れれば「一様分布から引いた」という嘘になり、現 model で計算し直した値を
入れれば「behavior は teacher なのに learner の確率を behavior として記録する」
という嘘になる。後者は V-trace ratio を無意味にする。正典 §10.3 は
「V-trace ratio が補正するのは subject behavior と learner policy の差だけ」と
定める。

したがって teacher decision は importance ratio を持たない **BC / ExIt の policy
target** として teacher section へ入れる。これは schema を通すための妥協ではなく、
正典どおりの区別である。

## 非公開情報境界

逆写像は observation から作った actor-visible な envelope だけを使う (正典 §9.2)。
teacher の identity と相手の真の deck / policy hash は record の ``source`` /
``provenance`` にだけ置き、decision feature へは渡さない。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


# The bounded exact enumeration used to invert "which complete action did the
# teacher pick?".  正典 §8.3 は完全列挙が上限を超える schema について
# 「beam search や疑似 micro-step を黙って混ぜず、同一 contract で実行不能と
# 報告する」ことを求める。したがって超過は例外であり、近似で埋めない。
DEFAULT_ENUMERATION_LIMIT_V1 = 4096

# 正典 §9.3: 「rule / checkpoint demonstration は action legality、teacher
# confidence、重複 cap、matchup cap で重み付けする」。単一の hard selection には
# 探索の visit count のような内在的な confidence が無いため、既定を 1.0 とし、
# 重み付けは収集側 (重複 cap / matchup cap) の責務とする。
DEFAULT_HARD_SELECTION_QUALITY_WEIGHT_V1 = 1.0


class TeacherDatasetV1Error(ValueError):
    """Raised when a teacher decision cannot be represented exactly."""


class TeacherActionNotEnumerableV1Error(TeacherDatasetV1Error):
    """The teacher's committed action is not in the bounded exact enumeration.

    Distinguished from a generic error so a caller can report "this decision is
    not expressible under the current action contract" separately from "the
    teacher played badly" -- 正典 §8.3 の「性能負け」と「同一 contract で実行
    不能」の区別に対応する。
    """


def invert_teacher_option_indices_v1(
    envelope: Any,
    option_indices: Sequence[int],
    *,
    enumeration_limit: int = DEFAULT_ENUMERATION_LIMIT_V1,
) -> tuple[str, ...]:
    """Map the CABT option indices a teacher returned back to ``local_action_id``s.

    The returned tuple is exactly the ``selection`` that ``build_local_record_v2``
    expects, so a teacher's committed action becomes a record through the same
    validated path as any other decision.

    Exact and bounded.  The enumeration raises when it would exceed
    ``enumeration_limit`` rather than approximating, and indices matching no
    enumerated action raise ``TeacherActionNotEnumerableV1Error`` rather than
    snapping to the nearest candidate -- snapping would fabricate a target the
    teacher never chose.
    """
    from mage_ptcg.meta_specialist.runtime_actions_v2 import (
        RuntimeEnumerationError,
        enumerate_runtime_complete_actions_v2,
    )

    try:
        candidates = enumerate_runtime_complete_actions_v2(envelope, limit=enumeration_limit)
    except RuntimeEnumerationError as exc:
        raise TeacherActionNotEnumerableV1Error(
            f"complete-action enumeration exceeded the limit: {exc}"
        ) from exc

    ordered = envelope._order_semantics == "ordered_sequence"
    wanted = tuple(int(index) for index in option_indices)
    wanted_key = wanted if ordered else tuple(sorted(wanted))
    for candidate in candidates:
        got = tuple(int(index) for index in candidate.option_indices)
        got_key = got if ordered else tuple(sorted(got))
        if got_key == wanted_key:
            return tuple(candidate.local_action_ids)
    raise TeacherActionNotEnumerableV1Error(
        f"the teacher's option indices {wanted!r} match none of the "
        f"{len(candidates)} enumerated complete actions"
    )


def hard_selection_teacher_payload_v1(
    *,
    teacher_id: str,
    teacher_revision: str,
    model_input_id: str,
    decision_id: str,
    information_state: Mapping[str, Any],
    selection: Sequence[str],
    quality_weight: float = DEFAULT_HARD_SELECTION_QUALITY_WEIGHT_V1,
    value_target: float | None = None,
) -> dict[str, Any]:
    """Build the ``teacher`` section for one committed external-teacher decision.

    ``target_kind="hard_selection"`` is the canon's category for a rule or
    checkpoint demonstration: exactly one complete action with unit weight
    (正典 §9.3).  ``value_target`` carries win/draw/loss when the episode
    outcome is known; the canon uses outcome for value/return targets while
    keeping loss decisions at nonzero weight for policy imitation, so callers
    must not drop losing games here.
    """
    from mage_ptcg.meta_specialist.local_dataset_v2 import derive_complete_action_id_v1

    selected = tuple(selection)
    minimum = information_state["min_count"]
    if not selected and minimum > 0:
        raise TeacherDatasetV1Error(
            f"the teacher committed an empty selection but min_count is {minimum}"
        )
    # An empty selection is legitimate when ``min_count == 0``: "select nothing"
    # is itself a legal complete action there, and dropping those decisions
    # would silently bias the dataset away from the teacher's choice to decline.
    complete_action_id = derive_complete_action_id_v1(
        decision_id=decision_id,
        selection_type=information_state["selection_type"],
        selection_context=information_state["selection_context"],
        selection=selected,
    )
    return {
        "status": "available",
        "teacher_id": teacher_id,
        "teacher_revision": teacher_revision,
        "input_id": model_input_id,
        "target_kind": "hard_selection",
        "quality_weight": float(quality_weight),
        "value_target": value_target,
        "mass_rows": [
            {
                "complete_action_id": complete_action_id,
                "selection": list(selected),
                "weight": 1,
            }
        ],
    }


def unavailable_teacher_payload_v1(reason: str) -> dict[str, Any]:
    """The ``teacher`` section for a decision the teacher could not label.

    Recording the reason keeps an unlabelled decision distinguishable from a
    decision that was never seen, which matters when auditing coverage: 正典
    §9.3 は「複数選択を理由に game または episode を黙って除外しない」と定める。
    """
    return {"status": "unavailable", "reason": reason}


__all__ = [
    "DEFAULT_ENUMERATION_LIMIT_V1",
    "DEFAULT_HARD_SELECTION_QUALITY_WEIGHT_V1",
    "TeacherActionNotEnumerableV1Error",
    "TeacherDatasetV1Error",
    "hard_selection_teacher_payload_v1",
    "invert_teacher_option_indices_v1",
    "unavailable_teacher_payload_v1",
]
