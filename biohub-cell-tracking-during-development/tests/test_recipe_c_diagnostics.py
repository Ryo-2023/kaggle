"""Contract tests for the GT-free Recipe C stage diagnostic receipt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import biohub.recipe_c.diagnostics as diagnostics
from biohub.recipe_c.geff_bridge import write_json_exclusive


def _line(sample: str = "sample") -> str:
    payload = {
        "candidate_edges": 4,
        "combined_detector_nodes": 6,
        "ilp_post_edges": 3,
        "ilp_post_nodes": 6,
        "ilp_pre_edges": 4,
        "ilp_pre_nodes": 6,
        "sample_id": sample,
        "schema_version": 1,
    }
    return diagnostics.DIAGNOSTIC_STDOUT_PREFIX + json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    )


def test_child_stdout_parser_requires_one_finite_exact_sample() -> None:
    parsed = diagnostics._parse_child_diagnostic_stdout(_line(), ("sample",))
    assert parsed["sample"]["candidate_edges"] == 4
    with pytest.raises(ValueError, match=r"duplicate|missing|exact"):
        diagnostics._parse_child_diagnostic_stdout(_line() + "\n" + _line(), ("sample",))
    with pytest.raises(ValueError, match=r"finite|schema|JSON"):
        diagnostics._parse_child_diagnostic_stdout(
            diagnostics.DIAGNOSTIC_STDOUT_PREFIX
            + '{"candidate_edges":NaN,"combined_detector_nodes":6,"ilp_post_edges":3,'
            '"ilp_post_nodes":6,"ilp_pre_edges":4,"ilp_pre_nodes":6,"sample_id":"sample",'
            '"schema_version":1}',
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
                    "candidate_edges": 4,
                    "combined_detector_nodes": 6,
                    "ilp_post_edges": 3,
                    "ilp_post_nodes": 6,
                    "ilp_pre_edges": 4,
                    "ilp_pre_nodes": 6,
                    "sample_id": "sample",
                    "schema_version": 1,
                    "unexpected": 0,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
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
    with pytest.raises(ValueError, match="production CSV"):
        recorder.record_csv_and_trace(
            tmp_path / "submission.csv",
            {"sample": csv_graph},
            lambda nodes, edges, **kwargs: (nodes, edges, {}, {}),
            strict=True,
        )


def test_trace_requires_all_pinned_stage_boundaries() -> None:
    with pytest.raises(ValueError, match=r"incomplete|missing"):
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
