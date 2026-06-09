"""
Kotlin source parser using tree-sitter.
Extracts: packages, classes, interfaces, objects, functions, properties,
inheritance (delegation_specifiers), and call expressions.
Also tracks property initializer references (READS edges) to constants and
properties from other objects/classes (e.g. Constants.SHARE_PREFERENCE_NAME).
"""
from pathlib import Path
from typing import List, Tuple, Optional
import hashlib

import tree_sitter_kotlin as tskotlin
from tree_sitter import Language, Parser, Node as TSNode

from knode.graph.models import Node, Edge, NodeType, EdgeType

KOTLIN_LANGUAGE = Language(tskotlin.language())
_parser = Parser(KOTLIN_LANGUAGE)


def _node_text(node: TSNode, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _uid(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _get_package(root: TSNode, src: bytes) -> str:
    for child in root.children:
        if child.type == "package_header":
            # Find the qualified_identifier child
            for sub in child.children:
                if sub.type == "qualified_identifier":
                    return _node_text(sub, src).strip()
            # Fallback: strip 'package' keyword from raw text
            text = _node_text(child, src)
            return text.removeprefix("package").strip()
    return ""


def _get_visibility(node: TSNode, src: bytes) -> str:
    for child in node.children:
        if child.type == "modifiers":
            text = _node_text(child, src)
            if "public" in text:
                return "public"
            if "private" in text:
                return "private"
            if "protected" in text:
                return "protected"
            if "internal" in text:
                return "internal"
    return "public"


class KotlinFileParser:
    def __init__(self, file_path: str, src: bytes):
        self.file_path = file_path
        self.src = src
        self.package = ""
        self.nodes: List[Node] = []
        self.edges: List[Edge] = []

    def _add_node(self, node: Node):
        self.nodes.append(node)

    def _add_edge(self, source_id: str, target_id: str, etype: EdgeType, meta=None):
        eid = _uid(source_id, target_id, etype.value)
        meta = meta or {}
        
        for existing in self.edges:
            if existing.id == eid:
                if "line" in meta:
                    if "lines" not in existing.metadata:
                        if "line" in existing.metadata:
                            existing.metadata["lines"] = [existing.metadata["line"]]
                        else:
                            existing.metadata["lines"] = []
                    if meta["line"] not in existing.metadata["lines"]:
                        existing.metadata["lines"].append(meta["line"])
                    existing.metadata["line"] = meta["line"]
                return

        if "line" in meta:
            meta["lines"] = [meta["line"]]

        self.edges.append(Edge(id=eid, source_id=source_id, target_id=target_id,
                               type=etype, metadata=meta))

    def _get_node(self, nid: str) -> Optional[Node]:
        return next((n for n in self.nodes if n.id == nid), None)

    # ------------------------------------------------------------------ #
    def parse(self, root: TSNode):
        self.package = _get_package(root, self.src)
        for child in root.children:
            if child.type in ("class_declaration", "object_declaration"):
                self._parse_class(child, parent_id=None)
            elif child.type == "function_declaration":
                self._parse_function(child, parent_id=None)
            elif child.type == "property_declaration":
                self._parse_property(child, parent_id=None)

    # ------------------------------------------------------------------ #
    def _parse_class(self, node: TSNode, parent_id: Optional[str]):
        # Determine kind: class / interface / object / enum class
        kind = "class"
        is_abstract = False
        if node.type == "object_declaration":
            kind = "object"
            
        for child in node.children:
            if child.type == "interface":
                kind = "interface"
            if child.type == "modifiers":
                mod_text = _node_text(child, self.src)
                if "abstract" in mod_text:
                    is_abstract = True
                if "enum" in mod_text:
                    kind = "enum"
            if child.type == "object":
                kind = "object"

        # Name — tree-sitter-kotlin uses 'identifier' (not 'type_identifier') for class names
        name = ""
        for child in node.children:
            if child.type in ("type_identifier", "identifier"):
                name = _node_text(child, self.src)
                break
        if not name:
            return

        qname = f"{self.package}.{name}" if self.package else name

        if kind == "interface":
            ntype = NodeType.INTERFACE
        elif kind == "enum":
            ntype = NodeType.ENUM
        elif kind == "object":
            ntype = NodeType.OBJECT
        elif is_abstract:
            ntype = NodeType.ABSTRACT_CLASS
        else:
            ntype = NodeType.CLASS

        visibility = _get_visibility(node, self.src)
        nid = _uid(qname, self.file_path)
        n = Node(
            id=nid, type=ntype, name=name, qualified_name=qname,
            file_path=self.file_path, line_number=node.start_point[0] + 1,
            package_name=self.package, language="kotlin",
            is_abstract=is_abstract, visibility=visibility,
        )
        self._add_node(n)

        if parent_id:
            self._add_edge(parent_id, nid, EdgeType.CONTAINS)

        # --- Delegation specifiers (superclass / interfaces) ---
        for child in node.children:
            if child.type == "delegation_specifiers":
                self._parse_delegation(child, nid)

        # --- Body ---
        for child in node.children:
            if child.type == "class_body":
                self._parse_class_body(child, nid)
            elif child.type == "enum_class_body":
                self._parse_class_body(child, nid)

    def _parse_delegation(self, node: TSNode, class_id: str):
        """Extract superclass and implemented interfaces from delegation_specifiers."""
        # Each child is either a constructor_invocation (class extends) or a
        # user_type / type_identifier (interface implements)
        for child in node.children:
            if child.type in (",",):
                continue
            self._collect_type_refs(child, class_id)

    def _collect_type_refs(self, node: TSNode, class_id: str):
        # tree-sitter-kotlin uses 'type_identifier' OR plain 'identifier' for type names
        if node.type in ("type_identifier", "identifier") and node.parent and node.parent.type not in (
            "function_declaration", "property_declaration", "variable_declaration",
            "simple_identifier",
        ):
            type_name = _node_text(node, self.src).strip()
            if not type_name or type_name in (":", ",", "(", ")"):
                return
            tid = _uid(type_name, "type_ref")
            if not self._get_node(tid):
                self.nodes.append(Node(id=tid, type=NodeType.CLASS, name=type_name,
                                       qualified_name=type_name, language="kotlin"))
            android_base = any(type_name.endswith(s) for s in
                               ("Activity", "Fragment", "ViewModel", "Service",
                                "Receiver", "Provider", "Application", "View",
                                "Adapter", "ViewHolder", "Dialog", "Worker"))
            etype = EdgeType.EXTENDS if android_base else EdgeType.IMPLEMENTS
            self._add_edge(class_id, tid, etype)
        else:
            for child in node.children:
                self._collect_type_refs(child, class_id)

    def _parse_class_body(self, body: TSNode, parent_id: str):
        for child in body.children:
            if child.type in ("class_declaration", "object_declaration"):
                self._parse_class(child, parent_id)
            elif child.type == "function_declaration":
                self._parse_function(child, parent_id)
            elif child.type == "property_declaration":
                self._parse_property(child, parent_id)
            elif child.type == "companion_object":
                self._parse_companion(child, parent_id)

    def _parse_companion(self, node: TSNode, parent_id: str):
        parent = self._get_node(parent_id)
        comp_name = f"{parent.name if parent else 'Unknown'}.Companion"
        qname = f"{parent.qualified_name}.Companion" if parent else comp_name
        nid = _uid(qname, self.file_path)
        n = Node(
            id=nid, type=NodeType.OBJECT, name="Companion", qualified_name=qname,
            file_path=self.file_path, line_number=node.start_point[0] + 1,
            package_name=self.package, language="kotlin",
        )
        self._add_node(n)
        self._add_edge(parent_id, nid, EdgeType.CONTAINS)
        for child in node.children:
            if child.type == "class_body":
                self._parse_class_body(child, nid)

    def _parse_function(self, node: TSNode, parent_id: Optional[str]):
        name = ""
        for child in node.children:
            # tree-sitter-kotlin uses 'simple_identifier' OR 'identifier' for function names
            if child.type in ("simple_identifier", "identifier"):
                name = _node_text(child, self.src)
                break
        if not name:
            return

        parent = self._get_node(parent_id) if parent_id else None
        qname = f"{parent.qualified_name}.{name}" if parent else name
        visibility = _get_visibility(node, self.src)
        fid = _uid(qname, self.file_path, node.start_point[0])
        f = Node(
            id=fid, type=NodeType.METHOD, name=name, qualified_name=qname,
            file_path=self.file_path, line_number=node.start_point[0] + 1,
            package_name=self.package, language="kotlin", visibility=visibility,
        )
        self._add_node(f)
        if parent_id:
            self._add_edge(parent_id, fid, EdgeType.CONTAINS)

        # Collect calls inside function body
        for child in node.children:
            if child.type == "function_body":
                self._collect_calls(child, fid)

    def _parse_property(self, node: TSNode, parent_id: Optional[str]):
        name = ""
        for child in node.children:
            if child.type == "variable_declaration":
                for sub in child.children:
                    if sub.type in ("simple_identifier", "identifier"):
                        name = _node_text(sub, self.src)
                        break
            elif child.type in ("simple_identifier", "identifier") and not name:
                # Fallback: some property_declarations don't have variable_declaration
                name = _node_text(child, self.src)
            if name:
                break
        if not name:
            return

        parent = self._get_node(parent_id) if parent_id else None
        qname = f"{parent.qualified_name}.{name}" if parent else name
        visibility = _get_visibility(node, self.src)
        pid = _uid(qname, self.file_path, node.start_point[0])
        p = Node(
            id=pid, type=NodeType.PROPERTY, name=name, qualified_name=qname,
            file_path=self.file_path, line_number=node.start_point[0] + 1,
            package_name=self.package, language="kotlin", visibility=visibility,
        )
        self._add_node(p)
        if parent_id:
            self._add_edge(parent_id, pid, EdgeType.CONTAINS)

        # Scan the initializer expression for READS edges to constants/properties
        self._collect_property_reads(node, pid)

    def _collect_property_reads(self, prop_node: TSNode, prop_id: str):
        """Walk the property initializer and emit READS edges for
        navigation expressions of the form  Qualifier.member  that reference
        constants or properties defined on another class/object.

        Tree-sitter-kotlin represents  Constants.SHARE_PREFERENCE_NAME  as:
            navigation_expression
              ├─ simple_identifier  "Constants"       (the qualifier)
              └─ navigation_suffix
                  └─ simple_identifier  "SHARE_PREFERENCE_NAME"  (the member)
        """
        # Locate the initializer — it follows the '=' token in the property_declaration
        initializer = None
        found_eq = False
        for child in prop_node.children:
            if found_eq:
                initializer = child
                break
            if child.type == "=" or _node_text(child, self.src) == "=":
                found_eq = True

        if initializer is None:
            return

        self._walk_reads(initializer, prop_id)

    def _walk_reads(self, node: TSNode, prop_id: str):
        """Recursively scan an expression subtree and emit READS edges for
        every  Qualifier.MEMBER  navigation expression that is a pure property
        or constant access (not a method call callee).

        Skips navigation_expressions that are the callee of a call_expression
        (e.g. ``context.getSharedPreferences(...)``).
        """
        if node.type == "navigation_expression":
            # Skip if this navigation_expression is the callee of a call_expression
            is_method_callee = (
                node.parent is not None
                and node.parent.type == "call_expression"
                and node.parent.children
                and node.parent.children[0].id == node.id
            )
            if not is_method_callee:
                qualifier, member = self._extract_nav_parts(node)
                if qualifier and member:
                    ref_qname = f"{qualifier}.{member}"
                    ref_id = _uid(ref_qname, "property_ref")
                    if not self._get_node(ref_id):
                        self.nodes.append(Node(
                            id=ref_id, type=NodeType.PROPERTY,
                            name=member, qualified_name=ref_qname,
                            language="kotlin",
                        ))
                    self._add_edge(prop_id, ref_id, EdgeType.READS,
                                   {"line": node.start_point[0] + 1,
                                    "qualifier": qualifier})
            # Do NOT recurse into children of this navigation_expression.
            return
        # Recurse into children for all other node types
        for child in node.children:
            self._walk_reads(child, prop_id)


    def _extract_nav_parts(self, nav_node: TSNode) -> tuple:
        """Return (qualifier, member) for a navigation_expression node,
        or (None, None) if the structure doesn't qualify.

        tree-sitter-kotlin produces two possible AST shapes:

        Shape A (flat identifiers):
            navigation_expression
              identifier  "Constants"
              .
              identifier  "SHARE_PREFERENCE_NAME"

        Shape B (navigation_suffix):
            navigation_expression
              simple_identifier  "Constants"
              navigation_suffix
                simple_identifier  "SHARE_PREFERENCE_NAME"

        Rules
        -----
        - Only handle simple two-part expressions (Qualifier.MEMBER).
        - The qualifier MUST start with an uppercase letter — this filters out
          instance-variable access like ``throwable.message`` or
          ``_state.postValue`` where the qualifier is a local variable/field.
          Uppercase qualifiers indicate class names or singleton objects
          (e.g. Constants, BuildConfig, Context, ActivityResultContracts).
        """
        identifiers = []

        for child in nav_node.children:
            if child.type == "navigation_suffix":
                for sub in child.children:
                    if sub.type in ("simple_identifier", "identifier"):
                        identifiers.append(_node_text(sub, self.src).strip())
            elif child.type in ("simple_identifier", "identifier"):
                identifiers.append(_node_text(child, self.src).strip())
            # skip "." and other punctuation

        # Only handle simple two-part expressions (Qualifier.MEMBER)
        if len(identifiers) == 2:
            qualifier, member = identifiers[0], identifiers[1]
            # Qualifier must be a class/object name (starts with uppercase)
            if qualifier and qualifier[0].isupper():
                return qualifier, member
        return None, None





    def _collect_calls(self, node: TSNode, caller_id: str):
        if node.type == "call_expression":
            # ── Emit CALLS edge for the callee ──────────────────────────────
            first = node.children[0] if node.children else None
            if first:
                callee_name = ""
                qualifier_name = ""  # e.g. "loginRepo" in loginRepo.getMerchant()
                if first.type in ("simple_identifier", "identifier"):
                    callee_name = _node_text(first, self.src)
                elif first.type == "navigation_expression":
                    # e.g. loginRepo.getMerchant() — capture qualifier + method name
                    nav_children = first.children
                    # Last identifier is the method name
                    for sub in reversed(nav_children):
                        if sub.type in ("simple_identifier", "identifier"):
                            callee_name = _node_text(sub, self.src)
                            break
                    # First identifier is the qualifier (receiver)
                    for sub in nav_children:
                        if sub.type in ("simple_identifier", "identifier"):
                            qualifier_name = _node_text(sub, self.src)
                            break
                if callee_name:
                    cid = _uid(callee_name, "method_ref")
                    if not self._get_node(cid):
                        self.nodes.append(Node(id=cid, type=NodeType.METHOD,
                                               name=callee_name, qualified_name=callee_name,
                                               language="kotlin"))
                    meta = {"line": node.start_point[0] + 1}
                    if qualifier_name and qualifier_name != callee_name:
                        meta["qualifier"] = qualifier_name
                    self._add_edge(caller_id, cid, EdgeType.CALLS, meta)

        elif node.type == "navigation_expression":
            # ── Emit READS edge for Qualifier.MEMBER constant/property access ──
            # Only when this expression is NOT the callee of a call_expression.
            # The callee is always children[0] of the enclosing call_expression.
            is_method_callee = (
                node.parent is not None
                and node.parent.type == "call_expression"
                and node.parent.children
                and node.parent.children[0].id == node.id
            )
            if not is_method_callee:
                qualifier, member = self._extract_nav_parts(node)
                if qualifier and member:
                    ref_qname = f"{qualifier}.{member}"
                    ref_id = _uid(ref_qname, "property_ref")
                    if not self._get_node(ref_id):
                        self.nodes.append(Node(
                            id=ref_id, type=NodeType.PROPERTY,
                            name=member, qualified_name=ref_qname,
                            language="kotlin",
                        ))
                    self._add_edge(caller_id, ref_id, EdgeType.READS,
                                   {"line": node.start_point[0] + 1,
                                    "qualifier": qualifier})
            # We must recurse because navigation_expression can contain call_expression!

        for child in node.children:
            self._collect_calls(child, caller_id)



def parse_kotlin_file(file_path: str) -> Tuple[List[Node], List[Edge]]:
    src = Path(file_path).read_bytes()
    tree = _parser.parse(src)
    fp = KotlinFileParser(file_path, src)
    fp.parse(tree.root_node)

    # ── Removed aggressive intra-file method call resolution ──
    # Leaving abstract nodes intact allows _resolve_cross_file_calls to link to ALL matching methods across the project.

    # ── Intra-file property/constant READS resolution ─────────────────────
    # Build a lookup: qualified_name → node.id for every concrete Property in this file
    concrete_props_by_qname = {
        n.qualified_name: n.id
        for n in fp.nodes
        if n.type == NodeType.PROPERTY and n.file_path
    }
    # property_ref placeholders have no file_path
    prop_refs = {n.id: n for n in fp.nodes if n.type == NodeType.PROPERTY and not n.file_path}

    for edge in fp.edges:
        if edge.type == EdgeType.READS and edge.target_id in prop_refs:
            ref_qname = prop_refs[edge.target_id].qualified_name
            if ref_qname in concrete_props_by_qname:
                edge.target_id = concrete_props_by_qname[ref_qname]

    # ── Clean up unreferenced placeholder nodes ────────────────────────────
    used_nodes = {e.target_id for e in fp.edges} | {e.source_id for e in fp.edges}
    fp.nodes = [n for n in fp.nodes if n.file_path or n.id in used_nodes]

    return fp.nodes, fp.edges
