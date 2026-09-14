"""Session GEOM-A — the form, the browser card, and the promises that move with
them.

The upload form is where geometry stops being a model field and starts changing
what an analyst gets. Four things are pinned:

  * every geometry field is ON the page, is remembered by the machine card, and
    is nothing the card should not hold (no invite code, no file, no consent);
  * a typo is a FREE 422 before a job exists — never a PARSE_ERROR from inside
    the sandbox, which would file an analyst's mistake as a claim about their
    file (the `_acquisition_422` precedent, and the reason it exists);
  * a blade-pass case uploaded through the live endpoint COMMITS, where the
    same file with the count left blank cannot;
  * privacy.html and the retention ledger name what the card now holds and what
    now travels to the model — `agent/loop.py` puts `machine.model_dump_json()`
    in the drafting prompt, so every MachineMeta field reaches the API by
    construction, and the page that says otherwise would be wrong.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient, build_consistent_echo, draft_message
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm, belt_frequency_hz
from vib_agent.agent.consistency import TITLE_TEMPLATE
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.webapp import assembly as A
from vib_agent.webapp.app import create_app

_RPM = 1800.0
_SHAFT_HZ = _RPM / 60.0
_STATIC = Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "webapp" / "static"

#: Every geometry field the form posts. The single source this file checks
#: everything else against.
GEOMETRY_FIELDS = (
    "measurement_location", "coupling", "blades", "drive_type", "poles", "line_freq_hz",
    "rotor_bars", "gear_teeth_driving", "gear_teeth_driven",
    "drive_pulley_mm", "driven_pulley_mm", "pulley_center_distance_mm",
)


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


def _app(fake=None):
    return create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                      anthropic_client_factory=(lambda: fake) if fake else None)


def _form(**over):
    data = {"invite_code": "demo-code", "machine_alias": "TestPump", "rpm": str(_RPM),
            "iso_group": "2", "iso_support": "rigid", "machine_type": "motor"}
    data.update({k: str(v) for k, v in over.items()})
    return data


def _post(client, path, **over):
    with open(path, "rb") as handle:
        return client.post("/api/jobs", files={"file": (path.name, handle, "text/csv")},
                           data=_form(**over))


def _index() -> str:
    with TestClient(_app()) as client:
        return client.get("/").text


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _privacy() -> str:
    return (_STATIC / "privacy.html").read_text()


def _prose(text: str) -> str:
    """Collapse whitespace: these are assertions about what a reader sees, and
    where a source line happens to wrap is not that."""
    return re.sub(r"\s+", " ", text)


# ══════════════════════════════════════════════════════════════════════════
# 1 · The fields are on the page and in the card
# ══════════════════════════════════════════════════════════════════════════
class TestTheFormCarriesTheGeometry:
    def test_every_geometry_field_is_a_form_field(self):
        html = _index()
        for field in GEOMETRY_FIELDS:
            assert f'name="{field}"' in html, f"form field {field} missing from index.html"

    def test_the_vocabularies_are_offered_as_options(self):
        html = _index()
        for value in ("coupled", "uncoupled", "direct_on_line", "vfd", "soft_starter"):
            assert f'value="{value}"' in html, f"missing option {value}"

    def test_coupling_and_drive_type_default_to_not_stated(self):
        """A pre-selected "coupled" would put an answer in the analyst's mouth,
        and the report would print it as declared."""
        html = _index()
        assert '<option value="" selected>Not stated</option>' in html
        # Session UX-5 (C11) made `Not stated` the one word for a blank field,
        # so the two acquisition selects that said "Not provided" now say it
        # too and the count is four. What GEOM-A's pin is about is that THESE
        # two default to it -- silence is a meaningful answer the report prints
        # for coupling and drive type -- so it is asserted by name.
        for field in ("coupling", "drive_type"):
            block = html.partition(f'name="{field}"')[2].partition("</select>")[0]
            assert '<option value="" selected>Not stated</option>' in block, field
        assert html.count('<option value="" selected>Not stated</option>') == 4

    def test_the_card_remembers_every_geometry_field(self):
        fields = _app_js().partition("const MEMORY_FIELDS = [")[2].partition("];")[0]
        for field in GEOMETRY_FIELDS:
            assert f"'{field}'" in fields, f"{field} is collected but never remembered"

    def test_the_card_still_holds_no_credential_or_consent(self):
        fields = _app_js().partition("const MEMORY_FIELDS = [")[2].partition("];")[0]
        for banned in ("invite_code", "code", "retain_trace", "file", "remember"):
            assert f"'{banned}'" not in fields, f"{banned} must never be stored in the browser"

    def test_the_selects_carry_exactly_the_vocabularies_app_py_validates(self):
        """The INTAKE-HONEST precedent: a drifted <option> becomes an
        analyst-facing 422 for choosing something the page offered."""
        from vib_agent.webapp.app import _COUPLING_STATES, _DRIVE_TYPES

        html = _index()
        coupling_block = html.partition('name="coupling"')[2].partition("</select>")[0]
        for value in _COUPLING_STATES:
            assert f'value="{value}"' in coupling_block
        drive_block = html.partition('name="drive_type"')[2].partition("</select>")[0]
        for value in _DRIVE_TYPES:
            assert f'value="{value}"' in drive_block

    def test_geometry_lives_under_more_options_not_the_main_form(self):
        html = _index()
        head, _, rest = html.partition('<details class="more" id="more-options">')
        more = rest.partition("</details>")[0]
        for field in GEOMETRY_FIELDS:
            assert f'name="{field}"' in more, f"{field} should be under More options"
            assert f'name="{field}"' not in head, f"{field} is in the main form"

    def test_the_sandbox_subprocess_round_trip_carries_the_geometry(self, tmp_path):
        """The real child process, the real argv/JSON boundary. A new UploadForm
        field that never crossed it would vanish silently on the product path
        while every in-process test stayed green."""
        from vib_agent.config import CONFIG_DIR
        from vib_agent.webapp.parsing import parse_in_subprocess

        path = _csv(tmp_path / "upload.csv", [(_SHAFT_HZ, 0.5)])
        form_dict = {
            "machine_alias": "Pump", "rpm": _RPM, "iso_group": "2", "iso_support": "rigid", "machine_type": "motor",
            "bearing_model": "6206", "velocity_unit": "mm_s", "detection_type": "rms",
            "mode": "spectrum", "wav_sensitivity": None,
            "measurement_location": "Motor DE", "coupling": "uncoupled", "blades": 6,
            "gear_teeth_driving": 23, "gear_teeth_driven": 91, "rotor_bars": 44,
            "poles": 4, "line_freq_hz": 60.0, "drive_type": "vfd",
            "drive_pulley_mm": 150.0, "driven_pulley_mm": 300.0,
            "pulley_center_distance_mm": 600.0,
        }
        case_dict, kind, _note = parse_in_subprocess(
            path, form_dict,
            bearings_cfg_path=CONFIG_DIR / "bearings.json",
            cwru_cfg_path=CONFIG_DIR / "cwru.json",
            mfpt_cfg_path=CONFIG_DIR / "mfpt.json",
            wt_cfg_path=CONFIG_DIR / "wind_turbine.json",
            mafaulda_cfg_path=CONFIG_DIR / "mafaulda.json",
            out_path=tmp_path / "out.json",
            timeout_s=60.0, memory_mb=512,
        )
        assert kind == "tabular_spectrum"
        machine = case_dict["machine"]
        assert machine["coupled"] is False and machine["coupled_stated"] is True
        assert machine["blades"] == 6 and machine["poles"] == 4
        assert machine["gear_teeth_driving"] == 23 and machine["rotor_bars"] == 44
        assert machine["drive_type"] == "vfd" and machine["location"] == "Motor DE"
        assert machine["belt"]["freq_hz"] == pytest.approx(
            belt_frequency_hz(150.0, 300.0, 600.0, _RPM), rel=1e-9)

    def test_every_remembered_field_actually_exists_on_the_form(self):
        """The other direction: a card that restores a field the page does not
        have silently drops what the analyst typed."""
        html = _index()
        fields = _app_js().partition("const MEMORY_FIELDS = [")[2].partition("];")[0]
        for name in re.findall(r"'([a-z_0-9]+)'", fields):
            if name == "direction":
                continue  # slot-1 direction; its name is on the measurements block
            assert f'name="{name}"' in html, f"remembered field {name!r} is not on the form"


# ══════════════════════════════════════════════════════════════════════════
# 2 · A typo is free
# ══════════════════════════════════════════════════════════════════════════
class TestGeometryIsValidatedAtTheBoundary:
    @pytest.mark.parametrize("over,fragment", [
        ({"poles": 3}, "even"),
        ({"poles": 0}, "even"),
        ({"blades": 0}, "positive whole number"),
        ({"rotor_bars": -2}, "positive whole number"),
        ({"gear_teeth_driving": 0}, "positive whole number"),
        ({"line_freq_hz": 0}, "positive number"),
        ({"coupling": "welded"}, "coupling must be one of"),
        ({"drive_type": "diesel"}, "drive_type must be one of"),
        ({"measurement_location": "x" * 61}, "60 characters"),
        ({"drive_pulley_mm": 150}, "none of them"),
        ({"drive_pulley_mm": 150, "driven_pulley_mm": 300}, "none of them"),
        ({"drive_pulley_mm": 300, "driven_pulley_mm": 300,
          "pulley_center_distance_mm": 250}, "overlap"),
        ({"drive_pulley_mm": 0, "driven_pulley_mm": 300,
          "pulley_center_distance_mm": 600}, "positive numbers"),
    ])
    def test_bad_geometry_is_a_422_and_costs_nothing(self, tmp_path, over, fragment):
        path = _csv(tmp_path / "upload.csv", [(_SHAFT_HZ, 0.5)])
        app = _app()
        with TestClient(app) as client:
            response = _post(client, path, **over)
        assert response.status_code == 422, response.text
        assert fragment in response.json()["detail"], response.text
        # No job, so nothing was charged and nothing was parsed.
        assert app.state.vib.pending_job_count() == 0
        assert not app.state.vib.registry.all_jobs()

    def test_a_valid_geometry_post_is_accepted(self, tmp_path):
        path = _csv(tmp_path / "upload.csv", [(_SHAFT_HZ, 0.5)])
        with TestClient(_app(FakeAnthropicClient(responses=[]))) as client:
            response = _post(client, path, poles=4, blades=6, coupling="uncoupled",
                             drive_type="vfd", drive_pulley_mm=150, driven_pulley_mm=300,
                             pulley_center_distance_mm=600)
        assert response.status_code == 202, response.text

    def test_an_empty_geometry_post_is_unchanged(self, tmp_path):
        """Blank number inputs post "" and blank selects post "". Neither may
        become a value, and neither may 422."""
        path = _csv(tmp_path / "upload.csv", [(_SHAFT_HZ, 0.5)])
        blank = {field: "" for field in GEOMETRY_FIELDS}
        with TestClient(_app(FakeAnthropicClient(responses=[]))) as client:
            response = _post(client, path, **blank)
        assert response.status_code == 202, response.text


# ══════════════════════════════════════════════════════════════════════════
# 3 · Reachable from the form, end to end
# ══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def cfg():
    return {"iso_table": load_config("iso_zones")["zones"],
            "thresholds": load_thresholds("route"),
            "rules": load_config("next_measurements"),
            "bearings": load_config("bearings")}


def _echo(paths_dirs, form_over, cfg, alias="TestPump"):
    """A fake-LLM draft consistent with what the pipeline will actually commit
    for THIS upload — geometry included, or the consistency check correctly
    hard-fails on the fault the geometry unlocked."""
    parsed = []
    for path, direction in paths_dirs:
        form = UploadForm(machine_alias=alias, rpm=_RPM, iso_group="2", iso_support="rigid", machine_type="motor",
                          **form_over)
        case, kind, note = parse_upload(path, form, bearings_cfg=cfg["bearings"])
        # A single file with no declared direction is the assembly's IDENTITY
        # path: radial-h, `assumed=True`. Passing None here is not what the
        # webapp does and the label lookup has no entry for it.
        parsed.append(A.ParsedChannel(direction or "radial_h", direction is None,
                                      case, kind, note))
    outcome = A.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                               thresholds=cfg["thresholds"], rules=cfg["rules"])
    result = run_analysis(outcome.case, iso_table=cfg["iso_table"],
                          thresholds=cfg["thresholds"], rules=cfg["rules"])
    narrative = (f"{TITLE_TEMPLATE.format(machine_name=alias)}\n\nBody.\n\n"
                 "DRAFT -- prepared by automated analysis, pending analyst review.")
    return draft_message(narrative, build_consistent_echo(result)), result, outcome.case


def _document(result, case, cfg) -> str:
    """The report document for an analysis, as text.

    A completed job leaves ONLY `report.pdf` (worker.py::_keep_only_report), and
    a weasyprint PDF's text sits in compressed streams — so an e2e test cannot
    read the delivered document without a PDF extractor, and making the suite
    depend on one installed binary is how a green suite starts describing a
    different product than the one that ships. So the document is rendered here
    from the SAME result and case the job analysed, through
    `render_markdown` — the function `render_report` itself calls, with the same
    inputs. What THIS proves is that the geometry reaches the document. That the
    delivered PDF is that document is the Part C human read, and the render is
    pinned deterministically in tests/test_geometry_report.py.
    """
    from vib_agent.report.generate import render_markdown

    return render_markdown(result, case.machine, case=case,
                           thresholds=cfg["thresholds"], profile="route")


class TestBladePassFromTheLiveForm:
    """The acceptance §S6 names: "a blades-bearing case commits blade-pass where
    yesterday's form could not". Same file, same endpoint, one field."""

    BLADES = 6
    BPF_HZ = BLADES * _SHAFT_HZ  # 180 Hz

    def _upload(self, tmp_path, cfg, *, blades):
        path = _csv(tmp_path / "upload.csv",
                    [(self.BPF_HZ, 3.5), (81.3, 0.8), (_SHAFT_HZ, 0.4)])
        over = {"blades": blades} if blades is not None else {}
        message, result, merged = _echo([(path, None)], over, cfg)
        app = _app(FakeAnthropicClient(responses=[message]))
        with TestClient(app) as client:
            response = _post(client, path, **over)
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            data = _poll_until_terminal(client, job_id)
            assert data["state"] == "done", data
            assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 200
            return _document(result, merged, cfg), result

    def test_without_the_count_the_report_cannot_name_blade_pass(self, tmp_path, cfg):
        _md, result = self._upload(tmp_path, cfg, blades=None)
        assert "elevated_blade_pass" not in {f.fault for f in result.findings}

    def test_with_the_count_the_report_commits_blade_pass(self, tmp_path, cfg):
        md, result = self._upload(tmp_path, cfg, blades=self.BLADES)
        assert "elevated_blade_pass" in {f.fault for f in result.findings}
        assert "blade pass" in md.lower(), md[:400]
        # and the geometry that made it possible is printed
        assert "Blade / vane count" in md and "6 (declared)" in md

    def test_the_roster_stops_calling_it_not_assessed(self, tmp_path, cfg):
        md, _result = self._upload(tmp_path, cfg, blades=self.BLADES)
        assert "**Vane / blade pass** — not assessed" not in md


