"""Where a run's files live: a local folder (the run's shared folder) or an s3:// prefix."""

from __future__ import annotations

import shutil
from pathlib import Path


class Store:
    def __init__(self, root: str) -> None:
        self.root = root.rstrip("/")
        if self.root.startswith("s3://"):
            import boto3

            bucket, _, prefix = self.root[len("s3://") :].partition("/")
            self._bucket, self._prefix = bucket, prefix
            self._s3 = boto3.client("s3")
        else:
            self._s3 = None

    def _key(self, rel: str) -> str:
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def _local(self, rel: str) -> Path:
        return Path(self.root) / rel

    def uri(self, rel: str) -> str:
        return f"{self.root}/{rel}"

    def exists(self, rel: str) -> bool:
        if self._s3 is None:
            return self._local(rel).is_file()
        from botocore.exceptions import ClientError

        try:
            self._s3.head_object(Bucket=self._bucket, Key=self._key(rel))
            return True
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return False
            raise

    def get(self, rel: str, dest: Path) -> Path:
        """A local path to rel: the file itself in a local folder, else a download to dest."""
        if self._s3 is None:
            return self._local(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._s3.download_file(self._bucket, self._key(rel), str(dest))
        return dest

    def put(self, src: Path, rel: str) -> None:
        if self._s3 is None:
            self._local(rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, self._local(rel))
        else:
            self._s3.upload_file(str(src), self._bucket, self._key(rel))

    def read_text(self, rel: str) -> str:
        if self._s3 is None:
            return self._local(rel).read_text()
        return (
            self._s3.get_object(Bucket=self._bucket, Key=self._key(rel))["Body"]
            .read()
            .decode()
        )

    def write_text(self, rel: str, text: str) -> None:
        if self._s3 is None:
            self._local(rel).parent.mkdir(parents=True, exist_ok=True)
            self._local(rel).write_text(text)
        else:
            self._s3.put_object(
                Bucket=self._bucket, Key=self._key(rel), Body=text.encode()
            )

    def children(self, rel: str) -> list[str]:
        """The names of the folders directly under rel."""
        if self._s3 is None:
            folder = self._local(rel)
            return (
                sorted(p.name for p in folder.iterdir() if p.is_dir())
                if folder.is_dir()
                else []
            )
        names: set[str] = set()
        paginator = self._s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self._bucket, Prefix=self._key(rel) + "/", Delimiter="/"
        ):
            for common in page.get("CommonPrefixes", []):
                names.add(common["Prefix"].rstrip("/").rsplit("/", 1)[-1])
        return sorted(names)
