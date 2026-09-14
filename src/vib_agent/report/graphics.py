"""Evidence graphics — inline SVG about numbers the report already states.

Session V2-WIRE (WIRING.md slice W6b). Three graphics, each bound to a computed
fact and each carrying a caption that names it. A graphic whose fact is not
named is a picture of an unnamed number, and there is then no way to audit it.

Each one renders NOTHING rather than a guess: no arrays, no tolerance, no
geometry — no figure. Empty is a legitimate outcome here, not a failure.

WHAT IS NOT HERE, deliberately: the differential **ratio bars**. The four
numbers that graphic needs are computed by
`pdm_core/bearing_rca.py::_misalignment_amplitude_gate` and serialised ONLY into
the adjudication sentence; `DifferentialCandidate` carries no numeric field.
The prototype draws them by regex over that prose, cross-checked against the one
value that does survive. That is honest for a prototype and wrong to ship — the
report layer would be one wording change away from silently losing a figure. The
fix is `WIRING.md` §7.2 W6b-1, which adds the values to the model, and
`pdm_core/**` is latched. **Do not port the regex.**
"""

from __future__ import annotations

from html import escape
from typing import Any

from markupsafe import Markup

from vib_agent.models import AnalysisResult, Case, Spectrum
from vib_agent.pdm_core.staging import STAGE_LABELS
# Module reference, not names: the unit/order helpers are looked up at call time so
# a test can monkeypatch `charts.spectrum_unit_label` and see it bind here too.
from vib_agent.report import charts as _charts
from vib_agent.report.charts import (
    C_FAULT,
    C_INK,
    C_MATCH,
    C_MUTED,
    C_TRACE,
    ROLE_BEARING,
    ROLE_FLOW,
    _amplitude_floor_value,
    _channel_spectra,
    _peak_in_window,
)

_SANS = '"IBM Plex Sans", system-ui, sans-serif'
_MONO = '"IBM Plex Mono", ui-monospace, monospace'
_GRID = "#D8DCD7"
_PANEL = "#F6F7F5"

#: A white stroke painted UNDER the glyphs, so a rule crossing a label leaves it
#: legible. `paint-order:stroke` draws the stroke first, so it never eats the glyph.
_HALO = "paint-order:stroke;stroke:#FFFFFF;stroke-width:3.5;stroke-linejoin:round"

# ── type sizes, in viewBox units ─────────────────────────────────────────
#
# These are NOT points. The report column on A4 (184 mm of content, less the
# figure's own padding) renders a 900-unit box at roughly 0.75 scale, so a size
# here lands at about 0.56 of its number in points on paper. The prototype's
# 11 units came out at ~6.2 pt in this column — well under the ~8 pt floor the
# figure work set for exactly this reason, and it was only legible when the SVG
# was pulled out and viewed on its own. These sizes clear the floor at the size
# the graphic is actually printed.
_PT_SMALL = 15.0     # tick and value labels
_PT_MED = 16.5       # column names, callouts
_PT_LARGE = 18.0     # the stage ladder's rungs

#: The product's own ladder, IMPORTED rather than quoted — dict order is the
#: ladder's order, and `not_determinable` is not a rung. The prototype kept a
#: copy of these labels and asserted the two agreed; a live port has no reason
#: to hold a second copy at all.
STAGE_ORDER: tuple[str, ...] = tuple(k for k in STAGE_LABELS if k != "not_determinable")


def _e(value: Any) -> str:
    return escape(str(value), quote=False)


def _figure(svg: str, caption: str, *, kind: str) -> str:
    """One graphic plus the one line that says what computed fact it depicts."""
    return (f'<figure class="gfx" data-gfx="{_e(kind)}">'
            f'<div class="plot">{svg}</div>'
            f"<figcaption>{caption}</figcaption></figure>")


# ── plumbing: the same series, the same rules, as the figure above ────────


