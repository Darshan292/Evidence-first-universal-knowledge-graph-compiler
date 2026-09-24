/* Knowledge Graph Workbench -- the whole frontend.
 *
 * Two rules this file obeys and never bends:
 *
 *   1. Source content reaches the DOM as TEXT. Every insertion goes through
 *      textContent. A file containing "Ignore previous instructions" is a string
 *      on a page, not markup and not an instruction.
 *   2. There is no second answering path. /ask returns whatever kgq.answer.ask
 *      decided; this file renders it. It cannot promote an ABSTAIN, compose an
 *      answer, or show the model's unvalidated prose as the answer.
 */

const EDGE_PREDS = ["CONTAINS", "CALLS", "IMPORTS", "EXTENDS", "HAS_VALUE"];

/* Predicate identity rides a text label plus a dash pattern -- never colour.
   Five categorical hues cannot clear the all-pairs CVD separation gate, so the
   validator's answer was to stop using hue for this channel. */
const DASH = {
  CONTAINS:  "",            // solid
  CALLS:     "7 4",
  IMPORTS:   "1.5 4",
  EXTENDS:   "10 3 2 3",
  HAS_VALUE: "3 3",
};

const SEQ = ["var(--seq-0)", "var(--seq-1)", "var(--seq-2)"];

const state = {
  wid: null, meta: null, stats: null,
  root: null, nbh: null, hops: 1, preds: new Set(EDGE_PREDS),
  selectedEdge: null, selectedNode: null, poll: null,
  view: { k: 1, tx: 0, ty: 0 },
};

/* ── tiny DOM helpers (text-only by construction) ─────────────── */
const $ = (sel, root = document) => root.querySelector(sel);
const SVGNS = "http://www.w3.org/2000/svg";

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = String(v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    e.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return e;
}
function s(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false || v === "") continue;
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "text") e.textContent = String(v);
    else e.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) if (kid) e.append(kid);
  return e;
}
const clear = (n) => { while (n.firstChild) n.removeChild(n.firstChild); return n; };
const fmt = (n) => (typeof n === "number" ? n.toLocaleString("en-US") : String(n ?? "–"));
const bytes = (n) => n >= 1048576 ? (n / 1048576).toFixed(1) + " MB"
                   : n >= 1024 ? (n / 1024).toFixed(1) + " KB" : n + " B";

async function api(path, opts) {
  const r = await fetch(path, opts);
  let body = null;
  try { body = await r.json(); } catch { body = null; }
  if (!r.ok) throw new Error((body && (body.detail || body.error)) || `${r.status} ${r.statusText}`);
  return body;
}

/* ── views ────────────────────────────────────────────────────── */
function show(which) {
  for (const id of ["home", "progress", "workspace"]) $("#" + id).classList.toggle("hidden", id !== which);
}
function setTab(name) {
  for (const t of document.querySelectorAll(".tab")) t.classList.toggle("active", t.dataset.tab === name);
  for (const n of ["overview", "graph", "ask"]) $("#tab-" + n).classList.toggle("hidden", n !== name);
}

/* ── home ─────────────────────────────────────────────────────── */
async function loadHome() {
  show("home");
  $("#ws-chip").classList.add("hidden");
  const data = await api("/api/workspaces");
  renderProvider(data.provider);
  const list = clear($("#ws-list"));
  if (!data.workspaces.length) { list.append(h("p", { class: "muted", text: "None yet." })); return; }
  for (const w of data.workspaces) {
    const c = w.counts || {};
    list.append(h("div", { class: "ws-item" },
      h("div", { class: "row" },
        h("button", { class: "ws-open", text: w.name || w.id, onclick: () => openWorkspace(w.id) }),
        statusTag(w.status)),
      h("div", { class: "muted small", text:
        w.counts ? `${fmt(c.artifacts)} files · ${fmt(c.symbols)} symbols · ${fmt(c.claims)} claims`
                 : "no graph yet" }),
      h("div", { class: "row" },
        h("span", { class: "muted small", text: new Date((w.created || 0) * 1000).toLocaleString() }),
        h("button", { class: "tiny", text: "Delete", onclick: async (e) => {
          e.stopPropagation();
          if (!confirm(`Delete workspace "${w.name || w.id}" and its database?`)) return;
          await api(`/api/workspaces/${w.id}`, { method: "DELETE" });
          loadHome();
        } }))));
  }
}

function statusTag(status) {
  const cls = status === "READY" ? "good"
            : status === "FAILED" || status === "REJECTED" ? "critical"
            : "warning";
  return h("span", { class: "tag " + cls, text: status || "unknown" });
}

function renderProvider(p) {
  const chip = $("#provider-chip");
  chip.className = "chip " + (p && p.configured ? "on" : "off");
  chip.textContent = p && p.configured ? `model: ${p.model}` : "model: not configured";
  chip.title = p && p.configured ? `via ${p.base_url}`
    : (p && p.reason) || "set KGQ_BASE_URL and KGQ_MODEL for semantic answers";
}

