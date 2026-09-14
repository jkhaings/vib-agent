"""Session R3-DIFF (item 3) — why jobs degraded, and the payload that caused it.

THE SYMPTOM. Production degraded to "Drafted narrative unavailable" on 3 of 3
cause-bearing reports and 0 of 2 without. Nothing in the log said why: both
handlers in webapp/worker.py caught the exception and discarded it, so a
consistency hard-fail, an API error and a spend cap were indistinguishable
after the fact -- three different bugs wearing one symptom.

THE CAUSE. agent/loop.py handed the drafting model the deterministic report as
its reference. On a bearing-fault case that report is 90% cause section
(30,030 of 33,329 characters -- eleven causes with their full sourced
mechanisms), and agent/consistency.py::check_cause_language requires any
mechanism a draft writes up to appear VERBATIM. Hand a model 156 lines of
sourced prose and tell it to write the report, and it paraphrases; the check
refuses it; the retry paraphrases differently; the job degrades. On a report
with no committed bearing fault there is no cause section, nothing to
paraphrase, and nothing fails -- hence 3-for-3 against 0-for-2.

THE FIX, in two independent halves:
  * the drafter is no longer handed the section it cannot improve (the section
    is spliced in deterministically afterwards regardless), and
  * every consistency contract stays armed, so a draft that invents cause
    language is still refused. Removing the temptation is not removing the check.
"""

from __future__ import annotations

import logging

import pytest

from vib_agent.agent.consistency import check_cause_language
from vib_agent.config import load_config, load_thresholds
from vib_agent.knowledge import CAUSE_HEADING, lookup_causes_for
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_markdown
from vib_agent.synth.generator import make_case
from vib_agent.webapp import worker


@pytest.fixture
def route() -> dict:
    return load_thresholds("route")


@pytest.fixture
def iso_table() -> dict:
    return load_config("iso_zones")["zones"]


@pytest.fixture
def bpfo(route, iso_table):
    """A committed bearing fault -- so the cause lookup returns entries."""
    case = make_case("bpfo", iso_table=iso_table, thresholds=route, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=route,
                          rules=load_config("next_measurements"))
    assert any(f.fault.startswith("bearing_") for f in result.findings)
    return case, result


@pytest.fixture
def healthy(route, iso_table):
    case = make_case("healthy", iso_table=iso_table, thresholds=route, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=route,
                          rules=load_config("next_measurements"))
    return case, result


# ══════════════════════════════════════════════════════════════════════════════
class TestThePayloadThatCausedIt:
    def test_the_cause_section_dominated_the_drafting_prompt(self, bpfo):
        """The measurement that identified the cause. Kept as a test so the
        number is re-derived rather than remembered."""
        case, result = bpfo
        full = render_markdown(result, case.machine)
        trimmed = render_markdown(result, case.machine, include_causes=False)
        removed = len(full) - len(trimmed)
        assert removed / len(full) > 0.80, (
            f"expected the cause section to dominate the prompt; it was {removed}/{len(full)}"
        )

    def test_the_drafter_is_no_longer_handed_it(self, bpfo):
        case, result = bpfo
        trimmed = render_markdown(result, case.machine, include_causes=False)
        assert CAUSE_HEADING not in trimmed
        for cause in lookup_causes_for([f.fault for f in result.findings]):
            assert cause.mechanism not in trimmed

    def test_nothing_else_was_removed_with_it(self, bpfo):
        """The exclusion must take exactly the two deterministically-spliced
        sections and nothing else. Pin moved by Session REPORT-NA: the flag now
        also removes the coverage roster from the drafting prompt, for the same
        reason it removes the causes — the section is spliced in afterwards
        from the same builder, and no consistency check pins coverage language,
        so keeping it out of the prompt is what keeps it deterministic."""
        import re

        case, result = bpfo
        full_h = re.findall(r"^#{1,3} .*$", render_markdown(result, case.machine), re.M)
        trim_h = re.findall(
            r"^#{1,3} .*$", render_markdown(result, case.machine, include_causes=False), re.M
        )
        removed = [h for h in full_h if h not in trim_h]
        assert removed, "nothing was removed at all"
        assert removed[0] == f"## {CAUSE_HEADING}"
        assert removed[-1] == "## Coverage — what this analysis did not assess"
        # everything else removed is a ### cause name under the cause heading
        assert all(h.startswith("### ") for h in removed[1:-1])
        assert not [h for h in trim_h if h not in full_h], "the exclusion ADDED a section"

    def test_a_report_with_no_bearing_fault_is_byte_identical(self, healthy):
        """The 0-for-2 half: with nothing committed there is no cause section.
        Pin moved by Session REPORT-NA: the flag now also removes the coverage
        roster, which every report carries — so the no-op claim becomes
        "byte-identical once exactly that section is excised", proving the flag
        still cannot move anything else in a healthy report."""
        case, result = healthy
        full = render_markdown(result, case.machine)
        trimmed = render_markdown(result, case.machine, include_causes=False)
        i_cov = full.index("## Coverage — what this analysis did not assess")
        i_rev = full.index("## Review & Approval")
        assert full[:i_cov] + full[i_rev:] == trimmed

    def test_the_default_still_includes_them(self, bpfo):
        """render_report and the deterministic --no-llm path are untouched."""
        case, result = bpfo
        assert CAUSE_HEADING in render_markdown(result, case.machine)


