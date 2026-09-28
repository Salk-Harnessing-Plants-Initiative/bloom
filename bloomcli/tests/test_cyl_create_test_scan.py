"""bloomctl cyl create-test-scan — synthetic scan creation in A4-PIPELINE-E2E-TEST (mocked client)."""

import json

import pytest
from click.testing import CliRunner

import bloomctl.cli as climod
import bloomctl.cyl.create_test_scan as cts
from bloomctl.cli import cli
from bloomctl.cyl._locks import LockContendedError

EXPERIMENT_ROW = {"id": cts.EXPERIMENT_ID, "name": "A4-PIPELINE-E2E-TEST (synthetic -- safe to break/delete)"}


def _api_error(message, code="P0001"):
    from postgrest import APIError

    return APIError({"message": message, "code": code, "details": None, "hint": None})


def _patch_authed(monkeypatch):
    monkeypatch.setattr(climod, "_authed_client", lambda profile: object())


def _patch_lock(monkeypatch, calls=None):
    """No-op lock by default; records (path, staleness_seconds) into `calls` if given."""

    class _NullLock:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    def _acquire(path, *, staleness_seconds):
        if calls is not None:
            calls.append((path, staleness_seconds))
        return _NullLock()

    monkeypatch.setattr(cts, "acquire_lock", _acquire)


