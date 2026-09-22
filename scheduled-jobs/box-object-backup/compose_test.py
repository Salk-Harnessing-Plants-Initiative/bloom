"""Tests for compose.yml, the file every run of the job is started from.

Parsed rather than grepped, so a setting left in a comment cannot satisfy a
check the real key fails.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest
import yaml

import backup_objects as job
import rclone_daemon

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
COMPOSE = HERE / "compose.yml"
PROD_STACK = REPO / "docker-compose.prod.yml"
WORKFLOW = REPO / ".github" / "workflows" / "box-object-backup.yml"
DOCKERFILE = HERE / "Dockerfile"

SERVICE = "box-object-backup"
PROJECT = "bloom-box-object-backup"
CREDENTIALS = {"POSTGRES_PASSWORD", "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"}
ALLOWED_ENVIRONMENT = {"HOME", "OBJECT_BACKUP_RCLONE_CONFIG", *CREDENTIALS}
RCLONE_FOLDER = "/home/bloom-deploy/.config/rclone-box-object-backup"
# (source, target) of every mount: the state, the job's own rclone folder, the settings.
MOUNTS = {
    (job.DEFAULT_STATE_DIR, job.DEFAULT_STATE_DIR),
    (RCLONE_FOLDER, "/config/rclone"),
    ("../../.env.prod.defaults", "/etc/box-object-backup/settings.env"),
}
# Every key the service may set; anything else (pid, privileged, devices...) is new review.
SERVICE_KEYS = {
    "image",
    "init",
    "restart",
    "read_only",
    "tmpfs",
    "security_opt",
    "cap_drop",
    "mem_limit",
    "memswap_limit",
    "stop_grace_period",
    "networks",
    "environment",
    "volumes",
    "entrypoint",
}
# GitHub force-stops a cancelled run's remaining steps after 5 minutes.
GITHUB_CANCEL_WINDOW_SECONDS = 300
# What the summary step needs after the cancel step: one ssh and the renderer.
SUMMARY_ALLOWANCE_SECONDS = 60


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def service(compose: dict) -> dict:
    return compose["services"][SERVICE]


def _bind(service: dict, target: str) -> dict:
    found = [v for v in service["volumes"] if v["target"] == target]
    assert len(found) == 1, f"expected one mount at {target}, found {found}"
    return found[0]


def _seconds(value: str) -> int:
    match = re.fullmatch(r"(\d+)(s|m)", str(value))
    assert match, f"unexpected duration {value!r}"
    return int(match.group(1)) * (60 if match.group(2) == "m" else 1)


class TestItStandsApartFromTheStack:
    def test_it_has_its_own_project_name(self, compose: dict):
        assert compose["name"] == PROJECT

    def test_it_defines_only_the_backup_service(self, compose: dict):
        assert list(compose["services"]) == [SERVICE]

    def test_the_stack_does_not_define_it(self):
        assert SERVICE not in PROD_STACK.read_text(encoding="utf-8")

    def test_it_runs_once_and_is_never_restarted(self, service: dict):
        assert service["restart"] == "no"
        assert service["init"] is True


class TestOnlyProductionsNetwork:
    def test_exactly_one_network_is_declared(self, compose: dict, service: dict):
        assert list(compose["networks"]) == ["prod"]
        assert service["networks"] == ["prod"]

    def test_it_is_the_stacks_own_network(self, compose: dict):
        """Named the way Docker names the stack's `supanet`, so a rename there fails here."""
        stack = yaml.safe_load(PROD_STACK.read_text(encoding="utf-8"))
        assert "supanet" in stack["networks"]
        assert compose["networks"]["prod"] == {
            "external": True,
            "name": f"{stack['name']}_supanet",
        }

    def test_staging_is_never_named(self, compose: dict):
        # The settings, not the comments: the header says where to run it from.
        assert "staging" not in yaml.safe_dump(compose)


class TestNothingListensAndNothingReachesDocker:
    def test_no_port_is_published_or_exposed(self, service: dict):
        assert "ports" not in service
        assert "expose" not in service
        assert "network_mode" not in service

    def test_the_docker_socket_is_not_mounted(self, service: dict):
        for volume in service["volumes"]:
            assert "docker.sock" not in str(volume["source"])


class TestHardening:
    def test_the_root_filesystem_is_read_only(self, service: dict):
        assert service["read_only"] is True
        assert service["tmpfs"] == ["/tmp:mode=1777,size=64m"]

    def test_it_drops_every_capability_and_cannot_gain_any(self, service: dict):
        assert service["cap_drop"] == ["ALL"]
        assert "no-new-privileges:true" in service["security_opt"]
        assert "privileged" not in service
        assert "cap_add" not in service

    def test_swap_cannot_double_the_memory_limit(self, service: dict):
        assert service["mem_limit"] == service["memswap_limit"]

    def test_it_sets_nothing_beyond_the_reviewed_keys(self, service: dict):
        assert set(service) == SERVICE_KEYS

    def test_it_mounts_exactly_the_state_the_rclone_folder_and_the_settings(
        self, service: dict
    ):
        """A wider mount, such as the whole deploy tree, would expose .env.prod."""
        assert {(v["source"], v["target"]) for v in service["volumes"]} == MOUNTS


