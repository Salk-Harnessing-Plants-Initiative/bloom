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


def prod_heredoc(body_lines: list[str], terminator: str = "ENVEOF") -> str:
    indent = "          "
    lines = [
        f"{indent}ssh cmd \"cat > /opt/bloom/production/.env.prod << '{terminator}'",
    ]
    lines.extend(f"{indent}{l}" for l in body_lines)
    lines.append(f"{indent}{terminator}")
    return "\n".join(lines)


def staging_heredoc(body_lines: list[str], terminator: str = "ENVEOF") -> str:
    indent = "          "
    lines = [
        f"{indent}ssh cmd \"cat > /opt/bloom/staging/.env.staging << '{terminator}'",
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
    """Assert exit=1, stderr has '<path>:<line>: <class>: ...', stdout has GH annotation with class+detail."""
    assert result.returncode == 1, (
        f"Expected exit 1, got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert re.search(
        r".*/deploy\.yml:\d+:\s+" + re.escape(failure_class),
        result.stderr,
    ), f"stderr missing '<path>:<line>: {failure_class}:'\n{result.stderr}"
    assert re.search(
        r"::error file=.*/deploy\.yml,line=\d+::" + re.escape(failure_class),
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
    prod_url = leaky.replace("STAGING_", "PROD_").replace("PROD_POSTGRES_PASSWORD", "PROD_POSTGRES_PASSWORD")
    # Re-construct prod-correct URL
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
    # Prod heredoc start with no matching ENVEOF
    indent = "          "
    body = (
        f"{indent}ssh cmd \"cat > /opt/bloom/production/.env.prod << 'ENVEOF'\n"
        f"{indent}POSTGRES_DB=${{{{ secrets.PROD_POSTGRES_DB }}}}\n"
        # No closing ENVEOF!
    )
    f = write_deploy(tmp_path, body)
    result = run(f)
    assert result.returncode == 1
    assert "unclosed heredoc" in result.stderr


def test_third_env_block_fails(tmp_path):
    indent = "          "
    dev_heredoc = (
        f"{indent}ssh cmd \"cat > /opt/bloom/dev/.env.dev << 'DEVEOF'\n"
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
