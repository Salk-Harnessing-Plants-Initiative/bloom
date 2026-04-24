"""Unit tests for scripts/verify_env_parity.py.

See openspec/changes/update-env-parity-check/specs/deploy-env-parity/spec.md
for the authoritative requirements and scenarios.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_env_parity.py"


def run(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(target)],
        capture_output=True,
        text=True,
    )


def write_deploy(tmp_path: Path, body: str) -> Path:
    f = tmp_path / "deploy.yml"
    f.write_text(body)
    return f


def prod_heredoc(body_lines: list[str], terminator: str = "SECRETS") -> str:
    """Emit the new-style secret-append block for .env.prod.

    Matches the shape produced by deploy.yml — a shell-var assignment that
    names the target env file, followed by a `cat >> "$f"` heredoc. The
    parity script resolves the env name by walking back from the heredoc
    to that assignment.
    """
    indent = "          "
    lines = [
        f'{indent}f="/opt/bloom/production/.env.prod"',
        f'{indent}cat >> "$f" << \'{terminator}\'',
    ]
    lines.extend(f"{indent}{l}" for l in body_lines)
    lines.append(f"{indent}{terminator}")
    return "\n".join(lines)


def staging_heredoc(body_lines: list[str], terminator: str = "SECRETS") -> str:
    indent = "          "
    lines = [
        f'{indent}f="/opt/bloom/staging/.env.staging"',
        f'{indent}cat >> "$f" << \'{terminator}\'',
    ]
    lines.extend(f"{indent}{l}" for l in body_lines)
    lines.append(f"{indent}{terminator}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1.4 Drift-guard (placed first so real-file drift surfaces immediately)
# ---------------------------------------------------------------------------

def test_real_deploy_yml_passes():
    real = REPO_ROOT / ".github" / "workflows" / "deploy.yml"
    result = run(real)
    assert result.returncode == 0, (
        f"Real deploy.yml failed parity check.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert re.search(
        r"OK — \d+ vars, \d+ secret refs, prod and staging aligned",
        result.stdout,
    ), f"Happy-path summary missing. stdout={result.stdout!r}"


# ---------------------------------------------------------------------------
# 1.2 Happy-path tests
# ---------------------------------------------------------------------------

def test_happy_path_single_ref_per_line(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "JWT_SECRET=${{ secrets.PROD_JWT_SECRET }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "JWT_SECRET=${{ secrets.STAGING_JWT_SECRET }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr
    assert re.search(
        r"OK — \d+ vars, \d+ secret refs, prod and staging aligned",
        result.stdout,
    )


def test_happy_path_composite_value(tmp_path):
    prod_url = (
        "LANGCHAIN_POSTGRES_URL=postgresql://"
        "${{ secrets.PROD_POSTGRES_USER }}:"
        "${{ secrets.PROD_POSTGRES_PASSWORD }}@"
        "${{ secrets.PROD_POSTGRES_HOST }}:"
        "${{ secrets.PROD_POSTGRES_PORT }}/"
        "${{ secrets.PROD_POSTGRES_DB }}"
    )
    staging_url = prod_url.replace("PROD_", "STAGING_")
    body = prod_heredoc([prod_url]) + "\n" + staging_heredoc([staging_url])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr
    m = re.search(r"OK — (\d+) vars, (\d+) secret refs", result.stdout)
    assert m is not None
    n_vars, m_refs = int(m.group(1)), int(m.group(2))
    # 1 var in each block (same name), 5 refs per block × 2 blocks = 10
    assert n_vars == 1
    assert m_refs == 10, f"Expected 10 refs (5 per block × 2), got {m_refs}"


def test_literal_port_line_passes(tmp_path):
    body = prod_heredoc([
        "POSTGRES_HOST_PORT=5432",
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_HOST_PORT=5433",
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_empty_rhs_line_passes(tmp_path):
    body = prod_heredoc([
        "CADDY_HTTP_PORT=",
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "CADDY_HTTP_PORT=",
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_lhs_suffix_differs_from_rhs_suffix_passes(tmp_path):
    body = prod_heredoc([
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=${{ secrets.PROD_ANON_KEY }}",
    ]) + "\n" + staging_heredoc([
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=${{ secrets.STAGING_ANON_KEY }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_alternate_terminator_accepted(tmp_path):
    body = (
        prod_heredoc(["POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}"], terminator="PROD_ENV_END")
        + "\n"
        + staging_heredoc(["POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}"], terminator="STAGING_ENV_END")
    )
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr


def test_comments_and_blank_lines_ignored(tmp_path):
    body = prod_heredoc([
        "# This is a comment",
        "",
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "   ",
        "# another comment",
        "JWT_SECRET=${{ secrets.PROD_JWT_SECRET }}",
    ]) + "\n" + staging_heredoc([
        "# staging header",
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "",
        "JWT_SECRET=${{ secrets.STAGING_JWT_SECRET }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, result.stderr


# ---------------------------------------------------------------------------
# 1.3 Failure-mode tests
# ---------------------------------------------------------------------------

def assert_three_channels(result, failure_class: str, detail: str):
    """Assert exit=1, stderr has '<path>:<line>: <class>: ...', stdout has GH annotation with class+detail.

    The `.*?deploy\\.yml` path prefix uses a non-greedy wildcard so it matches
    both POSIX (`/tmp/.../deploy.yml`) and Windows (`C:\\...\\deploy.yml`) paths.
    """
    assert result.returncode == 1, (
        f"Expected exit 1, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert re.search(
        r".*?deploy\.yml:\d+:\s+" + re.escape(failure_class),
        result.stderr,
    ), f"stderr missing '<path>:<line>: {failure_class}:'\n{result.stderr}"
    assert re.search(
        r"::error file=.*?deploy\.yml,line=\d+::" + re.escape(failure_class),
        result.stdout,
    ), f"stdout missing GitHub annotation for '{failure_class}'\n{result.stdout}"
    assert detail in (result.stdout + result.stderr), (
        f"Expected detail '{detail}' in output\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_cross_prefix_leak_in_staging_block_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "wrong-prefix", "PROD_POSTGRES_DB")


def test_cross_prefix_leak_in_prod_block_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "wrong-prefix", "STAGING_POSTGRES_DB")


def test_composite_value_with_one_leaked_ref_fails(tmp_path):
    leaky = (
        "LANGCHAIN_POSTGRES_URL=postgresql://"
        "${{ secrets.STAGING_POSTGRES_USER }}:"
        "${{ secrets.PROD_POSTGRES_PASSWORD }}@"
        "${{ secrets.STAGING_POSTGRES_HOST }}:"
        "${{ secrets.STAGING_POSTGRES_PORT }}/"
        "${{ secrets.STAGING_POSTGRES_DB }}"
    )
    prod_url = (
        "LANGCHAIN_POSTGRES_URL=postgresql://"
        "${{ secrets.PROD_POSTGRES_USER }}:"
        "${{ secrets.PROD_POSTGRES_PASSWORD }}@"
        "${{ secrets.PROD_POSTGRES_HOST }}:"
        "${{ secrets.PROD_POSTGRES_PORT }}/"
        "${{ secrets.PROD_POSTGRES_DB }}"
    )
    body = prod_heredoc([prod_url]) + "\n" + staging_heredoc([leaky])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "wrong-prefix", "PROD_POSTGRES_PASSWORD")


def test_suffix_present_in_prod_missing_in_staging_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "NEW_VAR=${{ secrets.PROD_NEW_VAR }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "NEW_VAR=${{ secrets.STAGING_OTHER_NAME }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "missing from staging", "NEW_VAR")


def test_suffix_present_in_staging_missing_in_prod_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "EXTRA=${{ secrets.PROD_OTHER_NAME }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "EXTRA=${{ secrets.STAGING_EXTRA }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "missing from prod", "EXTRA")


def test_lhs_missing_in_staging_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "NEW_VAR=${{ secrets.PROD_NEW_VAR }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "LHS missing", "NEW_VAR")


def test_literal_in_one_block_secret_in_other_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=bloom_prod",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "inconsistent", "POSTGRES_DB")


def test_malformed_heredoc_fails_fast(tmp_path):
    # Prod heredoc start with no matching terminator. Asserts the three-channel
    # contract (stderr line, GitHub annotation, failure class) so a regression
    # that drops the annotation is caught.
    indent = "          "
    body = (
        f'{indent}f="/opt/bloom/production/.env.prod"\n'
        f"{indent}cat >> \"$f\" << 'SECRETS'\n"
        f"{indent}POSTGRES_DB=${{{{ secrets.PROD_POSTGRES_DB }}}}\n"
        # No closing SECRETS!
    )
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "unclosed heredoc", ".env.prod")


def test_third_env_block_fails(tmp_path):
    indent = "          "
    dev_heredoc = (
        f'{indent}f="/opt/bloom/dev/.env.dev"\n'
        f"{indent}cat >> \"$f\" << 'DEVEOF'\n"
        f"{indent}FOO=bar\n"
        f"{indent}DEVEOF"
    )
    body = (
        prod_heredoc(["POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}"])
        + "\n"
        + staging_heredoc(["POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}"])
        + "\n"
        + dev_heredoc
    )
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 1
    assert "unexpected env block" in (result.stdout + result.stderr)
    assert ".env.dev" in (result.stdout + result.stderr)


def test_zero_env_blocks_fails(tmp_path):
    body = "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo hi\n"
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 1
    assert "0 env blocks found, expected 2" in (result.stdout + result.stderr)


def test_malformed_secret_ref_lowercase_fails(tmp_path):
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.prod_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 1
    assert "malformed" in result.stderr
    assert "prod_POSTGRES_DB" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# Round-4 review additions: parser hardening + coverage gaps
# ---------------------------------------------------------------------------

# --- Symmetric direction tests (round-4 I4) ---------------------------------

def test_lhs_missing_in_prod_fails(tmp_path):
    """Symmetric counterpart to test_lhs_missing_in_staging_fails."""
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "NEW_VAR=${{ secrets.STAGING_NEW_VAR }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "LHS missing", "NEW_VAR")


def test_literal_in_prod_secret_in_staging_fails(tmp_path):
    """Symmetric counterpart to test_literal_in_one_block_secret_in_other_fails."""
    body = prod_heredoc([
        "POSTGRES_DB=bloom_prod",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "inconsistent", "POSTGRES_DB")


# --- Exit-code-2 contract (round-4 I2) --------------------------------------

def test_missing_file_returns_exit_2(tmp_path):
    """File not found exits 2, not 1 — CI consumers distinguish usage/IO from parity."""
    nonexistent = tmp_path / "no-such-file.yml"
    result = run(nonexistent)
    assert result.returncode == 2, (
        f"Expected exit 2 for missing file, got {result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "not found" in result.stderr.lower()


def test_no_args_returns_exit_2():
    """Script invoked with no path argument exits 2 with usage message."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2, (
        f"Expected exit 2 for no args, got {result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "usage" in result.stderr.lower()


