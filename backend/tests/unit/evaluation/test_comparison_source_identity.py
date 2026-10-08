from pathlib import Path

import pytest

from tests.unit.evaluation.report_support import report
from tests.unit.evaluation.test_access_reports import benchmark
from tests.unit.evaluation.test_security_comparisons import injection


def test_utility_dirty_source_records_remain_replayable_but_cannot_be_paired(
    tmp_path: Path,
) -> None:
    from app.evaluation.reports import (
        compare_utility_reports,
        validate_utility_report,
        write_utility_report,
    )

    first, second = report(), report(strategy="tenant_collections")
    dirty = first.provenance.model_copy(update={"git_dirty": True})
    first, second = (
        first.model_copy(update={"provenance": dirty}),
        second.model_copy(update={"provenance": dirty}),
    )
    path = write_utility_report(first, tmp_path)
    replayed = validate_utility_report(path)
    assert replayed.provenance.git_dirty and replayed.coverage_complete
    with pytest.raises(ValueError):
        compare_utility_reports(replayed, second)


def test_access_dirty_source_records_remain_replayable_but_cannot_be_paired(
    tmp_path: Path,
) -> None:
    from app.evaluation.access_reports import (
        validate_access_benchmark,
        write_access_benchmark,
    )
    from app.evaluation.security_comparisons import compare_access_reports

    first, second = benchmark(), benchmark(lab=True)
    dirty = first.provenance.model_copy(update={"git_dirty": True})
    first, second = (
        first.model_copy(update={"provenance": dirty}),
        second.model_copy(update={"provenance": dirty}),
    )
    path = write_access_benchmark(first, tmp_path)
    replayed = validate_access_benchmark(path)
    assert replayed.provenance.git_dirty and replayed.coverage_complete
    with pytest.raises(ValueError):
        compare_access_reports(replayed, second)


def test_injection_dirty_source_records_remain_replayable_but_cannot_be_paired(
    tmp_path: Path,
) -> None:
    from app.evaluation.security_comparisons import compare_injection_reports
    from app.evaluation.security_reports import (
        validate_injection_benchmark,
        write_injection_benchmark,
    )

    first, second = injection(), injection(leak=True)
    dirty = first.provenance.model_copy(update={"git_dirty": True})
    first, second = (
        first.model_copy(update={"provenance": dirty}),
        second.model_copy(update={"provenance": dirty}),
    )
    path = write_injection_benchmark(first, tmp_path)
    replayed = validate_injection_benchmark(path)
    assert replayed.provenance.git_dirty and replayed.coverage_complete
    with pytest.raises(ValueError):
        compare_injection_reports(replayed, second)
