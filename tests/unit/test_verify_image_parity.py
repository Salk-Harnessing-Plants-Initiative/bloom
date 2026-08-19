"""Unit tests for scripts/verify_image_parity.py.

Guards the dev/prod image-parity contract (issue #692): docker-compose.dev.yml must pin
the same image versions as docker-compose.prod.yml, so production-only behaviour is
reproducible locally instead of surfacing only in compose-health-check.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "verify_image_parity.py"
DEV_COMPOSE = REPO_ROOT / "docker-compose.dev.yml"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"


def run(dev: Path, prod: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(dev), str(prod)],
        capture_output=True,
        text=True,
    )


def write(tmp_path: Path, name: str, services: dict[str, str]) -> Path:
    body = ["services:"]
    for service, image in services.items():
        body += [f"  {service}:", f"    image: {image}"]
    path = tmp_path / name
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def test_repo_compose_files_are_in_parity():
    """The real files must pass — this is the check the CI job runs."""
    result = run(DEV_COMPOSE, PROD_COMPOSE)
    assert result.returncode == 0, result.stderr


def test_version_mismatch_fails(tmp_path):
    dev = write(tmp_path, "dev.yml", {"storage": "supabase/storage-api:v1.25.7"})
    prod = write(tmp_path, "prod.yml", {"storage": "supabase/storage-api:v1.48.14"})
    result = run(dev, prod)
    assert result.returncode == 1
    assert "storage" in result.stderr
    assert "::error" in result.stdout


def test_matching_versions_pass(tmp_path):
    dev = write(tmp_path, "dev.yml", {"storage": "supabase/storage-api:v1.48.14"})
    prod = write(tmp_path, "prod.yml", {"storage": "supabase/storage-api:v1.48.14"})
    assert run(dev, prod).returncode == 0


def test_digest_pin_on_prod_only_is_allowed(tmp_path):
    """Prod pins digests for supply-chain reasons; dev should not be forced to."""
    dev = write(tmp_path, "dev.yml", {"rest": "postgrest/postgrest:v12.2.12"})
    prod = write(tmp_path, "prod.yml", {"rest": "postgrest/postgrest:v12.2.12@sha256:abc123"})
    assert run(dev, prod).returncode == 0


def test_digest_does_not_mask_a_version_mismatch(tmp_path):
    dev = write(tmp_path, "dev.yml", {"rest": "postgrest/postgrest:v12.0.0"})
    prod = write(tmp_path, "prod.yml", {"rest": "postgrest/postgrest:v12.2.12@sha256:abc123"})
    assert run(dev, prod).returncode == 1


def test_database_service_alias_is_compared(tmp_path):
    """db-dev and db-prod are the same service under different names."""
    dev = write(tmp_path, "dev.yml", {"db-dev": "supabase/postgres:15.8.1.060"})
    prod = write(tmp_path, "prod.yml", {"db-prod": "supabase/postgres:15.14.1.104"})
    result = run(dev, prod)
    assert result.returncode == 1
    assert "db-dev / db-prod" in result.stderr


def test_database_service_alias_passes_when_aligned(tmp_path):
    dev = write(tmp_path, "dev.yml", {"db-dev": "supabase/postgres:15.14.1.104"})
    prod = write(tmp_path, "prod.yml", {"db-prod": "supabase/postgres:15.14.1.104"})
    assert run(dev, prod).returncode == 0


def test_unexpected_dev_only_service_is_reported(tmp_path):
    """A new service must not silently escape the check."""
    dev = write(tmp_path, "dev.yml", {"newthing": "example/newthing:v1"})
    prod = write(tmp_path, "prod.yml", {})
    result = run(dev, prod)
    assert result.returncode == 1
    assert "newthing" in result.stderr


def test_known_dev_only_service_is_ignored(tmp_path):
    dev = write(tmp_path, "dev.yml", {"swagger-ui": "swaggerapi/swagger-ui:v5.18.2"})
    prod = write(tmp_path, "prod.yml", {})
    assert run(dev, prod).returncode == 0


def test_missing_file_exits_two(tmp_path):
    prod = write(tmp_path, "prod.yml", {})
    assert run(tmp_path / "nope.yml", prod).returncode == 2
