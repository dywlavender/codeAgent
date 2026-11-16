"""Compatibility imports for the versioned AST material view."""

from business_code_agent.codeintel.ast.material import (
    STAMP_CACHE_VERSION,
    STAMP_IGNORED_PARTS,
    AstMaterial,
    current_stamp,
    source_stamp,
)

__all__ = ["AstMaterial", "current_stamp", "source_stamp",
           "STAMP_CACHE_VERSION", "STAMP_IGNORED_PARTS"]
