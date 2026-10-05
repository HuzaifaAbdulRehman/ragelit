# Owned isolation comparison

Issue 22 compares the roadmap's shared pre-filter, tenant-separated and lab-only
retrieve-then-filter strategies. Use the same invented documents, real ingestion,
PostgreSQL authorization, chat service and versioned 51-case audit inventory.
The application keeps shared pre-filtering as its default. This is an experiment,
not a deployment option or a claim that a preferred strategy wins.

The user authorized autonomous completion of the existing roadmap. No new
dependency, ordinary-document import, paid model call, production configuration
selector or additional dashboard is needed. Issue 23 still owns model-quality
measurements, confidence intervals and the full benchmark metric set.

## Storage and query boundary

Move the existing Qdrant query into the store without changing its dense/sparse
prefetch filters, RRF fusion, thresholds, limits or payload behavior. Add a typed
SearchProjection with raw and accepted point tuples. Shared and tenant stores
return identical tuples. AuthorizedRetriever records raw points before mapping
accepted points into AuthorizedChunk; current membership, eligible-version SQL
and projection validation remain mandatory.

Store search receives the refreshed organization ID, nonempty eligible-version
tuple, Embedding, Qdrant authorization Filter and limit. It never discovers its
tenant from query text, payload metadata, headers or a mutable global collection.

TenantCollectionStore lives in the audit package and routes real stage,
activation, access updates, deactivation and search to a QdrantChunkStore per
registered organization. Names are the owned audit namespace plus
_tenant_<organization UUID hex>. Retain all authorization filters inside each
tenant's collection; physical separation does not replace document grants.

All registered collections use the existing dense/sparse configuration and
indexes. Unknown organizations, unowned pre-existing collections, missing or
unexpected namespace collections and altered points fail closed. Never delete,
adopt or reset collections automatically. Do not copy a shared index and query
stale tenant mirrors.

## Owned workspace integration

AuditConfiguration gains an explicit vector_strategy of shared_pre_filter
(default) or tenant_collections. Only the alternate strategy changes config_hash;
default configuration hashes, fixture checksums and binding schema stay intact.
Ordinary application Settings and the existing configuration reader gain no
strategy selector.

Create tenant collections during the existing organization-seeding mutation.
Snapshot every registered collection, including vectors and payloads, with
collection-qualified point IDs for tenant mode. Validate the exact namespace
inventory and saved bindings on reopen and before mutations. Keep legacy shared
snapshot keys unchanged. A partially seeded workspace remains interrupted and
cannot be silently reused.

The tenant registry comes from saved owned organization bindings, never a caller's
arbitrary collection name. The lab's database, filesystem, service endpoints and
application-role guards still apply. Test cleanup resolves only the fixture's
exact database, role and registered collection names.

## Retrieve-then-filter baseline

LabPostFilterStore is guarded by an open validated AuditWorkspace, explicit lab
opt-in and local/test configuration. It queries shared storage without dense,
sparse or outer authorization filters, then filters fused results by the current
organization, active flag and SQL-authorized version IDs. The observer still sees
the original fused results. Malformed accepted projections remain errors.

This intentionally exposes forbidden evidence at retrieval_raw even if later
context and output are contained. Candidate starvation and utility failures are
valid negative results, not reasons to tune fixtures until the baseline passes.
Internal dense/sparse prefetch is still outside the observation boundary.

BundledAuditTarget may accept an explicit retrieval_store override only with lab
opt-in, safe target profile, the same client and the workspace's exact collection
inventory. No web route or ordinary Settings can enable the baseline. Existing
vulnerable/deny-all target behavior and their 51-case artifacts remain unchanged.

## Execution and evidence

Add a separate isolation_cli with --strategy shared_pre_filter,
tenant_collections or lab_post_filter. The baseline requires --lab. Use explicit
RAGELIT_AUDIT_* credentials and an owned namespace; do not alter the old audit or
injection command's environment interpretation. Use FixtureEmbeddings and
FixtureCitingProvider, not downloaded model weights.

Run prepare_pack and the same execute_cases/scorer for all strategies. Positive
and negative functional controls cover organization, direct-user and group
access, foreign tenants, forged groups, access removal, deletion and replacement.
The full audit inventory includes revocation, metadata and citation controls.
Shared and tenant-separated full runs must return 0 with all 51 cases covered.
The lab full run must return 1, with retrieval_raw exposure and no forbidden
context/output. Missing evidence, partial inventory or runtime errors return 2.

Persist a strict IsolationReport wrapping the ordinary AuditReport, declared
strategy, exact physical collection inventory and the bound AuditCase tuple.
Record existing Git/dirty/lock/template/binding/config provenance. Re-score
observations with the saved bound cases and verify logical IDs, positive flags
and expected statuses against the pinned fixture. This is operator provenance,
not an attestation against an administrator fabricating bindings or observations.

Use original-byte SHA-256 receipts, bounded duplicate-key-free JSON, redaction,
atomic no-overwrite publication and exact inventory validation. The old report
schema and its validator do not change. Export the three comparison reports and
receipts separately; the release gate checks literal 0/0/1 outcomes and coverage.
A safe strategy that denies every positive query cannot pass.

Retain commands, measured runtimes, counts, first-exposure boundaries and failures.
Do not present deterministic embeddings as real-model quality measurements.
No latency, index/storage, revocation or retrieval-quality metric is dropped
from issue 23. No release or issue closure until the reviewed current head's
required Linux gate passes and repository visibility permits private delivery.
