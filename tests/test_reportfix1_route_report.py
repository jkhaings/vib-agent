"""Session REPORTFIX-1 — four measured points, one job, one DOCUMENT.

The pin INTAKE-2 and REPORT-3 could each write only half of. INTAKE-2 proved
the four points are analysed and their numbers reach the wire
(`test_intake2_route_e2e.py`); REPORT-3 proved a renderer handed a roster puts
the machine's worst point on page 1 (`test_r3_locations.py`). Neither could
show the two ends joined, because nothing in the product passed a roster to the
renderer — `worker.py::_document_context` returned `{case, thresholds,
comparison}` and the multi-location renderer was unreachable in production.

So this file drives the REAL intake — the same `POST /api/jobs` a browser
posts, twelve files at four points, the form values FIXTURE-1's README says a
human types — and reads the document that comes out the other end.

**The ground truth it is read against** (`outputs/demo_package/multi_location/`):
a BPFO is planted at Compressor DE and NOWHERE else. Motor DE is Zone A,
Motor NDE Zone B, Compressor DE Zone D, Compressor NDE Zone A. The document's
own subject is location 1 — Motor DE, a healthy point — so every assertion here
is about a report whose subject is fine reporting a machine that is not. That
asymmetry is the whole diagnostic act on a route, and it is what a
single-location fixture cannot exercise.

The second run substitutes FIXTURE-1's 13th file at Compressor NDE: a healthy
point lifted to 20 mm/s RMS by a low-frequency integration artifact. It is a
false-positive trap, and INTAKE-2's F-7 recorded that nothing exercised it at
the multi-location level — which is exactly where a report might call the wrong
point the worst.

No API key and no paid call: the fake client is handed in with no canned
responses, so every job takes the designed degrade lane and still produces
every number read below.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "outputs" / "demo_package" / "multi_location"

pytestmark = pytest.mark.skipif(
    not _FIXTURE.is_dir(),
    reason="outputs/demo_package/multi_location/ is not in the tree (arrives with FIXTURE-1)",
)

#: FIXTURE-1's README, §"The four points", transcribed as data — label, file
#: stem, planted zone. Restated rather than parsed out of the markdown so a
#: reader can diff two tables by eye, and so a reworded README cannot silently
#: change what this test believes.
_POINTS = (
    ("Motor DE",       "motor_de",       "A"),
    ("Motor NDE",      "motor_nde",      "B"),
    ("Compressor DE",  "compressor_de",  "D"),
    ("Compressor NDE", "compressor_nde", "A"),
)

#: The README's "every form value a human types" table.
_MACHINE = {
    "machine_alias": "Synthetic Compressor Train 01",
    "machine_type": "compressor",
    "rpm": "1800",
    "rated_kw": "75",
    "iso_group": "2",
    "iso_support": "rigid",
    "bearing_model": "6206",
    "velocity_unit": "mm_s",
    "detection_type": "rms",
    "mode": "spectrum",
}

_DIRECTIONS = (("h", "radial_h"), ("v", "radial_v"), ("a", "axial"))

#: The planted outer-race defect at Compressor DE, from the README.
_BPFO_HZ = 107.1875
_BPFO_ORDER = 3.57

#: FIXTURE-1's 13th file — a healthy point plus an integration artifact.
_SKI_SLOPE = "compressor_nde_h_ski_slope_artifact_not_a_fault.csv"


def _files_and_data(*, ski_slope_at_compressor_nde: bool = False):
    """The whole route in one post — the slot naming the browser form uses.

    Location 1 is UNPREFIXED (`file`, `direction`, `measurement_location`); every
    other location is `loc<i>_`. That asymmetry is INTAKE-2's and it is
    load-bearing: it is what keeps a single-location post byte-identical to the
    pre-INTAKE-2 product.
    """
    files, data, handles = [], dict(_MACHINE), []
    for index, (label, stem, _zone) in enumerate(_POINTS, start=1):
        for slot, (suffix, direction) in enumerate(_DIRECTIONS):
            name = f"{stem}_{suffix}.csv"
            if ski_slope_at_compressor_nde and name == "compressor_nde_h.csv":
                name = _SKI_SLOPE
            path = _FIXTURE / name
            assert path.is_file(), f"FIXTURE-1 file missing: {path.name}"
            handle = path.open("rb")
            handles.append(handle)
            slot_name = "file" if slot == 0 else f"file_{slot + 1}"
            dir_name = "direction" if slot == 0 else f"direction_{slot + 1}"
            if index == 1:
                files.append((slot_name, (path.name, handle, "text/csv")))
                data[dir_name] = direction
            else:
                files.append((f"loc{index}_{slot_name}", (path.name, handle, "text/csv")))
                data[f"loc{index}_{dir_name}"] = direction
        if index == 1:
            data["measurement_location"] = label
        else:
            data[f"loc{index}_label"] = label
    return files, data, handles


class _Rendered:
    """Both documents the job produced, captured where they are handed on.

    `report.md` cannot be read afterwards — `_keep_only_report` deletes every
    intermediate the moment `report.pdf` exists, which is the privacy promise
    working — and a PDF's text is compressed. So the v2 HTML page is spied as
    `_pdf_from_html` receives it and the markdown as `render_pdf` /
    `render_drafted_pdf` receive it in `markdown_text=`.

    BOTH, not whichever one this host happens to reach: Session HIST-2-FIX
    records five tests that passed here and failed on the operator's box for no
    reason but the PDF engine. Capturing both takes the host out of the
    assertion.
    """

    def __init__(self) -> None:
        self.html: list[str] = []
        self.markdown: list[str] = []

    @property
    def documents(self) -> dict[str, str]:
        assert self.html, "no HTML page was rendered"
        assert self.markdown, "no markdown document was rendered"
        return {"html": _visible(self.html[-1]), "markdown": self.markdown[-1]}


def _visible(html: str) -> str:
    """What a READER sees, not what the markup says."""
    body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
    return re.sub(r"[ \t ]+", " ", re.sub(r"(?s)<[^>]+>", " ", body))


#: Page 1 ends where the body begins. REPORT-3 item 2 put Evidence on page 2 and
#: the Executive Summary immediately after the sheet, so the first of these that
#: appears is the end of the sheet in either document.
_PAGE_ONE_ENDS = ("Executive Summary", "Evidence by location", "Machine Details")


def _page_one(document: str) -> str:
    cuts = [document.index(m) for m in _PAGE_ONE_ENDS if m in document]
    assert cuts, f"no page-1 boundary found in:\n{document[:600]}"
    return document[:min(cuts)]


def _run_route(*, ski_slope: bool = False) -> tuple[dict, _Rendered]:
    """One real job through the real intake, with both documents captured."""
    from vib_agent.report import generate
    from vib_agent.webapp import worker

    rendered = _Rendered()
    with pytest.MonkeyPatch.context() as patch:
        original_html = generate._pdf_from_html

        def _spy_html(html, out_dir, *args, **kwargs):
            rendered.html.append(html)
            return original_html(html, out_dir, *args, **kwargs)

        patch.setattr(generate, "_pdf_from_html", _spy_html)

        for name in ("render_pdf", "render_drafted_pdf"):
            original = getattr(worker, name)

            def _spy_markdown(*args, _original=original, **kwargs):
                text = kwargs.get("markdown_text")
                if text is not None:
                    rendered.markdown.append(text)
                return _original(*args, **kwargs)

            patch.setattr(worker, name, _spy_markdown)

        app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                         anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
        with TestClient(app) as client:
            files, data, handles = _files_and_data(ski_slope_at_compressor_nde=ski_slope)
            data["invite_code"] = "demo-code"
            try:
                res = client.post("/api/jobs", files=files, data=data)
            finally:
                for handle in handles:
                    handle.close()
            assert res.status_code == 202, f"the route was refused: {res.status_code} {res.text}"
            body = _poll_until_terminal(client, res.json()["job_id"], timeout_s=600)
    return body, rendered


@pytest.fixture(scope="module")
def route():
    return _run_route()


@pytest.fixture(scope="module")
def ski_route():
    return _run_route(ski_slope=True)


@pytest.fixture(scope="module")
def pages(route):
    _body, rendered = route
    return {kind: _page_one(doc) for kind, doc in rendered.documents.items()}


DOCS = ("markdown", "html")


class TestTheRouteReachesADocument:

    def test_the_job_is_terminal_and_not_an_error(self, route):
        body, _ = route
        assert body["state"] in ("done", "degraded"), body

    def test_all_four_points_were_analysed(self, route):
        body, _ = route
        others = body.get("locations") or []
        assert [e["label"] for e in others] == [lbl for lbl, _s, _z in _POINTS[1:]]
        assert len(others) + 1 == len(_POINTS)


class TestPageOneIsTheMachineNotTheFirstPoint:
    """The document's subject is Motor DE, which is HEALTHY. Everything here is
    about page 1 refusing to read as a clean bill for the machine."""

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_machine_severity_is_zone_d(self, doc, pages):
        assert "Zone D" in pages[doc], pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_zone_d_is_named_at_compressor_de(self, doc, pages):
        """A zone with no point beside it is the defect this session exists to
        end: the reader cannot act on "the machine is Zone D"."""
        assert "Compressor DE" in pages[doc], pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_fault_is_named_as_a_bearing_outer_race_fault(self, doc, pages):
        page = pages[doc].lower()
        assert "outer-race" in page or "outer race" in page, pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_fault_carries_its_frequency_and_its_shaft_order(self, doc, pages):
        """The reason the live result is carried in-process at all. The wire
        holds `committed_fault` as a bare id — no Hz, no order — and a report
        may not print a number no tool result produced."""
        page = pages[doc]
        assert f"{_BPFO_HZ:.2f}" in page or f"{_BPFO_HZ:.1f}" in page, page
        assert f"{_BPFO_ORDER:.2f}" in page or f"{_BPFO_ORDER:.1f}" in page, page

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_three_healthy_points_are_not_named_as_faults(self, doc, pages):
        """They may be LISTED — page 1 names every point measured — but none of
        them may appear as a position the fault was found at."""
        page = pages[doc]
        fault_line = ""
        for line in page.splitlines():
            if "outer-race" in line.lower() or "outer race" in line.lower():
                fault_line = line
                break
        assert fault_line, page
        for healthy, _stem, _zone in (_POINTS[0], _POINTS[1], _POINTS[3]):
            assert healthy not in fault_line, f"{healthy} is named on the fault line: {fault_line}"

    @pytest.mark.parametrize("doc", DOCS)
    def test_there_is_exactly_one_conclusion(self, doc, pages):
        """One machine, one call — not one conclusion per point."""
        page = pages[doc].lower()
        assert page.count("decision:") <= 1, pages[doc]


class TestTheOneLineVerdictIsTheMachines:
    """The status line is the most quotable thing in the document and the one a
    reader may take instead of the page. On a route it must be the MACHINE's.

    Measured here before the fix: it read "ISO ZONE A — GOOD · no fault
    signature identified" directly above a health line reading "ISO Zone D —
    unacceptable ... Bearing outer-race fault (BPFO) at Compressor DE". It was
    location 1's own verdict, and location 1 is healthy. That is a clean bill on
    a machine with a Zone D bearing fault on it — the standing Part C rule calls
    it a fail however green the job looks, and it is the defect class Phase 7B
    found.
    """

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_status_line_states_the_machines_zone(self, doc, pages):
        page = pages[doc].upper()
        assert "ZONE D" in page, pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_status_line_does_not_call_the_machine_good(self, doc, pages):
        """The exact string that was there, named so it cannot come back."""
        page = pages[doc].upper()
        assert "ISO ZONE A — GOOD" not in page, pages[doc]
        assert "NO FAULT SIGNATURE IDENTIFIED" not in page, pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_verdict_and_the_health_line_agree(self, doc, pages):
        """Two statements of one judgement, on one page, in one document."""
        page = pages[doc].upper()
        assert page.count("ZONE A") == 0 or "ZONE D" in page, pages[doc]


class TestEvidenceIsPerLocationInRosterOrder:

    @pytest.mark.parametrize("doc", DOCS)
    def test_every_point_has_its_own_evidence_section(self, doc, route):
        _body, rendered = route
        document = rendered.documents[doc]
        assert "Evidence by location" in document, document[:600]
        for label, _stem, _zone in _POINTS:
            assert label in document, f"{label} has no section"

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_sections_are_in_the_order_the_route_was_walked(self, doc, route):
        """Route order, never severity order — an analyst reads against their own
        route sheet, and re-sorting would put them somewhere other than where
        they stood (contract §5 rule 5)."""
        _body, rendered = route
        section = rendered.documents[doc].partition("Evidence by location")[2]
        assert section, "no per-location evidence was rendered"
        positions = [section.index(label) for label, _s, _z in _POINTS]
        assert positions == sorted(positions), (
            f"roster order is {[lbl for lbl, _s, _z in _POINTS]}, document order is not")

    @pytest.mark.parametrize("doc", DOCS)
    def test_each_points_own_zone_is_stated(self, doc, route):
        _body, rendered = route
        section = rendered.documents[doc].partition("Evidence by location")[2]
        for label, _stem, zone in _POINTS:
            window = section[section.index(label):section.index(label) + 400]
            assert f"Zone {zone}" in window, (
                f"{label} should read Zone {zone}; section said:\n{window[:300]}")


class TestTheSkiSlopeTrapIsCaughtOnPageOne:
    """FIXTURE-1's 13th file at Compressor NDE — a HEALTHY point lifted to
    20 mm/s RMS by a low-frequency integration artifact, which is Zone D on a
    machine that is Zone A.

    INTAKE-2's F-7: the route pin used the clean file, so the trap was never
    exercised where it matters. REPORT-3's F-1 is the argument this makes into a
    pin — the caveat and the severity land on the same page, and the caveat is
    what tells the reader which to believe first.
    """

    @pytest.fixture(scope="class")
    def ski_pages(self, ski_route):
        _body, rendered = ski_route
        return {kind: _page_one(doc) for kind, doc in rendered.documents.items()}

    def test_the_job_still_reaches_a_document(self, ski_route):
        body, _ = ski_route
        assert body["state"] in ("done", "degraded"), body

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_data_quality_line_is_present(self, doc, ski_pages):
        page = ski_pages[doc]
        assert "artifact" in page.lower(), page
        assert "re-measure" in page.lower(), page

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_data_quality_line_sits_under_the_health_line(self, doc, ski_pages):
        """Order is the claim: a caveat printed above the severity it qualifies
        reads as a caveat about the machine, not about the number."""
        page = ski_pages[doc]
        health = page.lower().index("zone")
        caveat = page.lower().index("re-measure")
        assert health < caveat, page

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_artifact_point_is_named_rather_than_left_to_be_guessed(self, doc, ski_pages):
        """The flagged point is NOT the one on the headline — Compressor DE wins
        the tie on roster order — so an unqualified caveat would read as doubt
        about the Zone D bearing fault, which is the opposite of the truth."""
        assert "Compressor NDE" in ski_pages[doc], ski_pages[doc]

    def test_the_planted_fault_is_still_called_at_compressor_de(self, ski_pages):
        """The trap's real danger: a 20 mm/s artifact out-ranking a 5.2 mm/s
        genuine fault and moving the machine's headline to a healthy point."""
        page = ski_pages["markdown"]
        assert "Compressor DE" in page, page
