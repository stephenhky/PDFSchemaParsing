"""pdfschemaparsing: Resilient schema-driven PDF and Markdown section parser."""

from pdfschemaparsing.normalizer import (
    canonicalize_header,
    clean_inner_text,
    normalize_unicode_and_whitespace,
    strip_markdown_header_syntax,
)
from pdfschemaparsing.parser import (
    ParsedSection,
    ParseResult,
    SchemaParser,
    parse_markdown,
    parse_pdf,
)
from pdfschemaparsing.schema import (
    DocumentSchema,
    MissingRequiredSectionError,
    SchemaValidationError,
    SectionSpec,
)

__all__ = [
    "DocumentSchema",
    "MissingRequiredSectionError",
    "ParseResult",
    "ParsedSection",
    "SchemaParser",
    "SchemaValidationError",
    "SectionSpec",
    "canonicalize_header",
    "clean_inner_text",
    "normalize_unicode_and_whitespace",
    "parse_markdown",
    "parse_pdf",
    "strip_markdown_header_syntax",
]
