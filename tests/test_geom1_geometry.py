"""Session GEOM-1 — the bearing the catalogue does not hold.

STRANGER B5, the last *blocking* row of the stranger test still standing after
UX-4 and UX-5: *"Eight entries, three of which are benchmark-dataset names with
underscores... Nearly every real machine on my route will be 'Not listed',
which silently turns the headline feature off."*

UX-5 hid the three rig keys and stopped there, deliberately and in writing: the
geometry fallback needed four files it had not been given, and its close-out
names all four with the reason each one blocked the path —

  * `adapters/uploads/common.py::UploadForm` had only `bearing_model: str|None`
  * `bearing_spec_from_form` was a catalogue lookup that RAISES INSIDE THE
    PARSE SANDBOX, so a bearing we hold no geometry for reached the analyst as
    an error about their own file
  * `app.py::_geometry_422` had no bearing branch
  * `POST /api/jobs` had no fields for it

All four are open here, and `pdm_core` is not: `BearingSpec` has always taken
the four numbers and `bearing_rca` has always computed BPFO/BPFI/BSF/FTF from
them. This session is a front door, not a capability.

Four things are pinned, in this order:

  1. **the identity path** — a catalogue bearing produces exactly what it
     produced before, byte for byte in the report;
  2. **the geometry path** — the same physics, and a REPORT LABEL that names
     the geometry as entered rather than a catalogue name it has no right to;
  3. **one sentence per problem**, from `_geometry_422` and nowhere else,
     before a job exists and therefore free;
  4. **the two implementations of the label agree**, because the page shows one
     and the report prints the other.
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import (
    BEARING_GEOMETRY_FIELDS,
    BEARING_GEOMETRY_REQUIRED,
    UploadForm,
    bearing_geometry_label,
    bearing_geometry_stated,
    bearing_spec_from_form,
)
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import render_markdown
from vib_agent.webapp.app import _geometry_422, create_app

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"
_RPM = 1800.0
_SHAFT_HZ = _RPM / 60.0

#: The 6206's own numbers, so the two paths can be compared on ONE bearing:
#: same geometry, one named from the catalogue and one typed by the analyst.
_6206 = {"bearing_n_balls": 9, "bearing_ball_dia_mm": 9.53, "bearing_pitch_dia_mm": 46.0}
#: The BPFO those numbers put at 1800 rpm, which is the peak the example file
#: carries — so a committed diagnosis proves the geometry actually reached the
#: detector rather than merely surviving validation.
_BPFO_HZ = 107.03


def _csv(path, peaks, *, n=400, fmax=200.0):
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for pk_hz, pk_amp in peaks:
        idx = min(range(n), key=lambda i: abs(freqs[i] - pk_hz))
        amp[idx] = pk_amp
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["freq_hz", "amplitude"])
        for freq, value in zip(freqs, amp):
            writer.writerow([freq, value])
    return path


def _bpfo_csv(path):
    """A spectrum a 6206 at 1800 rpm commits an outer-race fault on."""
    return _csv(path, [(_SHAFT_HZ, 0.4), (_BPFO_HZ, 2.4), (2 * _BPFO_HZ, 1.1)])


def _form(**over):
    # `machine_type` is REQUIRED on the wire since Session INTAKE-2 (PARTC F-2).
    # Declared once, here, beside the other fields every post in this file needs.
    data = {"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(_RPM),
            "iso_group": "2", "iso_support": "rigid", "machine_type": "pump"}
    data.update({k: str(v) for k, v in over.items()})
    return data


def _post(client, path, **over):
    with open(path, "rb") as handle:
        return client.post("/api/jobs", files={"file": (path.name, handle, "text/csv")},
                           data=_form(**over))


def _app(fake=None):
    return create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                      anthropic_client_factory=(lambda: fake) if fake else None)


@pytest.fixture(scope="module")
def cfg():
    return {"iso_table": load_config("iso_zones")["zones"],
            "thresholds": load_thresholds("route"),
            "rules": load_config("next_measurements"),
            "bearings": load_config("bearings")}


def _report(path, cfg, **form_kwargs):
    """One upload, all the way to rendered markdown, the way the product does."""
    form = UploadForm(machine_alias="Route Pump", rpm=_RPM, iso_group="2",
                      iso_support="rigid", **form_kwargs)
    case = parse_upload(path, form, bearings_cfg=cfg["bearings"])[0]
    result = run_analysis(case, iso_table=cfg["iso_table"],
                          thresholds=cfg["thresholds"], rules=cfg["rules"])
    return case, result, render_markdown(result, case.machine, case=case,
                                         thresholds=cfg["thresholds"])


# ══════════════════════════════════════════════════════════════════════════
# 1 · The identity path — a catalogue bearing is untouched
# ══════════════════════════════════════════════════════════════════════════
class TestTheCatalogueBearingIsUnmoved:
    """The whole session is additive or it is a regression. A route analyst who
    picks 6206 today must get the same bytes they got yesterday."""

    def test_the_spec_is_still_the_config_entry_verbatim(self):
        bearings = load_config("bearings")
        spec = bearing_spec_from_form(
            UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid",
                       bearing_model="6206"),
            bearings,
        )
        entry = bearings["bearings"]["6206"]
        assert spec.n_balls == entry["n_balls"]
        assert spec.ball_dia_mm == entry["ball_dia_mm"]
        assert spec.pitch_dia_mm == entry["pitch_dia_mm"]
        assert spec.contact_angle_deg == entry["contact_angle_deg"]
        # The MODEL is the catalogue key and nothing else. If this ever became
        # a descriptive label, every report that names a bearing would change.
        assert spec.model == "6206"

    def test_no_bearing_at_all_is_still_None(self):
        assert bearing_spec_from_form(
            UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid"),
            load_config("bearings"),
        ) is None

    def test_the_four_new_fields_default_to_absent(self):
        form = UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid")
        for name in BEARING_GEOMETRY_FIELDS:
            assert getattr(form, name) is None, name
        assert bearing_geometry_stated(form) is False

    def test_the_report_is_byte_identical_with_the_new_fields_present_but_blank(
        self, tmp_path, cfg
    ):
        """The wire sends "" for an untouched number input and FastAPI reads
        that as None — so the SHIPPED default state has all four fields present
        and empty. That state must render the same document as one where they
        do not exist at all."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, _, before = _report(path, cfg, bearing_model="6206")
        _, _, after = _report(path, cfg, bearing_model="6206",
                              **{name: None for name in BEARING_GEOMETRY_FIELDS})
        assert before == after
        assert "| Bearing | 6206 |" in before, before[:400]
        assert "geometry as entered" not in before


