"""Session REPORT-3 item 8 — the renderer takes locations[].

    Multi-location: the renderer takes locations[]. Page 1 is machine-level —
    machine severity is the worst location's zone with that location named; the
    same fault at more than one location is named once with every position; one
    conclusion. Evidence is per location, in roster order. One location renders
    byte-identical to today's single-location report except for the page-1
    restructure. Until INTAKE-2 lands, drive this with an in-test list built
    from the trio.

Driven from FIXTURE-1's four-point route sample rather than the trio, because
the trio is one point measured three ways and this is about MORE THAN ONE
POINT. The list is built in-test, exactly as the brief says, and its shape is
the one INTAKE-2 item 4 describes: label, result, case (plus charts when the
caller has them). When `docs/contracts/machine_result.md` lands, the names
reconcile against it.
"""

from __future__ import annotations

import pytest

from tests.test_r3_fault_sheet import cfg, visible_text  # noqa: F401  (fixture)
from tests.test_r3_ground_truth import GROUND_TRUTH, _point

from vib_agent.report.generate import (
    fault_sheet_context,
    location_roster,
    render_html,
    render_markdown,
)

ORDER = ["motor_de", "motor_nde", "compressor_de", "compressor_nde"]


@pytest.fixture(scope="module")
def route(tmp_path_factory, cfg):
    """The four points, in the order the route is walked."""
    base = tmp_path_factory.mktemp("route")
    out = []
    for key in ORDER:
        case, result = _point(base / key, key, cfg)
        out.append({"label": GROUND_TRUTH[key]["label"], "result": result, "case": case})
    return out


class TestOneLocationIsTheProductAsItWas:

    def test_no_roster_renders_for_none(self, cfg):
        assert location_roster(None) is None

    def test_no_roster_renders_for_a_single_point(self, route):
        """A machine measured at one point IS the single-location product. A
        heading saying "Evidence — Motor DE" above the only evidence there is
        would be furniture, not information."""
        assert location_roster(route[:1]) is None

    def test_a_single_location_document_is_byte_identical_after_page_one(self, route, cfg):
        """The identity property the brief asks for, stated exactly: byte-
        identical "except for the page-1 restructure".

        A one-entry roster DOES change page 1, and legitimately — item 1 puts
        "measurement location(s)" on the sheet, so a caller that names the point
        gets it named. What must not change is anything else, and that is what
        is measured here: everything from the Evidence heading onward is the
        same bytes, and no Evidence-by-location section appears.
        """
        loc = route[2]                       # Compressor DE — the faulted point
        kw = dict(case=loc["case"], thresholds=cfg["thresholds"], profile="route")

        plain_md = render_markdown(loc["result"], loc["case"].machine, **kw)
        rostered_md = render_markdown(loc["result"], loc["case"].machine,
                                      locations=[loc], **kw)
        assert (plain_md[plain_md.index("## Executive Summary"):]
                == rostered_md[rostered_md.index("## Executive Summary"):])

        plain_html = render_html(loc["result"], loc["case"].machine, **kw)
        rostered_html = render_html(loc["result"], loc["case"].machine,
                                    locations=[loc], **kw)
        head = '<h2 class="evidence-head">'
        assert plain_html[plain_html.index(head):] == rostered_html[rostered_html.index(head):]

        for doc in (rostered_md, rostered_html):
            assert "Evidence by location" not in doc

    def test_the_only_page_one_change_is_the_point_being_named(self, route, cfg):
        loc = route[2]
        kw = dict(case=loc["case"], thresholds=cfg["thresholds"], profile="route")
        rostered = render_markdown(loc["result"], loc["case"].machine, locations=[loc], **kw)
        sheet = rostered[:rostered.index("## Executive Summary")]
        assert "Compressor DE" in sheet


class TestPageOneIsMachineLevel:

    def test_the_machine_takes_the_worst_locations_zone_and_names_it(self, route, cfg):
        """A/B/D/A — the machine is Zone D, and the sheet says WHERE."""
        lead = route[0]                      # rendered from Motor DE, a Zone A point
        sheet = fault_sheet_context(lead["result"], lead["case"].machine, lead["case"],
                                    locations=route)
        assert sheet["health"]["zone"] == "D"
        assert sheet["worst_location"] == "Compressor DE"
        assert sheet["fault"]["label"].startswith("Bearing outer-race fault")

    def test_the_roster_order_decides_ties_not_amplitude(self, route):
        """Two points in Zone D are both in Zone D; naming one of them worse
        would be a ranking pdm_core did not make."""
        de = route[2]
        both = [dict(de, label="First D"), dict(de, label="Second D")]
        sheet = fault_sheet_context(de["result"], de["case"].machine, de["case"],
                                    locations=both)
        assert sheet["worst_location"] == "First D"

    def test_the_fault_is_named_once_with_every_position(self, route):
        de = route[2]
        both = [route[0], dict(de, label="Compressor DE"), dict(de, label="Compressor IB")]
        sheet = fault_sheet_context(de["result"], de["case"].machine, de["case"],
                                    locations=both)
        assert sheet["fault"]["positions"] == ["Compressor DE", "Compressor IB"]
        # ONE conclusion, not one per location.
        assert isinstance(sheet["fault"], dict)
        # Session REPORT-4 (item 7): no decision token, on a route either.
        assert "decision" not in sheet

    def test_every_point_is_listed_on_page_one(self, route, cfg):
        lead = route[0]
        sheet = fault_sheet_context(lead["result"], lead["case"].machine, lead["case"],
                                    locations=route)
        assert sheet["locations"] == [GROUND_TRUTH[k]["label"] for k in ORDER]


class TestEvidenceIsPerLocationInRosterOrder:

    def test_the_roster_keeps_the_order_the_route_was_walked(self, route):
        rows = location_roster(route)
        assert [r["label"] for r in rows] == [GROUND_TRUTH[k]["label"] for k in ORDER]

    def test_each_row_carries_that_points_own_call(self, route):
        rows = {r["label"]: r for r in location_roster(route)}
        assert rows["Compressor DE"]["committed"] == ["Bearing outer-race fault (BPFO)"]
        assert rows["Compressor DE"]["zone"] == "D"
        for healthy in ("Motor DE", "Motor NDE", "Compressor NDE"):
            assert rows[healthy]["committed"] is None, healthy

    @pytest.mark.parametrize("renderer", ["markdown", "html"])
    def test_both_documents_render_every_point_in_order(self, renderer, route, cfg):
        lead = route[0]
        kw = dict(case=lead["case"], thresholds=cfg["thresholds"], profile="route",
                  locations=route)
        doc = (render_markdown(lead["result"], lead["case"].machine, **kw) if renderer == "markdown"
               else visible_text(render_html(lead["result"], lead["case"].machine, **kw)))
        assert "Evidence by location" in doc
        positions = [doc.index(GROUND_TRUTH[k]["label"] + " — ISO Zone") for k in ORDER]
        assert positions == sorted(positions), doc[:0]

    @pytest.mark.parametrize("renderer", ["markdown", "html"])
    def test_a_healthy_point_says_nothing_was_committed_rather_than_going_silent(
        self, renderer, route, cfg
    ):
        lead = route[0]
        kw = dict(case=lead["case"], thresholds=cfg["thresholds"], profile="route",
                  locations=route)
        doc = (render_markdown(lead["result"], lead["case"].machine, **kw) if renderer == "markdown"
               else visible_text(render_html(lead["result"], lead["case"].machine, **kw)))
        assert "Committed at this point:" in doc
        assert "nothing." in doc
