"""
FastAPI web server — serves the graph browser UI and REST API.
Run via:  knode serve --project <path> [--port 7070]
"""
import json
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from knode.graph.storage import GraphStorage, get_db_path
from knode.graph.models import NodeType, EdgeType

_storage: GraphStorage | None = None
_project_path: str = ""

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if _storage:
        _storage.close()


app = FastAPI(title="KNode Graph Browser", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_st() -> GraphStorage:
    if _storage is None:
        raise HTTPException(status_code=503, detail="No project loaded.")
    return _storage


# ──────────────────────────────────────────────────────────────────────────────
# REST API
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def api_stats():
    st = get_st()
    stats = st.get_stats()
    project_name = st.get_project_info("name") or Path(_project_path).name
    indexed_at = st.get_project_info("indexed_at") or ""
    return {"project": project_name, "path": _project_path, "indexed_at": indexed_at, **stats}


@app.get("/api/graph")
def api_graph(
    node_types: str = Query(default="", description="Comma-separated NodeType values to include"),
    edge_types: str = Query(default="", description="Comma-separated EdgeType values to include"),
    max_nodes: int = Query(default=0, description="Max nodes to return, 0 means all"),
):
    st = get_st()
    all_nodes = st.get_all_nodes()
    all_edges = st.get_all_edges()

    # Filter by node type
    allowed_node_types = set(node_types.split(",")) if node_types else None
    if allowed_node_types:
        all_nodes = [n for n in all_nodes if n.type.value in allowed_node_types]

    # Limit nodes for performance
    if max_nodes > 0:
        all_nodes = all_nodes[:max_nodes]
    node_ids = {n.id for n in all_nodes}

    # Only keep edges where both endpoints are visible
    allowed_edge_types = set(edge_types.split(",")) if edge_types else None
    filtered_edges = [
        e for e in all_edges
        if e.source_id in node_ids and e.target_id in node_ids
        and (not allowed_edge_types or e.type.value in allowed_edge_types)
    ]

    return {
        "nodes": [n.to_dict() for n in all_nodes],
        "edges": [e.to_dict() for e in filtered_edges],
    }


@app.get("/api/node/{node_id}")
def api_node(node_id: str):
    st = get_st()
    node = st.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="Node not found.")
    out_edges = st.get_edges_from(node_id)
    in_edges = st.get_edges_to(node_id)

    def enrich(edges):
        result = []
        for e in edges:
            other_id = e.target_id if e.source_id == node_id else e.source_id
            other = st.get_node(other_id)
            result.append({**e.to_dict(), "other_node": other.to_dict() if other else None})
        return result

    return {
        "node": node.to_dict(),
        "outgoing": enrich(out_edges),
        "incoming": enrich(in_edges),
    }


@app.get("/api/source")
def api_source(file_path: str = Query(...)):
    global _project_path
    if not file_path or file_path == "—":
        raise HTTPException(status_code=400, detail="Invalid file path")
        
    try:
        # Check if the file is inside the project path to prevent path traversal
        target_path = Path(file_path).resolve()
        proj_path = Path(_project_path).resolve()
        
        # Windows paths might have different casing, using is_relative_to
        if not target_path.is_relative_to(proj_path):
             raise HTTPException(status_code=403, detail="Access denied: outside project directory")
             
        if not target_path.exists() or not target_path.is_file():
            raise HTTPException(status_code=404, detail="File not found on disk")
            
        content = target_path.read_text(encoding="utf-8", errors="replace")
        return {"content": content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/api/search")
def api_search(
    q: str = Query(default=""),
    types: str = Query(default="", description="Comma-separated node types to filter"),
    limit: int = Query(default=50, ge=1, le=200),
):
    st = get_st()
    if not q or len(q.strip()) < 1:
        return []
    q = q.strip()
    node_types = [t for t in types.split(",") if t] if types else None
    nodes = st.search_nodes(q, node_types=node_types, limit=limit)

    # Type priority: lower number = higher priority in results
    TYPE_PRIORITY = {
        "Class":         1,
        "Interface":     1,
        "AbstractClass": 1,
        "Enum":          2,
        "Object":        2,
        "Method":        3,
        "Function":      3,
        "Field":         4,
        "Property":      4,
    }

    # Compute rank score for frontend display
    q_lower = q.lower()
    results = []
    for n in nodes:
        name_lower = n.name.lower()
        if name_lower == q_lower:
            score = 3  # exact
        elif name_lower.startswith(q_lower):
            score = 2  # prefix
        else:
            score = 1  # contains
        d = n.to_dict()
        d["_score"] = score
        d["_type_priority"] = TYPE_PRIORITY.get(n.type.value, 5)
        results.append(d)

    # Sort: score DESC (higher first), then type priority ASC (structural types first), then name ASC
    results.sort(key=lambda x: (-x["_score"], x["_type_priority"], x.get("name", "").lower()))

    return results


class IndexRequest(BaseModel):
    project_path: str


@app.post("/api/index")
def api_index(req: IndexRequest):
    global _storage, _project_path
    p = Path(req.project_path)
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path not found: {req.project_path}")

    from knode.indexer.graph_builder import build_graph
    if _storage:
        _storage.close()
    _storage = build_graph(req.project_path)
    _project_path = req.project_path
    return {"status": "ok", **_storage.get_stats()}


# ──────────────────────────────────────────────────────────────────────────────
# Static files & SPA fallback
# ──────────────────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def root():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# Startup helper (called from CLI)
# ──────────────────────────────────────────────────────────────────────────────

def create_app(project_path: str) -> FastAPI:
    global _storage, _project_path
    _project_path = project_path
    db_path = get_db_path(project_path)
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"No index found. Run `knode index \"{project_path}\"` first."
        )
    _storage = GraphStorage(db_path)
    _storage.connect()
    return app
