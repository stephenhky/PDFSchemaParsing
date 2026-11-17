"""Unit tests for resilient text normalization and schema validation."""

import pytest

from pdfschemaparsing import (
    DocumentSchema,
    SchemaValidationError,
    SectionSpec,
    canonicalize_header,
    clean_inner_text,
    normalize_unicode_and_whitespace,
)


def test_unicode_and_whitespace_quirks() -> None:
    # Non-breaking spaces (\u00a0), zero-width spaces (\u200b), soft hyphens (\u00ad), ligatures (\ufb01)
    quirky = "  Finan\u00adcial\u200b   \u00a0\t  Pro\ufb01le \r\n  Next\t\tLine  "
    norm = normalize_unicode_and_whitespace(quirky, preserve_newlines=True)
    assert norm == "Financial Profile\nNext Line"


def test_canonicalize_header_casing_numbering_and_markdown() -> None:
    variants = [
        "# 1. Executive Summary",
        "### I. EXECUTIVE   SUMMARY ###",
        "**Section 1: Executive\u00a0Summary:**",
        "1.2.3) executive summary -",
        "Part A: __Executive Summary__",
        "• Executive   Summary",
    ]
    for v in variants:
        assert canonicalize_header(v) == "executive summary", f"Failed on {v!r}"


def test_canonicalize_header_ampersand_equivalence() -> None:
    h1 = canonicalize_header("Materials & Methods")
    h2 = canonicalize_header("2. MATERIALS AND METHODS:")
    assert h1 == h2 == "materials and methods"


def test_clean_inner_text_dehyphenation_and_blank_lines() -> None:
    lines = [
        "This is an impor-",
        "tant discovery in compu-",
        "ter science.",
        "",
        "",
        "",
        "Second paragraph\u00a0with\todd   spaces.",
    ]
    cleaned = clean_inner_text(lines, dehyphenate=True)
    assert cleaned == "This is an important discovery in computer science.\n\nSecond paragraph with odd spaces."


def test_schema_loading_formats() -> None:
    # List of strings
    s1 = DocumentSchema.from_any(["Abstract", "Introduction", "Conclusion"])
    assert [sec.title for sec in s1.sections] == ["Abstract", "Introduction", "Conclusion"]

    # Shorthand dict with aliases
    s2 = DocumentSchema.from_any(
        {
            "Methods": ["Methodology", "Materials & Methods"],
            "Results": ["Findings"],
        }
    )
    assert s2.sections[0].title == "Methods"
    assert s2.sections[0].aliases == ["Methodology", "Materials & Methods"]

    # JSON string
    s3 = DocumentSchema.from_any(
        '{"ordered": true, "sections": [{"title": "Overview", "required": true}]}'
    )
    assert s3.ordered is True
    assert s3.sections[0].required is True

    with pytest.raises(SchemaValidationError):
        DocumentSchema.from_any([])
