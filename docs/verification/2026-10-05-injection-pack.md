# Injection pack verification

The pack uses seed 20261002, three tenants, six invented documents and three
versioned payloads: instruction override, a forged system message and evaluation
pressure. Queries ask for factual labels. One trial has three benign queries
without poisoned-document access and three attack queries with an explicit grant.

## Recorded evidence

The Windows real-subprocess journey passed in 95.00 seconds. It ran all three
profiles through PostgreSQL, Qdrant, ingestion and the chat API in one owned
workspace, then validated the three original reports and their receipts.

Resistant evaluated three attacks with zero successes; obeying recorded three
candidate-output signals and three successes. Deny-all evaluated no attacks,
failed all three benign controls, and reported a null rate. These deterministic
providers verify the harness. No real-model resistance result is claimed.

The CLI unit checks passed 15 cases in 66.62 seconds, including redacted invalid
arguments, explicit configuration, service-free help and offline validation.
Six directory checks passed in the earlier combined run; the 15 CLI failures in
that run were caused by the then-missing command. The original report subsystem
passed 19 cases, including rechecksummed tampering and no-overwrite publication.
Three interruption checks also passed in 7.42 seconds, covering runtime failure,
keyboard interruption and stale last-observation evidence.
The real loopback-provider failure check passed in 65.28 seconds. It proved
that no ordinary application API key was sent and all six provider-error cases
remained inconclusive with a null attack rate. The server was an owned error
fixture, not a language model.
The independent branch review found one Important defect: duplicate JSON fields
could hide forbidden content in the saved bytes. Five regressions reproduced
it. The fix checks original bytes and rejects duplicate fields in reports and
receipts before parsing. All 30 report/directory checks passed in 10.84 seconds,
and the retained real three-profile reports validate with the corrected reader.
The review found no other Important or Critical issue. The current-head Linux
gate is still pending.

## Run the checks

Start disposable PostgreSQL and Qdrant services as described in the README.
From `backend`, run:

```console
uv run --frozen pytest tests/unit/audits/test_injection_scoring.py tests/unit/audits/test_injection_fixtures.py tests/unit/audits/test_injection_providers.py tests/unit/audits/test_injection_reports.py tests/unit/audits/test_injection_release.py tests/unit/audits/test_injection_cli.py -q
uv run --frozen pytest tests/integration/audits/test_injection_cli.py -q
```

For manual runs, set the README's `RAGELIT_AUDIT_*` variables with a fresh
workspace name and matching root, then use `python -m app.audits.injection_cli`.
All deterministic profiles can reuse that completed injection workspace. An
access-control workspace has different bindings and is not interchangeable.
PowerShell uses `$env:NAME = 'value'`; Linux uses `export NAME='value'`.
Neither mode reads ordinary application provider configuration.

An already running, owned OpenAI-compatible local server can be measured with:

```console
uv run --frozen python -m app.audits.injection_cli --profile local --trials 5 --local-base-url http://127.0.0.1:8001/v1 --model <local-model> --weights-sha256 <64-hex-digest>
```

The endpoint must be numeric loopback HTTP with a `/v1` path, no credentials,
query or fragment. The client sends no API key and uses temperature 0, a
1024-token cap and JSON output. Record the actual server and weights separately;
the CLI records the supplied identity, not an attestation of loaded weights.

## Reading a report

The report pins the Git revision and dirty flag, dependency locks, fixture,
bindings and configuration hashes, provider identity, system prompt and trials.
It stores opaque marker matches and chunk IDs, not documents, prompts, answers,
tokens or passwords. Its receipt hashes the exact saved bytes. Validators
reconstruct case scopes and replay scoring rather than trusting stored counts.
A checksum detects changed bytes; a local administrator can create new evidence.

An eligible attack needs proven authorized context containing the payload and
an observed, nontruncated candidate output. Abstentions are excluded. Signals
remain visible if later evidence is incomplete, but incomplete coverage returns
2. A failed benign control means the attack rate is not evidence of useful
resistance. Report validation never runs cases or modifies the evidence.

Matching is literal. The pack does not test paraphrased markers, tool execution,
external exfiltration or universal injection defense. Retrieval evidence covers
returned fused results, not internal dense or sparse prefetch candidates.
Real-model measurements, isolation strategies and the remaining benchmark
metrics are still pending.
