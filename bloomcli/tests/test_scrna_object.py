"""bloomctl scrna — fingerprinting, gzipping, naming and the resume cache."""

import gzip
import hashlib

import pytest

from bloomctl.scrna import _object as obj


def _file(tmp_path, data=b"\x89HDF\r\n\x1a\n" + b"payload" * 1000):
    path = tmp_path / "data.h5ad"
    path.write_bytes(data)
    return path, data


def test_an_object_is_named_by_its_fingerprint():
    fingerprint = "a" * 64
    assert obj.object_path(fingerprint) == f"h5ad/{fingerprint}.h5ad.gz"


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "g" * 64, "../" + "a" * 61])
def test_a_malformed_fingerprint_names_nothing(bad):
    with pytest.raises(ValueError):
        obj.object_path(bad)


def test_the_hdf5_signature_is_recognised(tmp_path):
    path, _ = _file(tmp_path)
    assert obj.is_hdf5(path)


def test_a_file_without_the_signature_is_not_hdf5(tmp_path):
    path, _ = _file(tmp_path, b"cell,gene\n1,2\n")
    assert not obj.is_hdf5(path)


def test_the_fingerprint_is_the_sha256_of_the_uncompressed_file(tmp_path):
    path, data = _file(tmp_path)
    assert obj.fingerprint_of(path) == hashlib.sha256(data).hexdigest()


def test_staging_hashes_and_gzips_in_one_pass(tmp_path):
    path, data = _file(tmp_path)
    staged = obj.stage(path, tmp_path / "stage")
    assert staged.fingerprint == hashlib.sha256(data).hexdigest()
    assert staged.gz_path.name == f"{staged.fingerprint}.h5ad.gz"
    assert gzip.decompress(staged.gz_path.read_bytes()) == data
    assert staged.size == staged.gz_path.stat().st_size


def test_a_staged_form_left_by_an_interrupted_run_is_reused(tmp_path):
    """A resumed upload has to send the bytes the server already holds part of."""
    path, _ = _file(tmp_path)
    first = obj.stage(path, tmp_path / "stage")
    kept = first.gz_path.read_bytes()
    first.gz_path.write_bytes(kept)  # the same bytes, as a resume would find them
    second = obj.stage(path, tmp_path / "stage")
    assert second.gz_path == first.gz_path
    assert second.gz_path.read_bytes() == kept


def test_staging_leaves_no_temporary_file(tmp_path):
    path, _ = _file(tmp_path)
    obj.stage(path, tmp_path / "stage")
    obj.stage(path, tmp_path / "stage")
    assert not list((tmp_path / "stage").glob("*.tmp"))


def test_the_upload_is_kept_with_what_identifies_it_and_cleared_with_the_staged_form(tmp_path):
    path, _ = _file(tmp_path)
    stage = tmp_path / "stage"
    staged = obj.stage(path, stage)
    obj.save_upload(stage, staged.fingerprint, "u0", staged.size, "http://api.test")
    assert obj.load_upload(stage, staged.fingerprint) == {
        "id": "u0", "size": staged.size, "api_url": "http://api.test",
    }
    obj.clear(stage, staged.fingerprint)
    assert obj.load_upload(stage, staged.fingerprint) is None
    assert not staged.gz_path.exists()


def test_an_upload_kept_for_another_server_is_not_offered(tmp_path):
    """Resuming it would send this session's token to the other server."""
    path, _ = _file(tmp_path)
    stage = tmp_path / "stage"
    staged = obj.stage(path, stage)
    obj.save_upload(stage, staged.fingerprint, "u0", staged.size, "http://staging.test")
    assert obj.load_upload(stage, staged.fingerprint, api_url="http://prod.test") is None
    assert obj.load_upload(stage, staged.fingerprint, api_url="http://staging.test") is not None


def test_an_upload_kept_for_a_different_length_is_not_offered(tmp_path):
    """The gzipped form was rewritten, so the server's upload is for other bytes."""
    path, _ = _file(tmp_path)
    stage = tmp_path / "stage"
    staged = obj.stage(path, stage)
    obj.save_upload(stage, staged.fingerprint, "u0", staged.size + 17, "http://api.test")
    assert obj.load_upload(stage, staged.fingerprint, size=staged.size) is None
    assert obj.load_upload(stage, staged.fingerprint, size=staged.size + 17) is not None


def test_an_unreadable_upload_record_is_ignored(tmp_path):
    path, _ = _file(tmp_path)
    stage = tmp_path / "stage"
    staged = obj.stage(path, stage)
    obj.save_upload(stage, staged.fingerprint, "u0", staged.size, "http://api.test")
    (stage / f"{staged.fingerprint}.upload").write_text("{ this is not json")
    assert obj.load_upload(stage, staged.fingerprint) is None


def test_the_size_limit_is_the_storage_limit():
    assert obj.MAX_OBJECT_BYTES == 500 * 1024 * 1024
