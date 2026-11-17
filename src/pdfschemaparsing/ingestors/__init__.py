"""Document ingestors for Markdown, plain text, and PDF inputs."""

from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.ingestors.markdown_ingestor import ingest_markdown
from pdfschemaparsing.ingestors.pdf_ingestor import (
    SpanFragment,
    ingest_pdf,
    reconstruct_lines_from_spans,
    strip_running_headers_and_footers,
)

__all__ = [
    "SpanFragment",
    "TextLine",
    "ingest_markdown",
    "ingest_pdf",
    "reconstruct_lines_from_spans",
    "strip_running_headers_and_footers",
]
