// Offline vanilla ES2022 operator dashboard (ADR-0007, zero external CDN dependencies).
const MAX_DOM_EVENTS = 2000;

const state = {
  mentalMap: null,
  keyPool: [],
  skillsCount: 0,
  lastSeq: 0,
  selectedNodeId: null,
  ws: null,
  reconnectAttempt: 0,
};

const STATUS_COLORS = {
  DONE: "#2ea043",
  IN_PROGRESS: "#1f6feb",
  VERIFYING: "#8957e5",
  LEASED: "#388bfd",
  READY: "#3fb950",
  NEEDS_REWORK: "#d29922",
  BLOCKED_HUMAN: "#f85149",
  FAILED: "#da3633",
  PENDING_DEPS: "#484f58",
  DRAFT: "#30363d",
};

const el = (id) => document.getElementById(id);

function appendStreamLine(kind, text) {
  const container = el("live-stream");
  const row = document.createElement("div");
  row.className = `log-entry ${kind === "thinking" ? "log-thinking" : "log-rpc"}`;
  const ts = new Date().toLocaleTimeString("ru-RU");
  row.textContent = `[${ts}] ${text}`;
  container.appendChild(row);
  while (container.childElementCount > MAX_DOM_EVENTS) {
    container.removeChild(container.firstElementChild);
  }
  container.scrollTop = container.scrollHeight;
}

function computeNodeLayers(nodesMap) {
  const ids = Object.keys(nodesMap).sort();
  const depth = {};
  const visit = (id, stack = new Set()) => {
    if (depth[id] !== undefined) return depth[id];
    if (stack.has(id)) return 0;
    stack.add(id);
    const deps = nodesMap[id]?.dependencies || [];
    let d = 0;
    for (const dep of deps) {
      if (nodesMap[dep]) d = Math.max(d, 1 + visit(dep, stack));
    }
    depth[id] = d;
    return d;
  };
  ids.forEach((id) => visit(id));
  const layers = {};
  for (const id of ids) {
    const l = depth[id] || 0;
    (layers[l] ||= []).push(id);
  }
  return layers;
}

function renderDagSvg() {
  const svg = el("dag-svg");
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const mm = state.mentalMap;
  if (!mm || !mm.nodes) return;

  const layers = computeNodeLayers(mm.nodes);
  const layerKeys = Object.keys(layers).map(Number).sort((a, b) => a - b);
  const coords = {};
  const width = Math.max(900, layerKeys.length * 220);
  let maxRows = 1;
  layerKeys.forEach((k) => { maxRows = Math.max(maxRows, layers[k].length); });
  const height = Math.max(360, maxRows * 95);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);

  layerKeys.forEach((layerIdx, col) => {
    const colIds = layers[layerIdx];
    colIds.forEach((nid, row) => {
      coords[nid] = {
        x: 90 + col * 210,
        y: 55 + row * 85,
      };
    });
  });

  const ns = "http://www.w3.org/2000/svg";
  for (const [nid, node] of Object.entries(mm.nodes)) {
    for (const dep of node.dependencies || []) {
      if (!coords[dep] || !coords[nid]) continue;
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", String(coords[dep].x + 70));
      line.setAttribute("y1", String(coords[dep].y));
      line.setAttribute("x2", String(coords[nid].x - 70));
      line.setAttribute("y2", String(coords[nid].y));
      line.setAttribute("stroke", "#6e7681");
      line.setAttribute("stroke-width", "1.6");
      svg.appendChild(line);
    }
  }

  for (const [nid, node] of Object.entries(mm.nodes)) {
    const pt = coords[nid];
    const g = document.createElementNS(ns, "g");
    g.style.cursor = "pointer";
    g.addEventListener("click", () => {
      state.selectedNodeId = nid;
      renderInspector();
    });

    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", String(pt.x - 70));
    rect.setAttribute("y", String(pt.y - 24));
    rect.setAttribute("width", "140");
    rect.setAttribute("height", "48");
    rect.setAttribute("rx", "7");
    rect.setAttribute("fill", STATUS_COLORS[node.status] || "#30363d");
    rect.setAttribute("stroke", state.selectedNodeId === nid ? "#ffffff" : "#0d1117");
    rect.setAttribute("stroke-width", "2");
    g.appendChild(rect);

    const label = document.createElementNS(ns, "text");
    label.setAttribute("x", String(pt.x));
    label.setAttribute("y", String(pt.y - 4));
    label.setAttribute("text-anchor", "middle");
    label.setAttribute("fill", "#ffffff");
    label.setAttribute("font-size", "11");
    label.setAttribute("font-weight", "600");
    label.textContent = `${nid}: ${node.title.slice(0, 14)}`;
    g.appendChild(label);

    const sub = document.createElementNS(ns, "text");
    sub.setAttribute("x", String(pt.x));
    sub.setAttribute("y", String(pt.y + 13));
    sub.setAttribute("text-anchor", "middle");
    sub.setAttribute("fill", "#e6edf3");
    sub.setAttribute("font-size", "9.5");
    sub.textContent = node.status;
    g.appendChild(sub);

    svg.appendChild(g);
  }
}