/* ── upload ───────────────────────────────────────────────────── */
function wireUpload() {
  const input = $("#file-input"), drop = $("#filedrop"), btn = $("#upload-btn");
  const label = $("#filedrop-label");
  const pick = (f) => {
    if (!f) return;
    input._file = f;
    label.textContent = `${f.name} — ${bytes(f.size)}`;
    btn.disabled = false;
  };
  input.addEventListener("change", () => pick(input.files[0]));
  for (const ev of ["dragenter", "dragover"])
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"])
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); });
  drop.addEventListener("drop", (e) => pick(e.dataTransfer.files[0]));

  btn.addEventListener("click", async () => {
    const f = input._file || input.files[0];
    if (!f) return;
    btn.disabled = true; btn.textContent = "Uploading…";
    const fd = new FormData();
    fd.append("file", f, f.name);
    fd.append("name", f.name);
    try {
      const r = await api("/api/workspaces/upload", { method: "POST", body: fd });
      watch(r.id);
    } catch (e) {
      alert("Upload refused: " + e.message);
    } finally { btn.disabled = false; btn.textContent = "Compile"; }
  });

  $("#demo-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { watch((await api("/api/workspaces/demo", { method: "POST" })).id); }
    catch (err) { alert(err.message); }
    finally { e.target.disabled = false; }
  });
}

/* ── progress ─────────────────────────────────────────────────── */
function watch(wid) {
  state.wid = wid;
  show("progress");
  clear($("#progress-files"));
  $("#progress-error").classList.add("hidden");
  if (state.poll) clearInterval(state.poll);
  const tick = async () => {
    let st;
    try { st = await api(`/api/workspaces/${wid}/status`); }
    catch { return; }
    renderStages(st);
    if (st.stage === "Ready" || (st.metadata && st.metadata.status === "READY")) {
      clearInterval(state.poll); state.poll = null;
      openWorkspace(wid);
    } else if (st.stage === "FAILED" || (st.metadata && ["FAILED", "REJECTED"].includes(st.metadata.status))) {
      clearInterval(state.poll); state.poll = null;
      const b = $("#progress-error");
      clear(b).append(h("span", { class: "label", text: "compile failed" }),
                      document.createTextNode(st.error || "unknown error"));
      b.classList.remove("hidden");
      b.after(h("p", {}, h("button", { class: "secondary", text: "Back", onclick: () => location.hash = "" })));
    }
  };
  tick();
  state.poll = setInterval(tick, 700);
}

function renderStages(st) {
  $("#progress-title").textContent = st.metadata && st.metadata.name
    ? `Compiling ${st.metadata.name}` : "Compiling";
  const stages = st.stages || [];
  const at = stages.indexOf(st.stage);
  const ol = clear($("#stage-list"));
  stages.forEach((name, i) => {
    const cls = at < 0 ? "" : i < at ? "done" : i === at ? "active" : "";
    ol.append(h("li", { class: cls }, h("span", { class: "dot" }), name));
  });
  const files = (st.metadata && st.metadata.files) || [];
  const box = clear($("#progress-files"));
  if (files.length) box.append(uploadTable(files));
}

/* ── workspace ────────────────────────────────────────────────── */
async function openWorkspace(wid) {
  state.wid = wid;
  location.hash = "#/w/" + wid;
  const st = await api(`/api/workspaces/${wid}/status`);
  state.meta = st.metadata || {};
  if ((state.meta.status || st.stage) !== "READY") { watch(wid); return; }
  show("workspace");
  const chip = $("#ws-chip");
  chip.textContent = state.meta.name || wid;
  chip.classList.remove("hidden");
  const data = await api(`/api/workspaces/${wid}/stats`);
  state.stats = data.statistics;
  renderOverview(data);
  resetGraph();
  renderAskHint();
  setTab("overview");
}

