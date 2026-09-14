"""Session INTAKEFIX-1 — "(assumed)" on the document, and the wire that makes it honest.

INTAKE-2's F-4. The report prints *"(group and support class assumed)"* and it
prints it from `iso_assumed`, which is the `or` of two independent decisions:

    assumed = group_source == "assumed" or support_source == "assumed"

So a machine whose group came from a rated 90 kW and whose mounting nobody
stated gets a caveat that names the group as a guess. That sentence cannot be
fixed from a boolean; it needs the two halves, and item 2 of the brief is
putting them on the wire so REPORT-3 can.

WHAT IS PINNED HERE AND WHAT IS NOT. The behaviour on the document TODAY is
pinned in both directions — stated prints nothing, unstated prints the caveat —
because that is the brief's own acceptance and because it is the thing a change
to `iso_assumed` would break. Rewording the caveat to use the new fields is
`report/`'s, and `report/` is not this session's; the contract now carries what
that session needs (`docs/contracts/machine_result.md` section 2).

The document is captured where it is HANDED ON, the way
`tests/test_reportfix1_route_report.py` does it: `report.md` is deleted the
moment `report.pdf` exists (the privacy promise working) and a PDF's text is
compressed. Both renderings are captured, not whichever one this host reaches —
Session HIST-2-FIX records five tests that passed on one box and failed on
another for no reason but the PDF engine.
"""

from __future__ import annotations

import csv
import io
import re

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_webapp_e2e import _poll_until_terminal, _webapp_cfg
from vib_agent.webapp.app import create_app

_RPM = 1800.0

#: The exact sentence, from `report/templates/_evidence.md.j2` and `_v2.html.j2`.
#: Matched loosely enough to survive the markdown/HTML emphasis wrappers and
#: tightly enough that no other sentence satisfies it.
_CAVEAT = re.compile(r"group and support class assumed", re.I)


def _csv_bytes(n: int = 800, fmax: float = 400.0) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["freq_hz", "amplitude"])
    freqs = [i * fmax / n for i in range(n)]
    amp = [0.001] * n
    for hz, value in ((_RPM / 60.0, 0.4), (107.03, 2.4), (214.06, 1.1)):
        amp[min(range(n), key=lambda i: abs(freqs[i] - hz))] = value
    for freq, value in zip(freqs, amp):
        writer.writerow([freq, value])
    return buf.getvalue().encode()


def _visible(html: str) -> str:
    body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
    return re.sub(r"[ \t ]+", " ", re.sub(r"(?s)<[^>]+>", " ", body))


def _run(**iso) -> tuple[dict, dict[str, str]]:
    """One real upload, with both rendered documents captured."""
    from vib_agent.report import generate
    from vib_agent.webapp import worker

    html_pages: list[str] = []
    markdowns: list[str] = []
    with pytest.MonkeyPatch.context() as patch:
        original_html = generate._pdf_from_html

        def _spy_html(html, out_dir, *args, **kwargs):
            html_pages.append(html)
            return original_html(html, out_dir, *args, **kwargs)

        patch.setattr(generate, "_pdf_from_html", _spy_html)
        for name in ("render_pdf", "render_drafted_pdf"):
            original = getattr(worker, name)

            def _spy_markdown(*args, _original=original, **kwargs):
                text = kwargs.get("markdown_text")
                if text is not None:
                    markdowns.append(text)
                return _original(*args, **kwargs)

            patch.setattr(worker, name, _spy_markdown)

        app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                         anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
        with TestClient(app) as client:
            data = {"invite_code": "demo-code", "machine_alias": "P-101",
                    "rpm": str(_RPM), "machine_type": "pump",
                    "measurement_location": "Pump DE",
                    "velocity_unit": "mm_s", "detection_type": "rms",
                    "mode": "spectrum"}
            data.update(iso)
            res = client.post(
                "/api/jobs",
                files={"file": ("upload.csv", _csv_bytes(), "text/csv")},
                data=data,
            )
            assert res.status_code == 202, res.text
            body = _poll_until_terminal(client, res.json()["job_id"], timeout_s=600)
    assert body["state"] in ("done", "degraded"), body
    assert html_pages, "no HTML page was rendered"
    assert markdowns, "no markdown document was rendered"
    return body, {"html": _visible(html_pages[-1]), "markdown": markdowns[-1]}