# ══════════════════════════════════════════════════════════════════════════
# 2 · The geometry path — same physics, honest provenance
# ══════════════════════════════════════════════════════════════════════════
class TestGeometryReachesTheDetector:

    def test_it_builds_the_same_bearing_the_catalogue_would(self):
        bearings = load_config("bearings")
        named = bearing_spec_from_form(
            UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid",
                       bearing_model="6206"), bearings)
        typed = bearing_spec_from_form(
            UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid",
                       **_6206), bearings)
        for field in ("n_balls", "ball_dia_mm", "pitch_dia_mm", "contact_angle_deg"):
            assert getattr(named, field) == getattr(typed, field), field
        assert named.model != typed.model, "the provenance must differ; the physics must not"

    def test_a_blank_contact_angle_is_the_deep_groove_zero(self):
        """The one field that may be left out. It is not a guess: 0° is what
        every entry in config/bearings.json that omits it is recorded under,
        and it is what BearingSpec already defaults to."""
        spec = bearing_spec_from_form(
            UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid",
                       **_6206), load_config("bearings"))
        assert spec.contact_angle_deg == 0.0
        assert "contact 0°" in spec.model

    def test_a_contact_angle_is_carried_when_given(self):
        spec = bearing_spec_from_form(
            UploadForm(machine_alias="M", rpm=_RPM, iso_group="2", iso_support="rigid",
                       bearing_contact_angle_deg=15.0, **_6206), load_config("bearings"))
        assert spec.contact_angle_deg == 15.0
        assert "contact 15°" in spec.model

    def test_the_same_fault_is_committed_from_typed_geometry(self, tmp_path, cfg):
        """The point of the whole session: a bearing we hold no geometry for is
        screened exactly like one we do."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, named, _ = _report(path, cfg, bearing_model="6206")
        _, typed, _ = _report(path, cfg, **_6206)
        committed = sorted(f.fault for f in named.findings)
        assert "bearing_outer_race" in committed, committed
        assert sorted(f.fault for f in typed.findings) == committed
        assert named.rca.bearing_freqs == typed.rca.bearing_freqs

    def test_the_two_paths_differ_ONLY_in_how_the_bearing_is_named(self, tmp_path, cfg):
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, _, named = _report(path, cfg, bearing_model="6206")
        _, _, typed = _report(path, cfg, **_6206)
        label = bearing_geometry_label(9, 9.53, 46.0, 0.0)
        assert named != typed
        assert typed.replace(label, "6206") == named, (
            "the geometry path changed something other than the bearing's name"
        )


class TestTheReportNamesTheGeometryAsEntered:
    """`report/` is read-only this session, so `BearingSpec.model` is the ONLY
    channel the report has for saying where these numbers came from. Four
    render sites print it: the Machine Details row (`generate.py`), the drafted
    report's duty strip (`_v2.html.j2`), the fault-frequency map header
    (`charts.py`) and the markdown survey table (`default_survey.md.j2`)."""

    def test_the_bearing_row_names_the_provenance_and_the_numbers(self, tmp_path, cfg):
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, _, markdown = _report(path, cfg, **_6206)
        row = [line for line in markdown.splitlines() if line.startswith("| Bearing |")]
        assert row, markdown[:600]
        assert row[0] == (
            "| Bearing | geometry as entered — 9 elements, element 9.53 mm, "
            "pitch 46 mm, contact 0° |"
        ), row[0]

    def test_it_is_never_a_catalogue_name(self, tmp_path, cfg):
        """The failure this exists to prevent: printing "6206" for numbers that
        happen to match it would claim we hold geometry we do not, and would
        make the report unfalsifiable against the analyst's own datasheet."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, _, markdown = _report(path, cfg, **_6206)
        row = [ln for ln in markdown.splitlines() if ln.startswith("| Bearing |")][0]
        for key in load_config("bearings")["bearings"]:
            assert key not in row, f"the report named {key} for hand-entered geometry"

    def test_the_row_never_says_None(self, tmp_path, cfg):
        """`default_survey.md.j2` renders the bearing through Jinja's
        `default()` filter, which fires only for an UNDEFINED value — a `None`
        prints the word "None" into the report. Measured, and the reason
        `bearing_geometry_label` returns a string rather than leaving
        `BearingSpec.model` at its default."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, _, markdown = _report(path, cfg, **_6206)
        assert "| Bearing | None" not in markdown

    def test_the_location_still_travels_with_it(self, tmp_path, cfg):
        """GEOM-A's rule: a bearing number with no location is a machine-wide
        claim. It has to survive a bearing that is a sentence rather than a
        number."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        _, _, markdown = _report(path, cfg, measurement_location="Motor DE", **_6206)
        row = [ln for ln in markdown.splitlines() if ln.startswith("| Bearing |")][0]
        assert row.endswith("(at Motor DE) |"), row


