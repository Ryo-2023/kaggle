"""FoundationInit の重み provenance と teacher の派生資格。

正典 §1 は curriculum の起点を Foundation θ0 と定め、§9.3 は teacher dataset を
manifest で固定することを求める。AGENTS.md は Rule Agent v0 を Promotion Gate
なしに Champion へ昇格させない比較対象・fallback に限定する。

この module が答える問いは 2 つある。

1. **この重みはどこから来たか。** 乱数初期化か、どの teacher からの BC 蒸留か、
   どの親 checkpoint からの warm start か。checkpoint payload に載せ、
   後から「rule v0 の模倣に戻っていないこと」を検査できるようにする。
2. **その teacher から派生物を作ってよいか。** pool の asset は全て
   ``local_eval_only`` であり、各 ``SOURCE.md`` が再配布と as-is 提出を禁じて
   いる。対戦相手として実行することと、その挙動を蒸留した重みを提出物へ
   載せることは別の判断である。正典 §5 は「派生 checkpoint の扱いも source
   policy と競技規約を qualification で判定する」と定める。

## なぜ fail-closed か

以前この repository では、テストが grep する文字列をキーに持つだけの参照ゼロの
dict (``FOUNDATION_INIT_PROVENANCE_V1``) を追加することで provenance 検査が
通ってしまった。値はハードコードされた ``False``/``None`` で、実際の初期化
経路とは何の関係も無かった。

したがってここでは、既定を「不明」ではなく **拒否** にする。teacher の派生
資格が明示的に記録されていない限り、その teacher を使う FoundationInit は
構築できない。判断を保留したまま学習を進める経路を作らない。
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from mage_ptcg.meta_specialist.local_dataset_v2 import canonical_json_bytes_v2


FOUNDATION_INIT_SCHEMA_V1 = "specialist-foundation-init-v1"

# How θ0 was produced.
INIT_KIND_RANDOM_V1 = "random"
INIT_KIND_BC_DISTILLED_V1 = "bc_distilled"
INIT_KIND_WARM_START_V1 = "warm_start"
_INIT_KINDS_V1 = frozenset({INIT_KIND_RANDOM_V1, INIT_KIND_BC_DISTILLED_V1, INIT_KIND_WARM_START_V1})

# Whether a teacher may produce derived weights that ship in a submission.
# `unqualified` is the default and is refused: an undecided licence question is
# not the same as a permissive answer.
DERIVATION_QUALIFIED_V1 = "derivation_qualified"
DERIVATION_FORBIDDEN_V1 = "derivation_forbidden"
DERIVATION_UNQUALIFIED_V1 = "derivation_unqualified"
_DERIVATION_BOUNDARIES_V1 = frozenset(
    {DERIVATION_QUALIFIED_V1, DERIVATION_FORBIDDEN_V1, DERIVATION_UNQUALIFIED_V1}
)

# Rule Agent v0.  Named so `assert_primary_teacher_is_not_rule_v0_v1` can state
# the canon rule directly rather than through a substring match on source code.
RULE_AGENT_V0_TEACHER_ID_V1 = "cabt_rule_agent_v0"


class FoundationInitV1Error(ValueError):
    """Raised when θ0's provenance is missing, malformed, or unqualified."""


@dataclass(frozen=True, slots=True)
class TeacherRefV1:
    """One teacher that contributed to θ0, with its derivation decision."""

    teacher_id: str
    teacher_kind: str
    policy_hash: str
    usage_boundary: str
    derivation_boundary: str
    decision_ref: str

    def __post_init__(self) -> None:
        for name in ("teacher_id", "teacher_kind", "usage_boundary", "derivation_boundary"):
            if not getattr(self, name):
                raise FoundationInitV1Error(f"{name} must be non-empty")
        if self.derivation_boundary not in _DERIVATION_BOUNDARIES_V1:
            raise FoundationInitV1Error(
                f"derivation_boundary must be one of {sorted(_DERIVATION_BOUNDARIES_V1)}"
            )
        if self.derivation_boundary != DERIVATION_UNQUALIFIED_V1 and not self.decision_ref:
            raise FoundationInitV1Error(
                f"{self.teacher_id}: a decided derivation_boundary needs a decision_ref "
                "(where the qualification decision is recorded)"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "teacher_id": self.teacher_id,
            "teacher_kind": self.teacher_kind,
            "policy_hash": self.policy_hash,
            "usage_boundary": self.usage_boundary,
            "derivation_boundary": self.derivation_boundary,
            "decision_ref": self.decision_ref,
        }


