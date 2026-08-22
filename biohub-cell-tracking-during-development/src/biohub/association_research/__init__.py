"""Lane F association-method research.

Calibration-free edge-scoring rules that consume the detector-fixed cache
contract (``nodes.npz`` + ``candidate_edges.npz``) and hand the resulting
candidate edges to the unchanged upstream ``build_graph`` + ILP path.

``scoring`` is deliberately free of every project import so the rules can be
unit tested with numpy alone.  ``runner`` holds the cache and graph plumbing.
"""

from biohub.association_research.scoring import RESEARCH_RULES, PairInputs, ScoringRule

__all__ = ["RESEARCH_RULES", "PairInputs", "ScoringRule"]
