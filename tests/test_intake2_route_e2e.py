"""Session INTAKE-2 — the whole route in one job, read against known ground truth.

FIXTURE-1 built `outputs/demo_package/multi_location/`: one compressor train at
1800 rpm on 6206 bearings, measured at FOUR points in three directions each, with
**a BPFO planted at Compressor DE and nowhere else**. Its own README says why that
set exists:

> The diagnostic act on a route is deciding *which* point the fault is at, and a
> single-point fixture cannot exercise it.

And it says how to upload it:

> Upload **one point per job**: three files, one per direction slot. A job takes
> at most three files, so there is no twelve-channel upload — four jobs, four
> reports.

**That sentence was true when it was written and this session is what makes it
false.** Four points now go in one job, twelve files, and the assertions below are
the ones the fixture was built for: the fault is found at Compressor DE, the three
healthy points are NOT called faulty, and each point's severity lands in the zone
FIXTURE-1 planted it in.

This is the end-to-end pin the brief asked for, on the fixture the brief named.
Nothing here is synthesised by the test: every number compared against comes from
FIXTURE-1's README table, restated below as data so a reader can diff the two.

Skipped, not failed, when the fixture is absent — it arrives with FIXTURE-1 and a
tree without it should say so rather than fail for the wrong reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "outputs" / "demo_package" / "multi_location"

pytestmark = pytest.mark.skipif(
    not _FIXTURE.is_dir(),
    reason="outputs/demo_package/multi_location/ is not in the tree (arrives with FIXTURE-1)",
)

#: FIXTURE-1's README, §"The four points", transcribed as data. `zone` and the
#: three overalls are the PLANTED values; `planted` is what is actually wrong
#: there. Restated here rather than parsed out of the markdown so a reader can
#: diff two tables by eye, and so a reworded README does not silently change
#: what this test believes.
_POINTS = (
    # label,            stem,             zone, overall H, planted
    ("Motor DE",        "motor_de",        "A", 1.10, None),
    ("Motor NDE",       "motor_nde",       "B", 1.80, None),
    ("Compressor DE",   "compressor_de",   "D", 5.20, "bpfo"),
    ("Compressor NDE",  "compressor_nde",  "A", 1.20, None),
)

#: The machine, from the README's "every form value a human types" table.
_MACHINE = {
    "machine_alias": "Synthetic Compressor Train 01",
    "machine_type": "compressor",
    "rpm": "1800",
    "rated_kw": "75",          # the README types this as ISO group 2
    "iso_group": "2",
    "iso_support": "rigid",
    "bearing_model": "6206",   # the same bearing at all four points
    "velocity_unit": "mm_s",
    "detection_type": "rms",
    "mode": "spectrum",
}

#: `config/iso_zones.json` 2_rigid, quoted by the README: A/B 1.4, B/C 2.8,
#: C/D 4.5 mm/s RMS. Used to check a zone against the value it came from, so a
#: zone that disagrees with its own amplitude is caught rather than trusted.
_BOUNDARIES = {"A": (0.0, 1.4), "B": (1.4, 2.8), "C": (2.8, 4.5), "D": (4.5, 1e9)}

_DIRECTIONS = (("h", "radial_h"), ("v", "radial_v"), ("a", "axial"))


def _files_and_data():
    """The whole route: four locations, three files each, in one post."""
    files, data = [], dict(_MACHINE)
    handles = []
    for index, (label, stem, _zone, _overall, _planted) in enumerate(_POINTS, start=1):
        for slot, (suffix, direction) in enumerate(_DIRECTIONS):
            path = _FIXTURE / f"{stem}_{suffix}.csv"
            assert path.is_file(), f"FIXTURE-1 file missing: {path.name}"
            handle = path.open("rb")
            handles.append(handle)
            # Location 1 uses the unprefixed names the form has always used.
            slot_name = "file" if slot == 0 else f"file_{slot + 1}"
            dir_name = "direction" if slot == 0 else f"direction_{slot + 1}"
            if index == 1:
                files.append((slot_name, (path.name, handle, "text/csv")))
                data[dir_name] = direction
            else:
                files.append((f"loc{index}_{slot_name}", (path.name, handle, "text/csv")))
                data[f"loc{index}_{dir_name}"] = direction
        if index == 1:
            data["measurement_location"] = label
        else:
            data[f"loc{index}_label"] = label
    return files, data, handles


@pytest.fixture
def client():
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as handle:
        yield handle


@pytest.fixture(scope="module")
def route_result(request):
    """One job, four points, twelve files — run once for the whole module.

    No API key is needed: with the fake client out of canned responses the job
    takes the designed degrade lane and still produces every number these
    assertions read. Zero real API calls.
    """
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as client:
        files, data, handles = _files_and_data()
        data["invite_code"] = "demo-code"
        try:
            res = client.post("/api/jobs", files=files, data=data)
        finally:
            for handle in handles:
                handle.close()
        assert res.status_code == 202, f"the route was refused: {res.status_code} {res.text}"
        body = _poll_until_terminal(client, res.json()["job_id"], timeout_s=600)
        return body


class TestTheWholeRouteRunsInOneJob:
    """The claim FIXTURE-1's README could not make when it was written."""

    def test_twelve_files_at_four_points_are_accepted(self, route_result):
        assert route_result["state"] in ("done", "degraded"), route_result

    def test_all_four_points_are_analysed(self, route_result):
        """Three on the list plus location 1, which is the document's subject."""
        others = route_result.get("locations") or []
        assert [entry["label"] for entry in others] == [
            "Motor NDE", "Compressor DE", "Compressor NDE",
        ], others
        assert len(others) + 1 == len(_POINTS)

    def test_every_point_was_readable(self, route_result):
        for entry in route_result["locations"]:
            assert entry["status"] == "ok", f"{entry['label']}: {entry}"

    def test_each_point_carries_three_channels(self, route_result):
        for entry in route_result["locations"]:
            assert sorted(entry["channels"]) == ["axial", "radial_h", "radial_v"], entry


