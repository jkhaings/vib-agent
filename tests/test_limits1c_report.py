"""LIMITS-1c item 1 — the report stops signing ISO's name to a plant number.

LIMITS-1b branched the zone VERDICT (the health line, the Executive Summary,
the zone label) on `Reading.zone_basis` and then stopped, recording two adjacent
misattributions it declined to take on its own judgement (SESSION_LIMITS1B.md
§7 F-3):

  * the caption under the severity strip — "marked at its measured position on
    this machine's **ISO zone scale**";
  * the whole trend section — "**ISO §6.5.2** alarm limit: N mm/s" and
    "Projected ~N days to the next **ISO boundary**".

Its fix round started them and was cut short; the edits arrived on this branch
as commit `cc4ac44` with **no pins and no close-out**, which is the debt this
file pays.

WHY A TREND STATE EXISTS HERE AND NOT IN `test_limits1b_report.py`. Every state
in that file's `STATES` renders a document with **no trend at all**, so an
assertion that "§6.5.2" is absent passes there without the string ever having
been reachable. Measured before these pins were written:

    bpfo, no history, ISO basis   ->  '§6.5.2' in document: False
    bpfo, no history, custom      ->  '§6.5.2' in document: False

Both green, neither meaningful. The trend fixture below is what makes the
absence an absence OF SOMETHING — on the ISO basis the same document does carry
the string, and that is asserted in the same class, so a branch that silently
stopped rendering the trend section could not pass both halves.

Scope note: `pdm_core/synthesize.py:112` carries a third "next ISO boundary"
and is LATCHED. `report/charts.py:2069,2075` carry "ISO zone boundaries" and
"ISO §6.5.2 alarm limit" as trend-figure LEGEND labels, baked into the PNG and
not reachable by a text pin. Both are recorded as findings, not fixed here.
"""

from __future__ import annotations

import pytest

from vib_agent.models import MachineThresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report.charts import _basis_is_custom
from vib_agent.report.generate import _custom_basis, render_html, render_markdown
from vib_agent.synth.generator import make_case, make_history

# The rig, the fixture set and the two quoted constants come from LIMITS-1b
# rather than being respelled -- the two files describe one feature and a second
# spelling of `BASIS_PHRASE` is exactly the drift `_CUSTOM_BASIS_PHRASE` exists
# to stop, one layer up.
from tests.test_limits1b_report import (  # noqa: F401  (cfg is a fixture)
    BASIS_PHRASE,
    TO_ZONE_B,
    cfg,
)

#: The strings this session is responsible for removing from a custom-basis
#: document, quoted once. `§6.5.2` is the clause number the trend section
#: cites; "ISO zone scale" is the severity-strip caption.
ISO_CLAUSE = "§6.5.2"
ISO_SCALE = "ISO zone scale"

#: What each replaces, on a custom basis.
CUSTOM_ALARM = "Machine-specific alarm limit"
CUSTOM_SCALE = "the machine-specific limits scale"

DOCS = ("md", "html")


def _trend_case(cfg, limit=None, *, history=None, noise_pct=0.12):
    """The seeded `bpfo` trio WITH 30 days of history, so the trend section
    renders and its ISO wording is actually on the page.

    Same history the HIST-1 and report-HTML fixtures use
    (`tests/test_report_html.py::_bpfo(history=True)`), reproduced through the
    same `make_history` call rather than imported, because this file needs to
    vary the machine's limits and that helper does not take them.
    """
    case = make_case("bpfo", iso_table=cfg["iso_table"],
                     thresholds=cfg["thresholds"], seed=1)
    case = case.model_copy(update={"history": make_history(
        *(history or (30, 1.2, 5.2)), noise_pct=noise_pct, cadence="daily", seed=7)})
    if limit is not None:
        case = case.model_copy(
            update={"machine": case.machine.model_copy(update={"thresholds": limit})})
    result = run_analysis(case, iso_table=cfg["iso_table"],
                          thresholds=cfg["thresholds"], rules=cfg["rules"])
    return case, result


