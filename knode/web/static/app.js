/* ── KNode Graph Browser — Frontend (Sigma.js + Graphology) ── */
import Graph from 'https://esm.sh/graphology';
import forceAtlas2 from 'https://esm.sh/graphology-layout-forceatlas2';
import FA2Layout from 'https://esm.sh/graphology-layout-forceatlas2/worker';
import noverlap from 'https://esm.sh/graphology-layout-noverlap';
import Sigma from 'https://esm.sh/sigma';

// ── Configuration ─────────────────────────────────────────────────────────────
const NODE_COLORS = {
  Class:         "#6c63ff",
  AbstractClass: "#a78bfa",
  Interface:     "#00d4aa",
  Enum:          "#fb923c",
  Object:        "#38bdf8",
  Method:        "#f472b6",
  Function:      "#e879f9",
  Field:         "#4ade80",
  Property:      "#34d399",
  Package:       "#facc15",
  Project:       "#ef4444",
  Module:        "#f97316",
  Folder:        "#eab308",
  File:          "#3b82f6"
};

const EDGE_COLORS = {
  CONTAINS:      { color: '#2d5a3d', sizeMultiplier: 0.4 },
  DEFINES:       { color: '#0e7490', sizeMultiplier: 0.5 },
  IMPORTS:       { color: '#1d4ed8', sizeMultiplier: 0.6 },
  CALLS:         { color: '#7c3aed', sizeMultiplier: 0.8 },
  EXTENDS:       { color: '#c2410c', sizeMultiplier: 1.0 },
  IMPLEMENTS:    { color: '#be185d', sizeMultiplier: 0.9 },
  READS:         { color: '#10b981', sizeMultiplier: 0.7 },
  OVERRIDES:     { color: '#fb923c', sizeMultiplier: 0.9 },
  INSTANTIATES:  { color: '#38bdf8', sizeMultiplier: 0.8 },
  USES:          { color: '#94a3b8', sizeMultiplier: 0.5 }
};

function nodeColor(type) { return NODE_COLORS[type] || "#94a3b8"; }
function edgeStyle(type) { return EDGE_COLORS[type] || { color: '#4a4a5a', sizeMultiplier: 0.5 }; }

// ── State ─────────────────────────────────────────────────────────────────────
let graph = null;
let sigmaInst = null;
let fa2Layout = null;
let allData = { nodes: [], edges: [] };
let activeTypes = new Set(Object.keys(NODE_COLORS));
let activeEdgeTypes = new Set(Object.keys(EDGE_COLORS));
let statsData = {};
let selectedNodeId = null;
let hoveredNodeId = null;
let isLayoutRunning = false;
let layoutTimeout = null;

