"""Fail a PR that ships a migration change alongside files outside the migration surface.

A migration change is a migration file whose content is not already on staging (see
migration_changes.py), so a promotion from staging passes and a hotfix to main is checked.

Usage:
  lint_migration_isolation.py BASE [--head HEAD] [--staging-ref REF] [--repo DIR]

Exit codes:
  0  no migration change, or every other changed file is in the migration surface
  1  a migration change ships with files outside the surface
  2  a git ref cannot be resolved
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from migration_changes import RefError, changed_paths, migration_changes  # noqa: E402

SURFACE_PREFIXES = (
    "supabase/migrations/",
    "supabase/rollbacks/",
    "supabase/grants/",
    "tests/integration/",
    "tests/unit/",
    "_WIKI/",
)
# What `make gen-types` writes, plus the hand-kept web/types copy.
TYPE_FILES = (
    "web/lib/database.types.ts",
    "web/types/database.types.ts",
    "packages/bloom-js/src/types/database.types.ts",
    "packages/bloom-fs/src/types/database.types.ts",
    "packages/bloom-nextjs-auth/src/lib/database.types.ts",
)
NOT_ISOLATED = "::error title=Migration change not isolated::"


def in_surface(path: str) -> bool:
    return path.startswith(SURFACE_PREFIXES) or path in TYPE_FILES or path.lower().endswith(".md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("base", help="the PR's base commit")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--staging-ref", default="origin/staging")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args(argv)
    repo = Path(args.repo)

    try:
        migrations = migration_changes(args.base, args.head, args.staging_ref, repo)
        if not migrations:
            print("Migration-isolation check passed (no migration change).")
            return 0
        outside = [p for p in changed_paths(args.base, args.head, repo) if not in_surface(p)]
    except RefError as exc:
        print(f"::error title=lint_migration_isolation: cannot resolve ref::{exc}")
        return 2

    if outside:
        print(
            f"{NOT_ISOLATED}This PR changes migrations ({', '.join(migrations)}) and files outside "
            "the migration surface. Schema changes ship in their own PR. Offending files:"
        )
        for path in outside:
            print(f"::error::  {path}")
        print(
            "Allowed alongside a migration: supabase/migrations, supabase/rollbacks, "
            "supabase/grants, tests/integration, tests/unit, the generated database types, "
            "_WIKI and Markdown files."
        )
        return 1

    print(f"Migration-isolation check passed ({len(migrations)} migration change(s), isolated).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