@pytest.fixture(scope="module")
def trend_docs(cfg):
    """`{basis: {"md": ..., "html": ...}}` for a document that HAS a trend."""
    out = {}
    for basis, limit in (("iso", None), ("custom", TO_ZONE_B)):
        case, result = _trend_case(cfg, limit)
        out[basis] = {
            "md": render_markdown(result, case.machine, case=case,
                                  thresholds=cfg["thresholds"], profile="route"),
            "html": render_html(result, case.machine, case=case,
                                thresholds=cfg["thresholds"], profile="route"),
            "result": result,
        }
    return out


class TestTheTrendSectionIsReachableAtAll:
    """Non-vacuity, first — every absence pin below is worthless without it."""

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_iso_document_really_does_cite_the_clause(self, trend_docs, doc):
        assert ISO_CLAUSE in trend_docs["iso"][doc], (
            "the ISO-basis document does not cite §6.5.2, so the custom-basis "
            "absence below proves nothing -- the trend section is not rendering"
        )

    def test_the_severity_strip_caption_is_reachable_in_html(self, trend_docs):
        """`ISO zone scale` has no markdown twin: `default_survey.md.j2` carries
        no severity-strip caption. A markdown-only absence pin would be green on
        a string that was never emitted, so the positive half is HTML's."""
        assert ISO_SCALE in trend_docs["iso"]["html"]

    def test_it_has_no_markdown_twin_and_that_is_deliberate(self, trend_docs):
        assert ISO_SCALE not in trend_docs["iso"]["md"]

    def test_the_two_bases_are_actually_different(self, trend_docs):
        assert trend_docs["iso"]["result"].iso.zone_basis == "iso"
        assert trend_docs["custom"]["result"].iso.zone_basis == "custom"


