# Graph Report - biohub-cell-tracking-during-development  (2026-08-19)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 23 nodes · 22 edges · 5 communities (3 shown, 2 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `822df37f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 1
- Community 3
- Community 4

## God Nodes (most connected - your core abstractions)
1. `setup.sh script` - 4 edges
2. `log()` - 3 edges
3. `auto_start_docker_desktop()` - 3 edges
4. `fail()` - 2 edges
5. `Utilities for the Kaggle Biohub cell-tracking project.` - 1 edges
6. `biohub-cell-tracking` - 0 edges

## Surprising Connections (you probably didn't know these)
- None detected - all connections are within the same source files.

## Import Cycles
- None detected.

## Communities (5 total, 2 thin omitted)

### Community 1 - "Community 1"
Cohesion: 0.80
Nodes (4): auto_start_docker_desktop(), fail(), log(), setup.sh script

## Knowledge Gaps
- **1 isolated node(s):** `biohub-cell-tracking`
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What connects `biohub-cell-tracking` to the rest of the system?**
  _1 weakly-connected nodes found - possible documentation gaps or missing edges._