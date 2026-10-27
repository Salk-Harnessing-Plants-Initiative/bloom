"""Unit tests for the S3 folder check: the URL rules, the listing (against a fake S3), the
FASTQ rules, and the sample and files it returns."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import HTTPException

import s3_folder

R1 = "col0_S1_L001_R1_001.fastq.gz"
R2 = "col0_S1_L001_R2_001.fastq.gz"


def _listing(prefix, names, truncated=False, sizes=None):
    sizes = sizes or {}
    items = "".join(
        f"<Contents><Key>{prefix}{n}</Key><Size>{sizes.get(n, 100)}</Size>"
        f"<ETag>&quot;etag-{n}&quot;</ETag></Contents>"
        for n in names
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Prefix>{prefix}</Prefix><IsTruncated>{str(truncated).lower()}</IsTruncated>"
        f"{items}</ListBucketResult>"
    )


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _serving(names, prefix="run42/", **kwargs):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=_listing(prefix, names, **kwargs))

    return _client(handler), seen


def _refusal(url, client):
    with pytest.raises(HTTPException) as exc:
        s3_folder.check_folder(url, client)
    return exc.value


# --------------------------------------------------------------------------- #
# The URL
# --------------------------------------------------------------------------- #


def test_a_missing_slash_is_added():
    assert s3_folder.normalise_url("s3://lab-data/run42") == (
        "lab-data",
        "run42/",
        "s3://lab-data/run42/",
    )


def test_surrounding_spaces_are_ignored():
    assert s3_folder.normalise_url("  s3://lab-data/a/b/ ")[2] == "s3://lab-data/a/b/"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        7,
        "s3://lab-data/",
        "s3://Lab-Data/run42/",
        "https://lab-data.s3.amazonaws.com/run42/",
        "s3://lab-data/run 42/",
        "s3://lab-data/a/../b/",
        "s3://lab-data/./",
        "s3://ab/run42/",
        "s3://lab-data/" + "a" * 1020 + "/",
        "s3://lab-data/run42/?x=1",
    ],
)
def test_a_bad_url_is_refused(url):
    with pytest.raises(HTTPException) as exc:
        s3_folder.normalise_url(url)
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------- #
# The listing
# --------------------------------------------------------------------------- #


def test_the_folder_is_listed_once_one_level_deep_on_aws():
    client, seen = _serving([R1, R2])
    s3_folder.check_folder("s3://lab-data/run42/", client)
    assert len(seen) == 2, "one listing, then one file's first byte"
    url = urlparse(str(seen[0].url))
    assert (url.scheme, url.netloc, url.path) == (
        "https",
        "s3.amazonaws.com",
        "/lab-data",
    )
    assert parse_qs(url.query) == {
        "list-type": ["2"],
        "delimiter": ["/"],
        "max-keys": ["1000"],
        "prefix": ["run42/"],
    }
    assert "authorization" not in seen[0].headers
    read = seen[1]
    assert read.url.path == "/lab-data/run42/col0_S1_L001_R1_001.fastq.gz"
    assert read.headers["range"] == "bytes=0-0" and "authorization" not in read.headers


def test_a_bucket_in_another_region_is_followed_once():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "s3.amazonaws.com":
            return httpx.Response(301, headers={"x-amz-bucket-region": "eu-west-1"})
        return httpx.Response(200, text=_listing("run42/", [R1, R2]))

    assert (
        s3_folder.check_folder("s3://lab-data/run42/", _client(handler))["sample"]
        == "col0"
    )
    assert seen == [
        "s3.amazonaws.com",
        "s3.eu-west-1.amazonaws.com",
        "s3.eu-west-1.amazonaws.com",
    ], "listed once more in its region, then a file read there"


@pytest.mark.parametrize(
    "status, words",
    [
        (403, "must be public"),
        (401, "must be public"),
        (404, "doesn't exist"),
    ],
)
def test_an_unreadable_folder_says_why(status, words):
    client = _client(lambda r: httpx.Response(status))
    err = _refusal("s3://lab-data/run42/", client)
    assert err.status_code == 422 and words in err.detail


def test_an_s3_error_is_a_502_not_a_refusal():
    err = _refusal("s3://lab-data/run42/", _client(lambda r: httpx.Response(500)))
    assert err.status_code == 502


def test_s3_out_of_reach_is_a_502():
    def handler(request):
        raise httpx.ConnectError("no route")

    assert _refusal("s3://lab-data/run42/", _client(handler)).status_code == 502


def test_a_folder_with_over_1000_files_is_refused():
    client, _ = _serving([R1, R2], truncated=True)
    err = _refusal("s3://lab-data/run42/", client)
    assert "over 1000 files" in err.detail and "one sample's folder" in err.detail


# --------------------------------------------------------------------------- #
# The FASTQs
# --------------------------------------------------------------------------- #


def test_a_good_folder_names_its_sample_and_files():
    client, _ = _serving([R2, R1, "md5sums.txt", "report.html"], sizes={R1: 10, R2: 32})
    assert s3_folder.check_folder("s3://lab-data/run42/", client) == {
        "fastq_url": "s3://lab-data/run42/",
        "sample": "col0",
        "lanes": [1],
        "files": [
            {"name": R1, "size": 10, "etag": f'"etag-{R1}"'},
            {"name": R2, "size": 32, "etag": f'"etag-{R2}"'},
        ],
        "file_count": 2,
        "total_bytes": 42,
    }


def test_several_lanes_index_reads_and_plain_fastq_are_accepted():
    names = [
        f"col0_S1_L00{ln}_{r}_001.fastq" for ln in (1, 2) for r in ("R1", "R2", "I1")
    ]
    client, _ = _serving(names)
    result = s3_folder.check_folder("s3://lab-data/run42/", client)
    assert (result["lanes"], result["file_count"]) == ([1, 2], 6)


def test_files_in_subfolders_are_ignored():
    client, _ = _serving([R1, R2, "old/col1_S1_L001_R1_001.fastq.gz"])
    assert s3_folder.check_folder("s3://lab-data/run42/", client)["file_count"] == 2


def test_no_fastqs_is_refused():
    client, _ = _serving(["md5sums.txt"])
    assert "No FASTQs" in _refusal("s3://lab-data/run42/", client).detail


def test_misnamed_fastqs_are_listed():
    client, _ = _serving(["reads_1.fastq.gz", "reads_2.fastq.gz"])
    err = _refusal("s3://lab-data/run42/", client)
    assert "Illumina way" in err.detail and "reads_1.fastq.gz" in err.detail


def test_two_samples_are_refused():
    client, _ = _serving(
        [R1, R2, "col1_S1_L001_R1_001.fastq.gz", "col1_S1_L001_R2_001.fastq.gz"]
    )
    err = _refusal("s3://lab-data/run42/", client)
    assert "one sample" in err.detail and "col0, col1" in err.detail


def test_a_lane_without_its_r2_is_refused():
    client, _ = _serving([R1, R2, "col0_S1_L002_R1_001.fastq.gz"])
    assert "S1 L002" in _refusal("s3://lab-data/run42/", client).detail


def test_each_s_number_needs_its_own_r1_and_r2():
    client, _ = _serving(
        ["col0_S1_L001_R1_001.fastq.gz", "col0_S2_L001_R2_001.fastq.gz"]
    )
    detail = _refusal("s3://lab-data/run42/", client).detail
    assert "S1 L001" in detail and "S2 L001" in detail


def test_two_s_numbers_each_complete_are_accepted():
    names = [f"col0_S{n}_L001_{r}_001.fastq.gz" for n in (1, 2) for r in ("R1", "R2")]
    client, _ = _serving(names)
    result = s3_folder.check_folder("s3://lab-data/run42/", client)
    assert (result["lanes"], result["file_count"]) == ([1], 4)


def test_a_read_both_plain_and_gzipped_is_refused():
    client, _ = _serving([R1, R1.removesuffix(".gz"), R2])
    detail = _refusal("s3://lab-data/run42/", client).detail
    assert "twice" in detail and "col0_S1_L001_R1_001.fastq" in detail


def test_a_sample_name_cell_ranger_cant_use_is_refused():
    client, _ = _serving(["a.b_S1_L001_R1_001.fastq.gz", "a.b_S1_L001_R2_001.fastq.gz"])
    assert "Cell Ranger run id" in _refusal("s3://lab-data/run42/", client).detail


def test_more_than_96_fastqs_are_refused():
    names = [
        f"col0_S1_L{ln:03d}_{r}_001.fastq.gz"
        for ln in range(1, 50)
        for r in ("R1", "R2")
    ]
    client, _ = _serving(names)
    assert "at most 96" in _refusal("s3://lab-data/run42/", client).detail


# --------------------------------------------------------------------------- #
# The client the routes use
# --------------------------------------------------------------------------- #


class _RecordingHttpx:
    """httpx with Client recorded, serving `handler` instead of the network."""

    def __init__(self, handler):
        self.handler = handler
        self.kwargs = []

    def __getattr__(self, name):
        return getattr(httpx, name)

    def Client(self, **kwargs):
        self.kwargs.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def test_the_routes_client_has_a_short_connect_and_read_timeout(monkeypatch):
    fake = _RecordingHttpx(
        lambda r: httpx.Response(200, text=_listing("run42/", [R1, R2]))
    )
    monkeypatch.setattr(s3_folder, "httpx", fake)
    assert s3_folder.check_folder("s3://lab-data/run42/")["sample"] == "col0"
    assert fake.kwargs == [{"timeout": s3_folder.TIMEOUT}]
    assert (s3_folder.TIMEOUT.connect, s3_folder.TIMEOUT.read) == (3.0, 10.0)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
        httpx.RemoteProtocolError("x"),
    ],
)
def test_a_slow_or_broken_s3_is_a_502_on_the_routes_client(monkeypatch, error):
    def handler(request):
        raise error

    monkeypatch.setattr(s3_folder, "httpx", _RecordingHttpx(handler))
    err = _refusal("s3://lab-data/run42/", None)
    assert err.status_code == 502 and "Couldn't reach S3" in err.detail


# --------------------------------------------------------------------------- #
# Limits, the database's own
# --------------------------------------------------------------------------- #


def _url_of_length(n):
    head = "s3://lab-data/"
    return head + "a" * (n - len(head) - 1) + "/"


def test_a_url_of_exactly_1024_characters_is_accepted():
    url = _url_of_length(s3_folder.MAX_URL)
    assert len(url) == 1024 and s3_folder.normalise_url(url)[2] == url


def test_1024_characters_counts_the_slash_that_is_added():
    url = _url_of_length(s3_folder.MAX_URL)
    assert s3_folder.normalise_url(url.removesuffix("/"))[2] == url


def test_a_url_of_1025_characters_is_refused():
    with pytest.raises(HTTPException) as exc:
        s3_folder.normalise_url(_url_of_length(s3_folder.MAX_URL + 1))
    assert exc.value.status_code == 422


def _lanes(count):
    return [
        f"col0_S1_L{ln:03d}_{r}_001.fastq.gz"
        for ln in range(1, count + 1)
        for r in ("R1", "R2")
    ]


def test_exactly_96_fastqs_are_accepted():
    client, _ = _serving(_lanes(48))
    assert s3_folder.check_folder("s3://lab-data/run42/", client)["file_count"] == 96


def test_97_fastqs_are_refused():
    client, _ = _serving(_lanes(48) + ["col0_S1_L049_I1_001.fastq.gz"])
    assert "at most 96" in _refusal("s3://lab-data/run42/", client).detail


# --------------------------------------------------------------------------- #
# Friendlier refusals
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    ["https://lab-data.s3.us-west-2.amazonaws.com/run42/", "S3://lab-data/run42/"],
)
def test_a_web_address_or_upper_case_scheme_says_which_form_to_use(url):
    with pytest.raises(HTTPException) as exc:
        s3_folder.normalise_url(url)
    assert (
        exc.value.status_code == 422 and "s3://bucket/folder/ form" in exc.value.detail
    )


@pytest.mark.parametrize("bucket", ["a..b", "192.168.1.1"])
def test_a_bucket_name_s3_cant_have_is_refused_before_listing(bucket):
    with pytest.raises(HTTPException) as exc:
        s3_folder.normalise_url(f"s3://{bucket}/run42/")
    assert (
        exc.value.status_code == 422
        and "isn't a valid S3 bucket name" in exc.value.detail
    )


def test_s3s_invalid_bucket_name_is_a_refusal_not_a_502():
    body = "<Error><Code>InvalidBucketName</Code></Error>"
    err = _refusal(
        "s3://lab-data/run42/", _client(lambda r: httpx.Response(400, text=body))
    )
    assert err.status_code == 422 and "isn't a valid S3 bucket name" in err.detail


@pytest.mark.parametrize("status", [301, 307, 400])
def test_each_kind_of_region_redirect_is_followed(status):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "s3.amazonaws.com":
            return httpx.Response(status, headers={"x-amz-bucket-region": "us-west-2"})
        return httpx.Response(200, text=_listing("run42/", [R1, R2]))

    s3_folder.check_folder("s3://lab-data/run42/", _client(handler))
    assert seen == [
        "s3.amazonaws.com",
        "s3.us-west-2.amazonaws.com",
        "s3.us-west-2.amazonaws.com",
    ]


def test_a_second_redirect_is_not_followed():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(301, headers={"x-amz-bucket-region": "us-west-2"})

    err = _refusal("s3://lab-data/run42/", _client(handler))
    assert err.status_code == 502 and len(seen) == 2


@pytest.mark.parametrize(
    "region", ["evil.example/#", "us-west-2.evil", "", "US-WEST-2"]
)
def test_a_region_s3_wouldnt_send_is_not_followed(region):
    seen = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(301, headers={"x-amz-bucket-region": region})

    err = _refusal("s3://lab-data/run42/", _client(handler))
    assert err.status_code == 502 and seen == ["s3.amazonaws.com"]


def test_a_listing_that_isnt_xml_is_a_502():
    err = _refusal(
        "s3://lab-data/run42/", _client(lambda r: httpx.Response(200, text="not xml"))
    )
    assert err.status_code == 502 and "can't read" in err.detail


def _listing_with(prefix, items):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<Prefix>{prefix}</Prefix><IsTruncated>false</IsTruncated>{items}</ListBucketResult>"
    )


def _serving_items(items):
    return _client(lambda r: httpx.Response(200, text=_listing_with("run42/", items)))


def _item(name, size="100", etag="&quot;e&quot;", storage=None):
    parts = [f"<Key>run42/{name}</Key>"]
    if size is not None:
        parts.append(f"<Size>{size}</Size>")
    if etag is not None:
        parts.append(f"<ETag>{etag}</ETag>")
    if storage:
        parts.append(f"<StorageClass>{storage}</StorageClass>")
    return "<Contents>" + "".join(parts) + "</Contents>"


@pytest.mark.parametrize("etag", [None, "", "x" * 201])
def test_a_file_without_a_usable_etag_is_a_502(etag):
    client = _serving_items(_item(R1) + _item(R2, etag=etag))
    assert _refusal("s3://lab-data/run42/", client).status_code == 502


@pytest.mark.parametrize("size", [None, "", "1.5", "lots"])
def test_a_file_without_a_whole_size_is_a_502(size):
    client = _serving_items(_item(R1) + _item(R2, size=size))
    err = _refusal("s3://lab-data/run42/", client)
    assert err.status_code == 502 and "without a size" in err.detail


def test_empty_fastqs_are_refused():
    client = _serving_items(_item(R1) + _item(R2, size="0"))
    err = _refusal("s3://lab-data/run42/", client)
    assert err.status_code == 422 and "empty" in err.detail and R2 in err.detail


@pytest.mark.parametrize("storage", ["GLACIER", "DEEP_ARCHIVE"])
def test_archived_fastqs_are_refused(storage):
    client = _serving_items(_item(R1) + _item(R2, storage=storage))
    err = _refusal("s3://lab-data/run42/", client)
    assert err.status_code == 422 and "restored" in err.detail and R2 in err.detail


def test_instantly_readable_storage_classes_are_accepted():
    client = _serving_items(
        _item(R1, storage="STANDARD_IA") + _item(R2, storage="GLACIER_IR")
    )
    assert s3_folder.check_folder("s3://lab-data/run42/", client)["file_count"] == 2


@pytest.mark.parametrize(
    "other",
    [
        "col0_S1_L002_R1_001.fq.gz",
        "col0_S1_L002_R1_001.fq",
        "col0_S1_L002_R1_001.FASTQ.GZ",
    ],
)
def test_fastqs_with_another_extension_are_named_not_dropped(other):
    client, _ = _serving([R1, R2, other])
    err = _refusal("s3://lab-data/run42/", client)
    assert err.status_code == 422 and other in err.detail


def test_at_most_five_misnamed_files_are_shown():
    names = [f"reads_{n}.fastq.gz" for n in range(7)]
    client, _ = _serving(names)
    detail = _refusal("s3://lab-data/run42/", client).detail
    assert sum(name in detail for name in names) == 5


def test_no_fastqs_names_the_subfolders_that_might_hold_them():
    items = (
        "<CommonPrefixes><Prefix>run42/sampleA/</Prefix></CommonPrefixes>"
        "<CommonPrefixes><Prefix>run42/sampleB/</Prefix></CommonPrefixes>"
    )
    detail = _refusal("s3://lab-data/run42/", _serving_items(items)).detail
    assert "No FASTQs directly in" in detail and "sampleA/, sampleB/" in detail


def test_undetermined_reads_beside_a_sample_are_explained():
    client, _ = _serving(
        [
            R1,
            R2,
            "Undetermined_S0_L001_R1_001.fastq.gz",
            "Undetermined_S0_L001_R2_001.fastq.gz",
        ]
    )
    detail = _refusal("s3://lab-data/run42/", client).detail
    assert "Undetermined, col0" in detail and "couldn't assign" in detail