// ── Initialization ────────────────────────────────────────────────────────────
function initSigma() {
  const container = document.getElementById("sigma-container");
  
  graph = new Graph({ multi: true });
  
  sigmaInst = new Sigma(graph, container, {
    renderLabels: true,
    labelFont: 'JetBrains Mono, monospace',
    labelSize: 11,
    labelWeight: '500',
    labelColor: { color: '#e4e4ed' },
    labelRenderedSizeThreshold: 8,
    labelDensity: 0.1,
    labelGridCellSize: 70,
    defaultNodeColor: '#6b7280',
    defaultEdgeColor: '#2a2a3a',
    defaultEdgeType: 'line',
    minCameraRatio: 0.002,
    maxCameraRatio: 50,
    hideEdgesOnMove: true,
    zIndex: true,
    
    // Custom node hover
    defaultDrawNodeHover: (context, data, settings) => {
      const label = data.label;
      if (!label) return;
      const size = settings.labelSize || 11;
      context.font = `${settings.labelWeight || '500'} ${size}px ${settings.labelFont || 'sans-serif'}`;
      const textWidth = context.measureText(label).width;
      const nodeSize = data.size || 8;
      const x = data.x, y = data.y - nodeSize - 10;
      const paddingX = 8, paddingY = 5;
      const height = size + paddingY * 2, width = textWidth + paddingX * 2, radius = 4;
      
      context.fillStyle = '#12121c';
      context.beginPath();
      context.roundRect(x - width/2, y - height/2, width, height, radius);
      context.fill();
      
      context.strokeStyle = data.color || '#6366f1';
      context.lineWidth = 2;
      context.stroke();
      
      context.fillStyle = '#f5f5f7';
      context.textAlign = 'center';
      context.textBaseline = 'middle';
      context.fillText(label, x, y);
      
      context.beginPath();
      context.arc(data.x, data.y, nodeSize + 4, 0, Math.PI * 2);
      context.strokeStyle = data.color || '#6366f1';
      context.lineWidth = 2;
      context.globalAlpha = 0.5;
      context.stroke();
      context.globalAlpha = 1;
    },
    
    nodeReducer: (node, data) => {
      const res = { ...data };
      if (res.hidden) return res;
      
      if (selectedNodeId) {
        if (node === selectedNodeId) {
          res.size = (data.originalSize || 8) * 1.8;
          res.zIndex = 2;
          res.highlighted = true;
        } else if (graph.hasEdge(node, selectedNodeId) || graph.hasEdge(selectedNodeId, node)) {
          res.size = (data.originalSize || 8) * 1.3;
          res.zIndex = 1;
        } else {
          res.color = dimColor(data.color, 0.15);
          res.size = (data.originalSize || 8) * 0.5;
          res.zIndex = 0;
        }
      }
      
      return res;
    },
    
    edgeReducer: (edge, data) => {
      const res = { ...data };
      if (res.hidden) return res;
      
      if (selectedNodeId) {
        const [source, target] = graph.extremities(edge);
        if (source === selectedNodeId || target === selectedNodeId) {
          res.color = brightenColor(data.color, 1.5);
          res.size = Math.max(3, (data.originalSize || 1) * 4);
          res.zIndex = 2;
        } else {
          res.color = dimColor(data.color, 0.1);
          res.size = 0.3;
          res.zIndex = 0;
        }
      }
      return res;
    }
  });

  sigmaInst.on("clickNode", (e) => {
    focusNode(e.node);
  });
  
  sigmaInst.on("clickStage", () => {
    clearSelection();
  });
  
  sigmaInst.on("enterNode", (e) => {
    hoveredNodeId = e.node;
    document.getElementById("sigma-container").style.cursor = 'pointer';
    showHoverTooltip(e.node, sigmaInst.getNodeDisplayData(e.node));
  });
  
  sigmaInst.on("leaveNode", () => {
    hoveredNodeId = null;
    document.getElementById("sigma-container").style.cursor = 'grab';
    hideHoverTooltip();
  });
}

function dimColor(hex, amount) {
  return "#334155"; // Simplistic dim color
}
function brightenColor(hex, factor) {
  return "#ffffff";
}

// ── Graph logic ───────────────────────────────────────────────────────────────
async function loadGraph() {
  showLoading(true);
  try {
    const types = [...activeTypes].join(",");
    const resp = await fetch(`/api/graph?node_types=${encodeURIComponent(types)}&max_nodes=100000`);
    allData = await resp.json();
    
    if (!graph) initSigma();
    buildGraphology(allData);
    
    const statsResp = await fetch("/api/stats");
    statsData = await statsResp.json();
    updateStatsBar(statsData);
    updateProjectName(statsData);
    buildFilterList(statsData);
    buildEdgeLegend();
    
  } catch (e) {
    showToast("Failed to load graph: " + e.message, 4000);
  } finally {
    showLoading(false);
  }
}

