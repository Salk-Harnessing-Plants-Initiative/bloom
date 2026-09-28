"""Unit tests for scrna_s3 against an in-memory bucket that behaves like
list_objects_v2 with Prefix and Delimiter."""

import pytest
from fastapi import HTTPException

import scrna_s3


class FakeS3:
    """In-memory bucket: `objects` maps key -> size. `page_size` forces pagination."""

    def __init__(self, objects: dict[str, int], page_size: int = 1000):
        self.objects = objects
        self.page_size = page_size
        self.calls = 0

    def _list(self, Prefix="", Delimiter=None, MaxKeys=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        contents, prefixes = [], []
        for key in keys:
            rest = key[len(Prefix) :]
            if Delimiter and Delimiter in rest:
                folder = Prefix + rest.split(Delimiter, 1)[0] + Delimiter
                if folder not in prefixes:
                    prefixes.append(folder)
            else:
                contents.append({"Key": key, "Size": self.objects[key]})
        if MaxKeys is not None:
            contents = contents[:MaxKeys]
        return contents, prefixes

    def list_objects_v2(self, Bucket, Prefix="", Delimiter=None, MaxKeys=None):
        self.calls += 1
        contents, prefixes = self._list(Prefix, Delimiter, MaxKeys)
        return {
            "Contents": contents,
            "CommonPrefixes": [{"Prefix": p} for p in prefixes],
        }

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        fake = self

        class _Paginator:
            def paginate(self, Bucket, Prefix="", Delimiter=None):
                fake.calls += 1
                contents, prefixes = fake._list(Prefix, Delimiter)
                entries = [("c", c) for c in contents] + [("p", p) for p in prefixes]
                for i in range(0, max(len(entries), 1), fake.page_size):
                    chunk = entries[i : i + fake.page_size]
                    yield {
                        "Contents": [e for kind, e in chunk if kind == "c"],
                        "CommonPrefixes": [
                            {"Prefix": e} for kind, e in chunk if kind == "p"
                        ],
                    }

        return _Paginator()


BUCKET = {
    "raw_reads/root_b/root_b_S1_L001_R1_001.fastq.gz": 100,
    "raw_reads/root_b/root_b_S1_L001_R2_001.fastq.gz": 250,
    "raw_reads/root_b/md5sums.txt": 5,
    "raw_reads/root_b/old/root_b_S1_L001_R1_001.fastq.gz": 999,
    "raw_reads/root_a/root_a_S1_L001_R1_001.fastq.gz": 10,
    "raw_reads/empty/readme.txt": 1,
    "reference_genome/tiny_ref/reference.json": 391,
    "reference_genome/tiny_ref/star/SA": 5000,
    "reference_genome/partial/fasta/genome.fa": 10,
    "reference_genome/lookalike/reference.json.bak": 1,
}


def test_sample_folders_are_first_level_and_sorted():
    names, truncated = scrna_s3.list_sample_folders(FakeS3(BUCKET), limit=10)
    assert names == ["empty", "root_a", "root_b"]
    assert truncated is False


def test_sample_folder_listing_reports_truncation():
    names, truncated = scrna_s3.list_sample_folders(
        FakeS3(BUCKET, page_size=1), limit=2
    )
    assert names == ["empty", "root_a"]
    assert truncated is True


def test_reference_folders_include_folders_without_marker():
    names, _ = scrna_s3.list_reference_folders(FakeS3(BUCKET), limit=10)
    assert names == ["lookalike", "partial", "tiny_ref"]


def test_sample_fastqs_counts_only_fastq_gz_directly_in_the_folder():
    count, size = scrna_s3.sample_fastqs(FakeS3(BUCKET), "root_b")
    assert (count, size) == (2, 350)


def test_sample_fastqs_of_a_folder_without_fastqs_is_zero():
    assert scrna_s3.sample_fastqs(FakeS3(BUCKET), "empty") == (0, 0)


def test_sample_fastqs_of_a_missing_folder_is_zero():
    assert scrna_s3.sample_fastqs(FakeS3(BUCKET), "nope") == (0, 0)


def test_sample_prefix_does_not_match_a_longer_folder_name():
    bucket = {"raw_reads/root_a_extra/x_S1_L001_R1_001.fastq.gz": 7}
    assert scrna_s3.sample_fastqs(FakeS3(bucket), "root_a") == (0, 0)


@pytest.mark.parametrize(
    "name, expected",
    [("tiny_ref", True), ("partial", False), ("lookalike", False), ("absent", False)],
)
def test_reference_exists_requires_exact_reference_json(name, expected):
    assert scrna_s3.reference_exists(FakeS3(BUCKET), name) is expected


def test_client_without_credentials_is_a_configuration_error(monkeypatch):
    monkeypatch.setattr(scrna_s3, "ACCESS_KEY_ID", None)
    monkeypatch.setattr(scrna_s3, "SECRET_ACCESS_KEY", "x")
    with pytest.raises(HTTPException) as exc:
        scrna_s3.client()
    assert exc.value.status_code == 500
    assert "WORKFLOWS_SCRNA_S3_ACCESS_KEY_ID" in exc.value.detail


def test_client_with_credentials_uses_the_configured_region(monkeypatch):
    monkeypatch.setattr(scrna_s3, "ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setattr(scrna_s3, "SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(scrna_s3, "REGION", "us-west-2")
    s3 = scrna_s3.client()
    assert s3.meta.region_name == "us-west-2"
