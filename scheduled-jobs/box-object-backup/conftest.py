"""No test in this directory may open a network connection.

The suite is advertised as needing no network, no database and no Box account,
and it was not true: thirty tests reached a real socket on 127.0.0.1:5572
because `publish_report` and `publish_ledger` build their own `RcloneRC` from
the daemon's credentials rather than taking the one the run already holds, so
faking `wait_for_daemon` did not cover them.

That port is `rclone_rc.DEFAULT_RC_PORT` — the port this job's own rclone
daemon binds on the deploy host. The tests passed only because nothing was
listening on the machines they ran on. On the deploy host during a seed
something is: a daemon holding the Box OAuth token and MinIO's root
credentials, and these tests would have POSTed `operations/copyfile` and
`operations/stat` at it, with a Basic-auth header.

The consequences were not only theoretical. Every one of those tests took the
`except RcloneError` branch, so `publish_report`'s success path had never been
executed once; and `RcloneRC` defaults to a 900-second timeout, so on any host
where 5572 is filtered rather than refused they hang instead of failing —
which is the most likely explanation for the intermittent failures three
reviewers reported and none could reproduce.

So the ban is enforced here rather than left to each fixture remembering.
Anything that genuinely needs the network says so with
`@pytest.mark.needs_network`, which makes it visible in one grep.
"""

from __future__ import annotations

import socket

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "needs_network: this test really does contact something remote",
    )


def _refuse(self, address):
    raise RuntimeError(
        f"this test opened a network connection to {address!r}. The "
        "scheduled-jobs suite runs with no network, database or Box account: "
        "fake it at the process boundary, or mark the test "
        "@pytest.mark.needs_network if it genuinely needs one."
    )


@pytest.fixture(autouse=True)
def no_network(request, monkeypatch):
    if request.node.get_closest_marker("needs_network"):
        return
    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", _refuse)
