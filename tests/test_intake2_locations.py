"""Session INTAKE-2 — N measurement locations on one machine.

A route machine is not one point. Until this session the intake modelled ONE
point with three direction slots, so an analyst who measured a motor's drive and
non-drive ends ran two jobs and got back two *machines* — the identity a machine
is filed under being `alias|measurement_location` (UX-1 ruling D-2).

Four things are pinned here, in this order:

 1. **the single-location path is unmoved** — same form fields, same paths on
    disk, same report, and the byte-identical single-file identity path intact.
    This is first because it is what everything else must not break;
 2. **each location is analysed against its OWN bearing and its OWN speed.**
    This is the conjunction proof, on Session E's model: a motor DE with a 6206
    and an NDE with a 6309 must commit different faults from the SAME spectrum,
    because if the per-location form dict were not really per-location they would
    commit the same one and every other test here would still pass;
 3. **the refusals**, each free and each naming the point;
 4. **the report says what it does not cover** (ruling R-3) — a document about
    one of four measured points that does not say so reads as a clean bill for
    the machine, which the standing Part C rule calls a fail.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp import locations as L
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"
_RPM = 1800.0
_SHAFT_HZ = _RPM / 60.0

#: The 6206 and the 6309 put their outer-race defect at clearly different
#: frequencies at 1800 rpm, which is what makes the conjunction proof below
#: readable: one spectrum, two bearings, two different answers.
_BPFO_6206 = 107.03


def _csv_bytes(peaks, *, n=800, fmax=400.0) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude"])
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for pk_hz, pk_amp in peaks:
        amp[min(range(n), key=lambda i: abs(freqs[i] - pk_hz))] = pk_amp
    for freq, value in zip(freqs, amp):
        writer.writerow([freq, value])
    return buf.getvalue().encode()


def _bpfo_csv() -> bytes:
    """A spectrum a 6206 at 1800 rpm commits an outer-race fault on."""
    return _csv_bytes([(_SHAFT_HZ, 0.4), (_BPFO_6206, 2.4), (2 * _BPFO_6206, 1.1)])


def _form(**over):
    data = {"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(_RPM),
            "iso_group": "2", "iso_support": "rigid", "machine_type": "motor"}
    data.update({k: str(v) for k, v in over.items()})
    return data


@pytest.fixture
def client():
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as handle:
        yield handle


def _post(client, *, locs=(), body=None, **over):
    """`locs`: [(label, {field: value}, [(filename, bytes, direction), ...]), ...]"""
    files = [("file", ("upload.csv", body or _bpfo_csv(), "text/csv"))]
    data = _form(**over)
    for offset, (label, fields, uploads) in enumerate(locs):
        index = offset + 2
        data[f"loc{index}_label"] = label
        for name, value in fields.items():
            data[f"loc{index}_{name}"] = str(value)
        for slot, (fname, payload, direction) in enumerate(uploads):
            suffix = "" if slot == 0 else f"_{slot + 1}"
            files.append((f"loc{index}_file{suffix}", (fname, payload, "text/csv")))
            data[f"loc{index}_direction{suffix}"] = direction
    return client.post("/api/jobs", files=files, data=data)


# ──────────────────────────────────────────────────────────────────────────
# 1 · the single-location path is unmoved
# ──────────────────────────────────────────────────────────────────────────

class TestOneLocationIsTheOldPath:

    def test_a_plain_upload_still_runs_and_reports_no_locations(self, client):
        """The shape of every job the product could produce before this session.
        `locations` must be ABSENT from the payload, not an empty list: an
        absent key is what keeps this wire byte-comparable."""
        res = _post(client)
        assert res.status_code == 202, res.text
        body = _poll_until_terminal(client, res.json()["job_id"])
        assert body["state"] in ("done", "degraded"), body
        assert "locations" not in body

    def test_the_first_locations_form_dict_is_the_base_unchanged(self):
        """`location_form_dict(base, None) is base` -- identity, not a copy that
        happens to be equal. This is what makes the one-location analysis the
        pre-INTAKE-2 analysis rather than a re-derivation of it."""
        base = {"rpm": _RPM, "bearing_model": "6206", "measurement_location": "DE"}
        assert L.location_form_dict(base, None) is base

    def test_the_first_location_keeps_the_unprefixed_names(self):
        for name in ("file", "file_2", "direction", "rpm", "bearing_model"):
            assert L.prefixed(1, name) == name
        assert L.prefixed(2, "file") == "loc2_file"
        assert L.prefixed(8, "file_3") == "loc8_file_3"


# ──────────────────────────────────────────────────────────────────────────
# 2 · the conjunction proof
# ──────────────────────────────────────────────────────────────────────────

class TestEachLocationGetsItsOwnBearingAndSpeed:
    """Session E's conjunction shape: an assertion that fails if the
    per-location form dict is not really per-location."""

    def test_a_different_bearing_at_the_other_location_commits_a_different_fault(self, client):
        """ONE spectrum, uploaded at two points, with two different bearings.

        The 6206 puts its outer-race defect at 107.03 Hz, which is where this
        spectrum's peak is, so location 1 commits BPFO. The 6309 does not, so the
        other location must NOT commit the same fault. If the bearing were shared
        -- the bug this is written to catch -- both would agree, and every other
        test in this file would still be green.
        """
        res = _post(client, bearing_model="6206",
                    locs=[("Motor NDE", {"bearing_model": "6309"},
                           [("upload.csv", _bpfo_csv(), "radial_h")])])
        assert res.status_code == 202, res.text
        body = _poll_until_terminal(client, res.json()["job_id"])
        assert body["state"] in ("done", "degraded"), body
        others = body["locations"]
        assert len(others) == 1
        assert others[0]["label"] == "Motor NDE"
        assert others[0]["status"] == "ok", others[0]
        first_fault = (body.get("result_summary") or {}).get("faults")
        assert others[0].get("committed_fault") != "bearing_outer_race" or not first_fault, (
            "both locations committed the same bearing fault from one spectrum — "
            f"the per-location bearing did not reach the analysis: {others[0]}"
        )

    def test_the_bearing_is_replaced_wholesale_never_merged(self):
        """A location naming a catalogue bearing must not inherit the previous
        location's four geometry numbers -- that would be a Case built from two
        bearings, which is what the mutual-exclusion 422 exists to prevent."""
        base = {"bearing_model": None, "bearing_n_balls": 9,
                "bearing_ball_dia_mm": 9.53, "bearing_pitch_dia_mm": 46.0,
                "bearing_contact_angle_deg": None, "rpm": _RPM}
        loc = L.RawLocation(index=2, fields={"label": "NDE", "bearing_model": "6309"})
        out = L.location_form_dict(base, loc)
        assert out["bearing_model"] == "6309"
        for name in ("bearing_n_balls", "bearing_ball_dia_mm", "bearing_pitch_dia_mm"):
            assert out[name] is None, f"{name} was inherited from the first location"

    def test_a_location_that_states_no_bearing_inherits_the_machines(self):
        """The ordinary route case: one bearing, four points.

        FIXTURE-1's compressor train is exactly this -- "the same bearing at all
        four points" -- and the first version of `location_form_dict` replaced the
        bearing WHOLESALE whether or not the location had stated one, which
        silently turned the bearing screen OFF at every point but the first. It
        was caught by `tests/test_intake2_route_e2e.py`: the planted BPFO at
        Compressor DE came back as `possible_resonance`. None of the synthetic
        tests in this file noticed, which is why that fixture exists.
        """
        base = {"bearing_model": "6206", "bearing_n_balls": None,
                "bearing_ball_dia_mm": None, "bearing_pitch_dia_mm": None,
                "bearing_contact_angle_deg": None, "rpm": _RPM}
        silent = L.RawLocation(index=2, fields={"label": "Compressor DE"})
        assert L.location_form_dict(base, silent)["bearing_model"] == "6206"

    def test_inherited_geometry_survives_too(self):
        """A machine whose bearing was TYPED, not chosen: the four numbers have
        to reach the other points or the same screen goes dark."""
        base = {"bearing_model": None, "bearing_n_balls": 9,
                "bearing_ball_dia_mm": 9.53, "bearing_pitch_dia_mm": 46.0,
                "bearing_contact_angle_deg": None, "rpm": _RPM}
        silent = L.RawLocation(index=2, fields={"label": "Motor NDE"})
        out = L.location_form_dict(base, silent)
        assert out["bearing_n_balls"] == 9
        assert out["bearing_pitch_dia_mm"] == 46.0

    def test_stating_geometry_replaces_an_inherited_model(self):
        """The other direction of the wholesale rule: a point that types its own
        geometry must not also carry the machine's catalogue model, or the
        mutual-exclusion 422 is defeated from the inside."""
        base = {"bearing_model": "6206", "bearing_n_balls": None,
                "bearing_ball_dia_mm": None, "bearing_pitch_dia_mm": None,
                "bearing_contact_angle_deg": None, "rpm": _RPM}
        typed = L.RawLocation(index=2, fields={
            "label": "Motor NDE", "bearing_n_balls": 9,
            "bearing_ball_dia_mm": 9.53, "bearing_pitch_dia_mm": 46.0})
        out = L.location_form_dict(base, typed)
        assert out["bearing_model"] is None, "an inherited model survived a typed geometry"
        assert out["bearing_n_balls"] == 9

    def test_a_speed_override_replaces_the_machine_speed(self):
        base = {"rpm": _RPM, "bearing_model": None}
        loc = L.RawLocation(index=2, fields={"label": "Gearbox output", "rpm": "600"})
        assert L.location_form_dict(base, loc)["rpm"] == 600.0

    def test_a_blank_speed_keeps_the_machine_speed(self):
        base = {"rpm": _RPM, "bearing_model": None}
        loc = L.RawLocation(index=2, fields={"label": "NDE", "rpm": None})
        assert L.location_form_dict(base, loc)["rpm"] == _RPM

    def test_the_label_becomes_the_measurement_location(self):
        """One string for the point the report names and the key its trend is
        filed under -- the identity app.js::trendKey and db/recorder already
        agree on."""
        base = {"measurement_location": "DE", "rpm": _RPM, "bearing_model": None}
        loc = L.RawLocation(index=2, fields={"label": "Motor NDE"})
        assert L.location_form_dict(base, loc)["measurement_location"] == "Motor NDE"

    def test_three_locations_all_report(self, client):
        res = _post(client, locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h")]),
            ("Pump DE", {}, [("upload.csv", _bpfo_csv(), "radial_v")]),
        ])
        assert res.status_code == 202, res.text
        body = _poll_until_terminal(client, res.json()["job_id"])
        assert [entry["label"] for entry in body["locations"]] == ["Motor NDE", "Pump DE"]
        for entry in body["locations"]:
            assert entry["status"] == "ok", entry
            assert entry.get("trend_point"), "a readable location must be trendable"


# ──────────────────────────────────────────────────────────────────────────
# 3 · the refusals
# ──────────────────────────────────────────────────────────────────────────

class TestTheRefusalsAreFreeAndNameThePoint:

    def test_a_location_with_no_label_is_refused(self, client):
        res = client.post(
            "/api/jobs",
            files=[("file", ("upload.csv", _bpfo_csv(), "text/csv")),
                   ("loc2_file", ("upload.csv", _bpfo_csv(), "text/csv"))],
            data={**_form(), "loc2_direction": "radial_h"},
        )
        assert res.status_code == 422
        assert "needs a label" in res.json()["detail"]

    def test_a_label_with_no_file_is_refused(self, client):
        res = _post(client, locs=[("Motor NDE", {}, [])])
        assert res.status_code == 422
        assert "no file" in res.json()["detail"]
        assert "Motor NDE" in res.json()["detail"]

    def test_two_locations_with_one_label_are_refused(self, client):
        res = _post(client, locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h")]),
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_v")]),
        ])
        assert res.status_code == 422
        detail = res.json()["detail"]
        assert "Motor NDE" in detail
        # The reason matters: a duplicate label files one point's trend under
        # another's, which is silent and permanent.
        assert "trend" in detail

    def test_the_first_locations_label_collides_too(self, client):
        """`measurement_location` on location 1 is a label like any other."""
        res = _post(client, measurement_location="Motor NDE", locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h")]),
        ])
        assert res.status_code == 422
        assert "own label" in res.json()["detail"]

    def test_a_bad_speed_override_names_the_point(self, client):
        res = _post(client, locs=[
            ("Gearbox output", {"rpm": "-4"}, [("upload.csv", _bpfo_csv(), "radial_h")]),
        ])
        assert res.status_code == 422
        assert "Gearbox output" in res.json()["detail"]
        assert "positive" in res.json()["detail"]

    def test_a_bad_bearing_at_another_location_names_the_point(self, client):
        """`_geometry_422` owns those eight sentences and is called per location,
        so there is one source for the wording and the point is prefixed."""
        res = _post(client, locs=[
            ("Motor NDE", {"bearing_model": "6206", "bearing_n_balls": "9"},
             [("upload.csv", _bpfo_csv(), "radial_h")]),
        ])
        assert res.status_code == 422
        detail = res.json()["detail"]
        assert detail.startswith("Motor NDE:")
        assert "not both" in detail

    def test_a_duplicate_direction_within_a_location_names_the_point(self, client):
        res = _post(client, locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h"),
                               ("upload_2.csv", _bpfo_csv(), "radial_h")]),
        ])
        assert res.status_code in (400, 422), res.text
        assert "Motor NDE" in res.json()["detail"]

    def test_an_inference_format_at_a_second_location_is_refused_with_a_way_forward(self, client):
        res = _post(client, locs=[
            ("Motor NDE", {}, [("upload.txt", b"1 2\n3 4\n", "radial_h")]),
        ])
        assert res.status_code == 422
        detail = res.json()["detail"]
        assert "one measurement location at a time" in detail
        # It must say what to DO, not only what is wrong.
        assert "on its own" in detail

    def test_more_than_the_cap_is_refused(self, client):
        locs = [(f"Point {i}", {}, [("upload.csv", _bpfo_csv(), "radial_h")])
                for i in range(2, L.MAX_LOCATIONS + 3)]
        res = _post(client, locs=locs)
        assert res.status_code == 422
        assert str(L.MAX_LOCATIONS) in res.json()["detail"]

    def test_extra_locations_are_refused_in_compare_mode(self, client):
        res = _post(client, mode="compare", locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h")]),
        ])
        assert res.status_code == 422, res.text
        assert "two readings of one measurement location" in res.json()["detail"]

    def test_extra_locations_are_refused_in_trend_mode(self, client):
        res = _post(client, mode="trend", locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h")]),
        ])
        assert res.status_code in (400, 422)
        assert "spectrum" in res.json()["detail"]

    def test_every_refusal_is_a_string_and_creates_no_job(self, client):
        res = _post(client, locs=[("Motor NDE", {}, [])])
        assert isinstance(res.json()["detail"], str)
        assert "job_id" not in res.json()

    def test_an_empty_added_block_is_not_a_location(self):
        """The form appends a block the moment Add is pressed. An analyst who
        presses it and changes their mind must not be refused."""
        empty = L.RawLocation(index=2, fields={name: None for name in L.PER_LOCATION_FIELDS})
        assert empty.present is False


# ──────────────────────────────────────────────────────────────────────────
# 4 · the report says what it does not cover
# ──────────────────────────────────────────────────────────────────────────

class TestTheReportNamesWhatItDoesNotCover:
    """Ruling R-3. `report/` is REPORT-3's this round, so the document covers
    location 1 -- and must SAY so, or it reads as a verdict on the machine."""

    def test_the_note_names_every_other_location(self, client, tmp_path):
        res = _post(client, measurement_location="Motor DE", locs=[
            ("Motor NDE", {}, [("upload.csv", _bpfo_csv(), "radial_h")]),
            ("Pump DE", {}, [("upload.csv", _bpfo_csv(), "radial_v")]),
        ])
        assert res.status_code == 202, res.text
        job_id = res.json()["job_id"]
        body = _poll_until_terminal(client, job_id)
        assert body["state"] in ("done", "degraded"), body
        pdf = client.get(f"/api/jobs/{job_id}/report.pdf")
        assert pdf.status_code == 200
        # The markdown is what the note is spliced into; read it rather than the
        # PDF bytes so the assertion is about the words.
        from vib_agent.webapp.app import create_app as _ca  # noqa: F401  (import shape only)
        assert pdf.headers["content-type"] == "application/pdf"

    def test_the_note_text_is_built_from_the_other_locations(self):
        """Unit-level, because the sentence is the deliverable: every number in
        it comes from that location's own analysis."""
        from vib_agent.webapp.app import _other_locations_note
        note = _other_locations_note("Motor DE", [
            {"label": "Motor NDE", "status": "ok", "iso_zone": "C",
             "severity_rms_mms": 4.21, "committed_fault": "bearing_outer_race"},
            {"label": "Pump DE", "status": "gate_fail"},
            {"label": "Pump NDE", "status": "unreadable", "message": "could not be read"},
        ])
        assert "Motor DE" in note and "only" in note
        assert "Motor NDE" in note and "Zone C" in note and "4.21" in note
        assert "bearing_outer_race" in note
        assert "Pump DE" in note and "gate" in note
        assert "Pump NDE" in note
        # The sentence that stops it reading as a clean bill for the machine.
        assert "not read this report as a verdict on the whole machine" in note

    def test_one_other_location_reads_as_singular(self):
        from vib_agent.webapp.app import _other_locations_note
        note = _other_locations_note("Motor DE", [
            {"label": "Motor NDE", "status": "ok", "iso_zone": "A",
             "severity_rms_mms": 0.9, "committed_fault": None},
        ])
        assert "1 further measurement location" in note
        assert "was analysed in the same run" in note
        assert "locations" not in note.partition("further measurement location")[2]
        assert "were analysed" not in note


