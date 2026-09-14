"""Session REPORT-3 — page 1 is the report.

The brief's item 1 is a list, in an order, and "nothing else":

    machine ID and measurement location(s); date of collection; analyst name;
    one health line; the fault and where it is, with the evidence in one
    sentence; severity as a decision; the recommendation as REC-1's three
    parts under their own heading; the escalation trigger; one spectrum figure
    with the fault markers, and the trend if history exists; a blank
    "Work order:" field.

    Pin: the page-1 text contains every field above, and the PDF page 1 holds
    only page-1 content.

Both halves are here. The first is read off the rendered documents; the second
is read off the PAGINATED PDF, split page by page rather than joined — the
technique `tests/test_report_html.py::TestSurvivesIntoThePdf` established, used
for the one question it was not asked: what is on page 1 and what is not.

Items 6 (the escalation trigger) and 7 (the data-quality line beside the
severity) are page-1 fields, so they are pinned here too.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report.generate import (
    SKI_SLOPE_CAVEAT,
    escalation_trigger,
    fault_sheet_context,
    render_html,
    render_markdown,
    render_report,
)
from vib_agent.synth.generator import make_case


def squash(text: str) -> str:
    """pypdf splits ligatures and hyphenates across lines (scripts/grade_regen.py)."""
    return re.sub(r"\s+", " ", (text or "").replace("-\n", "").replace("\n", " ")).strip()


def visible_text(html: str) -> str:
    """What a READER sees, not what the markup says."""
    body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
    return squash(re.sub(r"(?s)<[^>]+>", " ", body))


@pytest.fixture(scope="module")
def cfg():
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }


def _analyse(name, cfg, **kw):
    case = make_case(name, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                     seed=1, **kw)
    return case, run_analysis(case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])


def _render_pair(name, tmp_path, cfg):
    case, result = _analyse(name, cfg)
    charts = charts_mod.render_charts(result, case.machine, tmp_path, case=case,
                                      thresholds=cfg["thresholds"])
    md = render_markdown(result, case.machine, charts=charts, case=case,
                         thresholds=cfg["thresholds"], profile="route")
    html = render_html(result, case.machine, charts=charts, case=case,
                       thresholds=cfg["thresholds"], profile="route")
    return case, result, charts, md, html


class TestEveryFieldIsOnPageOne:
    """Item 1's list, field by field, in both twins."""

    def test_the_context_carries_every_field(self, cfg):
        case, result = _analyse("bpfo", cfg)
        sheet = fault_sheet_context(result, case.machine, case)
        for field in ("machine_id", "locations", "collected", "analyst", "health",
                      "data_quality", "fault", "recommendation", "trigger"):
            assert field in sheet, field
        # Session REPORT-4 (item 7) removed the "Decision: monitor" token and the
        # context key behind it. Re-pinned at the ruled state rather than dropped:
        # the key must be ABSENT, so a session that reintroduces the zone->verb
        # map meets a red test instead of quietly printing a fifth verdict.
        assert "decision" not in sheet
        assert sheet["machine_id"]
        assert sheet["collected"]
        assert sheet["health"]["zone_clause"]
        assert sheet["fault"]["label"]
        assert sheet["fault"]["evidence"]
        # The three-part Recommendation is what the sheet says to DO now — it is
        # what item 7's ruling said already answers the question "Decision:" was
        # answering, so it is asserted present in the same breath.
        assert set(sheet["recommendation"]) == {"action", "timing", "reassess", "text"}
        assert sheet["trigger"]

    def test_the_markdown_sheet_states_every_field(self, tmp_path, cfg):
        _, _, _, md, _ = _render_pair("bpfo", tmp_path, cfg)
        sheet = md[md.index("## Fault Sheet"):md.index("## Executive Summary")]
        assert "Synthetic Compressor 01" in sheet
        assert "**Date of collection:**" in sheet
        assert "**Analyst:**" in sheet
        assert "ISO Zone D" in sheet and "5.20 mm/s RMS" in sheet
        assert "Group 2, rigid support" in sheet
        assert "Bearing outer-race fault (BPFO)" in sheet
        assert "107.25 Hz (3.58×)" in sheet          # fault Hz, its order
        assert "2× harmonic present" in sheet        # harmonics found
        assert "### Recommendation" in sheet
        for part in ("**Action:**", "**When:**", "**Reassess:**"):
            assert part in sheet, part
        assert "**Escalation trigger:**" in sheet
        assert "**Work order:**" in sheet

    def test_the_html_sheet_states_every_field(self, tmp_path, cfg):
        _, _, _, _, html = _render_pair("bpfo", tmp_path, cfg)
        assert '<section class="sheet">' in html
        sheet = visible_text(html[html.index('<section class="sheet">'):
                                  html.index('<h2 class="evidence-head">')])
        assert "Synthetic Compressor 01" in sheet
        assert "collected" in sheet and "Analyst:" in sheet
        assert "ISO Zone D" in sheet and "5.20 mm/s RMS" in sheet
        assert "Bearing outer-race fault (BPFO)" in sheet
        assert "107.25 Hz (3.58×)" in sheet
        assert "Recommendation" in sheet
        assert "Action:" in sheet and "When:" in sheet and "Reassess:" in sheet
        assert "Escalation trigger:" in sheet
        assert "Work order:" in sheet

    def test_the_sheet_carries_one_spectrum_figure_with_the_fault_markers(self, tmp_path, cfg):
        _, _, charts, md, html = _render_pair("bpfo", tmp_path, cfg)
        assert charts.sheet is not None
        # The axis the committed call was made on, not an arbitrary channel.
        assert charts.sheet.key == "sheet_y"
        assert "bearing outer race at 107.25 Hz (3.58×)" in charts.sheet.caption
        assert charts.sheet.rel_path in md
        head = html[html.index('<section class="sheet">'):html.index('<h2 class="evidence-head">')]
        assert charts.sheet.rel_path in head
        assert head.count("<figure") == 1, "page 1 carries ONE figure"


