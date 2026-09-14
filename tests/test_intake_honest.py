"""Session INTAKE-HONEST — declared acquisition settings (sensor sensitivity,
Fmax, lines, window, averages, integration) become first-class optional
inputs, and every intake ambiguity states itself in the report.

Slice 1 pins the carrier: form -> UploadForm -> parse_upload (central attach)
-> Case.acquisition, across lanes and across the sandbox JSON boundary, with
the pipeline provably indifferent (provenance-only, never computation).
"""

from __future__ import annotations

import csv
import json

import numpy as np
import pytest
from scipy.io import wavfile

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm, acquisition_from_form
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import Case
from vib_agent.pipeline import run_analysis

RPM = 1800.0
BPFO_HZ = 107.03

#: The six declared fields, exactly as they travel (form name == UploadForm
#: attribute == AcquisitionMeta field). Slice 3 reuses this to pin the HTML
#: inputs and the browser memory list against the same vocabulary.
ACQUISITION_FIELDS = (
    "sensor_sensitivity_mv_per_g",
    "fmax_hz",
    "spectral_lines",
    "window_type",
    "averages",
    "integration",
)

DECLARED = {
    "sensor_sensitivity_mv_per_g": 100.0,
    "fmax_hz": 200.0,
    "spectral_lines": 400,
    "window_type": "hanning",
    "averages": 8,
    "integration": "hardware",
}


@pytest.fixture
def bearings_cfg():
    return load_config("bearings")


def _spectrum_rows(peak_hz: float = BPFO_HZ, *, n: int = 400, fmax: float = 200.0):
    freqs = [i * fmax / n for i in range(n)]
    amps = [0.001] * n
    idx = min(range(n), key=lambda i: abs(freqs[i] - peak_hz))
    amps[idx] = 0.5
    return list(zip(freqs, amps))


def _write_spectrum_csv(path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("freq_hz", "amplitude"))
        for row in _spectrum_rows():
            w.writerow(row)


def _write_trend_csv(path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("timestamp", "overall_rms"))
        for day, value in enumerate((2.1, 2.4, 2.8)):
            w.writerow((f"2026-08-{10 + day}T00:00:00", value))


def _write_wav(path, fs: int = 12000):
    t = np.arange(fs) / fs
    signal = np.sin(2 * np.pi * 30.0 * t) + 0.4 * np.sin(2 * np.pi * BPFO_HZ * t)
    norm = signal / np.max(np.abs(signal)) * 0.8
    wavfile.write(str(path), fs, (norm * 32767).astype(np.int16))


def _form(**extra) -> UploadForm:
    return UploadForm(
        machine_alias="Pump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
        bearing_model="6206", **extra,
    )


class TestAcquisitionCarrier:
    def test_uploadform_defaults_every_declared_field_to_none(self):
        form = _form()
        for field in ACQUISITION_FIELDS:
            assert getattr(form, field) is None

    def test_untouched_form_leaves_case_acquisition_absent(self, tmp_path, bearings_cfg):
        """An analyst who declares nothing gets a Case exactly as absent of
        acquisition metadata as before this session existed."""
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(), bearings_cfg=bearings_cfg)
        assert case.acquisition is None

    def test_empty_select_strings_mean_not_provided(self):
        """'' from an unselected <select> normalizes to absent, not to a
        stored empty string an analyst never chose."""
        assert acquisition_from_form(_form(window_type="", integration="")) is None

    def test_declared_fields_attach_on_tabular_spectrum(self, tmp_path, bearings_cfg):
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(**DECLARED), bearings_cfg=bearings_cfg)
        assert case.acquisition is not None
        assert case.acquisition.model_dump() == DECLARED

    def test_declared_fields_attach_on_trend_mode(self, tmp_path, bearings_cfg):
        path = tmp_path / "t.csv"
        _write_trend_csv(path)
        case, _, _ = parse_upload(
            path, _form(mode="trend", **DECLARED), bearings_cfg=bearings_cfg
        )
        assert case.acquisition is not None
        assert case.acquisition.model_dump() == DECLARED

    def test_declared_fields_attach_on_wav(self, tmp_path, bearings_cfg):
        """The WAV lane builds its own Case; the central attach in parse_upload
        must cover it without wav.py knowing the fields exist."""
        path = tmp_path / "s.wav"
        _write_wav(path)
        case, _, _ = parse_upload(
            path, _form(wav_sensitivity=0.05, **DECLARED), bearings_cfg=bearings_cfg
        )
        assert case.acquisition is not None
        assert case.acquisition.model_dump() == DECLARED

    def test_partial_declaration_keeps_only_what_was_given(self, tmp_path, bearings_cfg):
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(
            path, _form(window_type="flattop"), bearings_cfg=bearings_cfg
        )
        assert case.acquisition is not None
        assert case.acquisition.window_type == "flattop"
        for field in ACQUISITION_FIELDS:
            if field != "window_type":
                assert getattr(case.acquisition, field) is None

    def test_acquisition_survives_the_sandbox_json_boundary(self, tmp_path, bearings_cfg):
        """_parse_child writes case.model_dump_json(); the parent re-validates
        with Case.model_validate. The declared settings must survive that
        exact round trip or the webapp lane silently drops them."""
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(**DECLARED), bearings_cfg=bearings_cfg)
        rebuilt = Case.model_validate(json.loads(case.model_dump_json()))
        assert rebuilt.acquisition is not None
        assert rebuilt.acquisition.model_dump() == DECLARED

    def test_old_case_json_without_acquisition_still_validates(self, tmp_path, bearings_cfg):
        """Additive-field guarantee: a pre-session Case JSON (no `acquisition`
        key at all) validates and reads as absent."""
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(), bearings_cfg=bearings_cfg)
        payload = json.loads(case.model_dump_json())
        payload.pop("acquisition", None)
        rebuilt = Case.model_validate(payload)
        assert rebuilt.acquisition is None


