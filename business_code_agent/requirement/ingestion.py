"""Compatibility facade for the shared requirement document parser.

The parser is shared with code-evidence verification; keeping it outside the
requirement package avoids a bidirectional codeintel/requirement dependency.
"""

from __future__ import annotations

from business_code_agent.shared.docparse import (
    ParsedSection,
    RequirementDocumentParser,
)

__all__ = ["ParsedSection", "RequirementDocumentParser"]