function buildGraphology(data) {
  graph.clear();
  
  const nodeCount = data.nodes.length;
  const parentToChildren = new Map();
  const childToParent = new Map();
  const hierarchyRelations = new Set(['CONTAINS', 'DEFINES', 'IMPORTS']);
  
  data.edges.forEach(e => {
    if (hierarchyRelations.has(e.type)) {
      if (!parentToChildren.has(e.source_id)) parentToChildren.set(e.source_id, []);
      parentToChildren.get(e.source_id).push(e.target_id);
      childToParent.set(e.target_id, e.source_id);
    }
  });

  const structuralTypes = new Set(['Project', 'Package', 'Module', 'Folder']);
  const structuralNodes = data.nodes.filter(n => structuralTypes.has(n.type));
  const structuralSpread = Math.sqrt(nodeCount) * 40;
  const childJitter = Math.sqrt(nodeCount) * 3;
  
  const nodePositions = new Map();
  
  structuralNodes.forEach((node, idx) => {
    const goldenAngle = Math.PI * (3 - Math.sqrt(5));
    const angle = idx * goldenAngle;
    const radius = structuralSpread * Math.sqrt((idx + 1) / Math.max(structuralNodes.length, 1));
    const jitter = structuralSpread * 0.15;
    const x = radius * Math.cos(angle) + (Math.random() - 0.5) * jitter;
    const y = radius * Math.sin(angle) + (Math.random() - 0.5) * jitter;
    nodePositions.set(node.id, { x, y });
  });

  const addNodeWithPos = (nodeData) => {
    if (graph.hasNode(nodeData.id)) return;
    let x, y;
    const parentId = childToParent.get(nodeData.id);
    const parentPos = parentId ? nodePositions.get(parentId) : null;
    
    if (parentPos) {
      x = parentPos.x + (Math.random() - 0.5) * childJitter;
      y = parentPos.y + (Math.random() - 0.5) * childJitter;
    } else {
      x = (Math.random() - 0.5) * structuralSpread * 0.5;
      y = (Math.random() - 0.5) * structuralSpread * 0.5;
    }
    
    // Default fallback position
    if(nodePositions.has(nodeData.id)) {
        x = nodePositions.get(nodeData.id).x;
        y = nodePositions.get(nodeData.id).y;
    } else {
        nodePositions.set(nodeData.id, { x, y });
    }

    const baseSize = 8;
    const scaledSize = nodeCount > 5000 ? Math.max(2, baseSize * 0.6) : baseSize;
    
    graph.addNode(nodeData.id, {
      ...nodeData,
      x, y,
      size: scaledSize,
      originalSize: scaledSize,
      color: nodeColor(nodeData.type),
      label: nodeData.name,
      nodeType: nodeData.type,
      type: 'circle'
    });
  };
  
  const nodeMap = new Map(data.nodes.map(n => [n.id, n]));
  const queue = [...structuralNodes.map(n => n.id)];
  const visited = new Set(queue);
  
  while (queue.length > 0) {
    const curr = queue.shift();
    const children = parentToChildren.get(curr) || [];
    for (const childId of children) {
      if (!visited.has(childId)) {
        visited.add(childId);
        const childNode = nodeMap.get(childId);
        if (childNode) addNodeWithPos(childNode);
        queue.push(childId);
      }
    }
  }
  
  data.nodes.forEach(n => {
    if (!graph.hasNode(n.id)) addNodeWithPos(n);
  });
  
  const edgeBaseSize = nodeCount > 5000 ? 0.6 : 1.0;
  
  data.edges.forEach(e => {
    if (graph.hasNode(e.source_id) && graph.hasNode(e.target_id)) {
      const style = edgeStyle(e.type);
      graph.addEdge(e.source_id, e.target_id, {
        ...e,
        size: edgeBaseSize * style.sizeMultiplier,
        originalSize: edgeBaseSize * style.sizeMultiplier,
        color: style.color,
        type: 'line',
        relationType: e.type
      });
    }
  });
  
  applyFilters();
  runLayout();
}

