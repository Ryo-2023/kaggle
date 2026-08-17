"""In-process batched central inference boundary used by CPU CABT actors."""
from __future__ import annotations

from dataclasses import dataclass
import queue
import threading
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    game_id: str; seat: int; sequence_state_id: str; policy_version: str; state: Any; actions: Any; legal_mask: Any


class CentralInferenceServer:
    def __init__(self, model: Any, *, max_batch_size: int = 128, max_delay_ms: float = 5.0) -> None:
        if max_batch_size < 1 or max_delay_ms < 0: raise ValueError("invalid batching config")
        self.model, self.max_batch_size, self.max_delay_ms = model, max_batch_size, max_delay_ms; self.hidden: dict[tuple[str, int, str], Any] = {}; self.metrics: dict[str, float] = {"requests": 0.0, "batches": 0.0, "queue_wait_ms": 0.0, "inference_ms": 0.0, "stale_policy": 0.0}
    def infer(self, request: InferenceRequest, *, expected_policy_version: str) -> dict[str, Any]:
        if request.policy_version != expected_policy_version: self.metrics["stale_policy"] += 1; raise ValueError("stale policy version")
        started = time.perf_counter(); key = (request.game_id, request.seat, request.sequence_state_id); output = self.model(request.state, request.actions, request.legal_mask, self.hidden.get(key)); self.hidden[key] = output["hidden"]
        self.metrics["requests"] += 1; self.metrics["batches"] += 1; self.metrics["inference_ms"] += (time.perf_counter() - started) * 1000; return output
    def infer_many(self, requests: list[InferenceRequest], *, expected_policy_version: str) -> list[dict[str, Any]]:
        """Run a compatible actor burst as one GPU forward call.

        Variable action counts are padded with an explicit false legal mask;
        callers may split incompatible tensor shapes into several bursts.
        """
        if not requests or len(requests) > self.max_batch_size: raise ValueError("inference batch size is invalid")
        if any(request.policy_version != expected_policy_version for request in requests): self.metrics["stale_policy"] += 1; raise ValueError("stale policy version")
        import torch
        started = time.perf_counter(); action_count = max(request.actions.shape[1] for request in requests)
        states = torch.cat([request.state for request in requests], dim=0); action_rows = []; masks = []
        for request in requests:
            padding = action_count - request.actions.shape[1]
            action_rows.append(torch.nn.functional.pad(request.actions, (0, 0, 0, padding)))
            masks.append(torch.nn.functional.pad(request.legal_mask, (0, padding), value=False))
        actions, legal_mask = torch.cat(action_rows, dim=0), torch.cat(masks, dim=0)
        hidden_values = [self.hidden.get((request.game_id, request.seat, request.sequence_state_id)) for request in requests]
        hidden = None
        if any(value is not None for value in hidden_values):
            template = next(value for value in hidden_values if value is not None)
            hidden = torch.cat([value if value is not None else torch.zeros_like(template) for value in hidden_values], dim=1)
        output = self.model(states, actions, legal_mask, hidden); self.metrics["requests"] += len(requests); self.metrics["batches"] += 1; self.metrics["inference_ms"] += (time.perf_counter() - started) * 1000
        result = []
        for index, request in enumerate(requests):
            key = (request.game_id, request.seat, request.sequence_state_id); hidden = output["hidden"][:, index:index + 1]
            self.hidden[key] = hidden; item: dict[str, Any] = {}
            for name, value in output.items():
                if name == "hidden":
                    item[name] = hidden
                elif hasattr(value, "shape") and value.shape[0] == len(requests):
                    sliced = value[index:index + 1]
                    if name in {"q", "logits"}: sliced = sliced[:, :request.actions.shape[1]]
                    item[name] = sliced
                else:
                    item[name] = value
            result.append(item)
        return result
    def reset_game(self, game_id: str) -> None:
        for key in list(self.hidden):
            if key[0] == game_id: del self.hidden[key]


@dataclass(slots=True)
class _QueuedRequest:
    request: InferenceRequest
    expected_policy_version: str
    enqueued: float
    event: threading.Event
    output: dict[str, Any] | None = None
    error: BaseException | None = None