class TestTheImage:
    def test_it_is_pinned_by_digest(self, service: dict):
        assert re.fullmatch(
            r"ghcr\.io/salk-harnessing-plants-initiative/box-object-backup"
            r":sha-[0-9a-f]{7,40}@sha256:[0-9a-f]{64}",
            service["image"],
        ), service["image"]

    def test_the_entrypoint_runs_the_images_own_script(self, service: dict):
        entrypoint = re.search(r"^ENTRYPOINT (.+)$", DOCKERFILE.read_text(), re.M)
        assert entrypoint, "the Dockerfile has no ENTRYPOINT"
        script = yaml.safe_load(entrypoint.group(1))[-1]
        assert service["entrypoint"][:2] == ["python3", script]


class TestTheStateDirectory:
    def test_it_is_the_path_the_job_and_the_workflow_use(self, service: dict):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        state = _bind(service, job.DEFAULT_STATE_DIR)
        assert state["source"] == state["target"] == workflow["env"]["STATE_DIR"]

    def test_a_missing_directory_fails_the_run(self, service: dict):
        """Docker would otherwise create it owned by root, and the job could not write it."""
        state = _bind(service, job.DEFAULT_STATE_DIR)
        assert state["bind"]["create_host_path"] is False
        assert not state.get("read_only")


class TestSettingsAndCredentials:
    def test_the_environment_is_exactly_the_allowed_set(self, service: dict):
        assert set(service["environment"]) == ALLOWED_ENVIRONMENT
        assert ALLOWED_ENVIRONMENT - {"HOME"} <= set(job.ENV_KEYS)

    @pytest.mark.parametrize("key", sorted(CREDENTIALS))
    def test_each_credential_is_required(self, service: dict, key: str):
        assert service["environment"][key].startswith(f"${{{key}:?"), (
            f"{key} would be blank without --env-file instead of refusing to start"
        )

    def test_rclone_can_save_the_refreshed_box_token(self, service: dict):
        """Box refresh tokens are single-use, so each new one has to reach the disk.

        The folder is mounted, not the file: rclone saves by renaming a temp
        file over rclone.conf, which a single-file bind mount refuses.
        """
        config = PurePosixPath(service["environment"]["OBJECT_BACKUP_RCLONE_CONFIG"])
        folder = _bind(service, str(config.parent))
        assert not folder.get("read_only")
        assert folder["bind"]["create_host_path"] is False

    def test_the_rclone_folder_is_this_jobs_own(self, service: dict):
        """The weekly Postgres backup refreshes its own login in ~/.config/rclone."""
        assert _bind(service, "/config/rclone")["source"] == RCLONE_FOLDER

    def test_the_settings_are_the_committed_defaults(self, service: dict):
        target = service["entrypoint"][service["entrypoint"].index("--env-file") + 1]
        settings = _bind(service, target)
        assert (HERE / settings["source"]).resolve() == REPO / ".env.prod.defaults"
        assert settings["read_only"] is True

    def test_the_deploy_env_file_is_not_mounted(self, service: dict):
        """It holds the JWT signing keys, which only auth may receive."""
        assert "env_file" not in service
        for volume in service["volumes"]:
            name = Path(str(volume["source"])).name
            assert not name.startswith(".env") or name == ".env.prod.defaults", name

    @pytest.mark.parametrize("secret", ["JWT", "ANON_KEY", "SERVICE_ROLE"])
    def test_no_stack_secret_is_named(self, secret: str):
        assert secret not in COMPOSE.read_text(encoding="utf-8")


class TestStopping:
    def test_the_grace_period_outlasts_rclones_own_stop(self, service: dict):
        assert (
            _seconds(service["stop_grace_period"]) > rclone_daemon.STOP_TIMEOUT_SECONDS
        )

    @staticmethod
    def _cancel_step() -> dict:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        return next(
            s
            for s in workflow["jobs"]["mirror"]["steps"]
            if s.get("name", "").startswith("Ask the host to stop")
        )

    def test_the_cancel_step_waits_longer_than_the_grace_period(self, service: dict):
        timeout = self._cancel_step()["timeout-minutes"] * 60
        assert timeout > _seconds(service["stop_grace_period"])

    def test_the_stop_and_the_summary_fit_githubs_cancel_window(self):
        timeout = self._cancel_step()["timeout-minutes"] * 60
        assert timeout + SUMMARY_ALLOWANCE_SECONDS <= GITHUB_CANCEL_WINDOW_SECONDS


def _compose_config(env: dict[str, str]) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in CREDENTIALS}
    return subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), "config", "--quiet"],
        env={**base, **env},
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not installed")
class TestComposeReadsIt:
    def test_it_is_valid_with_the_credentials_set(self):
        result = _compose_config({key: "placeholder" for key in CREDENTIALS})
        if "is not a docker command" in result.stderr:
            pytest.skip("docker compose is not installed")
        assert result.returncode == 0, result.stderr

    def test_it_refuses_without_the_credentials(self):
        result = _compose_config({})
        if "is not a docker command" in result.stderr:
            pytest.skip("docker compose is not installed")
        assert result.returncode != 0
        assert "pass --env-file .env.prod" in result.stderr
