# PDFSchemaParsing

A resilient, schema-driven Python parser for **PDF** and **Markdown** documents. Given a user-defined schema of sections (and optional nested subsections), `pdfschemaparsing` segments documents into structured JSON containing canonical section titles and cleaned inner texts.

## Key Features

- **Resilient to Whitespace & Unicode Quirks**: Normalizes compatibility characters (`NFKC`), ligatures (`ﬁ` -> `fi`), non-breaking spaces (`\u00a0`), zero-width spaces (`\u200b`), soft hyphens, and erratic line/tab spacing.
- **Resilient to Casing, Numbering & Punctuation**: Automatically handles `UPPERCASE`, `Title Case`, `lowercase`, Markdown adornments (`#`, `**`, `__`, Setext `===`/`---`), leading numbering (`1.2.`, `II.`, `(A)`, `Section 1:`), and `&` vs `and`.
- **Resilient to Layout Shifts**:
  - **Y-Tolerance Line Reconstruction (PDF)**: Groups horizontally scattered words into visual lines even when vertical coordinates jitter ($\pm 3\text{ pt}$).
  - **Multi-Line Wrapped Headings**: Detects headings wrapped across two lines.
  - **Inline Headings**: Splits bold/delimited inline headers (`**Abstract:** This study...`) into the matched section and its body text.
  - **Running Header & Footer Removal (PDF)**: Strips repeated page headers/footers and standalone page numbers across multi-page PDFs.
  - **Fuzzy Matching**: Tolerates minor OCR artifacts or typos in headings via configurable similarity thresholds.

---

## Examples

### 1. Basic Markdown Parsing (Noisy Casing, Numbering & Whitespace)

You can pass a schema as a list of titles, a shorthand `{"Canonical Title": ["Alias 1", "Alias 2"]}` dictionary, or a full configuration dictionary.

```python
from pdfschemaparsing import parse_markdown

markdown_text = """
I.   EXECUTIVE\u00a0\u00a0SUMMARY
================================
Our Q3 deployment reduced latency by 35%   across all edge nodes.

### Section 2: Materials & Methods:
We benchmarked   1,200 requests per second using synthetic workloads.

**3) Key   Findings**
Throughput remained stable under peak load.

---
**Conclusion:** The new architecture is ready for production.
"""

# Shorthand schema: {"Canonical Title": ["Alias 1", "Alias 2"]}
schema = {
    "Executive Summary": ["Overview", "Summary"],
    "Methodology": ["Materials and Methods", "Methods"],
    "Results": ["Key Findings", "Experimental Results"],
    "Conclusion": ["Concluding Remarks"],
}

# 1a. Flat dictionary mapping {title: inner_text} (as JSON string)
flat_json = parse_markdown(markdown_text, schema, as_flat_dict=True, as_json=True)
print(flat_json)
```

**Output (`as_flat_dict=True`):**
```json
{
  "Executive Summary": "Our Q3 deployment reduced latency by 35% across all edge nodes.",
  "Methodology": "We benchmarked 1,200 requests per second using synthetic workloads.",
  "Results": "Throughput remained stable under peak load.",
  "Conclusion": "The new architecture is ready for production."
}
```

If you omit `as_flat_dict=True` (the default), you get the structured list format with matched header metadata:

```python
structured_json = parse_markdown(markdown_text, schema, as_json=True)
print(structured_json)
```

**Output (default structured format):**
```json
{
  "sections": [
    {
      "title": "Executive Summary",
      "content": "Our Q3 deployment reduced latency by 35% across all edge nodes.",
      "matched_header": "I. EXECUTIVE SUMMARY",
      "subsections": []
    },
    {
      "title": "Methodology",
      "content": "We benchmarked 1,200 requests per second using synthetic workloads.",
      "matched_header": "### Section 2: Materials & Methods:",
      "subsections": []
    },
    {
      "title": "Results",
      "content": "Throughput remained stable under peak load.",
      "matched_header": "**3) Key Findings**",
      "subsections": []
    },
    {
      "title": "Conclusion",
      "content": "The new architecture is ready for production.",
      "matched_header": "Conclusion",
      "subsections": []
    }
  ]
}
```

---

### 2. Hierarchical Subsections, Wrapped Headers & Fuzzy Typo Matching

When documents have nested sections, minor OCR typos in headings (e.g., `"Methodolgy"`), or headings wrapped across two lines, configure `DocumentSchema` (or a dict) with `subsections` and `fuzzy_threshold`:

