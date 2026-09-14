"""hotfix-net — poll resilience, the error taxonomy, and limit sanity.

Three layers, one command:

  * the CLIENT loop is exercised in node against the real app.js (tests/js/),
    with a scripted fetch and a fake clock — a dropped poll, a 429, a long
    outage, the resume button;
  * the SERVER's limits are exercised through the app: status polls get their
    own per-IP budget, every 429 carries Retry-After;
  * the BUDGET is exercised for fairness: a rejected attempt costs the analyst
    nothing, because it cost us nothing.

The thing being defended throughout: a network problem must never be reported
as an analysis failure, and a client following our own protocol must not be able
to rate-limit itself out of its own job.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _spectrum_csv, _webapp_cfg
from vib_agent.config import load_config
from vib_agent.webapp.app import create_app
from vib_agent.webapp.hardening import IpRateLimiter, is_status_request

_ROOT = Path(__file__).resolve().parents[1]
_APP_JS = _ROOT / "src" / "vib_agent" / "webapp" / "static" / "app.js"
CODE = "demo-code"


def _app(**cfg_over):
    cfg = _webapp_cfg(**cfg_over)
    return create_app(webapp_cfg=cfg, invite_codes={CODE: "engineer-1"},
                      contact_email="ops@example.test")


def _bare_state(**cfg_over):
    """The AppState alone, for the Retry-After estimators. They are pure
    arithmetic over config plus observed durations, so they need no app, no
    client and no event loop."""
    return create_app(webapp_cfg=_webapp_cfg(**cfg_over),
                      invite_codes={CODE: "engineer-1"},
                      contact_email="ops@example.test").state.vib


def _post_job(client: TestClient, path: Path, *, code: str = CODE, **form_over):
    form = {"invite_code": code, "machine_alias": "M", "rpm": "1800",
            "iso_group": "2", "iso_support": "rigid", "machine_type": "motor"}
    form.update(form_over)
    with open(path, "rb") as handle:
        return client.post("/api/jobs", files={"file": (path.name, handle, "text/csv")}, data=form)


# ══════════════════════════════════════════════════════════════════════════
# 1-2 · The client loop, in node, against the real app.js
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_polling_behaviour_in_a_real_js_runtime():
    """Runs tests/js/poll_tests.js: flaky fetch, 429 backoff, lost contact,
    resume, the 20s confirm interval. Failures print with their assertion."""
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "poll_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    assert re.search(r"\n\d+/\d+ passed", result.stdout), result.stdout
    passed, total = map(int, re.search(r"\n(\d+)/(\d+) passed", result.stdout).groups())
    assert passed == total and total >= 10, result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_preview_state_renders_without_a_percentage():
    """UX-WIRE U1. Runs tests/js/preview_tests.js: walks every ?preview= key
    against the real app.js and asserts each renders, opens with a status line,
    and carries no percentage and no `Verifying` step -- plus the
    dashed-never-done treatment on the two states where drafting did not run.

    The preview map is the operator's review surface, so a key that throws is a
    state card nobody can look at. Walking it mechanically is what makes the
    prompt's "walk all twenty" a gate rather than a good intention."""
    result = subprocess.run(
        [shutil.which("node"), str(_ROOT / "tests" / "js" / "preview_tests.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"\n{result.stdout}\n{result.stderr}"
    passed, total = map(int, re.search(r"\n(\d+)/(\d+) passed", result.stdout).groups())
    # 26, not 25: the suite's printed total was one short of the checks it
    # ran (Session UX-2), so this floor was being met by arithmetic. With
    # the count corrected the floor is raised to the real number.
    assert passed == total and total >= 26, result.stdout


class TestTheRailIsHonest:
    """UX-WIRE U1 / UXWIRE_PROMPT §5.1-5.2, asserted at the SOURCE as well as
    at the render, because these are removals: a render test proves the current
    cards are clean, and a source test proves the machinery cannot come back."""

    def _css(self) -> str:
        return (_APP_JS.parent / "style.css").read_text()

    def test_no_percentage_survives_anywhere_in_the_client(self):
        """`{queued:10, analyzing:35, drafting:70}` rendered as a filled bar,
        and nothing measured those numbers. In a product whose pillar is
        *computed* confidence, a picture of a percentage nobody computed is the
        same class of defect as the `verifier: 31/31` claim PARITY §X10 cut
        from the export. Also gone from the upload side: `fetch` does not
        expose upload progress, so the submitting card gets no bar either."""
        js, css = _APP_JS.read_text(), self._css()
        for token in (".meter", "stepline", "phase-now"):
            assert token not in js, f"{token} is back in app.js"
            assert token not in css, f"{token} is back in style.css"
        assert not re.search(r"width:\s*\$?\{?\w*\}?\s*%", js), "a percentage width is back"
        assert not re.search(r"\bpct\b", js), "a percentage variable is back"

    def test_there_is_no_verifying_step(self):
        """Verification happens INSIDE the drafting call and is never published
        as its own state (jobs.py:27-31), so the client could only ever light
        this step retroactively. A step that is only lit in the past tense is a
        step the analyst cannot use. The guarantee is stated on the finished
        report instead, in the checks' own terms."""
        code = "\n".join(line.split("//")[0] for line in _APP_JS.read_text().splitlines())
        assert "Verifying" not in code, "the Verifying step is back in the rail"
        assert "verifying" not in code, "a `verifying` phase key is back"
        assert "RAIL_STEPS" in code

    def test_the_rail_names_only_distinguishable_moments(self):
        js = _APP_JS.read_text()
        assert "const RAIL_STEPS = ['Uploaded', 'Accepted', 'Analysis', 'Drafting', 'Report'];" in js

    def test_a_step_that_did_not_run_is_dashed_and_never_done(self):
        """§5.2's corollary. Marking a step that did not happen "done" is the
        meter's lie in a different font; marking it "pending" implies it is
        still coming. Both the class and its CSS have to exist -- a `stopped`
        class with no rule renders identically to `done`."""
        js, css = _APP_JS.read_text(), self._css()
        assert "stopped" in js and "skip.indexOf(i) !== -1" in js
        assert ".rail .s.stopped{" in css
        assert "border-top-style:dashed" in css.split(".rail .s.stopped{")[1].split("}")[0]

    def test_the_elapsed_clock_derives_from_the_page_that_posted(self):
        """Client-side and measured from THIS page's POST -- it needs no new
        field on the wire. It is not a countdown: a countdown here would be the
        meter's mistake wearing a clock."""
        js = _APP_JS.read_text()
        assert "function markRunStart() { runStartMs = Date.now(); }" in js
        assert "elapsed ${Math.floor(secs / 60)}" in js


class TestTheRetentionLedgerIsAPromise:
    """UX-WIRE U4. The ledger says what was kept and for how long. It is a
    PROMISE, and the one place in this session where a presentation change can
    become a lie: if privacy.html and the ledger disagree, one of them is
    false."""

    def _js(self) -> str:
        return _APP_JS.read_text()

    def _privacy(self) -> str:
        return (_APP_JS.parent / "privacy.html").read_text()

    def test_every_ledger_line_is_present(self):
        block = self._js().partition("function ledger() {")[2].partition("\n}")[0]
        for fact in ("Your uploaded file", "Working files", "This report",
                     "The analysis trace", "One log line", "This machine’s form values"):
            assert fact in block, f"ledger is missing its line for {fact}"
        assert "60 minutes" in block

    def test_the_two_flipping_lines_come_from_what_was_submitted(self):
        """Lines 4 and 6 flip on choices the analyst made on the form. Rendered
        from what was submitted, never assumed -- and captured at POST rather
        than read off the checkboxes when the card happens to render."""
        js = self._js()
        block = js.partition("function ledger() {")[2].partition("\n}")[0]
        assert "RETAINED.trace" in block and "RETAINED.remember" in block
        assert "RETAINED.trace = !!(form.elements.retain_trace" in js
        assert "RETAINED.remember = !!(rememberBox" in js

    def test_the_log_line_still_promises_no_ip(self):
        """The v6-A rule, restated where an analyst reads it. No request-level
        IP logging, application OR proxy -- and no card may imply a 'split'
        where the proxy holds them, because that split does not exist.

        Matched on collapsed whitespace, which is BILL-1's house rule and this
        pin is why it exists: the needle used to carry the source's own line
        break, so Session TIDY-1 adding "kept for 14 days" to the front of the
        row re-flowed the paragraph and broke a test about IP addresses. Where
        a template literal happens to wrap is not the promise.
        """
        block = re.sub(
            r"\s+", " ",
            self._js().partition("function ledger() {")[2].partition("\n}")[0])
        assert "No IP address, no filename, no machine name." in block
        for implied in ("proxy", "access log", "web server"):
            assert implied not in block.lower(), f"the ledger implies {implied}"

    def test_the_trace_line_is_true_on_the_paths_that_do_not_complete(self):
        """A trace is created during drafting REGARDLESS of consent -- consent
        gates RETENTION at completion, not creation. On the ordinary path it
        goes with the working files. But a STRANDED job never completes: the
        sweeper deliberately leaves its directory alone while an abandoned
        worker may still be writing into it, so anything there survives until a
        later sweep, up to one TTL (jobs.py:252-267, S7-ACCEPT F-3).

        So the line must be true on BOTH paths, must not say "the moment" about
        a path that never completes, and must not imply the tick is what causes
        a trace to be written."""
        block = self._js().partition("function ledger() {")[2].partition("\n}")[0]
        assert "cleared by the cleanup sweep within the hour" in block
        assert "when the analysis completes" in block
        assert "the moment" not in block, "the ledger claims immediacy it cannot keep"

    def test_privacy_and_the_ledger_do_not_contradict_each_other(self):
        """P2. Two documents that drift are one document that is false."""
        privacy = self._privacy()
        assert "unlinked as soon as it has been read" in privacy
        assert "cleared by\n    a cleanup sweep" in privacy
        assert "60 minutes" in privacy
        # consent gates RETENTION, not creation -- said on both surfaces
        assert "is not what causes one to be written" in privacy
        # and neither surface claims an immediacy the failure path cannot keep
        assert "the moment analysis finished" not in privacy

    def test_the_report_is_offered_twice_because_the_endpoint_is_idempotent(self):
        """hotfix-1 made report.pdf idempotent within the TTL -- viewing, saving
        and refreshing all work -- and the card was spending only half of it by
        offering one link. A browser's inline PDF viewer issues follow-up and
        ranged GETs on separate connections; that is the bug this pays off."""
        card = self._js().partition("function readyCard(")[2].partition("\n}")[0]
        assert card.count("/report.pdf") == 2
        assert "Open the report" in card and "Download PDF" in card
        assert "download>" in card                       # the save half
        assert 'target="_blank"' in card                 # the view half

    def test_degraded_says_which_kind_of_degraded(self):
        """`degraded_reason` has been on the wire since app.py:987-988 and the
        card rendered ONE sentence for both values. They are different
        situations to the person deciding what to do next: a spend cap clears
        tomorrow, a draft failure may not."""
        card = self._js().partition("function readyCard(")[2].partition("\n}")[0]
        assert "data.degraded_reason === 'spend_budget'" in card
        assert "resets tomorrow" in card
        assert "may not</b> clear by itself" in card or "may not clear by itself" in card

    def test_a_partial_result_does_not_present_as_a_complete_one(self):
        card = self._js().partition("function readyCard(")[2].partition("\n}")[0]
        assert "with an exclusion" in card


class TestClientPolicyConstants:
    """The numbers the loop is built on, asserted where a future edit would
    otherwise change them silently."""

    def _js(self) -> str:
        return _APP_JS.read_text()

    def test_poll_interval_is_at_least_2500ms(self):
        value = int(re.search(r"const POLL_INTERVAL_MS = (\d+)", self._js()).group(1))
        assert value >= 2500

    def test_confirm_interval_is_20s(self):
        assert "const POLL_CONFIRM_MS = 20000;" in self._js()

    def test_backoff_schedule_and_attempt_budget(self):
        js = self._js()
        assert "const POLL_BACKOFF_MS = [3000, 6000, 12000];" in js
        attempts = int(re.search(r"const POLL_MAX_FAILURES = (\d+)", js).group(1))
        assert 6 <= attempts <= 10

    def test_the_client_size_limit_matches_the_server(self):
        """A client-side pre-check is only kind if it agrees with the server.
        This is the pin that stops the two drifting."""
        js_limit = int(re.search(r"const MAX_UPLOAD_BYTES = (\d+)", self._js()).group(1))
        assert js_limit == load_config("webapp")["max_upload_bytes"]

    def test_network_error_copy_is_reserved_for_a_failed_fetch(self):
        """'Network error' must mean exactly one thing: the request never
        reached the server. Exactly two sites say it — the upload and the
        confirmation — and both tell the analyst what to do next."""
        js = self._js()
        assert js.count("Network error") == 2
        for fn in ("function offlineCard()", "function confirmOfflineCard(jobId)"):
            card = js.partition(fn)[2].partition("\nfunction ")[0]
            assert "Network error" in card
            assert "never reached" in card
            assert "Try again" in card or "Check again" in card
        # and no transient path uses it: not the poll loop, not a status code
        poll = js.partition("function pollJob(jobId)")[2]
        assert "Network error" not in poll
        for card in ("transientCard", "busyCard", "lostContactCard", "expiredCard"):
            body = js.partition(f"function {card}(")[2].partition("\nfunction ")[0]
            assert "Network error" not in body

    def test_each_status_code_has_its_own_card(self):
        js = self._js()
        submit = js.partition("async function submitJob()")[2].partition("\nform.addEventListener")[0]
        assert "res.status === 401" in submit and "inviteCard()" in submit
        assert "res.status === 413" in submit and "sizeCard(" in submit
        assert "res.status === 429" in submit and "busyCard(" in submit
        assert "res.status >= 500" in submit and "transientCard()" in submit

    def test_the_generic_catch_is_gone(self):
        """The old handler turned every non-2xx into one card that said
        'Upload failed (500)'. Nothing should reintroduce it."""
        assert "Upload failed (" not in self._js()


# ══════════════════════════════════════════════════════════════════════════
# 3 · Server limits
# ══════════════════════════════════════════════════════════════════════════
class TestStatusPollBudget:
    def test_status_gets_have_their_own_window(self):
        """A client polling its own job at our own documented interval must not
        exhaust the general per-IP budget: 60/min is 2.5 minutes of polling."""
        with TestClient(_app(ip_requests_per_minute=3, ip_status_requests_per_minute=50)) as client:
            codes = [client.get("/api/jobs/whatever").status_code for _ in range(10)]
        assert 429 not in codes            # the general window never bit
        assert set(codes) == {404}         # unknown job, but reached the handler

    def test_the_status_window_still_has_a_ceiling(self):
        with TestClient(_app(ip_status_requests_per_minute=2)) as client:
            codes = [client.get("/api/jobs/whatever").status_code for _ in range(4)]
        assert codes[:2] == [404, 404] and codes[2:] == [429, 429]

    def test_polling_does_not_consume_the_page_budget(self):
        """The concrete failure this prevents: poll a job for a minute, then be
        unable to load the privacy page because you 'made too many requests'."""
        with TestClient(_app(ip_requests_per_minute=5, ip_status_requests_per_minute=100)) as client:
            for _ in range(30):
                client.get("/api/jobs/whatever")
            assert client.get("/privacy").status_code == 200

    def test_report_downloads_are_not_status_requests(self):
        """A ranged/duplicated PDF fetch is a real transfer, not a poll — it
        stays on the general budget."""
        assert is_status_request("GET", "/api/jobs/abc") is True
        assert is_status_request("GET", "/api/jobs/abc/report.pdf") is False
        assert is_status_request("POST", "/api/jobs") is False

    def test_job_post_limits_are_unchanged(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        with TestClient(_app(ip_job_posts_per_hour=1, ip_requests_per_minute=1000)) as client:
            first = _post_job(client, csv_path)
            second = _post_job(client, csv_path)
        assert first.status_code == 202 and second.status_code == 429


class TestGuardRetryAfter:
    """DEP-6. Both guard 503s used to say "try again shortly" and carry NO
    Retry-After, leaving the client with nothing to count down. The per-IP 429
    family is the one rejection the backend already told the whole truth about
    (hardening.py computes the seconds until this IP's oldest entry leaves the
    window) — the asymmetry was a gap, not a decision.

    The rule both follow: a Retry-After is only worth emitting if waiting that
    long actually improves the odds, and neither may pretend to more precision
    than it has."""

    def test_the_queue_guard_states_a_wait_derived_from_measured_durations(self):
        state = _bare_state()
        # with nothing measured yet it falls back to the figure the product
        # already quotes analysts ("about a minute"), not to an invented one
        assert state.queue_retry_after(2) == 60          # 60s * 2 pending / 2 permits
        for _ in range(5):
            state.record_job_duration(10_000.0)          # 10s jobs, measured
        assert state.queue_retry_after(2) == 10          # 10s * 2 / 2
        assert state.queue_retry_after(20) == 100        # 10s * 20 / 2

    def test_the_queue_estimate_errs_long_not_short(self):
        """`pending` counts what pending_job_count() counts, which INCLUDES
        awaiting_confirm jobs that are not competing for a worker (DEP-3's
        caveat). That over-counts, so the estimate over-states the wait — the
        right direction to be wrong in. A Retry-After that expires into a
        second refusal teaches the analyst the numbers are decorative."""
        state = _bare_state()
        for _ in range(3):
            state.record_job_duration(20_000.0)
        assert state.queue_retry_after(4) >= state.queue_retry_after(2)

    def test_both_retry_afters_stay_inside_what_the_client_will_honour(self):
        """The client caps at MAX_RETRY_AFTER_S; a larger value would be
        silently truncated there and the two would disagree about what was
        promised."""
        cap = int(re.search(r"MAX_RETRY_AFTER_S = (\d+)", _APP_JS.read_text()).group(1))
        state = _bare_state()
        state.record_job_duration(600_000.0)              # a pathological job
        assert state.queue_retry_after(20) <= cap
        assert state.disk_retry_after() <= cap
        assert state.queue_retry_after(1) >= 5            # never a pointless 1s

    def test_the_disk_guard_is_a_fixed_conservative_value(self):
        """Fixed on purpose. Disk is freed by the TTL sweep reclaiming somebody
        else's aged-out report, which this process cannot see coming — there is
        no honest way to compute the moment, so it errs long."""
        state = _bare_state()
        assert state.disk_retry_after() == 300

    def test_a_full_disk_503_carries_the_header(self, tmp_path):
        app = _app(disk_min_free_mb=10**9)                # guaranteed to refuse
        with TestClient(app) as client:
            csv = tmp_path / "s.csv"
            csv.write_text("freq_hz,amplitude\n10,1\n")
            r = _post_job(client, csv)
        assert r.status_code == 503
        assert int(r.headers["Retry-After"]) == 300

    def test_a_full_queue_503_carries_the_header(self, tmp_path):
        app = _app(queue_depth_max=0)                     # guaranteed to refuse
        with TestClient(app) as client:
            csv = tmp_path / "s.csv"
            csv.write_text("freq_hz,amplitude\n10,1\n")
            r = _post_job(client, csv)
        assert r.status_code == 503
        assert int(r.headers["Retry-After"]) >= 5


class TestTheBusyCardOnlyCountsDownWhatItWasTold:
    """U7. The two halves of the busy card differ because the TRUTH differs,
    not as a style choice. With a Retry-After the wait is a number the server
    computed and the page counts it down; without one, "shortly" is the only
    honest wording. A timer invented to fill the gap — expiring into a second
    refusal — is what teaches an analyst that the numbers here are decorative."""

    def test_the_client_distinguishes_a_stated_wait_from_a_fallback(self):
        js = _APP_JS.read_text()
        fn = js.partition("function retryAfterSeconds(res)")[2].partition("\n}")[0]
        assert "return null" in fn, "an absent header must be distinguishable"
        card = js.partition("function busyCard(seconds)")[2].partition("\n}")[0]
        assert "typeof seconds === 'number'" in card
        assert "we made up" in card          # says why there is no timer

    def test_a_503_now_reaches_the_busy_card_not_the_generic_hiccup(self):
        js = _APP_JS.read_text()
        assert "res.status === 429 || res.status === 503" in js
        assert "if (stated !== null) startCountdown" in js, (
            "a countdown must only run on a wait the server actually stated")


class TestRetryAfter:
    def test_every_429_says_when_to_come_back(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        with TestClient(_app(ip_requests_per_minute=1)) as client:
            client.get("/privacy")
            page = client.get("/privacy")
        assert page.status_code == 429
        assert 1 <= int(page.headers["Retry-After"]) <= 60

        with TestClient(_app(ip_status_requests_per_minute=1)) as client:
            client.get("/api/jobs/x")
            status = client.get("/api/jobs/x")
        assert status.status_code == 429 and int(status.headers["Retry-After"]) >= 1

        with TestClient(_app(per_code_daily_jobs=0)) as client:
            daily = _post_job(client, csv_path)
        assert daily.status_code == 429 and int(daily.headers["Retry-After"]) > 60

    def test_retry_after_shrinks_as_the_window_drains(self, monkeypatch):
        limiter = IpRateLimiter(requests_per_minute=1, job_posts_per_hour=1)
        clock = {"t": 1000.0}
        monkeypatch.setattr(IpRateLimiter, "_now", staticmethod(lambda: clock["t"]))
        limiter.check_request("1.2.3.4")
        assert limiter.retry_after("1.2.3.4") == 60
        clock["t"] += 45
        assert limiter.retry_after("1.2.3.4") == 15


# ══════════════════════════════════════════════════════════════════════════
# 4 · Budget fairness
# ══════════════════════════════════════════════════════════════════════════
class TestBudgetFairness:
    def _spent(self, app, label="engineer-1") -> int:
        return app.state.vib.rate_limiter._per_code[label]

    def test_an_accepted_job_spends_exactly_one(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app()
        with TestClient(app) as client:
            assert _post_job(client, csv_path).status_code == 202
        assert self._spent(app) == 1

    @pytest.mark.parametrize("scenario", ["bad_code", "oversize", "unsupported", "bad_direction"])
    def test_a_rejected_attempt_costs_nothing(self, scenario, tmp_path):
        """401 / 413 / 415 / 400 all burn zero daily budget: they cost us no
        parse, no model call and no job, so they cost the analyst nothing."""
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app(max_upload_bytes=50)
        with TestClient(app) as client:
            if scenario == "bad_code":
                response = _post_job(client, csv_path, code="not-a-code")
                expected = 401
            elif scenario == "oversize":
                response = _post_job(client, csv_path)
                expected = 413
            elif scenario == "unsupported":
                weird = tmp_path / "thing.zip"
                weird.write_bytes(b"PK\x03\x04")
                response = _post_job(client, weird)
                expected = 415
            else:
                response = _post_job(client, csv_path, direction="sideways")
                expected = 400
        assert response.status_code == expected, response.text
        assert self._spent(app) == 0, f"{scenario} burned daily budget"

    def test_a_full_day_still_refuses(self, tmp_path):
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app(per_code_daily_jobs=1)
        with TestClient(app) as client:
            assert _post_job(client, csv_path).status_code == 202
            second = _post_job(client, csv_path)
        assert second.status_code == 429
        assert self._spent(app) == 1  # the refusal itself did not add one

    def test_the_per_ip_post_bucket_still_charges_for_the_attempt(self, tmp_path):
        """Anti-abuse is the other way round: an attacker firing bad requests
        must still be throttled, so the per-IP POST counter is pre-validation."""
        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app(ip_job_posts_per_hour=2, ip_requests_per_minute=1000)
        with TestClient(app) as client:
            assert _post_job(client, csv_path, code="not-a-code").status_code == 401
            assert _post_job(client, csv_path, code="not-a-code").status_code == 401
            assert _post_job(client, csv_path).status_code == 429   # bucket spent by the attempts
        assert self._spent(app) == 0                                # and no daily budget burned


# ══════════════════════════════════════════════════════════════════════════
# 5 · The client size pre-check
# ══════════════════════════════════════════════════════════════════════════
class TestClientSizePreCheck:
    def test_the_form_refuses_an_oversize_file_before_uploading(self):
        js = _APP_JS.read_text()
        check = js.partition("function oversizeFile()")[2].partition("\n}")[0]
        assert "file.size > MAX_UPLOAD_BYTES" in check
        submit = js.partition("async function submitJob()")[2].partition("\nform.addEventListener")[0]
        # the check happens BEFORE the fetch, and returns without one
        assert submit.index("oversizeFile()") < submit.index("fetch('/api/jobs'")
        assert "show(sizeCard(tooBig.name, tooBig.size));" in submit

    def test_the_size_card_says_what_to_do(self):
        js = _APP_JS.read_text()
        card = js.partition("function sizeCard(")[2].partition("\nfunction ")[0]
        assert "Nothing was uploaded" in card
        assert "email us the file" in card

    def test_every_extra_slot_is_checked_too(self):
        js = _APP_JS.read_text()
        check = js.partition("function oversizeFile()")[2].partition("\n}")[0]
        assert "EXTRA_SLOTS" in check


# ══════════════════════════════════════════════════════════════════════════
# 6 · The TEST HARNESS itself is a protocol-honest client (hotfix-net-t)
# ══════════════════════════════════════════════════════════════════════════
class _FlakyStatusClient:
    """A TestClient wrapper that injects a 429 into the status stream.

    The server's own Retry-After for a status window is ~60s, which no test can
    wait for; injecting the response is how a 429-mid-poll is exercised in under
    a second. Everything else passes through to the real app.
    """

    def __init__(self, client: TestClient, *, fail_on: tuple[int, ...], retry_after: str = "1"):
        self._client = client
        self._fail_on = set(fail_on)
        self._retry_after = retry_after
        self.status_calls = 0
        self.served_429 = 0

    class _Rejection:
        def __init__(self, retry_after: str) -> None:
            self.status_code = 429
            self.headers = {"Retry-After": retry_after}
            self.text = '{"detail": "Too many requests"}'

        def json(self):
            return {"detail": "Too many requests"}

    def get(self, url, *args, **kwargs):
        if url.startswith("/api/jobs/") and not url.endswith("/report.pdf"):
            self.status_calls += 1
            if self.status_calls in self._fail_on:
                self.served_429 += 1
                return self._Rejection(self._retry_after)
        return self._client.get(url, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._client, name)


class TestHarnessIsProtocolHonest:
    def test_a_429_mid_poll_does_not_fail_the_test(self, tmp_path):
        """The helper must behave like the browser it stands in for: honour the
        wait and carry on. Before this, a 429 anywhere in the stream raised
        KeyError('state') and the failure pointed at the product."""
        from tests.test_webapp_e2e import _poll_until_terminal

        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app()
        with TestClient(app) as raw:
            job_id = _post_job(raw, csv_path).json()["job_id"]
            flaky = _FlakyStatusClient(raw, fail_on=(1, 2))
            data = _poll_until_terminal(flaky, job_id)
        assert flaky.served_429 == 2, "the scenario must actually have hit a 429"
        assert data["state"] in ("done", "degraded", "gate_fail"), data

    def test_the_helper_honours_retry_after_rather_than_hammering(self, tmp_path):
        from tests.test_webapp_e2e import _MAX_TEST_RETRY_AFTER_S, _poll_until_terminal

        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app()
        with TestClient(app) as raw:
            job_id = _post_job(raw, csv_path).json()["job_id"]
            flaky = _FlakyStatusClient(raw, fail_on=(1,), retry_after="1")
            started = time.monotonic()
            _poll_until_terminal(flaky, job_id)
            waited = time.monotonic() - started
        assert waited >= 1.0, "a Retry-After: 1 must actually be waited out"
        assert _MAX_TEST_RETRY_AFTER_S <= 5.0, "a test must never wait a production-length window"

    def test_an_absurd_retry_after_is_clamped_in_tests_too(self, tmp_path):
        """A 600s Retry-After must be clamped, not honoured.

        `waited` is elapsed wall clock, so it is the CLAMP plus the job's own
        duration -- and the job runs a real analysis and a real tectonic PDF
        render. Under full-suite load that job alone was measured at 36.9s while
        the budget here was 32s, which failed this test for the one reason it is
        not about (see the durations recorded beside
        test_webapp_e2e._JOB_WALLCLOCK_DEADLINE_S). The budget is now the same
        host-calibrated constant, which keeps every bit of this assertion's
        discriminating power: honouring 600s would blow a 302s bound by a factor
        of two, and there is exactly one clamped sleep here (fail_on=(1,)).
        """
        from tests.test_webapp_e2e import (
            _JOB_WALLCLOCK_DEADLINE_S,
            _MAX_TEST_RETRY_AFTER_S,
            _poll_until_terminal,
        )

        csv_path = tmp_path / "spec.csv"
        _spectrum_csv(csv_path)
        app = _app()
        with TestClient(app) as raw:
            job_id = _post_job(raw, csv_path).json()["job_id"]
            flaky = _FlakyStatusClient(raw, fail_on=(1,), retry_after="600")
            started = time.monotonic()
            _poll_until_terminal(flaky, job_id)
            waited = time.monotonic() - started
        budget = _MAX_TEST_RETRY_AFTER_S + _JOB_WALLCLOCK_DEADLINE_S
        assert budget < 600.0, "the bound must stay below the window it proves was clamped"
        assert waited < budget

    def test_a_non_200_is_a_readable_assertion_not_a_keyerror(self):
        """The failure mode this replaces: KeyError('state') three frames deep,
        blaming the product for a transport problem."""
        from tests.test_webapp_e2e import _poll_until_terminal

        with TestClient(_app()) as client:
            with pytest.raises(AssertionError) as excinfo:
                _poll_until_terminal(client, "no-such-job", timeout_s=2.0)
        message = str(excinfo.value)
        assert "404" in message and "no-such-job" in message

    def test_the_inter_poll_sleep_is_not_faster_than_a_real_client(self):
        from tests.test_webapp_e2e import _POLL_SLEEP_S

        assert _POLL_SLEEP_S >= 0.2

    def test_the_shared_factory_hands_out_unlimited_budgets(self):
        """Limiter behaviour is pinned by the tests that set caps EXPLICITLY.
        Nothing else should be asserting limits by accident, from a default."""
        cfg = _webapp_cfg()
        for key in ("ip_requests_per_minute", "ip_job_posts_per_hour",
                    "ip_status_requests_per_minute"):
            assert cfg[key] >= 1_000_000, f"{key} is not effectively unlimited for e2e tests"
        # and an explicit override still wins, which is how the limiter tests work
        assert _webapp_cfg(ip_status_requests_per_minute=2)["ip_status_requests_per_minute"] == 2

    def test_the_confirm_pause_helper_uses_the_same_poll(self):
        source = (Path(__file__).parent / "test_inference_webapp.py").read_text()
        body = source.partition("def _await_confirm(")[2]
        awaiting = body.partition("\n@pytest")[0].partition("\ndef ")[0]
        assert "_status_once" in awaiting, "the confirm helper must share the honest poll"
        assert "client.get(" not in awaiting, "no raw status read: that is the KeyError path"
