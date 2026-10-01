"""
Check an S3 folder of FASTQs before a Cell Ranger run is started on it.

The folder is listed once, unsigned, so it must be public. It must hold one sample's
FASTQs directly inside it, named the Illumina way (`<sample>_S<n>_L<lane>_<R1|R2|I1|I2>_001
.fastq[.gz]`) with an R1 and an R2 in every lane. Other files and subfolders are ignored.
The result names the sample and lists each file's name, size and ETag, which the run
records so its stage step copies exactly these files.

Only the bucket and folder of the URL reach the request, and always on AWS's own S3
address, so a URL can't make this service call another host.
"""

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import httpx
from fastapi import HTTPException

# The same rules the database checks.
URL_RULE = re.compile(
    r"^s3://(?P<bucket>[a-z0-9][a-z0-9.-]{1,61}[a-z0-9])/(?P<prefix>(?:[A-Za-z0-9!_.*'()-]+/)+)$"
)
DOT_SEGMENT = re.compile(r"/\.{1,2}/")
MAX_URL = 1024
FASTQ_RULE = re.compile(
    r"^(?P<sample>.+)_S[0-9]+_L(?P<lane>[0-9]{3})_(?P<read>R1|R2|I1|I2)_001\.fastq(?:\.gz)?$"
)
SAMPLE_RULE = re.compile(r"^(?!.*__)[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_FILES = 96
# One listing page; a sample's folder holds far fewer.
MAX_KEYS = 1000
NAME_EXAMPLE = "col0_S1_L001_R1_001.fastq.gz"
TIMEOUT_SECONDS = 10.0
S3_HOST = "https://s3.amazonaws.com"
_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def _refuse(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def normalise_url(url) -> tuple[str, str, str]:
    """(bucket, folder prefix, URL with a trailing '/'), or a 422."""
    if not isinstance(url, str) or not url.strip():
        raise _refuse("fastq_url must be an S3 folder like s3://bucket/folder/")
    url = url.strip()
    if not url.endswith("/"):
        url += "/"
    match = URL_RULE.fullmatch(url)
    if len(url) > MAX_URL or match is None or DOT_SEGMENT.search(url):
        raise _refuse(
            "fastq_url must be an S3 folder like s3://bucket/folder/: a lower-case bucket, "
            "then a folder of letters, digits and !_.*'()- with no spaces"
        )
    return match["bucket"], match["prefix"], url


def _list_url(host: str, bucket: str, prefix: str) -> str:
    return (
        f"{host}/{quote(bucket)}?list-type=2&delimiter=%2F&max-keys={MAX_KEYS}"
        f"&prefix={quote(prefix, safe='')}"
    )


def _list(bucket: str, prefix: str, client: httpx.Client) -> ET.Element:
    """The folder's listing. Follows S3's redirect to the bucket's own region once."""
    resp = client.get(_list_url(S3_HOST, bucket, prefix))
    region = resp.headers.get("x-amz-bucket-region")
    if resp.status_code in (301, 307, 400) and region:
        resp = client.get(
            _list_url(f"https://s3.{region}.amazonaws.com", bucket, prefix)
        )
    if resp.status_code == 404:
        raise _refuse(f"Bucket {bucket} doesn't exist")
    if resp.status_code in (401, 403):
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
            with httpx.Client(timeout=TIMEOUT_SECONDS) as own:
                root = _list(bucket, prefix, own)
        else:
            root = _list(bucket, prefix, client)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Couldn't reach S3: {exc}"
        ) from exc

    if (root.findtext(f"{_NS}IsTruncated") or "").lower() == "true":
        raise _refuse(
            f"This folder holds over {MAX_KEYS} files; point at one sample's folder, "
            f"e.g. s3://{bucket}/<sample>/"
        )

    files, misnamed = [], []
    for item in root.iter(f"{_NS}Contents"):
        name = (item.findtext(f"{_NS}Key") or "")[len(prefix) :]
        if "/" in name or not name.endswith((".fastq", ".fastq.gz")):
            continue
        if not FASTQ_RULE.fullmatch(name):
            misnamed.append(name)
            continue
        files.append(
            {
                "name": name,
                "size": int(item.findtext(f"{_NS}Size") or 0),
                "etag": item.findtext(f"{_NS}ETag") or "",
            }
        )

    if misnamed:
        shown = ", ".join(sorted(misnamed)[:5])
        raise _refuse(
            f"FASTQs must be named the Illumina way, like {NAME_EXAMPLE}; these aren't: {shown}"
        )
    if not files:
        raise _refuse(f"No FASTQs directly in s3://{bucket}/{prefix}")
    if len(files) > MAX_FILES:
        raise _refuse(
            f"A run takes at most {MAX_FILES} FASTQs; this folder holds {len(files)}"
        )

    samples = {FASTQ_RULE.fullmatch(f["name"])["sample"] for f in files}
    if len(samples) > 1:
        raise _refuse(
            "A folder holds one sample; this one has " + ", ".join(sorted(samples))
        )
    sample = samples.pop()
    if not SAMPLE_RULE.fullmatch(sample):
        raise _refuse(
            f"The FASTQs' sample name {sample!r} can't be a Cell Ranger run id: letters, "
            "digits, '_' or '-', starting with a letter or digit, no '__', at most 64"
        )

    reads: dict[str, set[str]] = {}
    for f in files:
        m = FASTQ_RULE.fullmatch(f["name"])
        reads.setdefault(m["lane"], set()).add(m["read"])
    incomplete = sorted(lane for lane, got in reads.items() if not {"R1", "R2"} <= got)
    if incomplete:
        raise _refuse(
            "Every lane needs an R1 and an R2; lane "
            + ", ".join(f"L{lane}" for lane in incomplete)
            + " doesn't have both"
        )
    if any(not f["etag"] for f in files):
        raise HTTPException(status_code=502, detail="S3 listed a file without an ETag")

    files.sort(key=lambda f: f["name"])
    return {
        "fastq_url": url,
        "sample": sample,
        "lanes": sorted(int(lane) for lane in reads),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(f["size"] for f in files),
    }
