"""Session UX-4, slice 1 -- one visual system, at the source.

Four claims, each one an item a real browser measured in
`outputs/STRANGER_TEST_2026-09-07.md` (Claude in Chrome, Fable 5.1, driven
against a live 127.0.0.1:8000 -- which is why its findings carry pixel
numbers rather than readings of the source):

  * ONE BUTTON SYSTEM (U10). Six selectors carry a button and none is
    renamed -- app.js is written against these names and `readyCard`'s
    source is pinned byte-for-byte elsewhere. What is unified is what a
    reader sees: one family, one size, one height, one radius. The
    monospace half of U10 lives in `marketing.css` and is checked there,
    because that file is linked AFTER `style.css` and wins at equal
    specificity wherever the rule in `style.css` sits.
  * ONE TABLE TREATMENT (U2, U3). `overflow-wrap:anywhere` broke "CWRU
    bearings" into "CWR / U / beari / ngs" and "0.01414" into "0.01 / 414".
    It is gone from the file that could out-cascade the fix, and the one
    table component scrolls under 600px instead of wrapping.
  * FOCUS IS VISIBLE FOR THE KEYBOARD AND INVISIBLE FOR THE ROUTER (U6).
    `focusHeading()` rings an `h2[tabindex="-1"]` on arrival. The ring is
    removed from that ONE case via `:focus-visible` -- not switched off.
  * THREE.JS IS GONE (U11), and gone means the injector, the vendored
    build, the LICENSE, the dead CSS and the comment that described it.

The layer discipline is the fifth claim and the one that protects the
others: `tests/js/harness.js` parses `style.css` to decide whether a
`hidden` element is really off screen, so a bare single-class rule that
declares a `display` without its `[hidden]` guard makes that oracle lie.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"
_MARKETING = _ROOT / "src" / "vib_agent" / "webapp" / "marketing"

_BANNER = "UX-4 LAYER"


def _css() -> str:
    return (_STATIC / "style.css").read_text()


def _layer() -> str:
    """Everything below this session's banner, and nothing above it."""
    head, sep, tail = _css().partition(_BANNER)
    assert sep, "the UX-4 stylesheet layer is missing"
    return tail


def _marketing_css() -> str:
    return (_MARKETING / "assets" / "marketing.css").read_text()


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _app_py() -> str:
    return (_ROOT / "src" / "vib_agent" / "webapp" / "app.py").read_text()


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


# ══════════════════════════════════════════════════════════════════════════
# 1 · The layer plays by the house rules
# ══════════════════════════════════════════════════════════════════════════


class TestTheLayer:

    def test_it_exists_and_is_last(self):
        css = _css()
        for earlier in ("FACELIFT LAYER", "MOBILE-POLISH LAYER", "UX-1 LAYER", "UX-2 LAYER"):
            assert css.index(earlier) < css.index(_BANNER), earlier

    def test_every_bare_display_rule_carries_its_hidden_guard(self):
        """The rule every layer since UX-1 has stated -- but checked the way
        `tests/js/harness.js` actually parses, which is NOT what the UX-1 and
        UX-2 copies of this test check.

        Those two match `^\\.cls{...display:...}` at column 0, so a GROUPED
        selector (`.cta,.btn-ghost{display:inline-flex}`) slips past them.
        The harness does not: `loadMarkup` splits each selector on commas and
        registers every bare class it finds. A grouped `display` was
        therefore invisible to the pin and visible to the oracle -- exactly
        the drift the guard exists to prevent. This copy mirrors the harness.
        """
        layer = _strip_comments(_layer())
        declares: set[str] = set()
        guarded: set[str] = set()
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", layer):
            for part in selectors.split(","):
                sel = part.strip()
                bare = re.fullmatch(r"\.([A-Za-z][\w-]*)", sel)
                if bare and re.search(r"(^|[;\s])display\s*:", body):
                    declares.add(bare.group(1))
                guard = re.fullmatch(r"\.([A-Za-z][\w-]*)\[hidden\]", sel)
                if guard and "display:none" in body.replace(" ", ""):
                    guarded.add(guard.group(1))
        assert declares <= guarded, sorted(declares - guarded)

    def test_the_layer_sets_no_raw_font_size(self):
        """The scale is only a scale if the layer that declares it uses it.

        Deliberately scoped to this layer and said so in the banner: 26
        distinct sizes were in use above it and most still are. Claiming
        otherwise would be the kind of sentence SESSION_PAGES F-12 was
        written about.
        """
        layer = _strip_comments(_layer())
        # The token declarations themselves are the one legal source of a px
        # type size, so cut the :root block before looking.
        body = re.sub(r":root\{[^}]*\}", "", layer)
        raw = re.findall(r"font-size\s*:\s*\d", body)
        raw += re.findall(r"font\s*:\s*[^;}]*?\b\d+(?:\.\d+)?px", body)
        # `.sig-lb` is an SVG label whose size is geometry inside a fixed
        # 880x64 viewBox, not page type -- it does not scale with the reader.
        assert len(raw) == 1, f"raw px type sizes in the UX-4 layer: {raw}"

    def test_the_scale_tokens_are_declared(self):
        layer = _layer()
        for token in ("--t-xs", "--t-sm", "--t-base", "--t-md", "--t-lg",
                      "--t-xl", "--t-2xl", "--s-1", "--s-6", "--ctl-h"):
            assert f"{token}:" in layer, token


# ══════════════════════════════════════════════════════════════════════════
# 2 · U10 -- one button system
# ══════════════════════════════════════════════════════════════════════════


