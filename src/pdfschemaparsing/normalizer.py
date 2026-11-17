"""Resilient text normalization utilities for headers and document body text."""

from __future__ import annotations

import re
import unicodedata

# Zero-width and invisible formatting characters
_INVISIBLE_CHARS_RE = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad]"
)

# Any horizontal whitespace (including non-breaking spaces, thin spaces, ideographic spaces, tabs)
_HORIZONTAL_WS_RE = re.compile(r"[^\S\r\n]+")

# Markdown header prefix: e.g. "### "
_MD_ATX_PREFIX_RE = re.compile(r"^\s*#{1,6}\s*")
# Markdown ATX closing hashes: e.g. " ###"
_MD_ATX_SUFFIX_RE = re.compile(r"\s+#+\s*$")

# Markdown inline emphasis/bold/code wrappers on headers: e.g. "**Header**", "__Header__", "`Header`"
_MD_INLINE_WRAP_RE = re.compile(r"^(\*{1,3}|_{1,3}|`+)(.+?)\1$")

# Section numbering prefix:
# Matches forms like:
#   "1.", "1.2.", "1.2.3", "(1)", "[2]", "I.", "IV)", "A.", "b)",
#   "Section 1:", "Part II -", "Chapter 3.", "Appendix A:"
_SECTION_NUMBER_PREFIX_RE = re.compile(
    r"""^\s*
    (?:
        (?:section|sec\.?|part|chapter|chap\.?|appendix|item)\s+
        (?:\d+(?:\.\d+)*|[ivxlcdm]+|[a-z])\s*[:\.\-\)\]]*\s*
        |
        [\(\[]?(?:\d+(?:\.\d+)+|\d+|[ivxlcdm]{1,6}|[a-z])[\)\]]
        (?:\s*[:\.\-\u2013\u2014]\s*|\s+)
        |
        (?:\d+(?:\.\d+)+)\s*[:\.\-\u2013\u2014]?\s+
        |
        (?:\d+|[ivxlcdm]{1,6}|[a-z])(?:\.|\s*[:\-\u2013\u2014])\s+
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Leading bullet / decorative symbols in headers
_LEADING_DECORATION_RE = re.compile(r"^[\s\*\-\+\u2022\u2023\u25e6\u2043\u2219\u00b7\u25aa\u25b8\u25ba>]+")

# Trailing punctuation common in headers (colons, dashes, periods, etc.)
_TRAILING_HEADER_PUNCT_RE = re.compile(r"[\s:\.\-\u2013\u2014;_/\\]+$")

# Non-alphanumeric separator normalization (keep letters, digits, and single spaces)
_NON_WORD_PUNCT_RE = re.compile(r"[^\w\s&/+-]+", re.UNICODE)

# Hyphenated word wrap across line breaks: e.g., "infor-\nmation" -> "information"
_HYPHEN_LINEBREAK_RE = re.compile(r"([A-Za-z]{2,})-\n\s*([a-z]{2,})")

# Multiple consecutive blank lines (3 or more newlines -> 2 newlines)
_EXCESS_NEWLINES_RE = re.compile(r"\n{3,}")


def normalize_unicode_and_whitespace(text: str, preserve_newlines: bool = True) -> str:
    """Normalize unicode compatibility characters, strip invisible chars, and collapse whitespace."""
    if not text:
        return ""
    # NFKC converts ligatures (ﬁ -> fi), fullwidth characters, and compatibility spaces
    norm = unicodedata.normalize("NFKC", text)
    norm = _INVISIBLE_CHARS_RE.sub("", norm)
    # Standardize line endings
    norm = norm.replace("\r\n", "\n").replace("\r", "\n")
    if preserve_newlines:
        lines = [_HORIZONTAL_WS_RE.sub(" ", line).strip() for line in norm.split("\n")]
        return "\n".join(lines)
    return _HORIZONTAL_WS_RE.sub(" ", norm).strip()


def strip_markdown_header_syntax(line: str) -> str:
    """Remove Markdown heading markers (#, **, __, etc.) while preserving inner text."""
    s = line.strip()
    s = _MD_ATX_PREFIX_RE.sub("", s)
    s = _MD_ATX_SUFFIX_RE.sub("", s)
    s = s.strip()
    # Iteratively unwrap bold/italic/code wrappers around the entire heading
    while True:
        match = _MD_INLINE_WRAP_RE.match(s)
        if not match:
            break
        s = match.group(2).strip()
    return s


def canonicalize_header(
    text: str,
    *,
    strip_numbering: bool = True,
    normalize_ampersand: bool = True,
) -> str:
    """Convert a candidate header or schema title into a canonical comparable string.

    Transformations:
    1. Unicode NFKC + invisible character removal + whitespace collapse
    2. Strip Markdown `#`, `**`, `__` wrappers and leading bullets
    3. Optionally strip section numbering prefixes (`1.2.`, `II.`, `Section 1:`, etc.)
    4. Casefold to lowercase
    5. Normalize `&` <-> `and` if enabled
    6. Strip trailing colons/dashes/punctuation and collapse internal spaces
    """
    s = normalize_unicode_and_whitespace(text, preserve_newlines=False)
    if not s:
        return ""

    s = strip_markdown_header_syntax(s)
    s = _LEADING_DECORATION_RE.sub("", s).strip()

    if strip_numbering:
        # Apply up to twice in case of e.g. "Section 1: 1.1 Title"
        for _ in range(2):
            next_s = _SECTION_NUMBER_PREFIX_RE.sub("", s).strip()
            if next_s and next_s != s:
                s = strip_markdown_header_syntax(next_s)
            else:
                break

    s = _TRAILING_HEADER_PUNCT_RE.sub("", s).strip()
    s = strip_markdown_header_syntax(s)
    s = s.casefold()

    if normalize_ampersand:
        s = re.sub(r"\s*&\s*", " and ", s)

    # Replace remaining punctuation (commas, parens, quotes, hyphens, underscores) with spaces
    s = re.sub(r"[^\w\s]+|_", " ", s, flags=re.UNICODE)
    s = _HORIZONTAL_WS_RE.sub(" ", s).strip()
    return s


def clean_inner_text(
    lines: list[str],
    *,
    dehyphenate: bool = True,
) -> str:
    """Clean extracted section inner text while preserving paragraph structure."""
    if not lines:
        return ""
    cleaned_lines = [normalize_unicode_and_whitespace(line, preserve_newlines=False) for line in lines]
    joined = "\n".join(cleaned_lines).strip()
    if dehyphenate:
        joined = _HYPHEN_LINEBREAK_RE.sub(r"\1\2", joined)
    joined = _EXCESS_NEWLINES_RE.sub("\n\n", joined)
    return joined.strip()