def _series_for(case: Case | None, axis: str, *, bearing: bool) -> Spectrum | None:
    """The spectrum a graphic for `axis` should zoom into.

    Keyed on the DETECTOR ROLE, not on whatever array is handy: the bearing
    detectors read the envelope and the 1×-family reads raw/velocity, so a
    graphic about a bearing match must show the series that match came from.
    Zooming a different array than the figure above it would be a second,
    unlabelled measurement.

    The role is asked for by CONTAINMENT, not by equality. When an adapter
    supplies one array under both keys — or supplies only an envelope — that
    single series carries BOTH roles and legitimately serves both families.
    Testing `(ROLE_BEARING in roles) == bearing` rejected it for every
    non-bearing finding, so `multiaxis_trio_caseA` and `b2_blower_trio` silently
    drew no evidence inset at all despite both having a computed and an observed
    frequency. Found by comparing the rendered coverage against the record of
    which fixtures are supposed to produce which graphic.
    """
    if case is None:
        return None
    wanted = ROLE_BEARING if bearing else ROLE_FLOW
    for spectrum, roles in _channel_spectra(case).get(axis, []):
        if wanted in roles:
            return spectrum
    return None


def _tolerance_pct(thresholds: dict[str, Any] | None) -> float | None:
    """The match tolerance the ANALYSIS used, from its own resolved thresholds —
    never a constant, so a reading analysed under another profile is not drawn
    with route's window."""
    if not thresholds:
        return None
    value = thresholds.get("rca", {}).get("tolerance_pct")
    return float(value) if value else None


def _floor_multiple(thresholds: dict[str, Any] | None) -> float | None:
    if not thresholds:
        return None
    value = thresholds.get("rca", {}).get("floor_min")
    return float(value) if value else None


# ── (a) the damage-stage ladder ──────────────────────────────────────────


def stage_ladder(damage_stage: Any, *, width: int = 900) -> Markup:
    """Where the committed stage sits on the product's own three-rung ladder.

    Stages 1 and 2 are NOT drawn. They are not on the product's ladder, and
    drawing them would imply a resolution vibration monitoring does not have —
    which is the point the stage's own limitation sentence already makes in
    words, directly above this figure.
    """
    stage = getattr(damage_stage, "stage", None)
    if stage not in STAGE_ORDER:
        return Markup("")
    n = len(STAGE_ORDER)
    gap, pad, box_h = 14, 8, 56
    box = (width - pad * 2 - gap * (n - 1)) / n
    height = box_h + 38
    out = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="damage stage ladder, this reading at {_e(STAGE_LABELS[stage])}" '
        f'style="font-family:{_SANS}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
    ]
    for i, rung in enumerate(STAGE_ORDER):
        x = pad + i * (box + gap)
        here = rung == stage
        out.append(
            f'<rect x="{x:.1f}" y="0" width="{box:.1f}" height="{box_h}" '
            f'fill="{"#FBF1F0" if here else _PANEL}" stroke="{C_FAULT if here else C_MUTED}" '
            f'stroke-width="{2 if here else 1}"/>'
        )
        out.append(
            f'<text x="{x + box / 2:.1f}" y="{box_h / 2 + 5:.1f}" text-anchor="middle" '
            f'font-size="{_PT_LARGE}" font-weight="{600 if here else 400}" '
            f'fill="{C_FAULT if here else C_MUTED}">{_e(STAGE_LABELS[rung])}</text>'
        )
        if here:
            out.append(
                f'<text x="{x + box / 2:.1f}" y="{box_h + 26:.1f}" text-anchor="middle" '
                f'font-size="{_PT_SMALL}" fill="{C_FAULT}" font-family="{_MONO}" '
                f'font-weight="600">this reading</text>'
            )
        if i < n - 1:
            ax = x + box + gap / 2
            out.append(
                f'<path d="M {ax - 4:.1f} {box_h / 2 - 5:.1f} L {ax + 4:.1f} {box_h / 2:.1f} '
                f'L {ax - 4:.1f} {box_h / 2 + 5:.1f} Z" fill="{C_MUTED}"/>'
            )
    out.append("</svg>")
    return Markup(_figure(
        "\n".join(out),
        "The damage-stage ladder this analysis uses, with the committed stage marked. "
        "Stages earlier than the first rung are not drawn because the method cannot "
        "resolve them — which is what the note above says in words.",
        kind="stage-ladder",
    ))


