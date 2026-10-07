"""fetch-published <s3://bucket/runs_output/<run>> <rel> <dest>: copy one published file to dest.

Exits 6 when the file isn't there. Writes beside dest first, so a cut-off copy is never dest.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from .store import Store

EXIT_NOT_PUBLISHED = 6


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    root, rel, dest = args
    store = Store(root)
    if not store.exists(rel):
        return EXIT_NOT_PUBLISHED
    target = Path(dest)
    partial = target.with_name(f".{target.name}.part")
    target.parent.mkdir(parents=True, exist_ok=True)
    source = store.get(rel, partial)
    if source != partial:  # a local root hands back the file itself
        shutil.copyfile(source, partial)
    partial.replace(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
