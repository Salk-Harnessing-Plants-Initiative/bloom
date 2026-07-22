"""list_available_experiments — discovery tool: list experiment CSV files.

Not a ``sleap-roots-analyze`` wrapper — reads through the injected
``ExperimentReader`` port. Always-included in the agent's tool set (see
``ALWAYS_INCLUDE_MCP_TOOLS`` in ``langchain/routes/chat.py``).
"""

from bloom_mcp.tools import _ports


def list_available_experiments() -> str:
    """List all experiment CSV files available for analysis.

    Scans the data directory and shows each file with its row count,
    trait count, and auto-detected genotype column. Use this first to
    see what experiments are available before running analysis.
    """
    experiments = _ports.reader().list_experiments()

    if not experiments:
        return "No experiments available"

    lines = [f"Available experiments ({len(experiments)} files):\n"]

    for exp in experiments:
        lines.append(
            f"  {exp.filename}\n"
            f"    Experiment: {exp.experiment_name}\n"
            f"    Samples: {exp.rows}, Traits: {exp.trait_columns}, "
            f"Total columns: {exp.total_columns}\n"
            f"    Genotype column: {exp.genotype_col or 'not detected'}\n"
            f"    Sample ID column: {exp.sample_id_col or 'not detected'}"
        )

    lines.append(
        f"\nTo analyze an experiment, use its filename (e.g., '{experiments[0].filename}')"
    )

    return "\n".join(lines)
