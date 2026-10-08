# Isolation comparison verification

The comparison uses the same 51 logical access-control cases for three storage
strategies. Shared pre-filtering uses one Qdrant collection. Tenant collection
routing uses three, with the same authorization filters. The lab-only post-filter
baseline uses one and deliberately exposes unauthorized fused results before
applying those filters.

## Recorded evidence

The three real target journeys passed in 1394.91 seconds on Windows. They included
ingestion, non-owner PostgreSQL RLS, Qdrant searches, revocation, deletion,
replacement and forged-group checks. Expected gates were 0, 0 and 1, with
collection counts 1, 3 and 1. The lab exposed raw retrieval evidence; later
forbidden evidence remained contained apart from the deliberately rejected
citation challenge.

The Windows CLI attempt was interrupted by laptop sleep from about 20:41 to
22:44 local time. It took 7739.21 seconds and recorded 14 passing cases followed
by 37 inconclusive authentication denials (401). The original report and receipt
validate, but that does not make the audit pass. Its recorded exit remains 2,
and the three-strategy release pack was not produced.

The successful CLI export check and current-head Linux release gate are still
pending. The separate lab opt-in subprocess check passed in 14.29 seconds.

The later [2026-10-07 local checkpoint](2026-10-07-local-release-checkpoint.md)
supersedes the export-status line above for local evidence: a fresh
three-strategy report set completed and its six reports and receipts passed the
native validator. The historical interrupted report remains inconclusive, and
no real-model quality or security claim follows.

## Run the checks

Start disposable PostgreSQL and Qdrant services as described in the README.
From `backend`:

```console
uv run --frozen pytest tests/integration/audits/test_isolation_cli.py -q
```

Run each strategy with a fresh owned workspace. Set the README's
`RAGELIT_AUDIT_*` variables with a new name and matching root before each command:

```console
uv run --frozen python -m app.audits.isolation_cli --strategy shared_pre_filter
uv run --frozen python -m app.audits.isolation_cli --strategy tenant_collections
uv run --frozen python -m app.audits.isolation_cli --strategy lab_post_filter --lab
```

Do not reuse a shared workspace for the tenant strategy or mix this pack with
injection fixtures. Keep the laptop awake during service-backed checks.

## What the artifacts establish

Reports pin the source revision, dirty flag, dependency lock hashes, generated
fixture, configuration and saved bindings. Each report stores the 51 bound cases
and redacted observations. A SHA-256 receipt covers the original bytes. Validation
rejects duplicate fields, checks the pinned logical cases and replays scoring.
It does not attest that an operator's bound identities or observations came from
the database.

The release validator requires exactly three reports and their receipts,
complete coverage, gates 0/0/1 and collection counts 1/3/1. All three reports must
share source and lock provenance. Existing export destinations are refused.
No ordinary application setting can select the lab baseline or tenant audit
strategy.

These deterministic embeddings and answers test access boundaries, not retrieval
quality or real-model injection resistance. Observations cover fused results,
not internal prefetch candidates. The remaining benchmark metrics belong to the
existing evaluation milestone.
