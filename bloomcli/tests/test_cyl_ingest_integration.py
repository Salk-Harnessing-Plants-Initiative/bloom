"""Env-gated end-to-end test for `bloomctl cyl ingest-result` against a live Bloom.

Self-skips at collection time unless psycopg is available AND the opt-in env vars
are set, so normal CI (and `pytest -m "not integration"`) never runs it. When
enabled it: seeds a scan + images via a direct admin DSN, invokes the CLI against
the real ``insert_cyl_result_envelope`` RPC, asserts the written rows, checks the
idempotent no-op, then cleans up.

Required env (all must be set):
  BLOOMCTL_INTEGRATION   any truthy value — the opt-in switch
  BLOOMCTL_IT_DSN        psycopg DSN for seeding/asserting/cleanup (admin/owner)
  BLOOMCTL_IT_API_URL    Supabase REST url for the same DB (e.g. https://staging/api)
  BLOOMCTL_IT_ANON_KEY   anon key for that url
  BLOOMCTL_IT_EMAIL      login with write access (bloom_writer/bloom_admin)
  BLOOMCTL_IT_PASSWORD   its password
"""

import json
import os
from pathlib import Path

import pytest
from click.testing import CliRunner

# Skip cleanly at *collection* if psycopg (not a bloomctl dep) is unavailable.
psycopg = pytest.importorskip("psycopg")

pytestmark = pytest.mark.integration

_KEYS = (
    "BLOOMCTL_INTEGRATION",
    "BLOOMCTL_IT_DSN",
    "BLOOMCTL_IT_API_URL",
    "BLOOMCTL_IT_ANON_KEY",
    "BLOOMCTL_IT_EMAIL",
    "BLOOMCTL_IT_PASSWORD",
)
_ENV = {k: os.environ.get(k) for k in _KEYS}
if not all(_ENV.values()):
    pytest.skip(
        "set BLOOMCTL_INTEGRATION + BLOOMCTL_IT_* to run the live ingest test",
        allow_module_level=True,
    )

from cyl_it_helpers import cleanup, envelope_for, seed_scan  # noqa: E402  (shared harness)

import bloomctl.cli as climod  # noqa: E402  (imported after the skip guard)
from bloomctl.auth import make_authed_client  # noqa: E402
from bloomctl.cli import cli  # noqa: E402
from bloomctl.credentials import Credentials  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "scan0K9E8BI.result.json"
PREDICTIONS_DIR = Path(__file__).parent / "fixtures" / "predictions_scan0K9E8BI"


@pytest.fixture
def seeded():
    """Seed one scan with two images; yield ids + a model-valid envelope; clean up."""
    conn = psycopg.connect(_ENV["BLOOMCTL_IT_DSN"], autocommit=True)
    cur = conn.cursor()
    scan_id, img_ids = seed_scan(cur)
    envelope, idem = envelope_for(FIXTURE, img_ids)
    try:
        yield scan_id, envelope, idem, cur
    finally:
        cleanup(cur, scan_id, idem)
        conn.close()


@pytest.fixture
def authed_cli(monkeypatch):
    """Inject a real authed client (built from env creds) into the command."""
    creds = Credentials(
        _ENV["BLOOMCTL_IT_API_URL"],
        _ENV["BLOOMCTL_IT_ANON_KEY"],
        _ENV["BLOOMCTL_IT_EMAIL"],
        _ENV["BLOOMCTL_IT_PASSWORD"],
    )
    client = make_authed_client(creds)
    monkeypatch.setattr(climod, "_authed_client", lambda profile: client)
    return client


def test_ingest_writes_source_and_traits_then_noop(seeded, authed_cli, tmp_path):
    scan_id, envelope, idem, cur = seeded
    path = tmp_path / "env.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    # First ingest resolves the seeded scan and writes.
    r1 = CliRunner().invoke(cli, ["cyl", "ingest-result", str(path), "--json"])
    assert r1.exit_code == 0, r1.output
    out1 = json.loads(r1.output)
    assert out1["was_noop"] is False
    assert out1["scan_id"] == scan_id

    cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
    assert cur.fetchone()[0] == 1
    cur.execute(
        "SELECT count(*) FROM cyl_scan_traits st "
        "JOIN cyl_trait_sources s ON s.id = st.source_id "
        "WHERE s.idempotency_key = %s",
        (idem,),
    )
    assert cur.fetchone()[0] == 2  # two scan-grain traits in the fixture

    # Second ingest of the same envelope is a pure no-op — no duplicate source.
    r2 = CliRunner().invoke(cli, ["cyl", "ingest-result", str(path), "--json"])
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.output)["was_noop"] is True
    cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
    assert cur.fetchone()[0] == 1


def test_ingest_uploads_blobs_idempotently_and_rejects_collisions(
    seeded, authed_cli, tmp_path
):
    """--predictions-dir: first run uploads and is retrievable; a re-run with the
    same fixture skips re-upload (idempotent); a corrupted fixture pointed at
    the same envelope (same idempotency_key -> same object path) is rejected
    rather than silently overwriting the original bytes (bloom #407)."""
    scan_id, envelope, idem, cur = seeded
    path = tmp_path / "env.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    r1 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(PREDICTIONS_DIR), "--json"],
    )
    assert r1.exit_code == 0, r1.output
    out1 = json.loads(r1.output)
    assert out1["was_noop"] is False
    assert out1["blob_count"] == 2

    cur.execute(
        "SELECT root_type, s3_location, checksum FROM cyl_scan_intermediates csi "
        "JOIN cyl_trait_sources s ON s.id = csi.source_id "
        "WHERE s.idempotency_key = %s ORDER BY root_type",
        (idem,),
    )
    rows = cur.fetchall()
    assert [r[0] for r in rows] == ["crown", "primary"]
    import hashlib

    for root_type, s3_location, checksum in rows:
        assert s3_location and idem in s3_location
        data = authed_cli.storage.from_("cyl-intermediates").download(s3_location)
        assert hashlib.sha256(data).hexdigest() == checksum

    # Re-running the SAME envelope+predictions-dir is idempotent end to end:
    # the RPC call is a first-writer-wins no-op, and the blob upload step
    # itself would skip re-uploading (matching checksum at the same path)
    # even if the RPC weren't a no-op.
    r2 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(PREDICTIONS_DIR), "--json"],
    )
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.output)["was_noop"] is True

    # A corrupted manifest (same scan_key -> same object path for this
    # envelope's idempotency_key, but bytes that no longer match the
    # manifest's own declared checksum) must be rejected -- not silently
    # overwrite the original bytes already in storage.
    corrupt_dir = tmp_path / "predictions_corrupt"
    corrupt_dir.mkdir()
    manifest = json.loads((PREDICTIONS_DIR / "scan0K9E8BI.predictions.json").read_text())
    for artifact in manifest["artifacts"]:
        (corrupt_dir / artifact["slp_path"]).write_bytes(b"corrupted bytes, wrong checksum")
    (corrupt_dir / "scan0K9E8BI.predictions.json").write_text(json.dumps(manifest))

    r3 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(corrupt_dir), "--json"],
    )
    assert r3.exit_code != 0
    assert "checksum" in r3.output.lower()

    # The original, good objects must still be intact after the rejected attempt.
    for root_type, s3_location, checksum in rows:
        data = authed_cli.storage.from_("cyl-intermediates").download(s3_location)
        assert hashlib.sha256(data).hexdigest() == checksum
