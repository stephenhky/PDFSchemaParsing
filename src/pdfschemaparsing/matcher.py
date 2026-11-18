"""Multi-stage resilient section header matching (exact canonical, regex, fuzzy, and inline)."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from pdfschemaparsing.ingestors.base import TextLine
from pdfschemaparsing.normalizer import canonicalize_header, strip_markdown_header_syntax
from pdfschemaparsing.schema import SectionSpec

# Inline header delimiter pattern, e.g. "**Abstract:** This paper..." or "1. Introduction — In recent years..."
_INLINE_HEADER_SPLIT_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*{1,3}|_{1,3})?([^:\u2013\u2014\n]{2,60}?)\s*(?::|\u2014|\u2013|--)(?:\*{1,3}|_{1,3})?\s+(.+)$"
)


@dataclass
class HeaderMatch:
    """Represents a matched section header in the document."""

    spec: SectionSpec
    start_line_idx: int
    end_line_idx: int  # Inclusive line index consumed by the header (can be start_line_idx + 1 for wrapped headers)
    matched_header: str
    score: float
    match_type: str  # "exact", "regex", "fuzzy", "inline"
    inline_remainder: str | None = None


@dataclass
class _CompiledSectionTarget:
    spec: SectionSpec
    canonical_candidates: list[str]
    compiled_regex: re.Pattern[str] | None


def _compile_targets(
    specs: list[SectionSpec],
    *,
    strip_numbering: bool = True,
) -> list[_CompiledSectionTarget]:
    targets: list[_CompiledSectionTarget] = []
    for spec in specs:
        canon_list = [
            canonicalize_header(title, strip_numbering=strip_numbering)
            for title in spec.all_candidate_titles()
        ]
        canon_list = [c for c in canon_list if c]
        regex_obj = re.compile(spec.pattern, re.IGNORECASE) if spec.pattern else None
        targets.append(
            _CompiledSectionTarget(
                spec=spec,
                canonical_candidates=canon_list,
                compiled_regex=regex_obj,
            )
        )
    return targets


def compute_header_similarity(candidate_canon: str, target_canon: str) -> float:
    """Compute similarity score in [0.0, 1.0] between two canonicalized header strings."""
    if not candidate_canon or not target_canon:
        return 0.0
    if candidate_canon == target_canon:
        return 1.0

    # Guard against huge length mismatches (e.g., matching a 1-word header against a 6-word sentence)
    len_ratio = min(len(candidate_canon), len(target_canon)) / max(len(candidate_canon), len(target_canon))
    if len_ratio < 0.45:
        return 0.0

    char_ratio = SequenceMatcher(None, candidate_canon, target_canon).ratio()

    cand_tokens = candidate_canon.split()
    targ_tokens = target_canon.split()
    if len(cand_tokens) > 1 or len(targ_tokens) > 1:
        sorted_cand = " ".join(sorted(cand_tokens))
        sorted_targ = " ".join(sorted(targ_tokens))
        token_sort_ratio = SequenceMatcher(None, sorted_cand, sorted_targ).ratio()
        # Blend direct sequence ratio with token-order-insensitive ratio (weighted toward direct order)
        return max(char_ratio, 0.75 * char_ratio + 0.25 * token_sort_ratio)

    return char_ratio


def _looks_like_standalone_header(
    lines: list[TextLine],
    start_idx: int,
    end_idx: int,
    *,
    max_words: int,
    max_chars: int,
) -> bool:
    """Heuristic check that a 1- or 2-line candidate looks like a heading rather than mid-paragraph prose."""
    combined_raw = " ".join(lines[i].text.strip() for i in range(start_idx, end_idx + 1)).strip()
    if not combined_raw:
        return False

    stripped_md = strip_markdown_header_syntax(combined_raw)
    if len(stripped_md) > max_chars:
        return False

    words = stripped_md.split()
    if not words or len(words) > max_words:
        return False

    # If explicitly marked as heading by ingestor (Markdown #, Setext, Bold, or larger PDF font), accept immediately
    if any(lines[i].is_heading_hint for i in range(start_idx, end_idx + 1)):
        return True

    # Reject lines that end like normal prose sentences (period followed by multiple words, unless abbreviation)
    if stripped_md.endswith(".") and len(words) > 5:
        return False

    return True


def _match_text_against_targets(
    raw_text: str,
    targets: list[_CompiledSectionTarget],
    *,
    strip_numbering: bool,
    fuzzy_threshold: float,
    allow_fuzzy: bool = True,
) -> tuple[SectionSpec, float, str] | None:
    """Match a candidate header string against compiled section targets."""
    canon = canonicalize_header(raw_text, strip_numbering=strip_numbering)
    stripped_raw = strip_markdown_header_syntax(raw_text).strip()

    # Stage 1: Exact canonical match
    if canon:
        for target in targets:
            if canon in target.canonical_candidates:
                return (target.spec, 1.0, "exact")

    # Stage 2: Custom regex pattern match
    for target in targets:
        if target.compiled_regex is not None:
            if target.compiled_regex.search(stripped_raw) or (canon and target.compiled_regex.search(canon)):
                return (target.spec, 0.99, "regex")

    # Stage 3: Fuzzy match
    if allow_fuzzy and canon and len(canon) >= 3:
        best_spec: SectionSpec | None = None
        best_score = 0.0
        for target in targets:
            for cand_target in target.canonical_candidates:
                score = compute_header_similarity(canon, cand_target)
                if score > best_score:
                    best_score = score
                    best_spec = target.spec
        if best_spec is not None and best_score >= fuzzy_threshold:
            return (best_spec, best_score, "fuzzy")

    return None


def find_section_matches(
    lines: list[TextLine],
    specs: list[SectionSpec],
    *,
    ordered: bool = False,
    fuzzy_threshold: float = 0.85,
    strip_numbering: bool = True,
    max_header_words: int = 15,
    max_header_chars: int = 120,
) -> list[HeaderMatch]:
    """Scan a sequence of TextLines and identify section boundaries matching `specs`.

    Supports:
    - Single-line headings
    - 2-line wrapped headings (layout shift resilience when a long header wraps across two lines)
    - Inline headings (`**Abstract:** Text starts on the same line...`)
    - Ordered or unordered section discovery
    """
    if not lines or not specs:
        return []

    all_targets = _compile_targets(specs, strip_numbering=strip_numbering)
    matches: list[HeaderMatch] = []
    n = len(lines)
    i = 0
    next_ordered_idx = 0

    while i < n:
        line = lines[i]
        if line.is_blank or line.in_code_block or line.is_decorative_rule or line.is_footnote:
            i += 1
            continue

        active_targets = all_targets[next_ordered_idx:] if ordered else all_targets
        if not active_targets:
            break

        # 1. Check 2-line wrapped header lookahead FIRST only if it forms an exact match
        #    (prevents matching only the first half of a wrapped heading when the 2-line join is exact)
        two_line_match: HeaderMatch | None = None
        if (
            i + 1 < n
            and not lines[i + 1].is_blank
            and not lines[i + 1].in_code_block
            and not lines[i + 1].is_decorative_rule
            and not lines[i + 1].is_footnote
            and lines[i].page == lines[i + 1].page
            and _looks_like_standalone_header(
                lines, i, i + 1, max_words=max_header_words, max_chars=max_header_chars
            )
        ):
            combined_text = f"{lines[i].text.strip()} {lines[i + 1].text.strip()}"
            res_2 = _match_text_against_targets(
                combined_text,
                active_targets,
                strip_numbering=strip_numbering,
                fuzzy_threshold=max(fuzzy_threshold, 0.90),
                allow_fuzzy=True,
            )
            if res_2 is not None:
                spec_2, score_2, mtype_2 = res_2
                two_line_match = HeaderMatch(
                    spec=spec_2,
                    start_line_idx=i,
                    end_line_idx=i + 1,
                    matched_header=combined_text,
                    score=score_2,
                    match_type=mtype_2,
                )

        # 2. Check single-line standalone header
        single_line_match: HeaderMatch | None = None
        if _looks_like_standalone_header(
            lines, i, i, max_words=max_header_words, max_chars=max_header_chars
        ):
            res_1 = _match_text_against_targets(
                line.text,
                active_targets,
                strip_numbering=strip_numbering,
                fuzzy_threshold=fuzzy_threshold,
                allow_fuzzy=True,
            )
            if res_1 is not None:
                spec_1, score_1, mtype_1 = res_1
                single_line_match = HeaderMatch(
                    spec=spec_1,
                    start_line_idx=i,
                    end_line_idx=i,
                    matched_header=line.text.strip(),
                    score=score_1,
                    match_type=mtype_1,
                )

        # Decide between 1-line and 2-line match if either succeeded
        chosen: HeaderMatch | None = None
        if two_line_match and single_line_match:
            # Prefer 2-line match if it has a strictly higher score (e.g. exact vs fuzzy)
            chosen = two_line_match if two_line_match.score > single_line_match.score else single_line_match
        elif single_line_match:
            chosen = single_line_match
        elif two_line_match:
            chosen = two_line_match

        # 3. Check inline header (e.g., "**Methods:** We used...") if no standalone match
        if chosen is None:
            inline_m = _INLINE_HEADER_SPLIT_RE.match(line.text)
            if inline_m:
                prefix_candidate = inline_m.group(1).strip()
                remainder_text = inline_m.group(2).strip()
                if len(prefix_candidate.split()) <= max_header_words:
                    res_inline = _match_text_against_targets(
                        prefix_candidate,
                        active_targets,
                        strip_numbering=strip_numbering,
                        fuzzy_threshold=max(fuzzy_threshold, 0.90),
                        allow_fuzzy=True,
                    )
                    if res_inline is not None:
                        spec_in, score_in, _ = res_inline
                        chosen = HeaderMatch(
                            spec=spec_in,
                            start_line_idx=i,
                            end_line_idx=i,
                            matched_header=prefix_candidate,
                            score=score_in,
                            match_type="inline",
                            inline_remainder=remainder_text,
                        )

        if chosen is not None:
            matches.append(chosen)
            if ordered:
                for idx_t, target in enumerate(all_targets[next_ordered_idx:], start=next_ordered_idx):
                    if target.spec is chosen.spec:
                        next_ordered_idx = idx_t + 1
                        break
            i = chosen.end_line_idx + 1
        else:
            i += 1

    return matches
