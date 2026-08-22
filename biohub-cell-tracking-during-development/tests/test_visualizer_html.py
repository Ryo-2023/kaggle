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


def test_unscored_prediction_links_are_visible_by_default() -> None:
    """Without ground truth every link is unscored, so the layer must start on."""

    assert '<input type="checkbox" data-layer="prediction-edge" checked>' in VIEWER_HTML


def test_overlay_is_drawn_on_its_own_screen_space_layer() -> None:
    """Marks must keep a constant on-screen size.

    Sizing them in image pixels made a 2048 px frame - scaled to fit the panel -
    render node rings at ~1.4 px and links at ~0.7 px, i.e. invisible.
    """

    assert 'id="overlay-canvas"' in VIEWER_HTML
    assert "NODE_RADIUS_CSS" in VIEWER_HTML
    assert "devicePixelRatio" in VIEWER_HTML


def test_frames_wait_on_the_load_event_rather_than_decode() -> None:
    """`image.decode()` is deferred while the document is hidden, so a viewer
    opened in a background tab stayed blank forever and never recovered."""

    assert "await image.decode" not in VIEWER_HTML
    assert "image.onload = resolve" in VIEWER_HTML


def test_playback_and_scrubbing_cannot_stack_up_renders() -> None:
    """A fixed interval fired faster than a full-size frame could load, so
    frames overlapped and arrived out of order; renders are now serialized."""

    assert "setInterval(" not in VIEWER_HTML
    assert "setTimeout(playStep" in VIEWER_HTML
    assert "renderToken" in VIEWER_HTML