class _RPC:
    def __init__(self, result, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return type("R", (), {"data": self._result})()


class _Table:
    """Records every call; returns a canned response for `select`, records `update` payloads.

    Read-side calls (select/eq/single) are recorded into `queries` — (table, eq_filters,
    is_single) — so tests can pin the exact query shape a function used, not just the outcome.
    An earlier version of these fakes only recorded `update()` calls this way; a wrong `.eq()`
    column on a read (e.g. filtering by `id` instead of `experiment_id`) would have passed every
    existing test silently — the same class of gap as the real experiment-name bug this command
    hit live on staging (see design.md).
    """

    def __init__(self, name, responses, updates, queries, update_errors=None, query_errors=None):
        self.name = name
        self._responses = responses
        self._updates = updates
        self._queries = queries
        self._update_errors = update_errors or {}
        self._query_errors = query_errors or {}
        self._eq_filters = {}
        self._is_single = False

    def select(self, *_a, count=None):
        self._count_mode = count
        return self

    def eq(self, col, val):
        self._eq_filters[col] = val
        return self

    def single(self):
        self._is_single = True
        return self

    def update(self, fields):
        self._pending_update = fields
        return self

    def execute(self):
        if hasattr(self, "_pending_update"):
            key = (self.name, self._eq_filters.get("id"))
            self._updates.append((self.name, dict(self._eq_filters), dict(self._pending_update)))
            if key in self._update_errors:
                raise self._update_errors[key]
            return type("R", (), {"data": [self._pending_update]})()
        self._queries.append((self.name, dict(self._eq_filters), self._is_single))
        if self.name in self._query_errors:
            raise self._query_errors[self.name]
        resp = self._responses.get(self.name)
        if resp is None:
            return type("R", (), {"data": [], "count": 0})()
        rows, count = resp if isinstance(resp, tuple) else (resp, None)
        data = rows[0] if (rows and self._is_single) else rows
        return type("R", (), {"data": data, "count": count})()


class _Bucket:
    def __init__(self, uploads, error=None):
        self._uploads = uploads
        self._error = error

    def upload(self, path, data):
        if self._error is not None:
            raise self._error
        self._uploads.append((path, data))


class _Storage:
    def __init__(self, bucket):
        self._bucket = bucket

    def from_(self, name):
        assert name == cts.IMAGES_BUCKET
        return self._bucket


class _Client:
    """Hand-rolled fake Supabase client: table()/rpc()/storage, all call-recording."""

    def __init__(self, *, table_responses=None, rpc_result=None, rpc_results=None,
                 uploads=None, upload_error=None, update_errors=None, query_errors=None,
                 rpc_error=None):
        self.table_responses = table_responses or {}
        self.rpc_result = rpc_result
        self.rpc_results = list(rpc_results) if rpc_results is not None else None
        self.rpc_error = rpc_error
        self.rpc_calls = []
        self.updates = []
        self.queries = []
        self.uploads = uploads if uploads is not None else []
        self.storage = _Storage(_Bucket(self.uploads, error=upload_error))
        self._update_errors = update_errors or {}
        self._query_errors = query_errors or {}

    def table(self, name):
        return _Table(
            name, self.table_responses, self.updates, self.queries,
            self._update_errors, self._query_errors,
        )

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if self.rpc_results is not None:
            result = self.rpc_results[len(self.rpc_calls) - 1]
        else:
            result = self.rpc_result
        return _RPC(result, error=self.rpc_error)


def _default_table_responses(qr_suffixes=("001", "002", "009"), image_id=42, scan_id=777, frame_count=1):
    return {
        "cyl_experiments": ([EXPERIMENT_ROW], None),
        "cyl_plants_extended": ([{"qr_code": f"TEST-E2E-{s}"} for s in qr_suffixes], None),
        "cyl_images": ([{"scan_id": scan_id}], frame_count),
    }


# --- experiment guard --------------------------------------------------------


def test_experiment_guard_passes_on_matching_name():
    client = _Client(table_responses=_default_table_responses())
    cts.check_experiment_guard(client)  # must not raise
    assert ("cyl_experiments", {"id": cts.EXPERIMENT_ID}, False) in client.queries


def test_experiment_guard_rejects_missing_experiment():
    client = _Client(table_responses={"cyl_experiments": ([], None)})
    with pytest.raises(cts.CreateTestScanError):
        cts.check_experiment_guard(client)


def test_experiment_guard_rejects_mismatched_name():
    client = _Client(table_responses={"cyl_experiments": ([{"id": cts.EXPERIMENT_ID, "name": "Something Else"}], None)})
    with pytest.raises(cts.CreateTestScanError):
        cts.check_experiment_guard(client)


def test_experiment_guard_apierror_is_wrapped_cleanly():
    client = _Client(query_errors={"cyl_experiments": _api_error("permission denied", "42501")})
    with pytest.raises(cts.CreateTestScanError, match="permission denied"):
        cts.check_experiment_guard(client)


def test_resolve_next_qr_code_apierror_is_wrapped_cleanly():
    client = _Client(query_errors={"cyl_plants_extended": _api_error("boom")})
    with pytest.raises(cts.CreateTestScanError, match="boom"):
        cts.resolve_next_qr_code(client)


def test_call_insert_image_apierror_is_wrapped_cleanly():
    client = _Client(rpc_error=_api_error("permission denied for function insert_image_v2_0", "42501"))
    with pytest.raises(cts.CreateTestScanError, match="permission denied"):
        cts.call_insert_image(
            client, experiment_name=EXPERIMENT_ROW["name"], plant_qr_code="TEST-E2E-010", frame_number=1
        )


def test_resolve_scan_id_apierror_is_wrapped_cleanly():
    client = _Client(query_errors={"cyl_images": _api_error("permission denied", "42501")})
    with pytest.raises(cts.CreateTestScanError, match="permission denied"):
        cts.resolve_scan_id(client, 42)


def test_count_frames_for_scan_apierror_is_wrapped_cleanly():
    client = _Client(query_errors={"cyl_images": _api_error("permission denied", "42501")})
    with pytest.raises(cts.CreateTestScanError, match="permission denied"):
        cts.count_frames_for_scan(client, 777)


def test_cli_rpc_apierror_exits_cleanly_not_a_traceback(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",)),
        rpc_error=_api_error("permission denied for function insert_image_v2_0", "42501"),
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code != 0
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert "permission denied" in res.output
    assert "Traceback" not in res.output


def test_cli_guard_failure_makes_no_rpc_call(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses={"cyl_experiments": ([], None)})
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code != 0
    assert client.rpc_calls == []


# --- lock ---------------------------------------------------------------------


def test_lock_contention_exits_nonzero_with_no_rpc_call(monkeypatch):
    _patch_authed(monkeypatch)
    client = _Client(table_responses=_default_table_responses())
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)

    def _raise(*_a, **_kw):
        raise LockContendedError("locked by pid 123")

    monkeypatch.setattr(cts, "acquire_lock", _raise)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code != 0
    assert "locked" in res.output.lower() or "lock" in res.output.lower()
    assert client.rpc_calls == []


def test_lock_acquired_with_expected_path_and_staleness(monkeypatch):
    _patch_authed(monkeypatch)
    calls = []
    _patch_lock(monkeypatch, calls)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)), rpc_result=101)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert len(calls) == 1
    path, staleness = calls[0]
    assert str(path).replace("\\", "/").endswith(".bloom/.locks/cyl-create-test-scan-12880747.lock")
    from bloomctl.cyl._locks import DEFAULT_LOCK_STALENESS_SECONDS

    assert staleness == DEFAULT_LOCK_STALENESS_SECONDS


