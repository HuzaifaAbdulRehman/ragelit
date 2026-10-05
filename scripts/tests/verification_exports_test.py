import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

EXPORTS = {
    "audit": "RAGELIT_AUDIT_EXPORT_DIRECTORY",
    "injection": "RAGELIT_INJECTION_EXPORT_DIRECTORY",
    "isolation": "RAGELIT_ISOLATION_EXPORT_DIRECTORY",
}
MODULES = [
    "app.audits.cli",
    "app.audits.injection_cli",
    "app.audits.isolation_cli",
]
RUNNER = r"""
$ErrorActionPreference = 'Stop'
$global:LASTEXITCODE = 0
$global:Validated = [System.Collections.Generic.List[object]]::new()
function docker { $global:LASTEXITCODE = 0 }
function node { $global:LASTEXITCODE = 0 }
function git { $global:LASTEXITCODE = 0 }
function npx.cmd { $global:LASTEXITCODE = 0 }
function npm.cmd {
    $global:LASTEXITCODE = if ($env:FAIL_MODULE) { 83 } else { 0 }
}
function uv {
    if ($args[0] -eq 'sync') { $global:LASTEXITCODE = 0; return }
    if ($env:VERIFY_MODE -eq 'block') { $global:LASTEXITCODE = 81; return }
    if ($env:FAIL_VERIFICATION_TEST) {
        $expected = @(
            'run', '--frozen', 'python', '-m', 'unittest', 'discover',
            '-s', '../scripts/tests', '-p', 'verification_exports_test.py'
        )
        if (($args -join '|') -ceq ($expected -join '|')) {
            $global:LASTEXITCODE = 75; return
        }
        if ($args -contains 'pytest') { $global:LASTEXITCODE = 76; return }
    }
    if ($args -contains '--validate-reports') {
        $exports = @{
            'app.audits.cli' = 'RAGELIT_AUDIT_EXPORT_DIRECTORY'
            'app.audits.injection_cli' = 'RAGELIT_INJECTION_EXPORT_DIRECTORY'
            'app.audits.isolation_cli' = 'RAGELIT_ISOLATION_EXPORT_DIRECTORY'
        }
        if ($args.Count -ne 7 -or $args[0] -ne 'run' -or
            $args[1] -ne '--frozen' -or $args[2] -ne 'python' -or
            $args[3] -ne '-m' -or $args[5] -ne '--validate-reports' -or
            -not $exports.ContainsKey($args[4])) {
            $global:LASTEXITCODE = 82; return
        }
        $value = [Environment]::GetEnvironmentVariable($exports[$args[4]], 'Process')
        if (-not $value -or $args[6] -cne $value) {
            $global:LASTEXITCODE = 82; return
        }
        $global:Validated.Add([pscustomobject]@{module=$args[4]; directory=$value})
        $global:LASTEXITCODE = if ($env:FAIL_MODULE -eq $args[4]) { 74 } else { 0 }
        return
    }
    $global:LASTEXITCODE = 0
}
$names = @(
    'RAGELIT_AUDIT_EXPORT_DIRECTORY',
    'RAGELIT_INJECTION_EXPORT_DIRECTORY',
    'RAGELIT_ISOLATION_EXPORT_DIRECTORY'
)
$before = @($names | ForEach-Object {
    [Environment]::GetEnvironmentVariable($_, 'Process')
})
$initialLocation = (Get-Location).Path
$message = $null
try {
    & (Join-Path $PSScriptRoot 'scripts/verify.ps1')
    $code = $LASTEXITCODE
} catch {
    $code = 2
    $message = $_.Exception.Message
}
$restored = $true
for ($index = 0; $index -lt $names.Count; $index++) {
    if ([Environment]::GetEnvironmentVariable($names[$index], 'Process') -cne
        $before[$index]) { $restored = $false }
}
[pscustomobject]@{
    code=$code
    message=$message
    restored=$restored
    location_restored=((Get-Location).Path -ceq $initialLocation)
    validated=@($global:Validated.ToArray())
} | ConvertTo-Json -Compress -Depth 4
exit $code
"""


