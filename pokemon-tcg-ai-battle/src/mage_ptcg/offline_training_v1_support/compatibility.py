"""Contract compatibility checker.

Analyzes backward, forward, and bidirectional compatibility of JSON schema definitions.
"""

from __future__ import annotations

from typing import Any

class CompatibilityChecker:
    """Evaluates compatibility between two schema definition dictionary specifications."""

    def analyze(self, schema_a: dict[str, Any], schema_b: dict[str, Any]) -> dict[str, Any]:
        """Compare schema A (old/source) and schema B (new/target).

        Expected schema structure:
        {
            "version": str,
            "required": list[str],
            "optional": list[str],
            "types": dict[str, str], # field -> type_name
            "enums": dict[str, list[any]], # field -> list of allowed values
            "privacy": dict[str, str], # field -> 'PUBLIC' or 'PRIVATE'
            "unknown_field_policy": str, # 'allow' or 'reject'
            "hash_policy": str # 'sha256' or other
        }
        """
        reasons = []
        warnings = []

        ver_a = schema_a.get("version")
        ver_b = schema_b.get("version")
        if not ver_a or not ver_b:
            reasons.append("version_unknown")

        req_a = set(schema_a.get("required", []))
        req_b = set(schema_b.get("required", []))

        opt_a = set(schema_a.get("optional", []))
        opt_b = set(schema_b.get("optional", []))

        types_a = schema_a.get("types", {})
        types_b = schema_b.get("types", {})

        enums_a = schema_a.get("enums", {})
        enums_b = schema_b.get("enums", {})

        priv_a = schema_a.get("privacy", {})
        priv_b = schema_b.get("privacy", {})

        policy_a = schema_a.get("unknown_field_policy", "allow")
        policy_b = schema_b.get("unknown_field_policy", "allow")

        hash_a = schema_a.get("hash_policy", "sha256")
        hash_b = schema_b.get("hash_policy", "sha256")

        # 1. Check for breaking changes from A to B (Backward Compatibility: can B read A's output?)
        # - Added required fields (A does not have them, so B cannot parse A's output)
        added_req = req_b - req_a
        if added_req:
            reasons.append(f"required_field_added: {sorted(list(added_req))}")

        # - Field type change
        for field, t_b in types_b.items():
            if field in types_a:
                t_a = types_a[field]
                if t_a != t_b:
                    reasons.append(f"field_type_changed: {field} ({t_a} -> {t_b})")

        # - Enum values shrunken (B allows fewer values than A generated)
        for field, vals_b in enums_b.items():
            if field in enums_a:
                vals_a = enums_a[field]
                shrunken = set(vals_a) - set(vals_b)
                if shrunken:
                    reasons.append(f"enum_shrunken: {field} (removed {sorted(list(shrunken))})")

        # - Privacy classification relaxed (A treated as PRIVATE, but B treats as PUBLIC -> leak risk)
        for field, p_b in priv_b.items():
            if field in priv_a:
                p_a = priv_a[field]
                if p_a == "PRIVATE" and p_b == "PUBLIC":
                    reasons.append(f"privacy_classification_relaxed: {field} (PRIVATE -> PUBLIC)")

        # - Hash policy changed
        if hash_a != hash_b:
            reasons.append(f"hash_policy_changed: {hash_a} -> {hash_b}")

        # - Unknown-field policy changed to 'reject' (if B rejects unknown fields, A's extensions break B)
        if policy_a == "allow" and policy_b == "reject":
            reasons.append("unknown_field_policy_reject_enabled")

        # 2. Backward compatibility result
        backward_compatible = len(reasons) == 0

        # 3. Check for breaking changes from B to A (Forward Compatibility: can A read B's output?)
        fwd_reasons = []
        # - Required fields in A that B made optional or deleted
        removed_req_in_b = req_a - req_b
        if removed_req_in_b:
            fwd_reasons.append(f"required_field_removed: {sorted(list(removed_req_in_b))}")

        # - Enum values added in B (A cannot parse them)
        for field, vals_b in enums_b.items():
            if field in enums_a:
                vals_a = enums_a[field]
                added_vals = set(vals_b) - set(vals_a)
                if added_vals:
                    fwd_reasons.append(f"enum_expanded: {field} (added {sorted(list(added_vals))})")

        # - Type changes (same as backward)
        for field, t_b in types_b.items():
            if field in types_a:
                t_a = types_a[field]
                if t_a != t_b:
                    fwd_reasons.append(f"field_type_changed: {field} ({t_a} -> {t_b})")

        forward_compatible = len(fwd_reasons) == 0

        # Determine overall compatibility status
        if backward_compatible and forward_compatible:
            status = "BIDIRECTIONALLY_COMPATIBLE"
        elif backward_compatible:
            status = "BACKWARD_COMPATIBLE"
        elif forward_compatible:
            status = "FORWARD_COMPATIBLE"
        else:
            status = "BREAKING"

        # Generate Migration Plan Suggestion if breaking
        migration_plan = []
        if reasons:
            migration_plan.append("Proposed Migration Steps:")
            for r in reasons:
                if "required_field_added" in r:
                    fields = r.split(": ")[1]
                    migration_plan.append(f"  - Provide fallback defaults for new required fields: {fields}")
                elif "field_type_changed" in r:
                    migration_plan.append(f"  - Run data converter/cast script for modified types")
                elif "enum_shrunken" in r:
                    migration_plan.append(f"  - Filter or re-map obsolete enum values before parsing")
                elif "privacy_classification_relaxed" in r:
                    migration_plan.append(f"  - WARNING: Confirm that private fields are safe to be marked public")

        # Compile Markdown Summary
        md_lines = [
            f"# Schema Compatibility Report: {status}",
            f"- **Source Version**: {ver_a}",
            f"- **Target Version**: {ver_b}",
            "",
            "## Compatibility Matrix",
            f"- **Backward Compatible**: {'PASS' if backward_compatible else 'FAIL'}",
            f"- **Forward Compatible**: {'PASS' if forward_compatible else 'FAIL'}",
            "",
        ]
        if reasons:
            md_lines.append("## Breaking Changes (Backward Compatibility Violations)")
            for r in reasons:
                md_lines.append(f"- [BREAKING] {r}")
            md_lines.append("")

        if fwd_reasons:
            md_lines.append("## Forward Compatibility Warnings")
            for r in fwd_reasons:
                md_lines.append(f"- [WARN] {r}")
            md_lines.append("")

        if migration_plan:
            md_lines.append("## Suggested Migration Plan")
            md_lines.extend(migration_plan)
            md_lines.append("")

        return {
            "status": status,
            "backward_compatible": backward_compatible,
            "forward_compatible": forward_compatible,
            "breaking_reasons": reasons,
            "forward_warnings": fwd_reasons,
            "markdown_summary": "\n".join(md_lines),
            "migration_plan": migration_plan,
        }
