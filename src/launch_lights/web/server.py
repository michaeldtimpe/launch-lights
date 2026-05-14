"""Control panel HTTP + WebSocket server."""
from __future__ import annotations

import asyncio
import http
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, Optional

import websockets
from websockets.asyncio.server import serve, ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

if TYPE_CHECKING:
    from launch_lights.video.audio_source import AudioSource


log = logging.getLogger(__name__)
logging.getLogger("websockets.server").setLevel(logging.WARNING)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
STATE_PUSH_HZ = 10.0


def _http(status: int, reason: str, body: bytes, ctype: str) -> Response:
    return Response(
        status_code=status,
        reason_phrase=reason,
        headers=Headers([
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ]),
        body=body,
    )


# Glyph hints for emoji button labels — pure UI flavor, falls back to text.
EMOJI_GLYPHS: dict[str, str] = {
    "heart": "♥", "smiley": "☺", "sad": "☹", "skull": "☠",
    "music": "♪", "ghost": "👻", "fire": "🔥", "sun": "☀",
    "mini_smiley": "·☺", "mini_sad": "·☹", "mini_skull": "·☠",
    "mini_heart": "·♥", "mini_star": "·★",
}

# Glyph hints for shape buttons too where helpful.
SHAPE_GLYPHS: dict[str, str] = {
    "arrow_up": "↑", "arrow_down": "↓", "arrow_left": "←", "arrow_right": "→",
    "check": "✓", "circle": "○", "diamond": "◇", "lightning": "⚡",
    "plus": "+", "ring": "◯", "square": "□", "star": "★",
    "triangle_up": "△", "triangle_down": "▽", "x": "✕",
}


PAGE_TMPL = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>launch</title>
<link rel="icon" href="data:,">
<script>
  (function () {
    try {
      var t = localStorage.getItem("zoleb-theme") || "dark";
      document.documentElement.setAttribute("data-theme", t);
    } catch (e) {
      document.documentElement.setAttribute("data-theme", "dark");
    }
  })();