class TestUncoupledFromTheLiveForm:
    """`coupled=False` routes the bent-shaft branch, driven through the real
    multi-file endpoint — the wiring proof that reaches pdm_core unchanged."""

    def _upload(self, tmp_path, cfg, *, coupling):
        scale = 10.0
        paths = []
        for name, direction, amp in (("h.csv", "radial_h", 0.15),
                                     ("v.csv", "radial_v", 0.14),
                                     ("a.csv", "axial", 0.60)):
            paths.append((_csv(tmp_path / name, [(_SHAFT_HZ, amp * scale)]), direction))
        over = {"coupling": coupling} if coupling else {}
        message, result, merged = _echo(paths, over, cfg)
        app = _app(FakeAnthropicClient(responses=[message]))
        handles = []
        try:
            files, data = {}, _form(**over)
            for i, (path, direction) in enumerate(paths):
                handle = open(path, "rb")
                handles.append(handle)
                files[["file", "file_2", "file_3"][i]] = (path.name, handle, "text/csv")
                data[["direction", "direction_2", "direction_3"][i]] = direction
            with TestClient(app) as client:
                response = client.post("/api/jobs", files=files, data=data)
                assert response.status_code == 202, response.text
                job_id = response.json()["job_id"]
                assert _poll_until_terminal(client, job_id)["state"] == "done"
                assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 200
                return _document(result, merged, cfg), result
        finally:
            for handle in handles:
                handle.close()

    def test_unstated_coupling_still_reads_as_misalignment(self, tmp_path, cfg):
        _md, result = self._upload(tmp_path, cfg, coupling=None)
        assert "angular_misalignment" in {f.fault for f in result.findings}

    def test_declaring_uncoupled_commits_bent_shaft(self, tmp_path, cfg):
        md, result = self._upload(tmp_path, cfg, coupling="uncoupled")
        faults = {f.fault for f in result.findings}
        assert "bent_shaft" in faults and "angular_misalignment" not in faults
        assert "uncoupled (declared)" in md
        assert "**Bent shaft** — not assessed" not in md