class TestThePdfPageOneHoldsOnlyPageOneContent:
    """The half that only a paginated document can answer."""

    @pytest.fixture(scope="class")
    def pages(self, tmp_path_factory, cfg):
        pytest.importorskip("weasyprint")
        pypdf = pytest.importorskip("pypdf")
        out = tmp_path_factory.mktemp("r3pdf")
        case, result = _analyse("bpfo", cfg)
        written = render_report(result, case.machine, out, pdf=True, case=case,
                                thresholds=cfg["thresholds"], profile="route")
        assert "pdf" in written, "no PDF engine reached — this pin needs one"
        reader = pypdf.PdfReader(str(written["pdf"]))
        return [squash(p.extract_text()) for p in reader.pages]

    def test_page_one_states_the_call(self, pages):
        first = pages[0]
        assert "ISO Zone D" in first
        assert "Bearing outer-race fault (BPFO)" in first
        assert "Recommendation" in first
        assert "Work order:" in first

    def test_page_one_holds_nothing_from_the_evidence_appendix(self, pages):
        """The section that starts page 2, and three blocks that live in it, are
        absent from page 1 — which is what "the reader can stop after page 1"
        means mechanically."""
        first = pages[0]
        for marker in ("Evidence", "MACHINE DETAILS", "ANALYSIS PARAMETERS",
                       "FREQUENCY RANGE", "POSSIBLE UNDERLYING CAUSES"):
            assert marker.lower() not in first.lower(), marker

    def test_the_work_order_field_comes_last(self, pages):
        """Last of the sheet's own fields. NOT last in the extracted text: the
        masthead carries a visually-hidden <h1> for screen readers, and pypdf
        reads it wherever weasyprint parked it."""
        first = pages[0]
        assert first.index("Work order:") > first.index("Recommendation")
        assert first.index("Work order:") > first.index("Escalation trigger:")


class TestTheEscalationTrigger:
    """Item 6 — deterministic, and made of numbers the report already prints."""

    def test_zone_d_watches_the_amplitude_because_there_is_no_next_boundary(self, cfg):
        _, result = _analyse("bpfo", cfg)
        assert result.iso.iso_zone == "D"
        trigger = escalation_trigger(result)
        assert trigger == ("Re-measure before the scheduled window if the BPFO "
                           "amplitude increases by 6 dB.")
        assert "ISO zone boundary" not in trigger

    def test_a_lower_zone_names_the_boundary_it_would_cross(self, cfg):
        """`belt_fault` is Zone C with a committed fault, so BOTH clauses print
        — the only fixture that exercises the whole sentence."""
        _, result = _analyse("belt_fault", cfg)
        assert result.iso.iso_zone == "C"
        trigger = escalation_trigger(result)
        assert trigger is not None
        assert "crosses the next ISO zone boundary" in trigger
        assert "amplitude increases by 6 dB" in trigger
        assert f"({result.iso.th_cd:g} mm/s)" in trigger

    def test_a_reading_with_no_committed_fault_watches_only_the_boundary(self, cfg):
        """`healthy` — Zone A, gate PASS, nothing committed. There is no fault
        amplitude to watch, so that clause is dropped rather than filled with a
        name the analysis did not make."""
        _, result = _analyse("healthy", cfg)
        trigger = escalation_trigger(result)
        assert trigger == ("Re-measure before the scheduled window if the overall crosses "
                           f"the next ISO zone boundary ({result.iso.th_ab:g} mm/s).")
        assert "6 dB" not in trigger

    def test_a_gate_fail_reading_triggers_nothing(self, cfg):
        """No diagnosis was made, so there is nothing to escalate from."""
        _, result = _analyse("bpfo", cfg)
        result = result.model_copy(deep=True)
        result.quality_gate.overall = "fail"
        assert escalation_trigger(result) is None

    def test_the_number_is_the_one_the_severity_strip_prints(self, tmp_path, cfg):
        """Not a second computation: the trigger and the margin sentence read
        one helper, so they cannot name different boundaries."""
        _, result, _, _, html = _render_pair("belt_fault", tmp_path, cfg)
        trigger = escalation_trigger(result)
        boundary = re.search(r"\(([\d.]+) mm/s\)", trigger).group(1)
        assert f"{boundary} mm/s" in visible_text(html)


