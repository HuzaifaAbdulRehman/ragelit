#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

run_gate() {
  local name="$1"
  shift
  printf '\n==> %s\n' "$name"
  "$@"
}

cd "$repo_root"
run_gate "Compose configuration" docker compose config --quiet
run_gate "Fixture process cleanup" node --test scripts/tests/e2e-processes.test.mjs

cd "$repo_root/backend"
run_gate "Backend dependencies" uv sync --frozen
if [[ -z "${RAGELIT_AUDIT_EXPORT_DIRECTORY:-}" ]]; then
  audit_run_id="$(uv run --frozen python -c 'from uuid import uuid4; print(uuid4())')"
  export RAGELIT_AUDIT_EXPORT_DIRECTORY="$repo_root/data/audit-reports/verification-$audit_run_id"
fi
if [[ -e "$RAGELIT_AUDIT_EXPORT_DIRECTORY" || -L "$RAGELIT_AUDIT_EXPORT_DIRECTORY" ]]; then
  printf '%s\n' 'Audit exports need a fresh destination.' >&2
  exit 2
fi
run_gate "Backend format" uv run ruff format --check app tests
run_gate "Backend lint" uv run ruff check app tests
run_gate "CI script format" uv run ruff format --check --config pyproject.toml ../scripts
run_gate "CI script lint" uv run ruff check --config pyproject.toml ../scripts
run_gate "Backend types" uv run mypy app tests
run_gate "CI script types" uv run mypy ../scripts
run_gate "Backend unit tests" \
  uv run pytest tests/unit tests/api/test_health.py \
  tests/test_repository_contract.py -q
run_gate "PostgreSQL integration tests" \
  uv run pytest tests/integration tests/api tests/test_migration_head.py -q
run_gate "Audit release artifacts" \
  uv run --frozen python -m app.audits.cli \
  --validate-reports "$RAGELIT_AUDIT_EXPORT_DIRECTORY"

cd "$repo_root/frontend"
run_gate "Frontend dependencies" npm ci
run_gate "Playwright browser" npx playwright install --with-deps chromium
run_gate "Generated API client" npm run generate:api
run_gate "Generated API diff" \
  git -C "$repo_root" diff --exit-code -- frontend/src/api/generated/schema.ts
run_gate "Frontend checks" npm run check
run_gate "Frontend build" npm run build
run_gate "Browser journeys" \
  npm run test -- --reporter=line
