"""Markdown and plain-text ingestor with Setext/ATX/bold heading detection."""

from __future__ import annotations

from pathlib import Path
import re

from pdfschemaparsing.footnotes import mark_and_extract_markdown_footnotes
from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.normalizer import normalize_unicode_and_whitespace

_ATX_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.+?)(?:\s+#+\s*)?$")
_SETEXT_H1_RE = re.compile(r"^\s*={3,}\s*$")
_SETEXT_H2_RE = re.compile(r"^\s*-{3,}\s*$")
_HRULE_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_BOLD_LINE_RE = re.compile(r"^\s*(\*{2,3}|_{2,3})([^\*_]+?)\1\s*:?\s*$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def ingest_markdown(source: str | Path) -> list[TextLine]:
    """Ingest a Markdown or plain-text string/file into normalized TextLine objects.

    Handles:
    - ATX headings (`# Heading` through `###### Heading`)
    - Setext two-line headings (`Heading\\n=======` or `Heading\\n-------`)
    - Standalone bold headings (`**Heading**` or `__Heading__`)
    - Code fence tracking (lines inside ``` fences are never marked as headings)
    """
    if isinstance(source, Path):
        raw_text = source.read_text(encoding="utf-8")
    elif isinstance(source, str):
        # Check if it's a path to an existing markdown/text file (and single-line)
        if "\n" not in source and len(source) < 1024:
            candidate = Path(source)
            if candidate.exists() and candidate.is_file():
                raw_text = candidate.read_text(encoding="utf-8")
            else:
                raw_text = source
        else:
            raw_text = source
    else:
        raise TypeError(f"Expected str or Path for Markdown ingestion, got {type(source).__name__}")

    raw_lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    result: list[TextLine] = []
    aligned_raw_lines: list[str] = []
    in_code_fence = False
    fence_marker: str | None = None

    i = 0
    line_idx = 0
    n = len(raw_lines)
    while i < n:
        raw_line = raw_lines[i]
        fence_match = _FENCE_RE.match(raw_line)
        if fence_match:
            marker = fence_match.group(1)[0]
            if not in_code_fence:
                in_code_fence = True
                fence_marker = marker
            elif fence_marker == marker:
                in_code_fence = False
                fence_marker = None
            result.append(
                TextLine(
                    text=normalize_unicode_and_whitespace(raw_line, preserve_newlines=False),
                    line_index=line_idx,
                    page=1,
                    in_code_block=True,
                )
            )
            aligned_raw_lines.append(raw_line)
            line_idx += 1
            i += 1
            continue

        norm_line = normalize_unicode_and_whitespace(raw_line, preserve_newlines=False)

        if not in_code_fence and norm_line:
            if _HRULE_RE.match(norm_line):
                result.append(
                    TextLine(
                        text=norm_line,
                        line_index=line_idx,
                        page=1,
                        is_decorative_rule=True,
                    )
                )
                aligned_raw_lines.append(raw_line)
                line_idx += 1
                i += 1
                continue

            # Check Setext underline on the next line (e.g. "Title\n======")
            if i + 1 < n:
                next_raw = raw_lines[i + 1]
                if _SETEXT_H1_RE.match(next_raw):
                    result.append(
                        TextLine(
                            text=norm_line,
                            line_index=line_idx,
                            page=1,
                            is_heading_hint=True,
                            heading_level=1,
                            is_bold=True,
                        )
                    )
                    aligned_raw_lines.append(raw_line)
                    line_idx += 1
                    i += 2  # Consume both the title line and the === underline
                    continue
                if _SETEXT_H2_RE.match(next_raw) and not re.match(r"^\s*[-\*\+]\s+", raw_line):
                    result.append(
                        TextLine(
                            text=norm_line,
                            line_index=line_idx,
                            page=1,
                            is_heading_hint=True,
                            heading_level=2,
                            is_bold=True,
                        )
                    )
                    aligned_raw_lines.append(raw_line)
                    line_idx += 1
                    i += 2  # Consume both the title line and the --- underline
                    continue

            # Check ATX heading (# ... ######)
            atx_match = _ATX_HEADING_RE.match(norm_line)
            if atx_match:
                level = len(atx_match.group(1))
                result.append(
                    TextLine(
                        text=norm_line,
                        line_index=line_idx,
                        page=1,
                        is_heading_hint=True,
                        heading_level=level,
                        is_bold=True,
                    )
                )
                aligned_raw_lines.append(raw_line)
                line_idx += 1
                i += 1
                continue

            # Check standalone bold line (**Heading**)
            bold_match = _BOLD_LINE_RE.match(norm_line)
            if bold_match:
                result.append(
                    TextLine(
                        text=norm_line,
                        line_index=line_idx,
                        page=1,
                        is_heading_hint=True,
                        is_bold=True,
                    )
                )
                aligned_raw_lines.append(raw_line)
                line_idx += 1
                i += 1
                continue

        result.append(
            TextLine(
                text=norm_line,
                line_index=line_idx,
                page=1,
                in_code_block=in_code_fence,
            )
        )
        aligned_raw_lines.append(raw_line)
        line_idx += 1
        i += 1

    mark_and_extract_markdown_footnotes(result, raw_lines=aligned_raw_lines)
    return result
