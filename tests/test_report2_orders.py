"""Session REPORT-2 (item 3) — every fault-frequency mention prints Hz AND its shaft
order: "107.25 Hz (3.58×)".

A CAT analyst writes a bearing tone with its order, because the order is how the
tone is recognised. The report printed Hz alone everywhere on the diagnostic path.
The order is `freq / rca.shaft_freq_hz` at two decimals — presentation arithmetic in
the report layer on values the analysis already holds (`charts.fmt_hz_order`), and
for pdm_core's own sentences, which are latched and name frequencies in Hz alone,
`charts.annotate_orders` adds the order at render time without changing a word.

One deliberate exception, RULED by the operator this session: the Executive
Summary carries no decimal order, because agent/consistency.py::check_numeric_quotes
(read-only here) refuses any decimal in the drafted summary that is not in the
AnalysisResult, and an order ratio is not. tests/test_report2_diagnosis.py pins
that exception; this file pins everything else.

Note the rounding: 107.25 / 30 → 3.58 while the computed BPFO 107.03 / 30 → 3.57.
Both are printed where each frequency is; the evidence table shows them side by side.
"""

from __future__ import annotations

import re

import pytest

from tests.test_report_html import visible_text
from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts, generate, graphics
from vib_agent.report.charts import (
    _inside_parentheses,
    annotate_orders,
    fmt_hz_order,
    render_charts,
)
from vib_agent.report.generate import _evidence_rows, render_html, render_markdown
from vib_agent.synth.generator import make_case

_HZ = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?) Hz(?! \(\d+\.\d\d×\))")


@pytest.fixture(scope="module")
def cfg() -> dict:
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }


@pytest.fixture(scope="module")
def sample(cfg, tmp_path_factory):
    out = tmp_path_factory.mktemp("s3")
    case = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
    result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                          rules=cfg["rules"])
    chartset = render_charts(result, case.machine, out, case=case, thresholds=cfg["thresholds"])
    md = render_markdown(result, case.machine, charts=chartset, case=case,
                         thresholds=cfg["thresholds"], profile="route")
    html = render_html(result, case.machine, charts=chartset, case=case,
                       thresholds=cfg["thresholds"], profile="route")
    return case, result, chartset, md, html


def unpaired_hz(text: str) -> list[str]:
    """Every BARE "<n> Hz" in `text` not followed by "(N.NN×)" — the pin's checker.

    A mention already inside a parenthetical is exempt, because that is exactly the
    rule `charts.annotate_orders` follows (Session REPORT-2-FIX): a parenthetical is
    already a gloss and does not get a second one. Without this the checker would
    flag `matches calculated BPFO (107.0 Hz ±3%)` — the very sentence the rule was
    written to keep clean — so this makes the pin STATE the rule rather than pass
    around it."""
    return [m.group(1) for m in _HZ.finditer(text)
            if not _inside_parentheses(text, m.start())]


def _region(md: str, start: str, end: str) -> str:
    a = md.index(start)
    b = md.index(end, a)
    return md[a:b]


# ── the helpers ──────────────────────────────────────────────────────────


