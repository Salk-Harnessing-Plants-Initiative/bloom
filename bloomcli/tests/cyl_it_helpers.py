"""Shared harness for the env-gated cyl ingest integration + dev-stack smoke tests.

Not a test module (no ``test_`` prefix, so pytest won't collect it). These helpers
seed a scan, build a model-valid envelope retargeted to the seeded images, and
clean up — used by both ``test_cyl_ingest_integration.py`` and
``test_dev_stack_smoke.py``.
"""

import json
import uuid
from pathlib import Path


def seed_scan(cur, n_images: int = 2):
    """Insert one scan with ``n_images`` images (FK parents); return (scan_id, image_ids)."""
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    scan_id = cur.fetchone()[0]
    img_ids = []
    for _ in range(n_images):
        cur.execute("INSERT INTO cyl_images (scan_id) VALUES (%s) RETURNING id", (scan_id,))
        img_ids.append(cur.fetchone()[0])
    return scan_id, img_ids


def envelope_for(fixture_path, img_ids):
    """The committed fixture, retargeted to ``img_ids`` with a fresh derived key.

    Points inputs.image_ids at the seeded rows and gives inputs.images_checksum a
    unique value so the model-derived idempotency_key is fresh per run (the key
    hashes the checksum + scan_key/models/params, NOT image_ids, and the model
    rejects a mismatching key). Lets the model compute the key, then bakes it back
    in — the CLI still sends this original dict. Returns (envelope, idempotency_key).
    """
    from sleap_roots_contracts import ResultEnvelope

    env = json.loads(Path(fixture_path).read_text(encoding="utf-8"))
    env["provenance"]["inputs"]["image_ids"] = [str(i) for i in img_ids]
    env["provenance"]["inputs"]["images_checksum"] = f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}"
    env["provenance"].pop("idempotency_key", None)
    idem = ResultEnvelope.model_validate(env).provenance.idempotency_key
    env["provenance"]["idempotency_key"] = idem
    return env, idem


def cleanup(cur, scan_id, idem):
    """Delete the written rows (by idempotency_key) then the seeded scan/images (FK-safe)."""
    cur.execute(
        "DELETE FROM cyl_scan_traits WHERE source_id IN "
        "(SELECT id FROM cyl_trait_sources WHERE idempotency_key = %s)",
        (idem,),
    )
    cur.execute(
        "DELETE FROM cyl_scan_intermediates WHERE source_id IN "
        "(SELECT id FROM cyl_trait_sources WHERE idempotency_key = %s)",
        (idem,),
    )
    cur.execute("DELETE FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
    cur.execute("DELETE FROM cyl_images WHERE scan_id = %s", (scan_id,))
    cur.execute("DELETE FROM cyl_scans WHERE id = %s", (scan_id,))