function runLayout() {
  if (!graph || graph.order === 0) return;
  
  if (fa2Layout) {
    fa2Layout.kill();
    fa2Layout = null;
  }
  if (layoutTimeout) clearTimeout(layoutTimeout);
  
  const nodeCount = graph.order;
  const isSmall = nodeCount < 500;
  const isMedium = nodeCount >= 500 && nodeCount < 2000;
  
  const settings = forceAtlas2.inferSettings(graph);
  settings.gravity = isSmall ? 0.8 : isMedium ? 0.5 : 0.3;
  settings.scalingRatio = isSmall ? 15 : isMedium ? 30 : 60;
  settings.slowDown = isSmall ? 1 : isMedium ? 2 : 3;
  settings.barnesHutOptimize = nodeCount > 200;
  
  fa2Layout = new FA2Layout(graph, { settings });
  fa2Layout.start();
  
  document.getElementById("layout-indicator").classList.add("show");
  document.getElementById("btn-layout").classList.add("running");
  isLayoutRunning = true;
  
  const duration = nodeCount > 5000 ? 35000 : nodeCount > 1000 ? 25000 : 10000;
  
  layoutTimeout = setTimeout(() => {
    stopLayout();
  }, duration);
}

function stopLayout() {
  if (fa2Layout) {
    fa2Layout.stop();
    fa2Layout = null;
    
    // Apply Noverlap
    try {
        noverlap.assign(graph, {
          maxIterations: 20,
          ratio: 1.1,
          margin: 10,
          expansion: 1.05
        });
    } catch(e) { console.warn("Noverlap failed", e); }
    
    sigmaInst.refresh();
  }
  document.getElementById("layout-indicator").classList.remove("show");
  document.getElementById("btn-layout").classList.remove("running");
  isLayoutRunning = false;
}

// ── UI Actions ────────────────────────────────────────────────────────────────
function focusNode(nodeId) {
  if (!graph || !graph.hasNode(nodeId)) return;
  selectedNodeId = nodeId;
  
  // Wait for the side panels' CSS width transition (0.3s) to finish 
  // before updating the camera.
  setTimeout(() => {
    sigmaInst.refresh();
    if (sigmaInst) sigmaInst.getCamera().animatedReset({duration:300});
  }, 350);
  
  const nodeData = graph.getNodeAttributes(nodeId);
  showDetail(nodeData);
  showCodeViewer(nodeData.file_path, nodeData.line_number, nodeData.language);
  updateSelectedBar(nodeData);
  
  // Refresh immediately to show highlighting, then reset camera after panels open
  sigmaInst.refresh();
}

function clearSelection() {
  selectedNodeId = null;
  sigmaInst?.refresh();
  closeDetail();
  document.getElementById("selected-bar").classList.remove("show");
}

function updateSelectedBar(nodeData) {
  const bar = document.getElementById("selected-bar");
  document.getElementById("selected-bar-name").textContent = nodeData.name;
  document.getElementById("selected-bar-type").textContent = nodeData.type;
  bar.classList.add("show");
}

document.getElementById("selected-bar-clear").addEventListener("click", clearSelection);

// Tooltips
function showHoverTooltip(nodeId, displayData) {
  if (selectedNodeId) return; // Don't show if something is selected
  const node = graph.getNodeAttributes(nodeId);
  const tt = document.getElementById("hover-tooltip");
  tt.innerHTML = `<strong>${node.name}</strong><br/><span style="color:var(--text-dim)">${node.type} &middot; ${node.language || ""}</span>`;
  tt.style.display = "block";
  // Convert graph coordinates to viewport coordinates
  const vpPos = sigmaInst.framedGraphToViewport(displayData);
  tt.style.left = (vpPos.x + 12) + "px";
  tt.style.top  = (vpPos.y - 10) + "px";
}

function hideHoverTooltip() {
  document.getElementById("hover-tooltip").style.display = "none";
}

// ── Search & Filter ───────────────────────────────────────────────────────────
const searchInput = document.getElementById("search-input");
const searchDropdown = document.getElementById("search-dropdown");
let searchTimer = null;
let searchResults = [];  // current displayed results
let searchCursor = -1;   // keyboard nav cursor

