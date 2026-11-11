"""deploy/smtp-egress/apply.sh: the port-25 rule, checked without touching a real firewall."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "deploy" / "smtp-egress" / "apply.sh"
TAG = "bloom-smtp-egress"


def _run(*args: str, path: str | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "PATH": f"{path}:{os.environ['PATH']}"} if path else None
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _fake_iptables(tmp_path: Path, existing: list[str]) -> Path:
    """An iptables stand-in: `-S DOCKER-USER` lists `existing`; other calls are recorded."""
    log = tmp_path / "calls"
    listing = tmp_path / "listing"
    listing.write_text("\n".join(["-N DOCKER-USER", *existing]) + "\n")
    fake = tmp_path / "iptables"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$1" = "-S" ]; then cat {listing}; exit 0; fi\n'
        f'echo "$*" >> {log}\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return log


def test_print_lists_the_drop_then_the_three_accepts_above_it():
    out = _run("--print", str(REPO / ".env.prod.defaults"))
    assert out.returncode == 0, out.stderr
    rules = [line for line in out.stdout.splitlines() if line.startswith("iptables")]
    assert rules[0].endswith("-j DROP") and "-s 172.19.0.0/16" in rules[0]
    accepts = [r.split("-s ")[1].split()[0] for r in rules[1:]]
    assert accepts == ["172.19.0.2/32", "172.19.0.3/32", "172.19.0.4/32"]
    assert all(
        f"--comment {TAG}" in r and "--dport 25" in r and "-I DOCKER-USER" in r
        for r in rules
    )


def test_both_environments_get_their_own_rules():
    out = _run(
        "--print", str(REPO / ".env.prod.defaults"), str(REPO / ".env.staging.defaults")
    )
    assert out.returncode == 0, out.stderr
    assert "-s 172.19.0.0/16" in out.stdout and "-s 172.18.0.0/16" in out.stdout
    assert out.stdout.count("-j ACCEPT") == 6 and out.stdout.count("-j DROP") == 2


def test_apply_removes_only_its_own_old_rules_first(tmp_path):
    log = _fake_iptables(
        tmp_path,
        [
            f"-A DOCKER-USER -s 172.19.0.0/16 -p tcp -m tcp --dport 25 -m comment --comment {TAG} -j DROP",
            "-A DOCKER-USER -j RETURN",
        ],
    )
    out = _run(str(REPO / ".env.prod.defaults"), path=str(tmp_path))
    assert out.returncode == 0, out.stderr
    calls = log.read_text().splitlines()
    assert calls[0].startswith("-D DOCKER-USER -s 172.19.0.0/16") and TAG in calls[0]
    assert not any("RETURN" in c for c in calls)
    assert [c.split()[0] for c in calls[1:]] == ["-I"] * 4


def test_remove_only_removes(tmp_path):
    log = _fake_iptables(
        tmp_path,
        [
            f"-A DOCKER-USER -s 172.19.0.2/32 -p tcp -m tcp --dport 25 -m comment --comment {TAG} -j ACCEPT"
        ],
    )
    out = _run("--remove", path=str(tmp_path))
    assert out.returncode == 0, out.stderr
    assert [c.split()[0] for c in log.read_text().splitlines()] == ["-D"]


def test_a_missing_setting_stops_before_any_rule(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SUPANET_SUBNET=172.19.0.0/16\nMAIL_IP=172.19.0.2\n")
    out = _run("--print", str(env))
    assert out.returncode != 0
    assert "AUTH_IP is not set" in out.stderr
    assert "iptables" not in out.stdout


def test_no_env_file_is_a_usage_error():
    assert _run().returncode == 2
