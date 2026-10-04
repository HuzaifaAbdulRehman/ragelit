# RAGelit working instructions

## Keep verification proportionate

- Use focused tests during implementation. Run the full milestone gate once
  after the code is ready, not after every intermediate change.
- Do not rerun an unchanged passing suite for bookkeeping. Record its command,
  source revision, result, and log so later sessions can reuse that evidence.
- A failed check needs a focused investigation and regression test before a
  broader rerun. Keep tenant-isolation and access-control checks intact.
- Keep the approved milestone scope. Ask before adding features or extra
  hardening that would delay the usable application.
- Before starting a long check, explain its purpose and expected duration
  using measured runtimes. Keep the user updated while it runs.
- Record completed work and the next step before a pause. Resume from that
  record rather than rebuilding or rechecking completed tasks.

These instructions reflect the user's request on 4 October 2026 after repeated
full test runs delayed progress. They take precedence over skill suggestions
that would repeat the same verification at every task checkpoint.
