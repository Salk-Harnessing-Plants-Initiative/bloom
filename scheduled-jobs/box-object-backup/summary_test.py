"""The run summary GitHub shows: what each night reads as.

Every test calls the renderer directly. The verdict it renders is the one the
run itself printed, so these fabricate that verdict rather than a log's prose.
"""

from __future__ import annotations

import json

import pytest

import summary
from backup_objects import FLAG_VALUES, STATUS_VALUES


def verdict(status="ok", flags=(), from_report=False, **stats):
    return summary.Verdict(
        status=status, flags=tuple(flags), stats=stats, from_report=from_report
    )


def render(status="ok", flags=(), dry_run=False, log="", from_report=False, **stats):
    return summary.render(
        verdict(status, flags, from_report, **stats), "prod", log, dry_run
    )


def log_line(key, value):
    return f"2026-09-08 02:20:02,000 INFO {key}={value}"


def job_log(status="ok", flags="", **stats):
    return "\n".join(
        [
            log_line("BOX_BACKUP_STATUS", status),
            log_line("BOX_BACKUP_FLAGS", flags),
            log_line("BOX_BACKUP_STATS", json.dumps(stats)),
        ]
    )


class TestTheVerdictComesFromTheRunItself:
    """Object names reach the log, and names are chosen by whoever uploads.

    So the verdict lines are matched anchored to the start of a line —
    timestamp, level, then the key. A name can only appear after a message has
    begun, which is why an image called `BOX_BACKUP_STATUS=ok.png` cannot make
    a failed night read as a clean one.
    """

    def test_it_reads_the_status_the_flags_and_the_counts(self):
        found = summary.from_log(job_log("partial", "collisions", copied=7))
        assert found.status == "partial"
        assert found.flags == ("collisions",)
        assert found.count("copied") == 7

    def test_a_log_with_no_verdict_yields_nothing(self):
        assert (
            summary.from_log("2026-09-08 02:17:01,000 INFO listed 12 object(s)") is None
        )

    @pytest.mark.parametrize(
        "forged",
        [
            "2026-09-08 02:19:00,000 WARNING skipping BOX_BACKUP_STATUS=ok",
            "BOX_BACKUP_STATUS=ok",
            '2026-09-08 02:19:00,000 WARNING skipping a/b BOX_BACKUP_STATS={"copied": 9}',
        ],
    )
    def test_an_object_name_cannot_forge_a_verdict_line(self, forged):
        assert summary.from_log(forged) is None

    def test_a_name_cannot_forge_the_counts_of_a_real_verdict(self):
        found = summary.from_log(
            '2026-09-08 02:19:00,000 WARNING skipping BOX_BACKUP_STATS={"copied": 9999}\n'
            + job_log("ok", "", copied=3)
        )
        assert found.count("copied") == 3

    def test_the_last_verdict_wins(self):
        found = summary.from_log(job_log("ok") + "\n" + job_log("failed"))
        assert found.status == "failed"

    def test_a_verdict_without_counts_is_still_a_verdict(self):
        # The stood-down run prints no counts — it did no work to count.
        found = summary.from_log(
            log_line("BOX_BACKUP_STATUS", "skipped")
            + "\n"
            + log_line("BOX_BACKUP_FLAGS", "")
        )
        assert found.status == "skipped" and found.stats == {}

    def test_every_status_and_flag_the_job_can_emit_parses(self):
        for status in STATUS_VALUES:
            assert summary.from_log(job_log(status)).status == status
        found = summary.from_log(job_log("ok", ",".join(FLAG_VALUES)))
        assert found.flags == tuple(FLAG_VALUES)