# --- QR-code auto-increment ---------------------------------------------------


def test_resolve_next_qr_code_increments_past_highest_suffix():
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("001", "002", "009")))
    assert cts.resolve_next_qr_code(client) == "TEST-E2E-010"
    assert ("cyl_plants_extended", {"experiment_id": cts.EXPERIMENT_ID}, False) in client.queries


def test_resolve_next_qr_code_with_no_existing_scans():
    client = _Client(table_responses={"cyl_plants_extended": ([], None)})
    assert cts.resolve_next_qr_code(client) == "TEST-E2E-001"


def test_resolve_scan_id_query_shape():
    client = _Client(table_responses={"cyl_images": ([{"scan_id": 777}], None)})
    assert cts.resolve_scan_id(client, 42) == 777
    assert ("cyl_images", {"id": 42}, True) in client.queries


def test_count_frames_for_scan_query_shape():
    client = _Client(table_responses={"cyl_images": ([{"scan_id": 777}], 3)})
    assert cts.count_frames_for_scan(client, 777) == 3
    assert ("cyl_images", {"scan_id": 777}, False) in client.queries


# --- sentinel identity + sourced wave/device values --------------------------


def test_poison_rpc_call_uses_expected_params(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)), rpc_result=55)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output

    assert len(client.rpc_calls) == 1
    name, params = client.rpc_calls[0]
    assert name == "insert_image_v2_0"
    assert params["plant_qr_code"] == "TEST-E2E-010"
    assert params["frame_number_"] == 1

    # Sentinel identity fields — re-typed here from design.md, not copied from create_test_scan.py.
    assert params["phenotyper_name"] == "Synthetic Test Phenotyper"
    assert params["phenotyper_email"] == "synthetic-test-phenotyper@bloom.invalid"
    assert params["scientist_name"] == "Synthetic Test Scientist"
    assert params["scientist_email"] == "synthetic-test-scientist@bloom.invalid"
    assert params["accession_name"] == "SYNTHETIC-TEST-ACCESSION"
    for value in (params["phenotyper_email"], params["scientist_email"]):
        assert value.endswith(".invalid")

    # device_name is NOT a sentinel — it must be a real, existing scanner name.
    assert params["device_name"] == "FastScanner"

    # Wave/plant-batch metadata sourced from an existing scan (task 1.1).
    assert params["species_common_name"] == "Canola"
    assert params["wave_number"] == 9999
    assert params["germ_day"] == 1
    assert params["germ_day_color"] == "TestGray"
    assert params["plant_age_days"] == 2
    assert params["date_scanned_"] == "2026-08-24"

    # Regression (found live during staging validation): the RPC upserts cyl_experiments on an
    # EXACT (species_id, name) match. Passing anything other than the experiment's real,
    # currently-live name (which carries a descriptive suffix beyond the bare prefix) silently
    # creates a brand-new experiment instead of attaching to 12880747.
    assert params["experiment"] == EXPERIMENT_ROW["name"]
    assert params["experiment"] != cts.EXPERIMENT_NAME_PREFIX


