// mforth web visualizer client (bead mforth-10t.21). Vanilla JS, no framework, no build step. Budget: <500 lines.
// Protocol: drawer drawer_mforth_decisions_a8e467829495804153cd193d (server in src/mforth/viz/server.py).
//   server->client: {type:'snapshot', world_state:{links,variables,tick}} | {type:'event', event_type:'<ClassName>', payload:{...}} | {type:'control_ack', cmd}
//   client->server: {type:'control', cmd:'pause'|'resume'|'step'}
// Event types handled: MessagePrintEvent, MessagePrintflushEvent, SensorReadEvent, LinkResolvedEvent, WaitEvent, Variable{Read,Write}Event.
// Unknown event_type still flows into the event log (per .20 drawer's "do not enumerate" guidance).

(function () {
  "use strict";
  // ---- connection (auto-reconnect with backoff) ----
  const RECONNECT_MIN_MS = 500;
  const RECONNECT_MAX_MS = 15000;
  const PULSE_MS = 600;

  const state = {
    ws: null,
    backoff: RECONNECT_MIN_MS,
    closedByUser: false,
    links: {},          // name -> {type, state, pos:{x,y}}
    selectedBlock: null,
    messageBuffer: "",  // accumulated print text until printflush
    stack: [],          // synthesised from VariableWriteEvent on s0..sN
    tick: 0,
    source: null,       // {file, text} when bead .22 plumbs it through
    currentLoc: null,   // {file, line, col}
  };

  function wsUrl() {
    const host = window.location.hostname || "127.0.0.1";
    const params = new URLSearchParams(window.location.search);
    const wsPortRaw = parseInt(params.get("wsPort"), 10);
    // Convention from .20 stub: when no override given, try http_port+1.
    const httpPort = parseInt(window.location.port, 10);
    const wsPort = Number.isFinite(wsPortRaw)
      ? wsPortRaw
      : (Number.isFinite(httpPort) ? httpPort + 1 : 8765);
    return `ws://${host}:${wsPort}`;
  }

  function connect() {
    setStatus("connecting");
    let ws;
    try {
      ws = new WebSocket(wsUrl());
    } catch (e) {
      console.warn("mforth-viz: WebSocket construct failed:", e);
      scheduleReconnect();
      return;
    }
    state.ws = ws;

    ws.addEventListener("open", () => {
      state.backoff = RECONNECT_MIN_MS;
      setStatus("connected");
    });
    ws.addEventListener("message", (msg) => {
      let parsed;
      try {
        parsed = JSON.parse(msg.data);
      } catch (_) {
        return;
      }
      dispatch(parsed);
    });
    ws.addEventListener("close", () => {
      setStatus("disconnected");
      state.ws = null;
      if (!state.closedByUser) scheduleReconnect();
    });
    ws.addEventListener("error", () => {
      // Browsers fire 'close' right after 'error'; reconnect happens there.
    });
  }

  function scheduleReconnect() {
    const delay = state.backoff;
    state.backoff = Math.min(state.backoff * 2, RECONNECT_MAX_MS);
    setTimeout(connect, delay);
  }

  function setStatus(s) {
    const el = document.getElementById("mforth-status");
    if (!el) return;
    el.textContent = s;
    el.dataset.state = s;
  }

  function sendControl(cmd) {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
    state.ws.send(JSON.stringify({ type: "control", cmd }));
  }

  // ---- dispatch ----
  function dispatch(msg) {
    if (!msg || typeof msg !== "object") return;
    if (msg.type === "snapshot") return onSnapshot(msg.world_state || {});
    if (msg.type === "event")    return onEvent(msg.event_type, msg.payload || {});
    if (msg.type === "control_ack") return onControlAck(msg.cmd);
  }

  function onSnapshot(world) {
    state.links = {};
    const links = world.links || {};
    Object.keys(links).forEach((name) => {
      state.links[name] = {
        type: links[name].type,
        state: links[name].state || {},
        pos: null,
      };
    });
    state.tick = world.tick || 0;
    // Forward-compat: bead .22 may plumb source through.
    if (world.source) {
      state.source = world.source;
      renderForthSource();
    }
    layoutLinks();
    renderWorld();
    renderTick();
    renderStack();
    renderBlockDetail();
  }

  function onEvent(kind, payload) {
    appendLog(kind, payload);
    switch (kind) {
      case "MessagePrintEvent":
        state.messageBuffer += String(payload.text || "");
        if (state.selectedBlock) renderBlockDetail();
        break;
      case "MessagePrintflushEvent": {
        const name = payload.block_name;
        const buf = payload.buffer != null ? String(payload.buffer) : state.messageBuffer;
        state.messageBuffer = "";
        if (state.links[name]) {
          state.links[name].state = Object.assign({}, state.links[name].state, { buffer: buf });
          if (state.selectedBlock === name) renderBlockDetail();
        }
        break;
      }
      case "SensorReadEvent":
        pulseEdge(payload.block_name);
        break;
      case "LinkResolvedEvent": {
        const name = payload.block_name;
        if (!state.links[name]) {
          state.links[name] = { type: "generic", state: {}, pos: null };
        }
        layoutLinks();
        renderWorld();
        break;
      }
      case "WaitEvent":
        state.tick = (payload.timestamp != null) ? payload.timestamp : (state.tick + (payload.seconds || 0));
        renderTick();
        break;
      case "VariableWriteEvent":
      case "VariableReadEvent": {
        // Best-effort stack synthesis: track writes to s0..sN.
        const m = /^s(\d+)$/.exec(String(payload.name || ""));
        if (m && kind === "VariableWriteEvent") {
          const idx = parseInt(m[1], 10);
          while (state.stack.length <= idx) state.stack.push(0);
          state.stack[idx] = payload.value;
          renderStack();
        }
        break;
      }
      default:
        // Unknown event_type — already logged. No-op on the panes.
        break;
    }
    if (payload && payload.timestamp != null && kind !== "WaitEvent") {
      // Most events stamp their tick; keep tick monotonic with the stream.
      if (payload.timestamp > state.tick) {
        state.tick = payload.timestamp;
        renderTick();
      }
    }
    // (file,line,col) highlight is forward-compat — apply if present.
    if (payload && payload.file && payload.line != null) {
      state.currentLoc = { file: payload.file, line: payload.line, col: payload.col || 0 };
      renderForthHighlight();
    }
  }

  function onControlAck(cmd) {
    const el = document.getElementById("mforth-ctrl-ack");
    if (!el) return;
    el.textContent = `ack: ${cmd}`;
    clearTimeout(onControlAck._t);
    onControlAck._t = setTimeout(() => { el.textContent = ""; }, 1200);
  }

  // ---- world pane (SVG) ----
  function layoutLinks() {
    const names = Object.keys(state.links);
    const n = names.length;
    if (n === 0) return;
    const r = 70;
    names.forEach((name, i) => {
      const theta = (-Math.PI / 2) + (2 * Math.PI * i / n);
      state.links[name].pos = { x: r * Math.cos(theta), y: r * Math.sin(theta) };
    });
  }

  function renderWorld() {
    const edges = document.getElementById("mforth-world-edges");
    const nodes = document.getElementById("mforth-world-nodes");
    const svg = document.getElementById("mforth-world-svg");
    if (!edges || !nodes || !svg) return;
    edges.innerHTML = "";
    nodes.innerHTML = "";
    const names = Object.keys(state.links);
    svg.classList.toggle("has-links", names.length > 0);
    names.forEach((name) => {
      const pos = state.links[name].pos;
      if (!pos) return;
      const line = svgEl("line", {
        x1: 0, y1: 0, x2: pos.x, y2: pos.y,
        class: "world-edge",
        "data-block": name,
      });
      edges.appendChild(line);
      const circle = svgEl("circle", {
        cx: pos.x, cy: pos.y, r: 6,
        class: "world-node" + (state.selectedBlock === name ? " selected" : ""),
        "data-block": name,
      });
      circle.addEventListener("click", () => selectBlock(name));
      nodes.appendChild(circle);
      const label = svgEl("text", {
        x: pos.x, y: pos.y + 12, class: "world-node-label",
      });
      label.textContent = name;
      nodes.appendChild(label);
    });
  }

  function svgEl(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.keys(attrs).forEach((k) => el.setAttribute(k, attrs[k]));
    return el;
  }

  function pulseEdge(name) {
    const edge = document.querySelector(`#mforth-world-edges .world-edge[data-block="${cssEscape(name)}"]`);
    if (!edge) return;
    edge.classList.add("pulse");
    setTimeout(() => edge.classList.remove("pulse"), PULSE_MS);
  }

  function cssEscape(s) {
    if (window.CSS && CSS.escape) return CSS.escape(s);
    return String(s).replace(/["\\]/g, "\\$&");
  }

  // ---- block detail pane ----
  function selectBlock(name) {
    state.selectedBlock = name;
    const detail = document.getElementById("mforth-block-detail");
    if (detail) detail.dataset.collapsed = "false";
    renderWorld();
    renderBlockDetail();
  }

  function renderBlockDetail() {
    const body = document.getElementById("mforth-block-body");
    const empty = document.getElementById("mforth-block-empty");
    if (!body || !empty) return;
    if (!state.selectedBlock) {
      body.hidden = true;
      empty.style.display = "";
      return;
    }
    const link = state.links[state.selectedBlock];
    if (!link) {
      body.hidden = true;
      empty.style.display = "";
      empty.textContent = `(block '${state.selectedBlock}' not in snapshot)`;
      return;
    }
    body.hidden = false;
    empty.style.display = "none";
    setText("mforth-block-name", state.selectedBlock);
    setText("mforth-block-type", link.type);
    const pre = document.getElementById("mforth-block-state");
    if (pre) pre.textContent = formatBlockState(link.type, link.state);
  }

  function formatBlockState(type, st) {
    if (!st) return "";
    if (type === "message") {
      const buf = st.buffer;
      if (Array.isArray(buf)) return buf.join("");
      return buf != null ? String(buf) : "";
    }
    if (type === "memory-cell") {
      const data = st.data || [];
      const nonZero = data.map((v, i) => [i, v]).filter((p) => p[1] !== 0).slice(0, 32);
      if (nonZero.length === 0) return `(${data.length} cells, all zero)`;
      return nonZero.map(([i, v]) => `[${i}] = ${v}`).join("\n") +
             (data.length > 32 ? `\n... (${data.length - 32} more)` : "");
    }
    if (type === "switch") return `on = ${!!st.on}`;
    try { return JSON.stringify(st, null, 2); } catch (_) { return String(st); }
  }

  // ---- Forth pane ----
  function renderForthSource() {
    const pre = document.getElementById("mforth-forth-source");
    if (!pre || !state.source || !state.source.text) return;
    pre.innerHTML = "";
    state.source.text.split("\n").forEach((line, idx) => {
      const span = document.createElement("span");
      span.className = "forth-line";
      span.dataset.line = String(idx + 1);
      const no = document.createElement("span");
      no.className = "forth-line-no";
      no.textContent = String(idx + 1).padStart(3, " ");
      span.appendChild(no);
      span.appendChild(document.createTextNode(line + "\n"));
      pre.appendChild(span);
    });
  }

  function renderForthHighlight() {
    if (!state.currentLoc) return;
    const pre = document.getElementById("mforth-forth-source");
    if (!pre) return;
    pre.querySelectorAll(".forth-line.current").forEach((el) => el.classList.remove("current"));
    const target = pre.querySelector(`.forth-line[data-line="${state.currentLoc.line}"]`);
    if (target) {
      target.classList.add("current");
      target.scrollIntoView({ block: "nearest" });
    }
  }

  // ---- stack pane ----
  function renderStack() {
    const ol = document.getElementById("mforth-stack-list");
    if (!ol) return;
    ol.innerHTML = "";
    if (state.stack.length === 0) {
      const li = document.createElement("li");
      li.dataset.empty = "";
      li.textContent = "(empty)";
      ol.appendChild(li);
      return;
    }
    state.stack.forEach((v, i) => {
      const li = document.createElement("li");
      if (i === state.stack.length - 1) li.classList.add("top");
      const slot = document.createElement("span");
      slot.textContent = `s${i}`;
      const val = document.createElement("span");
      val.textContent = String(v);
      li.appendChild(slot);
      li.appendChild(val);
      ol.appendChild(li);
    });
  }

  // ---- tick ----
  function renderTick() {
    setText("mforth-tick", formatTick(state.tick));
  }

  function formatTick(t) {
    if (typeof t !== "number") return String(t);
    return t.toFixed(t === Math.floor(t) ? 0 : 3);
  }

  // ---- event log ----
  const LOG_MAX = 500;

  function appendLog(kind, payload) {
    const ol = document.getElementById("mforth-log-list");
    if (!ol) return;
    const li = document.createElement("li");
    li.dataset.type = kind || "";
    const t = (payload && payload.timestamp != null) ? formatTick(payload.timestamp) : "";
    const timeEl = document.createElement("span");
    timeEl.className = "log-time";
    timeEl.textContent = t;
    const typeEl = document.createElement("span");
    typeEl.className = "log-type";
    typeEl.textContent = kind || "?";
    const payEl = document.createElement("span");
    payEl.className = "log-payload";
    payEl.textContent = summarisePayload(payload);
    li.appendChild(timeEl);
    li.appendChild(typeEl);
    li.appendChild(payEl);
    applyFilter(li);
    ol.appendChild(li);
    while (ol.children.length > LOG_MAX) ol.removeChild(ol.firstChild);
    ol.scrollTop = ol.scrollHeight;
  }

  function summarisePayload(p) {
    if (!p) return "";
    const skip = new Set(["timestamp"]);
    const parts = [];
    Object.keys(p).forEach((k) => {
      if (skip.has(k)) return;
      const v = p[k];
      const s = typeof v === "string" ? JSON.stringify(v) : String(v);
      parts.push(`${k}=${s}`);
    });
    return parts.join(" ");
  }

  function applyFilter(li) {
    const input = document.getElementById("mforth-log-filter");
    if (!input || !input.value) { li.classList.remove("hidden"); return; }
    const q = input.value.toLowerCase();
    const t = (li.dataset.type || "").toLowerCase();
    li.classList.toggle("hidden", t.indexOf(q) === -1);
  }

  function refilterAll() {
    const ol = document.getElementById("mforth-log-list");
    if (!ol) return;
    Array.from(ol.children).forEach(applyFilter);
  }

  // ---- utilities + boot ----
  function setText(id, txt) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(txt);
  }

  function wireControls() {
    ["pause", "resume", "step"].forEach((cmd) => {
      const btn = document.getElementById(`mforth-ctrl-${cmd}`);
      if (btn) btn.addEventListener("click", () => sendControl(cmd));
    });
    const close = document.getElementById("mforth-block-close");
    if (close) close.addEventListener("click", () => {
      state.selectedBlock = null;
      const detail = document.getElementById("mforth-block-detail");
      if (detail) detail.dataset.collapsed = "true";
      renderWorld();
    });
    const toggle = document.getElementById("mforth-log-toggle");
    if (toggle) toggle.addEventListener("click", () => {
      const log = document.getElementById("mforth-event-log");
      if (!log) return;
      const collapsed = log.dataset.collapsed === "true";
      log.dataset.collapsed = collapsed ? "false" : "true";
      toggle.textContent = collapsed ? "[-]" : "[+]";
    });
    const clear = document.getElementById("mforth-log-clear");
    if (clear) clear.addEventListener("click", () => {
      const ol = document.getElementById("mforth-log-list");
      if (ol) ol.innerHTML = "";
    });
    const filter = document.getElementById("mforth-log-filter");
    if (filter) filter.addEventListener("input", refilterAll);
  }

  // Boot
  function boot() {
    wireControls();
    renderTick();
    renderStack();
    renderBlockDetail();
    connect();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  // Expose a tiny test surface so the manual harnesses in
  // tests/manual/viz/ can drive panes without a live WS connection.
  window.__mforthViz = {
    state,
    dispatch,
    onSnapshot,
    onEvent,
    renderWorld,
    renderStack,
    renderBlockDetail,
    renderForthSource,
    renderTick,
  };
})();
