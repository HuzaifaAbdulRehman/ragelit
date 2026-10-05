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

## Fixed utility corpus

`natural-utility-v1` uses seed `20261005`, three invented organizations, four
groups per organization and 21 actors. Its 87 short policy documents have 87
authored questions and document-level relevance labels: 60 organization-wide,
24 group-restricted and three direct-user records. Depending on the actor,
20 to 23 documents are eligible, so returning ten results cannot retrieve the
entire permitted collection.

The generator rejects incomplete inventories, foreign grants, forbidden labels
and unsupported answer facts. Owner/admin/auditor roles do not supply document
grants. This is declared dataset eligibility, not a replacement for production
authorization. Tests check corpus integrity; no model quality is established.
The short templated records are not a blind holdout or representative company
dataset. Existing access/injection fixtures remain separate security regressions.

The default corpus manifest has 55,094 bytes and SHA-256
`f3f2b17b7d40ec7f6ce26e1779941241bc1ce5711f5152d6b461d26bb7fe329a`.
It replaces text, questions and expected answers with hashes while retaining
effective identities, grants and labels. Hashes provide provenance, not
anonymization or proof that an operator executed a benchmark.

Reproduce that checksum from `backend` without downloading a model:

```console
uv run --frozen python -c "from app.evaluation.dataset import generate_utility_corpus; print(generate_utility_corpus().checksum)"
```

## Utility report replay

The [report schema and offline validator](../../backend/app/evaluation/reports.py)
are implemented. The production runner and measured cohort are still pending.
Tests use constructed observations; their scores are not model-quality results.

Primary Recall@10 and MRR@10 require all 87 retrieval observations. A measured
miss scores zero. Missing, truncated or observer-failed retrieval stays unknown.
Observed-only averages retain their sample count, and incomplete runs keep
exit code 2. Latencies use retrieval_accepted, including authorization refresh,
SQL eligibility, embeddings, vector search and projection validation.

For this corpus, citation correctness is recorded as citation_relevance: the
fraction of delivered citations pointing to a relevant, permitted version in
the actual context. It is source selection, not semantic entailment. Answer-label
matches are counted separately; abstention is a miss, not a dropped query.

Paired comparisons require complete logical query cohorts and identical source,
locks, models, generation settings, machine and service provenance. Reports
retain bounded IDs and observations, not document bodies, prompts or answers.
The validator checks original-byte receipts and replays derived fields without
model calls. Partial inventory is valid evidence but never a completed cohort.
Security, index/storage costs and revocation timing remain separate required
measurements; this utility report alone is not the complete release benchmark.

## Remaining measurements

These helpers and corpus checks do not establish genuine model quality.
[Local embedding pins](model-pins.md) now define verified offline embedding
assets. The release still requires effective generation pins and configuration, per-query
raw artifacts, unauthorized retrieval/context/output rates, injection success,
citation correctness, index time, storage, collection count and revocation
delay. Deterministic audit fixtures remain security regressions, not a substitute
for the required benchmark.

Run the calculation tests from `backend`:

```console
uv run --frozen pytest tests/unit/evaluation/test_metrics.py -q
```
