"""Render the run summary GitHub shows for a night's mirror.

The verdict and every number come from the run's own anchored marker lines, or
from the run report it wrote, parsed as JSON. Nothing is recovered by reading
the prose the job logs for people.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Anchored to the start of a log line — timestamp, level, then the key. An
# object name can only appear after a message has begun, so no name can forge
# one. See `emit_status` in backup_objects.py.
_ANCHOR = r"^[0-9-]+ [0-9:,]+ [A-Z]+ "
_STATUS = re.compile(_ANCHOR + r"BOX_BACKUP_STATUS=([a-z_]+)\s*$", re.M)
_FLAGS = re.compile(_ANCHOR + r"BOX_BACKUP_FLAGS=([a-z_,]*)\s*$", re.M)
_STATS = re.compile(_ANCHOR + r"BOX_BACKUP_STATS=(\{.*\})\s*$", re.M)

# The progress lines worth putting in front of an operator, newest last.
_TAIL = re.compile(
    r"^[0-9-]+ [0-9:,]+ (?:INFO|WARNING|ERROR) "
    r"(?:done —|verify:|listed|preflight|batch:)",
)
TAIL_LINES = 20


@dataclass
class Verdict:
    """What happened, as the run itself reported it."""

    status: str
    flags: tuple[str, ...] = ()
    stats: dict = field(default_factory=dict)
    # True when the log pipe died and this came off the host instead.
    from_report: bool = False
    # True when the run reported nothing at all and the step's own outcome had
    # to stand in. Nothing here was measured, so no count may be quoted.
    assumed: bool = False

    def has(self, flag: str) -> bool:
        return flag in self.flags

    def count(self, key: str) -> int | None:
        value = self.stats.get(key)
        return value if isinstance(value, int) else None


def _flags(raw: str) -> tuple[str, ...]:
    return tuple(f for f in raw.split(",") if f)


def from_log(text: str) -> Verdict | None:
    """The verdict as the run printed it, or None if it never got that far."""
    status = _STATUS.findall(text)
    if not status:
        return None
    flags = _FLAGS.findall(text)
    stats = _STATS.findall(text)
    counts = {}
    if stats:
        try:
            parsed = json.loads(stats[-1])
        except ValueError:
            # Degrade like `from_report` does. A page with no counts beats a
            # step that raises and leaves the summary empty.
            parsed = None
        counts = parsed if isinstance(parsed, dict) else {}
    return Verdict(
        status=status[-1],
        flags=_flags(flags[-1]) if flags else (),
        stats=counts,
    )


def from_report(text: str) -> Verdict | None:
    """The verdict from a run report written on the host.

    The report is JSON this job wrote, so a malformed one means the file was
    truncated or is not a report at all — either way it has nothing to say.
    """
    try:
        body = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(body, dict):
        return None
    status = body.get("status")
    if not isinstance(status, str) or not status:
        return None
    flags = body.get("flags")
    stats = body.get("stats")
    return Verdict(
        status=status,
        flags=tuple(f for f in flags if isinstance(f, str))
        if isinstance(flags, list)
        else (),
        stats=stats if isinstance(stats, dict) else {},
        from_report=True,
    )


def counts_phrase(verdict: Verdict, dry_run: bool) -> str:
    """The numbers, in words: what a bare "succeeded" cannot tell you.

    A week that copies nothing has to read differently from a week where the
    Box folder was deleted, and both are a green tick.

    Nothing at all for a run that measured nothing: `assumed` means the counts
    came from the step's outcome, and a quoted zero reads as a measurement.
    """
    if verdict.assumed:
        return ""
    copied = verdict.count("copied")
    checked = verdict.count("verify_checked")
    unverified = verdict.count("verify_unverified")
    gone = verdict.count("source_gone")

    if dry_run:
        # A dry run copies nothing, so the copy counts never applied to it.
        phrase = f"dry run — would copy {copied or 0:,}, nothing was copied"
    elif copied:
        phrase = f"{copied:,} images copied"
    elif copied is not None:
        phrase = f"nothing new to copy ({verdict.count('already_current') or 0:,} already on Box)"
    else:
        phrase = ""

    if checked:
        phrase += f", {checked:,} verified"
    elif unverified:
        # Nothing answered: without this the shortfall reads as a copy count.
        phrase += ", 0 verified"
    # Immediately after the count it qualifies, not at the end — trailing the
    # whole phrase, it reads as qualifying whatever clause came last.
    if unverified:
        phrase += f" ({unverified:,} unanswered)"
    # Never let a headline read "nothing new to copy" while objects were
    # passed over for having no image behind them.
    if gone:
        phrase += f"{', ' if phrase else ''}{gone:,} with no image behind them"
    return phrase


# One entry per additive notice: a condition that can occur on a night that
# otherwise succeeded, so none of them can be a branch of the headline.
_NOTICES = {
    "ledger_stale": (
        "**The ledger on Box was NOT updated.** Objects still copied fine — "
        "this is about the record of WHICH objects are already mirrored, which "
        "is what makes a re-seed unnecessary. It now exists only on the deploy "
        "host, the machine this job exists to survive losing. Every later "
        "night can still report success while that copy falls further behind, "
        "so fix it now: the job log says why the upload failed, and 'If the "
        "deploy host itself is gone' in the wiki says what losing it costs."
    ),
    "ledger_ahead": (
        "**The ledger on Box is newer than this host's — the upload was "
        "refused on purpose.** Box holds the record of what is already "
        "mirrored and this host holds a smaller one, which means this is not "
        "the machine that built the mirror: a rebuilt host, or a wiped state "
        "directory. **Restore the Box copy onto this host. Do NOT upload over "
        "it** — that would replace the record of millions of objects with this "
        "run's, and cost a full re-seed. See 'If the deploy host itself is "
        "gone' in the wiki."
    ),
    "source_gone": (
        "**Some rows in the database have no image behind them.** Postgres "
        "lists the object but MinIO does not hold the bytes, so there is "
        "nothing to copy and no run can ever back these up. That is usually a "
        "delete that removed the file and left the row, and it may mean the "
        "image is already lost rather than merely unmirrored.\n\n"
        "They are listed under `source_gone` in the run report under `_runs/` "
        "on Box. Someone has to decide whether each row should still exist — "
        "nothing here will."
    ),
    "verify_incomplete": (
        "**Verification did not cover its whole sample.** Box did not answer "
        "for some of the objects it was asked about, so those were neither "
        "confirmed present nor found missing — the `N verified` count above is "
        "what was actually established, not what was checked for. The copies "
        "were confirmed as they were made, so this is not a reason to re-copy "
        "anything. But a night reporting success on the strength of a check "
        "that mostly did not run is what the check exists to prevent, so if it "
        "repeats see 'What verification does, and does not, prove' in the "
        "wiki. The job log has the exact counts."
    ),
}


def _quote(text: str) -> str:
    return "\n".join(f">{' ' + line if line else ''}" for line in text.splitlines())


def _headline(
    verdict: Verdict, dry_run: bool, counts: str
) -> tuple[list[str], set[str]]:
    """The result line, and which conditions it has already accounted for."""
    said: set[str] = set()
    out: list[str] = []

    if dry_run and not verdict.assumed:
        # It writes no run report and records no run, so the branches below —
        # which all point at `_runs/` — cannot describe it.
        #
        # `assumed` excludes a dry run that died before reporting: its counts
        # were never measured, and "would copy 0, nothing was copied" reads as
        # a clean pre-seed check on the run that is meant to catch problems.
        said.add("dry")
        out.append(f"Result: **{counts}**.")
        if verdict.has("collisions") or verdict.has("skipped_names"):
            out += [
                "",
                "It found objects a real run would refuse. They are named in "
                "the `skipping` lines of the job log; a dry run writes no "
                "report.",
            ]
    elif verdict.status == "skipped":
        out.append(
            "Result: **skipped** — another run holds the lock, almost "
            "certainly the initial seed. Nothing was mirrored tonight; the "
            "seed is doing that work. This is expected until it ends."
        )
    elif verdict.status == "failed" and verdict.has("verify_mismatch"):
        said.add("mismatch")
        out.append(
            "Result: **FAILED, and verification found objects missing** — "
            "objects failed to copy tonight AND the check found others the "
            "copy had reported as successful are not on Box. Both are in the "
            "job log; the missing ones are named in the run report under "
            "`_runs/`. Read the log first: a night that failed copies "
            "outright is the larger problem."
        )
    elif verdict.has("verify_mismatch"):
        # Reached only when the run did NOT fail — the branch above takes those.
        said.add("mismatch")
        out += [
            "Result: **VERIFICATION FAILED** — objects the copy reported as "
            "successful are not on Box. Every one is named in the run report "
            f"under `_runs/`.{f' The night otherwise: {counts}.' if counts else ''}",
            "",
            "The run changed nothing to compensate: the ledger still records "
            "them as mirrored, so later runs skip them and this warning does "
            "**not** repeat. Putting them back needs a person — see 'What "
            "verification does, and does not, prove' in the wiki.",
            "",
            "**The watermark is not held for this.** Nothing on this side can "
            "restore the object, so holding it would freeze the mirror's "
            "progress until someone acted and make every later night re-read "
            "all eight million rows. Tonight is the notice; the run report "
            "under `_runs/` is the record.",
        ]
    elif verdict.has("collisions"):
        said.add("collisions")
        out.append(
            "Result: **OBJECTS NOT BACKED UP** — two object names normalize "
            "onto one path on Box, so only one of each pair can be stored and "
            "the job refused to overwrite the other. Rename the object named "
            "at the START of each `skipping` line in the job log — renaming "
            "its twin instead leaves this one refused for ever. The run is "
            "recorded **partial**, so they stay in view until it is done."
        )
        if verdict.status == "failed":
            out += [
                "",
                "**Objects also failed to copy tonight.** See the job log — "
                "that is the larger problem and this line does not mean "
                "nothing else went wrong.",
            ]
    elif verdict.status == "stopped":
        # Its own branch: this is the one outcome that means 'this is fine'.
        said.add("stopped")
        out.append(
            "Result: **stopped, progress kept** — the run was asked to stop "
            "before it finished the table, almost certainly the job's own time "
            "limit. Everything it copied is recorded and on Box; the next run "
            "carries on from there. Nothing is lost."
        )
        if verdict.from_report:
            out.append(
                "Check the job log for the ledger upload: the verdict came "
                "from the run report, which is written before it."
            )
        elif not verdict.flags:
            # Only when the night raised nothing. Every flag below prints a
            # notice telling someone to act, and during the seed every night
            # is stopped — so this line would contradict them nightly.
            out.append("Nothing needs doing.")
        if verdict.count("copied"):
            out.append(f"It got through {counts}.")
        else:
            out.append(
                "It stopped before copying anything — most likely during the "
                "manifest read, which a stop cannot interrupt."
            )
    elif verdict.status in ("ok", "partial"):
        out.append(
            f"Result: **succeeded** — {counts}"
            if counts
            else "Result: **succeeded** (no counts in the log — check it)"
        )
    else:
        out.append(
            "Result: **FAILED** — see the job log. Some or all of tonight's "
            "objects were not mirrored; anything that did copy is recorded and "
            "will not be copied again."
        )
    return out, said


def _notices(verdict: Verdict, said: set[str]) -> list[str]:
    """Everything the headline did not already say. Each stands on its own."""
    out: list[str] = []

    def note(text: str) -> None:
        out.extend(["", _quote(text)])

    if verdict.status == "stopped" and "stopped" not in said:
        note(
            "The run was also **asked to stop before it finished the table** "
            "— almost certainly the job's own time limit. Everything it "
            "copied is recorded and on Box, and the next run carries on from "
            "there."
        )
    if verdict.has("collisions") and not {"collisions", "dry"} & said:
        note(
            "**A name collision was also refused.** Two object names "
            "normalize onto one path on Box. Rename the object named at the "
            "START of its `skipping` line in the job log. Resolve this before "
            "the missing objects above: the two are entangled, and acting on "
            "one while the other stands can lose a backup."
        )
    if verdict.has("verify_mismatch") and "mismatch" not in said:
        note(
            "**Verification found objects missing from Box.** The copy "
            "reported them as successful and the check disagreed; every one "
            "is named in the run report under `_runs/`. Putting them back "
            "needs a person — see 'What verification does, and does not, "
            "prove' in the wiki."
        )
    if verdict.has("ledger_stale"):
        note(_NOTICES["ledger_stale"])
    if verdict.has("skipped_names") and "dry" not in said:
        text = (
            "**Some images were not backed up because of their filenames.** "
            "Box cannot store a name containing certain characters, so those "
            "images were refused rather than copied wrong. Nothing on this "
            "side can fix it — only renaming them in Supabase can. Each is "
            "named with its reason in the run report under `_runs/` on Box.\n"
        )
        if verdict.status == "ok":
            # True only on a clean night; a held watermark says it again tomorrow.
            text += (
                "\n**This is the only night that will say so.** The run is "
                "still recorded clean on purpose: holding the mirror's "
                "progress timestamp for a filename nothing can fix on its own "
                "would make every later night re-read all eight million "
                "rows.\n"
            )
        text += (
            "\nThe report on Box is the durable record — they are listed "
            "under `name_skips`, apart from the collisions."
        )
        note(text)
    if verdict.has("source_gone") and "dry" not in said:
        note(_NOTICES["source_gone"])
    # Never merged with ledger_stale: there Box is behind, here Box is ahead.
    if verdict.has("ledger_ahead"):
        note(_NOTICES["ledger_ahead"])
    if verdict.has("verify_incomplete"):
        note(_NOTICES["verify_incomplete"])
    return out


def tail(log: str) -> list[str]:
    """The last few progress lines, for an operator who wants the shape of it."""
    return [line for line in log.splitlines() if _TAIL.match(line)][-TAIL_LINES:]


def render(verdict: Verdict, env: str, log: str = "", dry_run: bool = False) -> str:
    counts = counts_phrase(verdict, dry_run)
    lines = [f"## Nightly Box object mirror — {env}", ""]
    headline, said = _headline(verdict, dry_run, counts)
    lines += headline
    lines += _notices(verdict, said)
    recent = tail(log)
    lines += [
        "",
        "### This run",
        "```",
        *(recent or ["(no summary produced — the run did not reach the copy phase)"]),
        "```",
        "",
        "_This job only uploads. Nothing on Box is ever deleted by it._",
    ]
    if "dry" not in said:
        lines.append(
            "_Per-run reports, including verification counts, are written to "
            "`<OBJECT_BACKUP_BOX_ROOT>/_runs/` on Box._"
        )
    return "\n".join(lines) + "\n"


def verdict_for(log: str, report: str, outcome: str) -> Verdict:
    """The verdict, from the log if the run reached the end, else the report.

    The log is preferred only because it is already on the runner. A cancel or
    a timeout kills the pipe mid-run, and then the report the host wrote is the
    only place the night's verdict exists.
    """
    found = from_log(log) or (from_report(report) if report else None)
    if found is None:
        # Nothing anywhere: the step's own outcome is the verdict.
        return Verdict(status="ok" if outcome == "success" else "failed", assumed=True)
    if found.from_report:
        print(
            f"verdict taken from the run report on the host: {found.status} "
            f"({','.join(found.flags)})",
            file=sys.stderr,
        )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", default="", help="the job's captured output")
    parser.add_argument(
        "--report", default="", help="a run report fetched from the host"
    )
    parser.add_argument("--env", required=True)
    parser.add_argument("--outcome", default="", help="the run step's own outcome")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    def read(path: str) -> str:
        if not path:
            return ""
        try:
            return Path(path).read_text(errors="replace")
        except OSError:
            return ""

    log = read(args.log)
    verdict = verdict_for(log, read(args.report), args.outcome)
    sys.stdout.write(render(verdict, args.env, log, args.dry_run))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
