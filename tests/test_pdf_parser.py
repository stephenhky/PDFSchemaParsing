"""Tests for PDF ingestion, layout-shift y-tolerance clustering, running header stripping, and end-to-end PDF parsing."""

from pathlib import Path
import pymupdf as fitz

from pdfschemaparsing import SchemaParser, parse_pdf, pdf_to_markdown
from pdfschemaparsing.ingestors import SpanFragment, reconstruct_lines_from_spans


def test_y_tolerance_line_reconstruction_with_jitter() -> None:
    # Simulate words on the same visual line with vertical layout jitter (+/- 2.2 pt)
    # and out-of-order stream emission
    spans = [
        SpanFragment(text="SUMMARY", x0=140.0, y0=101.8, font_size=14.0, is_bold=True),
        SpanFragment(text="1.", x0=50.0, y0=100.0, font_size=14.0, is_bold=True),
        SpanFragment(text="EXECUTIVE", x0=72.0, y0=99.6, font_size=14.0, is_bold=True),
        SpanFragment(text="Revenue grew", x0=50.0, y0=130.0, font_size=11.0),
        SpanFragment(text="by 25% in Q4.", x0=125.0, y0=131.9, font_size=11.0),
    ]
    lines = reconstruct_lines_from_spans(spans, y_tolerance=3.0, page=1)
    non_blank = [ln for ln in lines if not ln.is_blank]
    assert len(non_blank) == 2
    assert non_blank[0].text == "1. EXECUTIVE SUMMARY"
    assert non_blank[0].is_bold is True
    assert non_blank[1].text == "Revenue grew by 25% in Q4."