@dataclass(frozen=True, slots=True)
class FoundationInitProvenanceV1:
    """Where θ0's weights came from.  Carried in every checkpoint's metadata."""

    init_kind: str
    teachers: tuple[TeacherRefV1, ...]
    parent_checkpoint_sha256: str
    notes: str

    def __post_init__(self) -> None:
        if self.init_kind not in _INIT_KINDS_V1:
            raise FoundationInitV1Error(f"init_kind must be one of {sorted(_INIT_KINDS_V1)}")
        if self.init_kind == INIT_KIND_RANDOM_V1 and self.teachers:
            raise FoundationInitV1Error("a random init cannot name teachers")
        if self.init_kind == INIT_KIND_BC_DISTILLED_V1 and not self.teachers:
            raise FoundationInitV1Error("a bc_distilled init must name at least one teacher")
        if self.init_kind == INIT_KIND_WARM_START_V1 and not self.parent_checkpoint_sha256:
            raise FoundationInitV1Error("a warm_start init must name its parent checkpoint")
        for teacher in self.teachers:
            if teacher.derivation_boundary != DERIVATION_QUALIFIED_V1:
                raise FoundationInitV1Error(
                    f"{teacher.teacher_id}: derivation_boundary is "
                    f"{teacher.derivation_boundary!r}; only "
                    f"{DERIVATION_QUALIFIED_V1!r} may contribute to θ0. An "
                    "undecided licence question is refused, not assumed permissive."
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": FOUNDATION_INIT_SCHEMA_V1,
            "init_kind": self.init_kind,
            "teachers": [teacher.to_dict() for teacher in self.teachers],
            "parent_checkpoint_sha256": self.parent_checkpoint_sha256,
            "notes": self.notes,
        }

    def foundation_init_id(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:specialist-foundation-init:v1\0"
            + canonical_json_bytes_v2(self.to_dict())
        ).hexdigest()

    @property
    def is_rule_v0_derived(self) -> bool:
        return any(t.teacher_id == RULE_AGENT_V0_TEACHER_ID_V1 for t in self.teachers)


def random_init_provenance_v1(*, notes: str = "") -> FoundationInitProvenanceV1:
    """θ0 = independent random initialization, no teacher.

    This is an honest label, not a placeholder: a run that has not been
    distilled from anything should say so, so that "the policy converged to a
    sharpened copy of the behaviour policy" stays diagnosable afterwards.
    """
    return FoundationInitProvenanceV1(
        init_kind=INIT_KIND_RANDOM_V1,
        teachers=(),
        parent_checkpoint_sha256="",
        notes=notes,
    )


def qualify_pooled_teacher_v1(
    instance: Any,
    *,
    derivation_boundary: str,
    decision_ref: str,
) -> TeacherRefV1:
    """Turn one ``OpponentInstanceV1`` into a teacher reference.

    ``derivation_boundary`` is *supplied by the caller*, not inferred: whether a
    pooled agent's behaviour may be distilled into shipped weights is a licence
    and competition-rules judgement (正典 §5), and this module must not make it
    silently.  Passing ``DERIVATION_UNQUALIFIED_V1`` is allowed here so the
    undecided state can be recorded and inspected -- but
    ``FoundationInitProvenanceV1`` will then refuse to build.
    """
    return TeacherRefV1(
        teacher_id=getattr(instance, "opponent_id", ""),
        teacher_kind=getattr(instance, "policy_type", "") or "pooled_agent",
        policy_hash=getattr(instance, "policy_hash", ""),
        usage_boundary=getattr(instance, "usage_boundary", ""),
        derivation_boundary=derivation_boundary,
        decision_ref=decision_ref,
    )


def assert_primary_teacher_is_not_rule_v0_v1(provenance: FoundationInitProvenanceV1) -> None:
    """Rule Agent v0 must not be θ0's teacher.

    AGENTS.md pins Rule Agent v0 as a comparison target and emergency fallback,
    not a source of the champion lineage.  A θ0 distilled from it reproduces the
    measured failure where the learned policy becomes a sharpened copy of the
    rule agent and inherits its ceiling.
    """
    if provenance.is_rule_v0_derived:
        raise FoundationInitV1Error(
            f"{RULE_AGENT_V0_TEACHER_ID_V1} is named as a FoundationInit teacher. "
            "Rule Agent v0 is a comparison target and fallback, not the origin of "
            "the champion lineage."
        )


def parse_foundation_init_provenance_v1(value: Mapping[str, Any]) -> FoundationInitProvenanceV1:
    """Rebuild provenance from a checkpoint's metadata, validating it again."""
    if not isinstance(value, Mapping):
        raise FoundationInitV1Error("foundation_init must be a mapping")
    if value.get("schema_version") != FOUNDATION_INIT_SCHEMA_V1:
        raise FoundationInitV1Error("unsupported foundation_init schema_version")
    raw_teachers = value.get("teachers", ())
    if not isinstance(raw_teachers, (list, tuple)):
        raise FoundationInitV1Error("foundation_init.teachers must be a sequence")
    teachers = tuple(
        TeacherRefV1(
            teacher_id=str(entry.get("teacher_id", "")),
            teacher_kind=str(entry.get("teacher_kind", "")),
            policy_hash=str(entry.get("policy_hash", "")),
            usage_boundary=str(entry.get("usage_boundary", "")),
            derivation_boundary=str(entry.get("derivation_boundary", "")),
            decision_ref=str(entry.get("decision_ref", "")),
        )
        for entry in raw_teachers
    )
    return FoundationInitProvenanceV1(
        init_kind=str(value.get("init_kind", "")),
        teachers=teachers,
        parent_checkpoint_sha256=str(value.get("parent_checkpoint_sha256", "")),
        notes=str(value.get("notes", "")),
    )


__all__ = [
    "DERIVATION_FORBIDDEN_V1",
    "DERIVATION_QUALIFIED_V1",
    "DERIVATION_UNQUALIFIED_V1",
    "FOUNDATION_INIT_SCHEMA_V1",
    "INIT_KIND_BC_DISTILLED_V1",
    "INIT_KIND_RANDOM_V1",
    "INIT_KIND_WARM_START_V1",
    "RULE_AGENT_V0_TEACHER_ID_V1",
    "FoundationInitProvenanceV1",
    "FoundationInitV1Error",
    "TeacherRefV1",
    "assert_primary_teacher_is_not_rule_v0_v1",
    "parse_foundation_init_provenance_v1",
    "qualify_pooled_teacher_v1",
    "random_init_provenance_v1",
]
