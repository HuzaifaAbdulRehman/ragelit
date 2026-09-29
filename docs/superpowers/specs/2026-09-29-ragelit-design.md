# RAGelit design

## Purpose

RAGelit is a document question-answering application and retrieval evaluation
system. A user uploads documents, asks a question, and receives a cited answer.
Under that familiar interface, RAGelit selects between BM25, dense, hybrid, and
reranked retrieval and records the quality and latency trade-off.

The project serves two audiences:

- AI engineering reviewers need a working application, a clear API, tested
  failure paths, and measured performance.
- Master's admissions reviewers need a falsifiable question, credible
  baselines, an untouched test set, reproducible experiments, and honest
  limitations.

The research question is:

> Can a cheap, confidence-aware router transfer to an unseen document domain
> and reduce expensive reranking while bounding retrieval-quality loss?

This is an evaluation question, not a novelty claim. Adaptive retrieval and
pipeline routing already have published prior work. RAGelit will cite that work
and state only what its own experiments establish.

The starting references are Adaptive-RAG for complexity-based strategy
selection, RetrievalRouter for learned pipeline selection, BEIR for
cross-domain retrieval evaluation, and Qdrant's hybrid-query documentation for
rank fusion. The implementation report will distinguish reproduced behavior
from findings specific to RAGelit:

- <https://arxiv.org/abs/2403.14403>
- <https://arxiv.org/abs/2608.25625>
- <https://arxiv.org/abs/2104.08663>
- <https://qdrant.tech/documentation/search/hybrid-queries/>

## Product experience

The default interface is intentionally simple:

1. Upload TXT, Markdown, DOCX, or text-based PDF files.
2. Watch each document move through queued, processing, indexed, or failed
   states.
3. Ask a question against the active document collection.
4. Read an answer with passage-level citations.
5. Open the retrieval trace to see the selected strategy, timings, and source
   passages.

An optional comparison view runs the same question through every retrieval
strategy. It shows the result lists and latency side by side. Live use does not
display a made-up quality score because relevance labels do not exist for an
arbitrary uploaded document. Quality metrics appear only for labeled benchmark
queries.

The first release supports documents containing extractable text. It rejects
scanned or image-only PDFs with an actionable error. OCR, handwriting, tables,
images, and multimodal retrieval are outside the first release.

## System boundary

RAGelit is a standalone local-first system. ProposalPilot may call its API after
the standalone product is complete, but no ProposalPilot code or hosted account
is required to run it.

The repository contains:

- A Python 3.12 FastAPI service.
- A small React and TypeScript web interface.
- Qdrant in Docker for dense and sparse indexes.
- Local Hugging Face models for dense embeddings and cross-encoder reranking.
- SQLite for document, version, ingestion-job, and query-run metadata.
- Experiment commands that produce machine-readable results and a concise
  report.

The generator uses an OpenAI-compatible endpoint. ModelGate is a supported
configuration, not a runtime dependency. Retrieval benchmarks never require an
LLM, which keeps their results reproducible and separates retrieval failures
from generation failures.

## Components

### Document ingestion

The API accepts supported files, stores them under a local data directory, and
creates a durable SQLite job. A background worker in the API process resumes
queued or interrupted jobs when the service restarts. The first release runs
one API worker; a SQLite claim record prevents the ingestion loop from starting
the same job twice after recovery.

Each ingestion job:

1. Computes a SHA-256 digest and rejects an unchanged duplicate.
2. Extracts text and source-location metadata.
3. Applies the configured chunker.
4. Creates local dense embeddings in bounded batches.
5. Writes dense vectors, BM25 sparse vectors, text, and metadata to a new
   version in Qdrant.
6. Marks that version active only after every batch succeeds.

A failed version is never made queryable. Its staged Qdrant points are removed
during cleanup or the next startup recovery pass. Re-indexing is therefore safe
to retry without exposing a half-written corpus.

The first release implements fixed-token, sentence-boundary, and
structure-aware chunking. Structure-aware means Markdown headings and extracted
document paragraphs define preferred boundaries; it does not claim to infer
semantic topics.

