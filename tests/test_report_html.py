"""The v2 HTML report — parity with the markdown report, and the honesty guards.

Session V2-WIRE. `design/report_v2_proto/build_v2.py` proved these properties
against the prototype by re-deriving every KEEP-VERBATIM string from a golden
and re-checking it against the rendered page. That check lived outside the
suite, so it could not gate a commit. This is its port: the same properties,
computed against the LIVE renderer, in the product's own test run.

Four families, and each one has been shown to FAIL when the thing it protects is
deliberately broken — a guard that has never failed is not evidence:

  * PARITY   — every KEEP-VERBATIM string in the markdown report is in the HTML.
  * H2       — a non-PASS gate check may never be hidden, summarised, folded
               into a count, or demoted into the roster appendix.
  * N0       — every number in the HTML's prose traces to the analysis; no
               figure is invented, and no placeholder is filled in.
  * PDF text — the strings survive all the way into the rendered PDF, because
               CSS can hide an element and a page break can cut a table.
"""

from __future__ import annotations

import re
from html import unescape
from pathlib import Path

import pytest

from vib_agent.config import load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report.generate import (
    FAULT_LABELS,
    SUMMARISABLE,
    _machine_rows,
    render_html,
    render_markdown,
)
from vib_agent.synth.generator import make_case, make_history

pytestmark = pytest.mark.skipif(
    not charts_mod.matplotlib_available(), reason="matplotlib not installed ([pdf] extra)"
)


@pytest.fixture
def thresholds() -> dict:
    """`route`, named explicitly — this module's own, overriding the session one.

    The session fixture calls `load_thresholds()` with NO argument, which
    resolves `active_profile`: a default, not a decision. Every report rendered
    here names `profile="route"` in its own Analysis Parameters table, so
    analysing it under a different profile would make the document describe an
    analysis that did not happen — and it does: under `streaming` the
    route-only shaft-order gate never runs, and the populated-differential
    fixture came back empty, silently skipping the §4.8 contract check.
    """
    return load_thresholds("route")


# ── fixtures: the states the report can be in ────────────────────────────


def _analyse(case, iso_table, thresholds, rules):
    return case, run_analysis(case, iso_table=iso_table, thresholds=thresholds, rules=rules)


def _bpfo(iso_table, thresholds, rules, *, history=False):
    """The bearing case: damage stage, evidence table, the 11-cause section."""
    case = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    if history:
        case = case.model_copy(update={
            "history": make_history(30, 1.2, 5.2, noise_pct=0.12, cadence="daily", seed=7)})
    return _analyse(case, iso_table, thresholds, rules)


def _b2_blower(iso_table, thresholds, rules):
    """THE populated-differential fixture.

    A synthetic `make_case("imbalance", ...)` was tried first and produced an
    EMPTY differential, so the §4.8 adjudication check silently skipped — and a
    skipped contract check is not a check. This is the textbook imbalance trio
    the R3-DIFF shaft-order gate demotes `misalignment_general` out of, reused
    from the test module that owns it rather than copied, so the two cannot
    drift.
    """
    from tests.test_shaft_order_differential import _b2_blower_case

    return _analyse(_b2_blower_case(), iso_table, thresholds, rules)


def _accel_only(iso_table, thresholds, rules):
    """Acceleration-only: ISO zone not_assessable, the Severity & Coverage block."""
    base = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1)
    sd = base.sensor_data.model_copy(update={
        "x_velocity_mm_sec": None, "y_velocity_mm_sec": None, "z_velocity_mm_sec": None})
    return _analyse(base.model_copy(update={"sensor_data": sd}), iso_table, thresholds, rules)


def _gate_fail(iso_table, thresholds, rules):
    """The only fixture carrying a gate FAIL."""
    return _analyse(make_case("machine_off", iso_table=iso_table, thresholds=thresholds, seed=1),
                    iso_table, thresholds, rules)


def _healthy_no_geometry(iso_table, thresholds, rules):
    """No fault matched AND no bearing geometry, so the bearing screen never ran.

    That is NOT a clean bill, and the report says so: "none — but this reading
    is not clear". A reading with unexplained periodicity and nothing to explain
    it with must not read as a pass.
    """
    return _analyse(make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=3),
                    iso_table, thresholds, rules)


def _healthy_clean(iso_table, thresholds, rules):
    """The actual clean bill: geometry supplied, bearing screen ran, nothing
    found. "none — parameters within normal range", and the screened-for list."""
    healthy = make_case("healthy", iso_table=iso_table, thresholds=thresholds, seed=3)
    geometry = make_case("bpfo", iso_table=iso_table, thresholds=thresholds, seed=1).machine.bearing
    assert geometry is not None, "the bpfo fixture no longer supplies a bearing geometry"
    case = healthy.model_copy(update={
        "machine": healthy.machine.model_copy(update={"bearing": geometry})})
    return _analyse(case, iso_table, thresholds, rules)


_BUILDERS = {
    "bpfo": _bpfo,
    "trend": lambda i, t, r: _bpfo(i, t, r, history=True),
    "b2_blower": _b2_blower,
    "accel_only": _accel_only,
    "gate_fail": _gate_fail,
    "healthy_no_geometry": _healthy_no_geometry,
    "healthy_clean": _healthy_clean,
}


def _render_pair(name, tmp_path, iso_table, thresholds, rules):
    """The markdown and the HTML, from ONE analysis and ONE chart manifest."""
    case, result = _BUILDERS[name](iso_table, thresholds, rules)
    out = tmp_path / name
    charts = charts_mod.render_charts(result, case.machine, out, case=case,
                                      thresholds=thresholds, fault_labels=FAULT_LABELS)
    md = render_markdown(result, case.machine, charts=charts, case=case,
                         thresholds=thresholds, profile="route")
    html = render_html(result, case.machine, charts=charts, case=case,
                       thresholds=thresholds, profile="route")
    return case, result, md, html


