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

## Quick Usage

```python
from pdfschemaparsing import SchemaParser, parse_markdown, parse_pdf

schema = {
    "fuzzy_threshold": 0.85,
    "sections": [
        {"title": "Executive Summary", "aliases": ["Summary", "Overview"]},
        {
            "title": "Financial Performance",
            "aliases": ["Financials"],
            "subsections": [
                {"title": "Revenue Breakdown"},
                {"title": "Operating Expenses"},
            ],
        },
        {"title": "Risk Factors", "aliases": ["Risks"]},
    ],
}

# Parse Markdown (returns structured dict or JSON string when as_json=True)
json_str = parse_markdown("report.md", schema, as_json=True)

# Or get a direct {"Section Title": "inner text"} dictionary from a PDF
flat_dict = parse_pdf("report.pdf", schema, as_flat_dict=True)
```

## Running Tests

In the `textparse` conda environment:

```bash
conda run -n textparse pytest -v
```