# ══════════════════════════════════════════════════════════════════════════
# 3 · One sentence per problem, before a job exists
# ══════════════════════════════════════════════════════════════════════════
_KNOWN = ("6205", "6206", "SKF_6205")


def _problem(**over):
    form = {"rpm": _RPM}
    form.update(over)
    return _geometry_422(form, _KNOWN)


class TestEveryRefusalIsFreeAndNamesTheField:
    """`_geometry_422` stays the single source of these messages — the brief's
    words. The twins in `bearing_spec_from_form` are a backstop for anything
    reaching an adapter without passing the form boundary (the CLI), and they
    raise where a raise becomes a PARSE_ERROR, which is exactly why the
    analyst-facing copy cannot live there."""

    @pytest.mark.parametrize("over,fragment", [
        ({"bearing_model": "6206", "bearing_n_balls": 9}, "not both"),
        ({"bearing_model": "SKF 32222 J2"}, "not a bearing we hold geometry for"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 7.94}, "pitch diameter is missing"),
        ({"bearing_n_balls": 9}, "ball / roller diameter and pitch diameter are missing"),
        ({"bearing_contact_angle_deg": 15.0}, "a contact angle on its own is not a bearing"),
        ({"bearing_n_balls": 0, "bearing_ball_dia_mm": 7.94,
          "bearing_pitch_dia_mm": 39.04}, "positive whole number"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 0,
          "bearing_pitch_dia_mm": 39.04}, "positive number of millimetres"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 7.94,
          "bearing_pitch_dia_mm": -1}, "positive number of millimetres"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 39.04,
          "bearing_pitch_dia_mm": 39.04}, "would not fit"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 7.94,
          "bearing_pitch_dia_mm": 39.04, "bearing_contact_angle_deg": 120}, "0 and 90"),
    ])
    def test_each_problem_has_its_own_sentence(self, over, fragment):
        message = _problem(**over)
        assert message is not None, over
        assert fragment in message, message

    @pytest.mark.parametrize("over", [
        {},
        {"bearing_model": "6206"},
        {"bearing_model": ""},
        dict(_6206),
        dict(_6206, bearing_contact_angle_deg=15.0),
        dict(_6206, bearing_contact_angle_deg=0),
        dict(_6206, bearing_model=""),
    ])
    def test_an_acceptable_bearing_is_not_refused(self, over):
        assert _problem(**over) is None, over

    def test_each_message_is_one_sentence(self):
        """The brief: "validation errors are one sentence each". An em dash
        joins a clause; a full stop starts a second thing to read."""
        seen = [
            _problem(bearing_model="6206", bearing_n_balls=9),
            _problem(bearing_model="nope"),
            _problem(bearing_n_balls=9),
            _problem(bearing_contact_angle_deg=15.0),
            _problem(bearing_n_balls=0, bearing_ball_dia_mm=7.94, bearing_pitch_dia_mm=39.04),
            _problem(bearing_n_balls=9, bearing_ball_dia_mm=39.04, bearing_pitch_dia_mm=39.04),
        ]
        for message in seen:
            assert message and "." not in message.rstrip("."), message

    def test_the_catalogue_check_is_skipped_when_no_catalogue_is_supplied(self):
        """`known_bearings` defaults to empty and means "do not check" — a
        caller with no catalogue to hand must not refuse every bearing."""
        assert _geometry_422({"rpm": _RPM, "bearing_model": "6206"}) is None
        assert _geometry_422({"rpm": _RPM, "bearing_model": "anything"}) is None


