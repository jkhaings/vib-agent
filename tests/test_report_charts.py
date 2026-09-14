"""Session H — evidence-grade reports: charts, status badge, analysis
parameters, signature block, and the drafted-report splice.

The bar for every figure here is DETERMINISM (identical inputs -> identical
markdown and identical PNG bytes) and HONESTY (nothing plotted that the
pipeline did not compute; nothing implied about units the upload never
declared). PDF byte-identity is deliberately NOT asserted -- both PDF engines
embed their own document ids.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report.generate import (
    FAULT_LABELS,
    render_markdown,
    render_report,
    write_drafted_report,
)
from vib_agent.synth.generator import make_case, make_history

pytestmark = pytest.mark.skipif(
    not charts_mod.matplotlib_available(), reason="matplotlib not installed ([pdf] extra)"
)


# ── helpers ──────────────────────────────────────────────────────────────


def _bpfo(iso_table, thresholds, rules, *, history: bool = False):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    if history:
        case = case.model_copy(
            update={"history": make_history(30, 1.2, 5.2, noise_pct=0.12, cadence="daily", seed=7)}
        )
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _not_assessable(iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    sd = case.sensor_data.model_copy(
        update={"x_velocity_mm_sec": None, "y_velocity_mm_sec": None, "z_velocity_mm_sec": None}
    )
    case = case.model_copy(update={"sensor_data": sd})
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _svg_size(path: Path) -> tuple[float, float]:
    """Width/height in POINTS, straight off the SVG root — no XML library needed.

    Session CHARTS-2 item 1 made the figures SVG, so the old `_png_size` (which
    unpacked the PNG IHDR and asserted the magic bytes) has nothing to read. The
    unit changes with the format: a PNG carried pixels at 150 dpi, an SVG
    carries the authored size in points, `figsize * 72`. The assertion is the
    same one — the render size is fixed and does not drift.
    """
    head = path.read_text(errors="replace")[:2048]
    assert "<svg" in head, f"{path} is not an SVG"
    found = dict(re.findall(r'\b(width|height)="([0-9.]+)pt"', head))
    assert {"width", "height"} <= set(found), f"{path} has no pt size: {head[:300]}"
    return float(found["width"]), float(found["height"])


# ── figures are written, referenced, and complete ────────────────────────


class TestFiguresWritten:
    def test_spectrum_figure_per_axis_plus_badge(self, tmp_path, iso_table, thresholds, rules):
        case, result = _bpfo(iso_table, thresholds, rules)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        names = {p.name for p in charts_mod.chart_files(tmp_path)}
        # `sheet_y.svg` is Session REPORT-3's page-1 figure: the axis the
        # committed call was made on, drawn on the shorter page-1 canvas.
        assert names == {"status_badge.svg", "spectrum_x.svg", "spectrum_y.svg",
                         "spectrum_z.svg", "sheet_y.svg"}

    def test_markdown_embeds_every_written_figure_by_relative_path(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        md = (tmp_path / "report.md").read_text()
        referenced = set(re.findall(r"!\[[^\]]*\]\((charts/[^)]+)\)", md))
        written = {f"charts/{p.name}" for p in charts_mod.chart_files(tmp_path)}
        assert referenced == written
        # RELATIVE, never absolute — both PDF engines resolve against out_dir.
        assert str(tmp_path) not in md

    def test_trend_figure_only_when_history_was_analyzed(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        assert not (tmp_path / "charts" / "trend.svg").exists()

        with_history = tmp_path / "with_history"
        case_h, result_h = _bpfo(iso_table, thresholds, rules, history=True)
        render_report(result_h, case_h.machine, with_history, pdf=False, case=case_h,
                      thresholds=thresholds)
        assert (with_history / "charts" / "trend.svg").exists()
        assert "## Trend Summary" in (with_history / "report.md").read_text()

    def test_gate_fail_gets_a_badge_but_no_spectra(self, tmp_path, iso_table, thresholds, rules):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.quality_gate.overall == "fail"
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        names = {p.name for p in charts_mod.chart_files(tmp_path)}
        assert names == {"status_badge.svg"}
        assert "Insufficient data" in (tmp_path / "report.md").read_text()

    def test_figures_can_be_switched_off(self, tmp_path, iso_table, thresholds, rules):
        case, result = _bpfo(iso_table, thresholds, rules)
        render_report(result, case.machine, tmp_path, pdf=False, case=case,
                      thresholds=thresholds, figures=False)
        assert charts_mod.chart_files(tmp_path) == []
        md = (tmp_path / "report.md").read_text()
        assert "](charts/" not in md
        # The status is still stated -- in words, never by colour alone.
        assert "**STATUS — ISO ZONE D" in md


# ── the always-possible fallback ─────────────────────────────────────────


class TestFallbackWithoutCase:
    def test_frequency_map_replaces_spectra_when_no_case_reaches_the_renderer(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _case, result = _bpfo(iso_table, thresholds, rules, history=True)
        render_report(result, result.iso and _machine(iso_table, thresholds), tmp_path,
                      pdf=False, thresholds=thresholds)
        names = {p.name for p in charts_mod.chart_files(tmp_path)}
        assert names == {"status_badge.svg", "frequency_map.svg", "trend.svg"}

    def test_ncd_triplet_case_falls_back_too(self, tmp_path, iso_table, thresholds, rules):
        """The NCD sensor path carries onboard peak triplets and no spectrum
        array at all -- there is nothing to draw a line plot from, so the map
        is the honest figure even though a full Case reached the renderer."""
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        case = case.model_copy(update={"spectra": None, "raw_spectra": None})
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        names = {p.name for p in charts_mod.chart_files(tmp_path)}
        assert names == {"status_badge.svg", "frequency_map.svg"}

    def test_fallback_states_that_no_amplitude_axis_exists(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _case, result = _bpfo(iso_table, thresholds, rules)
        render_report(result, _machine(iso_table, thresholds), tmp_path, pdf=False,
                      thresholds=thresholds)
        md = (tmp_path / "report.md").read_text()
        assert "spectrum array was not retained" in md


def _machine(iso_table, thresholds):
    return make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1).machine


# ── determinism ──────────────────────────────────────────────────────────


class TestDeterminism:
    def test_identical_inputs_give_identical_markdown_and_identical_figures(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """Byte-determinism, and it survived the move to SVG (CHARTS-2 item 1).

        It did not survive it for free: matplotlib's SVG writer stamps a
        `<dc:date>` and salts every `<defs>` id PER PROCESS, so the default
        output differs between two renders in the same interpreter. `_save`
        passes `metadata={"Date": None}` and `_RC` pins `svg.hashsalt`; with
        either one removed this test is what goes red.
        """
        # ONE case, rendered twice. (Two make_case() calls would differ: the
        # generator stamps each reading with the wall clock, and that timestamp
        # is real data -- a difference in the input, not in the renderer.)
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        first, second = tmp_path / "a", tmp_path / "b"
        for out in (first, second):
            render_report(result, case.machine, out, pdf=False, case=case, thresholds=thresholds,
                          profile="route")

        assert (first / "report.md").read_text() == (second / "report.md").read_text()
        a_files = charts_mod.chart_files(first)
        b_files = charts_mod.chart_files(second)
        assert [p.name for p in a_files] == [p.name for p in b_files]
        for a, b in zip(a_files, b_files):
            assert _svg_size(a) == _svg_size(b), f"{a.name} changed dimensions between renders"
            assert a.read_bytes() == b.read_bytes(), f"{a.name} is not byte-deterministic"

    def test_figure_dimensions_are_the_fixed_render_size(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The spectrum figure has its own, taller size — V2-WIRE gave it the
        label lanes, and CHARTS-2 item 5 gave it a second panel (the full
        analysed span, so a 2 kHz analysis stops looking like a 300 Hz one). The
        badge, the fault-frequency map and the trend figure keep `_FIGSIZE`, so
        this pins both sizes rather than one.

        In POINTS now, not pixels: an SVG carries the authored size, `figsize *
        72`, where the PNG carried `figsize * 150`. Same claim — the render size
        is fixed — in the unit the format actually records.
        """
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        spectrum = (charts_mod._SPECTRUM_FIGSIZE[0] * 72, charts_mod._SPECTRUM_FIGSIZE[1] * 72)
        assert _svg_size(tmp_path / "charts" / "spectrum_y.svg") == pytest.approx(spectrum)
        assert spectrum == pytest.approx((547.2, 504.0))
        trend = (charts_mod._FIGSIZE[0] * 72, charts_mod._FIGSIZE[1] * 72)
        assert _svg_size(tmp_path / "charts" / "trend.svg") == pytest.approx(trend)
        assert trend == pytest.approx((547.2, 345.6))

    def test_no_llm_is_reachable_from_the_chart_path(self):
        source = Path(charts_mod.__file__).read_text()
        for forbidden in ("anthropic", "client.messages", "ANTHROPIC_API_KEY"):
            assert forbidden not in source