@unittest.skipUnless(sys.platform == "win32", "Native Windows verification script")
class WindowsVerificationExportsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="ragelit-verify-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "scripts").mkdir()
        (self.root / "backend").mkdir()
        (self.root / "frontend").mkdir()
        shutil.copyfile(
            Path(__file__).resolve().parents[1] / "verify.ps1",
            self.root / "scripts/verify.ps1",
        )
        (self.root / "runner.ps1").write_text(RUNNER, encoding="utf-8")
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if key not in EXPORTS.values()
        }
        self.environment.pop("FAIL_MODULE", None)
        self.environment.pop("VERIFY_MODE", None)
        self.environment.pop("FAIL_VERIFICATION_TEST", None)

    def invoke(self, environment: dict[str, str]) -> tuple[int, dict[str, Any], str]:
        powershell = shutil.which("powershell.exe")
        assert powershell is not None
        result = subprocess.run(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.root / "runner.ps1"),
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        self.assertEqual(result.stderr, "", result.stderr)
        details = json.loads(result.stdout.splitlines()[-1])
        self.assertEqual(details["code"], result.returncode)
        self.assertTrue(details["restored"], result.stdout)
        self.assertTrue(details["location_restored"], result.stdout)
        return result.returncode, details, result.stdout

    def supplied(self, prefix: str) -> dict[str, str]:
        return self.environment | {
            variable: str(self.root / f"{prefix}-{pack}")
            for pack, variable in EXPORTS.items()
        }

    def test_existing_destinations_are_preserved_before_checks(self) -> None:
        for pack in ("injection", "isolation"):
            for kind in ("file", "directory"):
                with self.subTest(pack=pack, kind=kind):
                    environment = self.supplied(f"blocked-{pack}-{kind}")
                    environment["VERIFY_MODE"] = "block"
                    destination = Path(environment[EXPORTS[pack]])
                    if kind == "file":
                        destination.write_bytes(b"retained evidence")
                    else:
                        destination.mkdir()
                    code, details, output = self.invoke(environment)
                    self.assertEqual(code, 2, output)
                    self.assertEqual(
                        details["message"],
                        f"{pack.capitalize()} exports need a fresh destination.",
                    )
                    self.assertNotIn("Backend format", output)
                    if kind == "file":
                        self.assertEqual(destination.read_bytes(), b"retained evidence")
                    else:
                        self.assertTrue(destination.is_dir())
                        self.assertEqual(list(destination.iterdir()), [])

    def test_each_failed_validator_blocks_frontend_checks(self) -> None:
        for pack in ("injection", "isolation"):
            with self.subTest(pack=pack):
                environment = self.supplied(f"failed-{pack}")
                environment["FAIL_MODULE"] = f"app.audits.{pack}_cli"
                code, _, output = self.invoke(environment)
                self.assertEqual(code, 74, output)
                self.assertNotIn("Frontend dependencies", output)

    def test_script_regressions_gate_the_expensive_backend_checks(self) -> None:
        environment = self.supplied("failed-script-tests")
        environment["FAIL_VERIFICATION_TEST"] = "1"
        code, _, output = self.invoke(environment)
        self.assertEqual(code, 75, output)
        self.assertNotIn("Backend unit tests", output)

    def test_success_validates_each_supplied_destination(self) -> None:
        environment = self.supplied("success")
        code, details, _ = self.invoke(environment)
        self.assertEqual(code, 0)
        self.assertEqual(
            details["validated"],
            [
                {"module": module, "directory": environment[variable]}
                for module, variable in zip(MODULES, EXPORTS.values(), strict=True)
            ],
        )

    def test_defaults_are_distinct_fresh_directories_and_do_not_leak(self) -> None:
        code, details, _ = self.invoke(self.environment)
        self.assertEqual(code, 0)
        validated = details["validated"]
        self.assertEqual([item["module"] for item in validated], MODULES)
        directories = [Path(item["directory"]) for item in validated]
        self.assertEqual(len(set(directories)), 3)
        for directory, pack in zip(directories, EXPORTS, strict=True):
            self.assertEqual(directory.parent, self.root / "data" / f"{pack}-reports")
            self.assertTrue(directory.name.startswith("verification-"))
            self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
