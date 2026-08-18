"""The workflows service must run one uvicorn worker.

Video generation serialises per scan with a `threading.Lock` in
`services/workflows/video.py`. That lock is a module global, so it holds only within one
process: with two workers, two requests for the same scan land in different interpreters,
both read the recorded frame count before either uploads, and both write the same
unversioned `cyl-videos/{scan_id}.mp4`. The worse encode can land last and win.

The service ran `--workers 2` until this was pinned, which made the lock a no-op in
production while the code read as if the race were closed. Raising the count again is
only safe once the lock moves into the database.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"


def _workflows_command() -> list[str]:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    return compose["services"]["workflows"]["command"]


def test_workflows_runs_a_single_uvicorn_worker():
    command = _workflows_command()

    assert "--workers" in command, (
        "workflows must state its worker count explicitly — the Dockerfile CMD defaults to "
        "one, and a compose command that omits the flag hides which value is in force"
    )
    assert command[command.index("--workers") + 1] == "1", (
        "video.py's per-scan lock only serialises within one process; more than one worker "
        "reopens the overwrite race on cyl-videos/{scan_id}.mp4"
    )