def test_experiment_name_sent_to_rpc_is_the_live_name_not_the_prefix_constant(monkeypatch):
    """Same regression as above, isolated: a differently-suffixed live name must be threaded
    through verbatim, proving the code reads it from the guard rather than hardcoding it."""
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    live_name = "A4-PIPELINE-E2E-TEST (a totally different suffix)"
    client = _Client(
        table_responses={
            "cyl_experiments": ([{"id": cts.EXPERIMENT_ID, "name": live_name}], None),
            "cyl_plants_extended": ([{"qr_code": "TEST-E2E-009"}], None),
            "cyl_images": ([{"scan_id": 777}], None),
        },
        rpc_result=999,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert client.rpc_calls[0][1]["experiment"] == live_name


# --- poison mode ---------------------------------------------------------------


# --- abandoned-scan warning sweep --------------------------------------------


def test_warns_about_scan_with_mixed_success_and_pending_frames(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses={
            "cyl_experiments": ([EXPERIMENT_ROW], None),
            "cyl_plants_extended": ([{"qr_code": "TEST-E2E-009"}], None),
            "cyl_scans_extended": ([{"scan_id": 555, "qr_code": "TEST-E2E-005"}], None),
        },
        rpc_result=100,
    )

    # "cyl_images" is queried two different ways in this flow: the sweep's per-scan status
    # check (no .single(), filtered by scan_id) and --poison's own resolve_scan_id call (with
    # .single(), filtered by id) — the simple name-keyed fake can't tell them apart, so this
    # test overrides execute() per-instance to route by whether .single() was called.
    real_table = client.table

    def _table(name):
        t = real_table(name)
        if name == "cyl_images":
            def _execute():
                if t._is_single:
                    return type("R", (), {"data": {"scan_id": 777}, "count": None})()
                return type("R", (), {"data": [{"status": "SUCCESS"}, {"status": "PENDING"}], "count": None})()

            t.execute = _execute
        return t

    monkeypatch.setattr(client, "table", _table)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert "TEST-E2E-005" in res.stderr
    assert "mix of SUCCESS and PENDING" in res.stderr


def test_no_warning_when_no_scan_has_mixed_status(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses={
            **_default_table_responses(qr_suffixes=("009",)),
            "cyl_scans_extended": ([{"scan_id": 1, "qr_code": "TEST-E2E-001"}], None),
        },
        rpc_result=100,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert "mix of SUCCESS and PENDING" not in res.stderr


def test_abandoned_scan_sweep_failure_does_not_block_scan_creation(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",)),
        rpc_result=100,
        query_errors={"cyl_scans_extended": _api_error("boom")},
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert "abandoned-scan sweep failed" in res.stderr


def test_abandoned_scan_sweep_survives_a_non_apierror_failure(monkeypatch):
    """Regression: the sweep's except clause used to only catch CreateTestScanError (from
    _run_query's APIError wrapping), so a malformed response shape from a real client — not
    modeled by _api_error — would have propagated uncaught, before the lock is even acquired,
    contradicting spec.md's "SHALL... without aborting scan creation"."""
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses={
            **_default_table_responses(qr_suffixes=("009",)),
            "cyl_scans_extended": (["not-a-dict"], None),
        },
        rpc_result=100,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert "abandoned-scan sweep failed" in res.stderr


def test_abandoned_scan_sweep_runs_inside_the_lock_not_before(monkeypatch):
    """Regression: running the sweep before acquire_lock let one invocation's sweep observe a
    second, concurrently-running invocation's own healthy in-progress scan and flag it as
    abandoned. It must run only once the lock is actually held."""
    _patch_authed(monkeypatch)
    order = []

    class _NullLock:
        def __enter__(self):
            order.append("lock_acquired")
            return None

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(cts, "acquire_lock", lambda path, *, staleness_seconds: _NullLock())

    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)), rpc_result=100)
    real_table = client.table

    def _table(name):
        if name == "cyl_scans_extended":
            order.append("sweep_query")
        return real_table(name)

    monkeypatch.setattr(client, "table", _table)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert order == ["lock_acquired", "sweep_query"]


def test_poison_mode_makes_no_storage_calls(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)), rpc_result=55)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert client.uploads == []
    assert client.updates == []


def test_poison_null_rpc_result_aborts_before_storage(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)), rpc_result=None)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code != 0
    assert "TEST-E2E-010" in res.output
    assert "1" in res.output  # frame number named
    assert client.uploads == []
    assert client.updates == []


# --- good mode -----------------------------------------------------------------


def _write_frame(tmp_path, name="frame1.png", size=2048):
    path = tmp_path / name
    path.write_bytes(b"\x00" * size)
    return path


