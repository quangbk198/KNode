from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class NodeType(str, Enum):
    PACKAGE = "Package"
    CLASS = "Class"
    ABSTRACT_CLASS = "AbstractClass"
    INTERFACE = "Interface"
    ENUM = "Enum"
    OBJECT = "Object"          # Kotlin object / companion object
    METHOD = "Method"
    FUNCTION = "Function"      # Kotlin top-level function
    FIELD = "Field"
    PROPERTY = "Property"      # Kotlin property


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    CALLS = "CALLS"
    USES = "USES"
    OVERRIDES = "OVERRIDES"
    INSTANTIATES = "INSTANTIATES"
    READS = "READS"   # property/field initializer reads a constant or property


@dataclass
class Node:
    id: str
    type: NodeType
    name: str
    qualified_name: str = ""
    file_path: str = ""
    line_number: int = 0
    package_name: str = ""
    language: str = ""          # "java" | "kotlin"
    is_abstract: bool = False
    visibility: str = "public"
    annotations: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type.value,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "package_name": self.package_name,
            "language": self.language,
            "is_abstract": self.is_abstract,
            "visibility": self.visibility,
            "annotations": self.annotations,
            "metadata": self.metadata,
        }


@dataclass
class Edge:
    id: str
    source_id: str
    target_id: str
    type: EdgeType
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "type": self.type.value,
            "metadata": self.metadata,
        }


@dataclass
class ProjectInfo:
    path: str
    name: str
    indexed_at: str
    node_count: int = 0
    edge_count: int = 0
    file_count: int = 0
    language_stats: Dict[str, int] = field(default_factory=dict)
