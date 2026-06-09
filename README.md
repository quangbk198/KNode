# KNode

<div align="center">

  <img src="assets/logo.png" alt="KNode Logo" width="320" height="320"/>

  <h2>Visual code intelligence for Android codebases.</h2>

  <a href="https://www.python.org/">
    <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python Version"/>
  </a>
  <a href="https://kotlinlang.org/">
    <img src="https://img.shields.io/badge/Language-Kotlin%20%2F%20Java-orange.svg" alt="Languages"/>
  </a>
  <a href="https://modelcontextprotocol.io/">
    <img src="https://img.shields.io/badge/MCP-Supported-green.svg" alt="MCP Supported"/>
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/>
  </a>

  <p><strong>Building the nervous system for Android AI agents.</strong></p>

</div>

KNode indexes your entire Android codebase — every class, method, field, dependency, and call chain — into a high-performance knowledge graph. It then exposes this intelligence through smart tools so AI agents (Cursor, Claude Code, etc.) stop missing context, breaking call chains, and shipping blind edits.

> *Like a magnifying glass for your Android architecture.* KNode helps you *visualize* and *analyze* your code through a relational lens that tracks every connection, not just text.

**TL;DR:** The **Graph Browser** is a visual way to explore any Android repo. The **CLI + MCP** is how you make your AI agent actually reliable — it gives your coding assistant a deep architectural view of your Kotlin/Java code so it stays aware of inheritance, interface implementations, and blast radius.

![KNode Graph UI](assets/screen_shot_graph_ui.png)

---

## Two Ways to Use KNode

|                   | **CLI + MCP (Recommended)**                                            | **Graph Browser (Web UI)**                                             |
| ----------------- | -------------------------------------------------------------- | ------------------------------------------------------------ |
| **What**    | Index repos locally, connect AI agents via MCP                 | Visual graph explorer + code navigation in browser           |
| **For**     | Daily development with Cursor, Claude Code, Windsurf           | Quick exploration, architectural audits, visual tracing      |
| **Scale**   | Full repos, any size                                           | Visual-focused, handles thousands of nodes via WebGL         |
| **Install** | `pip install -e .`                                    | Included in CLI (`KNode serve`)                          |
| **Storage** | SQLite + Global Registry (Persistent)                          | Served via FastAPI backend                                   |
| **Parsing** | AST-based indexing (Java/Kotlin)                               | Dynamic data fetching from graph                             |
| **Privacy** | Everything local, no network                                   | Everything local, no server outside your machine             |

---

## Quick Start

### 1. Installation

**Prerequisites:**
- **Python 3.10+** installed on your system.

```bash
# Clone the repository
git clone https://github.com/quangbk198/KNode.git
cd KNode

# Install in editable mode
pip install -e .
```

### 2. Index your Android project

Run this from your Android project root (or provide the path):

```bash
python -m KNode index .
```

> [!TIP]
> After running `pip install -e .`, you can also use the shorter `KNode` command directly if your Python Scripts folder is in your system `PATH`.

This command parses your code, builds the SQLite graph, registers the project in the global registry (`~/.KNode/registry.json`), and scaffolds agent-specific files (`AGENTS.md`).

### 3. Explore Visually

```bash
python -m KNode serve
```

Launches the interactive **Graph Browser** at `http://localhost:7070`.

---

## AI Agent Integration (MCP)

KNode runs a standard **Model Context Protocol (MCP)** server. This allows it to integrate with any AI editor or agent that supports the MCP standard.

### Supported Tools & Editors

KNode works out-of-the-box with:
- **Cursor**
- **Claude Code**
- **Antigravity**
- **Windsurf**
- **Claude Desktop**
- **Codex**
- **Any other MCP-compatible client**

### Universal Configuration

Most editors can be configured by adding KNode to your `mcp.json` or equivalent configuration file:

```json
{
  "mcpServers": {
    "KNode": {
      "command": "python",
      "args": ["-m", "KNode", "mcp"]
    }
  }
}
```

**Claude Code**:

