---
applyTo: "docs/**/*.md,docs/**/*.yaml,docs/**/*.json"
---

# Documentation Sync Instructions

- Keep Japanese as the primary language.
- Preserve technical detail.
- Separate canonical design (`docs/plan/`) from current status (`docs/status/`).
- Use exact Notion page IDs from `docs/notion/page_map.yaml`.
- Do not perform bidirectional automatic merge.
- Do not remove Mermaid, LaTeX, tables, code, gates, dates, fallback,
  or GO/NO-GO conditions.
- Validate with `python scripts/docs/validate_docs.py` before synchronization.
