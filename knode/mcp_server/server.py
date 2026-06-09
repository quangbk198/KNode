"""
KNode MCP Server — exposes the knowledge graph as MCP tools
so AI agents can query any indexed Android project.

Run via:  knode mcp
          knode mcp --project <path>   (optional: pin to one project)
Or add to Claude Desktop / Cursor config as a stdio server (no --project needed).
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from knode.graph.storage import GraphStorage, get_db_path
from knode.graph.models import NodeType, EdgeType

app = Server("knode")

# Registry of loaded projects: {name -> GraphStorage}
_projects: dict[str, GraphStorage] = {}
# Active project (used by all single-project tools)
_active_project: Optional[str] = None


def _get_storage(project: Optional[str] = None) -> GraphStorage:
    name = project or _active_project
    if name and name in _projects:
        return _projects[name]
    if _projects:
        return next(iter(_projects.values()))
    raise RuntimeError(
        "No project loaded. Run `knode index <path>` first, "
        "or pass project_name to the tool."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Tool definitions
# ──────────────────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_stats",
            description="Return statistics about the indexed project (node/edge counts by type).",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="search_nodes",
            description="Search for classes, methods, or fields by name.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Name or partial name to search for."},
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_class_info",
            description="Return full information about a class: fields, methods, superclass, interfaces.",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_name": {"type": "string", "description": "Simple or qualified class name."},
                },
                "required": ["class_name"],
            },
        ),
        types.Tool(
            name="find_usages",
            description="Find all nodes that call or reference a given method or class.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Method or class name to find usages of."},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="get_call_chain",
            description="Trace the call chain starting from a method (BFS, max depth 5).",
            inputSchema={
                "type": "object",
                "properties": {
                    "method_name": {"type": "string", "description": "Starting method name."},
                    "max_depth": {"type": "integer", "description": "Max depth (default 5)."},
                },
                "required": ["method_name"],
            },
        ),
        types.Tool(
            name="get_dependencies",
            description="List all classes that a given class depends on (EXTENDS, IMPLEMENTS, CALLS).",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_name": {"type": "string", "description": "Class name to analyse."},
                },
                "required": ["class_name"],
            },
        ),
        types.Tool(
            name="list_classes",
            description="List all classes/interfaces in a package or the whole project.",
            inputSchema={
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "Package prefix filter (optional)."},
                    "type_filter": {"type": "string", "description": "Node type: Class|Interface|Enum|AbstractClass (optional)."},
                },
                "required": [],
            },
        ),
        types.Tool(
            name="index_project",
            description="Re-index an Android project at the given path.",
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {"type": "string", "description": "Absolute path to the Android project root."},
                },
                "required": ["project_path"],
            },
        ),
        types.Tool(
            name="list_projects",
            description="List all indexed Android projects registered with KNode.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="switch_project",
            description="Switch the active project by name (as shown in list_projects).",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Project name from the registry."},
                },
                "required": ["name"],
            },
        ),
        types.Tool(
            name="get_node",
            description="Inspect a single node by its ID (returns file, line, type, etc).",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Node ID."},
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="get_neighbors",
            description="Get callers, callees, and relationships for a node.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Node ID."},
                    "direction": {"type": "string", "enum": ["incoming", "outgoing", "both"], "default": "both", "description": "Direction of edges."},
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="impact",
            description="Blast radius analysis: finding all nodes that depend on a given node (BFS on incoming edges).",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Target Node ID."},
                    "max_depth": {"type": "integer", "description": "Max depth (default 3).", "default": 3},
                },
                "required": ["id"],
            },
        ),
        types.Tool(
            name="sql_query",
            description="Run a raw SQLite query against the graph database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "SQL query (e.g. SELECT * FROM nodes WHERE type='Class')."},
                },
                "required": ["query"],
            },
        ),
    ]


# ──────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────────────────────────────────────

@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
    global _active_project

    def text(data: Any) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]

    # list_projects and switch_project don't need a storage handle
    if name == "list_projects":
        from knode.registry import list_projects as _lp
        result = []
        for pname, info in _lp().items():
            result.append({
                "name": pname,
                "path": info["path"],
                "indexed_at": info.get("indexed_at", "unknown"),
                "loaded": pname in _projects,
                "active": pname == _active_project,
                "db_exists": Path(info["db"]).exists(),
            })
        return text(result)

    if name == "switch_project":
        target = arguments.get("name", "")
        if target not in _projects:
            # Try to load it
            from knode.registry import list_projects as _lp
            reg = _lp()
            if target not in reg:
                return text({"error": f"Project '{target}' not found in registry."})
            db = reg[target]["db"]
            if not Path(db).exists():
                return text({"error": f"DB for '{target}' missing. Re-run `knode index`." })
            storage = GraphStorage(db)
            storage.connect()
            _projects[target] = storage
        _active_project = target
        return text({"status": "ok", "active_project": target})

    st = _get_storage()

    try:
        # ── get_stats ──────────────────────────────────────────────────────────
        if name == "get_stats":
            stats = st.get_stats()
            project_name = st.get_project_info("name") or "Unknown"
            indexed_at = st.get_project_info("indexed_at") or "Unknown"
            return text({"project": project_name, "indexed_at": indexed_at, **stats})

        # ── search_nodes ───────────────────────────────────────────────────────
        elif name == "search_nodes":
            query = arguments.get("query", "")
            nodes = st.search_nodes(query)
            return text([n.to_dict() for n in nodes])

        # ── get_class_info ─────────────────────────────────────────────────────
        elif name == "get_class_info":
            class_name = arguments.get("class_name", "")
            nodes = st.search_nodes(class_name)
            # Find best match
            target = next(
                (n for n in nodes if n.name == class_name and n.type.value in
                 ("Class", "AbstractClass", "Interface", "Enum", "Object")),
                nodes[0] if nodes else None,
            )
            if not target:
                return text({"error": f"Class '{class_name}' not found."})

            children_edges = st.get_edges_from(target.id)
            children = [st.get_node(e.target_id) for e in children_edges if e.type == EdgeType.CONTAINS]
            extends_edges = [e for e in children_edges if e.type == EdgeType.EXTENDS]
            impl_edges = [e for e in children_edges if e.type == EdgeType.IMPLEMENTS]

            return text({
                "class": target.to_dict(),
                "extends": [st.get_node(e.target_id).to_dict() for e in extends_edges if st.get_node(e.target_id)],
                "implements": [st.get_node(e.target_id).to_dict() for e in impl_edges if st.get_node(e.target_id)],
                "members": [n.to_dict() for n in children if n],
            })

        # ── find_usages ────────────────────────────────────────────────────────
        elif name == "find_usages":
            name_arg = arguments.get("name", "")
            targets = st.search_nodes(name_arg)
            results = []
            for target in targets[:5]:
                incoming = st.get_edges_to(target.id)
                for edge in incoming:
                    caller = st.get_node(edge.source_id)
                    if caller:
                        results.append({
                            "callee": target.to_dict(),
                            "caller": caller.to_dict(),
                            "edge_type": edge.type.value,
                        })
            return text(results)

        # ── get_call_chain ─────────────────────────────────────────────────────
        elif name == "get_call_chain":
            method_name = arguments.get("method_name", "")
            max_depth = int(arguments.get("max_depth", 5))
            starts = [n for n in st.search_nodes(method_name)
                      if n.type in (NodeType.METHOD, NodeType.FUNCTION)]
            if not starts:
                return text({"error": f"Method '{method_name}' not found."})

            visited, chain = set(), []
            queue = [(starts[0].id, 0)]

            while queue:
                node_id, depth = queue.pop(0)
                if depth > max_depth or node_id in visited:
                    continue
                
                visited.add(node_id)
                n = st.get_node(node_id)
                if not n:
                    continue
                    
                chain.append({"depth": depth, "node": n.to_dict()})
                for edge in st.get_edges_from(node_id):
                    if edge.type == EdgeType.CALLS:
                        queue.append((edge.target_id, depth + 1))

            return text(chain)
        # ── get_dependencies ───────────────────────────────────────────────────
        elif name == "get_dependencies":
            class_name = arguments.get("class_name", "")
            nodes = st.search_nodes(class_name)
            target = next((n for n in nodes if n.name == class_name), nodes[0] if nodes else None)
            if not target:
                return text({"error": f"Class '{class_name}' not found."})
            edges = st.get_edges_from(target.id)
            deps = []
            for e in edges:
                if e.type in (EdgeType.EXTENDS, EdgeType.IMPLEMENTS, EdgeType.USES):
                    dep_node = st.get_node(e.target_id)
                    if dep_node:
                        deps.append({"relation": e.type.value, "node": dep_node.to_dict()})
            return text(deps)

        # ── list_classes ───────────────────────────────────────────────────────
        elif name == "list_classes":
            package = arguments.get("package", "")
            type_filter = arguments.get("type_filter", "")
            all_nodes = st.get_all_nodes()
            result = []
            for n in all_nodes:
                if n.type.value not in ("Class", "AbstractClass", "Interface", "Enum", "Object"):
                    continue
                if type_filter and n.type.value != type_filter:
                    continue
                if package and not n.package_name.startswith(package):
                    continue
                result.append(n.to_dict())
            return text(result)

        # ── index_project ──────────────────────────────────────────────────────
        elif name == "index_project":
            project_path = arguments.get("project_path", "")
            if not Path(project_path).exists():
                return text({"error": f"Path '{project_path}' does not exist."})
            from knode.indexer.graph_builder import build_graph
            from knode.registry import register
            # Close old storage for this project if open
            pname = Path(project_path).name
            if pname in _projects:
                _projects[pname].close()
            new_storage = build_graph(project_path)
            register(project_path, get_db_path(project_path))
            _projects[pname] = new_storage
            _active_project = pname
            stats = new_storage.get_stats()
            return text({"status": "ok", "project": pname, "project_path": project_path, **stats})

        # ── get_node ───────────────────────────────────────────────────────────
        elif name == "get_node":
            node_id = arguments.get("id", "")
            node = st.get_node(node_id)
            if not node:
                return text({"error": f"Node '{node_id}' not found."})
            return text(node.to_dict())

        # ── get_neighbors ──────────────────────────────────────────────────────
        elif name == "get_neighbors":
            node_id = str(arguments.get("id", ""))
            direction = arguments.get("direction", "both")
            node = st.get_node(node_id)
            if not node:
                return text({"error": f"Node '{node_id}' not found."})
            
            result = {"node": node.to_dict()}
            if direction in ("outgoing", "both"):
                out_edges = st.get_edges_from(node_id)
                result["outgoing"] = [{"edge": e.to_dict(), "target": st.get_node(e.target_id).to_dict() if st.get_node(e.target_id) else None} for e in out_edges]
            if direction in ("incoming", "both"):
                in_edges = st.get_edges_to(node_id)
                result["incoming"] = [{"edge": e.to_dict(), "source": st.get_node(e.source_id).to_dict() if st.get_node(e.source_id) else None} for e in in_edges]
                
            return text(result)

        # ── impact ─────────────────────────────────────────────────────────────
        elif name == "impact":
            node_id = str(arguments.get("id", ""))
            max_depth = int(arguments.get("max_depth", 3))
            start_node = st.get_node(node_id)
            if not start_node:
                return text({"error": f"Node '{node_id}' not found."})

            visited, chain = set(), []
            queue = [(node_id, 0)]

            while queue:
                current_id, depth = queue.pop(0)
                if depth > max_depth or current_id in visited:
                    continue
                
                visited.add(current_id)
                n = st.get_node(current_id)
                if not n:
                    continue
                    
                chain.append({"depth": depth, "node": n.to_dict()})
                for edge in st.get_edges_to(current_id):
                    queue.append((edge.source_id, depth + 1))

            return text(chain)

        # ── sql_query ──────────────────────────────────────────────────────────
        elif name == "sql_query":
            query = arguments.get("query", "")
            try:
                query_upper = query.upper()
                if any(kw in query_upper for kw in ["DROP ", "DELETE ", "UPDATE ", "INSERT ", "REPLACE ", "ALTER "]):
                    return text({"error": "Only SELECT queries are allowed for safety."})
                rows = st._conn.execute(query).fetchall()
                return text([dict(r) for r in rows])
            except Exception as e:
                return text({"error": f"SQL Error: {str(e)}"})

        else:
            return text({"error": f"Unknown tool: {name}"})

    except Exception as e:
        import sys
        import traceback
        traceback.print_exc(file=sys.stderr)
        return text({"error": str(e), "type": type(e).__name__})


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

async def run_server(project_path: str | None = None):
    """Start the MCP server.

    If *project_path* is given, only that project is loaded.
    Otherwise ALL projects from ~/.knode/registry.json are loaded,
    and the nearest one (by cwd) becomes the active project.
    """
    global _active_project

    from knode.registry import get_all_db_paths, get_nearest_db, list_projects as _lp

    if project_path:
        # Pinned to one project
        db_path = get_db_path(project_path)
        if not Path(db_path).exists():
            raise FileNotFoundError(
                f"No index found at '{db_path}'.\n"
                f"Run `knode index \"{project_path}\"` first."
            )
        pname = Path(project_path).name
        storage = GraphStorage(db_path)
        storage.connect()
        _projects[pname] = storage
        _active_project = pname
    else:
        # Load ALL registered projects
        registry = _lp()
        for pname, info in registry.items():
            db = info["db"]
            if Path(db).exists():
                storage = GraphStorage(db)
                storage.connect()
                _projects[pname] = storage

        # Set active = nearest project to cwd (fallback: first loaded)
        nearest_db = get_nearest_db()
        if nearest_db:
            for pname, info in registry.items():
                if info["db"] == nearest_db and pname in _projects:
                    _active_project = pname
                    break
        if not _active_project and _projects:
            _active_project = next(iter(_projects))

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main(project_path: str | None = None):
    asyncio.run(run_server(project_path))