</script>
<style>
  *, *::before, *::after { box-sizing: border-box; }
  @font-face {
    font-family: "InterVariable";
    src: url("/static/InterVariable.ttf") format("truetype-variations"),
         url("/static/InterVariable.ttf") format("truetype");
    font-weight: 100 900;
    font-display: swap;
  }
  :root, html[data-theme="dark"] {
    color-scheme: dark;
    --bg:#272822; --surface:rgba(62,61,50,0.6); --surface-strong:rgba(73,72,62,0.85);
    --border:rgba(248,248,242,0.1); --text:#f8f8f2; --muted:#75715e;
    --accent:#fd971f; --accent-soft:rgba(253,151,31,0.14);
    --green:#a6e22e; --red:#f92672; --shadow:0 18px 60px rgba(0,0,0,0.5);
  }
  html[data-theme="light"] {
    color-scheme: light;
    --bg:#fafafa; --surface:rgba(39,40,34,0.04); --surface-strong:rgba(39,40,34,0.08);
    --border:rgba(39,40,34,0.12); --text:#272822; --muted:#75715e;
    --accent:#c24f0d; --accent-soft:rgba(194,79,13,0.12);
    --green:#4d8500; --red:#c00e5a; --shadow:0 8px 32px rgba(39,40,34,0.08);
  }
  html, body { background: var(--bg); color: var(--text); }
  body {
    margin: 0;
    font-family: "InterVariable", Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    font-feature-settings: "cv11", "ss01", "ss03";
    line-height: 1.5; font-size: 15px;
  }
  .page-header {
    display: flex; align-items: center; justify-content: space-between;
    gap: 1rem; padding: 14px 20px; flex-wrap: wrap;
    border-bottom: 1px solid var(--border);
  }
  .page-header .left { display: flex; align-items: center; gap: 0.85rem; flex-wrap: wrap; }
  .page-header .brand { color: var(--accent); font-weight: 600; font-size: 1rem; }
  .page-header .source-tag {
    font-size: 12px; color: var(--muted);
    border: 1px solid var(--border); border-radius: 3px;
    padding: 0.1rem 0.5rem; font-variant-numeric: tabular-nums;
  }
  .page-header .source-tag b { color: var(--text); font-weight: 600; }
  .page-header .right { display: flex; gap: 0.55rem; align-items: center; }
  .status {
    display: inline-flex; align-items: center; gap: 0.4rem;
    font-size: 12px; color: var(--muted);
    border: 1px solid var(--border); border-radius: 999px;
    padding: 0.15rem 0.6rem 0.15rem 0.5rem;
  }
  .status .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--red); transition: background 0.15s;
    box-shadow: 0 0 0 1px var(--border);
  }
  .status.live { border-color: var(--green); color: var(--text); }
  .status.live .dot { background: var(--green); box-shadow: 0 0 0 1px var(--green); }

  .theme-toggle, button.action, button.danger, button.reset {
    font: inherit; background: transparent; border-radius: 3px;
    padding: 0.3rem 0.7rem; cursor: pointer; font-size: 13px;
    color: var(--text); border: 1px solid var(--border);
  }
  .theme-toggle:hover, button.action:hover, button.reset:hover {
    border-color: var(--accent); color: var(--accent);
  }
  button.danger { color: var(--red); border-color: var(--red); }
  button.danger:hover { background: var(--red); color: var(--bg); }
  button.danger.on { background: var(--red); color: var(--bg); }

  main {
    margin: 1.25rem 0 4rem; padding: 0 1.5rem;
    display: grid; gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 6px; padding: 0.9rem 1rem; overflow: hidden;
    min-width: 0;
  }
  .card-footer {
    display: flex; justify-content: flex-end; gap: 0.5rem;
    margin-top: 0.7rem; flex-wrap: wrap; align-items: center;
  }
  .card.wide { grid-column: 1 / -1; }

  /* Live card: preview + stats + focus row */
  .card.live { display: grid; gap: 0.9rem 1.2rem;
               grid-template-columns: auto minmax(0, 1fr); align-items: start; }
  .card.live .focus-row { grid-column: 1 / -1; }
  @media (max-width: 480px) { .card.live { grid-template-columns: 1fr; } }
  .preview {
    width: 144px; height: 144px;
    display: grid; grid-template-columns: repeat(8, 1fr); grid-template-rows: repeat(8, 1fr);
    gap: 2px; padding: 4px; background: #000; border-radius: 6px;
  }
  .preview > div { background: #050505; border-radius: 2px; }

  .live-grid {
    display: grid; grid-template-columns: auto minmax(0, 1fr);
    gap: 0.4rem 1rem;
    font-variant-numeric: tabular-nums; align-self: start;
    min-width: 0;
  }
  .live-grid .l { color: var(--muted); font-size: 12.5px; white-space: nowrap; }
  .beat-pill {
    display: inline-block; padding: 0.05rem 0.5rem; border-radius: 3px;
    background: var(--accent-soft); color: var(--accent);
    transition: background 0.08s, color 0.08s; font-size: 12.5px;
  }
  .beat-pill.flash { background: var(--accent); color: var(--bg); }

  .vu {
    height: 8px; background: var(--surface-strong); border-radius: 4px;
    overflow: hidden; min-width: 0; width: 100%;
  }
  /* Fill color is set by JS; transition for both width and color. */
  .vu > div {
    height: 100%; width: 0%;
    background: hsl(120, 70%, 50%);
    transition: width 0.06s linear, background-color 0.1s linear;
  }

  .choice-list { display: flex; flex-wrap: wrap; gap: 0.4rem 0.5rem; }
  .choice-list label, .choice-list button {
    display: inline-flex; align-items: center; gap: 0.35rem;
    padding: 0.28rem 0.6rem;
    border: 1px solid var(--border); border-radius: 3px;
    cursor: pointer; font-size: 13px; font: inherit;
    background: transparent; color: var(--text);
  }
  .choice-list label:hover, .choice-list button:hover {
    border-color: var(--accent); color: var(--accent);
  }
  .choice-list label.on, .choice-list button.on {
    background: var(--accent-soft); color: var(--accent); border-color: var(--accent);
  }
  .choice-list input { display: none; }
  .choice-list.disabled label { opacity: 0.4; pointer-events: none; }

  .slider-row {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 0.3rem 1rem;
    align-items: center; margin-bottom: 0.6rem;
  }
  .slider-row:last-child { margin-bottom: 0; }
  .slider-row label { font-size: 13px; color: var(--muted); }
  .slider-row .val { font-variant-numeric: tabular-nums; color: var(--text); font-size: 13px; }
  .slider-row input[type=range] {
    grid-column: 1 / -1; accent-color: var(--accent); width: 100%; min-width: 0;
  }

  input[type=text] {
    background: transparent; color: var(--text); border: 1px solid var(--border);
    border-radius: 3px; padding: 0.4rem 0.55rem; font: inherit; font-size: 13px;
    width: 100%; max-width: 100%;
  }
  input[type=text]:focus { border-color: var(--accent); outline: none; }

  .tempo-row { display: flex; gap: 0.6rem; align-items: center; flex-wrap: wrap; }
  .tempo-row label.auto { font-size: 12px; display: inline-flex; gap: 0.3rem; align-items: center; }

  .toggle-row { display: flex; flex-wrap: wrap; gap: 0.5rem; margin-top: 0.6rem; }
  .toggle {
    font: inherit; font-size: 13px; cursor: pointer;
    padding: 0.28rem 0.65rem; border-radius: 3px;
    background: transparent; color: var(--text); border: 1px solid var(--border);
  }
  .toggle:hover { border-color: var(--accent); color: var(--accent); }
  .toggle.on { background: var(--accent-soft); color: var(--accent); border-color: var(--accent); }

  .text-row-inline {
    display: flex; gap: 0.5rem 0.7rem; align-items: center;
    flex-wrap: wrap; margin-top: 0.55rem;
  }
  .text-speed-inline {
    display: flex; gap: 0.4rem; align-items: center;
    flex: 1 1 180px; min-width: 0;
  }
  .text-speed-inline input[type=range] {
    flex: 1; min-width: 60px; accent-color: var(--accent);
  }
  .text-speed-inline label, .text-speed-inline .val {
    font-size: 13px; white-space: nowrap;
  }
  .text-speed-inline label { color: var(--muted); }
  .text-speed-inline .val { color: var(--text); font-variant-numeric: tabular-nums; }

  .group-label {
    font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); margin-bottom: 0.35rem;
  }
