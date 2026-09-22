"""bloomctl scrna upload / download — the commands, end to end against fakes."""

import gzip
import hashlib

import httpx
import pytest
from click.testing import CliRunner
from scrna_fixtures import gzipped, write_h5ad
from test_scrna_transfer import FakeStorage

from bloomctl.cli import cli
from bloomctl.scrna import _format, _object, _session, _transfer

NORMALIZATION = {"transform": "log1p", "scaling": "library_size", "target_sum": 10000}


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def select(self, _columns):
        return self

    def eq(self, column, value):
        return _Query([r for r in self.rows if str(r.get(column)) == str(value)])

    def is_(self, column, value):
        assert value == "null"
        return _Query([r for r in self.rows if r.get(column) is None])

    def execute(self):
        return type("R", (), {"data": self.rows})()


class FakeClient:
    def __init__(self, datasets=()):
        self.datasets = [dict({"deleted_at": None, "metadata": {}}, **d) for d in datasets]

    def table(self, name):
        assert name == "scrna_datasets"
        return _Query(self.datasets)


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def env(monkeypatch, tmp_path, storage):
    """A writer session, fake storage, and a resume cache under tmp_path."""
    state = {"role": "bloom_writer", "client": FakeClient()}
    monkeypatch.setattr(
        _session, "connect",
        lambda profile: _session.Connection(
            client=state["client"],
            endpoint=_transfer.Endpoint("http://api.test", "anon", "tok"),
            role=state["role"],
        ),
    )
    monkeypatch.setattr(
        _transfer, "open_client",
        lambda: httpx.Client(transport=httpx.MockTransport(storage.handle)),
    )
    monkeypatch.setattr(_object, "staging_dir", lambda: tmp_path / "stage")
    monkeypatch.setattr(_transfer, "CHUNK_BYTES", 1024)
    return state


def _run(*args):
    return CliRunner().invoke(cli, ["scrna", *args])


def _stored(storage, path):
    return gzip.decompress(storage.objects[f"scrna/{_object.object_path(hashlib.sha256(path.read_bytes()).hexdigest())}"])


# --- upload -------------------------------------------------------------------


