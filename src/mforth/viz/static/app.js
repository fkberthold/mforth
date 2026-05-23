// Stub viz client (bead mforth-10t.20).
//
// The real client — world pane, block detail, Forth pane, stack pane,
// event log — is bead mforth-10t.21. This stub exists so the static
// shell loads without 404s and so the WebSocket connect path can be
// smoke-tested manually.
//
// Protocol (mirror of src/mforth/viz/server.py docstring):
//   server -> client : {type:'snapshot', world_state:{...}}
//                      {type:'event', event_type:'...', payload:{...}}
//                      {type:'control_ack', cmd:...}
//   client -> server : {type:'control', cmd:'pause'|'resume'|'step'}

(function () {
  "use strict";

  const wsHost = window.location.hostname || "127.0.0.1";
  // Convention: WS port = HTTP port + 1 unless overridden by the
  // embedder. Bead .21 will surface a config knob; for now we just
  // log a hint and let manual testers pass ?wsPort=NNNN.
  const params = new URLSearchParams(window.location.search);
  const wsPort = parseInt(params.get("wsPort"), 10);
  if (!Number.isFinite(wsPort)) {
    console.log("mforth-viz: pass ?wsPort=NNNN to connect");
    return;
  }
  const ws = new WebSocket(`ws://${wsHost}:${wsPort}`);
  ws.addEventListener("open", () => {
    const el = document.getElementById("mforth-status");
    if (el) {
      el.textContent = "connected";
      el.dataset.state = "connected";
    }
  });
  ws.addEventListener("message", (msg) => {
    console.log("mforth-viz:", msg.data);
  });
})();
