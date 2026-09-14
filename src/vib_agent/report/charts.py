"""Deterministic evidence charts for the survey report (Session H).

Every figure here is rendered from values the deterministic pipeline already
computed — the spectrum arrays on the Case, the PeakSet-derived FaultMatch
frequencies on the AnalysisResult, the trend series, the configured bearing
geometry. **No LLM is involved in any chart**, and nothing here recomputes a
diagnosis: charts draw what pdm_core decided, they never decide.

Determinism is a hard requirement (Session H item 5): fixed matplotlib style,
fixed figure size, no creation metadata, no timestamps, no randomness. The same
AnalysisResult renders byte-identical files. Since Session CHARTS-2 those files
are SVG rather than 150 dpi PNG, so nothing rasterizes on the way to the page;
what keeps SVG deterministic is `svg.hashsalt` in `_RC` plus `metadata` in
`_save`, and neither is optional.

Legibility is a requirement too, and it is stated at the size the figure is
PRINTED rather than the size it is authored (`_PRINT_SCALE`): nothing on any
figure reaches the page below 8 pt, no text overlaps another text, and no text
overlaps the trace. Those are not style preferences — they are asserted, per
figure, in `tests/test_charts2_legibility.py`.

Reachability
------------
`render_charts` takes the AnalysisResult (always available at report-render
time) and an OPTIONAL Case. With the Case, the actual spectrum arrays and
trend series are plotted. Without it — the two webapp `render_report` call
sites that Session H could not touch (see SESSION_H_SUMMARY.md, packet 1) —
every chart degrades to what the AnalysisResult alone supports: a
fault-frequency map (computed fault frequencies, shaft orders, tolerance
bands, and the peaks the analysis matched) and a two-point trend summary.
Amplitudes are never invented; the fallback figures simply have no amplitude
axis and say so.

matplotlib is an optional dependency (the `[pdf]` extra). When it is absent
`render_charts` returns an empty ChartSet and the report renders exactly as
it did before Session H — charts are evidence, never a hard requirement.

Thread safety
-------------
`render_charts` is called from webapp worker threads, so the backend is chosen
once and figures are built through the object API under one process-wide lock.
That lock is NOT this module's — it lives in `report/render_lock.py` and is
shared with the PDF engine, which segfaults under concurrency for the same
reason and runs on the same threads. See the block above `_init_matplotlib` for
what each half is for.
"""

from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from vib_agent.models import AnalysisResult, Case, MachineMeta, Spectrum
from vib_agent.report.render_lock import serializes_render

CHARTS_DIRNAME = "charts"

# ─────────────────────────────────────────────────────────────────────────
# Palette — the ui-v1 tokens (webapp/static/style.css :root), extended to a
# 4-slot categorical set. Validated colorblind-safe against a white surface
# (lightness band / chroma floor / deutan+tritan adjacent separation /
# normal-vision floor / 3:1 contrast all PASS).
#
# Status hues (zoneA/zoneC/zoneD) are RESERVED for the status badge and are
# never reused as a series color.
# ─────────────────────────────────────────────────────────────────────────
C_TRACE = "#0B63C5"    # --trace: the measured spectrum / trend series
C_FAULT = "#A6480E"    # computed bearing fault frequencies (BPFO/BPFI/BSF/FTF)
C_MATCH = "#6D3FA8"    # peaks the analysis actually matched
C_SHAFT = "#0F8E80"    # shaft-order gridlines (1x/2x/3x)
C_INK = "#101816"      # --ink: all text
C_MUTED = "#5A6662"    # --mut: reference lines, secondary text
C_GRID = "#D8DCD7"     # --line: grid + axes

_ZONE_GOOD = "#2E7D46"   # --zoneA
_ZONE_WARN = "#8A5A12"   # --zoneC
_ZONE_CRIT = "#B3372B"   # --zoneD

_STATUS_STYLE: dict[str, tuple[str, str]] = {
    # zone letter -> (fill colour, plain-language qualifier)
    "A": (_ZONE_GOOD, "GOOD"),
    "B": (_ZONE_GOOD, "ACCEPTABLE"),
    "C": (_ZONE_WARN, "UNSATISFACTORY"),
    "D": (_ZONE_CRIT, "UNACCEPTABLE"),
}

_SPECTRUM_KIND_LABEL: dict[str, str] = {
    "raw_acceleration": "Acceleration spectrum",
    "velocity": "Velocity spectrum",
    "envelope": "Envelope spectrum",
}

_FIGSIZE = (7.6, 4.8)
_BADGE_FIGSIZE = (7.6, 0.72)
_DPI = 150
_HEADER_TOP = 0.70  # axes top, leaving room for the field-report header block

# ── the spectrum figure's own geometry (Session V2-WIRE, WIRING.md slice W6a) ──
# The spectrum figure is taller than the others because it seats things they do
# not have: the de-collided label lanes, and — since Session CHARTS-2 — a second
# panel. The fault-frequency map and the trend figure keep _FIGSIZE.
#
# ONE height for every spectrum figure, whether or not it carries a zoom panel.
# A figure that changed height with its content would set two different type
# scales on facing pages of the same report; the single-panel case spends the
# spare inches on a taller panel instead.
_SPECTRUM_FIGSIZE = (7.6, 7.0)

#: Session REPORT-3 — the SAME figure on a shorter canvas, for page 1.
#:
#: Page 1 is the fault sheet, and it has to seat the machine line, the health
#: line, the fault and its evidence, the decision, the recommendation, the
#: escalation trigger and a work-order field as well as a figure. Measured on
#: the A4 page this product prints (269 mm of content height): the full figure
#: is 174 mm of it and the sheet's text is 130 mm, so the two cannot share a
#: page and weasyprint pushes the figure whole onto page 2 — which is the one
#: thing page 1 must not do.
#:
#: The WIDTH is unchanged, which is the point: `_PRINT_SCALE` is a function of
#: width alone, every point size in this module is written as the size it
#: PRINTS, and the vertical budget below is fixed in INCHES. So a shorter canvas
#: shortens the two PANELS and changes no type size anywhere — the 8 pt floor
#: CHARTS-2 measured still holds, and `tests/test_charts2_legibility.py` runs
#: its whole checker over this variant to say so rather than to assume it.
#: MEASURED, and the reason page 1 does not get the two-panel figure. The
#: two-panel's FIXED furniture — header band, marker lanes, two x-axes, the
#: panel gap, the legend — is 3.88 in on the demo trio's y channel, before
#: either panel has any height at all. Page 1 has ~99 mm (≈ 4.2 in authored)
#: once the sheet's text has had its share, so a two-panel figure there renders
#: its panels at **0.21 in each** — measured, not estimated. A sliver of a plot
#: is not the figure the brief asked for; a single legible panel is.
#:
#: So the page-1 variant is the SAME builder in `compact` mode: the zoom window
#: — which is where the fault markers are and the only panel that is ever
#: direct-labelled — on its own, without the legend. The full two-panel figure
#: is unchanged and is what the Evidence appendix still shows for every channel.
_SHEET_FIGSIZE = (7.6, 3.5)

# Vertical budget, in INCHES from the top of the figure. Inches rather than
# figure fractions because the header block is a fixed physical size and must
# not stretch when the figure grows.
_HEADER_BAND_IN = 1.00      # title line + ONE row of measured header cells
_LANE_H_IN = 0.23           # one lane of horizontal marker labels
_LANE_GAP_IN = 0.07         # lanes to axes
_XAXIS_IN = 0.56            # x ticks + "Frequency (Hz)"
_PANEL_GAP_IN = 0.18        # full-span panel to the zoom panel's lane block
_LEGEND_ROW_IN = 0.21       # one wrapped row of legend entries
_LEGEND_PAD_IN = 0.10
_LEGEND_COLS = 2            # 3 put "Amplitude floor 0.1156 (12.73x mean)" into
                            # its neighbour; the entries are sentences, not words
_BOTTOM_IN = 0.10
_AXES_LEFT = 0.085
_AXES_WIDTH = 0.895
#: The fault-frequency map's y axis is labelled in words, so it needs a wider
#: gutter than the numeric axes do. See `_render_frequency_map`.
_MAP_AXES_LEFT = 0.125

# The focus window (C1). Margin past the last computed frequency, the smallest
# window we will ever crop to, and the point past which cropping buys nothing.
_FOCUS_MARGIN = 1.18
_FOCUS_FLOOR_FRAC = 0.12
_FOCUS_FULL_FRAC = 0.90

# ─────────────────────────────────────────────────────────────────────────
# Type size — an 8 pt floor at the size the figure is actually PRINTED
# (Session CHARTS-2, item 2)
#
# Every size in this module used to be an absolute point size on a 7.6 in
# figure, which is not the size anyone reads. `report.css` sets A4 portrait with
# 13 mm side margins and `figure img { max-width: 100% }`, so the figure is
# scaled down to fit the column before it is printed.
#
# MEASURED, not derived from the CSS by hand: laying the real `report.css` out
# through weasyprint 69.0 and reading the used width off the box tree gives
#
#     <img> width = 669.43 px   against 7.6 in = 729.60 px authored
#     scale = 669.43 / 729.60 = 0.9175
#
# so a label set at 7.6 pt here reached the page at 6.97 pt. Sizes are therefore
# written as the size they PRINT at and converted once, and the acceptance test
# re-derives the same product from the artists themselves.
# ─────────────────────────────────────────────────────────────────────────

#: The figure's printed width / its authored width. See the block above.
_PRINT_SCALE = 0.9175

#: Nothing on any figure prints below this. Item 2's floor.
_MIN_PRINT_PT = 8.0


def _pt(printed_pt: float) -> float:
    """An on-figure point size that PRINTS at `printed_pt`."""
    return printed_pt / _PRINT_SCALE


_PT_MARKER = _pt(8.0)            # lane labels: "BPFO 107.0"
_PT_CALLOUT = _pt(8.5)           # matched-peak label
_PT_CALLOUT_VALUE = _pt(8.0)     # matched-peak "107.25 Hz (3.58x)"
_PT_LEGEND = _pt(8.0)
_PT_TICK = _pt(8.0)
_PT_AXIS = _pt(8.5)
_PT_HEADER = _pt(8.0)            # the header row's keys and values
_PT_HEADER_TITLE = _pt(11.0)
_PT_BADGE = _pt(11.5)

#: Lane assignment pads labels by this many pixels before calling them clear.
_LANE_PAD_PX = 12.0

#: Two text boxes closer than this (in pixels) count as touching. Zero would
#: pass on labels that share an edge, which reads as overprinted on paper.
_TEXT_PAD_PX = 2.0


# ─────────────────────────────────────────────────────────────────────────
# Chart manifest — what the template renders
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FigureGeometry:
    """What the spectrum layout decided, published on the artifact.

    Session V2-WIRE. The prototype's G0 check reads geometry off the rendered
    element rather than recomputing it, so a check can never agree with a
    renderer that is wrong; the same idea here, carried on the manifest. A
    matplotlib PNG has nowhere to hang a `data-` attribute, so the numbers ride
    on the ChartFigure instead.
    """

    fmax: float
    focus_max: float
    has_context_strip: bool
    label_rows: int
    collisions: int
    n_markers: int
    n_matched: int

    @property
    def focus_pct(self) -> float:
        return 100.0 * self.focus_max / self.fmax if self.fmax else 100.0


@dataclass(frozen=True)
class ChartFigure:
    """One written PNG plus the presentation-ready text around it."""

    key: str
    rel_path: str
    title: str
    alt: str
    caption: str = ""
    header: tuple[tuple[str, str], ...] = ()
    geometry: "FigureGeometry | None" = None


@dataclass(frozen=True)
class ChannelFigures:
    """The figure pair for one measured channel (spectrum + envelope), in the
    order a field report prints them."""

    axis: str
    label: str
    figures: tuple[ChartFigure, ...]


@dataclass
class ChartSet:
    status: ChartFigure | None = None
    channels: list[ChannelFigures] = field(default_factory=list)
    trend: ChartFigure | None = None
    available: bool = False
    #: Session REPORT-3 — the page-1 figure: the channel the committed call was
    #: made on, drawn on `_SHEET_FIGSIZE`. A SEPARATE figure rather than a
    #: reference into `channels`, because page 1 and the Evidence appendix need
    #: the same content at two different heights and a figure that changed size
    #: with its context would set two type scales in one document.
    sheet: ChartFigure | None = None

    @property
    def has_channel_figures(self) -> bool:
        return any(c.figures for c in self.channels)


# ─────────────────────────────────────────────────────────────────────────
# ChartSet across the process boundary (Session RENDER-PROC)
#
# Figures are built in a child process now (see `report/render_proc.py`), so
# the manifest has to survive a JSON round trip. It carries no image bytes --
# only relative paths and presentation text -- so this is a small, total
# encoding rather than a serialization layer.
#
# `dataclasses.asdict` is safe here: every field is a scalar, a tuple of
# scalars, or another frozen dataclass, and `focus_pct` is a PROPERTY, not a
# field, so it is neither written nor needed. The one thing JSON loses is
# tuple-ness -- `header` and `figures` come back as lists -- and the decoder
# re-tuples both, because `ChartFigure` and `ChannelFigures` are frozen
# (hashable) and the templates index them positionally.
# ─────────────────────────────────────────────────────────────────────────


def chartset_to_dict(charts: "ChartSet") -> dict[str, Any]:
    """`ChartSet` -> a JSON-safe dict. Inverse of `chartset_from_dict`."""
    from dataclasses import asdict

    return asdict(charts)


def _figure_from_dict(d: dict[str, Any] | None) -> "ChartFigure | None":
    if d is None:
        return None
    geometry = d.get("geometry")
    return ChartFigure(
        key=d["key"], rel_path=d["rel_path"], title=d["title"], alt=d["alt"],
        caption=d.get("caption", ""),
        header=tuple(tuple(row) for row in d.get("header", ())),
        geometry=FigureGeometry(**geometry) if geometry is not None else None,
    )


def chartset_from_dict(d: dict[str, Any]) -> "ChartSet":
    """A JSON-decoded dict -> `ChartSet`. Inverse of `chartset_to_dict`."""
    return ChartSet(
        status=_figure_from_dict(d.get("status")),
        channels=[
            ChannelFigures(
                axis=c["axis"], label=c["label"],
                figures=tuple(_figure_from_dict(f) for f in c.get("figures", ())),
            )
            for c in d.get("channels", ())
        ],
        trend=_figure_from_dict(d.get("trend")),
        available=bool(d.get("available", False)),
        sheet=_figure_from_dict(d.get("sheet")),
    )


def matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
    except Exception:  # noqa: BLE001 -- a partially-installed backend must degrade, not crash
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────
# Deterministic style, backend selection, and thread discipline
#
# Session CHARTS-AGG. Two rules live here and they answer two different
# failures. Both matter because `render_charts` is reached from webapp worker
# THREADS (`webapp/app.py` runs `worker.process_job` through `asyncio.to_thread`
# under a `worker_concurrency` semaphore, and documents that an abandoned thread
# cannot be interrupted, so transiently there can be more than that).
#
# 1. THE BACKEND IS SELECTED ONCE, and only while pyplot has not been imported.
#    `matplotlib.use()` on an already-imported pyplot delegates to
#    `switch_backend`, which closes every figure in the process — reaching into
#    figures another thread may still be drawing. Guarding on `sys.modules`
#    means that branch can never be taken from here. This used to be a
#    `force=True` call issued once per figure; SESSION_CHARTSAGG.md records what
#    that did and did not actually do on matplotlib 3.11.
#
# 2. FIGURES ARE BUILT THROUGH THE OBJECT API and drawn one at a time. pyplot is
#    never imported by this module, so no process-global registry holds our
#    figures and there is nothing for anyone else's `close("all")` to free. That
#    removes the shared REGISTRY but not the shared DRAW state: matplotlib does
#    not support drawing from several threads at once, and the crash this
#    session closes was inside `Figure.add_axes` on a figure its own thread had
#    only just created (PROD_READINESS §7). The lock is what makes that safe;
#    the object API is what keeps everyone else away from our figures.
#
# Determinism is unaffected by either: the same AnalysisResult still renders
# byte-identical PNGs, which the suite pins by rendering it twice — and, since
# this session, by rendering it from four threads at once.
#
# Session RENDER-SERIAL moved the lock out of this module. It is now the ONE
# lock in `report/render_lock.py`, shared with the PDF engine — weasyprint
# segfaults under concurrency too (PROD_READINESS §7 F-1), it runs on these same
# worker threads moments after the figures, and a thread drawing and a thread
# laying out a page have to exclude each other, not merely their own kind. What
# is written here is unchanged; only which lock enforces it.
# ─────────────────────────────────────────────────────────────────────────

