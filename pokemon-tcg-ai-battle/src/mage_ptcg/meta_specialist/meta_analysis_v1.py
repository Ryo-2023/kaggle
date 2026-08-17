"""正典 §2.4 の production メタレポートを sealed census から作る。

## 直した意味の取り違え

以前の版は Gold / Silver / Bronze を「データの信頼度」(Gold = 検証済み replay、
Silver = 自己対戦、Bronze = heuristic) と定義していた。正典での Gold / Silver /
Bronze は **提出元の Kaggle medal band** であり、正典 §13 は `source_rank_band`
(出所) と `local_strength_band` (実測強度) を明確に分離することを要求する。
出所を強度や信頼度として読み替えると、その分離が最初の集計段階で壊れる。
band はここでは出所のラベルとしてのみ扱い、強度を意味しない。

## 出力する粒度 (正典 §2.4)

band ごとに、team 数、coverage、未分類 / 複数候補分類率、観測デッキ比率と
bootstrap CI、archetype / support package / exact 60-card hash の三段階集計、
core / flex の採用率と枚数分布、集中度と HHI、band 内順位の感度分析を出す。
過去 snapshot との差分は **classifier version が一致する場合だけ**出す。

## 出さないもの

- matchup / seat matrix は、replay battle outcome と opponent policy provenance が
  揃う場合だけ意味を持つ (正典 §2.4)。揃わない入力から deck share だけで相性を
  推測しない。本 module は matrix を組み立てず、要求されたら不足を述べて失敗する。
- 数値は `MetaAnalysisManifest` と census hash を伴う。census hash 無しでは作れない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import random
from typing import Mapping, Sequence


META_ANALYSIS_SCHEMA_V1 = "meta-specialist-meta-analysis-v1"

SOURCE_RANK_BANDS_V1: tuple[str, ...] = ("Gold", "Silver", "Bronze")
RANK_STRATA_V1: tuple[str, ...] = ("upper", "middle", "lower")
CLASSIFICATION_KINDS_V1: tuple[str, ...] = ("classified", "unclassified", "multi_candidate")

# A card counts as `core` for an archetype when nearly every observed build of
# that archetype runs it.  The threshold is a reported constant rather than a
# hidden one, so a stored report can be checked against the rule that produced it.
CORE_ADOPTION_THRESHOLD_V1 = 0.90
# Deterministic bootstrap: the seed is part of the manifest, so an interval can
# be reproduced exactly instead of being re-drawn to a slightly different answer.
BOOTSTRAP_RESAMPLES_V1 = 2_000
BOOTSTRAP_SEED_V1 = 20260801
BOOTSTRAP_CONFIDENCE_V1 = 0.95


class MetaAnalysisV1Error(ValueError):
    """Raised when a meta report cannot be produced as specified."""


@dataclass(frozen=True, slots=True)
class DeckObservationV1:
    """One observed submission's deck, as classified from the sealed census.

    ``rank_within_band`` is 1-based and is what the §2.4 sensitivity analysis
    slices on.  ``card_counts`` maps card id to copies, which is what core/flex
    adoption and the count distribution are computed from.
    """

    submission_id: str
    source_rank_band: str
    rank_within_band: int
    archetype_id: str
    support_package_id: str
    exact_deck_hash: str
    card_counts: Mapping[int, int] = field(default_factory=dict)
    classification: str = "classified"
    score: float | None = None

    def __post_init__(self) -> None:
        if type(self.submission_id) is not str or not self.submission_id:
            raise MetaAnalysisV1Error("submission_id must be a nonempty string")
        if self.source_rank_band not in SOURCE_RANK_BANDS_V1:
            raise MetaAnalysisV1Error(
                f"source_rank_band must be one of {SOURCE_RANK_BANDS_V1}, "
                f"got {self.source_rank_band!r}. This is the Kaggle medal band of the "
                "source submission, not a measured strength (正典 §13)."
            )
        if type(self.rank_within_band) is not int or self.rank_within_band < 1:
            raise MetaAnalysisV1Error("rank_within_band must be a 1-based positive int")
        if self.classification not in CLASSIFICATION_KINDS_V1:
            raise MetaAnalysisV1Error(f"unknown classification {self.classification!r}")
        if self.classification == "classified":
            for name in ("archetype_id", "support_package_id", "exact_deck_hash"):
                if not getattr(self, name):
                    raise MetaAnalysisV1Error(
                        f"a classified observation needs {name}; leave it unclassified instead"
                    )

    def to_dict(self) -> dict[str, object]:
        return {
            "submission_id": self.submission_id,
            "source_rank_band": self.source_rank_band,
            "rank_within_band": self.rank_within_band,
            "archetype_id": self.archetype_id,
            "support_package_id": self.support_package_id,
            "exact_deck_hash": self.exact_deck_hash,
            "card_counts": {str(key): value for key, value in sorted(self.card_counts.items())},
            "classification": self.classification,
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class ShareIntervalV1:
    """One observed share with its bootstrap interval."""

    key: str
    count: int
    share: float
    ci_low: float
    ci_high: float

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key, "count": self.count, "share": self.share,
            "ci_low": self.ci_low, "ci_high": self.ci_high,
        }


@dataclass(frozen=True, slots=True)
class CardAdoptionV1:
    """One card's adoption within one archetype, and how many copies are run."""

    card_id: int
    adoption_rate: float
    count_distribution: Mapping[str, int]
    is_core: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "card_id": self.card_id, "adoption_rate": self.adoption_rate,
            "count_distribution": dict(self.count_distribution), "is_core": self.is_core,
        }