// Score badge labels matching GitNexus style
const SCORE_LABEL = { 3: "exact", 2: "prefix", 1: "match" };
const SCORE_COLOR  = { 3: "#22c55e", 2: "#3b82f6", 1: "#94a3b8" };

searchInput.addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  const q = e.target.value.trim();
  if (!q) { searchDropdown.style.display = "none"; searchResults = []; searchCursor = -1; return; }
  searchTimer = setTimeout(() => doApiSearch(q), 180);
});

searchInput.addEventListener("keydown", (e) => {
  if (searchDropdown.style.display === "none") return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    searchCursor = Math.min(searchCursor + 1, searchResults.length - 1);
    highlightCursor();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    searchCursor = Math.max(searchCursor - 1, 0);
    highlightCursor();
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (searchCursor >= 0 && searchResults[searchCursor]) {
      selectSearchResult(searchResults[searchCursor]);
    } else if (searchResults.length > 0) {
      selectSearchResult(searchResults[0]);
    }
  } else if (e.key === "Escape") {
    searchDropdown.style.display = "none";
    searchCursor = -1;
  }
});

function highlightCursor() {
  const items = searchDropdown.querySelectorAll(".search-result-item");
  items.forEach((el, i) => el.classList.toggle("cursor", i === searchCursor));
  if (items[searchCursor]) items[searchCursor].scrollIntoView({ block: "nearest" });
}