#: Guards the once-only backend selection. Always taken WHILE the render lock is
#: held and never the other way round, so the two cannot deadlock.
_INIT_LOCK = threading.Lock()
_backend_selected = False

#: rcParams applied to every figure. Fixed style is a determinism requirement.
_RC: dict[str, Any] = {
    "figure.dpi": _DPI,
    "savefig.dpi": _DPI,
    "font.family": "DejaVu Sans",  # matplotlib-bundled: identical everywhere
    "font.size": _PT_TICK,
    "axes.titlesize": _PT_HEADER_TITLE,
    "axes.labelsize": _PT_AXIS,
    "axes.edgecolor": C_GRID,
    "axes.labelcolor": C_INK,
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": C_GRID,
    "grid.linewidth": 0.5,
    # Light (item 2). The grid is a reading aid under the trace, not a layer of
    # the drawing: at 0.9 on #D8DCD7 it competed with the amplitude-floor and
    # spectrum-mean reference lines, which are the same value of grey.
    "grid.alpha": 0.55,
    "xtick.color": C_MUTED,
    "ytick.color": C_MUTED,
    "xtick.labelsize": _PT_TICK,
    "ytick.labelsize": _PT_TICK,
    "legend.fontsize": _PT_LEGEND,
    "legend.frameon": True,
    "legend.framealpha": 1.0,
    "legend.edgecolor": C_GRID,
    "figure.facecolor": "#FFFFFF",
    "axes.facecolor": "#FFFFFF",
    "savefig.facecolor": "#FFFFFF",
    "path.simplify": False,  # simplification is resolution-dependent
    # ── SVG determinism (Session CHARTS-2, item 1) ──────────────────────────
    # matplotlib's SVG writer is NOT deterministic by default, and neither of
    # the two reasons is the one the PNG path had to defend against. Measured on
    # matplotlib 3.11.1, two renders of one figure differ in:
    #   1. a `<dc:date>` stamp  -> suppressed by `metadata={"Date": None}` in
    #      `_save`, the SVG spelling of the PNG path's `{"Software": None}`;
    #   2. the `id=` of every `<defs>` path, which is salted PER PROCESS unless
    #      `svg.hashsalt` is set -> pinned here.
    # With both, two renders are byte-identical, which is what keeps
    # `tests/test_charts_threading.py`'s four-thread byte-equality meaningful.
    "svg.hashsalt": "vib-agent-charts",
    # `svg.fonttype` is left at its "path" default DELIBERATELY. "none" would
    # keep real <text> in the SVG (and so make figure text extractable from the
    # PDF, which is how F-9 hid from Part C), but it makes weasyprint resolve
    # "DejaVu Sans" through fontconfig at PDF time — and matplotlib's bundled
    # copy is not registered there. The metrics this module measured would then
    # not be the metrics that get printed, which is the one thing this session
    # exists to guarantee. See outputs/SESSION_CHARTS2.md.
}


def _init_matplotlib() -> None:
    """Select the Agg backend exactly once, before pyplot can be imported.

    Deliberately at first use rather than at module import: matplotlib is the
    optional `[pdf]` extra and importing `report.charts` must keep working
    without it (`matplotlib_available`, the --no-llm install). Idempotent and
    double-checked, so N threads arriving together still select once.
    """
    global _backend_selected
    if _backend_selected:
        return
    with _INIT_LOCK:
        if _backend_selected:
            return
        import matplotlib

        if "matplotlib.pyplot" not in sys.modules:
            # Never forced. With pyplot unimported this only writes the rcParam,
            # so the figure-closing switch path is unreachable; and if something
            # else HAS imported pyplot, its backend is not ours to change — we
            # bind our own Agg canvas per figure in `_new_figure` regardless.
            matplotlib.use("Agg", force=False)
        _backend_selected = True


#: Hold the process-wide render lock for the whole call — the shared one in
#: `report/render_lock.py`, not a lock of this module's own. Applied to every
#: function here that builds a figure AND to `render_charts` itself, so a
#: document render takes it once at its entry point and the builders inside
#: pass straight through; a caller that reaches `render_charts` directly still
#: gets it. matplotlib is not thread-safe and `render_charts` runs on webapp
#: worker threads, which is what makes concurrent jobs safe rather than lucky.
_serialized = serializes_render


def _new_figure(figsize: tuple[float, float]):
    """A standalone Agg figure — the only way a figure is created in here.

    Called only from a `@_serialized` function, which is what lets the rcParams
    update below be safe: matplotlib's rcParams are process-global, and applying
    them per figure (as this module always has) would otherwise race a figure
    another thread is drawing. `tests/test_render_serial.py` pins that "only",
    by syntax tree rather than by trust.
    """
    _init_matplotlib()
    import matplotlib
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    matplotlib.rcParams.update(_RC)
    fig = Figure(figsize=figsize)
    FigureCanvasAgg(fig)  # savefig() and canvas.get_renderer() both need one
    return fig


#: The image format every figure is written in. SVG since Session CHARTS-2
#: (item 1): the figures were 150 dpi rasters scaled down to 92 % of their
#: authored width by `report.css`, so every glyph on every figure was resampled
#: on its way to the page. weasyprint draws an SVG as vector operations, so
#: nothing rasterizes. See `_RC` for what makes the output deterministic.
CHART_FORMAT = "svg"