class TestOneButtonSystem:

    #: Every selector that carries a button, in both stylesheets.
    BUTTONS = (".cta", ".btn-ghost", ".btn-mini", ".btn-link", ".dl")

    def test_the_shared_base_sets_one_family_one_height_one_radius(self):
        layer = _layer()
        base = re.search(
            r"\.cta,\.btn-ghost,\.btn-mini,\.dl\{([^}]*)\}", _strip_comments(layer)
        )
        assert base, "the shared button base is gone"
        body = base.group(1)
        assert "IBM Plex Sans" in body
        assert "var(--ctl-h)" in body
        assert "var(--r)" in body
        assert "var(--t-base)" in body

    def test_no_button_in_the_layer_is_monospace(self):
        """U10's own words: the two report links were monospace and the
        buttons above and below them were sans-serif."""
        layer = _strip_comments(_layer())
        for rule in re.findall(r"([^{}]+)\{([^{}]*)\}", layer):
            selectors, body = rule
            if any(b in selectors for b in self.BUTTONS) and "Mono" in body:
                raise AssertionError(f"a button rule names a mono family: {selectors.strip()}")




# ══════════════════════════════════════════════════════════════════════════
# 3 · U2 / U3 -- one table treatment
# ══════════════════════════════════════════════════════════════════════════


class TestOneTableTreatment:


    def test_headers_and_numbers_do_not_wrap(self):
        layer = _strip_comments(_layer())
        assert re.search(r"\.mtable th,\.mtable td\.num,\.mtable td\.zone\{[^}]*nowrap", layer)
        assert re.search(r"\.mtable th,\.mtable td\{[^}]*overflow-wrap\s*:\s*normal", layer)

    def test_the_table_scrolls_under_600px(self):
        layer = _strip_comments(_layer())
        phone = re.search(r"@media \(max-width:600px\)\{([\s\S]*)", layer)
        assert phone, "the 600px block is missing"
        assert re.search(r"\.mtable\{[^}]*overflow-x\s*:\s*auto", phone.group(1))



# ══════════════════════════════════════════════════════════════════════════
# 4 · U6 -- focus
# ══════════════════════════════════════════════════════════════════════════


class TestFocus:

    def test_the_router_ring_is_removed_and_the_keyboard_ring_is_not(self):
        layer = _strip_comments(_layer())
        assert re.search(
            r'\[tabindex="-1"\]:focus:not\(:focus-visible\)\{\s*outline\s*:\s*none', layer
        ), "U6's fix is missing"
        assert re.search(r"(?<!not\()\:focus-visible\{[^}]*outline\s*:\s*2px", layer), (
            "the keyboard ring was removed along with the router ring"
        )

    def test_the_headings_the_router_focuses_still_carry_tabindex(self):
        """The fix must not have been made by deleting the focus target --
        that would take the keyboard's place in the document with it."""
        html = _index()
        for element_id in ("machines-head", "machine-head", "machine-edit-head", "new-head"):
            assert re.search(rf'id="{element_id}" tabindex="-1"', html), element_id
        assert "focusHeading" in _app_js()


# ══════════════════════════════════════════════════════════════════════════
# 5 · U4 -- the signature strip's labels
# ══════════════════════════════════════════════════════════════════════════


class TestTheSignatureStrip:


    def test_the_labels_are_hidden_under_600px(self):
        layer = _strip_comments(_layer())
        phone = re.search(r"@media \(max-width:600px\)\{([\s\S]*)", layer)
        assert phone and re.search(r"\.sig-lb\{display:none\}", phone.group(1))

    def test_the_hardcoded_hexes_became_tokens(self):
        layer = _strip_comments(_layer())
        assert "--sig:#C9D2CD" in layer.replace(" ", ""), "the strip's trace is still a raw hex"
        assert re.search(r"\.spectrum polyline\{stroke:var\(--sig\)\}", layer)
        assert re.search(r"\.spectrum line\{stroke:var\(--line\)\}", layer)


# ══════════════════════════════════════════════════════════════════════════
# 6 · U11 -- three.js, and gone means gone
# ══════════════════════════════════════════════════════════════════════════


class TestThreeJsIsGone:

    def test_the_vendored_build_and_its_licence_are_deleted(self):
        assets = _STATIC / "assets"
        names = sorted(p.name for p in assets.iterdir())
        assert names == ["report-p01.png", "report-p08.png", "report-p22.png"], names

    def test_the_client_neither_injects_a_script_nor_reaches_for_the_library(self):
        js = _app_js()
        assert "heroDepth" not in js.replace("`heroDepth`", "")
        assert "window.THREE" not in js
        assert "createElement('script')" not in js and 'createElement("script")' not in js
        # A quoted /assets/ path is the only way this file can load one. The
        # prose above the landing block names the deleted path unquoted, on
        # purpose -- a reader should be able to find out what was removed.
        assert "'/assets/" not in js and '"/assets/' not in js

    def test_the_page_still_has_the_figure_the_3d_layer_sat_behind(self):
        """Non-vacuity, and the whole argument for the deletion: the thing an
        analyst reads was always the inline SVG."""
        html = _index()
        assert 'class="hv-svg"' in html
        assert 'aria-label="Velocity spectrum of the bundled example file' in html
        for label in ("1&#215; 30.0 Hz", "BPFO 107.0 Hz", "2&#215;BPFO 214.0 Hz",
                      "3&#215;BPFO 321.0 Hz"):
            assert label in html, label

    def test_the_dead_crossfade_css_went_with_it(self):
        css = _strip_comments(_css())
        assert "viz-3d-on" not in css
        assert ".hv-stage canvas" not in css

    def test_the_comment_that_described_the_build_no_longer_does(self):
        py = _app_py()
        assert "the vendored three.js UMD build and the sample-report" not in py
        assert "Session UX-4 deleted the" in py