class QueuedCentralInferenceServer:
    """Thread-safe microbatch queue for concurrent CPU CABT actors."""
    def __init__(self, model: Any, *, max_batch_size: int = 128, max_delay_ms: float = 5.0,
                 callback_timeout_seconds: float = 10.0) -> None:
        self.core = CentralInferenceServer(model, max_batch_size=max_batch_size, max_delay_ms=max_delay_ms)
        self.max_batch_size, self.max_delay_ms = max_batch_size, max_delay_ms
        self.callback_timeout_seconds = callback_timeout_seconds
        self._queue: queue.Queue[_QueuedRequest | None] = queue.Queue(); self._closed = False
        self._queue_wait_ms: list[float] = []; self._batch_sizes: list[int] = []; self._callback_ms: list[float] = []
        self._thread = threading.Thread(target=self._serve, name="r2d3-central-inference", daemon=True); self._thread.start()
    def _serve(self) -> None:
        while True:
            first = self._queue.get()
            if first is None: return
            batch = [first]; deadline = first.enqueued + self.max_delay_ms / 1000.0
            while len(batch) < self.max_batch_size:
                remaining = deadline - time.perf_counter()
                if remaining <= 0: break
                try: item = self._queue.get(timeout=remaining)
                except queue.Empty: break
                if item is None:
                    self._queue.put(None); break
                batch.append(item)
            started = time.perf_counter()
            self._queue_wait_ms.extend((started - item.enqueued) * 1000 for item in batch); self._batch_sizes.append(len(batch))
            try:
                versions = {item.expected_policy_version for item in batch}
                if len(versions) != 1: raise ValueError("mixed expected policy versions")
                outputs = self.core.infer_many([item.request for item in batch], expected_policy_version=versions.pop())
                for item, output in zip(batch, outputs, strict=True): item.output = output
            except BaseException as exc:
                for item in batch: item.error = exc
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                self._callback_ms.extend([elapsed] * len(batch))
                for item in batch: item.event.set()
    def infer(self, request: InferenceRequest, *, expected_policy_version: str) -> dict[str, Any]:
        if self._closed: raise RuntimeError("central inference server is closed")
        item = _QueuedRequest(request, expected_policy_version, time.perf_counter(), threading.Event()); self._queue.put(item)
        if not item.event.wait(self.callback_timeout_seconds): raise TimeoutError("central inference queue callback timed out")
        if item.error is not None: raise item.error
        assert item.output is not None
        return item.output
    def reset_game(self, game_id: str) -> None:
        self.core.reset_game(game_id)
    def close(self) -> None:
        if self._closed: return
        self._closed = True; self._queue.put(None); self._thread.join(timeout=self.callback_timeout_seconds)
        if self._thread.is_alive(): raise TimeoutError("central inference server did not stop")
    @property
    def metrics(self) -> dict[str, Any]:
        return {**self.core.metrics, "queue_wait_ms_samples": list(self._queue_wait_ms), "batch_sizes": list(self._batch_sizes),
                "callback_ms_samples": list(self._callback_ms)}


class IPCInferenceClient:
    """CPU actor-side proxy for a parent-owned CUDA inference queue."""
    def __init__(self, request_queue: Any, response_queue: Any, *, callback_timeout_seconds: float = 10.0) -> None:
        self.request_queue, self.response_queue = request_queue, response_queue
        self.callback_timeout_seconds = callback_timeout_seconds; self._counter = 0
    def infer(self, request: InferenceRequest, *, expected_policy_version: str) -> dict[str, Any]:
        import torch
        self._counter += 1; request_id = f"{request.game_id}:{request.seat}:{self._counter}"
        self.request_queue.put({"request_id": request_id, "response_queue": self.response_queue, "game_id": request.game_id,
            "seat": request.seat, "sequence_state_id": request.sequence_state_id, "policy_version": request.policy_version,
            "expected_policy_version": expected_policy_version, "state": request.state.cpu().tolist(),
            "actions": request.actions.cpu().tolist(), "legal_mask": request.legal_mask.cpu().tolist()})
        try: response = self.response_queue.get(timeout=self.callback_timeout_seconds)
        except queue.Empty as exc: raise TimeoutError("IPC central inference response timed out") from exc
        if response.get("request_id") != request_id: raise RuntimeError("IPC central inference response identity mismatch")
        if response.get("error"): raise RuntimeError(str(response["error"]))
        return {"q": torch.tensor(response["q"], dtype=torch.float32), "hidden": None}
    def reset_game(self, game_id: str) -> None:
        del game_id
