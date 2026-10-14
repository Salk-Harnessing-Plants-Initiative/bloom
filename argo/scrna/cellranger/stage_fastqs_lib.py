#!/usr/bin/env python3
"""The parts of stage-fastqs that read JSON: the run's recorded FASTQs (FASTQ_FILES) and the S3
folder's listing. Installed in the image as `stage-fastqs-lib`.

  stage-fastqs-lib check-files   FASTQ_FILES is a list of {name, size, etag}, all for SAMPLE.
                                 Exit 2 if it isn't a usable list, exit 3 (naming the other
                                 samples) if a FASTQ is named for another sample.
  stage-fastqs-lib fingerprint   One line naming FASTQ_URL and a hash of FASTQ_FILES.
  stage-fastqs-lib compare P     Compares the listing on stdin (list-objects-v2 JSON for prefix
                                 P) with FASTQ_FILES. Prints match, empty, partial: <missing>
                                 or changed: <reason>.
  stage-fastqs-lib rows          name, size and ETag of each recorded file, tab-separated.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

FASTQ_RULE = re.compile(r"^(.+)_S[0-9]+_L[0-9]{3}_(R1|R2|I1|I2)_001\.fastq(\.gz)?$")
EXIT_BAD_LIST = 2
EXIT_OTHER_SAMPLE = 3
FIELDS = ("name", "size", "etag")


def recorded() -> list[dict]:
    return json.loads(os.environ.get("FASTQ_FILES") or "null")


def bad_list(files) -> str | None:
    """Why FASTQ_FILES can't be used, or None."""
    if not isinstance(files, list) or not files:
        return "FASTQ_FILES must be the run's non-empty list of files"
    for f in files:
        if not isinstance(f, dict) or set(f) != set(FIELDS):
            return f"each file must be an object of exactly {', '.join(FIELDS)}: {f!r}"
        if not isinstance(f["name"], str) or "/" in f["name"] or not f["name"]:
            return f"a file's name must be a plain file name: {f['name']!r}"
        if not isinstance(f["size"], int) or isinstance(f["size"], bool) or f["size"] < 0:
            return f"{f['name']}'s size must be a whole number of bytes: {f['size']!r}"
        if not isinstance(f["etag"], str) or not f["etag"]:
            return f"{f['name']}'s ETag must be a non-empty string"
    return None


def other_samples(files, sample: str) -> list[str]:
    found = {m.group(1) for m in map(FASTQ_RULE.match, (f["name"] for f in files)) if m}
    return sorted(found - {sample})


def compare(listing: dict, prefix: str, files: list[dict]) -> str:
    if listing.get("IsTruncated"):
        return "changed: the folder now holds over 1000 files"
    expected = {f["name"]: f for f in files}
    found = {}
    for obj in listing.get("Contents") or []:
        name = obj["Key"][len(prefix):]
        if name.endswith((".fastq", ".fastq.gz")) and "/" not in name:
            found[name] = obj
    if not found:
        return "empty"
    for name, obj in sorted(found.items()):
        want = expected.get(name)
        if want is None:
            return f"changed: {name} was added"
        if obj["Size"] != want["size"]:
            return f"changed: {name} is {obj['Size']} bytes, not {want['size']}"
        if obj["ETag"] != want["etag"]:
            return f"changed: {name} was replaced (its ETag differs)"
    missing = sorted(set(expected) - set(found))
    return "partial: " + ", ".join(missing) if missing else "match"


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "check-files":
        try:
            files = recorded()
        except json.JSONDecodeError:
            files = None
        problem = bad_list(files)
        if problem:
            print(problem)
            return EXIT_BAD_LIST
        others = other_samples(files, os.environ.get("SAMPLE", ""))
        if others:
            print(", ".join(others))
            return EXIT_OTHER_SAMPLE
        return 0
    if command == "fingerprint":
        files = os.environ.get("FASTQ_FILES", "")
        print("folder", os.environ.get("FASTQ_URL", ""), hashlib.sha256(files.encode()).hexdigest())
        return 0
    if command == "compare" and len(argv) == 3:
        text = sys.stdin.read().strip()
        listing = json.loads(text) if text else {}
        print(compare(listing or {}, argv[2], recorded()))
        return 0
    if command == "rows":
        for f in recorded():
            print(f"{f['name']}\t{f['size']}\t{f['etag']}")
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv))