class TestBeltFromTheLiveForm:
    D1, D2, CENTRES = 120.0, 200.0, 400.0

    def test_pulley_dimensions_derive_a_belt_frequency_and_commit(self, tmp_path, cfg):
        derived = belt_frequency_hz(self.D1, self.D2, self.CENTRES, _RPM)
        path = _csv(tmp_path / "upload.csv",
                    [(derived, 2.2), (derived * 2, 0.9), (_SHAFT_HZ, 0.3)],
                    fmax=400.0, n=1600)
        over = {"drive_pulley_mm": self.D1, "driven_pulley_mm": self.D2,
                "pulley_center_distance_mm": self.CENTRES}
        message, result, merged = _echo([(path, None)], over, cfg)
        app = _app(FakeAnthropicClient(responses=[message]))
        with TestClient(app) as client:
            response = _post(client, path, **over)
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 200
        md = _document(result, merged, cfg)
        assert "belt_fault" in {f.fault for f in result.findings}
        # the derived number AND the arithmetic behind it are in the report
        assert f"{derived:.2f} Hz" in md
        assert "L = 2C + (π/2)(D1+D2) + (D2−D1)²/(4C)" in md
        assert "theoretical wrap length" in md


class TestGeometryOnFileReachesTheReport:
    def test_awaiting_detector_geometry_is_printed_and_declared(self, tmp_path, cfg):
        path = _csv(tmp_path / "upload.csv", [(_SHAFT_HZ, 0.5)])
        over = {"poles": 4, "rotor_bars": 44, "line_freq_hz": 60, "drive_type": "vfd",
                "gear_teeth_driving": 23, "gear_teeth_driven": 91,
                "measurement_location": "Motor DE"}
        message, result, merged = _echo([(path, None)], over, cfg)
        app = _app(FakeAnthropicClient(responses=[message]))
        with TestClient(app) as client:
            response = _post(client, path, **over)
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 200
        md = _document(result, merged, cfg)
        for token in ("Motor poles", "4 (declared)", "Rotor bars", "44 (declared)",
                      "Gear tooth counts", "variable-frequency drive (declared)",
                      "Motor DE"):
            assert token in md, f"{token!r} is missing from the report"
        assert "Geometry on file — awaiting a detector:" in md
        # ...and the families are still NOT ASSESSED, because they are.
        assert "**Rotor bar faults** — not assessed — no detector for this family." in md