# ── (b) the bearing evidence map ─────────────────────────────────────────


def bearing_map(
    result: AnalysisResult, *, case: Case | None, thresholds: dict[str, Any] | None,
    width: int = 900,
) -> Markup:
    """Every computed bearing frequency against the loudest measured line within
    tolerance of it, on the axis the bearing call was made on.

    Bar height is that line's amplitude on a shared scale and the amplitude
    floor is drawn across all four — so "cleared the floor" and "did not" is a
    comparison the reader MAKES rather than one they are told.

    Renders only where a geometry was supplied: `bearing_freqs` is None whenever
    none was, and a map of four frequencies nobody computed would be four
    invented numbers.
    """
    rca = result.rca
    tol = _tolerance_pct(thresholds)
    if rca is None or rca.bearing_freqs is None or tol is None:
        return Markup("")
    axis = next((m.axis for m in rca.primary_findings
                 if m.fault.startswith("bearing_") and m.axis), None)
    if axis is None:
        return Markup("")
    spectrum = _series_for(case, axis, bearing=True)
    if spectrum is None or not spectrum.freq_hz:
        return Markup("")
    floor = _amplitude_floor_value(spectrum, thresholds)
    multiple = _floor_multiple(thresholds)
    if floor is None or multiple is None:
        return Markup("")

    shaft = rca.shaft_freq_hz  # orders beside Hz (Session REPORT-2, item 3)
    freqs = {k: getattr(rca.bearing_freqs, k, None) for k in ("FTF", "BSF", "BPFO", "BPFI")}
    order = [k for k in ("FTF", "BSF", "BPFO", "BPFI") if freqs.get(k)]
    if not order:
        return Markup("")
    hits = {k: _peak_in_window(spectrum, float(freqs[k]), tol) for k in order}
    committed = {
        m.fault.replace("bearing_outer_race", "BPFO").replace("bearing_inner_race", "BPFI")
        .replace("bearing_ball_spin", "BSF").replace("bearing_cage", "FTF")
        for m in rca.primary_findings
        if m.fault.startswith("bearing_") and m.axis == axis
    }

    col_w, pad_l, pad_t, plot_h = 208, 74, 34, 132
    width = max(width, pad_l + col_w * len(order) + 24)
    height = pad_t + plot_h + 96
    top = max([hits[k][1] for k in order] + [floor]) * 1.25 or 1.0

    def y(amp: float) -> float:
        return pad_t + plot_h - (min(amp, top) / top) * plot_h

    out = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="computed bearing fault frequencies against the loudest measured line '
        f'within tolerance on axis {_e(axis)}" style="font-family:{_SANS}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
        f'<line x1="{pad_l - 10}" y1="{pad_t + plot_h}" x2="{width - 12}" '
        f'y2="{pad_t + plot_h}" stroke="{C_MUTED}"/>',
        f'<line x1="{pad_l - 10}" y1="{y(floor):.1f}" x2="{width - 12}" y2="{y(floor):.1f}" '
        f'stroke="{C_MUTED}" stroke-width="1.4" stroke-dasharray="9 3 2 3"/>',
        f'<text x="{pad_l - 14}" y="{y(floor) + 5:.1f}" text-anchor="end" '
        f'fill="{C_MUTED}" font-family="{_MONO}" font-size="{_PT_SMALL}">floor</text>',
    ]
    for i, key in enumerate(order):
        cx = pad_l + col_w * i + col_w / 2
        hit_f, hit_a = hits[key]
        is_committed = key in committed
        colour = C_MATCH if is_committed else C_MUTED
        bar_h = (pad_t + plot_h) - y(hit_a)
        out.append(
            f'<rect x="{cx - 26:.1f}" y="{y(hit_a):.1f}" width="52" height="{bar_h:.1f}" '
            f'fill="{colour}" opacity="{0.95 if is_committed else 0.32}"/>'
        )
        # The floating value label is drawn ONLY for a line that actually clears
        # the floor. A sub-floor bar is a few pixels tall, so a label offset
        # above its top lands ABOVE the floor rule — which is the very thing the
        # reader is being asked to judge, stated backwards. Sub-floor amplitudes
        # go into the text stack under the axis instead, where no vertical
        # position implies anything.
        if hit_a >= floor:
            out.append(
                f'<text x="{cx:.1f}" y="{y(hit_a) - 8:.1f}" text-anchor="middle" '
                f'font-size="{_PT_SMALL}" fill="{colour}" font-family="{_MONO}" '
                f'font-weight="{600 if is_committed else 400}" style="{_HALO}">'
                f'{hit_a:.4f}</text>'
            )
        out.append(
            f'<text x="{cx:.1f}" y="{pad_t + plot_h + 24}" text-anchor="middle" font-size="{_PT_MED}" '
            f'font-weight="600" fill="{C_FAULT if is_committed else C_INK}" '
            f'font-family="{_MONO}">{_e(key)}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="{pad_t + plot_h + 45}" text-anchor="middle" '
            f'font-size="{_PT_SMALL}" fill="{C_MUTED}" font-family="{_MONO}">'
            f'computed {_charts.fmt_hz_order(float(freqs[key]), shaft)}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="{pad_t + plot_h + 65}" text-anchor="middle" '
            f'font-size="{_PT_SMALL}" fill="{colour}" font-family="{_MONO}">'
            f'peak {_charts.fmt_hz_order(hit_f, shaft)} · {hit_a:.4f}</text>'
        )
        # `below floor` gets its OWN row rather than a suffix: as a suffix the
        # line ran to ~36 characters and adjacent stacks overprinted each other
        # at a narrower column. It never competes with COMMITTED — a line that
        # cleared the floor is not below it.
        if hit_a < floor:
            out.append(
                f'<text x="{cx:.1f}" y="{pad_t + plot_h + 85}" text-anchor="middle" '
                f'font-size="{_PT_SMALL}" fill="{C_MUTED}" font-family="{_MONO}">below floor</text>'
            )
        if is_committed:
            out.append(
                f'<text x="{cx:.1f}" y="{pad_t + plot_h + 85}" text-anchor="middle" '
                f'font-size="{_PT_SMALL}" fill="{C_MATCH}" font-family="{_MONO}" '
                f'font-weight="600">COMMITTED</text>'
            )
    out.append("</svg>")

    named = ", ".join(f"{k} {_charts.fmt_hz_order(float(freqs[k]), shaft)}" for k in order)
    series = _charts.spectrum_kind_and_unit(case, spectrum, lower=True)
    return Markup(_figure(
        "\n".join(out),
        f"Every fault frequency computed from the supplied geometry — {_e(named)} — against the "
        f"loudest measured line within ±{tol:g}% of it on axis {_e(axis)} of the {_e(series)}. "
        f"Bar height is that line's amplitude; the dashed rule is the amplitude floor a match has to clear, "
        # The floor's VALUE is a draw-time product of the spectrum mean and the
        # configured multiple. The MULTIPLE is in the analysis parameters, so it
        # is what the caption quotes; the figure labels the line itself.
        f"{multiple:g}× the spectrum mean. Only a frequency the analysis committed to is filled.",
        kind="bearing-map",
    ))


