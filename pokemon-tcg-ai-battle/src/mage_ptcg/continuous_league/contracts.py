"""共通の永続化・同一性契約。

時刻やホスト固有パスを content ID に含めず、同じ入力から同じ ID を得る。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


class LeagueContractError(ValueError):
    """継続リーグの fail-closed 契約違反。"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def content_id(domain: str, payload: Any) -> str:
    digest = hashlib.sha256()
    digest.update(domain.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical_json_bytes(payload))
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: str, field: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise LeagueContractError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


def load_json(path: Path) -> Any:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise LeagueContractError(f"failed to read JSON {path}: {exc}") from exc


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(Path(path), canonical_json_bytes(payload) + b"\n")


def publish_content_addressed_json(
    root: Path,
    *,
    domain: str,
    payload: Mapping[str, Any],
    id_field: str,
    filename: str = "manifest.json",
) -> tuple[str, Path]:
    """ID を埋めた immutable manifest を id ディレクトリへ公開する。"""

    identity_payload = dict(payload)
    identity_payload.pop(id_field, None)
    identity = content_id(domain, identity_payload)
    document = dict(identity_payload)
    document[id_field] = identity
    target_dir = Path(root) / identity
    target_path = target_dir / filename
    if target_path.exists():
        if load_json(target_path) != document:
            raise LeagueContractError(
                f"content-address collision for {domain} identity {identity}"
            )
        return identity, target_path
    target_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target_path, document)
    return identity, target_path


def append_jsonl_once(path: Path, payload: Mapping[str, Any], key: str) -> bool:
    """同一 key を重複追記しない小規模 durable JSONL writer。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    existing = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise LeagueContractError(
                        f"corrupt JSONL {path}:{line_number}: {exc}"
                    ) from exc
                if existing.get(key) == payload.get(key):
                    if existing != dict(payload):
                        raise LeagueContractError(
                            f"duplicate key with different payload: {payload.get(key)}"
                        )
                    return False
    encoded = canonical_json_bytes(dict(payload)) + b"\n"
    with path.open("ab") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return True