class TestTheDataQualityLineBesideTheSeverity:
    """Item 7 — when the gate's `ski_slope` check wants attention, page 1 says so
    directly under the health line, and the zone STILL PRINTS.

    Driven with FIXTURE-1's own ski-slope file and its healthy twin, which is
    the pair that exists to make exactly this distinction: the same measurement
    point, the same machine content, one of them with a low-frequency artifact
    lifting a 1.20 mm/s point to 20.00 mm/s. GENERATED into tmp_path rather than
    read from `outputs/` (law #22, the CHARTS-2 precedent).
    """

    @staticmethod
    def _point(tmp_path, filename, cfg):
        from scripts.make_multi_sample import write_sample
        import scripts.make_multi_sample as S
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm

        write_sample(tmp_path)
        form = UploadForm(machine_alias=S.MACHINE_ALIAS, rpm=S.RPM, iso_group=S.ISO_GROUP,
                          iso_support=S.ISO_SUPPORT, bearing_model=S.BEARING_MODEL)
        case, _kind, _note = parse_upload(tmp_path / filename, form,
                                          bearings_cfg=load_config("bearings"))
        return case, run_analysis(case, iso_table=cfg["iso_table"],
                                  thresholds=cfg["thresholds"], rules=cfg["rules"])

    SKI = "compressor_nde_h_ski_slope_artifact_not_a_fault.csv"
    HEALTHY = "compressor_nde_h.csv"

    def test_the_gate_really_does_flag_the_artifact_file(self, tmp_path, cfg):
        """Precondition, measured — without it the two tests below are vacuous."""
        _, ski = self._point(tmp_path / "a", self.SKI, cfg)
        _, healthy = self._point(tmp_path / "b", self.HEALTHY, cfg)
        status = {c.name: c.status for c in ski.quality_gate.checks}
        assert status["ski_slope"] == "warn"
        twin = {c.name: c.status for c in healthy.quality_gate.checks}
        assert twin["ski_slope"] == "pass"

    def test_the_caveat_is_on_page_one_and_the_zone_is_still_there(self, tmp_path, cfg):
        case, result = self._point(tmp_path, self.SKI, cfg)
        sheet = fault_sheet_context(result, case.machine, case)
        assert sheet["data_quality"] == SKI_SLOPE_CAVEAT
        # The caveat says the number may be INFLATED, not that it is unknown —
        # so the zone still prints. Withholding it would be a different claim
        # from the one the check supports.
        assert sheet["health"]["zone_clause"]
        assert "ISO Zone" in sheet["health"]["zone_clause"]

        md = render_markdown(result, case.machine, case=case,
                             thresholds=cfg["thresholds"], profile="route")
        page1 = md[md.index("## Fault Sheet"):md.index("## Executive Summary")]
        assert SKI_SLOPE_CAVEAT in page1
        assert "ISO Zone" in page1

        html = render_html(result, case.machine, case=case,
                           thresholds=cfg["thresholds"], profile="route")
        head = visible_text(html[html.index('<section class="sheet">'):
                                 html.index('<h2 class="evidence-head">')])
        assert SKI_SLOPE_CAVEAT in head
        assert "ISO Zone" in head

    def test_the_healthy_twin_carries_no_caveat(self, tmp_path, cfg):
        case, result = self._point(tmp_path, self.HEALTHY, cfg)
        sheet = fault_sheet_context(result, case.machine, case)
        assert sheet["data_quality"] is None
        md = render_markdown(result, case.machine, case=case,
                             thresholds=cfg["thresholds"], profile="route")
        assert SKI_SLOPE_CAVEAT not in md