# ── (c) the per-finding evidence inset ───────────────────────────────────


def evidence_insets(
    result: AnalysisResult, evidence_rows: list[dict[str, Any]], *,
    case: Case | None, thresholds: dict[str, Any] | None, width: int = 900,
    plot_h: int = 150,
) -> Markup:
    """One zoomed window per evidence row — the ±tolerance band, with the
    measured series inside it.

    The row IS the committed diagnosis: `evidence_rows` is built one per finding
    and FILTERED, never fabricated, so a finding with no frequencies produces no
    row here either. Everything drawn is a value the table above already prints,
    plus the amplitude of the line at the observed frequency, taken with the same
    loudest-in-window rule the analysis matched with.
    """
    tol = _tolerance_pct(thresholds)
    if tol is None:
        return Markup("")
    shaft = result.rca.shaft_freq_hz if result.rca is not None else 0.0
    out: list[str] = []
    for row in evidence_rows:
        axis, expected, observed = row.get("axis"), row.get("computed"), row.get("observed")
        if not axis or expected in (None, "—") or observed in (None, "—"):
            continue
        fault = row.get("fault") or ""
        spectrum = _series_for(case, str(axis), bearing=str(fault).startswith("bearing_"))
        if spectrum is None or not spectrum.freq_hz:
            continue
        floor = _amplitude_floor_value(spectrum, thresholds)
        if floor is None:
            continue
        unit = _charts.spectrum_unit_label(case, spectrum)
        svg = _inset_svg(
            spectrum, expected_hz=float(expected), observed_hz=float(observed),
            tol_pct=tol, floor=floor, axis=str(axis), label=str(row.get("label") or ""),
            width=width, plot_h=plot_h, unit=unit, shaft_hz=shaft,
        )
        if not svg:
            continue
        series = _charts.spectrum_kind_and_unit(case, spectrum, lower=True)
        out.append(_figure(
            svg,
            f"<b>{_e(row.get('label') or '')}</b> — the measured {_e(series)} on axis {_e(axis)} "
            f"within ±{tol:g}% of the computed {_charts.fmt_hz_order(float(expected), shaft)}. The shaded band is that "
            f"match tolerance; the dashed line is the computed frequency; the marker is the "
            f"loudest measured line inside the band.",
            kind="evidence-inset",
        ))
    return Markup("".join(out))