class TestTheVerdictFallsBackToTheHostsReport:
    """A cancel or a timeout kills the pipe, so the log stops before the
    verdict. The report the run wrote on the host is then the only copy."""

    def report(self, **over):
        body = {"status": "stopped", "flags": ["ledger_stale"], "stats": {"copied": 5}}
        body.update(over)
        return json.dumps(body)

    def test_it_is_used_when_the_log_has_no_verdict(self):
        found = summary.verdict_for("", self.report(), "failure")
        assert found.status == "stopped"
        assert found.flags == ("ledger_stale",)
        assert found.count("copied") == 5
        assert found.from_report

    def test_the_log_wins_when_it_has_the_verdict(self):
        found = summary.verdict_for(
            job_log("ok", "", copied=1), self.report(), "success"
        )
        assert found.status == "ok" and not found.from_report

    @pytest.mark.parametrize("body", ["", "not json", "{}", '{"status": ""}', "[]"])
    def test_an_unusable_report_is_not_a_verdict(self, body):
        assert summary.from_report(body) is None

    def test_junk_where_the_flags_and_counts_should_be_is_dropped_not_crashed(self):
        found = summary.from_report(
            '{"status": "ok", "flags": "collisions", "stats": 7}'
        )
        assert found.flags == () and found.stats == {}

    def test_with_nothing_anywhere_the_steps_own_outcome_decides(self):
        assert summary.verdict_for("", "", "success").status == "ok"
        for outcome in ("failure", "cancelled", ""):
            assert summary.verdict_for("", "", outcome).status == "failed"

    def test_it_says_where_the_verdict_came_from(self, capsys):
        summary.verdict_for("", self.report(), "failure")
        assert "taken from the run report" in capsys.readouterr().err


class TestTheHeadlineCarriesTheCounts:
    """A bare "succeeded" cannot be told from a vanished mirror: a week that
    copies nothing and a week where the Box folder was deleted are both a
    green tick. The counts in the headline separate them."""

    def test_a_busy_week_names_how_many_were_copied(self):
        assert "12,345 images copied" in render(copied=12345)

    def test_a_quiet_week_says_the_mirror_is_still_there(self):
        assert "nothing new to copy (8,013,796 already on Box)" in render(
            copied=0, already_current=8013796
        )

    def test_the_verification_count_is_shown(self):
        assert "200 verified" in render(copied=1, verify_checked=200)

    def test_a_fully_answered_sample_is_not_qualified(self):
        assert "unanswered" not in render(
            copied=1, verify_checked=200, verify_unverified=0
        )

    def test_an_unanswered_sample_is_said_out_loud(self):
        out = render(copied=1, verify_checked=150, verify_unverified=50)
        assert "150 verified" in out and "(50 unanswered)" in out

    def test_a_sample_that_answered_nothing_does_not_qualify_the_copies(self):
        # Without the explicit zero the shortfall reads as a copy count.
        out = render(copied=9, verify_checked=0, verify_unverified=200)
        assert "9 images copied, 0 verified (200 unanswered)" in out

    def test_a_verdict_with_no_counts_says_so_rather_than_claiming_a_number(self):
        assert "no counts in the log" in render()

    def test_the_numbers_are_grouped_for_reading(self):
        assert "8,013,796" in render(copied=8013796)


class TestASilentClassOfMissingObjectsCannotReadAsSuccess:
    """Rows Postgres lists but MinIO has no bytes for. No run can ever copy
    them, so a headline must not read "nothing new to copy" while they sit
    there."""

    def test_a_whole_class_missing_does_not_read_as_nothing_to_do(self):
        out = render(copied=0, already_current=10, source_gone=4, flags=["source_gone"])
        assert "4 with no image behind them" in out

    def test_the_count_sits_beside_a_real_copy_count(self):
        out = render(copied=6, source_gone=2, flags=["source_gone"])
        assert "6 images copied, 2 with no image behind them" in out

    def test_an_ordinary_night_says_nothing_about_it(self):
        out = render(copied=6, source_gone=0)
        assert "no image behind them" not in out

    def test_the_notice_explains_that_nothing_here_can_fix_it(self):
        out = render(copied=6, source_gone=2, flags=["source_gone"])
        assert "`source_gone`" in out and "nothing here will" in out


