"""Session INTAKEFIX-1 — one bad point must not cost an analyst the other three.

THE DEFECT THIS FILE EXISTS FOR, found by item 1's own gate pin and fixed in the
same commit. `app.py::_analyse_extra_location` built a `gate_fail` entry with

    entry["gate_reasons"] = [c.detail for c in gate.checks if c.status == "fail"]

and `Check` has no `detail` -- it has `reason`, which is what
`worker._gate_summary` and `assembly.py:483` both read. The line sits OUTSIDE
the try/except that wraps the merge and the analysis, so the `AttributeError`
reached `app._run_guarded` and the WHOLE JOB became `internal_error`: a
four-point route where one point had no sample rate returned *"The analysis
failed on our side"* and threw away three good analyses.

It shipped in INTAKE-2 and survived REPORTFIX-1 because **no test in the tree
had ever failed an EXTRA location's quality gate**. Every multi-location fixture
uploads points that pass. That is the same shape as INTAKE-2's own section 6.2
lesson -- 33 synthetic tests passed against a broken bearing inheritance because
each of them stated a bearing at every point.

The claim is deliberately made at the WIRE and not at the function: what an
analyst is entitled to is the other locations' results and a sentence about the
one that failed, and that is a property of the payload.
"""

from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp.app import create_app

_RPM = 1800.0


def _csv_bytes(n: int = 800, fmax: float = 400.0) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude"])
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for hz, value in ((_RPM / 60.0, 0.4), (107.03, 2.4), (214.06, 1.1)):
        amp[min(range(n), key=lambda i: abs(freqs[i] - hz))] = value
    for freq, value in zip(freqs, amp):
        writer.writerow([freq, value])
    return buf.getvalue().encode()


def _flat_csv_bytes(n: int = 8, fmax: float = 4.0) -> bytes:
    """Eight bins of nothing — fails the data-quality gate, which is the point."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude"])
    for i in range(n):
        writer.writerow([i * fmax / n, 0.0])
    return buf.getvalue().encode()


@pytest.fixture
def client():
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as handle:
        yield handle


@pytest.fixture
def route_with_one_failed_gate(client):
    """Three readable points and a fourth whose gate fails, in one job."""
    res = client.post(
        "/api/jobs",
        files=[("file", ("upload.csv", _csv_bytes(), "text/csv")),
               ("loc2_file", ("upload.csv", _csv_bytes(), "text/csv")),
               ("loc3_file", ("upload.csv", _csv_bytes(), "text/csv")),
               ("loc4_file", ("upload.csv", _flat_csv_bytes(), "text/csv"))],
        data={"invite_code": "demo-code", "machine_alias": "Compressor train 01",
              "rpm": str(_RPM), "iso_group": "2", "iso_support": "rigid",
              "machine_type": "compressor", "measurement_location": "Motor DE",
              "loc2_label": "Motor NDE", "loc2_direction": "radial_h",
              "loc3_label": "Compressor DE", "loc3_direction": "radial_h",
              "loc4_label": "Compressor NDE", "loc4_direction": "radial_h"},
    )
    assert res.status_code == 202, res.text
    return _poll_until_terminal(client, res.json()["job_id"])


class TestAFailedGateAtOnePointIsRecordedNotRaised:

    def test_the_job_still_reaches_a_diagnosing_state(self, route_with_one_failed_gate):
        """`internal_error` here is the bug. `error` is reserved for a job with
        nothing readable in it at all, which this is not."""
        assert route_with_one_failed_gate["state"] in ("done", "degraded"), (
            route_with_one_failed_gate
        )
        assert "failure_kind" not in route_with_one_failed_gate

    def test_the_failed_point_says_why_in_words_an_analyst_can_act_on(
        self, route_with_one_failed_gate
    ):
        last = route_with_one_failed_gate["locations"][-1]
        assert last["label"] == "Compressor NDE"
        # Non-vacuity: this fixture must actually fail a gate, or every
        # assertion in this file is about a route with four good points.
        assert last["status"] == "gate_fail", last
        assert last["gate_reasons"], "a gate_fail entry with no reason says nothing"
        assert all(isinstance(r, str) and r.strip() for r in last["gate_reasons"])
        # `Check.reason` is `str | None`; a None in this list would render as
        # the word "None" on a page an analyst reads.
        assert None not in last["gate_reasons"]

    def test_the_other_three_points_survive_it(self, route_with_one_failed_gate):
        """The whole of it: a bad fourth point costs the analyst nothing but the
        fourth point."""
        assert "trend_point" in route_with_one_failed_gate, "location 1 was analysed"
        others = route_with_one_failed_gate["locations"]
        assert [e["label"] for e in others] == [
            "Motor NDE", "Compressor DE", "Compressor NDE"
        ]
        good = [e for e in others if e["status"] == "ok"]
        assert len(good) == 2
        assert all(e.get("trend_point") for e in good)

    def test_a_gate_failed_point_carries_no_numbers(self, route_with_one_failed_gate):
        """Contract section 5 rule 1. No diagnosis was made, so a zone or a
        severity here would be a verdict nothing produced."""
        last = route_with_one_failed_gate["locations"][-1]
        for key in ("iso_zone", "severity_rms_mms", "trend_point", "committed_fault"):
            assert not last.get(key), f"{key} is present on a gate_fail entry"


class TestTheAttributeItReadsIsTheOneThatExists:
    """The narrow structural claim, so a future rename is caught without a job.

    `Check` is a Pydantic model: a mistyped attribute is an `AttributeError` at
    RUNTIME and nothing earlier sees it, which is exactly how `.detail` shipped.
    """

    def test_check_has_reason_and_not_detail(self):
        from vib_agent.models import Check

        fields = set(Check.model_fields)
        assert "reason" in fields
        assert "detail" not in fields

    def test_no_webapp_module_reads_detail_off_a_gate_check(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "webapp"
        for path in sorted(root.rglob("*.py")):
            body = path.read_text()
            assert "c.detail for c in" not in body, (
                f"{path.name} reads `.detail` off a Check; the field is `reason`"
            )