# ── turning HTML back into what a reader sees ────────────────────────────

_STYLE = re.compile(r"<style\b.*?</style>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def visible_text(html: str) -> str:
    """The page's visible text: no stylesheet, no tags, entities resolved.

    This is deliberately what a READER sees rather than what the markup says —
    a check that greps the HTML source would pass on an element that CSS hides.
    """
    body = _STYLE.sub(" ", html)
    return unescape(_TAG.sub(" ", body))


def squash(text: str) -> str:
    """Collapse whitespace so a string that wrapped differently still matches.

    Wrapping, line breaks and typography may change; wording, ordering within an
    item, and every bracketed locator may not — so this normalises exactly the
    former and nothing else. Space before closing punctuation is collapsed too:
    `visible_text` replaces every tag with a space, so `<code>x</code>.` comes
    back as `x .`, and that is an artifact of the stripper rather than anything
    a reader would see.
    """
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:!?)\]])", r"\1", text)


def plain_md(text: str) -> str:
    """The markdown report reduced to the words a reader of the PDF sees.

    Only the emphasis MARKERS are removed — `**bold**`, `*italic*` and
    `` `code` `` all render as styled text, so their markers are noise for a
    comparison. Underscores are left alone: the signature block is a row of
    them and they are content there.

    This deliberately does NOT run the markdown through `md_inline`. That
    converter is applied by the renderer to one FIELD at a time, and running it
    over a whole document mispairs — a `**` in one paragraph can capture a `*`
    three paragraphs later. The renderer never does that; a test helper should
    not either.

    Applied to the markdown side ONLY. The HTML side is left exactly as
    rendered, so an emphasis marker that leaked through into the page still
    fails the comparison instead of being normalised away.
    """
    return squash(re.sub(r"[*`]", "", text))


#: An element that is present in the markup but not on the page. Removing a
#: check and HIDING one are the same defect to a reader, and `visible_text`
#: cannot tell them apart — it strips tags, it does not resolve CSS.
_HIDING = re.compile(
    r"<[a-z]+[^>]*(?:\shidden[\s/>]|display\s*:\s*none|visibility\s*:\s*hidden)[^>]*>",
    re.I,
)


def _section(html: str, title: str) -> str:
    """One numbered section's HTML, from its heading to the next one.

    The title is HTML-escaped before matching: two section headings contain an
    ampersand, and searching the raw markup for a bare `&` silently returned an
    EMPTY section — which made the assertion that ran on it vacuous rather than
    failing. Found by writing a test that passed for the wrong reason.
    """
    needle = re.escape(title.replace("&", "&amp;"))
    m = re.search(rf"<h2>[^<]*·\s*{needle}.*?</h2>(.*?)(?=<h2>|</main>)", html, re.S | re.I)
    return m.group(1) if m else ""


# ── PARITY: the words are the product ────────────────────────────────────


class TestKeepVerbatim:
    """Every KEEP-VERBATIM string the markdown report emits is in the HTML.

    These are the strings PARITY.md tags KEEP-VERBATIM: the words themselves are
    the product, so the container may be restyled and the text may not. Each is
    asserted on the fixture that actually reaches its branch — a string with no
    fixture is not asserted here, because asserting it against a report that
    cannot emit it proves nothing.
    """

    CASES: list[tuple[str, str]] = [
        # §1.5 / §11.1 / §11.2 — the draft framing and the blank signature rules
        ("bpfo", "DRAFT — prepared by automated analysis, pending analyst review."),
        ("bpfo", "This report is a draft produced by automated analysis. It is not a certified "
                 "condition assessment until reviewed and signed below."),
        ("bpfo", "Reviewed and approved by: ______________________________  "
                 "(analyst, cert level: __________)"),
        ("bpfo", "Date: ______________________"),
        # §7.1 — the spectral-evidence preamble
        ("bpfo", "No figure introduces a number that is not already in this report."),
        # §5.3 — the damage-stage hedge
        ("bpfo", "A discrete defect frequency is the earliest damage standard vibration "
                 "monitoring can resolve; early-stage detection requires HF/ultrasonic trending, "
                 "so anything earlier than this could not have been seen and its absence is not "
                 "evidence of a healthy bearing."),
        # §9.2 / §9.3 — the cause preamble and its legend
        ("bpfo", "none of them is confirmed by this measurement, and this analysis did not "
                 "perform the inspection that would settle any of them."),
        ("bpfo", "A vibration reading identifies the fault; it does not identify what caused it."),
        ("bpfo", "Bracketed references resolve against references/INDEX.md."),
        # §9.7 — the collectibility split, the section's central structural idea
        ("bpfo", "Checked by this analysis"),
        ("bpfo", "Evidence to collect — analyst fieldwork; not performed by this analysis:"),
        # §10.2 — a STATED nothing, not an empty section
        ("bpfo", "None — the available data resolved the diagnosis without further measurement."),
        # §2.8 — the acceleration-only honesty block, both paragraphs
        ("accel_only", "What was assessed:"),
        ("accel_only", "Any findings above stand on that evidence alone."),
        ("accel_only", "What was not assessed:"),
        ("accel_only", "and severity is a velocity judgement, so it is left unrated. A velocity "
                       "measurement (mm/s RMS, per ISO 20816) is required to establish it"),
        # §4.1 — insufficient data
        ("gate_fail", "Insufficient data — no diagnosis made."),
        ("gate_fail", "The reading did not pass quality checks, so no fault assessment is "
                      "possible. See Recommended Follow-up Measurements for exactly what to "
                      "collect next."),
        # §4.3 — a clean bill is a positive result, not an absence of analysis
        ("healthy_clean", "Committed diagnosis: none — parameters within normal range."),
        ("healthy_clean", "No fault signature matched the evidence in this reading. This is a "
                          "positive result, not an absence of analysis: the following were "
                          "screened and came back clear."),
        # §4.2 — and a reading with unexplained periodicity and no geometry to
        # explain it with is NOT a clean bill, however few faults matched.
        ("healthy_no_geometry", "Committed diagnosis: none — but this reading is not clear."),
        ("healthy_no_geometry", "No fault signature matched, and no bearing geometry was supplied "
                                "— so bearing fault frequencies could not be computed and the "
                                "bearing screen never ran."),
        ("healthy_no_geometry", "That periodicity is at no shaft order this analysis models."),
    ]

    @pytest.mark.parametrize("fixture,phrase", CASES)
    def test_phrase_survives_into_the_html(
        self, fixture, phrase, tmp_path, iso_table, thresholds, rules
    ):
        _, _, md, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)
        assert squash(phrase) in plain_md(md), (
            f"{fixture}: the markdown no longer emits this string — the ASSERTION is stale, "
            f"not the HTML: {phrase!r}"
        )
        assert squash(phrase) in squash(visible_text(html)), (
            f"{fixture}: KEEP-VERBATIM string missing from the rendered HTML: {phrase!r}"
        )