# ── the status badge ─────────────────────────────────────────────────────


class TestStatusBadge:
    def test_zone_report_badge_names_the_zone(self, iso_table, thresholds, rules):
        _case, result = _bpfo(iso_table, thresholds, rules)
        assert charts_mod.status_text(result).startswith("ISO ZONE D — UNACCEPTABLE")

    def test_gate_fail_badge_says_insufficient_data(self, iso_table, thresholds, rules):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert charts_mod.status_text(result).startswith("INSUFFICIENT DATA")

    def test_not_assessable_badge_never_names_a_zone(self, tmp_path, iso_table, thresholds, rules):
        case, result = _not_assessable(iso_table, thresholds, rules)
        assert result.iso.iso_zone == "not_assessable"
        assert "unrated" in charts_mod.status_text(result).lower()
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        md = (tmp_path / "report.md").read_text()
        # The Session A contract holds through the whole evidence layer, alt
        # text included: no ISO-zone language anywhere on an unrated reading.
        assert not re.search(r"ISO\s+Zone|\bZone\s+[A-D]\b", md, re.IGNORECASE)
        assert "0.00 mm/s" not in md


# ── analysis parameters ──────────────────────────────────────────────────


class TestAnalysisParameters:
    def test_table_reports_computed_acquisition_values(self, iso_table, thresholds, rules):
        case, result = _bpfo(iso_table, thresholds, rules)
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds,
                             profile="route")
        assert "## Analysis Parameters" in md
        for expected in (
            # Session INTAKE-HONEST: same computed 2000, now carrying its
            # provenance — this case declares nothing, so the row says the
            # value came from the data.
            "| Spectral lines (N) | 2000 (from data; not declared) |",
            "| Line spacing (Δf) | 0.250 Hz |",
            "| Threshold profile | route |",
            f"| Match tolerance | ±{thresholds['rca']['tolerance_pct']:g}% |",
        ):
            assert expected in md

    def test_unknown_parameters_say_so_instead_of_being_invented(
        self, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules)
        md = render_markdown(result, case.machine, case=case, thresholds=thresholds)
        # Session INTAKE-HONEST: the Window hardcode became an honest
        # absent-statement (declared values print as declared; this case
        # declares nothing). Same property — the value is never invented.
        assert "| Window | not provided — unknown window; amplitudes taken as supplied |" in md
        # Spectrum.kind records which DSP ran, not what the amplitude is measured
        # in — the table must not turn it into a units claim.
        assert "as supplied at upload" in md

    def test_no_case_reports_acquisition_as_not_recorded(self, iso_table, thresholds, rules):
        _case, result = _bpfo(iso_table, thresholds, rules)
        md = render_markdown(result, _machine(iso_table, thresholds), thresholds=thresholds)
        assert "| Spectral lines (N) | not recorded |" in md


