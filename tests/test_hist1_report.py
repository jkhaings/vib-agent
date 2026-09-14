"""Session HIST-1 — the report says where a trend's readings came from.

When the history was held in the analyst's own browser and posted back with the
upload, we keep no copy of it and cannot check it against the files it came
from. A trend computed from it is still worth having; a trend computed from it
in SILENCE is a claim the document cannot support. So the trend section carries
a provenance note on every path that can render one.

Two structural facts these tests exist to protect:

  * the note travels on the CASE, not as a render kwarg. `case` is already
    handed to `build_context` by both renderers, so nothing in `generate.py`'s
    nine-signature chain, nothing in `worker._document_context` and nothing in
    `process_job`'s COMPARE-FWD-pinned parameter list had to move. If a later
    session "tidies" that into a kwarg, the drafted lanes and the no-weasyprint
    fallback are where it will silently go missing.
  * the note is ONE macro pair, called from four templates. Three copies of one
    sentence is exactly how the UNEXPLAINED-PEAK defect survived.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tests.test_report_html import (
    _analyse,
    _bpfo,
    squash,
    visible_text,
)
from vib_agent.report import charts as charts_mod
from vib_agent.report.generate import (
    FAULT_LABELS,
    build_context,
    render_drafted_html,
    render_html,
    render_markdown,
)

_ROOT = Path(__file__).resolve().parents[1]
_TEMPLATES = _ROOT / "src" / "vib_agent" / "report" / "templates"

#: The sentence's load-bearing halves. Wording may be improved; these two claims
#: may not quietly disappear, because they are the whole reason it is printed.
KEEP = "saved in this browser"
CANNOT_VERIFY = "cannot check them against the files they came from"


def _client_case(iso_table, thresholds, rules, *, n_points=None):
    """The `trend` fixture, restamped as history the ANALYST supplied.

    Re-analysed after any trim so `trend.n_days` is the truth about the points
    on the Case rather than a leftover from the untrimmed run.
    """
    case, _ = _bpfo(iso_table, thresholds, rules, history=True)
    points = list(case.history or [])
    if n_points is not None:
        points = points[:n_points]
    case = case.model_copy(update={"history": points, "history_source": "analyst_supplied"})
    return _analyse(case, iso_table, thresholds, rules)


def _pair(case, result, tmp_path, thresholds):
    """The markdown and the HTML, from ONE analysis and ONE chart manifest."""
    charts = charts_mod.render_charts(result, case.machine, tmp_path, case=case,
                                      thresholds=thresholds, fault_labels=FAULT_LABELS)
    md = render_markdown(result, case.machine, charts=charts, case=case,
                         thresholds=thresholds, profile="route")
    html = render_html(result, case.machine, charts=charts, case=case,
                       thresholds=thresholds, profile="route")
    return md, html


# ─────────────────────────────────────────────────────────────────────────
# 1 · The mirrored pair
# ─────────────────────────────────────────────────────────────────────────


class TestTheNoteIsMirrored:
    def test_it_renders_in_both_documents(self, tmp_path, iso_table, thresholds, rules):
        case, result = _client_case(iso_table, thresholds, rules)
        md, html = _pair(case, result, tmp_path, thresholds)
        for name, text in (("markdown", squash(md)), ("HTML", squash(visible_text(html)))):
            assert KEEP in text, f"the {name} document does not say where the history came from"
            assert CANNOT_VERIFY in text, f"the {name} document does not say it is unverified"

    def test_it_names_how_many_readings_it_stands_on(self, tmp_path, iso_table, thresholds, rules):
        case, result = _client_case(iso_table, thresholds, rules, n_points=6)
        md, html = _pair(case, result, tmp_path, thresholds)
        assert "6 in total" in squash(md)
        assert "6 in total" in squash(visible_text(html))

    def test_it_sits_inside_the_trend_section(self, tmp_path, iso_table, thresholds, rules):
        """Report law #7: sections are ADDED to, never restyled or re-ordered.
        The note belongs to the trend, so it renders with it."""
        case, result = _client_case(iso_table, thresholds, rules)
        md, _ = _pair(case, result, tmp_path, thresholds)
        after_heading = md.partition("## Trend Summary")[2]
        assert KEEP in after_heading, "the note escaped the Trend section"

    def test_one_macro_pair_serves_every_template(self):
        """The UNEXPLAINED-PEAK lesson, pinned at source: the sentence is written
        ONCE per output format, and the four page templates call it."""
        v2 = (_TEMPLATES / "_v2.html.j2").read_text()
        ev = (_TEMPLATES / "_evidence.md.j2").read_text()
        assert v2.count("macro history_provenance_note") == 1
        assert ev.count("macro history_provenance_note") == 1
        for page, macro in (
            ("survey.html.j2", "v2.history_provenance_note"),
            ("drafted.html.j2", "v2.history_provenance_note"),
            ("default_survey.md.j2", "ev.history_provenance_note"),
            ("drafted_evidence.md.j2", "ev.history_provenance_note"),
        ):
            body = (_TEMPLATES / page).read_text()
            assert macro in body, f"{page} does not call the provenance macro"
        # ...and no page writes the sentence itself.
        for page in ("survey.html.j2", "drafted.html.j2",
                     "default_survey.md.j2", "drafted_evidence.md.j2"):
            assert KEEP not in (_TEMPLATES / page).read_text(), (
                f"{page} carries its own copy of the provenance sentence"
            )


# ─────────────────────────────────────────────────────────────────────────
# 2 · Absent by default — every existing document is unmoved
# ─────────────────────────────────────────────────────────────────────────


class TestAbsentByDefault:
    def test_a_file_supplied_history_says_nothing_new(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """The trend-schema CSV lane's points came out of the analyst's own
        upload and are exactly as trustworthy as the reading beside them. The
        NCD stream, the synthetic generator and every benchmark adapter are the
        same. None of them gets a caveat it has not earned."""
        case, result = _bpfo(iso_table, thresholds, rules, history=True)
        assert case.history_source is None
        md, html = _pair(case, result, tmp_path, thresholds)
        assert KEEP not in squash(md)
        assert KEEP not in squash(visible_text(html))

    def test_a_report_with_no_history_at_all_is_unmoved(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _bpfo(iso_table, thresholds, rules)
        md, html = _pair(case, result, tmp_path, thresholds)
        assert result.trend is None
        assert KEEP not in squash(md)
        assert "No trend assessed" in squash(visible_text(html))

    def test_the_flag_only_adds(self, tmp_path, iso_table, thresholds, rules):
        """Byte-shape: stamping the provenance must ADD the note and change
        nothing else. Rendered from the same points and the same analysis, the
        two markdown documents differ only by the block the macro emits."""
        plain_case, plain_result = _bpfo(iso_table, thresholds, rules, history=True)
        stamped = plain_case.model_copy(update={"history_source": "analyst_supplied"})
        plain_md, _ = _pair(plain_case, plain_result, tmp_path / "plain", thresholds)
        stamped_md, _ = _pair(stamped, plain_result, tmp_path / "stamped", thresholds)
        added = [line for line in stamped_md.splitlines() if line not in plain_md.splitlines()]
        removed = [line for line in plain_md.splitlines() if line not in stamped_md.splitlines()]
        assert not removed, f"stamping the provenance removed lines: {removed}"
        assert added and all(KEEP in line or CANNOT_VERIFY in line or not line.strip()
                             for line in added), added

    def test_no_new_render_kwarg_was_threaded(self):
        """The structural claim, pinned. The flag rides on `case`, which every
        renderer already receives — so `render_report`, `render_pdf` and
        `render_markdown` grew nothing, and neither did `process_job`'s
        COMPARE-FWD-pinned signature. The worker's shared document context DID
        grow, under REPORTFIX-1 R-3 — but it grew live per-location analysis
        objects, never a provenance kwarg, so HIST-1's claim is untouched."""
        from vib_agent.report import generate as gen
        from vib_agent.webapp import worker as worker_mod

        for fn in (gen.render_report, gen.render_pdf, gen.render_markdown, gen.render_html):
            assert "history_provenance" not in inspect.signature(fn).parameters, (
                f"{fn.__name__} grew a provenance kwarg — it belongs on the Case, where "
                "both renderers already read it"
            )
        assert "history_provenance" not in inspect.signature(worker_mod._document_context).parameters, (
            "the worker's document context grew a provenance kwarg — it belongs on the "
            "Case, where both renderers already read it"
        )
        assert set(inspect.signature(worker_mod._document_context).parameters) == {
            "case", "thresholds", "comparison", "result", "charts", "job"
        }, (
            "the shared document context changed shape. It was {case, thresholds, comparison} "
            "when HIST-1 pinned it; REPORTFIX-1 R-3 added `job`, `result` and `charts` to carry "
            "live per-location results in-process — the wire holds `committed_fault` as a bare "
            "model id, and page 1 may not invent the evidence it cannot read there. Any further "
            "growth needs its own ruling: re-pin the exact set here, never widen this to a "
            "superset check."
        )
        assert "history_provenance" not in inspect.signature(worker_mod.process_job).parameters


