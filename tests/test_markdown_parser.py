"""Tests for Markdown parsing resilience across layout shifts, casing, and whitespace quirks."""

import json
import pytest

from pdfschemaparsing import MissingRequiredSectionError, SchemaParser, parse_markdown


def test_markdown_varied_heading_styles_and_whitespace() -> None:
    md_doc = """\
    Preamble note before sections.

    1. EXECUTIVE\u00a0\u00a0SUMMARY
    ==========================
    The project achieved   all Q1 milestones\twith zero downtime.

    ### II.   background & motivation :
    Legacy systems suffered from   layout shifts.
    We needed a schema-driven approach.

    **3) Experimental   Results**
    Precision reached 99.2% across 500 documents.

    ---
    **Conclusion:** Final remarks on the parser architecture.
    """

    schema = {
        "capture_preamble": True,
        "sections": [
            {"title": "Executive Summary", "aliases": ["Summary"]},
            {"title": "Background and Motivation"},
            {"title": "Results", "aliases": ["Experimental Results", "Findings"]},
            {"title": "Conclusion"},
        ],
    }

    flat = parse_markdown(md_doc, schema, as_flat_dict=True)
    assert flat["_preamble"] == "Preamble note before sections."
    assert flat["Executive Summary"] == "The project achieved all Q1 milestones with zero downtime."
    assert "Legacy systems suffered from layout shifts." in flat["Background and Motivation"]
    assert flat["Results"] == "Precision reached 99.2% across 500 documents."
    assert flat["Conclusion"] == "Final remarks on the parser architecture."


def test_nested_subsections_and_wrapped_headers() -> None:
    # Includes a 2-line wrapped heading ("Financial Performance\nand Outlook")
    md_doc = """\
    # 1. Overview
    High-level company summary.

    # 2. Financial Performance
    and Outlook
    General financial commentary for the fiscal year.

    ## 2.1 Revenue   Breakdown
    North America grew 14%.

    ## 2.2 Operating   Expenses
    R&D investment remained steady.

    # 3. Risk   Factors
    Supply chain and currency fluctuations.
    """

    schema = {
        "sections": [
            "Overview",
            {
                "title": "Financial Performance and Outlook",
                "subsections": [
                    {"title": "Revenue Breakdown"},
                    {"title": "Operating Expenses"},
                ],
            },
            "Risk Factors",
        ]
    }

    parser = SchemaParser(schema)
    structured = parser.parse_markdown(md_doc)
    sections = structured["sections"]
    assert len(sections) == 3
    assert sections[0]["title"] == "Overview"
    assert sections[1]["title"] == "Financial Performance and Outlook"
    assert sections[1]["content"] == "General financial commentary for the fiscal year."
    assert len(sections[1]["subsections"]) == 2
    assert sections[1]["subsections"][0]["title"] == "Revenue Breakdown"
    assert sections[1]["subsections"][0]["content"] == "North America grew 14%."
    assert sections[1]["subsections"][1]["title"] == "Operating Expenses"
    assert sections[1]["subsections"][1]["content"] == "R&D investment remained steady."

    # Verify JSON output serialization
    json_str = parser.parse_markdown(md_doc, as_json=True, as_flat_dict=True)
    parsed_json = json.loads(json_str)
    assert parsed_json["Overview"] == "High-level company summary."
    assert parsed_json["Financial Performance and Outlook"]["subsections"]["Revenue Breakdown"] == "North America grew 14%."


def test_fuzzy_matching_and_code_fence_immunity() -> None:
    # Minor OCR / typo in "Methodolgy" and a fake heading inside a code block
    md_doc = """\
    # Abstrct
    Short summary of the paper.

    ```markdown
    # Conclusion
    This is inside a code block and must NOT trigger the Conclusion section early!
    ```

    ## 2. Methodolgy
    We applied resilient fuzzy matching.

    # 3. Conclusion
    Real conclusion at the end.
    """

    schema = {
        "fuzzy_threshold": 0.82,
        "sections": ["Abstract", "Methodology", "Conclusion"],
    }

    flat = parse_markdown(md_doc, schema, as_flat_dict=True)
    assert "```markdown" in flat["Abstract"]
    assert "must NOT trigger the Conclusion section early!" in flat["Abstract"]
    assert flat["Methodology"] == "We applied resilient fuzzy matching."
    assert flat["Conclusion"] == "Real conclusion at the end."


def test_required_section_and_include_missing() -> None:
    md_doc = "# Introduction\nHello world."
    schema_req = {
        "sections": [
            {"title": "Introduction", "required": True},
            {"title": "Methods", "required": True},
        ]
    }
    with pytest.raises(MissingRequiredSectionError):
        parse_markdown(md_doc, schema_req)

    schema_opt = {
        "include_missing": True,
        "sections": ["Introduction", "Methods"],
    }
    flat = parse_markdown(md_doc, schema_opt, as_flat_dict=True)
    assert flat == {"Introduction": "Hello world.", "Methods": None}