@dataclass(frozen=True, slots=True)
class ArchetypeCompositionV1:
    """Core / flex split for one archetype inside one band."""

    archetype_id: str
    decks: int
    core_card_ids: tuple[int, ...]
    flex_card_ids: tuple[int, ...]
    adoption: tuple[CardAdoptionV1, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "archetype_id": self.archetype_id,
            "decks": self.decks,
            "core_card_ids": list(self.core_card_ids),
            "flex_card_ids": list(self.flex_card_ids),
            "adoption": [item.to_dict() for item in self.adoption],
        }


@dataclass(frozen=True, slots=True)
class BandReportV1:
    """Everything §2.4 requires for one source rank band."""

    source_rank_band: str
    teams: int
    classified: int
    coverage: float
    unclassified_rate: float
    multi_candidate_rate: float
    archetype_shares: tuple[ShareIntervalV1, ...]
    support_package_shares: tuple[ShareIntervalV1, ...]
    exact_deck_shares: tuple[ShareIntervalV1, ...]
    compositions: tuple[ArchetypeCompositionV1, ...]
    archetype_hhi: float
    archetype_diversity: int
    top_exact_deck_share: float
    rank_sensitivity: Mapping[str, Mapping[str, float]]

    def to_dict(self) -> dict[str, object]:
        return {
            "source_rank_band": self.source_rank_band,
            "teams": self.teams,
            "classified": self.classified,
            "coverage": self.coverage,
            "unclassified_rate": self.unclassified_rate,
            "multi_candidate_rate": self.multi_candidate_rate,
            "archetype_shares": [item.to_dict() for item in self.archetype_shares],
            "support_package_shares": [item.to_dict() for item in self.support_package_shares],
            "exact_deck_shares": [item.to_dict() for item in self.exact_deck_shares],
            "compositions": [item.to_dict() for item in self.compositions],
            "archetype_hhi": self.archetype_hhi,
            "archetype_diversity": self.archetype_diversity,
            "top_exact_deck_share": self.top_exact_deck_share,
            "rank_sensitivity": {
                stratum: dict(shares) for stratum, shares in self.rank_sensitivity.items()
            },
        }