async function doApiSearch(query) {
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(query)}&limit=50`);
    if (!resp.ok) return;
    const data = await resp.json();
    searchResults = data;
    searchCursor = -1;
    renderSearchDropdown(query);
  } catch (err) {
    console.warn("Search error:", err);
  }
}

function selectSearchResult(node) {
  searchDropdown.style.display = "none";
  searchInput.value = node.name;
  searchCursor = -1;

  // If node is in the current graph, focus it directly
  if (graph && graph.hasNode(node.id)) {
    focusNode(node.id);
  } else {
    // Node is filtered/hidden — still show its detail from API
    showDetailById(node.id);
  }
}

async function showDetailById(nodeId) {
  try {
    const resp = await fetch(`/api/node/${nodeId}`);
    const data = await resp.json();
    renderDetailPanel(data);
    document.getElementById("detail-panel").classList.add("open");
    if (data.node.file_path) showCodeViewer(data.node.file_path, data.node.line_number, data.node.language);
  } catch (err) {
    console.warn("Could not load node detail:", err);
  }
}

function renderSearchDropdown(query) {
  searchDropdown.innerHTML = "";

  if (searchResults.length === 0) {
    searchDropdown.innerHTML = `<div class="search-empty">No results for "<strong>${query}</strong>"</div>`;
    searchDropdown.style.display = "block";
    return;
  }

  // Type → group label
  const TYPE_GROUP_LABEL = {
    1: "Classes & Interfaces",
    2: "Enums & Objects",
    3: "Methods & Functions",
    4: "Fields & Properties",
    5: "Other",
  };

  // Track section dividers by composite key: "score-typePriority"
  let lastSectionKey = null;

  searchResults.forEach((n, idx) => {
    const score = n._score ?? 1;
    const typePri = n._type_priority ?? 5;
    const sectionKey = `${score}-${typePri}`;

    // Only emit a new section header when the key changes
    if (sectionKey !== lastSectionKey) {
      lastSectionKey = sectionKey;

      // Score label
      const scoreLabel = score === 3 ? "Exact" : score === 2 ? "Prefix" : "Contains";
      // Type group label
      const typeGroupLabel = TYPE_GROUP_LABEL[typePri] || "Other";

      const divider = document.createElement("div");
      divider.className = "search-section-header";
      divider.innerHTML = `<span class="ssh-score">${scoreLabel}</span><span class="ssh-sep">·</span>${typeGroupLabel}`;
      searchDropdown.appendChild(divider);
    }

    const color = nodeColor(n.type);
    const pkgPath = n.qualified_name && n.qualified_name !== n.name
      ? n.qualified_name.split(".").slice(0, -1).join(".")
      : n.package_name || "";
    const fileName = n.file_path ? n.file_path.split(/[/\\]/).pop() : "";
    const inGraph = graph && graph.hasNode(n.id);

    const div = document.createElement("div");
    div.className = "search-result-item";
    div.innerHTML = `
      <span class="sri-icon" style="background:${color}22;color:${color}">${n.type.charAt(0)}</span>
      <div class="sri-body">
        <div class="sri-top">
          <span class="sri-name">${highlightQuery(n.name, query)}</span>
          <span class="sri-type-badge" style="background:${color}22;color:${color}">${n.type}</span>
          ${!inGraph ? '<span class="sri-hidden-badge" title="Node not in current graph view">hidden</span>' : ''}
        </div>
        <div class="sri-pkg">${pkgPath ? pkgPath + ' · ' : ''}${fileName}</div>
      </div>
    `;
    div.addEventListener("mouseenter", () => {
      searchCursor = idx;
      highlightCursor();
    });
    div.addEventListener("click", () => selectSearchResult(n));
    searchDropdown.appendChild(div);
  });

  searchDropdown.style.display = "block";
}

function highlightQuery(text, query) {
  if (!query) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return text;
  return text.slice(0, idx) + `<mark class="sri-highlight">${text.slice(idx, idx + query.length)}</mark>` + text.slice(idx + query.length);
}

// Filtering
function applyFilters() {
  if (!graph) return;
  graph.forEachNode((node, attrs) => {
    graph.setNodeAttribute(node, "hidden", !activeTypes.has(attrs.nodeType));
  });
  graph.forEachEdge((edge, attrs) => {
    graph.setEdgeAttribute(edge, "hidden", !activeEdgeTypes.has(attrs.relationType));
  });
  if (sigmaInst) sigmaInst.refresh();
}

function buildFilterList(stats) {
  const container = document.getElementById("filter-list");
  container.innerHTML = "";
  Object.entries(NODE_COLORS).forEach(([type, color]) => {
    const checked = activeTypes.has(type);
    const div = document.createElement("div");
    div.className = "filter-item";
    div.innerHTML = `
      <input type="checkbox" id="f-${type}" ${checked ? "checked" : ""} />
      <span class="filter-dot" style="background:${color}"></span>
      <label class="filter-label" for="f-${type}">${type}</label>
    `;
    div.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? activeTypes.add(type) : activeTypes.delete(type);
      applyFilters();
    });
    container.appendChild(div);
  });
}

function buildEdgeLegend() {
  const container = document.getElementById("edge-legend-list");
  container.innerHTML = "";
  Object.entries(EDGE_COLORS).forEach(([type, style]) => {
    const checked = activeEdgeTypes.has(type);
    const div = document.createElement("div");
    div.className = "filter-item";
    div.innerHTML = `
      <input type="checkbox" id="e-${type}" ${checked ? "checked" : ""} />
      <div class="edge-legend-line" style="background:${style.color}"></div>
      <div class="edge-legend-label">${type}</div>
    `;
    div.querySelector("input").addEventListener("change", (e) => {
      e.target.checked ? activeEdgeTypes.add(type) : activeEdgeTypes.delete(type);
      applyFilters();
    });
    container.appendChild(div);
  });
}

// ── Node Detail Panel ─────────────────────────────────────────────────────────
async function showDetail(nodeData) {
  const panel = document.getElementById("detail-panel");
  const body  = document.getElementById("detail-body");
  panel.classList.add("open");
  
  body.innerHTML = `<div style="color:var(--text-dim);font-size:13px;padding:20px 0;text-align:center;">Loading…</div>`;
  
  try {
    const resp = await fetch(`/api/node/${nodeData.id}`);
    const data = await resp.json();
    renderDetailPanel(data);
  } catch {
    body.innerHTML = `<div style="color:var(--text-dim);font-size:13px;">Failed to load node detail.</div>`;
  }
}

function renderDetailPanel(data) {
  const n = data.node;
  const color = nodeColor(n.type);
  const body  = document.getElementById("detail-body");
  
  const relSection = (title, items) => {
    if (!items || !items.length) return "";
    const rows = items.map(e => {
      const other = e.other_node;
      if (!other) return "";
      return `<div class="related-item" data-id="${other.id}">
        <span class="related-dot" style="background:${nodeColor(other.type)}"></span>
        <span class="related-name">${other.name}</span>
        <span class="related-badge">${e.type}</span>
      </div>`;
    }).join("");
    return `<div class="detail-section"><div class="detail-section-title">${title}</div>${rows}</div>`;
  };
  
  body.innerHTML = `
    <div class="detail-type-badge" style="background:${color}22;color:${color}">${n.type}</div>
    <div class="detail-name">${n.name}</div>
    <div class="detail-qname">${n.qualified_name || n.name}</div>
    <div class="detail-section">
      <div class="detail-section-title">Info</div>
      <div class="detail-kv"><span class="k">Language</span><span class="v">${n.language || "—"}</span></div>
      <div class="detail-kv"><span class="k">Package</span><span class="v">${n.package_name || "—"}</span></div>
      <div class="detail-kv"><span class="k">File</span><span class="v">${n.file_path ? n.file_path.split(/[\\/]/).slice(-3).join('/') : "—"}</span></div>
      <div class="detail-kv"><span class="k">Line</span><span class="v">${n.line_number || "—"}</span></div>
    </div>
    ${relSection("Outgoing", data.outgoing)}
    ${relSection("Incoming", data.incoming)}
  `;
  
  body.querySelectorAll(".related-item[data-id]").forEach(el => {
    el.addEventListener("click", () => focusNode(el.dataset.id));
  });
}

function closeDetail() {
  document.getElementById("detail-panel").classList.remove("open");
  document.getElementById("code-panel").classList.remove("open");
}

// ── Code Panel ────────────────────────────────────────────────────────────────
async function showCodeViewer(filePath, lineNumber, language) {
  const panel = document.getElementById("code-panel");
  if (!filePath) { panel.classList.remove("open"); return; }
  
  const title = document.getElementById("code-panel-title");
  const codeContent = document.getElementById("code-content");
  
  title.textContent = filePath.split(/[\\/]/).pop();
  title.title = filePath;
  codeContent.textContent = "Loading...";
  codeContent.className = "hljs";
  panel.classList.add("open");
  
  try {
    const resp = await fetch(`/api/source?file_path=${encodeURIComponent(filePath)}`);
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    
    let highlighted = hljs.highlightAuto(data.content).value;
    if (language === "kotlin" || language === "java") {
      highlighted = hljs.highlight(data.content, { language }).value;
    }
    
    const lines = highlighted.split('\n');
    let finalHtml = "";
    lines.forEach((line, idx) => {
      if (lineNumber && idx + 1 === lineNumber) {
        finalHtml += `<span class="hl-line" id="hl-target">${line || ' '}</span>\n`;
      } else {
        finalHtml += `${line}\n`;
      }
    });
    codeContent.innerHTML = finalHtml;
    
    setTimeout(() => {
      const target = document.getElementById("hl-target");
      if (target) target.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 100);
  } catch (e) {
    codeContent.textContent = "Error: " + e.message;
  }
}

document.getElementById("code-panel-close").addEventListener("click", () => {
  document.getElementById("code-panel").classList.remove("open");
});

// ── Misc UI ───────────────────────────────────────────────────────────────────
function updateStatsBar(stats) {
  document.getElementById("stat-nodes").textContent = stats.node_count ?? "—";
  document.getElementById("stat-edges").textContent = stats.edge_count ?? "—";
}
function updateProjectName(stats) {
  document.getElementById("project-name").textContent = stats.project ? `📁 ${stats.project}` : "No project loaded";
}

let toastTimer = null;
function showToast(msg, duration = 2500) {
  const el = document.getElementById("toast");
  el.textContent = msg; el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), duration);
}
function showLoading(show) {
  document.getElementById("loading").classList.toggle("show", show);
}

// Theme
const themeBtn = document.getElementById("btn-theme");
const iconSun = document.getElementById("icon-sun");
const iconMoon = document.getElementById("icon-moon");
function setTheme(theme) {
  if (theme === "light") {
    document.documentElement.setAttribute("data-theme", "light");
    iconSun.style.display = "none"; iconMoon.style.display = "block";
    document.getElementById("hljs-theme").href = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-light.min.css";
    localStorage.setItem("KNode-theme", "light");
  } else {
    document.documentElement.removeAttribute("data-theme");
    iconSun.style.display = "block"; iconMoon.style.display = "none";
    document.getElementById("hljs-theme").href = "https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css";
    localStorage.setItem("KNode-theme", "dark");
  }
}
themeBtn.addEventListener("click", () => {
  setTheme(document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light");
});
const savedTheme = localStorage.getItem("KNode-theme");
if (savedTheme) setTheme(savedTheme);

// Resizers
function initResizer(resizerId, panelId, isRight, minW, maxW) {
  const resizer = document.getElementById(resizerId);
  const panel = document.getElementById(panelId);
  if (!resizer || !panel) return;
  let isDragging = false, startX, startWidth;
  
  resizer.addEventListener("mousedown", (e) => {
    isDragging = true; startX = e.clientX; startWidth = panel.offsetWidth;
    panel.classList.add("no-transition"); resizer.classList.add("dragging");
    document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none";
  });
  
  document.addEventListener("mousemove", (e) => {
    if (!isDragging) return;
    let dx = e.clientX - startX;
    let newWidth = isRight ? startWidth + dx : startWidth - dx;
    if (newWidth < minW) newWidth = minW;
    if (newWidth > maxW) newWidth = maxW;
    
    if (panelId === "sidebar") document.documentElement.style.setProperty("--sidebar-w", newWidth + "px");
    else if (panelId === "detail-panel") document.documentElement.style.setProperty("--detail-w", newWidth + "px");
    else if (panelId === "code-panel") document.documentElement.style.setProperty("--code-w", newWidth + "px");
  });
  
  document.addEventListener("mouseup", () => {
    if (isDragging) {
      isDragging = false;
      panel.classList.remove("no-transition"); resizer.classList.remove("dragging");
      document.body.style.cursor = ""; document.body.style.userSelect = "";
    }
  });
}
initResizer("resizer-sidebar", "sidebar", true, 200, 600);
initResizer("resizer-code", "code-panel", true, 300, 800);
initResizer("resizer-detail", "detail-panel", false, 250, 600);

// Controls
document.getElementById("btn-zoom-in").addEventListener("click", () => sigmaInst && sigmaInst.getCamera().animatedZoom({duration:200}));
document.getElementById("btn-zoom-out").addEventListener("click", () => sigmaInst && sigmaInst.getCamera().animatedUnzoom({duration:200}));
document.getElementById("btn-fit").addEventListener("click", () => {
  if (sigmaInst) sigmaInst.getCamera().animatedReset({duration:300});
});
document.getElementById("btn-layout").addEventListener("click", () => {
  if (isLayoutRunning) stopLayout();
  else runLayout();
});
document.getElementById("btn-reload").addEventListener("click", () => loadGraph());
document.getElementById("detail-close").addEventListener("click", clearSelection);

// Dropdown hide
document.addEventListener("click", (e) => {
  if (!e.target.closest("#search-container")) document.getElementById("search-dropdown").style.display = "none";
});

// Boot
loadGraph();
