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
    extract_footnotes,
    parse_markdown,
    parse_pdf,
    pdf_to_markdown,
)
from pdfschemaparsing.footnotes import Footnote
from pdfschemaparsing.schema import (
    DocumentSchema,
    MissingRequiredSectionError,
    MissingRequiredSectionWarning,
    SchemaValidationError,
    SectionSpec,
)

__all__ = [
    "DocumentSchema",
    "Footnote",
    "MissingRequiredSectionError",
    "MissingRequiredSectionWarning",
    "ParseResult",
    "ParsedSection",
    "SchemaParser",
    "SchemaValidationError",
    "SectionSpec",
    "canonicalize_header",
    "clean_inner_text",
    "extract_footnotes",
    "normalize_unicode_and_whitespace",
    "parse_markdown",
    "parse_pdf",
    "pdf_to_markdown",
    "strip_markdown_header_syntax",
]
