"""bloomctl scrna — the resumable upload and the checked download, against a fake storage."""

import base64
import hashlib

import httpx
import pytest
from scrna_fixtures import gzipped

from bloomctl.scrna import _transfer as tr

API = "http://api.test"
EP = tr.Endpoint(api_url=API, anon_key="anon", token="tok")
DUPLICATE = {"statusCode": "409", "error": "Duplicate", "message": "The resource already exists"}


class FakeStorage:
    """Just enough of the storage service: resumable uploads and authenticated reads."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.drop_after: int | None = None
        self.patches = 0
        self.hide_objects = False
        # Storage takes the bytes but never creates the object (a finalisation failure).
        self.finalise = True
        # Acknowledge fewer bytes than the next chunk sends, once, as the protocol permits.
        self.ack_short_by = 0
        self.short_acks = 1
        self.offset_header: str | None = None
        self.offset_status: int | None = None
        # Storage answers an unauthenticated caller much as it answers a missing object.
        self.expired = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/storage/v1/upload/resumable":
            meta = dict(item.split(" ") for item in request.headers["upload-metadata"].split(","))
            name = "/".join(base64.b64decode(meta[k]).decode() for k in ("bucketName", "objectName"))
            if name in self.objects:
                return httpx.Response(409, json=DUPLICATE)
            upload_id = f"u{len(self.uploads)}"
            self.uploads[upload_id] = {
                "name": name, "length": int(request.headers["upload-length"]), "data": bytearray(),
            }
            # Storage answers with its own address, not the gateway's.
            return httpx.Response(201, headers={"Location": f"http://storage:5000/upload/resumable/{upload_id}"})
        if path.startswith("/storage/v1/upload/resumable/"):
            upload = self.uploads.get(path.rsplit("/", 1)[-1])
            if upload is None:
                return httpx.Response(404)
            if request.method == "HEAD":
                if self.offset_status is not None:
                    return httpx.Response(self.offset_status)
                return httpx.Response(200, headers={"Upload-Offset": str(len(upload["data"]))})
            if int(request.headers["upload-offset"]) != len(upload["data"]):
                return httpx.Response(409, text="offset does not match")
            if self.drop_after is not None and self.patches >= self.drop_after:
                raise httpx.ConnectError("connection dropped")
            self.patches += 1
            short = self.ack_short_by if self.short_acks > 0 else 0
            if short:
                self.short_acks -= 1
            kept = request.content[: len(request.content) - short]
            upload["data"] += kept
            if len(upload["data"]) == upload["length"] and self.finalise:
                if upload["name"] in self.objects:
                    return httpx.Response(409, json=DUPLICATE)
                self.objects[upload["name"]] = bytes(upload["data"])
            offset = self.offset_header or str(len(upload["data"]))
            return httpx.Response(204, headers={"Upload-Offset": offset})
        prefix = "/storage/v1/object/authenticated/"
        if path.startswith(prefix):
            if self.expired:
                return httpx.Response(400, json={
                    "statusCode": "400", "error": "InvalidJWT", "message": "jwt expired",
                })
            data = None if self.hide_objects else self.objects.get(path[len(prefix):])
            if data is None:
                return httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
            return httpx.Response(200, content=b"" if request.method == "HEAD" else data)
        return httpx.Response(500)


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def http(storage):
    with httpx.Client(transport=httpx.MockTransport(storage.handle)) as client:
        yield client


@pytest.fixture
def small_chunks(monkeypatch):
    monkeypatch.setattr(tr, "CHUNK_BYTES", 10)


def _source(tmp_path, data=b"0123456789" * 5 + b"tail"):
    path = tmp_path / "x.h5ad.gz"
    path.write_bytes(data)
    return path, data


def test_an_upload_is_created_and_sent_in_chunks(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    tr.send(http, EP, url, path, 0, len(data))
    assert storage.objects["scrna/h5ad/f.h5ad.gz"] == data
    assert storage.patches == 6


def test_the_upload_address_is_the_gateways(tmp_path, http, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    assert url == f"{API}/storage/v1/upload/resumable/u0"


def test_requests_carry_the_session_and_the_protocol(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    tr.send(http, EP, url, path, 0, len(data))
    for request in storage.requests:
        assert request.headers["authorization"] == "Bearer tok"
        assert request.headers["apikey"] == "anon"
        assert request.headers["tus-resumable"] == "1.0.0"
    assert storage.requests[0].headers["x-upsert"] == "false"


def test_a_dropped_upload_resumes_from_the_bytes_received(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    storage.drop_after = 2
    with pytest.raises(httpx.ConnectError):
        tr.send(http, EP, url, path, 0, len(data))
    offset = tr.upload_offset(http, EP, url)
    assert offset == 20
    storage.drop_after = None
    tr.send(http, EP, url, path, offset, len(data))
    assert storage.objects["scrna/h5ad/f.h5ad.gz"] == data
    assert len(storage.uploads) == 1


def test_an_unknown_upload_has_no_offset(http):
    assert tr.upload_offset(http, EP, f"{API}/storage/v1/upload/resumable/nope") is None


def test_creating_an_upload_for_a_stored_object_says_so(http, storage):
    storage.objects["scrna/h5ad/f.h5ad.gz"] = b"x"
    with pytest.raises(tr.AlreadyStored):
        tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", 1)


def test_an_object_stored_first_by_another_upload_says_so(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    storage.objects["scrna/h5ad/f.h5ad.gz"] = data
    with pytest.raises(tr.AlreadyStored):
        tr.send(http, EP, url, path, 0, len(data))


def test_an_offset_conflict_is_not_mistaken_for_a_stored_object(tmp_path, http, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    with pytest.raises(tr.TransferError) as exc:
        tr.send(http, EP, url, path, 10, len(data))
    assert not isinstance(exc.value, tr.AlreadyStored)


def test_an_object_is_found_or_not(http, storage):
    storage.objects["scrna/h5ad/f.h5ad.gz"] = b"x"
    assert tr.object_exists(http, EP, "scrna", "h5ad/f.h5ad.gz")
    assert not tr.object_exists(http, EP, "scrna", "h5ad/g.h5ad.gz")


def test_a_download_is_decompressed_and_fingerprinted(tmp_path, http, storage):
    data = b"\x89HDF\r\n\x1a\n" + bytes(range(256)) * 400
    storage.objects["scrna/h5ad/f.h5ad.gz"] = gzipped(data)
    dest = tmp_path / "out.tmp"
    assert tr.download_to(http, EP, "scrna", "h5ad/f.h5ad.gz", dest) == hashlib.sha256(data).hexdigest()
    assert dest.read_bytes() == data


def test_a_missing_object_is_not_stored(tmp_path, http):
    with pytest.raises(tr.NotStored):
        tr.download_to(http, EP, "scrna", "h5ad/f.h5ad.gz", tmp_path / "out.tmp")


def test_a_truncated_download_is_refused(tmp_path, http, storage):
    storage.objects["scrna/h5ad/f.h5ad.gz"] = gzipped(b"x" * 10000)[:-12]
    with pytest.raises(tr.TransferError, match="ended before"):
        tr.download_to(http, EP, "scrna", "h5ad/f.h5ad.gz", tmp_path / "out.tmp")


# --- what the sender reports back ---------------------------------------------


def test_sending_reports_the_bytes_storage_acknowledged(tmp_path, http, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    assert tr.send(http, EP, url, path, 0, len(data)) == len(data)


def test_a_short_acknowledgement_resends_from_where_storage_actually_is(tmp_path, http, storage, small_chunks):
    """Storage may keep fewer bytes than were sent; the next chunk has to start there."""
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    storage.ack_short_by = 4
    assert tr.send(http, EP, url, path, 0, len(data)) == len(data)
    assert storage.objects["scrna/h5ad/f.h5ad.gz"] == data


def test_a_server_that_never_keeps_the_last_bytes_is_refused_rather_than_looping(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    storage.ack_short_by, storage.short_acks = 4, 99
    with pytest.raises(tr.TransferError, match="acknowledged"):
        tr.send(http, EP, url, path, 0, len(data))


def test_an_acknowledgement_that_goes_backwards_or_past_the_end_is_refused(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    storage.offset_header = str(len(data) + 99)
    with pytest.raises(tr.TransferError, match="acknowledged"):
        tr.send(http, EP, url, path, 0, len(data))


def test_an_unreadable_offset_is_refused(tmp_path, http, storage, small_chunks):
    path, data = _source(tmp_path)
    url = tr.create_upload(http, EP, "scrna", "h5ad/f.h5ad.gz", len(data))
    storage.offset_header = "not-a-number"
    with pytest.raises(tr.TransferError, match="offset"):
        tr.send(http, EP, url, path, 0, len(data))


@pytest.mark.parametrize("status", [401, 403, 404, 410])
def test_an_upload_this_session_cannot_ask_about_is_forgotten(http, storage, status):
    """Whatever the reason, the answer is to start a new upload, not to fail forever."""
    storage.uploads["u0"] = {"name": "scrna/h5ad/f.h5ad.gz", "length": 10, "data": bytearray()}
    storage.offset_status = status
    assert tr.upload_offset(http, EP, f"{API}/storage/v1/upload/resumable/u0") is None
