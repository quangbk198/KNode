"""
Java source parser using tree-sitter.
Extracts: packages, classes, interfaces, enums, methods, fields,
inheritance (extends/implements), and method call edges.
"""
from pathlib import Path
from typing import List, Tuple, Optional
import hashlib

import tree_sitter_java as tsjava
from tree_sitter import Language, Parser, Node as TSNode

from knode.graph.models import Node, Edge, NodeType, EdgeType

JAVA_LANGUAGE = Language(tsjava.language())
_parser = Parser(JAVA_LANGUAGE)


def _node_text(node: TSNode, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()


def _child_by_field(node: TSNode, field: str) -> Optional[TSNode]:
    return node.child_by_field_name(field)


def _uid(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def _get_package(root: TSNode, src: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            # e.g. "package com.example.app;"
            text = _node_text(child, src)
            return text.removeprefix("package").removesuffix(";").strip()
    return ""


def _get_modifiers(node: TSNode, src: bytes) -> Tuple[str, bool]:
    """Return (visibility, is_abstract) from a modifiers node."""
    visibility = "package"
    is_abstract = False
    for child in node.children:
        if child.type == "modifiers":
            mod_text = _node_text(child, src)
            if "public" in mod_text:
                visibility = "public"
            elif "protected" in mod_text:
                visibility = "protected"
            elif "private" in mod_text:
                visibility = "private"
            is_abstract = "abstract" in mod_text
    return visibility, is_abstract


def _extract_type_name(node: TSNode, src: bytes) -> str:
    """Get simple type name from a type node."""
    if node is None:
        return ""
    text = _node_text(node, src)
    # Strip generics: Map<String, Integer> → Map
    return text.split("<")[0].split(".")[-1].strip()


class JavaFileParser:
    def __init__(self, file_path: str, src: bytes, package: str = ""):
        self.file_path = file_path
        self.src = src
        self.package = package
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

    # ------------------------------------------------------------------ #
    # Top-level dispatcher
    # ------------------------------------------------------------------ #
    def parse(self, root: TSNode):
        self.package = _get_package(root, self.src)
        for child in root.children:
            if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                self._parse_type(child, parent_id=None)

    def _parse_type(self, node: TSNode, parent_id: Optional[str]):
        ntype_str = node.type  # class_declaration | interface_declaration | enum_declaration
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.src)
        qname = f"{self.package}.{name}" if self.package else name

        visibility, is_abstract = _get_modifiers(node, self.src)

        if ntype_str == "interface_declaration":
            ntype = NodeType.INTERFACE
        elif ntype_str == "enum_declaration":
            ntype = NodeType.ENUM
        elif is_abstract:
            ntype = NodeType.ABSTRACT_CLASS
        else:
            ntype = NodeType.CLASS

        nid = _uid(qname, self.file_path)
        n = Node(
            id=nid, type=ntype, name=name, qualified_name=qname,
            file_path=self.file_path, line_number=node.start_point[0] + 1,
            package_name=self.package, language="java",
            is_abstract=is_abstract, visibility=visibility,
        )
        self._add_node(n)

        if parent_id:
            self._add_edge(parent_id, nid, EdgeType.CONTAINS)

        # --- Superclass (extends) ---
        superclass_node = node.child_by_field_name("superclass")
        if superclass_node:
            for sc in superclass_node.children:
                sc_name = _extract_type_name(sc, self.src)
                if sc_name:
                    sc_id = _uid(sc_name, "type_ref")
                    if not any(x.id == sc_id for x in self.nodes):
                        self.nodes.append(Node(id=sc_id, type=NodeType.CLASS, name=sc_name,
                                               qualified_name=sc_name, language="java"))
                    self._add_edge(nid, sc_id, EdgeType.EXTENDS)

        # --- Interfaces (implements / extends for interfaces) ---
        for field_name in ("super_interfaces", "extends_interfaces"):
            iface_node = node.child_by_field_name(field_name)
            if iface_node:
                self._collect_interface_refs(nid, iface_node)

        # --- Body ---
        body_node = node.child_by_field_name("body")
        if body_node:
            self._parse_body(body_node, nid)

    def _collect_interface_refs(self, class_id: str, node: TSNode):
        for child in node.children:
            if child.type == "type_identifier":
                iname = _node_text(child, self.src)
                iid = _uid(iname, "type_ref")
                if not any(x.id == iid for x in self.nodes):
                    self.nodes.append(Node(id=iid, type=NodeType.INTERFACE, name=iname,
                                           qualified_name=iname, language="java"))
                self._add_edge(class_id, iid, EdgeType.IMPLEMENTS)
            else:
                self._collect_interface_refs(class_id, child)

    def _parse_body(self, body: TSNode, parent_id: str):
        for child in body.children:
            if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
                self._parse_type(child, parent_id)
            elif child.type == "method_declaration":
                self._parse_method(child, parent_id)
            elif child.type == "constructor_declaration":
                self._parse_constructor(child, parent_id)
            elif child.type == "field_declaration":
                self._parse_field(child, parent_id)

    def _parse_method(self, node: TSNode, class_id: str):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.src)
        visibility, is_abstract = _get_modifiers(node, self.src)
        parent_node = self._get_node(class_id)
        qname = f"{parent_node.qualified_name}.{name}" if parent_node else name
        mid = _uid(qname, self.file_path, node.start_point[0])
        m = Node(
            id=mid, type=NodeType.METHOD, name=name, qualified_name=qname,
            file_path=self.file_path, line_number=node.start_point[0] + 1,
            package_name=self.package, language="java",
            visibility=visibility,
        )
        self._add_node(m)
        self._add_edge(class_id, mid, EdgeType.CONTAINS)

        # Parse method body for calls
        body = node.child_by_field_name("body")
        if body:
            self._collect_calls(body, mid)

    def _parse_constructor(self, node: TSNode, class_id: str):
        name_node = node.child_by_field_name("name")
        if not name_node:
            return
        name = _node_text(name_node, self.src)
        parent_node = self._get_node(class_id)
        qname = f"{parent_node.qualified_name}.<init>" if parent_node else f"{name}.<init>"
        mid = _uid(qname, self.file_path, node.start_point[0])
        m = Node(
            id=mid, type=NodeType.METHOD, name=f"{name}(constructor)",
            qualified_name=qname, file_path=self.file_path,
            line_number=node.start_point[0] + 1, package_name=self.package, language="java",
        )
        self._add_node(m)
        self._add_edge(class_id, mid, EdgeType.CONTAINS)
        body = node.child_by_field_name("body")
        if body:
            self._collect_calls(body, mid)

    def _parse_field(self, node: TSNode, class_id: str):
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = _node_text(name_node, self.src)
                parent_node = self._get_node(class_id)
                qname = f"{parent_node.qualified_name}.{name}" if parent_node else name
                fid = _uid(qname, self.file_path, node.start_point[0])
                visibility, _ = _get_modifiers(node, self.src)
                f = Node(
                    id=fid, type=NodeType.FIELD, name=name, qualified_name=qname,
                    file_path=self.file_path, line_number=node.start_point[0] + 1,
                    package_name=self.package, language="java", visibility=visibility,
                )
                self._add_node(f)
                self._add_edge(class_id, fid, EdgeType.CONTAINS)

                # Scan the initializer for READS edges (e.g. Constants.SOME_CONST)
                init_node = child.child_by_field_name("value")
                if init_node:
                    self._walk_reads_java(init_node, fid)

    def _walk_reads_java(self, node: TSNode, field_id: str):
        """Recursively scan a Java expression and emit READS edges for
        field_access nodes of the form  Qualifier.MEMBER  (e.g. Constants.KEY)."""
        if node.type == "field_access":
            qualifier, member = self._extract_field_access(node)
            if qualifier and member:
                ref_qname = f"{qualifier}.{member}"
                ref_id = _uid(ref_qname, "property_ref")
                if not any(x.id == ref_id for x in self.nodes):
                    self.nodes.append(Node(
                        id=ref_id, type=NodeType.FIELD,
                        name=member, qualified_name=ref_qname,
                        language="java",
                    ))
                self._add_edge(field_id, ref_id, EdgeType.READS,
                               {"line": node.start_point[0] + 1,
                                "qualifier": qualifier})
        for child in node.children:
            self._walk_reads_java(child, field_id)

    def _extract_field_access(self, node: TSNode) -> tuple:
        """Return (qualifier, member) for a Java field_access node."""
        obj_node   = node.child_by_field_name("object")
        field_node = node.child_by_field_name("field")
        if obj_node and field_node:
            return _node_text(obj_node, self.src).strip(), _node_text(field_node, self.src).strip()
        return None, None




    def _collect_calls(self, node: TSNode, caller_id: str):
        """Recursively walk a block and emit CALLS + READS edges."""
        if node.type == "method_invocation":
            # ── Emit CALLS edge for the method name ─────────────────────────
            name_node = node.child_by_field_name("name")
            if name_node:
                callee_name = _node_text(name_node, self.src)
                callee_id = _uid(callee_name, "method_ref")
                if not any(x.id == callee_id for x in self.nodes):
                    self.nodes.append(Node(id=callee_id, type=NodeType.METHOD,
                                           name=callee_name, qualified_name=callee_name,
                                           language="java"))
                self._add_edge(caller_id, callee_id, EdgeType.CALLS,
                               {"line": node.start_point[0] + 1})

        elif node.type == "field_access":
            # ── Emit READS edge for ClassName.CONSTANT access ────────────────
            # Skip if this field_access is the "object" of a method_invocation
            # (e.g. SomeClass.INSTANCE.method() — the outer chain, not a value read).
            is_method_object = (
                node.parent is not None
                and node.parent.type == "method_invocation"
                and node.parent.child_by_field_name("object") is not None
                and node.parent.child_by_field_name("object").id == node.id
            )
            if not is_method_object:
                qualifier, member = self._extract_field_access(node)
                if qualifier and member:
                    ref_qname = f"{qualifier}.{member}"
                    ref_id = _uid(ref_qname, "property_ref")
                    if not any(x.id == ref_id for x in self.nodes):
                        self.nodes.append(Node(
                            id=ref_id, type=NodeType.FIELD,
                            name=member, qualified_name=ref_qname,
                            language="java",
                        ))
                    self._add_edge(caller_id, ref_id, EdgeType.READS,
                                   {"line": node.start_point[0] + 1,
                                    "qualifier": qualifier})
            # Do NOT recurse into field_access children
            return

        for child in node.children:
            self._collect_calls(child, caller_id)



    def _get_node(self, nid: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == nid:
                return n
        return None


def parse_java_file(file_path: str) -> Tuple[List[Node], List[Edge]]:
    src = Path(file_path).read_bytes()
    tree = _parser.parse(src)
    fp = JavaFileParser(file_path, src)
    fp.parse(tree.root_node)

    # ── Removed aggressive intra-file method call resolution ──

    # Remove unreferenced method_refs to keep graph clean
    used_nodes = {e.target_id for e in fp.edges} | {e.source_id for e in fp.edges}
    fp.nodes = [n for n in fp.nodes if n.file_path or n.id in used_nodes]

    return fp.nodes, fp.edges
