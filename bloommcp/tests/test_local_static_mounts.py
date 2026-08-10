"""bloom_mcp.server.build_app()'s local-mode `/output`/`/plots` StaticFiles
mounts (#642) — self-serving the local storage/plots roots so a
BLOOM_STORAGE_URL/BLOOM_PLOTS_URL built from storage_backend.self_serve_base_url()
actually resolves when bloommcp runs standalone (no docker-compose).

Both mounts are gated on `is_local_backend()`; neither is present on the
default (Supabase) backend. `IdentityMiddleware` wraps the whole app (outside
every `Mount`, per `build_app()`'s own docstring), so it still applies to
these routes even though nothing inside a `StaticFiles` mount does its own
auth — see the garbage-identity-header tests at the bottom.
"""

from __future__ import annotations

import time

import jwt
import pytest
from starlette.routing import Mount
from starlette.testclient import TestClient

import bloom_mcp.experiment_utils as eu
import bloom_mcp.storage_backend as sb
from bloom_mcp import server

SECRET = "test-jwt-secret"


def _mount_paths(app):
    return {r.path for r in app.routes if isinstance(r, Mount)}


# ── absent on the default backend ───────────────────────────────────────────


def test_output_mount_absent_on_default_backend(monkeypatch):
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    sb.reset_backend_for_tests()
    assert "/output" not in _mount_paths(server.build_app())


def test_plots_mount_absent_on_default_backend(monkeypatch):
    monkeypatch.delenv("BLOOM_STORAGE_BACKEND", raising=False)
    sb.reset_backend_for_tests()
    assert "/plots" not in _mount_paths(server.build_app())


# ── serving a real file: granular explicit-override tier ───────────────────


def test_output_mount_serves_local_root_file(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    nested = tmp_path / "bloommcp_output" / "qc_x" / "v1"
    nested.mkdir(parents=True)
    (nested / "_cleaned.csv").write_bytes(b"a,b\n1,2\n")

    with TestClient(server.build_app()) as client:
        resp = client.get("/output/bloommcp_output/qc_x/v1/_cleaned.csv")
    assert resp.status_code == 200
    assert resp.content == b"a,b\n1,2\n"


def test_plots_mount_serves_plot_file(monkeypatch, tmp_path):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    # PLOTS_DIR is a frozen module-level constant resolved once at interpreter
    # import time — setting only BLOOM_PLOTS_DIR would leave build_app()'s
    # already-imported PLOTS_DIR pointing elsewhere. Monkeypatch the constant
    # directly, matching test_local_mode.py's established convention.
    monkeypatch.setattr(eu, "PLOTS_DIR", tmp_path)
    (tmp_path / "histogram_x.png").write_bytes(b"\x89PNG-fake-bytes")

    with TestClient(server.build_app()) as client:
        resp = client.get("/plots/histogram_x.png")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG-fake-bytes"


# ── serving a real file: BLOOM_LOCAL_ROOT-derived tier ──────────────────────


def test_output_mount_serves_local_root_file_via_local_root_tier(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.delenv("BLOOM_STORAGE_LOCAL_ROOT", raising=False)
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    nested = root / "output" / "bloommcp_output" / "qc_x" / "v1"
    nested.mkdir(parents=True)
    (nested / "_cleaned.csv").write_bytes(b"a,b\n1,2\n")

    with TestClient(server.build_app()) as client:
        resp = client.get("/output/bloommcp_output/qc_x/v1/_cleaned.csv")
    assert resp.status_code == 200
    assert resp.content == b"a,b\n1,2\n"


def test_plots_mount_serves_plot_file_via_local_root_tier(monkeypatch, tmp_path):
    root = tmp_path / "local_root"
    root.mkdir()
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    sb.reset_backend_for_tests()
    plots_dir = root / "plots"
    plots_dir.mkdir()
    monkeypatch.setattr(eu, "PLOTS_DIR", plots_dir)
    (plots_dir / "histogram_x.png").write_bytes(b"\x89PNG-fake-bytes")

    with TestClient(server.build_app()) as client:
        resp = client.get("/plots/histogram_x.png")
    assert resp.status_code == 200
    assert resp.content == b"\x89PNG-fake-bytes"


# ── IdentityMiddleware still wraps the new mounts ───────────────────────────


@pytest.mark.parametrize("path", ["/output/x", "/plots/x"])
def test_garbage_identity_header_rejected_on_local_mounts(monkeypatch, tmp_path, path):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    monkeypatch.setattr(eu, "PLOTS_DIR", tmp_path)

    with TestClient(server.build_app()) as client:
        resp = client.get(path, headers={"X-Bloom-Identity": "garbage"})
    assert resp.status_code == 401


def _valid_identity_token():
    payload = {
        "sub": "11111111-1111-1111-1111-111111111111",
        "aud": "authenticated",
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.mark.parametrize("path", ["/output/x", "/plots/x"])
def test_missing_file_returns_404_not_500(monkeypatch, tmp_path, path):
    """A GET for a key/file that doesn't exist under the mounted root is a
    clean 404 from StaticFiles, not an unhandled error — no host path should
    leak into the response either way."""
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(tmp_path))
    sb.reset_backend_for_tests()
    monkeypatch.setattr(eu, "PLOTS_DIR", tmp_path)

    with TestClient(server.build_app()) as client:
        resp = client.get(path)
    assert resp.status_code == 404
    assert str(tmp_path) not in resp.text
