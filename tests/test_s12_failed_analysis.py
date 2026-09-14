"""S12FIX — a failed analysis must never read as a clean bill.

`HANDOFF_2026-08-27.md` §5.1, ruled D-3 in the roadmap: `run_rca` catches every
exception and returns `RcaResult(status="error", reason=...)`, and before this
session NO caller branched on that status. A broken analysis therefore fell
through `synthesize_findings` — which only refuses on a gate FAIL — to
`no_significant_findings` at HIGH confidence, carrying a full ISO Zone verdict.
The live reproduction was a single deleted config key.

The reproduction is reproduced here deterministically: the route profile with
ONE confidence weight removed. `_factor` reads `conf_cfg["weights"][key]`
directly (`bearing_rca.py:235`), so the first detector to cite that factor
raises inside `run_rca`'s catch-all → `status="error"`, with every other layer
(gate, ISO classify, trend) untouched and honest. That is the whole hazard: the
data was FINE, so nothing else complains, and only the RCA status says the
diagnosis never happened.

Scope note: `machine_off` is NOT an error — it is a legitimate determination
(rpm below `min_rpm`) that in the pipeline only arises behind a passing gate,
and it is pinned here to stay out of the error path.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vib_agent.cli import app
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import Case
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import status_text
from vib_agent.report.generate import render_markdown
from vib_agent.synth.generator import make_case
from vib_agent.webapp.jobs import ERROR_TAXONOMY, Job
from vib_agent.webapp.spend import SpendGuard
from vib_agent.webapp.worker import process_job

runner = CliRunner()

# The weight every detector's opening factor cites. Deleting it is the smallest
# possible "one config key" — the same shape as the live reproduction.
_DELETED_WEIGHT = "base"


@pytest.fixture
def iso_table() -> dict:
    return load_config("iso_zones")["zones"]


@pytest.fixture
def rules() -> dict:
    return load_config("next_measurements")


@pytest.fixture
def broken_thresholds() -> dict:
    """The product profile with exactly one rca-path config key removed."""
    t = copy.deepcopy(load_thresholds("route"))
    del t["confidence"]["weights"][_DELETED_WEIGHT]
    return t


@pytest.fixture
def faulted_case(iso_table, thresholds) -> Case:
    """A seeded BPFO case — a machine with a real fault, so a detector fires
    and reaches the deleted weight. Built on the INTACT profile so the case
    itself is identical to every other test's."""
    return make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)


@pytest.fixture
def quiet_faulted_case(faulted_case) -> Case:
    """The same seeded BPFO spectra on a QUIET machine (Zone A velocities).

    This is the literal §5.1 symptom and the worse half of the hazard: the
    faulted `bpfo` recipe reads 5.2 mm/s (Zone D), so a broken RCA there falls
    to `elevated_vibration_undetermined` — alarming enough that someone might
    look. Drop the velocities into Zone A and the same broken RCA produces
    `no_significant_findings` at HIGH confidence: a bearing tone is sitting in
    the envelope, the screen crashed, and the report says the machine is fine.
    Bearing tones are deliberately not severity-gated (thresholds.json
    `_rationale_one_x_severity_gate`), so the detector still fires here.
    """
    sd = faulted_case.sensor_data.model_copy(
        update={"x_velocity_mm_sec": 0.2, "y_velocity_mm_sec": 0.5, "z_velocity_mm_sec": 0.45}
    )
    return faulted_case.model_copy(update={"sensor_data": sd, "expected": None})


def _analyze(case, iso_table, thresholds, rules):
    return run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


class TestTheReproduction:
    """Before asserting the fix, prove the failure is real and is the one §5.1
    describes — otherwise the regression test guards nothing."""

    def test_one_deleted_key_makes_the_rca_status_error(
        self, faulted_case, iso_table, broken_thresholds, rules
    ):
        result = _analyze(faulted_case, iso_table, broken_thresholds, rules)
        assert result.rca is not None
        assert result.rca.status == "error"
        assert _DELETED_WEIGHT in (result.rca.reason or "")

    def test_nothing_else_complains(self, faulted_case, iso_table, broken_thresholds, rules):
        """The hazard's whole shape: the data is fine, so no other layer
        objects. Only the RCA status knows the diagnosis never ran."""
        result = _analyze(faulted_case, iso_table, broken_thresholds, rules)
        assert result.quality_gate.overall != "fail"
        assert result.iso is not None and result.iso.iso_zone in ("A", "B", "C", "D")

    def test_the_intact_profile_still_commits_the_fault(
        self, faulted_case, iso_table, thresholds, rules
    ):
        """Control: the same case on the intact profile is a committed bearing
        fault, so the tests below measure the deleted key and nothing else."""
        result = _analyze(faulted_case, iso_table, thresholds, rules)
        assert result.rca is not None and result.rca.status == "ok"
        assert any(f.fault.startswith("bearing_") for f in result.findings)


