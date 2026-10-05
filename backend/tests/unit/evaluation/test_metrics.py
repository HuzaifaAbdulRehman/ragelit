import math
import random
from uuid import UUID

import pytest

from app.evaluation.metrics import (
    latency_percentiles,
    paired_mean_interval,
    retrieval_scores,
)

A, B, C = UUID(int=1), UUID(int=2), UUID(int=3)


@pytest.mark.parametrize(
    ("ranked", "relevant", "recall", "reciprocal_rank"),
    [
        ((C, B, A), frozenset({A, B}), 1.0, 0.5),
        ((C, B), frozenset({A, B}), 0.5, 0.5),
        ((C,), frozenset({A, B}), 0.0, 0.0),
        ((), frozenset({A}), 0.0, 0.0),
        ((A, C), frozenset({A}), 1.0, 1.0),
    ],
)
def test_scores_use_all_labels_and_the_first_relevant_rank(
    ranked: tuple[UUID, ...],
    relevant: frozenset[UUID],
    recall: float,
    reciprocal_rank: float,
) -> None:
    result = retrieval_scores(ranked, relevant)
    assert result.recall_at_10 == recall
    assert result.reciprocal_rank_at_10 == reciprocal_rank


def test_rank_ten_counts_but_rank_eleven_does_not() -> None:
    ranked = tuple(UUID(int=index) for index in range(1, 12))
    result = retrieval_scores(ranked, frozenset({ranked[9], ranked[10]}))
    assert result.recall_at_10 == 0.5
    assert result.reciprocal_rank_at_10 == 0.1


def test_relevant_result_only_beyond_the_cutoff_has_zero_scores() -> None:
    ranked = tuple(UUID(int=index) for index in range(1, 12))
    result = retrieval_scores(ranked, frozenset({ranked[10]}))
    assert result.recall_at_10 == result.reciprocal_rank_at_10 == 0.0


def test_more_than_ten_labels_are_not_removed_from_recall_denominator() -> None:
    ranked = tuple(UUID(int=index) for index in range(1, 12))
    result = retrieval_scores(ranked, frozenset(ranked))
    assert result.recall_at_10 == pytest.approx(10.0 / 11.0)
    assert result.reciprocal_rank_at_10 == 1.0


def test_unlabeled_query_cannot_be_scored_as_perfect_or_zero() -> None:
    with pytest.raises(ValueError):
        retrieval_scores((A,), frozenset())


def test_duplicate_results_cannot_inflate_recall() -> None:
    with pytest.raises(ValueError):
        retrieval_scores((A, A, B), frozenset({A, B}))


def test_latency_uses_linear_interpolation_without_mutating_samples() -> None:
    samples = [40.0, 10.0, 30.0, 20.0]
    result = latency_percentiles(samples)
    assert result.p50_ms == 25.0
    assert result.p95_ms == pytest.approx(38.5)
    assert samples == [40.0, 10.0, 30.0, 20.0]


def test_single_latency_sample_has_identical_percentiles() -> None:
    result = latency_percentiles([12.0])
    assert result.p50_ms == result.p95_ms == 12.0


@pytest.mark.parametrize("samples", [[], [-1.0], [math.nan], [math.inf], [-math.inf]])
def test_missing_or_invalid_latency_is_not_silently_dropped(
    samples: list[float],
) -> None:
    with pytest.raises(ValueError):
        latency_percentiles(samples)


def test_paired_interval_resamples_query_differences_not_independent_runs() -> None:
    result = paired_mean_interval(
        {"q-a": 0.0, "q-b": 0.5, "q-c": 0.75},
        {"q-a": 0.25, "q-b": 0.75, "q-c": 1.0},
        resamples=256,
        seed=17,
    )
    assert result.mean_difference == result.low == result.high == 0.25
    assert result.paired_queries == 3
    assert result.resamples == 256
    assert result.seed == 17


def test_paired_interval_retains_negative_results() -> None:
    result = paired_mean_interval(
        {"q-a": 1.0, "q-b": 0.0},
        {"q-a": 0.0, "q-b": 1.0},
        resamples=2048,
        seed=17,
    )
    assert result.mean_difference == 0.0
    assert result.low == -1.0
    assert result.high == 1.0


def test_seeded_two_resample_fixture_uses_the_95_percent_interval() -> None:
    # Seed 0 draws two ones, then two zeroes: bootstrap means are 1 and 0.
    result = paired_mean_interval(
        {"q-a": 0.0, "q-b": 0.0},
        {"q-a": 0.0, "q-b": 1.0},
        resamples=2,
        seed=0,
    )
    assert result.mean_difference == 0.5
    assert result.low == pytest.approx(0.025)
    assert result.high == pytest.approx(0.975)


def test_paired_interval_is_order_independent_and_leaves_global_rng_alone() -> None:
    before = random.getstate()
    reference = {"q-a": 0.0, "q-b": 0.5, "q-c": 1.0}
    candidate = {"q-c": 0.75, "q-a": 0.25, "q-b": 0.0}
    first = paired_mean_interval(reference, candidate, resamples=128, seed=29)
    second = paired_mean_interval(
        dict(reversed(tuple(reference.items()))),
        dict(reversed(tuple(candidate.items()))),
        resamples=128,
        seed=29,
    )
    assert first == second
    assert first.mean_difference == pytest.approx(-1.0 / 6.0)
    assert random.getstate() == before


@pytest.mark.parametrize(
    ("reference", "candidate"),
    [
        ({}, {}),
        ({"q-a": 1.0}, {"q-a": 0.0}),
        ({"q-a": 1.0, "q-b": 0.0}, {"q-a": 0.0, "q-c": 1.0}),
        ({"q-a": math.nan, "q-b": 0.0}, {"q-a": 0.0, "q-b": 1.0}),
        ({"q-a": 1.0, "q-b": 0.0}, {"q-a": math.inf, "q-b": 1.0}),
        ({"q-a": -1e308, "q-b": 0.0}, {"q-a": 1e308, "q-b": 1.0}),
    ],
)
def test_incomplete_or_nonfinite_pairs_cannot_produce_an_interval(
    reference: dict[str, float], candidate: dict[str, float]
) -> None:
    with pytest.raises(ValueError):
        paired_mean_interval(reference, candidate, resamples=8, seed=17)


@pytest.mark.parametrize("resamples", [0, -1, True])
def test_invalid_resample_count_is_rejected(resamples: int) -> None:
    with pytest.raises(ValueError):
        paired_mean_interval(
            {"q-a": 0.0, "q-b": 1.0},
            {"q-a": 0.5, "q-b": 1.0},
            resamples=resamples,
            seed=17,
        )