# ─────────────────────────────────────────────────────────────────────────
# 3 · The shortfall sentence — what to collect next
# ─────────────────────────────────────────────────────────────────────────


class TestTheShortfallSentence:
    def test_below_the_floor_it_says_how_many_are_needed(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """`compute_trend` refuses below `trend.min_days` (14). An analyst two
        uploads into a route reads `insufficient_data` and deserves to be told
        what that takes rather than left to guess -- the product pillar's
        "says what to collect next"."""
        case, result = _client_case(iso_table, thresholds, rules, n_points=3)
        assert result.trend is not None and result.trend.status == "insufficient_data"
        md, html = _pair(case, result, tmp_path, thresholds)
        needed = str(thresholds["trend"]["min_days"])
        for text in (squash(md), squash(visible_text(html))):
            assert f"needs {needed} readings" in text
            assert "no ISO §6.5.2 alarm limit" in text

    def test_at_the_floor_it_says_nothing_further(
        self, tmp_path, iso_table, thresholds, rules
    ):
        case, result = _client_case(iso_table, thresholds, rules)   # 30 points
        assert result.trend is not None and result.trend.status == "ok"
        md, _ = _pair(case, result, tmp_path, thresholds)
        assert KEEP in squash(md)
        assert "readings; this one has" not in squash(md)

    def test_the_floor_is_read_from_config_not_hardcoded(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """CLAUDE.md: tunable constants live in config. The templates must not
        carry a 14 of their own -- `min_days` arrives through build_context."""
        case, result = _client_case(iso_table, thresholds, rules, n_points=3)
        doctored = dict(thresholds)
        doctored["trend"] = dict(thresholds["trend"], min_days=9)
        context = build_context(result, case.machine, case=case, thresholds=doctored)
        assert context["history_provenance"]["needed"] == 9
        for page in ("_v2.html.j2", "_evidence.md.j2"):
            body = (_TEMPLATES / page).read_text()
            block = body.partition("macro history_provenance_note")[2].partition("endmacro")[0]
            assert "14" not in block, f"{page} hardcodes the trend floor"

    def test_without_thresholds_the_shortfall_clause_is_withheld(
        self, iso_table, thresholds, rules
    ):
        """A caller that supplies no thresholds cannot be told the floor, so the
        report does not invent one -- it still says where the history came from."""
        case, result = _client_case(iso_table, thresholds, rules, n_points=3)
        context = build_context(result, case.machine, case=case, thresholds=None)
        assert context["history_provenance"]["needed"] is None
        assert context["history_provenance"]["n_points"] == 3


# ─────────────────────────────────────────────────────────────────────────
# 4 · The drafted lanes
# ─────────────────────────────────────────────────────────────────────────


class TestTheDraftedDocuments:
    def _drafted(self, tmp_path, iso_table, thresholds, rules):
        case, result = _client_case(iso_table, thresholds, rules, n_points=3)
        charts = charts_mod.render_charts(result, case.machine, tmp_path, case=case,
                                          thresholds=thresholds, fault_labels=FAULT_LABELS)
        narrative = render_markdown(result, case.machine, charts=None, case=case,
                                    thresholds=thresholds, profile="route",
                                    include_causes=False)
        html = render_drafted_html(narrative, result, case.machine, charts=charts, case=case,
                                   thresholds=thresholds, profile="route")
        assert html is not None, "the narrative could not be converted — is `markdown` installed?"
        return case, result, charts, html

    def test_the_drafted_page_carries_it(self, tmp_path, iso_table, thresholds, rules):
        """A model wrote the prose; it did not write this. Spliced
        deterministically on every drafted page, the REPORT-NA way -- there is
        no `want_` flag because the reference report the model is handed has no
        such section to duplicate."""
        _, _, _, html = self._drafted(tmp_path, iso_table, thresholds, rules)
        text = squash(visible_text(html))
        assert KEEP in text and CANNOT_VERIFY in text

    def test_the_drafted_markdown_appendix_carries_it(
        self, tmp_path, iso_table, thresholds, rules
    ):
        from vib_agent.report.generate import _drafted_evidence_block

        case, result, charts, _ = self._drafted(tmp_path, iso_table, thresholds, rules)
        block = _drafted_evidence_block(
            result, charts=charts, case=case, thresholds=thresholds, profile="route",
            draft_text="A narrative that mentions nothing of the sort.",
            machine=case.machine,
        )
        assert KEEP in squash(block)

    def test_it_does_not_depend_on_a_figure_being_drawn(
        self, tmp_path, iso_table, thresholds, rules
    ):
        """`render_report(figures=False)` and any render whose chart manifest is
        absent must still carry the caveat: the claim is about the DATA, not
        about the picture of it."""
        case, result = _client_case(iso_table, thresholds, rules, n_points=3)
        md = render_markdown(result, case.machine, charts=None, case=case,
                             thresholds=thresholds, profile="route")
        html = render_html(result, case.machine, charts=None, case=case,
                           thresholds=thresholds, profile="route")
        assert KEEP in squash(md)
        assert KEEP in squash(visible_text(html))
