"""Empty shell — outlier detection now lives in workflows.outlier.run_outlier_workflow.

Kept temporarily so `server.py`'s `outlier_tools.register(mcp)` call still
resolves. Will be deleted entirely in the registration cleanup phase.
"""


def register(mcp):
    """No-op — all outlier MCP exposure moved to workflows.outlier."""
    return