class TestEveryComputedItemTravels:
    """Not one sentence but EVERY item: nothing the analysis produced is dropped,
    truncated or capped on the way into the HTML."""

    def test_every_confidence_factor_appears(self, tmp_path, iso_table, thresholds, rules):
        _, result, _, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        text = squash(visible_text(html))
        factors = [f for finding in result.findings
                   for f in finding.evidence.get("confidence_factors", [])]
        assert factors
        for factor in factors:
            assert squash(factor) in text, f"confidence factor dropped: {factor!r}"

    def test_every_cause_and_every_citation_appears_in_library_order(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """§9.4/§9.9 — no cap, no truncation, and every bracketed locator whole.

        Truncating a locator is a contract breach, not an abbreviation: a
        citation that is complete where it is used cannot be shortened by a list
        that drifts.
        """
        _, _, md, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        text = squash(visible_text(html))
        locators = re.findall(r"\[#\d+[^\]]*\]", md)
        assert len(locators) > 20, "fixture no longer exercises the cause section"
        for locator in locators:
            assert squash(locator) in text, f"citation locator truncated or dropped: {locator!r}"

        names = re.findall(r"^### (.+)$", md, re.M)
        # Session REPORT-4 (item 7) changed the finding heading from
        # "<fault> — severity: info, confidence: high" to
        # "<fault> — confidence: high · damage stage: 3 (early)", so a filter
        # keyed only on " — severity:" started reading the finding heading as a
        # CAUSE name and then failed to find it among the causes. Both spellings
        # are excluded: "severity:" still appears on an unrated reading, where
        # the tag keeps the reason severity could not be rated.
        cause_names = [n for n in names if not n.startswith("Envelope")
                       and not n.startswith("Velocity")
                       and " — severity:" not in n and " — confidence:" not in n]
        positions = [text.find(squash(n)) for n in cause_names]
        assert all(p >= 0 for p in positions), "a cause name is missing from the HTML"
        assert positions == sorted(positions), "causes are not in the library's own order"

    def test_every_limitation_and_recommendation_appears(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _, _, md, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        text = squash(visible_text(html))
        for block in ("Limitations & Confidence Notes", "Recommendations"):
            body = re.search(rf"## {re.escape(block)}\n(.*?)(?=\n## |\Z)", md, re.S)
            assert body, block
            for line in re.findall(r"^(?:- |\d+\. )(.+)$", body.group(1), re.M):
                assert squash(line) in text, f"{block}: dropped {line!r}"

    def test_the_adjudication_travels_whole(self, tmp_path, iso_table, thresholds, rules):
        """§4.8 — the ratio arithmetic in an adjudication IS the argument.

        Truncating it to "Downgraded on amplitude" is a contract breach.
        """
        _, result, _, html = _render_pair("b2_blower", tmp_path, iso_table, thresholds, rules)
        candidates = result.rca.differential if result.rca else []
        # Asserted, never skipped: if this fixture stops producing a differential
        # the FIXTURE needs fixing, and a green skip would hide that the most
        # load-bearing KEEP-VERBATIM item in the report is no longer covered.
        assert candidates, "the b2_blower fixture no longer produces a differential candidate"
        text = squash(visible_text(html))
        for candidate in candidates:
            assert squash(candidate.adjudication) in text, (
                f"adjudication truncated: {candidate.adjudication!r}")

    def test_machine_details_match_the_markdown_table_row_for_row(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The HTML builds its Machine Details rows in Python; the markdown
        builds them in Jinja. They must not drift, so the markdown table is
        parsed and compared — including every one of its own `—` defaults."""
        for fixture in ("bpfo", "accel_only"):
            case, result, md, _ = _render_pair(fixture, tmp_path / "mr", iso_table,
                                               thresholds, rules)
            block = re.search(r"## Machine Details\n\n(.*?)\n\n", md, re.S)
            assert block, fixture
            parsed = []
            for line in block.group(1).splitlines():
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) == 2 and cells[0] not in ("Field",) and set(cells[0]) != {"-"}:
                    parsed.append((cells[0], cells[1]))
            built = _machine_rows(
                result, case.machine,
                not_assessable=(result.iso is not None
                                and result.iso.iso_zone == "not_assessable"),
            )
            assert built == parsed, fixture


# ── H2: the gate honesty guard ───────────────────────────────────────────


class TestGateHonesty:
    """THE COVERAGE RULE, computed.

    Every check whose status is neither `pass` nor `not_applicable` must appear
    in the rendered page's visible text with its NAME, its STATUS and its REASON
    IN FULL, inside the data-quality section and ABOVE the roster. `PASS` and
    `NOT_APPLICABLE` rows — and only those — may be collapsed to a count.

    The scope matters and was got wrong in the prototype first: checked against
    the WHOLE page, the guard passed even with the attention list deliberately
    suppressed, because the gate ribbon under the masthead was still printing
    the same notes. A guard that cannot fail is not a guard. Scoped to the
    data-quality section, the same break fails on exactly the fixtures that
    carry a non-PASS check and on none of the others.
    """

    @pytest.mark.parametrize("fixture", sorted(_BUILDERS))
    def test_no_non_pass_check_is_hidden_summarised_or_demoted(
        self, fixture, tmp_path, iso_table, thresholds, rules
    ):
        _, result, _, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)
        section = _section(html, "Data quality")
        assert section, f"{fixture}: no data-quality section rendered at all"
        above_roster = squash(visible_text(section.split('<div class="roster"')[0]))

        # Hiding an element and deleting it are the same defect to a reader,
        # and stripping tags cannot tell them apart. Found by breaking this
        # guard on purpose: marking the attention list `hidden` left every
        # name, status and reason in the markup, and the text check passed.
        assert not _HIDING.search(section.split('<div class="roster"')[0]), (
            f"{fixture}: something in the data-quality section is hidden from the page")

        attention = [c for c in result.quality_gate.checks
                     if str(c.status).lower() not in SUMMARISABLE]
        for check in attention:
            assert check.name in above_roster, (
                f"{fixture}: check {check.name!r} is {check.status} and does not appear above "
                f"the roster")
            assert str(check.status).upper() in above_roster, (
                f"{fixture}: {check.name!r} appears without its status")
            if check.reason:
                assert squash(check.reason) in above_roster, (
                    f"{fixture}: {check.name!r} appears without its reason in full — "
                    f"{check.reason!r}")

    @pytest.mark.parametrize("fixture", sorted(_BUILDERS))
    def test_the_roster_still_carries_every_check(
        self, fixture, tmp_path, iso_table, thresholds, rules
    ):
        """Collapsing the passes is a display decision, not a deletion: the full
        roster is still in the document, as a plain section that prints."""
        _, result, _, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)
        roster = squash(visible_text(_section(html, "Data quality")))
        for check in result.quality_gate.checks:
            assert check.name in roster, f"{fixture}: {check.name!r} missing from the roster"

    @pytest.mark.parametrize("fixture", sorted(_BUILDERS))
    def test_not_applicable_rows_are_named_not_merely_counted(
        self, fixture, tmp_path, iso_table, thresholds, rules
    ):
        """So that "not applicable" can never be read as "not run"."""
        _, result, _, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)
        above_roster = squash(visible_text(
            _section(html, "Data quality").split('<div class="roster"')[0]))
        na = [c.name for c in result.quality_gate.checks
              if str(c.status).lower() == "not_applicable"]
        for name in na:
            assert name in above_roster, f"{fixture}: n/a check {name!r} counted but not named"


# ── N0: no invented numbers ──────────────────────────────────────────────

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


class TestNoInventedNumbers:
    """Every number in the HTML's prose traces to the analysis.

    The audit runs on PROSE, which is where a fabricated figure would mislead.
    Three categories are exempt and each is declared rather than assumed:

      * numbers rendered INSIDE a figure (the PNG's axis ticks) — they are
        display roundings computed at draw time, and they are pixels here
        anyway;
      * numbers inside an inline `<svg>`, for the same reason: an axis tick, a
        band edge at `expected x (1 ± tol)`, or a plotted amplitude is a
        DRAW-TIME product of values the analysis did compute. The graphics'
        CAPTIONS are prose and stay policed — which is where this audit earns
        its keep, because a caption is where a derived number would arrive
        looking like a measurement. `TestGraphicsQuoteComputedFrequencies`
        covers what the exemption gives up;
      * four numbers the data-quality panel DERIVES by counting the gate's own
        checks. They are as traceable as any value inside that array, but they
        are counts rather than values, so they are named explicitly instead of
        exempting integers generally. A panel that printed "14 checks passed"
        when thirteen did would still fail.
    """

    @pytest.mark.parametrize("fixture", sorted(_BUILDERS))
    def test_every_number_in_prose_traces_to_the_analysis(
        self, fixture, tmp_path, iso_table, thresholds, rules
    ):
        _, result, md, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)

        checks = result.quality_gate.checks
        derived = {
            str(len(checks)),
            str(len([c for c in checks if str(c.status).lower() == "pass"])),
            str(len([c for c in checks if str(c.status).lower() == "not_applicable"])),
            str(len([c for c in checks if str(c.status).lower() not in SUMMARISABLE])),
        }
        # The zone strip prints the machine's own ISO band boundaries, and the
        # severity caption prints the computed margin to the next one. Both come
        # off `result.iso`; both are formatted for display, so both forms count.
        # The bearing evidence map's CAPTION names all four computed fault
        # frequencies, and three of them are nowhere else in the report — the
        # markdown states only the committed one. They are computed by the
        # analysis and carried on `rca.bearing_freqs`, so they trace; this audit
        # asks whether a number traces to the ANALYSIS, not whether it also
        # happens to appear in the markdown. Declared here rather than exempting
        # captions, which is where a derived value would arrive looking like a
        # measurement.
        haystack_values: set[str] = set()
        freqs = result.rca.bearing_freqs if result.rca is not None else None
        if freqs is not None:
            for key in ("FTF", "BSF", "BPFO", "BPFI"):
                value = getattr(freqs, key, None)
                if value:
                    haystack_values |= {f"{value:g}", f"{value:.2f}", str(value)}

        iso = result.iso
        if iso is not None:
            for value in (iso.th_ab, iso.th_bc, iso.th_cd, iso.severity_rms,
                          iso.margin_to_next_boundary, iso.x_vel_mms, iso.y_vel_mms,
                          iso.z_vel_mms):
                if value is None:
                    continue
                haystack_values |= {f"{value:g}", f"{value:.2f}", str(value)}
            if iso.th_cd is not None:
                top = max(iso.th_cd * 1.55, (iso.severity_rms or 0) * 1.15)
                haystack_values.add(f"{top:g}")
                # The A band's lower edge is the literal zero the scale starts
                # at, not a measurement — the only band edge that is not one of
                # the three thresholds above.
                haystack_values.add("0")

        haystack = squash(md) + " " + " ".join(haystack_values) + " " + " ".join(derived)
        haystack_numbers = set(_NUMBER.findall(haystack))

        # Two pieces of DOCUMENT FURNITURE are removed before the audit, because
        # neither is a claim about the machine: the section numbers
        # ("7 · Spectral evidence") and the cause ordinals ("01", "02", ...),
        # which number a list rather than measure anything.
        prose = re.sub(r'<span class="n">\d+</span>', " ", html)
        prose = re.sub(r"<svg\b.*?</svg>", " ", prose, flags=re.S | re.I)
        prose = visible_text(prose)
        prose = re.sub(r"\b\d+\s*·\s*", " ", prose)
        for token in _NUMBER.findall(squash(prose)):
            assert token in haystack_numbers, (
                f"{fixture}: {token!r} appears in the HTML prose but traces to nothing in the "
                f"analysis — a figure has been invented or a placeholder filled in"
            )


# ── the strings have to survive all the way into the PDF ─────────────────


def _weasyprint_available() -> bool:
    try:
        import weasyprint  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _weasyprint_available(),
                    reason="weasyprint (with its native libs) not available here")
class TestSurvivesIntoThePdf:
    """CSS can hide an element, a print rule can drop one, and a page break can
    cut a table — so the last check reads the PDF's own text rather than the
    HTML that produced it."""

    def test_keep_verbatim_survives_rendering(self, tmp_path, iso_table, thresholds, rules):
        pypdf = pytest.importorskip("pypdf")
        from weasyprint import HTML

        case, result, _, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        out = tmp_path / "bpfo"
        (out / "report.html").write_text(html)
        HTML(string=html, base_url=str(out)).write_pdf(str(out / "report.pdf"))
        text = squash(" ".join(p.extract_text() or ""
                               for p in pypdf.PdfReader(str(out / "report.pdf")).pages))

        for phrase in (
            "DRAFT — prepared by automated analysis, pending analyst review.",
            "No figure introduces a number that is not already in this report.",
            "A vibration reading identifies the fault; it does not identify what caused it.",
            "Reviewed and approved by:",
            "Date: ______________________",
        ):
            assert squash(phrase) in text, f"lost between HTML and PDF: {phrase!r}"

        # Every gate check reaches paper: the roster is a plain section, not a
        # disclosure, precisely because a disclosure cannot be disclosed in a PDF.
        for check in result.quality_gate.checks:
            assert check.name in text, f"gate check {check.name!r} did not reach the PDF"


class TestNoLeakedMarkup:
    """No markdown marker reaches the page as a literal character.

    This is the class of defect that is invisible to every other check here,
    because the WORDS are identical either way: a report that prints
    `see *Recommended Follow-up Measurements*.` with its asterisks showing
    passes every parity assertion above. It has happened twice in this design's
    history — once with `**`, once with a single `*` — so it is now a test.
    """

    @pytest.mark.parametrize("fixture", sorted(_BUILDERS))
    def test_no_asterisks_or_backticks_survive_into_the_page(
        self, fixture, tmp_path, iso_table, thresholds, rules
    ):
        _, _, _, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)
        text = visible_text(html)
        for marker in ("**", "*", "`"):
            assert marker not in text, (
                f"{fixture}: a literal {marker!r} reached the rendered page — an emphasis marker "
                f"leaked instead of being converted"
            )


class TestSeverityTruthfulness:
    """A rejected reading asserts no severity.

    Found by reading the gate-fail PDF: the severity card read
    "Zone A — good", in the largest type on the page, for a machine whose data
    the quality gate had just refused — directly under a status line saying
    INSUFFICIENT DATA. `classify` does still run and does still return a zone,
    but doctrine is that nothing downstream of a failed gate is valid, and a
    card is a verdict in a way a table row is not.

    This is the same class of defect as the release that shipped "ISO Zone A"
    on known-faulted, acceleration-only machines. The v2 page did not introduce
    the zone; it promoted it to a headline, which is what made it dangerous.
    """

    def test_a_gate_fail_report_asserts_no_zone_verdict(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _, result, _, html = _render_pair("gate_fail", tmp_path, iso_table, thresholds, rules)
        assert result.quality_gate.overall == "fail", "this fixture no longer fails the gate"
        assert result.iso is not None and result.iso.iso_zone in ("A", "B", "C", "D"), (
            "the fixture no longer classifies at all, so this test proves nothing")

        cards = html[html.index('<div class="cards">'):html.index("<section>")]
        text = squash(visible_text(cards))
        assert "Severity not established" in text
        assert "The reading did not pass quality checks, so no severity is asserted." in text
        assert f"Zone {result.iso.iso_zone}" not in text, (
            "the severity card states a zone for a reading the gate rejected")

    def test_a_gate_fail_report_places_no_value_on_the_zone_scale(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The scale is still drawn — it exists — but nothing is marked on it,
        and the reason is stated. The measured figure is not hidden: it stays in
        the Machine Details table, where it is a record and not a verdict."""
        _, result, _, html = _render_pair("gate_fail", tmp_path, iso_table, thresholds, rules)
        severity = _section(html, "Severity")
        assert '<div class="bands">' in severity, "the zone scale was dropped entirely"
        assert 'class="mark"' not in severity, "a rejected reading was marked on the zone scale"
        assert "No value is marked" in squash(visible_text(severity))
        assert "Per-axis velocity" not in squash(visible_text(severity))

        rows = squash(visible_text(_section(html, "Machine & analysis parameters")))
        assert "Overall vibration" in rows, "the measured value was hidden rather than demoted"


class TestDraftedPage:
    """The agent-drafted document, rendered as the v2 page.

    The model writes the narrative and NOTHING else: the masthead, the status
    line, the gate ribbon, the meta strip, the card pair, the evidence
    appendix, the provenance panel, the signature rules and the footer are all
    rendered from the AnalysisResult, through the same macros the deterministic
    page uses. What that buys is the point of these tests — the honesty
    elements hold on a document whose prose came from a language model.
    """

    def _drafted(self, tmp_path, iso_table, thresholds, rules, narrative=None):
        from vib_agent.report.generate import render_drafted_html

        case, result = _bpfo(iso_table, thresholds, rules)
        out = tmp_path / "drafted"
        charts = charts_mod.render_charts(result, case.machine, out, case=case,
                                          thresholds=thresholds, fault_labels=FAULT_LABELS)
        if narrative is None:
            # What the drafting model is actually handed as its reference: the
            # deterministic report WITHOUT the cause section.
            narrative = render_markdown(result, case.machine, charts=None, case=case,
                                        thresholds=thresholds, profile="route",
                                        include_causes=False)
        html = render_drafted_html(narrative, result, case.machine, charts=charts, case=case,
                                   thresholds=thresholds, profile="route")
        assert html is not None, "the narrative could not be converted — is `markdown` installed?"
        return result, html

    def test_the_gate_ribbon_is_deterministic_on_the_drafted_path(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The coverage rule survives a model-written report because the ribbon
        is not model-written. Every non-PASS check, with its name, status and
        reason, above the diagnosis — on a page whose prose we did not author."""
        result, html = self._drafted(tmp_path, iso_table, thresholds, rules,
                                     narrative="# Vibration Survey Report — X\n\nNothing here.\n")
        ribbon = html[html.index('<div class="ribbon'):html.index('<div class="meta">')]
        text = squash(visible_text(ribbon))
        attention = [c for c in result.quality_gate.checks
                     if str(c.status).lower() not in SUMMARISABLE]
        assert attention, "this fixture no longer carries a non-PASS check"
        for check in attention:
            assert check.name in text
            if check.reason:
                assert squash(check.reason) in text

    def test_the_narrative_title_is_not_printed_twice(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """`check_title_line` requires the draft to open with the report title,
        and it still does — but the masthead already carries it, so printing it
        again put the document's title twice on page one."""
        narrative = "# Vibration Survey Report — Synthetic Compressor 01\n\nBody text.\n"
        _, html = self._drafted(tmp_path, iso_table, thresholds, rules, narrative=narrative)
        body = html[html.index('<section class="narrative">'):]
        assert "Vibration Survey Report" not in visible_text(body)
        assert "Body text." in visible_text(body)

    def test_the_cause_section_and_its_art_are_rendered_deterministically(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The model never authors the cause section. When its draft does not
        carry one, the deterministic section is spliced in — with its
        illustrations and every citation."""
        from vib_agent.report.cause_art import DISCLAIMER

        _, html = self._drafted(tmp_path, iso_table, thresholds, rules,
                                narrative="# Vibration Survey Report — X\n\nNothing here.\n")
        assert html.count("data-cause-art") == 11
        assert html.count(DISCLAIMER) == 11
        assert "Possible underlying causes" in visible_text(html)

    def test_a_section_the_draft_already_wrote_is_not_duplicated(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The same want_* suppression the markdown appendix has used since
        Session H: a block the draft carries is not rendered a second time."""
        _, html = self._drafted(tmp_path, iso_table, thresholds, rules)  # reference report
        text = squash(visible_text(html))
        assert text.count("Spectral lines (N)") == 1, "the parameters table was duplicated"

        _, bare = self._drafted(tmp_path / "bare", iso_table, thresholds, rules,
                                narrative="# Vibration Survey Report — X\n\nNothing here.\n")
        assert "Spectral lines (N)" in squash(visible_text(bare)), (
            "a draft that omitted the parameters table did not get the deterministic one")

    def test_the_signature_rules_survive(self, tmp_path, iso_table, thresholds, rules):
        _, html = self._drafted(tmp_path, iso_table, thresholds, rules,
                                narrative="# Vibration Survey Report — X\n\nNothing here.\n")
        text = squash(visible_text(html))
        assert "Reviewed and approved by:" in text
        assert "Date: ______________________" in text
        assert "DRAFT — prepared by automated analysis, pending analyst review." in text


class TestGraphicsQuoteComputedFrequencies:
    """What the N0 SVG exemption gives up, bought back narrowly.

    N0 does not audit numbers inside an inline `<svg>`, because a tick, a band
    edge or a plotted amplitude is a draw-time product rather than a claim. The
    one thing a graphic DOES claim in so many words is `computed N Hz` — and
    that must be a frequency the analysis actually computed, not a number the
    drawing arrived at.
    """

    def test_the_bearing_map_quotes_the_analysis_own_fault_frequencies(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _, result, _, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        block = re.search(r'data-gfx="bearing-map">.*?</figure>', html, re.S)
        assert block, "the bearing map did not render on the fixture that has a geometry"
        quoted = set(re.findall(r"computed ([\d.]+) Hz", block.group(0)))
        assert quoted, "the map quoted no computed frequency at all"

        freqs = result.rca.bearing_freqs
        computed = {f"{getattr(freqs, k):.2f}" for k in ("FTF", "BSF", "BPFO", "BPFI")
                    if getattr(freqs, k, None)}
        assert quoted <= computed, (
            f"the bearing map quotes {sorted(quoted - computed)} as computed, and the analysis "
            f"computed {sorted(computed)}")

    def test_the_evidence_inset_quotes_its_own_row(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _, _, md, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        insets = re.findall(r'data-gfx="evidence-inset">.*?</figure>', html, re.S)
        assert insets, "no evidence inset rendered"
        for inset in insets:
            for value in re.findall(r"computed ([\d.]+) Hz", inset):
                assert value in md, (
                    f"the inset quotes computed {value} Hz, which is not in the report")

    def test_no_geometry_draws_no_bearing_map(self, tmp_path, iso_table, thresholds, rules):
        """Four frequencies nobody computed would be four invented numbers.

        Written first against the `b2_blower` fixture, and it passed for the
        WRONG REASON: that fixture commits `imbalance`, so the map is already
        refused for having no bearing call to draw, and deliberately breaking
        the geometry guard did not fail it. The bearing commit is kept and only
        the GEOMETRY is removed, so the guard under test is the one that runs.
        """
        from vib_agent.report.graphics import bearing_map

        case, result = _bpfo(iso_table, thresholds, rules)
        assert result.rca is not None and result.rca.bearing_freqs is not None
        assert any(m.fault.startswith("bearing_") for m in result.rca.primary_findings)

        assert bearing_map(result, case=case, thresholds=thresholds), (
            "the map does not render even WITH a geometry — this test proves nothing")
        no_geometry = result.model_copy(update={
            "rca": result.rca.model_copy(update={"bearing_freqs": None})})
        assert bearing_map(no_geometry, case=case, thresholds=thresholds) == ""

    def test_no_arrays_draws_nothing_rather_than_an_empty_frame(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """A reading with no measured series draws no graphic at all — it does
        not draw an axis with nothing on it."""
        from vib_agent.report.graphics import bearing_map, evidence_insets

        case, result = _bpfo(iso_table, thresholds, rules)
        assert bearing_map(result, case=None, thresholds=thresholds) == ""
        rows = [{"label": "x", "fault": "bearing_outer_race", "axis": "y",
                 "computed": 107.03, "observed": 107.25}]
        assert evidence_insets(result, rows, case=None, thresholds=thresholds) == ""
        assert evidence_insets(result, rows, case=case, thresholds=None) == ""


class TestGraphicCoverage:
    """WHERE each graphic renders, and where it deliberately does not.

    This matrix is the thing that regressed silently once already: a spectrum
    that serves both detector families was rejected for every non-bearing
    finding, so two fixtures drew no evidence inset at all — and nothing failed,
    because "no graphic" is a legitimate outcome everywhere else. Counting them
    is what turns that back into a detectable change.
    """

    EXPECTED: dict[str, set[str]] = {
        # a bearing commit with a geometry: all three
        "bpfo": {"evidence-inset", "bearing-map", "stage-ladder"},
        "trend": {"evidence-inset", "bearing-map", "stage-ladder"},
        # ISO severity not assessable, but the spectral evidence is intact — the
        # graphics are bound to the SPECTRUM, not to the severity
        "accel_only": {"evidence-inset", "bearing-map", "stage-ladder"},
        # a 1x-family commit: an inset over the series its own detector read, and
        # no map (no geometry was supplied) and no ladder (no bearing commit)
        "b2_blower": {"evidence-inset"},
        # gate FAIL: no diagnosis, so nothing to evidence
        "gate_fail": set(),
        # no fault matched, so no row to inset and no bearing call to map
        "healthy_clean": set(),
        "healthy_no_geometry": set(),
    }

    @pytest.mark.parametrize("fixture", sorted(EXPECTED))
    def test_graphics_render_exactly_where_the_data_supports_them(
        self, fixture, tmp_path, iso_table, thresholds, rules
    ):
        _, _, _, html = _render_pair(fixture, tmp_path, iso_table, thresholds, rules)
        drawn = set(re.findall(r'data-gfx="([a-z-]+)"', html))
        assert drawn == self.EXPECTED[fixture], (
            f"{fixture}: drew {sorted(drawn)}, expected {sorted(self.EXPECTED[fixture])}")

    def test_every_graphic_carries_a_caption(self, tmp_path, iso_table, thresholds, rules):
        """A graphic whose computed fact is not named is a picture of an unnamed
        number, and there is then no way to audit it."""
        _, _, _, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        figures = re.findall(r'(<figure class="gfx".*?</figure>)', html, re.S)
        assert len(figures) == 3
        for figure in figures:
            caption = re.search(r"<figcaption>(.*?)</figcaption>", figure, re.S)
            assert caption and len(squash(visible_text(caption.group(1)))) > 60, figure[:120]

    def test_the_differential_ratio_bars_are_not_drawn(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """CUT, and it must stay cut until the numbers are on the model.

        The four values that graphic needs live only inside the adjudication
        SENTENCE. The prototype recovers them by regex over that prose; porting
        that would leave the report layer one wording change away from silently
        losing a figure. If someone adds it back, it must be because
        DifferentialCandidate carries the numbers — not because the regex was
        copied over.
        """
        _, result, _, html = _render_pair("b2_blower", tmp_path, iso_table, thresholds, rules)
        assert result.rca.differential, "the fixture no longer produces a differential"
        assert 'data-gfx="ratio-bars"' not in html

        source = (Path(__file__).resolve().parents[1]
                  / "src" / "vib_agent" / "report" / "graphics.py").read_text()
        for prose_parse in ("2×/1× ratio on the dominant radial axis", "axial-to-radial velocity"):
            assert prose_parse not in source, (
                "graphics.py parses the adjudication sentence — that is the exact shape "
                "WIRING.md refuses for the live report")


class TestDraftedDocumentOrder:
    """One signature, one footer, one damage-stage section — all at the end.

    A shipped drafted sample put REVIEW & APPROVAL and a closing rule on page 5
    of 21, with the damage stage, three figures and eleven cause sections after
    them: a report that appears to end and then carries on for sixteen pages.
    The cause was that the model writes the document's own furniture — the
    prompt told it to — and the deterministic appendix is spliced after the
    whole narrative. DAMAGE STAGE ESTIMATE appeared twice for a second reason:
    `want_parameters`, `want_causes` and `want_signature` existed and stage was
    missed.
    """

    #: A narrative shaped like the one that shipped: title, body, its own stage
    #: section, its own Review & Approval, its own closing rule and footer.
    NARRATIVE = (
        "# Vibration Survey Report — Synthetic Compressor 01\n\n"
        "## Executive Summary\n\nBody of the summary.\n\n---\n\n"
        "## Damage Stage Estimate\n\nStage 3 (early) — the model's own prose.\n\n---\n\n"
        "## Review & Approval\n\nThis report is a draft produced by automated analysis.\n\n"
        "Reviewed and approved by: ______________________________  (analyst, cert level: __________)\n\n"
        "Date: ______________________\n\n---\n"
        "DRAFT — prepared by automated analysis, pending analyst review.\n"
    )

    def _drafted(self, tmp_path, iso_table, thresholds, rules, narrative):
        from vib_agent.report.generate import render_drafted_html

        case, result = _bpfo(iso_table, thresholds, rules)
        out = tmp_path / "d"
        charts = charts_mod.render_charts(result, case.machine, out, case=case,
                                          thresholds=thresholds, fault_labels=FAULT_LABELS)
        html = render_drafted_html(narrative, result, case.machine, charts=charts, case=case,
                                   thresholds=thresholds, profile="route")
        assert html is not None
        return html

    def test_exactly_one_signature_block_and_it_is_last(
        self, tmp_path, iso_table, thresholds, rules
    ):
        html = self._drafted(tmp_path, iso_table, thresholds, rules, self.NARRATIVE)
        text = squash(visible_text(html))
        assert text.count("Reviewed and approved by:") == 1

        # and it is AFTER the evidence, not before it
        sig = html.index("Reviewed and approved by:")
        for marker, name in (('data-cause-art', "the cause illustrations"),
                             ('data-gfx="stage-ladder"', "the damage-stage ladder"),
                             ("Spectral evidence", "the spectral evidence")):
            assert html.index(marker) < sig, f"{name} renders AFTER the signature block"

    def test_exactly_one_draft_footer(self, tmp_path, iso_table, thresholds, rules):
        """The masthead badge and the page footer are the document's two
        deliberate copies; the narrative's own is stripped."""
        html = self._drafted(tmp_path, iso_table, thresholds, rules, self.NARRATIVE)
        text = squash(visible_text(html))
        assert text.count("DRAFT — prepared by automated analysis, pending analyst review.") == 2
        body = html[html.index('<section class="narrative">'):html.index("<footer>")]
        assert "prepared by automated analysis" not in visible_text(body), (
            "the narrative still carries its own DRAFT footer, mid-document")

    def test_the_damage_stage_section_is_not_rendered_twice(
        self, tmp_path, iso_table, thresholds, rules
    ):
        html = self._drafted(tmp_path, iso_table, thresholds, rules, self.NARRATIVE)
        text = squash(visible_text(html))
        assert text.lower().count("damage stage estimate") == 1

    def test_the_ladder_survives_even_when_the_stage_prose_is_suppressed(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The deterministic prose is a duplicate of what the draft wrote; the
        LADDER is not — it is the only place the reading's position on the
        product's own three-rung scale is drawn."""
        html = self._drafted(tmp_path, iso_table, thresholds, rules, self.NARRATIVE)
        assert 'data-gfx="stage-ladder"' in html

    def test_a_draft_without_its_own_furniture_still_gets_every_block(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The mirror case, and the one that would break silently: the want_*
        flags are computed from the STRIPPED narrative. Reading the raw draft
        would let a signature that has just been removed go on suppressing the
        deterministic one, and the report would end with no signature at all."""
        bare = "# Vibration Survey Report — Synthetic Compressor 01\n\nJust a narrative.\n"
        html = self._drafted(tmp_path, iso_table, thresholds, rules, bare)
        text = squash(visible_text(html))
        assert text.count("Reviewed and approved by:") == 1
        assert text.lower().count("damage stage estimate") == 1
        assert "Spectral lines (N)" in text, "the deterministic parameters table is missing"


class TestFindingCountReadsAsEnglish:
    def test_one_finding_is_singular_everywhere_a_reader_meets_it(
        self, tmp_path, iso_table, thresholds, rules
    ):
        _, result, md, html = _render_pair("bpfo", tmp_path, iso_table, thresholds, rules)
        committed = [f for f in result.findings if f.fault != "no_significant_findings"]
        assert len(committed) == 1
        for document in (md, squash(visible_text(html))):
            assert "1 committed finding" in document
            assert "finding(s)" not in document
            assert "1 committed findings" not in document

    def test_two_findings_are_plural(self, tmp_path, iso_table, thresholds, rules):
        _, result, md, html = _render_pair("trend", tmp_path, iso_table, thresholds, rules)
        committed = [f for f in result.findings if f.fault != "no_significant_findings"]
        assert len(committed) == 2
        for document in (md, squash(visible_text(html))):
            assert "2 committed findings" in document
            assert "finding(s)" not in document

    def test_the_executive_summary_agrees_in_number_and_verb(self):
        from vib_agent.report.generate import _additional_findings

        assert _additional_findings(1) == " 1 additional finding is also reported."
        assert _additional_findings(2) == " 2 additional findings are also reported."