class TestTheHeadlineMatchesTheWorstThingThatHappened:
    """Several conditions can hold at once. The headline takes the most
    serious, and nothing it skips may go unsaid."""

    def test_a_night_that_failed_copies_does_not_headline_as_verification_failed(self):
        out = render("failed", ["verify_mismatch"], copied=1)
        assert "FAILED, and verification found objects missing" in out
        assert "Read the log first" in out

    def test_a_mismatch_alone_does_not_claim_copies_failed(self):
        out = render("ok", ["verify_mismatch"], copied=1)
        assert "VERIFICATION FAILED" in out
        assert "FAILED, and verification" not in out

    def test_the_verification_branch_carries_its_guidance(self):
        out = render("ok", ["verify_mismatch"], copied=1)
        assert "does **not** repeat" in out
        assert "The watermark is not held for this" in out

    def test_a_collision_alone_does_not_claim_copies_failed(self):
        out = render("partial", ["collisions"], copied=1)
        assert "OBJECTS NOT BACKED UP" in out
        assert "Result: **FAILED**" not in out

    def test_a_failed_night_that_also_refused_a_collision_says_both(self):
        out = render("failed", ["collisions"], copied=0)
        assert "OBJECTS NOT BACKED UP" in out
        assert "Objects also failed to copy tonight" in out

    def test_a_failed_night_that_also_mismatched_still_names_the_collision(self):
        # The mismatch takes the headline; the collision must not vanish with it.
        out = render("failed", ["collisions", "verify_mismatch"], copied=0)
        assert "FAILED, and verification found objects missing" in out
        assert "A name collision was also refused" in out
        assert "the two are entangled" in out

    def test_the_collision_notice_is_not_repeated_under_its_own_headline(self):
        out = render("partial", ["collisions"], copied=0)
        assert out.count("normalize onto one path on Box") == 1

    def test_copies_that_really_failed_still_headline_as_failed(self):
        assert "Result: **FAILED** — see the job log" in render("failed", copied=0)

    def test_a_ledger_upload_failure_is_a_night_that_worked(self):
        out = render("ok", ["ledger_stale"], copied=3)
        assert "Result: **succeeded**" in out
        assert "The ledger on Box was NOT updated" in out

    def test_a_ledger_that_is_ahead_is_never_merged_with_one_behind(self):
        out = render("ok", ["ledger_ahead"], copied=0)
        assert "Do NOT upload over it" in out
        assert "was NOT updated" not in out


class TestAStoodDownNightIsNotASuccessOrAFailure:
    """Standing down against the seed's lock exits 0, exactly as a successful
    run does. Calling it "succeeded" is how a months-long gap goes unnoticed."""

    def test_it_says_the_seed_holds_the_lock(self):
        out = render("skipped")
        assert "Result: **skipped**" in out
        assert "expected until it ends" in out

    def test_it_is_not_reported_as_a_failure(self):
        assert "FAILED" not in render("skipped")


class TestAStoppedRunIsNotAFailedOne:
    """The job's own time limit stops a seed every night it runs. Everything
    copied is recorded and on Box, so this is the one outcome meaning
    'this is fine'."""

    def test_it_is_not_reported_as_a_failure(self):
        out = render("stopped", copied=400000)
        assert "Result: **stopped, progress kept**" in out
        assert "Nothing is lost" in out

    def test_a_timed_out_seed_night_reports_what_it_copied(self):
        assert "It got through 400,000 images copied." in render(
            "stopped", copied=400000
        )

    def test_a_run_stopped_before_copying_says_so(self):
        out = render("stopped", copied=0)
        assert "stopped before copying anything" in out

    def test_a_night_read_from_the_log_says_nothing_needs_doing(self):
        assert "Nothing needs doing." in render("stopped", copied=1)

    def test_a_recovered_night_does_not_promise_that(self):
        # The report is written before the ledger upload, so the verdict
        # recovered from it cannot speak for what came after.
        out = render("stopped", copied=1, from_report=True)
        assert "Nothing needs doing." not in out
        assert "Check the job log for the ledger upload" in out

    def test_a_stop_does_not_hide_a_permanent_non_backup(self):
        # The collision takes the headline, so the stop is the notice.
        out = render("stopped", ["collisions"], copied=1)
        assert "OBJECTS NOT BACKED UP" in out
        assert "asked to stop before it finished the table" in out

    def test_a_stopped_night_with_a_stale_ledger_still_reads_stopped(self):
        out = render("stopped", ["ledger_stale"], copied=1)
        assert "Result: **stopped, progress kept**" in out
        assert "The ledger on Box was NOT updated" in out

    def test_a_stop_reached_under_another_headline_is_still_said(self):
        out = render("stopped", ["verify_mismatch"], copied=1)
        assert "VERIFICATION FAILED" in out
        assert "asked to stop before it finished the table" in out