def test_a_writer_uploads_a_file(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert fingerprint in result.output
    assert f"scrna/h5ad/{fingerprint}.h5ad.gz" in result.output
    assert _stored(storage, path) == path.read_bytes()
    assert not list((tmp_path / "stage").iterdir())


def test_a_reader_cannot_upload_and_nothing_is_read(tmp_path, env, monkeypatch):
    env["role"] = "bloom_user"
    monkeypatch.setattr(_format, "check_structure", lambda *_: pytest.fail("the file was read"))
    result = _run("upload", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code != 0
    assert "bloom_writer or bloom_admin" in result.output


def test_a_file_that_does_not_meet_the_format_is_refused_before_sending(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad", obs_ids=["a", "a", "c"])
    result = _run("upload", str(path))
    assert result.exit_code != 0
    assert "does not meet Bloom's h5ad format" in result.output
    assert "'a' appears more than once" in result.output
    assert storage.requests == []


def test_a_missing_extra_says_how_to_install_it(tmp_path, env, monkeypatch):
    def missing(_path):
        raise _format.MissingExtra("the structure check needs h5py: pip install 'bloomctl[scrna]'")

    monkeypatch.setattr(_format, "check_structure", missing)
    result = _run("upload", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code != 0
    assert "bloomctl[scrna]" in result.output


def test_the_same_file_twice_is_one_object(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", str(path)).exit_code == 0
    creates = len(storage.uploads)
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert "Already uploaded" in result.output
    assert len(storage.uploads) == creates
    assert not list((tmp_path / "stage").iterdir())


def test_a_file_that_landed_first_from_another_upload_is_reported_stored(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", str(path)).exit_code == 0
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert "Already uploaded" in result.output


def test_a_missing_normalization_is_refused_for_a_new_file(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad", normalization=None)
    result = _run("upload", str(path))
    assert result.exit_code != 0
    assert "uns['normalization']" in result.output
    assert "transform" in result.output and "scaling" in result.output
    assert not storage.uploads
    assert not list((tmp_path / "stage").iterdir())


def test_a_file_a_dataset_was_loaded_from_is_accepted_by_its_record(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad", normalization=None)
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    env["client"] = FakeClient([{
        "id": 14, "name": "MYB41 transgene", "source_checksum": fingerprint,
        "metadata": {"normalization": NORMALIZATION},
    }])
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert _stored(storage, path) == path.read_bytes()


def test_a_dataset_record_without_normalization_does_not_exempt_the_file(tmp_path, env):
    path = write_h5ad(tmp_path / "data.h5ad", normalization=None)
    env["client"] = FakeClient([{
        "id": 14, "name": "MYB41 transgene",
        "source_checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
    }])
    assert _run("upload", str(path)).exit_code != 0


def test_a_file_too_large_is_refused_before_sending(tmp_path, env, storage, monkeypatch):
    monkeypatch.setattr(_object, "MAX_OBJECT_BYTES", 10)
    result = _run("upload", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code != 0
    assert "limit" in result.output
    assert not storage.uploads
    assert not list((tmp_path / "stage").iterdir())


def test_an_interrupted_upload_resumes_on_the_next_run(tmp_path, env, storage, monkeypatch):
    monkeypatch.setattr(_transfer, "CHUNK_BYTES", 64)
    path = write_h5ad(tmp_path / "data.h5ad")
    storage.drop_after = 2
    first = _run("upload", str(path))
    assert first.exit_code != 0
    assert "Run the same command again" in first.output
    assert list((tmp_path / "stage").glob("*.h5ad.gz"))
    storage.drop_after = None
    second = _run("upload", str(path))
    assert second.exit_code == 0, second.output
    assert len(storage.uploads) == 1
    assert _stored(storage, path) == path.read_bytes()
    assert not list((tmp_path / "stage").iterdir())


def test_storage_refusing_to_start_says_nothing_was_sent(tmp_path, env, storage, monkeypatch):
    real = storage.handle

    def refuse_creates(request):
        if request.method == "POST":
            return httpx.Response(500, text="storage backend unavailable")
        return real(request)

    monkeypatch.setattr(storage, "handle", refuse_creates)
    result = _run("upload", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code != 0
    assert "Nothing was sent" in result.output
    assert "already sent" not in result.output


def test_bytes_taken_but_no_object_stored_is_not_success(tmp_path, env, storage):
    """Storage can accept every byte and still fail to finalise the object."""
    storage.finalise = False
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", str(path))
    assert result.exit_code != 0
    assert "not stored" in result.output
    assert "Uploaded" not in result.output
    assert list((tmp_path / "stage").glob("*.h5ad.gz")), "the only resumable copy was deleted"


def test_an_upload_already_at_full_length_is_confirmed_not_assumed(tmp_path, env, storage):
    """A resumed upload the server already holds in full, with no object behind it."""
    storage.finalise = False
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", str(path)).exit_code != 0   # leaves a full-length upload behind
    second = _run("upload", str(path))
    assert second.exit_code != 0
    assert "Uploaded" not in second.output


def test_an_upload_recorded_for_other_bytes_is_not_resumed(tmp_path, env, storage):
    path = write_h5ad(tmp_path / "data.h5ad")
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    stage = tmp_path / "stage"
    _object.stage(path, stage)
    _object.save_upload(stage, fingerprint, "u-stale", 999_999, "http://api.test")
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert not any("u-stale" in str(r.url) for r in storage.requests)
    assert _stored(storage, path) == path.read_bytes()


def test_an_upload_recorded_for_another_server_is_not_resumed(tmp_path, env, storage):
    """Resuming it would send this session's token to the other server."""
    path = write_h5ad(tmp_path / "data.h5ad")
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    stage = tmp_path / "stage"
    staged = _object.stage(path, stage)
    _object.save_upload(stage, fingerprint, "u-elsewhere", staged.size, "http://other.test")
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert not any("u-elsewhere" in str(r.url) for r in storage.requests)


def test_a_staged_copy_that_is_not_the_file_is_rebuilt(tmp_path, env, storage):
    """Its name asserts a fingerprint; storing other bytes under it could never be undone."""
    path = write_h5ad(tmp_path / "data.h5ad")
    stage = tmp_path / "stage"
    staged = _object.stage(path, stage)
    staged.gz_path.write_bytes(gzipped(b"not the file at all"))
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert _stored(storage, path) == path.read_bytes()


# --- download -----------------------------------------------------------------


def _dataset(storage, data=b"\x89HDF\r\n\x1a\n" + b"cells" * 500, **row):
    fingerprint = hashlib.sha256(data).hexdigest()
    storage.objects[f"scrna/{_object.object_path(fingerprint)}"] = gzipped(data)
    return data, fingerprint, {"id": 14, "name": "MYB41 transgene",
                               "source_checksum": fingerprint, **row}


def test_a_dataset_is_downloaded_by_name(tmp_path, env, storage):
    data, fingerprint, row = _dataset(storage)
    env["client"] = FakeClient([row])
    out = tmp_path / "myb41.h5ad"
    result = _run("download", "MYB41 transgene", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == data
    assert fingerprint in result.output


def test_a_dataset_is_downloaded_by_id_to_its_name_by_default(tmp_path, env, storage, monkeypatch):
    data, _, row = _dataset(storage)
    env["client"] = FakeClient([row])
    monkeypatch.chdir(tmp_path)
    result = _run("download", "14")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "MYB41_transgene.h5ad").read_bytes() == data


def test_an_object_is_downloaded_by_fingerprint(tmp_path, env, storage):
    data, fingerprint, _ = _dataset(storage)
    out = tmp_path / "f.h5ad"
    result = _run("download", "--checksum", fingerprint, "--out", str(out))
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == data


def test_a_corrupted_download_leaves_nothing_behind(tmp_path, env, storage):
    _, fingerprint, row = _dataset(storage)
    storage.objects[f"scrna/{_object.object_path(fingerprint)}"] = gzipped(b"something else")
    env["client"] = FakeClient([row])
    out = tmp_path / "myb41.h5ad"
    result = _run("download", "14", "--out", str(out))
    assert result.exit_code != 0
    assert fingerprint in result.output
    assert not out.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_a_file_already_present_is_left_alone(tmp_path, env, storage):
    data, _, row = _dataset(storage)
    env["client"] = FakeClient([row])
    out = tmp_path / "myb41.h5ad"
    out.write_bytes(data)
    result = _run("download", "14", "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "already" in result.output
    assert not [r for r in storage.requests if r.method == "GET"]


def test_a_different_file_at_the_destination_is_not_overwritten(tmp_path, env, storage):
    _, _, row = _dataset(storage)
    env["client"] = FakeClient([row])
    out = tmp_path / "myb41.h5ad"
    out.write_bytes(b"mine")
    result = _run("download", "14", "--out", str(out))
    assert result.exit_code != 0
    assert out.read_bytes() == b"mine"


def test_an_ambiguous_name_asks_for_the_id(env, storage):
    _, _, row = _dataset(storage)
    env["client"] = FakeClient([row, {**row, "id": 21}])
    result = _run("download", "MYB41 transgene")
    assert result.exit_code != 0
    assert "14" in result.output and "21" in result.output


def test_a_dataset_without_a_fingerprint_is_a_clear_error(env):
    env["client"] = FakeClient([{"id": 3, "name": "Old atlas", "source_checksum": None}])
    result = _run("download", "3")
    assert result.exit_code != 0
    assert "Old atlas" in result.output
    assert "fingerprint" in result.output


def test_a_dataset_whose_file_is_not_stored_names_the_path(env):
    fingerprint = "b" * 64
    env["client"] = FakeClient([{"id": 5, "name": "Atlas", "source_checksum": fingerprint}])
    result = _run("download", "5")
    assert result.exit_code != 0
    assert "Atlas" in result.output
    assert f"scrna/h5ad/{fingerprint}.h5ad.gz" in result.output


def test_an_unknown_dataset_is_a_clear_error(env):
    result = _run("download", "nope")
    assert result.exit_code != 0
    assert "nope" in result.output


def test_an_expired_session_is_not_reported_as_a_missing_file(tmp_path, env, storage):
    """Storage answers an unauthenticated caller the same way it answers a missing object."""
    _, _, row = _dataset(storage)
    env["client"] = FakeClient([row])
    storage.expired = True
    result = _run("download", "14", "--out", str(tmp_path / "x.h5ad"))
    assert result.exit_code != 0
    assert "log in" in result.output or "sign in" in result.output
    assert "no stored file" not in result.output


def test_a_destination_that_cannot_be_written_is_a_clear_error(tmp_path, env, storage):
    data, _, row = _dataset(storage)
    env["client"] = FakeClient([row])
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = _run("download", "14", "--out", str(locked / "x.h5ad"))
    finally:
        locked.chmod(0o700)
    assert result.exit_code != 0
    assert "x.h5ad" in result.output


def test_an_expired_session_does_not_start_a_second_upload(tmp_path, env, storage):
    storage.expired = True
    result = _run("upload", str(write_h5ad(tmp_path / "data.h5ad")))
    assert result.exit_code != 0
    assert "log in" in result.output or "sign in" in result.output
    assert not storage.uploads


def test_download_needs_a_dataset_or_a_fingerprint(env):
    result = _run("download")
    assert result.exit_code != 0
    assert "DATASET" in result.output or "--checksum" in result.output


def test_a_finalisation_failure_does_not_wedge_the_file(tmp_path, env, storage):
    """the record used to survive at full length, so every later run repeated the failure."""
    storage.finalise = False
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", str(path)).exit_code != 0
    assert not list((tmp_path / "stage").glob("*.upload")), "the stuck upload was kept"
    assert list((tmp_path / "stage").glob("*.h5ad.gz")), "the resumable copy was thrown away"

    storage.finalise = True
    again = _run("upload", str(path))
    assert again.exit_code == 0, again.output
    assert "Uploaded" in again.output


def test_a_duplicate_that_cannot_be_read_back_is_not_called_uploaded(tmp_path, env, storage):
    """this branch trusted a 409 body; it reported success with nothing in the bucket."""
    path = write_h5ad(tmp_path / "data.h5ad")
    storage.hide_objects = True          # nothing is readable
    storage.duplicate_creates = True     # but storage says the name is taken
    result = _run("upload", str(path))
    assert result.exit_code != 0, result.output
    assert "Already uploaded" not in result.output and "Uploaded" not in result.output
    assert list((tmp_path / "stage").glob("*.h5ad.gz")), "the only copy was deleted"


def test_a_blip_confirming_the_object_is_retried(tmp_path, env, storage):
    """one failed confirmation reported a stored object as an upload that had stopped."""
    storage.fail_reads = 1
    path = write_h5ad(tmp_path / "data.h5ad")
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert "Uploaded" in result.output


def test_a_destination_that_is_a_directory_is_refused_cleanly(tmp_path, env, storage, monkeypatch):
    """The name the download would write to is already a directory, so nothing can be saved."""
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", str(path)).exit_code == 0
    fingerprint = _object.fingerprint_of(path)
    monkeypatch.chdir(tmp_path)
    (tmp_path / f"{fingerprint}.h5ad").mkdir()          # what the default name would write to
    result = _run("download", "--checksum", fingerprint)
    assert result.exit_code != 0
    assert "is a directory" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception


def test_an_empty_object_is_not_reported_as_uploaded(tmp_path, env, storage):
    """A finalisation that keeps nothing leaves the name taken and the file absent."""
    path = write_h5ad(tmp_path / "data.h5ad")
    fingerprint = _object.fingerprint_of(path)
    storage.empty_objects.add(f"scrna/h5ad/{fingerprint}.h5ad.gz")
    result = _run("upload", str(path))
    assert result.exit_code != 0, result.output
    assert "Already uploaded" not in result.output and "Uploaded" not in result.output
    assert list((tmp_path / "stage").glob("*.h5ad.gz")), "the only copy was deleted"


def test_a_name_taken_by_something_unreadable_does_not_claim_bytes_were_sent(tmp_path, env, storage):
    """Nothing is sent on this path, so 'storage took every byte' would be untrue."""
    path = write_h5ad(tmp_path / "data.h5ad")
    storage.hide_objects = True
    storage.duplicate_creates = True
    result = _run("upload", str(path))
    assert result.exit_code != 0
    assert "took every byte" not in result.output
    assert "Nothing was sent" in result.output
    assert storage.patches == 0


def test_an_upload_storage_cannot_account_for_is_kept_not_dropped(tmp_path, env, storage, monkeypatch):
    """Unconfirmable is not the same as not stored: keep everything a rerun could need."""
    monkeypatch.setattr(_transfer, "CHUNK_BYTES", 64)
    path = write_h5ad(tmp_path / "data.h5ad")
    storage.fail_reads = 99          # both confirmation attempts fail
    result = _run("upload", str(path))
    assert result.exit_code != 0
    assert "could not be asked" in result.output
    assert list((tmp_path / "stage").glob("*.upload")), "the resumable upload was forgotten"
    assert list((tmp_path / "stage").glob("*.h5ad.gz"))


def test_an_expired_session_mid_upload_is_not_an_internal_error(tmp_path, env, storage, monkeypatch):
    """It used to leave the command's own handling entirely and log a traceback."""
    monkeypatch.setattr(_transfer, "CHUNK_BYTES", 64)
    path = write_h5ad(tmp_path / "data.h5ad")
    storage.expired = True
    result = _run("upload", str(path))
    assert result.exit_code != 0
    assert "log in again" in result.output
    assert not isinstance(result.exception, _transfer.SessionExpired), "escaped as an internal error"
    # The generic wording tells the user to re-run, which fails identically until they log in.
    assert "once you are logged in" in result.output
    assert "Run the same command again to resume" not in result.output


def test_a_record_naming_an_unusable_upload_is_forgotten(tmp_path, env, storage):
    """A record the command cannot use must not fail every later run."""
    import json
    path = write_h5ad(tmp_path / "data.h5ad")
    stage = tmp_path / "stage"
    stage.mkdir(exist_ok=True)
    fingerprint = _object.fingerprint_of(path)
    from bloomctl.scrna import _object as obj
    gz = obj.stage(path, stage)
    (stage / f"{fingerprint}.upload").write_text(
        json.dumps({"id": "../../auth/v1/token", "size": gz.size, "api_url": "http://api.test"})
    )
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert "Uploaded" in result.output


def test_a_file_that_appears_between_the_check_and_the_create_is_reported_stored(tmp_path, env, storage):
    """Another writer lands the same content first; storage refuses the name and it is there."""
    path = write_h5ad(tmp_path / "data.h5ad")
    assert _run("upload", str(path)).exit_code == 0
    storage.hide_reads = 1          # the pre-flight misses it, as a race would
    result = _run("upload", str(path))
    assert result.exit_code == 0, result.output
    assert "Already uploaded" in result.output
    assert not list((tmp_path / "stage").glob("*")), "the staged copy was kept for nothing"