### Retrieval pipelines

Every pipeline implements the same input and output contract: a query and limit
go in; ranked passages with scores, provenance, and timings come out.

- **BM25** uses Qdrant's sparse text index for exact terms and identifiers.
- **Dense** uses a local Sentence Transformers embedding and cosine similarity.
- **Hybrid** retrieves sparse and dense candidates and fuses ranks with
  Reciprocal Rank Fusion.
- **Reranked** applies a local cross-encoder to the hybrid candidate set and
  returns the highest-scoring passages.

Raw BM25 and cosine scores are never added directly because their scales are not
comparable. Fusion uses ranks. Each stage records wall-clock latency and the
number of candidates it processed.

### Confidence-aware router

The router chooses one of the four pipelines before retrieval. Its initial
feature set is deliberately small and inspectable: query token count, quoted
span count, digit ratio, uppercase ratio, punctuation ratio, and corpus-level
mean and maximum inverse document frequency.

For each labeled training query, all four pipelines run. The label is the least
expensive pipeline whose nDCG@10 is within 0.02 absolute of the best pipeline
for that query. A sensitivity analysis repeats labeling at 0.00 and 0.05; the
0.02 definition remains the preregistered primary result. A small scikit-learn
classifier learns those labels.

The classifier is calibrated on a validation split. Its fallback threshold is
the lowest confidence cutoff that keeps aggregate validation nDCG@10 within
0.02 absolute of always using hybrid plus reranking. If no cutoff meets that
condition, every query falls back. RAGelit never tunes the cutoff on the held-out
test domain. This fallback is part of the method, not an error path. The
evaluation reports how often it fires and whether it limits quality loss on an
unseen domain.

The live interface explains a route using the recorded input features and the
selected class. It does not expose hidden model reasoning or describe a
probability as certainty.

### Answer generation

The answer service receives the selected passages, constructs a bounded context,
and calls the configured OpenAI-compatible endpoint. The prompt requires
passage identifiers for factual claims. The response schema contains the answer,
cited passage identifiers, retrieval strategy, and timings.

Before generation, the cross-encoder scores the top three selected passages as
a constant-size evidence gate. Its threshold is fitted on validation relevance
labels for at least 90 percent precision and is frozen before held-out-domain
evaluation. If no passage clears that gate, the service abstains instead of
asking the generator to answer from memory. Evidence-gate time is reported
separately so it cannot be hidden inside generation latency. A provider failure
does not erase the retrieval trace; the interface shows the retrieved evidence
and the generation error separately.

### Evaluation runner

The evaluation runner operates independently from the web application. It
downloads or reads version-pinned benchmark data, builds isolated indexes, runs
each baseline, trains the router, and writes immutable run artifacts containing:

- Configuration and dependency versions.
- Dataset identifiers and checksums.
- Per-query rankings, relevance metrics, route choice, and timings.
- Aggregate metrics with paired bootstrap confidence intervals.
- Machine, CPU, memory, and concurrency settings.

The initial study uses three text-retrieval domains from BEIR: SciFact,
NFCorpus, and FiQA. It uses leave-one-domain-out evaluation. Two domains train
and calibrate the router; the third remains untouched until final evaluation.
The rotation repeats so every domain serves once as the unseen test domain.

Primary metrics are nDCG@10, Recall@10, MRR@10, p50 and p95 retrieval latency,
CPU time, peak resident memory, index build time, and index size. Router results
also report regret against the per-query oracle, fallback rate, and the
quality-latency frontier. Generation quality is secondary and is not used to
train the router.

## Data flow

```text
upload
  -> durable ingestion job
  -> extract and chunk
  -> dense embedding + BM25 sparse representation
  -> staged Qdrant version
  -> activate version

question
  -> inspectable query features
  -> calibrated router or explicit user-selected pipeline
  -> retrieve and optionally rerank
  -> cited context
  -> OpenAI-compatible generator
  -> answer + citations + retrieval trace
```

Benchmark runs use the same chunking and retrieval implementations as the live
API. They do not maintain a second research-only implementation.