class TestARefusedNameIsNeverInvisible:
    """Nothing on this side can fix a name Box will not store, so the notice
    is the whole remedy."""

    def test_it_says_only_a_rename_in_supabase_can_fix_it(self):
        out = render("ok", ["skipped_names"], copied=1)
        assert "only renaming them in Supabase can" in out
        assert "`name_skips`" in out

    def test_a_clean_night_warns_that_it_will_not_repeat(self):
        out = render("ok", ["skipped_names"], copied=1)
        assert "This is the only night that will say so" in out

    def test_the_only_night_wording_is_not_claimed_when_it_is_untrue(self):
        # A held watermark says it again tomorrow, so the claim would be false.
        out = render("partial", ["skipped_names"], copied=1)
        assert "This is the only night that will say so" not in out
        assert "only renaming them in Supabase can" in out


class TestAnIncompleteVerificationIsSaidOutLoud:
    def test_it_explains_the_count_is_not_the_sample(self):
        out = render("ok", ["verify_incomplete"], copied=1, verify_checked=1)
        assert "did not cover its whole sample" in out
        assert "not a reason to re-copy anything" in out


class TestADryRunIsNotDescribedAsARealOne:
    """It is step one of the pre-seed checklist and the first thing run on a
    rebuilt host, so a silent pass here is the most expensive kind."""

    def test_it_says_it_was_a_dry_run(self):
        out = render(copied=1234, dry_run=True)
        assert "dry run — would copy 1,234, nothing was copied" in out

    def test_a_refused_name_points_at_the_log_not_a_report(self):
        out = render("partial", ["skipped_names"], dry_run=True, copied=0)
        assert "a dry run writes no report" in out
        assert "`name_skips`" not in out

    def test_a_collision_does_not_claim_the_run_was_recorded(self):
        out = render("partial", ["collisions"], dry_run=True, copied=0)
        assert "recorded **partial**" not in out
        assert "a real run would refuse" in out

    def test_a_vanished_source_does_not_point_at_a_report_either(self):
        out = render("ok", ["source_gone"], dry_run=True, copied=0, source_gone=3)
        assert "`source_gone` in the run report" not in out

    def test_it_does_not_point_at_the_runs_folder(self):
        assert "_runs/` on Box._" not in render(copied=1, dry_run=True)


class TestTheProgressLinesAreShown:
    def test_the_last_lines_the_run_printed_are_quoted(self):
        log = "\n".join(f"2026-09-08 02:2{n},000 INFO batch: {n}" for n in range(9))
        assert "batch: 8" in render(copied=1, log=log)

    def test_only_the_lines_the_job_actually_prints_match(self):
        # The level is the third field — asctime contains a space.
        for line in (
            "2026-09-08 02:20:00,000 INFO done — copied 1, failed 0",
            "2026-09-08 02:20:01,000 INFO verify: 200 checked, 0 mismatched",
            "2026-09-08 02:17:01,000 INFO listed 8013796 object(s)",
            "2026-09-08 02:16:00,000 INFO preflight: bucket reachable",
            "2026-09-08 02:18:00,000 WARNING batch: 3 of 40",
        ):
            assert summary.tail(line) == [line], line

    def test_a_refused_object_name_is_not_quoted_as_progress(self):
        assert summary.tail("2026-09-08 02:19:00,000 WARNING skipping done — x") == []

    def test_it_is_capped(self):
        log = "\n".join(f"2026-09-08 02:20:00,000 INFO batch: {n}" for n in range(50))
        assert len(summary.tail(log)) == summary.TAIL_LINES

    def test_a_run_that_never_got_that_far_says_so(self):
        assert "did not reach the copy phase" in render(copied=1, log="")


