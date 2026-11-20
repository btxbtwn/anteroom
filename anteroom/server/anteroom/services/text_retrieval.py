"""BM25 ranking for small in-memory rule and conversation corpora."""
from __future__ import annotations

import math
import re
from collections import Counter


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text).casefold())


def rank_texts(query: str, documents: list[str]) -> list[tuple[int, float]]:
    """Rank by document frequency and saturated term frequency, ignoring query repetition."""
    terms = set(tokens(query))
    counts = [Counter(tokens(document)) for document in documents]
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / max(len(lengths), 1) or 1
    frequency = Counter(term for count in counts for term in count if term in terms)
    results = []
    for index, count in enumerate(counts):
        score = 0.0
        for term in terms & count.keys():
            inverse = math.log(1 + (len(counts) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            normalization = 1.2 * (0.25 + 0.75 * lengths[index] / average)
            score += inverse * count[term] * 2.2 / (count[term] + normalization)
        if score:
            results.append((index, score))
    return sorted(results, key=lambda item: (-item[1], item[0]))
