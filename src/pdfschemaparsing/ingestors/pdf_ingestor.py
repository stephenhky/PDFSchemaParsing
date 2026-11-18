"""PDF ingestor with y-tolerance line reconstruction, layout shift resilience, and header/footer removal."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
from statistics import median
from typing import BinaryIO

from pdfschemaparsing.footnotes import mark_and_extract_pdf_page_footnotes
from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.normalizer import canonicalize_header, normalize_unicode_and_whitespace

_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:page\s+\d+(?:\s+of\s+\d+)?|-\s*\d+\s*-|\[\s*\d+\s*\]|\d+\s*/\s*\d+|\d+)\s*$",
    re.IGNORECASE,
)


@dataclass
class SpanFragment:
    """Represents a positioned text span or word fragment on a PDF page."""

    text: str
    x0: float
    y0: float
    x1: float = 0.0
    y1: float = 0.0
    font_size: float | None = None
    is_bold: bool = False
    page: int = 1


def reconstruct_lines_from_spans(
    spans: list[SpanFragment],
    *,
    y_tolerance: float = 3.0,
    page: int = 1,
    start_line_index: int = 0,
) -> list[TextLine]:
    """Group positioned text spans into visual lines using vertical y-coordinate clustering.

    Resilient to minor vertical jitter (e.g., words on the same line shifted by +/- 2pt)
    and out-of-order PDF stream emission by sorting spans top-to-bottom with y_tolerance
    clustering, then left-to-right by x0.
    """
    non_empty = [s for s in spans if s.text and s.text.strip()]
    if not non_empty:
        return []

    # Sort primarily by y0 ascending, then x0 ascending
    sorted_spans = sorted(non_empty, key=lambda s: (s.y0, s.x0))

    clusters: list[list[SpanFragment]] = []
    cluster_y_means: list[float] = []

    for span in sorted_spans:
        placed = False
        # Check recent clusters in case slightly out-of-order y0 falls within y_tolerance
        for idx in range(len(clusters) - 1, max(-1, len(clusters) - 4), -1):
            if abs(span.y0 - cluster_y_means[idx]) <= y_tolerance:
                clusters[idx].append(span)
                # Update running mean y0 for the cluster
                cluster_y_means[idx] = sum(s.y0 for s in clusters[idx]) / len(clusters[idx])
                placed = True
                break
        if not placed:
            clusters.append([span])
            cluster_y_means.append(span.y0)

    # Order clusters strictly by their mean y0, and spans within each cluster by x0
    ordered_clusters = [
        sorted(cluster, key=lambda s: s.x0)
        for _, cluster in sorted(zip(cluster_y_means, clusters), key=lambda pair: pair[0])
    ]

    lines: list[TextLine] = []
    line_idx = start_line_index
    prev_y1: float | None = None

    # Estimate typical line spacing to insert blank lines between paragraphs
    y_gaps: list[float] = []
    for i in range(1, len(ordered_clusters)):
        prev_y = sum(s.y0 for s in ordered_clusters[i - 1]) / len(ordered_clusters[i - 1])
        curr_y = sum(s.y0 for s in ordered_clusters[i]) / len(ordered_clusters[i])
        gap = curr_y - prev_y
        if 4.0 < gap < 40.0:
            y_gaps.append(gap)
    median_gap = median(y_gaps) if y_gaps else 14.0

    for cluster in ordered_clusters:
        curr_y0 = sum(s.y0 for s in cluster) / len(cluster)
        if prev_y1 is not None and (curr_y0 - prev_y1) > max(median_gap * 1.45, 16.0):
            lines.append(TextLine(text="", line_index=line_idx, page=page))
            line_idx += 1

        # Join spans horizontally
        parts: list[str] = []
        prev_x1: float | None = None
        for s in cluster:
            piece = normalize_unicode_and_whitespace(s.text, preserve_newlines=False)
            if not piece:
                continue
            if prev_x1 is not None and s.x0 - prev_x1 < -1.0:
                # Overlapping duplicate glyph emission (sometimes used in PDFs for faux-bold)
                if parts and parts[-1].endswith(piece):
                    continue
            parts.append(piece)
            prev_x1 = s.x1 if s.x1 > s.x0 else s.x0 + len(piece) * 4.0

        line_text = normalize_unicode_and_whitespace(" ".join(parts), preserve_newlines=False)
        if not line_text:
            continue

        sizes = [s.font_size for s in cluster if s.font_size is not None and s.font_size > 0]
        avg_font_size = (sum(sizes) / len(sizes)) if sizes else None
        is_bold = any(s.is_bold for s in cluster)

        lines.append(
            TextLine(
                text=line_text,
                line_index=line_idx,
                page=page,
                font_size=avg_font_size,
                is_bold=is_bold,
                y0=curr_y0,
                x0=cluster[0].x0,
            )
        )
        line_idx += 1
        prev_y1 = curr_y0

    return lines


def strip_running_headers_and_footers(page_lines: list[list[TextLine]]) -> list[list[TextLine]]:
    """Remove standalone page numbers and repeated running headers/footers across multi-page PDFs."""
    num_pages = len(page_lines)
    if num_pages == 0:
        return []

    # Count canonical top/bottom line occurrences across pages (only relevant if >= 3 pages, or >= 2 with identical header)
    top_counter: Counter[str] = Counter()
    bottom_counter: Counter[str] = Counter()

    if num_pages >= 2:
        for lines in page_lines:
            non_blank = [ln for ln in lines if not ln.is_blank]
            if not non_blank:
                continue
            top_canon = canonicalize_header(non_blank[0].text, strip_numbering=True)
            if top_canon:
                top_counter[top_canon] += 1
            if len(non_blank) > 1:
                bot_canon = canonicalize_header(non_blank[-1].text, strip_numbering=True)
                if bot_canon:
                    bottom_counter[bot_canon] += 1

    min_repeat = 2 if num_pages == 2 else max(2, int(num_pages * 0.6))
    repeated_tops = {k for k, v in top_counter.items() if v >= min_repeat}
    repeated_bottoms = {k for k, v in bottom_counter.items() if v >= min_repeat}

    cleaned_pages: list[list[TextLine]] = []
    for lines in page_lines:
        non_blank_indices = [idx for idx, ln in enumerate(lines) if not ln.is_blank]
        if not non_blank_indices:
            cleaned_pages.append(lines)
            continue

        first_idx = non_blank_indices[0]
        last_idx = non_blank_indices[-1]
        drop_indices: set[int] = set()

        first_text = lines[first_idx].text.strip()
        first_canon = canonicalize_header(first_text, strip_numbering=True)
        if _PAGE_NUMBER_RE.match(first_text) or (first_canon in repeated_tops and num_pages >= 2):
            drop_indices.add(first_idx)

        last_text = lines[last_idx].text.strip()
        last_canon = canonicalize_header(last_text, strip_numbering=True)
        if _PAGE_NUMBER_RE.match(last_text) or (last_canon in repeated_bottoms and num_pages >= 2):
            drop_indices.add(last_idx)

        cleaned_pages.append(
            _trim_blank_edges([ln for idx, ln in enumerate(lines) if idx not in drop_indices])
        )

    return cleaned_pages


def _trim_blank_edges(lines: list[TextLine]) -> list[TextLine]:
    """Remove leading and trailing blank lines from a page's line list."""
    start = 0
    end = len(lines)
    while start < end and lines[start].is_blank:
        start += 1
    while end > start and lines[end - 1].is_blank:
        end -= 1
    return lines[start:end]


