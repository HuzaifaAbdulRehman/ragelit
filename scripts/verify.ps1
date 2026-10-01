$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Gate {
    param(
        [Parameter(Mandatory)]
        [string]$Name,
        [Parameter(Mandatory)]
        [string]$Command,
        [Parameter()]
        [string[]]$Arguments = @()
    )

    Write-Host
    Write-Host '==>' $Name
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Push-Location $RepoRoot
try {
    Invoke-Gate 'Compose configuration' 'docker' @('compose', 'config', '--quiet')

    Push-Location 'backend'
    try {
        Invoke-Gate 'Backend dependencies' 'uv' @('sync', '--frozen')
        Invoke-Gate 'Backend format' 'uv' @(
            'run', 'ruff', 'format', '--check', 'app', 'tests'
        )
        Invoke-Gate 'Backend lint' 'uv' @(
            'run', 'ruff', 'check', 'app', 'tests'
        )
        Invoke-Gate 'Backend types' 'uv' @(
            'run', 'mypy', 'app', 'tests'
        )
        Invoke-Gate 'Backend unit tests' 'uv' @(
            'run', 'pytest', 'tests/unit', 'tests/api/test_health.py',
            'tests/test_repository_contract.py', '-q'
        )
        Invoke-Gate 'PostgreSQL integration tests' 'uv' @(
            'run', 'pytest', 'tests/integration', 'tests/api',
            'tests/test_migration_head.py', '-q'
        )
    }
    finally {
        Pop-Location
    }

    Push-Location 'frontend'
    try {
        Invoke-Gate 'Frontend dependencies' 'npm.cmd' @('ci')
        Invoke-Gate 'Playwright browser' 'npx.cmd' @(
            'playwright', 'install', 'chromium'
        )
        Invoke-Gate 'Generated API client' 'npm.cmd' @(
            'run', 'generate:api'
        )
        Invoke-Gate 'Generated API diff' 'git' @(
            '-C', $RepoRoot, 'diff', '--exit-code', '--',
            'frontend/src/api/generated/schema.ts'
        )
        Invoke-Gate 'Frontend checks' 'npm.cmd' @('run', 'check')
        Invoke-Gate 'Frontend build' 'npm.cmd' @('run', 'build')
        Invoke-Gate 'Browser smoke tests' 'npm.cmd' @(
            'run', 'test', '--', 'auth.spec.ts', 'tenant-navigation.spec.ts'
        )
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