</style>
</head>
<body>
<header class="page-header">
  <div class="left">
    <div class="brand">launch</div>
    <div class="source-tag">source <b id="source-name">—</b></div>
  </div>
  <div class="right">
    <button id="blackout" class="danger" title="Clear all output">clear</button>
    <div class="status" id="status"><span class="dot"></span><span id="status-text">offline</span></div>
    <button id="theme-toggle" class="theme-toggle" type="button">theme</button>
  </div>
</header>

<main>
  <section class="card live wide">
    <div>
      <div id="preview" class="preview"></div>
    </div>
    <div>
      <div class="live-grid">
        <div class="l">scene</div><div id="scene-name">—</div>
        <div class="l">beat</div><div><span id="beat-pill" class="beat-pill">0</span></div>
        <div class="l">bpm</div><div id="bpm">—</div>
        <div class="l">conf</div><div id="conf">—</div>
        <div class="l">rms</div><div class="vu"><div id="vu-rms"></div></div>
        <div class="l">bass</div><div class="vu"><div id="vu-bass"></div></div>
        <div class="l">treble</div><div class="vu"><div id="vu-treble"></div></div>
        <div class="l">flux</div><div class="vu"><div id="vu-flux"></div></div>
      </div>
    </div>
    <div class="focus-row">
      <div class="choice-list" id="focus-list">
        <label data-focus="auto"><input type="radio" name="focus" value="auto" checked>auto</label>
        <label data-focus="bass"><input type="radio" name="focus" value="bass">bass</label>
        <label data-focus="melody"><input type="radio" name="focus" value="melody">melody</label>
        <label data-focus="harmony"><input type="radio" name="focus" value="harmony">harmony</label>
        <button class="reset" data-reset="focus" style="margin-left:auto">reset</button>
      </div>
    </div>
  </section>

  <section class="card" data-card="palette">
    <div class="choice-list" id="palette-list">
      <button data-palette="">none</button>
      {{PALETTE_OPTIONS}}
      <button class="reset" data-reset="palette" style="margin-left:auto">reset</button>
    </div>
  </section>

  <section class="card" data-card="color">
    <div class="slider-row">
      <label for="hue">hue rotate</label><span class="val" id="hue-val">0°</span>
      <input id="hue" type="range" min="-180" max="180" step="1" value="0">
    </div>
    <div class="slider-row">
      <label for="contrast">contrast</label><span class="val" id="contrast-val">1.00</span>
      <input id="contrast" type="range" min="0.5" max="2.5" step="0.05" value="1.0">
    </div>
    <div class="slider-row">
      <label for="gamma">gamma</label><span class="val" id="gamma-val">2.20</span>
      <input id="gamma" type="range" min="1.0" max="3.0" step="0.05" value="2.2">
    </div>
    <div class="slider-row">
      <label for="brightness">brightness</label><span class="val" id="brightness-val">1.00</span>
      <input id="brightness" type="range" min="0.1" max="1.5" step="0.05" value="1.0">
    </div>
    <div class="toggle-row">
      <button class="toggle" id="invert">invert</button>
      <button class="toggle" id="complementary">complementary</button>
      <button class="reset" data-reset="color" style="margin-left:auto">reset</button>
    </div>
  </section>

  <section class="card" data-card="motion">
    <div class="slider-row">
      <label for="trail">trails</label><span class="val" id="trail-val">0.00</span>
      <input id="trail" type="range" min="0" max="0.95" step="0.01" value="0">
    </div>
    <div class="slider-row">
      <label for="decay">bar decay</label><span class="val" id="decay-val">0.85</span>
      <input id="decay" type="range" min="0.1" max="0.99" step="0.01" value="0.85">
    </div>
    <div class="group-label">mirror</div>
    <div class="choice-list" id="mirror-list">
      <label data-mirror="off"><input type="radio" name="mirror" value="off" checked>off</label>
      <label data-mirror="horizontal"><input type="radio" name="mirror" value="horizontal">h</label>
      <label data-mirror="vertical"><input type="radio" name="mirror" value="vertical">v</label>
      <label data-mirror="quad"><input type="radio" name="mirror" value="quad">quad</label>
      <label data-mirror="radial"><input type="radio" name="mirror" value="radial">kaleido</label>
      <button class="reset" data-reset="motion" style="margin-left:auto">reset</button>
    </div>
  </section>

  <section class="card" data-card="audio">
    <div class="slider-row">
      <label for="sensitivity">sensitivity</label><span class="val" id="sensitivity-val">1.00</span>
      <input id="sensitivity" type="range" min="0.1" max="3.0" step="0.05" value="1.0">
    </div>
    <div class="slider-row">
      <label for="intensity">intensity</label><span class="val" id="intensity-val">1.00</span>
      <input id="intensity" type="range" min="0.1" max="3.0" step="0.05" value="1.0">
    </div>
    <div class="card-footer">
      <button class="reset" data-reset="audio">reset</button>
    </div>
  </section>

  <section class="card" data-card="tempo">
    <div class="slider-row">
      <label for="tempo">bpm</label><span class="val" id="tempo-val">auto</span>
      <input id="tempo" type="range" min="40" max="200" step="1" value="120" disabled>
    </div>
    <div class="tempo-row" style="margin-bottom: 0.6rem">
      <label class="auto"><input id="tempo-auto" type="checkbox" checked>auto-detect</label>
    </div>
    <div class="group-label">beat multiplier</div>
    <div class="choice-list" id="beat-mul-list">
      <label data-mul="0.5"><input type="radio" name="bmul" value="0.5">0.5×</label>
      <label data-mul="1.0"><input type="radio" name="bmul" value="1.0" checked>1×</label>
      <label data-mul="2.0"><input type="radio" name="bmul" value="2.0">2×</label>
      <button class="reset" data-reset="tempo" style="margin-left:auto">reset</button>
    </div>
  </section>

  <section class="card wide" data-card="scene">
    <div class="choice-list" id="scene-list">
      {{SCENE_OPTIONS}}
      <button class="action" id="scene-auto" style="margin-left:auto">auto</button>
      <button class="action" id="scene-none">none</button>
      <button class="reset" data-reset="scene">reset</button>
    </div>
  </section>

  <section class="card wide" data-card="mutator">
    <div class="choice-list" id="mutator-list">
      {{MUTATOR_OPTIONS}}
      <button class="action" id="mutator-none" style="margin-left:auto">none</button>
      <button class="reset" data-reset="mutator">reset</button>
    </div>
  </section>

  <section class="card wide" data-card="text">
    <input id="text-input" type="text" maxlength="200" value="LAUNCH LIGHTS">
    <div class="text-row-inline">
      <div class="choice-list" id="font-list">
        <label data-font="5x7"><input type="radio" name="font" value="5x7" checked>5×7</label>
        <label data-font="3x5"><input type="radio" name="font" value="3x5">3×5</label>
      </div>
      <div class="choice-list" id="dir-list">
        <label data-dir="left"><input type="radio" name="textdir" value="left" checked>← left</label>
        <label data-dir="right"><input type="radio" name="textdir" value="right">right →</label>
      </div>
      <div class="text-speed-inline">
        <label for="text-speed">speed</label>
        <input id="text-speed" type="range" min="1" max="40" step="1" value="8">
        <span class="val" id="text-speed-val">8</span>
      </div>
      <button class="action" id="text-show">show text</button>
      <button class="reset" data-reset="text" style="margin-left:auto">reset</button>
    </div>
  </section>

  <section class="card wide" data-card="shape">
    <div class="choice-list" id="shape-list">
      {{SHAPE_OPTIONS}}
    </div>
    <div class="choice-list" id="emoji-list" style="margin-top: 0.5rem">
      {{EMOJI_OPTIONS}}
    </div>
    <div class="card-footer">
      <button class="reset" data-reset="shape">reset</button>
    </div>
  </section>
