"""Plot rendering helper shared across all chart-emitting tools.

Used by `langchain/tools/cyl_viz_tools.py` today; future scRNA / other viz
modules can call it the same way with their own `namespace` value so
filenames stay disambiguated by tool family.

The matplotlib import lives at module top so import-time failures surface
on agent startup, not on the first plot call.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import matplotlib

# Non-interactive backend — required for headless container execution.
matplotlib.use("Agg")

from matplotlib.figure import Figure  # noqa: E402


def render_and_save(fig: Figure, prefix: str, namespace: str) -> str:
    """Save `fig` to BLOOM_PLOTS_DIR/<namespace>_<prefix>_<uuid8>.png and
    return its public URL constructed from BLOOM_PLOTS_URL.

    Filename shape ensures distinct tool families (e.g. `cyl_supabase`,
    `scrna_supabase`) don't collide. The 8-char uuid suffix keeps concurrent
    writes from the same prefix safe.

    Sets file mode 0o644 so the reverse proxy can serve it.
    Raises RuntimeError if either env var is unset — fail loud, not silent.
    """
    plots_dir = os.environ.get("BLOOM_PLOTS_DIR")
    plots_url = os.environ.get("BLOOM_PLOTS_URL")
    if not plots_dir:
        raise RuntimeError(
            "BLOOM_PLOTS_DIR env var is not set — cannot save plot. "
            "Check docker-compose env wiring for the langchain-agent service."
        )
    if not plots_url:
        raise RuntimeError(
            "BLOOM_PLOTS_URL env var is not set — cannot construct plot URL. "
            "Check docker-compose env wiring for the langchain-agent service."
        )

    uuid8 = uuid.uuid4().hex[:8]
    filename = f"{namespace}_{prefix}_{uuid8}.png"
    out_path = Path(plots_dir) / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    out_path.chmod(0o644)
    return f"{plots_url.rstrip('/')}/{filename}"