def mark_pdf_typography_headings(lines: list[TextLine]) -> list[TextLine]:
    """Annotate `is_heading_hint=True` on lines whose font size is noticeably larger than body text or bold."""
    font_sizes = [ln.font_size for ln in lines if ln.font_size is not None and not ln.is_blank]
    body_size = median(font_sizes) if font_sizes else None

    for ln in lines:
        if ln.is_blank:
            continue
        if body_size is not None and ln.font_size is not None and ln.font_size >= body_size * 1.12:
            ln.is_heading_hint = True
        elif ln.is_bold and len(ln.text.split()) <= 12:
            ln.is_heading_hint = True
    return lines


def _extract_with_pymupdf(
    source: str | Path | bytes | BinaryIO,
    *,
    y_tolerance: float = 3.0,
) -> list[list[TextLine]]:
    import pymupdf as fitz

    if isinstance(source, (str, Path)):
        doc = fitz.open(str(source))
    elif isinstance(source, (bytes, bytearray)):
        doc = fitz.open(stream=bytes(source), filetype="pdf")
    elif hasattr(source, "read"):
        data = source.read()
        doc = fitz.open(stream=data, filetype="pdf")
    else:
        raise TypeError(f"Unsupported PDF source type: {type(source).__name__}")

    page_lines: list[list[TextLine]] = []
    try:
        for page_num, page in enumerate(doc, start=1):
            page_dict = page.get_text("dict")
            spans: list[SpanFragment] = []
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # 0 = text block
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text or not text.strip():
                            continue
                        bbox = span.get("bbox", (0.0, 0.0, 0.0, 0.0))
                        font_name = (span.get("font") or "").lower()
                        flags = int(span.get("flags", 0))
                        # PyMuPDF flag bit 4 (16) indicates bold; also check font name
                        is_bold = bool(flags & 16) or ("bold" in font_name) or ("black" in font_name)
                        spans.append(
                            SpanFragment(
                                text=text,
                                x0=float(bbox[0]),
                                y0=float(bbox[1]),
                                x1=float(bbox[2]),
                                y1=float(bbox[3]),
                                font_size=float(span.get("size", 0.0)) or None,
                                is_bold=is_bold,
                                page=page_num,
                            )
                        )
            reconstructed = reconstruct_lines_from_spans(
                spans,
                y_tolerance=y_tolerance,
                page=page_num,
            )
            page_lines.append(reconstructed)
    finally:
        doc.close()

    return page_lines