```bash
claude mcp add KNode -- python -m KNode mcp
```

---

## CLI Commands Reference

| Command | Description |
|---|---|
| `python -m KNode index [path]` | Index an Android project (or update stale index) |
| `python -m KNode serve [path]` | Launch the interactive graph browser UI |
| `python -m KNode mcp` | Start MCP stdio server (serves all indexed repos) |
| `python -m KNode stats [path]` | Print graph statistics (nodes, edges, types) |
| `python -m KNode list` | List all indexed projects in the global registry |
| `python -m KNode clean [path]` | Delete index for a specific project |
| `python -m KNode clean --all` | Delete all indexes and clear the registry |

---

## What Your AI Agent Gets

**14 tools** exposed via MCP for deep codebase analysis:

| Tool | Description |
|---|---|
| `get_stats` | Check index freshness and node/edge counts |
| `search_nodes` | Find classes, methods, fields by name (fuzzy/type-aware) |
| `get_class_info` | Detailed view of a class (members, hierarchy, interfaces) |
| `get_node` | Inspect a single node (file path, line numbers, docs) |
| `get_neighbors` | Immediate callers, callees, and relationships |
| `get_call_chain` | Trace a full execution flow between two points |
| `impact` | Blast radius analysis (what breaks if I change this?) |
| `find_usages` | All references/calls of a method or class |
| `get_dependencies` | Class-level dependency analysis |
| `list_classes` | Catalog all classes in a package or project |
| `index_project` | Re-index a project on-demand via the agent |
| `sql_query` | Run custom SQLite queries against the knowledge graph |
| `list_projects` | Discover all indexed repositories |
| `switch_project` | Dynamically switch context between projects |

---

## How It Works

KNode uses a multi-phase indexing pipeline to build a structural map of your Android app:

```mermaid
graph TD
    A[Android Source Code] --> B{Indexer}
    B --> C[Java/Kotlin AST Parsing]
    C --> D[Symbol Resolution]
    D --> E[Relationship Mapping]
    E --> F[(SQLite Graph DB)]
    F --> G[MCP Server]
    F --> H[Graph Browser UI]
    G --> I[AI Agent]
```

1.  **Parsing**: Extracts every class, method, interface, and field using language-specific AST visitors.
2.  **Resolution**: Maps interface implementations, class inheritance, and method calls across the entire project.
3.  **Storage**: Builds a high-performance SQLite database stored locally in `.KNode/graph.db`.
4.  **Global Registry**: Centralizes all indexed projects so your AI agent can switch repos without reconfiguration.

---

## The Problem KNode Solves

Tools like **Cursor** and **Claude** are powerful, but they struggle with large-scale Android architectures. They often:
1.  Edit a method without knowing it's an interface override with 10 implementations.
2.  Miss usages of a constant that is accessed via static imports.
3.  Fail to trace a dependency injection chain.

**KNode provides precomputed structural intelligence.** Instead of the LLM guessing relationships from raw text, it queries a verified graph that knows exactly how your code hangs together.

---

## Tech Stack

-   **Backend**: Python 3.10+, FastAPI, Uvicorn
-   **Database**: SQLite (local, file-based)
-   **Parsing**: AST-based visitors for Java and Kotlin
-   **Frontend**: Force-Graph (WebGL), Canvas, Vanilla JS
-   **Agent Protocol**: Model Context Protocol (MCP)

---

## Security & Privacy

-   **Local First**: Everything runs on your machine. No code is ever uploaded to a server.
-   **Transparent**: The graph database is a standard SQLite file you can inspect yourself.
-   **Open Source**: Audit the code and the indexing logic.

---

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=quangbk198/KNodeGraph&type=Date)](https://star-history.com/#quangbk198/KNodeGraph&Date)

---

## Acknowledgments

-   [Tree-sitter](https://tree-sitter.github.io/) (for parsing inspirations)
-   [Force-Graph](https://github.com/vasturiano/force-graph) (for the high-performance visualization)
-   [MCP](https://modelcontextprotocol.io/) (for the agent communication standard)
