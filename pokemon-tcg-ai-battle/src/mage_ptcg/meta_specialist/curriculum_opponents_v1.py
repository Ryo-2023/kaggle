"""curriculum の phase mixture を、実在する相手の選択へ変換する。

正典 §13 が要求するのは「`local_strength_band` を curriculum の primary sampling
key にする」ことである。`curriculum_v1` は band ごとの割当 (mixture / quota) を
決めるが相手を知らず、`opponent_pool_v1` は相手を知るが band を知らない。両者を
繋ぐのがこの module であり、意図的にどちらにも寄せていない。

## fail-closed の方針

- **band 未確定の相手を使わない。** `calibrate_opponent_v1` が `ambiguous` を返す
  のは証拠不足の表明であり、そこへ勝手に band を与えると curriculum が測っていない
  強度で層化される。
- **quota を満たせない band があれば失敗する。** 足りない分を他の band で埋めると、
  「top_focus を走らせた」と記録しながら実際は lower 中心、という最も気付きにくい
  ずれになる。呼び出し側が局数か panel を変えるべきであり、この層が黙って調整しない。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from mage_ptcg.meta_specialist.curriculum_v1 import (
    BANDS_V1,
    CurriculumPhaseV1,
    opponent_quota_v1,
)


class CurriculumOpponentsV1Error(ValueError):
    """Raised when a phase's quota cannot be met by the banded pool."""


def band_map_from_manifest_v1(manifest: Mapping[str, Any]) -> dict[str, str]:
    """Read ``opponent_id -> band`` from a local strength manifest.

    Only opponents with a decided band are returned.  ``ambiguous`` and
    unbanded entries are dropped here rather than defaulted, so a caller that
    needs them will hit the shortfall check instead of silently training
    against unmeasured strength.
    """
    calibrations = manifest.get("calibrations")
    if not isinstance(calibrations, (list, tuple)):
        raise CurriculumOpponentsV1Error("manifest has no 'calibrations' sequence")
    band_map: dict[str, str] = {}
    for item in calibrations:
        if not isinstance(item, Mapping):
            raise CurriculumOpponentsV1Error("every calibration entry must be a mapping")
        band = item.get("band")
        opponent_id = item.get("opponent_id")
        if not opponent_id:
            raise CurriculumOpponentsV1Error("a calibration entry is missing opponent_id")
        if band in BANDS_V1:
            band_map[str(opponent_id)] = str(band)
    return band_map


def opponents_by_band_v1(
    band_map: Mapping[str, str], *, available: Sequence[str]
) -> dict[str, list[str]]:
    """Group the available opponents by their measured band, in stable order."""
    grouped: dict[str, list[str]] = {band: [] for band in BANDS_V1}
    for opponent_id in sorted(available):
        band = band_map.get(opponent_id)
        if band in grouped:
            grouped[band].append(opponent_id)
    return grouped


def select_phase_opponents_v1(
    phase: CurriculumPhaseV1,
    *,
    band_map: Mapping[str, str],
    available: Sequence[str],
    games: int,
) -> tuple[str, ...]:
    """Expand one phase's mixture into a concrete per-game opponent sequence.

    The returned tuple has exactly ``games`` entries; index ``i`` is the
    opponent for game ``i``.  Within a band the opponents cycle in sorted order,
    so a band with several members is covered evenly rather than concentrating
    on whichever one sorts first.

    Raises when a band's quota is nonzero but no opponent carries that band --
    see the module docstring on why this is not silently rebalanced.
    """
    quota = opponent_quota_v1(phase, games=games)
    grouped = opponents_by_band_v1(band_map, available=available)

    missing = [band for band, count in quota.items() if count > 0 and not grouped[band]]
    if missing:
        raise CurriculumOpponentsV1Error(
            f"phase {phase.phase!r} needs games in band(s) {missing} but no calibrated "
            f"opponent carries them. Banded pool sizes: "
            f"{ {band: len(ids) for band, ids in grouped.items()} }. "
            "Calibrate more opponents or lower the game count; the quota is not rebalanced."
        )

    schedule: list[str] = []
    for band in BANDS_V1:
        members = grouped[band]
        for index in range(quota[band]):
            schedule.append(members[index % len(members)])
    if len(schedule) != games:
        raise CurriculumOpponentsV1Error(
            f"expanded {len(schedule)} opponents for {games} games"
        )
    return tuple(schedule)


__all__ = [
    "CurriculumOpponentsV1Error",
    "band_map_from_manifest_v1",
    "opponents_by_band_v1",
    "select_phase_opponents_v1",
]
