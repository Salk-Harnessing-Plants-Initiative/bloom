"""bloomctl cyl _batch — shared ScanResult/BatchResult reporting (pure, no client)."""

import json

import pytest

import bloomctl.cyl._batch as batch


def test_scan_result_defaults_to_empty_error():
    r = batch.ScanResult("scan_1", "ok")
    assert r.scan_key == "scan_1"
    assert r.status == "ok"
    assert r.error == ""


def test_scan_result_accepts_all_three_statuses():
    for status in ("ok", "skipped", "failed"):
        r = batch.ScanResult("scan_1", status)
        assert r.status == status


def test_scan_result_rejects_invalid_status():
    """Review finding: status was a bare str with no runtime check, so a typo'd status
    string (e.g. "faild") would silently fail to count as a failure anywhere that checks
    `status == "failed"` or `status != "failed"` — construction must reject it loudly instead."""
    with pytest.raises(ValueError, match="faild"):
        batch.ScanResult("scan_1", "faild")


def test_scan_result_carries_error_message():
    r = batch.ScanResult("scan_1", "failed", "boom")
    assert r.error == "boom"


def test_scan_result_retriable_defaults_to_true():
    r = batch.ScanResult("scan_1", "failed", "boom")
    assert r.retriable is True


def test_batch_result_ok_true_when_no_failures():
    result = batch.BatchResult(
        [batch.ScanResult("scan_1", "ok"), batch.ScanResult("scan_2", "skipped")]
    )
    assert result.ok is True


def test_batch_result_ok_false_when_any_failure():
    result = batch.BatchResult(
        [batch.ScanResult("scan_1", "ok"), batch.ScanResult("scan_2", "failed", "bad")]
    )
    assert result.ok is False


def test_batch_result_ok_true_for_empty_scans():
    assert batch.BatchResult([]).ok is True


def test_batch_result_ok_false_regardless_of_retriable():
    """`.ok` reflects the real, unfiltered outcome — a non-retriable failure is still a
    failure for anyone checking `.ok` or reading the printed summary/JSON."""
    result = batch.BatchResult(
        [batch.ScanResult("scan_1", "failed", "boom", retriable=False)]
    )
    assert result.ok is False


def test_needs_retry_false_for_empty_scans():
    assert batch.BatchResult([]).needs_retry is False


def test_needs_retry_false_when_no_failures():
    result = batch.BatchResult(
        [batch.ScanResult("scan_1", "ok"), batch.ScanResult("scan_2", "skipped")]
    )
    assert result.needs_retry is False


def test_needs_retry_true_for_a_retriable_failure():
    result = batch.BatchResult([batch.ScanResult("scan_1", "failed", "boom")])
    assert result.needs_retry is True


def test_needs_retry_false_when_every_failure_is_non_retriable():
    result = batch.BatchResult(
        [
            batch.ScanResult("scan_1", "ok"),
            batch.ScanResult("scan_2", "failed", "boom", retriable=False),
        ]
    )
    assert result.ok is False  # still a real, reportable failure
    assert result.needs_retry is False  # but nothing a re-run could fix


def test_needs_retry_true_when_mixed_with_at_least_one_retriable_failure():
    """A genuinely retriable failure alongside a non-retriable one still warrants a retry —
    the retry might fix the retriable one, even though it can never fix the other."""
    result = batch.BatchResult(
        [
            batch.ScanResult("scan_1", "failed", "boom", retriable=False),
            batch.ScanResult("scan_2", "failed", "transient network error"),
        ]
    )
    assert result.needs_retry is True


def test_batch_result_defaults_to_empty_scans_list():
    assert batch.BatchResult().scans == []


# --- rendering ---------------------------------------------------------------


def test_format_summary_all_ok():
    result = batch.BatchResult(
        [batch.ScanResult("scan_1", "ok"), batch.ScanResult("scan_2", "ok")]
    )
    summary = batch.format_summary(result, verb="Staged", noun="scan", destination="/tmp/out")
    assert "2/2" in summary
    assert "/tmp/out" in summary
    assert "failed" not in summary.lower()


def test_format_summary_names_every_failure():
    result = batch.BatchResult(
        [
            batch.ScanResult("scan_1", "ok"),
            batch.ScanResult("scan_2", "failed", "no frames found for scan 2"),
        ]
    )
    summary = batch.format_summary(result, verb="Staged", noun="scan", destination="/tmp/out")
    assert "1/2" in summary
    assert "1 failed" in summary.lower()
    assert "scan_2" in summary
    assert "no frames found for scan 2" in summary


def test_format_summary_reports_skipped_count():
    result = batch.BatchResult(
        [batch.ScanResult("scan_1", "ok"), batch.ScanResult("scan_2", "skipped")]
    )
    summary = batch.format_summary(result, verb="Staged", noun="scan", destination="/tmp/out")
    assert "1 skipped" in summary.lower()


def test_format_json_round_trips_every_field():
    result = batch.BatchResult(
        [
            batch.ScanResult("scan_1", "ok"),
            batch.ScanResult("scan_2", "failed", "boom"),
            batch.ScanResult("scan_3", "skipped"),
            batch.ScanResult("scan_4", "failed", "unfixable", retriable=False),
        ]
    )
    data = json.loads(batch.format_json(result))
    assert data == [
        {"scan_key": "scan_1", "status": "ok", "error": "", "retriable": True},
        {"scan_key": "scan_2", "status": "failed", "error": "boom", "retriable": True},
        {"scan_key": "scan_3", "status": "skipped", "error": "", "retriable": True},
        {"scan_key": "scan_4", "status": "failed", "error": "unfixable", "retriable": False},
    ]


def test_format_json_empty_batch_is_empty_array():
    assert json.loads(batch.format_json(batch.BatchResult([]))) == []
