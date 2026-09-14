"""Report rendering: all sections present (incl. Diagnosis / "Also
considered" / Recommended Follow-up Measurements), DRAFT footer, a table row
per finding, evidence table for BPFO, renders without error on the
insufficient (gate-fail) case, and PDF rendering when weasyprint/markdown
are installed.
"""

from __future__ import annotations

import shutil

import pytest

from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import ChartSet
from vib_agent.report.generate import render_markdown, render_pdf, render_report
from vib_agent.synth.generator import make_case

_SECTION_HEADERS = (
    "## Executive Summary",
    "## Machine Details",
    "## Data Quality",
    "## Diagnosis",
    "## Recommended Follow-up Measurements",
    "## Recommendations",
    "## Limitations & Confidence Notes",
    "## Coverage — what this analysis did not assess",
)


def test_all_sections_present_and_draft_footer(iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    md = render_markdown(result, case.machine)
    for header in _SECTION_HEADERS:
        assert header in md
    assert md.rstrip().endswith("DRAFT — prepared by automated analysis, pending analyst review.")


def test_diagnosis_and_evidence_table_for_bpfo(iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    md = render_markdown(result, case.machine)
    assert "Bearing outer-race fault (BPFO)" in md
    assert "high confidence" in md.lower() or "confidence: high" in md
    # Evidence table has computed vs observed Hz
    assert "Computed (Hz)" in md and "Observed (Hz)" in md
    assert "107." in md  # the BPFO frequency appears in the evidence row


def test_also_considered_line_for_differential(iso_table, thresholds, rules):
    case = make_case("looseness", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    md = render_markdown(result, case.machine)
    assert "Also considered" in md


def test_renders_without_error_on_insufficient_case(iso_table, thresholds, rules):
    case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    md = render_markdown(result, case.machine)
    assert "Insufficient data" in md
    assert "## Recommended Follow-up Measurements" in md
    # the recommendation content itself must appear (not "None")
    assert "Re-measure" in md or "Verify" in md or "Reduce" in md


def _not_assessable_case(iso_table, thresholds):
    # A real BPFO case (spectra intact, so RCA still commits a finding) with the
    # velocity nulled out -> acceleration-only, severity not_assessable.
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    sd = case.sensor_data.model_copy(
        update={"x_velocity_mm_sec": None, "y_velocity_mm_sec": None, "z_velocity_mm_sec": None}
    )
    return case.model_copy(update={"sensor_data": sd})


def test_not_assessable_renders_coverage_block_and_no_zone_language(iso_table, thresholds, rules):
    import re

    case = _not_assessable_case(iso_table, thresholds)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    assert result.iso.iso_zone == "not_assessable"
    md = render_markdown(result, case.machine)
    assert "## Severity & Coverage" in md
    assert "unrated — ISO severity requires velocity data" in md
    assert "Velocity measurement per ISO 20816" in md
    # HARD: zero ISO-zone language, and no coerced 0.00 mm/s clean-bill number.
    assert not re.search(r"ISO\s+Zone|\bZone\s+[A-D]\b", md, re.IGNORECASE)
    assert "0.00 mm/s" not in md


def test_velocity_report_has_no_coverage_block(iso_table, thresholds, rules):
    # A normal velocity-bearing report is unchanged: no Severity & Coverage block,
    # and it DOES name the ISO zone.
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    md = render_markdown(result, case.machine)
    assert "## Severity & Coverage" not in md
    assert "ISO Zone" in md


def test_render_report_writes_markdown_and_json(tmp_path, iso_table, thresholds, rules):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    written = render_report(result, case.machine, tmp_path, pdf=False)
    assert written["markdown"].exists()
    assert written["analysis_json"].exists()
    assert "pdf" not in written


def _pdf_engine_available() -> bool:
    """`_try_render_pdf` has TWO paths — markdown+weasyprint, else pandoc.
    Probing only the first with importorskip is wrong twice over: it skips a
    machine where pandoc alone works, and it ERRORS on a machine where
    weasyprint is installed without its native pango/cairo libs (it imports
    partially, then raises OSError, not ImportError — see CLAUDE.md
    'Environment'). The product code catches exactly that; so must the test."""
    try:
        import markdown  # noqa: F401
        import weasyprint  # noqa: F401

        return True
    except Exception:  # noqa: BLE001 -- ImportError AND the OSError case above
        pass
    return shutil.which("pandoc") is not None


def test_pdf_rendering_when_available(tmp_path, iso_table, thresholds, rules):
    if not _pdf_engine_available():
        pytest.skip("neither weasyprint nor pandoc is available")
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
    written = render_report(result, case.machine, tmp_path, pdf=True)
    assert "pdf" in written
    assert written["pdf"].exists()


def test_pdf_embeds_the_evidence_figures(tmp_path, iso_table, thresholds, rules):
    """Session H: relative chart paths only resolve because the weasyprint path
    passes base_url and the pandoc path passes --resource-path. Without those
    every figure silently drops out of the PDF while the markdown still looks
    right — so assert the figures actually landed in the document.

    The CLAIM is Session H's and is unchanged. The MEASUREMENT changed in
    Session CHARTS-2: it counted `/Image` XObjects, which is how a 150 dpi PNG
    arrives in a PDF, and the figures are SVG now — weasyprint draws those as
    vector operators, so a perfectly embedded figure contributes zero `/Image`
    objects and the old assertion read the success case as a total failure.

    What is measured instead is the decompressed content stream of the same
    document rendered twice: once the ordinary way, once with an EMPTY manifest
    handed to `render_pdf`. A dropped figure is exactly the case where those two
    documents come out alike, so the gap between them IS what Session H wanted
    to see.

    Measured on this document and this engine: 1,725,500 bytes of drawing with
    the figures against 475,056 without, i.e. 3.6x. A DROPPED figure scores
    exactly 1.0x — weasyprint contributes nothing at all for an image it could
    not resolve (61 kB embedded against 174 bytes dropped, measured on one
    figure in isolation). The 2x floor therefore sits between the two states and
    close to neither. It is a ratio rather than a byte count so that it does not
    drift when the report's own inline evidence graphics grow — they are most of
    the 475 kB baseline.

    `figures=False` is deliberately NOT the baseline: it suppresses the figures
    in the markdown only, and `render_report`'s PDF step then re-renders the
    charts because no manifest was passed to it (`generate.py` :2065). The two
    PDFs come out byte-identical, which would have made this assertion vacuous
    in the one direction that matters.
    """
    if not _pdf_engine_available():
        pytest.skip("neither weasyprint nor pandoc is available")
    pytest.importorskip("matplotlib")
    pypdf = pytest.importorskip("pypdf")
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)

    def _drawing_bytes(pdf_path) -> int:
        return sum(len(page.get_contents().get_data())
                   for page in pypdf.PdfReader(str(pdf_path)).pages)

    with_figures = render_report(result, case.machine, tmp_path / "figs", pdf=True,
                                 case=case, thresholds=thresholds)
    nofigs = tmp_path / "nofigs"
    nofigs.mkdir()  # render_pdf writes its intermediate markdown into an existing dir
    without = render_pdf(result, case.machine, nofigs, charts=ChartSet(),
                         case=case, thresholds=thresholds)
    drawn, plain = _drawing_bytes(with_figures["pdf"]), _drawing_bytes(without)
    assert drawn > plain * 2, (
        f"the figures did not reach the PDF: {drawn} bytes of drawing with them, "
        f"{plain} without — a dropped figure leaves these two documents alike"
    )


class TestThePandocFallbackRefusesAFigurelessPdf:
    """Session REPORT-3 item 10, closing CHARTS-2 F-6.

    Every figure has been an SVG since CHARTS-2, and `pandoc --pdf-engine=
    tectonic` cannot place one: it drops the image and renders the document
    around it. The old code PRINTED a notice and returned the path, so a caller
    got back a report.pdf that looked complete and carried no evidence — the
    worst of the three outcomes, because a missing PDF is noticed and a
    figure-less one is not.

    Nothing in production takes this path (`deploy/setup_server.sh:49-50`
    installs weasyprint's native deps and says pandoc/tectonic is "the
    documented fallback, not installed here"). It is reachable here, which is
    why it can be tested at all.
    """

    def _md_with_figures(self):
        return (
            "# Vibration Survey Report — X\n\n## Spectral Evidence\n\n"
            "![Envelope spectrum, Y axis](charts/spectrum_y.svg)\n\n"
            "_Envelope spectrum (as supplied), Y axis._\n"
        )

    def test_it_raises_and_names_weasyprint(self, tmp_path):
        from vib_agent.report.generate import FigurelessPdfRefused, _pdf_from_markdown

        # Force the weasyprint route to decline, so the fallback is reached.
        import vib_agent.report.generate as gen
        real = gen._pdf_from_html
        gen._pdf_from_html = lambda *a, **k: None
        try:
            with pytest.raises(FigurelessPdfRefused) as excinfo:
                _pdf_from_markdown(self._md_with_figures(), tmp_path)
        finally:
            gen._pdf_from_html = real
        message = str(excinfo.value)
        assert "weasyprint" in message
        assert "SVG" in message
        # A refusal leaves nothing behind — not even the intermediate.
        assert not (tmp_path / "report.pdf").exists()
        assert not (tmp_path / "_report_for_pdf.md").exists()

    def test_a_document_with_no_figures_still_renders_through_pandoc(self, tmp_path):
        """The refusal NARROWS the old behaviour rather than replacing it: there
        is nothing to lose in a figure-less document, so nothing is refused."""
        import vib_agent.report.generate as gen
        from vib_agent.report.generate import _pdf_from_markdown

        real = gen._pdf_from_html
        calls: list[Path] = []
        gen._pdf_from_html = lambda *a, **k: None
        real_pandoc = gen._pandoc_to_pdf
        gen._pandoc_to_pdf = lambda source, out_dir, pdf_path, *, fmt: calls.append(pdf_path)
        try:
            _pdf_from_markdown("# Report\n\nNo figures here.\n", tmp_path)
        finally:
            gen._pdf_from_html = real
            gen._pandoc_to_pdf = real_pandoc
        assert calls, "pandoc was never reached for a figure-less document"

    def test_the_refusal_maps_to_an_already_ratified_wire_code(self):
        """Wire law #5 without a new ERROR_TAXONOMY row: `webapp/worker.py` has
        no try/except around its render calls, so this reaches
        `app.py::_run_guarded` and becomes `internal_error` — the same mapping
        Session RENDER-PROC made for `RenderChildCrashed`."""
        from vib_agent.report.generate import FigurelessPdfRefused

        assert issubclass(FigurelessPdfRefused, RuntimeError)
        assert not issubclass(FigurelessPdfRefused, (KeyboardInterrupt, SystemExit))