class TestPipelineIndifference:
    def test_pipeline_output_is_invariant_under_acquisition(self, tmp_path, bearings_cfg, iso_table, rules):
        """Provenance-only means provenance-only: the full deterministic
        pipeline produces an identical AnalysisResult (timestamps aside)
        whether or not acquisition metadata is attached."""
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(**DECLARED), bearings_cfg=bearings_cfg)
        assert case.acquisition is not None
        bare = case.model_copy(update={"acquisition": None})

        thresholds = load_thresholds("route")  # the upload profile, named explicitly
        with_meta = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        without_meta = run_analysis(bare, iso_table=iso_table, thresholds=thresholds, rules=rules)

        def strip_ts(obj):
            if isinstance(obj, dict):
                return {k: strip_ts(v) for k, v in obj.items() if k != "ts"}
            if isinstance(obj, list):
                return [strip_ts(v) for v in obj]
            return obj

        assert strip_ts(with_meta.model_dump()) == strip_ts(without_meta.model_dump())


class TestParametersTableHonesty:
    """Slice 2 — the Analysis-parameters table prints declared values as
    declared, cross-checks them against the data, and states plainly what was
    not provided. No value is ever invented; no declared value ever overrides
    a measured one."""

    def _params(self, tmp_path, bearings_cfg, iso_table, rules, **declared):
        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(**declared), bearings_cfg=bearings_cfg)
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        from vib_agent.report.charts import analysis_parameters

        return dict(analysis_parameters(result, case=case, thresholds=thresholds, profile="route"))

    def test_absent_fields_state_themselves(self, tmp_path, bearings_cfg, iso_table, rules):
        params = self._params(tmp_path, bearings_cfg, iso_table, rules)
        assert params["Window"] == "not provided — unknown window; amplitudes taken as supplied"
        assert params["Averages"] == "not provided — unknown"
        # Session INTAKEFIX-1, REPORTFIX-1's F-6. This asserted
        # "... (see conversion note)", and that note is a WEBAPP insertion --
        # `report/` never emits one, so on this very code path (a direct
        # `analysis_parameters` call, no webapp anywhere) the reference pointed
        # at nothing. The assertion is kept EXACT and kept in both directions:
        # the row still has to say what was not provided, and it must no longer
        # send the reader to a note that may not exist.
        assert params["Sensor sensitivity"] == (
            "not provided — amplitudes used as supplied"
        )
        assert "conversion note" not in params["Sensor sensitivity"], (
            "report/ cannot promise a note only webapp/worker.py inserts"
        )
        assert params["Integration before export"] == (
            "not provided — velocity values taken as supplied"
        )
        assert params["Spectral lines (N)"] == "400 (from data; not declared)"
        assert "not declared" in params["Fmax"]

    def test_declared_fields_print_as_declared(self, tmp_path, bearings_cfg, iso_table, rules):
        params = self._params(tmp_path, bearings_cfg, iso_table, rules, **DECLARED)
        assert params["Window"] == "Hanning (declared)"
        assert params["Averages"] == "8 (declared)"
        assert params["Sensor sensitivity"] == "100 mV/g (declared)"
        assert params["Integration before export"] == "integrated in the instrument (declared)"
        # declared 400 lines == the data's 400 bins; declared 200 Hz Fmax is
        # within one bin of the data's 199.5 Hz top bin.
        assert params["Spectral lines (N)"] == "400 (declared, matches data)"
        assert params["Fmax"] == "200.0 Hz (declared, matches data)"

    def test_declared_data_mismatch_states_both_and_uses_the_data(
        self, tmp_path, bearings_cfg, iso_table, rules
    ):
        params = self._params(
            tmp_path, bearings_cfg, iso_table, rules, spectral_lines=800, fmax_hz=400.0
        )
        assert params["Spectral lines (N)"] == "400 (data; 800 declared — data used)"
        assert params["Fmax"] == "199.5 Hz (data; 400.0 Hz declared — data used)"

    def test_declared_values_never_change_the_analysis(
        self, tmp_path, bearings_cfg, iso_table, rules
    ):
        """A wildly wrong declared Fmax moves the printed provenance and
        nothing else — the sample-rate and line-spacing rows still read from
        the data, and TestPipelineIndifference already pins the result."""
        params = self._params(tmp_path, bearings_cfg, iso_table, rules, fmax_hz=400.0)
        assert params["Sample rate (fs)"] == "399 Hz (from Fmax 200 Hz)"

    def test_rows_render_in_the_markdown_report(self, tmp_path, bearings_cfg, iso_table, rules):
        from vib_agent.report.generate import render_markdown

        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        case, _, _ = parse_upload(path, _form(**DECLARED), bearings_cfg=bearings_cfg)
        thresholds = load_thresholds("route")
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds)
        assert "| Window | Hanning (declared) |" in md
        assert "| Averages | 8 (declared) |" in md
        assert "| Sensor sensitivity | 100 mV/g (declared) |" in md
        assert "| Integration before export | integrated in the instrument (declared) |" in md