</main>

<script>
(function () {
  const root = document.documentElement;
  const btn = document.getElementById("theme-toggle");
  function sync() { btn.textContent = root.getAttribute("data-theme") !== "light" ? "light mode" : "dark mode"; }
  btn.addEventListener("click", function () {
    const next = root.getAttribute("data-theme") === "light" ? "dark" : "light";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem("zoleb-theme", next); } catch (e) {}
    sync();
  });
  sync();
})();

(function () {
  const p = document.getElementById("preview");
  for (let i = 0; i < 64; i++) p.appendChild(document.createElement("div"));
})();

// HSL green→yellow→red as intensity rises.
function intensityColor(t) {
  t = Math.max(0, Math.min(1, t));
  const hue = 120 * (1 - t);  // 120 = green, 60 = yellow, 0 = red
  return `hsl(${hue}, 75%, 50%)`;
}

const ws = (function () {
  const url = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  let sock = null, backoff = 500;
  const handlers = [];
  function set_status(live) {
    document.getElementById("status").classList.toggle("live", live);
    document.getElementById("status-text").textContent = live ? "live" : "offline";
  }
  function connect() {
    sock = new WebSocket(url);
    sock.addEventListener("open", () => { backoff = 500; set_status(true); });
    sock.addEventListener("close", () => {
      set_status(false);
      setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, 5000);
    });
    sock.addEventListener("message", (ev) => {
      try { const msg = JSON.parse(ev.data); handlers.forEach(h => h(msg)); }
      catch (e) { console.error(e); }
    });
  }
  connect();
  return {
    send(obj) { if (sock && sock.readyState === WebSocket.OPEN) sock.send(JSON.stringify(obj)); },
    on(fn) { handlers.push(fn); },
  };
})();