def _extract_with_pypdf(
    source: str | Path | bytes | BinaryIO,
    *,
    y_tolerance: float = 3.0,
) -> list[list[TextLine]]:
    import io
    from pypdf import PdfReader

    if isinstance(source, (str, Path)):
        reader = PdfReader(str(source))
    elif isinstance(source, (bytes, bytearray)):
        reader = PdfReader(io.BytesIO(bytes(source)))
    elif hasattr(source, "read"):
        reader = PdfReader(source)
    else:
        raise TypeError(f"Unsupported PDF source type: {type(source).__name__}")

    page_lines: list[list[TextLine]] = []
    for page_num, page in enumerate(reader.pages, start=1):
        spans: list[SpanFragment] = []

        def visitor_body(text: str, cm: list[float], tm: list[float], font_dict: dict | None, font_size: float) -> None:
            if not text or not text.strip():
                return
            x = float(tm[4]) if len(tm) > 4 else 0.0
            # PDF user space has y increasing bottom-to-top; invert so top-to-bottom is ascending
            y_pdf = float(tm[5]) if len(tm) > 5 else 0.0
            font_name = ""
            if isinstance(font_dict, dict):
                font_name = str(font_dict.get("/BaseFont", "")).lower()
            is_bold = "bold" in font_name
            for sub_idx, raw_line in enumerate(text.splitlines()):
                if raw_line.strip():
                    spans.append(
                        SpanFragment(
                            text=raw_line,
                            x0=x,
                            y0=-y_pdf + (sub_idx * (font_size or 12.0)),
                            font_size=float(font_size) if font_size else None,
                            is_bold=is_bold,
                            page=page_num,
                        )
                    )

        try:
            page.extract_text(visitor_text=visitor_body)
        except Exception:
            spans = []

        if spans:
            reconstructed = reconstruct_lines_from_spans(spans, y_tolerance=y_tolerance, page=page_num)
        else:
            raw_text = page.extract_text() or ""
            reconstructed = [
                TextLine(
                    text=normalize_unicode_and_whitespace(ln, preserve_newlines=False),
                    line_index=i,
                    page=page_num,
                )
                for i, ln in enumerate(raw_text.splitlines())
            ]
        page_lines.append(reconstructed)

    return page_lines


def ingest_pdf(
    source: str | Path | bytes | BinaryIO,
    *,
    y_tolerance: float = 3.0,
    strip_running_headers: bool = True,
    backend: str = "auto",
) -> list[TextLine]:
    """Ingest a PDF file, bytes, or stream into normalized TextLine objects."""
    page_lines: list[list[TextLine]] | None = None

    if backend in ("auto", "pymupdf"):
        try:
            page_lines = _extract_with_pymupdf(source, y_tolerance=y_tolerance)
        except ImportError:
            if backend == "pymupdf":
                raise
        except Exception:
            if backend == "pymupdf":
                raise

    if page_lines is None and backend in ("auto", "pypdf"):
        page_lines = _extract_with_pypdf(source, y_tolerance=y_tolerance)

    if page_lines is None:
        raise ValueError(f"Unknown or unavailable PDF backend: {backend!r}")

    if strip_running_headers:
        page_lines = strip_running_headers_and_footers(page_lines)

    for plist in page_lines:
        mark_and_extract_pdf_page_footnotes(plist)

    # Flatten pages and re-index lines sequentially
    flat_lines: list[TextLine] = []
    idx = 0
    for page_idx, plist in enumerate(page_lines):
        if page_idx > 0 and flat_lines and not flat_lines[-1].is_blank:
            # Ensure a clean line boundary between pages
            pass
        for ln in plist:
            ln.line_index = idx
            flat_lines.append(ln)
            idx += 1

    return mark_pdf_typography_headings(flat_lines)