/* ── overview ─────────────────────────────────────────────────── */
function renderOverview(data) {
  const st = data.statistics, c = state.meta.compile || {};
  const tiles = clear($("#tiles"));
  const add = (k, n, sub, status) =>
    tiles.append(h("div", { class: "tile" + (status ? " status-" + status : "") },
      h("div", { class: "n", text: fmt(n) }), h("div", { class: "k", text: k }),
      sub ? h("div", { class: "sub", text: sub }) : null));

  add("Files", st.artifacts, `${fmt(st.ok)} parsed`);
  add("Symbols", st.symbols, "functions, classes, pages");
  add("Claims", st.claims, `${fmt(st.relationships)} relationships`);
  add("Evidence", st.evidence, "byte-addressed spans");
  const badFiles = st.failed + st.unsupported;
  add("Not compiled", badFiles, `${fmt(st.failed)} failed · ${fmt(st.unsupported)} unsupported`,
      st.failed ? "critical" : badFiles ? "warning" : "good");
  const viol = c.invariant_violations;
  add("Invariant violations", Array.isArray(viol) ? viol.length : (viol ?? 0),
      Array.isArray(viol) && viol.length ? "see server log" : "database self-check",
      Array.isArray(viol) && viol.length ? "critical" : "good");
  add("Diagnostics", st.diagnostics, "what the extractor could not do");
  // resolution buckets are DETERMINISTIC / HEURISTIC / UNRESOLVED -- a
  // reference is resolved if it landed in either of the first two
  const rez = st.resolution || {};
  const unres = rez.UNRESOLVED || 0;
  const res = Object.entries(rez).reduce((n, [k, v]) => k === "UNRESOLVED" ? n : n + v, 0);
  add("Unresolved references", unres, `${fmt(res)} resolved (deterministic or heuristic)`,
      unres > res ? "warning" : null);

  bars($("#predicate-chart"), st.predicates);
  bars($("#kind-chart"), st.symbol_kinds);

  const probBox = clear($("#problems"));
  if (!data.problems.length) {
    probBox.append(h("p", { class: "muted small", text: "None. Every file produced a graph." }));
  } else {
    probBox.append(h("p", { class: "muted small", text:
      `${data.problems.length} file${data.problems.length === 1 ? "" : "s"} listed.` }));
    probBox.append(h("div", { class: "scrollbox" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ...["File", "Status", "Reason", "Size"].map(t => h("th", { text: t })))),
      h("tbody", {}, ...data.problems.map(p => h("tr", {},
        h("td", {}, h("code", { text: p.rel_path })),
        h("td", {}, h("span", { class: "tag " + (p.parse_status === "FAILED" ? "critical" : "warning"),
                                text: p.parse_status })),
        h("td", { class: "muted", text: p.parse_error || "—" }),
        h("td", { text: bytes(p.size_bytes || 0) })))))));
  }

  const up = clear($("#upload-report"));
  const files = state.meta.files || [];
  if (files.length) up.append(uploadTable(files));
  else up.append(h("p", { class: "muted small", text:
    `Source: ${state.meta.source_name || "—"}. Seen ${fmt(c.seen)} files, parsed ${fmt(c.parsed)}, ` +
    `unsupported ${fmt(c.unsupported)}, failed ${fmt(c.failed)}.` }));
}

function uploadTable(files) {
  const rows = files.slice(0, 400);
  return h("div", {},
    h("div", { class: "scrollbox" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ...["File", "Outcome", "Why", "Size", "sha256"].map(t => h("th", { text: t })))),
      h("tbody", {}, ...rows.map(f => h("tr", {},
        h("td", {}, h("code", { text: f.rel_path })),
        h("td", {}, h("span", { class: "tag " + (f.status === "ACCEPTED" ? "good"
                                : f.status === "REJECTED" ? "critical" : "warning"), text: f.status })),
        h("td", { class: "muted", text: f.reason || "" }),
        h("td", { text: bytes(f.size || 0) }),
        h("td", {}, h("code", { class: "muted", text: (f.sha256 || "").slice(0, 12) || "—" }))))))),
    files.length > rows.length
      ? h("p", { class: "muted small", text: `${files.length - rows.length} more not listed.` }) : null);
}

function bars(box, counts) {
  clear(box);
  const entries = Object.entries(counts || {}).sort((a, b) => b[1] - a[1]);
  if (!entries.length) { box.append(h("p", { class: "muted small", text: "Nothing to show." })); return; }
  const max = entries[0][1] || 1;
  const wrap = h("div", { class: "bars" });
  for (const [k, v] of entries) {
    wrap.append(h("div", { class: "bar-row" },
      h("span", { class: "mono", text: k }),
      h("div", { class: "bar-track" },
        h("div", { class: "bar-fill", style: `width:${Math.max(2, (v / max) * 100)}%` })),
      h("span", { class: "v", text: fmt(v) })));
  }
  box.append(wrap);
}

/* ── symbol search ────────────────────────────────────────────── */
function wireSearch() {
  const input = $("#symbol-search");
  let timer = null;
  let seq = 0;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    // stale hits must not stay clickable while the next query is in flight:
    // clicking one opens a neighbourhood the user did not ask for
    clear($("#search-results")).append(h("p", { class: "muted small", text: "searching…" }));
    const mine = ++seq;
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (q.length < 2) {
        clear($("#search-results")).append(
          h("p", { class: "muted small", text: "Type at least two characters." }));
        return;
      }
      const { results } = await api(`/api/workspaces/${state.wid}/search?q=${encodeURIComponent(q)}`);
      if (mine !== seq) return;                  // a later keystroke already won
      const box = clear($("#search-results"));
      if (!results.length) { box.append(h("p", { class: "muted small", text: "No symbol matches." })); return; }
      for (const r of results) {
        box.append(h("button", { class: "result", onclick: () => loadNeighbourhood(r.symbol_id) },
          h("b", { text: r.qualified_name }),
          h("span", { text: `${r.kind} · ${r.rel_path}` +
            (r.location ? ` · ${r.location}` : "") })));
      }
    }, 160);
  });
}

/* ── graph ────────────────────────────────────────────────────── */
function resetGraph() {
  state.root = null; state.nbh = null; state.selectedEdge = null;
  clear($("#graph-svg")); clear($("#graph-legend"));
  $("#graph-note").textContent = "";
  clear($("#search-results")).append(h("p", { class: "muted small",
    text: "Search for a function, class or method to open its neighbourhood." }));
  clear($("#graph-controls")).append(h("span", { class: "muted small",
    text: "Nothing loaded. Pick a symbol on the left." }));
  clear($("#inspector")).append(h("p", { class: "muted small",
    text: "Click a node or an edge. An edge opens the claim behind it and the source bytes that prove it." }));
}