class TestTheHelpers:
    def test_fmt_hz_order(self):
        assert fmt_hz_order(107.25, 30.0) == "107.25 Hz (3.58×)"
        assert fmt_hz_order(107.03, 30.0) == "107.03 Hz (3.57×)"
        assert fmt_hz_order(107.03, 30.0, digits=1) == "107.0 Hz (3.57×)"
        assert fmt_hz_order(107.25, 0.0) == "107.25 Hz"  # no shaft rate: Hz only
        assert fmt_hz_order(107.25, None) == "107.25 Hz"

    def test_annotate_orders_adds_and_is_idempotent(self):
        reason = ("Peak at 107.2 Hz on y (rank 1). matches calculated BPFO (107.0 Hz ±3%). "
                  "for 6206. 2× BPFO harmonic also detected (supporting evidence)")
        once = annotate_orders(reason, 30.0)
        assert once == ("Peak at 107.2 Hz (3.57×) on y (rank 1). matches calculated BPFO "
                        "(107.0 Hz ±3%). for 6206. 2× BPFO harmonic also detected "
                        "(supporting evidence)")
        assert annotate_orders(once, 30.0) == once
        assert annotate_orders(reason, 0.0) == reason
        assert annotate_orders("", 30.0) == ""

    def test_a_mention_already_inside_a_parenthetical_is_left_alone(self):
        """Session REPORT-2-FIX, closing F-6. A parenthetical is already a gloss;
        a gloss inside a gloss is noise, and pdm_core's bracketed frequencies have
        all been introduced in prose first. The three sentences the first cut
        mangled, each measured from its own fixture."""
        assert annotate_orders("1× shaft frequency (29.0 Hz) dominant on radial axis z", 29.0) == (
            "1× shaft frequency (29.0 Hz) dominant on radial axis z")
        assert annotate_orders("blade pass frequency (6 blades × 30.0 Hz shaft = 180.0 Hz).", 30.0) == (
            "blade pass frequency (6 blades × 30.0 Hz shaft = 180.0 Hz).")
        # and a bare mention in the SAME sentence still gets exactly one gloss
        assert annotate_orders("Elevated peak at 180.0 Hz matches blade pass (at 30.0 Hz shaft).", 30.0) == (
            "Elevated peak at 180.0 Hz (6.00×) matches blade pass (at 30.0 Hz shaft).")

    def test_the_bare_mention_the_failing_test_was_about_still_gets_its_order(self):
        """The sentence CI went red on (test_imbalance_rank_wording.py:247). It is
        bare, so the rule above does not reach it and the pin there moves."""
        assert annotate_orders(
            "The loudest line on y is 107.0 Hz, unexplained by this finding.", 30.0
        ) == "The loudest line on y is 107.0 Hz (3.57×), unexplained by this finding."

    def test_the_checker_sees_an_unpaired_hz(self):
        assert unpaired_hz("at 107.25 Hz (3.58×) on y") == []
        assert unpaired_hz("at 107.25 Hz on y") == ["107.25"]
        assert unpaired_hz("Fmax 500 Hz") == ["500"]
        # exempt, and for the same reason annotate_orders leaves it alone
        assert unpaired_hz("matches calculated BPFO (107.0 Hz ±3%)") == []
        assert unpaired_hz("(6 blades × 30.0 Hz shaft = 180.0 Hz)") == []


# ── the sample, every surface ────────────────────────────────────────────


class TestEveryFaultFrequencyOnTheSample:
    def test_the_finding_reason(self, sample):
        _, _, _, md, html = sample
        reason = ("Peak at 107.2 Hz (3.57×) on y (rank 1). matches calculated BPFO "
                  "(107.0 Hz ±3%). for 6206.")
        assert reason in md
        assert reason in visible_text(html)

    def test_the_damage_stage_evidence(self, sample):
        _, _, _, md, html = sample
        line = "Committed fault: bearing_outer_race at 107.25 Hz (3.58×)"
        assert f"- {line}" in md
        assert line in visible_text(html)

    def test_the_evidence_table_has_order_columns(self, sample):
        _, _, _, md, html = sample
        assert ("| Finding | Axis | Computed (Hz) | Computed (×) | Observed (Hz) | Observed (×) "
                "| 2× harmonic | Sidebands |") in md
        assert "| Bearing outer-race fault (BPFO) | y | 107.03 | 3.57× | 107.25 | 3.58× | yes | — |" in md
        text = visible_text(html)
        for token in ("Computed (×)", "Observed (×)", "3.57×", "3.58×"):
            assert token in text

    def test_the_spectrum_caption_and_png_callout(self, sample):
        _, result, chartset, md, html = sample
        y = next(ch for ch in chartset.channels if ch.axis == "y").figures[0]
        assert y.caption.endswith("Matched: bearing outer race at 107.25 Hz (3.58×).")
        assert y.caption in md and y.caption in visible_text(html)

    def test_the_graphics(self, sample):
        _, _, _, _, html = sample
        text = visible_text(html)
        assert "BPFO 107.03 Hz (3.57×), BPFI 162.97 Hz (5.43×)" in text  # bearing-map caption
        assert "of the computed 107.03 Hz (3.57×)" in text  # inset caption
        assert "computed 107.03 Hz (3.57×)</tspan>" in html  # inside the inset SVG
        assert "observed 107.25 Hz (3.58×) · " in html
        assert "computed 107.03 Hz (3.57×)</text>" in html  # bearing-map column

    def test_no_fault_frequency_on_the_diagnostic_path_is_unpaired(self, sample):
        """From the Diagnosis heading through the Spectral Evidence figures: every
        "<n> Hz" that is a fault frequency carries its order. Image alt text is
        skipped (it names the context strip's span, not a fault), and the running
        speed and Fmax rows sit outside this region by design."""
        _, _, _, md, _ = sample
        region = _region(md, "## Diagnosis", "## Possible underlying causes")
        lines = [ln for ln in region.splitlines() if not ln.startswith("![")]
        assert unpaired_hz("\n".join(lines)) == []

    def test_no_invented_numbers_survives(self, sample):
        """The orders appear identically in both twins — the HTML audit in
        tests/test_report_html.py depends on that; here the direct check."""
        _, _, _, md, html = sample
        for token in ("3.57", "3.58", "5.43", "2.31", "0.40"):
            assert token in md and token in visible_text(html)