# --- Silent-skip behaviors (round-4 I3) -------------------------------------

def test_empty_heredoc_body_passes(tmp_path):
    """Documents current behavior: empty env blocks (both sides) report 0 vars / 0 refs
    with exit 0. If policy ever changes to 'fail on empty', update this test + spec.
    """
    body = prod_heredoc([]) + "\n" + staging_heredoc([])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 0, (
        f"Expected exit 0 for empty-but-paired blocks, got {result.returncode}\n"
        f"stderr:\n{result.stderr}"
    )
    assert re.search(
        r"OK — 0 vars, 0 secret refs, prod and staging aligned",
        result.stdout,
    ), f"Expected 0-vars/0-refs summary, got stdout={result.stdout!r}"


def test_lowercase_lhs_is_silently_skipped(tmp_path):
    """Characterizes current behavior: lines whose LHS doesn't match `^[A-Z][A-Z0-9_]*$`
    are skipped ENTIRELY by `parse_block` — neither the LHS nor the `${{ secrets... }}`
    refs on that line are tracked. So a prod-only `secret_key=${{ secrets.PROD_X }}`
    line passes parity even though staging has no counterpart.

    Conventional bash env vars are uppercase, and deploy.yml today uses uppercase
    throughout — this is documenting the current narrow scope, not endorsing it.
    If policy ever tightens to 'flag lowercase as malformed', update this test
    + spec.md requirement 'LHS Variable Parity' accordingly.
    """
    body = prod_heredoc([
        "secret_key=${{ secrets.PROD_SECRET_KEY }}",
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
    ]) + "\n" + staging_heredoc([
        # Note: staging has NO secret_key line — if the script tracked lowercase,
        # this would be a parity failure.
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    # Observed: exit 0, only POSTGRES_DB counted. The lowercase line is invisible.
    assert result.returncode == 0, (
        f"Expected exit 0 (lowercase LHS is skipped, not flagged).\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert re.search(r"OK — 1 vars, 2 secret refs", result.stdout), (
        f"Expected summary to count only POSTGRES_DB.\n{result.stdout!r}"
    )
    # If a future change makes lowercase a malformed-ref error, SECRET_KEY
    # would surface here — fail loudly in that case so the test is updated.
    assert "SECRET_KEY" not in (result.stdout + result.stderr), (
        "Lowercase LHS behavior changed: `secrets.PROD_SECRET_KEY` surfaced. "
        "Update this test + spec if the new behavior is intentional."
    )


# --- Parser hardening: duplicate detection (round-4 B4, TDD RED-first) ------
#
# Per spec.md "Exactly Two Env Blocks" (amended round-4): duplicate .env.prod
# or .env.staging heredocs MUST NOT be silently collapsed. Per spec.md "LHS
# Variable Parity" (amended round-4): duplicate LHS within a single block
# MUST NOT be silently collapsed either.
#
# These tests fail against the pre-hardening implementation (dict overwrite
# semantics) — they drive the implementation changes in scripts/verify_env_parity.py.

def test_duplicate_prod_heredoc_fails(tmp_path):
    """Two .env.prod heredocs (one .env.staging) — second prod must be flagged,
    not silently overwrite the first."""
    body = (
        prod_heredoc(["POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}"])
        + "\n"
        + staging_heredoc(["POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}"])
        + "\n"
        + prod_heredoc(["POSTGRES_DB=${{ secrets.PROD_OTHER }}"])
    )
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "duplicate env block", ".env.prod")


def test_duplicate_staging_heredoc_fails(tmp_path):
    """Symmetric: two .env.staging heredocs."""
    body = (
        prod_heredoc(["POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}"])
        + "\n"
        + staging_heredoc(["POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}"])
        + "\n"
        + staging_heredoc(["POSTGRES_DB=${{ secrets.STAGING_OTHER }}"])
    )
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "duplicate env block", ".env.staging")


def test_duplicate_lhs_in_prod_fails(tmp_path):
    """Same LHS declared twice in one block — flag the second occurrence."""
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "JWT_SECRET=${{ secrets.PROD_JWT_SECRET }}",
        "POSTGRES_DB=${{ secrets.PROD_OTHER }}",  # duplicate
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "JWT_SECRET=${{ secrets.STAGING_JWT_SECRET }}",
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "duplicate LHS", "POSTGRES_DB")


def test_duplicate_lhs_in_staging_fails(tmp_path):
    """Symmetric: duplicate LHS inside the staging block."""
    body = prod_heredoc([
        "POSTGRES_DB=${{ secrets.PROD_POSTGRES_DB }}",
        "JWT_SECRET=${{ secrets.PROD_JWT_SECRET }}",
    ]) + "\n" + staging_heredoc([
        "POSTGRES_DB=${{ secrets.STAGING_POSTGRES_DB }}",
        "JWT_SECRET=${{ secrets.STAGING_JWT_SECRET }}",
        "JWT_SECRET=${{ secrets.STAGING_OTHER }}",  # duplicate
    ])
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert_three_channels(result, "duplicate LHS", "JWT_SECRET")
