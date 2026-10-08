$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PreviousAuditExport = [Environment]::GetEnvironmentVariable(
    'RAGELIT_AUDIT_EXPORT_DIRECTORY', 'Process'
)
$PreviousInjectionExport = [Environment]::GetEnvironmentVariable(
    'RAGELIT_INJECTION_EXPORT_DIRECTORY', 'Process'
)
$PreviousIsolationExport = [Environment]::GetEnvironmentVariable(
    'RAGELIT_ISOLATION_EXPORT_DIRECTORY', 'Process'
)

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
    Invoke-Gate 'Fixture process cleanup' 'node' @(
        '--test', 'scripts/tests/e2e-processes.test.mjs'
    )

    Push-Location 'backend'
    try {
        Invoke-Gate 'Backend dependencies' 'uv' @('sync', '--frozen')
        $AuditExport = if ([string]::IsNullOrWhiteSpace($PreviousAuditExport)) {
            Join-Path $RepoRoot ('data/audit-reports/verification-' + [guid]::NewGuid())
        } else {
            [System.IO.Path]::GetFullPath($PreviousAuditExport)
        }
        if (Test-Path -LiteralPath $AuditExport) {
            throw 'Audit exports need a fresh destination.'
        }
        [Environment]::SetEnvironmentVariable(
            'RAGELIT_AUDIT_EXPORT_DIRECTORY', $AuditExport, 'Process'
        )
        $InjectionExport = if ([string]::IsNullOrWhiteSpace($PreviousInjectionExport)) {
            Join-Path $RepoRoot ('data/injection-reports/verification-' + [guid]::NewGuid())
        } else {
            [System.IO.Path]::GetFullPath($PreviousInjectionExport)
        }
        if (Test-Path -LiteralPath $InjectionExport) {
            throw 'Injection exports need a fresh destination.'
        }
        [Environment]::SetEnvironmentVariable(
            'RAGELIT_INJECTION_EXPORT_DIRECTORY', $InjectionExport, 'Process'
        )
        $IsolationExport = if ([string]::IsNullOrWhiteSpace($PreviousIsolationExport)) {
            Join-Path $RepoRoot ('data/isolation-reports/verification-' + [guid]::NewGuid())
        } else {
            [System.IO.Path]::GetFullPath($PreviousIsolationExport)
        }
        if (Test-Path -LiteralPath $IsolationExport) {
            throw 'Isolation exports need a fresh destination.'
        }
        [Environment]::SetEnvironmentVariable(
            'RAGELIT_ISOLATION_EXPORT_DIRECTORY', $IsolationExport, 'Process'
        )
        Invoke-Gate 'Backend format' 'uv' @(
            'run', 'ruff', 'format', '--check', 'app', 'tests'
        )
        Invoke-Gate 'Backend lint' 'uv' @(
            'run', 'ruff', 'check', 'app', 'tests'
        )
        Invoke-Gate 'Backend types' 'uv' @(
            'run', 'mypy', 'app', 'tests'
        )
        Invoke-Gate 'CI script format' 'uv' @(
            'run', 'ruff', 'format', '--check', '--config', 'pyproject.toml',
            '../scripts'
        )
        Invoke-Gate 'CI script lint' 'uv' @(
            'run', 'ruff', 'check', '--config', 'pyproject.toml', '../scripts'
        )
        Invoke-Gate 'CI script types' 'uv' @('run', 'mypy', '../scripts')
        Invoke-Gate 'Verification export tests' 'uv' @(
            'run', '--frozen', 'python', '-m', 'unittest', 'discover',
            '-s', '../scripts/tests', '-p', 'verification_exports_test.py'
        )
        Invoke-Gate 'Backend unit tests' 'uv' @(
            'run', 'pytest', 'tests/unit', 'tests/api/test_health.py',
            'tests/test_repository_contract.py', '-q'
        )
        Invoke-Gate 'PostgreSQL integration tests' 'uv' @(
            'run', 'pytest', 'tests/integration', 'tests/api',
            'tests/test_migration_head.py', '-q'
        )
        Invoke-Gate 'Audit release artifacts' 'uv' @(
            'run', '--frozen', 'python', '-m', 'app.audits.cli',
            '--validate-reports', $AuditExport
        )
        Invoke-Gate 'Injection release artifacts' 'uv' @(
            'run', '--frozen', 'python', '-m', 'app.audits.injection_cli',
            '--validate-reports', $InjectionExport
        )
        Invoke-Gate 'Isolation release artifacts' 'uv' @(
            'run', '--frozen', 'python', '-m', 'app.audits.isolation_cli',
            '--validate-reports', $IsolationExport
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
        Invoke-Gate 'Browser journeys' 'npm.cmd' @(
            'run', 'test', '--', '--reporter=line'
        )
    }
    finally {
        Pop-Location
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        'RAGELIT_AUDIT_EXPORT_DIRECTORY', $PreviousAuditExport, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'RAGELIT_INJECTION_EXPORT_DIRECTORY', $PreviousInjectionExport, 'Process'
    )
    [Environment]::SetEnvironmentVariable(
        'RAGELIT_ISOLATION_EXPORT_DIRECTORY', $PreviousIsolationExport, 'Process'
    )
    Pop-Location
}
