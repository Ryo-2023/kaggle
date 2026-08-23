"""Contract tests for the GT-free Recipe C stage diagnostic receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import biohub.recipe_c.diagnostics as diagnostics
from biohub.recipe_c.geff_bridge import write_json_exclusive


def _line(sample: str = "sample") -> str:
    payload = {
        "candidate_graph_edges": 4,
        "candidate_graph_nodes": 6,
        "candidate_graph_per_frame_node_counts": {"0": 3, "1": 3},
        "candidate_graph_topology_sha256": "a" * 64,
        "combined_detector_edges": 0,
        "combined_detector_nodes": 6,
        "combined_detector_per_frame_node_counts": {"0": 3, "1": 3},
        "combined_detector_topology_sha256": "b" * 64,
        "ilp_post_edges": 3,
        "ilp_post_nodes": 6,
        "ilp_post_per_frame_node_counts": {"0": 3, "1": 3},
        "ilp_post_topology_sha256": "c" * 64,
        "ilp_pre_edges": 4,
        "ilp_pre_nodes": 6,
        "ilp_pre_per_frame_node_counts": {"0": 3, "1": 3},
        "ilp_pre_topology_sha256": "d" * 64,
        "sample_id": sample,
        "schema_version": 1,
    }
    return diagnostics.DIAGNOSTIC_STDOUT_PREFIX + json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    )


def test_child_stdout_parser_requires_one_finite_exact_sample() -> None:
    parsed = diagnostics._parse_child_diagnostic_stdout(_line(), ("sample",))
    assert parsed["sample"]["candidate_graph_edges"] == 4
    with pytest.raises(ValueError, match=r"duplicate|missing|exact"):
        diagnostics._parse_child_diagnostic_stdout(_line() + "\n" + _line(), ("sample",))
    with pytest.raises(ValueError, match=r"finite|schema|JSON"):
        diagnostics._parse_child_diagnostic_stdout(
            _line().replace('"candidate_graph_edges":4', '"candidate_graph_edges":NaN'),
            ("sample",),
        )


def test_predictor_diagnostic_instrumentation_is_deterministic_and_counts_only() -> None:
    source = (
        b"import json\n"
        b"def build_graph(coords, edges):\n"
        b"    return type('G', (), {'num_nodes': lambda self: len(coords), 'num_edges': lambda self: len(edges)})()\n"
        b"def save_graph(graph, path): pass\n"
        b"def predict(cfg, name):\n"
        b"    coords, edges = predict_video()\n"
        b"    graph = build_graph(coords, edges)\n"
        b"    if cfg.use_ilp and graph.num_edges() > 0:\n"
        b"        graph = solver.solve(graph)\n"
        b"    save_graph(graph, name)\n"
    )
    first, applied = diagnostics._apply_predictor_diagnostic_instrumentation(source)
    second, applied_again = diagnostics._apply_predictor_diagnostic_instrumentation(source)
    assert applied is True
    assert applied_again is True
    assert first == second
    assert hashlib.sha256(first).hexdigest() != hashlib.sha256(source).hexdigest()
    text = first.decode()
    assert "BIOHUB_RECIPE_C_DIAGNOSTIC " in text
    assert "candidate_graph_per_frame_node_counts" in text
    assert "ilp_post_topology_sha256" in text
    assert "ground_truth" not in text.lower()


def test_trace_copy_adds_only_per_frame_counts(tmp_path: Path) -> None:
    source = (
        b"def _edge_set(edges):\n"
        b"    return set()\n"
        b"def filter_output_graph_traced(nodes_by_id, raw_edges, **kwargs):\n"
        b"    stage_snapshots = []\n"
        b"    def checkpoint(stage, nodes, edges):\n"
        b"        stage_snapshots.append({'stage': stage, 'n_nodes': len(nodes), 'n_edges': len(edges)})\n"
        b"    checkpoint('final', nodes_by_id, raw_edges)\n"
        b"    return nodes_by_id, raw_edges, {}, {'stage_snapshots': stage_snapshots}\n"
    )
    derived, source_hash, derived_hash = diagnostics._derive_trace_payload(source)
    assert hashlib.sha256(source).hexdigest() == source_hash
    assert hashlib.sha256(derived).hexdigest() == derived_hash
    assert derived_hash != source_hash
    text = derived.decode()
    assert "per_frame_node_counts" in text
    assert "filter_output_graph_traced" in text
    repeated, repeated_source_hash, repeated_derived_hash = diagnostics._derive_trace_payload(derived)
    assert repeated == derived
    assert repeated_source_hash == derived_hash
    assert repeated_derived_hash == derived_hash


def _graph(node_id: int = 0, *, frame: int = 0) -> tuple[dict[int, dict[str, object]], list[dict[str, object]]]:
    return (
        {
            node_id: {
                "node_id": node_id,
                "t": frame,
                "z": 1,
                "y": 2,
                "x": 3,
            }
        },
        [],
    )


def test_child_stdout_parser_rejects_partial_and_non_exact_records() -> None:
    with pytest.raises(ValueError, match=r"duplicate|missing"):
        diagnostics._parse_child_diagnostic_stdout(_line(), ("sample", "other"))
    with pytest.raises(ValueError, match="prefix"):
        diagnostics._parse_child_diagnostic_stdout(
            "noise " + _line() + "\n" + _line("other"),
            ("sample", "other"),
        )
    with pytest.raises(ValueError, match="schema"):
        diagnostics._parse_child_diagnostic_stdout(
            diagnostics.DIAGNOSTIC_STDOUT_PREFIX
            + json.dumps(
                {
                    "candidate_graph_edges": 4,
                    "candidate_graph_nodes": 6,
                    "candidate_graph_per_frame_node_counts": {"0": 3, "1": 3},
                    "candidate_graph_topology_sha256": "a" * 64,
                    "combined_detector_edges": 0,
                    "combined_detector_nodes": 6,
                    "combined_detector_per_frame_node_counts": {"0": 3, "1": 3},
                    "combined_detector_topology_sha256": "b" * 64,
                    "ilp_post_edges": 3,
                    "ilp_post_nodes": 6,
                    "ilp_post_per_frame_node_counts": {"0": 3, "1": 3},
                    "ilp_post_topology_sha256": "c" * 64,
                    "ilp_pre_edges": 4,
                    "ilp_pre_nodes": 6,
                    "ilp_pre_per_frame_node_counts": {"0": 3, "1": 3},
                    "ilp_pre_topology_sha256": "d" * 64,
                    "sample_id": "sample",
                    "schema_version": 1,
                    "unexpected": 0,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            ("sample",),
        )


def test_child_stdout_parser_rejects_duplicate_json_keys() -> None:
    encoded = _line()[len(diagnostics.DIAGNOSTIC_STDOUT_PREFIX) :]
    duplicate = encoded[:-1] + ',"sample_id":"sample"}'
    with pytest.raises(ValueError, match=r"duplicate|JSON"):
        diagnostics._parse_child_diagnostic_stdout(
            diagnostics.DIAGNOSTIC_STDOUT_PREFIX + duplicate,
            ("sample",),
        )


def test_graph_counts_are_per_frame_and_canonically_hashed() -> None:
    nodes = {
        0: {"node_id": 0, "t": 0, "z": 1, "y": 2, "x": 3},
        1: {"node_id": 1, "t": 1, "z": 1, "y": 2, "x": 3},
        2: {"node_id": 2, "t": 1, "z": 2, "y": 2, "x": 3},
    }
    counts = diagnostics._graph_counts(
        nodes,
        [{"source_id": 0, "target_id": 1}, {"source_id": 0, "target_id": 2}],
    )
    assert counts["nodes"] == 3
    assert counts["edges"] == 2
    assert counts["forks"] == 1
    assert counts["per_frame_node_counts"] == {"0": 1, "1": 2}
    assert isinstance(counts["canonical_sha256"], str)
    assert len(counts["canonical_sha256"]) == 64


def _complete_snapshots(nodes: dict[int, dict[str, object]], edges: list[dict[str, object]]) -> list[dict[str, object]]:
    per_frame: dict[str, int] = {}
    for node in nodes.values():
        frame = str(int(node["t"]))
        per_frame[frame] = per_frame.get(frame, 0) + 1
    return [
        {
            "stage": stage,
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "per_frame_node_counts": dict(per_frame),
        }
        for stage in diagnostics._TRACE_STAGES
    ]


def _trace_double(
    nodes: dict[int, dict[str, object]], edges: list[dict[str, object]], **kwargs: object
) -> tuple[dict[int, dict[str, object]], list[dict[str, object]], dict[str, int], dict[str, object]]:
    return nodes, edges, {}, {"stage_snapshots": _complete_snapshots(nodes, edges)}


def test_trace_requires_exact_stage_sequence() -> None:
    nodes, edges = _graph()
    extra = [*_complete_snapshots(nodes, edges),
        {
            "stage": "unexpected",
            "n_nodes": 1,
            "n_edges": 0,
            "per_frame_node_counts": {"0": 1},
        }
    ]
    reordered = list(reversed(_complete_snapshots(nodes, edges)))

    def make_traced(
        stage_snapshots: list[dict[str, object]],
    ) -> object:
        def traced(
            _nodes: object,
            _edges: object,
            **kwargs: object,
        ) -> tuple[object, object, dict[str, object], dict[str, object]]:
            return _nodes, _edges, {}, {"stage_snapshots": stage_snapshots}

        return traced

    for snapshots in (extra, reordered):
        with pytest.raises(ValueError, match=r"sequence|extra|order|exact"):
            diagnostics._trace_graph(
                make_traced(snapshots),
                nodes,
                edges,
                dataset="sample",
            )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda snapshot: snapshot.update(n_nodes=2),
        lambda snapshot: snapshot.update(n_edges=True),
        lambda snapshot: snapshot.update(per_frame_node_counts={"0": 2}),
    ],
)
def test_trace_requires_exact_snapshot_counts(mutate: object) -> None:
    nodes, edges = _graph()
    snapshots = _complete_snapshots(nodes, edges)
    mutate(snapshots[0])  # type: ignore[operator]
    with pytest.raises(ValueError, match=r"snapshot|per-frame|n_nodes|n_edges"):
        diagnostics._trace_graph(
            lambda _nodes, _edges, **kwargs: (_nodes, _edges, {}, {"stage_snapshots": snapshots}),
            nodes,
            edges,
            dataset="sample",
        )


def test_semantic_graph_signature_uses_source_round_and_clamp() -> None:
    nodes = {0: {"node_id": 0, "t": 0, "z": 1.8, "y": -1.2, "x": 3.0}}

    def canonicalizer(
        dataset: str,
        graph_nodes: dict[int, dict[str, object]],
        graph_edges: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        node = graph_nodes[0]
        return [
            {
                "row_type": "node",
                "node_id": 0,
                "t": 0,
                "z": max(0, round(float(node["z"]))),
                "y": max(0, round(float(node["y"]))),
                "x": max(0, round(float(node["x"]))),
            }
        ]

    signature = diagnostics._semantic_graph_signature("sample", nodes, [], canonicalizer)
    assert signature["nodes"][0] == (0, 2, 0, 3)


def test_shadow_trace_is_observed_before_production_csv() -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.state["samples"] = {"sample": {}}
    raw_graph = _graph()
    events: list[str] = []

    def trace(nodes: dict[int, dict[str, object]], edges: list[dict[str, object]], **kwargs: object):
        events.append("shadow_trace")
        return _trace_double(nodes, edges, **kwargs)

    recorder.record_shadow_trace({"sample": raw_graph}, trace, strict=True)
    def monkeypatch_csv(path: Path, sample_ids: tuple[str, ...]) -> dict[str, object]:
        events.append("production_csv")
        return {"sample": raw_graph}

    original = diagnostics._csv_signatures
    diagnostics._csv_signatures = monkeypatch_csv  # type: ignore[assignment]
    try:
        recorder.record_csv_and_trace(
            Path("submission.csv"), {"sample": raw_graph}, None, strict=True
        )
    finally:
        diagnostics._csv_signatures = original
    assert events == ["shadow_trace", "production_csv"]
    assert recorder.state["trace_execution"]["mode"] == "shadow_trace"  # type: ignore[index]


def test_semantic_coordinates_are_used_for_trace_and_csv_identity() -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.state["samples"] = {"sample": {}}
    nodes = {0: {"node_id": 0, "t": 0, "z": 1.8, "y": -1.2, "x": 3.0}}
    graph = (nodes, [])

    def canonicalizer(
        dataset: str,
        graph_nodes: dict[int, dict[str, object]],
        graph_edges: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        node = graph_nodes[0]
        return [
            {
                "row_type": "node",
                "node_id": 0,
                "t": 0,
                "z": max(0, round(float(node["z"]))),
                "y": max(0, round(float(node["y"]))),
                "x": max(0, round(float(node["x"]))),
            }
        ]

    recorder.semantic_canonicalizer = canonicalizer
    original = diagnostics._csv_signatures
    def csv_signatures(path: Path, sample_ids: tuple[str, ...]) -> dict[str, object]:
        return {"sample": ({0: {"node_id": 0, "t": 0, "z": 2, "y": 0, "x": 3}}, [])}

    diagnostics._csv_signatures = csv_signatures  # type: ignore[assignment]
    try:
        recorder.record_shadow_trace({"sample": graph}, _trace_double, strict=True)
        recorder.record_csv_and_trace(Path("submission.csv"), {"sample": graph}, None, strict=True)
    finally:
        diagnostics._csv_signatures = original
    assert recorder.state["samples"]["sample"]["source_trace"]["identity_matches_csv"] is True  # type: ignore[index]


def test_child_schema_rejects_nested_invalid_counts_and_hashes() -> None:
    payload = json.loads(_line()[len(diagnostics.DIAGNOSTIC_STDOUT_PREFIX) :])
    payload["ilp_post_per_frame_node_counts"] = {"0": 5}
    with pytest.raises(ValueError, match=r"per-frame|schema|count"):
        diagnostics._parse_child_diagnostic_stdout(
            diagnostics.DIAGNOSTIC_STDOUT_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ("sample",),
        )
    payload = json.loads(_line()[len(diagnostics.DIAGNOSTIC_STDOUT_PREFIX) :])
    payload["ilp_post_topology_sha256"] = "not-a-hash"
    with pytest.raises(ValueError, match=r"hash|schema"):
        diagnostics._parse_child_diagnostic_stdout(
            diagnostics.DIAGNOSTIC_STDOUT_PREFIX + json.dumps(payload, sort_keys=True, separators=(",", ":")),
            ("sample",),
        )


def test_diagnostics_explicitly_records_gt_metric_zero_counts() -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    assert recorder.state["ground_truth_open_count"] == 0
    assert recorder.state["ground_truth_opened"] is False
    assert recorder.state["metric_call_count"] == 0
    assert recorder.state["metric_status"] == "not_run_gt_guard"
    counts = recorder.receipt_counts(None)
    assert counts["ground_truth_open_count"] == 0
    assert counts["ground_truth_opened"] is False
    assert counts["metric_call_count"] == 0
    assert counts["metric_status"] == "not_run_gt_guard"


def test_trace_adapter_rejects_missing_pinned_callable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="trace"):
        diagnostics._prepare_trace_adapter(
            SimpleNamespace(trace_filter_output_graph=None, trace_module_path=None),
            tmp_path / "derived.py",
        )


def test_persisted_reload_is_loaded_again_and_mismatch_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.state["samples"] = {"sample": {}}
    csv_graph = _graph()
    observed = iter([_graph(), _graph(frame=1)])
    monkeypatch.setattr(diagnostics, "_graph_records_or_empty", lambda path, strict: next(observed))
    with pytest.raises(ValueError, match=r"persisted|reload|identity"):
        recorder.record_final_sample(
            "sample",
            tmp_path / "final.geff",
            {"nodes": 1, "edges": 0, "forks": 0},
            {},
            csv_graph,
            strict=True,
            digest_function=lambda path: {},
        )


def test_source_provenance_rejects_mutation(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_path = source_root / "src" / "biohub_pipeline" / "postprocessing.py"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"frozen")
    before = diagnostics._capture_source_provenance({"postprocessing": source_path}, source_root)
    source_path.write_bytes(b"mutated")
    with pytest.raises(ValueError, match=r"source|provenance|mutat"):
        diagnostics._verify_source_provenance(
            {"postprocessing": source_path}, source_root, before
        )


def test_recorder_rejects_missing_child_record_after_instrumentation(tmp_path: Path) -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.predictor_diagnostic_instrumented = True
    recorder.state["samples"] = {}
    with pytest.raises(ValueError, match="child diagnostic record"):
        recorder.record_raw_sample(
            "sample",
            tmp_path / "missing.geff",
            {"nodes": 0, "edges": 0, "forks": 0},
            {},
            strict=False,
        )


def test_recorder_rejects_trace_production_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.state["samples"] = {"sample": {}}
    csv_graph = _graph()
    trace_graph = _graph(frame=1)
    monkeypatch.setattr(diagnostics, "_csv_signatures", lambda path, sample_ids: {"sample": csv_graph})
    monkeypatch.setattr(
        diagnostics,
        "_trace_graph",
        lambda trace, nodes, edges, dataset: (
            trace_graph[0],
            trace_graph[1],
            {
                "stats": {},
                "trace": {
                    "stage_snapshots": [
                        {
                            "stage": "final",
                            "n_nodes": 1,
                            "n_edges": 0,
                            "per_frame_node_counts": {"1": 1},
                        }
                    ]
                },
            },
        ),
    )
    recorder.record_shadow_trace(
        {"sample": csv_graph},
        lambda nodes, edges, **kwargs: (nodes, edges, {}, {}),
        strict=True,
    )
    with pytest.raises(ValueError, match="production CSV"):
        recorder.record_csv_and_trace(
            tmp_path / "submission.csv",
            {"sample": csv_graph},
            lambda nodes, edges, **kwargs: (nodes, edges, {}, {}),
            strict=True,
        )


def test_trace_requires_all_pinned_stage_boundaries() -> None:
    with pytest.raises(ValueError, match=r"sequence|expected|actual|missing"):
        diagnostics._trace_graph(
            lambda nodes, edges, **kwargs: (
                nodes,
                edges,
                {},
                {
                    "stage_snapshots": [
                        {
                            "stage": "linefit_smooth",
                            "n_nodes": len(nodes),
                            "n_edges": len(edges),
                            "per_frame_node_counts": {"0": len(nodes)},
                        }
                    ]
                },
            ),
            _graph()[0],
            _graph()[1],
            dataset="sample",
        )


def test_recorder_rejects_bridge_identity_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.state["samples"] = {"sample": {}}
    csv_graph = _graph()
    monkeypatch.setattr(diagnostics, "_graph_records_or_empty", lambda path, strict: _graph(frame=1))
    with pytest.raises(ValueError, match="bridge GEFF"):
        recorder.record_final_sample(
            "sample",
            tmp_path / "final.geff",
            {"nodes": 1, "edges": 0, "forks": 0},
            {},
            csv_graph,
            strict=True,
        )


def test_diagnostics_finalize_is_write_once_and_does_not_clobber(
    tmp_path: Path,
) -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    path = tmp_path / "diagnostics.json"
    recorder.finalize(path, {"trace_module_sha256": "abc"}, write_json_exclusive)
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        recorder.finalize(path, {"trace_module_sha256": "changed"}, write_json_exclusive)
    assert path.read_bytes() == before


def test_six_frame_done_contract_requires_positive_detector_ilp_and_final_nodes() -> None:
    recorder = diagnostics.DiagnosticRecorder.create(("sample",), "cpu")
    recorder.state["samples"] = {
        "sample": {
            "combined_detector": {"nodes": 1},
            "ilp": {"post": {"nodes": 1}},
            "bridge_final_geff": {"nodes": 1},
        }
    }
    recorder.assert_six_frame_contract()
    recorder.state["samples"]["sample"]["bridge_final_geff"]["nodes"] = 0
    with pytest.raises(ValueError, match="positive node counts"):
        recorder.assert_six_frame_contract()
