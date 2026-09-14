"""Session UX-4, slice 2 -- the shell: one nav, the home order, the machines
header, the step rail, and a button that says where it goes.

OPERATOR AMENDMENT, binding from this slice on: **this is a web application
first.** 1280 and 1440 are the design target; 375px is a must-not-break
constraint, not the layout to design for. Where the two conflict the desktop
one wins. Several pins below therefore assert that a phone rule CANNOT reach
the desktop -- the compact step rail is the clearest case, and it is the kind
of leak that is invisible in a source diff.

The claims:

  * ONE NAV, in six sources (C1). Not three, which is what `style.css` said,
    and not five: `static/billing.html` carries one too. The old pin swept
    four PAGES drawn from three of the six, which is exactly how
    `marketing/_shell.html` diverged for a whole session without a red test.
  * THE HOME PAGE LEADS WITH THE PITCH (C2), and then with the product --
    not with an empty grey dashboard for something the reader has not read
    about yet.
  * THE MACHINES HEADER IS ONE ROW with both actions (the operator's
    screenshot), and the two duplicate copies of one of them are gone.
  * A BUTTON SAYS WHERE IT GOES (C12).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"
_MARKETING = _ROOT / "src" / "vib_agent" / "webapp" / "marketing"

_BANNER = "UX-4 LAYER"
_SLICE2 = "UX-4 LAYER · slice 2"


def _css() -> str:
    return (_STATIC / "style.css").read_text()


def _layer() -> str:
    head, sep, tail = _css().partition(_BANNER)
    assert sep, "the UX-4 stylesheet layer is missing"
    return tail


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


#: Every slice banner in the UX-4 layer, so a section can be bounded by the
#: NEXT one. A first draft sliced to end-of-file, which meant slice 2's
#: "no phone rule reaches the desktop" pin was reading slice 4's rules and
#: failing on `.tw{overflow-x:auto}` -- a rule in a different section, for a
#: different job, that has no media query because it needs none.
_BANNERS = ("UX-4 LAYER · slice 2", "UX-4 LAYER · slice 3a",
            "UX-4 LAYER · slice 3b", "UX-4 LAYER · slice 4")


def _slice2() -> str:
    """The slice-2 rules, comment-free, and NOTHING from a later slice.

    Partitioning on the banner text lands INSIDE the banner comment, so the
    rest of that comment survives `_strip_comments` (which never sees its
    opening `/*`) and every `in` check below would match banner prose rather
    than a rule. Cut to the end of the banner first. This bit once already.
    """
    tail = _css().partition(_SLICE2)[2]
    body = tail.partition("*/")[2]
    for later in _BANNERS[1:]:
        body = body.partition(later)[0]
    return _strip_comments(body)


def _phone_block() -> str:
    """Everything inside the slice-2 `@media (max-width:600px)` block.

    Sliced by brace balance rather than by regex, so a nested rule cannot
    fall out of the block and be checked as if it were unconditional.
    """
    layer = _slice2()
    start = layer.index("@media (max-width:600px){")
    i = layer.index("{", start)
    depth, j = 0, i
    while j < len(layer):
        if layer[j] == "{":
            depth += 1
        elif layer[j] == "}":
            depth -= 1
            if depth == 0:
                return layer[i + 1:j]
        j += 1
    raise AssertionError("unbalanced @media block")


def _desktop_rules() -> str:
    """The slice-2 rules that apply at 1280/1440: the layer minus every
    `@media` block in it. This is the half the amendment made primary."""
    layer = _slice2()
    out, i = [], 0
    while i < len(layer):
        at = layer.find("@media", i)
        if at == -1:
            out.append(layer[i:])
            break
        out.append(layer[i:at])
        j = layer.index("{", at)
        depth = 0
        while j < len(layer):
            if layer[j] == "{":
                depth += 1
            elif layer[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1
    return "".join(out)


# ══════════════════════════════════════════════════════════════════════════
# 1 · C1 -- one nav, and it is six files
# ══════════════════════════════════════════════════════════════════════════


class TestTheNavIsOneThingInSixFiles:

    #: Kept in step with tests/test_ux1_views.py, which owns the served-page
    #: half. Declared here too rather than imported: two files that must
    #: agree and derive from each other agree vacuously.
    EXPECTED = ("/#/", "/validation", "/privacy")

    SOURCES = (
        "src/vib_agent/webapp/static/index.html",
        "src/vib_agent/webapp/static/privacy.html",
        "src/vib_agent/webapp/app.py",
    )

    def test_the_two_pins_agree_about_the_nav(self):
        """Non-vacuity for the pair: if `test_ux1_views.py` changes EXPECTED
        without this file, one of them is wrong and the failure says which."""
        other = (_ROOT / "tests" / "test_ux1_views.py").read_text()
        declared = re.search(r"EXPECTED = \(([^)]*)\)", other)
        assert declared
        found = tuple(re.findall(r'"([^"]+)"', declared.group(1)))
        assert found == self.EXPECTED, found

    def test_every_source_carries_it(self):
        for rel in self.SOURCES:
            text = (_ROOT / rel).read_text()
            navs = re.findall(r"<nav>[\s\S]*?</nav>", text)
            assert len(navs) == 1, f"{rel}: {len(navs)} <nav> blocks"
            assert tuple(re.findall(r'href="([^"]+)"', navs[0])) == self.EXPECTED, rel

    def test_the_stylesheet_no_longer_says_three(self):
        css = _css()
        assert "duplicated in three places" not in css
        assert "duplicated in SIX places" in css

    def test_index_keeps_its_account_slot(self):
        """`{{account_nav}}` renders "Sign in" on STORE_BACKEND=db and is
        dropped WITH ITS WHOLE LINE on browser. Rewriting the nav must not
        have taken it."""
        nav = re.search(r"<nav>[\s\S]*?</nav>", _index()).group(0)
        assert "{{account_nav}}" in nav

    def test_the_nav_is_one_scrollable_row_on_a_phone_and_untouched_above(self):
        """U4. The remedy is the second of the two the finding itself names
        ("collapse to a menu OR a scrollable row"). What matters for the
        amendment is the other half: none of it reaches the desktop."""
        phone = _phone_block()
        assert re.search(r"nav\{[^}]*overflow-x\s*:\s*auto", phone)
        assert re.search(r"nav\{[^}]*flex-wrap\s*:\s*nowrap", phone)
        desktop = _desktop_rules()
        assert not re.search(r"(^|[;}\s])nav\{[^}]*overflow-x", desktop), (
            "the phone nav rule leaked out of its media block and now applies "
            "at 1280/1440, which is the design target"
        )
        assert not re.search(r"(^|[;}\s])nav\{[^}]*flex-wrap", desktop), (
            "the phone nav wrap rule leaked to the desktop"
        )


# ══════════════════════════════════════════════════════════════════════════
# 1b · SESSION_LEGAL1 §7.2 -- the footer, which is the SEVENTH duplication
# ══════════════════════════════════════════════════════════════════════════


class TestTheFooterIsOneThingToo:
    """LEGAL-1 §7 item 2, verbatim: *"A seventh duplication, not covered by the
    nav pin."*

    The nav pin above sweeps `<nav>` and stops there, so the legal strip in the
    footer -- Validation record · Privacy · Terms -- drifted with nothing
    watching it. `/terms` shipped in LEGAL-1 with no footer link anywhere and
    no test that could notice, which is the same mechanism UX-1 F-1 found one
    element up. Asserted as the SET, in order, for the same reason the nav is:
    a test that only checked `/terms` had been added would close the instance
    and leave the mechanism.

    Eight sources, not seven: `app.py` spells the footer in `_footer()` as well
    as the nav in `_MASTHEAD`, and `static/terms.html` carries both.
    """

    #: The legal strip, in order, as every footer must render it.
    EXPECTED = ("/validation", "/privacy")

    SOURCES = TestTheNavIsOneThingInSixFiles.SOURCES

    def test_every_source_spells_the_same_footer(self):
        for rel in self.SOURCES:
            text = (_ROOT / rel).read_text()
            strip = re.search(
                r'<span><a href="/validation">Validation record</a>[\s\S]*?</span>', text)
            assert strip, f"{rel}: no footer legal strip at all"
            found = tuple(re.findall(r'href="([^"]+)"', strip.group(0)))
            assert found == self.EXPECTED, f"{rel}: {found}"

    def test_the_footer_still_says_what_a_report_is(self):
        """The sentence the links sit beside is the accountability claim, and
        adding a link is exactly the edit that drops it."""
        for rel in self.SOURCES:
            text = (_ROOT / rel).read_text()
            assert "Reports are drafts pending review by a qualified analyst." in text, rel

class TestTheHomeOrder:

    def test_the_markup_order_is_unchanged(self):
        """The order moved to CSS precisely so this stayed true: `#state` is
        still the last section, and the view containers are still real markup
        above the hero so the node harness can prove them hidden."""
        sections = re.findall(r'^  <section[^>]*id="([\w-]+)"', _index(), re.M)
        assert sections[-1] == "state", sections
        assert sections.index("view-machines") < sections.index("hero")

    def test_the_landing_leads_and_the_product_follows_it(self):
        desktop = _desktop_rules()
        assert re.search(r"main\.wrap\{[^}]*flex-direction\s*:\s*column", desktop)
        assert re.search(r"#hero\{order:1\}", desktop)
        assert re.search(r'#view-machines\[data-first="landing"\]\{order:2\}', desktop)
        assert re.search(r"#how,#proof,#showcase,#start\{order:3\}", desktop)

    def test_the_client_sets_the_attribute_on_every_branch(self):
        """`renderMachines` has exactly two outcomes and both must order the
        page: the resolved list, and the catch. The catch is the one that is
        easy to forget -- a visitor who is not signed in gets a 401 there, and
        they are exactly the reader the pitch is written for.

        Counted as OCCURRENCES, not lines. `grep -c` counts lines and is how
        SESSION_PAGES F-6 got the brand count wrong twice before it was right.
        """
        js = _app_js()
        assert js.count("function orderHome(landingFirst)") == 1
        body = js.partition("function orderHome(landingFirst) {")[2].partition("\n}")[0]
        calls = js.count("orderHome(") - 1 - body.count("orderHome(")
        assert calls == 2, f"{calls} call sites; renderMachines has two outcomes"
        for branch in ("orderHome(empty);", "orderHome(true);"):
            assert branch in js, branch

    def test_it_is_an_attribute_and_not_a_class(self):
        """`tests/js/harness.js` records setAttribute and stubs classList to
        no-ops, so a class here would be true of the page and invisible to
        every node suite -- the UX-2 F-1 / UX-3 F-12 trap."""
        fn = _app_js().partition("function orderHome(landingFirst) {")[2].partition("\n}")[0]
        assert "setAttribute" in fn
        assert "classList" not in fn


# ══════════════════════════════════════════════════════════════════════════
# 3 · The machines header (the operator's screenshot)
# ══════════════════════════════════════════════════════════════════════════


class TestTheMachinesHeader:

    def test_one_row_title_left_both_actions_right(self):
        head = re.search(r'<div class="mhead">[\s\S]*?</div>\s*</div>', _index())
        assert head, "the machines header is gone"
        block = head.group(0)
        assert 'id="machines-head"' in block
        assert 'href="/#/m/new"' in block, "Add a machine is not in the header"
        assert 'href="/#/new"' in block, "New reading is not in the header"
        assert '<div class="mhead-actions">' in block
        assert block.index("machines-head") < block.index("mhead-actions"), "title is not first"

    def test_the_actions_are_the_same_height(self):
        """The operator's words. Both take the shared button base from slice
        1, and `.mhead` centres rather than baselines them -- baseline lines a
        44px button up by the text inside it, which is why a heading and a
        button never sat level."""
        desktop = _desktop_rules()
        assert re.search(r"\.mhead\{[^}]*align-items\s*:\s*center", desktop)
        head = re.search(r'<div class="mhead-actions">[\s\S]*?</div>', _index()).group(0)
        classes = re.findall(r'<a class="([^"]+)"', head)
        assert classes == ["btn-ghost", "cta"], classes

    def test_the_two_duplicates_are_gone_from_the_client(self):
        """It was rendered into #machines-body twice: once under the card
        list, once inside the empty state."""
        js = _app_js()
        assert '<div class="ready-actions"><a class="cta" href="/#/m/new">' not in js
        assert '<div class="ready-actions"><a class="btn-ghost" href="/#/m/new">' not in js

    def test_home_still_offers_a_way_to_add_a_machine(self):
        """The claim `tests/js/machine_store_tests.js` used to make from
        inside #machines-body, kept at the source now that the control lives
        in static markup the node harness cannot read."""
        assert _index().count('href="/#/m/new"') >= 1


# ══════════════════════════════════════════════════════════════════════════
# 4 · U5 -- the step rail, and the amendment's clearest test
# ══════════════════════════════════════════════════════════════════════════


class TestTheStepRail:

    def test_the_desktop_rail_is_untouched(self):
        """THE amendment pin. The compact form is a phone constraint fix; the
        four-step rail is the design target. A rule that escaped the media
        block would replace it at 1280 and nothing else here would notice."""
        desktop = _desktop_rules()
        assert "rail" not in desktop, (
            "a step-rail rule is applying at the desktop widths this product "
            "is designed for"
        )
        html = _index()
        for i, word in enumerate(("Machine", "Measurement", "Details", "Review"), start=1):
            assert f'id="rail-{i}"><b>{i}</b> {word}' in html, word

    def test_the_compact_form_is_built_from_the_attributes_the_client_sets(self):
        phone = _phone_block()
        assert 'content:"Step " attr(data-step) " of " attr(data-of) " — "' in phone
        assert re.search(r"\.rail\.steps \.s\{display:none\}", phone)
        assert re.search(r"\.rail\.steps \.s\.now\{display:inline", phone)
        assert re.search(r"\.rail\.steps \.s\.now b\{display:none\}", phone), (
            "without this the compact rail reads 'Step 1 of 4 - 1 Machine'"
        )

    def test_render_rail_publishes_both_numbers(self):
        fn = _app_js().partition("function renderRail(step, max) {")[2].partition("\n}")[0]
        assert "'data-step'" in fn and "'data-of'" in fn
        assert "STEP_COUNT" in fn, "the total is retyped rather than read from the constant"


# ══════════════════════════════════════════════════════════════════════════
# 5 · C12
# ══════════════════════════════════════════════════════════════════════════


class TestTheSecondLandingButton:

    def test_it_says_what_it_does(self):
        """It linked to /#/new and was labelled "Enter an invite code" -- the
        invite field is on step 4 of that wizard, so the label named neither
        the action nor the destination."""
        html = _index()
        assert "Enter an invite code" not in html
        assert '<a class="btn-ghost" href="/#/new">Upload your own file</a>' in html

    def test_the_invite_fact_survives_where_it_was_already_true(self):
        assert "You need only an invite code." in _index()


# ══════════════════════════════════════════════════════════════════════════
# 6 · The node half
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_shell_in_a_real_js_runtime():
    """Runs tests/js/shell_tests.js against the shipped app.js."""
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "shell_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = map(int, match.groups())
    assert passed == total and total >= 8, result.stdout
