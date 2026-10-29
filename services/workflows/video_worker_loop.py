"""The claim loop both video workers run: claim, render, then complete or fail.

One job at a time. Progress is written to the job row, throttled; a watchdog
fails a render that stops reporting and exits so Docker restarts the worker.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable

from video_queue import UNEXPECTED, Queue, RenderFailed, claim, complete, fail, report

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL_SECONDS = 1.0
POLL_SECONDS = float(os.environ.get("WORKFLOWS_VIDEO_POLL_SECONDS", "2"))

# Touched on every poll and progress report; the compose healthcheck reads its age.
HEARTBEAT_PATH = Path(
    os.environ.get("WORKFLOWS_VIDEO_HEARTBEAT", "/tmp/video-worker-heartbeat")
)
# Longer than the encode, the one stretch that touches nothing.
HEARTBEAT_STALE_SECONDS = 600

# A render that reports nothing for this long is stalled. Well past the silent
# encode, so only a hang trips it; a long plate that keeps reporting never does.
RENDER_STALL_SECONDS = float(os.environ.get("WORKFLOWS_VIDEO_STALL_SECONDS", "600"))
WATCHDOG_CHECK_SECONDS = 15.0
STALLED = "stalled"


def beat() -> None:
    """Mark this worker alive for the healthcheck. Never fails a render."""
    try:
        HEARTBEAT_PATH.touch()
    except OSError as exc:
        logger.warning("could not touch the heartbeat at %s: %s", HEARTBEAT_PATH, exc)


class ProgressReporter:
    """A render's progress callback: at most once per interval, always on a stage change.

    A failed report is logged and dropped. Progress is advisory, and the render
    is worth more than the row's frame count; a lapse in renewal only risks a
    redelivery, which the next claim settles.
    """

    def __init__(
        self,
        send: Callable[[str, int | None, int | None], bool],
        *,
        clock: Callable[[], float] = time.monotonic,
        interval: float = PROGRESS_INTERVAL_SECONDS,
    ):
        self._send = send
        self._clock = clock
        self._interval = interval
        self._stage: str | None = None
        self._last: float | None = None
        self.lost = False
        # Every call counts, throttled or not: the watchdog asks whether the render moves.
        self.last_activity = clock()

    def __call__(
        self, stage: str, done: int | None = None, total: int | None = None
    ) -> None:
        now = self._clock()
        self.last_activity = now
        if (
            stage == self._stage
            and self._last is not None
            and now - self._last < self._interval
        ):
            return
        self._stage = stage
        self._last = now
        beat()
        try:
            ours = self._send(stage, done, total)
        except Exception as exc:
            logger.warning("could not report progress (%s): %s", stage, exc)
            return
        if not ours and not self.lost:
            # Settled elsewhere, e.g. failed as redelivered; complete will say so too.
            logger.warning("progress refused: the job is no longer rendering")
            self.lost = True


class Watchdog:
    """Calls `on_stall` once if `progress` goes quiet for longer than `stall_seconds`.

    A thread cannot be interrupted, so the worker's answer to a stall is to give
    up the job and exit; Docker restarts it and the queue moves on.
    """

    def __init__(
        self,
        progress: ProgressReporter,
        on_stall: Callable[[float], None],
        *,
        stall_seconds: float = RENDER_STALL_SECONDS,
        check_seconds: float = WATCHDOG_CHECK_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._progress = progress
        self._on_stall = on_stall
        self._stall = stall_seconds
        self._check = check_seconds
        self._clock = clock
        self._done = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, name="render-watchdog", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._done.set()
        self._thread.join()

    def _watch(self) -> None:
        while not self._done.wait(self._check):
            quiet = self._clock() - self._progress.last_activity
            if quiet > self._stall:
                self._on_stall(quiet)
                return


# --- the claim loop ----------------------------------------------------------


Render = Callable[[object, dict, ProgressReporter], str]


class Worker:
    """Claim → render → complete or fail, one job at a time.

    On SIGTERM the current job finishes and no new one is claimed. A failed
    complete or fail call leaves the job for redelivery rather than guessing.
    """

    def __init__(
        self,
        queue: Queue,
        render: Render,
        client_factory: Callable[[], object],
        *,
        sleep: Callable[[float], None] = time.sleep,
        exit: Callable[[int], None] = os._exit,
        stall_seconds: float = RENDER_STALL_SECONDS,
        check_seconds: float = WATCHDOG_CHECK_SECONDS,
    ):
        self.queue = queue
        self.render = render
        self.client_factory = client_factory
        self.sleep = sleep
        self.exit = exit
        self.stall_seconds = stall_seconds
        self.check_seconds = check_seconds
        self.running = True

    def stop(self, signum=None, _frame=None) -> None:
        logger.info(
            "%s: signal %s, stopping after the current job", self.queue.name, signum
        )
        self.running = False

    def process_one(self, client) -> bool:
        """Claim and settle one job. True when a job was handled."""
        beat()
        # A claim error reaches run(), which reconnects: an expired session looks like this.
        job = claim(client, self.queue)
        if not job:
            return False

        job_id = job["job_id"]
        logger.info(
            "%s: claimed job %s (delivery %s)",
            self.queue.name,
            job_id,
            job.get("reads"),
        )
        progress = ProgressReporter(
            lambda stage, done, total: report(
                client, self.queue, job, stage, done, total
            )
        )

        watchdog = Watchdog(
            progress,
            lambda quiet: self._abandon(client, job, quiet),
            stall_seconds=self.stall_seconds,
            check_seconds=self.check_seconds,
        )
        watchdog.start()
        try:
            self._run_job(client, job, progress)
        finally:
            watchdog.stop()
        return True

    def _run_job(self, client, job: dict, progress: ProgressReporter) -> None:
        """Render one claimed job and settle it as its outcome says."""
        job_id = job["job_id"]
        try:
            outcome = self.render(client, job, progress)
        except RenderFailed as exc:
            code, error = exc.code, exc.error
            logger.warning(
                "%s: job %s failed (%s): %s", self.queue.name, job_id, code, error
            )
        except Exception as exc:
            code, error = UNEXPECTED, f"{type(exc).__name__}: {exc}"
            logger.exception("%s: job %s failed unexpectedly", self.queue.name, job_id)
        else:
            logger.info("%s: job %s %s", self.queue.name, job_id, outcome)
            self._settle(job_id, lambda: complete(client, self.queue, job, outcome))
            return

        self._settle(job_id, lambda: fail(client, self.queue, job, code, error))

    def _abandon(self, client, job: dict, quiet: float) -> None:
        """Fail a stalled job so it is not handed out again, then exit for a restart."""
        logger.error(
            "%s: job %s made no progress for %.0fs; failing it and restarting",
            self.queue.name,
            job["job_id"],
            quiet,
        )
        error = f"no progress for {quiet:.0f}s"
        self._settle(
            job["job_id"], lambda: fail(client, self.queue, job, STALLED, error)
        )
        self.exit(1)

    def _settle(self, job_id, call: Callable[[], bool]) -> None:
        try:
            settled = call()
        except Exception as exc:
            logger.error(
                "%s: job %s could not be settled; leaving it for redelivery: %s",
                self.queue.name,
                job_id,
                exc,
            )
            return
        if not settled:
            logger.warning(
                "%s: job %s was already settled elsewhere", self.queue.name, job_id
            )

    def _connect(self):
        """Retry until connected or stopped, so a Supabase outage at start does not crash-loop."""
        while self.running:
            # Alive while waiting out an outage, or the deploy's --wait reads it as dead.
            beat()
            try:
                return self.client_factory()
            except Exception as exc:
                logger.error(
                    "%s: could not connect, retrying: %s", self.queue.name, exc
                )
                self.sleep(POLL_SECONDS)
        return None

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        client = self._connect()
        logger.info("%s worker started (poll=%ss)", self.queue.name, POLL_SECONDS)
        while self.running and client is not None:
            try:
                handled = self.process_one(client)
            except Exception as exc:
                logger.error("%s: claim failed, reconnecting: %s", self.queue.name, exc)
                self.sleep(POLL_SECONDS)
                client = self._connect()
                continue
            if not handled:
                self.sleep(POLL_SECONDS)
        logger.info("%s worker stopped", self.queue.name)
