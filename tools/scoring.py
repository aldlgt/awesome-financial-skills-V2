#!/usr/bin/env python3
"""Shared scoring / text matching utilities for candidate discovery."""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

# Short / ambiguous tokens that must not match longer unrelated words.
# e.g. "quant" must not hit "quantum" / "quantization"
_BOUNDARY_KEYWORDS = {
    "quant",
    "fund",
    "bond",
    "etf",
    "nav",
    "rag",
    "mcp",
    "api",
    "pe",
    "pb",
    "roe",
    "dcf",
    "cro",
    "tam",
    "sam",
    "som",
    "mod",
}

# If these appear, treat as non-finance even if "quant" matched earlier.
_HARD_NEGATIVES = [
    "quantum",
    "quantization",
    "quantized",
    "qubit",
    "qecc",
    "munich-quantum",
    "量子计算",
    "量子通信",
    "quanten",
]


def normalize(text: str | None) -> str:
    return (text or "").lower()


def _keyword_pattern(kw: str) -> re.Pattern:
    k = kw.lower().strip()
    escaped = re.escape(k)
    if k in _BOUNDARY_KEYWORDS or len(k) <= 3:
        # word-ish boundary: avoid matching inside longer alpha tokens
        return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.I)
    if k == "quant":  # pragma: no cover - kept in set above
        return re.compile(rf"(?<![a-z0-9])quant(?!um|ization|ized|en)", re.I)
    return re.compile(escaped, re.I)


def contains_any(text: str, keywords: Iterable[str]) -> List[str]:
    """Return keywords that match in text (case-insensitive, boundary-aware for short tokens)."""
    t = normalize(text)
    hits = []
    for kw in keywords:
        if not kw:
            continue
        if _keyword_pattern(kw).search(t):
            hits.append(kw)
    return hits


def score_text(
    text: str,
    *,
    finance_signals: Sequence[str],
    topic_signals: Sequence[str],
    skill_signals: Sequence[str],
    blacklist: Sequence[str],
) -> dict:
    """
    Score a repo name+description for financial relevance and topic fit.

    Returns dict with score, hit lists, and rejected flag.
    """
    t = normalize(text)

    for neg in _HARD_NEGATIVES:
        if neg in t:
            return {
                "score": 0.0,
                "rejected": True,
                "reject_reason": f"hard_negative:{neg}",
                "finance_hits": [],
                "topic_hits": [],
                "skill_hits": [],
            }

    bl_hits = contains_any(t, blacklist)
    if bl_hits:
        return {
            "score": 0.0,
            "rejected": True,
            "reject_reason": f"blacklist:{bl_hits[0]}",
            "finance_hits": [],
            "topic_hits": [],
            "skill_hits": [],
        }

    finance_hits = contains_any(t, finance_signals)
    topic_hits = contains_any(t, topic_signals)
    skill_hits = contains_any(t, skill_signals)

    # Weighted scoring: topic fit matters most for thematic batches.
    score = (
        2.5 * len(set(h.lower() for h in finance_hits))
        + 3.0 * len(set(h.lower() for h in topic_hits))
        + 1.0 * len(set(h.lower() for h in skill_hits))
    )

    if finance_hits and topic_hits:
        score += 2.0
    if len(topic_hits) >= 2:
        score += 1.5
    if len(finance_hits) >= 2:
        score += 1.0

    return {
        "score": round(score, 2),
        "rejected": False,
        "reject_reason": None,
        "finance_hits": finance_hits[:8],
        "topic_hits": topic_hits[:8],
        "skill_hits": skill_hits[:8],
    }
