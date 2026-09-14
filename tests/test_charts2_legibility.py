"""Session CHARTS-2 — the acceptance: figures an analyst can read.

The brief's item 6 is the session's grade, not a smoke test:

    for every figure rendered from the existing demo trio, compute every text
    artist's bounding box and assert none intersects the spectrum trace
    polyline or another text artist. This is the acceptance; a figure that
    fails it fails the session.

with items 2 and 3 adding the other two halves — nothing below 8 pt at the size
the figure is PRINTED, and nothing placed by a hand-tuned offset.

Why this is measured off the artists and not off the file: a figure is not
wrong in its SVG, it is wrong on a page, and the only thing that knows where a
glyph lands is the renderer that laid it out. `charts.py` decides placement from
exactly these numbers (`_text_size_px`, `_TraceEnvelope`, `_place_block`), so
the renderer and the check read the same geometry — the rule this module has
followed since V2-WIRE's `_lane_collisions` ("read off the geometry the renderer
actually drew with"). A checker that re-derived positions its own way could
agree with a renderer that is wrong.

This is what Part C could not do. Its method note records that every page had to
be *viewed as an image* "because the figure headers are PNG and text extraction
cannot see them" — which is how F-9, a label printed straight through its
neighbour on nine pages of two shipped PDFs, survived to a human read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.synth.generator import make_case, make_history

pytestmark = pytest.mark.skipif(
    not charts_mod.matplotlib_available(), reason="matplotlib not installed ([pdf] extra)"
)

#: A trace this short is a reference line, a leader or an axis spine, not the
#: measured series. The spectra here carry thousands of points.
_TRACE_MIN_POINTS = 50


# ── capturing the figures ────────────────────────────────────────────────


def _render_capturing(monkeypatch, out_dir, result, machine, **kw):
    """Every figure `render_charts_inprocess` builds, as live Figure objects.

    `_save` is wrapped rather than a hook being added to `charts.py`: a figure
    is a local inside its builder and dies with it, and a module-level list of
    them in production code would be both a leak and a lie about what the
    renderer keeps. The wrapper still calls through, so the files this asserts
    about are the files the report gets.
    """
    captured: list[tuple[object, Path]] = []
    real_save = charts_mod._save

    def spy(fig, path):
        captured.append((fig, Path(path)))
        real_save(fig, path)

    monkeypatch.setattr(charts_mod, "_save", spy)
    charts_mod.render_charts_inprocess(result, machine, out_dir, **kw)
    return captured


def _text_artists(fig):
    from matplotlib.text import Text

    return [t for t in fig.findobj(Text)
            if t.get_visible() and (t.get_text() or "").strip()]


def _traces(fig):
    """The measured series on each axes, with the transform that places it."""
    out = []
    for ax in fig.axes:
        for line in ax.get_lines():
            if line.get_linestyle() == "none":
                continue
            if len(line.get_xdata()) < _TRACE_MIN_POINTS:
                continue
            out.append((ax, line))
    return out


def _overlaps(a, b) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1


def figure_problems(fig) -> list[str]:
    """Every way this figure fails the acceptance, named. Empty is a pass."""
    # Measure against ONE renderer. `_save` writes SVG, and that backend lays
    # out at 72 dpi while `get_window_extent` below asks the Agg renderer at the
    # figure's own 150 — mixing the two reports every legend entry as
    # overlapping its neighbour, which is a bug in the CHECK and not in the
    # figure. Re-drawing with Agg first is what makes the two agree.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    width, height = fig.canvas.get_width_height()

    artists = _text_artists(fig)
    boxes = [(t, t.get_window_extent(renderer=renderer)) for t in artists]
    traces = _traces(fig)
    problems: list[str] = []

    for i, (artist, box) in enumerate(boxes):
        label = (artist.get_text() or "")[:40].replace("\n", " ")

        printed_pt = artist.get_fontsize() * charts_mod._PRINT_SCALE
        if printed_pt < charts_mod._MIN_PRINT_PT - 1e-6:
            problems.append(
                f"{label!r} prints at {printed_pt:.2f} pt, below the "
                f"{charts_mod._MIN_PRINT_PT:g} pt floor (item 2)"
            )

        if box.x0 < -0.5 or box.y0 < -0.5 or box.x1 > width + 0.5 or box.y1 > height + 0.5:
            problems.append(f"{label!r} is outside the figure: {_fmt_box(box)}")

        for other, other_box in boxes[i + 1:]:
            if _overlaps(box, other_box):
                problems.append(
                    f"{label!r} {_fmt_box(box)} overlaps "
                    f"{(other.get_text() or '')[:40]!r} {_fmt_box(other_box)}"
                )

        for ax, line in traces:
            if _hits_trace(ax, line, box):
                problems.append(f"{label!r} {_fmt_box(box)} sits on the trace")
                break

    return problems


def _fmt_box(box) -> str:
    return f"[{box.x0:.0f},{box.y0:.0f}..{box.x1:.0f},{box.y1:.0f}]"


def _hits_trace(ax, line, box) -> bool:
    points = ax.transData.transform(list(zip(line.get_xdata(), line.get_ydata())))
    for px, py in points:
        if box.x0 <= px <= box.x1 and box.y0 <= py <= box.y1:
            return True
    return False


# ── fixtures: the cases every figure family comes from ───────────────────


def _bpfo(iso_table, thresholds, rules, *, history: bool = False):
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    if history:
        case = case.model_copy(
            update={"history": make_history(30, 1.2, 5.2, noise_pct=0.12,
                                            cadence="daily", seed=7)}
        )
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _multi_location_point(tmp_path, key, iso_table, thresholds, rules):
    """One measurement point of FIXTURE-1's four-point route sample.

    The brief made this conditional — "and from `outputs/demo_package/
    multi_location/` **if it is on master when you merge**". It was not there at
    plan time; FIXTURE-1 landed it mid-session, and the second law-#18 merge
    brought it in, so the condition is met and these are in.

    Worth more than the extra coverage: the trio is one faulted point, and this
    is a route — a BPFO planted at Compressor DE and nowhere else. That gives
    the placer three HEALTHY points with no matched peaks and so a different
    text population per figure, plus 12,801 lines against the trio's 8,001.
    """
    from scripts.make_multi_sample import DIRECTIONS, POINTS, write_sample
    from vib_agent.adapters.uploads import parse_upload
    from vib_agent.adapters.uploads.common import UploadForm
    from vib_agent.config import load_config
    from vib_agent.webapp import assembly

    import scripts.make_multi_sample as S

    write_sample(tmp_path)
    bearings = load_config("bearings")
    point = next(p for p in POINTS if p.key == key)
    parsed = []
    for suffix, direction, _axis, _label in DIRECTIONS:
        form = UploadForm(machine_alias=S.MACHINE_ALIAS, rpm=S.RPM, iso_group=S.ISO_GROUP,
                          iso_support=S.ISO_SUPPORT, bearing_model=S.BEARING_MODEL)
        case, kind, note = parse_upload(tmp_path / f"{point.key}_{suffix}.csv", form,
                                        bearings_cfg=bearings)
        parsed.append(assembly.ParsedChannel(direction, False, case, kind, note))
    outcome = assembly.merge_channels(parsed, [], iso_table=iso_table, thresholds=thresholds,
                                      rules=rules, speed_tolerance_pct=5.0)
    return outcome.case, run_analysis(outcome.case, iso_table=iso_table,
                                      thresholds=thresholds, rules=rules)


def _demo_trio(tmp_path, iso_table, thresholds, rules):
    """The brief's "existing demo trio", GENERATED rather than read.

    `outputs/demo_package/bpfo_synthetic_fmax2000/` holds the committed copy,
    but law #22 is that a test reads nothing that moves — so this writes its own
    from the same generator (`scripts/make_sample_csv.write_sample`, byte-identical
    on every run, pinned by `tests/test_report2_sample_csv.py`) and parses it
    back through the upload lane the live form uses. That also makes this the
    2 kHz / 300 Hz case E2 item 1 is about: eight thousand lines to Fmax 2000
    with every computed vertical below 170 Hz.
    """
    from scripts.make_sample_csv import write_sample
    from vib_agent.adapters.uploads import parse_upload
    from vib_agent.adapters.uploads.common import UploadForm
    from vib_agent.config import load_config
    from vib_agent.webapp import assembly

    write_sample(tmp_path)
    bearings = load_config("bearings")
    parsed = []
    for name, direction in (("radial_h.csv", "radial_h"), ("radial_v.csv", "radial_v"),
                            ("axial.csv", "axial")):
        form = UploadForm(machine_alias="Synthetic Compressor 01", rpm=1800.0,
                          iso_group="2", iso_support="rigid", bearing_model="6206")
        case, kind, note = parse_upload(tmp_path / name, form, bearings_cfg=bearings)
        parsed.append(assembly.ParsedChannel(direction, False, case, kind, note))
    outcome = assembly.merge_channels(parsed, [], iso_table=iso_table,
                                      thresholds=thresholds, rules=rules)
    return outcome.case, run_analysis(outcome.case, iso_table=iso_table,
                                      thresholds=thresholds, rules=rules)


# ── the acceptance ───────────────────────────────────────────────────────


class TestNothingOverprints:
    """No text over a line, no text over another label — on every figure family."""

    @pytest.mark.parametrize(
        "point", ["motor_de", "motor_nde", "compressor_de", "compressor_nde"]
    )
    def test_every_point_of_the_route_sample(self, point, tmp_path, monkeypatch,
                                             iso_table, thresholds, rules):
        """FIXTURE-1's four-point route sample — the brief's conditional, met."""
        case, result = _multi_location_point(tmp_path, point, iso_table, thresholds, rules)
        figures = _render_capturing(monkeypatch, tmp_path / "out", result, case.machine,
                                    case=case, thresholds=thresholds)
        # 5 since Session REPORT-3: badge + three channels + the page-1
        # figure (`sheet_<axis>.svg`, the same builder on a shorter canvas).
        # The count is raised rather than the figure excluded, precisely so
        # `_assert_clean` runs over it: a page-1 figure that overprints would
        # be the first thing an analyst sees.
        assert len(figures) == 5, [p.name for _f, p in figures]
        _assert_clean(figures)

    def test_the_demo_trio(self, tmp_path, monkeypatch, iso_table, thresholds, rules):
        case, result = _demo_trio(tmp_path, iso_table, thresholds, rules)
        figures = _render_capturing(monkeypatch, tmp_path / "out", result, case.machine,
                                    case=case, thresholds=thresholds)
        # badge + three channels + REPORT-3's page-1 figure
        assert len(figures) == 5, [p.name for _f, p in figures]
        _assert_clean(figures)

    def test_spectra_and_trend(self, tmp_path, monkeypatch, iso_table, thresholds, rules):
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        figures = _render_capturing(monkeypatch, tmp_path, result, case.machine,
                                    case=case, thresholds=thresholds)
        names = {p.name for _f, p in figures}
        assert "trend.svg" in names and "spectrum_y.svg" in names, names
        _assert_clean(figures)

    def test_the_frequency_map_fallback(self, tmp_path, monkeypatch, iso_table,
                                        thresholds, rules):
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        figures = _render_capturing(monkeypatch, tmp_path, result, case.machine,
                                    thresholds=thresholds)  # no case -> no spectra
        names = {p.name for _f, p in figures}
        assert "frequency_map.svg" in names, names
        _assert_clean(figures)

    def test_the_gate_fail_badge(self, tmp_path, monkeypatch, iso_table, thresholds, rules):
        case = make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1)
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        assert result.quality_gate.overall == "fail"
        figures = _render_capturing(monkeypatch, tmp_path, result, case.machine,
                                    case=case, thresholds=thresholds)
        assert [p.name for _f, p in figures] == ["status_badge.svg"]
        _assert_clean(figures)

    def test_a_trend_with_no_severity_to_rate(self, tmp_path, monkeypatch, iso_table,
                                              thresholds, rules):
        """No ISO boundary lines and no zone labels — a different text population
        on the same figure, and the one the placer has least to work with."""
        case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
        sd = case.sensor_data.model_copy(update={"x_velocity_mm_sec": None,
                                                 "y_velocity_mm_sec": None,
                                                 "z_velocity_mm_sec": None})
        case = case.model_copy(update={
            "sensor_data": sd,
            "history": make_history(30, 1.2, 5.2, noise_pct=0.12, cadence="daily", seed=7),
        })
        result = run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)
        figures = _render_capturing(monkeypatch, tmp_path, result, case.machine,
                                    case=case, thresholds=thresholds)
        _assert_clean(figures)


def _assert_clean(figures) -> None:
    failures = []
    for fig, path in figures:
        for problem in figure_problems(fig):
            failures.append(f"{path.name}: {problem}")
    assert not failures, "\n".join(failures)


class TestTheAcceptanceCanFail:
    """A check that cannot fail is decoration. This drives the same three rules
    against figures that deliberately break each one."""

    def _fig(self):
        fig = charts_mod._new_figure((4.0, 3.0))
        return fig, fig.add_axes((0.15, 0.15, 0.8, 0.8))

    def test_overlapping_text_is_caught(self):
        fig, ax = self._fig()
        ax.text(0.5, 0.5, "BPFO 107.0", ha="center", fontsize=12)
        ax.text(0.5, 0.5, "BPFI 163.0", ha="center", fontsize=12)
        assert any("overlaps" in p for p in figure_problems(fig))

    def test_text_on_the_trace_is_caught(self):
        fig, ax = self._fig()
        xs = [i / 200 for i in range(201)]
        ax.plot(xs, [0.5] * len(xs), linewidth=1.6)
        ax.text(0.5, 0.5, "on the line", ha="center", va="center", fontsize=12)
        assert any("sits on the trace" in p for p in figure_problems(fig))

    def test_type_below_the_print_floor_is_caught(self):
        fig, ax = self._fig()
        ax.text(0.5, 0.5, "too small", fontsize=6.0)
        assert any("below the" in p for p in figure_problems(fig))

    def test_a_clean_figure_reports_nothing(self):
        fig, ax = self._fig()
        ax.text(0.1, 0.9, "alone", fontsize=charts_mod._PT_CALLOUT)
        assert figure_problems(fig) == []


class TestThePrintScaleIsMeasured:
    """`_PRINT_SCALE` is the one number the 8 pt floor rests on, and it is a
    measurement of `report.css` — not a constant anyone may round."""

    def test_the_floor_is_stated_at_printed_size(self):
        for name in ("_PT_MARKER", "_PT_CALLOUT", "_PT_CALLOUT_VALUE", "_PT_LEGEND",
                     "_PT_TICK", "_PT_AXIS", "_PT_HEADER", "_PT_HEADER_TITLE", "_PT_BADGE"):
            size = getattr(charts_mod, name)
            printed = size * charts_mod._PRINT_SCALE
            assert printed >= charts_mod._MIN_PRINT_PT - 1e-9, f"{name} prints at {printed:.2f} pt"

    def test_the_scale_matches_the_stylesheet_weasyprint_actually_applies(self, tmp_path):
        """Re-measured here, so a stylesheet change that narrows the figure
        column turns this red instead of silently shrinking every label.

        The figure is authored 7.6 in wide; `report.css` gives A4 with 13 mm
        margins and `figure img { max-width: 100% }`, so it is scaled to fit.
        A REAL file, with a real base_url: weasyprint drops an image it cannot
        load, and a probe that laid out nothing would measure nothing.
        """
        weasyprint = pytest.importorskip("weasyprint")
        css = (Path(charts_mod.__file__).parent / "report.css").read_text()
        authored_px = 7.6 * 96
        (tmp_path / "x.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{7.6 * 72:g}pt" '
            f'height="{7.0 * 72:g}pt" viewBox="0 0 {7.6 * 72:g} {7.0 * 72:g}"></svg>'
        )
        html = (f"<html><head><style>{css}</style></head><body><div class='doc'>"
                f"<figure><div class='plot'><img src='x.svg'></div></figure>"
                f"</div></body></html>")
        page = weasyprint.HTML(string=html, base_url=f"{tmp_path}/").render().pages[0]

        def walk(box):
            yield box
            for child in getattr(box, "children", []) or []:
                yield from walk(child)

        widths = [b.width for b in walk(page._page_box)
                  if getattr(b, "element_tag", None) == "img"]
        assert widths, "the probe laid out no image"
        measured = widths[0] / authored_px
        assert measured == pytest.approx(charts_mod._PRINT_SCALE, abs=0.005), (
            f"report.css now scales the figure to {measured:.4f} of its authored width, "
            f"but charts._PRINT_SCALE says {charts_mod._PRINT_SCALE}. Every point size in "
            f"charts.py is derived from that number."
        )
