"""Core SchemaParser orchestrating ingestion, section boundary segmentation, and JSON formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from statistics import median
from typing import Any, BinaryIO, Mapping, Sequence

from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.ingestors.markdown_ingestor import ingest_markdown
from pdfschemaparsing.ingestors.pdf_ingestor import ingest_pdf
from pdfschemaparsing.footnotes import Footnote, extract_footnotes_from_lines
from pdfschemaparsing.matcher import find_section_matches
from pdfschemaparsing.normalizer import clean_inner_text
from pdfschemaparsing.schema import DocumentSchema, MissingRequiredSectionError, SectionSpec


@dataclass
class ParsedSection:
    """Represents a parsed section and its inner text / subsections."""

    title: str
    content: str | None
    matched_header: str | None = None
    subsections: list[ParsedSection] = field(default_factory=list)

    def to_dict(self, *, include_metadata: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "title": self.title,
            "content": self.content,
        }
        if include_metadata:
            data["matched_header"] = self.matched_header
        if self.subsections or include_metadata:
            data["subsections"] = [
                sub.to_dict(include_metadata=include_metadata) for sub in self.subsections
            ]
        return data


def _serialize_flat_sections(sections: list[ParsedSection]) -> dict[str, Any]:
    """Recursively serialize a list of ParsedSection objects into a flat dictionary.

    Handles:
    - Arbitrary nesting depth (`subsections` inside `subsections`)
    - Repeated inner headings with the same title (disambiguated as `"Title"`, `"Title (2)"`, etc.,
      or preserved under their distinct resolved titles like `"1.1 Section 1"`, `"1.2 Section 2"`)
    """
    out: dict[str, Any] = {}
    title_counts: dict[str, int] = {}
    for sec in sections:
        base_title = sec.title
        count = title_counts.get(base_title, 0) + 1
        title_counts[base_title] = count
        key = base_title if count == 1 else f"{base_title} ({count})"

        if sec.subsections:
            out[key] = {
                "content": sec.content,
                "subsections": _serialize_flat_sections(sec.subsections),
            }
        else:
            out[key] = sec.content
    return out


@dataclass
class ParseResult:
    """Container for parsed document sections with flexible dictionary and JSON serialization."""

    sections: list[ParsedSection]
    preamble: str | None = None
    footnotes: list[Footnote] = field(default_factory=list)

    def get_footnotes(
        self,
        *,
        as_flat_dict: bool = False,
        as_json: bool = False,
    ) -> list[dict[str, Any]] | dict[str, str] | str:
        """Return extracted footnotes as a list of dicts, `{id: content}` dict, or JSON string."""
        if as_flat_dict:
            data: Any = {fn.id: fn.content for fn in self.footnotes}
        else:
            data = [fn.to_dict() for fn in self.footnotes]
        if as_json:
            return json.dumps(data, indent=2, ensure_ascii=False)
        return data

    def to_dict(
        self,
        *,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
        include_footnotes: bool = False,
    ) -> dict[str, Any]:
        """Convert parsed sections to either a structured list dictionary or `{title: inner_text}` mapping."""
        if as_flat_dict:
            out: dict[str, Any] = {}
            if self.preamble is not None:
                out["_preamble"] = self.preamble
            out.update(_serialize_flat_sections(self.sections))
            if include_footnotes:
                out["_footnotes"] = {fn.id: fn.content for fn in self.footnotes}
            return out

        result: dict[str, Any] = {
            "sections": [sec.to_dict(include_metadata=include_metadata) for sec in self.sections]
        }
        if self.preamble is not None:
            result["preamble"] = self.preamble
        if include_footnotes:
            result["footnotes"] = [fn.to_dict() for fn in self.footnotes]
        return result

    def to_markdown(
        self,
        *,
        include_footnotes: bool = False,
        base_heading_level: int = 1,
    ) -> str:
        """Render the parsed sections (and optional preamble/footnotes) as a clean Markdown string."""
        parts: list[str] = []
        if self.preamble:
            parts.append(self.preamble)

        def _render_sections(secs: list[ParsedSection], level: int) -> None:
            hashes = "#" * min(max(level, 1), 6)
            for sec in secs:
                parts.append(f"{hashes} {sec.title}")
                if sec.content:
                    parts.append(sec.content)
                if sec.subsections:
                    _render_sections(sec.subsections, level + 1)

        _render_sections(self.sections, base_heading_level)

        if include_footnotes and self.footnotes:
            fn_lines = [f"[^{fn.id}]: {fn.content}" for fn in self.footnotes]
            parts.append("---\n" + "\n".join(fn_lines))

        return "\n\n".join(parts).strip()

    def to_json(
        self,
        *,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
        include_footnotes: bool = False,
        indent: int = 2,
    ) -> str:
        """Serialize the parsed result directly to a JSON string."""
        return json.dumps(
            self.to_dict(
                as_flat_dict=as_flat_dict,
                include_metadata=include_metadata,
                include_footnotes=include_footnotes,
            ),
            indent=indent,
            ensure_ascii=False,
        )


class SchemaParser:
    """Resilient schema-driven parser for PDF and Markdown documents."""

    def __init__(
        self,
        schema: DocumentSchema | Mapping[str, Any] | Sequence[Any] | str | Path,
    ) -> None:
        self.schema = DocumentSchema.from_any(schema)

    def parse_lines(self, lines: list[TextLine]) -> ParseResult:
        """Segment a list of ingested TextLine objects according to `self.schema`."""
        footnotes = extract_footnotes_from_lines(lines)
        sections, preamble = self._segment_block(
            lines=lines,
            specs=self.schema.sections,
            is_top_level=True,
        )
        return ParseResult(
            sections=sections,
            preamble=preamble if self.schema.capture_preamble else None,
            footnotes=footnotes,
        )

    def _segment_block(
        self,
        lines: list[TextLine],
        specs: list[SectionSpec],
        *,
        is_top_level: bool = False,
    ) -> tuple[list[ParsedSection], str | None]:
        matches = find_section_matches(
            lines=lines,
            specs=specs,
            ordered=self.schema.ordered,
            fuzzy_threshold=self.schema.fuzzy_threshold,
            strip_numbering=self.schema.strip_numbering,
            max_header_words=self.schema.max_header_words,
            max_header_chars=self.schema.max_header_chars,
        )

        # Check required sections
        matched_titles = {m.spec.title for m in matches}
        for spec in specs:
            if spec.required and spec.title not in matched_titles:
                raise MissingRequiredSectionError(
                    f"Required section '{spec.title}' was not found in the document."
                )

        preamble: str | None = None
        if is_top_level and self.schema.capture_preamble:
            first_idx = matches[0].start_line_idx if matches else len(lines)
            preamble = clean_inner_text(
                [
                    ln.text
                    for ln in lines[:first_idx]
                    if not ln.is_decorative_rule and not ln.is_footnote
                ],
                strip_inline_footnote_refs=True,
            )

        parsed_by_match: list[ParsedSection] = []
        for idx, match in enumerate(matches):
            effective_title = match.resolved_title or match.spec.title
            body_start = match.end_line_idx + 1
            body_end = matches[idx + 1].start_line_idx if (idx + 1 < len(matches)) else len(lines)
            slice_lines = [
                ln
                for ln in lines[body_start:body_end]
                if not ln.is_decorative_rule and not ln.is_footnote
            ]

            if match.inline_remainder:
                # Prepend the inline remainder as the first line of the section's body
                inline_line = TextLine(
                    text=match.inline_remainder,
                    line_index=match.end_line_idx,
                    page=lines[match.end_line_idx].page,
                )
                slice_lines = [inline_line, *slice_lines]

            if match.spec.subsections:
                sub_Matches = find_section_matches(
                    lines=slice_lines,
                    specs=match.spec.subsections,
                    ordered=self.schema.ordered,
                    fuzzy_threshold=self.schema.fuzzy_threshold,
                    strip_numbering=self.schema.strip_numbering,
                    max_header_words=self.schema.max_header_words,
                    max_header_chars=self.schema.max_header_chars,
                )
                if sub_Matches:
                    lead_end = sub_Matches[0].start_line_idx
                    lead_text = clean_inner_text(
                        [ln.text for ln in slice_lines[:lead_end]],
                        strip_inline_footnote_refs=True,
                    )
                    subsections, _ = self._segment_block(
                        lines=slice_lines,
                        specs=match.spec.subsections,
                        is_top_level=False,
                    )
                    parsed_by_match.append(
                        ParsedSection(
                            title=effective_title,
                            content=lead_text,
                            matched_header=match.matched_header,
                            subsections=subsections,
                        )
                    )
                    continue
                else:
                    # Check if any subsection was marked required
                    for sub_spec in match.spec.subsections:
                        if sub_spec.required:
                            raise MissingRequiredSectionError(
                                f"Required subsection '{sub_spec.title}' under '{effective_title}' was not found."
                            )

            body_text = clean_inner_text(
                [ln.text for ln in slice_lines],
                strip_inline_footnote_refs=True,
            )
            parsed_by_match.append(
                ParsedSection(
                    title=effective_title,
                    content=body_text,
                    matched_header=match.matched_header,
                    subsections=[],
                )
            )

        # Merge duplicate occurrences of the same canonical section or preserve missing sections if requested
        if self.schema.include_missing:
            # Map each spec.title to all ParsedSections matched by that spec
            by_spec_title: dict[str, list[ParsedSection]] = {}
            for match, psec in zip(matches, parsed_by_match):
                spec_key = match.spec.title
                if spec_key not in by_spec_title:
                    by_spec_title[spec_key] = [psec]
                else:
                    # Merge only if the resolved title is identical; keep consecutive distinct chapters separate
                    existing_list = by_spec_title[spec_key]
                    merged = False
                    for existing in existing_list:
                        if existing.title == psec.title:
                            combined = "\n\n".join(filter(None, [existing.content, psec.content]))
                            existing.content = combined
                            existing.subsections.extend(psec.subsections)
                            merged = True
                            break
                    if not merged:
                        existing_list.append(psec)

            final_sections: list[ParsedSection] = []
            for spec in specs:
                if spec.title in by_spec_title:
                    final_sections.extend(by_spec_title[spec.title])
                else:
                    final_sections.append(
                        ParsedSection(
                            title=spec.title,
                            content=None,
                            matched_header=None,
                            subsections=[],
                        )
                    )
            return final_sections, preamble

        return parsed_by_match, preamble

    def parse_markdown(
        self,
        source: str | Path,
        *,
        as_json: bool = False,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
    ) -> dict[str, Any] | str:
        """Parse a Markdown string or file into a structured dictionary or JSON string."""
        lines = ingest_markdown(source)
        result = self.parse_lines(lines)
        if as_json:
            return result.to_json(as_flat_dict=as_flat_dict, include_metadata=include_metadata)
        return result.to_dict(as_flat_dict=as_flat_dict, include_metadata=include_metadata)

    def parse_pdf(
        self,
        source: str | Path | bytes | BinaryIO,
        *,
        as_json: bool = False,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
        backend: str = "auto",
    ) -> dict[str, Any] | str:
        """Parse a PDF file, bytes, or stream into a structured dictionary or JSON string."""
        lines = ingest_pdf(
            source,
            y_tolerance=self.schema.y_tolerance,
            strip_running_headers=self.schema.strip_running_headers,
            backend=backend,
        )
        result = self.parse_lines(lines)
        if as_json:
            return result.to_json(as_flat_dict=as_flat_dict, include_metadata=include_metadata)
        return result.to_dict(as_flat_dict=as_flat_dict, include_metadata=include_metadata)

    def parse(
        self,
        source: str | Path | bytes | BinaryIO,
        *,
        as_json: bool = False,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
    ) -> dict[str, Any] | str:
        """Auto-detect PDF vs Markdown/text and parse into a structured dictionary or JSON string."""
        if isinstance(source, (bytes, bytearray)) or hasattr(source, "read"):
            return self.parse_pdf(
                source,
                as_json=as_json,
                as_flat_dict=as_flat_dict,
                include_metadata=include_metadata,
            )
        if isinstance(source, Path) and source.suffix.lower() == ".pdf":
            return self.parse_pdf(
                source,
                as_json=as_json,
                as_flat_dict=as_flat_dict,
                include_metadata=include_metadata,
            )
        if isinstance(source, str) and "\n" not in source and source.lower().endswith(".pdf"):
            candidate = Path(source)
            if candidate.exists():
                return self.parse_pdf(
                    candidate,
                    as_json=as_json,
                    as_flat_dict=as_flat_dict,
                    include_metadata=include_metadata,
                )
        return self.parse_markdown(
            source,
            as_json=as_json,
            as_flat_dict=as_flat_dict,
            include_metadata=include_metadata,
        )

    def to_markdown(
        self,
        source: str | Path | bytes | BinaryIO,
        *,
        include_footnotes: bool = False,
        backend: str = "auto",
    ) -> str:
        """Parse a PDF or Markdown document according to `self.schema` and render it as clean Markdown."""
        is_pdf = False
        if isinstance(source, (bytes, bytearray)) or hasattr(source, "read"):
            is_pdf = True
        elif isinstance(source, Path) and source.suffix.lower() == ".pdf":
            is_pdf = True
        elif isinstance(source, str) and "\n" not in source and source.lower().endswith(".pdf"):
            if Path(source).exists():
                is_pdf = True

        if is_pdf:
            lines = ingest_pdf(
                source,
                y_tolerance=self.schema.y_tolerance,
                strip_running_headers=self.schema.strip_running_headers,
                backend=backend,
            )
        else:
            if not isinstance(source, (str, Path)):
                raise TypeError(f"Expected str or Path for Markdown source, got {type(source).__name__}")
            lines = ingest_markdown(source)

        return self.parse_lines(lines).to_markdown(include_footnotes=include_footnotes)

    def extract_footnotes(
        self,
        source: str | Path | bytes | BinaryIO,
        *,
        as_json: bool = False,
        as_flat_dict: bool = False,
        backend: str = "auto",
    ) -> list[dict[str, Any]] | dict[str, str] | str:
        """Extract footnotes from a PDF or Markdown document."""
        return extract_footnotes(
            source,
            as_json=as_json,
            as_flat_dict=as_flat_dict,
            y_tolerance=self.schema.y_tolerance,
            strip_running_headers=self.schema.strip_running_headers,
            backend=backend,
        )


def pdf_to_markdown(
    source: str | Path | bytes | BinaryIO,
    *,
    y_tolerance: float = 3.0,
    strip_running_headers: bool = True,
    ignore_footnotes: bool = True,
    include_footnotes_at_end: bool = False,
    dehyphenate: bool = True,
    backend: str = "auto",
) -> str:
    """Convert a PDF file, bytes, or stream directly to clean Markdown text (without requiring a schema).

    Applies all layout-resilience mechanisms:
    - Y-tolerance vertical clustering to reconstruct jittered lines
    - Running header, footer, and page number removal across pages
    - Automatic Markdown heading formatting (`#`, `##`, `###`) inferred from PDF font size & bolding
    - De-hyphenation of words wrapped across lines
    - Exclusion of bottom-of-page footnotes from the main prose (with optional emission as `[^id]: ...` at the end)
    """
    lines = ingest_pdf(
        source,
        y_tolerance=y_tolerance,
        strip_running_headers=strip_running_headers,
        backend=backend,
    )

    non_blank = [ln for ln in lines if not ln.is_blank and not ln.is_footnote]
    font_sizes = [ln.font_size for ln in non_blank if ln.font_size is not None and ln.font_size > 0]
    body_size = median(font_sizes) if font_sizes else None

    md_lines: list[str] = []
    for ln in lines:
        if ln.is_footnote:
            if not ignore_footnotes:
                md_lines.append(ln.text)
            continue

        if ln.is_blank:
            md_lines.append("")
            continue

        text = ln.text.strip()
        if ln.is_heading_hint:
            # Infer heading depth (# vs ## vs ###) from relative font size
            level = 2
            if body_size is not None and ln.font_size is not None:
                ratio = ln.font_size / body_size
                if ratio >= 1.35:
                    level = 1
                elif ratio >= 1.15:
                    level = 2
                else:
                    level = 3
            elif ln.is_bold:
                level = 2
            # Ensure blank line before a Markdown heading
            if md_lines and md_lines[-1] != "":
                md_lines.append("")
            clean_heading = text.lstrip("#").strip()
            md_lines.append(f"{'#' * level} {clean_heading}")
            md_lines.append("")
        else:
            md_lines.append(text)

    body_md = clean_inner_text(md_lines, dehyphenate=dehyphenate, strip_inline_footnote_refs=ignore_footnotes)

    if include_footnotes_at_end:
        footnotes = extract_footnotes_from_lines(lines)
        if footnotes:
            fn_block = "\n".join(f"[^{fn.id}]: {fn.content}" for fn in footnotes)
            body_md = f"{body_md}\n\n---\n{fn_block}" if body_md else fn_block

    return body_md


def extract_footnotes(
    source: str | Path | bytes | BinaryIO,
    *,
    as_json: bool = False,
    as_flat_dict: bool = False,
    y_tolerance: float = 3.0,
    strip_running_headers: bool = True,
    backend: str = "auto",
) -> list[dict[str, Any]] | dict[str, str] | str:
    """Extract all footnotes from a PDF or Markdown document.

    Returns:
    - By default (`as_flat_dict=False`): `[{"id": "1", "content": "...", "page": 1}, ...]`
    - When `as_flat_dict=True`: `{"1": "...", "2": "..."}`
    - When `as_json=True`: A JSON string representation of the above.
    """
    is_pdf = False
    if isinstance(source, (bytes, bytearray)) or hasattr(source, "read"):
        is_pdf = True
    elif isinstance(source, Path) and source.suffix.lower() == ".pdf":
        is_pdf = True
    elif isinstance(source, str) and "\n" not in source and source.lower().endswith(".pdf"):
        if Path(source).exists():
            is_pdf = True

    if is_pdf:
        lines = ingest_pdf(
            source,
            y_tolerance=y_tolerance,
            strip_running_headers=strip_running_headers,
            backend=backend,
        )
    else:
        if not isinstance(source, (str, Path)):
            raise TypeError(f"Expected str or Path for Markdown source, got {type(source).__name__}")
        lines = ingest_markdown(source)

    footnotes = extract_footnotes_from_lines(lines)
    if as_flat_dict:
        data: Any = {fn.id: fn.content for fn in footnotes}
    else:
        data = [fn.to_dict() for fn in footnotes]
    if as_json:
        return json.dumps(data, indent=2, ensure_ascii=False)
    return data


def parse_markdown(
    source: str | Path,
    schema: DocumentSchema | Mapping[str, Any] | Sequence[Any] | str | Path,
    *,
    as_json: bool = False,
    as_flat_dict: bool = False,
    include_metadata: bool = True,
) -> dict[str, Any] | str:
    """Convenience function to parse Markdown or plain text with a schema."""
    parser = SchemaParser(schema)
    return parser.parse_markdown(
        source,
        as_json=as_json,
        as_flat_dict=as_flat_dict,
        include_metadata=include_metadata,
    )


def parse_pdf(
    source: str | Path | bytes | BinaryIO,
    schema: DocumentSchema | Mapping[str, Any] | Sequence[Any] | str | Path,
    *,
    as_json: bool = False,
    as_flat_dict: bool = False,
    include_metadata: bool = True,
    backend: str = "auto",
) -> dict[str, Any] | str:
    """Convenience function to parse a PDF file/bytes with a schema."""
    parser = SchemaParser(schema)
    return parser.parse_pdf(
        source,
        as_json=as_json,
        as_flat_dict=as_flat_dict,
        include_metadata=include_metadata,
        backend=backend,
    )