# ── signature block ──────────────────────────────────────────────────────


def test_signature_block_precedes_the_draft_footer(iso_table, thresholds, rules):
    case, result = _bpfo(iso_table, thresholds, rules)
    md = render_markdown(result, case.machine, case=case, thresholds=thresholds)
    assert "Reviewed and approved by:" in md
    assert "cert level" in md
    assert md.index("Reviewed and approved by:") < md.index("DRAFT — prepared by automated")
    assert md.rstrip().endswith("DRAFT — prepared by automated analysis, pending analyst review.")


# ── drafted-report splice ────────────────────────────────────────────────


_DRAFT = """# Vibration Survey Report — Synthetic Compressor 01

## Executive Summary

Synthetic Compressor 01 is in ISO Zone D. The committed diagnosis is Bearing
outer-race fault (BPFO) (high confidence).

## Machine Details

| Field | Value |
|---|---|
| Machine | Synthetic Compressor 01 |

---
DRAFT — prepared by automated analysis, pending analyst review.
"""


class TestDraftedSplice:
    def test_badge_lands_under_the_title_and_evidence_before_the_footer(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules)
        write_drafted_report(_DRAFT, result, tmp_path, pdf=False, machine=case.machine,
                             case=case, thresholds=thresholds)
        md = (tmp_path / "report.md").read_text()
        lines = md.splitlines()
        assert lines[0] == "# Vibration Survey Report — Synthetic Compressor 01"
        assert lines[2].startswith("![ISO ZONE D")
        assert md.index("## Spectral Evidence") < md.rindex("DRAFT — prepared by")
        assert md.rstrip().endswith("DRAFT — prepared by automated analysis, pending analyst review.")

    def test_drafted_and_deterministic_reports_carry_the_same_figures(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        drafted, deterministic = tmp_path / "drafted", tmp_path / "deterministic"
        write_drafted_report(_DRAFT, result, drafted, pdf=False, machine=case.machine,
                             case=case, thresholds=thresholds)
        render_report(result, case.machine, deterministic, pdf=False, case=case,
                      thresholds=thresholds)
        assert [p.name for p in charts_mod.chart_files(drafted)] == [
            p.name for p in charts_mod.chart_files(deterministic)
        ]

    def test_blocks_the_draft_already_wrote_are_not_duplicated(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules)
        draft = _DRAFT.replace(
            "---\nDRAFT",
            "## Analysis Parameters\n\n| Parameter | Value |\n|---|---|\n| Window | x |\n\n"
            "Reviewed and approved by: ____\n\n---\nDRAFT",
        )
        write_drafted_report(draft, result, tmp_path, pdf=False, machine=case.machine,
                             case=case, thresholds=thresholds)
        md = (tmp_path / "report.md").read_text()
        assert md.count("## Analysis Parameters") == 1
        assert md.count("Reviewed and approved by:") == 1

    def test_without_a_machine_the_draft_is_written_untouched(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _case, result = _bpfo(iso_table, thresholds, rules)
        write_drafted_report(_DRAFT, result, tmp_path, pdf=False)
        assert (tmp_path / "report.md").read_text() == _DRAFT
        assert charts_mod.chart_files(tmp_path) == []


# ── charts are INTERMEDIATES ─────────────────────────────────────────────


class TestChartsAreIntermediates:
    def test_completion_purge_deletes_every_chart(self, tmp_path, iso_table, thresholds, rules):
        """The webapp keeps only report.pdf (plus a consented trace) at
        completion. Charts are evidence INSIDE that PDF, not artifacts that
        outlive it -- they must not survive the purge."""
        from vib_agent.webapp.jobs import Job
        from vib_agent.webapp.worker import _keep_only_report

        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        (tmp_path / "report.pdf").write_bytes(b"%PDF-1.7\n")
        assert charts_mod.chart_files(tmp_path)  # precondition: charts exist

        _keep_only_report(Job(id="j", job_dir=tmp_path), keep_trace=False)

        assert not (tmp_path / charts_mod.CHARTS_DIRNAME).exists()
        assert charts_mod.chart_files(tmp_path) == []
        assert {p.name for p in tmp_path.iterdir()} == {"report.pdf"}

    def test_charts_survive_the_purge_only_when_the_pdf_never_rendered(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """No PDF means report.md IS the report -- but the charts still go: a
        markdown-only report referencing deleted PNGs is the honest outcome,
        and keeping them would leave upload-derived data on disk."""
        from vib_agent.webapp.jobs import Job
        from vib_agent.webapp.worker import _keep_only_report

        case, result = _bpfo(iso_table, thresholds, rules)
        render_report(result, case.machine, tmp_path, pdf=False, case=case, thresholds=thresholds)
        _keep_only_report(Job(id="j", job_dir=tmp_path), keep_trace=False)
        assert {p.name for p in tmp_path.iterdir()} == {"report.md"}
        assert charts_mod.chart_files(tmp_path) == []


# ── Session V2-WIRE · the v2 figure family (WIRING.md slice W6a) ──────────
#
# The prototype proved these properties with a build check that read the
# geometry off the rendered SVG element. A matplotlib PNG has nowhere to hang a
# `data-` attribute, so `_render_spectrum` publishes what the layout decided on
# the manifest instead, and these read it from there — the artifact, not a
# recomputation that could agree with a renderer that is wrong.


def _geometries(result, machine, out_dir, case, thresholds):
    charts = charts_mod.render_charts(
        result, machine, out_dir, case=case, thresholds=thresholds, fault_labels=FAULT_LABELS
    )
    return {f.key: f.geometry for c in charts.channels for f in c.figures if f.geometry}


class TestFocusWindow:
    def test_focus_window_ignores_the_amplitude_array_entirely(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The window is chosen from COMPUTED frequencies only.

        Choosing it from the data would let the figure crop away a region the
        report is silent about. This is the property that makes the crop safe,
        so it is asserted rather than left to a docstring: scaling every
        amplitude by 1000x, and moving the loudest line to the far end of the
        span, must not move the window by one hertz.
        """
        case, result = _bpfo(iso_table, thresholds, rules)
        before = _geometries(result, case.machine, tmp_path / "a", case, thresholds)

        def _shout(series):
            # BOTH dicts, identically: _channel_spectra dedupes a raw/envelope
            # pair only while the two arrays are equal, so scaling one of them
            # would split every channel into two figures and this test would be
            # measuring the dedup, not the window.
            out = {}
            for axis, spectrum in (series or {}).items():
                amps = [a * 1000.0 for a in spectrum.amplitude]
                amps[-1] = max(amps) * 10.0      # the loudest line, far outside the window
                out[axis] = spectrum.model_copy(update={"amplitude": amps})
            return out

        loud = case.model_copy(update={
            "spectra": _shout(case.spectra),
            "raw_spectra": _shout(case.raw_spectra),
        })
        after = _geometries(result, case.machine, tmp_path / "b", loud, thresholds)

        assert before and set(before) == set(after)
        for key, geo in before.items():
            assert after[key].focus_max == geo.focus_max, key

    def test_a_cropped_axis_always_carries_the_context_strip(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """Nothing is concealed: if the main panel does not show the whole span,
        the strip that does show it is mandatory."""
        case, result = _bpfo(iso_table, thresholds, rules)
        geos = _geometries(result, case.machine, tmp_path, case, thresholds)
        assert geos
        for key, geo in geos.items():
            assert geo.has_context_strip == (geo.focus_max < geo.fmax), key
            assert geo.focus_max <= geo.fmax, key

    def test_the_window_covers_every_vertical_the_figure_may_draw(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """Regression, found by rendering the figure and reading it.

        `_draw_matches` renders a 2x harmonic when the analysis found one, and
        guards it with `<= xmax`. Feeding the window only the matched
        FUNDAMENTALS let a harmonic the report states in words fall outside the
        axis and vanish silently.
        """
        case, result = _bpfo(iso_table, thresholds, rules)
        geos = _geometries(result, case.machine, tmp_path, case, thresholds)
        for match in result.rca.primary_findings:
            if match.freq_hz is None or not match.harmonic_present:
                continue
            geo = geos.get(f"spectrum_{match.axis}")
            if geo is None:
                continue
            assert match.freq_hz * 2 <= geo.focus_max, (
                f"2x harmonic of {match.fault} at {match.freq_hz * 2:.1f} Hz is outside the "
                f"{geo.focus_max:g} Hz window it would be drawn in"
            )


class TestLabelLanes:
    def test_no_marker_label_overprints_another_on_any_fixture(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """Zero collisions, computed from the same geometry the renderer drew
        with — on every fixture, not just the convenient one."""
        for i, (name, build) in enumerate((
            ("bpfo", lambda: _bpfo(iso_table, thresholds, rules)),
            ("trend", lambda: _bpfo(iso_table, thresholds, rules, history=True)),
            ("not_assessable", lambda: _not_assessable(iso_table, thresholds, rules)),
        )):
            case, result = build()
            geos = _geometries(result, case.machine, tmp_path / str(i), case, thresholds)
            assert geos, name
            for key, geo in geos.items():
                assert geo.collisions == 0, f"{name}/{key}: {geo.collisions} label collision(s)"
                assert geo.label_rows <= 3, f"{name}/{key}"


class TestMatchedPeakMarkers:
    def test_harmonic_marker_takes_the_loudest_line_in_tolerance(self, thresholds):
        """PARITY §7.7, and a defect this session found by looking.

        The marker used the bin nearest an arithmetic target, so on
        demo_bpfo_6206 Y it landed in the valley beside its own peak and drew
        the harmonic at a fraction of its real height. Both numbers are real, so
        a number audit passes either way; only one of them is the peak the
        report is talking about.
        """
        from vib_agent.models import Spectrum

        spectrum = Spectrum(
            kind="envelope",
            freq_hz=[100.0, 100.5, 101.0, 101.5, 102.0],
            amplitude=[0.01, 0.02, 0.03, 0.90, 0.02],
        )
        tol = thresholds["rca"]["tolerance_pct"]
        freq, amp = charts_mod._peak_in_window(spectrum, 101.0, tol)
        assert (freq, amp) == (101.5, 0.90)

        # With no configured tolerance it degrades to the previous behaviour —
        # the nearest bin — rather than guessing at a window.
        assert charts_mod._peak_in_window(spectrum, 101.0, None) == (101.0, 0.03)
