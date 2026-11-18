"""Tests for consecutive repeating headings (e.g., Chapter 1, Chapter 2, Chapter 3) sharing the same subsections."""

from pdfschemaparsing import SchemaParser, parse_markdown


def test_consecutive_chapters_with_shared_subsections_placeholder_syntax() -> None:
    md_doc = """\
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

# References
1. Smith et al. (2025)
"""

    schema = {
        "ordered": True,
        "sections": [
            {"title": "Preface"},
            {
                "title": "Chapter {n}",
                "subsections": [
                    {"title": "Motivation", "aliases": ["Background"]},
                    {"title": "Summary", "aliases": ["Takeaways"]},
                ],
            },
            {"title": "References"},
        ],
    }

    # 1. Test flat dict output
    flat = parse_markdown(md_doc, schema, as_flat_dict=True)
    assert list(flat.keys()) == [
        "Preface",
        "Chapter 1",
        "Chapter 2: Advanced Layout Recovery",
        "Chapter III - Applications",
        "References",
    ]
    assert flat["Preface"] == "Welcome to the textbook."
    assert flat["Chapter 1"] == {
        "content": "Introductory text for Chapter 1.",
        "subsections": {
            "Motivation": "Why we study resilient parsing.",
            "Summary": "Key takeaways of Chapter 1.",
        },
    }
    assert flat["Chapter 2: Advanced Layout Recovery"] == {
        "content": "Introductory text for Chapter 2.",
        "subsections": {
            "Motivation": "Handling PDF coordinate jitter.",
            "Summary": "Key takeaways of Chapter 2.",
        },
    }
    assert flat["Chapter III - Applications"] == {
        "content": "Introductory text for Chapter 3.",
        "subsections": {
            "Motivation": "Real-world deployment scenarios.",
            "Summary": "Key takeaways of Chapter 3.",
        },
    }
    assert flat["References"] == "1. Smith et al. (2025)"

    # 2. Test structured list output
    parser = SchemaParser(schema)
    structured = parser.parse_markdown(md_doc)
    assert len(structured["sections"]) == 5
    assert structured["sections"][1]["title"] == "Chapter 1"
    assert structured["sections"][2]["title"] == "Chapter 2: Advanced Layout Recovery"
    assert structured["sections"][3]["title"] == "Chapter III - Applications"


def test_repeatable_flag_and_fuzzy_consecutive_headings() -> None:
    # Uses `repeatable: True` explicitly without `{n}` placeholder, plus a slight typo in "Chpter 2"
    md_doc = """\
# Chapter 1
First chapter overview.
## Exercises
Solve problems 1-5.

# Chpter 2
Second chapter overview (with typo in heading).
## Exercises
Solve problems 6-10.
"""

    schema = {
        "fuzzy_threshold": 0.80,
        "sections": [
            {
                "title": "Chapter",
                "repeatable": True,
                "subsections": ["Exercises"],
            }
        ],
    }

    flat = parse_markdown(md_doc, schema, as_flat_dict=True)
    assert flat["Chapter 1"]["content"] == "First chapter overview."
    assert flat["Chapter 1"]["subsections"]["Exercises"] == "Solve problems 1-5."
    assert flat["Chpter 2"]["content"] == "Second chapter overview (with typo in heading)."
    assert flat["Chpter 2"]["subsections"]["Exercises"] == "Solve problems 6-10."


def test_inner_repeated_headings_and_nested_repeatable_subsections() -> None:
    """Verify inner repeated headings (like '1.1 Section 1', '1.2 Section 2') and 3-level nested subsections."""
    md_doc = """\
# Chapter 1: Fundamentals
Overview of Chapter 1.

## 1.1 Section 1: Linear Models
Intro to linear models.

### Theory
Mathematical derivation for 1.1.

### Experiments
Empirical results for 1.1.

## 1.2 Section 2: Nonlinear Models
Intro to nonlinear models.

### Theory
Mathematical derivation for 1.2.

### Experiments
Empirical results for 1.2.

# Chapter 2: Deployment
Overview of Chapter 2.

## 2.1 Section 1: Cloud Serving
Intro to cloud serving.

### Theory
Latency model for 2.1.

### Experiments
Throughput results for 2.1.
"""

    schema = {
        "ordered": True,
        "sections": [
            {
                "title": "Chapter {n}",
                "subsections": [
                    {
                        "title": "Section {n}",
                        "subsections": [
                            {"title": "Theory"},
                            {"title": "Experiments"},
                        ],
                    }
                ],
            }
        ],
    }

    flat = parse_markdown(md_doc, schema, as_flat_dict=True)
    assert "Chapter 1: Fundamentals" in flat
    ch1_subs = flat["Chapter 1: Fundamentals"]["subsections"]
    assert "1.1 Section 1: Linear Models" in ch1_subs
    assert "1.2 Section 2: Nonlinear Models" in ch1_subs
    assert ch1_subs["1.1 Section 1: Linear Models"] == {
        "content": "Intro to linear models.",
        "subsections": {
            "Theory": "Mathematical derivation for 1.1.",
            "Experiments": "Empirical results for 1.1.",
        },
    }
    assert ch1_subs["1.2 Section 2: Nonlinear Models"] == {
        "content": "Intro to nonlinear models.",
        "subsections": {
            "Theory": "Mathematical derivation for 1.2.",
            "Experiments": "Empirical results for 1.2.",
        },
    }

    ch2_subs = flat["Chapter 2: Deployment"]["subsections"]
    assert ch2_subs["2.1 Section 1: Cloud Serving"] == {
        "content": "Intro to cloud serving.",
        "subsections": {
            "Theory": "Latency model for 2.1.",
            "Experiments": "Throughput results for 2.1.",
        },
    }


def test_inner_repeated_identical_headings_preserved() -> None:
    """Verify repeated inner headings with the exact same title (e.g. multiple 'Case Study' blocks) are all detected."""
    md_doc = """\
# Chapter 1
Chapter 1 intro.

## Case Study
First case study in healthcare.

## Case Study
Second case study in finance.

## Summary
End of chapter summary.
"""

    schema = {
        "ordered": True,
        "sections": [
            {
                "title": "Chapter {n}",
                "subsections": [
                    {"title": "Case Study", "repeatable": True},
                    {"title": "Summary"},
                ],
            }
        ],
    }

    flat = parse_markdown(md_doc, schema, as_flat_dict=True)
    subs = flat["Chapter 1"]["subsections"]
    assert subs["Case Study"] == "First case study in healthcare."
    assert subs["Case Study (2)"] == "Second case study in finance."
    assert subs["Summary"] == "End of chapter summary."

