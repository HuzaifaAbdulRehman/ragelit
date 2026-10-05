# Natural-language utility corpus

This is the dataset portion of the approved M5.2 benchmark, not a replacement
for the security packs or the remaining release work. Audit topic tokens and
hand-built vectors are useful for access regressions but cannot establish
real embedding or answer quality. Keep those fixtures unchanged.

Create `app.evaluation.dataset` with immutable, extra-field-rejecting corpus
models using the existing fixture organization, group and actor contracts.
`generate_utility_corpus(seed: int = 20261005) -> UtilityCorpus` is pure: no
filesystem, database, network, embedding or generation operation.

The generator ID is `natural-utility-v1`. Each of three invented organizations
has four groups and seven actors. Each has 20 organization-wide policy records,
two records per group and one direct-user record: 87 documents and 87 permitted
questions total. Engineering members ask the public and direct-user questions;
each group's member asks its two restricted questions. There are no real users
or company documents. Seed must be an integer in `[0, 2**32)`.

Documents have logical ID, organization, topic, text, expected answer, visibility
and user/group grants. Questions have logical ID, actor, natural-language text,
relevant document IDs and expected answer. The policy catalog contains explicit
question paraphrases and fact templates. Seeded numeric values differ across
organizations without adding opaque topic tokens or answer hints to questions.
Text is short enough for one production chunk. Labels come from the authored
catalog, never from a retriever or model response.

`UtilityCorpus.permitted_document_ids(actor_id: str) -> frozenset[str]` computes
declared dataset eligibility for labels. It is not a runtime authorization
implementation. Same-organization membership and organization visibility,
direct grant or matching group grant are required. Owner/admin/auditor roles
alone do not grant restricted access. Production remains PostgreSQL/RLS and
AuthorizedRetriever; later measurements compare observations with these labels.

Validate fixed inventory, unique identities, group/actor organization references,
same-organization grants, nonempty text/answers/questions, complete document-label
coverage, permitted relevant documents and matching expected facts. Reject
missing, foreign or contradictory labels before any benchmark I/O.

`canonical_manifest() -> bytes` uses sorted-key compact UTF-8 JSON. It retains
scope/grant identities, ordered inventory and relevance labels, but replaces
document text, question text and expected answers with SHA-256 hashes.
`checksum: str` hashes those exact bytes. Content, question, label, actor, grant
or seed changes must change it. Full corpus data stays available to the local
runner through the immutable objects, not in redacted result manifests.

This is a small authored synthetic corpus, not a blind holdout or evidence of
generalization. A complete benchmark still needs actual embedding weights and
settings, generation provenance, all primary security/utility/cost metrics,
per-query artifacts and the three isolation strategies. No model download,
paid provider, app setting, new dependency or new portal is part of this slice.