# ══════════════════════════════════════════════════════════════════════════
# 4 · The promises move with the card
# ══════════════════════════════════════════════════════════════════════════
class TestBearingIsNamedWithItsLocation:
    """ROADMAP §S6 asks for "bearing part number PER MEASUREMENT LOCATION". A
    report covers one measurement point, so what that means here is that the
    number is bound to the point it was measured at rather than floating free
    on the machine — a bearing number with no location is a machine-wide claim,
    and on a machine with four bearings that is the wrong claim."""

    def test_the_machine_table_names_the_bearing_with_its_location(self, tmp_path, cfg):
        path = _csv(tmp_path / "upload.csv", [(_SHAFT_HZ, 0.5)])
        over = {"bearing_model": "6206", "measurement_location": "Motor DE"}
        message, result, merged = _echo([(path, None)], over, cfg)
        app = _app(FakeAnthropicClient(responses=[message]))
        with TestClient(app) as client:
            response = _post(client, path, **over)
            assert response.status_code == 202, response.text
            job_id = response.json()["job_id"]
            assert _poll_until_terminal(client, job_id)["state"] == "done"
            assert client.get(f"/api/jobs/{job_id}/report.pdf").status_code == 200
        md = _document(result, merged, cfg)
        assert "6206 (at Motor DE)" in md

    def test_without_a_location_the_bearing_row_is_unchanged(self, iso_table, thresholds, rules):
        """Byte-compat: today's reports must not grow a parenthetical."""
        from vib_agent.report.generate import _machine_rows
        from vib_agent.synth.generator import make_case

        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        rows = dict(_machine_rows(result, case.machine, not_assessable=False))
        assert "(at " not in rows["Bearing"]


