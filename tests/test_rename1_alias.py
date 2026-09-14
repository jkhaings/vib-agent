"""Session RENAME+PRICE (A3) — the machine alias is bounded on the WIRE.

**The door this closes was open, and both the halves that existed were
decorative.** `static/index.html`'s `maxlength="120"` and `app.js`'s
machines-card control both constrain what a person TYPES; neither constrains
anything that posts directly, and neither constrains a value assigned by script.
`db/models.py` holds the column at `String(120)`. Between the two,
`POST /api/jobs` accepted an alias of any length.

**What happened to one.** `db/recorder.py:271` refuses to record a machine whose
alias exceeds the column — silently, returning `None`, because the upload wire
was not that session's to change and it said so in a docstring. So an over-long
alias produced a job that ran, spent the analyst's daily allowance, wrote **no
machine row** and told them nothing; `tests/test_jobdb_recorder.py:283-294` is
the test that documents it. And because SQLite does not enforce VARCHAR length
while Postgres does, the same row fits on a laptop and raises
`StringDataRightTruncation` in production.

Booked twice before it was fixed — Session UX-3, then SESSION_JOBDB F-3 — each
time by a session whose scope did not include `app.py`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fake_anthropic import FakeAnthropicClient
from tests.test_intake2_visible_errors import _csv_bytes, _form
from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.webapp.app import _ALIAS_MAX_CHARS, _alias_422, create_app

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    app = create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"},
                     anthropic_client_factory=lambda: FakeAnthropicClient(responses=[]))
    with TestClient(app) as handle:
        yield handle


def _post(client, alias: str):
    return client.post(
        "/api/jobs",
        files={"file": ("upload.csv", _csv_bytes(), "text/csv")},
        data=_form(machine_alias=alias),
    )


class TestTheBoundary:
    def test_the_cap_is_the_ruled_one(self):
        assert _ALIAS_MAX_CHARS == 120

    def test_exactly_the_cap_is_accepted(self):
        assert _alias_422({"machine_alias": "x" * _ALIAS_MAX_CHARS}) is None

    def test_one_over_is_refused(self):
        assert _alias_422({"machine_alias": "x" * (_ALIAS_MAX_CHARS + 1)}) is not None

    def test_absent_and_empty_are_both_fine(self):
        """The alias is OPTIONAL on this form — `index.html` says so in the
        label. `routes_machines.py` refuses an unnamed machine on the path where
        a name is actually required, and that is a different refusal."""
        assert _alias_422({}) is None
        assert _alias_422({"machine_alias": ""}) is None
        assert _alias_422({"machine_alias": None}) is None


class TestTheRefusalReadsLikeASentence:
    """LIMITS-1c F-3's rule. A refusal nearly shipped pydantic's documentation
    URL to an analyst there; the answer was that what a person reads is written
    for the person, and is checked for plumbing rather than hoped about."""

    @property
    def detail(self) -> str:
        return _alias_422({"machine_alias": "x" * 200})

    def test_it_leaks_no_plumbing(self):
        for leak in ("pydantic", "https://", "http://", "Value error",
                     "validation error", "machine_alias", "String(", "VARCHAR"):
            assert leak not in self.detail, (leak, self.detail)

    def test_it_names_the_actual_length_and_the_limit(self):
        """*"Too long"* makes an analyst count characters. The numbers are what
        turn the refusal into an instruction."""
        assert "200 characters" in self.detail
        assert str(_ALIAS_MAX_CHARS) in self.detail

    def test_it_says_what_to_do(self):
        assert "shorten it" in self.detail

    def test_it_is_a_sentence_and_not_a_field_report(self):
        detail = self.detail
        assert detail[0].islower() or detail[0].isupper()
        assert detail.rstrip().endswith(".")
        assert "\n" not in detail


class TestOnTheWire:
    def test_the_cap_is_accepted(self, client):
        response = _post(client, "P" * _ALIAS_MAX_CHARS)
        assert response.status_code == 202, response.text

    def test_one_over_is_a_422_with_a_string_detail(self, client):
        """INTAKE-2's contract, which this refusal has to satisfy or it is a
        SILENT one: `app.js` routes any non-string detail to a generic
        transient card, discarding the server's account of the problem."""
        response = _post(client, "P" * (_ALIAS_MAX_CHARS + 1))
        assert response.status_code == 422, response.text
        body = response.json()
        assert isinstance(body["detail"], str), body
        assert "shorten it" in body["detail"]

    def test_the_refusal_is_free(self, client):
        """Like every `_*_422`, it runs before the job is created and before
        the daily allowance is spent — so a paste one character over costs
        nothing. If it did not, the analyst would be charged for a typo."""
        for _ in range(4):
            assert _post(client, "P" * 400).status_code == 422
        assert _post(client, "Pump A").status_code == 202

    def test_it_is_refused_before_the_file_is_read(self, client):
        """The hazard `_thresholds_422`'s docstring names: a value that reaches
        the parse sandbox comes back as a PARSE_ERROR, which is a claim about
        the analyst's FILE for something they typed into a form. An unreadable
        file and an over-long alias must not produce the same answer."""
        response = client.post(
            "/api/jobs",
            files={"file": ("upload.csv", b"not a spectrum at all", "text/csv")},
            data=_form(machine_alias="P" * 400),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "shorten it" in detail, (
            "an over-long alias was reported as a problem with the file"
        )


class TestTheBrowserShowsItInline:
    """The `#lim-error` pattern, which is the shape the brief names. The server
    is the authority and this is the immediate half.

    **It is not dead code behind `maxlength`.** `maxlength` constrains typing
    and pasting; it does not constrain a value assigned by script, and this form
    is populated by script every time a saved machine card is restored into it.
    A card written before the cap existed — or imported — puts a longer alias in
    the box, and without this the first the analyst hears of it is a 422 after
    they have chosen a file.
    """

    @property
    def app_js(self) -> str:
        return (REPO / "src/vib_agent/webapp/static/app.js").read_text()

    def test_the_slot_exists_beside_the_input(self):
        index = (REPO / "src/vib_agent/webapp/static/index.html").read_text()
        assert '<p class="help stop" id="alias-error" hidden></p>' in index
        alias_at = index.index('id="alias"')
        error_at = index.index('id="alias-error"')
        assert 0 < error_at - alias_at < 400, (
            "the error slot is not beside the field it describes"
        )

    def test_the_mirror_is_wired_on_change_and_on_input(self):
        source = self.app_js
        assert "function aliasProblem(" in source
        assert "function showAliasError(" in source
        assert "aliasEl.addEventListener('change', onAliasChange)" in source
        assert "aliasEl.addEventListener('input', onAliasChange)" in source

    def test_the_browser_holds_one_copy_of_the_number(self):
        """Three copies of `120` lived in `app.js` before this session. The
        mirror and the machines-card control now both read `ALIAS_MAX`; the
        upload input's `maxlength` attribute is in `index.html`, which is the
        second and last place in the browser."""
        source = self.app_js
        declared = re.findall(r"^const ALIAS_MAX = (\d+);", source, re.M)
        assert len(declared) == 1, f"ALIAS_MAX declared {len(declared)} times"
        assert int(declared[0]) == _ALIAS_MAX_CHARS
        # Every `maxlength:` in the machines-card roster must be a constant or
        # its own field's number -- never a second copy of the alias cap. A bare
        # `str.count("120")` is the wrong instrument here and measuring that was
        # worth it: it matches 12000, 1200 and 120.5, all of which are unrelated
        # timings and coordinates elsewhere in this file.
        caps = re.findall(r"maxlength:\s*(\w+)", source)
        assert str(_ALIAS_MAX_CHARS) not in caps, (
            f"a bare {_ALIAS_MAX_CHARS} is back in a maxlength; it should read "
            "ALIAS_MAX"
        )
        assert "ALIAS_MAX" in caps


# ── the browser half, in node ────────────────────────────────────────────

_SUITE = REPO / "tests" / "js" / "rename1_alias_tests.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_alias_suite_passes():
    """`tests/js/rename1_alias_tests.js` against the SHIPPED `app.js`, driven
    from here so `pytest` stays the one command (`test_limits1c_store.py`'s
    pattern, and its `skipif` too)."""
    result = subprocess.run([shutil.which("node"), str(_SUITE)],
                            capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    # Non-vacuity, against the suite's OWN test count rather than a hard-coded
    # range: a suite that collected nothing exits 0 too, and a range wide enough
    # to survive adding a test is wide enough to miss losing three.
    declared = len(re.findall(r"^test\(", _SUITE.read_text(), re.M))
    assert declared, "the node suite declares no tests"
    assert f"{declared}/{declared} passed" in result.stdout, result.stdout