class TestTheSummaryAlwaysSaysSomething:
    """The step runs on every night including the ones that went wrong, and a
    summary that renders nothing is indistinguishable from a lost run."""

    @pytest.mark.parametrize("status", STATUS_VALUES)
    @pytest.mark.parametrize("dry_run", [False, True])
    def test_every_status_renders_a_result_line(self, status, dry_run):
        out = render(status, dry_run=dry_run, copied=1)
        assert out.startswith("## Nightly Box object mirror — prod")
        assert "\nResult: **" in out
        assert out.endswith("\n")

    @pytest.mark.parametrize("flag", FLAG_VALUES)
    def test_every_flag_the_job_can_set_reaches_the_page(self, flag):
        # A flag with no notice would be a condition raised and never shown.
        plain = render("ok", copied=1)
        assert render("ok", [flag], copied=1, source_gone=1) != plain, flag

    def test_it_never_says_it_deletes_anything(self):
        assert "Nothing on Box is ever deleted by it" in render(copied=1)


class TestTheCommandLine:
    def test_it_writes_the_summary_it_was_given(self, tmp_path, capsys):
        log = tmp_path / "log.txt"
        log.write_text(job_log("ok", "", copied=2))
        assert summary.main(["--env", "prod", "--log", str(log)]) == 0
        assert "2 images copied" in capsys.readouterr().out

    def test_an_unreadable_log_is_not_a_crash(self, capsys):
        assert (
            summary.main(["--env", "prod", "--log", "/nope", "--outcome", "success"])
            == 0
        )
        assert "Result: **succeeded**" in capsys.readouterr().out

    def test_the_report_is_read_when_the_log_has_no_verdict(self, tmp_path, capsys):
        report = tmp_path / "r.json"
        report.write_text('{"status": "stopped", "stats": {"copied": 8}}')
        summary.main(["--env", "prod", "--report", str(report)])
        assert "It got through 8 images copied." in capsys.readouterr().out


class TestANoticeIsNeverSwallowedByAHeadline:
    """Each of these can hold on a night that otherwise succeeded.

    So none of them may be a branch of the result: as an `elif` the notice
    would be invisible on exactly the nights it happens. The property is that
    whichever headline wins, the condition is still said somewhere.
    """

    # The phrase that proves the condition reached the page, per flag.
    SAID = {
        "collisions": "normalize onto one path on Box",
        "skipped_names": "only renaming them in Supabase can",
        "verify_mismatch": "not on Box",
        "verify_incomplete": "did not cover its whole sample",
        "ledger_stale": "The ledger on Box was NOT updated",
        "ledger_ahead": "Do NOT upload over it",
        "source_gone": "have no image behind them",
    }

    def test_every_flag_the_job_can_set_has_a_notice(self):
        assert set(FLAG_VALUES) == set(self.SAID), (
            "a flag with no notice is a condition raised and never shown"
        )

    # Every status a run that did work can end on. A stood-down run sets no
    # flags at all — it did nothing to raise one — which the test below pins.
    WORKED = tuple(s for s in STATUS_VALUES if s != "skipped")

    def test_a_stood_down_run_raises_no_condition_to_hide(self, caplog):
        import logging

        caplog.set_level(logging.INFO)
        emit = __import__("backup_objects").emit_status
        emit("skipped")
        assert "BOX_BACKUP_FLAGS=\n" in caplog.text + "\n"

    @pytest.mark.parametrize("flag", sorted(SAID))
    @pytest.mark.parametrize("status", WORKED)
    def test_it_survives_whichever_headline_won(self, flag, status):
        out = render(status, [flag], copied=3, source_gone=2)
        assert self.SAID[flag] in out, f"{status} + {flag} hides the notice"

    @pytest.mark.parametrize("flag", sorted(SAID))
    def test_it_survives_alongside_every_other_condition(self, flag):
        out = render("failed", list(self.SAID), copied=3, source_gone=2)
        assert self.SAID[flag] in out, f"{flag} is lost when everything happens"