let beat_flash_until = 0;
const previewCells = document.getElementById("preview").children;

function setVU(id, value, scale) {
  const t = Math.max(0, Math.min(1, value * (scale || 1)));
  const el = document.getElementById(id);
  el.style.width = (t * 100).toFixed(0) + "%";
  el.style.background = intensityColor(t);
}

ws.on((msg) => {
  if (msg.type !== "state") return;
  document.getElementById("scene-name").textContent = msg.scene || "—";
  document.getElementById("source-name").textContent = msg.source || "—";
  document.getElementById("bpm").textContent = msg.bpm ? msg.bpm.toFixed(0) : "—";
  document.getElementById("conf").textContent = msg.beat_confidence != null
    ? (msg.beat_confidence * 100).toFixed(0) + "%" : "—";
  setVU("vu-rms",    msg.rms);
  setVU("vu-bass",   msg.bass);
  setVU("vu-treble", msg.treble);
  setVU("vu-flux",   msg.spectral_flux, 2.0);
  const pill = document.getElementById("beat-pill");
  pill.textContent = msg.beat_count;
  if (msg.is_beat) { pill.classList.add("flash"); beat_flash_until = performance.now() + 120; }
  else if (performance.now() > beat_flash_until) { pill.classList.remove("flash"); }
  if (msg.grid && msg.grid.length === 192) {
    const g = msg.grid;
    for (let i = 0; i < 64; i++) {
      const r = g[i*3] * 4, gC = g[i*3+1] * 4, b = g[i*3+2] * 4;
      previewCells[i].style.background = (r||gC||b) ? `rgb(${r},${gC},${b})` : "#050505";
    }
  }
});

function bindRadio(listId, sendKey, valueTransform) {
  const list = document.getElementById(listId);
  if (!list) return;
  list.querySelectorAll("label").forEach(l => {
    l.addEventListener("click", () => {
      list.querySelectorAll("label").forEach(x => x.classList.remove("on"));
      l.classList.add("on");
      const input = l.querySelector("input");
      input.checked = true;
      const v = valueTransform ? valueTransform(input.value) : input.value;
      ws.send({ type: sendKey, value: v });
    });
  });
  const checked = list.querySelector("input:checked");
  if (checked) checked.parentElement.classList.add("on");
}
bindRadio("focus-list",    "focus",  v => v === "auto" ? null : v);
bindRadio("mirror-list",   "mirror", v => v);
bindRadio("beat-mul-list", "beat_multiplier", v => parseFloat(v));
bindRadio("font-list",     "text_font", v => v);
bindRadio("dir-list",      "text_dir",  v => v);

// Scene list: radio for the per-scene picks. auto/none handled by buttons.
(function () {
  const list = document.getElementById("scene-list");
  list.querySelectorAll("label").forEach(l => {
    l.addEventListener("click", () => {
      list.querySelectorAll("label").forEach(x => x.classList.remove("on"));
      l.classList.add("on");
      const input = l.querySelector("input");
      input.checked = true;
      ws.send({ type: "scene", value: input.value });
    });
  });
})();
document.getElementById("scene-auto").addEventListener("click", () => {
  document.querySelectorAll("#scene-list label").forEach(l => l.classList.remove("on"));
  document.querySelectorAll("#scene-list input").forEach(i => { i.checked = false; });
  ws.send({ type: "scene", value: null });
});
document.getElementById("scene-none").addEventListener("click", () => {
  document.querySelectorAll("#scene-list label").forEach(l => l.classList.remove("on"));
  document.querySelectorAll("#scene-list input").forEach(i => { i.checked = false; });
  ws.send({ type: "scene", value: "none" });
});

// Palette
(function () {
  const list = document.getElementById("palette-list");
  list.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      list.querySelectorAll("button").forEach(b => b.classList.remove("on"));
      btn.classList.add("on");
      ws.send({ type: "palette", value: btn.dataset.palette || null });
    });
  });
  list.querySelector('button[data-palette=""]').classList.add("on");
})();

// Shape + Emoji: click → set bitmap and lock scene to "shape"
function bindBitmapList(listId) {
  const list = document.getElementById(listId);
  if (!list) return;
  list.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      // Single global "selected bitmap" — clear other bitmap-list buttons too
      document.querySelectorAll("#shape-list button, #emoji-list button")
        .forEach(b => b.classList.remove("on"));
      btn.classList.add("on");
      ws.send({ type: "shape", value: btn.dataset.shape });
      ws.send({ type: "scene", value: "shape" });
      document.querySelectorAll("#scene-list label").forEach(l => l.classList.remove("on"));
      const radio = document.querySelector('#scene-list input[value="shape"]');
      if (radio) { radio.checked = true; radio.parentElement.classList.add("on"); }
    });
  });
}
bindBitmapList("shape-list");
bindBitmapList("emoji-list");

