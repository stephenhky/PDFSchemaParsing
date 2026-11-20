"""Base intermediate representation for document lines across Markdown and PDF ingestors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextLine:
    """Represents a single logical visual line in a document."""

    text: str
    line_index: int
    page: int = 1
    raw_text: str | None = None
    is_heading_hint: bool = False
    heading_level: int | None = None
    font_size: float | None = None
    is_bold: bool = False
    y0: float | None = None
    x0: float | None = None
    in_code_block: bool = False
    is_decorative_rule: bool = False
    is_footnote: bool = False
    footnote_id: str | None = None
    footnote_body: str | None = None

    @property
    def is_blank(self) -> bool:
        return not self.text or not self.text.strip()
