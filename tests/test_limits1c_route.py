"""Session LIMITS-1c item 7 — two real jobs, the whole way through.

The pin that matters. Everything else in this session is a layer tested in
isolation; this is the one that asks whether an analyst who types three numbers
into the form gets a report that honours them — and whether an analyst who
types nothing gets the byte-identical document they got yesterday.

Built on `tests/test_reportfix1_route_report.py`'s shape, deliberately: the same
FIXTURE-1 four-point route, the same `_files_and_data` slot naming (location 1
UNPREFIXED, 2..N `loc<i>_`), the same `_Rendered` spy that captures BOTH
documents where they are handed on, and the same `_page_one` slice. Copied
rather than imported where the two differ, so neither file constrains the other.

THE ARITHMETIC, checked before the assertions were written. FIXTURE-1's four
points against ISO 20816-3 group 2 / rigid (1.4 / 2.8 / 4.5) and against the
plant limit 5 / 8 / 12, on `_zone_for`'s `>=` ladder:

    point            mm/s   ISO   custom   iso_zone_would_be
    Motor DE         1.10    A      A          A
    Motor NDE        1.80    B      A          B
    Compressor DE    5.20    D      B          D      <- the worst point
    Compressor NDE   1.20    A      A          A

Compressor DE at 5.20 mm/s is the SAME reading as the seeded `bpfo` case
LIMITS-1b ruled its four health-line states from, so `SESSION_LIMITS1B.md`
§5.1's sentence is reproducible here, on a real route, through the real intake.

And it is an EXTRA location — location 1 is Motor DE — so page 1 reaches it
through the wire-only fallback in `_sheet_health`. That is exactly the path
LIMITS-1b F-2 said would print "per ISO 20816-3" over a plant number, and it is
why this file is the acceptance for putting `zone_basis` on the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_reportfix1_route_report import (
    _DIRECTIONS,
    _MACHINE,
    _POINTS,
    _Rendered,
    _page_one,
    _visible,
)
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "outputs" / "demo_package" / "multi_location"

pytestmark = pytest.mark.skipif(
    not _FIXTURE.is_dir(),
    reason="outputs/demo_package/multi_location/ is not in the tree (arrives with FIXTURE-1)",
)

#: The brief's trio.
LIMITS = {"limit_ab": "5.0", "limit_bc": "8.0", "limit_cd": "12.0"}

#: What SESSION_LIMITS1B.md §5 ruled, quoted once.
BASIS_PHRASE = "machine-specific limits"
GATE_SENTENCE = "Machine-specific limits also govern the 1× severity gate for this machine."

DOCS = ("markdown", "html")


def _files_and_data(limits: dict | None):
    """The whole route in one post, optionally carrying the three limits."""
    files, data, handles = [], dict(_MACHINE), []
    for index, (label, stem, _zone) in enumerate(_POINTS, start=1):
        for slot, (suffix, direction) in enumerate(_DIRECTIONS):
            path = _FIXTURE / f"{stem}_{suffix}.csv"
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
    # Machine-level, so ONE copy reaches every point through `form_dict` ->
    # `location_form_dict`. That is what "limits on every location" means here.
    data.update(limits or {})
    return files, data, handles


def _run_route(limits: dict | None):
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
            files, data, handles = _files_and_data(limits)
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
def custom():
    return _run_route(LIMITS)


@pytest.fixture(scope="module")
def blank():
    return _run_route(None)


@pytest.fixture(scope="module")
def custom_pages(custom):
    _body, rendered = custom
    return {kind: _page_one(doc) for kind, doc in rendered.documents.items()}


class TestPageOneCarriesBothZones:
    """The whole session, read off page 1 of a real route report."""

    @pytest.mark.parametrize("doc", DOCS)
    def test_it_names_the_plants_own_authority(self, custom_pages, doc):
        assert BASIS_PHRASE in custom_pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_it_prints_the_three_numbers(self, custom_pages, doc):
        """Formatted `:g`, which is what every other boundary on the page uses —
        so 5.0 reads "5", not "5.00": a precision the analyst did not type."""
        assert "5 / 8 / 12 mm/s RMS" in custom_pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_it_says_what_iso_would_have_said(self, custom_pages, doc):
        """**D**, and the letter is the point. A custom limit that quietly
        downgrades a Zone D machine to Zone B is the exact failure the operator's
        ruling exists to stop; the page has to carry both answers."""
        assert "would give Zone D" in custom_pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_it_carries_the_gate_sentence(self, custom_pages, doc):
        """SESSION_LIMITS1.md F-2, said out loud: a looser plant limit also
        suppresses 1×-family faults ISO would have committed."""
        assert GATE_SENTENCE in custom_pages[doc]

    @pytest.mark.parametrize("doc", DOCS)
    def test_the_worst_point_is_no_longer_attributed_to_iso(self, custom_pages, doc):
        """LIMITS-1b F-2, closed. The worst point is Compressor DE — an EXTRA
        location — so this line is built from the wire, which now carries
        `zone_basis`. Before this session it read "ISO Zone B — acceptable per
        ISO 20816-3" on the same line as "Judged against machine-specific
        limits"."""
        page = custom_pages[doc]
        assert "per ISO 20816-3: overall" not in page, (
            "page 1 still signs ISO's name to a plant number:\n" + page[:900]
        )


