"""Small metric container shared by CPU and central-GPU inference benchmarks."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(slots=True)
class InferenceMetrics:
    decisions: int = 0; faults: int = 0; timeouts: int = 0; illegal: int = 0; elapsed_seconds: float = 0.0; peak_vram_bytes: int = 0
    def document(self) -> dict[str, Any]:
        result = asdict(self); result["decisions_per_second"] = self.decisions / self.elapsed_seconds if self.elapsed_seconds else 0.0; return result