// Mutator
(function () {
  const list = document.getElementById("mutator-list");
  function emit() {
    const names = Array.from(list.querySelectorAll("input:checked")).map(i => i.value);
    ws.send({ type: "mutator", value: names });
  }
  list.querySelectorAll("label").forEach(l => {
    l.addEventListener("click", () => {
      const input = l.querySelector("input");
      setTimeout(() => { l.classList.toggle("on", input.checked); emit(); }, 0);
    });
  });
  document.getElementById("mutator-none").addEventListener("click", () => {
    list.querySelectorAll("input").forEach(i => { i.checked = false; });
    list.querySelectorAll("label").forEach(l => l.classList.remove("on"));
    emit();
  });
})();

function bindSlider(id, sendKey, fmt) {
  const el = document.getElementById(id);
  const val = document.getElementById(id + "-val");
  el.addEventListener("input", () => {
    val.textContent = fmt(parseFloat(el.value));
    ws.send({ type: sendKey, value: parseFloat(el.value) });
  });
}
bindSlider("hue",         "hue",         v => v.toFixed(0) + "°");
bindSlider("contrast",    "contrast",    v => v.toFixed(2));
bindSlider("gamma",       "gamma",       v => v.toFixed(2));
bindSlider("brightness",  "brightness",  v => v.toFixed(2));
bindSlider("trail",       "trail",       v => v.toFixed(2));
bindSlider("decay",       "decay_rate",  v => v.toFixed(2));
bindSlider("sensitivity", "sensitivity", v => v.toFixed(2));
bindSlider("intensity",   "intensity",   v => v.toFixed(2));

// Tempo + auto
(function () {
  const tempo = document.getElementById("tempo");
  const tempoVal = document.getElementById("tempo-val");
  const auto = document.getElementById("tempo-auto");
  function emit() {
    if (auto.checked) { tempo.disabled = true; tempoVal.textContent = "auto"; ws.send({ type: "tempo", value: null }); }
    else { tempo.disabled = false; tempoVal.textContent = tempo.value + " bpm"; ws.send({ type: "tempo", value: parseFloat(tempo.value) }); }
  }
  auto.addEventListener("change", emit);
  tempo.addEventListener("input", emit);
})();

function bindToggle(id, sendKey) {
  const el = document.getElementById(id);
  el.addEventListener("click", () => {
    const on = !el.classList.contains("on");
    el.classList.toggle("on", on);
    ws.send({ type: sendKey, value: on });
  });
}
bindToggle("invert", "invert");
bindToggle("complementary", "complementary");

(function () {
  const el = document.getElementById("blackout");
  el.addEventListener("click", () => {
    const on = !el.classList.contains("on");
    el.classList.toggle("on", on);
    el.textContent = on ? "clear (on)" : "clear";
    ws.send({ type: "blackout", value: on });
  });
})();

(function () {
  const input = document.getElementById("text-input");
  const speed = document.getElementById("text-speed");
  const speedVal = document.getElementById("text-speed-val");
  input.addEventListener("input", () => ws.send({ type: "text", value: input.value }));
  speed.addEventListener("input", () => {
    speedVal.textContent = speed.value;
    ws.send({ type: "text_speed", value: parseFloat(speed.value) });
  });
  document.getElementById("text-show").addEventListener("click", () => {
    ws.send({ type: "scene", value: "text" });
    document.querySelectorAll("#scene-list label").forEach(l => l.classList.remove("on"));
    const radio = document.querySelector('#scene-list input[value="text"]');
    if (radio) { radio.checked = true; radio.parentElement.classList.add("on"); }
  });
})();

// ---------- Reset buttons ----------
function setSliderDefault(id, defaultVal, sendKey, fmt) {
  const el = document.getElementById(id);
  el.value = defaultVal;
  document.getElementById(id + "-val").textContent = fmt(defaultVal);
  ws.send({ type: sendKey, value: defaultVal });
}
function setRadioDefault(listId, value, sendKey, valueTransform) {
  const list = document.getElementById(listId);
  list.querySelectorAll("label").forEach(l => l.classList.remove("on"));
  const sel = list.querySelector(`input[value="${value}"]`);
  if (sel) { sel.checked = true; sel.parentElement.classList.add("on"); }
  const v = valueTransform ? valueTransform(value) : value;
  ws.send({ type: sendKey, value: v });
}
function setToggleDefault(id, on, sendKey) {
  const el = document.getElementById(id);
  el.classList.toggle("on", on);
  ws.send({ type: sendKey, value: on });
}