def test_discover_frame_files_sorts_numerically_not_lexicographically(tmp_path):
    """PR review (Benfica): plain sorted() would put '10.png' before '2.png'. discover_frame_files
    must match the numeric ordering the rest of this CLI uses (download_for_predict.py names
    staged frames exactly f"{frame_number}{ext}", unpadded)."""
    for name in ("1.png", "2.png", "10.png", "3.png", "9.png"):
        _write_frame(tmp_path, name)
    files = cts.discover_frame_files(tmp_path)
    assert [p.name for p in files] == ["1.png", "2.png", "3.png", "9.png", "10.png"]


def test_good_mode_single_frame_full_flow(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), image_id=42, scan_id=777, frame_count=1),
        rpc_result=42,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output

    assert len(client.uploads) == 1
    object_path, data = client.uploads[0]
    assert object_path.startswith("cyl-images/cyl-image_42_")
    assert object_path.endswith(".png")

    assert len(client.updates) == 1
    table_name, eq_filters, fields = client.updates[0]
    assert table_name == "cyl_images"
    assert eq_filters == {"id": 42}
    assert fields["object_path"] == object_path
    assert fields["status"] == "SUCCESS"


def test_good_mode_json_output_includes_scan_id(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), image_id=42, scan_id=777, frame_count=1),
        rpc_result=42,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path), "--json"]
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["scan_id"] == 777


def test_good_mode_multiple_frames_sequential_numbers(monkeypatch, tmp_path):
    # NOTE: the fake table() override below assumes exactly 3 client.table("cyl_images") calls
    # per frame (resolve_scan_id, count_frames_for_scan, update_image_row's own call — the
    # update ignores the injected response but still consumes one slot from the iterator,
    # since the override intercepts every "cyl_images" table() call uniformly). If a future
    # change to create_test_scan_core adds or removes a "cyl_images" access per frame, this
    # test will fail with an unhelpful StopIteration/leftover-items error rather than a clear
    # message — update the `* 3` below to match the new per-frame call count.
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path, "a.png")
    _write_frame(tmp_path, "b.png")
    _write_frame(tmp_path, "c.png")
    client = _Client(
        table_responses={
            "cyl_experiments": ([EXPERIMENT_ROW], None),
            "cyl_plants_extended": ([{"qr_code": "TEST-E2E-009"}], None),
        },
        rpc_results=[1, 2, 3],
    )

    # frame-count check must track each frame's own scan/count pair. Three separate
    # client.table("cyl_images") calls happen per frame (resolve_scan_id, count_frames_for_scan,
    # then the update in update_image_row — which ignores this response but still consumes one
    # from the iterator since the fake intercepts every table("cyl_images") call uniformly), so
    # each pair below is consumed three times.
    responses_by_call = iter(
        pair
        for count in (1, 2, 3)
        for pair in (({"scan_id": 777}, count),) * 3
    )

    real_table = client.table

    def _table(name):
        t = real_table(name)
        if name == "cyl_images":
            row, count = next(responses_by_call)
            t._responses = {**t._responses, "cyl_images": ([row], count)}
        return t

    monkeypatch.setattr(client, "table", _table)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    assert [p for p, _d in client.uploads].__len__() == 3
    assert [call[1]["frame_number_"] for call in client.rpc_calls] == [1, 2, 3]
    assert len(client.updates) == 3


def test_good_mode_null_on_second_frame_stops_processing(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path, "a.png")
    _write_frame(tmp_path, "b.png")
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), scan_id=777, frame_count=1),
        rpc_results=[1, None],
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert "2" in res.output  # names the failing frame
    assert len(client.rpc_calls) == 2  # frame 1 succeeded, frame 2's RPC ran and returned NULL
    assert len(client.uploads) == 1  # only frame 1 uploaded
    assert len(client.updates) == 1  # only frame 1 updated


def test_good_mode_update_failure_after_successful_upload(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), image_id=42, scan_id=777, frame_count=1),
        rpc_result=42,
        update_errors={("cyl_images", 42): RuntimeError("boom")},
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert len(client.uploads) == 1
    assert "42" in res.output


def test_good_mode_frame_count_mismatch_aborts_before_upload(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), image_id=42, scan_id=777, frame_count=2),
        rpc_result=42,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert client.uploads == []


def test_good_mode_frame_below_size_floor_rejected(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    _write_frame(tmp_path, size=100)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)))
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert "1024" in res.output or "KiB" in res.output
    assert client.rpc_calls == []