async function loadNeighbourhood(symbolId) {
  state.root = symbolId;
  state.view = { k: 1, tx: 0, ty: 0 };
  const preds = [...state.preds].join(",");
  const nbh = await api(`/api/workspaces/${state.wid}/neighbourhood/${symbolId}` +
                        `?hops=${state.hops}&predicates=${encodeURIComponent(preds)}`);
  state.nbh = nbh;
  renderControls();
  drawGraph(nbh);
  showNode(symbolId);
  setTab("graph");
}

function renderControls() {
  const box = clear($("#graph-controls"));
  box.append(h("label", {},
    "hops ",
    h("select", { onchange: (e) => { state.hops = Number(e.target.value); loadNeighbourhood(state.root); } },
      ...[1, 2].map(n => h("option", { value: n, selected: n === state.hops, text: String(n) })))));
  for (const p of EDGE_PREDS) {
    box.append(h("label", {},
      h("input", { type: "checkbox", checked: state.preds.has(p),
        onchange: (e) => {
          e.target.checked ? state.preds.add(p) : state.preds.delete(p);
          if (!state.preds.size) { state.preds.add(p); e.target.checked = true; return; }
          loadNeighbourhood(state.root);
        } }),
      h("span", { class: "mono", text: p })));
  }
  box.append(h("button", { class: "tiny", text: "Reset view",
    onclick: () => { state.view = { k: 1, tx: 0, ty: 0 }; drawGraph(state.nbh); } }));
}

/* Deterministic layout: rings by hop depth, then angle-only relaxation.
   Nodes cannot fly off, the picture is the same every time, and distance from
   the centre means exactly one thing -- how many hops away the symbol is. */
function layout(nbh, W, H) {
  const nodes = [], byId = new Map();
  for (const n of nbh.nodes) {
    const node = { id: n.symbol_id, label: n.name || n.qualified_name, q: n.qualified_name,
                   kind: n.kind, path: n.rel_path, depth: Math.min(n.depth ?? 1, 2), real: true };
    nodes.push(node); byId.set(node.id, node);
  }
  const edges = [];
  for (const e of nbh.edges) {
    const a = byId.get(e.source);
    let b = e.target ? byId.get(e.target) : null;
    if (!a) continue;
    if (!b) {                                   // a literal or an unresolved name
      const id = "x:" + e.claim_id;
      b = { id, label: String(e.object ?? "?"), q: String(e.object ?? "?"), kind: e.resolved ? "symbol" : "unresolved",
            path: "", depth: Math.min(a.depth + 1, 2), real: false };
      nodes.push(b); byId.set(id, b);
    }
    edges.push({ ...e, a, b });
  }
  const rings = {}, cx = W / 2, cy = H / 2;
  for (const n of nodes) (rings[n.depth] ||= []).push(n);
  // elliptical rings: a workbench canvas is wide, and a circle would waste it
  const RX = { 0: 0, 1: W * 0.29, 2: W * 0.44 };
  const RY = { 0: 0, 1: H * 0.32, 2: H * 0.46 };
  for (const [d, group] of Object.entries(rings))
    group.forEach((n, i) => { n.ang = (i / group.length) * Math.PI * 2 + Number(d) * 0.7; });

  for (let it = 0; it < 220; it++) {
    for (const e of edges) {
      const [deep, shallow] = e.a.depth >= e.b.depth ? [e.a, e.b] : [e.b, e.a];
      // the centre sits ON the centre, so it has no angle to be attracted to --
      // pulling every spoke toward it collapses the whole ring onto one bearing
      if (shallow.depth === 0 || deep.depth === shallow.depth) continue;
      let d = shallow.ang - deep.ang;
      while (d > Math.PI) d -= 2 * Math.PI;
      while (d < -Math.PI) d += 2 * Math.PI;
      deep.ang += d * 0.05;
    }
    for (const [d, group] of Object.entries(rings)) {
      if (d === "0" || group.length < 2) continue;
      const g = group.slice().sort((p, q) => p.ang - q.ang);
      const min = (2 * Math.PI) / g.length * 0.9;
      for (let i = 0; i < g.length; i++) {
        const a = g[i], b = g[(i + 1) % g.length];
        let gap = b.ang - a.ang;
        while (gap < 0) gap += 2 * Math.PI;
        if (gap < min) { const push = (min - gap) * 0.25; a.ang -= push; b.ang += push; }
      }
    }
  }
  for (const n of nodes) {
    n.x = cx + RX[n.depth] * Math.cos(n.ang);
    n.y = cy + RY[n.depth] * Math.sin(n.ang);
  }
  return { nodes, edges };
}

