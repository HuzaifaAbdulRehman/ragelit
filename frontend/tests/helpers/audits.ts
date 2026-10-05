import type { Page } from "@playwright/test"
import type { components } from "../../src/api/generated/schema"
import { runId, signIn } from "./session"

export async function openAudit(page: Page) {
  await page.route("**/api/v1/audits?*", (route) =>
    route.fulfill({
      json: { items: [summary], total: 1, limit: 20, offset: 0 },
    }),
  )
  await signIn(page)
  await page.getByRole("link", { name: "Audits", exact: true }).click()
  await page
    .getByRole("link", { name: summary.created_at, exact: true })
    .click()
}

export const summary: components["schemas"]["AuditRunSummary"] = {
  id: runId,
  created_at: "2026-10-04T10:00:00Z",
  state: "queued",
  outcome: "unknown",
  report_available: false,
  started_at: null,
  finished_at: null,
  exit_code: null,
  error_code: null,
  report_id: null,
  report_sha256: null,
}
export const evidence: components["schemas"]["AuditReport"] = {
  schema_version: "1",
  run_id: "88888888-8888-4888-8888-888888888888",
  created_at: "2026-10-04T10:01:00Z",
  required_case_ids: ["synthetic-case"],
  coverage_complete: false,
  exit_code: 2,
  inventory_reason: "inventory_incomplete",
  runtime_failed: false,
  scope_notice:
    "Synthetic fixtures and deterministic providers only; not a security certification or model-quality benchmark.",
  retrieval_notice:
    "Retrieval evidence covers returned fused results, not internal dense or sparse prefetch candidates.",
  metadata: {
    profile: "safe",
    pack_id: "access-control-v1",
    generator_id: "synthetic-fixtures-v1",
    embedding_id: "fixture-topic-v1",
    provider_id: "fixture-citing-v1",
    git_revision: "0".repeat(40),
    git_dirty: false,
    lock_hashes: ["0".repeat(64)],
    template_hash: "0".repeat(64),
    binding_hash: "0".repeat(64),
    config_hash: "0".repeat(64),
  },
  results: [
    {
      case_id: "synthetic-case",
      status: "inconclusive",
      reason: "incomplete_evidence",
      coverage_complete: false,
      first_exposure: "output_candidate",
      observation: {
        case_id: "synthetic-case",
        http_status: 200,
        terminal: "answered",
        boundaries: [
          {
            boundary: "output_candidate",
            sequence: 0,
            state: "observed",
            chunk_ids: [],
            canary_matches: [],
            duration_ms: 2,
            truncated: false,
            decision: "normal",
          },
          {
            boundary: "output_delivered",
            sequence: 1,
            state: "not_reached",
            chunk_ids: [],
            canary_matches: [],
            duration_ms: 0,
            truncated: false,
            decision: "normal",
          },
          {
            boundary: "citations_delivered",
            sequence: 2,
            state: "unobserved",
            chunk_ids: [],
            canary_matches: [],
            duration_ms: 0,
            truncated: false,
            decision: "normal",
          },
        ],
      },
    },
  ],
}
