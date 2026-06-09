"""
Graph builder — orchestrates scanning, parsing, and storing the knowledge graph.
"""
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from knode.indexer.scanner import scan_sources, count_sources
from knode.indexer.java_parser import parse_java_file
from knode.indexer.kotlin_parser import parse_kotlin_file
from knode.graph.storage import GraphStorage, get_db_path

console = Console(stderr=True)

def _update_gitignore(project_path: str):
    gitignore_path = Path(project_path) / ".gitignore"
    entry = ".knode/"
    if gitignore_path.exists():
        try:
            content = gitignore_path.read_text(encoding="utf-8")
            if entry not in content.splitlines():
                with gitignore_path.open("a", encoding="utf-8") as f:
                    if content and not content.endswith("\n"):
                        f.write("\n")
                    f.write(f"{entry}\n")
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not update .gitignore: {exc}[/yellow]")
    else:
        try:
            gitignore_path.write_text(f"{entry}\n", encoding="utf-8")
        except Exception as exc:
            console.print(f"[yellow]⚠ Could not create .gitignore: {exc}[/yellow]")


def _resolve_cross_file_calls(storage: GraphStorage):
    """Link abstract method refs to concrete methods across the entire project.
    
    Uses the 'qualifier' metadata on CALLS edges (e.g. 'loginRepo' for loginRepo.getMerchant())
    to prefer methods whose containing class name matches the qualifier — preventing false
    cross-layer edges (e.g. ViewModel → Service in MVVM architecture).
    """
    import hashlib
    import json
    from knode.graph.models import Edge, EdgeType

    # Ignore very common standard library/framework method names that cause false positive links
    IGNORE_METHODS = {
        "remove", "add", "get", "set", "clear", "toString", "invoke",
        "let", "run", "with", "apply", "also", "forEach", "map", "filter",
        "postValue", "setValue", "getValue", "observe", "launch", "cancel"
    }

    abstract_methods = {
        n.id: n.name 
        for n in storage.get_all_nodes() 
        if n.type.value == "Method" and not n.file_path
    }
    if not abstract_methods:
        return

    # Build lookup: method name → [node, …]
    concrete_nodes = {}
    for n in storage.get_all_nodes():
        if n.type.value in ("Method", "Class") and n.file_path:
            concrete_nodes.setdefault(n.name, []).append(n)

    # Build lookup: method node id → parent class name (from CONTAINS edges)
    # e.g. Service.getMerchant → parent class = "Service"
    method_parent_class = {}
    for e in storage.get_all_edges():
        if e.type.value == "CONTAINS":
            method_parent_class[e.target_id] = e.source_id  # target=method, source=class

    # Build lookup: class node id → class name
    class_name_by_id = {
        n.id: n.name
        for n in storage.get_all_nodes()
        if n.type.value in ("Class", "Interface", "AbstractClass")
    }
        
    new_edges = []
    edges_to_delete = []
    
    for e in storage.get_all_edges():
        if e.type == EdgeType.CALLS and e.target_id in abstract_methods:
            method_name = abstract_methods[e.target_id]
            if method_name in IGNORE_METHODS:
                continue

            concrete_targets = concrete_nodes.get(method_name, [])
            if not concrete_targets:
                continue

            # ── Qualifier-based filtering ─────────────────────────────────
            # If the edge has a 'qualifier' (e.g. 'loginRepo' from loginRepo.getMerchant()),
            # prefer candidates whose parent class name contains the qualifier (case-insensitive).
            # e.g. qualifier='loginRepo' → prefer 'LoginRepo', 'LoginRepoImpl' over 'Service'
            qualifier = None
            if e.metadata:
                try:
                    meta = e.metadata if isinstance(e.metadata, dict) else json.loads(e.metadata)
                    qualifier = meta.get("qualifier", "")
                except Exception:
                    pass

            if qualifier:
                qualifier_lower = qualifier.lower()
                filtered = []
                for cnode in concrete_targets:
                    parent_class_id = method_parent_class.get(cnode.id)
                    if parent_class_id:
                        parent_class_name = class_name_by_id.get(parent_class_id, "").lower()
                        # Match: e.g. qualifier='loginRepo' matches class 'LoginRepo' or 'LoginRepoImpl'
                        if qualifier_lower in parent_class_name or parent_class_name in qualifier_lower:
                            filtered.append(cnode)
                # Only use filtered set if it is non-empty — prevents over-filtering
                if filtered:
                    concrete_targets = filtered

            edges_to_delete.append(e.id)
            for cnode in concrete_targets:
                cid = cnode.id
                etype = EdgeType.INSTANTIATES if cnode.type.value == "Class" else e.type
                new_eid = hashlib.md5(f"{e.source_id}|{cid}|{etype.value}".encode()).hexdigest()[:16]
                new_edge = Edge(
                    id=new_eid,
                    source_id=e.source_id,
                    target_id=cid,
                    type=etype,
                    metadata=e.metadata
                )
                new_edges.append(new_edge)
                    
    for e in new_edges:
        storage.upsert_edge(e)
    for eid in edges_to_delete:
        storage._conn.execute("DELETE FROM edges WHERE id=?", (eid,))
        
    storage._conn.execute("""
        DELETE FROM nodes 
        WHERE type='Method' AND (file_path IS NULL OR file_path='')
        AND id NOT IN (SELECT target_id FROM edges)
        AND id NOT IN (SELECT source_id FROM edges)
    """)
    storage.commit()


