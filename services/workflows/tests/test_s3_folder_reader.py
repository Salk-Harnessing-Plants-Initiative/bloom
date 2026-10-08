"""Unit tests for reading a folder a scientist shared with Bloom's AWS user: the folder
check lists anonymously first, and only when that's refused and the reader is configured does
it list again, signed as that user (against a fake S3)."""

import httpx
import pytest

import s3_folder
from tests.test_s3_folder import R1, R2, _client, _listing, _refusal

KEY_ID = "AKIATESTREADER000001"
SECRET = "test-reader-secret-never-printed"
URL = "s3://lab-private/run42/"


@pytest.fixture
def reader(monkeypatch):
    monkeypatch.setattr(s3_folder, "READER_KEY_ID", KEY_ID)
    monkeypatch.setattr(s3_folder, "READER_SECRET", SECRET)


def _private(seen, region_on_refusal=None, home_region=None):
    """A bucket that refuses anonymous requests and serves the listing to the reader. With
    `home_region`, a signed request elsewhere is told to go there."""

    def handler(request):
        seen.append(request)
        signed = request.headers.get("authorization", "").startswith("AWS4-HMAC-SHA256")
        if not signed:
            headers = (
                {"x-amz-bucket-region": region_on_refusal} if region_on_refusal else {}
            )
            return httpx.Response(403, headers=headers)
        if home_region and request.url.host != f"s3.{home_region}.amazonaws.com":
            return httpx.Response(400, headers={"x-amz-bucket-region": home_region})
        return httpx.Response(200, text=_listing("run42/", [R1, R2]))

    return _client(handler)


def _scope(request) -> str:
    """The credential scope of a signed request: <key>/<date>/<region>/s3/aws4_request."""
    auth = request.headers["authorization"]
    return auth.split("Credential=", 1)[1].split(",", 1)[0]


def test_a_shared_private_folder_is_read_as_the_reader(reader):
    seen = []
    assert s3_folder.check_folder(URL, _private(seen))["sample"] == "col0"
    assert len(seen) == 3, "anonymous listing, signed listing, signed read of one file"
    assert "authorization" not in seen[0].headers, "the first listing must be anonymous"
    scope = _scope(seen[1])
    assert scope.startswith(f"{KEY_ID}/") and scope.endswith(
        "/us-east-1/s3/aws4_request"
    )
    assert seen[1].headers["x-amz-date"] and seen[1].headers["x-amz-content-sha256"]
    assert seen[1].url == seen[0].url, "the signed retry lists the same folder"
    assert seen[2].url.path.endswith("/col0_S1_L001_R1_001.fastq.gz")
    assert _scope(seen[2]).startswith(f"{KEY_ID}/"), "a shared folder's file is read signed"
    assert "range" in seen[2].headers["authorization"].split("SignedHeaders=", 1)[1]


def test_the_signed_listing_goes_to_the_region_s3_named(reader):
    seen = []
    s3_folder.check_folder(URL, _private(seen, region_on_refusal="eu-west-1"))
    assert seen[1].url.host == "s3.eu-west-1.amazonaws.com"
    assert _scope(seen[1]).endswith("/eu-west-1/s3/aws4_request")


def test_a_signed_listing_in_the_wrong_region_is_signed_again_for_the_right_one(reader):
    seen = []
    s3_folder.check_folder(URL, _private(seen, home_region="ap-southeast-2"))
    hosts = [r.url.host for r in seen]
    assert hosts == [
        "s3.amazonaws.com",
        "s3.amazonaws.com",
        "s3.ap-southeast-2.amazonaws.com",
        "s3.ap-southeast-2.amazonaws.com",
    ]
    assert _scope(seen[2]).endswith("/ap-southeast-2/s3/aws4_request")


def test_a_public_folder_never_uses_the_reader(reader):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=_listing("run42/", [R1, R2]))

    s3_folder.check_folder(URL, _client(handler))
    assert len(seen) == 2
    assert all("authorization" not in r.headers for r in seen)


def test_a_folder_not_shared_says_to_make_it_public_or_share_it(reader):
    err = _refusal(URL, _client(lambda r: httpx.Response(403)))
    assert err.status_code == 422
    assert "make the folder public, or share it with Bloom's AWS user" in err.detail
    assert SECRET not in err.detail and KEY_ID not in err.detail


def test_without_the_reader_a_private_folder_must_be_public():
    seen = []
    err = _refusal(URL, _private(seen))
    assert "the folder must be public" in err.detail
    assert len(seen) == 1, "no signed listing without the reader's keys"


@pytest.mark.parametrize("key_id, secret", [(KEY_ID, None), (None, SECRET), ("", "")])
def test_half_a_key_is_no_reader(monkeypatch, key_id, secret):
    monkeypatch.setattr(s3_folder, "READER_KEY_ID", key_id)
    monkeypatch.setattr(s3_folder, "READER_SECRET", secret)
    assert s3_folder.reader_configured() is False


def test_the_secret_never_reaches_the_request(reader):
    seen = []
    s3_folder.check_folder(URL, _private(seen))
    for request in seen:
        assert SECRET not in str(request.url)
        assert all(SECRET not in value for value in request.headers.values())


# --------------------------------------------------------------------------- #
# Reading a file, not only listing the folder
# --------------------------------------------------------------------------- #


def _listed_not_read(seen, signed_listing):
    """A folder whose listing is served (signed or anonymous) and whose files are refused."""

    def handler(request):
        seen.append(request)
        signed = "authorization" in request.headers
        if "list-type" in str(request.url):
            if signed_listing and not signed:
                return httpx.Response(403)
            return httpx.Response(200, text=_listing("run42/", [R1, R2]))
        return httpx.Response(403)

    return _client(handler)


def test_a_shared_folder_bloom_can_list_but_not_read_is_refused_naming_both_grants(reader):
    seen = []
    err = _refusal(URL, _listed_not_read(seen, signed_listing=True))
    assert err.status_code == 422
    assert "s3:GetObject" in err.detail and "kms:Decrypt" in err.detail
    assert SECRET not in err.detail and KEY_ID not in err.detail


def test_a_public_listing_with_private_files_is_refused(reader):
    seen = []
    err = _refusal(URL, _listed_not_read(seen, signed_listing=False))
    assert err.status_code == 422
    assert "Anyone can list this folder but not read its files" in err.detail
    assert all("authorization" not in r.headers for r in seen), "read the way it listed"


def test_a_read_s3_answers_oddly_is_a_502(reader):
    def handler(request):
        if "list-type" in str(request.url):
            return httpx.Response(200, text=_listing("run42/", [R1, R2]))
        return httpx.Response(500)

    err = _refusal(URL, _client(handler))
    assert err.status_code == 502 and "reading a FASTQ" in err.detail
