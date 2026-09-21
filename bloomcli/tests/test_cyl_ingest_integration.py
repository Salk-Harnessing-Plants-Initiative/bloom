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


def test_ingest_uploads_blobs_idempotently_and_rejects_checksum_mismatch(
    seeded, authed_cli, tmp_path
):
    """--predictions-dir: first run uploads and is retrievable; a re-run with the
    same fixture skips re-upload (idempotent); a corrupted fixture (bytes no
    longer matching its OWN manifest's stale declared checksum) is rejected by
    client-side checksum verification -- before any upload is even attempted --
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

    # Re-running the SAME envelope+predictions-dir is idempotent end to end. Note what this
    # leg does NOT prove any more: since the idempotency gate landed, the upload step is skipped
    # entirely here, so upload_blob's same-checksum skip is never exercised. (The comment that
    # used to sit here claimed the upload "would skip re-uploading ... even if the RPC weren't a
    # no-op" -- true only for identical bytes, and precisely the blind spot that hid
    # sleap-roots-pipeline#76. Divergent bytes are covered by the dedicated test below.)
    r2 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(PREDICTIONS_DIR), "--json"],
    )
    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.output)["was_noop"] is True

    # A corrupted manifest (bytes no longer matching its own STALE declared checksum -- e.g. a
    # partial write or disk corruption after the manifest was authored) must be rejected by
    # client-side verify_blob_checksum, before any upload or overwrite is even attempted.
    #
    # This runs against a FRESH idempotency_key, deliberately. Checksum verification happens
    # inside the upload step, so on an already-ingested key the gate skips it and this leg would
    # exit 0 -- testing nothing. A never-ingested key is the only state in which client-side
    # verification is reachable at all.
    # A second envelope over the same seeded images. envelope_for gives inputs.images_checksum
    # a fresh value per call, so the model DERIVES a different idempotency_key -- which is the
    # only way to get a never-ingested key here. Hand-editing the key instead would be rejected:
    # Provenance._fill_idempotency_key raises when a supplied key disagrees with the derived one,
    # so the leg would die in validation without ever reaching checksum verification.
    fresh, _fresh_idem = envelope_for(FIXTURE, envelope["provenance"]["inputs"]["image_ids"])
    fresh_path = tmp_path / "env_fresh.json"
    fresh_path.write_text(json.dumps(fresh), encoding="utf-8")

    corrupt_dir = tmp_path / "predictions_corrupt"
    corrupt_dir.mkdir()
    manifest = json.loads((PREDICTIONS_DIR / "scan0K9E8BI.predictions.json").read_text())
    for artifact in manifest["artifacts"]:
        (corrupt_dir / artifact["slp_path"]).write_bytes(b"corrupted bytes, wrong checksum")
    (corrupt_dir / "scan0K9E8BI.predictions.json").write_text(json.dumps(manifest))

    r3 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(fresh_path), "--predictions-dir", str(corrupt_dir), "--json"],
    )
    assert r3.exit_code != 0
    assert "checksum" in r3.output.lower()

    # The original, good objects must still be intact after the rejected attempt.
    for root_type, s3_location, checksum in rows:
        data = authed_cli.storage.from_("cyl-intermediates").download(s3_location)
        assert hashlib.sha256(data).hexdigest() == checksum


def test_ingest_rejects_a_genuine_storage_path_collision(seeded, authed_cli, tmp_path, monkeypatch):
    """A SECOND run's bytes that pass their OWN manifest's checksum (so client-side
    verify_blob_checksum has nothing to catch) but differ from what's already stored at the same
    derived object path must be refused by upload_blob's existence check -- the actual 'path
    collision' code path, not checksum verification (bloom #407 / PR #508 review follow-up).

    Aimed at the ORPHAN state, which since sleap-roots-pipeline#76 is the only way this branch
    is still reachable: bytes at the derived path belonging to NO ingested source. Previously
    this test ingested successfully and then re-delivered, but that now short-circuits on the
    idempotency gate and exits 0 -- it would assert the opposite of the intended behaviour.

    The orphan is built the way production makes one: the upload succeeds and the RPC then
    fails, so the bytes land with no cyl_trait_sources row. A later recompute at the same key
    (predict's .slp output not being byte-reproducible) then collides, permanently, because the
    gate correctly reports "not ingested". `cleanup` deletes storage objects by the idem prefix,
    so the orphan is torn down with everything else."""
    scan_id, envelope, idem, cur = seeded
    path = tmp_path / "env.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    # Upload lands, RPC dies -> bytes in the bucket with no source row.
    from postgrest import APIError

    import bloomctl.cyl.ingest as ing

    def die(*_a, **_kw):
        raise APIError({"message": "simulated write-back crash after upload"})

    # Scoped context, NOT monkeypatch.undo(): `authed_cli` requests the same function-scoped
    # monkeypatch fixture, so undo() would revert ITS patch of climod._authed_client too. The
    # next invoke passes no -p, so it would fall back to DEFAULT_PROFILE ("prod") and run the
    # upload and RPC against whatever ~/.bloom/credentials.txt points at.
    with monkeypatch.context() as m:
        m.setattr(ing, "call_insert_envelope", die)
        r1 = CliRunner().invoke(
            cli,
            [
                "cyl",
                "ingest-result",
                str(path),
                "--predictions-dir",
                str(PREDICTIONS_DIR),
                "--json",
            ],
        )
        assert r1.exit_code != 0, "the RPC was supposed to fail, leaving orphaned blobs"

    orphan_paths = [
        ing.blob_object_path(envelope["provenance"]["scan_key"], idem, "predictions_slp", rt)
        for rt in ("primary", "crown")
    ]
    orphan_bytes = {
        op: authed_cli.storage.from_("cyl-intermediates").download(op) for op in orphan_paths
    }
    cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
    assert cur.fetchone()[0] == 0, "premise broken: a source row exists, so this is not an orphan"

    # A second manifest at the SAME scan_key (-> same object path for this
    # envelope's idempotency_key) whose artifacts are genuinely different
    # bytes with a checksum that correctly matches THOSE new bytes -- so
    # client-side verification passes, and the only thing that can catch this
    # is upload_blob comparing against what's already in storage.
    import hashlib

    collision_dir = tmp_path / "predictions_collision"
    collision_dir.mkdir()
    manifest = json.loads((PREDICTIONS_DIR / "scan0K9E8BI.predictions.json").read_text())
    for artifact in manifest["artifacts"]:
        new_bytes = f"different run's bytes for {artifact['root_type']}".encode()
        (collision_dir / artifact["slp_path"]).write_bytes(new_bytes)
        artifact["checksum"] = hashlib.sha256(new_bytes).hexdigest()
        artifact["file_size"] = len(new_bytes)
    (collision_dir / "scan0K9E8BI.predictions.json").write_text(json.dumps(manifest))

    r2 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(collision_dir), "--json"],
    )
    assert r2.exit_code != 0
    assert "refusing to overwrite" in r2.output.lower() or "different checksum" in r2.output.lower()
    # The error has to name an identity that can actually clear the object: bloom_workflows holds
    # no DELETE on cyl-intermediates, so "delete it" alone is not an actionable instruction.
    assert "bloom_admin" in r2.output or "service_role" in r2.output

    # The orphaned bytes must still be byte-identical after the refused collision.
    for object_path, original in orphan_bytes.items():
        current = authed_cli.storage.from_("cyl-intermediates").download(object_path)
        assert current == original, f"{object_path} was overwritten"


def test_redelivery_with_recomputed_bytes_is_a_noop_not_a_collision(seeded, authed_cli, tmp_path):
    """The sleap-roots-pipeline#76 regression, end to end against a real Bloom.

    An already-ingested envelope re-delivered with .slp files whose bytes DIFFER (predict is not
    byte-reproducible, so the same inputs give the same idempotency_key and different bytes) used
    to fail: the object path embeds the key, so the recomputed bytes targeted an occupied address
    and upload_blob refused to overwrite -- before the RPC's first-writer-wins gate could make
    the delivery the benign no-op it was always documented to be.

    The load-bearing assertion is the last one: the stored bytes must still be the FIRST run's.
    A "fix" that tolerated the collision by overwriting would satisfy everything above it.
    """
    import hashlib

    scan_id, envelope, idem, cur = seeded
    path = tmp_path / "env.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")

    r1 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(PREDICTIONS_DIR), "--json"],
    )
    assert r1.exit_code == 0, r1.output
    assert json.loads(r1.output)["was_noop"] is False

    cur.execute(
        "SELECT s3_location, checksum FROM cyl_scan_intermediates csi "
        "JOIN cyl_trait_sources s ON s.id = csi.source_id "
        "WHERE s.idempotency_key = %s ORDER BY root_type",
        (idem,),
    )
    stored = cur.fetchall()
    assert len(stored) == 2
    first_run_bytes = {
        loc: authed_cli.storage.from_("cyl-intermediates").download(loc) for loc, _ in stored
    }

    # A recompute: same scan, same key, genuinely different bytes whose checksums match their
    # own manifest (so client-side verification passes and only the storage comparison could
    # object). Same recipe as the collision fixture -- the difference is that here the key IS
    # already ingested, so no upload should be attempted at all.
    recomputed_dir = tmp_path / "predictions_recomputed"
    recomputed_dir.mkdir()
    manifest = json.loads((PREDICTIONS_DIR / "scan0K9E8BI.predictions.json").read_text())
    for artifact in manifest["artifacts"]:
        new_bytes = f"recomputed bytes for {artifact['root_type']}".encode()
        (recomputed_dir / artifact["slp_path"]).write_bytes(new_bytes)
        artifact["checksum"] = hashlib.sha256(new_bytes).hexdigest()
        artifact["file_size"] = len(new_bytes)
    (recomputed_dir / "scan0K9E8BI.predictions.json").write_text(json.dumps(manifest))

    r2 = CliRunner().invoke(
        cli,
        ["cyl", "ingest-result", str(path), "--predictions-dir", str(recomputed_dir), "--json"],
    )

    assert r2.exit_code == 0, r2.output
    assert json.loads(r2.output)["was_noop"] is True

    # No duplicate source, and no new intermediates rows.
    cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
    assert cur.fetchone()[0] == 1
    cur.execute(
        "SELECT count(*) FROM cyl_scan_intermediates csi "
        "JOIN cyl_trait_sources s ON s.id = csi.source_id WHERE s.idempotency_key = %s",
        (idem,),
    )
    assert cur.fetchone()[0] == 2

    # First-writer-wins on the bytes themselves.
    for location, original in first_run_bytes.items():
        current = authed_cli.storage.from_("cyl-intermediates").download(location)
        assert current == original, f"{location} was overwritten by the re-delivery"