class TestNoIsoAttributionSurvivesOnACustomBasis:
    """The brief's item-1 pin, both documents."""

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_clause_number_is_gone(self, trend_docs, doc):
        page = trend_docs["custom"][doc]
        assert ISO_CLAUSE not in page, (
            f"the custom-basis {doc} still cites ISO {ISO_CLAUSE} over a plant number"
        )

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_scale_caption_is_gone(self, trend_docs, doc):
        assert ISO_SCALE not in trend_docs["custom"][doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_it_was_replaced_rather_than_deleted(self, trend_docs, doc):
        """An absence is only half the claim: the alarm limit is still a real
        number the analyst needs, so the sentence must still be there under the
        plant's own name."""
        assert CUSTOM_ALARM in trend_docs["custom"][doc]

    def test_the_caption_names_the_plants_scale_instead(self, trend_docs):
        assert CUSTOM_SCALE in trend_docs["custom"]["html"]


class TestTheIsoPathDidNotMove:
    """The other direction, which is the one that protects every existing
    report: on an ISO basis both strings still appear where they did, and no
    custom vocabulary leaks onto the page."""

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_clause_number_still_appears(self, trend_docs, doc):
        assert ISO_CLAUSE in trend_docs["iso"][doc]

    def test_the_scale_caption_still_appears(self, trend_docs):
        assert ISO_SCALE in trend_docs["iso"]["html"]

    @pytest.mark.parametrize("doc", DOCS)
    def test_no_custom_vocabulary_leaks(self, trend_docs, doc):
        page = trend_docs["iso"][doc]
        assert BASIS_PHRASE not in page
        assert CUSTOM_ALARM not in page
        assert CUSTOM_SCALE not in page


class TestTheChartCaptionAgreesWithThePage:
    """`report/charts.py` cannot import `_custom_basis` -- `generate.py` imports
    IT (`generate.py:37`), so the predicate is spelled twice. This is what holds
    the two spellings together, so a drift lands as a red test rather than as
    two attributions on one page."""

    def test_the_two_predicates_agree(self, trend_docs):
        for basis in ("iso", "custom"):
            result = trend_docs[basis]["result"]
            assert _basis_is_custom(result) == _custom_basis(result), basis

    def test_and_they_are_not_both_trivially_false(self, trend_docs):
        assert _basis_is_custom(trend_docs["custom"]["result"]) is True
        assert _basis_is_custom(trend_docs["iso"]["result"]) is False


class TestTheProvenanceNoteBranchesToo:
    """The HIST-1 note ("...so no ISO §6.5.2 alarm limit and no forward
    projection is offered yet") renders from a MACRO, and every import of the
    two macro files is a plain `{% import %}` WITHOUT `with context`. So a macro
    there cannot see the caller's variables: `ctx.custom_basis` inside it is
    silently Undefined -- falsy -- which is the ISO branch on every basis, i.e.
    a fix that renders as a no-op. The flag is a macro PARAMETER for that
    reason, and this is the pin that would have caught the no-op."""

    def test_the_macro_takes_the_flag_as_a_parameter(self):
        """Read off the shipped template: a regression to `ctx.custom_basis`
        would be invisible in a render, because it is Undefined and falsy."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "report" / "templates"
        for name in ("_evidence.md.j2", "_v2.html.j2"):
            text = (root / name).read_text()
            assert "macro history_provenance_note(provenance, custom_basis=false)" in text, name
            assert "{% if ctx.custom_basis %}" not in text, (
                f"{name} reads the flag off the context, which a plain "
                "{% import %} does not carry -- it would render as a no-op"
            )

    def test_every_call_site_passes_it(self):
        """A defaulted parameter that nobody passes is the same no-op wearing a
        different hat. All FOUR call sites, the drafted lane included."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1] / "src" / "vib_agent" / "report" / "templates"
        callers = ("default_survey.md.j2", "survey.html.j2",
                   "drafted.html.j2", "drafted_evidence.md.j2")
        for name in callers:
            text = (root / name).read_text()
            assert "history_provenance_note(history_provenance, custom_basis)" in text, name


#: A GENTLER history, for the one line the fixture above cannot reach.
#: `days_to_next_boundary` projects forward to `trend.alarm_limit`
#: (`baseline_avg + 0.25 x th_bc`), and the 1.2 -> 5.2 ramp is already PAST that
#: on both bases, so the projection is negative and `pdm_core/trend.py:113`
#: withholds it -- the boundary sentence never renders. Measured: `days=None` on
#: both bases with the standard history, `days=14.8` (ISO) and `days=90.9`
#: (custom) with this one. Without this, `charts.py`'s fix would be unpinned and
#: the pin asserting it would have been green against a line that never ran.
GENTLE_HISTORY = (30, 1.0, 1.5)


class TestTheChartCaptionCarriesTheBasisToo:
    """`report/charts.py:2145` -- item 1's third occurrence, the one the brief
    names. The string is built in `_trend_annotation` and lands on
    `ChartFigure.caption`, so this asserts it there: that is where this module
    is responsible for it, and it does not depend on whether a given render
    includes the figure block."""

    @pytest.fixture(scope="class")
    def captions(self, cfg, tmp_path_factory):
        from vib_agent.report.charts import render_charts
        out = {}
        for basis, limit in (("iso", None), ("custom", TO_ZONE_B)):
            case, result = _trend_case(cfg, limit, history=GENTLE_HISTORY, noise_pct=0.05)
            assert result.trend.days_to_next_boundary is not None, (
                f"{basis}: no forward projection, so the sentence under test "
                "never renders -- this fixture is not exercising it"
            )
            charts = render_charts(result, case.machine,
                                   tmp_path_factory.mktemp(f"l1c_{basis}"),
                                   case=case, thresholds=cfg["thresholds"])
            assert charts.trend is not None, f"{basis}: no trend figure was built"
            out[basis] = charts.trend.caption
        return out

    def test_the_iso_caption_still_says_iso(self, captions):
        assert "to the next ISO boundary" in captions["iso"]

    def test_the_custom_caption_names_the_machines_own_scale(self, captions):
        assert "to the next machine-specific zone boundary" in captions["custom"]

    def test_and_does_not_say_iso(self, captions):
        assert "next ISO boundary" not in captions["custom"]


class TestWhatIsStillIsoOnACustomPageAndWhy:
    """Recorded rather than fixed, so the next reader meets a test instead of a
    surprise.

    `pdm_core/synthesize.py:112` appends *"Projected ~N days to the next ISO
    boundary."* to a trend Finding's `reason`, and `pdm_core/` is LATCHED this
    session -- the brief says so in as many words: *"The pdm_core/synthesize.py
    :112 occurrence is latched; record it, do not touch it."* So a custom-basis
    document DOES still contain the substring "next ISO boundary", from that one
    site, and item 1's pin is scoped to the two strings the brief names for
    exactly this reason.

    This pin is deliberately the wrong way round: it asserts the defect is still
    there. When a session with `pdm_core/` open fixes it, this goes red and
    tells them where the rest of the story is."""

    def test_the_latched_site_still_signs_isos_name(self, cfg):
        case, result = _trend_case(cfg, TO_ZONE_B, history=GENTLE_HISTORY, noise_pct=0.05)
        reasons = [f.reason or "" for f in result.findings]
        assert any("to the next ISO boundary" in r for r in reasons), (
            "pdm_core/synthesize.py:112 no longer says 'next ISO boundary' on a "
            "custom basis. If that was deliberate, delete this test and widen "
            "item 1's pin to the whole phrase; the report layer is already ready."
        )


class TestTheOtherTwoTrendSentencesBranchToo:
    """The two `§6.5.2` sites the carried diff did NOT branch, and which the
    operator ruled in: the not-assessable trend twin
    (`default_survey.md.j2:163` / `survey.html.j2:204`) and the HIST-1
    provenance note (`_evidence.md.j2:72` / `_v2.html.j2:42`).

    Both need a state the standard fixture cannot produce, and both would be
    silently green without one -- which is the whole reason this class renders
    its own documents rather than reusing `trend_docs`."""

    @staticmethod
    def _pair(cfg, case, result):
        return (render_markdown(result, case.machine, case=case,
                                thresholds=cfg["thresholds"], profile="route"),
                render_html(result, case.machine, case=case,
                            thresholds=cfg["thresholds"], profile="route"))

    # ── the HIST-1 shortfall note ────────────────────────────────────────
    @pytest.fixture(scope="class")
    def shortfall(self, cfg):
        """Analyst-supplied history, trimmed below `trend.min_days`, on both
        bases. `tests/test_hist1_report.py::TestTheShortfallSentence` owns the
        ISO half of this sentence; this is the same state with a plant limit."""
        out = {}
        for basis, limit in (("iso", None), ("custom", TO_ZONE_B)):
            case, _ = _trend_case(cfg, limit)
            case = case.model_copy(update={
                "history": list(case.history or [])[:3],
                "history_source": "analyst_supplied"})
            result = run_analysis(case, iso_table=cfg["iso_table"],
                                  thresholds=cfg["thresholds"], rules=cfg["rules"])
            assert result.trend is not None and result.trend.status == "insufficient_data", basis
            out[basis] = self._pair(cfg, case, result)
        return out

    @pytest.mark.parametrize("i", (0, 1))
    def test_the_iso_note_still_cites_the_clause(self, shortfall, i):
        assert "no ISO §6.5.2 alarm limit" in shortfall["iso"][i], (
            "the shortfall sentence is not rendering, so the custom half below "
            "proves nothing"
        )

    @pytest.mark.parametrize("i", (0, 1))
    def test_the_custom_note_does_not(self, shortfall, i):
        page = shortfall["custom"][i]
        assert "no machine-specific alarm limit" in page
        assert ISO_CLAUSE not in page

    # ── the not-assessable trend twin ────────────────────────────────────
    @pytest.fixture(scope="class")
    def withheld(self, cfg):
        """A reading `mark_not_assessable` has withheld, WITH a trend -- the
        branch that says the alarm limit is "not applicable without velocity
        data". `zone_basis` survives being withheld (SESSION_LIMITS1.md §5.2),
        which is what makes this sentence's basis knowable at all."""
        from vib_agent.pdm_core.iso_classify import mark_not_assessable
        out = {}
        for basis, limit in (("iso", None), ("custom", TO_ZONE_B)):
            case, result = _trend_case(cfg, limit)
            result = result.model_copy(update={"iso": mark_not_assessable(
                result.iso, "velocity outside the plausible range")})
            out[basis] = self._pair(cfg, case, result)
        return out

    @pytest.mark.parametrize("i", (0, 1))
    def test_the_withheld_iso_page_still_cites_the_clause(self, withheld, i):
        assert "§6.5.2 alarm limit not applicable" in withheld["iso"][i], (
            "the not-assessable trend branch is not rendering"
        )

    @pytest.mark.parametrize("i", (0, 1))
    def test_the_withheld_custom_page_does_not(self, withheld, i):
        page = withheld["custom"][i]
        assert "Machine-specific alarm limit not applicable" in page
        assert ISO_CLAUSE not in page
