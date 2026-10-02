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

cd "$repo_root/backend"
run_gate "Backend dependencies" uv sync --frozen
run_gate "Backend format" uv run ruff format --check app tests
run_gate "Backend lint" uv run ruff check app tests
run_gate "Backend types" uv run mypy app tests
run_gate "Backend unit tests" \
  uv run pytest tests/unit tests/api/test_health.py \
  tests/test_repository_contract.py -q
run_gate "PostgreSQL integration tests" \
  uv run pytest tests/integration tests/api tests/test_migration_head.py -q

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
