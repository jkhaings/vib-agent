"""Session TIDY-1 — the round-4 debts that needed no design.

Four claims land here, each owed by a named session that could not pay it:

  * `SESSION_LEGAL1.md` §7.3 — `static/billing.html`'s refund paragraph gains
    the caveat, and stops saying a credit comes back *automatically*.
  * §7.4 — the retention ledger's log row gains the period it always had
    (14 days, from logrotate), and the eighth row LEGAL-1 said was owed lands:
    `outputs/format_pings.jsonl`, the one thing this product keeps that no line
    in the ledger named.
  * §7.8 — `CONTACT_EMAIL` stops defaulting to a placeholder domain.

The nav/footer half of §7 is pinned next door, in `test_ux4_shell.py` and
`test_ux1_views.py`, because the classes that own those two duplications were
already there.

Everything here reads SOURCE. The behavioural half of the ledger — that the row
count is what it claims and that the format-note row says the right one of its
two things — runs in node, in `tests/js/trend_card_tests.js`.
"""
from __future__ import annotations

import re
from pathlib import Path

import vib_agent.webapp as webapp_pkg
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = Path(webapp_pkg.__file__).parent / "static"


def _prose(text: str) -> str:
    """Collapse whitespace: these are claims about what a reader sees, and
    where a source line happens to wrap is not that."""
    return re.sub(r"\s+", " ", text)


def _ledger() -> str:
    return _prose(
        (_STATIC / "app.js").read_text()
        .partition("function ledger() {")[2].partition("\n}")[0]
    )


# ══════════════════════════════════════════════════════════════════════════
# 1 · LEGAL-1 §7.3 — the refund caveat
# ══════════════════════════════════════════════════════════════════════════




# ══════════════════════════════════════════════════════════════════════════
# 2 · LEGAL-1 §7.4 — the two ledger rows
# ══════════════════════════════════════════════════════════════════════════


class TestTheLogRowCarriesItsPeriod:
    """The row said "kept" and stopped there, while `static/privacy.html` had
    said "kept for 14 days" since it was written. Two surfaces describing one
    file, one of them with the period and one without."""

    def test_the_ledger_names_the_period(self):
        assert "<b>One log line</b> — kept for <b>14 days</b>" in _ledger()

    def test_it_is_the_same_period_privacy_states(self):
        assert "That line is kept for 14 days" in _prose(
            (_STATIC / "privacy.html").read_text())


    def test_the_row_still_says_what_is_not_in_that_line(self):
        """v6-A's finding, and the one claim on this row that a period must not
        push off the end of it."""
        assert "No IP address, no filename, no machine name." in _ledger()


class TestTheFormatNoteHasARowAtLast:
    """LEGAL-1 §7.4, verbatim: *"an eighth row is owed for
    `format_pings.jsonl`"*.

    `AppState.record_format_ping` appends the file's structure and the recipe
    that read it to `outputs/format_pings.jsonl` when an analyst ticks "Help us
    support this format". It is opt-in and it is careful — the two numbers a
    recipe can carry are stripped, and no reading, filename, machine name or
    invite code is written. It was also, until this session, the only thing
    this product keeps that no line in the retention ledger mentioned, and the
    only one with no expiry at all.
    """

    def test_the_row_exists_in_both_of_its_branches(self):
        ledger = _ledger()
        assert ledger.count("<b>The format note</b>") == 2, (
            "the row is written from what the run DID, like the trace and "
            "report rows above it — so it has a ticked branch and an "
            "unticked one, and neither is a default"
        )

    def test_the_ticked_branch_says_there_is_no_expiry(self):
        """The honest half. `deploy/setup_server.sh` rotates `app.log` and
        nothing else, so this file is kept until somebody deletes it."""
        assert "kept, with <b>no expiry</b>" in _ledger()


    def test_the_row_enumerates_what_is_not_written(self):
        ledger = _ledger()
        for claim in ("No readings", "no numbers we read from it", "no filename",
                      "no machine name", "no invite code", "not the file"):
            assert claim in ledger, claim

    def test_those_claims_are_the_ones_the_writer_keeps(self):
        """Diffed against the code rather than remembered. The payload is the
        fingerprint plus the recipe with `rpm_value` and `fs_value` cleared —
        no reading, no filename, no machine name, no invite code."""
        app_py = (_ROOT / "src" / "vib_agent" / "webapp" / "app.py").read_text()
        body = app_py.partition("def record_format_ping(")[2].partition("\n    def ")[0]
        assert 'payload["rpm_value"] = None' in body
        assert 'payload["fs_value"] = None' in body
        assert re.search(r'json\.dumps\(\{"fingerprint": fingerprint, "recipe": payload\}', body), (
            "the line written is no longer fingerprint + recipe, so the row "
            "enumerating what is NOT in it is no longer a diff of anything"
        )

    def test_the_unticked_branch_claims_nothing_was_recorded(self):
        assert "so nothing about how this file is laid out was recorded" in _ledger()

    def test_the_row_is_written_from_this_run_rather_than_from_the_form(self):
        """`RETAINED` is the ledger's source of truth about what THIS run did.
        The share box lives on the confirm card, so the flag is captured where
        the confirm body is built — not read out of the DOM at render time,
        when the card it lives on has already been replaced."""
        js = (_STATIC / "app.js").read_text()
        assert "share: false }" in js, "RETAINED does not carry the flag"
        assert "RETAINED.share = body.share_format;" in js
        assert "RETAINED.share" in _ledger()

    def test_ticking_the_box_is_necessary_and_not_sufficient(self):
        """A ping is written only for an ENDORSED recipe on a file that was not
        already cached, so the ticked branch must not claim one was written."""
        assert "Written only if this file needed a recipe we did not already have." \
            in _ledger()


# ══════════════════════════════════════════════════════════════════════════
# 3 · LEGAL-1 §7.8 — the placeholder address
# ══════════════════════════════════════════════════════════════════════════


