from __future__ import annotations

import hashlib
import re

# Order matters: timestamps and UUIDs must be masked before the generic number
# rule gets to them, or they decompose into a soup of <n> tokens.
_MASKS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"), "<uuid>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"), "<timestamp>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b[0-9a-fA-F]{12,}\b"), "<hash>"),
    (re.compile(r"\b[a-z0-9-]+-[0-9a-f]{8,10}-[a-z0-9]{5}\b"), "<pod>"),
    (re.compile(r"\b\d+(?:\.\d+)?(?:ms|s|m|h|ns|us|KB|MB|GB|kB|B)\b"), "<duration>"),
    (re.compile(r"\b\d+(?:\.\d+)?\b"), "<n>"),
    (re.compile(r"'[^']{0,200}'"), "'<str>'"),
    (re.compile(r'"[^"]{0,200}"'), '"<str>"'),
)

_WHITESPACE = re.compile(r"\s+")


def fingerprint(message: str) -> str:
    """Collapses a log message to its template.

    'Payment failed after 1203ms for trace tr-9f2a' and the same line with
    different numbers are one pattern, not two. Without this, a terms aggregation
    on raw messages returns thousands of near-identical buckets and the top-N cut
    keeps whichever happened to be most frequent rather than what matters.
    """
    text = (message or "").strip()
    if not text:
        return "<empty>"
    for pattern, replacement in _MASKS:
        text = pattern.sub(replacement, text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text[:300]


def pattern_id(service: str | None, template: str) -> str:
    digest = hashlib.sha1(f"{service or '-'}::{template}".encode()).hexdigest()[:8]
    slug = (service or "system").replace(".", "-")
    return f"pat:{slug}:{digest}"
