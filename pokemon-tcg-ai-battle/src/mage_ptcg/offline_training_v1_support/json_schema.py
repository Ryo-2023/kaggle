"""JSON Schema equivalent definition generator for offline training platform.

Provides deterministic dictionary schema outputs without relying on external libraries.
"""

from __future__ import annotations
from typing import Any

# Mini validation helper
def validate_dict_by_schema(data: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, str]:
    """Perform a light, secure dictionary validation against a json schema dictionary."""
    properties = schema.get("properties", {})
    required = schema.get("required", [])

    # Check required fields
    for req in required:
        if req not in data:
            return False, f"Missing required property: {req}"

    # Check types and properties
    for k, v in data.items():
        if k not in properties:
            continue
        prop_schema = properties[k]
        expected_type = prop_schema.get("type")
        if expected_type == "string" and not isinstance(v, str):
            return False, f"Property {k} must be string"
        elif expected_type == "integer" and not isinstance(v, int):
            return False, f"Property {k} must be integer"
        elif expected_type == "number" and not isinstance(v, (int, float)):
            return False, f"Property {k} must be number"
        elif expected_type == "boolean" and not isinstance(v, bool):
            return False, f"Property {k} must be boolean"
        elif expected_type == "array" and not isinstance(v, (list, tuple)):
            return False, f"Property {k} must be array"
        elif expected_type == "object" and not isinstance(v, dict):
            return False, f"Property {k} must be object"

    return True, ""


# Definitions of the schemas
GAME_RESULT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "GameResult",
    "type": "object",
    "properties": {
        "game_id": {"type": "string"},
        "winner_seat": {"type": "integer"},
        "turns": {"type": "integer"},
        "player_0_deck": {"type": "string"},
        "player_1_deck": {"type": "string"},
    },
    "required": ["game_id", "winner_seat", "turns"],
}

DECISION_DIAGNOSTIC_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DecisionDiagnostic",
    "type": "object",
    "properties": {
        "decision_id": {"type": "string"},
        "model_id": {"type": "string"},
        "latency_ms": {"type": "number"},
        "chosen_action": {"type": "string"},
    },
    "required": ["decision_id", "model_id", "latency_ms"],
}

DATASET_MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "DatasetManifest",
    "type": "object",
    "properties": {
        "dataset_hash": {"type": "string"},
        "record_count": {"type": "integer"},
        "creation_time": {"type": "number"},
    },
    "required": ["dataset_hash", "record_count", "creation_time"],
}

MODEL_MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ModelManifest",
    "type": "object",
    "properties": {
        "model_id": {"type": "string"},
        "architecture": {"type": "string"},
        "parameters": {"type": "integer"},
    },
    "required": ["model_id", "architecture"],
}

EXPERIMENT_RECORD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ExperimentRecord",
    "type": "object",
    "properties": {
        "experiment_id": {"type": "string"},
        "dataset_hash": {"type": "string"},
        "model_id": {"type": "string"},
        "status": {"type": "string"},
    },
    "required": ["experiment_id", "dataset_hash", "model_id"],
}

TEACHER_DESCRIPTOR_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TeacherDescriptor",
    "type": "object",
    "properties": {
        "teacher_id": {"type": "string"},
        "type": {"type": "string"},
        "version": {"type": "string"},
    },
    "required": ["teacher_id", "type"],
}

TEACHER_OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "TeacherOutput",
    "type": "object",
    "properties": {
        "decision_id": {"type": "string"},
        "probs": {"type": "array"},
        "confidence": {"type": "number"},
    },
    "required": ["decision_id", "probs"],
}

HARD_STATE_RECORD_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "HardStateRecord",
    "type": "object",
    "properties": {
        "state_hash": {"type": "string"},
        "loss": {"type": "number"},
        "difficulty": {"type": "number"},
    },
    "required": ["state_hash", "loss"],
}

AUDIT_EVENT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "AuditEvent",
    "type": "object",
    "properties": {
        "event_id": {"type": "string"},
        "timestamp": {"type": "number"},
        "action": {"type": "string"},
        "user": {"type": "string"},
    },
    "required": ["event_id", "timestamp", "action"],
}

LINEAGE_NODE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LineageNode",
    "type": "object",
    "properties": {
        "node_id": {"type": "string"},
        "type": {"type": "string"},
        "dependencies": {"type": "array"},
    },
    "required": ["node_id", "type"],
}

PROMOTION_REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PromotionReport",
    "type": "object",
    "properties": {
        "candidate_id": {"type": "string"},
        "baseline_id": {"type": "string"},
        "win_rate": {"type": "number"},
        "passed": {"type": "boolean"},
    },
    "required": ["candidate_id", "baseline_id", "win_rate", "passed"],
}

REPRO_BUNDLE_MANIFEST_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "ReproBundleManifest",
    "type": "object",
    "properties": {
        "bundle_hash": {"type": "string"},
        "files": {"type": "array"},
        "environment": {"type": "object"},
    },
    "required": ["bundle_hash", "files"],
}

SCHEMAS = {
    "game_result": GAME_RESULT_SCHEMA,
    "decision_diagnostic": DECISION_DIAGNOSTIC_SCHEMA,
    "dataset_manifest": DATASET_MANIFEST_SCHEMA,
    "model_manifest": MODEL_MANIFEST_SCHEMA,
    "experiment_record": EXPERIMENT_RECORD_SCHEMA,
    "teacher_descriptor": TEACHER_DESCRIPTOR_SCHEMA,
    "teacher_output": TEACHER_OUTPUT_SCHEMA,
    "hard_state_record": HARD_STATE_RECORD_SCHEMA,
    "audit_event": AUDIT_EVENT_SCHEMA,
    "lineage_node": LINEAGE_NODE_SCHEMA,
    "promotion_report": PROMOTION_REPORT_SCHEMA,
    "repro_bundle_manifest": REPRO_BUNDLE_MANIFEST_SCHEMA,
}

def get_schema(name: str) -> dict[str, Any]:
    """Retrieve schema by key safely."""
    if name not in SCHEMAS:
        raise KeyError(f"Schema {name} not found")
    # Return a deep-copy equivalent to ensure no caller mutations
    import json
    return json.loads(json.dumps(SCHEMAS[name]))

def validate_record(data: dict[str, Any], schema_name: str) -> None:
    """Validate dictionary against registered schema name, raising error if invalid."""
    from mage_ptcg.offline_training_v1_support.errors import ValidationError
    schema = get_schema(schema_name)
    ok, err_msg = validate_dict_by_schema(data, schema)
    if not ok:
        raise ValidationError(f"Schema validation failed for {schema_name}: {err_msg}")
