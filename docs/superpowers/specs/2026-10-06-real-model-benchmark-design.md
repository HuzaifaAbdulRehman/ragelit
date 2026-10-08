# Real-model benchmark integration

This implements the remaining M5.2 benchmark, not a new product feature. The
utility corpus and calculation helpers are already committed. Completion needs
measurements through the existing application, all original primary metrics,
raw records, and honest failure coverage. Deterministic fixture results remain
separate from real-model results.

## Model and dataset identity

Use `natural-utility-v1`, seed `20261005`, with its recorded manifest checksum.
Keep the access-control and injection packs for security rates: utility facts
are not unique disclosure canaries.

Use the production embedding implementation with FastEmbed `0.8.1`, dense
`BAAI/bge-small-en-v1.5` (384 dimensions), and sparse `Qdrant/bm25`. Pin the
effective Qdrant model repositories to immutable commits and SHA-256/size pairs
for every consumed file, including tokenizer and English stopwords. Download
only public assets without authentication into an owned local folder. Preserve
the downloaded model cards and license declarations. Do not commit weights.

The benchmark provider inherits the production document/query methods. It
loads explicit local model paths with `local_files_only=True`, two CPU threads,
and `CPUExecutionProvider`. Verify all assets before constructing either model.
Missing, changed, linked, oversized, or extra model files fail closed. Pin sparse
settings to the current production defaults: English, stemmer enabled,
`k=1.2`, `b=0.75`, `avg_len=256`, and token length `40`. Record these settings and
the pin checksum. Reranker is `none`; do not add one just for this benchmark.

Generation uses the existing compatible provider against an owned loopback
model server, with recorded model/weights/server identity, prompt hash,
temperature `0`, maximum tokens `1024`, JSON response format, and 30-second
deadline. A declared weights checksum alone does not prove what a server loaded.
Keep fake-provider measurements explicitly labeled. No paid provider calls.

## Application path and workspace

Reuse `AuditWorkspace` ownership markers, locks, non-owner RLS role, inventory,
and mutation guards. Bind embedding identity into the configuration checksum;
default fixture configurations must retain their existing identity. Pass the
same embedding instance to ingestion and the application retriever. Collection
geometry follows its dimension. Reject an unbound custom provider before I/O.

Give utility seeding an explicit template identity. Seed documents and grants
through the existing upload/administration APIs and ingestion worker. Missing
or failed ingestion is setup failure. Do not require a correct retrieval answer
during utility seeding: doing so would exclude the failures being measured.
Existing deterministic seeding retains its retrieval probes by default.

Run queries through production chat and `AuthorizedRetriever`, with the same
context limits and citation validation. Refresh login for each measured query
without changing token lifetime. Authentication time is not retrieval latency.
Collapse chunk rankings to distinct documents in first-occurrence order before
Recall@10/MRR@10. Match strategy comparisons by logical query IDs, not physical
workspace UUIDs. Keep the lab strategy guarded and local-only.

## Evidence and all primary metrics

Write bounded per-query records with logical IDs, qrels, ranked document/chunk
IDs, stage durations, terminal state, citation/answer-label checks, and errors.
Do not write passwords, tokens, full prompts, answers, document bodies, or raw
canary values. Missing stages remain missing, not zero exposure or zero latency.
Preserve partial records on interruption. Offline validation replays summaries
against the pinned corpus and rejects duplicate or omitted query IDs.

Measure all roadmap metrics: unauthorized retrieval, context exposure, output
disclosure, injection success, Recall@10, MRR@10, citation correctness, p50/p95
retrieval latency, index-build time, storage, collection count, and revocation
delay. State each denominator and missing coverage. Use unique canaries only
for the security packs. Paired confidence intervals require matching complete
query cohorts and use the committed seeded bootstrap helper.

Record dataset/model/settings checksums, source revision/dirty state, dependency
locks, server versions, and machine details. Define index time as measured
ingestion work, excluding model download/loading. Keep measured storage units
and scope explicit; database/vector storage are not inferred from embedding
dimension. Measure revocation against the application authorization boundary,
with the same actor before and after the committed grant change.

No tuning primary metrics after seeing results. Negative retrieval, abstentions,
timeouts, invalid citations, and incomplete coverage belong in the result.
Checksum verification is integrity evidence, not operator attestation. This is
a small authored synthetic corpus, not a representative company dataset or a
research novelty claim.

## Verification and delivery

Implement inline under the user's autonomous execution instructions. Use TDD
and focused checks while building, then one independent integration review and
the current-head milestone gate. Preserve earlier unchanged evidence. A local
model smoke test proves embedding execution, not application quality.

Finish with actual production-path measurements and offline artifact validation.
Long service-backed runs wait for a confirmed awake laptop window. Do not alter
power settings or token expiry to work around sleep. Remote writes remain held
while GitHub reports the repository public; privacy is a separate unresolved
manual decision. Keep this goal active until all original release work is done.
