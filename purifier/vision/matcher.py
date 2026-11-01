"""Normalized cosine matching with a threshold and inter-person margin."""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def normalize(vector: Sequence[float]) -> tuple[float, ...]:
    if not 1 <= len(vector) <= 1024 or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError('invalid embedding')
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError('invalid embedding norm')
    return tuple(v / norm for v in vector)


@dataclass(frozen=True)
class Match:
    user_id: str
    similarity: float | None
    status: str


def match(vector: Sequence[float], enrolled: Mapping[str, Sequence[Sequence[float]]],
          threshold: float, margin: float) -> Match:
    if not -1 <= threshold <= 1 or not 0 < margin <= 2:
        raise ValueError('invalid matching thresholds')
    query = normalize(vector)
    scores: list[tuple[float, str]] = []
    for user, samples in enrolled.items():
        best = -1.0
        if not samples:
            continue
        for sample in samples:
            reference = normalize(sample)
            if len(query) != len(reference):
                raise ValueError('embedding dimension mismatch')
            best = max(best, sum(a * b for a, b in zip(query, reference)))
        scores.append((best, user))
    scores.sort(reverse=True)
    if not scores or scores[0][0] < threshold:
        return Match('guest', None, 'no_match')
    if len(scores) > 1 and scores[0][0] - scores[1][0] < margin:
        return Match('guest', None, 'ambiguous')
    return Match(scores[0][1], scores[0][0], 'matched')
