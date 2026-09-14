"""Session REPORT-2 (item 2) — every figure, caption, table row and header that shows
a spectrum names its TYPE and carries the matching UNIT.

A CAT analyst reviewing the outreach sample said spectrum type and units must be
consistent through the report: an envelope spectrum in g, a velocity spectrum in
mm/s, type and unit stated everywhere, never mixed on one figure. `Spectrum` has no
unit field, so `charts.spectrum_unit_label` resolves one from the Case's provenance
(the `_rms_label` discriminators — no new field, no guess) and every surface names
the spectrum through ONE phrase, `charts.spectrum_kind_and_unit`:

    Velocity spectrum (mm/s RMS)     an upload the units module converted
    Envelope spectrum (g)            a benchmark recording, or a scaled WAV
    Envelope spectrum (as supplied)  a synthetic case, an unscaled WAV — a stated non-claim

The positive pins render through the real child-process path; the negative controls
strip the unit at the layer the strings are born and assert the checker goes red.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile

from tests.test_report_html import visible_text
from vib_agent.adapters.uploads import parse_upload
from vib_agent.adapters.uploads.common import UploadForm
from vib_agent.config import load_config, load_thresholds
from vib_agent.models import Case, MachineMeta, SensorData, Spectrum
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts, graphics
from vib_agent.report.charts import (
    _SPECTRUM_KIND_LABEL,
    SPECTRUM_UNIT_ACCELERATION,
    SPECTRUM_UNIT_AS_SUPPLIED,
    SPECTRUM_UNIT_VELOCITY,
    analysis_parameters,
    render_charts,
    spectrum_kind_and_unit,
    spectrum_unit_label,
)
from vib_agent.report.generate import _evidence_rows, render_html, render_markdown
from vib_agent.synth.generator import make_case

_REPO = Path(__file__).resolve().parents[1]
_EXAMPLE_CSV = _REPO / "src" / "vib_agent" / "webapp" / "static" / "example_spectrum.csv"
_UNITS = (SPECTRUM_UNIT_VELOCITY, SPECTRUM_UNIT_ACCELERATION, SPECTRUM_UNIT_AS_SUPPLIED)


@pytest.fixture(scope="module")
def cfg() -> dict:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
        "bearings": load_config("bearings"),
    }


def _analyse(case: Case, cfg: dict):
    return run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                        rules=cfg["rules"])


def states_type_and_unit(text: str) -> bool:
    """The pin itself: a spectrum kind label AND one of the three unit phrases, as
    `spectrum_kind_and_unit` joins them — `"<Kind> spectrum (<unit>)"`."""
    kinds = "|".join(re.escape(k) for k in _SPECTRUM_KIND_LABEL.values())
    units = "|".join(re.escape(u) for u in _UNITS)
    return re.search(rf"(?i)(?:{kinds}) \((?:{units})\)", text) is not None


def _sample(cfg: dict) -> Case:
    return make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)


def _upload_csv(cfg: dict) -> Case:
    form = UploadForm(machine_alias="Demo Pump", rpm=1800.0, iso_group="2",
                      iso_support="rigid", machine_type="motor", bearing_model="6206")
    case, _, _ = parse_upload(_EXAMPLE_CSV, form, bearings_cfg=cfg["bearings"])
    return case


def _benchmark_case() -> Case:
    machine = MachineMeta(mac="X", name="X", machine_class="medium", mounting="rigid",
                          axial_axis="x", coupled=True, rpm_nominal=1800.0, active=True,
                          iso_group="2", iso_support="rigid")
    spectrum = Spectrum(freq_hz=[0.0, 1.0, 2.0], amplitude=[0.1, 0.2, 0.1], fmax_hz=2.0,
                        kind="envelope")
    return Case(name="b", machine=machine, sensor_data=SensorData(rpm=1800.0, y_rms_ACC_G=1.034),
                spectra={"y": spectrum}, source="cwru", validation_scope=["rca"])


def _wav_case(tmp_path: Path, cfg: dict, *, sensitivity: float | None) -> Case:
    fs = 12000.0
    n = int(fs)
    t = np.arange(n) / fs
    signal = np.sin(2 * np.pi * 3000 * t) * (1 + 0.5 * np.sin(2 * np.pi * 107.16 * t))
    signal += 0.01 * np.random.default_rng(1).standard_normal(n)
    path = tmp_path / "upload.wav"
    wavfile.write(str(path), int(fs), ((signal / np.max(np.abs(signal))) * 0.8 * 32767).astype(np.int16))
    form = UploadForm(machine_alias="W", rpm=1800.0, iso_group="2", iso_support="rigid", machine_type="motor",
                      bearing_model="6206", wav_sensitivity=sensitivity)
    case, _, _ = parse_upload(path, form, bearings_cfg=cfg["bearings"])
    return case


# ── the resolver ─────────────────────────────────────────────────────────


class TestTheUnitComesFromProvenance:
    def test_a_converted_velocity_upload_is_mm_s_rms(self, cfg):
        case = _upload_csv(cfg)
        assert spectrum_unit_label(case, case.spectra["y"]) == SPECTRUM_UNIT_VELOCITY
        assert spectrum_kind_and_unit(case, case.spectra["y"]) == "Velocity spectrum (mm/s RMS)"

    def test_a_benchmark_recording_is_g(self):
        case = _benchmark_case()
        assert spectrum_kind_and_unit(case, case.spectra["y"]) == "Envelope spectrum (g)"

    def test_a_scaled_wav_is_g_and_an_unscaled_one_is_as_supplied(self, tmp_path, cfg):
        (tmp_path / "s").mkdir()
        (tmp_path / "u").mkdir()
        scaled = _wav_case(tmp_path / "s", cfg, sensitivity=0.05)
        unscaled = _wav_case(tmp_path / "u", cfg, sensitivity=None)
        assert spectrum_unit_label(scaled, scaled.spectra["y"]) == SPECTRUM_UNIT_ACCELERATION
        assert spectrum_unit_label(unscaled, unscaled.spectra["y"]) == SPECTRUM_UNIT_AS_SUPPLIED

    def test_the_synthetic_sample_claims_nothing_it_cannot_know(self, cfg):
        """The generator states velocity separately from its spectrum and declares no
        amplitude unit for the array — so the figures say "as supplied", not a guess."""
        case = _sample(cfg)
        for axis in ("x", "y", "z"):
            assert spectrum_unit_label(case, case.spectra[axis]) == SPECTRUM_UNIT_AS_SUPPLIED
        assert spectrum_unit_label(None, case.spectra["y"]) == SPECTRUM_UNIT_AS_SUPPLIED


# ── every surface, on the sample (the outreach report's own case) ───────


class TestEveryFigureAndRowOnTheSample:
    @pytest.fixture(scope="class")
    def rendered(self, cfg, tmp_path_factory):
        out = tmp_path_factory.mktemp("s2")
        case = _sample(cfg)
        result = _analyse(case, cfg)
        chartset = render_charts(result, case.machine, out, case=case, thresholds=cfg["thresholds"])
        md = render_markdown(result, case.machine, charts=chartset, case=case,
                             thresholds=cfg["thresholds"], profile="route")
        html = render_html(result, case.machine, charts=chartset, case=case,
                           thresholds=cfg["thresholds"], profile="route")
        return case, result, chartset, md, html

    def test_every_channel_figure_names_type_and_unit(self, rendered):
        _, _, chartset, _, _ = rendered
        figures = [f for ch in chartset.channels for f in ch.figures]
        assert len(figures) == 3
        for figure in figures:
            assert states_type_and_unit(figure.title), figure.title
            assert states_type_and_unit(figure.caption), figure.caption
            assert states_type_and_unit(figure.alt), figure.alt
            # Session CHARTS-2 item 4 split this one "Spectrum" cell into Type
            # and Unit, and evicted "(as supplied)" from the header entirely
            # (Part C E2 item 3: it is the string that tipped the header into
            # printing over the Δf field on nine pages of two shipped PDFs).
            # Where provenance cannot name a unit the CELL IS OMITTED rather
            # than filled with a non-claim -- and the non-claim is still stated
            # on the figure by the caption, which is checked three lines above.
            # REPORT-3 owns where it finally lands.
            header = dict(figure.header)
            assert header["Type"] == "Envelope spectrum", header
            assert "Unit" not in header, header
            assert "as supplied" not in " ".join(header.values()), header

    def test_the_kind_word_stays_first_in_the_title(self, rendered):
        """tests/test_report_html.py classifies the markdown's ### headings by it."""
        _, _, chartset, _, _ = rendered
        for ch in chartset.channels:
            for figure in ch.figures:
                assert figure.title.startswith(("Envelope spectrum (", "Velocity spectrum (",
                                                "Acceleration spectrum ("))

    def test_both_parameter_rows_carry_the_unit(self, rendered):
        case, result, _, _, _ = rendered
        params = dict(analysis_parameters(result, case=case, thresholds=None, profile="route"))
        assert params["Spectrum type"] == "Envelope spectrum (as supplied)"
        # the unresolved case keeps the honest non-value it always had
        assert params["Spectrum amplitude units"] == "as supplied at upload"

    def test_the_words_reach_both_documents(self, rendered):
        _, _, _, md, html = rendered
        assert "### Envelope spectrum (as supplied) — Y (radial)" in md
        caption = "Envelope spectrum (as supplied), Y axis. Matched: bearing outer race at"
        assert caption in md
        assert caption in visible_text(html)
        assert "| Spectrum type | Envelope spectrum (as supplied) |" in md

    def test_the_graphics_name_the_series_and_its_unit(self, rendered):
        case, result, _, _, html = rendered
        assert "on axis y of the envelope spectrum (as supplied)" in visible_text(html)
        assert "the measured envelope spectrum (as supplied) on axis y" in visible_text(html)
        assert "Amplitude (as supplied)</text>" in html  # the SVG inset's own axis

    def test_the_figure_axis_label_is_the_same_phrase(self, rendered, monkeypatch, tmp_path, cfg):
        """The y-axis label lives inside the figure; pin the phrase the renderer
        asks for.

        SIX calls for three channels since Session CHARTS-2 item 5: each spectrum
        figure now carries two panels — the full analysed span and a detail on
        the fault region — and each panel labels its own amplitude axis. An
        unlabelled second axis is exactly the defect item 2 is about, so the
        count going up here is the pin doing its job.
        """
        case, result, _, _, _ = rendered
        asked: list[str] = []
        real = charts.amplitude_axis_label
        monkeypatch.setattr(charts, "amplitude_axis_label", lambda unit: asked.append(unit) or real(unit))
        charts.render_charts_inprocess(result, case.machine, tmp_path, case=case,
                                       thresholds=cfg["thresholds"])
        # SEVEN since Session REPORT-3: the six panels above plus the page-1
        # figure's single panel. Same phrase, same resolver — which is the
        # claim, and the reason a new figure had to move this number.
        assert asked == [SPECTRUM_UNIT_AS_SUPPLIED] * 7
        assert real(SPECTRUM_UNIT_VELOCITY) == "Amplitude (mm/s RMS)"


