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

sys.dont_write_bytecode = True

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

EOF = "          # _EOF_MARKER_"

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


# --- a value nothing verifiable backs (#699's shape, inside the tool) ----------
# The block supplying a key is not proof the key resolves: only a secret this check
# can confirm exists, or a defaults entry, is. Everything else reaches the deploy
# unverified while reading as "supplied".


@pytest.mark.parametrize(
    "value",
    [
        "${{ vars.PROD_API_TOKEN }}",
        "${{ env.PROD_API_TOKEN }}",
        "${{ github.sha }}",
        "a-literal-typed-into-the-block",
    ],
)
def test_a_value_no_secret_backs_is_reported(tree, capsys, value):
    deploy = DEPLOY.replace("API_TOKEN=${{ secrets.PROD_API_TOKEN }}", f"API_TOKEN={value}")
    (tree / "deploy.yml").write_text(deploy)
    assert run(tree, ALL_PRESENT) == 1
    assert "nothing verifiable backs it" in capsys.readouterr().err


def test_a_value_no_secret_backs_is_reported_in_wiring_mode_too(tree, capsys):
    """The finding needs no secret list, so losing the token cannot hide it."""
    deploy = DEPLOY.replace(
        "API_TOKEN=${{ secrets.PROD_API_TOKEN }}", "API_TOKEN=${{ vars.SOMETHING }}"
    )
    (tree / "deploy.yml").write_text(deploy)
    assert run(tree) == 1
    assert "nothing verifiable backs it" in capsys.readouterr().err


def test_the_finding_names_the_environment_it_was_found_in(tree, capsys):
    """Both environments are checked separately; staging's block is its own."""
    deploy = DEPLOY.replace(
        "API_TOKEN=${{ secrets.STAGING_API_TOKEN }}", "API_TOKEN=${{ vars.SOMETHING }}"
    )
    (tree / "deploy.yml").write_text(deploy)
    assert run(tree, ALL_PRESENT) == 1
    err = capsys.readouterr().err
    assert ".env.staging block" in err
    assert ".env.prod block" not in err


def test_a_defaults_supplied_key_is_still_not_required_in_the_block(tree):
    """REGION comes from the defaults file, so it needs no secret behind it."""
    assert run(tree, ALL_PRESENT) == 0


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
    assert [f for _n, f in found] == [form]


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


# --- agreement with validate_env.sh on whether a key is SUPPLIED ---------------
# validate_env.sh:76 accepts `^KEY=[^[:space:]#].*`. Checking the name alone passed
# all five inputs below while the deploy rejected every one of them.


@pytest.mark.parametrize(
    "defaults, reason",
    [
        ("NEEDED=\n", "empty value"),
        ("NEEDED=#TODO\n", "comment placeholder"),
        ("NEEDED= \n", "whitespace value"),
        ("  NEEDED=x\n", "indented, so not anchored at column 0"),
    ],
)
def test_defaults_entry_without_a_usable_value_is_not_supplied(defaults, reason):
    assert vds.defaults_keys(defaults) == set(), reason


def test_defaults_entry_with_a_value_is_supplied():
    assert vds.defaults_keys("NEEDED=real\n") == {"NEEDED"}


def _body(*lines):
    return [(n, line) for n, line in enumerate(lines, start=1)]


def test_heredoc_key_with_an_empty_value_is_not_supplied():
    """`ALPHA=` parses as a key with no secret ref, so the existence loop never ran
    and it passed even in full mode — while the deploy rejected the file."""
    supplied, _after = vds.block_entries(_body("          ALPHA=", "          " + vds.EOF_MARKER))
    assert supplied == {}


def test_heredoc_key_indented_past_the_block_margin_is_not_supplied():
    """The YAML block scalar strips only the block's own indent, so the surplus
    survives into the env file and breaks validate_env.sh's column-0 anchor."""
    supplied, _after = vds.block_entries(
        _body("          ALPHA=${{ secrets.X }}", "            BETA=${{ secrets.Y }}",
              "          " + vds.EOF_MARKER)
    )
    assert set(supplied) == {"ALPHA"}


@pytest.mark.parametrize(
    "trailing",
    [
        "          LATE=${{ secrets.X }}",
        "          LATE=",
        "          # rotated 2026-08-19",
        "",
    ],
    ids=["key", "valueless key", "comment", "blank line"],
)
def test_anything_after_the_eof_marker_is_reported(trailing):
    """validate_env.sh greps `tail -n1` for the marker, so anything following it makes
    every deploy reject the assembled file as truncated."""
    _supplied, bad_last = vds.block_entries(
        _body("          " + vds.EOF_MARKER, trailing)
    )
    assert bad_last == trailing[10:]


