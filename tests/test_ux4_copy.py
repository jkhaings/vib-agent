"""Session UX-4, slice 4 -- the copy a stranger reads, and the two document
pages that stopped being `cat` output.

Four claims:

  * U7 · `/validation` and `/field-validation-results.md` are FORMATTED
    PAGES. They were `<pre class="doc">`, and the stranger's note is the one
    worth keeping in mind: "The content is your best sales asset; right now
    it looks like `cat` output pasted into a `<pre>`."
  * U13 · FILE PATHS LIVE ON `/validation` AND NOWHERE ELSE. Six
    `pdm_core/*.py` names were rendered into `/how-it-works` as marketing
    copy.
  * B6 (RULED) · `/demo` stops naming a control the wizard does not show.
    This is the one place the site told a stranger exactly what to click and
    the click was not there.
  * The empty state is one line.

The renderer is tested against BOTH real documents, because they are not the
same kind of document -- see `webapp/mdpage.py` for the measurement that
decided its design -- and against adversarial input, because a document page
is HTML built from a file and that is the shape an injection takes.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.webapp.app import create_app
from vib_agent.webapp.mdpage import render_doc

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"
_OUTPUTS = _ROOT / "outputs"

#: Anything that reads as a source path to a stranger.
_PATHISH = re.compile(
    r"\b(?:pdm_core|adapters|report|agent|eval|scripts|tests|config|outputs|marketing)"
    r"/[A-Za-z0-9_./-]+\.(?:py|json|md|j2|js)\b"
)


def _app():
    return create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "engineer-1"},
                      contact_email="ops@example.test")


def _flat(html: str) -> str:
    """Collapse runs of whitespace so a phrase that WRAPS in the template
    source is still found. Borrowed from `tests/test_pages_surface.py`, which
    added it after SESSION_PAGES F-2 found three pins scanning for a form of a
    string the page never contains."""
    return re.sub(r"\s+", " ", html)


def _served() -> dict[str, str]:
    pages = ("/validation", "/field-validation-results.md")
    with TestClient(_app()) as c:
        return {p: c.get(p).text for p in pages}


# ══════════════════════════════════════════════════════════════════════════
# 1 · U7 -- the document pages
# ══════════════════════════════════════════════════════════════════════════


class TestTheDocumentPages:

    def test_neither_is_a_monospace_dump_any_more(self):
        served = _served()
        for path in ("/validation", "/field-validation-results.md"):
            html = served[path]
            assert html.strip(), f"{path} served nothing -- this pin would be vacuous"
            assert '<pre class="doc">' not in html, path
            assert "<h2>" in html or "<h3>" in html, f"{path} has no headings"

    def test_the_pipe_table_is_a_table(self):
        """The stranger's exact words: 'a Markdown pipe table rendered as text
        and cut off on the right'. It is now a real table, in slice 1's one
        scroll container, so a wide one scrolls in its own box."""
        html = _served()["/field-validation-results.md"]
        assert '<table class="mtable">' in html
        assert '<div class="tw">' in html
        assert "<thead><tr><th" in html
        assert html.count("<td>") > 20, "the table body is empty"

    def test_the_aligned_stat_lines_became_a_real_two_column_row(self):
        """`validation_summary.md` aligns these with spaces, which works only
        in a monospace column. Reflowing them as prose would have destroyed
        the one thing they are for."""
        html = _served()["/validation"]
        assert '<dl class="docstat">' in html
        assert "<dt>Committed inner/outer-race diagnosis correct</dt>" in html
        assert "<dd>20 / 24</dd>" in html

    def test_every_section_title_in_the_source_is_a_heading(self):
        """Counted rather than sampled. A first draft of the renderer required
        a title to be all-caps and silently demoted four of the five to a
        horizontal rule -- every one of them carries a proper noun."""
        src = (_OUTPUTS / "validation_summary.md").read_text()
        rules = sum(1 for ln in src.split("\n") if re.match(r"^\s*[-=]{4,}\s*$", ln))
        assert rules == 5, "the source changed; re-check this expectation"
        assert _served()["/validation"].count("<h2>") == rules

    def test_hard_wrapped_prose_is_rejoined(self):
        """The half of U7 that matters most: the source is wrapped at about 90
        characters for a terminal, and a page that keeps those breaks reads
        like a terminal."""
        html = _served()["/validation"]
        assert ("<p>Hand-authored and regeneration-safe. eval/runner.py rewrites "
                "cwru_results.md and field_validation_results.md wholesale, so the "
                "consolidated status lives here instead. Every number below is "
                "measured, and every miss is stated as a miss.</p>") in html

    def test_hard_wrapped_bullets_stay_one_item_and_one_list(self):
        """The defect this had no pin for until a negative control found the
        hole: every list item in `validation_summary.md` is hard-wrapped with
        a deeper-indented continuation line, and a renderer that treats that
        line as a new paragraph produces ELEVEN single-item lists with the
        second half of each sentence stranded between them.

        Checked as a shape (items outnumber lists) and as a sentence (one
        item carries text from both of its source lines), because either one
        alone can pass on a broken render.
        """
        html = _served()["/validation"]
        lists, items = html.count('<ul class="doclist">'), html.count("<li>")
        assert items > lists, f"{items} items in {lists} lists -- one each"
        assert lists == 4 and items == 11, (lists, items)
        assert ("its BPFO peak stands at only 3.5-8.2x the spectrum mean, below the "
                "amplitude floor a committed call requires") in html, (
            "a bullet's continuation line was stranded outside its <li>"
        )

    def test_a_wrapped_stat_value_stays_in_its_row(self):
        """A stat's VALUE wraps too, and its continuation is indented to the
        value column -- far deeper than the stat line itself. Found by reading
        the rendered page rather than by a test: the row showed the figure
        "0 (the last one cleared by the" with "amplitude floor)" stranded in a
        paragraph underneath it."""
        html = _served()["/validation"]
        assert "<dd>0     (the last one cleared by the amplitude floor)</dd>" in html
        assert ("<dd>NOT EVALUABLE -- the Welford baseline never armed (weight peaked "
                "at 29.74 against a 30-reading minimum).</dd>") in html
        # Non-vacuity: every row the source declares is a row on the page.
        assert html.count("<dt>") == 11

    def test_a_stat_value_that_is_a_sentence_can_wrap(self):
        """375px is a must-not-break constraint, and this broke it. Eleven of
        the stat values are figures ("20 / 24") and three are prose
        ("NOT EVALUABLE -- the Welford baseline never armed"); CSS cannot tell
        them apart, so `white-space:nowrap` held a sentence on one line and
        made the row 441px wide in a 375px viewport. It was the only thing on
        the whole site scrolling sideways, and the capture is what found it.
        """
        css = (_STATIC / "style.css").read_text()
        rule = re.search(r"\.docstat dd\{([^}]*)\}", css)
        assert rule, ".docstat dd is gone"
        assert "nowrap" not in rule.group(1)
        row = re.search(r"\.docstat > div\{([^}]*)\}", css)
        assert row and "minmax(0,1fr) minmax(0,max-content)" in row.group(1), (
            "`1fr auto` lets a long value blow the grid past the viewport"
        )

    def test_a_line_that_ended_is_not_glued_to_the_next(self):
        """The counterpart. Without a short-line rule the document's title and
        its date become one sentence."""
        html = _served()["/validation"]
        assert "<p>VALIDATION SUMMARY — vib-agent</p>" in html
        assert "vib-agent Last updated" not in html


class TestTheRendererIsSafe:

    def test_it_escapes_before_it_marks_up(self):
        out = render_doc("A <script>alert(1)</script> line")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_a_handler_attribute_cannot_survive(self):
        out = render_doc('Text <b onclick="alert(1)">x</b> more')
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", out)

    def test_link_targets_are_allowlisted_not_blocklisted(self):
        """An allowlist has to anticipate no scheme; a `javascript:` blocklist
        has to anticipate every one of them."""
        assert '<a href="/validation">ok</a>' in render_doc("[ok](/validation)")
        assert '<a href="https://example.org">ok</a>' in render_doc("[ok](https://example.org)")
        for bad in ("javascript:alert(1)", "data:text/html,x", "vbscript:x", "//evil.test"):
            out = render_doc(f"[click]({bad})")
            assert "<a " not in out, bad
            assert "click" in out, "the sentence lost its words as well as its link"

    def test_a_table_cell_cannot_break_out_of_its_cell(self):
        out = render_doc("| a | b |\n|---|---|\n| <img src=x onerror=y> | 2 |\n")
        assert "<img" not in out
        assert not re.search(r"<[^>]*\son[a-z]+\s*=", out)

    def test_the_two_document_routes_carry_no_inline_script(self):
        """`tests/test_hardening.py` sweeps this for every page; restated here
        because this session is the one that made these two GENERATED."""
        for path, html in _served().items():
            assert not re.search(r"<[^>]*\son[a-z]+\s*=\s*[\"'][^\"']*[\"']", html), path
            assert "javascript:" not in html.lower(), path
            for tag in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S | re.I):
                assert not tag.group(1).strip(), path


# ══════════════════════════════════════════════════════════════════════════
# 2 · U13 -- file paths live on /validation and nowhere else
# ══════════════════════════════════════════════════════════════════════════


class TestTheEmptyState:

    def test_it_is_one_line(self):
        js = (_STATIC / "app.js").read_text()
        fn = js.partition("function machinesEmpty() {")[2].partition("\n}")[0]
        # TWO returns: the `db` backend's first, then the browser one. Taking
        # the first is how a first draft of this test read the wrong branch
        # and reported the wrong string.
        returns = fn.split("return `")
        assert len(returns) == 3, f"{len(returns) - 1} returns in machinesEmpty"
        browser = returns[2].partition("`;")[0]
        assert browser, "the browser-mode empty state is gone"
        flat = " ".join(browser.split())
        text = re.sub(r"<[^>]+>", "", flat).replace("\\u2014", "—").strip()
        assert len(text) < 130, f"{len(text)} chars: {text}"
        assert "No machines yet" in text
        assert "Remember this machine" not in text, (
            "the empty state is describing a checkbox on another screen again"
        )

    def test_the_two_actions_are_in_the_header_where_they_belong(self):
        """Where the instruction went. The panel says what to do in one line;
        the doing is two labelled controls above it."""
        html = (_STATIC / "index.html").read_text()
        head = re.search(r'<div class="mhead-actions">[\s\S]*?</div>', html).group(0)
        assert 'href="/#/m/new"' in head and 'href="/#/new"' in head
