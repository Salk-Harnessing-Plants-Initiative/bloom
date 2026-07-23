"""bloommcp's raw-input data mount must be named for its purpose, not a tool.

`bloommcp/data/SLEAP_OUT_CSV` (env `BLOOM_TRAITS_DIR`) named a tool + file format
instead of purpose, and didn't match its own env var — unlike `PLOTS_DIR`/
`BLOOM_PLOTS_DIR` and `ANALYSIS_OUTPUT`/`BLOOM_OUTPUT_DIR`, which already matched.
Renamed to `TRAITS_DIR` (issue #477). These tests pin the rename in both compose
files, confirm the other two necessary mounts aren't accidentally dropped, and
fence against the old name silently reappearing anywhere it was removed from.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every file the SLEAP_OUT_CSV -> TRAITS_DIR rename touched that should NEVER legitimately
# reference the old name again. If the old string reappears in any of these, the rename was
# reverted or a stale reference crept back in (e.g. from a doc copy-paste) — this is the
# standing version of the one-time grep sweep done when the rename landed.
#
# Deliberately EXCLUDES files that must permanently keep referencing SLEAP_OUT_CSV as part of
# describing/implementing the legacy-host migration itself (not stale leftovers): .github/
# workflows/deploy.yml, scripts/migrate_bloommcp_legacy_traits_dir.sh,
# tests/unit/test_migrate_bloommcp_legacy_traits_dir.py, and PROD_SETUP.md (documents the
# migration step and the pre-existing-host remediation, both of which name the old directory
# on purpose).
RENAMED_FILES = [
    "docker-compose.dev.yml",
    "docker-compose.prod.yml",
    "bloommcp/Dockerfile",
    ".gitignore",
    "bloommcp/docs/storage-backends.md",
    "bloommcp/docs/local-validation.md",
    "_WIKI/BLOOMMCP/storage-workflow.md",
    "_WIKI/BLOOMMCP/README.md",
    "bloommcp/src/bloom_mcp/storage/analysis_dir.py",
    "bloommcp/tests/smoke/live_plot_tool_smoke.py",
    "scripts/ensure_bloommcp_data_dirs.sh",
    "DEV_SETUP.md",
    "openspec/project.md",
]


def _compose(filename: str) -> dict:
    return yaml.safe_load((REPO_ROOT / filename).read_text(encoding="utf-8"))


def test_traits_dir_name_matches_env_var_in_both_compose_files():
    for filename in ("docker-compose.dev.yml", "docker-compose.prod.yml"):
        bloommcp = _compose(filename)["services"]["bloommcp"]
        env = bloommcp["environment"]
        assert env["BLOOM_TRAITS_DIR"] == "/app/data/TRAITS_DIR", (
            f"{filename}: BLOOM_TRAITS_DIR must point at /app/data/TRAITS_DIR, "
            f"not a tool/file-format-named directory. Got {env.get('BLOOM_TRAITS_DIR')!r}"
        )
        volumes = bloommcp.get("volumes", [])
        assert any(
            isinstance(v, str)
            and v.rstrip("\\").rstrip("/").endswith("TRAITS_DIR:/app/data/TRAITS_DIR")
            for v in volumes
        ), (
            f"{filename}: expected a bind-mount ending in "
            f".../TRAITS_DIR:/app/data/TRAITS_DIR among bloommcp's volumes, got {volumes!r}"
        )


def test_prod_compose_keeps_all_three_bloommcp_data_mounts():
    """The three prod/staging data mounts back reachable code paths (raw-input
    fallback + provenance hashing + phenotyping_segmentation demo tools +
    plotting) — none may be silently dropped without a corresponding spec
    change. See the bloommcp-deployment-data-mounts capability."""
    volumes = _compose("docker-compose.prod.yml")["services"]["bloommcp"].get("volumes", [])
    joined = "\n".join(v for v in volumes if isinstance(v, str))
    for target in ("TRAITS_DIR", "ANALYSIS_OUTPUT", "PLOTS_DIR"):
        assert target in joined, (
            f"docker-compose.prod.yml: bloommcp's volumes must still mount a "
            f"directory for {target} — see the necessity investigation in "
            f"openspec/changes/rename-bloommcp-sleap-out-csv-dir/proposal.md"
        )


def test_no_stale_sleap_out_csv_references():
    for filename in RENAMED_FILES:
        text = (REPO_ROOT / filename).read_text(encoding="utf-8")
        assert "SLEAP_OUT_CSV" not in text, (
            f"{filename}: stale SLEAP_OUT_CSV reference — this directory was "
            f"renamed to TRAITS_DIR (issue #477)."
        )