def _resolve_cross_file_reads(storage: GraphStorage):
    """Resolve property_ref placeholder nodes (no file_path) to concrete
    Property/Field nodes that were indexed from other files.

    Strategy
    --------
    Placeholders created by _walk_reads carry a  qualified_name  of the form
    ``SimpleClassName.MEMBER``  (e.g. "Constants.SHARE_PREFERENCE_NAME").
    We try two levels of matching, in order:

    1. Exact qualified_name match against concrete Property/Field nodes.
    2. Simple-name match when only one concrete node has that member name
       (avoids false-positive links when the name is common).
    """
    import hashlib
    from knode.graph.models import Edge, EdgeType, NodeType

    # ── Collect placeholder property refs (no file_path) ──────────────────
    prop_refs = {
        n.id: n
        for n in storage.get_all_nodes()
        if n.type.value in ("Property", "Field") and not n.file_path
    }
    if not prop_refs:
        return

    # ── Index concrete Property/Field nodes ───────────────────────────────
    concrete_by_qname: dict[str, list] = {}   # qualified_name → [node, …]
    concrete_by_name:  dict[str, list] = {}   # simple name    → [node, …]
    for n in storage.get_all_nodes():
        if n.type.value in ("Property", "Field") and n.file_path:
            concrete_by_qname.setdefault(n.qualified_name, []).append(n)
            concrete_by_name.setdefault(n.name, []).append(n)

    new_edges: list[Edge] = []
    edges_to_delete: list[str] = []

    for e in storage.get_all_edges():
        if e.type != EdgeType.READS or e.target_id not in prop_refs:
            continue

        ref_node = prop_refs[e.target_id]
        ref_qname = ref_node.qualified_name   # e.g. "Constants.SHARE_PREFERENCE_NAME"
        ref_name  = ref_node.name             # e.g. "SHARE_PREFERENCE_NAME"

        targets = []

        # 1. Exact qualified_name match
        #    The placeholder qname is "Qualifier.MEMBER" which may or may not
        #    include the full package prefix, so check both the full qname and
        #    any concrete node whose qname ends with ".Qualifier.MEMBER".
        for cqname, nodes in concrete_by_qname.items():
            if cqname == ref_qname or cqname.endswith(f".{ref_qname}"):
                targets.extend(nodes)

        # 2. Fallback: simple-name match only when unambiguous
        if not targets:
            candidates = concrete_by_name.get(ref_name, [])
            if len(candidates) == 1:
                targets = candidates

        if not targets:
            continue

        edges_to_delete.append(e.id)
        for cnode in targets:
            new_eid = hashlib.md5(
                f"{e.source_id}|{cnode.id}|{EdgeType.READS.value}".encode()
            ).hexdigest()[:16]
            new_edges.append(Edge(
                id=new_eid,
                source_id=e.source_id,
                target_id=cnode.id,
                type=EdgeType.READS,
                metadata=e.metadata,
            ))

    for e in new_edges:
        storage.upsert_edge(e)
    for eid in edges_to_delete:
        storage._conn.execute("DELETE FROM edges WHERE id=?", (eid,))

    # Remove fully-orphaned placeholder nodes
    storage._conn.execute("""
        DELETE FROM nodes
        WHERE (file_path IS NULL OR file_path='')
          AND type IN ('Property', 'Field')
          AND id NOT IN (SELECT target_id FROM edges)
          AND id NOT IN (SELECT source_id FROM edges)
    """)
    storage.commit()




