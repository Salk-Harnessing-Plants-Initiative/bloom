"""The fakes every scrna command test signs in with: a writer session, storage and a database."""

import httpx
import pytest
from scrna_fake_db import database
from test_scrna_transfer import FakeStorage

from bloomctl.scrna import _object, _session, _transfer


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def env(monkeypatch, tmp_path, storage):
    """A writer session, fake storage, and a resume cache under tmp_path."""
    state = {"role": "bloom_writer", "client": database()}
    monkeypatch.setattr(
        _session, "connect",
        lambda profile: _session.Connection(
            client=state["client"],
            endpoint=_transfer.Endpoint("http://api.test", "anon", "tok"),
            role=state["role"],
        ),
    )
    monkeypatch.setattr(
        _transfer, "open_client",
        lambda: httpx.Client(transport=httpx.MockTransport(storage.handle)),
    )
    monkeypatch.setattr(_object, "staging_dir", lambda: tmp_path / "stage")
    monkeypatch.setattr(_transfer, "CHUNK_BYTES", 1024)
    return state
