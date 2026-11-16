"""Compatibility imports for evaluation callers."""

from business_code_agent.codeintel.ast.generator import (
    MAX_DOC_CHARS,
    MAX_DOC_LINES,
    generate_ast_documents,
    _split,
)

__all__ = ["generate_ast_documents", "MAX_DOC_LINES", "MAX_DOC_CHARS"]