## API surface

The first public API contains:

- `POST /v1/documents` to upload one supported document.
- `GET /v1/documents` to list documents and ingestion states.
- `DELETE /v1/documents/{document_id}` to remove a document and its versions.
- `POST /v1/query` to answer with `auto`, `bm25`, `dense`, `hybrid`, or
  `reranked` retrieval.
- `POST /v1/compare` to run retrieval-only comparison across all pipelines.
- `GET /v1/runs/{run_id}` to inspect a stored query trace.
- `GET /health` to report API, SQLite, model, and Qdrant readiness.

Requests have explicit size and timeout limits. Errors use stable codes for
unsupported files, extraction failure, empty text, indexing failure, unavailable
models, unavailable Qdrant, insufficient evidence, and provider failure.

## Failure handling

- Upload validation happens before a durable job is created.
- Ingestion states and errors survive process restarts.
- Only fully indexed document versions become active.
- Model loading failures make affected capabilities unavailable in `/health`.
- Query timeouts cancel downstream work and preserve the partial trace.
- Empty or weak retrieval returns an abstention with suggested next actions.
- Generation errors remain separate from retrieval errors.
- Benchmark runs fail closed when expected rows, labels, or artifacts are
  missing; they never silently skip a dataset.

Logs contain identifiers, stage names, durations, and error codes. They do not
contain uploaded document text, prompts, answers, API keys, or provider headers.

## Testing

Unit tests cover chunk boundaries, provenance, fusion, router features, metric
calculations, calibration thresholds, and abstention decisions.

Integration tests use a real local Qdrant container and a deterministic tiny
corpus. They cover indexing, version activation, all four pipelines, deletion,
restart recovery, and API contracts. Generator tests use a local fake
OpenAI-compatible server so CI needs no secret or network access.

One regression fixture contains exact identifiers, paraphrases, and mixed
questions whose relevant passages are labeled. It guards the behavioral
difference between BM25, dense, hybrid, and reranked retrieval without asserting
unstable floating-point scores.

CI runs formatting, linting, type checks, unit tests, integration tests, and a
small benchmark smoke test. Before a public release, the documented setup is run
from a clean clone on Windows and a Linux CI runner.

## Success criteria

The project is complete when:

- A clean clone can start the API, web interface, and Qdrant with documented
  commands.
- A user can upload a supported document and receive a cited answer.
- All four retrieval pipelines and automatic routing are visible in the
  comparison interface.
- Tests prove retry-safe ingestion, version activation, retrieval behavior, and
  abstention.
- The leave-one-domain-out study is reproducible from committed configuration
  and produces per-query artifacts.
- The report states whether the router helped, failed, or traded quality for
  latency. Completion does not depend on obtaining a positive result.

No resume bullet will claim scale, accuracy, or latency until a committed
artifact reproduces that number.

## Delivery milestones

1. **Foundation:** package layout, configuration, health checks, Docker Qdrant,
   and test harness.
2. **Deterministic retrieval:** benchmark import, chunking, indexing, four
   retrieval pipelines, metrics, and regression corpus.
3. **Document application:** durable ingestion, API contracts, web upload and
   query screens, citations, and traces.
4. **Routing study:** labels, classifier, calibration, fallback, held-out-domain
   protocol, and statistical analysis.
5. **Answer generation:** OpenAI-compatible integration, abstention, provider
   failure behavior, and optional ModelGate validation.
6. **Release evidence:** complete benchmark, clean-clone checks, documentation,
   demo script, and resume wording grounded in measured results.

These become GitHub milestones after the local plan is approved and a remote
repository is created. Each issue must end in an observable behavior or artifact;
generic issues such as "improve RAG" are not accepted.

## Explicit non-goals

The first release will not include authentication, billing, tenants, OCR,
multimodal retrieval, knowledge graphs, autonomous agents, Kubernetes, Milvus,
custom vector-database algorithms, or LLM fine-tuning. It will not claim that
adaptive routing is new. ProposalPilot integration begins only after RAGelit
passes its standalone clean-clone and benchmark checks.
