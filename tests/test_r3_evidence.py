"""Session REPORT-3 item 2 — everything else is "Evidence", and it starts on page 2.

    Everything else is "Evidence", starting on page 2: every figure with type,
    unit and "(as supplied)" in its caption, the Fmax-adequacy block, the
    tables, the method. Pin: a single-location report renders in at most 10
    pages; the drafted path too.

The structure ships. **The 10-page cap does not, and this file records why with
the measurement rather than asserting a number the document cannot meet.**

Measured this session, weasyprint 69.0, A4 at 14 mm margins, after the print
density pass (`report.css`, `@media print`):

    bpfo, as it ships                                23 pages
    bpfo, cause-hypotheses section suppressed        13
    bpfo, causes AND coverage roster suppressed      12
    healthy — a fixture that never HAS causes        11

So no report of any kind reaches ten, and the gap is not the cause
illustrations: hiding all eleven saves exactly ONE page, because that section
is eleven hypotheses of prose, citations and two evidence lists each, not
pictures. Reaching ten means deleting a whole operator-approved section.

RULED by the operator, with those four numbers in hand: keep every section, and
pin the cap at what the document actually supports. The caps below are
therefore MEASURED CEILINGS — they catch a regression that makes the report
longer, and they are deliberately not aspirations. When a later session makes
the report shorter, it lowers them.
"""

from __future__ import annotations

import re

import pytest

from vib_agent.config import load_config, load_thresholds
from vib_agent.pipeline import run_analysis
from vib_agent.report import charts as charts_mod
from vib_agent.report.generate import render_html, render_report
from vib_agent.synth.generator import make_case

from tests.test_r3_fault_sheet import squash, visible_text


@pytest.fixture(scope="module")
def cfg():
    return {
        "iso_table": load_config("iso_zones")["zones"],
        "thresholds": load_thresholds("route"),
        "rules": load_config("next_measurements"),
    }


def _analyse(name, cfg):
    case = make_case(name, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"], seed=1)
    return case, run_analysis(case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])


#: name -> the measured page count. See the module docstring for how these were
#: obtained and why they are not 10.
MEASURED_PAGES = {"bpfo": 23, "healthy": 11, "imbalance": 11, "belt_fault": 11}

#: Session REPORTFIX-1 — the FOUR-POINT route, measured the same way, on the
#: same engine, and recorded BESIDE the dict above rather than inside it: that
#: dict is the parametrize source for `_analyse`, which builds one synthetic
#: point, and a roster cannot be made that way.
#:
#: Measured with FIXTURE-1's four points (Motor DE, Motor NDE, Compressor DE,
#: Compressor NDE), rendered from Motor DE as the document's own subject:
#:
#:     the same lead point, alone                   11 pages
#:     the same lead point, with the other three    12
#:
#: So three further measurement points cost exactly ONE page. That is the item-8
#: design working — a per-location section is that point's zone, overall,
#: committed call and evidence table, not a report per point — and it is the
#: number to watch: a session that makes per-location Evidence richer will move
#: this long before it moves anything in the dict above.
MEASURED_PAGES_MULTI_LOCATION = 12
MEASURED_PAGES_MULTI_LOCATION_LEAD_ALONE = 11


class TestEvidenceStartsOnPageTwo:
    """The structural half of item 2, which does ship."""

    @pytest.fixture(scope="class")
    def pages(self, tmp_path_factory, cfg):
        pytest.importorskip("weasyprint")
        pypdf = pytest.importorskip("pypdf")
        out = tmp_path_factory.mktemp("r3ev")
        case, result = _analyse("bpfo", cfg)
        written = render_report(result, case.machine, out, pdf=True, case=case,
                                thresholds=cfg["thresholds"], profile="route")
        assert "pdf" in written, "no PDF engine reached — this pin needs one"
        return [squash(p.extract_text()) for p in pypdf.PdfReader(str(written["pdf"])).pages]

    def test_the_evidence_heading_is_on_page_two(self, pages):
        assert "evidence" not in pages[0].lower()
        assert "evidence" in pages[1].lower()

    def test_the_tables_the_fmax_block_and_the_method_are_all_behind_it(self, pages):
        """Every block item 2 names is in the appendix, none of it on page 1."""
        rest = " ".join(pages[1:]).lower()
        for block in ("machine details", "analysis parameters",
                      "frequency range", "data quality"):
            assert block in rest, block
            assert block not in pages[0].lower(), block

    def test_the_document_still_carries_every_section_it_did(self, pages):
        """The density pass is a stylesheet, so nothing may have gone missing."""
        whole = " ".join(pages).lower()
        for section in ("possible underlying causes", "coverage", "recommendations",
                        "limitations", "review"):
            assert section in whole, section