function renderInspector() {
  const box = el("node-inspector");
  const mm = state.mentalMap;
  if (!mm || !state.selectedNodeId || !mm.nodes[state.selectedNodeId]) return;
  const n = mm.nodes[state.selectedNodeId];
  const defects = (n.rework_defects || []).join("; ") || "нет";
  const files = (n.owned_files || []).join(", ") || "нет";
  box.textContent =
    `Узел: ${n.id} (${n.title})\n` +
    `Статус: ${n.status} | Роль: ${n.owner_role} | Агент: ${n.assigned_agent || "—"}\n` +
    `Попыток: ${n.retry_count} | Токены: ${n.tokens_used}/${n.token_budget}\n` +
    `Файлы: ${files}\n` +
    `Дефекты: ${defects}`;
  box.style.whiteSpace = "pre-line";
}

function renderQuestionsAndKeys() {
  const qBox = el("questions-box");
  qBox.textContent = "";
  const qs = Object.values(state.mentalMap?.open_questions || {}).filter(
    (q) => q.status === "OPEN"
  );
  if (qs.length === 0) {
    qBox.textContent = "Открытых вопросов к оператору нет.";
  } else {
    qs.forEach((q) => {
      const wrap = document.createElement("div");
      const title = document.createElement("div");
      title.textContent = `[${q.id}] ${q.question_ru}`;
      wrap.appendChild(title);
      (q.options_ru || []).forEach((opt) => {
        const btn = document.createElement("button");
        btn.className = "opt-btn";
        btn.textContent = opt;
        btn.addEventListener("click", async () => {
          await fetch(`/api/v1/questions/${encodeURIComponent(q.id)}/answer`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ answer: opt }),
          });
        });
        wrap.appendChild(btn);
      });
      qBox.appendChild(wrap);
    });
  }

  const kBox = el("keys-box");
  if (!state.keyPool.length) {
    kBox.textContent = "Пул ключей: mock / 0 ключей.";
  } else {
    kBox.textContent = state.keyPool
      .map((k) => `${k.key_id}: ${k.status} (req=${k.total_requests}, 429=${k.total_errors_429})`)
      .join(" | ");
  }
}

function renderHeader() {
  const mm = state.mentalMap;
  if (!mm) return;
  el("project-badge").textContent = `проект: ${mm.project_id}`;
  el("stage-badge").textContent = `стадия: ${mm.stage}`;
  el("map-version").textContent = `v${mm.version}`;
  const nCount = Object.keys(mm.nodes || {}).length;
  el("summary-counters").textContent =
    `Узлы: ${nCount} | Ключи: ${state.keyPool.length} | Скиллы: ${state.skillsCount}`;
}

function handleFrame(frame) {
  if (frame.seq && frame.seq > state.lastSeq) state.lastSeq = frame.seq;
  if (frame.type === "snapshot.init") {
    state.mentalMap = frame.payload.mental_map;
    state.keyPool = frame.payload.key_pool || [];
    state.skillsCount = frame.payload.skills_count || 0;
    renderHeader();
    renderDagSvg();
    renderInspector();
    renderQuestionsAndKeys();
  } else if (frame.type === "map.updated") {
    state.mentalMap = frame.payload.mental_map;
    renderHeader();
    renderDagSvg();
    renderInspector();
    renderQuestionsAndKeys();
  } else if (frame.type === "rpc.message") {
    const p = frame.payload;
    appendStreamLine("rpc", `RPC ${p.sender_id} -> ${p.recipient_id} [${p.msg_type}]`);
  } else if (frame.type === "agent.thinking.delta") {
    appendStreamLine("thinking", `THINK [${frame.payload.agent_id}]: ${frame.payload.text}`);
  }
}

function connectWebSocket() {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsUrl = `${proto}//${window.location.host}/ws?since_seq=${state.lastSeq}`;
  const pill = el("ws-status");
  pill.className = "ws-pill ws-connecting";
  pill.textContent = "WS: подключение…";

  const ws = new WebSocket(wsUrl);
  state.ws = ws;
  ws.addEventListener("open", () => {
    state.reconnectAttempt = 0;
    pill.className = "ws-pill ws-connected";
    pill.textContent = "WS: онлайн";
  });
  ws.addEventListener("message", (ev) => {
    try {
      handleFrame(JSON.parse(ev.data));
    } catch {
      // Ignore malformed frame
    }
  });
  ws.addEventListener("close", () => {
    pill.className = "ws-pill ws-disconnected";
    pill.textContent = "WS: переподключение…";
    const delay = Math.min(10000, 500 * 2 ** state.reconnectAttempt++);
    setTimeout(connectWebSocket, delay);
  });
}

el("clear-stream-btn").addEventListener("click", () => {
  el("live-stream").textContent = "";
});

connectWebSocket();
