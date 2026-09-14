"""Session UX-1 — the front door, and the two things it must not have moved.

**This file never skips** (except the node runner, when node is absent). Every
claim below is about the DEPLOYED backend — `STORE_BACKEND=browser`, which is
what production runs and what `deploy/deploy.sh` installs the dependencies for.
A pin guarded by `importorskip("sqlalchemy")` could be silenced on exactly the
box it matters most on.

Four things are held here:

  1. **The upload form did not move.** UX-1 is allowed a new nav and a prefill;
     it is not allowed to edit the form. The `#form-card` section is compared
     byte-for-byte against a fixture captured from `master` before the first
     edit of this session, so "I only reindented it" is a failure too.
  2. **The report page did not move.** The report page is `readyCard()`'s
     output, so the function's source is pinned the same way. The one thing
     that DID change about that card is a stylesheet rule for a tile that has
     never had one (`.rcard.zA`…) — CSS, not markup, and deliberately so.
  3. **Neither new route exists on this backend.** The router is mounted inside
     `create_app`'s flag branch, so browser mode 404s because the path is not
     there at all — which is also what keeps the three separate
     `no route path mentions a machine` pins true.
  4. **Common law #8 does not fire.** UX-1 reads the two stores HIST-1 and
     GEOM-A already wrote and adds no key, widens no key, and adds no column,
     so the retention ledger and `/privacy` are untouched — asserted, not
     claimed.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _webapp_cfg
from vib_agent.webapp.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "vib_agent" / "webapp" / "static"
_FIXTURES = _ROOT / "tests" / "fixtures" / "ux1"


def _app(**kwargs):
    return create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"}, **kwargs)


def _index() -> str:
    return (_STATIC / "index.html").read_text()


def _app_js() -> str:
    return (_STATIC / "app.js").read_text()


# ══════════════════════════════════════════════════════════════════════════
# 1 · The views, in a real JS runtime
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_machines_views_in_a_real_js_runtime():
    """Runs tests/js/machines_view_tests.js: the adapter over both stores, the
    routing, the empty state, the timeline, the prefill, and the db backend
    against a scripted fetch.

    A TestClient cannot reproduce any of it — `localStorage`, `location.hash`,
    and "would a browser actually show this element" are browser mechanics, and
    the Session HIST-2-FIX-2 lesson is that the last one in particular passes
    every property-reading test while being visibly wrong in Chrome.
    """
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "machines_view_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    match = re.search(r"\n(\d+)/(\d+) passed", result.stdout)
    assert match, result.stdout
    passed, total = int(match.group(1)), int(match.group(2))
    assert passed == total and total >= 20, result.stdout


# ══════════════════════════════════════════════════════════════════════════
# 2 · The upload form and the report page did not move
# ══════════════════════════════════════════════════════════════════════════


class TestTheFormAndTheReportPageAreUnchanged:
    """The session's own boundary, made mechanical.

    Both fixtures were captured from the pristine tree at preflight, before any
    edit. They are not goldens in the "regenerate when it fails" sense: if one
    of these fails, either the change was outside what this session is allowed
    to touch, or it is a deliberate widening that needs saying out loud first.
    """

    def test_the_upload_form_is_one_form_that_posts_once(self):
        """RETIRED, deliberately, by Session UX-2 — and replaced rather than
        deleted.

        UX-1 pinned `#form-card` byte-for-byte because it was not allowed to
        edit the form. UX-2 is the session that rebuilds it: the bearing field
        becomes a closed select over `config/bearings.json` (the free-text one
        could produce a PARSE_ERROR about the analyst's own file), and the
        single form becomes four steps. A byte pin cannot survive that and
        should not — but what it was PROTECTING can, so this says it directly.

        What it was protecting: that `#form-card` is ONE form making ONE POST.
        The failure a stepped intake invites is four forms, or four posts, or a
        field that quietly stops being submitted because it moved.
        """
        text = _index()
        start = text.index('  <section class="card" id="form-card">')
        marker = "\n  </section>\n"
        card = text[start:text.index(marker, start) + len(marker)]
        assert card.count("<form ") == 1, "the intake is one form, however many steps"
        assert card.count("</form>") == 1
        assert 'id="upload-form"' in card

    def test_every_field_the_server_reads_appears_exactly_once(self):
        """The other half of the retired byte pin. A field duplicated across
        two steps posts twice and the server reads whichever came last."""
        card = _index().partition('<section class="card" id="form-card">')[2] \
            .partition("\n  </section>\n")[0]
        for name in ("file", "rpm", "iso_group", "machine_alias", "velocity_unit",
                     "bearing_model", "iso_support", "measurement_location", "coupling",
                     "mode", "invite_code"):
            # A SPACE before `name=`, so a real attribute is counted and the tail
            # of another one is not. Session INTAKE-2 added a per-location
            # `<template>` whose controls carry `data-name="file"` and
            # `data-name="bearing_model"` -- inert markup, cloned and prefixed to
            # `loc2_file` before it can post anything, so it is NOT a second
            # submission of `file`, which is what this pin protects against.
            # Counting attributes rather than characters says that precisely.
            assert card.count(f' name="{name}"') == 1, name

    def test_the_ready_card_is_one_form_making_one_report(self):
        """RETIRED as a byte pin by Session UX-5, and replaced the way this
        file's own `#form-card` byte pin was retired by UX-2 -- by saying
        directly what it was protecting.

        UX-1 pinned this because it was not allowed to edit the report page.
        UX-3 and UX-4 both worked around it for the same reason. UX-5 IS the
        session chartered to change it: STRANGER B3 (the job reference the copy
        tells you to quote and never shows), U9 (no mm/s on the severity card),
        C6 (the reading is silently not saved) and RULED D-26 (the report is
        kept with the reading) all land inside this one function.

        What the byte pin protected, and what is asserted here instead: the
        report page renders the report itself two ways, from the job that
        produced it; it never derives a severity of its own; and it carries the
        retention ledger, which is the promise D-22 makes it diff against
        `static/privacy.html`.
        """
        card = _app_js().partition("function readyCard(jobId, data, degraded) {")[2] \
                        .partition("\n}\n")[0]
        assert card.count("/api/jobs/${esc(jobId)}/report.pdf") == 2
        assert "ledger()" in card, "the retention promise must stay on the report page"
        for invented in ("Zone ", "mm/s", "ISO 20816"):
            assert invented not in card, (
                f"{invented!r} is spelled in the card; every number it shows is "
                "computed by the server and rendered by resultCards/trendCard"
            )
        assert (_FIXTURES / "ready_card_fn.js").exists() is False, (
            "the fixture is retired with the pin; a stale copy invites a "
            "later session to 'restore' a card four findings moved past"
        )

    def test_the_form_still_carries_every_field_the_server_reads(self):
        """Belt and braces on the byte pin: a fixture that was updated by
        accident would still have to lose a field to fail here."""
        html = _index()
        for name in ("file", "rpm", "iso_group", "machine_alias", "velocity_unit",
                     "bearing_model", "iso_support", "measurement_location", "coupling",
                     "mode", "invite_code"):
            assert f'name="{name}"' in html, name


# ══════════════════════════════════════════════════════════════════════════
# 3 · Neither new route exists on the deployed backend
# ══════════════════════════════════════════════════════════════════════════


class TestTheMachinesApiIsNotOnThisBackend:

    def test_every_path_and_verb_is_404(self):
        """Widened by Session UX-2 to the four write routes.

        This is the claim that matters most on this backend, and it holds for
        the same structural reason it always did: the router is imported and
        included inside `create_app`'s `if store_backend == "db":` branch, so
        in `browser` mode the path does not exist at all. A write route that
        leaked out of that branch would answer something other than 404 here
        long before it answered it in production."""
        with TestClient(_app()) as client:
            assert client.get("/api/machines").status_code == 404
            assert client.get("/api/machines/anything").status_code == 404
            assert client.post("/api/machines", json={}).status_code == 404
            assert client.put("/api/machines/x", json={}).status_code == 404
            assert client.request(
                "DELETE", "/api/machines/x", json={"confirm": "x"}
            ).status_code == 404
            assert client.delete("/api/machines/x/readings/2026-01-01T00:00:00").status_code == 404

    def test_no_route_path_mentions_a_machine(self):
        """The same assertion three other files make. Repeated here because
        UX-1 is the session that had the motive to break it."""
        app = _app()
        assert not any("machine" in getattr(route, "path", "") for route in app.routes)

    def test_the_router_module_is_never_imported_at_module_level(self):
        """It imports SQLAlchemy and the auth package at module scope, and the
        deployed box installs neither. Same rule as the db and auth imports,
        and the AST pins that hold those look for THEIR module names, not this
        one — so this needs its own."""
        import ast

        from vib_agent.webapp import app as app_module

        tree = ast.parse(Path(app_module.__file__).read_text())
        names: set[str] = set()
        for node in tree.body:  # module level only, deliberately
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module + "." + ",".join(a.name for a in node.names))
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        assert not any("routes_machines" in name for name in names)

    def test_the_no_store_prefix_tuple_did_not_grow(self):
        """The new routes set `Cache-Control: no-store` themselves rather than
        joining this tuple, which is asserted by strict equality in two other
        files for exactly that reason."""
        from vib_agent.webapp.hardening import NO_STORE_PREFIXES

        assert NO_STORE_PREFIXES == ("/api/jobs", "/api/store", "/healthz")


# ══════════════════════════════════════════════════════════════════════════
# 4 · Common law #8 does not fire
# ══════════════════════════════════════════════════════════════════════════


class TestNothingNewIsKeptAnywhere:

    def test_app_js_writes_to_exactly_the_two_stores_it_already_had(self):
        """UX-1 READS `vib.machines.v2` and `vib.trend.v1`; it writes neither a
        third key nor a new field into either. That is the whole reason the
        retention ledger and `/privacy` are untouched by this session, so it is
        asserted rather than asserted-in-prose."""
        setters = set(re.findall(r"localStorage\.setItem\(\s*([A-Za-z_][\w]*)", _app_js()))
        # Session UX-5 adds the THIRD and last one, under RULED D-26: what this
        # browser knows about the reports it is holding -- file name, committed
        # call, confidence, job reference. Small strings, beside the trend they
        # describe. The PDF BYTES are deliberately NOT here: a ~300 KB blob in a
        # ~5 MB origin-wide quota shared with the other two stores would make
        # `setItem` throw, and the thing that would then stop working is saving
        # a READING. They live in IndexedDB, which this pin does not police --
        # `test_ux5_run_flow.py` does.
        assert setters == {"STORE_KEY", "TREND_KEY", "REPORTS_KEY"}

    def test_the_store_keys_are_the_ones_hist1_left(self):
        js = _app_js()
        assert "const STORE_KEY = 'vib.machines.v2';" in js
        assert "const LEGACY_STORE_KEYS = ['vib.machines.v1'];" in js
        assert "const TREND_KEY = 'vib.trend.v1';" in js

    def test_the_privacy_page_changed_only_in_its_nav(self):
        """The retention promises are not this session's to move. The one edit
        to that file is the masthead link, so every paragraph that makes a
        promise is still where HIST-1 and DB-1 left it."""
        privacy = (_STATIC / "privacy.html").read_text()
        assert '<a href="/#/">Machines</a>' in privacy
        for promise in (
            "Your invite code is never stored.",
            "Saved machines are stored only in your browser, never on our servers.",
            "stays available for 60 minutes, then it is deleted too",
        ):
            assert promise in privacy, promise

    def _ux1_block(self) -> str:
        js = _app_js()
        block = js.partition("// ── machines → readings: the front door")[2]
        assert block, "the UX-1 block is not in app.js"
        return block.partition("\n// ── the honest rail")[0]

    def test_the_new_block_never_assigns_the_property(self):
        assert ".hidden =" not in self._ux1_block(), "hide it through setHidden, not the property"

    def test_the_hides_this_session_inherited_were_converted(self):
        """Four bare `.hidden =` assignments became `setHidden` calls: the
        saved-machine row, the two on the state card, and the preview sweep
        (its own test below). The state card's are not optional — `route()`
        hides it with an inline `display`, which a bare property cannot clear."""
        js = _app_js()
        assert "setHidden(savedRow, list.length === 0);" in js
        assert "setHidden(stateCard, !onUpload);" in js
        assert "setHidden(stateCard, true);" in js

    def test_the_state_card_belongs_to_the_upload_view(self):
        """Polling calls `show()` on every status tick, so a report landing
        while the analyst is reading their machines would otherwise drop a
        status card onto that list."""
        js = _app_js()
        assert "const onUpload = previewMode() || parseRoute().view === 'new';" in js

    def test_preview_mode_hides_by_id_not_by_query_selector(self):
        """`document.querySelectorAll` is the one DOM call the node harness
        answers nothing about, so the sweep that used it was the only piece of
        view-switching in the file no test could see."""
        js = _app_js()
        assert "'.hero, .landing, #form-card, main > section:last-of-type'" not in js
        assert ("LANDING_IDS.concat(['form-card', 'view-machines', 'view-machine',\n"
                "    'view-machine-edit', 'state'])") in js

    def test_every_new_display_rule_carries_its_hidden_guard(self, display_guard_audit):
        """`tests/js/harness.js` reads this stylesheet to decide whether a
        `hidden` element would really be off screen. A bare single-class rule
        declaring a `display` makes it answer "visible" for something a browser
        hides — so each one is guarded, and this checks the guards exist.

        UX-4 F-3, closed: this used to match one bare class at column 0,
        which cannot see a GROUPED selector (`.cta,.btn-ghost{display:...}`) —
        so the pin was weaker than the harness it protects. It now parses the
        way `harness.js:122-151` does, via the shared `display_guard_audit`
        fixture in `tests/conftest.py`.
        """
        css = (_STATIC / "style.css").read_text()
        layer = css.partition("UX-1 LAYER")[2]
        assert layer, "the UX-1 stylesheet layer is missing"
        declares, guarded = display_guard_audit(layer)
        assert declares <= guarded, sorted(declares - guarded)


class TestTheNav:

    def test_the_machines_link_is_in_both_static_pages(self):
        link = '<a href="/#/">Machines</a>'
        assert link in _index()
        assert link in (_STATIC / "privacy.html").read_text()

    def test_the_sample_report_left_the_nav_and_kept_a_home(self):
        """UX-4 removed `/sample-report.pdf` from the masthead. The route is
        untouched and the document is not orphaned: the upload page links it.
        A nav item that becomes an unreachable page is a worse outcome than the
        drift it was removed to fix."""
        index = (_STATIC / "index.html").read_text()
        assert 'href="/sample-report.pdf"' in index

    def test_the_landing_ctas_point_at_the_route_not_the_anchor(self):
        """The upload form is a VIEW now. `#form-card` would scroll to an
        element the router has hidden."""
        html = _index()
        assert 'href="#form-card"' not in html
        # Three: the hero's, the "Start" section's, and the machines list's own
        # primary action.
        assert html.count('href="/#/new"') == 3

    def test_the_landing_sections_have_the_ids_the_router_hides_them_by(self):
        html = _index()
        for section_id in ("hero", "how", "proof", "showcase", "start"):
            assert f'id="{section_id}"' in html, section_id

    def test_the_view_containers_are_real_markup(self):
        """The node harness refuses to answer "is this visible?" about an id
        index.html does not define, so a view built at runtime could not be
        proved hidden."""
        html = _index()
        for element_id in ("view-machines", "view-machine", "machines-body",
                           "machine-body", "machines-head", "machine-head",
                           # Session UX-2's create/edit view. Its FIELDS are
                           # rendered by app.js -- see tests/test_ux2_store.py
                           # for why they must not be written here -- but the
                           # container it renders into is markup, so `visible()`
                           # can be asked about it.
                           "view-machine-edit", "machine-edit-head",
                           "machine-edit-body"):
            assert f'id="{element_id}"' in html, element_id

    def test_the_state_card_is_still_the_last_section_in_main(self):
        """`main > section:last-of-type` is not used any more, but the two new
        sections were placed BEFORE the hero partly so that selector would keep
        meaning what it meant. Pinned so a later insert does not quietly change
        it back under some other rule."""
        html = _index()
        sections = re.findall(r'^  <section[^>]*id="([\w-]+)"', html, re.M)
        assert sections[-1] == "state", sections


class TestTheFourNavsCannotDriftApart:
    """Session SEC-3 — closing UX-1 F-1, and the finding underneath it.

    UX-1 added a **Machines** link to `static/index.html` and
    `static/privacy.html` and could not add it to `app.py::_MASTHEAD`, which
    serves `/validation` and `/field-validation-results.md`, because scope
    closed that file (D-3). So two pages had it and two did not, for two
    sessions, on a site whose own stylesheet says the markup "is duplicated in
    three places and must stay byte-identical" (`style.css:573-576`).

    THE LINK IS THE SMALL HALF. UX-1 wrote it down plainly: *"the real finding
    underneath it is that a three-way duplication with no equality test will
    drift again — this is simply the first time anyone has watched it happen."*
    A test that asserted the missing `<a>` had been added would close the
    instance and leave the mechanism, so this asserts the SET instead, across
    every page that renders a masthead. The next divergence fails here rather
    than shipping and being noticed a session later.

    Browser mode on purpose: it is the backend production runs, and it is also
    where the comparison is exact — `{{account_nav}}` is empty there and
    `_fill_page` takes its whole line with it, so all four navs are the same
    four links with nothing to normalise away.
    """

    #: The nav, in order, as every masthead must render it. Declared rather
    #: than derived from one of the pages: derived, a test comparing the pages
    #: to each other stays green when all of them lose a link together.
    #:
    #: Session UX-4 (STRANGER C1) set this list. Two changes: `/how-it-works`
    #: joins, and `/sample-report.pdf` leaves. The sample report did not stop
    #: existing -- it is linked from `/product`, twice, along with `/demo`,
    #: which is where a document and a worked example belong. What C1 found
    #: was not a missing link, it was TWO navs: the marketing pages rendered
    #: Product / How it works / Demo / Pricing / Start and the app pages
    #: rendered something else, so a stranger crossing between them could not
    #: tell it was one site.
    EXPECTED = ("/#/", "/validation", "/privacy")

    #: Every page a reader can reach that carries a `<nav>`, on the backend
    #: production runs.
    #:
    #: Session UX-4 added the four marketing routes. Before that this tuple
    #: held four pages drawn from three sources, and `marketing/_shell.html`
    #: -- a FOURTH source, added by Session PAGES -- was swept by nothing.
    #: That is how the two navs diverged for a whole session without a red
    #: test. `/login` and `/billing` carry one too and exist only under
    #: `STORE_BACKEND=db`, so they are checked at the SOURCE below instead.
    PAGES = ("/", "/privacy", "/validation", "/field-validation-results.md")

    #: Every file that spells the nav out. Six, not three -- the count in the
    #: failure message below was wrong from Session PAGES until UX-4 measured
    #: it, and `style.css` still said "duplicated in three places".
    SOURCES = (
        "src/vib_agent/webapp/static/index.html",
        "src/vib_agent/webapp/static/privacy.html",
        "src/vib_agent/webapp/app.py",
    )

    def _nav_links(self, client, path: str) -> tuple[str, ...]:
        html = client.get(path).text
        nav = re.search(r"<nav>(.*?)</nav>", html, re.S)
        assert nav, f"{path} renders no <nav> at all"
        return tuple(re.findall(r'href="([^"]+)"', nav.group(1)))

    def test_every_masthead_renders_the_same_nav(self):
        with TestClient(_app()) as client:
            found = {path: self._nav_links(client, path) for path in self.PAGES}
        wrong = {p: links for p, links in found.items() if links != self.EXPECTED}
        assert not wrong, (
            "the masthead nav has drifted. The markup is duplicated in SEVEN "
            "files: static/{index,privacy,terms,login,billing}.html, "
            "app.py::_MASTHEAD (which serves /validation and "
            "/field-validation-results.md) and "
            f"marketing/templates/_shell.html (the four marketing pages): {wrong}"
        )

    def test_every_source_spells_the_same_nav(self):
        """Asserted against the SOURCES as well as the served pages, so the
        failure names the file to edit rather than the URL that looked wrong
        -- and so `/login` and `/billing`, which exist only under
        `STORE_BACKEND=db` and cannot be fetched from the browser-backend app
        this class builds, are covered at all."""
        root = Path(__file__).resolve().parents[1]
        for rel in self.SOURCES:
            text = (root / rel).read_text()
            navs = re.findall(r"<nav>[\s\S]*?</nav>", text)
            assert len(navs) == 1, f"{rel}: {len(navs)} <nav> blocks"
            hrefs = tuple(re.findall(r'href="([^"]+)"', navs[0]))
            assert hrefs == self.EXPECTED, f"{rel}: {hrefs}"
