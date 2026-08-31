"""Unit tests for backup_lib.py and rclone_rc.py.

The manifest fixtures are shaped like real `storage.objects` rows from the
Bloom deploy — cylinder scan frames under `images/`, rendered plate videos
under `videos/`, and the version UUIDs storage-api mints on write. The
mapping assertions lock in the one property the whole job exists for: the
Box path keeps the extension, the MinIO key keeps the version suffix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import backup_lib as lib  # noqa: E402
from backup_lib import (  # noqa: E402
    BACKING_BUCKET, SQLITE_MAX_VARIABLES, BackupError, StorageObject,
    batches, box_path, build_plan, chunked, format_bytes, iter_manifest,
    objects_query, parse_manifest, unsafe_reason,
)
from ledger import Ledger, utcnow  # noqa: E402
from rclone_rc import MinioSource, RcloneError, _is_retryable, redact  # noqa: E402

VERSION = "0f8b1c2a-4d5e-4f60-9a1b-2c3d4e5f6a7b"
OTHER_VERSION = "11112222-3333-4444-5555-666677778888"


def obj(
    bucket_id: str = "images",
    name: str = "exp-42/plate-7/frame_0001.png",
    version: str | None = VERSION,
    size: int | None = 2048,
    updated_at: str = "2026-08-01T12:00:00+00",
) -> StorageObject:
    return StorageObject(bucket_id, name, version, size, updated_at)


# ---------- the core mapping ----------

def test_minio_key_is_relative_to_the_backing_bucket():
    # NOT a whole MinIO address — the bucket comes from MinioSource.fs() and
    # the tenant prefix from source_remote(). Handing this to rclone against a
    # provider-root fs makes it read "images" as the bucket name, which is a
    # real bucket that does not hold these objects.
    assert obj().minio_key == f"images/exp-42/plate-7/frame_0001.png/{VERSION}"


def test_storage_path_drops_the_version_suffix():
    assert obj().storage_path == "images/exp-42/plate-7/frame_0001.png"


def test_storage_path_keeps_the_file_extension():
    assert obj().storage_path.endswith(".png")


def test_minio_key_without_version_has_no_suffix():
    assert obj(version=None).minio_key == "images/exp-42/plate-7/frame_0001.png"


def test_minio_key_and_storage_path_differ_only_by_version():
    o = obj()
    assert o.minio_key == f"{o.storage_path}/{o.version}"


def test_backing_bucket_matches_compose_value():
    assert BACKING_BUCKET == "bloom-storage"


def test_ledger_key_is_bucket_and_name():
    assert obj().ledger_key == ("images", "exp-42/plate-7/frame_0001.png")


# ---------- box_path ----------

def test_box_path_without_root_is_the_storage_path():
    assert box_path(obj()) == "images/exp-42/plate-7/frame_0001.png"


def test_box_path_prefixes_the_root():
    assert box_path(obj(), "Bloom-Backups/prod").startswith("Bloom-Backups/prod/images/")


def test_box_path_tolerates_slashes_around_the_root():
    assert box_path(obj(), "/Bloom-Backups/prod/") == box_path(obj(), "Bloom-Backups/prod")


def test_box_path_normalizes_unicode_to_nfc():
    decomposed = obj(name="échantillon/scan.png")
    assert box_path(decomposed) == "images/échantillon/scan.png"


def test_box_path_preserves_nested_directories():
    o = obj(bucket_id="videos", name="a/b/c/d.mp4")
    assert box_path(o, "root") == "root/videos/a/b/c/d.mp4"


# ---------- manifest parsing ----------

def test_parse_manifest_reads_a_single_row():
    raw = f"images\texp-42/frame.png\t{VERSION}\t2048\t2026-08-01T12:00:00+00"
    [parsed] = parse_manifest(raw)
    assert parsed == obj(name="exp-42/frame.png")


def test_parse_manifest_reads_multiple_rows():
    raw = (
        f"images\ta.png\t{VERSION}\t10\t2026-08-01T12:00:00+00\n"
        f"videos\tb.mp4\t{OTHER_VERSION}\t20\t2026-08-02T12:00:00+00\n"
    )
    assert [o.bucket_id for o in parse_manifest(raw)] == ["images", "videos"]


def test_parse_manifest_ignores_blank_lines():
    raw = f"\nimages\ta.png\t{VERSION}\t10\t2026-08-01T12:00:00+00\n\n"
    assert len(parse_manifest(raw)) == 1


def test_parse_manifest_treats_missing_size_as_none():
    raw = f"images\ta.png\t{VERSION}\t-1\t2026-08-01T12:00:00+00"
    assert parse_manifest(raw)[0].size is None


def test_parse_manifest_treats_empty_version_as_none():
    raw = "images\ta.png\t\t10\t2026-08-01T12:00:00+00"
    assert parse_manifest(raw)[0].version is None


def test_parse_manifest_rejects_a_short_row():
    with pytest.raises(BackupError, match="expected 5 fields"):
        parse_manifest("images\ta.png\t10")


def test_parse_manifest_rejects_an_extra_field():
    raw = f"images\ta.png\t{VERSION}\t10\t2026-08-01T12:00:00+00\textra"
    with pytest.raises(BackupError, match="expected 5 fields"):
        parse_manifest(raw)


def test_parse_manifest_keeps_spaces_in_names():
    raw = f"images\tmy scan/frame 1.png\t{VERSION}\t10\t2026-08-01T12:00:00+00"
    assert parse_manifest(raw)[0].name == "my scan/frame 1.png"


def test_parse_manifest_of_empty_input_is_empty():
    assert parse_manifest("") == []


def test_iter_manifest_streams_without_materializing():
    lines = iter([f"images\ta.png\t{VERSION}\t10\t2026-08-01T12:00:00+00"])
    assert next(iter_manifest(lines)).name == "a.png"


def test_iter_manifest_strips_the_trailing_newline():
    lines = [f"images\ta.png\t{VERSION}\t10\t2026-08-01T12:00:00+00\n"]
    assert list(iter_manifest(lines))[0].updated_at == "2026-08-01T12:00:00+00"


def test_iter_manifest_reads_a_file_handle(tmp_path):
    path = tmp_path / "manifest.tsv"
    path.write_text(
        f"images\ta.png\t{VERSION}\t10\t2026-08-01T12:00:00+00\n"
        f"videos\tb.mp4\t{OTHER_VERSION}\t20\t2026-08-02T12:00:00+00\n"
    )
    with path.open() as handle:
        assert len(list(iter_manifest(handle))) == 2


# ---------- batching ----------

def test_batches_splits_a_stream():
    objects = [obj(name=f"a/{i}.png") for i in range(5)]
    assert [len(b) for b in batches(iter(objects), 2)] == [2, 2, 1]


def test_batches_of_an_empty_stream_yields_nothing():
    assert list(batches(iter([]), 10)) == []


def test_batches_larger_than_the_stream_yield_one_batch():
    objects = [obj(name=f"a/{i}.png") for i in range(3)]
    assert [len(b) for b in batches(iter(objects), 100)] == [3]


# ---------- SQL construction ----------

def test_objects_query_excludes_tus_files_by_default():
    assert "'tus-files'" in objects_query()


def test_objects_query_filters_to_requested_buckets():
    sql = objects_query(buckets=["images", "videos"])
    assert "o.bucket_id IN ('images', 'videos')" in sql


def test_objects_query_adds_the_since_watermark():
    sql = objects_query(since="2026-08-01T00:00:00+00")
    assert "o.updated_at > '2026-08-01T00:00:00+00'::timestamptz" in sql


def test_objects_query_omits_since_when_absent():
    assert "updated_at >" not in objects_query()


def test_objects_query_escapes_quotes_in_bucket_names():
    sql = objects_query(buckets=["it's"])
    assert "'it''s'" in sql


def test_objects_query_rejects_a_nul_byte():
    with pytest.raises(BackupError, match="NUL byte"):
        objects_query(buckets=["bad\x00name"])


def test_objects_query_selects_the_version_column():
    assert "o.version" in objects_query()


def test_objects_query_reads_size_from_metadata():
    assert "o.metadata->>'size'" in objects_query()


# ---------- path safety ----------

def test_safe_object_has_no_reason():
    assert unsafe_reason(obj()) is None


def test_absolute_name_is_unsafe():
    assert "absolute" in unsafe_reason(obj(name="/etc/passwd"))


def test_empty_name_is_unsafe():
    assert unsafe_reason(obj(name="")) is not None


def test_parent_directory_segment_is_unsafe():
    assert "parent-directory" in unsafe_reason(obj(name="a/../../etc/passwd"))


def test_control_character_is_unsafe():
    assert "control character" in unsafe_reason(obj(name="a/b\nc.png"))


@pytest.mark.parametrize("char", ["\\", ":", "*", "?", '"', "<", ">", "|"])
def test_box_illegal_characters_are_unsafe(char):
    assert "Box-illegal" in unsafe_reason(obj(name=f"a/frame{char}1.png"))


def test_forward_slash_is_a_separator_not_an_illegal_character():
    assert unsafe_reason(obj(name="a/b/c.png")) is None


def test_segment_ending_in_a_period_is_unsafe():
    assert "space or period" in unsafe_reason(obj(name="folder./frame.png"))


def test_segment_ending_in_a_space_is_unsafe():
    assert "space or period" in unsafe_reason(obj(name="folder /frame.png"))


def test_filename_extension_period_is_not_a_trailing_period():
    assert unsafe_reason(obj(name="folder/frame.png")) is None


def test_object_over_the_box_file_cap_is_unsafe():
    assert "per-file limit" in unsafe_reason(obj(size=60 * 1024**3))


def test_unknown_size_is_not_treated_as_oversized():
    assert unsafe_reason(obj(size=None)) is None


# ---------- planning ----------

def test_plan_copies_an_object_absent_from_the_ledger():
    plan = build_plan([obj()], {})
    assert len(plan.copies) == 1 and plan.already_current == 0


def test_plan_skips_an_object_whose_version_is_already_copied():
    plan = build_plan([obj()], {obj().ledger_key: VERSION})
    assert plan.copies == () and plan.already_current == 1


def test_plan_recopies_an_object_whose_version_changed():
    plan = build_plan([obj(version=OTHER_VERSION)], {obj().ledger_key: VERSION})
    assert len(plan.copies) == 1


def test_plan_separates_unsafe_objects_from_copies():
    plan = build_plan([obj(), obj(name="bad:name.png")], {})
    assert len(plan.copies) == 1 and len(plan.skipped) == 1


def test_plan_records_why_an_object_was_skipped():
    plan = build_plan([obj(name="bad:name.png")], {})
    assert "Box-illegal" in plan.skipped[0].reason


def test_plan_honours_the_limit():
    objects = [obj(name=f"a/{i}.png") for i in range(10)]
    assert len(build_plan(objects, {}, limit=3).copies) == 3


def test_plan_totals_the_bytes_to_copy():
    objects = [obj(name="a.png", size=100), obj(name="b.png", size=200)]
    assert build_plan(objects, {}).total_bytes == 300


def test_plan_tolerates_objects_of_unknown_size():
    assert build_plan([obj(size=None)], {}).total_bytes == 0


def test_plan_of_nothing_is_empty():
    plan = build_plan([], {})
    assert plan.copies == () and plan.skipped == () and plan.already_current == 0


# ---------- ledger ----------

@pytest.fixture
def ledger(tmp_path):
    # Through the production constructor — see the note in
    # backup_objects_test.py. Using a real file also exercises the WAL pragma
    # open() sets, which an in-memory connection never reaches.
    led = Ledger.open(str(tmp_path / "ledger.db"))
    yield led
    led.close()


def test_new_ledger_has_no_copied_objects(ledger):
    assert ledger.copied_versions() == {}


def test_mark_copied_records_the_version(ledger):
    ledger.mark_copied(obj())
    assert ledger.copied_versions()[obj().ledger_key] == VERSION


def test_mark_copied_twice_updates_rather_than_duplicates(ledger):
    ledger.mark_copied(obj())
    ledger.mark_copied(obj(version=OTHER_VERSION))
    assert ledger.copied_versions() == {obj().ledger_key: OTHER_VERSION}


def test_same_name_in_two_buckets_is_two_entries(ledger):
    ledger.mark_copied(obj(bucket_id="images", name="a.png"))
    ledger.mark_copied(obj(bucket_id="videos", name="a.png"))
    assert len(ledger.copied_versions()) == 2


def test_ledger_round_trips_a_null_version(ledger):
    ledger.mark_copied(obj(version=None))
    assert ledger.copied_versions()[obj().ledger_key] is None


def test_no_successful_run_yet(ledger):
    assert ledger.last_successful_run() is None


def test_last_successful_run_returns_the_start_time(ledger):
    run_id = ledger.start_run(now="2026-08-01T00:00:00+00")
    ledger.finish_run(run_id, "ok", {"copied": 1})
    assert ledger.last_successful_run() == "2026-08-01T00:00:00+00"


def test_partial_run_does_not_advance_the_watermark(ledger):
    run_id = ledger.start_run(now="2026-08-01T00:00:00+00")
    ledger.finish_run(run_id, "partial", {"failed": 3})
    assert ledger.last_successful_run() is None


def test_latest_successful_run_wins(ledger):
    first = ledger.start_run(now="2026-08-01T00:00:00+00")
    ledger.finish_run(first, "ok", {})
    second = ledger.start_run(now="2026-08-08T00:00:00+00")
    ledger.finish_run(second, "ok", {})
    assert ledger.last_successful_run() == "2026-08-08T00:00:00+00"


def test_versions_for_returns_only_the_requested_objects(ledger):
    ledger.mark_copied(obj(name="a.png"))
    ledger.mark_copied(obj(name="b.png"))
    found = ledger.versions_for([("images", "a.png")])
    assert found == {("images", "a.png"): VERSION}


def test_versions_for_omits_objects_it_has_never_seen(ledger):
    assert ledger.versions_for([("images", "never.png")]) == {}


def test_versions_for_distinguishes_buckets(ledger):
    ledger.mark_copied(obj(bucket_id="videos", name="a.png"))
    assert ledger.versions_for([("images", "a.png")]) == {}


def test_versions_for_handles_more_keys_than_sqlite_allows_parameters(ledger):
    names = [f"a/{i}.png" for i in range(SQLITE_MAX_VARIABLES)]
    for name in names:
        ledger.mark_copied(obj(name=name))
    found = ledger.versions_for([("images", name) for name in names])
    assert len(found) == len(names)


def test_versions_for_of_no_keys_is_empty(ledger):
    assert ledger.versions_for([]) == {}


def test_ledger_survives_reopening(tmp_path):
    path = str(tmp_path / "ledger.db")
    first = Ledger.open(path)
    first.mark_copied(obj())
    first.commit()
    first.close()
    second = Ledger.open(path)
    assert second.copied_versions()[obj().ledger_key] == VERSION
    second.close()


def test_ledger_migration_is_idempotent(tmp_path):
    path = str(tmp_path / "ledger.db")
    Ledger.open(path).close()
    Ledger.open(path).close()  # would raise if CREATE TABLE were unguarded


def test_utcnow_is_an_iso_timestamp_with_offset():
    assert utcnow().endswith("+00") and "T" in utcnow()


# ---------- resume semantics ----------

def test_a_resumed_run_only_copies_what_the_ledger_lacks(ledger):
    objects = [obj(name=f"a/{i}.png") for i in range(5)]
    for done in objects[:3]:
        ledger.mark_copied(done)
    plan = build_plan(objects, ledger.copied_versions())
    assert [o.name for o in plan.copies] == ["a/3.png", "a/4.png"]


# ---------- MinIO connection string ----------

def test_minio_fs_declares_the_minio_provider():
    assert "provider=Minio" in MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()


def test_minio_fs_carries_the_endpoint_quoted():
    # QUOTED, not bare. rclone ends an fs at the first unquoted colon, and the
    # endpoint carries two — bare, rclone read the endpoint as `http`, dropped
    # every parameter after it, and no object could be copied. The previous
    # version of this test asserted the bare form, so it held the bug in place.
    fs = MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()
    assert 'endpoint="http://m:9000"' in fs
    assert "endpoint=http://m:9000," not in fs


def test_minio_fs_keeps_every_parameter_after_the_endpoint():
    # The real damage was positional: everything following the unquoted colon
    # was read as path, so the credentials never applied at all.
    fs = MinioSource("http://m:9000", "key", "sec", "bloom-storage").fs()
    tail = fs.split('endpoint="http://m:9000"', 1)[1]
    for param in ("access_key_id=", "secret_access_key=", "region=", "force_path_style="):
        assert param in tail, f"{param} lost after the endpoint"


def test_minio_fs_ends_at_the_bucket_not_inside_the_endpoint():
    fs = MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()
    assert fs.rsplit(":", 1)[1] == "bloom-storage"


def test_minio_fs_forces_path_style():
    assert "force_path_style=true" in MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()


def test_minio_fs_is_a_connection_string_not_a_named_remote():
    fs = MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()
    assert fs.startswith(":s3,")


def test_minio_fs_names_the_bucket_rather_than_stopping_at_the_root():
    # The old form ended at ':' — a provider-root fs — which made rclone read
    # the first segment of every remote as a bucket name.
    fs = MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()
    assert fs.endswith(":bloom-storage")
    assert not fs.endswith(",:")


def test_minio_fs_quotes_a_secret_containing_a_comma():
    assert '"a,b"' in MinioSource("http://m:9000", "k", "a,b", "bloom-storage").fs()


def test_minio_fs_doubles_an_embedded_quote():
    assert '""' in MinioSource("http://m:9000", "k", 'a"b', "bloom-storage").fs()


# ---------- retry classification ----------

def test_box_throttling_is_retryable():
    assert _is_retryable(429, "rate limit")


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_errors_are_retryable(status):
    assert _is_retryable(status, "")


def test_a_missing_source_object_is_not_retryable():
    assert not _is_retryable(404, "object not found")


def test_a_bad_request_is_not_retryable():
    assert not _is_retryable(400, "malformed path")


def test_a_reset_connection_is_retryable_whatever_the_status():
    assert _is_retryable(400, "read: connection reset by peer")


def test_rclone_error_defaults_to_not_retryable():
    assert not RcloneError("boom").retryable


# ---------- credential redaction ----------

def test_redact_hides_the_minio_secret():
    fs = MinioSource("http://m:9000", "rootuser", "s3cr3t-value", "bloom-storage").fs()
    assert "s3cr3t-value" not in redact(f"copy failed on {fs}")


def test_redact_hides_the_access_key():
    fs = MinioSource("http://m:9000", "rootuser", "s3cr3t", "bloom-storage").fs()
    assert "rootuser" not in redact(fs)


def test_redact_hides_the_daemon_password():
    assert "hunter2" not in redact("flag --rc-pass=hunter2 rejected")


def test_redact_leaves_the_endpoint_readable():
    fs = MinioSource("http://supabase-minio:9000", "k", "s", "bloom-storage").fs()
    assert 'endpoint="http://supabase-minio:9000"' in redact(fs)


def test_redact_hides_a_secret_that_needed_quoting():
    # The characters that force quoting are exactly the ones the old pattern
    # excluded, so a quoted secret passed through in full. Once the endpoint is
    # quoted too, a redactor that cannot read quotes redacts nothing at all.
    for secret in ('pa,ss', 'pa"ss', 'pa:ss', 'pa,s"s:x'):
        out = redact(MinioSource("http://m:9000", "user", secret, "bloom-storage").fs())
        assert secret not in out, f"leaked {secret!r}"
        assert "user" not in out.replace("bloom-storage", "")


def test_redact_leaves_an_ordinary_message_alone():
    assert redact("object not found") == "object not found"


def test_redact_marks_where_it_scrubbed():
    assert "secret_access_key=***" in redact("secret_access_key=abc123")


# ---------- small helpers ----------

def test_chunked_splits_evenly():
    assert list(chunked([1, 2, 3, 4], 2)) == [[1, 2], [3, 4]]


def test_chunked_keeps_a_short_final_chunk():
    assert list(chunked([1, 2, 3], 2)) == [[1, 2], [3]]


def test_chunked_of_empty_yields_nothing():
    assert list(chunked([], 5)) == []


def test_format_bytes_reports_plain_bytes():
    assert format_bytes(512) == "512 B"


def test_format_bytes_scales_to_gibibytes():
    assert format_bytes(3 * 1024**3) == "3.0 GiB"


def test_format_bytes_caps_at_tebibytes():
    assert format_bytes(7 * 1024**4).endswith("TiB")


class TestSourceAddress:
    """The full MinIO address, which is what actually has to be right.

    storage-api files bytes at
    <backing bucket>/<tenant prefix>/<bucket_id>/<name>/<version>. Getting any
    component wrong 404s every object while looking like a working run, so
    each part is pinned here rather than assumed.
    """

    def test_prefix_precedes_the_object_key(self):
        assert lib.source_remote(obj(), "storage-single-tenant") == (
            f"storage-single-tenant/images/exp-42/plate-7/frame_0001.png/{VERSION}"
        )

    def test_no_prefix_leaves_the_key_untouched(self):
        assert lib.source_remote(obj(), "") == obj().minio_key

    def test_surrounding_slashes_do_not_double_up(self):
        assert lib.source_remote(obj(), "/tenant/") == f"tenant/{obj().minio_key}"

    def test_the_bucket_lives_in_the_fs_not_the_remote(self):
        fs = MinioSource("http://m:9000", "k", "s", "bloom-storage").fs()
        assert fs.endswith(":bloom-storage")
        assert "bloom-storage" not in lib.source_remote(obj(), "storage-single-tenant")

    def test_an_empty_bucket_is_refused_outright(self):
        # An empty bucket silently rebuilds the original defect, so it must
        # fail at construction rather than at object number one.
        with pytest.raises(ValueError, match="BACKUP_MINIO_BUCKET"):
            MinioSource("http://m:9000", "k", "s", "")

    def test_the_composed_address_matches_the_deployed_layout(self):
        # The path on the prod stack:
        #   /data/bloom-storage/storage-single-tenant/<bucket_id>/<name>/<ver>
        # Also what services/video-worker/video_listener.py reads from.
        source = MinioSource(
            "http://supabase-minio:9000", "k", "s", "bloom-storage",
            prefix="storage-single-tenant",
        )
        assert source.fs().endswith(":bloom-storage")
        assert source.root() == "bloom-storage/storage-single-tenant"
        assert lib.source_remote(obj(), source.prefix) == (
            f"storage-single-tenant/images/exp-42/plate-7/frame_0001.png/{VERSION}"
        )


class TestTheClientRedactsWhereTheErrorIsBuilt:
    """`RcloneRC.call` is where a credential would escape, and it had no tests.

    rclone echoes the failing remote back in its errors, and ours is a
    connection string carrying MinIO's root credentials. The only test that
    claimed to cover this asserted `"MINIO" not in caplog.text` — a string that
    appears nowhere in the code path — and it passed with redaction disabled.
    """

    SECRET = 'pa,ss"w:rd'

    def client(self, monkeypatch, raises):
        import urllib.request

        from rclone_rc import RcloneRC

        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(raises)
        )
        return RcloneRC("http://127.0.0.1:5572", "u", "p")

    def http_error(self, body: str):
        import io
        import urllib.error

        return urllib.error.HTTPError(
            "http://x", 500, "err", {}, io.BytesIO(body.encode())
        )

    def test_an_http_error_body_carrying_the_secret_is_redacted(self, monkeypatch):
        from rclone_rc import RcloneError

        fs = MinioSource("http://m:9000", "rootuser", self.SECRET, "bloom-storage").fs()
        client = self.client(monkeypatch, self.http_error(f'{{"error": "cannot read {fs}"}}'))
        with pytest.raises(RcloneError) as caught:
            client.copy_file("src", "a", "dst", "b")
        assert self.SECRET not in str(caught.value)
        assert "rootuser" not in str(caught.value)

    def test_a_transport_error_carrying_the_secret_is_redacted(self, monkeypatch):
        import urllib.error

        from rclone_rc import RcloneError

        fs = MinioSource("http://m:9000", "rootuser", self.SECRET, "bloom-storage").fs()
        client = self.client(monkeypatch, urllib.error.URLError(f"refused for {fs}"))
        with pytest.raises(RcloneError) as caught:
            client.copy_file("src", "a", "dst", "b")
        assert self.SECRET not in str(caught.value)

    def test_a_throttle_is_marked_retryable(self, monkeypatch):
        from rclone_rc import RcloneError

        client = self.client(monkeypatch, self.http_error('{"error": "rate_limit"}'))
        with pytest.raises(RcloneError) as caught:
            client.stat("dst", "b")
        assert caught.value.retryable is True

    def test_a_missing_object_is_not_retryable(self, monkeypatch):
        import io
        import urllib.error

        from rclone_rc import RcloneError

        err = urllib.error.HTTPError(
            "http://x", 404, "not found", {}, io.BytesIO(b'{"error": "object not found"}')
        )
        client = self.client(monkeypatch, err)
        with pytest.raises(RcloneError) as caught:
            client.stat("dst", "b")
        assert caught.value.retryable is False


class TestLedgerKeyMatchesTheDestination:
    """The record and the file on Box must be keyed the same way.

    `box_path` normalizes and `ledger_key` did not, so two rows that are one
    file on Box got two ledger entries — both claiming a backup, while the
    second copy had overwritten the first and nothing said so.
    """

    # "café.png" twice: the accent as one character, then as e + combining mark.
    COMPOSED = "café.png"
    DECOMPOSED = "café.png"

    def test_the_two_spellings_really_are_different_text(self):
        # If this ever stops being true the rest of the class proves nothing.
        assert self.COMPOSED != self.DECOMPOSED
        assert len(self.COMPOSED) != len(self.DECOMPOSED)

    def test_they_land_on_the_same_box_path(self):
        a = obj(name=self.COMPOSED)
        b = obj(name=self.DECOMPOSED)
        assert lib.box_path(a, "root") == lib.box_path(b, "root")

    def test_and_therefore_share_one_ledger_entry(self):
        # One file on Box, one record. Keyed raw, this was two records for one
        # file, each asserting a backup that only one of them had.
        a = obj(name=self.COMPOSED)
        b = obj(name=self.DECOMPOSED)
        assert a.ledger_key == b.ledger_key

    def test_an_ordinary_name_is_untouched(self):
        plain = "cyl-images/cyl-image_13891376_282e916f.png"
        assert obj(name=plain).ledger_key == ("images", plain)

    def test_the_key_and_the_destination_agree(self):
        # The property the box_path docstring claimed all along.
        for name in (self.COMPOSED, self.DECOMPOSED, "plain/a.png"):
            o = obj(name=name)
            bucket, key = o.ledger_key
            assert lib.box_path(o) == f"{bucket}/{key}"
