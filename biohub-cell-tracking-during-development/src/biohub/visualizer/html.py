"""Self-contained browser UI for visual inspection."""

VIEWER_HTML = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Biohub Visual Inspector</title>
  <style>
    :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #0b0f14; color: #e8eef7; }
    header { padding: 14px 18px; border-bottom: 1px solid #27313d; background: #111821; }
    h1 { margin: 0 0 4px; font-size: 20px; }
    #subtitle { color: #9aabbd; font-size: 13px; }
    main { padding: 14px; display: grid; gap: 12px; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; }
    .metric { background: #111821; border: 1px solid #27313d; border-radius: 8px; padding: 9px 11px; }
    .metric .label { color: #91a2b4; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; }
    .metric .value { margin-top: 3px; font-size: 17px; font-variant-numeric: tabular-nums; }
    .panels { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .panel { min-width: 0; background: #111821; border: 1px solid #27313d; border-radius: 10px; overflow: hidden; }
    .panel-title { padding: 9px 12px; border-bottom: 1px solid #27313d; color: #c7d2df; font-size: 13px; font-weight: 650; }
    .canvas-wrap { position: relative; display: grid; place-items: center; min-height: 320px; background: #05080c; overflow: auto; }
    canvas { max-width: 100%; height: auto; image-rendering: pixelated; }
    .controls { display: grid; gap: 10px; background: #111821; border: 1px solid #27313d; border-radius: 10px; padding: 12px; }
    .row { display: grid; grid-template-columns: 88px 1fr 64px; gap: 10px; align-items: center; }
    input[type="range"] { width: 100%; }
    button { border: 1px solid #3c4a59; border-radius: 7px; padding: 7px 11px; background: #19232f; color: #e8eef7; cursor: pointer; }
    button:hover { background: #243244; }
    .layers { display: flex; flex-wrap: wrap; gap: 9px 16px; align-items: center; }
    .layers label { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; }
    .swatch { width: 11px; height: 11px; border-radius: 50%; display: inline-block; }
    .pred { background: #40c4ff; } .gt { background: #ffd54f; }
    .tp { background: #36d17c; } .fp { background: #ff5d73; } .fn { background: #5596ff; }
    .muted { color: #91a2b4; font-size: 12px; }
    #error { display: none; padding: 10px 12px; border: 1px solid #7e2f3e; border-radius: 8px; background: #32151c; color: #ffc7d1; white-space: pre-wrap; }
    @media (max-width: 900px) { .panels { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
<header>
  <h1>Biohub Visual Inspector</h1>
  <div id="subtitle">Loading dataset…</div>
</header>
<main>
  <div id="error"></div>
  <section class="metrics" id="metrics"></section>
  <section class="panels">
    <article class="panel">
      <div class="panel-title">Input microscopy slice</div>
      <div class="canvas-wrap"><canvas id="input-canvas"></canvas></div>
    </article>
    <article class="panel">
      <div class="panel-title">Model output overlay</div>
      <div class="canvas-wrap"><canvas id="output-canvas"></canvas></div>
    </article>
  </section>
  <section class="controls">
    <div class="row">
      <button id="play-button" type="button">▶ Play</button>
      <input id="time-slider" type="range" min="0" max="0" value="0" step="1">
      <output id="time-value">t=0</output>
    </div>
    <div class="row">
      <span>Z plane</span>
      <input id="z-slider" type="range" min="0" max="0" value="0" step="1">
      <output id="z-value">z=0</output>
    </div>
    <div class="row">
      <span>Z radius</span>
      <input id="radius-slider" type="range" min="0" max="5" value="1" step="0.25">
      <output id="radius-value">±1</output>
    </div>
    <div class="layers">
      <strong>Layers</strong>
      <label><input type="checkbox" data-layer="prediction-node" checked><span class="swatch pred"></span>Predicted nodes</label>
      <label><input type="checkbox" data-layer="ground-truth-node" checked><span class="swatch gt"></span>Ground truth</label>
      <label><input type="checkbox" data-layer="tp" checked><span class="swatch tp"></span>TP links</label>
      <label><input type="checkbox" data-layer="fp" checked><span class="swatch fp"></span>FP links</label>
      <label><input type="checkbox" data-layer="fn" checked><span class="swatch fn"></span>FN links</label>
      <label><input type="checkbox" data-layer="prediction-edge"><span class="swatch pred"></span>Unscored links</label>
    </div>
    <div class="muted">The left panel is the raw input. The right panel uses the same slice and draws model nodes and outgoing t→t+1 motion vectors. When ground truth is available, link colors follow the official metric's TP/FP/FN classification.</div>
  </section>
</main>
<script>
(() => {
  const state = { meta: null, t: 0, z: 0, radius: 1, playing: false, timer: null };
  const inputCanvas = document.getElementById('input-canvas');
  const outputCanvas = document.getElementById('output-canvas');
  const inputCtx = inputCanvas.getContext('2d');
  const outputCtx = outputCanvas.getContext('2d');
  const layerEnabled = category => {
    const boxes = [...document.querySelectorAll(`[data-layer="${category}"]`)];
    return boxes.some(box => box.checked);
  };
  const categoryColor = { prediction: '#40c4ff', ground_truth: '#ffd54f', tp: '#36d17c', fp: '#ff5d73', fn: '#5596ff' };

  function showError(error) {
    const box = document.getElementById('error');
    box.textContent = String(error?.stack || error);
    box.style.display = 'block';
  }

  function metricCard(label, value) {
    const rendered = value === null || value === undefined || Number.isNaN(value) ? '—' : value;
    return `<div class="metric"><div class="label">${label}</div><div class="value">${rendered}</div></div>`;
  }

  function renderMetrics(metrics) {
    const cards = [
      ['Edge Jaccard', metrics.edge_jaccard?.toFixed?.(4)],
      ['Division Jaccard', metrics.division_jaccard?.toFixed?.(4)],
      ['Edge TP', metrics.edge_tp], ['Edge FP', metrics.edge_fp], ['Edge FN', metrics.edge_fn],
      ['Division TP', metrics.division_tp], ['Division FP', metrics.division_fp], ['Division FN', metrics.division_fn],
      ['Predicted nodes', metrics.num_pred_nodes],
    ];
    document.getElementById('metrics').innerHTML = cards.map(([k, v]) => metricCard(k, v)).join('');
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}: ${await response.text()}`);
    return response.json();
  }

  async function loadImage() {
    const image = new Image();
    image.src = `/api/frame?t=${state.t}&z=${state.z}&_=${Date.now()}`;
    await image.decode();
    for (const canvas of [inputCanvas, outputCanvas]) {
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
    }
    inputCtx.drawImage(image, 0, 0);
    outputCtx.drawImage(image, 0, 0);
  }

  function drawArrow(ctx, edge, color) {
    const dx = edge.x2 - edge.x1;
    const dy = edge.y2 - edge.y1;
    const length = Math.hypot(dx, dy) || 1;
    const ux = dx / length, uy = dy / length;
    const head = Math.min(8, Math.max(4, length * 0.2));
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(edge.x1, edge.y1); ctx.lineTo(edge.x2, edge.y2); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(edge.x2, edge.y2);
    ctx.lineTo(edge.x2 - ux * head - uy * head * 0.55, edge.y2 - uy * head + ux * head * 0.55);
    ctx.lineTo(edge.x2 - ux * head + uy * head * 0.55, edge.y2 - uy * head - ux * head * 0.55);
    ctx.closePath(); ctx.fill();
  }

  function drawNode(ctx, node) {
    const layer = node.kind === 'prediction' ? 'prediction-node' : 'ground-truth-node';
    if (!layerEnabled(layer)) return;
    const color = categoryColor[node.kind];
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = node.kind === 'ground_truth' ? 2 : 2.5;
    ctx.beginPath(); ctx.arc(node.x, node.y, node.kind === 'ground_truth' ? 5 : 4, 0, Math.PI * 2); ctx.stroke();
    if (node.kind === 'prediction') {
      ctx.globalAlpha = 0.28; ctx.fill(); ctx.globalAlpha = 1;
    }
  }

  async function render() {
    await loadImage();
    const overlay = await fetchJson(`/api/overlay?t=${state.t}&z=${state.z}&z_radius=${state.radius}`);
    for (const edge of overlay.edges) {
      const layer = edge.category === 'prediction' ? 'prediction-edge' : edge.category;
      if (layerEnabled(layer)) drawArrow(outputCtx, edge, categoryColor[edge.category]);
    }
    for (const node of overlay.nodes) drawNode(outputCtx, node);
    document.getElementById('time-value').textContent = `t=${state.t}`;
    document.getElementById('z-value').textContent = `z=${state.z}`;
    document.getElementById('radius-value').textContent = `±${state.radius}`;
  }

  function bindRange(id, field) {
    const element = document.getElementById(id);
    element.addEventListener('input', () => {
      state[field] = Number(element.value);
      render().catch(showError);
    });
  }

  async function init() {
    state.meta = await fetchJson('/api/meta');
    state.t = 0;
    state.z = Math.floor(state.meta.shape[1] / 2);
    document.getElementById('subtitle').textContent = `${state.meta.dataset} · shape ${state.meta.shape.join(' × ')}`;
    const timeSlider = document.getElementById('time-slider');
    const zSlider = document.getElementById('z-slider');
    timeSlider.max = Math.max(0, state.meta.shape[0] - 1);
    zSlider.max = Math.max(0, state.meta.shape[1] - 1);
    zSlider.value = state.z;
    renderMetrics(state.meta.metrics || {});
    bindRange('time-slider', 't');
    bindRange('z-slider', 'z');
    bindRange('radius-slider', 'radius');
    for (const box of document.querySelectorAll('[data-layer]')) box.addEventListener('change', () => render().catch(showError));
    document.getElementById('play-button').addEventListener('click', event => {
      state.playing = !state.playing;
      event.currentTarget.textContent = state.playing ? '❚❚ Pause' : '▶ Play';
      clearInterval(state.timer);
      if (state.playing) state.timer = setInterval(() => {
        state.t = (state.t + 1) % state.meta.shape[0];
        timeSlider.value = state.t;
        render().catch(showError);
      }, 350);
    });
    await render();
  }

  init().catch(showError);
})();
</script>
</body>
</html>'''
