/* ══════════════════════════════════════════════════════════════════════════════
   Interactive 3D view of the autonomous trading network (real layer sizes):
     60×7 bar sequence ─► GRU(48, recurrent ring) ─┐
                                                    ├─► fusion head (64) ─► P(long), P(short)
     18 market features ─► MLP(64) ────────────────┘
   Drag to rotate · scroll / pinch / +− to zoom · hover a layer to highlight it ·
   click a layer to fly to it and open its explainer, filled with live data for
   the chosen stock from /api/ml/explain (inputs + occlusion attribution).
   ══════════════════════════════════════════════════════════════════════════════ */
(function () {
  const canvas = document.getElementById('nn3d');
  if (!canvas || !window.FX || !FX.ok()) {
    const p = document.getElementById('nn3d-wrap'); if (p) p.style.display = 'none';
    return;
  }
  const renderer = FX.makeRenderer(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 2, 0.1, 200);
  const rig = FX.orbit(camera, canvas, { radius: 18.5, theta: 0.42, phi: 1.28, target: new THREE.Vector3(0, 0, 0), spin: 0.07,
                                          minPhi: 0.5, maxPhi: 2.0, minRadius: 4, maxRadius: 48, zoom: true });

  const C = { seq: '#4cc9ff', gru: '#62e0ff', tab: '#a98bff', mlp: '#c3a6ff', head: '#e8edf7', long: '#2fd47e', short: '#ff5470' };
  const layers = {};   // id → { pts?, mat?, baseSize, hit, outline, center, label, focusRadius }

  function addLayer(id, positions, color, size, { parent = scene, focusRadius = 9 } = {}) {
    let pts = null, mat = null;
    if (positions) {
      mat = new THREE.PointsMaterial({ size, color, map: FX.glowTexture('#ffffff'), transparent: true, opacity: .9,
        depthWrite: false, blending: THREE.AdditiveBlending });
      pts = new THREE.Points(new THREE.BufferGeometry().setFromPoints(positions), mat);
      parent.add(pts);
    }
    layers[id] = { id, pts, mat, baseSize: size, focusRadius, color };
    return layers[id];
  }
  // Invisible box for picking + a wireframe outline shown on hover/selection
  function addHitBox(id, center, dims) {
    const geo = new THREE.BoxGeometry(dims.x, dims.y, dims.z);
    const hit = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ visible: false }));
    hit.position.copy(center); hit.userData.layer = id; scene.add(hit);
    const outline = new THREE.LineSegments(new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({ color: layers[id].color, transparent: true, opacity: 0 }));
    outline.position.copy(center); scene.add(outline);
    Object.assign(layers[id], { hit, outline, center: center.clone() });
  }
  const labels = [];   // [sprite, layer id] — other layers' labels fade while one is focused
  function label(text, pos, color, id) {
    const s = FX.labelSprite(text, { color, size: 38, weight: 600 });
    s.scale.multiplyScalar(.62); s.position.copy(pos); scene.add(s);
    labels.push([s, id]);
    return s;
  }

  // ── Layers ───────────────────────────────────────────────────────────────
  const seq = [];
  for (let t = 0; t < 60; t++) for (let c = 0; c < 7; c++)
    seq.push(new THREE.Vector3(-10.5 + c * .18, 2.6 + (c - 3) * .42, (t - 29.5) * .14));
  addLayer('seq', seq, C.seq, .16);
  addHitBox('seq', new THREE.Vector3(-10, 2.6, 0), new THREE.Vector3(1.8, 3.4, 8.8));
  label('60 one-min bars × 7', new THREE.Vector3(-10, 4.9, 0), C.seq, 'seq');

  const gruGroup = new THREE.Group(); gruGroup.position.set(-4.6, 2.6, 0); scene.add(gruGroup);
  const gru = [];
  for (let i = 0; i < 48; i++) { const a = i / 48 * Math.PI * 2; gru.push(new THREE.Vector3(0, Math.cos(a) * 1.7, Math.sin(a) * 1.7)); }
  addLayer('gru', gru, C.gru, .3, { parent: gruGroup });
  const gruRing = new THREE.Mesh(new THREE.TorusGeometry(1.7, .012, 8, 96),
    new THREE.MeshBasicMaterial({ color: C.gru, transparent: true, opacity: .35 }));
  gruRing.rotation.y = Math.PI / 2; gruGroup.add(gruRing);
  addHitBox('gru', gruGroup.position, new THREE.Vector3(1.4, 4, 4));
  label('GRU · 48 (recurrent)', new THREE.Vector3(-4.6, 5.0, 0), C.gru, 'gru');

  const tab = [];
  for (let i = 0; i < 18; i++) tab.push(new THREE.Vector3(-10.2, -2.8 + (i % 6 - 2.5) * .5, (Math.floor(i / 6) - 1) * .6));
  addLayer('tab', tab, C.tab, .3);
  addHitBox('tab', new THREE.Vector3(-10.2, -2.8, 0), new THREE.Vector3(1.4, 3.4, 2.2));
  label('18 market features', new THREE.Vector3(-10, -5.0, 0), C.tab, 'tab');

  const mlp = [];
  for (let i = 0; i < 64; i++) mlp.push(new THREE.Vector3(-4.6, -2.8 + (i % 8 - 3.5) * .36, (Math.floor(i / 8) - 3.5) * .36));
  addLayer('mlp', mlp, C.mlp, .24);
  addHitBox('mlp', new THREE.Vector3(-4.6, -2.8, 0), new THREE.Vector3(1.4, 3.2, 3.2));
  label('MLP · 64', new THREE.Vector3(-4.6, -5.0, 0), C.mlp, 'mlp');

  const head = [];
  for (let i = 0; i < 64; i++) head.push(new THREE.Vector3(1.6, (i % 8 - 3.5) * .4, (Math.floor(i / 8) - 3.5) * .4));
  addLayer('head', head, C.head, .24);
  addHitBox('head', new THREE.Vector3(1.6, 0, 0), new THREE.Vector3(1.4, 3.6, 3.6));
  label('fusion head · 64', new THREE.Vector3(1.6, 2.6, 0), '#c9d4ee', 'head');

  const outs = [new THREE.Vector3(6.4, 1.3, 0), new THREE.Vector3(6.4, -1.3, 0)];
  addLayer('out', null, C.long, 0, { focusRadius: 8 });
  const outMeshes = outs.map((p, i) => {
    const hex = i === 0 ? C.long : C.short;
    const core = new THREE.Mesh(new THREE.SphereGeometry(.34, 24, 18), new THREE.MeshBasicMaterial({ color: hex }));
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: FX.glowTexture(hex), transparent: true,
      depthWrite: false, blending: THREE.AdditiveBlending }));
    halo.scale.setScalar(3); core.position.copy(p); halo.position.copy(p);
    scene.add(core, halo);
    return { core, halo, val: .45 };
  });
  addHitBox('out', new THREE.Vector3(6.6, 0, 0), new THREE.Vector3(2, 4.2, 1.8));
  label('P(long ✓)', new THREE.Vector3(8.0, 1.3, 0), C.long, 'out');
  label('P(short ✓)', new THREE.Vector3(8.1, -1.3, 0), C.short, 'out');

  // ── Connections (sampled — the real net is fully connected) ──────────────
  // gruLive holds the ring's current world positions. It is updated in place
  // each frame, and edges hold references to these same vectors, so pulses
  // and lines follow the spinning ring without any per-frame allocation.
  gruGroup.updateMatrixWorld();
  const gruLive = gru.map(p => p.clone().applyMatrix4(gruGroup.matrixWorld));
  function updateGruLive() {
    gruGroup.updateMatrixWorld();
    for (let i = 0; i < gru.length; i++) gruLive[i].copy(gru[i]).applyMatrix4(gruGroup.matrixWorld);
  }
  const edges = [];
  function connect(from, to, n, stage, kind) {
    for (let i = 0; i < n; i++) edges.push([from[Math.random() * from.length | 0], to[Math.random() * to.length | 0], stage, kind]);
  }
  connect(seq, gruLive, 140, 0, 'seq'); connect(tab, mlp, 110, 0, 'tab');
  connect(gruLive, head, 110, 1, 'seq'); connect(mlp, head, 110, 1, 'tab');
  connect(head, outs, 60, 2, 'out');
  const linePos = new Float32Array(edges.length * 6);
  const writeEdge = i => { const [a, b] = edges[i]; linePos.set([a.x, a.y, a.z, b.x, b.y, b.z], i * 6); };
  edges.forEach((_, i) => writeEdge(i));
  const gruSet = new Set(gruLive);
  const gruEdgeIdx = edges.map((e, i) => (gruSet.has(e[0]) || gruSet.has(e[1])) ? i : -1).filter(i => i >= 0);
  const lineGeo = new THREE.BufferGeometry(); lineGeo.setAttribute('position', new THREE.BufferAttribute(linePos, 3));
  const lineMat = new THREE.LineBasicMaterial({ color: 0x6f86c9, transparent: true, opacity: .09, depthWrite: false, blending: THREE.AdditiveBlending });
  scene.add(new THREE.LineSegments(lineGeo, lineMat));

  // ── Pulses travelling input → output ────────────────────────────────────
  const byStage = [0, 1, 2].map(s => edges.filter(e => e[2] === s));
  const P = 140, pulses = [];
  const pPos = new Float32Array(P * 3), pCol = new Float32Array(P * 3);
  const cA = new THREE.Color(C.seq), cB = new THREE.Color(C.tab), cOut = [new THREE.Color(C.long), new THREE.Color(C.short)];
  function spawn(p, stage = 0) { const l = byStage[stage]; p.edge = l[Math.random() * l.length | 0]; p.stage = stage; p.t = 0; p.speed = .5 + Math.random() * .7; }
  for (let i = 0; i < P; i++) { const p = {}; spawn(p, Math.random() * 3 | 0); p.t = Math.random(); pulses.push(p); }
  const pulseGeo = new THREE.BufferGeometry();
  pulseGeo.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
  pulseGeo.setAttribute('color', new THREE.BufferAttribute(pCol, 3));
  scene.add(new THREE.Points(pulseGeo, new THREE.PointsMaterial({ size: .32, vertexColors: true, map: FX.glowTexture('#ffffff'),
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending })));

  // ── Picking ─────────────────────────────────────────────────────────────
  const ray = new THREE.Raycaster(), mouse = new THREE.Vector2(-9, -9);
  const tip = document.getElementById('nn3d-tip');
  const hitBoxes = Object.values(layers).map(l => l.hit);
  let hovered = null, selected = null;
  const NAMES = { seq: 'Input · 60 one-minute bars', gru: 'GRU · sequence memory', tab: 'Input · 18 market features',
                  mlp: 'MLP · feature processing', head: 'Fusion head', out: 'Outputs · P(long), P(short)' };
  let pointerDirty = false;
  canvas.addEventListener('pointermove', e => {
    pointerDirty = true;
    const r = canvas.getBoundingClientRect();
    mouse.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    tip.style.left = (e.clientX - r.left) + 'px'; tip.style.top = (e.clientY - r.top) + 'px';
  });
  canvas.addEventListener('pointerleave', () => { mouse.set(-9, -9); pointerDirty = true; });
  // Pick at the click position itself — don't rely on the per-frame hover
  // state, which can lag behind a fast click on a slow frame.
  function pickAt(e) {
    const r = canvas.getBoundingClientRect();
    const m = new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(m, camera);
    const hit = ray.intersectObjects(hitBoxes)[0];
    return hit ? hit.object.userData.layer : null;
  }
  canvas.addEventListener('click', e => {
    if (rig.wasDrag()) return;
    const id = pickAt(e);
    if (id) select(id);
  });

  function select(id) {
    selected = id;
    const l = layers[id];
    rig.flyTo(l.center, l.focusRadius);
    rig.paused = true;
    openInfo(id);
  }
  function deselect() {
    selected = null; rig.paused = false; rig.reset();
    document.getElementById('nn-info').classList.remove('open');
  }

  // ── Info panel ──────────────────────────────────────────────────────────
  const fmt = (v, d = 3) => v == null ? '—' : (+v).toFixed(d);
  const pp = v => (v >= 0 ? '+' : '') + (v * 100).toFixed(1) + ' pts';
  let explain = null, explainSym = null;

  async function loadExplain(sym) {
    try {
      const r = await fetch('/api/ml/explain' + (sym ? '?symbol=' + encodeURIComponent(sym) : ''));
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      explain = await r.json(); explainSym = explain.symbol;
      outMeshes[0].val = explain.p_long; outMeshes[1].val = explain.p_short;
      const el = document.getElementById('nn3d-reading');
      if (el) el.innerHTML = `showing <b>${explain.symbol}</b> · P(long) ${fmt(explain.p_long)} · P(short) ${fmt(explain.p_short)} · threshold ${explain.threshold}`;
    } catch (e) { explain = { error: String(e.message || e) }; }
    if (selected) renderInfo(selected);
  }

  function leanBar(v, max) {
    const w = Math.min(50, Math.abs(v) / (max || 1) * 50);
    const col = v >= 0 ? 'var(--green)' : 'var(--red)';
    return `<div class="lean"><div class="lean-mid"></div><div class="lean-fill" style="${v >= 0 ? 'left:50%' : `left:${50 - w}%`};width:${w}%;background:${col}"></div></div>`;
  }

  function sparkline(closes) {
    if (!closes || closes.length < 2) return '';
    const W = 300, H = 70, lo = Math.min(...closes), hi = Math.max(...closes), span = hi - lo || 1;
    const pts = closes.map((c, i) => `${(i / (closes.length - 1) * W).toFixed(1)},${(H - 4 - (c - lo) / span * (H - 8)).toFixed(1)}`).join(' ');
    const up = closes[closes.length - 1] >= closes[0];
    return `<svg viewBox="0 0 ${W} ${H}" class="nn-spark" role="img" aria-label="Last ${closes.length} one-minute closes">
      <polyline points="${pts}" fill="none" stroke="${up ? 'var(--green)' : 'var(--red)'}" stroke-width="1.6"/></svg>
      <div class="nn-axis"><span>60 min ago</span><span>$${lo.toFixed(2)} – $${hi.toFixed(2)}</span><span>now</span></div>`;
  }

  function windowsBlock(e) {
    const m = Math.max(...e.sequence_windows.map(w => Math.abs(w.lean)), .005);
    return `<div class="nn-sub">Which part of the last hour mattered</div>
      <table class="nn-table">${e.sequence_windows.map(w => `<tr><td>${w.bars_ago} min ago</td><td class="nn-lean-cell">${leanBar(w.lean, m)}</td>
        <td class="mono ${w.lean >= 0 ? 'pos' : 'neg'}">${pp(w.lean)}</td></tr>`).join('')}</table>`;
  }

  function featuresBlock(e, limit) {
    const fs = limit ? e.features.slice(0, limit) : e.features;
    const m = Math.max(...e.features.map(f => Math.abs(f.lean)), .005);
    return `<table class="nn-table">${fs.map(f => `<tr title="${f.help.replace(/"/g, '&quot;')}">
        <td>${f.label}<div class="nn-help">${f.help}</div></td><td class="mono">${fmt(f.value, 2)}</td>
        <td class="nn-lean-cell">${leanBar(f.lean, m)}</td><td class="mono ${f.lean >= 0 ? 'pos' : 'neg'}">${pp(f.lean)}</td></tr>`).join('')}</table>`;
  }

  const STATIC = {
    seq: { params: 'no weights (input)', what: `The raw material: the stock's last 60 one-minute candles. Each candle becomes 7 numbers —
      return, body, upper wick, lower wick, range, relative volume, and a "real bar" flag — all scaled by the stock's normal volatility,
      and mirrored for shorts, so the network sees the <i>shape</i> of price action rather than the price level.` },
    gru: { params: '8,208 weights', what: `A Gated Recurrent Unit reads the 60 candles in order, one minute at a time, updating a
      48-number memory as it goes (the spinning ring). Gates decide what to keep and what to forget, so it can pick up patterns like
      "three rising candles on growing volume". Its final memory summarises the last hour.` },
    tab: { params: 'no weights (input)', what: `Hand-engineered context the bars alone don't show directly: momentum over several
      horizons, distance from VWAP, RSI, moving-average gap, volume surges, where price sits in the day's range, and time of day.` },
    mlp: { params: '5,376 weights', what: `Two dense layers (18 → 64 → 64, GELU activations, dropout during training) that turn the
      18 features into 64 learned combinations — e.g. "far below VWAP <i>and</i> oversold <i>and</i> volume spiking".` },
    head: { params: '7,362 weights (+96 LayerNorm)', what: `Concatenates the GRU's 48-number memory with the MLP's 64 numbers and
      mixes them (112 → 64 → 2). This is where price-shape and context get weighed against each other.` },
    out: { params: 'sigmoid outputs', what: `Two independent probabilities: that a <b>long</b> entered now would be profitable after its
      60-minute window (stop 1.5×ATR, target 2× the stop), and the same for a <b>short</b>. The bot trades the larger one only if it
      clears the threshold learned on validation data.` },
  };

  function renderInfo(id) {
    const body = document.getElementById('nn-info-body');
    document.getElementById('nn-info-title').textContent = NAMES[id];
    document.getElementById('nn-info-params').textContent = STATIC[id].params;
    let live = '';
    const e = explain;
    if (!e) live = '<div class="nn-sub">Loading live data…</div>';
    else if (e.error) live = `<div class="nn-sub">Live data unavailable: ${e.error}</div>`;
    else if (id === 'seq') live = `<div class="nn-sub">${e.symbol}'s last hour</div>${sparkline(e.closes)}${windowsBlock(e)}`;
    else if (id === 'gru') live = `<div class="nn-kv"><span>Whole bar sequence pushes toward</span>
        <b class="${e.branch_lean.sequence >= 0 ? 'pos' : 'neg'}">${e.branch_lean.sequence >= 0 ? 'LONG' : 'SHORT'} ${pp(e.branch_lean.sequence)}</b></div>${windowsBlock(e)}
        <div class="nn-note">Recent minutes dominating is normal: the GRU's memory fades older bars.</div>`;
    else if (id === 'tab') live = `<div class="nn-sub">${e.symbol} right now — sorted by influence</div>${featuresBlock(e)}`;
    else if (id === 'mlp') live = `<div class="nn-kv"><span>All 18 features together push toward</span>
        <b class="${e.branch_lean.features >= 0 ? 'pos' : 'neg'}">${e.branch_lean.features >= 0 ? 'LONG' : 'SHORT'} ${pp(e.branch_lean.features)}</b></div>
        <div class="nn-sub">Top drivers</div>${featuresBlock(e, 5)}`;
    else if (id === 'head') live = `<div class="nn-kv"><span>Price shape (GRU branch)</span><b class="${e.branch_lean.sequence >= 0 ? 'pos' : 'neg'}">${pp(e.branch_lean.sequence)}</b></div>
        <div class="nn-kv"><span>Context (feature branch)</span><b class="${e.branch_lean.features >= 0 ? 'pos' : 'neg'}">${pp(e.branch_lean.features)}</b></div>
        <div class="nn-note">Positive = pushes toward a long, negative = toward a short.</div>`;
    else if (id === 'out') {
      const best = e.p_long >= e.p_short ? ['LONG', e.p_long] : ['SHORT', e.p_short];
      const go = best[1] >= e.threshold;
      live = `<div class="nn-probs">
          <div><span>P(long ✓)</span><b class="pos">${fmt(e.p_long)}</b><div class="pbar"><div class="fill" style="width:${e.p_long * 100}%;background:var(--green)"></div><div class="thr" style="left:${e.threshold * 100}%"></div></div></div>
          <div><span>P(short ✓)</span><b class="neg">${fmt(e.p_short)}</b><div class="pbar"><div class="fill" style="width:${e.p_short * 100}%;background:var(--red)"></div><div class="thr" style="left:${e.threshold * 100}%"></div></div></div>
        </div>
        <div class="nn-verdict ${go ? (best[0] === 'LONG' ? 'pos' : 'neg') : ''}">${go ? `Would go <b>${best[0]}</b> ${e.symbol} (p ${fmt(best[1])} ≥ ${e.threshold})`
          : `No trade — strongest side ${best[0]} at ${fmt(best[1])} is below the ${e.threshold} threshold`}</div>
        <div class="nn-note">Yellow line = threshold. Model trained ${e.model_trained_at.slice(0, 10)}. Walk-forward tests show no edge after costs yet — see below.</div>`;
    }
    body.innerHTML = `<p class="nn-what">${STATIC[id].what}</p>${live}`;
  }

  function openInfo(id) {
    document.getElementById('nn-info').classList.add('open');
    renderInfo(id);
    if (!explain || explain.error) loadExplain(explainSym);
  }

  // Controls
  document.getElementById('nn-info-close').onclick = deselect;
  document.getElementById('nn-zoom-in').onclick = () => rig.zoomBy(0.75);
  document.getElementById('nn-zoom-out').onclick = () => rig.zoomBy(1.33);
  document.getElementById('nn-reset').onclick = deselect;
  document.querySelectorAll('[data-nn-layer]').forEach(b => b.onclick = () => select(b.dataset.nnLayer));
  const symSel = document.getElementById('nn-symbol');
  symSel.onchange = () => loadExplain(symSel.value);
  window.addEventListener('keydown', e => { if (e.key === 'Escape' && selected) deselect(); });

  (async () => {
    await loadExplain();
    if (explain && explain.symbols) {
      symSel.innerHTML = explain.symbols.map(s => `<option ${s === explainSym ? 'selected' : ''}>${s}</option>`).join('');
    }
  })();
  setInterval(() => loadExplain(explainSym), 30000);

  // ── Loop ────────────────────────────────────────────────────────────────
  // While the info drawer is open, shift the projection so the focused layer
  // renders in the visible area to the left of the drawer.
  const drawer = document.getElementById('nn-info');
  let viewShift = 0;
  function applyViewShift(dt) {
    const want = drawer.classList.contains('open') ? drawer.offsetWidth / 2 : 0;
    viewShift += (want - viewShift) * Math.min(1, dt * 6);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (Math.abs(viewShift) < .5) camera.clearViewOffset();
    else camera.setViewOffset(w, h, viewShift, 0, w, h);
  }

  FX.loop((dt, t) => {
    FX.fit(renderer, camera, canvas);
    rig.fitAspect(camera.aspect, 1.8);
    applyViewShift(dt);
    rig.update(dt);
    gruGroup.rotation.x += dt * .5;
    updateGruLive();
    for (const i of gruEdgeIdx) writeEdge(i);
    lineGeo.attributes.position.needsUpdate = true;
    for (let i = 0; i < P; i++) {
      const p = pulses[i];
      p.t += dt * p.speed;
      if (p.t >= 1) spawn(p, (p.stage + 1) % 3);
      const a = p.edge[0], b = p.edge[1];
      pPos[i * 3] = a.x + (b.x - a.x) * p.t; pPos[i * 3 + 1] = a.y + (b.y - a.y) * p.t; pPos[i * 3 + 2] = a.z + (b.z - a.z) * p.t;
      const c = p.stage === 2 ? cOut[b === outs[0] ? 0 : 1] : (a.y > 0 ? cA : cB);
      pCol[i * 3] = c.r; pCol[i * 3 + 1] = c.g; pCol[i * 3 + 2] = c.b;
    }
    pulseGeo.attributes.position.needsUpdate = true; pulseGeo.attributes.color.needsUpdate = true;
    outMeshes.forEach((o, i) => {
      const k = .6 + (o.val - .35) * 4 + Math.sin(t * 2.2 + i) * .08;
      o.halo.scale.setScalar(Math.max(1.6, 3.2 * k));
      o.core.scale.setScalar(Math.max(.7, k));
    });

    // hover / selection highlight
    let h = hovered;
    if (pointerDirty || (mouse.x > -2 && (rig.dragging || rig.fly || (!rig.paused && rig.idle > 2.5)))) {
      pointerDirty = false;
      ray.setFromCamera(mouse, camera);
      const hit = ray.intersectObjects(hitBoxes)[0];
      h = hit ? hit.object.userData.layer : null;
    }
    if (h !== hovered) {
      hovered = h;
      canvas.style.cursor = h ? 'pointer' : '';
      if (h) { tip.textContent = NAMES[h] + ' — click to explore'; tip.classList.add('show'); } else tip.classList.remove('show');
    }
    for (const id in layers) {
      const l = layers[id], on = id === hovered || id === selected;
      l.outline.material.opacity += ((on ? (id === selected ? .55 : .35) : 0) - l.outline.material.opacity) * Math.min(1, dt * 8);
      if (l.mat) {
        l.mat.size += ((on ? l.baseSize * 1.6 : l.baseSize) - l.mat.size) * Math.min(1, dt * 8);
        l.mat.opacity = selected && !on ? .35 : .9;
      }
    }
    lineMat.opacity = selected ? .05 : .09;
    for (const [sp, id] of labels) {
      const want = selected && id !== selected ? 0 : 1;
      sp.material.opacity += (want - sp.material.opacity) * Math.min(1, dt * 6);
      sp.visible = sp.material.opacity > .02;
    }
    renderer.render(scene, camera);
  }, canvas);
})();
