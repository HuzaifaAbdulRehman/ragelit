# Benchmark metric definitions

Status: calculation helpers are implemented and unit-tested. The dataset
runner, real-model measurements and release benchmark are not complete.

[Implementation](../../backend/app/evaluation/metrics.py) and
[tests](../../backend/tests/unit/evaluation/test_metrics.py) use the standard
library. No scientific package or model call is needed to test the calculations.

## Retrieval utility

Each permitted query needs a nonempty, independently assigned set of relevant
UUIDs and an ordered list of unique retrieved UUIDs. Rankings and labels must
use the same unit. Document-level evaluation must collapse repeated document
IDs from retrieved chunks in first-occurrence order before scoring. Labels
must be frozen before running the experiment; the helper does not discover
relevance or decide document permissions.

Recall@10 is the number of relevant IDs in the first ten results divided by
the total number of relevant labels, including labels outside those results.
Reciprocal rank@10 is one divided by the first relevant result's one-based
rank, or zero if none appears in the first ten. MRR@10 is the macro average of
those per-query reciprocal ranks. Average Recall@10 also weights each query
equally. An empty result list scores zero; missing labels and duplicate ranked
IDs are errors, not queries silently excluded from the denominator.

## Latency and paired comparisons

p50 and p95 use linear interpolation at positions `(n - 1) * 0.50` and
`(n - 1) * 0.95` in sorted millisecond samples. Empty, negative and nonfinite
samples are rejected. This is the linear quantile definition described in
the [NumPy documentation](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html).

`paired_mean_interval` compares candidate minus reference for exactly matching
query IDs. At least two pairs are required; missing or nonfinite measurements
abort the comparison rather than reducing it to the successful intersection.
Query IDs are sorted before sampling. A process-local `random.Random` uses
seed `20261005` and 10,000 resamples by default without changing global RNG state.
Each resample draws query differences with replacement and computes their mean.
The 95% percentile interval uses the bootstrap distribution's 2.5th and 97.5th
linear percentiles. Pair-preserving resampling and percentile intervals are
described in the [SciPy documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html).
No SciPy implementation is copied or required.

The result records the mean difference, interval, pair count, resample count
and seed. Positive differences mean higher candidate values, not necessarily
better performance: higher latency is worse. The interval describes variation
under query resampling, not uncertainty in p50/p95 or a guarantee about unseen
organizations. Correlated queries and repeated trials need a declared sampling
unit before applying it. Small synthetic datasets can give misleadingly narrow
intervals. Record the Python version with experiment provenance.

## Remaining measurements

These helpers do not establish dataset coverage, label correctness or genuine
model quality. The release still requires pinned data and models, per-query
raw artifacts, unauthorized retrieval/context/output rates, injection success,
citation correctness, index time, storage, collection count and revocation
delay. Deterministic audit fixtures remain security regressions, not a substitute
for the required benchmark.

Run the calculation tests from `backend`:

```console
uv run --frozen pytest tests/unit/evaluation/test_metrics.py -q
```