def test_marker_with_a_trailing_space_is_reported():
    """The shell anchors on `^# _EOF_MARKER_$`; a trailing space is as fatal as none."""
    _supplied, bad_last = vds.block_entries(_body("          " + vds.EOF_MARKER + " "))
    assert bad_last == vds.EOF_MARKER + " "


def test_missing_eof_marker_is_reported():
    _supplied, bad_last = vds.block_entries(_body("          A=${{ secrets.X }}"))
    assert bad_last == "A=${{ secrets.X }}"


def test_block_ending_in_the_marker_is_clean():
    _supplied, bad_last = vds.block_entries(
        _body("          A=${{ secrets.X }}", "          " + vds.EOF_MARKER)
    )
    assert bad_last is None


def test_duplicate_heredoc_key_keeps_the_last_ref():
    """`cat >>` writes both lines and the last wins at deploy time."""
    supplied, _after = vds.block_entries(
        _body("          A=${{ secrets.FIRST }}", "          A=${{ secrets.SECOND }}",
              "          " + vds.EOF_MARKER)
    )
    assert supplied["A"] == ["SECOND"]


# --- input validation: every unusable input is exit 2, never exit 1 ------------


@pytest.mark.parametrize(
    "payload",
    ["not json", "[]", "null", '{"production": null}', '{"production": "PROD_API_TOKEN"}',
     '{"production": [1, 2]}', '{"production": []}'],
)
def test_malformed_secrets_json_is_a_usage_error(tree, payload):
    """Exit 1 would make a broken generator step indistinguishable from a real
    finding; a bare string would degrade into a set of characters."""
    path = tree / "secrets.json"
    path.write_text(payload)
    assert vds.main([
        "--compose", str(tree / "docker-compose.prod.yml"),
        "--deploy", str(tree / "deploy.yml"),
        "--defaults-dir", str(tree),
        "--secrets-json", str(path),
    ]) == 2


def test_missing_input_file_is_a_usage_error(tree):
    assert vds.main([
        "--compose", str(tree / "nope.yml"), "--deploy", str(tree / "deploy.yml"),
        "--defaults-dir", str(tree),
    ]) == 2


def test_missing_defaults_file_is_reported_before_any_environment_runs(tree, capsys):
    """Returning 2 mid-loop discarded findings already emitted and skipped staging."""
    (tree / ".env.staging.defaults").unlink()
    assert run(tree, ALL_PRESENT) == 2
    assert "problem(s) found" not in capsys.readouterr().err


# --- the lint must not pre-empt itself ----------------------------------------


def test_compose_written_entirely_with_unbraced_refs_names_the_mistake(tree, capsys):
    """This yields zero required keys; reporting 'no ${VAR} references found' would
    hide the exact error the lint exists to name."""
    (tree / "docker-compose.prod.yml").write_text(
        "services:\n  x:\n    environment:\n      A: $ALPHA\n"
    )
    assert run(tree, ALL_PRESENT) == 1
    assert "$ALPHA" in capsys.readouterr().err


def test_a_trailing_comment_is_not_a_finding():
    """Compose does not interpolate comments; failing a PR over a margin note sent
    the author to 'fix' it into a real deploy failure."""
    assert vds.nonstandard_refs("      C: ${GAMMA}  # set $HOME first\n") == []


# --- environments are looked up independently ---------------------------------


def test_a_secret_present_only_in_the_other_environment_still_fails(tree, capsys):
    """Guards against collapsing the two environments into one pooled set."""
    secrets = {"production": [], "staging": ["PROD_API_TOKEN", "PROD_JWT_JWKS",
                                             "STAGING_API_TOKEN", "STAGING_JWT_JWKS"]}
    assert run(tree, secrets) == 1
    assert "PROD_API_TOKEN" in capsys.readouterr().err


def test_escaped_reference_is_not_told_to_drop_its_escape():
    """`$$` is compose's escape for a literal `$`. Suggesting `${NAME}` would move
    expansion from the container to the deploy host and change what the container
    runs — while CI went green, because the author did as the message said."""
    hint = vds.hint_for("$${HOME}")
    assert "$$HOME" in hint
    assert not hint.startswith("Use ${HOME}")


def test_unescaped_forms_keep_the_direct_suggestion():
    assert vds.hint_for("$BARE") == "Use ${BARE} instead."
    assert vds.hint_for("${lower}") == "Use ${LOWER} instead."