def _save(fig, path: Path) -> None:
    """Write the figure with no creation metadata — byte-determinism.

    Nothing global holds `fig` — the object API registers a figure nowhere — so
    there is nothing to close: it dies with the last reference to it.

    `{"Date": None}` is the SVG writer's spelling of the PNG path's
    `{"Software": None}`; the salt that makes the rest of the document stable is
    in `_RC`. `"Software"` is not a key the SVG writer accepts.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, format=CHART_FORMAT, metadata={"Date": None})


#: The header block's geometry in INCHES from the top of the figure. Inches, not
#: figure fractions, because the header is a fixed physical size and must not
#: stretch when the figure grows.
_HEADER_TITLE_IN = 0.1575
_HEADER_ROW0_IN = 0.5600
_HEADER_ROW_STEP_IN = 0.2975

#: Gap between one header cell and the next, in figure fractions, and the floor
#: it may be squeezed to before a cell is dropped instead.
_HEADER_GAP = 0.030
_HEADER_GAP_MIN = 0.012
_HEADER_LEFT = 0.012
_HEADER_RIGHT = 0.994

#: Gap between a header KEY and its value, in figure fractions (~4 px at 1140).
#: Explicit rather than carried by a trailing space in the key: a text extent
#: does not account for trailing whitespace the same way the advance does, so
#: measuring `"Speed: "` and advancing by exactly that width put the value's box
#: a fraction of a pixel inside the key's. Invisible on the page, but this
#: session's whole claim is that the boxes do not touch, and a claim with a
#: rounding exception is not the claim.
_HEADER_KV_GAP = 0.0035


def _header_block(fig, entries: Sequence[tuple[str, str]], title: str) -> None:
    """The field-report header: a title line plus key/value cells laid out at
    MEASURED widths, mirroring a CSI-ecosystem plot header block.

    Session CHARTS-2, closing Part C's F-9. This laid four cells per row at a
    hard-fixed `0.988 / 4` pitch and put each value at
    `x + 0.0075 * (len(key) + 2)` — a character-count estimate of a proportional
    face, in a module that has measured real text widths since V2-WIRE
    (`_text_width_px`, 200 lines below). REPORT-2 then made the Spectrum value
    longer than a column, and `"Velocity spectrum (mm/s RMS)"` printed straight
    through `"Δf:"` on every spectrum figure of both 2 kHz PDFs. Nothing
    measured, so nothing noticed: `Δ` alone makes `len(key)` a bad proxy.

    So the cells are advanced by the width the renderer reports for the text
    actually being drawn. If the row still would not fit, the inter-cell gap is
    squeezed to `_HEADER_GAP_MIN` and any cell that STILL does not fit is
    dropped rather than drawn over its neighbour — a missing field is a visible
    absence, an overprinted one is a misreading. The caller orders `entries` by
    what it can least afford to lose.
    """
    height_in = fig.get_figheight()

    def _y(inches_from_top: float) -> float:
        return 1.0 - inches_from_top / height_in

    fig.text(_HEADER_LEFT, _y(_HEADER_TITLE_IN), title, ha="left", va="top",
             fontsize=_PT_HEADER_TITLE, color=C_INK, fontweight="bold")
    if not entries:
        return

    fig_w_px = fig.get_figwidth() * fig.dpi
    cells = [
        (
            key,
            value,
            _text_width_px(fig, f"{key}:", _PT_HEADER, weight="normal") / fig_w_px
            + _HEADER_KV_GAP,
            _text_width_px(fig, value, _PT_HEADER, weight="bold") / fig_w_px,
        )
        for key, value in entries
    ]
    total = sum(k + v for _key, _value, k, v in cells)
    gaps = max(len(cells) - 1, 1)
    room = _HEADER_RIGHT - _HEADER_LEFT
    gap = _HEADER_GAP
    if total + gap * gaps > room:
        gap = max(_HEADER_GAP_MIN, (room - total) / gaps)

    y = _y(_HEADER_ROW0_IN)
    x = _HEADER_LEFT
    for key, value, key_w, val_w in cells:
        if x + key_w + val_w > _HEADER_RIGHT:
            break  # dropped, not overprinted — see the docstring
        fig.text(x, y, f"{key}:", ha="left", va="top", fontsize=_PT_HEADER, color=C_MUTED)
        fig.text(x + key_w, y, value, ha="left", va="top", fontsize=_PT_HEADER,
                 color=C_INK, fontweight="bold")
        x += key_w + val_w + gap


def _fmt(value: float | None, digits: int = 2, unit: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{unit}"


# ─────────────────────────────────────────────────────────────────────────
# Status badge
# ─────────────────────────────────────────────────────────────────────────


def status_text(result: AnalysisResult) -> str:
    """The status line, in words. Used as the badge image's alt text and as
    the plain-text fallback when matplotlib is unavailable — so the verdict
    is never carried by colour alone."""
    if result.quality_gate.overall == "fail":
        return "INSUFFICIENT DATA — quality gate FAIL, no diagnosis made"
    # S12FIX: the fault screen crashed. This line is the badge's alt text and
    # the plain-text verdict, so an "ISO ZONE A — GOOD · no fault signature
    # identified" here is the clean bill in its most quotable form. The zone is
    # a real measurement, but it is not a verdict when nothing was screened.
    if result.rca is not None and result.rca.status == "error":
        return "ANALYSIS FAILED — the fault screen did not complete, no diagnosis made"
    if result.iso is not None and result.iso.iso_zone == "not_assessable":
        return "SEVERITY UNRATED — ISO severity requires velocity data"
    if result.iso is None:
        return "SEVERITY UNRATED — no classified reading"
    zone = result.iso.iso_zone
    qualifier = _STATUS_STYLE.get(zone, (_ZONE_WARN, ""))[1]
    # Session REPORT-4, item 1 — the banner on a custom basis.
    #
    # This line used to open "ISO ZONE B — ACCEPTABLE" on a machine judged
    # against the plant's own 5 / 8 / 12 mm/s limits, on a reading ISO 20816-3
    # itself calls Zone D. It is the largest type on page 1 and the badge's alt
    # text, so it is also what a screen reader says first and what a phone
    # preview shows; a custom zone hiding a real problem hides it here most.
    #
    # The operator's ruled string, and the rule that goes with it: the ISO zone
    # is on the FIRST LINE whenever it differs. Not in a footnote, not only in
    # the health line's would-give clause three inches below — a reader who
    # takes only the banner must not take away "acceptable" from a reading the
    # standard calls unacceptable.
    #
    # The committed-finding tail is not in the ruled string and is not printed
    # on this basis. It is one of three things the banner could say and the
    # least load-bearing of them: the fault sheet states the finding, by name,
    # two lines down. The ISO basis keeps it, byte for byte.
    if _basis_is_custom(result):
        head = f"ZONE {zone} — {qualifier} · MACHINE-SPECIFIC LIMITS"
        would_be = result.iso.iso_zone_would_be
        if would_be and would_be != zone:
            head += f" · ISO 20816-3 WOULD GIVE ZONE {would_be}"
        return head
    committed = [f for f in result.findings if f.fault != "no_significant_findings"]
    # "finding(s)" is a form for a template, not for a report an analyst signs.
    # This string is the status line AND the badge's alt text, so it is read
    # aloud by a screen reader as well as printed.
    n = len(committed)
    tail = f"{n} committed finding{'' if n == 1 else 's'}" if committed else \
        "no fault signature identified"
    return f"ISO ZONE {zone} — {qualifier} · {tail}"


def zone_word(zone: str | None) -> str:
    """The plain-language qualifier for an ISO zone letter — GOOD / ACCEPTABLE /
    UNSATISFACTORY / UNACCEPTABLE.

    Read from the SAME table `status_text` builds the status line from, so the
    severity card and the status line above it can never disagree about what a
    zone is called.
    """
    return _STATUS_STYLE.get(str(zone), ("", ""))[1]


def _status_color(result: AnalysisResult) -> str:
    if result.quality_gate.overall == "fail":
        return C_MUTED
    if result.iso is None or result.iso.iso_zone == "not_assessable":
        return C_MUTED
    return _STATUS_STYLE.get(result.iso.iso_zone, (_ZONE_WARN, ""))[0]


@_serialized
def _render_status_badge(result: AnalysisResult, out_dir: Path) -> ChartFigure:
    from matplotlib.patches import Rectangle

    text = status_text(result)
    fig = _new_figure(_BADGE_FIGSIZE)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_axis_off()
    ax.add_patch(
        Rectangle((0.004, 0.10), 0.992, 0.80, facecolor=_status_color(result), edgecolor="none")
    )
    ax.text(0.022, 0.50, text, ha="left", va="center", fontsize=_PT_BADGE,
            color="#FFFFFF", fontweight="bold")
    rel = f"{CHARTS_DIRNAME}/status_badge.{CHART_FORMAT}"
    _save(fig, out_dir / rel)
    return ChartFigure(key="status", rel_path=rel, title="Status", alt=text)


# ─────────────────────────────────────────────────────────────────────────
# Overlays shared by the spectrum figures
# ─────────────────────────────────────────────────────────────────────────


def _bearing_freq_items(result: AnalysisResult) -> list[tuple[str, float]]:
    bf = result.rca.bearing_freqs if result.rca is not None else None
    if bf is None:
        return []
    return [("BPFO", bf.BPFO), ("BPFI", bf.BPFI), ("BSF", bf.BSF), ("FTF", bf.FTF)]


def _matches_on_axis(result: AnalysisResult, axis: str) -> list[Any]:
    if result.rca is None:
        return []
    return [m for m in result.rca.primary_findings if m.axis == axis and m.freq_hz is not None]


# ── the focus window, the lanes, and the marker overlay (slice W6a) ──────────
#
# Ported from design/report_v2_proto/lib/chart_focus.py, which is the record of
# what these figures were designed to look like and why.


@dataclass(frozen=True)
class Marker:
    """One computed vertical: a bearing fault frequency or a shaft order."""

    label: str
    freq: float
    kind: str  # "fault" | "shaft"


def _axis_step(span: float, target: int = 8) -> float:
    """A round tick step giving roughly `target` ticks across `span`."""
    import math

    if span <= 0:
        return 1.0
    raw = span / target
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if raw <= mult * mag:
            return mult * mag
    return 10 * mag


def focus_span(
    fmax: float,
    markers: Sequence[Marker],
    matched_freqs: Sequence[float],
    *,
    margin: float = _FOCUS_MARGIN,
    floor_frac: float = _FOCUS_FLOOR_FRAC,
    full_frac: float = _FOCUS_FULL_FRAC,
) -> float:
    """The informative x-range: every computed marker and every matched peak,
    with margin, rounded up to a tick.

    Returns `fmax` unchanged when there is nothing to focus on (no markers, no
    matches) or when the window would already cover `full_frac` of the span —
    in which case a context strip would just duplicate the main panel.

    **This reads only frequencies the analysis itself computed. It never
    inspects the amplitude array to decide where the "interesting" part is.**
    Choosing the window from the data would let the figure crop away a region
    the report is silent about; choosing it from what the analysis computed
    cannot, because everything cropped is by construction something the analysis
    did not mark. The context strip then means nothing is hidden either way.
    A port that picks the window from the data instead has introduced a
    concealment bug that no test would catch — so `amplitude` is deliberately
    not a parameter of this function.
    """
    import math

    xs = [m.freq for m in markers if 0.0 < m.freq <= fmax]
    xs += [f for f in matched_freqs if f is not None and 0.0 < f <= fmax]
    if not xs or fmax <= 0:
        return fmax
    hi = max(max(xs) * margin, fmax * floor_frac)
    if hi >= fmax * full_frac:
        return fmax
    step = _axis_step(hi)
    return min(fmax, math.ceil(hi / step) * step)


def _spectrum_markers(result: AnalysisResult, roles: frozenset[str]) -> list[Marker]:
    """The computed verticals this spectrum is entitled to carry.

    Bearing detectors read the envelope; the 1×-family reads raw/velocity. Only
    the spectrum that actually fed a detector family carries its overlay — the
    same rule the pre-existing code applied, kept.
    """
    out: list[Marker] = []
    if ROLE_BEARING in roles:
        for name, freq in _bearing_freq_items(result):
            if freq:
                out.append(Marker(label=name, freq=float(freq), kind="fault"))
    shaft_hz = result.rca.shaft_freq_hz if result.rca is not None else 0.0
    if shaft_hz and shaft_hz > 0:
        for order in (1, 2, 3):
            out.append(Marker(label=f"{order}×", freq=float(shaft_hz) * order, kind="shaft"))
    return out


def _text_size_px(fig, text: str, fontsize: float, weight: str = "bold") -> tuple[float, float]:
    """The (width, height) the renderer will actually give `text`, in pixels.

    Measured, not estimated. The prototype estimated label widths with a
    monospace advance because its figures were set in a monospace face. These
    are set in DejaVu Sans, where an advance calibrated for a monospace font is
    simply wrong — and a wrong width is exactly what produces the collisions
    lane assignment exists to prevent. matplotlib can measure the real thing,
    so it does. `_header_block` did not, which is Part C's F-9.
    """
    renderer = fig.canvas.get_renderer()
    probe = fig.text(0, 0, text, fontsize=fontsize, fontweight=weight)
    box = probe.get_window_extent(renderer=renderer)
    probe.remove()
    return float(box.width), float(box.height)


def _text_width_px(fig, text: str, fontsize: float, weight: str = "bold") -> float:
    """Just the width. See `_text_size_px`."""
    return _text_size_px(fig, text, fontsize, weight)[0]


def _lane_rows(boxes: Sequence[tuple[float, float]], max_rows: int = 3) -> list[int]:
    """Greedy lane assignment in PIXELS, not in Hz.

    `boxes` is (centre_px, width_px) per marker. A label drops a lane when its
    left edge would land inside the right edge of the last label placed there.
    Measuring in frequency units is what let "BSF 69.3" and "BPFO 107.0" collide
    in the prototype's first pass: they are 37.7 Hz apart, which is wide in data
    space and narrow on the page. Deterministic, left to right, so the same
    input always lays out the same way.
    """
    right_edge: list[float] = []
    order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
    out = [0] * len(boxes)
    for i in order:
        cx, w = boxes[i]
        left, right = cx - w / 2, cx + w / 2
        placed = False
        for r in range(len(right_edge)):
            if left - right_edge[r] >= _LANE_PAD_PX:
                right_edge[r] = right
                out[i] = r
                placed = True
                break
        if not placed:
            if len(right_edge) < max_rows:
                right_edge.append(right)
                out[i] = len(right_edge) - 1
            else:
                r = min(range(len(right_edge)), key=lambda k: right_edge[k])
                right_edge[r] = right
                out[i] = r
    return out


def _lane_collisions(boxes: Sequence[tuple[float, float]], rows: Sequence[int]) -> int:
    """Every pair of marker labels that would overprint: same lane, boxes
    overlapping. Zero is the acceptance condition, and it is read off the
    geometry the renderer actually drew with."""
    bad = 0
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if rows[i] != rows[j]:
                continue
            li, ri = boxes[i][0] - boxes[i][1] / 2, boxes[i][0] + boxes[i][1] / 2
            lj, rj = boxes[j][0] - boxes[j][1] / 2, boxes[j][0] + boxes[j][1] / 2
            if li < rj and lj < ri:
                bad += 1
    return bad


# ── label placement in pixel space (Session CHARTS-2, item 3) ────────────────
#
# `_draw_matches` used to place its callouts with hand-tuned offsets: `dx = 9 if
# right else -9`, a `below` flip when the peak sat in the top 15 % band, and a
# white `_halo` painted under the glyphs for when a marker line ran through the
# text anyway. The halo is an admission that the placement is expected to
# collide — it makes an overprint legible rather than preventing it.
#
# The brief's acceptance is that NO text artist intersects the trace or another
# text artist. That cannot be met by tuning offsets, because the next fixture
# puts a different peak somewhere else; it is met by measuring. So a label is
# placed by trying a fixed ladder of candidate positions and taking the first
# one that is provably clear of everything already on the figure.
#
# The same geometry the placer decides with is what the acceptance test reads
# back, which is the rule this module already follows for lane assignment
# (`_lane_collisions`: "read off the geometry the renderer actually drew with").


@dataclass(frozen=True)
class _Rect:
    """An axis-aligned box in DISPLAY (pixel) coordinates."""

    x0: float
    y0: float
    x1: float
    y1: float

    def overlaps(self, other: "_Rect", pad: float = 0.0) -> bool:
        return (
            self.x0 - pad < other.x1 and other.x0 - pad < self.x1
            and self.y0 - pad < other.y1 and other.y0 - pad < self.y1
        )


class _TraceEnvelope:
    """The drawn trace, reduced to one (lo, hi) span per pixel COLUMN.

    A spectrum figure carries up to 8001 points and a figure carries up to a
    dozen candidate label positions, so testing every segment against every
    candidate is ~10^5 segment tests per figure for an answer that is only ever
    read at pixel resolution. Collapsing the polyline to a per-column vertical
    span once makes each test a scan over the columns the label actually covers,
    and at 150 dpi a column is a fifth of a point — finer than any overlap a
    reader could see.
    """

    __slots__ = ("_lo", "_hi", "_x0", "_n")

    def __init__(self, ax, freqs: Sequence[float], amps: Sequence[float]) -> None:
        box = ax.get_window_extent()
        self._x0 = float(box.x0)
        self._n = max(int(round(box.width)), 1)
        self._lo: list[float] = [float("inf")] * self._n
        self._hi: list[float] = [float("-inf")] * self._n
        if not freqs or not amps:
            return
        pts = ax.transData.transform(list(zip(freqs, amps)))
        for px, py in pts:
            col = int(px - self._x0)
            if 0 <= col < self._n:
                if py < self._lo[col]:
                    self._lo[col] = float(py)
                if py > self._hi[col]:
                    self._hi[col] = float(py)

    def intersects(self, rect: _Rect, pad: float = 0.0) -> bool:
        lo_col = max(int(rect.x0 - pad - self._x0), 0)
        hi_col = min(int(rect.x1 + pad - self._x0), self._n - 1)
        for col in range(lo_col, hi_col + 1):
            hi = self._hi[col]
            if hi == float("-inf"):
                continue
            if self._lo[col] - pad <= rect.y1 and rect.y0 <= hi + pad:
                return True
        return False


#: Candidate offsets for a callout block, in pixels from its anchor, tried in
#: this order. Up first and nearest first, so a figure with room reads exactly
#: as the hand-tuned version did; the rest are what make the guarantee.
_CALLOUT_LADDER: tuple[tuple[float, float, str], ...] = tuple(
    (dx, dy, ha)
    for dy in (11.0, 26.0, 41.0, 56.0, 71.0, 88.0)
    for dx, ha in ((9.0, "left"), (-9.0, "right"), (0.0, "center"))
)


def _block_rect(anchor_px: tuple[float, float], size: tuple[float, float],
                dx: float, dy: float, ha: str) -> _Rect:
    """Where a text block of `size` lands for one candidate offset."""
    w, h = size
    x = anchor_px[0] + dx
    if ha == "right":
        x -= w
    elif ha == "center":
        x -= w / 2
    y = anchor_px[1] + dy
    return _Rect(x, y, x + w, y + h)


#: A ladder for a label that must keep its HEIGHT — an ISO zone boundary label
#: belongs on its own line and nowhere else — so only x is free to move.
_SIDEWAYS_LADDER: tuple[tuple[float, float, str], ...] = tuple(
    (dx, 2.0, "right") for dx in (-4.0, -46.0, -88.0, -130.0, -172.0)
)


def _place_block(anchor_px: tuple[float, float], size: tuple[float, float],
                 placed: Sequence[_Rect], envelope: "_TraceEnvelope | None",
                 bounds: _Rect,
                 ladder: Sequence[tuple[float, float, str]] | None = None,
                 ) -> tuple[_Rect, float, float, str, bool]:
    """The first candidate that collides with nothing. Deterministic.

    Returns the chosen rect, its offsets, its alignment, and whether the ladder
    was EXHAUSTED — the caller counts that as a collision rather than hiding it,
    so a fixture the ladder cannot satisfy fails the acceptance instead of
    quietly printing one label over another.
    """
    last = None
    for dx, dy, ha in (ladder if ladder is not None else _CALLOUT_LADDER):
        rect = _block_rect(anchor_px, size, dx, dy, ha)
        last = (rect, dx, dy, ha)
        if rect.x0 < bounds.x0 or rect.x1 > bounds.x1 or rect.y1 > bounds.y1:
            continue
        if any(rect.overlaps(p, _TEXT_PAD_PX) for p in placed):
            continue
        if envelope is not None and envelope.intersects(rect, _TEXT_PAD_PX):
            continue
        return rect, dx, dy, ha, False
    assert last is not None
    return last[0], last[1], last[2], last[3], True


def _marker_label(m: Marker) -> str:
    return f"{m.label} {m.freq:.1f}"


def _draw_marker_lanes(ax, markers: Sequence[Marker], rows: Sequence[int],
                       xmax: float, axes_h_in: float) -> None:
    """Computed verticals, direct-labelled HORIZONTALLY in de-collided lanes
    above the axes, each with a leader down to its gridline.

    The labels were rotated 90° and stacked at the top of the plot area, where
    they overprinted each other and the data. Identity is still never carried by
    colour alone — every line is direct-labelled, and fault vs shaft is also
    separated by dash pattern so the figure survives greyscale.
    """
    trans = ax.get_xaxis_transform()  # x in data, y in axes fraction
    for m, row in zip(markers, rows):
        if not (0 < m.freq <= xmax):
            continue
        colour = C_FAULT if m.kind == "fault" else C_SHAFT
        dash = (0, (5, 2)) if m.kind == "fault" else (0, (1, 2))
        ax.axvline(m.freq, color=colour, linewidth=1.1, linestyle=dash, zorder=2, alpha=0.9)
        lane_bottom = 1.0 + (_LANE_GAP_IN + row * _LANE_H_IN) / axes_h_in
        lane_text = lane_bottom + (_LANE_H_IN * 0.30) / axes_h_in
        ax.plot([m.freq, m.freq], [1.0, lane_bottom], color=colour, linewidth=0.8,
                alpha=0.5, transform=trans, clip_on=False, zorder=2)
        ax.text(m.freq, lane_text, _marker_label(m), transform=trans, ha="center",
                va="bottom", fontsize=_PT_MARKER, color=colour, fontweight="bold",
                clip_on=False, zorder=7)

def _amplitude_at(spectrum: Spectrum, freq: float) -> float:
    """Nearest-bin amplitude — a lookup into the measured array, not a new
    measurement."""
    if not spectrum.freq_hz:
        return 0.0
    idx = min(range(len(spectrum.freq_hz)), key=lambda i: abs(spectrum.freq_hz[i] - freq))
    return spectrum.amplitude[idx] if idx < len(spectrum.amplitude) else 0.0


def _peak_in_window(spectrum: Spectrum, target: float,
                    tolerance_pct: float | None) -> tuple[float, float]:
    """The LOUDEST line within ±tolerance of `target` — the rule
    `pdm_core._find_peaks_matching` matches with (PARITY §7.7).

    Session V2-WIRE, found by looking at `demo_bpfo_6206` Y. The 2× harmonic
    marker was placed with `_amplitude_at(spectrum, freq * 2)`, i.e. at the bin
    nearest an **arithmetic target**. The harmonic the analysis actually found
    sits a couple of bins away, so the marker landed in the valley beside its
    own peak and drew the harmonic as a fraction of its real height — a figure
    saying something quieter than the report's words.

    Both numbers are real, so a number audit passes either way; only one of them
    is the peak the report is talking about. Falls back to the nearest bin when
    no tolerance is configured, which is the previous behaviour.
    """
    freqs = spectrum.freq_hz
    if not freqs:
        return target, 0.0
    if not tolerance_pct:
        return target, _amplitude_at(spectrum, target)
    half = target * float(tolerance_pct) / 100.0
    best_i, best_a = None, -1.0
    for i, f in enumerate(freqs):
        if abs(f - target) > half or i >= len(spectrum.amplitude):
            continue
        if spectrum.amplitude[i] > best_a:
            best_i, best_a = i, spectrum.amplitude[i]
    if best_i is None:
        return target, _amplitude_at(spectrum, target)
    return freqs[best_i], best_a


def _halo(width: float = 3.0):
    """A white stroke painted UNDER the glyphs of a callout.

    Ported from the prototype's `paint-order:stroke` fix, which was found by
    looking: on `b2_blower_trio` Y the callout is long enough to reach the
    `2× 59.3` shaft-order line, and the dotted vertical ran straight through the
    word "distribution". Moving the callout is not a general fix — the next
    fixture puts a different line somewhere else — so the text carries its own
    background instead. matplotlib strokes before filling, so the halo never
    eats the glyph.
    """
    from matplotlib import patheffects

    return [patheffects.withStroke(linewidth=width, foreground="#FFFFFF")]


def _draw_matches(fig, ax, matches: Sequence[Any], spectrum: Spectrum, shaft_hz: float,
                  xmax: float, labels: dict[str, str],
                  tolerance_pct: float | None = None,
                  envelope: "_TraceEnvelope | None" = None,
                  placed: "list[_Rect] | None" = None) -> tuple[bool, int]:
    """Matched peaks, their harmonic-family flags, and ±1× sideband brackets.

    Each callout is placed by `_place_block` — the first position on a fixed
    ladder that is clear of the trace, of every label already on the figure and
    of the axes' own bounds — and joined to its peak by a leader line. It is not
    placed by the offsets this function used to carry (Session CHARTS-2, item 3;
    the ladder and its rationale are documented above `_Rect`).

    Returns `(drew_anything, collisions)`. A collision is a callout whose ladder
    ran out — reported, never hidden, because it is what the acceptance reads.

    The value line prints the matched peak's **frequency** and not its
    amplitude. The observed frequency is already in the report — it is the
    Evidence table's `Observed (Hz)` column — whereas the amplitude at that bin
    is not, and the spectral-evidence preamble is KEEP-VERBATIM that "No figure
    introduces a number that is not already in this report." The amplitude is
    still shown, by the marker's position against a labelled y-axis; it is just
    not quoted as a figure. (The prototype quoted both; this is a deliberate,
    more conservative deviation — see SESSION_V2WIRE.md.)
    """
    drawn = False
    collisions = 0
    placed = placed if placed is not None else []
    bounds = _Rect(*_axes_bounds(ax))
    line_gap = 2.0

    def _callout(freq: float, amp: float, lines: Sequence[tuple[str, float, str]]) -> None:
        nonlocal collisions
        sizes = [_text_size_px(fig, text, size, weight) for text, size, weight in lines]
        w = max(s[0] for s in sizes)
        h = sum(s[1] for s in sizes) + line_gap * (len(sizes) - 1)
        anchor = ax.transData.transform((freq, amp))
        rect, _dx, _dy, ha, exhausted = _place_block(
            (float(anchor[0]), float(anchor[1])), (w, h), placed, envelope, bounds
        )
        if exhausted:
            collisions += 1
        placed.append(rect)
        # The leader runs from just above the peak to the nearest lower corner of
        # the block, so the reader is never asked which peak a callout belongs to.
        tail_x = rect.x0 if ha == "left" else (rect.x1 if ha == "right" else (rect.x0 + rect.x1) / 2)
        ax.plot(
            *zip(*ax.transData.inverted().transform(
                [(anchor[0], anchor[1] + 3.0), (tail_x, rect.y0 - 1.5)]
            )),
            color=C_MATCH, linewidth=0.8, alpha=0.55, zorder=5, clip_on=False,
        )
        # Top line first: the block grows DOWNWARD from rect.y1 in display space.
        y = rect.y1
        x = {"left": rect.x0, "right": rect.x1, "center": (rect.x0 + rect.x1) / 2}[ha]
        for (text, size, weight), (_tw, th) in zip(lines, sizes):
            pos = ax.transData.inverted().transform((x, y - th))
            ax.text(pos[0], pos[1], text, ha=ha, va="bottom", fontsize=size,
                    color=C_MATCH, fontweight=weight, path_effects=_halo(3.0),
                    zorder=8, clip_on=False)
            y -= th + line_gap

    for match in matches:
        freq = match.freq_hz
        if freq is None or not (0 <= freq <= xmax):
            continue
        amp = _amplitude_at(spectrum, freq)
        ax.plot([freq], [amp], marker="v", markersize=6.5, color=C_MATCH,
                markeredgecolor="#FFFFFF", markeredgewidth=0.9, linestyle="none", zorder=6)
        _callout(freq, amp, [
            (labels.get(match.fault, match.fault), _PT_CALLOUT, "bold"),
            (fmt_hz_order(freq, shaft_hz), _PT_CALLOUT_VALUE, "normal"),
        ])
        drawn = True
        if match.harmonic_present and 0 < freq * 2 <= xmax:
            h_freq, h_amp = _peak_in_window(spectrum, freq * 2, tolerance_pct)
            ax.plot([h_freq], [h_amp], marker="v", markersize=5.0, color=C_MATCH,
                    markeredgecolor="#FFFFFF", markeredgewidth=0.7, linestyle="none", zorder=6)
            _callout(h_freq, h_amp, [("2× harmonic", _PT_CALLOUT_VALUE, "normal")])
        bands = [s for s in (match.sidebands or []) if 0 <= s <= xmax]
        if bands and shaft_hz > 0:
            _draw_sideband_bracket(ax, sorted(bands), max(amp, _sideband_amp(spectrum, bands)))
    return drawn, collisions


def _axes_bounds(ax) -> tuple[float, float, float, float]:
    """The axes' own pixel box — the frame a label may not leave."""
    box = ax.get_window_extent()
    return float(box.x0), float(box.y0), float(box.x1), float(box.y1)


