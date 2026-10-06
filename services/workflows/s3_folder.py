"""
Check an S3 folder of FASTQs before a Cell Ranger run is started on it.

The folder is listed anonymously; when that's refused and Bloom's read-only reader is
configured, it's listed again signed as the reader, for a folder a scientist shared with it.
Otherwise it must be public. It must hold one sample's
FASTQs directly inside it, named the Illumina way (`<sample>_S<n>_L<lane>_<R1|R2|I1|I2>_001
.fastq[.gz]`) with an R1 and an R2 in every lane. Other files and subfolders are ignored.
The result names the sample and lists each file's name, size and ETag, which the run
records so its stage step copies exactly these files.

Only the bucket and folder of the URL reach the request, and always on AWS's own S3
address, so a URL can't make this service call another host.
"""

import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from fastapi import HTTPException

# Bloom's read-only reader (bloom-fastq-reader), for a folder a scientist shared with it. Unset,
# only folders anyone can read work. Never logged.
READER_KEY_ID = os.environ.get("WORKFLOWS_S3_READER_ACCESS_KEY_ID") or None
READER_SECRET = os.environ.get("WORKFLOWS_S3_READER_SECRET_ACCESS_KEY") or None

# The same rules the database checks.
URL_RULE = re.compile(
    r"^s3://(?P<bucket>[a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/(?P<prefix>(?:[A-Za-z0-9!_.*'()-]+/)+)$"
)
DOT_SEGMENT = re.compile(r"/\.{1,2}/")
MAX_URL = 1024
FASTQ_RULE = re.compile(
    r"^(?P<sample>.+)_S(?P<number>[0-9]+)_L(?P<lane>[0-9]{3})_(?P<read>R1|R2|I1|I2)_001\.fastq(?:\.gz)?$"
)
# Allowed sample names ('__' separates run_key parts); also Cell Ranger's run id, which
# allows no '.' and at most 64 characters.
SAMPLE_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SAMPLE_HELP = (
    "letters, digits, '_' or '-', starting with a letter or digit, "
    "with no '__' (at most 64)"
)
MAX_FILES = 96
MAX_ETAG = 200
# One listing page, files and subfolders together; a sample's folder holds far fewer.
MAX_KEYS = 1000
# Anything named like a FASTQ, so a .fq or upper-case file is named in the refusal, not dropped.
FASTQ_LIKE = re.compile(r"\.(?:fastq|fq)(?:\.gz)?$", re.IGNORECASE)
# Objects S3 lists but can't hand out until they're restored.
ARCHIVED = {"GLACIER", "DEEP_ARCHIVE"}
# bcl2fastq and bcl-convert write the reads they couldn't assign to a sample under this name.
UNDETERMINED = "Undetermined"
NAME_EXAMPLE = "col0_S1_L001_R1_001.fastq.gz"
TIMEOUT = httpx.Timeout(10.0, connect=3.0)
S3_HOST = "https://s3.amazonaws.com"
# The region a request to S3_HOST is signed for, until S3 names the bucket's own.
DEFAULT_REGION = "us-east-1"
# What S3 sends in x-amz-bucket-region, e.g. us-west-2 or ap-southeast-1.
REGION_RULE = re.compile(r"^[a-z]{2}(?:-[a-z]+)+-[0-9]$")
IP_LIKE = re.compile(r"^[0-9]+(?:\.[0-9]+){3}$")
URL_HELP = (
    "fastq_url must be an S3 folder like s3://bucket/folder/: a lower-case bucket, "
    "then a folder of letters, digits and !_.*'()- with no spaces"
)
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def normalise_url(url) -> tuple[str, str, str]:
    """(bucket, folder prefix, URL with a trailing '/'), or a 422."""
    if not isinstance(url, str) or not url.strip():
        raise _refuse("fastq_url must be an S3 folder like s3://bucket/folder/")
    url = url.strip()
    if url.lower().startswith(("http://", "https://")) or url.startswith("S3://"):
        raise _refuse(
            f"{URL_HELP}. Use the s3://bucket/folder/ form, not a web address"
        )
    if not url.endswith("/"):
        url += "/"
    match = URL_RULE.fullmatch(url)
    if len(url) > MAX_URL or match is None or DOT_SEGMENT.search(url):
        raise _refuse(URL_HELP)
    if ".." in match["bucket"] or IP_LIKE.fullmatch(match["bucket"]):
        raise _refuse(f"{match['bucket']} isn't a valid S3 bucket name")
    return match["bucket"], match["prefix"], url