class TestAssumptionNotes:
    """Slice 2 — the acceleration-proxy convention states itself in the
    conversion note on every lane that uses it."""

    def test_tabular_spectrum_note_states_the_proxy(self, tmp_path, bearings_cfg):
        from vib_agent.adapters.uploads.tabular import ACCEL_PROXY_NOTE

        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        _, _, note = parse_upload(path, _form(), bearings_cfg=bearings_cfg)
        assert note.endswith(ACCEL_PROXY_NOTE)
        assert "no conversion applied" in note  # the units half survives intact

    def test_trend_note_states_the_proxy(self, tmp_path, bearings_cfg):
        from vib_agent.adapters.uploads.tabular import ACCEL_PROXY_NOTE

        path = tmp_path / "t.csv"
        _write_trend_csv(path)
        _, _, note = parse_upload(path, _form(mode="trend"), bearings_cfg=bearings_cfg)
        assert note.endswith(ACCEL_PROXY_NOTE)

    def test_recipe_lane_note_states_the_proxy(self, tmp_path, bearings_cfg):
        from vib_agent.adapters.uploads.recipe import ParseRecipe
        from vib_agent.adapters.uploads.tabular import ACCEL_PROXY_NOTE

        path = tmp_path / "s.txt"
        path.write_text("\n".join(f"{f},{a}" for f, a in _spectrum_rows()))
        recipe = ParseRecipe(kind="spectrum", delimiter="comma", columns=["x", "amplitude"])
        _, kind, note = parse_upload(path, _form(), bearings_cfg=bearings_cfg, recipe=recipe)
        assert kind == "inferred_spectrum"
        assert note.endswith(ACCEL_PROXY_NOTE)

    def test_wav_lane_does_not_claim_a_proxy(self, tmp_path, bearings_cfg):
        """A WAV carries a real signal; the proxy sentence would be false
        there and must not appear."""
        from vib_agent.adapters.uploads.tabular import ACCEL_PROXY_NOTE

        path = tmp_path / "s.wav"
        _write_wav(path)
        _, _, note = parse_upload(
            path, _form(wav_sensitivity=0.05), bearings_cfg=bearings_cfg
        )
        assert ACCEL_PROXY_NOTE not in note


