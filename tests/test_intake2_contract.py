"""Session INTAKE-2 — `docs/contracts/machine_result.md` describes the real shape.

Session INTAKEFIX-1 appended `group_source`/`support_source` to three rosters in
this file and added `TestTheProvenanceHalvesAreDistinguishable`. That is a
widening to RE-POINT a pin this session's own authorised wire change invalidated
(INTAKE-2's F-4, item 2 of the brief) — not a licence to rewrite what this file
asserts. Every roster stays EXACT; none became a superset check.

REPORT-3 builds against that file, on another branch, in another session. A
contract nobody diffs against the code is a document that is true on the day it
is written, and this is the test that makes it stay true — the same reason
`MEMORY_FIELDS` is diffed against `CARD_FIELDS` rather than trusted.

Two directions, and the second is the one that matters:

  * every field the contract NAMES exists on the producer;
  * every field the producer PUTS ON THE WIRE is named by the contract. That is
    the half that catches a later session adding a key and forgetting to say so,
    which is how a consumer comes to meet a field nothing documents.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp import locations as L
from vib_agent.webapp.app import create_app
from vib_agent.webapp.jobs import Job

_ROOT = Path(__file__).resolve().parents[1]
_CONTRACT = _ROOT / "docs" / "contracts" / "machine_result.md"
_RPM = 1800.0


def _csv_bytes(n=800, fmax=400.0) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude"])
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for hz, a in ((_RPM / 60.0, 0.4), (107.03, 2.4), (214.06, 1.1)):
        amp[min(range(n), key=lambda i: abs(freqs[i] - hz))] = a
    for freq, value in zip(freqs, amp):
        writer.writerow([freq, value])
    return buf.getvalue().encode()


@pytest.fixture
def client():
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as handle:
        yield handle


@pytest.fixture
def two_location_payload(client):
    """One machine, two points, two different bearings — the contract's own
    example, produced rather than transcribed."""
    res = client.post(
        "/api/jobs",
        files=[("file", ("upload.csv", _csv_bytes(), "text/csv")),
               ("loc2_file", ("upload.csv", _csv_bytes(), "text/csv"))],
        data={"invite_code": "demo-code", "machine_alias": "P-101", "rpm": str(_RPM),
              "iso_group": "2", "iso_support": "rigid", "machine_type": "motor",
              "bearing_model": "6206", "rated_kw": "90",
              "measurement_location": "Motor DE",
              "loc2_label": "Motor NDE", "loc2_direction": "radial_h",
              "loc2_bearing_model": "6309", "loc2_rpm": "1780",
              "loc2_mount_note": "stud, gearbox housing"},
    )
    assert res.status_code == 202, res.text
    body = _poll_until_terminal(client, res.json()["job_id"])
    assert body["state"] in ("done", "degraded"), body
    return body


class TestTheContractExists:

    def test_it_is_where_report_3_will_look(self):
        assert _CONTRACT.is_file(), "docs/contracts/machine_result.md is missing"

    def test_it_names_its_producer_and_its_consumer(self):
        text = _CONTRACT.read_text()
        assert "REPORT-3" in text
        assert "webapp/locations.py" in text or "locations.py" in text


class TestEveryFieldTheContractNamesIsReal:
    """Direction one: the document does not describe fields that do not exist."""

    #: Session INTAKEFIX-1 appended `group_source`/`support_source` — INTAKE-2's
    #: own F-4, and the one wire change its brief authorised. The tuple stays
    #: EXACT rather than becoming a prefix check: the point of an exact roster is
    #: that the next session to widen this contract meets a red test and a
    #: ruling, not a silent pass.
    MACHINE_FIELDS = ("machine_type", "iso_group", "iso_support", "iso_assumed",
                      "group_source", "support_source", "iso_note", "locations")

    @pytest.mark.parametrize("name", MACHINE_FIELDS)
    def test_the_machine_level_field_is_on_the_job(self, name):
        assert hasattr(Job(id="x"), name), f"the contract names Job.{name}, which does not exist"

    @pytest.mark.parametrize("name", MACHINE_FIELDS)
    def test_the_machine_level_field_is_documented(self, name):
        assert f"`{name}`" in _CONTRACT.read_text(), f"{name} is undocumented"

    def test_the_entry_fields_it_names_appear_in_a_real_entry(self, two_location_payload):
        entry = two_location_payload["locations"][0]
        for name in ("label", "status", "channels", "rpm", "bearing",
                     "iso_zone", "severity_rms_mms", "committed_fault", "trend_point"):
            assert name in entry, f"the contract names {name}, absent from a real entry"

    def test_the_trend_point_shape_is_hist1s(self, two_location_payload):
        point = two_location_payload["locations"][0]["trend_point"]
        assert set(point) == {"severity_rms_mms", "iso_zone", "dominant_axis", "captured_at"}


class TestEveryFieldOnTheWireIsDocumented:
    """Direction two — the half that catches the NEXT session.

    A key added to the payload and not to the contract is a field REPORT-3 meets
    with no description. This is the assertion that makes that a red test rather
    than a surprise on another branch.
    """

    def test_no_machine_level_key_is_undocumented(self, two_location_payload):
        text = _CONTRACT.read_text()
        # Only the keys this contract is responsible for: the rest of the payload
        # (state, phase, failure_kind, …) is S7's wire contract, documented in
        # outputs/S7_SPEC.md, and is deliberately not restated here.
        mine = {"machine_type", "iso_group", "iso_support", "iso_assumed",
                "group_source", "support_source", "iso_note", "locations"}
        for key in set(two_location_payload) & mine:
            assert f"`{key}`" in text, f"{key} is on the wire and undocumented"

    def test_no_entry_key_is_undocumented(self, two_location_payload):
        text = _CONTRACT.read_text()
        for entry in two_location_payload["locations"]:
            for key in entry:
                assert f"`{key}`" in text, (
                    f"locations[].{key} is on the wire and undocumented — "
                    "add it to docs/contracts/machine_result.md"
                )

    def test_the_status_vocabulary_is_complete(self):
        """Every value `status` can take is in the document.

        Read from `locations.LOCATION_STATUSES`, the DECLARATION, and not by
        grepping `"status": "..."` out of `app.py`. The first version of this pin
        did exactly that and caught the schema-inference plan's own unrelated
        `pending` — a pin measuring the wrong structure, which is worse than no
        pin because it is counted.
        """
        text = _CONTRACT.read_text()
        assert L.LOCATION_STATUSES, "the status vocabulary is empty"
        for value in L.LOCATION_STATUSES:
            assert f'`"{value}"`' in text or f'`{value}`' in text, (
                f'status "{value}" is in the vocabulary and undocumented'
            )

    def test_every_status_the_producer_emits_is_in_the_vocabulary(self):
        """The other side of that: a status invented at a call site and never
        added to the tuple would be documented by nothing."""
        source = (_ROOT / "src" / "vib_agent" / "webapp" / "app.py").read_text()
        body = source.partition("def _analyse_extra_location(")[2] \
                     .partition("\ndef ")[0]
        assert body, "_analyse_extra_location is gone"
        for value in set(re.findall(r'"status": "([a-z_]+)"', body)):
            assert value in L.LOCATION_STATUSES, (
                f'_analyse_extra_location emits status "{value}", which is not in '
                "locations.LOCATION_STATUSES"
            )


class TestTheProvenanceHalvesAreDistinguishable:
    """Session INTAKEFIX-1, closing INTAKE-2's F-4.

    `iso_assumed` is the `or` of the two sources, so it can say "something was
    assumed" and nothing more — while the report prints *"(group and support
    class assumed)"*, a claim about BOTH halves. These assert the wire can now
    tell them apart, which is the only thing that makes that sentence fixable.
    """

    def test_a_rated_group_and_a_stated_support_are_distinguishable(
        self, two_location_payload
    ):
        assert two_location_payload["group_source"] == "rated", (
            "the group came from rated_kw=90 and must say so"
        )
        assert two_location_payload["support_source"] == "stated", (
            "the support class came from the analyst's own select"
        )
        # Non-vacuity: the two halves really do differ on this payload, so an
        # implementation that reported one value for both would fail here.
        assert two_location_payload["group_source"] != two_location_payload["support_source"]
        assert two_location_payload["iso_assumed"] is False

    def test_both_halves_assumed_is_the_only_case_that_sets_the_flag(self, client):
        """`iso_assumed` stays the ONE thing that gates the word (rule 3)."""
        res = client.post(
            "/api/jobs",
            files=[("file", ("upload.csv", _csv_bytes(), "text/csv"))],
            data={"invite_code": "demo-code", "machine_alias": "P-102", "rpm": str(_RPM),
                  "machine_type": "pump",
                  # NOT blank: `iso_group` is `Form(...)` and FastAPI reads an
                  # empty value for a required str as MISSING, so a blank one is
                  # a 422 and the shipped form cannot reach this state at all.
                  # An unrecognised word is the case `resolve_iso_class`'s
                  # docstring names — "an older client, or a post built by hand".
                  "iso_group": "unspecified", "iso_support": "unspecified",
                  "velocity_unit": "mm_s", "detection_type": "rms",
                  "mode": "spectrum"},
        )
        assert res.status_code == 202, res.text
        body = _poll_until_terminal(client, res.json()["job_id"])
        assert body["state"] in ("done", "degraded"), body
        assert body["group_source"] == "assumed"
        assert body["support_source"] == "assumed"
        assert body["iso_assumed"] is True

    def test_a_blank_group_is_refused_rather_than_assumed(self, client):
        """The measured fact the case above is written around, pinned so it
        cannot change silently: making the selects optional would make
        `assumed` reachable from the form, which is an intake decision with a
        report consequence and wants its own ruling."""
        res = client.post(
            "/api/jobs",
            files=[("file", ("upload.csv", _csv_bytes(), "text/csv"))],
            data={"invite_code": "demo-code", "machine_alias": "P-103", "rpm": str(_RPM),
                  "machine_type": "pump", "iso_group": "", "iso_support": "",
                  "velocity_unit": "mm_s", "detection_type": "rms",
                  "mode": "spectrum"},
        )
        assert res.status_code == 422, res.text


class TestTheContractsClaimsAreTrue:
    """The statements a reader would act on, checked rather than trusted."""

    def test_a_single_location_job_carries_no_locations_key(self, client):
        res = client.post(
            "/api/jobs",
            files={"file": ("upload.csv", _csv_bytes(), "text/csv")},
            data={"invite_code": "demo-code", "machine_alias": "P-101", "rpm": str(_RPM),
                  "iso_group": "2", "iso_support": "rigid", "machine_type": "pump"},
        )
        assert res.status_code == 202, res.text
        body = _poll_until_terminal(client, res.json()["job_id"])
        assert "locations" not in body, (
            "the contract says the key is ABSENT on a one-location job, not empty"
        )

    def test_locations_holds_the_OTHER_points_only(self, two_location_payload):
        """`len(locations) + 1` is the number of points measured -- so the
        document's own subject must not appear in the list."""
        labels = [entry["label"] for entry in two_location_payload["locations"]]
        assert labels == ["Motor NDE"]
        assert "Motor DE" not in labels, "location 1 is repeated in its own list"

    def test_iso_assumed_is_false_when_a_rating_was_given(self, two_location_payload):
        assert two_location_payload["iso_assumed"] is False

    def test_iso_assumed_ships_even_when_false(self, two_location_payload):
        """The contract says so explicitly: a browser that had to read absent as
        false could not tell an old server from a rated machine."""
        assert "iso_assumed" in two_location_payload

    def test_the_per_location_speed_really_differs(self, two_location_payload):
        """The contract warns a consumer that this can differ from the machine
        speed. If it could not, that warning would be noise."""
        assert two_location_payload["locations"][0]["rpm"] == 1780.0
        assert two_location_payload["locations"][0]["rpm"] != _RPM

    def test_the_example_in_the_document_is_the_shape_that_is_produced(self, two_location_payload):
        """The document's JSON block, parsed and compared key-for-key against a
        real run. An example that has drifted is worse than none: it is what a
        consumer codes against."""
        import json
        text = _CONTRACT.read_text()
        block = text.partition('"state": "degraded"')[0].rpartition("```json")[2]
        block = '{\n  "state": "degraded"' + text.partition('"state": "degraded"')[2]
        block = block.partition("```")[0].rstrip()
        example = json.loads(block)
        assert set(example) == set(
            k for k in two_location_payload
            if k in {"state", "machine_type", "iso_group", "iso_support",
                     "iso_assumed", "group_source", "support_source",
                     "locations"}
        ), "the documented example's machine-level keys have drifted"
        assert set(example["locations"][0]) == set(two_location_payload["locations"][0]), (
            "the documented example's entry keys have drifted from a real entry"
        )
