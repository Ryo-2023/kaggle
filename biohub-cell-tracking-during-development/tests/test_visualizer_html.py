from biohub.visualizer.html import VIEWER_HTML


def test_viewer_html_has_input_output_panels_and_visual_controls() -> None:
    assert 'id="input-canvas"' in VIEWER_HTML
    assert 'id="output-canvas"' in VIEWER_HTML
    assert 'id="time-slider"' in VIEWER_HTML
    assert 'id="z-slider"' in VIEWER_HTML
    assert 'id="play-button"' in VIEWER_HTML
    assert 'data-layer="tp"' in VIEWER_HTML
    assert 'data-layer="fp"' in VIEWER_HTML
    assert 'data-layer="fn"' in VIEWER_HTML


def test_viewer_html_separates_prediction_node_and_unscored_edge_toggles() -> None:
    assert 'data-layer="prediction-node"' in VIEWER_HTML
    assert 'data-layer="prediction-edge"' in VIEWER_HTML