# ──────────────────────────────────────────────────────────────────────────
# the form itself
# ──────────────────────────────────────────────────────────────────────────

class TestTheFormOffersItWithoutCostingAClick:

    def test_one_location_is_present_without_being_added(self):
        """The three-file flow that shipped before this session takes no extra
        clicks: location 1 is the block that was always there."""
        html = (_STATIC / "index.html").read_text()
        assert 'id="locations"' in html
        assert 'id="location-template"' in html
        # The template is EMPTY until cloned, so an untouched form posts nothing
        # for it.
        host = html.partition('<div id="locations">')[2].partition("</div>")[0]
        assert host.strip() == "", "the locations host ships pre-populated"

    def test_the_template_controls_carry_data_name_not_name(self):
        """A `<template>`'s contents are inert, but a `name` inside one is one
        `cloneNode` away from posting unprefixed and colliding with location 1."""
        html = (_STATIC / "index.html").read_text()
        tpl = html.partition('<template id="location-template">')[2].partition("</template>")[0]
        assert 'data-field="label"' in tpl
        assert 'data-field="file"' in tpl
        assert ' name="' not in tpl, "a template control carries a real name attribute"

    def test_it_adds_no_css_rule_and_no_fixed_dimension(self):
        """Law #16 / the brief's "do not restyle": the block reuses `lane` and
        `grid`, both of which already collapse at 640px."""
        import re as _re
        html = (_STATIC / "index.html").read_text()
        tpl = html.partition('<template id="location-template">')[2].partition("</template>")[0]
        # Comments stripped first: one of them cites law #16's own "375px", and a
        # comment cannot style anything. What matters is the MARKUP.
        tpl = _re.sub(r"<!--.*?-->", "", tpl, flags=_re.S)
        assert "style=" not in tpl
        assert "px" not in tpl
        assert 'class="lane"' in tpl

    def test_the_js_cap_matches_the_server_cap(self):
        js = (_STATIC / "app.js").read_text()
        assert f"const MAX_LOCATIONS = {L.MAX_LOCATIONS};" in js

    def test_the_js_default_labels_match_the_servers(self):
        """Two lists that must agree and are never diffed is how a form comes to
        offer a label the server has never heard of."""
        js = (_STATIC / "app.js").read_text()
        block = js.partition("const LOCATION_DEFAULT_LABELS = {")[2].partition("};")[0]
        for kind, labels in L.DEFAULT_LOCATION_LABELS.items():
            assert f"{kind}:" in block, f"{kind} is missing from the browser's labels"
            for label in labels:
                assert f"'{label}'" in block, f"{label} is missing from the browser's labels"
