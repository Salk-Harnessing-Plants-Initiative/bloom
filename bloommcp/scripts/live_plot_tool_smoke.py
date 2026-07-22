"""Live plot-tool smoke — call a real plotting tool through the running dev-stack
container over its actual MCP transport (issue #472).

Unlike ``live_persistence_smoke.py`` (which runs in-process on the host and
deliberately overrides ``BLOOM_TRAITS_DIR``/``BLOOM_OUTPUT_DIR``/``BLOOM_PLOTS_DIR``
to host temp dirs before ``import bloom_mcp``), this script never imports
``bloom_mcp`` — it is a plain network client (``fastmcp.Client``, the same library
bloommcp itself depends on) connecting to the already-running ``bloommcp``
container at ``http://localhost:$BLOOMMCP_PORT/mcp``. That distinction is the
point: plotting tools always write straight to the container's bind-mounted
``BLOOM_PLOTS_DIR`` regardless of storage backend, so the only way to prove the
real bind-mount write path works is to go through the real container — an
in-process, env-overridden call (like ``live_persistence_smoke.py``'s) would
never touch the actual mount and would pass even if the underlying bind-mount
permission bug (this script's reason for existing) were still present.

Seeds the raw ``turface_19_raw_data.csv`` fixture into the **real**, host-side
bind-mounted ``bloommcp/data/TRAITS_DIR/`` (not a host tempdir) as
``turface_raw.csv``, so the running container can see it at its
``BLOOM_TRAITS_DIR`` (``/app/data/TRAITS_DIR``).

Env (sourced from ``.env.dev`` by the ``make bloommcp-plot-smoke`` target):
    BLOOMMCP_PORT      host port the bloommcp container publishes 8811 on
    BLOOMMCP_API_KEY   Bearer token the server validates (see auth.py)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

from fastmcp import Client

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "bloommcp" / "tests" / "fixtures" / "turface_19_raw_data.csv"
TRAITS_DIR = REPO_ROOT / "bloommcp" / "data" / "TRAITS_DIR"
PLOTS_DIR = REPO_ROOT / "bloommcp" / "data" / "PLOTS_DIR"
EXPERIMENT = "turface_raw.csv"
PNG = PLOTS_DIR / "histograms_turface_raw.png"

_CHECKS: list[tuple[str, bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _CHECKS.append((name, ok, detail))
    print(
        f"{'OK' if ok else 'FAIL'}  {name}{f' — {detail}' if detail and not ok else ''}"
    )


def _redact(secret: str, text: str) -> str:
    return text.replace(secret, "***REDACTED***") if secret else text


def _seed_fixture() -> None:
    TRAITS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURE, TRAITS_DIR / EXPERIMENT)


def _clear_stale_png() -> None:
    # A PNG left over from an earlier successful run would let the "landed on
    # PLOTS_DIR" check below pass even if this run's write silently failed.
    PNG.unlink(missing_ok=True)


async def main() -> int:
    port = os.environ.get("BLOOMMCP_PORT", "8811")
    api_key = os.environ.get("BLOOMMCP_API_KEY")
    if not api_key:
        print(
            "live_plot_tool_smoke: BLOOMMCP_API_KEY is empty — run 'make init'.",
            file=sys.stderr,
        )
        return 1

    _seed_fixture()
    _check(
        "fixture seeded into the real bind-mounted TRAITS_DIR",
        (TRAITS_DIR / EXPERIMENT).exists(),
    )
    _clear_stale_png()

    url = f"http://localhost:{port}/mcp"
    try:
        async with Client(url, auth=api_key, timeout=30, init_timeout=15) as client:
            result = await client.call_tool(
                "sleap_roots_plot_trait_histograms", {"filename": EXPERIMENT}
            )
    except Exception as exc:  # noqa: BLE001 — report, don't hide, the failure
        _check(
            "sleap_roots_plot_trait_histograms call succeeds",
            False,
            _redact(api_key, repr(exc)),
        )
        _print_summary()
        return 1

    text = result.data if isinstance(result.data, str) else str(result.data)
    _check(
        "sleap_roots_plot_trait_histograms returns a success summary, not a permission error",
        "Plot saved:" in text and "denied" not in text.lower(),
        text,
    )

    _check(
        "the PNG actually landed on the real bind-mounted PLOTS_DIR (not just claimed)",
        PNG.exists() and PNG.stat().st_size > 0,
        str(PNG),
    )

    return _print_summary()


def _print_summary() -> int:
    n_fail = sum(1 for _, ok, _ in _CHECKS if not ok)
    print(f"\n{len(_CHECKS) - n_fail}/{len(_CHECKS)} checks passed.")
    if n_fail:
        print(
            "live_plot_tool_smoke FAILED — a plotting tool could not write to "
            "PLOTS_DIR through the real container. If this is a permission "
            "error, see openspec/changes/fix-bloommcp-dev-data-dir-permissions/ "
            "and scripts/ensure_bloommcp_data_dirs.sh.",
            file=sys.stderr,
        )
        return 1
    print("live_plot_tool_smoke PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
