/* ══════════════════════════════════════════════════════════════════════════════
   Market Galaxy — every watched stock as a glowing body in 3D space.
     height (y)  → signal score 0–100
     x           → distance from VWAP (%)
     z           → change since the open (%)
     colour      → signal (green / red / neutral leaning long or short)
     size        → relative volume
   Drag to rotate · hover for details · click to open the stock.
   Data: /api/signals (polled) + live SSE signal_update events.
   ══════════════════════════════════════════════════════════════════════════════ */
(function () {
  const canvas = document.getElementById('galaxy');
  if (!canvas || !window.FX || !FX.ok()) {
    const wrap = document.getElementById('galaxy-wrap');
    if (wrap) wrap.style.display = 'none';
    return;
  }

  const COLORS = {
    GREEN: '#2fd47e', FLASH_GREEN: '#5cffa8', RED: '#ff5470', FLASH_RED: '#ff7d92',
    LEAN_LONG: '#4cc9ff', LEAN_SHORT: '#a98bff', NEUTRAL: '#6f7fa3',
  };
  const BOX = { x: 7, y: 4.2, z: 5 };            // half-extents of the plotted volume
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const renderer = FX.makeRenderer(canvas);
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(0x060912, 0.028);
  const camera = new THREE.PerspectiveCamera(42, 2, 0.1, 200);
  const rig = FX.orbit(camera, canvas, { radius: 19, theta: 0.55, phi: 1.2, target: new THREE.Vector3(0, 0, 0), spin: 0.06, maxRadius: 42 });

  // ── Stage: floor grid, VWAP plane, score axis ───────────────────────────
  const floorY = -BOX.y - 0.3;
  const grid = new THREE.GridHelper(BOX.x * 2 + 2, 16, 0x2a3a66, 0x16203c);
  grid.position.y = floorY;
  grid.material.transparent = true; grid.material.opacity = .55;
  scene.add(grid);

  const vwapPlane = new THREE.Mesh(
    new THREE.PlaneGeometry(BOX.z * 2 + 2, BOX.y * 2 + 0.6),
    new THREE.MeshBasicMaterial({ color: 0x4cc9ff, transparent: true, opacity: .045, side: THREE.DoubleSide, depthWrite: false }));
  vwapPlane.rotation.y = Math.PI / 2;
  scene.add(vwapPlane);
  const vwapEdge = new THREE.LineSegments(new THREE.EdgesGeometry(vwapPlane.geometry),
    new THREE.LineBasicMaterial({ color: 0x4cc9ff, transparent: true, opacity: .25 }));
  vwapEdge.rotation.y = Math.PI / 2;
  scene.add(vwapEdge);
  const vwapLabel = FX.labelSprite('VWAP', { color: '#4cc9ff', size: 34 });
  vwapLabel.scale.multiplyScalar(0.55); vwapLabel.position.set(0, BOX.y + 0.75, -BOX.z - 0.6);
  scene.add(vwapLabel);

  // threshold ring at the score where trades become possible (~62)
  let ringY = (62 - 50) / 50 * BOX.y;
  const ring = new THREE.Mesh(new THREE.RingGeometry(BOX.x * 0.98, BOX.x, 96),
    new THREE.MeshBasicMaterial({ color: 0xffc857, transparent: true, opacity: .16, side: THREE.DoubleSide, depthWrite: false }));
  ring.rotation.x = -Math.PI / 2; ring.position.y = ringY; ring.scale.set(1, BOX.z / BOX.x, 1);
  scene.add(ring);

  // dust
  const dustGeo = new THREE.BufferGeometry();
  const dust = new Float32Array(600 * 3);
  for (let i = 0; i < 600; i++) {
    dust[i * 3] = (Math.random() - .5) * 40; dust[i * 3 + 1] = (Math.random() - .5) * 20; dust[i * 3 + 2] = (Math.random() - .5) * 40;
  }
  dustGeo.setAttribute('position', new THREE.BufferAttribute(dust, 3));
  scene.add(new THREE.Points(dustGeo, new THREE.PointsMaterial({
    size: .12, color: 0x8fa8ff, transparent: true, opacity: .35, map: FX.glowTexture('#ffffff'),
    depthWrite: false, blending: THREE.AdditiveBlending })));

  // ── Bodies ───────────────────────────────────────────────────────────────
  const bodies = {};          // symbol → { group, core, halo, stem, label, target: Vector3, data }
  const sphereGeo = new THREE.SphereGeometry(1, 24, 18);

  function colorKey(d) {
    if (COLORS[d.color] && d.color !== 'NEUTRAL') return d.color;
    if (d.direction > 0) return 'LEAN_LONG';
    if (d.direction < 0) return 'LEAN_SHORT';
    return 'NEUTRAL';
  }

  // Axes scale to the current spread of the data (90th percentile of |value|),
  // so a quiet market still fills the space instead of collapsing to a dot.
  const scale = { vw: 1, ch: 1, lo: 30, hi: 70 };
  const vwOf = d => d.vwap && d.price ? (d.price - d.vwap) / d.vwap * 100 : 0;
  function pct90(xs) {
    const a = xs.map(Math.abs).sort((p, q) => p - q);
    return a.length ? a[Math.floor((a.length - 1) * 0.9)] : 1;
  }
  function rescale() {
    const ds = Object.values(bodies).map(b => b.data);
    if (!ds.length) return;
    scale.vw = Math.max(0.15, pct90(ds.map(vwOf)));
    scale.ch = Math.max(0.25, pct90(ds.map(d => d.change_pct ?? 0)));
    const sc = ds.map(d => d.score ?? 50);
    scale.lo = Math.min(30, Math.min(...sc) - 3);
    scale.hi = Math.max(70, Math.max(...sc) + 3);
    for (const sym in bodies) {
      const b = bodies[sym];
      b.target.copy(targetFor(b.data));
      if (!b.placed) { b.group.position.copy(b.target); b.placed = true; }   // first load: no fly-in from the origin
    }
    ring.position.y = ringY;
    ringY = ((62 - scale.lo) / (scale.hi - scale.lo) * 2 - 1) * BOX.y;
    const el = document.getElementById('galaxy-scale');
    if (el) el.textContent = `↔ ±${scale.vw.toFixed(2)}% from VWAP · ⤢ ±${scale.ch.toFixed(2)}% on the day · ↕ score ${scale.lo.toFixed(0)}–${scale.hi.toFixed(0)}`;
  }
  function targetFor(d) {
    return new THREE.Vector3(
      clamp(vwOf(d) / scale.vw, -1, 1) * BOX.x,
      clamp(((d.score ?? 50) - scale.lo) / (scale.hi - scale.lo) * 2 - 1, -1, 1) * BOX.y,
      clamp((d.change_pct ?? 0) / scale.ch, -1, 1) * BOX.z);
  }

  function makeBody(sym) {
    const group = new THREE.Group();
    const core = new THREE.Mesh(sphereGeo, new THREE.MeshBasicMaterial({ color: 0xffffff }));
    const halo = new THREE.Sprite(new THREE.SpriteMaterial({ map: FX.glowTexture('#ffffff'), transparent: true,
      depthWrite: false, blending: THREE.AdditiveBlending }));
    const label = FX.labelSprite(sym, { size: 40 });
    label.scale.multiplyScalar(0.48);
    const stemGeo = new THREE.BufferGeometry();          // 2 vertices, updated in place every frame
    stemGeo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(6), 3));
    const stem = new THREE.Line(stemGeo, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: .22 }));
    stem.frustumCulled = false;
    const shadow = new THREE.Mesh(new THREE.CircleGeometry(.35, 24),
      new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: .18, depthWrite: false }));
    shadow.rotation.x = -Math.PI / 2;
    group.add(core, halo, label);
    scene.add(group, stem, shadow);
    core.userData.symbol = sym;
    return { group, core, halo, label, stem, shadow, target: new THREE.Vector3(), pulse: Math.random() * 6 };
  }

  const coreList = [];
  function update(d) {
    if (!d || !d.symbol || d.symbol === 'SPY' || d.symbol === 'QQQ') return;
    if (!bodies[d.symbol]) { bodies[d.symbol] = makeBody(d.symbol); coreList.push(bodies[d.symbol].core); }
    const b = bodies[d.symbol];
    b.data = Object.assign(b.data || {}, d);
    b.target.copy(targetFor(b.data));
    const hex = COLORS[colorKey(b.data)];
    b.core.material.color.set(hex);
    b.halo.material.map = FX.glowTexture(hex);
    b.halo.material.needsUpdate = true;
    b.stem.material.color.set(hex);
    b.shadow.material.color.set(hex);
    const rv = clamp(b.data.rvol || 1, 0.4, 4);
    b.size = 0.16 + 0.13 * Math.sqrt(rv);
    b.flash = b.data.color === 'FLASH_GREEN' || b.data.color === 'FLASH_RED';
  }

  // ── Hover / click ───────────────────────────────────────────────────────
  const tip = document.getElementById('galaxy-tip');
  const ray = new THREE.Raycaster();
  const mouse = new THREE.Vector2(-9, -9);
  let hovered = null;
  let pointerDirty = false;
  canvas.addEventListener('pointermove', e => {
    pointerDirty = true;
    const r = canvas.getBoundingClientRect();
    mouse.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    tip.style.left = (e.clientX - r.left) + 'px';
    tip.style.top = (e.clientY - r.top) + 'px';
  });
  canvas.addEventListener('pointerleave', () => { mouse.set(-9, -9); pointerDirty = true; });
  canvas.addEventListener('click', e => {            // pick at the click itself (hover can lag a frame)
    if (rig.wasDrag()) return;
    const r = canvas.getBoundingClientRect();
    ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera);
    const hit = ray.intersectObjects(coreList)[0];
    if (hit) window.location.href = '/symbol/' + hit.object.userData.symbol;
  });

  function showTip(sym) {
    const d = bodies[sym].data;
    const fmt = (v, dp = 2) => v == null ? '—' : v.toFixed(dp);
    const vw = d.vwap && d.price ? (d.price - d.vwap) / d.vwap * 100 : null;
    const col = COLORS[colorKey(d)];
    tip.innerHTML = `<div class="gt-sym" style="color:${col}">${sym}
        <span style="color:var(--text-muted);font-weight:500;font-size:.75rem">${(d.label || '').replace('_', ' ')}</span></div>
      <div>$${fmt(d.price)} · score <b>${fmt(d.score, 0)}</b> · ${d.direction > 0 ? 'long bias' : d.direction < 0 ? 'short bias' : 'no bias'}</div>
      <div style="color:var(--text-muted)">vs VWAP ${vw == null ? '—' : (vw >= 0 ? '+' : '') + vw.toFixed(2) + '%'}
        · day ${d.change_pct == null ? '—' : (d.change_pct >= 0 ? '+' : '') + d.change_pct.toFixed(2) + '%'}
        · RVOL ${d.rvol == null ? '—' : d.rvol.toFixed(1) + '×'}</div>`;
    tip.classList.add('show');
  }

  // ── Loop ────────────────────────────────────────────────────────────────
  FX.loop((dt, t) => {
    FX.fit(renderer, camera, canvas);
    rig.fitAspect(camera.aspect, 1.8);
    rig.update(dt);
    ring.material.opacity = .12 + Math.sin(t * 1.4) * .05;
    ring.position.y += (ringY - ring.position.y) * Math.min(1, dt * 3);

    for (const sym in bodies) {
      const b = bodies[sym];
      b.group.position.lerp(b.target, 1 - Math.exp(-dt * 4));   // frame-rate independent easing
      const p = b.group.position;
      const s = b.size * (b.flash ? 1 + Math.sin(t * 6 + b.pulse) * .18 : 1 + Math.sin(t * 1.5 + b.pulse) * .04);
      b.core.scale.setScalar(s * (sym === hovered ? 1.35 : 1));
      b.halo.scale.setScalar(s * (b.flash ? 9 : 6.5));
      b.label.position.set(0, s + .42, 0);
      b.label.material.opacity = sym === hovered ? 1 : .78;
      const sp = b.stem.geometry.attributes.position;
      sp.setXYZ(0, p.x, p.y, p.z); sp.setXYZ(1, p.x, floorY, p.z); sp.needsUpdate = true;
      b.shadow.position.set(p.x, floorY + .01, p.z);
      b.shadow.scale.setScalar(s * 2.2);
    }

    // Re-pick only when the pointer moved, or while it rests over a turning scene
    if (pointerDirty || (mouse.x > -2 && (rig.dragging || rig.idle > 2.5))) {
      pointerDirty = false;
      ray.setFromCamera(mouse, camera);
      const hits = ray.intersectObjects(coreList);
      pickHover(hits.length ? hits[0].object.userData.symbol : null);
    }
    renderer.render(scene, camera);
  }, canvas);

  function pickHover(h) {
    if (h !== hovered) {
      hovered = h;
      if (h) showTip(h); else tip.classList.remove('show');
      canvas.style.cursor = h ? 'pointer' : '';
    }
  }

  // ── Data ────────────────────────────────────────────────────────────────
  async function load() {
    try {
      const sigs = await (await fetch('/api/signals')).json();
      Object.values(sigs).forEach(update);
      rescale();
      const n = Object.keys(bodies).length;
      const hot = Object.values(bodies).filter(b => ['GREEN', 'RED', 'FLASH_GREEN', 'FLASH_RED'].includes(b.data.color)).length;
      const el = document.getElementById('galaxy-count');
      if (el) el.textContent = `${n} stocks · ${hot} active signal${hot === 1 ? '' : 's'}`;
    } catch (e) { /* server restarting — try again next tick */ }
  }
  load();
  setInterval(load, 15000);
  window.galaxyUpdate = update;
     // dashboard.js forwards SSE signal_update events here
})();