def test_good_mode_missing_frames_dir(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)))
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path / "nope")]
    )
    assert res.exit_code != 0
    assert client.rpc_calls == []


def test_good_mode_empty_frames_dir(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)))
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert client.rpc_calls == []


def test_good_mode_non_image_files_ignored(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    (tmp_path / "readme.txt").write_text("not a frame")
    _write_frame(tmp_path, "frame.png")
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), image_id=42, scan_id=777, frame_count=1),
        rpc_result=42,
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code == 0, res.output
    assert len(client.rpc_calls) == 1
    assert len(client.uploads) == 1


# --- mutual exclusivity -------------------------------------------------------


def test_both_flags_rejected(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    client = _Client(table_responses=_default_table_responses())
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--poison", "--good", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert client.rpc_calls == []


def test_neither_flag_rejected(monkeypatch):
    _patch_authed(monkeypatch)
    client = _Client(table_responses=_default_table_responses())
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan"])
    assert res.exit_code != 0
    assert client.rpc_calls == []


def test_poison_with_frames_dir_rejected(monkeypatch, tmp_path):
    _patch_authed(monkeypatch)
    client = _Client(table_responses=_default_table_responses())
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(
        cli, ["cyl", "create-test-scan", "--poison", "--frames-dir", str(tmp_path)]
    )
    assert res.exit_code != 0
    assert client.rpc_calls == []


# --- upload helper retry behavior --------------------------------------------


def test_upload_object_retries_once_on_transient_error(monkeypatch):
    import bloomctl._storage as storage

    attempts = {"n": 0}

    class _RetryBucket:
        def upload(self, path, data):
            attempts["n"] += 1
            if attempts["n"] == 1:
                err = RuntimeError("rate limited")
                err.status = 429
                raise err

    class _Storage2:
        def from_(self, name):
            return _RetryBucket()

    class _C:
        storage = _Storage2()

    monkeypatch.setattr(storage, "time", type("T", (), {"sleep": staticmethod(lambda *_: None)}))
    storage.upload_object(_C(), b"data", "some/path.png", bucket="images")
    assert attempts["n"] == 2


def test_upload_object_does_not_retry_non_transient_error():
    import bloomctl._storage as storage

    class _FailBucket:
        def upload(self, path, data):
            err = RuntimeError("forbidden")
            err.status = 403
            raise err

    class _Storage2:
        def from_(self, name):
            return _FailBucket()

    class _C:
        storage = _Storage2()

    with pytest.raises(storage.StorageError):
        storage.upload_object(_C(), b"data", "some/path.png", bucket="images")


# --- profile / registration ---------------------------------------------------


def test_default_profile_forwarded_to_authed_client(monkeypatch):
    seen = {}
    monkeypatch.setattr(climod, "_authed_client", lambda profile: seen.setdefault("profile", profile) or _Client(table_responses={"cyl_experiments": ([], None)}))
    CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    from bloomctl.credentials import DEFAULT_PROFILE

    assert seen["profile"] == DEFAULT_PROFILE


def test_cli_registration_in_help():
    res = CliRunner().invoke(cli, ["cyl", "--help"])
    assert "create-test-scan" in res.output


# --- output conventions -------------------------------------------------------


def test_json_output_clean_on_stdout(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(table_responses=_default_table_responses(qr_suffixes=("009",)), rpc_result=55)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["plant_qr_code"] == "TEST-E2E-010"
    assert "cyl_images_ids" in payload


def test_json_output_includes_scan_id(monkeypatch):
    """Regression: the tool's own cyl_images_ids field was mistaken for a scan_id by a
    downstream reviewer, since download-for-predict actually needs a real scan_id. Surface it
    explicitly, in both --poison and --good, matching ingest.py's scan_id= convention."""
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), scan_id=888), rpc_result=55
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["scan_id"] == 888


def test_human_summary_without_json(monkeypatch):
    _patch_authed(monkeypatch)
    _patch_lock(monkeypatch)
    client = _Client(
        table_responses=_default_table_responses(qr_suffixes=("009",), scan_id=888), rpc_result=55
    )
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    res = CliRunner().invoke(cli, ["cyl", "create-test-scan", "--poison"])
    assert res.exit_code == 0, res.output
    assert "TEST-E2E-010" in res.stdout
    assert "scan_id=888" in res.stdout