function drawGraph(nbh) {
  const svg = clear($("#graph-svg"));
  if (!nbh) return;
  const frame = svg.parentElement;
  const W = Math.max(520, Math.round(frame.getBoundingClientRect().width));
  const H = 560;
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.style.height = H + "px";
  const { nodes, edges } = layout(nbh, W, H);
  const g = s("g", { transform: `translate(${state.view.tx},${state.view.ty}) scale(${state.view.k})` });
  svg.append(g);

  const defs = s("defs", {});
  defs.append(s("marker", { id: "arrow", viewBox: "0 0 10 10", refX: 9, refY: 5,
                            markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" },
                s("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "var(--edge)" })));
  svg.append(defs);

  const labelNodes = nodes.length <= 40;
  // Selective direct labels: a predicate repeated fifteen times around one hub
  // is noise, so each predicate is labelled once -- on its longest edge, where
  // there is room -- and every edge of a rare predicate is labelled.
  const perPred = {};
  for (const e of edges) (perPred[e.predicate] ||= []).push(e);
  const labelled = new Set();
  for (const [, group] of Object.entries(perPred)) {
    if (group.length <= 4 && edges.length <= 26) { group.forEach(e => labelled.add(e.claim_id)); continue; }
    const len = (e) => Math.hypot(e.b.x - e.a.x, e.b.y - e.a.y);
    labelled.add(group.slice().sort((x, y) => len(y) - len(x))[0].claim_id);
  }
  const tip = $("#graph-tip");

  for (const e of edges) {
    const path = `M ${e.a.x} ${e.a.y} L ${e.b.x} ${e.b.y}`;
    const line = s("path", { class: "edge-line", d: path, "stroke-dasharray": DASH[e.predicate],
                             "marker-end": "url(#arrow)" });
    if (state.selectedEdge === e.claim_id) line.classList.add("sel");
    const hit = s("path", {
      class: "edge-hit", d: path,
      onclick: () => showEdge(e.claim_id),
      onmousemove: (ev) => moveTip(ev, `${e.predicate}\n${e.subject} → ${e.object ?? "?"}` +
        (e.resolved ? "" : "  (unresolved)")),
      onmouseleave: () => tip.classList.add("hidden"),
    });
    g.append(line, hit);
    if (labelled.has(e.claim_id)) {
      // 70% of the way out, not the midpoint: in a hub layout every midpoint
      // lands on top of the hub
      const t = 0.68;
      g.append(s("text", { class: "edge-label", x: e.a.x + (e.b.x - e.a.x) * t,
                           y: e.a.y + (e.b.y - e.a.y) * t - 4,
                           "text-anchor": "middle", text: e.predicate }));
    }
  }

  for (const n of nodes) {
    const r = n.depth === 0 ? 10 : n.real ? 7 : 5.5;
    const isRoot = n.depth === 0;
    const shape = n.real
      ? s("circle", { class: "node-dot" + (isRoot ? " root" : ""), cx: n.x, cy: n.y, r,
                      fill: SEQ[n.depth] })
      : s("rect", { class: "node-dot", x: n.x - r, y: n.y - r, width: r * 2, height: r * 2,
                    rx: 1.5, fill: "var(--surface)", stroke: "var(--edge)" });
    const hit = s("circle", {
      class: "node-hit", cx: n.x, cy: n.y, r: Math.max(r + 6, 12), fill: "transparent",
      onclick: () => { if (n.real) showNode(n.id); },
      ondblclick: () => { if (n.real) loadNeighbourhood(n.id); },
      onmousemove: (ev) => moveTip(ev, n.real ? `${n.q}\n${n.kind} · ${n.path}\nhop ${n.depth}` +
                                                 (n.id === state.root ? "" : "\ndouble-click to re-centre")
                                              : `${n.label}\nnot resolved to a symbol in this corpus`),
      onmouseleave: () => tip.classList.add("hidden"),
    });
    g.append(shape, hit);
    if (labelNodes || isRoot) {
      // labels radiate outward from the centre, so a ring of them fans out
      // instead of stacking
      const cosA = Math.cos(n.ang ?? 0), out = r + 7;
      const attrs = isRoot
        ? { x: n.x, y: n.y - r - 8, "text-anchor": "middle" }
        : { x: n.x + cosA * out, y: n.y + Math.sin(n.ang) * out + 3.5,
            "text-anchor": cosA >= 0 ? "start" : "end" };
      g.append(s("text", Object.assign({ class: "node-label" + (isRoot ? " root" : ""),
                 text: n.label.length > 26 ? n.label.slice(0, 25) + "…" : n.label }, attrs)));
    }
  }

  wirePanZoom(svg, g);
  renderLegend(edges, nodes);
  const note = [`${nodes.length} nodes, ${edges.length} edges, ${state.hops} hop${state.hops > 1 ? "s" : ""}`];
  if (nbh.truncated) note.push(`truncated at ${nbh.limit} nodes — narrow the predicates or use 1 hop`);
  if (!labelNodes) note.push("labels are on hover at this density");
  $("#graph-note").textContent = note.join(" · ");
}

function moveTip(ev, text) {
  const tip = $("#graph-tip"), frame = ev.currentTarget.ownerSVGElement.parentElement;
  const r = frame.getBoundingClientRect();
  tip.textContent = text;
  tip.style.left = Math.min(ev.clientX - r.left + 12, r.width - 260) + "px";
  tip.style.top = (ev.clientY - r.top + 12) + "px";
  tip.classList.remove("hidden");
}

function wirePanZoom(svg, g) {
  let drag = null;
  svg.addEventListener("mousedown", (e) => { if (e.target === svg || e.target.tagName === "g") drag = { x: e.clientX, y: e.clientY, tx: state.view.tx, ty: state.view.ty }; });
  window.addEventListener("mouseup", () => { drag = null; });
  svg.addEventListener("mousemove", (e) => {
    if (!drag) return;
    state.view.tx = drag.tx + (e.clientX - drag.x);
    state.view.ty = drag.ty + (e.clientY - drag.y);
    g.setAttribute("transform", `translate(${state.view.tx},${state.view.ty}) scale(${state.view.k})`);
  });
  svg.addEventListener("wheel", (e) => {
    e.preventDefault();
    state.view.k = Math.min(3, Math.max(0.4, state.view.k * (e.deltaY < 0 ? 1.1 : 0.9)));
    g.setAttribute("transform", `translate(${state.view.tx},${state.view.ty}) scale(${state.view.k})`);
  }, { passive: false });
}

function renderLegend(edges, nodes) {
  const box = clear($("#graph-legend"));
  const present = [...new Set(edges.map(e => e.predicate))].sort();
  for (const p of present) {
    box.append(h("span", { class: "item" },
      svgSample(s("path", { d: "M 2 6 L 34 6", stroke: "var(--edge)", "stroke-width": 2,
                            "stroke-dasharray": DASH[p], fill: "none" }), 36, 12),
      h("span", { class: "mono", text: p })));
  }
  const depths = [...new Set(nodes.filter(n => n.real).map(n => n.depth))].sort();
  for (const d of depths) {
    box.append(h("span", { class: "item" },
      svgSample(s("circle", { cx: 7, cy: 6, r: d === 0 ? 6 : 5, fill: SEQ[d],
                              stroke: "var(--raised)", "stroke-width": 2 }), 15, 12),
      h("span", { text: d === 0 ? "the symbol you opened" : `${d} hop${d > 1 ? "s" : ""} away` })));
  }
  if (nodes.some(n => !n.real)) {
    box.append(h("span", { class: "item" },
      svgSample(s("rect", { x: 2, y: 1, width: 10, height: 10, rx: 1.5, fill: "var(--surface)",
                            stroke: "var(--edge)", "stroke-width": 2 }), 15, 12),
      h("span", { text: "literal or unresolved name" })));
  }
}
function svgSample(child, w, hgt) {
  const el = s("svg", { width: w, height: hgt, viewBox: `0 0 ${w} ${hgt}` }, child);
  return el;
}

/* ── inspectors ───────────────────────────────────────────────── */
async function showNode(symbolId) {
  state.selectedNode = symbolId;
  const n = await api(`/api/workspaces/${state.wid}/node/${symbolId}`);
  const box = clear($("#inspector"));
  box.append(h("h3", { class: "mono", text: n.qualified_name }));
  const kv = h("dl", { class: "kv" });
  const row = (k, v, mono) => { kv.append(h("dt", { text: k }), h("dd", { class: mono ? "mono" : "", text: v })); };
  row("kind", n.kind);
  row("file", n.rel_path, true);
  row("location", locText(n.locator_kind, n.locator), true);
  row("file status", n.parse_status);
  row("symbol id", n.symbol_id, true);
  box.append(kv);
  if (n.docstring) box.append(h("p", { class: "muted small", text: n.docstring.slice(0, 400) }));
  const deg = Object.entries(n.neighbours || {});
  if (deg.length) {
    box.append(h("p", { class: "section-h", text: "incident claims" }));
    box.append(h("table", { class: "grid" }, h("tbody", {}, ...deg.map(([p, c]) =>
      h("tr", {}, h("td", {}, h("code", { text: p })), h("td", { text: fmt(c) }))))));
  }
  box.append(h("p", {},
    h("button", { class: "tiny", text: "Centre the graph here", onclick: () => loadNeighbourhood(symbolId) }),
    " ",
    h("button", { class: "tiny", text: "View source", onclick: () => viewSymbolSource(symbolId, box) })));
}

function locText(kind, loc) {
  if (kind === "byte_range")
    return (loc.line_start ? `line ${loc.line_start}, ` : "") + `bytes ${loc.byte_start}–${loc.byte_end}`;
  if (kind === "pdf_box") return loc.page ? `page ${loc.page}` : "whole document";
  if (kind === "docx_para") return `${loc.unit || "paragraph"} ${loc.para}`;
  return kind;
}

async function viewSymbolSource(symbolId, box) {
  const r = await api(`/api/workspaces/${state.wid}/symbol_source/${symbolId}`);
  const old = box.querySelector(".source"); if (old) old.remove();
  box.append(sourceView(r.context, r.rel_path));
}

async function showEdge(claimId) {
  state.selectedEdge = claimId;
  if (state.nbh) drawGraph(state.nbh);
  const e = await api(`/api/workspaces/${state.wid}/edge/${claimId}`);
  const box = clear($("#inspector"));
  box.append(h("h3", {}, h("code", { text: e.predicate })));
  box.append(h("p", { class: "mono small", text: `${e.subj} → ${e.obj ?? e.object_literal ?? "?"}` }));
  const kv = h("dl", { class: "kv" });
  const row = (k, v, mono) => { kv.append(h("dt", { text: k }), h("dd", { class: mono ? "mono" : "", text: v })); };
  row("establishment", e.establishment);
  row("lifecycle", e.lifecycle);
  row("produced by", `${e.extractor_id} ${e.extractor_version}`);
  row("model", e.model_id || "none — no model wrote this claim");
  row("claim id", e.claim_id, true);
  if (e.resolution) {
    row("reference", e.resolution.to_name, true);
    row("resolution", e.resolution.resolution + (e.resolution.reason ? ` (${e.resolution.reason})` : ""));
  }
  box.append(kv);
  box.append(h("p", { class: "section-h", text: "evidence" }));
  if (!e.evidence.length) box.append(h("p", { class: "muted small", text: "none recorded" }));
  for (const ev of e.evidence) if (ev) box.append(evidenceBlock(ev));
}

function evidenceBlock(ev) {
  const wrap = h("div", {});
  wrap.append(h("div", { class: "mono small" }, h("b", { text: ev.rel_path }), " — ", ev.location));
  wrap.append(h("div", { class: "muted small", text:
    `${ev.verification_strength} · ${ev.verifier_engine} · sha256 ${String(ev.artifact_sha256).slice(0, 12)}` }));
  wrap.append(h("div", { class: "evline" },
    h("button", { class: "tiny", text: "Show in source", onclick: async (e) => {
      e.target.disabled = true;
      const full = await api(`/api/workspaces/${state.wid}/evidence/${ev.evidence_id}`);
      const old = wrap.querySelector(".source"); if (old) old.remove();
      wrap.append(sourceView(full.context, full.rel_path));
      e.target.disabled = false;
    } })));
  return wrap;
}

/* The source viewer. Line numbers appear only for formats that have them. */
function sourceView(ctx, relPath) {
  const box = h("div", { class: "source" });
  if (!ctx) { box.append(h("div", { class: "plain", text: "no source available" })); return box; }
  if (ctx.kind !== "byte_range") {
    box.append(h("div", { class: "plain", text: ctx.text || "(empty)" }));
    box.append(h("div", { class: "muted small", style: "padding:.3rem .7rem",
                          text: ctx.note || "" }));
    return box;
  }
  const tbody = h("tbody", {});
  ctx.lines.forEach((line, i) => {
    const num = ctx.first_line + i;
    const hl = num >= ctx.highlight_from && num <= ctx.highlight_to;
    tbody.append(h("tr", { class: hl ? "hl" : "" },
      h("td", { class: "ln", text: String(num) }),
      h("td", { class: "src", text: line })));
  });
  box.append(h("table", {}, tbody));
  if (relPath) box.title = relPath;
  return box;
}

/* ── ask ──────────────────────────────────────────────────────── */
function renderAskHint() {
  api("/api/provider").then(p => {
    renderProvider(p);
    $("#ask-hint").textContent = p.configured
      ? "The model proposes; the deterministic gate decides whether anything is shown. " +
        "Every statement must cite evidence that still matches the source bytes."
      : "No model is configured, so no semantic answer will be attempted. The deterministic " +
        "evidence and structural facts below are produced without one.";
  });
}

function wireAsk() {
  $("#ask-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#question").value.trim() || $("#question").placeholder;
    const box = clear($("#answer"));
    box.append(h("p", { class: "muted" }, h("span", { class: "spinner" }), " retrieving and checking…"));
    let r;
    try {
      r = await api(`/api/workspaces/${state.wid}/ask`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      });
    } catch (err) {
      clear(box).append(banner("critical", "error", err.message));
      return;
    }
    renderAnswer(clear(box), r);
  });
}

function banner(kind, label, text) {
  return h("p", { class: "banner " + kind }, h("span", { class: "label", text: label }), text);
}

function renderAnswer(box, r) {
  if (r.status === "ANSWER") {
    box.append(banner("good", "answer · proposed",
      "A model wrote this and the deterministic gate accepted it. Accepted means grounded — " +
      "every statement cites evidence that exists, was retrieved for this question, and still " +
      "matches the source bytes. It does not mean the statement is true."));
    box.append(h("p", { class: "answer-text", text: r.answer }));
  } else if (r.status === "ABSTAIN_AMBIGUOUS") {
    box.append(banner("warning", "abstained · ambiguous", r.abstain_reason));
    if (r.identity && r.identity.ambiguous && r.identity.ambiguous.length) {
      const a = r.identity.ambiguous[0];
      box.append(h("ul", {}, ...a.candidates.map(c => h("li", {}, h("code", { text: c })))));
    }
  } else {
    box.append(banner("warning", "abstained", r.abstain_reason ||
      "nothing could be shown for this question"));
  }

  const vclaims = (r.validation && r.validation.claims) || [];
  if (vclaims.length) {
    box.append(h("p", { class: "section-h", text: "statements and the evidence each one cites" }));
    for (const c of vclaims) {
      const st = h("div", { class: "statement " + (c.ok ? "ok" : "bad") },
        h("div", { class: "t", text: c.text }));
      for (const ev of c.evidence || []) {
        st.append(h("div", { class: "evline" },
          h("code", { text: String(ev.evidence_id).slice(0, 12) }), " ",
          ev.rel_path || "(unknown file)",
          ev.byte_start !== null && ev.byte_start !== undefined
            ? ` bytes ${ev.byte_start}–${ev.byte_end}` : "",
          " — ", ev.ok ? "re-read and matched" : (ev.error || "did not check out"), " ",
          h("button", { class: "tiny", text: "source", onclick: async (e) => {
            e.target.disabled = true;
            const full = await api(`/api/workspaces/${state.wid}/evidence/${ev.evidence_id}`);
            const old = st.querySelector(".source"); if (old) old.remove();
            st.append(sourceView(full.context, full.rel_path));
            e.target.disabled = false;
          } })));
      }
      for (const sc of c.structural || []) {
        st.append(h("div", { class: "evline" },
          h("span", { class: "tag " + (sc.status === "SUPPORTED" ? "good"
                      : sc.status === "EXPLICIT_CONTRADICTION" ? "critical" : ""), text: sc.status }),
          " ", h("code", { text: `${sc.predicate}(${sc.subject}, ${sc.object})` }),
          sc.detail ? " — " + sc.detail : ""));
      }
      for (const p of c.problems || []) st.append(h("div", { class: "evline", text: "problem: " + p }));
      box.append(st);
    }
  }

  if (r.structural_facts && r.structural_facts.length) {
    box.append(h("p", { class: "section-h", text: "structural facts (DERIVED — a parser wrote these, not a model)" }));
    box.append(h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ...["Claim", "Resolved", "Where", "Evidence"].map(t => h("th", { text: t })))),
      h("tbody", {}, ...r.structural_facts.slice(0, 40).map(f => h("tr", {},
        h("td", {}, h("code", { text: `${f.predicate}(${f.subject}, ${f.object})` })),
        h("td", {}, h("span", { class: "tag " + (f.resolved ? "good" : "warning"),
                                text: f.resolved ? "resolved" : "unresolved" })),
        h("td", {}, h("code", { class: "muted", text: `${f.rel_path}:${f.byte_start}` })),
        h("td", {}, h("button", { class: "tiny", text: "show",
          onclick: () => { setTab("graph"); showEdge(f.claim_id); } })))))));
  }

  if (r.evidence && r.evidence.length) {
    box.append(h("p", { class: "section-h", text: "evidence retrieved deterministically for this question" }));
    for (const sp of r.evidence) {
      const wrap = h("div", { class: "evline" },
        h("code", { text: String(sp.evidence_id).slice(0, 12) }), " ",
        h("b", { text: sp.rel_path }), sp.line_start ? `:${sp.line_start}` : "",
        " — ", sp.symbol || "", ` (${sp.kind}, ${sp.chars}B, found by ${sp.how})`, " ",
        h("button", { class: "tiny", text: "source", onclick: async (e) => {
          e.target.disabled = true;
          const full = await api(`/api/workspaces/${state.wid}/evidence/${sp.evidence_id}`);
          const old = wrap.querySelector(".source"); if (old) old.remove();
          wrap.append(sourceView(full.context, full.rel_path));
          e.target.disabled = false;
        } }));
      box.append(wrap);
    }
  }

  const v = r.validation || {};
  box.append(h("p", { class: "section-h", text: "how this was decided" }));
  const kv = h("dl", { class: "kv" });
  const row = (k, val) => { kv.append(h("dt", { text: k }), h("dd", { text: String(val) })); };
  row("gate outcome", v.outcome || "not reached");
  if (v.reason) row("reason", v.reason);
  row("structural check", r.structural_status || "not applicable");
  row("subject identity", (r.subjects || []).map(x => x.qualified_name).join(", ") || "none resolved");
  row("interpretation", (r.intent && r.intent.source) || "deterministic");
  row("attempts", `${r.attempts} (regenerations: ${r.regenerations})`);
  row("latency", r.latency_seconds + " s");
  if (r.usage && r.usage.total_tokens) row("tokens", fmt(r.usage.total_tokens));
  box.append(kv);
  box.append(h("p", { class: "muted small", text:
    "The gate checks grounding, not entailment. It cannot prove the cited bytes support the " +
    "sentence; an answer that cites the right function and describes it wrongly would pass." }));
}

/* ── boot ─────────────────────────────────────────────────────── */
function route() {
  const m = location.hash.match(/^#\/w\/([a-z0-9]+)$/i);
  if (m) openWorkspace(m[1]).catch(() => loadHome());
  else loadHome();
}

wireUpload();
wireSearch();
wireAsk();
for (const t of document.querySelectorAll(".tab")) t.addEventListener("click", () => setTab(t.dataset.tab));
$("#home-link").addEventListener("click", (e) => { e.preventDefault(); location.hash = ""; loadHome(); });
let resizeTimer = null;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => { if (state.nbh && !$("#tab-graph").classList.contains("hidden")) drawGraph(state.nbh); }, 150);
});
window.addEventListener("hashchange", route);
route();