def _sideband_amp(spectrum: Spectrum, bands: Sequence[float]) -> float:
    return max((_amplitude_at(spectrum, b) for b in bands), default=0.0)


def _draw_sideband_bracket(ax, bands: Sequence[float], base_amp: float) -> None:
    """A ±1× sideband bracket spanning the detected sidebands."""
    lo, hi = bands[0], bands[-1]
    if hi <= lo or base_amp <= 0:
        return
    y = base_amp * 1.28
    ax.plot([lo, hi], [y, y], color=C_MATCH, linewidth=0.9, zorder=5)
    for x in (lo, hi):
        ax.plot([x, x], [y * 0.94, y], color=C_MATCH, linewidth=0.9, zorder=5)
    ax.annotate("±1× sidebands", xy=((lo + hi) / 2, y), xytext=(0, 3),
                textcoords="offset points", ha="center", va="bottom",
                fontsize=5.8, color=C_MATCH)


def _legend_handles(entries: Sequence[tuple[str, dict]]):
    from matplotlib.lines import Line2D

    return [Line2D([0], [0], label=label, **style) for label, style in entries]


def _legend_height_in(n_entries: int) -> float:
    """The inches a wrapped `_LEGEND_COLS`-column legend of `n_entries` needs.

    Part of the vertical budget rather than a constant, because the entry count
    varies with what the analysis found: a figure with a matched peak, a
    detail panel, an amplitude floor and a spectrum mean carries seven entries
    where a quiet channel carries three. A fixed reservation is right for one of
    those and wrong for the other, and the wrong one puts the x-axis label
    underneath the legend.
    """
    rows = -(-max(n_entries, 1) // _LEGEND_COLS)  # ceil
    return rows * _LEGEND_ROW_IN + _LEGEND_PAD_IN


def _draw_legend(fig, entries: Sequence[tuple[str, dict]], height_in: float) -> None:
    """The legend, below the axes, left-aligned with them."""
    fig.legend(
        handles=_legend_handles(entries), loc="lower left",
        bbox_to_anchor=(_AXES_LEFT, _BOTTOM_IN / height_in), ncols=_LEGEND_COLS,
        fontsize=_PT_LEGEND, frameon=False, handlelength=2.6, columnspacing=1.6,
    )


# ─────────────────────────────────────────────────────────────────────────
# Spectrum figure (line plot — the Case's own array)
# ─────────────────────────────────────────────────────────────────────────


def _axis_role(machine: MachineMeta, axis: str) -> str:
    return "axial" if axis == machine.axial_axis else "radial"


#: Case sources whose *_rms_ACC_G IS a real accelerometer reading in g.
#: Everything else routes through _rms_label below.
_REAL_ACCELERATION_SOURCES = frozenset({"cwru", "mfpt", "mafaulda", "wind_turbine"})


# Session R3-DIFF (item 4) put an `_rms_label` here, resolving the unit of
# `SensorData.*_rms_ACC_G` for the figure header's "RMS" row — because that row
# printed whatever landed in the field as "N.NNN g", and for half the upload
# paths that is a false claim (a CSV velocity upload stores `overall_rms_mms *
# 0.08`, an acceleration PROXY; an unscaled WAV stores a normalized-waveform RMS
# in arbitrary units — both adapters say "it is not a real g reading" in their
# own comments).
#
# Session CHARTS-2 item 4 cut the figure header to one row of Type/Unit/Δf/Fmax,
# so that "RMS" row is gone and `_rms_label` had no caller left. It is deleted
# rather than left as an unreachable resolver: the discrimination it existed for
# survives intact in `spectrum_unit_label` below, which uses THE SAME
# discriminators and now feeds the header's Unit cell, the y-axis label, the
# caption and both parameter rows. A velocity upload can still never be labelled
# "g" anywhere — `tests/test_unit_labels.py` drives every one of R3-DIFF's cases
# through that function instead. What genuinely left the report is the proxy
# NUMBER itself, which no surface now prints; it was a number whose unit the
# report could not name, which is why it was being labelled "not measured".


#: The three amplitude units a spectrum figure, caption, header or parameter row
#: may claim (Session REPORT-2). Resolved by `spectrum_unit_label`, never read off
#: `Spectrum.kind`, which records a DSP and not a unit.
SPECTRUM_UNIT_VELOCITY = "mm/s RMS"
SPECTRUM_UNIT_ACCELERATION = "g"
SPECTRUM_UNIT_AS_SUPPLIED = "as supplied"


def spectrum_unit_label(case: Case | None, spectrum: Spectrum) -> str:
    """Session REPORT-2 — the amplitude unit the report may state for `spectrum`.

    A CAT analyst reviewing the outreach sample asked for the spectrum TYPE and
    its UNIT to be stated consistently wherever a spectrum is shown: a velocity
    spectrum in mm/s, an envelope spectrum in g, never mixed on one figure.
    `Spectrum` carries no unit field, so the unit is resolved from what the Case
    already carries -- the same discriminators `_rms_label` uses, no new field,
    no guess:

      source is a benchmark                        -> g  (accelerometer recordings)
      an upload with severity in scope, velocity
        kind                                       -> mm/s RMS (adapters/uploads/units.py
                                                      converted it: CSV/XLSX, UFF spectrum,
                                                      recipe velocity lanes)
      an upload with severity in scope, any other
        kind                                       -> g  (a scaled WAV: sensitivity applied)
      otherwise                                    -> "as supplied" (a synthetic case, an
                                                      unscaled WAV, a UFF/recipe waveform
                                                      whose validation_scope is ["rca"])

    "as supplied" is a stated non-claim, not a blank: the row above the figure
    still carries the real mm/s reading, and the conversion note says what the
    upload declared.
    """
    if case is None:
        return SPECTRUM_UNIT_AS_SUPPLIED
    if case.source in _REAL_ACCELERATION_SOURCES:
        return SPECTRUM_UNIT_ACCELERATION
    if case.source == "upload" and "severity" in case.validation_scope:
        if spectrum.kind == "velocity":
            return SPECTRUM_UNIT_VELOCITY
        return SPECTRUM_UNIT_ACCELERATION
    return SPECTRUM_UNIT_AS_SUPPLIED


def spectrum_kind_and_unit(case: Case | None, spectrum: Spectrum, *, lower: bool = False) -> str:
    """`"Velocity spectrum (mm/s RMS)"`, `"Envelope spectrum (as supplied)"` -- the
    one phrase every figure title, caption, header row and parameter row uses to
    name a spectrum, so type and unit cannot drift apart between them. `lower`
    lowercases the KIND for running prose ("of the velocity spectrum (mm/s RMS)");
    the unit is never case-transformed -- "mm/s rms" is not a unit."""
    kind = _SPECTRUM_KIND_LABEL.get(spectrum.kind, "Spectrum")
    if lower:
        kind = kind.lower()
    return f"{kind} ({spectrum_unit_label(case, spectrum)})"


# ── orders beside Hz (Session REPORT-2, item 3) ──────────────────────────
#
# A CAT analyst writes a fault frequency as "107.25 Hz (3.57×)" -- the shaft order
# is how a bearing tone is recognised, and a report that prints only the Hz makes
# the reader do the division. The order is that division and nothing else:
# freq / rca.shaft_freq_hz, two decimals, on values the analysis already holds.
# It is presentation arithmetic in the report layer (the unmatched-periodicity
# sentence has printed "N.NN× shaft" this way since R3-DIFF), not a new number
# from a model. Note 107.25 / 30 rounds to 3.58 while the computed BPFO
# 107.03 / 30 rounds to 3.57; both are printed where each frequency is.


def fmt_hz_order(freq_hz: float, shaft_hz: float | None, digits: int = 2) -> str:
    """`"107.25 Hz (3.58×)"` -- a frequency and its shaft order, together. Without a
    shaft rate (RCA did not run, or reported none) only the Hz is printed."""
    hz = f"{freq_hz:.{digits}f} Hz"
    if not shaft_hz or shaft_hz <= 0:
        return hz
    return f"{hz} ({freq_hz / shaft_hz:.2f}×)"


#: A "<number> Hz" mention not already followed by a parenthesis -- so a string
#: that already carries "(N×)" is left alone and the pass is idempotent.
_HZ_MENTION_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?) Hz(?! \()")


def _inside_parentheses(text: str, index: int) -> bool:
    """Is `text[index]` inside a parenthetical? Counted rather than parsed, which
    is exact for balanced text and is what pdm_core writes."""
    before = text[:index]
    return before.count("(") > before.count(")")


def annotate_orders(text: str, shaft_hz: float | None) -> str:
    """Append the shaft order after every BARE "<number> Hz" in a pdm_core-authored
    finding string the report prints verbatim -- `Finding.reason`,
    `FaultMatch.evidence`, the damage-stage evidence lines. pdm_core is latched and
    its strings name fault frequencies in Hz alone; this pass adds the order at
    presentation time and changes no word. Applied to those strings ONLY: never to
    the running-speed row, the parameters table, or the differential adjudication
    (tests/test_report_html.py compares that one raw).

    A mention already INSIDE a parenthetical is left alone (Session REPORT-2-FIX,
    closing this session's own F-6). A parenthetical is already a gloss, and a
    gloss inside a gloss is noise -- every pdm_core sentence that puts a frequency
    in brackets has introduced it in prose first, so the order is still stated
    once. The first cut annotated those too and shipped three mangled sentences:
    `1× shaft frequency (29.0 Hz (1.00×)) dominant` glossed "1×" onto a line the
    sentence had just called 1×; `matches calculated BPFO (107.0 Hz (3.57×) ±3%)`
    nested a bracket inside a bracket; and the blade-pass arithmetic became
    `(6 blades × 30.0 Hz (1.00×) shaft = 180.0 Hz (6.00×))`, which reads as
    nonsense. Bare mentions -- which is where an analyst actually needs the order,
    `The loudest line on y is 107.0 Hz (3.57×)` -- are unaffected.

    `re.sub` scans the ORIGINAL string, so `m.start()` indexes unmodified text and
    the depth count is exact however much the result grows.
    """
    if not text or not shaft_hz or shaft_hz <= 0:
        return text

    def _gloss(m: re.Match[str]) -> str:
        if _inside_parentheses(text, m.start()):
            return m.group(0)
        return f"{m.group(1)} Hz ({float(m.group(1)) / shaft_hz:.2f}×)"

    return _HZ_MENTION_RE.sub(_gloss, text)


def _channel_header(spectrum: Spectrum, case: Case | None) -> tuple[tuple[str, str], ...]:
    """ONE row: the spectrum's type, its unit, its line spacing and its Fmax.

    Session CHARTS-2, item 4. This returned eight entries that `_header_block`
    laid in two rows of four, and the pair that collided was `("Spectrum",
    <type and unit>)` immediately followed by `("Δf", …)` — Part C's F-9.

    What each of the four is doing here:

      * TYPE and UNIT are split into their own cells. REPORT-2 composed them
        into one phrase, `spectrum_kind_and_unit`, precisely so the eight
        surfaces that name a spectrum could not drift apart; splitting them for
        LAYOUT does not re-open that, because both halves still come from the
        same two resolvers the phrase is built from.
      * Δf is what tells an analyst whether a tone is resolvable at all.
      * FMAX IS NEW, and it is E2 item 1's other half. The report said 2 kHz
        while the figure's x-axis said 300 Hz, and nothing on the figure named
        the range the analysis actually covered. The two-panel layout shows it;
        this states it.

    Channel/Overall/RMS/Speed/Lines/Date are gone from the figure. The channel
    is already in the figure's own title, and the rest are in the report's
    Machine Details and Analysis-parameters tables — a figure header is not the
    place to restate a table. Operator ruling at plan time.

    "(as supplied)" leaves the header entirely (E2 item 3): where provenance
    cannot name a unit, the Unit CELL IS OMITTED rather than filled with a
    non-claim. `_spectrum_caption` is untouched and still carries the full
    `spectrum_kind_and_unit` phrase, so the figure surface still states the
    non-claim once — REPORT-3 owns where it finally lands.
    """
    unit = spectrum_unit_label(case, spectrum)
    fmax = spectrum.fmax_hz or (spectrum.freq_hz[-1] if spectrum.freq_hz else None)
    entries: list[tuple[str, str]] = [
        ("Type", _SPECTRUM_KIND_LABEL.get(spectrum.kind, "Spectrum")),
    ]
    if unit and unit != SPECTRUM_UNIT_AS_SUPPLIED:
        entries.append(("Unit", unit))
    entries.append(("Δf", _fmt(_line_spacing(spectrum), 3, " Hz")))
    entries.append(("Fmax", _fmt(fmax, 1, " Hz") if fmax else "—"))
    return tuple(entries)


def _line_spacing(spectrum: Spectrum) -> float | None:
    if len(spectrum.freq_hz) < 2:
        return None
    return spectrum.freq_hz[1] - spectrum.freq_hz[0]


@_serialized
def _render_spectrum(
    result: AnalysisResult, machine: MachineMeta, axis: str, spectrum: Spectrum,
    out_dir: Path, *, key: str, roles: frozenset[str], case: Case | None,
    thresholds: dict[str, Any] | None, labels: dict[str, str],
    figsize: tuple[float, float] = _SPECTRUM_FIGSIZE, compact: bool = False,
) -> ChartFigure:
    """One channel, as TWO panels: the whole analysed span, then a zoom on the
    fault region. Legend below both.

    Session V2-WIRE built this as a focused main panel over a slim full-span
    context strip. Part C read the result and filed E2 item 1: the analysis ran
    to 2 kHz, the main panel showed 0–300 Hz, and the other 1700 Hz survived
    only as a 0.32 in sliver with no amplitude axis and no ticks. *"An analyst
    who asked for 2 kHz in order to see the bearing harmonics gets a statement
    about them, not a plot of them."*

    Session CHARTS-2 item 5 promotes that strip to a real panel and puts it
    FIRST, so the figure opens at the range the report claims:

      * **Panel 1 — 0 to Fmax.** The whole measured array, every computed
        vertical, every matched peak, and the zoom window shaded on it. This is
        the panel that makes a 2 kHz analysis look like one.
      * **Panel 2 — the zoom.** `focus_span`'s window, where the lines are far
        enough apart to be direct-labelled and the callouts have somewhere to go.

    Labels live on panel 2 for a measured reason: on the trio, seven computed
    verticals fall between 11.9 Hz and 163 Hz, which is the leftmost 8 % of a
    2 kHz axis — seven labels into 90 px. Panel 1 draws the lines, panel 2 names
    them, and both are in view at once.

    A single panel when `focus_span` returns `fmax` (nothing to zoom into); the
    figure keeps its height either way and spends the spare inches on the panel.

    Same series, same verticals, same annotations, same axis meanings as before
    — nothing is added: no smoothing, no interpolation, no derived series, no
    threshold the report does not already carry.
    """
    # Type and unit first (Session REPORT-2) -- "Envelope spectrum (as supplied) — Y
    # (radial)". The kind word stays first: tests/test_report_html.py classifies
    # the markdown's ### headings by it.
    unit = spectrum_unit_label(case, spectrum)
    title = f"{spectrum_kind_and_unit(case, spectrum)} — {axis.upper()} ({_axis_role(machine, axis)})"

    freqs, amps = spectrum.freq_hz, spectrum.amplitude
    fmax = freqs[-1] if freqs else 0.0
    matches = _matches_for_roles(result, axis, roles)
    markers = _spectrum_markers(result, roles)

    # C1 — the window, from COMPUTED frequencies only. See focus_span's docstring
    # for why the amplitude array is not an input here.
    #
    # Every frequency the figure is ENTITLED TO DRAW goes in, not just the
    # matched fundamentals: `_draw_matches` renders a 2× harmonic and a sideband
    # bracket when the analysis found them, and both are guarded by `<= xmax`.
    # Feeding the window only the fundamentals would let a harmonic the report
    # states in words ("2× BPFO harmonic present") fall outside the axis and
    # vanish without a trace. Found by rendering the BPFO fixture and reading
    # the geometry back.
    matched_freqs: list[float] = []
    for m in matches:
        if m.freq_hz is None:
            continue
        matched_freqs.append(m.freq_hz)
        if m.harmonic_present:
            matched_freqs.append(m.freq_hz * 2)
        matched_freqs.extend(s for s in (m.sidebands or []) if s)
    focus_max = focus_span(fmax, markers, matched_freqs)
    # Session REPORT-3 — `compact` draws the DETAIL panel only. It is not "no
    # zoom": the single panel still spans 0-focus_max, which is the zoom
    # window, so page 1 shows the region the fault is in at the width the
    # markers need. What it drops is the full-span companion panel, whose job
    # (naming the analysed range) the sheet's own caption and the Evidence
    # appendix both do.
    zoom = focus_max < fmax and not compact
    vis_markers = [m for m in markers if 0 < m.freq <= focus_max]

    fig = _new_figure(figsize)

    # C4 — lanes, for the panel that carries the labels. Widths are measured on
    # this figure's own renderer, before any axes exist: the axes' horizontal
    # extent is fixed and does not depend on how many lanes we end up needing,
    # so there is no circularity.
    ax_left_px = _AXES_LEFT * figsize[0] * _DPI
    ax_w_px = _AXES_WIDTH * figsize[0] * _DPI
    boxes = [
        (ax_left_px + (m.freq / focus_max) * ax_w_px if focus_max else ax_left_px,
         _text_width_px(fig, _marker_label(m), _PT_MARKER))
        for m in vis_markers
    ]
    rows = _lane_rows(boxes)
    collisions = _lane_collisions(boxes, rows)
    n_rows = (max(rows) + 1) if vis_markers else 0

    floor = _amplitude_floor_value(spectrum, thresholds)
    peak_amp = max(amps) if amps else 0.0
    mean_amp = sum(amps) / len(amps) if amps else 0.0
    shaft_hz = result.rca.shaft_freq_hz if result.rca is not None else 0.0
    tolerance_pct = (thresholds or {}).get("rca", {}).get("tolerance_pct")

    # C3 — fitted to what is drawn, with head-room, and it must clear the
    # amplitude floor so that line is never off-scale. `peak_amp` is the whole
    # array's peak, not the window's: a window that rescaled itself would make
    # two channels of the same machine incomparable, and would hide that the
    # loudest line on the axis sits outside the focus. The head-room is larger
    # when there are matches than it was, because the callouts are placed INSIDE
    # the axes now and the ladder needs somewhere to climb (item 3).
    y_top = max(peak_amp, floor or 0.0) * (1.42 if matches else 1.08) or 1.0

    # The legend is built BEFORE the layout, not appended to while drawing: its
    # wrapped height is part of the vertical budget, and a budget that guessed
    # the row count is how the detail panel's x-axis label ended up underneath
    # "Spectrum mean 0.0091" on the first cut of this session.
    drawable = [m for m in matches if m.freq_hz is not None and 0 <= m.freq_hz <= focus_max]
    show_floor = floor is not None and floor <= y_top
    legend: list[tuple[str, dict]] = [("Measured spectrum", {"color": C_TRACE, "linewidth": 1.6})]
    if any(m.kind == "fault" for m in vis_markers):
        legend.append(("Computed fault freq.",
                       {"color": C_FAULT, "linestyle": (0, (5, 2)), "linewidth": 1.1}))
    if any(m.kind == "shaft" for m in vis_markers):
        legend.append(("Shaft orders 1–3×",
                       {"color": C_SHAFT, "linestyle": (0, (1, 2)), "linewidth": 1.1}))
    if mean_amp > 0:
        legend.append((f"Spectrum mean {mean_amp:.4f}",
                       {"color": C_MUTED, "linestyle": (0, (1, 3)), "linewidth": 1.0}))
    if show_floor:
        multiple = (thresholds or {}).get("rca", {}).get("floor_min")
        tail = f" ({float(multiple):g}× mean)" if multiple else ""
        legend.append((f"Amplitude floor {floor:.4f}{tail}",
                       {"color": C_MUTED, "linestyle": (0, (4, 2, 1, 2)), "linewidth": 1.1}))
    if drawable:
        legend.append(("Matched peak",
                       {"color": C_MATCH, "marker": "v", "linestyle": "none", "markersize": 5.5}))
    if zoom:
        legend.append((f"Detail below: 0–{focus_max:g} Hz",
                       {"color": C_MATCH, "linewidth": 1.1}))

    # ── vertical budget, in inches, top down ────────────────────────────────
    # header | [full-span panel + its x-axis + gap] | lanes | detail panel +
    # its x-axis | legend | bottom. The two panels split whatever is left, so
    # the figure keeps ONE height whether or not it zooms.
    height_in = figsize[1]
    lane_block_in = n_rows * _LANE_H_IN + _LANE_GAP_IN
    # The legend is 0.72 in of a 4.1 in canvas and every entry in it is
    # repeated on the same channel's full figure in the Evidence appendix.
    legend_in = 0.0 if compact else _legend_height_in(len(legend))
    fixed_in = (_HEADER_BAND_IN + lane_block_in + _XAXIS_IN + legend_in + _BOTTOM_IN)
    if zoom:
        fixed_in += _XAXIS_IN + _PANEL_GAP_IN
    plot_in = height_in - fixed_in
    span_h_in = plot_in / 2 if zoom else 0.0
    detail_h_in = plot_in - span_h_in

    def _panel(top_in: float, h_in: float):
        return fig.add_axes((
            _AXES_LEFT, 1.0 - (top_in + h_in) / height_in, _AXES_WIDTH, h_in / height_in,
        ))

    # ── panel 1: the whole analysed span (item 5) ───────────────────────────
    span_ax = None
    if zoom:
        span_ax = _panel(_HEADER_BAND_IN, span_h_in)
        span_ax.plot(freqs, amps, color=C_TRACE, linewidth=1.1, zorder=4)
        span_ax.set_xlim(0, fmax)
        span_ax.set_ylim(0, y_top)
        for m in markers:
            if 0 < m.freq <= fmax:
                span_ax.axvline(
                    m.freq, color=C_FAULT if m.kind == "fault" else C_SHAFT,
                    linewidth=1.0, alpha=0.8, zorder=2,
                    linestyle=(0, (5, 2)) if m.kind == "fault" else (0, (1, 2)),
                )
        for match in matches:
            if match.freq_hz is None or not (0 <= match.freq_hz <= fmax):
                continue
            span_ax.plot([match.freq_hz], [_amplitude_at(spectrum, match.freq_hz)],
                         marker="v", markersize=5.5, color=C_MATCH, markeredgecolor="#FFFFFF",
                         markeredgewidth=0.8, linestyle="none", zorder=6)
            if match.harmonic_present and 0 < match.freq_hz * 2 <= fmax:
                h_freq, h_amp = _peak_in_window(spectrum, match.freq_hz * 2, tolerance_pct)
                span_ax.plot([h_freq], [h_amp], marker="v", markersize=4.5, color=C_MATCH,
                             markeredgecolor="#FFFFFF", markeredgewidth=0.7,
                             linestyle="none", zorder=6)
        span_ax.axvspan(0, focus_max, color=C_MATCH, alpha=0.09, zorder=1)
        span_ax.axvline(focus_max, color=C_MATCH, linewidth=1.1, zorder=5)
        span_ax.set_xticks(_tick_ladder(fmax))
        span_ax.set_yticks(_tick_ladder(y_top, 5))
        span_ax.tick_params(axis="both", labelsize=_PT_TICK)
        span_ax.set_xlabel(f"Frequency (Hz) — full analysed range, 0–{fmax:g} Hz",
                           fontsize=_PT_AXIS)
        span_ax.set_ylabel(amplitude_axis_label(unit), fontsize=_PT_AXIS)

    # ── panel 2: the detail (or the only panel) ─────────────────────────────
    detail_top_in = _HEADER_BAND_IN + lane_block_in
    if zoom:
        detail_top_in += span_h_in + _XAXIS_IN + _PANEL_GAP_IN
    ax = _panel(detail_top_in, detail_h_in)
    ax.plot(freqs, amps, color=C_TRACE, linewidth=1.6, zorder=4)  # C5
    ax.set_xlim(0, focus_max)
    ax.set_ylim(0, y_top)
    ax.set_xticks(_tick_ladder(focus_max))
    ax.set_yticks(_tick_ladder(y_top, 5))
    ax.tick_params(axis="both", labelsize=_PT_TICK)

    _draw_marker_lanes(ax, vis_markers, rows, focus_max, detail_h_in)

    # C6 — the lines keep their true y and carry no text; their VALUES are in
    # the legend, which is below the plot. Drawn at the right margin they
    # overprinted each other on every b2_blower_trio channel, where the two are
    # nearly coincident — which is the exact case the labels existed for.
    for axes in (a for a in (span_ax, ax) if a is not None):
        if mean_amp > 0:
            axes.axhline(mean_amp, color=C_MUTED, linewidth=1.0, linestyle=(0, (1, 3)), zorder=3)
        if show_floor:
            axes.axhline(floor, color=C_MUTED, linewidth=1.1,
                         linestyle=(0, (4, 2, 1, 2)), zorder=3)

    fig.canvas.draw()  # the placer measures against laid-out axes, so lay them out
    envelope = _TraceEnvelope(ax, freqs, amps)
    _drew, placed_collisions = _draw_matches(
        fig, ax, matches, spectrum, shaft_hz, focus_max, labels, tolerance_pct,
        envelope=envelope,
    )
    collisions += placed_collisions

    ax.set_xlabel(
        f"Frequency (Hz) — detail, 0–{focus_max:g} Hz of {fmax:g} Hz" if zoom
        else "Frequency (Hz)",
        fontsize=_PT_AXIS,
    )
    ax.set_ylabel(amplitude_axis_label(unit), fontsize=_PT_AXIS)

    if not compact:
        _draw_legend(fig, legend, height_in)
    header = _channel_header(spectrum, case)
    _header_block(fig, header, title)

    rel = f"{CHARTS_DIRNAME}/{key}.{CHART_FORMAT}"
    _save(fig, out_dir / rel)
    return ChartFigure(
        key=key,
        rel_path=rel,
        title=title,
        alt=f"{title}: measured spectrum with computed fault-frequency and shaft-order overlays"
            + (f", shown over the full 0–{fmax:g} Hz analysed range with a detail panel "
               f"on 0–{focus_max:g} Hz" if zoom else ""),
        caption=_spectrum_caption(result, axis, spectrum, matches, case=case,
                                  tolerance_pct=tolerance_pct),
        header=header,
        geometry=FigureGeometry(
            fmax=fmax, focus_max=focus_max, has_context_strip=zoom, label_rows=n_rows,
            collisions=collisions, n_markers=len(vis_markers),
            n_matched=len([m for m in matches if m.freq_hz is not None
                           and 0 <= m.freq_hz <= focus_max]),
        ),
    )


def _tick_ladder(vmax: float, target: int = 8) -> list[float]:
    """Round ticks from 0 to `vmax` at `_axis_step`'s pitch, none past `vmax`.

    Set explicitly on both axes rather than left to the default locator, which
    emits ticks OUTSIDE the view limits: on the trio's Z channel `ylim` topped
    out at 4.197 and the locator still produced a `5`, whose label is a live
    text artist sitting above the axes — invisible on the page (matplotlib does
    not paint it) but real to anything that reads the figure's geometry back,
    including this session's own acceptance test. A phantom label that a checker
    can see and a reader cannot is a bad thing to leave lying around either way.
    """
    step = _axis_step(vmax, target)
    ticks, t = [], 0.0
    while t <= vmax + 1e-6:
        ticks.append(t)
        t += step
    return ticks


def _matches_for_roles(result: AnalysisResult, axis: str, roles: frozenset[str]) -> list[Any]:
    """A match is marked only on the spectrum its own detector family read —
    marking a BPFO hit on a raw velocity spectrum that never carried that
    demodulated tone would put a marker where there is no peak."""
    out = []
    for match in _matches_on_axis(result, axis):
        wanted = ROLE_BEARING if match.fault in BEARING_FAULTS else ROLE_FLOW
        if wanted in roles:
            out.append(match)
    return out


def amplitude_axis_label(unit: str) -> str:
    """The spectrum figure's y-axis label -- "Amplitude (mm/s RMS)", "Amplitude (g)",
    "Amplitude (as supplied)". One function so the PNG axis, the SVG inset axis and
    the test that pins them agree on the wording."""
    return f"Amplitude ({unit})"


#: Session REPORT-3 (item 5, PARTC F-11). How far over the spectrum's own median
#: a line has to stand before a caption will say this channel SHOWS it.
#:
#: Deliberately the intake's own presence bar, not the RCA's evidence floor. The
#: floor is what a peak must clear to be COMMITTED as evidence; a line that
#: cleared it here would have been matched here. The question this constant
#: answers is the weaker, honest one a reader asks looking at the plot — "is
#: that tone visibly there?" — which is the same question
#: `webapp/assembly.py`'s cross-channel speed check asks of a 1x line, at the
#: same ">= 3x the median" bar (FIXTURE-1's README quotes it).
_CAPTION_PRESENCE_OVER_MEDIAN = 3.0


def _match_seen_on_this_channel(
    result: AnalysisResult, axis: str, spectrum: Spectrum,
    tolerance_pct: float | None,
) -> tuple[Any, float, float] | None:
    """A fault this analysis matched on ANOTHER axis, visibly present on this one.

    Session REPORT-3 (item 5) closes PARTC F-11. The Z-axis caption read

        "No peak on this channel matched a computed fault frequency within
        tolerance."

    while the loudest bin on Z was 107.25 Hz at 0.330 — the BPFO line, the one
    the report commits to two paragraphs earlier. The sentence was true about
    the RCA's bookkeeping (the match is recorded on y) and false as English
    about the figure it sits under, on all three PDFs Part C read.

    Returns `(match, freq_hz, amplitude)` for the first such fault, measured in
    THIS channel's array: the peak inside the match tolerance around the
    committed frequency, if it stands clear of this spectrum's own median.
    Nothing is re-diagnosed and no new match is made — the caption reports an
    amplitude, it does not commit evidence.
    """
    rca = result.rca
    if rca is None or not spectrum.amplitude:
        return None
    amps = sorted(a for a in spectrum.amplitude if a is not None)
    if not amps:
        return None
    median = amps[len(amps) // 2]
    for match in rca.primary_findings:
        if match.axis == axis or not match.freq_hz:
            continue
        freq, amp = _peak_in_window(spectrum, float(match.freq_hz), tolerance_pct)
        if freq is None or amp is None:
            continue
        if median > 0 and amp >= _CAPTION_PRESENCE_OVER_MEDIAN * median:
            return match, freq, amp
    return None


def _spectrum_caption(result: AnalysisResult, axis: str, spectrum: Spectrum,
                      matches: Sequence[Any], *, case: Case | None = None,
                      tolerance_pct: float | None = None) -> str:
    """The one string that reaches BOTH documents' visible prose (markdown as the
    figure's italic line, HTML as its <figcaption>). It names the spectrum's type
    and unit (Session REPORT-2) and every matched fault frequency.

    Session REPORT-3 (item 5) closes PARTC F-11. A channel with no match of its
    own no longer says "no peak on this channel matched" when the committed
    fault's line is plainly there on the plot beneath it: it says where the
    match WAS recorded, and names what this channel shows.
    """
    named = spectrum_kind_and_unit(case, spectrum)
    shaft = result.rca.shaft_freq_hz if result.rca is not None else 0.0
    if not matches:
        seen = _match_seen_on_this_channel(result, axis, spectrum, tolerance_pct)
        if seen is not None:
            match, freq, amp = seen
            fault = match.fault.replace("_", " ")
            return (
                f"{named}, {axis.upper()} axis. The {fault} match was recorded on the "
                f"{str(match.axis).upper()} axis; this channel shows {amp:.3g} at "
                f"{fmt_hz_order(freq, shaft)}."
            )
        return (
            f"{named}, {axis.upper()} axis. No peak on "
            "this channel matched a computed fault frequency within tolerance."
        )
    parts = [f"{m.fault.replace('_', ' ')} at {fmt_hz_order(m.freq_hz, shaft)}"
             for m in matches if m.freq_hz]
    return f"{named}, {axis.upper()} axis. Matched: " + "; ".join(parts) + "."


def _amplitude_floor_value(spectrum: Spectrum, thresholds: dict[str, Any] | None) -> float | None:
    """The commit gate a matched bearing peak has to clear: peak amplitude /
    axis-spectrum MEAN >= rca.floor_min (config, route profile). Drawn only
    when the profile that ran is known — never guessed."""
    if thresholds is None or not spectrum.amplitude:
        return None
    floor_min = thresholds.get("rca", {}).get("floor_min")
    if not floor_min:
        return None
    mean_amp = sum(spectrum.amplitude) / len(spectrum.amplitude)
    return mean_amp * float(floor_min) if mean_amp > 0 else None


# ─────────────────────────────────────────────────────────────────────────
# Fault-frequency map — the always-possible fallback
# ─────────────────────────────────────────────────────────────────────────

_AXIS_MARKERS = {"x": "o", "y": "s", "z": "D"}


@_serialized
def _render_frequency_map(
    result: AnalysisResult, machine: MachineMeta, out_dir: Path, *,
    thresholds: dict[str, Any] | None, labels: dict[str, str],
) -> ChartFigure | None:
    """No spectrum array reached the renderer, so there is no amplitude axis to
    draw. What IS computed: where every fault frequency sits, how wide the match
    tolerance is, and which peaks the analysis matched. That is the figure —
    with no amplitude implied anywhere."""
    if result.rca is None or result.rca.status != "ok":
        return None
    from matplotlib.patches import Rectangle

    shaft_hz = result.rca.shaft_freq_hz or 0.0
    bearing = _bearing_freq_items(result)
    matches = [m for m in result.rca.primary_findings if m.freq_hz is not None]
    if not bearing and not matches and shaft_hz <= 0:
        return None

    candidates = [f for _, f in bearing] + [m.freq_hz for m in matches] + [shaft_hz * 3]
    xmax = max([f for f in candidates if f] or [1.0]) * 1.12

    # Same vertical budget discipline as the spectrum figure: a header band, the
    # axes, its x-axis, the legend BELOW the axes, and a bottom margin. The
    # legend used to sit at `loc="upper right"` INSIDE the plot, on top of the
    # very labels this figure exists to show (item 2).
    fig = _new_figure(_FIGSIZE)
    height_in = _FIGSIZE[1]
    # The legend is drawn after the axes here, so the budget reserves the MOST
    # entries this figure can produce — fault freq + tolerance + shaft orders +
    # one per matched axis (x/y/z). The total figure height is fixed either way,
    # so over-reserving costs plot height on a sparse figure and never a collision.
    axes_h_in = height_in - _HEADER_BAND_IN - _XAXIS_IN - _legend_height_in(6) - _BOTTOM_IN
    # A WIDER left margin than the spectrum figure's: this axis is labelled
    # "computed"/"observed" in words, not in numbers, and at the 8 pt floor
    # "computed" is 90 px — wider than `_AXES_LEFT`'s whole gutter, so it hung
    # off the left edge of the figure.
    left = _MAP_AXES_LEFT
    ax = fig.add_axes((
        left, 1.0 - (_HEADER_BAND_IN + axes_h_in) / height_in,
        _AXES_WIDTH - (left - _AXES_LEFT), axes_h_in / height_in,
    ))
    ax.set_xlim(0, xmax)
    ax.set_xticks(_tick_ladder(xmax))
    ax.set_ylim(0, 1.4)  # headroom so the labels never leave the frame
    ax.set_yticks([0.72, 0.30])
    ax.set_yticklabels(["computed", "observed"])
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="both", labelsize=_PT_TICK)

    tol = None
    if thresholds is not None:
        tol = thresholds.get("rca", {}).get("tolerance_pct")

    fig.canvas.draw()  # lay the axes out before anything is measured against them
    ax_box = ax.get_window_extent()

    def _lane_place(items: Sequence[tuple[str, float]], fontsize: float) -> list[int]:
        """Lane rows for labels centred on their own frequency, measured in
        PIXELS — the same rule `_lane_rows` applies on the spectrum figure, for
        the same reason: BSF 69.3 and BPFO 107.0 are wide apart in Hz and narrow
        on the page, and this figure drew them at a fixed y."""
        boxes = [
            (float(ax_box.x0 + (freq / xmax) * ax_box.width) if xmax else float(ax_box.x0),
             _text_width_px(fig, text, fontsize))
            for text, freq in items
        ]
        return _lane_rows(boxes)

    legend: list[tuple[str, dict]] = []
    drawable = [(name, freq) for name, freq in bearing if freq and freq <= xmax]
    lanes = _lane_place([(n, f) for n, f in drawable], _PT_MARKER)
    lane_h = 0.115  # in data units of this axes' 0..1.4 range
    for (name, freq), row in zip(drawable, lanes):
        ax.plot([freq, freq], [0.60, 0.84], color=C_FAULT, linewidth=1.4, zorder=4)
        ax.text(freq, 0.87 + row * lane_h, name, ha="center", va="bottom",
                fontsize=_PT_MARKER, color=C_FAULT, fontweight="bold")
        if row:
            ax.plot([freq, freq], [0.84, 0.86 + row * lane_h], color=C_FAULT,
                    linewidth=0.7, alpha=0.5, zorder=3)
        if tol:
            half = freq * float(tol) / 100.0
            ax.add_patch(Rectangle((freq - half, 0.60), 2 * half, 0.24,
                                   facecolor=C_FAULT, alpha=0.14, edgecolor="none", zorder=3))
    if bearing:
        legend.append(("Computed fault freq.", {"color": C_FAULT, "linewidth": 1.4}))
        if tol:
            legend.append((f"Match tolerance ±{tol:g}%",
                           {"color": C_FAULT, "alpha": 0.35, "linewidth": 5}))
    if shaft_hz > 0:
        orders = [(f"{o}×", shaft_hz * o) for o in (1, 2, 3) if shaft_hz * o <= xmax]
        order_lanes = _lane_place(orders, _PT_MARKER)
        for (text, freq), row in zip(orders, order_lanes):
            ax.axvline(freq, color=C_SHAFT, linewidth=0.9, linestyle=(0, (1, 2)), zorder=2)
            ax.text(freq, 0.02 + row * lane_h, text, ha="center", va="bottom",
                    fontsize=_PT_MARKER, color=C_SHAFT, fontweight="bold")
        legend.append(("Shaft orders 1–3×", {"color": C_SHAFT, "linestyle": (0, (1, 2)), "linewidth": 1.0}))

    seen_axes: set[str] = set()
    drawn_matches = [m for m in matches if m.freq_hz is not None and m.freq_hz <= xmax]
    placed: list[_Rect] = []
    bounds = _Rect(*_axes_bounds(ax))
    for match in drawn_matches:
        freq = match.freq_hz
        marker = _AXIS_MARKERS.get(match.axis, "o")
        ax.plot([freq], [0.30], marker=marker, markersize=6, color=C_MATCH,
                markeredgecolor="#FFFFFF", markeredgewidth=0.8, linestyle="none", zorder=6)
        text = f"{labels.get(match.fault, match.fault)} {fmt_hz_order(freq, shaft_hz)}"
        size = _text_size_px(fig, text, _PT_CALLOUT_VALUE, "normal")
        anchor = ax.transData.transform((freq, 0.30))
        rect, _dx, _dy, ha, _exhausted = _place_block(
            (float(anchor[0]), float(anchor[1]) - size[1] - 10.0), size, placed, None, bounds
        )
        placed.append(rect)
        x = {"left": rect.x0, "right": rect.x1, "center": (rect.x0 + rect.x1) / 2}[ha]
        pos = ax.transData.inverted().transform((x, rect.y0))
        ax.text(pos[0], pos[1], text, ha=ha, va="bottom", fontsize=_PT_CALLOUT_VALUE,
                color=C_MATCH, clip_on=False)
        seen_axes.add(match.axis)
    for axis in sorted(seen_axes):
        legend.append((f"Matched peak ({axis.upper()} axis)",
                       {"color": C_MATCH, "marker": _AXIS_MARKERS.get(axis, "o"),
                        "linestyle": "none", "markersize": 5}))

    ax.set_xlabel("Frequency (Hz)", fontsize=_PT_AXIS)
    if legend:
        _draw_legend(fig, legend, height_in)

    if machine.bearing is None or result.rca.bearing_freqs is None:
        bearing_label = "not specified"
    else:
        bearing_label = machine.bearing.model or "geometry supplied"
    header = (
        ("Speed", f"{result.rca.rpm:.0f} rpm" if result.rca.rpm else "—"),
        ("Shaft rate", _fmt(shaft_hz, 2, " Hz")),
        ("Bearing", bearing_label),
        ("Date", result.ts[:19] if result.ts else "—"),
    )
    _header_block(fig, header, "Fault-frequency map")

    rel = f"{CHARTS_DIRNAME}/frequency_map.{CHART_FORMAT}"
    _save(fig, out_dir / rel)
    return ChartFigure(
        key="frequency_map",
        rel_path=rel,
        title="Fault-frequency map",
        alt="Fault-frequency map: computed bearing fault frequencies, shaft orders, "
            "match tolerance bands, and the peaks the analysis matched",
        caption=(
            "The spectrum array was not retained with this analysis, so no amplitude axis "
            "is drawn. Positions and tolerance bands are the computed values the match "
            "was made against."
        ),
        header=header,
    )


# ─────────────────────────────────────────────────────────────────────────
# Trend figure
# ─────────────────────────────────────────────────────────────────────────


@_serialized
def _render_trend(
    result: AnalysisResult, out_dir: Path, *, case: Case | None,
) -> ChartFigure | None:
    trend = result.trend
    if trend is None:
        return None
    not_assessable = result.iso is not None and result.iso.iso_zone == "not_assessable"
    unit = "" if not_assessable else " mm/s"

    # Budget as on the other two figures: the legend and the slope note both used
    # to sit INSIDE the plot area — `loc="upper left"` and an axes-fraction text
    # box at (0.985, 0.06) — i.e. on top of the series they annotate (item 2).
    fig = _new_figure(_FIGSIZE)
    height_in = _FIGSIZE[1]
    note_in = 0.42  # the slope/R² note, below the axes and above the legend
    # Reserved for the most entries this figure can produce: the series, the
    # baseline/current pair, the ISO boundaries and the alarm limit.
    axes_h_in = (height_in - _HEADER_BAND_IN - _XAXIS_IN - note_in
                 - _legend_height_in(5) - _BOTTOM_IN)
    ax = fig.add_axes((
        _AXES_LEFT, 1.0 - (_HEADER_BAND_IN + axes_h_in) / height_in,
        _AXES_WIDTH, axes_h_in / height_in,
    ))
    ax.tick_params(axis="both", labelsize=_PT_TICK)

    history = list(case.history) if (case is not None and case.history) else []
    legend: list[tuple[str, dict]] = []
    values: list[float] = []

    if history:
        xs = list(range(len(history)))
        ys = [p.value for p in history]
        values = ys
        ax.plot(xs, ys, color=C_TRACE, linewidth=1.1, marker="o", markersize=3,
                markeredgecolor="#FFFFFF", markeredgewidth=0.5, zorder=5)
        legend.append(("Reading history", {"color": C_TRACE, "marker": "o", "markersize": 4,
                                           "linewidth": 1.2}))
        ax.set_xlim(-0.5, len(history) - 0.5)
        # Explicit, for the reason in `_tick_ladder`: the default locator on a
        # 0..29 reading index emits -5 and 35 as well, and their labels are live
        # text artists hanging off both edges of the figure.
        ax.set_xticks(_tick_ladder(len(history) - 1, 6))
        ax.set_xlabel(f"Reading index (oldest → newest, {trend.n_days} days)",
                      fontsize=_PT_AXIS)
    else:
        # No series reached the renderer: plot the two computed aggregates the
        # TrendResult carries, and say so. Nothing is interpolated between them.
        pts = [(0, trend.baseline_avg), (1, trend.current_avg)]
        pts = [(x, v) for x, v in pts if v is not None]
        if not pts:
            # `fig` is registered nowhere, so abandoning it IS closing it.
            return None
        values = [v for _, v in pts]
        ax.plot([x for x, _ in pts], values, color=C_TRACE, linewidth=1.4, marker="o",
                markersize=6, markeredgecolor="#FFFFFF", markeredgewidth=0.8, zorder=5)
        ax.set_xlim(-0.35, 1.35)
        ax.set_xticks([x for x, _ in pts][: len(pts)])
        ax.set_xticklabels(["baseline (first 7 d)", "current (last 7 d)"][: len(pts)])
        legend.append(("Computed averages", {"color": C_TRACE, "marker": "o", "markersize": 4,
                                             "linewidth": 1.2}))
        ax.set_xlabel(f"Trend window ({trend.n_days} days)", fontsize=_PT_AXIS)

    for value in (trend.baseline_avg, trend.current_avg):
        if value is not None and history:
            ax.axhline(value, color=C_MUTED, linewidth=0.7, linestyle=(0, (1, 3)), zorder=3)
    if history and (trend.baseline_avg is not None or trend.current_avg is not None):
        legend.append(("Baseline / current avg", {"color": C_MUTED, "linestyle": (0, (1, 3)),
                                                  "linewidth": 0.9}))

    # ISO boundary band lines are a VELOCITY judgement — omitted entirely when
    # severity is not assessable (the series is in the reading's native units).
    zone_lines: list[tuple[float, str]] = []
    if not not_assessable and result.iso is not None:
        for value, label in ((result.iso.th_ab, "A/B"), (result.iso.th_bc, "B/C"),
                             (result.iso.th_cd, "C/D")):
            if value is None:
                continue
            ax.axhline(value, color=_ZONE_WARN, linewidth=0.8, linestyle=(0, (6, 3)), zorder=3)
            zone_lines.append((value, label))
            values.append(value)
        boundaries_label, alarm_label = trend_legend_labels(result)
        legend.append((boundaries_label, {"color": _ZONE_WARN, "linestyle": (0, (6, 3)),
                                          "linewidth": 1.0}))
        if trend.alarm_limit is not None:
            ax.axhline(trend.alarm_limit, color=_ZONE_CRIT, linewidth=0.9,
                       linestyle=(0, (4, 2)), zorder=3)
            values.append(trend.alarm_limit)
            legend.append((alarm_label, {"color": _ZONE_CRIT,
                                         "linestyle": (0, (4, 2)), "linewidth": 1.0}))

    top = max(values) if values else 1.0
    y_top = top * 1.35 if top > 0 else 1.0
    ax.set_ylim(0, y_top)
    ax.set_yticks(_tick_ladder(y_top, 6))
    ax.set_ylabel(f"Overall vibration ({unit.strip() or 'native units'})", fontsize=_PT_AXIS)
    _draw_legend(fig, legend, height_in)

    # A zone label belongs ON its own boundary line and nowhere else, so it may
    # slide sideways but never up or down (`_SIDEWAYS_LADDER`). The x-axis is
    # padded to the right first, so the labels land past the last reading rather
    # than over the series; two boundaries closer together than a line height
    # then step left instead of overprinting. These used to be pinned to
    # `ax.get_xlim()[1]` with no check at all.
    if zone_lines:
        fig.canvas.draw()
        x0, x1 = ax.get_xlim()
        ax.set_xlim(x0, x1 + (x1 - x0) * 0.10)
        fig.canvas.draw()
        placed: list[_Rect] = []
        bounds = _Rect(*_axes_bounds(ax))
        for value, label in zone_lines:
            size = _text_size_px(fig, label, _PT_MARKER, "bold")
            anchor = ax.transData.transform((ax.get_xlim()[1], value))
            rect, _dx, _dy, _ha, _exhausted = _place_block(
                (float(anchor[0]), float(anchor[1])), size, placed, None, bounds,
                ladder=_SIDEWAYS_LADDER,
            )
            placed.append(rect)
            pos = ax.transData.inverted().transform((rect.x1, rect.y0))
            ax.text(pos[0], pos[1], label, ha="right", va="bottom", fontsize=_PT_MARKER,
                    color=_ZONE_WARN, fontweight="bold", path_effects=_halo(3.0), zorder=7)

    # The slope/R² note moves BELOW the axes. At (0.985, 0.06) in axes fractions
    # it was a white box sitting on the series it describes.
    note = _trend_annotation(result, not_assessable)
    fig.text(_AXES_LEFT, (_BOTTOM_IN + _legend_height_in(5)) / height_in, note,
             ha="left", va="bottom", fontsize=_PT_CALLOUT_VALUE, color=C_INK, linespacing=1.35)

    header = (
        ("Status", trend.status),
        ("Severity", "unrated" if not_assessable else trend.severity),
        ("Window", f"{trend.n_days} days"),
        ("Points", str(len(history)) if history else "aggregates only"),
    )
    _header_block(fig, header, "Trend — overall vibration")

    rel = f"{CHARTS_DIRNAME}/trend.{CHART_FORMAT}"
    _save(fig, out_dir / rel)
    return ChartFigure(
        key="trend", rel_path=rel, title="Trend — overall vibration",
        alt=f"Trend chart over {trend.n_days} days with computed slope and threshold lines",
        caption=note.replace("\n", " "),
        header=header,
    )


#: Session LIMITS-1c, item 1 -- the third "next ISO boundary" occurrence.
#:
#: The predicate is `report/generate.py::_custom_basis`, spelled again here
#: rather than imported: `generate.py` imports THIS module (`generate.py:37`),
#: so an import back is a cycle. One line, and the two spellings are held
#: together by `tests/test_limits1c_report.py`, which renders BOTH the caption
#: and the health line from one analysis and asserts they agree about the
#: basis -- a drift would land as a red test rather than as two attributions
#: on one page.
def _basis_is_custom(result: AnalysisResult) -> bool:
    return result.iso is not None and result.iso.zone_basis == "custom"


#: Session REPORT-4, item 8 -- LIMITS-1c F-1, closed.
#:
#: The trend figure's two threshold legend labels. They were the literals
#: `"ISO zone boundaries"` and `"ISO §6.5.2 alarm limit"`, baked straight into
#: the legend call, and on a custom basis they signed ISO's name to the plant's
#: numbers: the lines the legend describes are drawn from `result.iso.th_*` and
#: `trend.alarm_limit`, which on that basis ARE the analyst's own limits. The
#: prose beside the figure has said "machine-specific" since LIMITS-1c; the
#: figure's own key still said ISO.
#:
#: A FUNCTION, and public, for the reason F-1 records: these strings are
#: rendered into the figure, and there is no instrument in the tree that can
#: read text back out of a chart. So the label builder is the seam the pin
#: holds, and the figure is left to draw whatever it is handed. The wording
#: follows `_trend_annotation` above, which is the other half of the same
#: sentence on the same page.
def trend_legend_labels(result: AnalysisResult) -> tuple[str, str]:
    """`(zone-boundaries label, alarm-limit label)` for the trend figure's legend,
    named for the authority that actually set the numbers being drawn."""
    if _basis_is_custom(result):
        return ("Machine-specific zone boundaries", "Machine-specific alarm limit")
    return ("ISO zone boundaries", "ISO §6.5.2 alarm limit")


def _trend_annotation(result: AnalysisResult, not_assessable: bool) -> str:
    trend = result.trend
    assert trend is not None
    lines = [f"slope {trend.slope:+.4f}/day   change {trend.pct_change:+.1f}%"]
    if trend.r_squared is not None:
        lines[-1] += f"   R² {trend.r_squared:.2f}"
    if trend.trend_note:
        lines.append(trend.trend_note)
    elif not_assessable:
        lines.append("No forward boundary projection without velocity data.")
    elif trend.days_to_next_boundary is not None:
        # The boundary this counts down to is `trend.days_to_next_boundary`,
        # computed against the thresholds actually in force -- which on a
        # custom basis are the plant's numbers, not a row of ISO 20816-3.
        whose = ("machine-specific zone" if _basis_is_custom(result) else "ISO")
        lines.append(f"~{trend.days_to_next_boundary:.0f} days to the next {whose} boundary "
                     "at the current rate.")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────


BEARING_FAULTS = frozenset(
    {"bearing_outer_race", "bearing_inner_race", "bearing_ball_spin", "bearing_cage"}
)

# Which detector family read this spectrum (Session B B1 routing, mirrored from
# pipeline.run_analysis): the bearing detectors read `case.spectra`; the
# 1×-family reads `case.raw_spectra` when an adapter supplied one and otherwise
# falls back to the same bearing context. A figure only carries the overlays of
# the family that actually read it.
ROLE_BEARING = "bearing"
ROLE_FLOW = "flow"


def _channel_spectra(case: Case | None) -> dict[str, list[tuple[Spectrum, frozenset[str]]]]:
    """Per-axis spectra to plot, each tagged with the detector role(s) it fed.
    Deduped when an adapter supplied one array under both keys."""
    if case is None:
        return {}
    out: dict[str, list[tuple[Spectrum, frozenset[str]]]] = {}
    for axis in ("x", "y", "z"):
        raw = (case.raw_spectra or {}).get(axis)
        env = (case.spectra or {}).get(axis)
        same = (
            raw is not None and env is not None
            and raw.freq_hz == env.freq_hz and raw.amplitude == env.amplitude
        )
        series: list[tuple[Spectrum, frozenset[str]]] = []
        if env is not None and same:
            series.append((env, frozenset({ROLE_BEARING, ROLE_FLOW})))
        else:
            if raw is not None:
                series.append((raw, frozenset({ROLE_FLOW})))
            if env is not None:
                # No raw spectrum ⇒ the 1×-family detectors reused this one too.
                roles = {ROLE_BEARING} | ({ROLE_FLOW} if raw is None else set())
                series.append((env, frozenset(roles)))
        if series:
            out[axis] = series
    return out


@_serialized
def render_charts(
    result: AnalysisResult,
    machine: MachineMeta,
    out_dir: Path,
    *,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    fault_labels: dict[str, str] | None = None,
) -> ChartSet:
    """Render every evidence figure this analysis supports into
    `out_dir/charts/` and return the manifest the template renders.

    **Runs in a child process** (Session RENDER-PROC). GEOM-A's `EXIT=139` was
    a SIGSEGV inside `Figure.add_axes` on a webapp worker thread, and a native
    fault kills the interpreter — uvicorn and every in-flight upload, not the
    job. The figures are built by `render_charts_inprocess` in a child; this
    function only ships the analysis over and decodes the manifest back.

    Raises `RenderChildCrashed` if that child dies from a signal or times out.
    **That is a change**: this function used to promise it never raised. It
    still does not raise for the case that promise was written for — matplotlib
    being absent still returns an empty ChartSet, and a figure that cannot be
    built from the available evidence is still simply absent from the manifest.
    What no longer passes silently is a native crash, which previously could
    not be caught at all because it took the process down. It now surfaces as a
    retryable `internal_error`; a figure-less report that says nothing went
    wrong would hide exactly the defect this boundary exists to expose.

    Serialized here as well as on each builder, so a direct caller — the tests,
    and anyone who renders figures without rendering a document — takes the
    lock ONCE for the whole figure set. Reached through `generate.py` the
    acquisition has already happened and this is a pass-through. The lock now
    bounds how many children are in flight (one), which is what keeps the
    `MemoryMax=1500M` cgroup in `deploy/vibagent.service` honest; see
    `report/render_lock.py` and `report/render_proc.py`.
    """
    from vib_agent.report.render_proc import run_child

    out_dir = Path(out_dir)
    # NOTE: no `matplotlib_available()` probe here. That helper imports
    # matplotlib, and importing it in the PARENT is the one thing this boundary
    # exists to prevent; the child reports availability instead.
    reply = run_child("charts", {
        "result": result.model_dump_json(),
        "machine": machine.model_dump_json(),
        "case": case.model_dump_json() if case is not None else None,
        "out_dir": str(out_dir),
        "thresholds": thresholds,
        "fault_labels": fault_labels,
    })
    if reply["status"] != "ok":
        return ChartSet()
    return chartset_from_dict(reply["charts"])


def render_charts_inprocess(
    result: AnalysisResult,
    machine: MachineMeta,
    out_dir: Path,
    *,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    fault_labels: dict[str, str] | None = None,
) -> ChartSet:
    """The figure set, built HERE. Called only from `_render_child.py`.

    This is `render_charts`'s body as it stood before Session RENDER-PROC,
    unchanged — which is what makes the byte-identity proof in
    outputs/SESSION_RENDERPROC.md a statement about the process boundary and
    nothing else. Do not call it from the parent: every `_new_figure` below it
    is a native allocation in whatever process runs it.
    """
    charts = ChartSet()
    if not matplotlib_available():
        return charts
    out_dir = Path(out_dir)
    labels = fault_labels or {}

    charts.status = _render_status_badge(result, out_dir)
    charts.available = True

    # A gate-fail reading has no diagnosis to evidence — the badge and the
    # insufficient-data text carry it. Drawing spectra there would dress up a
    # reading the gate already rejected.
    if result.quality_gate.overall == "fail":
        return charts

    channel_spectra = _channel_spectra(case)
    for axis in sorted(channel_spectra):
        figures = []
        for i, (spectrum, roles) in enumerate(channel_spectra[axis]):
            key = f"spectrum_{axis}" if i == 0 else f"spectrum_{axis}_{i}"
            figures.append(
                _render_spectrum(result, machine, axis, spectrum, out_dir, key=key,
                                 roles=roles, case=case, thresholds=thresholds, labels=labels)
            )
        charts.channels.append(
            ChannelFigures(axis=axis, label=f"{axis.upper()} ({_axis_role(machine, axis)})",
                           figures=tuple(figures))
        )

    if not charts.channels:
        fallback = _render_frequency_map(result, machine, out_dir,
                                         thresholds=thresholds, labels=labels)
        if fallback is not None:
            charts.channels.append(
                ChannelFigures(axis="—", label="All channels", figures=(fallback,))
            )

    # Session REPORT-3 — page 1's figure. One extra render, for the axis the
    # committed call was made on (the first channel with a figure when nothing
    # was committed, so a healthy report still shows its spectrum). Same
    # builder, same content, shorter canvas.
    charts.sheet = _render_sheet_spectrum(result, machine, out_dir, channel_spectra,
                                          case=case, thresholds=thresholds, labels=labels)

    charts.trend = _render_trend(result, out_dir, case=case)
    return charts


def _sheet_axis(result: AnalysisResult, channel_spectra: dict[str, Any]) -> str | None:
    """The axis page 1 shows: the one the committed call was made on.

    Falls back to the first measured channel, because a report with no committed
    fault still earns a spectrum on page 1 — "nothing matched" is a finding an
    analyst wants to see the evidence for, not only read.
    """
    if not channel_spectra:
        return None
    committed = [f for f in result.findings if f.fault != "no_significant_findings"]
    axis = committed[0].evidence.get("axis") if committed else None
    if axis in channel_spectra:
        return axis
    return sorted(channel_spectra)[0]


def _render_sheet_spectrum(
    result: AnalysisResult, machine: MachineMeta, out_dir: Path,
    channel_spectra: dict[str, Any], *, case: Case | None,
    thresholds: dict[str, Any] | None, labels: dict[str, str],
) -> ChartFigure | None:
    """Page 1's spectrum figure — `_SHEET_FIGSIZE`, everything else identical."""
    axis = _sheet_axis(result, channel_spectra)
    if axis is None or not channel_spectra.get(axis):
        return None
    spectrum, roles = channel_spectra[axis][0]
    return _render_spectrum(result, machine, axis, spectrum, out_dir,
                            key=f"sheet_{axis}", roles=roles, case=case,
                            thresholds=thresholds, labels=labels,
                            figsize=_SHEET_FIGSIZE, compact=True)


def chart_files(out_dir: Path) -> list[Path]:
    """Every figure this module would have written into `out_dir` — used by the
    tests that prove charts are intermediates and die in the completion purge.

    Both extensions, deliberately. Nothing writes a PNG since Session CHARTS-2,
    but this function is what the purge pins count, and a purge check that could
    only see one format would go quietly green on a directory full of the other.
    """
    charts_dir = Path(out_dir) / CHARTS_DIRNAME
    if not charts_dir.is_dir():
        return []
    return sorted([*charts_dir.glob("*.svg"), *charts_dir.glob("*.png")])


def _amplitude_units_row(case: Case | None, spectra: Sequence[Spectrum]) -> str:
    """The "Spectrum amplitude units" parameter row (Session REPORT-2). One resolved
    unit is stated as itself; the unresolved case keeps the wording it always had.

    Session REPORT-4 (item 8), closing INTAKEFIX-1 F-1. This row used to end in
    "(see conversion note)" three times over, and to say "(converted at upload)"
    beside the velocity unit. Both were wrong in the same way the sibling row's
    cross-reference was, and the second one worse:

      * the cross-reference points at a note this module cannot keep. It exists
        only where the webapp inserted one, and `_insert_note_after_machine_details`
        inserts nothing when there is nothing to insert — so on every generator,
        CLI and eval path the reader was sent to a note that is not in the
        document. That is INTAKEFIX-1 item 9's argument, verbatim, about the row
        a few lines below; R-1 left these three because the pin that asserts two
        of them was out of that session's scope. It is in this one's.

      * "(converted at upload)" asserts an EVENT. `spectrum_unit_label` resolves
        a velocity-kind upload to mm/s RMS whether or not `units.py` converted
        anything, so on a file the analyst already exported in mm/s the table
        claimed a conversion while page 1 said "Input already in mm/s RMS — no
        conversion applied" three inches above it. Measured on FIXTURE-1's
        compressor DE horizontal file, which is what the operator read.

    So the row states the UNIT and nothing else. Whether a conversion happened,
    and from what, is the conversion note's job, under Machine Details, where it
    is accurate in both directions and needs no pointer from here.
    """
    units = sorted({spectrum_unit_label(case, s) for s in spectra})
    if not units or units == [SPECTRUM_UNIT_AS_SUPPLIED]:
        return "as supplied at upload"
    return ", ".join(units)


def analysis_parameters(
    result: AnalysisResult,
    *,
    case: Case | None = None,
    thresholds: dict[str, Any] | None = None,
    profile: str | None = None,
    #: Session REPORT-4 (item 6). Declared measurement directions, axis -> label.
    #: When the analyst named a direction the report says the DIRECTION
    #: everywhere; the sensor axis letter then survives in exactly one place,
    #: this table, in parentheses beside the channel it belongs to. Absent on
    #: every reading that declared no direction, which is what keeps those
    #: documents byte-identical.
    axis_names: dict[str, str] | None = None,
) -> list[tuple[str, str]]:
    """The 'Analysis parameters' table — acquisition and detection settings, every
    one of them a value the analysis computed, was configured with, or the analyst
    DECLARED on the upload form (Session INTAKE-HONEST: Case.acquisition).
    Anything not recorded says so rather than being invented; a declared value is
    printed as declared, cross-checked against the data where the data can check
    it, and never overrides a measured one."""
    spectra = _channel_spectra(case)
    all_spectra = [s for _, series in sorted(spectra.items()) for s, _roles in series]
    first = all_spectra[0] if all_spectra else None
    acq = case.acquisition if case is not None else None

    def _sample_rate() -> str:
        if first is None or len(first.freq_hz) < 2:
            return "not recorded"
        fmax = first.fmax_hz or first.freq_hz[-1]
        return f"{2 * fmax:.0f} Hz (from Fmax {fmax:.0f} Hz)"

    def _lines_row() -> str:
        data_n = len(first.freq_hz) if first is not None else None
        declared_n = acq.spectral_lines if acq is not None else None
        if declared_n is not None and data_n is not None:
            if declared_n == data_n:
                return f"{data_n} (declared, matches data)"
            return f"{data_n} (data; {declared_n} declared — data used)"
        if declared_n is not None:
            return f"{declared_n} (declared)"
        if data_n is not None:
            return f"{data_n} (from data; not declared)"
        return "not recorded"

    def _fmax_row() -> str:
        data_fmax = (first.fmax_hz or (first.freq_hz[-1] if first.freq_hz else None)) \
            if first is not None else None
        declared_fmax = acq.fmax_hz if acq is not None else None
        if declared_fmax is not None and data_fmax is not None:
            # 2% relative, or one bin, whichever is looser — a declared Fmax is
            # a round nameplate number, the data's top bin rarely lands on it.
            spacing = _line_spacing(first) or 0.0
            tolerance = max(0.02 * declared_fmax, spacing)
            if abs(declared_fmax - data_fmax) <= tolerance:
                return f"{_fmt(declared_fmax, 1, ' Hz')} (declared, matches data)"
            return (f"{_fmt(data_fmax, 1, ' Hz')} (data; {_fmt(declared_fmax, 1, ' Hz')} "
                    f"declared — data used)")
        if declared_fmax is not None:
            return f"{_fmt(declared_fmax, 1, ' Hz')} (declared)"
        if data_fmax is not None:
            return f"{_fmt(data_fmax, 1, ' Hz')} (top bin; not declared)"
        return "not recorded"

    _window_labels = {"hanning": "Hanning", "flattop": "Flat-top",
                      "rectangular": "Rectangular (none)", "other": "Other"}

    def _window_row() -> str:
        declared = acq.window_type if acq is not None else None
        if declared is not None:
            return f"{_window_labels.get(declared, declared)} (declared)"
        return "not provided — unknown window; amplitudes taken as supplied"

    def _averages_row() -> str:
        declared = acq.averages if acq is not None else None
        return f"{declared} (declared)" if declared is not None else "not provided — unknown"

    def _sensitivity_row() -> str:
        # Session INTAKEFIX-1, closing REPORTFIX-1's F-6. This said "(see
        # conversion note)" and `report/` NEVER EMITS ONE: the units-conversion
        # note is a webapp insertion, added to the finished markdown by
        # `webapp/worker.py::_insert_note_after_machine_details`. So on every
        # generator, CLI and eval path the reader was sent to a note that is not
        # in the document, and on the webapp path to one that may or may not be
        # -- that function inserts nothing when there is nothing to insert.
        #
        # The honest fix is to stop making a cross-reference this module cannot
        # keep. What the row has to say is what was NOT provided, and it says it;
        # where a conversion note does exist it is a few lines above, under
        # Machine Details, and needs no pointer from here.
        #
        # INTAKEFIX-1 scoped this narrowly on purpose (operator ruling R-1) and
        # left the three sibling strings in `_amplitude_units_row` above, because
        # the pin that asserts two of them (`tests/test_report2_units.py`) was
        # outside that session's scope. It is inside Session REPORT-4's, and F-1
        # is closed there — see that function's own docstring.
        declared = acq.sensor_sensitivity_mv_per_g if acq is not None else None
        if declared is not None:
            return f"{declared:g} mV/g (declared)"
        return "not provided — amplitudes used as supplied"

    _integration_labels = {
        "none": "none — data as sensed (declared)",
        "hardware": "integrated in the instrument (declared)",
        "software": "integrated in software after capture (declared)",
    }

    def _integration_row() -> str:
        declared = acq.integration if acq is not None else None
        if declared is not None:
            return _integration_labels.get(declared, f"{declared} (declared)")
        return "not provided — velocity values taken as supplied"

    rows: list[tuple[str, str]] = [
        ("Sample rate (fs)", _sample_rate()),
        ("Spectral lines (N)", _lines_row()),
        ("Line spacing (Δf)", _fmt(_line_spacing(first), 3, " Hz") if first is not None else "not recorded"),
        ("Fmax", _fmax_row()),
        ("Window", _window_row()),
        ("Averages", _averages_row()),
        ("Sensor sensitivity", _sensitivity_row()),
        ("Integration before export", _integration_row()),
        # Type AND unit, per spectrum (Session REPORT-2) -- the same phrase the
        # figure titles, captions and headers use, so they cannot disagree.
        ("Spectrum type", ", ".join(sorted({
            spectrum_kind_and_unit(case, s) for s in all_spectra
        })) or "not recorded"),
        # Session REPORT-4 (item 5): no "(pdm_core Layer 5)". The row says what
        # the detection DOES, which is the analyst's question; the name of the
        # module and the number of the layer inside it answer a maintainer's.
        ("Detection", "peak-pick against computed fault frequencies"),
        # Spectrum amplitude units are never inferred from the array. Where the
        # Case's provenance names one (an upload converted to mm/s RMS, a benchmark
        # or scaled-WAV acceleration in g) the row states it; otherwise it stays
        # the honest non-value it always was, and the conversion note says what
        # the upload declared.
        ("Spectrum amplitude units", _amplitude_units_row(case, all_spectra)),
        ("Severity units", "mm/s RMS (ISO 20816-3)"),
    ]
    if axis_names:
        # THE one surviving axis letter, in parentheses. In route order, not dict
        # order, so a three-channel job reads H, V, A the way the form collects it.
        rows.append((
            "Measurement channel",
            ", ".join(f"{axis_names[a]} ({a})" for a in ("y", "z", "x") if a in axis_names),
        ))
    if profile:
        rows.append(("Threshold profile", profile))
    if result.iso is not None:
        rows.append(("Threshold source", f"{result.iso.threshold_source} — {result.iso.threshold_note}"))
    if thresholds is not None:
        tol = thresholds.get("rca", {}).get("tolerance_pct")
        if tol is not None:
            rows.append(("Match tolerance", f"±{tol:g}%"))
        floor_min = thresholds.get("rca", {}).get("floor_min")
        if floor_min is not None:
            rows.append(("Amplitude floor", f"{floor_min:g}× spectrum mean"))
    if result.rca is not None and result.rca.rpm:
        rows.append(("Running speed", f"{result.rca.rpm:.0f} rpm "
                                      f"({result.rca.shaft_freq_hz:.2f} Hz shaft rate)"))
    machine = case.machine if case is not None else None
    if machine is not None:
        rows.extend(machine_geometry_rows(machine, thresholds=thresholds))
    return rows


# ─────────────────────────────────────────────────────────────────────────
# Session GEOM-A — declared machine geometry
#
# A row appears only when the analyst DECLARED the field. Absence is not
# silence: the coverage roster (report/generate.py::coverage_context) names
# every family that went unassessed for want of an input, row for row, keyed
# to the audit. Printing ten "not provided" rows here as well would say the
# same thing twice and bury the values that ARE on file.
#
# Nothing here computes a diagnosis or a threshold. The one derived number,
# the belt fundamental, was computed at intake in the adapter layer
# (adapters/uploads/common.py::belt_spec_from_form) and is reprinted with the
# formula and the dimensions it came from, so a reader can redo it by hand.
# ─────────────────────────────────────────────────────────────────────────

_DRIVE_TYPE_LABELS = {
    "direct_on_line": "direct on line (declared)",
    "vfd": "variable-frequency drive (declared)",
    "soft_starter": "soft starter (declared)",
}


def _belt_rows(machine: MachineMeta, *, thresholds: dict[str, Any] | None) -> list[tuple[str, str]]:
    from vib_agent.adapters.uploads.common import BELT_DERIVATION_CAVEAT

    belt = machine.belt
    if belt is None:
        return []
    rows = [("Belt fundamental", f"{belt.freq_hz:.2f} Hz")]
    if belt.drive_pulley_mm is None or belt.belt_length_mm is None:
        # A Case JSON may still carry `freq_hz` alone, exactly as it could
        # before this session. Saying where it came from is the honest half.
        rows.append(("Belt derivation", "supplied directly — no pulley geometry recorded"))
        return rows
    tol = None
    if thresholds is not None:
        tol = thresholds.get("rca", {}).get("tolerance_pct")
    matched = f" Matched against the spectrum at ±{tol:g}%." if tol is not None else ""
    rows.append((
        "Belt derivation",
        f"Drive pulley {belt.drive_pulley_mm:g} mm, driven {belt.driven_pulley_mm:g} mm, "
        f"centres {belt.center_distance_mm:g} mm. "
        f"L = 2C + (π/2)(D1+D2) + (D2−D1)²/(4C) = {belt.belt_length_mm:.1f} mm; "
        f"belt frequency = π·D1·N/(60·L). "
        f"{BELT_DERIVATION_CAVEAT}{matched}"
    ))
    return rows


def machine_geometry_rows(
    machine: MachineMeta, *, thresholds: dict[str, Any] | None = None
) -> list[tuple[str, str]]:
    """The declared machine geometry, as Analysis-parameters rows — only the
    fields the analyst actually supplied."""
    rows: list[tuple[str, str]] = []
    if machine.coupled_stated:
        rows.append(("Coupling",
                     "coupled (declared)" if machine.coupled else "uncoupled (declared)"))
    if machine.blades is not None:
        rows.append(("Blade / vane count", f"{machine.blades} (declared)"))
    rows.extend(_belt_rows(machine, thresholds=thresholds))
    if machine.gear_teeth_driving is not None or machine.gear_teeth_driven is not None:
        driving = machine.gear_teeth_driving
        driven = machine.gear_teeth_driven
        parts = []
        if driving is not None:
            parts.append(f"driving {driving}")
        if driven is not None:
            parts.append(f"driven {driven}")
        rows.append(("Gear tooth counts", f"{', '.join(parts)} (declared)"))
    if machine.rotor_bars is not None:
        rows.append(("Rotor bars", f"{machine.rotor_bars} (declared)"))
    if machine.poles is not None:
        rows.append(("Motor poles", f"{machine.poles} (declared)"))
    if machine.line_freq_hz is not None:
        rows.append(("Line frequency", f"{machine.line_freq_hz:g} Hz (declared)"))
    if machine.drive_type is not None:
        rows.append(("Drive type",
                     _DRIVE_TYPE_LABELS.get(machine.drive_type, f"{machine.drive_type} (declared)")))
    return rows
