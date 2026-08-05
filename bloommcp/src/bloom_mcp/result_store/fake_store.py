"""In-memory :class:`ResultStore` for tests — no Supabase, no manifest I/O.

Mirrors :class:`SupabaseResultStore`'s observable behaviour, including its
commit-failure and duplicate-version-id guards, via test-only injection hooks
(`fail_next_commit`, `seed_collision`, `seed_v2_run`) so `test_store_parity.py`
can exercise both backends against one shared failure/collision scenario set.
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Optional

from bloom_mcp.manifest.versioning import next_version_id, version_dir_name

from ._artifacts import (
    SIGNED_URL_EXPIRES_SECONDS,
    build_output_links,
    hash_outputs,
    validate_outputs,
)
from ._locks import KeyedLock
from .ports import (
    CommitFailedError,
    ManifestReadError,
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)

if TYPE_CHECKING:
    from bloom_mcp.contract.provenance import Provenance
    from bloom_mcp.data_access import SourceInfo

# Bounded attempts to allocate a free version_id before giving up, mirroring
# SupabaseResultStore.commit's pre-record collision check. Same value, an
# independent constant — not imported from supabase_store.py, whose constant
# is module-private.
_MAX_ID_ATTEMPTS = 3


# Mirrors SupabaseResultStore's per-key commit lock (see that module's
# docstring for why this is load-bearing under FastMCP's thread-pool
# dispatch, not defensive belt-and-suspenders) — same shared `KeyedLock`
# (see `_locks.py`), so a concurrent-commit test against the fake exercises
# the same mutual-exclusion property the real adapter provides.
def _commit_lock(output_root: str, experiment: str, tool_class: str) -> KeyedLock:
    # "fake" namespaces this adapter's keys apart from SupabaseResultStore's
    # in the shared registry — see supabase_store.py's identical note.
    return KeyedLock(("fake", output_root, experiment, tool_class))


# Placeholder timestamp for stub `StoredRun`s seeded via `seed_collision`/
# `seed_v2_run` — these are test-only synthetic history, never derived from
# wall-clock time.
_STUB_CREATED_AT = "2026-01-01T00:00:00Z"


@dataclass
class _FakeRunState:
    experiment: str
    tool_class: str
    version_id: str
    version_dir: str
    prefix: str
    provenance: "Provenance"
    user_label: Optional[str]


def _manifest_view(existing: list[StoredRun]) -> SimpleNamespace:
    """Duck-typed stand-in for a `Manifest`, for reuse with `next_version_id`.

    `next_version_id` only reads `.versions` and each entry's `.id` — no real
    `Manifest`/Pydantic instance is required, so the fake's `list[StoredRun]`
    (there is no manifest concept here) can feed it directly. Reusing the
    shared function (instead of a second hand-rolled max(N)+1 scan) makes the
    fake's allocation semantics provably identical to the real adapter's.
    """
    return SimpleNamespace(versions=[SimpleNamespace(id=r.run_ref) for r in existing])


class FakeResultStore:
    """Records versioned runs in memory, mirroring :class:`SupabaseResultStore`."""

    def __init__(self, output_root: str = "bloommcp_output") -> None:
        self._output_root = output_root
        self._runs: dict[tuple[str, str], list[StoredRun]] = {}
        self._open: set[int] = set()
        # One-shot: (experiment, tool_class) -> after_outputs, consumed by the
        # next commit() for that pair regardless of outcome.
        self._fail_next: dict[tuple[str, str], int] = {}
        # One-shot: (experiment, tool_class) -> [version_id, ...] visible only
        # to commit()'s pre-append recheck, simulating a collision that lands
        # after the pre-record check already passed. See `seed_collision`.
        self._pending_collisions: dict[tuple[str, str], list[str]] = {}
        # One-shot: (experiment, tool_class) pairs armed by `fail_next_read`,
        # consumed by whichever of create_run/list_runs/get_run is called
        # first for that key. Guarded by its own lock (not `KeyedLock` — this
        # protects one shared set's check-then-discard, not per-key mutual
        # exclusion) so two threads racing the same armed key can't both
        # observe it set before either discards it, which would break the
        # documented one-shot contract.
        self._fail_next_read: set[tuple[str, str]] = set()
        self._fail_next_read_lock = threading.Lock()

    def create_run(
        self,
        *,
        experiment: str,
        tool_class: str,
        provenance: "Provenance",
        user_label: Optional[str] = None,
        source_csv: Optional[Path] = None,
        source: Optional["SourceInfo"] = None,
    ) -> RunHandle:
        self._maybe_fail_read(experiment, tool_class)
        if source is not None:
            provenance = provenance.model_copy(
                update={
                    "source_id": source.source_id,
                    "source_name": source.source_name,
                }
            )
        key = (experiment, tool_class)
        existing = self._runs.get(key, [])
        # Provisional: `commit` re-derives `taken` fresh and reallocates on
        # collision before recording anything, so this allocation racing
        # another `create_run` is not a correctness issue — only `commit`'s
        # ordering is. Mirrors SupabaseResultStore.create_run's comment.
        version_id = next_version_id(_manifest_view(existing))
        version_dir = version_dir_name(version_id, user_label)
        prefix = f"{self._output_root}/{tool_class}_{_stem(experiment)}/"
        staging = Path(tempfile.mkdtemp(prefix="fake_v_"))
        handle = RunHandle(
            version_id=version_id,
            staging_dir=staging,
            manifest_path=f"{prefix}manifest.json",
            _backend=_FakeRunState(
                experiment=experiment,
                tool_class=tool_class,
                version_id=version_id,
                version_dir=version_dir,
                prefix=prefix,
                provenance=provenance,
                user_label=user_label,
            ),
        )
        self._open.add(id(handle))
        return handle

    def commit(self, run: RunHandle, outputs: dict[str, str]) -> StoredRun:
        if id(run) not in self._open:
            raise RunStateError("commit() on an unknown or already-committed run")
        validate_outputs(outputs)
        state: _FakeRunState = run._backend
        key = (state.experiment, state.tool_class)

        # Mirrors SupabaseResultStore.commit's per-key lock: makes commit()
        # calls for the same (experiment, tool_class) fully mutually
        # exclusive, so a concurrent-commit test against the fake exercises
        # the same guarantee the real adapter provides.
        lock = _commit_lock(self._output_root, state.experiment, state.tool_class)
        with lock:
            fail_after = self._fail_next.pop(key, None)

            try:
                # Finalize version_id + version_dir together, before
                # recording anything: re-derive `taken` fresh from
                # `self._runs` on every attempt (not once, cached) — a stale
                # snapshot can never re-collide with the id
                # `next_version_id` just computed from it, which would make
                # the bound unreachable through real contention. A
                # `seed_collision` call (or, in production, another writer)
                # may have landed since `create_run` saw its provisional id.
                version_id = state.version_id
                version_dir = state.version_dir
                attempts = 0
                while True:
                    existing = self._runs.get(key, [])
                    taken = {r.run_ref for r in existing}
                    if version_id not in taken:
                        break
                    attempts += 1
                    if attempts >= _MAX_ID_ATTEMPTS:
                        raise RuntimeError(
                            f"could not allocate a free version id after "
                            f"{attempts} attempts (last tried: {version_id!r})"
                        )
                    version_id = next_version_id(_manifest_view(existing))
                    version_dir = version_dir_name(version_id, state.user_label)
                run.version_id = version_id

                def key_for(rel: str) -> str:
                    return f"{state.prefix}{version_dir}/{rel}"

                output_keys, output_sha256, output_size_bytes = hash_outputs(
                    run.staging_dir, outputs, key_for
                )
                # Synthesized, not a real signed URL (bloom#581 Decision 7) —
                # this store never uploads real bytes to any backend, so it
                # never calls storage_backend.active_backend().
                output_links = build_output_links(
                    output_keys,
                    output_sha256,
                    output_size_bytes,
                    url_for=lambda key: (
                        f"fake://signed/{key}?expires_in={SIGNED_URL_EXPIRES_SECONDS}"
                    ),
                    expected_prefix=f"{state.prefix}{version_dir}/",
                )

                # Per-output recording loop, mirroring where
                # SupabaseResultStore.commit's upload loop sits relative to
                # its own hash_outputs call — a no-op here (nothing external
                # to write to) except as `fail_next_commit`'s injection
                # checkpoint.
                recorded = 0
                for _name in outputs:
                    if fail_after is not None and recorded == fail_after:
                        raise RuntimeError(
                            "simulated commit failure (fail_next_commit)"
                        )
                    recorded += 1
                if fail_after is not None and recorded == fail_after:
                    # fail_after == len(outputs): every output "recorded",
                    # fail at the pre-append/manifest-write-equivalent step.
                    raise RuntimeError("simulated commit failure (fail_next_commit)")

                prov = state.provenance.model_copy(
                    update={
                        "outputs": dict(outputs),
                        "output_keys": output_keys,
                        "output_sha256": output_sha256,
                        "version_dir": version_dir,
                        "user_label": state.user_label,
                    }
                )
                entry = prov.to_version_entry(version_id=version_id)

                # Pre-append recheck: catches a collision only visible after
                # the pre-record check above already passed. See
                # `seed_collision`'s `visible_at="pre_append"` for how a test
                # drives this. Under the lock above, no *other* commit() call
                # can be mid-flight for this same key — this remains as
                # defense-in-depth (and as the fake's hook for simulating the
                # still-open multi-instance case).
                fresh_taken = {r.run_ref for r in self._runs.get(key, [])}
                fresh_taken |= set(self._pending_collisions.pop(key, []))
                if version_id in fresh_taken:
                    raise RuntimeError(
                        f"version {version_id!r} was claimed by another writer "
                        f"during commit"
                    )

                stored = StoredRun.from_version_entry(
                    entry,
                    tool_class=state.tool_class,
                    experiment=state.experiment,
                    manifest_path=run.manifest_path,
                )
            except Exception as exc:
                # Leave the handle open and the staging dir intact so the
                # caller can retry — a retry re-enters commit() and
                # re-derives `taken` against then-current state.
                raise CommitFailedError(
                    f"commit failed for {state.tool_class}/{_stem(state.experiment)} "
                    f"(transient — retry)"
                ) from exc

            # Success only: tear down staging and seal the handle.
            shutil.rmtree(run.staging_dir, ignore_errors=True)
            self._open.discard(id(run))
            # `stored` (no output_links) is what persists in `self._runs` — it
            # mirrors what a real manifest-backed get_run/list_runs would
            # re-derive (never signed, bloom#581 Decision 1). Only commit()'s
            # own return value carries the freshly-built links.
            self._runs.setdefault(key, []).append(stored)
            return replace(stored, output_links=output_links)

    def list_runs(self, experiment: str, tool_class: str) -> list[StoredRun]:
        self._maybe_fail_read(experiment, tool_class)
        return list(self._runs.get((experiment, tool_class), []))

    def get_run(
        self,
        experiment: str,
        tool_class: str,
        run_ref: str = "latest",
    ) -> StoredRun:
        self._maybe_fail_read(experiment, tool_class)
        runs = self._runs.get((experiment, tool_class), [])
        if not runs:
            raise RunNotFoundError(f"No runs for {tool_class}/{_stem(experiment)}.")
        if run_ref == "latest":
            return runs[-1]
        for stored in runs:
            if stored.run_ref == run_ref:
                return stored
        raise RunNotFoundError(
            f"No run {run_ref!r} for {tool_class}/{_stem(experiment)}."
        )

    # --- Test-only failure/collision injection ------------------------------

    def fail_next_read(self, experiment: str, tool_class: str) -> None:
        """The next `create_run`/`list_runs`/`get_run` call for
        `(experiment, tool_class)` — whichever is called first — raises
        `ManifestReadError` once, then clears itself.

        Mirrors `fail_next_commit`'s one-shot pattern: #596 guards each of the
        three read call sites against a real manifest-read failure on
        `SupabaseResultStore`, but this fake's flat in-memory dict has no read
        of its own to fail organically — this hook is the only way to
        exercise that guard's contract without a live Supabase adapter. Only
        the generic `ManifestReadError` is simulated, not the
        schema-incompatible subtype (`ManifestIncompatibleError`) — manifest
        schema parsing is a real-backend-only concern this flat model has no
        equivalent of.
        """
        with self._fail_next_read_lock:
            self._fail_next_read.add((experiment, tool_class))

    def _maybe_fail_read(self, experiment: str, tool_class: str) -> None:
        key = (experiment, tool_class)
        with self._fail_next_read_lock:
            armed = key in self._fail_next_read
            if armed:
                self._fail_next_read.discard(key)
        if armed:
            raise ManifestReadError(
                f"Simulated manifest read failure for {tool_class}/{_stem(experiment)}."
            )

    def fail_next_commit(
        self, experiment: str, tool_class: str, *, after_outputs: int = 0
    ) -> None:
        """The next `commit()` for `(experiment, tool_class)` raises
        `CommitFailedError` after `after_outputs` outputs are recorded, then
        clears itself (one-shot, regardless of the call's outcome).

        `after_outputs` ranges `0..len(outputs)` inclusive: `0..len(outputs)-1`
        models a failure *during* recording (partial-upload analog); `==
        len(outputs)` models a failure after every output is recorded but
        before the run is appended (manifest-write analog).
        """
        self._fail_next[(experiment, tool_class)] = after_outputs

    def seed_collision(
        self,
        experiment: str,
        tool_class: str,
        version_id: str,
        *,
        visible_at: str = "pre_record",
    ) -> None:
        """Simulate another writer having already claimed `version_id`.

        `visible_at="pre_record"` (default) makes the collision visible to
        `commit`'s pre-record check (an interloper that landed before this
        commit started) — the realistic "immediate reallocation" case.
        `visible_at="pre_append"` makes it visible only to the pre-append
        recheck (an interloper whose commit lands during this commit's
        in-flight window) — the "late collision" case. It is consumed
        (one-shot) by that recheck and never actually added to `self._runs`,
        matching the real adapter's stubbed-interloper test technique: a
        retry after the safe failure finds a genuinely free id.
        """
        key = (experiment, tool_class)
        if visible_at == "pre_record":
            stored = self._stub_stored_run(
                experiment=experiment,
                tool_class=tool_class,
                version_id=version_id,
                version_dir=f"{version_id}_interloper",
                tool="interloper",
                outputs={},
                output_keys={},
                output_sha256={},
            )
            self._runs.setdefault(key, []).append(stored)
        elif visible_at == "pre_append":
            self._pending_collisions.setdefault(key, []).append(version_id)
        else:
            raise ValueError(f"unknown visible_at: {visible_at!r}")

    def seed_v2_run(
        self, experiment: str, tool_class: str, *, tool: str, outputs: dict[str, str]
    ) -> StoredRun:
        """Register a v2-shaped historical run: no `seed`/`agent`/
        `output_sha256`/`output_keys`, matching the checked-in
        `manifest_v2.json` fixture's shape — schema evolution, not a bug.
        """
        key = (experiment, tool_class)
        existing = self._runs.get(key, [])
        version_id = next_version_id(_manifest_view(existing))
        stored = self._stub_stored_run(
            experiment=experiment,
            tool_class=tool_class,
            version_id=version_id,
            version_dir=version_dir_name(version_id),
            tool=tool,
            outputs=outputs,
            output_keys={},
            output_sha256={},
        )
        self._runs.setdefault(key, []).append(stored)
        return stored

    def _stub_stored_run(
        self,
        *,
        experiment: str,
        tool_class: str,
        version_id: str,
        version_dir: str,
        tool: str,
        outputs: dict[str, str],
        output_keys: dict[str, str],
        output_sha256: dict[str, str],
    ) -> StoredRun:
        prefix = f"{self._output_root}/{tool_class}_{_stem(experiment)}/"
        return StoredRun(
            run_ref=version_id,
            tool=tool,
            tool_class=tool_class,
            experiment=experiment,
            version_dir=version_dir,
            manifest_path=f"{prefix}manifest.json",
            created_at=_STUB_CREATED_AT,
            outputs=dict(outputs),
            output_keys=dict(output_keys),
            output_sha256=dict(output_sha256),
            seed=None,
            agent=None,
            environment=None,
            code_versions={},
            input_validation=None,
        )


def _stem(name: str) -> str:
    return name[:-4] if name.endswith(".csv") else name