class TestEveryContractStaysArmed:
    """Removing the section from the PROMPT is not removing the CHECK."""

    def test_a_fabricated_cause_is_still_refused(self, bpfo):
        _case, result = bpfo
        narrative = (
            f"## {CAUSE_HEADING}\n\n"
            "### Excessive belt tension overloading the bearing\n\n"
            "The belt was too tight.\n"
        )
        mismatches = check_cause_language(narrative, result)
        assert any("fabricated cause" in m for m in mismatches)

    def test_a_rewritten_mechanism_is_still_refused(self, bpfo):
        _case, result = bpfo
        cause = lookup_causes_for([f.fault for f in result.findings])[0]
        narrative = (
            f"## {CAUSE_HEADING}\n\n### {cause.cause}\n\n"
            "Some dirt gets in and wears the raceway down over time.\n"
        )
        mismatches = check_cause_language(narrative, result)
        assert any("cause mechanism" in m for m in mismatches)

    def test_cause_language_without_a_bearing_fault_is_still_refused(self, healthy):
        _case, result = healthy
        mismatches = check_cause_language(
            f"## {CAUSE_HEADING}\n\n### Anything at all\n", result
        )
        assert mismatches

    def test_the_prompt_tells_the_model_not_to_write_the_section(self):
        """The other half of the fix. The reference report no longer carries the
        section, but the model still has the lookup_causes tool -- so if the
        prompt still invited a cause section it could write one from the tool
        payload instead, and a model-written section REPLACES the approved one
        (report/generate.py only splices when the draft has no such heading).
        The instruction closes that door; the checks above cover the case where
        it walks through it anyway."""
        from vib_agent.agent.system_prompt import SYSTEM_PROMPT

        lowered = SYSTEM_PROMPT.lower()
        assert "do not write the cause section at all" in lowered
        assert "appended to your report automatically" in lowered
        # Session F2 doctrine still holds for anything handed to the model.
        assert "root cause" not in lowered and "rca" not in lowered.split()

    def test_a_draft_with_no_cause_section_passes(self, bpfo):
        """The normal outcome now: the model writes prose, the section is spliced
        in afterwards, and the check has nothing to object to."""
        _case, result = bpfo
        narrative = "## Diagnosis\n\nAn outer-race fault is committed on the measured spectrum.\n"
        assert check_cause_language(narrative, result) == []


class TestTheDegradeIsNoLongerSilent:
    def test_the_failure_is_logged_with_the_job_id_and_a_traceback(self, caplog):
        job = worker.Job(id="job-abc123", code_label="beta-tester")
        try:
            raise ValueError("upstream refused the draft")
        except ValueError as exc:
            with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
                worker._log_draft_failure(job, exc)
        record = next(r for r in caplog.records if "draft_failed" in r.getMessage())
        message = record.getMessage()
        assert "job=job-abc123" in message
        assert "ValueError" in message
        assert "upstream refused the draft" in message
        assert "Traceback" in message

    def test_the_exception_type_distinguishes_the_three_causes(self, caplog):
        """A consistency hard-fail and an API error degrade identically in the
        PDF. They must not degrade identically in the log."""
        from vib_agent.agent.loop import AgentAnalysisError

        job = worker.Job(id="job-xyz", code_label="beta-tester")
        with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
            try:
                raise AgentAnalysisError("failed the consistency check after one retry: ...")
            except AgentAnalysisError as exc:
                worker._log_draft_failure(job, exc)
        assert "AgentAnalysisError" in caplog.text

    def test_it_logs_nothing_derived_from_the_upload(self, caplog):
        """The privacy model: the per-job line carries the invite LABEL, never
        the code, never a filename, never a machine alias, never an address.
        The draft-failure line must hold to the same bar."""
        job = worker.Job(id="job-priv", code_label="beta-tester")
        job.kind = "csv_spectrum"
        with caplog.at_level(logging.WARNING, logger="vib_agent.webapp"):
            try:
                raise RuntimeError("boom")
            except RuntimeError as exc:
                worker._log_draft_failure(job, exc)
        text = caplog.text
        assert "beta-tester" not in text  # the label belongs on the outcome line, not here
        for forbidden in ("192.168.", "127.0.0.1", "upload.csv", "@"):
            assert forbidden not in text

    def test_logging_never_turns_a_degrade_into_a_crash(self, monkeypatch):
        """A broken logger must not be the reason an analyst loses their report."""
        def explode(*_a, **_k):
            raise OSError("log sink is gone")

        monkeypatch.setattr(worker._log, "warning", explode)
        job = worker.Job(id="job-boom", code_label="x")
        worker._log_draft_failure(job, ValueError("original"))  # must not raise
