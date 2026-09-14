"""INTAKE-HARDEN stretch A — the beta digest, tested against fixture lines.

Offline: this suite never touches a server. The fixture lines below are built
from the SOURCE of the log line, not from memory —
`src/vib_agent/webapp/app.py::_log_job_outcome` for the message and the
`logging.Formatter` in `create_app` for the prefix — and one test regenerates a
line through the real logger to prove that claim rather than assert it.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

# scripts/ is not a package, so the digest is loaded from its path. Registering
# it in sys.modules BEFORE exec_module is not optional: @dataclass resolves its
# annotations through sys.modules[cls.__module__], and an unregistered module
# makes that None.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "beta_digest.py"
_spec = importlib.util.spec_from_file_location("beta_digest", _SCRIPT)
beta_digest = importlib.util.module_from_spec(_spec)
sys.modules["beta_digest"] = beta_digest
_spec.loader.exec_module(beta_digest)


LINES = """\
2026-08-05 09:14:02,101 INFO vib_agent.webapp vib-agent webapp starting mode=prod version=0.1.0
2026-08-05 09:15:11,880 INFO vib_agent.webapp job=a1 code=engineer-1 kind=tabular_spectrum size_bytes=41003 duration_ms=18422.7 tokens=14210 outcome=done
2026-08-05 09:31:44,004 INFO vib_agent.webapp job=a2 code=engineer-1 kind=inferred(txt) size_bytes=8213 duration_ms=9133.4 tokens=150 outcome=gate_fail
2026-08-05 10:02:07,551 INFO vib_agent.webapp job=a3 code=reliability-2 kind=unknown size_bytes=512 duration_ms=n/a tokens=0 outcome=error
2026-08-05 11:44:19,020 INFO vib_agent.webapp job=a4 code=reliability-2 kind=multiaxis(tabular_spectrum,tabular_spectrum,tabular_spectrum) size_bytes=122880 duration_ms=31004.2 tokens=22940 outcome=done
2026-08-05 15:09:55,300 INFO vib_agent.webapp job=a5 code=reliability-2 kind=inferred_spectrum size_bytes=9541 duration_ms=7710.0 tokens=1980 outcome=degraded
2026-08-06 08:02:00,000 INFO vib_agent.webapp job=b1 code=engineer-1 kind=tabular_trend size_bytes=2048 duration_ms=4210.5 tokens=9100 outcome=done
2026-08-06 08:40:13,777 INFO vib_agent.webapp job=b2 code=engineer-1 kind=inferred(dat) size_bytes=8317 duration_ms=n/a tokens=310 outcome=awaiting_confirm
"""


@pytest.fixture
def parsed():
    return beta_digest.parse_log(LINES)


class TestParsing:
    def test_only_job_lines_are_parsed(self, parsed):
        jobs, unparsed = parsed
        assert len(jobs) == 7          # the startup line is not a job line
        assert unparsed == 0
        assert [j.job for j in jobs] == ["a1", "a2", "a3", "a4", "a5", "b1", "b2"]

    def test_every_field_survives(self, parsed):
        first = parsed[0][0]
        assert first.date == "2026-08-05" and first.time == "09:15:11"
        assert first.code == "engineer-1"
        assert first.kind == "tabular_spectrum"
        assert first.size_bytes == 41003
        assert first.duration_ms == 18422.7
        assert first.tokens == 14210
        assert first.outcome == "done"

    def test_duration_na_is_none_not_zero(self, parsed):
        """`duration_ms` is logged with %s, so an unstarted job writes 'n/a'.
        Read as 0.0 it would drag every median it appears in downwards."""
        by_id = {j.job: j for j in parsed[0]}
        assert by_id["a3"].duration_ms is None
        assert by_id["a1"].duration_ms == 18422.7

    def test_a_kind_containing_commas_and_parens_is_not_split(self, parsed):
        """multiaxis(a,b,c) is one field. Tokenising on commas would shear it
        and take size_bytes with it."""
        by_id = {j.job: j for j in parsed[0]}
        assert by_id["a4"].kind == "multiaxis(tabular_spectrum,tabular_spectrum,tabular_spectrum)"
        assert by_id["a4"].size_bytes == 122880

    def test_lane_families(self, parsed):
        by_id = {j.job: j for j in parsed[0]}
        assert by_id["a1"].family == "tabular"
        assert by_id["a2"].family == "inferred"
        assert by_id["a5"].family == "inferred"      # inferred_spectrum, same lane
        assert by_id["a4"].family == "multiaxis"
        assert by_id["a3"].family == "unknown"

    def test_a_changed_format_is_counted_not_swallowed(self):
        """A digest that silently reports 0 jobs after a log-format change is
        worse than no digest."""
        broken = "2026-08-06 08:02:00,000 INFO vib_agent.webapp job=z1 code=e1 outcome=done\n"
        jobs, unparsed = beta_digest.parse_log(broken)
        assert jobs == [] and unparsed == 1

    def test_the_pattern_matches_a_line_the_real_logger_writes(self, caplog):
        """The claim this whole script rests on, proved rather than asserted:
        the app's own logger, with the app's own format string, produces a line
        this parser reads."""
        from vib_agent.webapp.app import _log_job_outcome
        from vib_agent.webapp.jobs import Job

        job = Job(id="deadbeef", state="done", code_label="engineer-1",
                  kind="inferred(txt)", file_size=8213)
        job.duration_ms = 9133.4
        job.token_usage = {"input_tokens": 100, "output_tokens": 50}

        with caplog.at_level(logging.INFO, logger="vib_agent.webapp"):
            _log_job_outcome(job)
        message = caplog.records[-1].getMessage()

        formatted = f"2026-08-06 01:58:47,826 INFO vib_agent.webapp {message}"
        line = beta_digest.parse_line(formatted)
        assert line is not None, f"the parser no longer reads the real line: {message}"
        assert line.job == "deadbeef" and line.outcome == "done"
        assert line.kind == "inferred(txt)" and line.tokens == 150


class TestDigest:
    def test_it_splits_by_day(self, parsed):
        markdown = beta_digest.build_digest(*parsed)
        assert "## 2026-08-05" in markdown and "## 2026-08-06" in markdown
        assert markdown.index("## 2026-08-05") < markdown.index("## 2026-08-06")

    def test_a_single_day_can_be_selected(self, parsed):
        markdown = beta_digest.build_digest(*parsed, only="2026-08-06")
        assert "## 2026-08-06" in markdown and "## 2026-08-05" not in markdown

    def test_outcomes_are_counted_and_explained(self, parsed):
        markdown = beta_digest.build_digest(*parsed, only="2026-08-05")
        assert "| done | 2 |" in markdown
        assert "| gate_fail | 1 |" in markdown
        assert "| error | 1 |" in markdown
        assert "| degraded | 1 |" in markdown
        assert "insufficient data" in markdown        # the taxonomy, not just a count

    def test_an_unknown_outcome_is_flagged_loudly(self):
        line = ("2026-08-06 08:02:00,000 INFO vib_agent.webapp job=x1 code=e1 kind=k "
                "size_bytes=1 duration_ms=1.0 tokens=0 outcome=exploded\n")
        markdown = beta_digest.build_digest(*beta_digest.parse_log(line))
        assert "UNKNOWN OUTCOME" in markdown

    def test_codes_and_lanes_are_broken_out(self, parsed):
        markdown = beta_digest.build_digest(*parsed, only="2026-08-05")
        assert "engineer-1" in markdown and "reliability-2" in markdown
        assert "inferred" in markdown and "multiaxis" in markdown

    def test_the_failure_taxonomy_appears_when_there_are_failures(self, parsed):
        markdown = beta_digest.build_digest(*parsed, only="2026-08-05")
        assert "### Failure taxonomy" in markdown
        assert "2 of 5 jobs did not produce a diagnosis" in markdown

    def test_no_failure_section_on_a_clean_day(self):
        line = ("2026-08-06 08:02:00,000 INFO vib_agent.webapp job=x1 code=e1 kind=k "
                "size_bytes=1 duration_ms=1.0 tokens=0 outcome=done\n")
        markdown = beta_digest.build_digest(*beta_digest.parse_log(line))
        assert "Failure taxonomy" not in markdown

    def test_tokens_and_timing_are_summarised(self, parsed):
        markdown = beta_digest.build_digest(*parsed, only="2026-08-05")
        assert "39,280 tokens" in markdown          # 14210+150+0+22940+1980
        assert "median" in markdown and "slowest" in markdown

    def test_it_survives_a_log_with_no_job_lines(self):
        markdown = beta_digest.build_digest(*beta_digest.parse_log(
            "2026-08-05 09:14:02,101 INFO vib_agent.webapp vib-agent webapp starting mode=prod\n"))
        assert "No job lines found." in markdown


class TestItStaysOffline:
    def test_the_script_makes_no_connections(self):
        """A digest tool is the obvious place for someone to add a convenience
        fetch. The ssh one-liner is DOCUMENTED, in a string, and never run."""
        source = _SCRIPT.read_text()
        for forbidden in ("subprocess", "socket", "requests", "urllib", "httpx", "paramiko"):
            assert forbidden not in source, f"beta_digest.py imported {forbidden}"
        assert "ssh <host>" in beta_digest.FETCH_COMMAND
        assert "/var/log/vibagent/app.log" in beta_digest.FETCH_COMMAND

    def test_the_digest_carries_nothing_the_log_does_not(self, parsed):
        """The log line has no IP, no filename, no machine alias and no file
        content — by design. This script must never become the reason someone
        adds them."""
        source = _SCRIPT.read_text()
        for field in ("ip", "filename", "machine_alias", "client_addr"):
            assert f"{field}=" not in source

    def test_cli_reports_a_missing_file_without_a_traceback(self, capsys, tmp_path):
        assert beta_digest.main([str(tmp_path / "nope.log")]) == 2
        assert "no such file" in capsys.readouterr().err

    def test_cli_writes_markdown(self, tmp_path):
        log = tmp_path / "app.log"
        log.write_text(LINES)
        out = tmp_path / "digest.md"
        assert beta_digest.main([str(log), "--out", str(out)]) == 0
        assert "# vib-agent beta digest" in out.read_text()


# ══════════════════════════════════════════════════════════════════════════
# Session TIDY-2 — the alarm stops crying wolf, and the crash line gets read
#
# FLIP-1 F-6: `parse_log` counted every line containing " job=" that was not
# the full outcome line as UNPARSED, which is the counter whose whole meaning
# is "the log format changed". Eight writers in the tree emit a ` job=` line
# that is not that line, so the alarm fired on the days something crashed —
# the days an operator most needs to be able to trust it. `app.py` was not
# TIDY-2's to edit; the reader was.
# ══════════════════════════════════════════════════════════════════════════

P = "2026-09-10 08:02:00,000 "

# Every line in the tree that names a job and is NOT `_log_job_outcome`'s
# record, read off source at TIDY-2. A ninth writer is one row here.
NOT_THE_OUTCOME_LINE = [
    ("app.py::_log_internal_failure",
     P + "WARNING vib_agent.webapp job=a1 internal_failure exc=builtins.ValueError"),
    ("worker.py::_log_draft_failure",
     P + "WARNING vib_agent.webapp job=a1 draft_failed exc=vib_agent.agent.loop.AgentAnalysisError"),
    ("worker.py store_write_failed",
     P + "WARNING vib_agent.webapp job=a1 store_write_failed exc=sqlalchemy.exc.OperationalError"),
    ("worker.py credit_refund_failed",
     P + "WARNING vib_agent.webapp job=a1 credit_refund_failed exc=sqlalchemy.exc.OperationalError"),
    ("worker.py rca_error side=",
     P + "WARNING vib_agent.webapp job=a1 rca_error side=left reason=no_geometry"),
    ("worker.py rca_error",
     P + "WARNING vib_agent.webapp job=a1 rca_error reason=no_geometry"),
    ("worker.py compare_failed",
     P + "WARNING vib_agent.webapp job=a1 compare_failed"),
    ("billing/spend.py credit_refunded",
     P + "INFO vib_agent.webapp credit_refunded job=a1"),
    ("app.py confirm_abandoned (FLIP-1, already `job_id=`)",
     P + "INFO vib_agent.webapp confirm_abandoned job_id=a1 code=engineer-1"),
]

# What the alarm IS for: a line that is trying to be the per-job record.
CHANGED_OUTCOME_LINES = [
    ("a field dropped",
     P + "INFO vib_agent.webapp job=z1 code=e1 outcome=done"),
    ("a field renamed",
     P + "INFO vib_agent.webapp job=z1 label=e1 kind=k size_bytes=1 duration_ms=1.0 "
         "tokens=0 outcome=done"),
    ("outcome moved off the end",
     P + "INFO vib_agent.webapp job=z1 code=e1 kind=k size_bytes=1 outcome=done "
         "duration_ms=1.0 tokens=0"),
    ("a field added in the middle",
     P + "INFO vib_agent.webapp job=z1 code=e1 kind=k size_bytes=1 duration_ms=1.0 "
         "tokens=0 retries=2 outcome=done"),
]


class TestTheFormatAlarmCountsOnlyTheLineItIsAbout:

    @pytest.mark.parametrize("name,line", NOT_THE_OUTCOME_LINE,
                             ids=[n for n, _ in NOT_THE_OUTCOME_LINE])
    def test_an_event_line_about_a_job_is_not_a_format_change(self, name, line):
        """The bug, one row at a time. Before TIDY-2 the first eight of these
        each scored 1, so a day with one crash and one lost draft told the
        operator the log format had changed twice."""
        jobs, unparsed = beta_digest.parse_log(line)
        assert (jobs, unparsed) == ([], 0), (
            f"{name} is being counted as a log-format change; the alarm is about "
            "`_log_job_outcome`'s record, not about every line that names a job"
        )

    def test_the_whole_inventory_together_is_still_zero(self):
        """A day's worth of them, because the counter is a sum and a per-line
        pin would not catch a predicate that leaked on the second occurrence."""
        jobs, unparsed = beta_digest.parse_log(
            "\n".join(line for _, line in NOT_THE_OUTCOME_LINE))
        assert (jobs, unparsed) == ([], 0)

    @pytest.mark.parametrize("name,line", CHANGED_OUTCOME_LINES,
                             ids=[n for n, _ in CHANGED_OUTCOME_LINES])
    def test_a_real_format_change_still_fires(self, name, line):
        """Non-vacuity, and the reason the filter is not simply "does it parse".
        Each of these IS the per-job record with something moved, and a digest
        that reported 0 jobs for a day of traffic without saying so is the
        failure this counter exists to prevent."""
        jobs, unparsed = beta_digest.parse_log(line)
        assert (jobs, unparsed) == ([], 1), f"a changed outcome line ({name}) was swallowed"

    def test_a_real_line_beside_a_crash_reports_one_job_and_no_alarm(self):
        """The mixed case, which is what a real log is."""
        real = LINES.splitlines()[1]
        crash = NOT_THE_OUTCOME_LINE[0][1]
        jobs, unparsed = beta_digest.parse_log(f"{real}\n{crash}\n")
        assert len(jobs) == 1 and unparsed == 0

    def test_the_traceback_under_a_crash_line_is_not_counted_either(self):
        """`_log_internal_failure` writes the traceback into the same record,
        so `splitlines()` hands this parser the frames as well. None of them
        may reach the counter — including the frame that would show the format
        string itself, which is the one that contains the word `outcome=`."""
        record = (NOT_THE_OUTCOME_LINE[0][1] + "\nTraceback (most recent call last):\n"
                  '  File "/opt/vibagent/src/vib_agent/webapp/app.py", line 122, in _log_job_outcome\n'
                  '    "job=%s code=%s kind=%s size_bytes=%d duration_ms=%s tokens=%d%s outcome=%s",\n'
                  "ValueError: boom")
        assert beta_digest.parse_log(record) == ([], 0)

    def test_the_alarm_says_it_is_about_the_format(self):
        """What the alarm SHOULD have said. The old text — *"contained `job=`"*
        — described the population it counted rather than the thing it detects,
        which is exactly how it came to count eight other lines."""
        markdown = beta_digest.build_digest(
            *beta_digest.parse_log(CHANGED_OUTCOME_LINES[0][1]))
        assert "The log line has probably changed" in markdown
        assert "_log_job_outcome" in markdown
        assert "not a count of failures" in markdown
        assert "contained `job=` but did not match" not in markdown


class TestTheCrashLineIsReadRatherThanIgnored:
    """Narrowing the filter on its own would have cost the operator the only
    crash signal this digest has — today a crash at least produces SOMETHING,
    even if it is mislabelled. So the line is parsed instead of skipped."""

    CRASH = P + "WARNING vib_agent.webapp job=deadbeefcafe internal_failure " \
                "exc=vib_agent.report.render_proc.RenderChildCrashed"

    def test_it_is_parsed(self):
        crashes = beta_digest.parse_crashes(self.CRASH)
        assert len(crashes) == 1
        assert crashes[0].job == "deadbeefcafe"
        assert crashes[0].exc == "vib_agent.report.render_proc.RenderChildCrashed"
        assert crashes[0].date == "2026-09-10"

    def test_the_traceback_is_not_read(self):
        """The frames name paths on the server and are the one part of this
        line that is not already safe to paste around."""
        crashes = beta_digest.parse_crashes(
            self.CRASH + '\nTraceback (most recent call last):\n  File "/opt/vibagent/x.py"')
        assert len(crashes) == 1
        assert not any("opt/vibagent" in str(c) for c in crashes)

    def test_nothing_else_is_read_as_a_crash(self):
        for _, line in NOT_THE_OUTCOME_LINE[1:]:
            assert beta_digest.parse_crashes(line) == [], line
        assert beta_digest.parse_crashes(LINES) == []

    def test_it_reaches_the_digest_under_its_own_heading(self):
        markdown = beta_digest.build_digest(
            *beta_digest.parse_log(LINES),
            crashes=beta_digest.parse_crashes(self.CRASH))
        assert "### Internal failures" in markdown
        assert "RenderChildCrashed" in markdown
        assert "failed on OUR side" in markdown
        assert "not an analyst's file" in markdown

    def test_it_lands_on_its_own_day(self):
        """Grouped by date like everything else, so a crash on a quiet day is
        not filed under a busy one."""
        markdown = beta_digest.build_digest(
            *beta_digest.parse_log(LINES),
            crashes=beta_digest.parse_crashes(self.CRASH), only="2026-08-05")
        assert "RenderChildCrashed" not in markdown
        assert "## 2026-08-05" in markdown

    def test_a_day_with_a_crash_and_no_jobs_still_reports_it(self):
        """The worst day this product can have — every job died before it wrote
        an outcome — must not render as `No job lines found.`"""
        markdown = beta_digest.build_digest(
            *beta_digest.parse_log(self.CRASH),
            crashes=beta_digest.parse_crashes(self.CRASH))
        assert "### Internal failures" in markdown
        assert "RenderChildCrashed" in markdown
        assert "No job lines found." not in markdown

    def test_the_old_call_shape_still_works(self):
        """`build_digest(*parse_log(text))` is what every existing caller and
        both existing test files do; `crashes` is keyword-only with a default
        so none of them had to move."""
        assert "# vib-agent beta digest" in beta_digest.build_digest(
            *beta_digest.parse_log(LINES))

    def test_the_cli_reads_both_lines_in_one_pass_of_the_file(self, tmp_path):
        """End to end, through `main`, because the two parsers are wired
        separately and a digest that never called the second one would pass
        every unit test above."""
        log = tmp_path / "app.log"
        log.write_text(LINES + self.CRASH + "\n")
        out = tmp_path / "digest.md"
        assert beta_digest.main([str(log), "--out", str(out)]) == 0
        markdown = out.read_text()
        assert "### Internal failures" in markdown
        assert "RenderChildCrashed" in markdown
        assert "The log line has probably changed" not in markdown, (
            "the crash line raised the format alarm through the CLI path"
        )

    def test_the_crash_section_carries_nothing_the_log_does_not(self):
        """`TestItStaysOffline`'s rule, applied to the new section: the id and
        the exception class were already on the line, and nothing else is."""
        source = _SCRIPT.read_text()
        for field in ("ip", "filename", "machine_alias", "client_addr", "traceback="):
            assert f"{field}=" not in source