```python
from pdfschemaparsing import SchemaParser

doc = """
Document Preamble: Internal Technical Report v2.4

# 1. Abstrct
This study evaluates layout-resilient document parsing.

# 2. System Architecture
and Implementation
High-level overview of the ingestion pipeline.

## 2.1 PDF Line   Reconstruction
Words are clustered vertically within y_tolerance.

## 2.2 Fuzzy   Header Matching
Handles minor OCR errors in section titles.

# 3. Final   Remarks
All benchmarks passed.
"""

schema = {
    "capture_preamble": True,
    "fuzzy_threshold": 0.82,  # Tolerates "Abstrct" -> "Abstract"
    "sections": [
        {"title": "Abstract", "required": True},
        {
            "title": "System Architecture and Implementation",
            "subsections": [
                {"title": "PDF Line Reconstruction"},
                {"title": "Fuzzy Header Matching"},
            ],
        },
        {"title": "Conclusion", "aliases": ["Final Remarks"]},
    ],
}

parser = SchemaParser(schema)
print(parser.parse_markdown(doc, as_flat_dict=True, as_json=True))
```

**Output:**
```json
{
  "_preamble": "Document Preamble: Internal Technical Report v2.4",
  "Abstract": "This study evaluates layout-resilient document parsing.",
  "System Architecture and Implementation": {
    "content": "High-level overview of the ingestion pipeline.",
    "subsections": {
      "PDF Line Reconstruction": "Words are clustered vertically within y_tolerance.",
      "Fuzzy Header Matching": "Handles minor OCR errors in section titles."
    }
  },
  "Conclusion": "All benchmarks passed."
}
```

---

### 3. Parsing a PDF File (Resilient to Vertical Jitter & Running Headers/Footers)

`parse_pdf` (or `SchemaParser.parse`) accepts a file path (`str` or `Path`), raw `bytes`, or a file-like binary stream (`BytesIO`). It automatically clusters words with vertical jitter (`y_tolerance`), strips repeated running headers/footers across pages, and rejoins hyphenated line wraps (`"expec-\ntations"` → `"expectations"`).

```python
from pathlib import Path
from pdfschemaparsing import SchemaParser, parse_pdf

schema = {
    "y_tolerance": 3.5,            # Group words within +/- 3.5pt vertical jitter into the same line
    "strip_running_headers": True, # Strip repeated top/bottom headers & page numbers ("Page 1 of 5")
    "include_missing": True,       # Emit null for schema sections absent from the PDF
    "sections": [
        {"title": "Executive Summary", "aliases": ["Summary"]},
        {"title": "Financial Performance", "aliases": ["Financials", "Revenue & Earnings"]},
        {"title": "Risk Factors", "aliases": ["Risks"]},
        {"title": "Appendix"},     # Will be null if not present in the PDF
    ],
}

# Option A: Direct convenience function
result_dict = parse_pdf(Path("annual_report.pdf"), schema, as_flat_dict=True)

# Option B: Reusing a SchemaParser instance across multiple PDFs or Markdown files
parser = SchemaParser(schema)
json_output = parser.parse("annual_report.pdf", as_json=True)
print(json_output)
```

---

### 4. Custom Regex Patterns for Specialized Headings

If a section title follows a dynamic pattern (such as `"Item 1A. Risk Factors"` in SEC 10-K filings or numbered clinical trial endpoints), you can supply a `pattern` regex on any `SectionSpec`:

```python
from pdfschemaparsing import DocumentSchema, SectionSpec, SchemaParser

schema = DocumentSchema(
    ordered=True,
    sections=[
        SectionSpec(
            title="Business Overview",
            pattern=r"^item\s+1[\.\:\s]+business",
            required=True,
        ),
        SectionSpec(
            title="Risk Factors",
            pattern=r"^item\s+1a[\.\:\s]+risk\s+factors",
            required=True,
        ),
    ],
)

parser = SchemaParser(schema)
filing_text = """
ITEM 1. BUSINESS
We design and manufacture modular energy storage systems.

ITEM 1A. RISK FACTORS
Fluctuations in lithium carbonate supply could impact margins.
"""

print(parser.parse(filing_text, as_flat_dict=True, as_json=True))
```

**Output:**
```json
{
  "Business Overview": "We design and manufacture modular energy storage systems.",
  "Risk Factors": "Fluctuations in lithium carbonate supply could impact margins."
}
```

---

## Running Tests

In the `textparse` conda environment:

```bash
conda run -n textparse pytest -v
```