def _inset_svg(
    spectrum: Spectrum, *, expected_hz: float, observed_hz: float, tol_pct: float,
    floor: float, axis: str, label: str, width: int, plot_h: int,
    unit: str = _charts.SPECTRUM_UNIT_AS_SUPPLIED, shaft_hz: float = 0.0,
) -> str:
    lo_t, hi_t = expected_hz * (1 - tol_pct / 100), expected_hz * (1 + tol_pct / 100)
    span = hi_t - lo_t
    lo, hi = expected_hz - span * 2.2, expected_hz + span * 2.2
    points = [(f, a) for f, a in zip(spectrum.freq_hz, spectrum.amplitude) if lo <= f <= hi]
    if len(points) < 3:
        return ""
    observed_hz, observed_amp = _peak_in_window(spectrum, expected_hz, tol_pct)

    pad_l, pad_r, pad_t, pad_b = 74, 24, 38, 62
    inner = width - pad_l - pad_r
    height = pad_t + plot_h + pad_b
    # 1.45, not 1.30: the callout sits 17 units above the marker and the marker
    # 26 above the peak, so a peak that fills 77 % of the panel leaves the label
    # jammed against the top edge — visible on both `demo_bpfo_6206` and
    # `b2_blower_trio`, where the matched peak IS the tallest line in the window
    # by construction. This is a LAYOUT reservation, not a change of scale: the
    # same amount of curve is drawn either way.
    top = max(max(a for _, a in points), floor) * 1.45 or 1.0

    def x(freq: float) -> float:
        return pad_l + (freq - lo) / (hi - lo) * inner

    def y(amp: float) -> float:
        return pad_t + plot_h - (min(amp, top) / top) * plot_h

    out = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" '
        f'aria-label="measured spectrum within plus or minus {tol_pct:g} percent of the computed '
        f'{expected_hz:.2f} hertz on axis {_e(axis)}" style="font-family:{_SANS}">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#FFFFFF"/>',
        f'<rect x="{pad_l}" y="{pad_t}" width="{inner}" height="{plot_h}" fill="#FFFFFF" '
        f'stroke="{_GRID}"/>',
        # the tolerance band — what the word "within" means, drawn
        f'<rect x="{x(lo_t):.1f}" y="{pad_t}" width="{x(hi_t) - x(lo_t):.1f}" height="{plot_h}" '
        f'fill="{C_FAULT}" opacity="0.09"/>',
    ]
    if floor <= top:
        out.append(
            f'<line x1="{pad_l}" y1="{y(floor):.1f}" x2="{pad_l + inner}" y2="{y(floor):.1f}" '
            f'stroke="{C_MUTED}" stroke-width="1.4" stroke-dasharray="9 3 2 3"/>'
        )
        out.append(
            f'<text x="{pad_l + inner - 4}" y="{y(floor) - 6:.1f}" text-anchor="end" '
            f'font-size="{_PT_SMALL}" fill="{C_MUTED}" font-family="{_MONO}" style="{_HALO}">'
            f'amplitude floor {floor:.4f}</text>'
        )
    out.append(
        f'<line x1="{x(expected_hz):.1f}" y1="{pad_t}" x2="{x(expected_hz):.1f}" '
        f'y2="{pad_t + plot_h}" stroke="{C_FAULT}" stroke-width="1.4" stroke-dasharray="7 4"/>'
    )
    out.append(
        '<polyline points="' + " ".join(f"{x(f):.1f},{y(a):.1f}" for f, a in points)
        + f'" fill="none" stroke="{C_TRACE}" stroke-width="2"/>'
    )
    ox, oy = x(observed_hz), y(observed_amp)
    out.append(
        f'<path d="M {ox - 8:.1f} {oy - 17:.1f} L {ox + 8:.1f} {oy - 17:.1f} '
        f'L {ox:.1f} {oy - 5:.1f} Z" fill="{C_MATCH}"/>'
    )
    out.append(
        f'<text x="{ox:.1f}" y="{oy - 26:.1f}" text-anchor="middle" font-size="{_PT_MED}" '
        f'font-weight="600" fill="{C_MATCH}" style="{_HALO}">{_e(label)}</text>'
    )
    # The band's two edges are labelled OUTWARD from the band; the computed and
    # observed values share one centred line below them.
    #
    # All four sat on one row in the prototype, each centred on its own
    # frequency. At this figure's real width in the report column that row reads
    # `-3% 103.8?omputed 107.03 H?3% 110.24`: the three are only a few percent
    # apart in Hz, which is wide in data space and narrow on the page — exactly
    # what lane assignment exists to prevent on the channel figures. Found by
    # pulling the SVG out and viewing it at the column's actual width.
    row_1 = pad_t + plot_h + 22
    row_2 = pad_t + plot_h + 46
    for freq, text, anchor, dx in (
        (lo_t, f"−{tol_pct:g}%  {lo_t:.2f}", "end", -6),
        (hi_t, f"+{tol_pct:g}%  {hi_t:.2f}", "start", 6),
    ):
        out.append(
            f'<text x="{x(freq) + dx:.1f}" y="{row_1}" text-anchor="{anchor}" '
            f'font-size="{_PT_SMALL}" fill="{C_FAULT}" font-family="{_MONO}" '
            f'style="{_HALO}">{_e(text)}</text>'
        )
    out.append(
        f'<text x="{x(expected_hz):.1f}" y="{row_2}" text-anchor="middle" '
        f'font-size="{_PT_SMALL}" font-family="{_MONO}" style="{_HALO}">'
        f'<tspan fill="{C_FAULT}">computed {_charts.fmt_hz_order(expected_hz, shaft_hz)}</tspan>'
        f'<tspan fill="{C_MUTED}">   ·   </tspan>'
        f'<tspan fill="{C_MATCH}">observed {_charts.fmt_hz_order(observed_hz, shaft_hz)} · {observed_amp:.4f}</tspan>'
        f"</text>"
    )
    out.append(
        f'<text transform="translate(18,{pad_t + plot_h / 2:.1f}) rotate(-90)" '
        f'text-anchor="middle" font-size="{_PT_MED}" fill="{C_INK}">'
        f'{_e(_charts.amplitude_axis_label(unit))}</text>'
    )
    out.append("</svg>")
    return "\n".join(out)
