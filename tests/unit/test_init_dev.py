"""Unit tests for scripts/init_dev.py — the local-dev credential generator.

The generator (``make init``) produces a working ``.env.dev`` from
``.env.dev.example`` with fresh local secrets. The dangerous parts are: the
encryption-key sizes (a wrong DB_ENC_KEY silently crashes Realtime ~90s in) and
the API keys, which must be JWTs *signed by the generated JWT_SECRET* with the
right role claim — independent random strings are rejected by GoTrue/PostgREST.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import jwt
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "init_dev.py"


def _load():
    spec = importlib.util.spec_from_file_location("init_dev", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


init_dev = _load()


# ---------- secret generation ----------

def test_encryption_key_sizes():
    """Sizes mirror the verified scripts/generate-secrets.sh constraints."""
    v = init_dev.generate_secrets()
    assert len(v["DB_ENC_KEY"]) == 16, "DB_ENC_KEY must be exactly 16 bytes (Realtime AES-128)"
    assert len(v["VAULT_ENC_KEY"]) == 32
    assert len(v["SECRET_KEY_BASE"]) >= 64
    # SUPAVISOR_ENC_KEY: 64 hex chars
    assert len(v["SUPAVISOR_ENC_KEY"]) == 64
    assert all(c in "0123456789abcdef" for c in v["SUPAVISOR_ENC_KEY"])
    assert len(v["JWT_SECRET"]) >= 32


def test_role_keys_are_jwts_signed_by_jwt_secret():
    v = init_dev.generate_secrets()
    secret = v["JWT_SECRET"]
    expected_roles = {
        "ANON_KEY": "anon",
        "NEXT_PUBLIC_SUPABASE_ANON_KEY": "anon",
        "SERVICE_ROLE_KEY": "service_role",
        "BLOOM_AGENT_KEY": "bloom_agent",
    }
    for key, role in expected_roles.items():
        decoded = jwt.decode(
            v[key], secret, algorithms=["HS256"], audience="authenticated"
        )
        assert decoded["role"] == role, f"{key} should carry role={role}"
        assert decoded["iss"] == "supabase"
    # A wrong secret must NOT verify (proves they're really signed by JWT_SECRET).
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(
            v["ANON_KEY"],
            "a-different-secret-of-sufficient-length-32+",
            algorithms=["HS256"],
            audience="authenticated",
        )


def test_secrets_are_unique_per_run():
    a = init_dev.generate_secrets()
    b = init_dev.generate_secrets()
    assert a["JWT_SECRET"] != b["JWT_SECRET"]
    assert a["POSTGRES_PASSWORD"] != b["POSTGRES_PASSWORD"]


# ---------- rendering ----------

def _value_lines(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def test_render_fills_every_placeholder():
    template = (REPO_ROOT / ".env.dev.example").read_text(encoding="utf-8")
    out = init_dev.render(template, init_dev.generate_secrets())
    # No KEY=value line may still hold a placeholder (comments may mention the
    # word CHANGEME — that's documentation, not a value).
    leftover = [k for k, v in _value_lines(out).items() if "CHANGEME" in v]
    assert not leftover, f"placeholders left unfilled: {leftover}"


def test_render_tolerates_crlf_template():
    crlf = "POSTGRES_PASSWORD=CHANGEME\r\n# comment\r\nPOSTGRES_DB=postgres\r\n"
    out = init_dev.render(crlf, init_dev.generate_secrets())
    assert "\r" not in out, "output must be LF only"
    assert "CHANGEME" not in out
    assert "POSTGRES_DB=postgres" in out


# ---------- CLI / file behaviour ----------

def _mini_template() -> str:
    return (
        "POSTGRES_PASSWORD=CHANGEME\n"
        "JWT_SECRET=CHANGEME\n"
        "ANON_KEY=CHANGEME\n"
        "SERVICE_ROLE_KEY=CHANGEME\n"
        "BLOOM_AGENT_KEY=CHANGEME\n"
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=CHANGEME\n"
        "SUPAVISOR_ENC_KEY=CHANGEME\n"
        "VAULT_ENC_KEY=CHANGEME\n"
        "SECRET_KEY_BASE=CHANGEME\n"
        "DB_ENC_KEY=CHANGEME\n"
        "MINIO_ROOT_PASSWORD=CHANGEME\n"
        "DASHBOARD_PASSWORD=CHANGEME\n"
        "BLOOMMCP_API_KEY=CHANGEME\n"
        "LANGCHAIN_POSTGRES_URL=postgresql://supabase_admin:CHANGEME@db-dev:5432/postgres\n"
        "OPENAI_API_KEY=\n"
    )


def _run(tmp_path, *args, template_text=None):
    template = tmp_path / ".env.dev.example"
    template.write_text(_mini_template() if template_text is None else template_text)
    output = tmp_path / ".env.dev"
    rc = init_dev.main(
        ["--template", str(template), "--output", str(output), *args]
    )
    return rc, output


def test_creates_env_dev(tmp_path):
    rc, output = _run(tmp_path)
    assert rc == 0
    assert output.exists()
    assert "CHANGEME" not in output.read_text()


def test_refuses_to_overwrite_without_force(tmp_path):
    rc, output = _run(tmp_path)
    assert rc == 0
    first = output.read_text()
    rc2, _ = _run(tmp_path)  # second run, no --force
    assert rc2 != 0, "must refuse to overwrite an existing .env.dev without --force"
    assert output.read_text() == first, "the existing file must be left untouched"


def test_force_backs_up_then_regenerates(tmp_path):
    rc, output = _run(tmp_path)
    original = output.read_text()
    rc2, _ = _run(tmp_path, "--force")
    assert rc2 == 0
    backup = tmp_path / ".env.dev.backup"
    assert backup.exists(), "--force must back up the prior .env.dev"
    assert backup.read_text() == original
    assert output.read_text() != original, "a new .env.dev must be generated"


def test_force_does_not_clobber_existing_backup(tmp_path):
    _run(tmp_path)
    _run(tmp_path, "--force")  # creates .env.dev.backup
    _run(tmp_path, "--force")  # must NOT overwrite the existing backup silently
    backups = list(tmp_path.glob(".env.dev.backup*"))
    assert len(backups) >= 2, (
        "a second --force must write a distinct (timestamped) backup, not "
        "destroy the first"
    )


def test_missing_template_errors(tmp_path):
    output = tmp_path / ".env.dev"
    rc = init_dev.main(
        ["--template", str(tmp_path / "nope.example"), "--output", str(output)]
    )
    assert rc != 0
    assert not output.exists()


def test_no_secret_values_printed(tmp_path, capsys):
    rc, output = _run(tmp_path)
    assert rc == 0
    printed = capsys.readouterr().out
    # The actual generated secrets must never reach stdout.
    for line in output.read_text().splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        if value and value != "" and key not in ("POSTGRES_DB",):
            # Long generated values must not appear verbatim in stdout.
            if len(value) >= 16:
                assert value not in printed, f"secret value for {key} leaked to stdout"