const RESETS = {
  focus: () => setRadioDefault("focus-list", "auto", "focus", v => v === "auto" ? null : v),
  palette: () => {
    const list = document.getElementById("palette-list");
    list.querySelectorAll("button").forEach(b => b.classList.remove("on"));
    list.querySelector('button[data-palette=""]').classList.add("on");
    ws.send({ type: "palette", value: null });
  },
  color: () => {
    setSliderDefault("hue", 0, "hue", v => v.toFixed(0) + "°");
    setSliderDefault("contrast", 1.0, "contrast", v => v.toFixed(2));
    setSliderDefault("gamma", 2.2, "gamma", v => v.toFixed(2));
    setSliderDefault("brightness", 1.0, "brightness", v => v.toFixed(2));
    setToggleDefault("invert", false, "invert");
    setToggleDefault("complementary", false, "complementary");
  },
  motion: () => {
    setSliderDefault("trail", 0, "trail", v => v.toFixed(2));
    setSliderDefault("decay", 0.85, "decay_rate", v => v.toFixed(2));
    setRadioDefault("mirror-list", "off", "mirror", v => v);
  },
  audio: () => {
    setSliderDefault("sensitivity", 1.0, "sensitivity", v => v.toFixed(2));
    setSliderDefault("intensity", 1.0, "intensity", v => v.toFixed(2));
  },
  tempo: () => {
    const auto = document.getElementById("tempo-auto");
    auto.checked = true;
    document.getElementById("tempo").value = 120;
    document.getElementById("tempo").disabled = true;
    document.getElementById("tempo-val").textContent = "auto";
    ws.send({ type: "tempo", value: null });
    setRadioDefault("beat-mul-list", "1.0", "beat_multiplier", v => parseFloat(v));
  },
  scene: () => {
    document.querySelectorAll("#scene-list label").forEach(l => l.classList.remove("on"));
    document.querySelectorAll("#scene-list input").forEach(i => { i.checked = false; });
    ws.send({ type: "scene", value: null });  // auto
  },
  mutator: () => {
    const list = document.getElementById("mutator-list");
    list.querySelectorAll("input").forEach(i => { i.checked = false; });
    list.querySelectorAll("label").forEach(l => l.classList.remove("on"));
    ws.send({ type: "mutator", value: [] });
  },
  text: () => {
    document.getElementById("text-input").value = "LAUNCH LIGHTS";
    ws.send({ type: "text", value: "LAUNCH LIGHTS" });
    setRadioDefault("font-list", "5x7", "text_font", v => v);
    setRadioDefault("dir-list",  "left", "text_dir", v => v);
    document.getElementById("text-speed").value = 8;
    document.getElementById("text-speed-val").textContent = 8;
    ws.send({ type: "text_speed", value: 8 });
  },
  shape: () => {
    document.querySelectorAll("#shape-list button, #emoji-list button")
      .forEach(b => b.classList.remove("on"));
    const heart = document.querySelector('#emoji-list button[data-shape="heart"]');
    if (heart) heart.classList.add("on");
    ws.send({ type: "shape", value: "heart" });
  },
};
document.querySelectorAll("button.reset").forEach(b => {
  b.addEventListener("click", () => {
    const card = b.dataset.reset;
    if (RESETS[card]) RESETS[card]();
  });
});
</script>
</body>
</html>"""


def _render_page(viz_names: list[str], palette_names: list[str],
                 shape_names: list[str], emoji_names: list[str]) -> str:
    scene_opts = "\n      ".join(
        f'<label data-scene="{n}"><input type="radio" name="scene" value="{n}">{n}</label>'
        for n in viz_names
    )
    mutator_opts = "\n      ".join(
        f'<label data-mutator="{n}"><input type="checkbox" value="{n}">{n}</label>'
        for n in viz_names
    )
    palette_opts = "\n      ".join(
        f'<button data-palette="{n}">{n}</button>' for n in palette_names
    )

    def _btn(n: str, glyphs: dict) -> str:
        glyph = glyphs.get(n, "")
        label = f"{glyph} {n}" if glyph else n
        return f'<button data-shape="{n}">{label}</button>'

    shape_opts = "\n      ".join(_btn(n, SHAPE_GLYPHS) for n in shape_names)
    emoji_opts = "\n      ".join(_btn(n, EMOJI_GLYPHS) for n in emoji_names)

    return (PAGE_TMPL
            .replace("{{SCENE_OPTIONS}}", scene_opts)
            .replace("{{MUTATOR_OPTIONS}}", mutator_opts)
            .replace("{{PALETTE_OPTIONS}}", palette_opts)
            .replace("{{SHAPE_OPTIONS}}", shape_opts)
            .replace("{{EMOJI_OPTIONS}}", emoji_opts))


# Hidden from the scene picker (Clear button handles blackout).
HIDDEN_FROM_SCENE_PICKER = {"blackout"}


class ControlServer:
    def __init__(self, audio: "AudioSource", host: str = "127.0.0.1", port: int = 8095,
                 source_label: str = "audio") -> None:
        self._audio = audio
        self._host = host
        self._port = port
        self._source_label = source_label
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        from launch_lights.video.audio_show import ALL_VIZES, PALETTES, SHAPES, EMOJIS
        self._all_viz_names = [c.name for c in ALL_VIZES]
        picker = sorted(c.name for c in ALL_VIZES if c.name not in HIDDEN_FROM_SCENE_PICKER)
        palettes = sorted(PALETTES.keys())
        shapes = sorted(SHAPES.keys())
        emojis = sorted(EMOJIS.keys())
        self._page = _render_page(picker, palettes, shapes, emojis).encode("utf-8")
        with open(os.path.join(STATIC_DIR, "InterVariable.ttf"), "rb") as f:
            self._font = f.read()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="control-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _process_request(self, conn: ServerConnection, request: Request) -> Optional[Response]:
        path = request.path
        if path == "/ws":
            return None
        if path == "/":
            return _http(200, "OK", self._page, "text/html; charset=utf-8")
        if path == "/static/InterVariable.ttf":
            return _http(200, "OK", self._font, "font/ttf")
        return _http(404, "Not Found", b"not found\n", "text/plain")

    async def _ws_handler(self, conn: ServerConnection) -> None:
        push_task = asyncio.create_task(self._push_loop(conn))
        try:
            async for raw in conn:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._apply(msg)
        finally:
            push_task.cancel()
            try:
                await push_task
            except asyncio.CancelledError:
                pass

    async def _push_loop(self, conn: ServerConnection) -> None:
        interval = 1.0 / STATE_PUSH_HZ
        while True:
            try:
                await conn.send(json.dumps(self._snapshot()))
            except Exception:
                return
            await asyncio.sleep(interval)

    def _snapshot(self) -> dict:
        a = self._audio
        grid = a.latest_frame_grid()
        flat = grid.reshape(-1).tolist()
        return {
            "type": "state",
            "source": self._source_label,
            "scene": a.show.current_primary_name(),
            "rms": float(min(1.0, getattr(a, "_last_rms", 0.0))),
            "bass": float(getattr(a, "_last_bass", 0.0)),
            "treble": float(getattr(a, "_last_treble", 0.0)),
            "bpm": float(getattr(a, "_last_bpm", 0.0)),
            "is_beat": bool(getattr(a, "_last_is_beat", False)),
            "beat_count": int(getattr(a, "_beat_count", 0)),
            "beat_confidence": float(getattr(a, "_last_beat_confidence", 0.0)),
            "spectral_flux": float(getattr(a, "_last_flux", 0.0)),
            "grid": flat,
        }

    def _apply(self, msg: dict) -> None:
        kind = msg.get("type")
        v = msg.get("value")
        a = self._audio
        if kind == "scene":
            if v == "none":
                a.show.set_locked("blackout")
            elif v in self._all_viz_names:
                a.show.set_locked(v)
            else:
                a.show.set_locked(None)
        elif kind == "mutator" and isinstance(v, list):
            a.show.set_extras([n for n in v if n in self._all_viz_names])
        elif kind == "sensitivity" and isinstance(v, (int, float)):
            a.set_gain(float(v))
        elif kind == "intensity" and isinstance(v, (int, float)):
            a.set_intensity(float(v))
        elif kind == "decay_rate" and isinstance(v, (int, float)):
            a.set_decay_rate(float(v))
        elif kind == "tempo":
            a.set_tempo_override(v if v is not None else None)
        elif kind == "focus":
            a.set_focus(v)
        elif kind == "brightness" and isinstance(v, (int, float)):
            a.show.set_brightness(float(v))
        elif kind == "palette":
            a.show.set_palette(v)
        elif kind == "hue" and isinstance(v, (int, float)):
            a.show.set_hue(float(v))
        elif kind == "contrast" and isinstance(v, (int, float)):
            a.show.set_contrast(float(v))
        elif kind == "gamma" and isinstance(v, (int, float)):
            a.show.set_gamma(float(v))
        elif kind == "trail" and isinstance(v, (int, float)):
            a.show.set_trail(float(v))
        elif kind == "mirror" and isinstance(v, str):
            a.show.set_mirror(v)
        elif kind == "invert":
            a.show.set_invert(bool(v))
        elif kind == "complementary":
            a.show.set_complementary(bool(v))
        elif kind == "blackout":
            a.show.set_blackout(bool(v))
        elif kind == "beat_multiplier" and isinstance(v, (int, float)):
            a.show.set_beat_multiplier(float(v))
        elif kind == "text" and isinstance(v, str):
            a.show.set_text(text=v)
        elif kind == "text_font" and isinstance(v, str):
            a.show.set_text(font=v)
        elif kind == "text_speed" and isinstance(v, (int, float)):
            a.show.set_text(speed=float(v))
        elif kind == "text_dir" and isinstance(v, str):
            a.show.set_text(direction=v)
        elif kind == "shape" and isinstance(v, str):
            a.show.set_shape(v)

    def _run(self) -> None:
        async def main():
            self._loop = asyncio.get_running_loop()
            async with serve(
                self._ws_handler, self._host, self._port,
                process_request=self._process_request,
            ):
                log.info("control server listening on http://%s:%d", self._host, self._port)
                await self._loop.create_future()
        try:
            asyncio.run(main())
        except Exception as e:
            log.warning("control server stopped: %s", e)