@dataclass(frozen=True, slots=True)
class HistoricalDiffV1:
    """Inflow / outflow against a prior snapshot classified the same way."""

    previous_manifest_id: str
    classifier_version: str
    inflow_archetypes: tuple[str, ...]
    outflow_archetypes: tuple[str, ...]
    share_deltas: Mapping[str, float]

    def to_dict(self) -> dict[str, object]:
        return {
            "previous_manifest_id": self.previous_manifest_id,
            "classifier_version": self.classifier_version,
            "inflow_archetypes": list(self.inflow_archetypes),
            "outflow_archetypes": list(self.outflow_archetypes),
            "share_deltas": dict(self.share_deltas),
        }


@dataclass(frozen=True, slots=True)
class MetaAnalysisManifestV1:
    """The §2.4 report. Carries the census hash it was computed from."""

    manifest_id: str
    census_id: str
    census_content_hash: str
    classifier_version: str
    bands: tuple[BandReportV1, ...]
    historical_diff: HistoricalDiffV1 | None
    rules: Mapping[str, object]

    def band(self, source_rank_band: str) -> BandReportV1:
        for report in self.bands:
            if report.source_rank_band == source_rank_band:
                return report
        raise MetaAnalysisV1Error(f"no report for band {source_rank_band!r}")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": META_ANALYSIS_SCHEMA_V1,
            "manifest_id": self.manifest_id,
            "census_id": self.census_id,
            "census_content_hash": self.census_content_hash,
            "classifier_version": self.classifier_version,
            "bands": [item.to_dict() for item in self.bands],
            "historical_diff": (
                self.historical_diff.to_dict() if self.historical_diff is not None else None
            ),
            "rules": dict(self.rules),
        }

    def content_hash(self) -> str:
        return hashlib.sha256(
            b"mage_ptcg:meta-analysis:v1\0"
            + json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


def _sealed_rules_v1() -> dict[str, object]:
    return {
        "core_adoption_threshold": CORE_ADOPTION_THRESHOLD_V1,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES_V1,
        "bootstrap_seed": BOOTSTRAP_SEED_V1,
        "bootstrap_confidence": BOOTSTRAP_CONFIDENCE_V1,
    }


def _bootstrap_share_interval_v1(
    labels: Sequence[str], key: str, *, seed: int
) -> tuple[float, float]:
    """Percentile bootstrap interval for one label's share of ``labels``.

    Resampling the observations (not the counts) makes this an interval on the
    share a *new* sample of the same size would show, which is what §2.4 asks
    for when it pairs an observed deck share with a CI.
    """
    total = len(labels)
    if total == 0:
        return 0.0, 0.0
    rng = random.Random(seed)
    shares: list[float] = []
    for _draw in range(BOOTSTRAP_RESAMPLES_V1):
        hits = 0
        for _pick in range(total):
            if labels[rng.randrange(total)] == key:
                hits += 1
        shares.append(hits / total)
    shares.sort()
    tail = (1.0 - BOOTSTRAP_CONFIDENCE_V1) / 2.0
    low_index = min(len(shares) - 1, max(0, int(math.floor(tail * len(shares)))))
    high_index = min(len(shares) - 1, max(0, int(math.ceil((1.0 - tail) * len(shares))) - 1))
    return shares[low_index], shares[high_index]


def _shares_v1(labels: Sequence[str], *, seed: int) -> tuple[ShareIntervalV1, ...]:
    total = len(labels)
    counts: dict[str, int] = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    rows: list[ShareIntervalV1] = []
    for offset, key in enumerate(sorted(counts)):
        low, high = _bootstrap_share_interval_v1(labels, key, seed=seed + offset)
        rows.append(ShareIntervalV1(
            key=key, count=counts[key], share=counts[key] / total if total else 0.0,
            ci_low=low, ci_high=high,
        ))
    # Most common first, ties broken by key so the order is stable.
    rows.sort(key=lambda item: (-item.count, item.key))
    return tuple(rows)


def _composition_v1(
    archetype_id: str, decks: Sequence[DeckObservationV1]
) -> ArchetypeCompositionV1:
    total = len(decks)
    card_ids = sorted({card for deck in decks for card in deck.card_counts})
    adoption: list[CardAdoptionV1] = []
    core: list[int] = []
    flex: list[int] = []
    for card_id in card_ids:
        running = [deck.card_counts.get(card_id, 0) for deck in decks]
        played = sum(1 for count in running if count > 0)
        rate = played / total
        distribution: dict[str, int] = {}
        for count in running:
            if count > 0:
                distribution[str(count)] = distribution.get(str(count), 0) + 1
        is_core = rate >= CORE_ADOPTION_THRESHOLD_V1
        (core if is_core else flex).append(card_id)
        adoption.append(CardAdoptionV1(
            card_id=card_id, adoption_rate=rate,
            count_distribution=dict(sorted(distribution.items())), is_core=is_core,
        ))
    return ArchetypeCompositionV1(
        archetype_id=archetype_id, decks=total,
        core_card_ids=tuple(core), flex_card_ids=tuple(flex), adoption=tuple(adoption),
    )


def _rank_sensitivity_v1(
    classified: Sequence[DeckObservationV1],
) -> dict[str, dict[str, float]]:
    """Archetype shares in the upper / middle / lower third of a band by rank.

    §2.4 asks for this because a band's aggregate share can be dominated by its
    long tail; an archetype that only appears near the top of Silver is a
    different observation from one spread evenly through it.
    """
    if not classified:
        return {stratum: {} for stratum in RANK_STRATA_V1}
    ordered = sorted(classified, key=lambda item: (item.rank_within_band, item.submission_id))
    total = len(ordered)
    strata = len(RANK_STRATA_V1)
    bounds = [round(total * (index + 1) / strata) for index in range(strata)]
    sensitivity: dict[str, dict[str, float]] = {}
    start = 0
    for stratum, end in zip(RANK_STRATA_V1, bounds):
        slice_ = ordered[start:end]
        start = end
        counts: dict[str, int] = {}
        for item in slice_:
            counts[item.archetype_id] = counts.get(item.archetype_id, 0) + 1
        sensitivity[stratum] = (
            {key: counts[key] / len(slice_) for key in sorted(counts)} if slice_ else {}
        )
    return sensitivity


def build_band_report_v1(
    source_rank_band: str, observations: Sequence[DeckObservationV1]
) -> BandReportV1:
    """Aggregate one band at the three levels §2.4 names."""
    if source_rank_band not in SOURCE_RANK_BANDS_V1:
        raise MetaAnalysisV1Error(f"unknown source rank band {source_rank_band!r}")
    rows = [item for item in observations if item.source_rank_band == source_rank_band]
    if not rows:
        raise MetaAnalysisV1Error(
            f"band {source_rank_band!r} has no observation; an absent band is reported as "
            "absent by the caller rather than as a zero-share meta"
        )
    teams = len(rows)
    classified = [item for item in rows if item.classification == "classified"]
    unclassified = sum(1 for item in rows if item.classification == "unclassified")
    multi = sum(1 for item in rows if item.classification == "multi_candidate")

    seed = BOOTSTRAP_SEED_V1 + SOURCE_RANK_BANDS_V1.index(source_rank_band) * 1_000
    archetype_shares = _shares_v1([item.archetype_id for item in classified], seed=seed)
    package_shares = _shares_v1(
        [item.support_package_id for item in classified], seed=seed + 100
    )
    exact_shares = _shares_v1([item.exact_deck_hash for item in classified], seed=seed + 200)

    by_archetype: dict[str, list[DeckObservationV1]] = {}
    for item in classified:
        by_archetype.setdefault(item.archetype_id, []).append(item)
    compositions = tuple(
        _composition_v1(key, by_archetype[key]) for key in sorted(by_archetype)
    )
    hhi = math.fsum(row.share ** 2 for row in archetype_shares)
    return BandReportV1(
        source_rank_band=source_rank_band,
        teams=teams,
        classified=len(classified),
        coverage=len(classified) / teams,
        unclassified_rate=unclassified / teams,
        multi_candidate_rate=multi / teams,
        archetype_shares=archetype_shares,
        support_package_shares=package_shares,
        exact_deck_shares=exact_shares,
        compositions=compositions,
        archetype_hhi=hhi,
        archetype_diversity=len(archetype_shares),
        top_exact_deck_share=exact_shares[0].share if exact_shares else 0.0,
        rank_sensitivity=_rank_sensitivity_v1(classified),
    )


def build_historical_diff_v1(
    *,
    previous: MetaAnalysisManifestV1,
    current_bands: Sequence[BandReportV1],
    classifier_version: str,
) -> HistoricalDiffV1:
    """Inflow / outflow against a prior snapshot, refusing a classifier mismatch.

    §2.4 permits a historical comparison only when the classifier version is
    aligned.  A share that moved because the classifier changed is not a meta
    shift, and presenting it as one is the error this refusal exists to prevent.
    """
    if previous.classifier_version != classifier_version:
        raise MetaAnalysisV1Error(
            f"previous snapshot was classified by {previous.classifier_version!r} but this "
            f"one by {classifier_version!r}; §2.4 requires the classifier version to be "
            "aligned before a historical difference is reported"
        )
    before: dict[str, float] = {}
    for report in previous.bands:
        for row in report.archetype_shares:
            before[f"{report.source_rank_band}/{row.key}"] = row.share
    after: dict[str, float] = {}
    for report in current_bands:
        for row in report.archetype_shares:
            after[f"{report.source_rank_band}/{row.key}"] = row.share
    deltas = {
        key: after.get(key, 0.0) - before.get(key, 0.0)
        for key in sorted(set(before) | set(after))
    }
    return HistoricalDiffV1(
        previous_manifest_id=previous.manifest_id,
        classifier_version=classifier_version,
        inflow_archetypes=tuple(sorted(set(after) - set(before))),
        outflow_archetypes=tuple(sorted(set(before) - set(after))),
        share_deltas=deltas,
    )


def build_meta_analysis_manifest_v1(
    *,
    manifest_id: str,
    census_id: str,
    census_content_hash: str,
    classifier_version: str,
    observations: Sequence[DeckObservationV1],
    previous: MetaAnalysisManifestV1 | None = None,
) -> MetaAnalysisManifestV1:
    """Build the §2.4 report for every band present in ``observations``."""
    for name, value in (
        ("manifest_id", manifest_id),
        ("census_id", census_id),
        ("census_content_hash", census_content_hash),
        ("classifier_version", classifier_version),
    ):
        if type(value) is not str or not value:
            raise MetaAnalysisV1Error(
                f"{name} must be a nonempty string; §2.4 does not treat a number as a "
                "current fact unless it carries the census hash it came from"
            )
    if not observations:
        raise MetaAnalysisV1Error("a meta report needs at least one observation")
    present = [
        band for band in SOURCE_RANK_BANDS_V1
        if any(item.source_rank_band == band for item in observations)
    ]
    bands = tuple(build_band_report_v1(band, observations) for band in present)
    diff = (
        None if previous is None
        else build_historical_diff_v1(
            previous=previous, current_bands=bands, classifier_version=classifier_version,
        )
    )
    return MetaAnalysisManifestV1(
        manifest_id=manifest_id,
        census_id=census_id,
        census_content_hash=census_content_hash,
        classifier_version=classifier_version,
        bands=bands,
        historical_diff=diff,
        rules=_sealed_rules_v1(),
    )


def matchup_matrix_v1(*, replay_outcomes: object, opponent_provenance: object) -> None:
    """Refuse to build a matchup matrix without both of its preconditions.

    §2.4 allows a matchup / seat matrix only when replay battle outcomes *and*
    opponent policy provenance are both available, and explicitly forbids
    inferring matchup causality from deck share.  This function exists so that a
    caller asking for the matrix gets the missing precondition named, rather than
    a plausible matrix computed from shares.
    """
    missing = [
        name for name, value in
        (("replay_outcomes", replay_outcomes), ("opponent_provenance", opponent_provenance))
        if not value
    ]
    raise MetaAnalysisV1Error(
        "a matchup / seat matrix needs replay battle outcomes and opponent policy "
        f"provenance together; missing: {missing or ['(not implemented)']}. "
        "§2.4 forbids inferring matchup causality from deck share."
    )


def render_markdown_report_v1(manifest: MetaAnalysisManifestV1) -> str:
    """The human-readable half of §2.4's "machine-readable and Markdown both"."""
    if type(manifest) is not MetaAnalysisManifestV1:
        raise MetaAnalysisV1Error("manifest must be a MetaAnalysisManifestV1")
    lines = [
        f"# メタレポート {manifest.manifest_id}",
        "",
        f"- census: `{manifest.census_id}` (`{manifest.census_content_hash[:16]}`)",
        f"- classifier: `{manifest.classifier_version}`",
        f"- core 判定閾値: 採用率 {CORE_ADOPTION_THRESHOLD_V1:.0%} 以上",
        "",
    ]
    for report in manifest.bands:
        lines += [
            f"## {report.source_rank_band}",
            "",
            f"team {report.teams} / 分類済み {report.classified} "
            f"(coverage {report.coverage:.1%}、未分類 {report.unclassified_rate:.1%}、"
            f"複数候補 {report.multi_candidate_rate:.1%})",
            "",
            "| archetype | 件数 | 比率 | bootstrap 95% CI |",
            "|---|---:|---:|---|",
        ]
        for row in report.archetype_shares:
            lines.append(
                f"| {row.key} | {row.count} | {row.share:.1%} | "
                f"[{row.ci_low:.1%}, {row.ci_high:.1%}] |"
            )
        lines += [
            "",
            f"archetype 多様性 {report.archetype_diversity}、HHI {report.archetype_hhi:.3f}、"
            f"最頻 exact deck 比率 {report.top_exact_deck_share:.1%}",
            "",
        ]
    if manifest.historical_diff is not None:
        diff = manifest.historical_diff
        lines += [
            "## 過去 snapshot との差分",
            "",
            f"- 比較対象: `{diff.previous_manifest_id}` (classifier `{diff.classifier_version}`)",
            f"- 流入: {list(diff.inflow_archetypes) or 'なし'}",
            f"- 流出: {list(diff.outflow_archetypes) or 'なし'}",
            "",
        ]
    return "\n".join(lines)


__all__ = [
    "BOOTSTRAP_CONFIDENCE_V1",
    "BOOTSTRAP_RESAMPLES_V1",
    "BOOTSTRAP_SEED_V1",
    "CLASSIFICATION_KINDS_V1",
    "CORE_ADOPTION_THRESHOLD_V1",
    "META_ANALYSIS_SCHEMA_V1",
    "RANK_STRATA_V1",
    "SOURCE_RANK_BANDS_V1",
    "ArchetypeCompositionV1",
    "BandReportV1",
    "CardAdoptionV1",
    "DeckObservationV1",
    "HistoricalDiffV1",
    "MetaAnalysisManifestV1",
    "MetaAnalysisV1Error",
    "ShareIntervalV1",
    "build_band_report_v1",
    "build_historical_diff_v1",
    "build_meta_analysis_manifest_v1",
    "matchup_matrix_v1",
    "render_markdown_report_v1",
]
