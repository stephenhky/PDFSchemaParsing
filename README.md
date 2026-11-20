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
  - **Footnote Filtering & Extraction**: Automatically excludes Markdown and bottom-of-page PDF footnotes (and inline `[^id]` references) from main section inner texts, and provides `parser.extract_footnotes(...)` / `extract_footnotes(...)` to retrieve them separately.
  - **Consecutive Repeating Sections**: Supports consecutive headings (`"Chapter 1"`, `"Chapter 2"`, `"Chapter III - Applications"`, etc.) sharing the same subsections via placeholder syntax (`"Chapter {n}"` / `"Chapter *"`) or `repeatable=True`.
  - **Direct PDF-to-Markdown Conversion**: Converts PDFs directly into clean Markdown via `pdf_to_markdown(...)` (without a schema) or `parser.to_markdown(...)` (with a schema).
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

### 5. Ignoring Footnotes in Main Text & Extracting Footnotes Separately

Footnotes in both Markdown (`[^1]: ...`, `¹ ...`, `[1] ...`) and PDF documents (bottom-of-page smaller-font footnotes) are automatically excluded from the returned section inner texts. You can retrieve all footnotes using `parser.extract_footnotes(...)` or the standalone `extract_footnotes(...)` function:

```python
from pdfschemaparsing import SchemaParser, extract_footnotes

md_with_footnotes = """
# 1. Executive Summary
Revenue increased by 24% year-over-year[^1] driven by enterprise adoption.

[^1]: Audited figures exclude Q4 divestitures.
    Additional accounting details are available in Appendix B.

# 2. Methodology
We evaluated 10,000 samples across three regions[^note-b].
² Regional weights were calibrated using 2025 census data.

---
[^note-b]: Europe, North America, and APAC.
"""

parser = SchemaParser(["Executive Summary", "Methodology"])

# 1. Main section text automatically ignores footnotes and strips inline [^...] markers
print(parser.parse_markdown(md_with_footnotes, as_flat_dict=True, as_json=True))

# 2. Extract footnotes via SchemaParser method or standalone extract_footnotes()
print(parser.extract_footnotes(md_with_footnotes, as_flat_dict=True, as_json=True))
# Or as a structured list with page numbers:
# print(extract_footnotes("annual_report.pdf", as_json=True))
```

**Main Text Output (footnotes excluded):**
```json
{
  "Executive Summary": "Revenue increased by 24% year-over-year driven by enterprise adoption.",
  "Methodology": "We evaluated 10,000 samples across three regions."
}
```

**Extracted Footnotes Output (`parser.extract_footnotes(..., as_flat_dict=True)`):**
```json
{
  "1": "Audited figures exclude Q4 divestitures.\nAdditional accounting details are available in Appendix B.",
  "2": "Regional weights were calibrated using 2025 census data.",
  "note-b": "Europe, North America, and APAC."
}
```

---

### 6. Consecutive Repeating Headings Sharing the Same Subsections (`Chapter 1`, `Chapter 2`, ...)

For consecutive sections like `"Chapter 1"`, `"Chapter 2: Advanced Layout Recovery"`, `"Chapter III - Applications"` that all share the same internal subsection template, specify `"title": "Chapter {n}"` (or `"Chapter *"`, or set `"repeatable": True` on `"title": "Chapter"`):

```python
from pdfschemaparsing import parse_markdown

book_md = """
# Preface
Welcome to the textbook.

# Chapter 1
Introductory text for Chapter 1.

## 1.1 Motivation
Why we study resilient parsing.

## 1.2 Summary
Key takeaways of Chapter 1.

# Chapter 2: Advanced Layout Recovery
Introductory text for Chapter 2.

## 2.1 Motivation
Handling PDF coordinate jitter.

## 2.2 Summary
Key takeaways of Chapter 2.

# Chapter III - Applications
Introductory text for Chapter 3.

## 3.1 Motivation
Real-world deployment scenarios.

## 3.2 Summary
Key takeaways of Chapter 3.
"""

schema = {
    "ordered": True,
    "sections": [
        {"title": "Preface"},
        {
            "title": "Chapter {n}",  # Or {"title": "Chapter", "repeatable": True}
            "subsections": [
                {"title": "Motivation", "aliases": ["Background"]},
                {"title": "Summary", "aliases": ["Takeaways"]},
            ],
        },
    ],
}

print(parse_markdown(book_md, schema, as_flat_dict=True, as_json=True))
```

**Output:**
```json
{
  "Preface": "Welcome to the textbook.",
  "Chapter 1": {
    "content": "Introductory text for Chapter 1.",
    "subsections": {
      "Motivation": "Why we study resilient parsing.",
      "Summary": "Key takeaways of Chapter 1."
    }
  },
  "Chapter 2: Advanced Layout Recovery": {
    "content": "Introductory text for Chapter 2.",
    "subsections": {
      "Motivation": "Handling PDF coordinate jitter.",
      "Summary": "Key takeaways of Chapter 2."
    }
  },
  "Chapter III - Applications": {
    "content": "Introductory text for Chapter 3.",
    "subsections": {
      "Motivation": "Real-world deployment scenarios.",
      "Summary": "Key takeaways of Chapter 3."
    }
  }
}
```

---

### 7. Converting a PDF Document to Markdown Text

You can convert a PDF to clean Markdown in two ways:
1. **Without a schema (`pdf_to_markdown`)**: Reconstructs jittered lines using `y_tolerance`, strips running headers/footers and page numbers, de-hyphenates wrapped words, excludes footnotes (or appends them at the bottom with `include_footnotes_at_end=True`), and infers `#` / `##` / `###` Markdown headings from PDF font sizes and bolding.
2. **With a schema (`parser.to_markdown`)**: Segments the PDF according to your schema and renders the canonical section hierarchy as `# Section` / `## Subsection` Markdown.

```python
from pdfschemaparsing import SchemaParser, pdf_to_markdown

# Option A: Direct schema-free PDF -> Markdown conversion
md_text = pdf_to_markdown(
    "annual_report.pdf",
    y_tolerance=3.5,
    strip_running_headers=True,
    ignore_footnotes=True,
    include_footnotes_at_end=False,  # Set True to append [^id]: ... at the bottom
)
print(md_text)

# Option B: Schema-normalized PDF -> Markdown conversion
parser = SchemaParser(
    {
        "sections": [
            {"title": "Executive Summary", "aliases": ["Summary"]},
            {"title": "System Architecture and Design"},
            {"title": "Conclusion", "aliases": ["Concluding Remarks"]},
        ]
    }
)
clean_schema_md = parser.to_markdown("annual_report.pdf", include_footnotes=False)
print(clean_schema_md)
```

---

## Running Tests

In the `textparse` conda environment:

```bash
conda run -n textparse pytest -v
```