class TestTheUploadPathSaysVelocityInMmS:
    def test_figure_caption_header_and_rows(self, cfg, tmp_path):
        case = _upload_csv(cfg)
        result = _analyse(case, cfg)
        chartset = render_charts(result, case.machine, tmp_path, case=case, thresholds=cfg["thresholds"])
        figures = [f for ch in chartset.channels for f in ch.figures]
        assert figures and all("Velocity spectrum (mm/s RMS)" in f.title for f in figures)
        assert all("Velocity spectrum (mm/s RMS)" in f.caption for f in figures)
        # Type and unit in their own cells since CHARTS-2 item 4. Here provenance
        # DOES name a unit, so the header states it -- this is the positive half
        # of the "as supplied" omission pinned in TestEveryFigureAndRowOnTheSample.
        assert all(dict(f.header)["Type"] == "Velocity spectrum" for f in figures)
        assert all(dict(f.header)["Unit"] == "mm/s RMS" for f in figures)
        params = dict(analysis_parameters(result, case=case, thresholds=None, profile="route"))
        assert params["Spectrum type"] == "Velocity spectrum (mm/s RMS)"
        assert params["Spectrum amplitude units"] == "mm/s RMS"
        html = render_html(result, case.machine, charts=chartset, case=case,
                           thresholds=cfg["thresholds"], profile="route")
        assert "of the velocity spectrum (mm/s RMS)" in visible_text(html)
        assert "Amplitude (mm/s RMS)</text>" in html

    def test_a_benchmark_row_says_g(self, cfg):
        case = _benchmark_case()
        result = _analyse(case, cfg)
        params = dict(analysis_parameters(result, case=case, thresholds=None, profile="route"))
        assert params["Spectrum type"] == "Envelope spectrum (g)"
        assert params["Spectrum amplitude units"] == "g"