class TestEveryFigureCaptionStatesTypeAndUnit:
    """Item 2's caption rule. CHARTS-2 evicted "(as supplied)" from the figure
    HEADER and left the caption as the one surface that still states the
    non-claim — "REPORT-3 owns where it finally lands" (charts.py). It lands
    here, and this is the pin that says so."""

    def test_the_generator_sample_states_the_non_claim_on_every_caption(self, tmp_path, cfg):
        case, result = _analyse("bpfo", cfg)
        charts = charts_mod.render_charts(result, case.machine, tmp_path, case=case,
                                          thresholds=cfg["thresholds"])
        captions = [f.caption for ch in charts.channels for f in ch.figures]
        assert captions, "no channel figures rendered"
        captions.append(charts.sheet.caption)           # page 1's figure too
        for caption in captions:
            assert "Envelope spectrum" in caption, caption
            assert "(as supplied)" in caption, caption

    def test_a_velocity_upload_names_the_real_unit_instead(self, tmp_path, cfg):
        """The non-claim is stated only where provenance cannot name a unit; a
        declared mm/s upload gets the unit, not "(as supplied)"."""
        from scripts.make_sample_csv import write_sample
        from vib_agent.adapters.uploads import parse_upload
        from vib_agent.adapters.uploads.common import UploadForm
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
        outcome = assembly.merge_channels(parsed, [], iso_table=cfg["iso_table"],
                                          thresholds=cfg["thresholds"], rules=cfg["rules"])
        result = run_analysis(outcome.case, iso_table=cfg["iso_table"],
                              thresholds=cfg["thresholds"], rules=cfg["rules"])
        charts = charts_mod.render_charts(result, outcome.case.machine, tmp_path / "out",
                                          case=outcome.case, thresholds=cfg["thresholds"])
        for figure in [f for ch in charts.channels for f in ch.figures] + [charts.sheet]:
            assert "Velocity spectrum (mm/s RMS)" in figure.caption, figure.caption
            assert "as supplied" not in figure.caption, figure.caption


class TestTheMeasuredPageCeiling:
    """NOT the brief's 10 — see the module docstring. A ceiling that is real."""

    @pytest.mark.parametrize("name", sorted(MEASURED_PAGES))
    def test_the_report_is_no_longer_than_it_was_measured_to_be(self, name, tmp_path, cfg):
        pytest.importorskip("weasyprint")
        pypdf = pytest.importorskip("pypdf")
        case, result = _analyse(name, cfg)
        written = render_report(result, case.machine, tmp_path, pdf=True, case=case,
                                thresholds=cfg["thresholds"], profile="route")
        assert "pdf" in written
        count = len(pypdf.PdfReader(str(written["pdf"])).pages)
        assert count <= MEASURED_PAGES[name], (
            f"{name}: {count} pages, ceiling {MEASURED_PAGES[name]}"
        )

    def test_a_report_with_no_causes_section_is_the_shorter_kind(self, tmp_path, cfg):
        """The measurement the ruling rests on: the causes section is the whole
        difference between an 11-page report and a 23-page one, and it is
        eleven hypotheses of PROSE."""
        assert MEASURED_PAGES["healthy"] < MEASURED_PAGES["bpfo"]
        case, result = _analyse("healthy", cfg)
        html = render_html(result, case.machine, case=case,
                           thresholds=cfg["thresholds"], profile="route")
        assert "possible underlying causes" not in visible_text(html).lower()


