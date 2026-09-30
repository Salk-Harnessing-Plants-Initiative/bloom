"""Helpers shared by the single-cell loaders' integration tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import httpx

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"


def load_script(name: str):
    """Import scripts/<name>.py. The loaders import the shared helper themselves,
    so every loader loaded here raises the same IngestError."""
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class LoseReply(httpx.BaseTransport):
    """Sends every request, and loses the reply to the chosen one after it committed."""

    def __init__(self, lose):
        self.inner, self.lose = httpx.HTTPTransport(), lose

    def handle_request(self, request):
        response = self.inner.handle_request(request)
        if self.lose(request):
            response.read()
            response.close()
            raise httpx.ReadTimeout("the reply was lost", request=request)
        return response


def losing(table: str | None = None, nth: int = 1, *, upload: bool = False):
    """A client factory whose nth insert into `table`, or nth upload, is sent and
    then times out."""
    seen = []

    def lose(request):
        path = request.url.path
        hit = "/storage/v1/object/" in path if upload else path.endswith(f"/rest/v1/{table}")
        if request.method == "POST" and hit:
            seen.append(1)
            return len(seen) == nth
        return False

    def create_client(url, key):
        from supabase.lib.client_options import SyncClientOptions

        from supabase import create_client
        return create_client(url, key, options=SyncClientOptions(
            httpx_client=httpx.Client(transport=LoseReply(lose), timeout=120)))
    return create_client


def signer(api, accounts: dict, address: tuple[str, str], marker_path: Path):
    """sign_in(role, create_client) -> a Writer from the loader's own helper `api`,
    so its refusals are the loader's IngestError."""
    def make(role: str = "writer", create_client=None):
        account = accounts[role]
        extra = {"create_client": create_client} if create_client else {}
        session = api.sign_in(*address, account["email"], account["password"], **extra)
        return api.Writer(session, api.Marker(marker_path, wait_s=0))
    return make
