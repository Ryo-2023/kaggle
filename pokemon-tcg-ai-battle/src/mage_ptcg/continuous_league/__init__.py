"""継続学習とオフラインリーグ評価を接続する公開 API。"""

from .benchmark import (
    BenchmarkManifest,
    ExposureCohort,
    ExposureSnapshot,
    ScheduledGame,
    build_schedule,
)
from .catalog import CatalogEntry, CatalogSnapshot
from .contracts import LeagueContractError

__all__ = [
    "BenchmarkManifest",
    "CatalogEntry",
    "CatalogSnapshot",
    "ExposureCohort",
    "ExposureSnapshot",
    "LeagueContractError",
    "ScheduledGame",
    "build_schedule",
]
