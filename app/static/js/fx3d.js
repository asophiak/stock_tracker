/* ══════════════════════════════════════════════════════════════════════════════
   fx3d.js — shared 3D effects (Three.js r128)
     • FX.starfield()       animated depth starfield behind every page
     • FX.tiltCards()       mouse-driven 3D tilt + glare on the centred stock card
     • FX.glowTexture()     radial glow sprite texture
     • FX.labelSprite()     text label sprite
     • FX.orbit()           drag-to-rotate camera rig with idle auto-spin
   Everything degrades gracefully: no WebGL / reduced motion → nothing breaks.
   ══════════════════════════════════════════════════════════════════════════════ */
window.FX = (function () {
  const reduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hasGL = (() => {
    try { const c = document.createElement('canvas'); return !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl'))); }
    catch (e) { return false; }
  })();
  const ok = () => typeof THREE !== 'undefined' && hasGL;

  // Render loops pause while the tab is hidden, and — when given an element —
  // while that element is scrolled out of view (no GPU work for unseen scenes).
  const loops = new Set();
  function loop(fn, el) {
    loops.add(fn);
    let visible = true;
    if (el && 'IntersectionObserver' in window) {
      new IntersectionObserver(es => { visible = es[0].isIntersecting; }, { rootMargin: '100px' }).observe(el);
    }
    let last = performance.now();
    function frame(t) {
      if (!loops.has(fn)) return;
      const dt = Math.min(0.25, (t - last) / 1000); last = t;   // capped so a background tab doesn't jump
      if (!document.hidden && visible) fn(dt, t / 1000);
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    return () => loops.delete(fn);
  }

  function makeRenderer(canvas, { maxPixelRatio = 1.5, antialias = true } = {}) {
    const r = new THREE.WebGLRenderer({ canvas, antialias, alpha: true, powerPreference: 'high-performance' });
    r.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxPixelRatio));
    r.setClearColor(0x000000, 0);
    return r;
  }

  function fit(renderer, camera, canvas) {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    if (canvas.width !== Math.floor(w * renderer.getPixelRatio()) || canvas.height !== Math.floor(h * renderer.getPixelRatio())) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
  }

  // ── Textures ──────────────────────────────────────────────────────────────
  const _glowCache = {};
  function glowTexture(hex = '#ffffff') {
    if (_glowCache[hex]) return _glowCache[hex];
    const c = document.createElement('canvas'); c.width = c.height = 128;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, hex); grd.addColorStop(0.18, hex + 'cc'); grd.addColorStop(0.45, hex + '33'); grd.addColorStop(1, hex + '00');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    return (_glowCache[hex] = new THREE.CanvasTexture(c));
  }

  function labelSprite(text, { color = '#e8edf7', size = 44, weight = 700 } = {}) {
    const c = document.createElement('canvas');
    const g = c.getContext('2d');
    const font = `${weight} ${size}px Inter, -apple-system, sans-serif`;
    g.font = font;
    const w = Math.ceil(g.measureText(text).width) + 24;
    c.width = w; c.height = size + 20;
    g.font = font; g.fillStyle = color; g.textBaseline = 'middle';
    g.shadowColor = 'rgba(0,0,0,.8)'; g.shadowBlur = 8;
    g.fillText(text, 12, c.height / 2);
    const tex = new THREE.CanvasTexture(c);
    tex.minFilter = THREE.LinearFilter;
    const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
    s.scale.set(c.width / c.height, 1, 1);
    return s;
  }

  // ── Camera rig: drag to orbit, wheel / pinch to zoom, fly-to, idle auto-spin ─
  function orbit(camera, el, { radius = 10, theta = 0.6, phi = 1.15, target = new THREE.Vector3(), spin = 0.08,
                               minPhi = 0.35, maxPhi = 1.5, minRadius = radius * 0.3, maxRadius = radius * 1.8,
                               zoom = false } = {}) {
    const home = { radius, theta, phi, target: target.clone() };
    const st = { radius, theta, phi, target: target.clone(), dragging: false, lastX: 0, lastY: 0, idle: 0, moved: 0,
                 fly: null, paused: false };
    const pointers = new Map();
    let pinchDist = 0;
    function apply() {
      camera.position.set(
        st.target.x + st.radius * Math.sin(st.phi) * Math.sin(st.theta),
        st.target.y + st.radius * Math.cos(st.phi),
        st.target.z + st.radius * Math.sin(st.phi) * Math.cos(st.theta));
      camera.lookAt(st.target);
    }
    const setRadius = r => { st.radius = Math.max(minRadius, Math.min(maxRadius, r)); st.fly = null; st.idle = 0; apply(); };
    el.addEventListener('pointerdown', e => {
      pointers.set(e.pointerId, e);
      st.dragging = pointers.size === 1; st.lastX = e.clientX; st.lastY = e.clientY; st.moved = 0;
      if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinchDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }
      el.setPointerCapture && el.setPointerCapture(e.pointerId);
    });
    el.addEventListener('pointermove', e => {
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, e);
      if (zoom && pointers.size === 2) {                     // pinch zoom on touch screens
        const [a, b] = [...pointers.values()];
        const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        if (pinchDist) setRadius(st.radius * pinchDist / d);
        pinchDist = d; st.moved += 10; return;
      }
      if (!st.dragging) return;
      const dx = e.clientX - st.lastX, dy = e.clientY - st.lastY;
      st.moved += Math.abs(dx) + Math.abs(dy);
      st.theta -= dx * 0.006;
      st.phi = Math.max(minPhi, Math.min(maxPhi, st.phi - dy * 0.005));
      st.lastX = e.clientX; st.lastY = e.clientY; st.idle = 0; st.fly = null; apply();
    });
    const end = e => { pointers.delete(e.pointerId); pinchDist = 0; st.dragging = false; };
    el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end); el.addEventListener('pointerleave', end);
    if (zoom) {
      el.addEventListener('wheel', e => {                    // mouse wheel / trackpad pinch (ctrl+wheel)
        e.preventDefault();
        setRadius(st.radius * Math.exp(e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)));
      }, { passive: false });
    }
    // Smoothly move the camera to look at `to` from `radius` away
    st.flyTo = (to, r = st.radius) => { st.fly = { target: to.clone(), radius: Math.max(minRadius, Math.min(maxRadius, r)) }; st.idle = 0; };
    st.zoomBy = f => { st.fly = { target: st.target.clone(), radius: Math.max(minRadius, Math.min(maxRadius, st.radius * f)) }; st.idle = 0; };
    st.reset = () => { st.fly = { target: home.target.clone(), radius: home.radius, theta: home.theta, phi: home.phi }; st.idle = 0; };
    st.update = dt => {
      st.idle += dt;
      if (st.fly) {
        const k = 1 - Math.exp(-dt * 5);
        st.target.lerp(st.fly.target, k);
        st.radius += (st.fly.radius - st.radius) * k;
        if (st.fly.theta != null) st.theta += (st.fly.theta - st.theta) * k;
        if (st.fly.phi != null) st.phi += (st.fly.phi - st.phi) * k;
        if (st.target.distanceTo(st.fly.target) < 0.01 && Math.abs(st.radius - st.fly.radius) < 0.01) st.fly = null;
        apply();
      } else if (!st.dragging && !st.paused && st.idle > 2.5 && !reduced) { st.theta += spin * dt; apply(); }
    };
    st.wasDrag = () => st.moved > 6;
    // Keep the whole scene in frame on narrow screens: a portrait canvas has a
    // much narrower horizontal field of view, so pull the camera back to match.
    st.fitAspect = (aspect, wideAspect = 2) => {
      const k = Math.max(1, wideAspect / Math.max(aspect, .3));
      const want = Math.min(maxRadius, radius * Math.pow(k, .85));
      if (Math.abs(want - home.radius) < .01) return;
      const wasHome = !st.fly && Math.abs(st.radius - home.radius) < .01;
      home.radius = want;
      if (wasHome) { st.radius = want; apply(); }
    };
    apply();
    return st;
  }

  // ── Starfield background ────────────────────────────────────────────────
  // Two identical star slabs leapfrog toward the camera. Only the two objects'
  // transforms change per frame — no vertex data is re-uploaded.
  function starfield() {
    if (!ok() || reduced) return;
    let canvas = document.getElementById('fx-bg');
    if (!canvas) { canvas = document.createElement('canvas'); canvas.id = 'fx-bg'; document.body.prepend(canvas); }
    const renderer = makeRenderer(canvas, { maxPixelRatio: 1, antialias: false });
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(60, 1, 0.1, 200);
    camera.position.z = 30;

    const DEPTH = 120, N = 900, pos = new Float32Array(N * 3), col = new Float32Array(N * 3);
    const palette = [new THREE.Color('#4cc9ff'), new THREE.Color('#a98bff'), new THREE.Color('#ffffff')];
    for (let i = 0; i < N; i++) {
      pos[i * 3] = (Math.random() - .5) * 140; pos[i * 3 + 1] = (Math.random() - .5) * 90; pos[i * 3 + 2] = -Math.random() * DEPTH;
      const c = palette[Math.random() < .7 ? 2 : (Math.random() < .5 ? 0 : 1)];
      col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('color', new THREE.BufferAttribute(col, 3));
    const mat = new THREE.PointsMaterial({
      size: 0.28, vertexColors: true, transparent: true, opacity: .55, map: glowTexture('#ffffff'),
      depthWrite: false, blending: THREE.AdditiveBlending,
    });
    const slabs = [new THREE.Points(geo, mat), new THREE.Points(geo, mat)];
    slabs[1].position.z = -DEPTH;
    slabs.forEach(s => scene.add(s));

    let mx = 0, my = 0;
    window.addEventListener('pointermove', e => { mx = e.clientX / innerWidth - .5; my = e.clientY / innerHeight - .5; }, { passive: true });
    loop((dt) => {
      fit(renderer, camera, canvas);
      for (const s of slabs) {
        s.position.z += dt * 1.6;
        if (s.position.z > DEPTH - 90) s.position.z -= DEPTH * 2;   // recycle behind the other slab
      }
      camera.position.x += (mx * 4 - camera.position.x) * .03;
      camera.position.y += (-my * 3 - camera.position.y) * .03;
      camera.lookAt(0, 0, -40);
      renderer.render(scene, camera);
    });
  }

  // ── Stock card tilt ─────────────────────────────────────────────────────
  function tiltCards() {
    if (reduced) return;
    document.querySelectorAll('.cf-card').forEach(card => {
      const inner = card.querySelector('.card-tilt');
      if (!inner) return;
      card.addEventListener('pointermove', e => {
        if (card.dataset.slot !== '0') return;
        const r = card.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        inner.style.transform = `rotateY(${(x - .5) * 16}deg) rotateX(${(.5 - y) * 12}deg) translateZ(14px)`;
        inner.style.setProperty('--gx', `${x * 100}%`);
        inner.style.setProperty('--gy', `${y * 100}%`);
      });
      card.addEventListener('pointerleave', () => { inner.style.transform = ''; });
    });
  }

  return { ok, reduced, loop, makeRenderer, fit, glowTexture, labelSprite, orbit, starfield, tiltCards };
})();

document.addEventListener('DOMContentLoaded', () => {
  try { FX.starfield(); } catch (e) { console.warn('starfield disabled', e); }
  try { FX.tiltCards(); } catch (e) { console.warn('tilt disabled', e); }
});
