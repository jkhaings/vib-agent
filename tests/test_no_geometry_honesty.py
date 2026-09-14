"""Session R3-DIFF (item 2) — a missing instrument is not a clean result.

Without a bearing model number there are no fault frequencies to compute, so
the bearing detector cannot run AT ALL. Before this change a reading in that
state rendered the same paragraph as a genuinely clean one --

    **Committed diagnosis: none — parameters within normal range.**

-- and an analyst had no way to tell "we looked and it is fine" from "we could
not look". The fixture below is the sharp version: a WAV carrying a 107 Hz
modulation at 164x the broadband noise floor, uploaded with no bearing model.
That is an unmistakable repeating impact, and the report called it normal.

The rule is narrow on purpose. It fires only when geometry is ABSENT: with
geometry supplied, an unmatched tone is the bearing detector's business and the
existing differential already adjudicates it.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.io import wavfile

from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.generate import build_context, render_report, unmatched_periodicity

RPM = 1800.0
SHAFT_HZ = RPM / 60.0  # 30 Hz
BPFO_HZ = 107.16  # 3.57x shaft -- not an integer order, and not a half order
FS = 12000.0

CLEAN_PHRASING = "parameters within normal range"


def _waveform(modulation_hz: float = BPFO_HZ, carrier_hz: float = 3000.0) -> np.ndarray:
    """The same shape tests/test_webapp_adapters.py uses for its BPFO WAV:
    a carrier amplitude-modulated at the fault rate."""
    n = int(FS)
    t = np.arange(n) / FS
    signal = np.sin(2 * np.pi * carrier_hz * t) * (1 + 0.5 * np.sin(2 * np.pi * modulation_hz * t))
    return signal + 0.01 * np.random.default_rng(1).standard_normal(n)


@pytest.fixture
def route() -> dict:
    return load_thresholds("route")


@pytest.fixture
def iso_table() -> dict:
    return load_config("iso_zones")["zones"]


def _wav_case(tmp_path, *, bearing_model, modulation_hz=BPFO_HZ):
    signal = _waveform(modulation_hz)
    # Named `upload.wav` deliberately: the webapp stores every upload under a
    # stem of "upload", so a fixture with a descriptive filename would not
    # exercise what production actually parses.
    path = tmp_path / "upload.wav"
    wavfile.write(str(path), int(FS), ((signal / np.max(np.abs(signal))) * 0.8 * 32767).astype(np.int16))
    form = UploadForm(
        machine_alias="WavPump", rpm=RPM, iso_group="2", iso_support="rigid", machine_type="motor",
        bearing_model=bearing_model,
    )
    case, _kind, _note = parse_upload(path, form, bearings_cfg=load_config("bearings"))
    return case


def _analyse(case, route, iso_table):
    return run_analysis(case, iso_table=iso_table, thresholds=route)


def _report_markdown(case, result, route, out_dir):
    render_report(result, case.machine, out_dir, case=case, thresholds=route,
                  profile="route", pdf=False)
    return (out_dir / "report.md").read_text()


# ══════════════════════════════════════════════════════════════════════════════
class TestTheDefect:
    """A WAV with no geometry and an obvious repeating impact."""

    def test_the_analysis_commits_nothing(self, tmp_path, route, iso_table):
        """Precondition, not the point: with no geometry the bearing detector
        cannot run, so nothing is committed. That is correct -- what was wrong
        was calling it normal."""
        case = _wav_case(tmp_path, bearing_model=None)
        result = _analyse(case, route, iso_table)
        assert [f.fault for f in result.findings] == ["no_significant_findings"]
        assert result.rca.bearing_specs_present is False

    def test_it_no_longer_reads_as_a_clean_bill(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model=None)
        md = _report_markdown(case, _analyse(case, route, iso_table), route, tmp_path / "out")
        assert CLEAN_PHRASING not in md, "a 164x-floor tone was reported as normal"

    def test_it_names_the_periodicity_and_what_to_supply(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model=None)
        md = _report_markdown(case, _analyse(case, route, iso_table), route, tmp_path / "out")
        assert "Strong unmatched envelope periodicity" in md
        assert "107.0 Hz" in md
        assert "3.57× shaft" in md
        assert "supply bearing geometry to identify" in md

    def test_the_sentence_carries_every_number_it_quotes(self, tmp_path, route, iso_table):
        """Every figure in the sentence comes from the peak set, not from prose."""
        case = _wav_case(tmp_path, bearing_model=None)
        found = unmatched_periodicity(_analyse(case, route, iso_table), case, route)
        assert found is not None
        assert found["freq_hz"] == pytest.approx(BPFO_HZ, abs=1.0)
        assert found["order"] == pytest.approx(BPFO_HZ / SHAFT_HZ, abs=0.02)
        assert found["floor_multiple"] > route["rca"]["floor_min"]
        assert found["sentence"].startswith("Strong unmatched")
        assert found["sentence"].endswith("supply bearing geometry to identify.")

    def test_it_still_says_what_it_DID_screen(self, tmp_path, route, iso_table):
        """Honesty runs both ways: the shaft-order family really was screened,
        and the report must not now imply nothing was checked."""
        case = _wav_case(tmp_path, bearing_model=None)
        md = _report_markdown(case, _analyse(case, route, iso_table), route, tmp_path / "out")
        assert "1x/2x shaft-order family" in md
        assert "came back" in md


class TestWithGeometryNothingChanges:
    """The same waveform, with the bearing model supplied. This is the golden
    path and it must be untouched."""

    def test_the_bearing_fault_is_committed_as_before(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model="6206")
        result = _analyse(case, route, iso_table)
        assert any(m.fault == "bearing_outer_race" for m in result.rca.primary_findings)

    def test_the_section_does_not_fire(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model="6206")
        result = _analyse(case, route, iso_table)
        assert unmatched_periodicity(result, case, route) is None

    def test_no_new_language_reaches_the_report(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model="6206")
        md = _report_markdown(case, _analyse(case, route, iso_table), route, tmp_path / "out")
        assert "Strong unmatched" not in md
        assert "supply bearing geometry" not in md


class TestItStaysQuietWhenItShould:
    """Every path where the question cannot be asked, or has no answer."""

    def test_a_shaft_order_tone_is_not_unmatched(self, tmp_path, route, iso_table):
        """Modulate at 2x shaft instead. Geometry is still absent, but the
        periodicity IS a shaft order -- the detectors model it, so this section
        has nothing to add and must not manufacture a mystery."""
        case = _wav_case(tmp_path, bearing_model=None, modulation_hz=2 * SHAFT_HZ)
        result = _analyse(case, route, iso_table)
        found = unmatched_periodicity(result, case, route)
        if found is not None:  # a stray sideband may survive; it must not be the 2x
            assert abs(found["freq_hz"] - 2 * SHAFT_HZ) > 1.0

    def test_no_floor_in_the_profile_means_no_claim(self, tmp_path, iso_table):
        """`streaming` carries no floor_min, so "strong" is undefined there and
        the section stays silent rather than guessing at a threshold."""
        streaming = load_thresholds("streaming")
        assert "floor_min" not in streaming["rca"]
        case = _wav_case(tmp_path, bearing_model=None)
        result = _analyse(case, streaming, iso_table)
        assert unmatched_periodicity(result, case, streaming) is None

    def test_no_spectrum_means_no_claim(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model=None)
        result = _analyse(case, route, iso_table)
        stripped = case.model_copy(update={"spectra": None})
        assert unmatched_periodicity(result, stripped, route) is None

    def test_a_missing_rca_is_not_an_error(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model=None)
        result = _analyse(case, route, iso_table)
        assert unmatched_periodicity(result.model_copy(update={"rca": None}), case, route) is None


class TestTheContextExposesIt:
    def test_build_context_carries_the_flag(self, tmp_path, route, iso_table):
        case = _wav_case(tmp_path, bearing_model=None)
        result = _analyse(case, route, iso_table)
        ctx = build_context(result, case.machine, case=case, thresholds=route, profile="route")
        assert ctx["no_findings"] is True
        assert ctx["unmatched_periodicity"] is not None

    def test_a_clean_reading_keeps_the_clean_phrasing(self, tmp_path, route, iso_table):
        """The positive result still exists and still reads as one -- this change
        must not make every quiet machine sound suspicious."""
        case = _wav_case(tmp_path, bearing_model="6206")
        result = _analyse(case, route, iso_table)
        ctx = build_context(result, case.machine, case=case, thresholds=route, profile="route")
        assert ctx["unmatched_periodicity"] is None