class TestTheWire:

    def test_geometry_is_accepted(self, tmp_path):
        path = _bpfo_csv(tmp_path / "upload.csv")
        with TestClient(_app(FakeAnthropicClient(responses=[]))) as client:
            response = _post(client, path, **_6206)
        assert response.status_code == 202, response.text

    def test_the_shipped_blank_state_is_accepted(self, tmp_path):
        """Every control empty is what an untouched form posts."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        blank = {name: "" for name in BEARING_GEOMETRY_FIELDS}
        with TestClient(_app(FakeAnthropicClient(responses=[]))) as client:
            response = _post(client, path, bearing_model="", **blank)
        assert response.status_code == 202, response.text

    @pytest.mark.parametrize("over,fragment", [
        ({"bearing_model": "6206", "bearing_n_balls": 9}, "not both"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 7.94}, "pitch diameter is missing"),
        ({"bearing_n_balls": 9, "bearing_ball_dia_mm": 39.04,
          "bearing_pitch_dia_mm": 39.04}, "would not fit"),
    ])
    def test_a_bad_bearing_is_a_422_and_costs_nothing(self, tmp_path, over, fragment):
        path = _bpfo_csv(tmp_path / "upload.csv")
        app = _app()
        with TestClient(app) as client:
            response = _post(client, path, **over)
        assert response.status_code == 422, response.text
        assert fragment in response.json()["detail"], response.text
        assert app.state.vib.pending_job_count() == 0
        assert not app.state.vib.registry.all_jobs()

    def test_an_unknown_bearing_is_a_422_and_no_longer_a_claim_about_the_file(self, tmp_path):
        """UX-5's STOP named this as one of the four things blocking the path:
        `bearing_spec_from_form` raises INSIDE the parse sandbox, so a bearing
        we hold no geometry for came back to the analyst as `upload_unreadable`
        — an accusation about a file that was fine."""
        path = _bpfo_csv(tmp_path / "upload.csv")
        app = _app()
        with TestClient(app) as client:
            response = _post(client, path, bearing_model="SKF 32222 J2")
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert "not a bearing we hold geometry for" in detail, detail
        assert "file" not in detail.lower(), detail
        assert not app.state.vib.registry.all_jobs()


# ══════════════════════════════════════════════════════════════════════════
# 4 · The two implementations of the label agree
# ══════════════════════════════════════════════════════════════════════════
class TestThePageAndTheReportSpellTheBearingTheSameWay:
    """`app.js::bearingGeometryLabel` shows the analyst what the run will be
    filed under; `common.py::bearing_geometry_label` is what the report prints.
    Two spellings of one bearing is an analyst checking two strings against one
    datasheet, so the two are diffed rather than assumed."""

    #: Realistic geometries, including the shapes that make a formatter
    #: disagree with itself: an integral float, a trailing zero, a zero angle,
    #: a single element, and a three-digit pitch.
    CASES = [
        (9, 7.94, 39.04, 0.0),
        (9, 9.53, 46.0, 0.0),
        (8, 17.46, 72.5, 0.0),
        (19, 24.0, 141.0, 10.0),
        (18, 17.0, 110.0, 0.0),
        (1, 5.969, 31.623, 0.0),
        (12, 0.5, 100.0, 90.0),
    ]

    @pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
    def test_the_javascript_twin_produces_the_same_string(self, tmp_path):
        script = tmp_path / "label.js"
        app_js = (_STATIC / "app.js").read_text()
        # Slice the two pure functions out of the shipped file rather than
        # re-implementing them: a copy that drifted would pass.
        num = re.search(r"function geomNum\([\s\S]*?\n}\n", app_js)
        label = re.search(r"function bearingGeometryLabel\([\s\S]*?\n}\n", app_js)
        assert num and label, "the label functions are gone from app.js"
        script.write_text(
            num.group(0) + label.group(0)
            + "const cases = JSON.parse(process.argv[2]);\n"
            + "console.log(JSON.stringify(cases.map("
            + "(c) => bearingGeometryLabel(c[0], c[1], c[2], c[3]))));\n"
        )
        out = subprocess.run(
            [shutil.which("node"), str(script), json.dumps(self.CASES)],
            capture_output=True, text=True, timeout=60,
        )
        assert out.returncode == 0, f"{out.stdout}\n{out.stderr}"
        from_js = json.loads(out.stdout)
        from_py = [bearing_geometry_label(*case) for case in self.CASES]
        assert from_js == from_py

    def test_the_python_label_is_what_the_tests_above_assert(self):
        assert bearing_geometry_label(9, 9.53, 46.0, 0.0) == (
            "geometry as entered — 9 elements, element 9.53 mm, pitch 46 mm, contact 0°"
        )

    def test_the_required_three_are_the_three_the_formulas_read(self):
        assert BEARING_GEOMETRY_REQUIRED == (
            "bearing_n_balls", "bearing_ball_dia_mm", "bearing_pitch_dia_mm")
        assert BEARING_GEOMETRY_FIELDS[3] == "bearing_contact_angle_deg"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_browser_half_in_a_real_js_runtime():
    """`tests/js/bearing_geometry_tests.js` drives the SHIPPED app.js in node.

    It cannot be a Python test: whether a revealed block is actually shown,
    whether a hidden input still holds a value it would post, and whether the
    confirmation's undo puts both halves back are all things a TestClient post
    cannot be asked."""
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "bearing_geometry_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = map(int, match.groups())
    assert passed == total and total >= 20, result.stdout
