"""Footnote detection, extraction, and filtering for Markdown and PDF documents."""

from __future__ import annotations

from dataclasses import dataclass
import re
from statistics import median
from typing import Any

from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.normalizer import clean_inner_text

# 1. Markdown standard footnote definition: e.g. "[^1]: Footnote text" or "[^note-a]: Text"
_MD_FOOTNOTE_DEF_RE = re.compile(r"^\s*\[\^([^\]]+)\]:\s*(.*)$")

# 2. Indented continuation line in Markdown (2+ leading spaces or a tab in raw line, or immediately following a footnote)
# 3. Bracketed numeric footnote at bottom of page/document: e.g. "[1] Footnote text"
_BRACKET_FOOTNOTE_RE = re.compile(r"^\s*\[(\d{1,3}|[a-zA-Z])\]\s+(.+)$")

# 4. Superscript unicode markers: ¹ ² ³ ⁴ ⁵ ⁶ ⁷ ⁸ ⁹ ⁰ or symbols * † ‡ §
# Note: Only allow '*' as a footnote marker when it is NOT a Markdown bold line (**...**)
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")
_SUPERSCRIPT_FOOTNOTE_RE = re.compile(
    r"^\s*([¹²³⁴⁵⁶⁷⁸⁹][⁰¹²³⁴⁵⁶⁷⁸⁹]*|[\u2020\u2021\u00a7]{1,3}|\*(?!\*))\s*(.+)$"
)

# 5. Bottom-of-page PDF numeric or symbol footnote: e.g. "1 Footnote text...", "* Footnote...", "· Footnote..."
# (Only applied when positioned in the bottom footnote zone of a PDF page or with smaller font)
_PDF_NUMERIC_FOOTNOTE_RE = re.compile(
    r"^\s*(\d{1,2}|[\*\u2020\u2021\u00a7\u00b7])[\.\)]?\s+([A-Z\"'\(\[].*)$"
)


@dataclass
class Footnote:
    """Represents an extracted footnote from a document."""

    id: str
    content: str
    page: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "page": self.page,
        }


def _normalize_footnote_id(raw_id: str) -> str:
    return raw_id.strip().translate(_SUPERSCRIPT_MAP)


