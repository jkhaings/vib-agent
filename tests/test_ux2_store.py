"""Session UX-2 — the lifecycle: the claims python can make about it.

The behavioural half runs in node against the shipped `app.js`
(`tests/js/machine_store_tests.js`), because what a rename does to two
localStorage keys is not a thing a `TestClient` can be asked. This file runs
that suite and adds the claims that are about SOURCE rather than behaviour —
the ones a browser test would pass while the promise underneath it went stale.

This file never skips, except the node runner when node is absent.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

import vib_agent.webapp as webapp_pkg

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(webapp_pkg.__file__).parent / "static"


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


def _index() -> str:
    return (_STATIC / "index.html").read_text()


# ══════════════════════════════════════════════════════════════════════════
# 1 · The behaviour, in a real JS runtime
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_lifecycle_in_a_real_js_runtime():
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "machine_store_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = int(match.group(1)), int(match.group(2))
    assert passed == total and total >= 24, result.stdout


# ══════════════════════════════════════════════════════════════════════════
# 2 · A machine is an alias AND a measurement point — in the source, once
# ══════════════════════════════════════════════════════════════════════════


class TestIdentityHasOneDefinition:
    """Session F2 de-duped saved cards by ALIAS while every other part of the
    product identified a machine by alias + measurement point. Saving "Pump A ·
    Motor DE" and then "Pump A · Pump NDE" therefore destroyed the first card.
    The node suite proves the behaviour; these two pin the shape, so a later
    edit cannot reintroduce the old predicate and still read plausibly."""

    def test_the_card_dedupe_is_by_key_not_by_name(self):
        js = _app_js()
        assert "cardKey(m) !== cardKey(entry)" in js
        assert "machineName(m) !== entry.machine_alias" not in js, (
            "the alias-only dedupe is back; two measurement points on one "
            "machine will collapse into a single card again"
        )

    def test_card_key_is_written_through_trend_key(self):
        """One definition of "which machine is this", shared by the card store
        and the trend store — they cannot come to disagree if only one of them
        computes it."""
        block = _app_js().partition("function cardKey(card) {")[2].partition("\n}")[0]
        assert "trendKey(" in block, block


# ══════════════════════════════════════════════════════════════════════════
# 3 · The promise moves with the control (D-22, and the file we do not own)
# ══════════════════════════════════════════════════════════════════════════


class TestTheVerbMatchesItsOwnPrivacyPage:
    """ONE VERB, and it is `delete`.

    Session UX-2 made this backend-dependent and said why: `privacy.html`
    promised that *the "Forget" button deletes it*, that file belonged to
    another session, and naming the control anything else would have left the
    sentence describing a control that did not exist. So `browser` said Forget
    and only `db` said Delete.

    Session UX-5 (STRANGER C11: "Delete is called Forget everywhere") closes it
    the other way. Forget is softer than what the control does — it removes a
    machine and every reading filed under it, with no copy anywhere — and two
    words for one action was the drift UX-2 was avoiding, one level up.

    `privacy.html` belonged to LEGAL-1 that round, so the sentence it owed was
    written verbatim in `outputs/SESSION_UX5.md` and the pin below was a STRICT
    xfail. PAID -- Session TIDY-1 landed the wording and removed the marker; it
    is an ordinary pin now, and it still fails loudly on wording that says
    something else.
    """

    def test_there_is_one_word_and_it_does_not_depend_on_the_backend(self):
        js = _app_js()
        block = js.partition("function deleteWord(capital) {")[2].partition("\n}")[0]
        assert "const word = 'delete';" in block, block
        assert "storeMode()" not in block, (
            "the verb branches on the backend again; one action, one word"
        )

    def test_the_browser_promise_names_the_control_by_its_new_name(self):
        """D-22's other half. The control is named `Delete` on both backends,
        so the sentence that promises what it does has to say `Delete` too —
        exactly the coupling UX-2 protected, pointed at the new word."""
        privacy = (_STATIC / "privacy.html").read_text()
        assert "the “Delete” button deletes it" in privacy

    def test_the_old_sentence_is_still_there_until_legal_1_moves_it(self):
        """The coupling this class protects, asserted apart from the word: a
        control and its promise move together, so the sentence itself must
        survive the rename. It did -- the page says "Delete" now, and the
        clause that says what the button does is still in it."""
        privacy = (_STATIC / "privacy.html").read_text()
        assert "button deletes it" in privacy, (
            "the sentence itself has gone; the coupling this class protects "
            "was that a control and its promise move together"
        )


# ══════════════════════════════════════════════════════════════════════════
# 4 · The new stylesheet layer plays by the harness's rules
# ══════════════════════════════════════════════════════════════════════════


class TestTheStylesheetLayer:

    def test_the_ux2_layer_exists_and_is_last(self):
        css = (_STATIC / "style.css").read_text()
        assert "UX-2 LAYER" in css
        assert css.index("UX-1 LAYER") < css.index("UX-2 LAYER")

    def test_every_bare_display_rule_carries_its_hidden_guard(self, display_guard_audit):
        """The same rule `test_ux1_views.py` applies to the UX-1 layer, stated
        again for this one: `tests/js/harness.js` reads this stylesheet to
        decide whether a `hidden` element is really off screen, and an
        unguarded bare-class `display` makes it answer "visible" for something
        a browser hides — the HIST-2-FIX-2 defect.

        UX-4 F-3, closed: parses grouped selectors the way `harness.js` does,
        through the shared fixture, instead of matching one class at column 0.
        """
        layer = (_STATIC / "style.css").read_text().partition("UX-2 LAYER")[2]
        assert layer
        declares, guarded = display_guard_audit(layer)
        assert declares <= guarded, sorted(declares - guarded)


# ══════════════════════════════════════════════════════════════════════════
# 5 · The edit form is rendered, not written into index.html
# ══════════════════════════════════════════════════════════════════════════


class TestTheEditFormStaysOutOfTheMarkup:
    """`bearing_model`, `iso_support` and `detection_type` are pinned to live
    under the More options disclosure and NOWHERE BEFORE IT
    (`tests/test_session_f2.py`, `tests/test_intake_honest.py`). A second
    static copy of those names in the machine form — which sits above the
    upload form — would turn both pins red while changing nothing an analyst
    sees. So the fields are rendered by app.js into a container the markup
    declares, and this says so out loud rather than leaving it to luck."""

    def test_the_container_is_real_markup_and_the_fields_are_not(self):
        html = _index()
        for element_id in ("view-machine-edit", "machine-edit-head", "machine-edit-body"):
            assert f'id="{element_id}"' in html, element_id
        head = html.partition('<details class="more" id="more-options">')[0]
        for name in ("bearing_model", "iso_support", "detection_type"):
            # A SPACE before `name=`, so this matches the real attribute rather
            # than the tail of another one. Session INTAKE-2 added a
            # per-location `<template>` whose controls carry
            # `data-name="bearing_model"` -- inert markup, cloned and prefixed
            # to `loc2_bearing_model` before it can post anything -- and the bare
            # substring read that as the machine form being written into the
            # page. The property protected here is unchanged; it is now tested
            # for an attribute instead of for six characters.
            assert f' name="{name}"' not in head, (
                f"{name} is in the markup above More options; the machine form "
                f"must be rendered by app.js, not written here"
            )

    def test_the_rendered_fields_are_the_ones_that_exist_end_to_end(self):
        """Every field the machine form offers is a MEMORY_FIELDS entry, so a
        machine created here is a machine the upload form could have created
        and the server-side mirror already has a column for. A field invented
        here would be dropped by `migrateEntry` on the next read."""
        js = _app_js()
        whitelist = set(re.findall(r"'([a-z_]+)'", js.partition("const MEMORY_FIELDS = [")[2]
                                   .partition("];")[0]))
        block = js.partition("const EDIT_FIELDS = [")[2].partition("\n];")[0]
        offered = set(re.findall(r"\{ name: '([a-z_]+)'", block))
        assert offered, block[:200]
        assert offered <= whitelist, sorted(offered - whitelist)

    def test_the_form_does_not_offer_a_machine_type(self):
        """There is no machine-type field anywhere in the product: not in
        MEMORY_FIELDS, and so — by `tests/test_db1_schema.py`'s tuple equality
        — not as a column either. Offering one here would store something the
        `db` backend silently drops on import."""
        block = _app_js().partition("const EDIT_FIELDS = [")[2].partition("\n];")[0]
        assert "machine_type" not in block