class TestRetentionLedgerAndPrivacy:
    """Common law #8: a session that widens a localStorage key updates the
    retention ledger AND privacy.html in the same commit. They are diffed
    against each other deliberately."""

    def test_privacy_names_the_geometry_in_all_three_enumerations(self):
        privacy = _privacy()
        assert _prose(privacy).count("machine geometry — measurement location, coupling state") == 3, (
            "privacy.html enumerates what is processed, what stays in the browser and what is "
            "sent to the model — the geometry belongs in all three"
        )

    def test_privacy_says_the_geometry_reaches_the_model(self):
        """`agent/loop.py` puts `machine.model_dump_json()` into the drafting
        prompt, so every MachineMeta field travels by construction."""
        sent = _prose(_privacy()).partition("What is sent when we draft your report.")[2] \
            .partition("</p>")[0]
        assert "machine geometry" in sent

    def test_privacy_states_that_old_cards_are_read_unchanged(self):
        assert "Cards saved before the geometry fields existed" in _prose(_privacy())

    def test_the_ledger_line_names_the_geometry(self):
        ledger = _prose(_app_js().partition("function ledger() {")[2].partition("\n}")[0])
        assert "declared geometry" in ledger
        assert "its details or its geometry" in ledger

    def test_the_ledger_still_promises_no_server_copy(self):
        ledger = _prose(_app_js().partition("function ledger() {")[2].partition("\n}")[0])
        assert "Never on our servers" in ledger and "never your invite code" in ledger

    def test_no_server_side_machine_state_appeared(self):
        app = _app()
        with TestClient(app) as client:
            response = client.get("/")
        assert "set-cookie" not in {k.lower() for k in response.headers}
        assert not hasattr(app.state.vib, "machines")
        assert not any("machine" in getattr(route, "path", "") for route in app.routes)