def mark_and_extract_markdown_footnotes(
    lines: list[TextLine],
    raw_lines: list[str] | None = None,
) -> list[Footnote]:
    """Identify footnotes in Markdown/text lines, mark `line.is_footnote = True`, and return Footnote objects.

    Recognizes:
    - Standard Markdown footnote definitions: `[^1]: Footnote content` (plus indented multi-line continuations)
    - Superscript/symbol footnotes: `¹ Footnote content`, `† Footnote content`
    - Bottom-of-document or post-rule bracketed footnotes: `[1] Footnote content`
    """
    n = len(lines)
    if n == 0:
        return []

    # Locate the last decorative horizontal rule (`---`) if any, which often separates body from footnotes
    last_rule_idx = -1
    for idx, ln in enumerate(lines):
        if ln.is_decorative_rule and not ln.in_code_block:
            last_rule_idx = idx

    footnotes: list[Footnote] = []
    i = 0
    while i < n:
        ln = lines[i]
        if ln.is_blank or ln.in_code_block or ln.is_decorative_rule or ln.is_heading_hint:
            i += 1
            continue

        text = ln.text.strip()
        raw_text_line = raw_lines[i].strip() if (raw_lines is not None and i < len(raw_lines)) else text
        fn_id: str | None = None
        fn_first_line: str | None = None
        is_md_def = False

        # Case A: Standard Markdown `[^id]: text`
        m_md = _MD_FOOTNOTE_DEF_RE.match(text)
        if m_md:
            fn_id = m_md.group(1).strip()
            fn_first_line = m_md.group(2).strip()
            is_md_def = True

        # Case B: Unicode superscript (`¹ Source...`) or symbol (`† Note...`, `* Note...` when not bold/bullet list)
        # Check against `raw_text_line` so unicode superscripts like `¹` are caught before NFKC turns them into `1`
        if fn_id is None:
            m_sup = _SUPERSCRIPT_FOOTNOTE_RE.match(raw_text_line)
            if m_sup:
                marker = m_sup.group(1)
                # Avoid treating normal markdown unordered bullet `* item` as a footnote unless after a rule
                if not (marker == "*" and last_rule_idx == -1):
                    fn_id = _normalize_footnote_id(marker)
                    fn_first_line = m_sup.group(2).strip()

        # Case C: Bracketed `[1] Footnote...` after a horizontal rule or in the trailing 25% of the document
        if fn_id is None and (
            (last_rule_idx != -1 and i > last_rule_idx) or (i >= int(n * 0.7))
        ):
            m_br = _BRACKET_FOOTNOTE_RE.match(text)
            if m_br:
                fn_id = m_br.group(1).strip()
                fn_first_line = m_br.group(2).strip()

        if fn_id is not None and fn_first_line is not None:
            ln.is_footnote = True
            ln.footnote_id = fn_id
            ln.footnote_body = fn_first_line
            body_parts = [fn_first_line] if fn_first_line else []

            # Consume continuation lines for standard Markdown footnotes (indented by 2+ spaces or tab)
            j = i + 1
            while j < n:
                next_ln = lines[j]
                if next_ln.in_code_block or next_ln.is_decorative_rule:
                    break
                if next_ln.is_blank:
                    # Look ahead to see if another indented continuation line follows
                    if (
                        is_md_def
                        and j + 1 < n
                        and raw_lines is not None
                        and j + 1 < len(raw_lines)
                        and re.match(r"^(?: {2,}|\t)\S", raw_lines[j + 1])
                        and not _MD_FOOTNOTE_DEF_RE.match(lines[j + 1].text)
                    ):
                        next_ln.is_footnote = True
                        next_ln.footnote_id = None
                        next_ln.footnote_body = ""
                        body_parts.append("")
                        j += 1
                        continue
                    break

                # Stop if the next line starts a new footnote or heading
                if (
                    next_ln.is_heading_hint
                    or _MD_FOOTNOTE_DEF_RE.match(next_ln.text)
                    or _SUPERSCRIPT_FOOTNOTE_RE.match(next_ln.text)
                    or _BRACKET_FOOTNOTE_RE.match(next_ln.text)
                ):
                    break

                is_indented = (
                    raw_lines is not None
                    and j < len(raw_lines)
                    and bool(re.match(r"^(?: {2,}|\t)\S", raw_lines[j]))
                )
                if is_md_def and is_indented:
                    next_ln.is_footnote = True
                    next_ln.footnote_id = None
                    next_ln.footnote_body = next_ln.text.strip()
                    body_parts.append(next_ln.text.strip())
                    j += 1
                else:
                    break

            footnotes.append(
                Footnote(
                    id=fn_id,
                    content=clean_inner_text(body_parts),
                    page=ln.page,
                )
            )
            i = j
            continue

        i += 1

    return footnotes


