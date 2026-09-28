"""
Listing access to the scRNA workflows bucket: sample folders under raw_reads/,
their FASTQ files, and references under reference_genome/ that have reference.json.
Every call is a listing (s3:ListBucket); no object is read.
"""

import os

from fastapi import HTTPException

BUCKET = os.environ.get("WORKFLOWS_SCRNA_S3_BUCKET", "bloomv2-workflows")
REGION = os.environ.get("WORKFLOWS_SCRNA_S3_REGION", "us-west-2")
ACCESS_KEY_ID = os.environ.get("WORKFLOWS_SCRNA_S3_ACCESS_KEY_ID")
SECRET_ACCESS_KEY = os.environ.get("WORKFLOWS_SCRNA_S3_SECRET_ACCESS_KEY")

RAW_READS_PREFIX = "raw_reads/"
REFERENCE_PREFIX = "reference_genome/"
FASTQ_SUFFIX = ".fastq.gz"
REFERENCE_MARKER = "reference.json"


def client():
    """An S3 client for the bucket; 500 if the key is not configured."""
    missing = [
        name
        for name, val in [
            ("WORKFLOWS_SCRNA_S3_ACCESS_KEY_ID", ACCESS_KEY_ID),
            ("WORKFLOWS_SCRNA_S3_SECRET_ACCESS_KEY", SECRET_ACCESS_KEY),
        ]
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=f"workflows service not configured: missing {', '.join(missing)}",
        )

    import boto3

    return boto3.client(
        "s3",
        region_name=REGION,
        aws_access_key_id=ACCESS_KEY_ID,
        aws_secret_access_key=SECRET_ACCESS_KEY,
    )


def _folders(s3, prefix: str, limit: int) -> tuple[list[str], bool]:
    """First-level folder names under `prefix`, sorted, at most `limit`; and whether more exist."""
    names: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix, Delimiter="/"):
        for entry in page.get("CommonPrefixes", []):
            name = entry["Prefix"][len(prefix) :].rstrip("/")
            if name:
                names.append(name)
            if len(names) > limit:
                return sorted(names)[:limit], True
    return sorted(names), False


def list_sample_folders(s3, limit: int) -> tuple[list[str], bool]:
    return _folders(s3, RAW_READS_PREFIX, limit)


def list_reference_folders(s3, limit: int) -> tuple[list[str], bool]:
    return _folders(s3, REFERENCE_PREFIX, limit)


def sample_fastqs(s3, sample: str) -> tuple[int, int]:
    """(number of .fastq.gz files, their total bytes) directly inside raw_reads/<sample>/."""
    prefix = f"{RAW_READS_PREFIX}{sample}/"
    count = 0
    total = 0
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix, Delimiter="/"):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(FASTQ_SUFFIX):
                count += 1
                total += obj.get("Size", 0)
    return count, total


def reference_exists(s3, reference: str) -> bool:
    """Whether reference_genome/<reference>/reference.json exists (checked by listing)."""
    key = f"{REFERENCE_PREFIX}{reference}/{REFERENCE_MARKER}"
    res = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
    return any(obj["Key"] == key for obj in res.get("Contents", []))
