"""Tests for ignoring footnotes in main section text and extracting footnotes separately."""

import json
from pathlib import Path
import pymupdf as fitz

from pdfschemaparsing import SchemaParser, extract_footnotes, parse_markdown, parse_pdf


def test_markdown_footnotes_ignored_in_main_text_and_extracted() -> None:
    md_doc = """\
# 1. Executive Summary
Revenue increased by 24% year-over-year[^1] driven by enterprise adoption.

[^1]: Audited figures exclude Q4 divestitures.
    Additional accounting details are available in Appendix B.

# 2. Methodology
We evaluated 10,000 samples across three regions[^note-b].
² Regional weights were calibrated using 2025 census data.

---
[^note-b]: Europe, North America, and APAC.
[3] Secondary benchmark results from external lab.
"""

    schema = {
        "sections": ["Executive Summary", "Methodology"],
    }

    parser = SchemaParser(schema)
    flat_main = parser.parse_markdown(md_doc, as_flat_dict=True)

    # Verify footnotes and inline [^...] markers are excluded from main text
    assert flat_main["Executive Summary"] == "Revenue increased by 24% year-over-year driven by enterprise adoption."
    assert flat_main["Methodology"] == "We evaluated 10,000 samples across three regions."
    for text in flat_main.values():
        assert "Audited figures" not in text
        assert "Appendix B" not in text
        assert "Regional weights" not in text
        assert "Europe, North America" not in text
        assert "Secondary benchmark" not in text

    # Verify method on SchemaParser and standalone function extract all footnotes
    fn_dict = parser.extract_footnotes(md_doc, as_flat_dict=True)
    assert fn_dict == {
        "1": "Audited figures exclude Q4 divestitures.\nAdditional accounting details are available in Appendix B.",
        "2": "Regional weights were calibrated using 2025 census data.",
        "note-b": "Europe, North America, and APAC.",
        "3": "Secondary benchmark results from external lab.",
    }

    fn_list = extract_footnotes(md_doc)
    assert len(fn_list) == 4
    ids = [item["id"] for item in fn_list]
    assert ids == ["1", "2", "note-b", "3"]

    # Also test JSON serialization of footnotes
    fn_json = parser.extract_footnotes(md_doc, as_json=True)
    parsed_fn = json.loads(fn_json)
    assert len(parsed_fn) == 4
    assert parsed_fn[0]["content"].startswith("Audited figures exclude Q4 divestitures.")


def test_pdf_footnotes_ignored_in_main_text_and_extracted(tmp_path: Path) -> None:
    pdf_path = tmp_path / "doc_with_footnotes.pdf"
    doc = fitz.open()

    # Page 1
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((72, 80), "1. Introduction", fontsize=14)
    p1.insert_text((72, 110), "This clinical study enrolled 450 participants.", fontsize=11)
    p1.insert_text((72, 130), "All endpoints met statistical significance.", fontsize=11)
    # Bottom of Page 1 footnotes (smaller font 8.5pt, near y=700)
    p1.insert_text((72, 690), "1 Participants were recruited across 12 medical centers.", fontsize=8.5)
    p1.insert_text((72, 703), "  Informed consent was obtained prior to screening.", fontsize=8.5)
    p1.insert_text((72, 720), "[2] Statistical threshold was pre-specified at p < 0.01.", fontsize=8.5)

    # Page 2
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 80), "2. Conclusion", fontsize=14)
    p2.insert_text((72, 110), "The treatment demonstrated a favorable safety profile.", fontsize=11)
    # Bottom of Page 2 footnote
    p2.insert_text((72, 710), "* Follow-up monitoring continues through 2027.", fontsize=8.5)

    doc.save(str(pdf_path))
    doc.close()

    schema = {"sections": ["Introduction", "Conclusion"]}
    parser = SchemaParser(schema)

    main_sections = parse_pdf(pdf_path, schema, as_flat_dict=True)
    assert main_sections["Introduction"] == (
        "This clinical study enrolled 450 participants.\n"
        "All endpoints met statistical significance."
    )
    assert main_sections["Conclusion"] == "The treatment demonstrated a favorable safety profile."

    # Verify footnotes are completely absent from main text
    for val in main_sections.values():
        assert "12 medical centers" not in val
        assert "Statistical threshold" not in val
        assert "Follow-up monitoring" not in val

    # Verify extracted footnotes from PDF
    pdf_footnotes = parser.extract_footnotes(pdf_path)
    assert len(pdf_footnotes) == 3
    assert pdf_footnotes[0] == {
        "id": "1",
        "content": "Participants were recruited across 12 medical centers.\nInformed consent was obtained prior to screening.",
        "page": 1,
    }
    assert pdf_footnotes[1] == {
        "id": "2",
        "content": "Statistical threshold was pre-specified at p < 0.01.",
        "page": 1,
    }
    assert pdf_footnotes[2] == {
        "id": "*",
        "content": "Follow-up monitoring continues through 2027.",
        "page": 2,
    }