def test_end_to_end_pdf_with_layout_shifts_and_running_headers(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample_shifted.pdf"
    doc = fitz.open()

    # Page 1: Running header, jittered section heading, hyphenated body text, page number footer
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((72, 36), "CONFIDENTIAL ANNUAL REPORT 2026", fontsize=9)
    # Jittered words on y=80..82 for "I. EXECUTIVE SUMMARY"
    p1.insert_text((72, 80.0), "I.", fontsize=14)
    p1.insert_text((95, 81.7), "EXECUTIVE", fontsize=14)
    p1.insert_text((185, 79.4), "SUMMARY:", fontsize=14)
    p1.insert_text((72, 110), "Our cloud division outperformed expec-", fontsize=11)
    p1.insert_text((72, 125), "tations across all regions.", fontsize=11)

    p1.insert_text((72, 170), "2. System Architecture & Design", fontsize=14)
    p1.insert_text((72, 195), "The platform uses a modular pipeline", fontsize=11)
    p1.insert_text((280, 755), "Page 1 of 2", fontsize=9)

    # Page 2: Same running header, continuation of section 2, then Section 3, and footer
    p2 = doc.new_page(width=612, height=792)
    p2.insert_text((72, 36), "CONFIDENTIAL ANNUAL REPORT 2026", fontsize=9)
    p2.insert_text((72, 80), "with resilient layout-shift tolerances.", fontsize=11)
    p2.insert_text((72, 130), "III.   CONCLUDING   REMARKS", fontsize=14)
    p2.insert_text((72, 155), "All validation tests passed successfully.", fontsize=11)
    p2.insert_text((280, 755), "Page 2 of 2", fontsize=9)

    doc.save(str(pdf_path))
    doc.close()

    schema = {
        "y_tolerance": 3.5,
        "strip_running_headers": True,
        "sections": [
            {"title": "Executive Summary"},
            {"title": "System Architecture and Design"},
            {"title": "Conclusion", "aliases": ["Concluding Remarks", "Final Remarks"]},
        ],
    }

    # Test with explicit pymupdf backend
    flat = parse_pdf(pdf_path, schema, as_flat_dict=True, backend="pymupdf")
    assert flat["Executive Summary"] == "Our cloud division outperformed expectations across all regions."
    assert (
        flat["System Architecture and Design"]
        == "The platform uses a modular pipeline\nwith resilient layout-shift tolerances."
    )
    assert flat["Conclusion"] == "All validation tests passed successfully."
    # Ensure running header and page numbers were stripped
    for val in flat.values():
        assert "CONFIDENTIAL ANNUAL REPORT" not in val
        assert "Page " not in val

    # Test with pypdf backend (default in "auto" mode)
    flat_pypdf = parse_pdf(pdf_path, schema, as_flat_dict=True, backend="pypdf")
    assert "outperformed expectations" in flat_pypdf["Executive Summary"]
    assert "All validation tests passed successfully." in flat_pypdf["Conclusion"]

    # Test non-flat dictionary output from pypdf backend includes title, original_heading, and content
    non_flat_pypdf = parse_pdf(pdf_path, schema, as_flat_dict=False, backend="pypdf")
    assert [s["title"] for s in non_flat_pypdf["sections"]] == [
        "Executive Summary",
        "System Architecture and Design",
        "Conclusion",
    ]
    assert non_flat_pypdf["sections"][0]["original_heading"] == "I. EXECUTIVE SUMMARY:"

    # Test auto-detection via SchemaParser.parse()
    parser = SchemaParser(schema)
    auto_res = parser.parse(pdf_path, as_flat_dict=True)
    assert auto_res == flat

    # Test schema-guided Markdown conversion: parser.to_markdown()
    schema_md = parser.to_markdown(pdf_path)
    assert "# Executive Summary" in schema_md
    assert "# System Architecture and Design" in schema_md
    assert "# Conclusion" in schema_md
    assert "CONFIDENTIAL ANNUAL REPORT" not in schema_md

    # Test direct schema-free PDF-to-Markdown conversion: pdf_to_markdown()
    direct_md = pdf_to_markdown(pdf_path, y_tolerance=3.5)
    assert "## I. EXECUTIVE SUMMARY:" in direct_md
    assert "Our cloud division outperformed expectations across all regions." in direct_md
    assert "## 2. System Architecture & Design" in direct_md
    assert "CONFIDENTIAL ANNUAL REPORT" not in direct_md
    assert "Page 1 of 2" not in direct_md


def test_pypdf_ctm_transformed_pdf_headings(tmp_path: Path) -> None:
    """Verify pypdf backend extracts headings properly when PDF uses CTM (`cm`) translations instead of `tm`."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    pdf_path = tmp_path / "ctm_translated.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    # Set up standard Helvetica font resource
    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})}
    )

    # Content stream where `tm` stays at (0, 0) (via BT ... ET) and vertical positioning is done purely via `cm`!
    content_stream = DecodedStreamObject()
    stream_text = "\n".join(
        [
            "q 1 0 0 1 72 700 cm BT /F1 14 Tf (1. Introduction) Tj ET Q",
            "q 1 0 0 1 72 675 cm BT /F1 11 Tf (First paragraph of the introduction.) Tj ET Q",
            "q 1 0 0 1 72 620 cm BT /F1 14 Tf (2. Methodology) Tj ET Q",
            "q 1 0 0 1 72 595 cm BT /F1 11 Tf (Details of the experimental setup.) Tj ET Q",
        ]
    )
    content_stream.set_data(stream_text.encode("latin1"))
    page[NameObject("/Contents")] = content_stream

    with open(pdf_path, "wb") as f:
        writer.write(f)

    schema = {"sections": ["Introduction", "Methodology"]}
    res = parse_pdf(pdf_path, schema, backend="pypdf")
    titles = [sec["title"] for sec in res["sections"]]
    assert titles == ["Introduction", "Methodology"]
    assert res["sections"][0]["original_heading"] == "1. Introduction"
    assert res["sections"][0]["content"] == "First paragraph of the introduction."
    assert res["sections"][1]["original_heading"] == "2. Methodology"
    assert res["sections"][1]["content"] == "Details of the experimental setup."