def _list_url(host: str, bucket: str, prefix: str) -> str:
    return (
        f"{host}/{quote(bucket)}?list-type=2&delimiter=%2F&max-keys={MAX_KEYS}"
        f"&prefix={quote(prefix, safe='')}"
    )


def reader_configured() -> bool:
    return bool(READER_KEY_ID and READER_SECRET)


def _reader_headers(url: str, region: str) -> dict[str, str]:
    """A GET of `url` signed as Bloom's reader (AWS Signature V4)."""
    request = AWSRequest(method="GET", url=url)
    S3SigV4Auth(Credentials(READER_KEY_ID, READER_SECRET), "s3", region).add_auth(
        request
    )
    return dict(request.headers.items())


def _get(client: httpx.Client, url: str, signed: bool, region: str) -> httpx.Response:
    return client.get(url, headers=_reader_headers(url, region) if signed else None)


def _fetch(
    bucket: str,
    prefix: str,
    client: httpx.Client,
    signed: bool,
    region: str | None = None,
) -> tuple[httpx.Response, str | None]:
    """One listing, anonymous or as the reader, following S3's redirect to the bucket's own
    region once. Returns the response and the bucket's region, when S3 named it."""
    host = S3_HOST if region is None else f"https://s3.{region}.amazonaws.com"
    resp = _get(
        client, _list_url(host, bucket, prefix), signed, region or DEFAULT_REGION
    )
    named = resp.headers.get("x-amz-bucket-region")
    if named and not REGION_RULE.fullmatch(named):
        raise HTTPException(
            status_code=502, detail=f"S3 named an unknown region for {bucket}"
        )
    if resp.status_code in (301, 307, 400) and named and named != region:
        resp = _get(
            client,
            _list_url(f"https://s3.{named}.amazonaws.com", bucket, prefix),
            signed,
            named,
        )
    return resp, named or region


def _list(bucket: str, prefix: str, client: httpx.Client) -> ET.Element:
    """The folder's listing: anonymous first, then as Bloom's reader if that's refused and the
    reader is configured."""
    resp, region = _fetch(bucket, prefix, client, signed=False)
    if resp.status_code in (401, 403) and reader_configured():
        resp, _ = _fetch(bucket, prefix, client, signed=True, region=region)
    if resp.status_code == 400 and b"InvalidBucketName" in resp.content:
        raise _refuse(f"{bucket} isn't a valid S3 bucket name")
    if resp.status_code == 404:
        raise _refuse(f"Bucket {bucket} doesn't exist")
    if resp.status_code in (401, 403):
        if reader_configured():
            raise _refuse(
                f"Bloom can't read s3://{bucket}/{prefix}; make the folder public, or share it "
                "with Bloom's reader (the ? next to the folder field says how)"
            )
        raise _refuse(
            f"Bloom can't read s3://{bucket}/{prefix}; the folder must be public "
            "(anyone can list it and read its files)"
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"S3 answered {resp.status_code} when listing the folder",
        )
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise HTTPException(
            status_code=502, detail="S3 sent a listing Bloom can't read"
        ) from exc