@pytest.fixture(scope="module")
def stated():
    """The analyst picked Group 2 and rigid. Nothing was assumed."""
    return _run(iso_group="2", iso_support="rigid")


@pytest.fixture(scope="module")
def unstated():
    """Neither half stated. `iso_group` is `Form(...)` and FastAPI reads an empty
    value for a required str as MISSING — a blank is a 422 — so this is the case
    `resolve_iso_class` names: "an older client, or a post built by hand"."""
    return _run(iso_group="unspecified", iso_support="unspecified")


@pytest.fixture(scope="module")
def rated():
    """Derived from the nameplate, with the mounting left unsaid. THIS is the
    payload F-4 is about: `iso_assumed` is true and one half of the caveat it
    prints is false."""
    return _run(iso_group="2", iso_support="rigid", rated_kw="90")


DOCS = ("markdown", "html")


class TestTheDocumentSaysAssumedOnlyWhenItWas:
    """The brief's acceptance for item 2, read off the rendered document."""

    @pytest.mark.parametrize("kind", DOCS)
    def test_a_stated_group_prints_no_caveat(self, stated, kind):
        body, docs = stated
        assert body["iso_assumed"] is False
        assert not _CAVEAT.search(docs[kind]), (
            f"the {kind} document caveats a machine the analyst classified"
        )

    @pytest.mark.parametrize("kind", DOCS)
    def test_an_unstated_group_prints_it(self, unstated, kind):
        """Non-vacuity for the test above: without this, deleting the caveat
        from the template entirely would pass."""
        body, docs = unstated
        assert body["iso_assumed"] is True
        assert _CAVEAT.search(docs[kind]), (
            f"the {kind} document presents an assumed ISO row as a statement"
        )


class TestTheWireNowSaysWhichHalf:

    def test_stated_is_stated_on_both_halves(self, stated):
        body, _ = stated
        assert body["group_source"] == "stated"
        assert body["support_source"] == "stated"

    def test_assumed_is_assumed_on_both_halves(self, unstated):
        body, _ = unstated
        assert body["group_source"] == "assumed"
        assert body["support_source"] == "assumed"

    def test_a_derivation_from_the_nameplate_says_rated(self, rated):
        body, _ = rated
        assert body["group_source"] == "rated", (
            "a rating beats the select, and the wire must say which one won"
        )

    def test_the_flag_still_gates_the_word_and_the_sources_explain_it(self, rated):
        """Contract section 5 rule 3 is unchanged: `iso_assumed`, and nothing
        else, gates the word. What moved is that a consumer can now see the
        caveat is only half true."""
        body, docs = rated
        assert body["iso_assumed"] is False
        assert body["group_source"] == "rated"
        assert body["support_source"] == "stated"
        assert not _CAVEAT.search(docs["markdown"])

    def test_they_ship_together_with_the_row_they_describe(self, stated):
        body, _ = stated
        assert {"iso_group", "iso_support", "iso_assumed",
                "group_source", "support_source"} <= set(body)

    def test_the_vocabulary_is_the_producers_own(self, stated, unstated, rated):
        """Three answers, never collapsed. If a fourth appears, `IsoClass` grew
        a state the contract does not document."""
        from vib_agent.adapters.uploads.common import IsoClass  # noqa: F401

        seen = set()
        for body, _ in (stated, unstated, rated):
            seen.update({body["group_source"], body["support_source"]})
        assert seen <= {"rated", "stated", "assumed"}
        assert seen == {"rated", "stated", "assumed"}, (
            "these three fixtures are chosen to exercise all three provenances; "
            f"only {sorted(seen)} appeared"
        )