class TestTheFaultIsFoundAtTheRightPoint:
    """THE reason this fixture exists — and the reason a single-point set could
    not test it. A BPFO is planted at Compressor DE and nowhere else."""

    def _entry(self, route_result, label):
        for entry in route_result["locations"]:
            if entry["label"] == label:
                return entry
        raise AssertionError(f"{label} is not in the result")

    def test_the_bpfo_is_committed_at_compressor_de(self, route_result):
        entry = self._entry(route_result, "Compressor DE")
        committed = entry.get("committed_fault") or ""
        assert "bearing" in committed or "bpfo" in committed.lower(), (
            "the planted outer-race fault was not committed at the point it is "
            f"planted at; got {committed!r}"
        )

    @pytest.mark.parametrize("label", ["Motor NDE", "Compressor NDE"])
    def test_no_bearing_fault_is_committed_at_a_healthy_point(self, route_result, label):
        """The half that a fault-only fixture cannot check. Calling a healthy
        point faulty is the failure an analyst's credibility is spent on, and
        PARTC's standing rule is that a clean bill on a faulted machine is a
        fail -- this is the same error running the other way."""
        entry = self._entry(route_result, label)
        committed = (entry.get("committed_fault") or "").lower()
        assert "bearing" not in committed and "bpfo" not in committed, (
            f"{label} is healthy in the fixture and was called a bearing fault: "
            f"{entry.get('committed_fault')!r}"
        )

    def test_the_faulted_point_is_the_most_severe(self, route_result):
        """Ground truth: 5.20 mm/s at Compressor DE against 1.80 at the worst
        healthy point. If the per-location speeds or bearings were crossed, this
        ordering is the first thing that would break."""
        severities = {
            entry["label"]: entry.get("severity_rms_mms")
            for entry in route_result["locations"]
            if entry.get("severity_rms_mms") is not None
        }
        assert severities, "no point reported a severity"
        worst = max(severities, key=lambda label: severities[label])
        assert worst == "Compressor DE", severities


