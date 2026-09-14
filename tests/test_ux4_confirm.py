"""Session UX-4, slice 3 -- the confirmation layer, and the wider container
the operator ruled for the app surfaces.

Two claims, and the second is a design decision rather than a fix:

  * ONE CONFIRMATION (STRANGER U12). There were four shapes for one job -- a
    `notice` threaded into a re-render, a small grey `.help` line, a hidden
    `#...-error` box, and a wholesale card swap -- plus two actions that
    reported nothing at all: renaming a machine, and forgetting a saved one
    from the intake form. There is now one component, and the run's outcome
    enters with the same motion rather than having its own.
  * THE APP SURFACES GET ROOM. RULED by the operator under "this is a web
    application first": `.wrap` has capped every page at 880px since ui-v1,
    which is a reading measure. The machines list, a machine page and the
    readings table now get 1200px; /product, /privacy, /validation and the
    intake form keep 880, because a 1200px-wide column of labelled fields is
    a worse form and a 1200px paragraph is a worse paragraph.

The node half runs the real `app.js` (`tests/js/notify_tests.js`) and uses
`strictEl` throughout -- the refusal UX-3 F-12 asked the next session with
`tests/js/harness.js` open to add, and this is that session.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"

_TOAST_BANNER = "UX-4 LAYER · slice 3b"
_WIDTH_BANNER = "UX-4 LAYER · slice 3a"
_BANNERS = ("UX-4 LAYER · slice 2", "UX-4 LAYER · slice 3a",
            "UX-4 LAYER · slice 3b", "UX-4 LAYER · slice 4")


def _css() -> str:
    return (_STATIC / "style.css").read_text()


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def _section(banner: str) -> str:
    """The rules under one slice banner, comment-free.

    Cut to the end of the banner comment first: partitioning on the banner
    text lands INSIDE that comment, and `_strip_comments` cannot remove a
    comment whose opening `/*` it never saw.
    """
    tail = _css().partition(banner)[2]
    assert tail, f"{banner} is missing"
    body = tail.partition("*/")[2]
    # Bounded by the NEXT banner, not by end-of-file: a section that runs to
    # EOF picks up every later slice's rules, and a pin about what section N
    # contains stops meaning anything the moment section N+1 is written.
    for later in _BANNERS:
        if _css().index(later) > _css().index(banner):
            body = body.partition(later)[0]
    return _strip_comments(body)


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _harness() -> str:
    return (_ROOT / "tests" / "js" / "harness.js").read_text()


# ══════════════════════════════════════════════════════════════════════════
# 1 · The component exists once, and is reachable
# ══════════════════════════════════════════════════════════════════════════


class TestTheComponent:

    def test_the_region_is_real_markup_and_a_live_region(self):
        """Real markup for the reason `index.html:46-52` gives about the view
        containers: `tests/js/harness.js` refuses to answer about an id the
        page does not define. And a live region must be in the document
        BEFORE its first mutation, or the first announcement is swallowed as
        an unhide -- the same reason `show()` defers its first write."""
        html = _index()
        region = re.search(r'<div id="toasts"[^>]*>', html)
        assert region, "#toasts is not in the markup"
        tag = region.group(0)
        assert 'role="status"' in tag
        assert 'aria-live="polite"' in tag
        assert html.count('id="toasts"') == 1
        assert '<div id="toasts" class="toasts" role="status" aria-live="polite" ' \
               'aria-atomic="false"></div>' in html, "the region is not empty at rest"

    def test_there_is_one_emitter(self):
        js = _app_js()
        assert js.count("function notify(") == 1
        assert js.count("function renderToasts(") == 1
        assert js.count("function dismissToast(") == 1

    def test_it_is_dismissible_and_labelled(self):
        js = _app_js()
        assert 'aria-label="Dismiss this message"' in js
        assert "id === 'toast-dismiss'" in js

    def test_it_never_eats_a_click_meant_for_the_page(self):
        """The region spans a corner of every screen. `pointer-events:none` on
        the container with `auto` on the toast is what keeps it non-blocking."""
        layer = _section(_TOAST_BANNER)
        assert re.search(r"\.toasts\{[^}]*pointer-events\s*:\s*none", layer)
        assert re.search(r"\.toast\{[^}]*pointer-events\s*:\s*auto", layer)

    def test_every_bare_display_rule_in_the_layer_carries_its_guard(self):
        """The house rule, checked the way `tests/js/harness.js` parses --
        splitting each selector on commas -- rather than the way the UX-1 and
        UX-2 copies do, which only match a single bare class at column 0 and
        therefore cannot see a grouped rule."""
        for banner in (_WIDTH_BANNER, _TOAST_BANNER):
            layer = _section(banner)
            declares, guarded = set(), set()
            for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", layer):
                for part in selectors.split(","):
                    sel = part.strip()
                    bare = re.fullmatch(r"\.([A-Za-z][\w-]*)", sel)
                    if bare and re.search(r"(^|[;\s])display\s*:", body):
                        declares.add(bare.group(1))
                    guard = re.fullmatch(r"\.([A-Za-z][\w-]*)\[hidden\]", sel)
                    if guard and "display:none" in body.replace(" ", ""):
                        guarded.add(guard.group(1))
            assert declares <= guarded, (banner, sorted(declares - guarded))

    def test_reduced_motion_is_handled_in_the_code_and_not_only_the_css(self):
        """The global block at the top of style.css kills every animation with
        `!important`, so the exit animation does not run under reduced motion
        -- and waiting 200ms for it would leave the message on screen doing
        nothing. `dismissToast` skips the phase instead of shortening it."""
        fn = _app_js().partition("function dismissToast() {")[2].partition("\n}")[0]
        assert "reduceMotion" in fn


# ══════════════════════════════════════════════════════════════════════════
# 2 · Every lifecycle action reports through it
# ══════════════════════════════════════════════════════════════════════════


class TestEveryActionReports:

    #: Every outcome this component reports, and the words that carry it.
    #:
    #: Session UX-5 changes the roster in three ways, each with a reason:
    #:   * "saved machine forgotten" is GONE as an outcome. That control no
    #:     longer finishes anything -- it opens the machine's own typed
    #:     confirmation (C11), and the toast for the delete comes from there.
    #:   * "auto-saved" and "undone" are NEW. A reading is now filed against a
    #:     saved machine without being asked for (RULED, C6), which is exactly
    #:     the kind of outcome this component exists to report, and the undo is
    #:     an action ON the toast rather than a second surface.
    #:   * "copied" is new, for the job reference (B3).
    #:
    #: Session GEOM-1 adds two, both on the bearing block and both for the
    #: reason this component exists:
    #:   * "geometry entered" — four numbers typed into a section the analyst
    #:     had to open to find, which turn the headline screen on. That it is
    #:     now on is the thing they are waiting to hear.
    #:   * "geometry cleared" — choosing a catalogue bearing DISCARDS geometry
    #:     already entered, because a run is analysed against one bearing.
    #:     UX-5's rule for the bearing a `<select>` silently reset: dropping
    #:     what somebody entered is a thing to say, not a thing to do. It
    #:     carries the undo action, which puts BOTH halves back.
    SITES = (
        ("added", "added."),
        ("renamed", "moved with it."),
        ("machine deleted", "are gone from this browser."),
        ("reading deleted", "deleted."),
        ("saved to trend", "trend, in this browser."),
        ("auto-saved (C6)", r"\u2019s trend, in this browser."),
        ("undone (C6)", "Not saved."),
        ("job reference copied (B3)", "Job reference copied."),
        ("copy refused, so it says the reference (B3)", "Copy it by hand:"),
        ("bearing geometry entered (GEOM-1)", "Bearing geometry entered"),
        ("bearing geometry cleared for a catalogue bearing (GEOM-1)",
         "The geometry you had entered was cleared"),
    )

    def test_each_site_emits(self):
        js = _app_js()
        for label, needle in self.SITES:
            assert needle in js, f"{label}: {needle!r}"

    #: Ten CALL SITES for eleven outcomes: "added" and "renamed" still share
    #: one, because `submitMachineEdit` already knows which it did (a truthy
    #: `editingId`) and a ternary there is honester than two calls in two
    #: branches that could drift apart. GEOM-1's two are two separate calls in
    #: two separate functions -- one fires when the geometry becomes complete,
    #: the other when it is discarded, and nothing knows both.
    CALL_SITES = 10

    def test_the_count_is_the_count(self):
        """One definition plus one call per call site, and no stray extra.

        Counted as OCCURRENCES, not lines -- `grep -c` counts lines and is how
        SESSION_PAGES F-6 got its brand count wrong twice before it was right.
        """
        js = _app_js()
        assert js.count("notify(") == 1 + self.CALL_SITES, js.count("notify(")
        assert len(self.SITES) == self.CALL_SITES + 1, (
            "add and rename share a call site; if that changed, so did this"
        )

    def test_the_undo_is_an_action_on_the_component_not_a_second_surface(self):
        """C6 asks for an undo. It is a control INSIDE the one confirmation
        surface, and it holds longer than a plain confirmation -- a control
        that leaves in 5.2 seconds was not really offered."""
        js = _app_js()
        assert 'id="toast-action"' in js
        assert "TOAST_ACTION_HOLD_MS" in js
        assert js.count("function notify(message, kind, action) {") == 1
        hold = js.partition("const TOAST_ACTION_HOLD_MS = ")[2].partition(";")[0]
        assert int(hold) > 5200, hold

    def test_the_small_grey_line_it_replaces_is_gone_from_the_ready_card(self):
        """U12 quoted this one by name. It lived in `trendBlock`, not
        `readyCard` -- which matters, because `readyCard`'s source is pinned
        byte-for-byte by `tests/test_ux1_views.py` against a fixture, and this
        slice must not have touched it."""
        js = _app_js()
        old = ('<p class="help" style="margin-top:10px">Saved to this machine’s '
               'trend, in this browser.</p>')
        assert old not in js

    def test_the_type_to_confirm_guard_survives(self):
        """It is a GUARD, not a report -- the stranger called it good, and the
        toast is what follows it rather than what replaces it."""
        js = _app_js()
        assert "md-confirm" in js and "md-go" in js
        assert "That is not this machine’s alias" in js


# ══════════════════════════════════════════════════════════════════════════
# 3 · The run's outcome shares the motion
# ══════════════════════════════════════════════════════════════════════════


class TestTheOutcomeSharesTheMotion:

    def test_the_keyframes_are_shared_and_the_restart_is_deliberate(self):
        layer = _section(_TOAST_BANNER)
        assert "@keyframes ux4-in{" in layer
        assert "@keyframes ux4-in-b{" in layer, (
            "the second name is the restart mechanism, not a duplicate: a CSS "
            "animation restarts only when its animation-name changes, and "
            "#state is not recreated between polls"
        )
        assert re.search(r'#state\[data-enter="a"\]\{animation:ux4-in ', layer)
        assert re.search(r'#state\[data-enter="b"\]\{animation:ux4-in-b ', layer)
        for name in ("ux4-in", "ux4-in-b"):
            body = layer.partition("@keyframes " + name + "{")[2].partition("}}")[0]
            assert "translateY(8px)" in body, name

    def test_the_toast_and_the_outcome_use_one_duration_token(self):
        layer = _section(_TOAST_BANNER)
        assert "--motion-in:" in layer
        assert layer.count("animation:ux4-in var(--motion-in)") >= 2

    def test_show_run_flips_it_only_on_an_announced_state(self):
        fn = _app_js().partition("function showRun(jobId, data) {")[2].partition("\n}")[0]
        assert "data-enter" in fn
        assert fn.index("RUN_ANNOUNCE.indexOf(state)") < fn.index("data-enter"), (
            "the attribute is flipped before the already-announced guard, so "
            "every poll would restart the animation"
        )

    def test_the_ready_card_is_the_report_page_it_has_always_been(self):
        """RETIRED as a byte pin by Session UX-5, and replaced rather than
        dropped -- the UX-2 precedent for `#form-card`'s own byte pin.

        UX-3 and UX-4 both worked AROUND this fixture because neither was
        chartered to change the report page. UX-5 is: four of the stranger's
        findings land inside this one function (B3 the job reference, U9 the
        mm/s value, C6 the auto-save, D-26 the kept report), and a byte pin
        cannot survive that and should not.

        What it was PROTECTING can, and is asserted directly: the report page
        offers the report two ways, offers the next run, carries the result
        cards, the trend block and the retention ledger, and emits exactly one
        focus target so `id="run-status"` stays unique inside `#state`.
        """
        js = _app_js()
        card = js.partition("function readyCard(jobId, data, degraded) {")[2] \
                 .partition("\n}\n")[0]
        assert card.count("/api/jobs/") == 2, "view and download, both from the job"
        assert 'target="_blank"' in card and "download>" in card
        assert 'id="again"' in card, "the next run"
        assert "resultCards(rs, data.trend_point)" in card, "severity + committed call"
        assert "trendBlock(data)" in card and "ledger()" in card
        assert card.count("statusLine(") == 2, (
            "one status line per branch, so `id=run-status` is unique in #state"
        )
        assert "channelsBlock(data.channels)" in card


    def test_the_shell_widens_together(self):
        """Header, main and footer, or the chrome and the content stop sharing
        an edge and the nav jumps when an analyst crosses into /#/."""
        layer = _section(_WIDTH_BANNER)
        rule = re.search(r"body\.app header \.wrap,\s*body\.app main\.wrap,\s*"
                         r"body\.app footer \.wrap\{([^}]*)\}", layer)
        assert rule, "the three wrappers do not widen together"
        assert "max-width:1200px" in rule.group(1)

    def test_the_gutter_is_clamped_not_fixed(self):
        """A flat 32px is right at 1440 and is 24px stolen from a 375px screen
        that has none to give. Measured: the content column went 335 -> 311
        before this, which is a regression the desktop ruling did not ask for."""
        layer = _section(_WIDTH_BANNER)
        assert "clamp(20px, 2vw, 32px)" in layer

    def test_the_reading_surfaces_keep_their_measure(self):
        layer = _section(_WIDTH_BANNER)
        rule = re.search(r"body\.app #hero,([\s\S]*?)\{([^}]*)\}", layer)
        assert rule, "the reading sections are not constrained"
        assert "max-width:880px" in rule.group(2)
        for section_id in ("#how", "#proof", "#showcase", "#start", "#form-card"):
            assert section_id in rule.group(1) + rule.group(0), section_id

    def test_the_intake_form_is_a_reading_surface_on_purpose(self):
        """It is the one app surface that keeps 880: a 1200px-wide column of
        labelled fields is a worse form. Pinned so the next session does not
        'finish the job' by widening it."""
        layer = _section(_WIDTH_BANNER)
        assert "body.app #form-card" in layer


