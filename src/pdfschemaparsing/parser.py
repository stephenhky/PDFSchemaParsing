"""Core SchemaParser orchestrating ingestion, section boundary segmentation, and JSON formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.ingestors.markdown_ingestor import ingest_markdown
from pdfschemaparsing.ingestors.pdf_ingestor import ingest_pdf
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


@dataclass
class ParseResult:
    """Container for parsed document sections with flexible dictionary and JSON serialization."""

    sections: list[ParsedSection]
    preamble: str | None = None

    def to_dict(
        self,
        *,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
    ) -> dict[str, Any]:
        """Convert parsed sections to either a structured list dictionary or `{title: inner_text}` mapping."""
        if as_flat_dict:
            out: dict[str, Any] = {}
            if self.preamble is not None:
                out["_preamble"] = self.preamble
            for sec in self.sections:
                if sec.subsections:
                    sub_map = {sub.title: sub.content for sub in sec.subsections}
                    out[sec.title] = {
                        "content": sec.content,
                        "subsections": sub_map,
                    }
                else:
                    out[sec.title] = sec.content
            return out

        result: dict[str, Any] = {
            "sections": [sec.to_dict(include_metadata=include_metadata) for sec in self.sections]
        }
        if self.preamble is not None:
            result["preamble"] = self.preamble
        return result

    def to_json(
        self,
        *,
        as_flat_dict: bool = False,
        include_metadata: bool = True,
        indent: int = 2,
    ) -> str:
        """Serialize the parsed result directly to a JSON string."""
        return json.dumps(
            self.to_dict(as_flat_dict=as_flat_dict, include_metadata=include_metadata),
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
        sections, preamble = self._segment_block(
            lines=lines,
            specs=self.schema.sections,
            is_top_level=True,
        )
        return ParseResult(
            sections=sections,
            preamble=preamble if self.schema.capture_preamble else None,
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
            preamble = clean_inner_text([ln.text for ln in lines[:first_idx]])

        parsed_by_match: list[ParsedSection] = []
        for idx, match in enumerate(matches):
            body_start = match.end_line_idx + 1
            body_end = matches[idx + 1].start_line_idx if (idx + 1 < len(matches)) else len(lines)
            slice_lines = [
                ln for ln in lines[body_start:body_end] if not ln.is_decorative_rule
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
                    lead_text = clean_inner_text([ln.text for ln in slice_lines[:lead_end]])
                    subsections, _ = self._segment_block(
                        lines=slice_lines,
                        specs=match.spec.subsections,
                        is_top_level=False,
                    )
                    parsed_by_match.append(
                        ParsedSection(
                            title=match.spec.title,
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
                                f"Required subsection '{sub_spec.title}' under '{match.spec.title}' was not found."
                            )

            body_text = clean_inner_text([ln.text for ln in slice_lines])
            parsed_by_match.append(
                ParsedSection(
                    title=match.spec.title,
                    content=body_text,
                    matched_header=match.matched_header,
                    subsections=[],
                )
            )

        # Merge duplicate occurrences of the same canonical section or preserve missing sections if requested
        if self.schema.include_missing:
            by_title: dict[str, ParsedSection] = {}
            for psec in parsed_by_match:
                if psec.title in by_title:
                    existing = by_title[psec.title]
                    combined = "\n\n".join(filter(None, [existing.content, psec.content]))
                    existing.content = combined
                    existing.subsections.extend(psec.subsections)
                else:
                    by_title[psec.title] = psec

            final_sections: list[ParsedSection] = []
            for spec in specs:
                if spec.title in by_title:
                    final_sections.append(by_title[spec.title])
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
