/*
 * stop-ripple.js — ambient "busy" ripples inside the Stop control.
 *
 * While an operation runs, the ask-bar Send button becomes a Stop control
 * (`.thread-reply-submit.is-stop`). On the in-thread screens that button is the
 * only visible progress affordance, so this layers a canvas behind its glyph and
 * emits ripples that start as a sharp square (matching the stop glyph) and morph
 * into the button's squircle as they expand and fade — a "pulse" point rides
 * around some rings. The button itself stays brand-pink and fully clickable
 * (canvas is pointer-events:none, below the glyph). No dependencies.
 *
 * Config was dialled in with the Stop Button Lab harness. Geometry is in the
 * button's own px (36), so it renders 1:1 at real size.
 */
(function () {
  "use strict";

  var CFG = {
    buttonSize: 36,     // .thread-reply-submit is 36×36, radius 11
    emitRate: 0.9,      // waves per second (randomised ±40%)
    waveLife: 2.3,      // seconds for a wave to travel out and fade
    width: 2.6,         // stroke width at birth (px)
    taper: 0.35,        // end width = width * taper
    maxAlpha: 1,
    fadeExp: 3,         // higher = fades sooner as it expands
    glow: 5.5,          // shadowBlur (px)
    pulseDots: 1,       // fallback count when pulseRandom is off
    pulseRandom: true,  // each wave rolls its own: 0→60% 1→30% 2→10%
    pulseSpeed: 0.5,    // how fast pulse points slide around the ring
    endRadius: 18,      // ultimate corner radius (button edge is 11; 18 = circle)
    roundBias: 0,       // 0 = round from the start, 1 = stay square until the end
    glyphSize: 12,      // real stop glyph ≈12px (measured from stop.png); waves born on its edge
    startRadius: 1,     // birth corner radius — the glyph is slightly rounded, not a hard square
    edgeInset: 0,       // final wave reaches the button edge
    color: "#FFFFFF"
  };

  var TAU = Math.PI * 2;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)");
  var RGB = hexRGB(CFG.color);

  function hexRGB(h) {
    h = String(h).replace("#", "");
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
  }
  function rgba(a) { return "rgba(" + RGB[0] + "," + RGB[1] + "," + RGB[2] + "," + Math.max(0, Math.min(1, a)) + ")"; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }

  // rounded-rect path + ray-cast to its boundary (for pulse points)
  function rrPath(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath(); ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }
  function sdBox(px, py, h, r) {
    var qx = Math.abs(px) - h + r, qy = Math.abs(py) - h + r;
    return Math.min(Math.max(qx, qy), 0) + Math.hypot(Math.max(qx, 0), Math.max(qy, 0)) - r;
  }
  function rayR(theta, h, r) {
    var cx = Math.cos(theta), cy = Math.sin(theta), lo = 0, hi = h * 1.7 + r + 4;
    for (var k = 0; k < 18; k++) { var t = (lo + hi) / 2; if (sdBox(cx * t, cy * t, h, r) < 0) lo = t; else hi = t; }
    return (lo + hi) / 2;
  }
  function pickPulses() {
    if (!CFG.pulseRandom) return CFG.pulseDots;
    var r = Math.random(); return r < 0.6 ? 0 : (r < 0.9 ? 1 : 2); // 0:60% 1:30% 2:10%
  }

  var insts = new Map(); // button -> instance
  var rafId = null, last = 0;

  function makeInst(btn) {
    var canvas = document.createElement("canvas");
    canvas.className = "stop-ripple-canvas";
    canvas.setAttribute("aria-hidden", "true");
    btn.appendChild(canvas);
    var inst = { btn: btn, canvas: canvas, ctx: canvas.getContext("2d"), waves: [], clock: 0, nextEmit: 0, active: false, scale: 1, cx: 18, cy: 18 };
    insts.set(btn, inst);
    return inst;
  }

  function sizeCanvas(inst) {
    var dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    var r = inst.btn.getBoundingClientRect();
    var w = r.width || CFG.buttonSize, h = r.height || CFG.buttonSize;
    inst.canvas.width = Math.max(1, Math.round(w * dpr));
    inst.canvas.height = Math.max(1, Math.round(h * dpr));
    inst.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    inst.scale = w / CFG.buttonSize;
    inst.cx = w / 2; inst.cy = h / 2;
  }

  function seed(inst) {
    inst.waves.length = 0; inst.nextEmit = inst.clock;
    for (var i = 1; i <= 3; i++) inst.waves.push(newWave(inst.clock - i * (CFG.waveLife / 3.2)));
  }
  function newWave(born) { return { born: born, seed: Math.random(), dir: Math.random() < 0.5 ? -1 : 1, pulses: pickPulses() }; }

  function emit(inst) {
    var guard = 0;
    while (inst.clock >= inst.nextEmit && guard++ < 20) {
      inst.waves.push(newWave(inst.nextEmit));
      inst.nextEmit += (1 / CFG.emitRate) * (0.6 + Math.random() * 0.8);
    }
    for (var i = inst.waves.length - 1; i >= 0; i--) if ((inst.clock - inst.waves[i].born) > CFG.waveLife) inst.waves.splice(i, 1);
  }

  function drawWave(ctx, cx, cy, scale, w, clock) {
    var age = clock - w.born, t = age / CFG.waveLife;
    if (t < 0 || t > 1) return;
    var te = 1 - (1 - t) * (1 - t);                       // easeOut expansion
    var g0 = CFG.glyphSize / 2, g1 = 18 - CFG.edgeInset;  // born right on the glyph line
    var half = lerp(g0, g1, te);
    var shapeT = Math.pow(t, 1 + CFG.roundBias * 4);      // corners round late
    var corner = Math.min(lerp(CFG.startRadius, CFG.endRadius, shapeT), half * 0.999);
    var fin = clamp(t / 0.06, 0, 1);
    var base = fin * Math.pow(1 - t, CFG.fadeExp) * CFG.maxAlpha;
    base *= 0.88 + 0.12 * Math.sin(age * 8 + w.seed * TAU);
    if (base <= 0.01) return;
    var lw = lerp(CFG.width, CFG.width * CFG.taper, t) * scale;
    ctx.save(); ctx.translate(cx, cy);
    rrPath(ctx, -half * scale, -half * scale, half * 2 * scale, half * 2 * scale, corner * scale);
    ctx.lineWidth = lw; ctx.lineJoin = corner > 0.1 ? "round" : "miter";
    ctx.strokeStyle = rgba(base); ctx.shadowBlur = CFG.glow * scale; ctx.shadowColor = rgba(1);
    ctx.stroke(); ctx.shadowBlur = 0;
    for (var b = 0; b < w.pulses; b++) {
      var phi = (w.seed + b / Math.max(1, w.pulses) + t * CFG.pulseSpeed * w.dir) * TAU;
      var r = rayR(phi, half, corner), x = Math.cos(phi) * r, y = Math.sin(phi) * r;
      ctx.beginPath(); ctx.arc(x * scale, y * scale, Math.max(0.6, lw * 0.9), 0, TAU);
      ctx.fillStyle = rgba(clamp(base * 1.7, 0, 1)); ctx.shadowBlur = CFG.glow * scale * 1.3; ctx.shadowColor = rgba(1);
      ctx.fill(); ctx.shadowBlur = 0;
    }
    ctx.restore();
  }

  function render(inst) {
    var ctx = inst.ctx;
    ctx.clearRect(0, 0, inst.canvas.width, inst.canvas.height);
    for (var i = 0; i < inst.waves.length; i++) drawWave(ctx, inst.cx, inst.cy, inst.scale, inst.waves[i], inst.clock);
  }

  function loop(now) {
    var dt = Math.min(0.05, (now - last) / 1000); last = now;
    var anyActive = false;
    insts.forEach(function (inst) {
      if (!inst.active) return;
      anyActive = true;
      inst.clock += dt; emit(inst); render(inst);
    });
    if (anyActive) rafId = requestAnimationFrame(loop);
    else { rafId = null; }
  }
  function startLoop() { if (rafId == null) { last = performance.now(); rafId = requestAnimationFrame(loop); } }

  function activate(btn) {
    if (reduce && reduce.matches) return;                 // honour reduced motion
    var inst = insts.get(btn) || makeInst(btn);
    sizeCanvas(inst); seed(inst); inst.active = true; startLoop();
  }
  function deactivate(btn) {
    var inst = insts.get(btn); if (!inst) return;
    inst.active = false;
    inst.ctx.clearRect(0, 0, inst.canvas.width, inst.canvas.height);
  }

  function bind(btn) {
    if (insts.get(btn)) return;
    makeInst(btn);
    var obs = new MutationObserver(function () {
      if (btn.classList.contains("is-stop")) activate(btn); else deactivate(btn);
    });
    obs.observe(btn, { attributes: true, attributeFilter: ["class"] });
    if (btn.classList.contains("is-stop")) activate(btn);
  }

  function init() {
    var btns = document.querySelectorAll(".thread-reply-submit");
    for (var i = 0; i < btns.length; i++) bind(btns[i]);
  }

  window.addEventListener("resize", function () {
    insts.forEach(function (inst) { if (inst.active) sizeCanvas(inst); });
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
