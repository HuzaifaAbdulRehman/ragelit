# Utility Corpus Implementation Plan

> For agentic workers: use superpowers:executing-plans inline. Track the
> dataset proof separately from benchmark or release completion.

**Goal:** Pin natural-language documents, authorized relevance labels and
their provenance for the existing M5.2 experiment.

**Architecture:** A pure generator reuses fixture identity contracts while
leaving access/injection fixtures unchanged. Corpus validation protects label
coverage; a redacted canonical manifest pins all effective dataset inputs.

**Tech Stack:** Python 3.12, existing Pydantic and pytest, standard-library hashes.

**Spec:** `docs/superpowers/specs/2026-10-05-utility-corpus-design.md`

## Global constraints

- Generator ID `natural-utility-v1`, default seed `20261005`, integer32-bit seed.
- Three organizations, four groups each,21 actors,87 documents,87 questions.
- Each organization:20 public documents,8 group documents,1 direct-user document.
- No production authorization/provider change, new dependency or real user data.
- No benchmark, model-quality, release or privacy claim from dataset tests alone.
- Preserve all original M5.2 metrics and private-repository publishing boundary.

## Review focus

- A privileged role without a grant must not acquire restricted labels.
- A grant naming a known actor/group from another organization must reject.
- Missing/duplicate questions must not silently improve the metric denominator.
- A changed question, answer, relevance label or grant must change provenance.
- Canonical result manifests must not include source text, questions or answers.

## Task 1: Corpus and independently checkable labels

Create `backend/app/evaluation/dataset.py` and
`backend/tests/unit/evaluation/test_dataset.py`; update
`docs/evaluation/metric-definitions.md` with the corpus command and measured limits.

Consumes: existing FixtureOrganization, FixtureGroup, FixtureActor, Identifier
and frozen AuditModel. No earlier measurement or model result is consumed.
Produces: CorpusDocument, CorpusQuery, UtilityCorpus;
`generate_utility_corpus(seed: int = 20261005) -> UtilityCorpus`;
`UtilityCorpus.permitted_document_ids(actor_id: str) -> frozenset[str]`;
`UtilityCorpus.canonical_manifest() -> bytes`; `UtilityCorpus.checksum: str`.

- [ ] Write literal inventory/label/scope/hash tests. Assert87/87,3/12/21,
  first policy paraphrase/qrel and natural text without audittopic markers;
  public20/engineering23/finance22/owner20 eligible counts with no foreign IDs.
- [ ] Run `uv run --frozen pytest tests/unit/evaluation/test_dataset.py -q`.
  Expected: intended failures for absent generation/scope/manifest behavior.
- [ ] Implement the fixed catalog, seeded facts, label predicate and hashes.
  Run the same tests. Expected: all implemented behaviors pass.
- [ ] Add malformed-inventory, foreign-reference, forbidden-label and missing-fact
  tests. Run before custom validators. Expected: the invalid corpus is accepted.
- [ ] Implement the spec's validators; rerun the focused tests. Expected: pass.
- [ ] Run frozen Ruff format/lint and mypy on the new source/test; source-import
  correlation and post-code review use actual callers, not an unrelated heuristic.
- [ ] Humanize the short corpus notes, secret/diff-check and commit
  `add labeled natural-language utility corpus`.

Use one final review with the completed benchmark integration; until then retain
the corpus proof and do not claim the entire issue or release is complete.