# ══════════════════════════════════════════════════════════════════════════
# 5 · The harness got the refusal UX-3 F-12 asked for
# ══════════════════════════════════════════════════════════════════════════


class TestTheHarness:

    def test_strict_el_exists_and_refuses_an_unknown_id(self):
        h = _harness()
        assert "strictEl(key)" in h
        fn = h.partition("strictEl(key) {")[2].partition("\n    },")[0]
        assert "_known" in fn and "throw" in fn

    def test_get_el_still_invents_one(self):
        """F-12's reasoning, kept: making `getEl` throw would touch every
        suite, and this session has no more evidence than UX-3 did about what
        leans on the invented-element behaviour. `strictEl` is opt-in."""
        h = _harness()
        fn = h.partition("const getEl = (id) => {")[2].partition("\n  };")[0]
        assert "throw" not in fn

    def test_the_new_suite_uses_it(self):
        suite = (_ROOT / "tests" / "js" / "notify_tests.js").read_text()
        assert "strictEl" in suite
        assert "a.getEl(" not in suite, (
            "a check that reads an invented element is a check that passes "
            "for a component that was never built -- F-12 exactly"
        )


# ══════════════════════════════════════════════════════════════════════════
# 6 · The node half
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_confirmation_layer_in_a_real_js_runtime():
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "notify_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = map(int, match.groups())
    assert passed == total and total >= 16, result.stdout