def mark_and_extract_pdf_page_footnotes(
    page_lines: list[TextLine],
    *,
    page_height: float | None = None,
) -> list[Footnote]:
    """Identify footnotes at the bottom of a PDF page, mark `line.is_footnote = True`, and return Footnote objects.

    Combines spatial position (bottom of page), typography (smaller font size than body median),
    and footnote marker syntax (`[^1]:`, `[1]`, `¹`, `†`, `*`, or bottom-zone `1 Footnote...`).
    """
    non_blank = [ln for ln in page_lines if not ln.is_blank]
    if not non_blank:
        return []

    font_sizes = [ln.font_size for ln in non_blank if ln.font_size is not None and ln.font_size > 0]
    median_font = median(font_sizes) if font_sizes else None

    y_coords = [ln.y0 for ln in non_blank if ln.y0 is not None]
    max_y = max(y_coords) if y_coords else None
    effective_height = page_height or (max_y / 0.88 if max_y else None)

    footnotes: list[Footnote] = []
    n = len(page_lines)

    # Determine where the bottom footnote block starts on this page
    in_footnote_zone = False
    current_fn_id: str | None = None
    current_fn_parts: list[str] = []
    current_fn_page: int = non_blank[0].page

    def _flush_current() -> None:
        nonlocal current_fn_id, current_fn_parts
        if current_fn_id is not None:
            footnotes.append(
                Footnote(
                    id=current_fn_id,
                    content=clean_inner_text(current_fn_parts),
                    page=current_fn_page,
                )
            )
            current_fn_id = None
            current_fn_parts = []

    for idx, ln in enumerate(page_lines):
        if ln.is_blank:
            continue

        text = ln.text.strip()
        is_bottom_area = (
            (effective_height is not None and ln.y0 is not None and ln.y0 >= effective_height * 0.65)
            or (idx >= max(1, int(n * 0.65)))
        )
        is_smaller_font = (
            median_font is not None
            and ln.font_size is not None
            and ln.font_size <= median_font * 0.90
        )

        fn_id: str | None = None
        fn_body: str | None = None

        # Explicit footnote patterns anywhere on the page
        m_md = _MD_FOOTNOTE_DEF_RE.match(text)
        if m_md:
            fn_id = m_md.group(1).strip()
            fn_body = m_md.group(2).strip()

        if fn_id is None:
            m_sup = _SUPERSCRIPT_FOOTNOTE_RE.match(text)
            if m_sup and (is_bottom_area or is_smaller_font or m_sup.group(1) != "*"):
                fn_id = _normalize_footnote_id(m_sup.group(1))
                fn_body = m_sup.group(2).strip()

        if fn_id is None and (is_bottom_area or is_smaller_font or in_footnote_zone):
            m_br = _BRACKET_FOOTNOTE_RE.match(text)
            if m_br:
                fn_id = m_br.group(1).strip()
                fn_body = m_br.group(2).strip()

        if fn_id is None and (is_bottom_area and (is_smaller_font or in_footnote_zone)):
            m_num = _PDF_NUMERIC_FOOTNOTE_RE.match(text)
            if m_num and not ln.is_heading_hint:
                fn_id = m_num.group(1).strip()
                fn_body = m_num.group(2).strip()

        if fn_id is not None and fn_body is not None:
            _flush_current()
            in_footnote_zone = True
            ln.is_footnote = True
            ln.footnote_id = fn_id
            ln.footnote_body = fn_body
            current_fn_id = fn_id
            current_fn_parts = [fn_body]
            current_fn_page = ln.page
        elif in_footnote_zone and (is_smaller_font or is_bottom_area) and not ln.is_heading_hint:
            # Continuation line of a multi-line footnote at the bottom of the page
            ln.is_footnote = True
            ln.footnote_id = None
            ln.footnote_body = text
            current_fn_parts.append(text)
        else:
            # Leaving footnote zone if normal body text resumes
            if in_footnote_zone and not is_smaller_font:
                _flush_current()
                in_footnote_zone = False

    _flush_current()
    return footnotes


def extract_footnotes_from_lines(lines: list[TextLine]) -> list[Footnote]:
    """Reconstruct Footnote objects from lines already marked with `is_footnote=True`."""
    footnotes: list[Footnote] = []
    current_id: str | None = None
    current_parts: list[str] = []
    current_page: int = 1

    def _flush() -> None:
        nonlocal current_id, current_parts
        if current_id is not None:
            footnotes.append(
                Footnote(
                    id=current_id,
                    content=clean_inner_text(current_parts),
                    page=current_page,
                )
            )
            current_id = None
            current_parts = []

    for ln in lines:
        if not ln.is_footnote:
            continue
        if ln.footnote_id is not None:
            # Start of a new footnote
            _flush()
            current_id = ln.footnote_id
            current_page = ln.page
            body_text = ln.footnote_body if ln.footnote_body is not None else ln.text.strip()
            current_parts = [body_text]
        elif current_id is not None:
            # Continuation line of the current footnote
            body_text = ln.footnote_body if ln.footnote_body is not None else ln.text.strip()
            current_parts.append(body_text)

    _flush()
    return footnotes
