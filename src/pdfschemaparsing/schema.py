"""Schema models and validation for resilient PDF and Markdown parsing."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

# Detects placeholder tokens in section titles such as "{n}", "{num}", "{id}", "{roman}", "{alpha}", or "*"
_PLACEHOLDER_TOKEN_RE = re.compile(
    r"(\{\s*(?:n|num|number|id|index|i|roman|alpha|letter|title|name)?\s*\}|\*)",
    re.IGNORECASE,
)


class SchemaValidationError(ValueError):
    """Raised when a user-provided schema is invalid."""


class MissingRequiredSectionError(ValueError):
    """Raised when a schema section marked `required=True` is missing in the document."""


@dataclass
class SectionSpec:
    """Specification for a single section or a repeating consecutive section series (e.g., 'Chapter 1', 'Chapter 2').

    To define a consecutive repeating section series that shares the same subsections:
    - Set `repeatable=True` (e.g., `SectionSpec(title="Chapter", repeatable=True, subsections=[...])`), OR
    - Use a placeholder in `title` such as `"Chapter {n}"`, `"Chapter {roman}"`, `"Part {alpha}"`, or `"Chapter *"`, OR
    - Provide a custom `pattern` with `repeatable=True`.
    """

    title: str
    aliases: list[str] = field(default_factory=list)
    pattern: str | None = None
    required: bool = False
    repeatable: bool = False
    subsections: list[SectionSpec] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise SchemaValidationError("Each section must have a non-empty string 'title'.")
        self.title = self.title.strip()
        self.aliases = [str(a).strip() for a in (self.aliases or []) if str(a).strip()]
        if self.pattern is not None:
            self.pattern = str(self.pattern).strip() or None

        # Auto-enable `repeatable=True` if the title or any alias contains a placeholder like "{n}" or "*"
        if not self.repeatable:
            for candidate in [self.title, *self.aliases]:
                if _PLACEHOLDER_TOKEN_RE.search(candidate):
                    self.repeatable = True
                    break

        parsed_subs: list[SectionSpec] = []
        for sub in self.subsections or []:
            if isinstance(sub, SectionSpec):
                parsed_subs.append(sub)
            elif isinstance(sub, str):
                parsed_subs.append(SectionSpec(title=sub))
            elif isinstance(sub, Mapping):
                parsed_subs.append(SectionSpec.from_dict(sub))
            else:
                raise SchemaValidationError(
                    f"Invalid subsection specification under '{self.title}': {sub!r}"
                )
        self.subsections = parsed_subs

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | str) -> SectionSpec:
        """Create a SectionSpec from a dictionary or plain title string."""
        if isinstance(data, str):
            return cls(title=data)
        if not isinstance(data, Mapping):
            raise SchemaValidationError(f"Section specification must be a dict or str, got {type(data).__name__}")
        title = data.get("title") or data.get("name") or data.get("header")
        if not title:
            raise SchemaValidationError(f"Section dictionary is missing 'title': {data!r}")
        aliases = data.get("aliases") or data.get("alt_titles") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        repeatable_flag = bool(
            data.get("repeatable", False)
            or data.get("consecutive", False)
            or data.get("multiple", False)
        )
        return cls(
            title=str(title),
            aliases=list(aliases),
            pattern=data.get("pattern"),
            required=bool(data.get("required", False)),
            repeatable=repeatable_flag,
            subsections=list(data.get("subsections") or data.get("children") or []),
        )

    def all_candidate_titles(self) -> list[str]:
        """Return canonical title followed by all aliases."""
        seen: set[str] = set()
        result: list[str] = []
        for candidate in [self.title, *self.aliases]:
            if candidate not in seen:
                seen.add(candidate)
                result.append(candidate)
        return result

    def base_prefix_titles(self) -> list[str]:
        """Return candidate title prefixes with placeholders (`{n}`, `*`, etc.) stripped."""
        prefixes: list[str] = []
        for candidate in self.all_candidate_titles():
            cleaned = _PLACEHOLDER_TOKEN_RE.sub("", candidate).strip(" :.-—–")
            if cleaned and cleaned not in prefixes:
                prefixes.append(cleaned)
        return prefixes

    @property
    def has_placeholder(self) -> bool:
        """Return True if the title or any alias explicitly contains a placeholder token like `{n}` or `*`."""
        return any(bool(_PLACEHOLDER_TOKEN_RE.search(c)) for c in self.all_candidate_titles())


@dataclass
class DocumentSchema:
    """Top-level schema describing how a document should be segmented into sections."""

    sections: list[SectionSpec]
    ordered: bool = False
    fuzzy_threshold: float = 0.85
    strip_numbering: bool = True
    max_header_words: int = 15
    max_header_chars: int = 120
    capture_preamble: bool = False
    include_missing: bool = False
    y_tolerance: float = 3.0
    strip_running_headers: bool = True

    def __post_init__(self) -> None:
        if not self.sections:
            raise SchemaValidationError("DocumentSchema requires at least one section.")
        normalized_sections: list[SectionSpec] = []
        for item in self.sections:
            if isinstance(item, SectionSpec):
                normalized_sections.append(item)
            elif isinstance(item, str):
                normalized_sections.append(SectionSpec(title=item))
            elif isinstance(item, Mapping):
                normalized_sections.append(SectionSpec.from_dict(item))
            else:
                raise SchemaValidationError(f"Unsupported section item: {item!r}")
        self.sections = normalized_sections

        if not (0.0 <= self.fuzzy_threshold <= 1.0):
            raise SchemaValidationError("fuzzy_threshold must be between 0.0 and 1.0.")
        if self.max_header_words < 1 or self.max_header_chars < 1:
            raise SchemaValidationError("max_header_words and max_header_chars must be positive integers.")

    @classmethod
    def from_any(
        cls,
        source: DocumentSchema | Mapping[str, Any] | Sequence[Any] | str | Path,
    ) -> DocumentSchema:
        """Load a DocumentSchema from an instance, dict, list of sections, JSON string, or file path."""
        if isinstance(source, cls):
            return source

        if isinstance(source, Path):
            raw_text = source.read_text(encoding="utf-8")
            return cls.from_any(json.loads(raw_text))

        if isinstance(source, str):
            stripped = source.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                return cls.from_any(json.loads(stripped))
            path_candidate = Path(stripped)
            if path_candidate.exists() and path_candidate.is_file():
                return cls.from_any(json.loads(path_candidate.read_text(encoding="utf-8")))
            raise SchemaValidationError(
                "String schema input must be a valid JSON string or path to an existing JSON file."
            )

        if isinstance(source, Mapping):
            if "sections" in source:
                sections_raw = source["sections"]
                if not isinstance(sections_raw, Sequence) or isinstance(sections_raw, (str, bytes)):
                    raise SchemaValidationError("'sections' in schema must be a list.")
                return cls(
                    sections=list(sections_raw),
                    ordered=bool(source.get("ordered", False)),
                    fuzzy_threshold=float(source.get("fuzzy_threshold", 0.85)),
                    strip_numbering=bool(source.get("strip_numbering", True)),
                    max_header_words=int(source.get("max_header_words", 15)),
                    max_header_chars=int(source.get("max_header_chars", 120)),
                    capture_preamble=bool(source.get("capture_preamble", False)),
                    include_missing=bool(source.get("include_missing", False)),
                    y_tolerance=float(source.get("y_tolerance", 3.0)),
                    strip_running_headers=bool(source.get("strip_running_headers", True)),
                )
            # Support shorthand dict mapping: {"Section Title": ["Alias 1", "Alias 2"]}
            inferred_sections: list[SectionSpec] = []
            for key, val in source.items():
                if isinstance(val, Sequence) and not isinstance(val, (str, bytes)):
                    inferred_sections.append(SectionSpec(title=str(key), aliases=[str(v) for v in val]))
                elif isinstance(val, Mapping):
                    spec_dict = dict(val)
                    spec_dict.setdefault("title", str(key))
                    inferred_sections.append(SectionSpec.from_dict(spec_dict))
                else:
                    inferred_sections.append(SectionSpec(title=str(key)))
            return cls(sections=inferred_sections)

        if isinstance(source, Sequence) and not isinstance(source, (str, bytes)):
            return cls(sections=list(source))

        raise SchemaValidationError(f"Cannot build DocumentSchema from {type(source).__name__}")
