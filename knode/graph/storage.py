"""
SQLite-backed storage for the knode knowledge graph.
All data is stored in .knode/graph.db inside the indexed project.
"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from knode.graph.models import Node, Edge, NodeType, EdgeType, ProjectInfo


class GraphStorage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------
    def _init_schema(self):
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id            TEXT PRIMARY KEY,
                type          TEXT NOT NULL,
                name          TEXT NOT NULL,
                qualified_name TEXT,
                file_path     TEXT,
                line_number   INTEGER DEFAULT 0,
                package_name  TEXT,
                language      TEXT,
                is_abstract   INTEGER DEFAULT 0,
                visibility    TEXT DEFAULT 'public',
                annotations   TEXT DEFAULT '[]',
                metadata      TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS edges (
                id        TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                type      TEXT NOT NULL,
                metadata  TEXT DEFAULT '{}',
                FOREIGN KEY (source_id) REFERENCES nodes(id),
                FOREIGN KEY (target_id) REFERENCES nodes(id)
            );

            CREATE TABLE IF NOT EXISTS project_info (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS indexed_files (
                file_path  TEXT PRIMARY KEY,
                mtime      REAL NOT NULL,
                language   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_package ON nodes(package_name);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_edges_type ON edges(type);
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------
    def upsert_node(self, node: Node):
        self._conn.execute("""
            INSERT INTO nodes
                (id, type, name, qualified_name, file_path, line_number,
                 package_name, language, is_abstract, visibility, annotations, metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                type=excluded.type, name=excluded.name,
                qualified_name=excluded.qualified_name,
                file_path=excluded.file_path, line_number=excluded.line_number,
                package_name=excluded.package_name, language=excluded.language,
                is_abstract=excluded.is_abstract, visibility=excluded.visibility,
                annotations=excluded.annotations, metadata=excluded.metadata
        """, (
            node.id, node.type.value, node.name, node.qualified_name,
            node.file_path, node.line_number, node.package_name, node.language,
            int(node.is_abstract), node.visibility,
            json.dumps(node.annotations), json.dumps(node.metadata),
        ))

    def upsert_edge(self, edge: Edge):
        self._conn.execute("""
            INSERT INTO edges (id, source_id, target_id, type, metadata)
            VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                source_id=excluded.source_id, target_id=excluded.target_id,
                type=excluded.type, metadata=excluded.metadata
        """, (
            edge.id, edge.source_id, edge.target_id, edge.type.value,
            json.dumps(edge.metadata),
        ))

    def commit(self):
        self._conn.commit()

    def clear(self):
        self._conn.executescript(
            "DELETE FROM nodes; DELETE FROM edges; "
            "DELETE FROM project_info; DELETE FROM indexed_files;"
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Incremental indexing helpers — indexed_files table
    # ------------------------------------------------------------------
    def record_file(self, file_path: str, mtime: float, language: str):
        """Record that a file has been indexed at the given mtime."""
        self._conn.execute(
            """
            INSERT INTO indexed_files (file_path, mtime, language)
            VALUES (?, ?, ?)
            ON CONFLICT(file_path) DO UPDATE SET mtime=excluded.mtime, language=excluded.language
            """,
            (file_path, mtime, language),
        )

    def get_indexed_files(self) -> Dict[str, tuple]:
        """Return {file_path: (mtime, language)} for all tracked files."""
        rows = self._conn.execute(
            "SELECT file_path, mtime, language FROM indexed_files"
        ).fetchall()
        return {r["file_path"]: (r["mtime"], r["language"]) for r in rows}

    def remove_file_data(self, file_path: str):
        """Delete all nodes and edges belonging to the given file_path.

        Edges are removed if *either* endpoint belongs to the file (to avoid
        dangling foreign-key references in the incremental graph).
        """
        node_ids = [
            r[0]
            for r in self._conn.execute(
                "SELECT id FROM nodes WHERE file_path=?", (file_path,)
            ).fetchall()
        ]
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            self._conn.execute(
                f"DELETE FROM edges WHERE source_id IN ({placeholders}) "
                f"OR target_id IN ({placeholders})",
                node_ids + node_ids,
            )
            self._conn.execute(
                f"DELETE FROM nodes WHERE id IN ({placeholders})",
                node_ids,
            )
        self._conn.execute(
            "DELETE FROM indexed_files WHERE file_path=?", (file_path,)
        )

    def clear_indexed_files(self):
        """Remove all indexed_files records (used before a full re-index)."""
        self._conn.execute("DELETE FROM indexed_files")
        self._conn.commit()

    def set_project_info(self, key: str, value: Any):
        self._conn.execute(
            "INSERT INTO project_info(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value))
        )
        self._conn.commit()

    def get_project_info(self, key: str) -> Any:
        row = self._conn.execute("SELECT value FROM project_info WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    # ------------------------------------------------------------------
    # Read — nodes
    # ------------------------------------------------------------------
    def _row_to_node(self, row) -> Node:
        return Node(
            id=row["id"], type=NodeType(row["type"]), name=row["name"],
            qualified_name=row["qualified_name"] or "",
            file_path=row["file_path"] or "",
            line_number=row["line_number"] or 0,
            package_name=row["package_name"] or "",
            language=row["language"] or "",
            is_abstract=bool(row["is_abstract"]),
            visibility=row["visibility"] or "public",
            annotations=json.loads(row["annotations"] or "[]"),
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def get_all_nodes(self) -> List[Node]:
        rows = self._conn.execute("SELECT * FROM nodes").fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_nodes_by_type(self, node_type: NodeType) -> List[Node]:
        rows = self._conn.execute("SELECT * FROM nodes WHERE type=?", (node_type.value,)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_node(self, node_id: str) -> Optional[Node]:
        row = self._conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        return self._row_to_node(row) if row else None

    def search_nodes(self, query: str, node_types: list[str] | None = None, limit: int = 50) -> List[Node]:
        """Ranked search: exact name → prefix → contains (name or qualified_name).
        
        Results are ranked:
        - Score 3: exact name match (case-insensitive)
        - Score 2: name starts with query
        - Score 1: name contains query or qualified_name contains query
        """
        q_lower = query.lower()
        type_filter = ""
        params_base: list = []
        if node_types:
            placeholders = ",".join("?" * len(node_types))
            type_filter = f" AND type IN ({placeholders})"
            params_base = list(node_types)

        # Pass 1: exact name match
        exact_rows = self._conn.execute(
            f"SELECT * FROM nodes WHERE lower(name)=?{type_filter}",
            [q_lower] + params_base
        ).fetchall()
        exact_ids = {r["id"] for r in exact_rows}

        # Pass 2: prefix match on name
        prefix_rows = self._conn.execute(
            f"SELECT * FROM nodes WHERE lower(name) LIKE ?{type_filter} AND id NOT IN ({','.join('?' * len(exact_ids)) or 'NULL'})",
            [q_lower + "%"] + params_base + list(exact_ids)
        ).fetchall() if len(exact_rows) < limit else []
        prefix_ids = {r["id"] for r in prefix_rows}

        # Pass 3: contains match (name or qualified_name)
        already_found = exact_ids | prefix_ids
        contains_rows = self._conn.execute(
            f"""SELECT * FROM nodes WHERE (lower(name) LIKE ? OR lower(qualified_name) LIKE ?){type_filter}
                AND id NOT IN ({','.join('?' * len(already_found)) or 'NULL'})
                LIMIT ?""",
            ["%" + q_lower + "%", "%" + q_lower + "%"] + params_base + list(already_found) + [limit]
        ).fetchall()

        all_rows = exact_rows + prefix_rows + contains_rows
        return [self._row_to_node(r) for r in all_rows[:limit]]


    # ------------------------------------------------------------------
    # Read — edges
    # ------------------------------------------------------------------
    def _row_to_edge(self, row) -> Edge:
        return Edge(
            id=row["id"], source_id=row["source_id"], target_id=row["target_id"],
            type=EdgeType(row["type"]), metadata=json.loads(row["metadata"] or "{}"),
        )

    def get_all_edges(self) -> List[Edge]:
        rows = self._conn.execute("SELECT * FROM edges").fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_edges_from(self, node_id: str) -> List[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE source_id=?", (node_id,)).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_edges_to(self, node_id: str) -> List[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE target_id=?", (node_id,)).fetchall()
        return [self._row_to_edge(r) for r in rows]

    def get_edges_by_type(self, edge_type: EdgeType) -> List[Edge]:
        rows = self._conn.execute("SELECT * FROM edges WHERE type=?", (edge_type.value,)).fetchall()
        return [self._row_to_edge(r) for r in rows]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    def get_stats(self) -> Dict[str, Any]:
        node_count = self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self._conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        type_rows = self._conn.execute(
            "SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type"
        ).fetchall()
        edge_rows = self._conn.execute(
            "SELECT type, COUNT(*) as cnt FROM edges GROUP BY type"
        ).fetchall()
        return {
            "node_count": node_count,
            "edge_count": edge_count,
            "nodes_by_type": {r["type"]: r["cnt"] for r in type_rows},
            "edges_by_type": {r["type"]: r["cnt"] for r in edge_rows},
        }


def get_db_path(project_path: str) -> str:
    return str(Path(project_path) / ".knode" / "graph.db")