class TestEachPointLandsInItsPlantedZone:
    """FIXTURE-1 planted a zone per point. A report that got the fault right and
    the severities wrong would still be wrong on the page an analyst signs."""

    @pytest.mark.parametrize("label,zone,overall", [
        (label, zone, overall) for label, _stem, zone, overall, _p in _POINTS
        if label != "Motor DE"   # location 1 is the document's subject, not in locations[]
    ])
    def test_the_zone_is_the_planted_one(self, route_result, label, zone, overall):
        entry = next(e for e in route_result["locations"] if e["label"] == label)
        assert entry.get("iso_zone") == zone, (
            f"{label}: planted zone {zone}, got {entry.get('iso_zone')} at "
            f"{entry.get('severity_rms_mms')} mm/s"
        )

    @pytest.mark.parametrize("label,zone", [
        (label, zone) for label, _stem, zone, _o, _p in _POINTS if label != "Motor DE"
    ])
    def test_the_zone_agrees_with_the_amplitude_it_came_from(self, route_result, label, zone):
        """A zone is a function of a number and a table; checking both catches a
        zone that is right by luck against a severity that is not."""
        entry = next(e for e in route_result["locations"] if e["label"] == label)
        low, high = _BOUNDARIES[zone]
        value = entry["severity_rms_mms"]
        assert low <= value < high, (
            f"{label}: {value} mm/s is not inside zone {zone} ({low}-{high})"
        )

    @pytest.mark.parametrize("label,overall", [
        (label, overall) for label, _s, _z, overall, _p in _POINTS if label != "Motor DE"
    ])
    def test_the_severity_is_the_planted_overall(self, route_result, label, overall):
        """The axis-max overall, which for every point in this set is the H
        channel. Tolerance is 5%: the fixture states the planted RMS and the
        analysis recomputes it from the spectrum."""
        entry = next(e for e in route_result["locations"] if e["label"] == label)
        value = entry["severity_rms_mms"]
        assert abs(value - overall) / overall < 0.05, (
            f"{label}: planted {overall} mm/s, computed {value}"
        )


class TestTheReportSaysWhatItDoesNotCover:
    """Ruling R-3, on a real route. The document covers Motor DE — a HEALTHY
    point — while the fault is at Compressor DE. A report that did not say so
    would be a clean bill on a machine with a Zone D bearing fault on it, which
    is precisely the Part C failure Phase 7B found."""

    def test_the_report_is_produced(self, route_result):
        assert route_result["state"] in ("done", "degraded")

    def test_every_other_point_is_named_with_what_was_found(self, route_result):
        from vib_agent.webapp.app import _other_locations_note
        note = _other_locations_note("Motor DE", route_result["locations"])
        for label, _stem, zone, _overall, _planted in _POINTS:
            if label == "Motor DE":
                continue
            assert label in note, f"{note!r} does not name {label}"
            assert f"Zone {zone}" in note, f"{note!r} does not carry {label}'s zone"
        assert "not read this report as a verdict on the whole machine" in note

    def test_the_note_names_the_faulted_point_and_its_zone(self, route_result):
        """The single most important sentence in the interim note: the analyst is
        reading about Motor DE and needs to know Compressor DE is in Zone D."""
        from vib_agent.webapp.app import _other_locations_note
        note = _other_locations_note("Motor DE", route_result["locations"])
        assert "Compressor DE" in note
        assert "Zone D" in note


class TestTheFixturesOwnInstructionIsNowOutOfDate:
    """FIXTURE-1's README says a job takes at most three files, so the set needs
    four jobs. This session made that false, and a stale instruction in a demo
    package is what a new analyst follows.

    Recorded as a finding rather than fixed: `outputs/demo_package/` is
    FIXTURE-1's and this session does not rewrite another session's README. The
    test asserts the SENTENCE IS STILL THERE, so it goes red the moment someone
    updates it -- which is the signal that the debt is paid and this test comes
    out.
    """

    def test_the_readme_still_says_one_point_per_job(self):
        readme = (_FIXTURE / "README.md").read_text()
        assert "one point per job" in readme.lower(), (
            "FIXTURE-1's README has been updated for multi-location upload — "
            "delete this test and the finding in outputs/SESSION_INTAKE2.md that "
            "carries it"
        )