class TestBrowserSurfaceAndPromise:
    """Slice 3 — the six fields exist on the form (under More options), join
    the browser-held machine card, and the privacy page's two enumerations
    say so in the same commit (the ledger/privacy promise-diff)."""

    @staticmethod
    def _static(name: str) -> str:
        from pathlib import Path

        import vib_agent.webapp as webapp_pkg

        return (Path(webapp_pkg.__file__).parent / "static" / name).read_text()

    def test_fields_live_under_more_options_not_the_main_form(self):
        html = self._static("index.html")
        head, _, rest = html.partition('<details class="more" id="more-options">')
        more, _, _ = rest.partition("</details>")
        for field in ACQUISITION_FIELDS:
            assert f'name="{field}"' in more, f"{field} should be under More options"
            assert f'name="{field}"' not in head, f"{field} is in the main form"
        assert "not provided" in more.lower()  # the no-guessing help line

    def test_selects_carry_exactly_the_served_vocabularies(self):
        """The <option> values are the closed vocabularies app.py validates
        against — a drifted option would turn into an analyst-facing 422."""
        from vib_agent.webapp.app import _INTEGRATION_KINDS, _WINDOW_TYPES

        html = self._static("index.html")
        _, _, rest = html.partition('name="window_type"')
        window_block, _, _ = rest.partition("</select>")
        for value in _WINDOW_TYPES:
            assert f'value="{value}"' in window_block
        _, _, rest = html.partition('name="integration"')
        integration_block, _, _ = rest.partition("</select>")
        for value in _INTEGRATION_KINDS:
            assert f'value="{value}"' in integration_block

    def test_memory_fields_cover_acquisition_and_still_ban_credentials(self):
        js = self._static("app.js")
        fields = js.partition("const MEMORY_FIELDS = [")[2].partition("];")[0]
        for field in ACQUISITION_FIELDS:
            assert f"'{field}'" in fields, f"{field} missing from the machine card"
        for banned in ("invite_code", "retain_trace", "file"):
            assert f"'{banned}'" not in fields

    def test_privacy_names_acquisition_in_both_enumerations(self):
        html = self._static("privacy.html")
        processed = html.partition("What is processed")[2].partition("<h2>")[0]
        browser = html.partition("What stays in your browser")[2].partition("<h2>")[0]
        for section, label in ((processed, "What is processed"),
                               (browser, "What stays in your browser")):
            assert "acquisition settings" in section, f"{label} omits the new fields"
            for word in ("sensitivity", "Fmax", "window", "averages", "integration"):
                assert word in section, f"{label} omits {word}"

    def test_sandbox_subprocess_round_trip_carries_acquisition(self, tmp_path):
        """The real child process, the real argv/JSON boundary — the declared
        settings survive parse_in_subprocess exactly as the webapp uses it."""
        from vib_agent.config import CONFIG_DIR
        from vib_agent.webapp.parsing import parse_in_subprocess

        path = tmp_path / "s.csv"
        _write_spectrum_csv(path)
        form_dict = {
            "machine_alias": "Pump", "rpm": 1800.0, "iso_group": "2",
            "iso_support": "rigid", "machine_type": "motor", "bearing_model": "6206",
            "velocity_unit": "mm_s", "detection_type": "rms", "mode": "spectrum",
            "wav_sensitivity": None, **DECLARED,
        }
        case_dict, kind, _ = parse_in_subprocess(
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
        assert case_dict["acquisition"] == DECLARED


class TestAcquisitionValidation:
    """The 422 boundary: a bad declared value costs nothing — no job, no
    charge, and never a sandbox PARSE_ERROR misfiled as `upload_unreadable`."""

    def _post(self, client, files=None, **overrides):
        data = {
            "invite_code": "beta-1", "machine_alias": "Pump", "rpm": "1800",
            "iso_group": "2", "iso_support": "rigid", "machine_type": "motor",
        }
        data.update({k: str(v) for k, v in overrides.items()})
        if files is None:
            files = {"file": ("s.csv", b"freq_hz,amplitude\n10.0,0.1\n", "text/csv")}
        return client.post("/api/jobs", data=data, files=files)

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient

        from vib_agent.webapp.app import create_app

        app = create_app(
            webapp_cfg=load_config("webapp"),
            invite_codes={"beta-1": "tester"},
        )
        with TestClient(app) as client:
            yield client

    @pytest.mark.parametrize("field,value", [
        ("sensor_sensitivity_mv_per_g", "-5"),
        ("fmax_hz", "0"),
        ("spectral_lines", "0"),
        ("averages", "-1"),
        ("window_type", "kaiser"),
        ("integration", "twice"),
    ])
    def test_bad_declared_value_is_a_422_not_a_job(self, client, field, value):
        r = self._post(client, **{field: value})
        assert r.status_code == 422
        assert field in r.json()["detail"] if isinstance(r.json().get("detail"), str) else True

    def test_empty_select_values_are_accepted(self, client):
        r = self._post(client, window_type="", integration="")
        assert r.status_code == 202

    def test_declared_values_are_accepted(self, client):
        r = self._post(client, **DECLARED)
        assert r.status_code == 202