def _resolve_cross_file_types(storage: GraphStorage):
    """Link abstract type refs to concrete classes/interfaces across the entire project."""
    import hashlib
    from knode.graph.models import Edge, EdgeType

    abstract_types = {
        n.id: n.name 
        for n in storage.get_all_nodes() 
        if n.type.value in ("Class", "Interface", "Enum", "AbstractClass") and not n.file_path
    }
    if not abstract_types:
        return

    concrete_by_name = {}
    for n in storage.get_all_nodes():
        if n.type.value in ("Class", "Interface", "Enum", "AbstractClass") and n.file_path:
            concrete_by_name.setdefault(n.name, []).append(n.id)

    new_edges = []
    edges_to_delete = []

    for e in storage.get_all_edges():
        if e.type.value in ("EXTENDS", "IMPLEMENTS") and e.target_id in abstract_types:
            type_name = abstract_types[e.target_id]
            concrete_ids = concrete_by_name.get(type_name, [])

            if concrete_ids:
                edges_to_delete.append(e.id)
                for cid in concrete_ids:
                    new_eid = hashlib.md5(f"{e.source_id}|{cid}|{e.type.value}".encode()).hexdigest()[:16]
                    new_edge = Edge(
                        id=new_eid,
                        source_id=e.source_id,
                        target_id=cid,
                        type=e.type,
                        metadata=e.metadata
                    )
                    new_edges.append(new_edge)

    for e in new_edges:
        storage.upsert_edge(e)
    for eid in edges_to_delete:
        storage._conn.execute("DELETE FROM edges WHERE id=?", (eid,))

    storage._conn.execute("""
        DELETE FROM nodes 
        WHERE type IN ('Class', 'Interface', 'Enum', 'AbstractClass') 
        AND (file_path IS NULL OR file_path='')
        AND id NOT IN (SELECT target_id FROM edges)
        AND id NOT IN (SELECT source_id FROM edges)
    """)
    storage.commit()


def build_graph(
    project_path: str,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> GraphStorage:
    """
    Index an Android project and return an open GraphStorage.
    Caller is responsible for closing the storage.
    """
    db_path = get_db_path(project_path)
    storage = GraphStorage(db_path)
    storage.connect()
    storage.clear()

    files = list(scan_sources(project_path))
    total = len(files)

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Indexing {total} files…", total=total)

        for i, (file_path, lang) in enumerate(files):
            progress.update(task, description=f"[cyan]{file_path.name}", advance=1)
            if on_progress:
                on_progress(i + 1, total, str(file_path))

            try:
                if lang == "java":
                    nodes, edges = parse_java_file(str(file_path))
                else:
                    nodes, edges = parse_kotlin_file(str(file_path))

                for node in nodes:
                    storage.upsert_node(node)
                for edge in edges:
                    storage.upsert_edge(edge)

            except Exception as exc:  # noqa: BLE001
                console.print(f"[yellow]⚠ Skipped {file_path.name}: {exc}")

        storage.commit()

    # Perform cross-file call, type, and property-reads resolution
    _resolve_cross_file_calls(storage)
    _resolve_cross_file_types(storage)
    _resolve_cross_file_reads(storage)


    stats = storage.get_stats()
    storage.set_project_info("path", project_path)
    storage.set_project_info("name", Path(project_path).name)
    storage.set_project_info("indexed_at", datetime.now(timezone.utc).isoformat())
    storage.set_project_info("stats", stats)

    # Automatically add .knode/ to .gitignore
    _update_gitignore(project_path)

    return storage