class TestTheWireCarriesTheBasis:

    def test_every_ok_location_states_its_basis(self, custom):
        body, _ = custom
        locations = body.get("locations") or []
        assert locations, "no extra locations on the wire"
        for entry in locations:
            if entry.get("status") != "ok":
                continue
            assert entry.get("zone_basis") == "custom", entry

    def test_and_what_iso_would_have_said(self, custom):
        body, _ = custom
        by_label = {e["label"]: e for e in (body.get("locations") or [])}
        # Motor NDE: 1.80 mm/s — Zone A on 5/8/12, Zone B on ISO's 1.4/2.8/4.5.
        assert by_label["Motor NDE"]["iso_zone"] == "A"
        assert by_label["Motor NDE"]["iso_zone_would_be"] == "B"
        # Compressor DE: 5.20 mm/s — Zone B on the limit, Zone D on ISO.
        assert by_label["Compressor DE"]["iso_zone"] == "B"
        assert by_label["Compressor DE"]["iso_zone_would_be"] == "D"

    def test_the_blank_job_says_iso_and_offers_no_second_opinion(self, blank):
        body, _ = blank
        for entry in (body.get("locations") or []):
            if entry.get("status") != "ok":
                continue
            assert entry.get("zone_basis") == "iso", entry
            assert entry.get("iso_zone_would_be") is None, entry

    def test_the_limits_actually_moved_a_zone(self, custom, blank):
        """Non-vacuity, and the claim an analyst cares about: the same files
        judged two ways give two different answers."""
        c = {e["label"]: e.get("iso_zone") for e in (custom[0].get("locations") or [])}
        b = {e["label"]: e.get("iso_zone") for e in (blank[0].get("locations") or [])}
        assert b["Compressor DE"] == "D" and c["Compressor DE"] == "B", (b, c)
        assert b["Motor NDE"] == "B" and c["Motor NDE"] == "A", (b, c)


class TestTheBlankJobIsYesterdaysDocument:
    """All three blank = ISO, byte-identical to before this session."""

    def test_no_custom_vocabulary_anywhere_in_either_document(self, blank):
        _body, rendered = blank
        for kind, doc in rendered.documents.items():
            assert BASIS_PHRASE not in doc, kind
            assert GATE_SENTENCE not in doc, kind
            assert "would give Zone" not in doc, kind
            assert "Judged against" not in doc, kind

    def test_the_iso_wording_is_still_there(self, blank):
        _body, rendered = blank
        page = _page_one(rendered.documents["markdown"])
        assert "ISO Zone" in page