class TestNoCleanBill:
    """The S12 assertion itself, at the layer that produces the claim."""

    def test_a_failed_analysis_commits_no_findings(
        self, faulted_case, iso_table, broken_thresholds, rules
    ):
        result = _analyze(faulted_case, iso_table, broken_thresholds, rules)
        assert result.findings == [], (
            "a failed RCA must commit nothing — any Finding here is a claim the "
            "analysis did not earn"
        )

    def test_never_no_significant_findings(
        self, quiet_faulted_case, iso_table, broken_thresholds, rules
    ):
        """The exact §5.1 symptom, on the machine that actually produces it."""
        result = _analyze(quiet_faulted_case, iso_table, broken_thresholds, rules)
        assert "no_significant_findings" not in {f.fault for f in result.findings}

    def test_the_quiet_machine_reproduction_is_the_real_one(
        self, quiet_faulted_case, iso_table, thresholds, broken_thresholds, rules
    ):
        """Guards the fixture itself: on the INTACT profile this case commits a
        bearing fault, and its zone is A/B — so a clean bill here would be the
        literal §5.1 sentence, not a near miss. If a future threshold change
        stops the detector firing, this test says so instead of the suite
        quietly passing on a vacuous reproduction."""
        good = _analyze(quiet_faulted_case, iso_table, thresholds, rules)
        assert good.rca is not None and good.rca.status == "ok"
        assert any(f.fault.startswith("bearing_") for f in good.findings)
        assert good.iso is not None and good.iso.iso_zone in ("A", "B")
        broken = _analyze(quiet_faulted_case, iso_table, broken_thresholds, rules)
        assert broken.rca is not None and broken.rca.status == "error"

    def test_the_status_line_carries_no_zone_verdict(
        self, faulted_case, iso_table, broken_thresholds, rules
    ):
        """`status_text` is the badge alt-text AND the plain-text verdict — the
        single most quotable clean-bill line in the document."""
        result = _analyze(faulted_case, iso_table, broken_thresholds, rules)
        line = status_text(result)
        assert "ISO ZONE" not in line
        assert "no fault signature identified" not in line

    def test_the_report_never_says_normal_range(
        self, faulted_case, iso_table, broken_thresholds, rules
    ):
        result = _analyze(faulted_case, iso_table, broken_thresholds, rules)
        md = render_markdown(
            result, faulted_case.machine, case=faulted_case,
            thresholds=broken_thresholds, profile="route",
        )
        for clean_bill in (
            "parameters within normal range",
            "no fault signature identified",
            "This is a positive result",
            "Continue routine monitoring",
        ):
            assert clean_bill not in md, f"clean-bill language survived: {clean_bill!r}"

    def test_the_report_says_the_analysis_failed(
        self, faulted_case, iso_table, broken_thresholds, rules
    ):
        """Silence is not honesty: an empty Diagnosis section reads as a clean
        bill too. The document must SAY that the screen did not run."""
        result = _analyze(faulted_case, iso_table, broken_thresholds, rules)
        md = render_markdown(
            result, faulted_case.machine, case=faulted_case,
            thresholds=broken_thresholds, profile="route",
        )
        lowered = md.lower()
        assert "no diagnosis" in lowered
        assert "fault screen" in lowered or "analysis failed" in lowered

    def test_a_healthy_report_is_untouched(self, iso_table, thresholds, rules):
        """The ok path keeps its clean bill — this fix must not make the
        product timid about a genuinely clean machine."""
        case = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = _analyze(case, iso_table, thresholds, rules)
        assert result.rca is not None and result.rca.status == "ok"
        assert "no_significant_findings" in {f.fault for f in result.findings}
        assert "ISO ZONE" in status_text(result)


