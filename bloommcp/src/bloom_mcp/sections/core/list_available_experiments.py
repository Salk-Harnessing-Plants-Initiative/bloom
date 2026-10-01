"""list_available_experiments — discovery tool: list available experiments.

Not a ``sleap-roots-analyze`` wrapper — reads through the injected
``ExperimentReader`` port. Always-included in the agent's tool set (see
``ALWAYS_INCLUDE_MCP_TOOLS`` in ``langchain/helpers/foundational_tools.py``).
"""

from datetime import UTC, datetime, timedelta
from typing import Optional

from bloom_mcp.tools import _ports

# bloom#831: a pg_cron job inside Postgres refreshes this cache nightly at 06:00 UTC, in every
# environment, and a weekly job re-queues every experiment. A row is therefore at most a day
# behind a new result, and at most a week behind an edit the change log can't see. A missed or
# delayed run still looks identical to ordinary lag, so elapsed time past a couple of days is
# flagged explicitly rather than printed as a plain "as of" timestamp.
_STALE_AFTER = timedelta(days=2)


def _traits_note(updated_at: Optional[str], *, now: Optional[datetime] = None) -> str:
    """Render the freshness caveat for a cached `n_traits` count.

    `updated_at` is `None` for "never refreshed" (no cache row yet) and for a
    live/pinned call (no cache involved at all) -- both cases render
    identically here since this tool never pins a source/run.
    """
    if not updated_at:
        return " (never refreshed)"
    try:
        parsed = datetime.fromisoformat(updated_at)
    except ValueError:
        # Unexpected format from PostgREST -- surface the raw value rather
        # than fail the whole listing over a display-only caveat.
        return f" (as of {updated_at})"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    elapsed = (now or datetime.now(UTC)) - parsed
    if elapsed > _STALE_AFTER:
        return (
            f" (as of {updated_at}, {elapsed.days}d ago -- "
            f"trait counts refresh nightly, not on every write; "
            f"a timestamp older than a day means a scheduled run did not complete)"
        )
    return f" (as of {updated_at})"


def list_available_experiments() -> str:
    """List all experiments available for analysis.

    Scans the data directory and shows each experiment with its row count,
    trait count, and auto-detected genotype column. Use this first to
    see what experiments are available before running analysis.
    """
    experiments = _ports.reader().list_experiments()

    if not experiments:
        return "No experiments available"

    lines = [f"Available experiments ({len(experiments)} total):\n"]

    for exp in experiments:
        traits_note = _traits_note(exp.trait_columns_updated_at)
        lines.append(
            f"  {exp.filename}\n"
            f"    Experiment: {exp.experiment_name}\n"
            f"    Samples: {exp.rows}, Traits: {exp.trait_columns}{traits_note}, "
            f"Total columns: {exp.total_columns}\n"
            f"    Genotype column: {exp.genotype_col or 'not detected'}\n"
            f"    Sample ID column: {exp.sample_id_col or 'not detected'}"
        )

    lines.append(
        f"\nTo analyze an experiment, use its identifier (e.g., '{experiments[0].filename}')"
    )

    return "\n".join(lines)