class TestACleanReadingNamesItsScreenedFrequenciesWithOrders:
    def test_checked_for_line(self, cfg):
        """The clean bill with a geometry supplied (tests/test_report_html.py's
        `_healthy_clean` construction): the bearing screen ran and found nothing,
        and the screened-for line names the four computed frequencies."""
        healthy = make_case("healthy", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=3)
        geometry = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1).machine.bearing
        case = healthy.model_copy(update={
            "machine": healthy.machine.model_copy(update={"bearing": geometry})})
        result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        assert [f.fault for f in result.findings] == ["no_significant_findings"]
        assert result.rca is not None and result.rca.bearing_freqs is not None
        md = render_markdown(result, case.machine)
        bf = result.rca.bearing_freqs
        shaft = result.rca.shaft_freq_hz
        assert f"(BPFO {fmt_hz_order(bf.BPFO, shaft)}, BPFI {fmt_hz_order(bf.BPFI, shaft)}" in md


# ── the negative controls ────────────────────────────────────────────────


class TestStrippingTheOrderGoesRed:
    def test_captions_and_graphics(self, cfg, monkeypatch):
        case = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
        result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        spectrum = case.spectra["y"]
        matches = charts._matches_for_roles(result, "y", frozenset({charts.ROLE_BEARING}))
        assert unpaired_hz(charts._spectrum_caption(result, "y", spectrum, matches, case=case)) == []
        rows = _evidence_rows(result)
        assert unpaired_hz(str(graphics.bearing_map(result, case=case, thresholds=cfg["thresholds"]))) == []
        assert unpaired_hz(str(graphics.evidence_insets(result, rows, case=case, thresholds=cfg["thresholds"]))) == []

        monkeypatch.setattr(charts, "fmt_hz_order", lambda f, s, digits=2: f"{f:.{digits}f} Hz")
        assert unpaired_hz(charts._spectrum_caption(result, "y", spectrum, matches, case=case)) == ["107.25"]
        assert unpaired_hz(str(graphics.bearing_map(result, case=case, thresholds=cfg["thresholds"]))) != []
        assert unpaired_hz(str(graphics.evidence_insets(result, rows, case=case, thresholds=cfg["thresholds"]))) != []

    def test_reason_and_stage(self, cfg, monkeypatch):
        case = make_case("bpfo", iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
        result = run_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                              rules=cfg["rules"])
        md = render_markdown(result, case.machine)
        region = _region(md, "## Diagnosis", "## Evidence")
        assert unpaired_hz(region) == []
        monkeypatch.setattr(generate, "annotate_orders", lambda text, shaft: text)
        md = render_markdown(result, case.machine)
        region = _region(md, "## Diagnosis", "## Evidence")
        assert "107.2" in unpaired_hz(region) and "107.25" in unpaired_hz(region)
