"""Unit tests for scripts/verify_deploy_secrets.py (bloom #699).

The script predicts, at PR time, whether a deploy will fail at env assembly. Its
value depends entirely on agreeing with `scripts/validate_env.sh`, which makes
that call for real at deploy time — so several tests below pin that agreement
rather than the script's behaviour in isolation.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "verify_deploy_secrets", SCRIPTS / "verify_deploy_secrets.py"
)
vds = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vds)


COMPOSE = """
services:
  api:
    environment:
      TOKEN: ${API_TOKEN}
      REGION: ${REGION}
      OPTIONAL: ${JWT_JWKS:-null}
      PROJECT: ${COMPOSE_PROJECT_NAME}
"""

DEPLOY = """
jobs:
  deploy:
    steps:
      - run: |
          f="/srv/.env.prod.next"
          cat >> "$f" << 'SECRETS'
          API_TOKEN=${{ secrets.PROD_API_TOKEN }}
          JWT_JWKS=${{ secrets.PROD_JWT_JWKS }}
          # _EOF_MARKER_
          SECRETS
      - run: |
          f="/srv/.env.staging.next"
          cat >> "$f" << 'SECRETS'
          API_TOKEN=${{ secrets.STAGING_API_TOKEN }}
          JWT_JWKS=${{ secrets.STAGING_JWT_JWKS }}
          # _EOF_MARKER_
          SECRETS
"""

DEFAULTS = "# comment\nREGION=us-west-2\n"

ALL_PRESENT = {
    "production": ["PROD_API_TOKEN", "PROD_JWT_JWKS"],
    "staging": ["STAGING_API_TOKEN", "STAGING_JWT_JWKS"],
}


@pytest.fixture
def tree(tmp_path):
    """A minimal repo: compose, deploy workflow, and both defaults files."""
    (tmp_path / "docker-compose.prod.yml").write_text(COMPOSE)
    (tmp_path / "deploy.yml").write_text(DEPLOY)
    for env in ("prod", "staging"):
        (tmp_path / f".env.{env}.defaults").write_text(DEFAULTS)
    return tmp_path


def run(tree, secrets=None):
    argv = [
        "--compose", str(tree / "docker-compose.prod.yml"),
        "--deploy", str(tree / "deploy.yml"),
        "--defaults-dir", str(tree),
    ]
    if secrets is not None:
        path = tree / "secrets.json"
        path.write_text(json.dumps(secrets))
        argv += ["--secrets-json", str(path)]
    return vds.main(argv)


# --- agreement with validate_env.sh -----------------------------------------


def test_excluded_keys_match_validate_env_sh():
    """The two must skip the same keys, or this script's verdict is not the one
    the deploy will reach. validate_env.sh stays the source of truth."""
    text = (SCRIPTS / "validate_env.sh").read_text(encoding="utf-8")
    match = re.search(r"grep -v -E '\^\(([A-Z_|]+)\)\$'", text)
    assert match, "could not find the exclusion filter in validate_env.sh"
    assert set(match.group(1).split("|")) == vds.EXCLUDED_KEYS


def test_a_compose_default_does_not_make_a_var_optional():
    """`${JWT_JWKS:-null}` still counts as required: validate_env.sh's regex stops
    at the var name, so the deploy demands a non-empty value regardless of the
    default compose would have applied. Treating it as optional here would let a
    guaranteed deploy failure through."""
    assert "JWT_JWKS" in vds.compose_required_keys(COMPOSE)


def test_compose_project_name_is_excluded():
    assert "COMPOSE_PROJECT_NAME" not in vds.compose_required_keys(COMPOSE)


# --- key derivation ----------------------------------------------------------


def test_defaults_keys_ignores_comments_and_blanks():
    assert vds.defaults_keys("# c\n\nA=1\nB=2\n") == {"A", "B"}


def test_defaults_satisfy_a_required_key(tree):
    """REGION comes from the defaults file, so it needs no secret."""
    assert run(tree, ALL_PRESENT) == 0


# --- the checks ---------------------------------------------------------------


def test_passes_when_every_secret_exists(tree):
    assert run(tree, ALL_PRESENT) == 0


def test_missing_secret_fails(tree, capsys):
    """The bloom #677 case: wired in deploy.yml, but the secret was never created."""
    secrets = {"production": ["PROD_JWT_JWKS"], "staging": list(ALL_PRESENT["staging"])}
    assert run(tree, secrets) == 1
    err = capsys.readouterr().err
    assert "PROD_API_TOKEN" in err and "production" in err


def test_unwired_var_fails(tree, capsys):
    """A compose var in neither the defaults nor the heredoc."""
    (tree / "docker-compose.prod.yml").write_text(
        COMPOSE + "      EXTRA: ${BRAND_NEW_VAR}\n"
    )
    assert run(tree, ALL_PRESENT) == 1
    assert "BRAND_NEW_VAR" in capsys.readouterr().err


def test_wiring_mode_does_not_check_existence(tree):
    """Without --secrets-json the script cannot see GitHub, so a missing secret
    passes. The notice it prints is what stops that reading as a clean bill."""
    assert run(tree) == 0


def test_wiring_mode_announces_its_own_blind_spot(tree, capsys):
    run(tree)
    assert "Secret existence was not verified" in capsys.readouterr().out


def test_every_environment_is_checked(tree, capsys):
    """A secret missing only from staging must still fail the run."""
    secrets = {"production": list(ALL_PRESENT["production"]), "staging": ["STAGING_JWT_JWKS"]}
    assert run(secrets=secrets, tree=tree) == 1
    assert "STAGING_API_TOKEN" in capsys.readouterr().err


def test_compose_with_no_vars_is_an_error_not_a_pass(tree):
    """An empty required set would make every check vacuous."""
    (tree / "docker-compose.prod.yml").write_text("services:\n  api:\n    image: x\n")
    assert run(tree, ALL_PRESENT) == 2


# --- the ${UPPERCASE} convention the required-keys regex depends on ----------


@pytest.mark.parametrize(
    "line, form",
    [
        ("      BARE: $SOME_TOKEN", "$SOME_TOKEN"),
        ("      LOW: ${some_token}", "${some_token}"),
        ("      ESCAPED: $${SOME_TOKEN}", "$${SOME_TOKEN}"),
    ],
)
def test_non_standard_references_are_rejected(line, form):
    """Compose interpolates bare and lowercase names, which the required-keys
    regex never sees; `$${VAR}` is a literal it wrongly counts. Each would make
    the check silently wrong, so the file is held to plain ${UPPERCASE}."""
    found = vds.nonstandard_refs(COMPOSE + line + "\n")
    assert [f for _n, f, _l in found] == [form]


def test_plain_uppercase_references_are_accepted():
    assert vds.nonstandard_refs(COMPOSE) == []


def test_comments_are_not_flagged():
    """Compose does not interpolate comments, so a $VAR mentioned in prose is fine."""
    assert vds.nonstandard_refs("# see $LEGACY_VAR and ${old_name}\n") == []


def test_a_bad_reference_fails_the_run(tree, capsys):
    (tree / "docker-compose.prod.yml").write_text(COMPOSE + "      BARE: $SOME_TOKEN\n")
    assert run(tree, ALL_PRESENT) == 1
    assert "$SOME_TOKEN" in capsys.readouterr().err


def test_the_real_compose_file_holds_the_convention():
    """The regex is only correct because docker-compose.prod.yml sticks to
    ${UPPERCASE}; nothing but this enforces that."""
    compose = REPO_ROOT / "docker-compose.prod.yml"
    assert vds.nonstandard_refs(compose.read_text(encoding="utf-8")) == []
