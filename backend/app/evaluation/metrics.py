from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import floor, isfinite
from random import Random
from statistics import fmean
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RetrievalScores:
    recall_at_10: float
    reciprocal_rank_at_10: float


@dataclass(frozen=True, slots=True)
class LatencyPercentiles:
    p50_ms: float
    p95_ms: float


@dataclass(frozen=True, slots=True)
class PairedInterval:
    mean_difference: float
    low: float
    high: float
    paired_queries: int
    resamples: int
    seed: int


def retrieval_scores(
    ranked_ids: Sequence[UUID], relevant_ids: frozenset[UUID]
) -> RetrievalScores:
    if not relevant_ids:
        raise ValueError("relevance labels are required")
    if len(set(ranked_ids)) != len(ranked_ids):
        raise ValueError("ranked identifiers must be unique")
    top_ten = ranked_ids[:10]
    recall = len(set(top_ten) & relevant_ids) / len(relevant_ids)
    reciprocal_rank = next(
        (
            1.0 / rank
            for rank, identifier in enumerate(top_ten, 1)
            if identifier in relevant_ids
        ),
        0.0,
    )
    return RetrievalScores(recall, reciprocal_rank)


def _quantile(ordered: Sequence[float], probability: float) -> float:
    position = (len(ordered) - 1) * probability
    lower = floor(position)
    weight = position - lower
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def latency_percentiles(durations_ms: Sequence[float]) -> LatencyPercentiles:
    if not durations_ms or any(
        not isfinite(value) or value < 0 for value in durations_ms
    ):
        raise ValueError("latency needs nonempty finite nonnegative samples")
    ordered = sorted(durations_ms)
    return LatencyPercentiles(_quantile(ordered, 0.5), _quantile(ordered, 0.95))


def paired_mean_interval(
    reference: Mapping[str, float],
    candidate: Mapping[str, float],
    *,
    resamples: int = 10_000,
    seed: int = 20261005,
) -> PairedInterval:
    if len(reference) < 2 or reference.keys() != candidate.keys():
        raise ValueError("comparison needs at least two exactly matched queries")
    if isinstance(resamples, bool) or resamples < 1:
        raise ValueError("resample count must be a positive integer")
    if any(not isfinite(value) for value in (*reference.values(), *candidate.values())):
        raise ValueError("paired measurements must be finite")
    differences = [
        candidate[identifier] - reference[identifier]
        for identifier in sorted(reference)
    ]
    if any(not isfinite(value) for value in differences):
        raise ValueError("paired differences must be finite")
    generator = Random(seed)
    distribution = sorted(
        fmean(generator.choices(differences, k=len(differences)))
        for _ in range(resamples)
    )
    return PairedInterval(
        fmean(differences),
        _quantile(distribution, 0.025),
        _quantile(distribution, 0.975),
        len(differences),
        resamples,
        seed,
    )
