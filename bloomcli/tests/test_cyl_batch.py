"""bloomctl cyl _batch — shared ScanResult/BatchResult reporting (pure, no client)."""

import json

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


def test_scan_result_carries_error_message():
    r = batch.ScanResult("scan_1", "failed", "boom")
    assert r.error == "boom"


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
        ]
    )
    data = json.loads(batch.format_json(result))
    assert data == [
        {"scan_key": "scan_1", "status": "ok", "error": ""},
        {"scan_key": "scan_2", "status": "failed", "error": "boom"},
        {"scan_key": "scan_3", "status": "skipped", "error": ""},
    ]


def test_format_json_empty_batch_is_empty_array():
    assert json.loads(batch.format_json(batch.BatchResult([]))) == []