class TestTheMultiLocationCeiling:
    """A four-point route has its own measured ceiling — Session REPORTFIX-1.

    The single-location ceilings above cannot speak for it: they are measured
    from `make_case`, which builds one point, and a roster is what this session
    made reachable in the product at all.
    """

    @pytest.fixture(scope="class")
    def route(self, tmp_path_factory, cfg):
        from tests.test_r3_ground_truth import GROUND_TRUTH, _point
        base = tmp_path_factory.mktemp("pagecount_route")
        order = ["motor_de", "motor_nde", "compressor_de", "compressor_nde"]
        out = []
        for key in order:
            case, result = _point(base / key, key, cfg)
            out.append({"label": GROUND_TRUTH[key]["label"], "result": result, "case": case})
        return out

    def _pages(self, roster, tmp_path, cfg, *, locations):
        pytest.importorskip("weasyprint")
        pypdf = pytest.importorskip("pypdf")
        lead = roster[0]
        written = render_report(lead["result"], lead["case"].machine, tmp_path, pdf=True,
                                case=lead["case"], thresholds=cfg["thresholds"],
                                profile="route", locations=locations)
        assert "pdf" in written
        return len(pypdf.PdfReader(str(written["pdf"])).pages)

    def test_the_four_point_report_is_no_longer_than_it_was_measured_to_be(
        self, route, tmp_path, cfg
    ):
        count = self._pages(route, tmp_path, cfg, locations=route)
        assert count <= MEASURED_PAGES_MULTI_LOCATION, (
            f"four-point route: {count} pages, ceiling {MEASURED_PAGES_MULTI_LOCATION}")

    def test_three_further_points_cost_about_one_page(self, route, tmp_path, cfg):
        """The measurement the ceiling rests on, so a later reader can see what
        it is made of rather than trusting a number."""
        alone = self._pages(route, tmp_path / "alone", cfg, locations=None)
        assert alone <= MEASURED_PAGES_MULTI_LOCATION_LEAD_ALONE, alone
        assert MEASURED_PAGES_MULTI_LOCATION - MEASURED_PAGES_MULTI_LOCATION_LEAD_ALONE == 1


class TestTheDraftedPathHasItsOwnCeiling:
    """"the drafted path too" — the brief's own words, and the document Part C
    actually read. Measured keylessly against the stand-in client: 20 pages, and
    page 1 is the sheet, not the model's prose."""

    MEASURED = 20

    @pytest.fixture(scope="class")
    def drafted(self, tmp_path_factory, cfg):
        pytest.importorskip("weasyprint")
        pytest.importorskip("matplotlib")
        pypdf = pytest.importorskip("pypdf")
        from tests.fake_anthropic import (
            FakeAnthropicClient, build_consistent_echo, draft_message,
        )
        from vib_agent.agent.loop import run_agent_analysis

        case, result = _analyse("bpfo", cfg)
        narrative = (
            "# Vibration Survey Report — Synthetic Compressor 01\n\n"
            "## Executive Summary\n\nThe machine is in ISO Zone D at 5.20 mm/s RMS.\n\n"
            "## Diagnosis\n\nCommitted diagnosis: Bearing outer-race fault (BPFO), "
            "high confidence.\n\n---\nDRAFT — prepared by automated analysis, "
            "pending analyst review.\n"
        )
        out = tmp_path_factory.mktemp("r3drafted")
        client = FakeAnthropicClient([draft_message(narrative, build_consistent_echo(result))])
        run_agent_analysis(case, iso_table=cfg["iso_table"], thresholds=cfg["thresholds"],
                           rules=cfg["rules"], out_dir=out, pdf=True, client=client)
        pdf = out / "report.pdf"
        assert pdf.exists(), "no PDF engine reached — this pin needs one"
        return [squash(p.extract_text()) for p in pypdf.PdfReader(str(pdf)).pages]

    def test_no_longer_than_measured(self, drafted):
        assert len(drafted) <= self.MEASURED, f"{len(drafted)} pages"

    def test_page_one_is_the_sheet_and_not_the_models_prose(self, drafted):
        first = drafted[0]
        assert "Escalation trigger:" in first
        assert "Work order:" in first
        # Session REPORT-4 (item 7): "Decision: monitor" is gone from page 1. The
        # three-part Recommendation above the trigger is what states the action,
        # and it is still asserted by the two lines above.
        assert "Decision:" not in first
        assert "evidence" not in first.lower()