# ── the negative controls ────────────────────────────────────────────────


class TestStrippingTheUnitGoesRed:
    """The checker fails at the layer each string is born when the unit is removed —
    so the positive pins above measure something."""

    def test_the_checker_refuses_a_kind_without_a_unit(self):
        assert states_type_and_unit("Envelope spectrum (as supplied), Y axis.")
        assert not states_type_and_unit("Envelope spectrum, Y axis.")
        assert not states_type_and_unit("Envelope spectrum (), Y axis.")
        assert not states_type_and_unit("Amplitude (mm/s RMS)")  # a unit with no kind

    def test_caption_header_and_rows_go_red(self, cfg, monkeypatch):
        case = _sample(cfg)
        result = _analyse(case, cfg)
        spectrum = case.spectra["y"]
        matches = charts._matches_for_roles(result, "y", frozenset({charts.ROLE_BEARING}))
        assert states_type_and_unit(charts._spectrum_caption(result, "y", spectrum, matches, case=case))
        # The header's own half of this: with a unit resolvable it states one,
        # and with the resolver stripped it states none -- it never invents one
        # and never prints an empty cell (CHARTS-2 item 4).
        upload = _upload_csv(cfg)
        upload_spectrum = upload.spectra[sorted(upload.spectra)[0]]
        assert dict(charts._channel_header(upload_spectrum, upload))["Unit"] == "mm/s RMS"
        monkeypatch.setattr(charts, "spectrum_unit_label", lambda case, spectrum: "")
        assert not states_type_and_unit(charts._spectrum_caption(result, "y", spectrum, matches, case=case))
        assert "Unit" not in dict(charts._channel_header(spectrum, case))
        params = dict(analysis_parameters(result, case=case, thresholds=None, profile="route"))
        assert not states_type_and_unit(params["Spectrum type"])

    def test_the_graphics_go_red_too(self, cfg, monkeypatch):
        case = _sample(cfg)
        result = _analyse(case, cfg)
        rows = _evidence_rows(result)
        before = str(graphics.bearing_map(result, case=case, thresholds=cfg["thresholds"]))
        before_insets = str(graphics.evidence_insets(result, rows, case=case, thresholds=cfg["thresholds"]))
        assert "envelope spectrum (as supplied)" in before
        assert "envelope spectrum (as supplied)" in before_insets and "Amplitude (as supplied)" in before_insets
        monkeypatch.setattr(charts, "spectrum_unit_label", lambda case, spectrum: "")
        after = str(graphics.bearing_map(result, case=case, thresholds=cfg["thresholds"]))
        after_insets = str(graphics.evidence_insets(result, rows, case=case, thresholds=cfg["thresholds"]))
        assert "envelope spectrum (as supplied)" not in after
        assert "as supplied" not in after_insets
