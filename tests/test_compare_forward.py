"""Session COMPARE-FWD — `process_compare_job` forwards every parameter
`process_job` accepts.

Session WORKER-CASE fixed one call site that handed its callee a subset of the
arguments the callee takes, and recorded a second instance of the same defect
class in its own close-out (`outputs/SESSION_WORKERCASE.md` §8 finding 1):

    `process_compare_job` silently drops three kwargs when it forwards to
    `process_job` — `coverage_note`, `forced_fail_checks` and
    `channel_summary` … A new `process_job` kwarg is invisible on the compare
    lane and no test would notice.

Measured on master: `process_job` declares 15 parameters, the forward passed
12. The three fell back to their defaults, so on a compare job —

  * `job.channel_summary` was always None (`worker.py`, set once at the top of
    `process_job`), and the status payload's `channels` key never appeared;
  * the `if forced_fail_checks:` branch was **dead**, which makes
    `_render_fail_closed` unreachable from this lane — and with it the only
    route that hands `comparison=` into that render;
  * `coverage_note` never reached `markdown_with_notes` or the HTML note list.

The dropped values are not really the defect. The SHAPE is: a hand-kept subset
with nothing holding it to its callee, so parameter 16 goes the same way and
just as quietly. Hence two pins that are about the shape rather than about
today's three names — a signature-parity test and an AST read of the forward —
and three that prove each dropped parameter now arrives somewhere an analyst
can see.

Scope note, so a later reader does not mistake this for a feature: the fix is
`worker.py` only. `app.py::_run_compare` still computes none of the three
(`coverage_note` has exactly one producer in the codebase,
`assembly.merge_channels`, and the compare lane deliberately never merges), so
the plumbing is inert in production until that is wired. `outputs/PROD_READINESS.md`
carries the finding.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from vib_agent.models import Check, ComparisonResult
from vib_agent.webapp import worker as worker_mod
from vib_agent.webapp.jobs import Job
from vib_agent.webapp.spend import SpendGuard
#: Bound at import, deliberately: one test monkeypatches `worker.process_job`
#: with a spy, and `inspect.signature` on the module ATTRIBUTE would then read
#: the spy's `(job, case, **kwargs)` instead of the real parameter list — the
#: parity check would quietly start asserting nothing.
from vib_agent.webapp.worker import process_compare_job, process_job as _real_process_job

# The WORKER-CASE fixtures, imported rather than re-minted: `documents` already
# captures BOTH documents plus the kwargs each renderer was handed, which is
# exactly what a forwarding pin needs. Cross-module import is the house style
# (tests/test_compare_webapp.py imports its helpers from test_webapp_e2e).
from tests.test_worker_case import _case, _spectrum_csv
from tests.test_worker_case import bearings, documents  # noqa: F401 -- used AS fixtures

#: 1800 rpm -> 30 Hz shaft; a 6206's BPFO sits at 107.03 Hz.
_ONE_X = 30.0
_BPFO = 107.03
#: The planted-repair pair (tests/test_compare_webapp.py's `_COMPARE_PAIRS`):
#: both readings keep a 1x line so both gates pass and the two are comparable;
#: the only thing that changed is the bearing tone.
_BEFORE = {_ONE_X: 0.5, _BPFO: 0.5}
_AFTER = {_ONE_X: 0.5}

_COVERAGE = "Severity/ISO assessment is based on the SENTINEL channel only."


# ── the two parameters this function legitimately derives ────────────────
#
# Named here, with the reason, because the parity test below is only as honest
# as this exemption list. Anything else missing from the forward is a defect.
_DERIVED = {
    "case": "the AFTER reading — the compare lane reports the machine's condition NOW",
    "comparison": "computed inside process_compare_job by pdm_core.compare.compare_readings",
}
#: `process_compare_job`'s own extras: the pair from which `case` is derived.
_COMPARE_ONLY = {"before_case", "after_case"}


def _forwardable() -> set[str]:
    """Every `process_job` parameter `process_compare_job` must accept and pass
    on — the whole signature minus `job` (positional, always forwarded) and the
    two derived above."""
    return set(inspect.signature(_real_process_job).parameters) - {"job"} - set(_DERIVED)


def _forward_call() -> ast.Call:
    """The `process_job(...)` call inside `process_compare_job`, as a syntax
    tree.

    Read with `ast`, not a text grep, for the reason
    tests/test_charts_threading.py gives: this module's prose and worker.py's
    docstring both have to be free to NAME the dropped kwargs while explaining
    them, and a grep over stripped text can be defeated by a docstring."""
    tree = ast.parse(Path(worker_mod.__file__).read_text())
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "process_compare_job"
    )
    calls = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "process_job"
    ]
    assert len(calls) == 1, f"expected exactly one forward, found {len(calls)}"
    return calls[0]


def _pair(tmp_path, bearings):
    """Two readings of one point, Before and After."""
    before = _case(_spectrum_csv(tmp_path / "before.csv", _BEFORE), bearings, bearing_model="6206")
    after = _case(_spectrum_csv(tmp_path / "after.csv", _AFTER), bearings, bearing_model="6206")
    return before, after


def _run_compare_job(job_dir, before_case, after_case, iso_table, thresholds, rules, **over) -> Job:
    """`drafting_available=False` lands every gate-passing case on
    `_render_degraded` — the tests/test_worker_case.py `_run_job` idiom, with no
    app, no HTTP and no API key."""
    job = Job(id="cf", code_label="engineer-1", job_dir=job_dir)
    kwargs = dict(
        before_case=before_case, after_case=after_case,
        iso_table=iso_table, thresholds=thresholds, rules=rules,
        spend_guard=SpendGuard(daily_token_budget=10**9),
        conversion_note="", client=None, drafting_available=False,
    )
    kwargs.update(over)
    process_compare_job(job, **kwargs)
    return job


# ── the shape: a 16th parameter must fail loudly ─────────────────────────


class TestSignatureParity:
    def test_every_process_job_parameter_is_on_process_compare_job(self):
        """The pin the session exists for. `inspect.signature` on both, the
        tests/test_history_tenancy.py idiom — equality, not containment, so a
        compare-only parameter that no longer means anything is caught too."""
        compare_params = (
            set(inspect.signature(worker_mod.process_compare_job).parameters)
            - {"job"} - _COMPARE_ONLY
        )
        forwardable = _forwardable()

        dropped = forwardable - compare_params
        assert not dropped, (
            "process_compare_job cannot forward what it does not accept: "
            f"{sorted(dropped)} are on process_job and missing here. Add them as "
            "explicit keyword parameters and forward them — or, if one is genuinely "
            "derived inside process_compare_job, name it in _DERIVED with its reason."
        )
        stale = compare_params - forwardable
        assert not stale, (
            f"process_compare_job accepts {sorted(stale)}, which process_job no "
            "longer takes — the forward would raise, or the value is dead"
        )

    def test_the_shared_parameters_keep_the_same_defaults(self):
        """Names matching is not enough: a forwarded parameter whose default
        drifts changes what a caller that omits it gets, on one lane only."""
        job_params = inspect.signature(_real_process_job).parameters
        compare_params = inspect.signature(worker_mod.process_compare_job).parameters
        for name in sorted(_forwardable()):
            assert compare_params[name].default == job_params[name].default, (
                f"{name!r} defaults differ: process_job has "
                f"{job_params[name].default!r}, process_compare_job has "
                f"{compare_params[name].default!r}"
            )
            assert compare_params[name].kind == job_params[name].kind, name
            assert compare_params[name].kind is inspect.Parameter.KEYWORD_ONLY, (
                f"{name!r} is not keyword-only, so the forward's order matters"
            )

    def test_the_forward_is_explicit_and_never_star_star(self):
        """The signature test above proves the parameters EXIST; this proves
        each one is actually handed on. They are different failures: a
        parameter can be declared and then quietly not forwarded, which is
        precisely what master did to the three."""
        call = _forward_call()
        assert not any(kw.arg is None for kw in call.keywords), (
            "the forward splats **kwargs — a new process_job parameter would then "
            "ride along invisibly instead of failing the parity test above, which "
            "is the whole point of forwarding explicitly"
        )
        assert not any(isinstance(arg, ast.Starred) for arg in call.args), (
            "the forward splats *args"
        )
        # `comparison` is derived rather than accepted, but it is still handed on
        # BY KEYWORD, so the call's keyword set is the forwardable list plus it.
        # `case` is the other derived one and travels positionally, asserted below.
        assert {kw.arg for kw in call.keywords} == _forwardable() | {"comparison"}, (
            "the forward's keyword arguments are not process_job's parameter list"
        )
        assert len(call.args) == 2, "the forward's positionals moved"
        assert isinstance(call.args[1], ast.Name) and call.args[1].id == "after_case", (
            "the compare lane must report on the AFTER reading"
        )


class TestEveryParameterArrives:
    def test_every_forwardable_parameter_arrives_at_process_job(
        self, tmp_path, bearings, monkeypatch, iso_table, thresholds, rules
    ):
        """The runtime twin of the AST pin: distinct values in, the same
        objects out the other side. `PASSED` is checked against the signature
        FIRST, so adding a parameter to `process_job` forces a value here
        rather than silently skipping it."""
        seen: dict = {}

        def _spy(job, case, **kwargs):
            seen.update(job=job, case=case, kwargs=kwargs)

        monkeypatch.setattr(worker_mod, "process_job", _spy)

        before_case, after_case = _pair(tmp_path, bearings)
        # iso_table/thresholds/rules are consumed by run_analysis BEFORE the
        # forward, so they are the real fixtures and asserted by identity.
        passed = {
            "iso_table": iso_table,
            "thresholds": thresholds,
            "rules": rules,
            "spend_guard": SpendGuard(daily_token_budget=10**9),
            "conversion_note": "SENTINEL conversion note",
            "retain_trace": True,
            "client": None,
            "assembly_notes": ["SENTINEL assembly note"],
            "coverage_note": _COVERAGE,
            "forced_fail_checks": [
                Check(name="cross_channel_speed_agreement", status="fail", reason="SENTINEL")
            ],
            "channel_summary": {"channels": [{"label": "Before"}], "speed_warning": None},
            "drafting_available": False,
        }
        missing = _forwardable() - set(passed)
        assert not missing, (
            f"no value here for {sorted(missing)} — add one so this test keeps "
            "proving the forward carries every parameter"
        )

        job = Job(id="cf", code_label="engineer-1", job_dir=tmp_path / "job")
        process_compare_job(job, before_case=before_case, after_case=after_case, **passed)

        assert seen, "process_compare_job never reached the forward"
        assert seen["job"] is job
        assert seen["case"] is after_case, "the compare lane reported on the wrong reading"
        for name, value in passed.items():
            assert seen["kwargs"][name] is value, f"{name!r} did not arrive at process_job"
        comparison = seen["kwargs"]["comparison"]
        assert isinstance(comparison, ComparisonResult)
        assert comparison.status != "not_comparable", comparison.status


# ── the three, where an analyst can see them ─────────────────────────────


class TestTheThreeDroppedParametersReachTheDocument:
    def test_a_compare_job_with_a_coverage_note_carries_it_in_both_documents(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        """Both documents, per the HIST-2-FIX rule: which one an analyst is
        handed depends on the HOST, so a one-sided assertion is the defect
        returning."""
        before_case, after_case = _pair(tmp_path, bearings)
        job = _run_compare_job(
            tmp_path / "job", before_case, after_case, iso_table, thresholds, rules,
            coverage_note=_COVERAGE,
        )
        assert job.state == "degraded", job.state
        assert documents.markdown, "no markdown document was rendered"
        assert documents.html, "no HTML page was rendered"
        assert _COVERAGE in documents.markdown[0], "report.md lost the coverage note"
        assert _COVERAGE in documents.html[0], "the PDF's page lost the coverage note"
        # ...and as DATA, not only as spliced text: this is the route the
        # no-weasyprint fallback and the v2 page both read.
        assert _COVERAGE in documents.pdf_kwargs[0]["notes"]

    def test_forced_fail_checks_reach_the_fail_closed_render_on_the_compare_path(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        """`_render_fail_closed` was UNREACHABLE from this lane on master: the
        `if forced_fail_checks:` branch could never be true, because nothing
        forwarded the list. It is also the only branch that hands `comparison=`
        into that render, so the delta reaching the insufficient-data document
        is a fact this session creates."""
        before_case, after_case = _pair(tmp_path, bearings)
        job = _run_compare_job(
            tmp_path / "job", before_case, after_case, iso_table, thresholds, rules,
            forced_fail_checks=[
                Check(name="cross_channel_speed_agreement", status="fail",
                      reason="channels disagreed on running speed")
            ],
        )
        assert job.state == "gate_fail", job.state
        # The gate-fail contract: the report names the re-capture that resolves it.
        recapture = worker_mod._FAIL_CLOSED_RECS["cross_channel_speed_agreement"].technique
        for doc, label in ((documents.markdown[0], "markdown"), (documents.html[0], "html")):
            assert recapture in doc, f"{label}: the fail-closed re-capture is missing"
        assert documents.report_kwargs[0]["comparison"] is not None, (
            "the comparison did not reach the fail-closed render"
        )

    def test_channel_summary_is_carried_on_the_compare_path(
        self, tmp_path, bearings, iso_table, thresholds, rules
    ):
        """`job.channel_summary` is set once at the top of `process_job` and
        carried into every terminal state — it was None on every compare job."""
        summary = {"channels": [{"label": "Before", "status": "ok"}], "speed_warning": None}
        before_case, after_case = _pair(tmp_path, bearings)
        job = _run_compare_job(
            tmp_path / "job", before_case, after_case, iso_table, thresholds, rules,
            channel_summary=summary,
        )
        assert job.channel_summary == summary
        assert job.state in ("done", "degraded", "gate_fail"), job.state


class TestTheShippedPathIsUnchanged:
    def test_a_compare_job_that_passes_none_of_them_is_unchanged(
        self, tmp_path, bearings, documents, iso_table, thresholds, rules
    ):
        """The defaults match `process_job`'s exactly, so a caller that passes
        none of the three — which is every caller today, `app.py::_run_compare`
        included — gets what it always got. This one passes on master too, by
        design: it is the no-change guard, not a pin on the fix.
        """
        before_case, after_case = _pair(tmp_path, bearings)
        job = _run_compare_job(
            tmp_path / "job", before_case, after_case, iso_table, thresholds, rules,
        )
        assert job.state == "degraded", job.state
        assert job.channel_summary is None
        assert _COVERAGE not in documents.markdown[0]