class TestWebappTerminatesAsError:
    """The job path: the analyst gets an error card, never a report."""

    def _run(self, job_dir, case, iso_table, thresholds, rules, **over):
        # A SUBDIRECTORY, never `tmp_path` itself. PURGE-SYNC deletes a failing
        # job's directory at the moment of failure and every case in this class
        # fails, so handing over the pytest temp root would have this helper
        # rmtree it. Same idiom as tests/test_worker_case.py::_run_job.
        job_dir = Path(job_dir) / "job"
        job_dir.mkdir(parents=True, exist_ok=True)
        job = Job(id="s12", code_label="engineer-1", job_dir=job_dir)
        kwargs = dict(
            iso_table=iso_table, thresholds=thresholds, rules=rules,
            spend_guard=SpendGuard(daily_token_budget=10**9),
            conversion_note="", client=None, drafting_available=False,
        )
        kwargs.update(over)
        process_job(job, case, **kwargs)
        return job

    def test_the_job_ends_in_error(
        self, tmp_path, faulted_case, iso_table, broken_thresholds, rules
    ):
        job = self._run(tmp_path, faulted_case, iso_table, broken_thresholds, rules)
        assert job.state == "error"

    def test_degraded_is_not_the_answer(
        self, tmp_path, faulted_case, iso_table, broken_thresholds, rules
    ):
        """`drafting_available=False` would normally degrade to the
        deterministic report. An RCA failure must be caught BEFORE that branch:
        degrading here would ship the clean bill as a PDF."""
        job = self._run(tmp_path, faulted_case, iso_table, broken_thresholds, rules)
        assert job.state != "degraded"
        assert job.degraded_reason is None
        assert job.pdf_path is None

    def test_it_maps_onto_the_ratified_taxonomy(
        self, tmp_path, faulted_case, iso_table, broken_thresholds, rules
    ):
        job = self._run(tmp_path, faulted_case, iso_table, broken_thresholds, rules)
        assert job.error_code == "internal_error"
        assert job.failure_kind == "server_error"
        assert job.retryable is True

    def test_no_result_summary_is_published(
        self, tmp_path, faulted_case, iso_table, broken_thresholds, rules
    ):
        """`result_summary` is what the UI card renders as the verdict."""
        job = self._run(tmp_path, faulted_case, iso_table, broken_thresholds, rules)
        assert job.result_summary is None

    def test_a_good_job_still_completes(
        self, tmp_path, faulted_case, iso_table, thresholds, rules
    ):
        """Control: the same job on the intact profile still produces a report
        (degraded, because drafting is unavailable in this test) — the new
        branch fires on the RCA status and on nothing else."""
        job = self._run(tmp_path, faulted_case, iso_table, thresholds, rules)
        assert job.state == "degraded"
        assert job.error_code is None


class TestCliRefusesToPrintACleanBill:
    def test_no_llm_exits_nonzero(self, tmp_path, monkeypatch, faulted_case, broken_thresholds):
        case_file = tmp_path / "bpfo_case.json"
        case_file.write_text(faulted_case.model_dump_json())
        monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", tmp_path / "outputs")
        monkeypatch.setattr("vib_agent.cli.load_thresholds", lambda _p: broken_thresholds)

        result = runner.invoke(app, ["analyze", str(case_file), "--no-llm"])

        assert result.exit_code != 0, result.output
        assert "no diagnosis" in result.output.lower()

    def test_a_good_case_still_exits_zero(self, tmp_path, monkeypatch, faulted_case):
        case_file = tmp_path / "bpfo_case.json"
        case_file.write_text(faulted_case.model_dump_json())
        monkeypatch.setattr("vib_agent.cli._OUTPUTS_DIR", tmp_path / "outputs")
        result = runner.invoke(app, ["analyze", str(case_file), "--no-llm"])
        assert result.exit_code == 0, result.output


class TestTaxonomyUnchanged:
    def test_no_new_error_code_was_added(self):
        """Wire law: THIS session maps onto the ratified taxonomy, it does not
        extend it. `internal_error` already means exactly this.

        Kept as a full-set pin rather than narrowed to S12FIX's own code, because
        the property worth protecting is that nobody extends the taxonomy
        SILENTLY: adding a code has to break this assertion and be justified in a
        close-out. Session HIST-2 did exactly that — `not_comparable`
        (bad_upload, not retryable), ratified by the operator under roadmap D-7
        for the two-file comparison lane, where "both files read fine and are
        still not a before/after pair" had no honest existing code
        (`merge_failed` is a different promise). The S12FIX claim below is
        unchanged and still asserted: an RCA crash is `internal_error`."""
        assert set(ERROR_TAXONOMY) == {
            "upload_unreadable",
            "interpretation_failed",
            "inference_unavailable",
            "merge_failed",
            "internal_error",
            "timeout",
            "upload_write_failed",
            "not_comparable",   # HIST-2, ratified (roadmap D-7)
        }
        # S12FIX's own claim, which the file already asserted and which is what
        # survives any future ratified addition: the crash path uses a code that
        # already existed.
        assert ERROR_TAXONOMY["internal_error"].kind == "server_error"
        assert ERROR_TAXONOMY["internal_error"].retryable is True