def check_folder(url, client: httpx.Client | None = None) -> dict:
    """The folder's sample, lanes and FASTQs, or a 422 saying what's wrong with it."""
    bucket, prefix, url = normalise_url(url)
    try:
        if client is None:
            with httpx.Client(timeout=TIMEOUT) as own:
                root = _list(bucket, prefix, own)
        else:
            root = _list(bucket, prefix, client)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't reach S3: {exc}"
        ) from exc

    if (root.findtext(f"{_NS}IsTruncated") or "").lower() == "true":
        raise _refuse(
            f"This folder holds over {MAX_KEYS} files and subfolders; point at one "
            f"sample's folder, e.g. s3://{bucket}/<sample>/"
        )

    files, misnamed, empty, archived = [], [], [], []
    for item in root.iter(f"{_NS}Contents"):
        name = (item.findtext(f"{_NS}Key") or "")[len(prefix) :]
        if "/" in name or not FASTQ_LIKE.search(name):
            continue
        if not FASTQ_RULE.fullmatch(name):
            misnamed.append(name)
            continue
        try:
            size = int(item.findtext(f"{_NS}Size"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=502, detail=f"S3 listed {name} without a size"
            ) from exc
        if size == 0:
            empty.append(name)
        if item.findtext(f"{_NS}StorageClass") in ARCHIVED:
            archived.append(name)
        files.append(
            {"name": name, "size": size, "etag": item.findtext(f"{_NS}ETag") or ""}
        )

    if misnamed:
        shown = ", ".join(sorted(misnamed)[:5])
        raise _refuse(
            f"FASTQs must be named the Illumina way, like {NAME_EXAMPLE} (.fastq or "
            f".fastq.gz); these aren't: {shown}"
        )
    if not files:
        subfolders = sorted(
            (p.findtext(f"{_NS}Prefix") or "")[len(prefix) :]
            for p in root.iter(f"{_NS}CommonPrefixes")
        )
        hint = (
            "; FASTQs in subfolders aren't counted, so point at the folder that holds them "
            "(this one has " + ", ".join(subfolders[:5]) + ")"
            if subfolders
            else ""
        )
        raise _refuse(f"No FASTQs directly in s3://{bucket}/{prefix}{hint}")
    if empty:
        raise _refuse(
            "These FASTQs are empty (0 bytes): " + ", ".join(sorted(empty)[:5])
        )
    if archived:
        raise _refuse(
            "These FASTQs are archived in Glacier and must be restored before a run can read "
            "them: " + ", ".join(sorted(archived)[:5])
        )
    if len(files) > MAX_FILES:
        raise _refuse(
            f"A run takes at most {MAX_FILES} FASTQs; this folder holds {len(files)}"
        )

    samples = {FASTQ_RULE.fullmatch(f["name"])["sample"] for f in files}
    if len(samples) > 1:
        undetermined = (
            f"; {UNDETERMINED} holds the reads the sequencer couldn't assign, so move it out "
            "too"
            if UNDETERMINED in samples
            else ""
        )
        raise _refuse(
            "A folder holds one sample; this one has "
            + ", ".join(sorted(samples))
            + undetermined
        )
    sample = samples.pop()
    if not SAMPLE_RULE.fullmatch(sample):
        raise _refuse(
            f"The FASTQs' sample name {sample!r} can't be a Cell Ranger run id: {SAMPLE_HELP}"
        )

    # One read listed both plain and gzipped (e.g. unzipped in place) would be counted twice.
    by_read: dict[str, list[str]] = {}
    for f in files:
        by_read.setdefault(f["name"].removesuffix(".gz"), []).append(f["name"])
    twice = sorted(read for read, names in by_read.items() if len(names) > 1)
    if twice:
        raise _refuse(
            "These reads are in the folder twice, as .fastq and .fastq.gz; remove one copy: "
            + ", ".join(twice[:5])
        )

    # Each S number's lanes are their own set of reads, so each needs its R1 and R2.
    reads: dict[tuple[str, str], set[str]] = {}
    for f in files:
        m = FASTQ_RULE.fullmatch(f["name"])
        reads.setdefault((m["number"], m["lane"]), set()).add(m["read"])
    incomplete = sorted(key for key, got in reads.items() if not {"R1", "R2"} <= got)
    if incomplete:
        raise _refuse(
            "Every lane needs an R1 and an R2; "
            + ", ".join(f"S{number} L{lane}" for number, lane in incomplete)
            + " doesn't have both"
        )
    if any(not 1 <= len(f["etag"]) <= MAX_ETAG for f in files):
        raise HTTPException(
            status_code=502, detail="S3 listed a file without a usable ETag"
        )

    files.sort(key=lambda f: f["name"])
    return {
        "fastq_url": url,
        "sample": sample,
        "lanes": sorted({int(lane) for _, lane in reads}),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(f["size"] for f in files),
    }
