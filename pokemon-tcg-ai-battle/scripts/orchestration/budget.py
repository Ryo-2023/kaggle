"""Durable budget counters and fail-closed pre-operation guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


class BudgetExceeded(RuntimeError):
    """Raised before an operation that would exceed a declared limit."""


@dataclass
class Budget:
    limits: dict[str, int]
    provider_calls: int = 0
    prompt_bytes: int = 0
    elapsed_seconds: float = 0.0
    exact_input_tokens: int | None = None
    exact_output_tokens: int | None = None
    token_usage_complete: bool = True

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "Budget":
        limits = value.get("limits")
        if not isinstance(limits, Mapping):
            raise ValueError("budget limits are missing")
        return cls(
            limits={str(key): int(item) for key, item in limits.items()},
            provider_calls=int(value.get("provider_calls", 0)),
            prompt_bytes=int(value.get("prompt_bytes", 0)),
            elapsed_seconds=float(value.get("elapsed_seconds", 0.0)),
            exact_input_tokens=(
                int(value["exact_input_tokens"])
                if value.get("exact_input_tokens") is not None
                else None
            ),
            exact_output_tokens=(
                int(value["exact_output_tokens"])
                if value.get("exact_output_tokens") is not None
                else None
            ),
            token_usage_complete=bool(value.get("token_usage_complete", False)),
        )

    def to_dict(self) -> dict[str, object]:
        exact = (
            self.token_usage_complete
            and self.exact_input_tokens is not None
            and self.exact_output_tokens is not None
        )
        known_measured_usage = (
            {
                "input_tokens": self.exact_input_tokens,
                "output_tokens": self.exact_output_tokens,
                "total_tokens": self.exact_input_tokens + self.exact_output_tokens,
            }
            if self.exact_input_tokens is not None and self.exact_output_tokens is not None
            else None
        )
        return {
            "limits": dict(self.limits),
            "provider_calls": self.provider_calls,
            "prompt_bytes": self.prompt_bytes,
            "elapsed_seconds": self.elapsed_seconds,
            "token_usage": (
                {
                    "input_tokens": self.exact_input_tokens,
                    "output_tokens": self.exact_output_tokens,
                    "total_tokens": self.exact_input_tokens + self.exact_output_tokens,
                }
                if exact
                else "unknown"
            ),
            "proxy_usage": {
                "provider_calls": self.provider_calls,
                "prompt_bytes": self.prompt_bytes,
                "elapsed_seconds": self.elapsed_seconds,
            },
            "exact_input_tokens": self.exact_input_tokens,
            "exact_output_tokens": self.exact_output_tokens,
            "token_usage_complete": self.token_usage_complete,
            "known_measured_usage": known_measured_usage,
        }

    def check(
        self,
        *,
        additional_provider_calls: int = 0,
        prompt_bytes_for_call: int = 0,
        elapsed_seconds: float | None = None,
    ) -> None:
        elapsed = self.elapsed_seconds if elapsed_seconds is None else elapsed_seconds
        if self.provider_calls + additional_provider_calls > self.limits["max_provider_calls"]:
            raise BudgetExceeded("MAX_PROVIDER_CALLS")
        if prompt_bytes_for_call > self.limits["max_prompt_bytes_per_call"]:
            raise BudgetExceeded("MAX_PROMPT_BYTES_PER_CALL")
        if elapsed >= self.limits["max_elapsed_seconds"]:
            raise BudgetExceeded("MAX_ELAPSED_SECONDS")

    def charge_provider(self, prompt_bytes: int) -> None:
        self.check(additional_provider_calls=1, prompt_bytes_for_call=prompt_bytes)
        self.provider_calls += 1
        self.prompt_bytes += prompt_bytes

    def record_elapsed(self, elapsed_seconds: float) -> None:
        self.elapsed_seconds = max(self.elapsed_seconds, elapsed_seconds)

    def record_usage(self, input_tokens: int | None, output_tokens: int | None) -> None:
        if input_tokens is None or output_tokens is None:
            self.token_usage_complete = False
            return
        self.exact_input_tokens = (self.exact_input_tokens or 0) + input_tokens
        self.exact_output_tokens = (self.exact_output_tokens or 0) + output_tokens
