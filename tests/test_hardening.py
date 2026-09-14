"""RUN v6-A — app-layer hardening tests (webapp only, offline).

Covers the per-IP sliding-window limiter, disk guard, the TTL sweeper timer,
security headers + CSP, no-store on sensitive routes, prod-schema closure,
/healthz, the log-hygiene (no IPs) contract, and the CSP inline-JS extraction.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import time
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_webapp_e2e import _webapp_cfg
from vib_agent import __version__
from vib_agent.webapp import app as app_module
from vib_agent.webapp.app import create_app
from vib_agent.webapp.hardening import (
    HSTS_HEADER,
    NO_STORE_PREFIXES,
    SECURITY_HEADERS,
    IpRateLimiter,
    client_ip,
    request_is_https,
)

_ROOT = Path(__file__).resolve().parents[1]


def _app(**cfg_over):
    return create_app(webapp_cfg=_webapp_cfg(**cfg_over), invite_codes={"demo-code": "engineer-1"},
                      contact_email="ops@example.test")


def _tiny_csv() -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["freq_hz", "amplitude"])
    w.writerow([10.0, 0.1])
    return buf.getvalue().encode()


def _post_job(client, **form_over):
    form = {"invite_code": "demo-code", "machine_alias": "M", "rpm": "1800",
            "iso_group": "2", "iso_support": "rigid", "machine_type": "motor"}
    form.update(form_over)
    return client.post("/api/jobs", files={"file": ("upload.csv", _tiny_csv(), "text/csv")}, data=form)


# ── IpRateLimiter unit ────────────────────────────────────────────────────────
class TestIpRateLimiterUnit:
    def test_admits_up_to_limit_then_rejects(self):
        lim = IpRateLimiter(requests_per_minute=3, job_posts_per_hour=2)
        assert [lim.check_request("a") for _ in range(4)] == [True, True, True, False]

    def test_buckets_are_per_ip(self):
        lim = IpRateLimiter(requests_per_minute=1, job_posts_per_hour=1)
        assert lim.check_request("a") is True
        assert lim.check_request("a") is False
        assert lim.check_request("b") is True  # different IP unaffected

    def test_window_slides(self, monkeypatch):
        t = [1000.0]
        monkeypatch.setattr(IpRateLimiter, "_now", staticmethod(lambda: t[0]))
        lim = IpRateLimiter(requests_per_minute=1, job_posts_per_hour=1)
        assert lim.check_request("a") is True
        assert lim.check_request("a") is False
        t[0] += 61.0  # past the 60s window
        assert lim.check_request("a") is True

    def test_job_and_request_windows_are_independent(self):
        lim = IpRateLimiter(requests_per_minute=10, job_posts_per_hour=1)
        assert lim.check_job_post("a") is True
        assert lim.check_job_post("a") is False  # hourly job cap hit
        assert lim.check_request("a") is True     # global request window untouched


class TestClientIpTrust:
    def _req(self, host, xff=None):
        headers = {"x-forwarded-for": xff} if xff else {}
        return types.SimpleNamespace(client=types.SimpleNamespace(host=host), headers=headers)

    def test_loopback_peer_honors_xff_first_hop(self):
        assert client_ip(self._req("127.0.0.1", "203.0.113.9, 10.0.0.1")) == "203.0.113.9"

    def test_untrusted_peer_ignores_xff(self):
        assert client_ip(self._req("198.51.100.7", "203.0.113.9")) == "198.51.100.7"

    def test_no_xff_falls_back_to_peer(self):
        assert client_ip(self._req("198.51.100.7")) == "198.51.100.7"

    def test_missing_client_is_unknown(self):
        r = types.SimpleNamespace(client=None, headers={})
        assert client_ip(r) == "unknown"


# ── per-IP limiting through the middleware ────────────────────────────────────
class TestPerIpMiddleware:
    def test_global_window_trips_429(self):
        with TestClient(_app(ip_requests_per_minute=3)) as c:
            codes = [c.get("/privacy").status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]

    def test_browser_429_is_html_card_api_429_is_json(self):
        # Both windows are set to 1: a job-status GET now spends from its OWN
        # budget (hotfix-net), so tripping it takes its own cap, not the
        # general one. The claim under test is unchanged — an API path gets a
        # JSON 429 and a browser path gets the HTML card.
        with TestClient(_app(ip_requests_per_minute=1, ip_status_requests_per_minute=1)) as c:
            c.get("/privacy")  # consume the single general slot
            html = c.get("/privacy")
            c.get("/api/jobs/whatever")   # consume the single status slot
            api = c.get("/api/jobs/whatever")
        assert html.status_code == 429 and "text/html" in html.headers["content-type"]
        assert "Too many requests" in html.text and not html.text.lstrip().startswith("{")
        assert api.status_code == 429 and api.json()["detail"].startswith("Too many requests")
        assert html.headers["Retry-After"].isdigit() and api.headers["Retry-After"].isdigit()

    def test_second_ip_unaffected(self):
        app = _app(ip_requests_per_minute=1)
        with TestClient(app, client=("10.0.0.1", 1)) as c1:
            assert c1.get("/privacy").status_code == 200
            assert c1.get("/privacy").status_code == 429
        with TestClient(app, client=("10.0.0.2", 1)) as c2:
            assert c2.get("/privacy").status_code == 200  # distinct IP, fresh bucket

    def test_job_post_hourly_cap(self, monkeypatch):
        # generous global window, tiny job window -> the job bucket is what trips.
        app = _app(ip_requests_per_minute=1000, ip_job_posts_per_hour=1)
        # avoid the disk/parse cost: a huge disk floor is not it -- just let the
        # first POST run (it 202s) and assert the SECOND is 429 from the job bucket.
        with TestClient(app) as c:
            r1 = _post_job(c)
            r2 = _post_job(c)
        assert r1.status_code == 202
        assert r2.status_code == 429

    def test_healthz_is_rate_limit_exempt(self):
        with TestClient(_app(ip_requests_per_minute=1)) as c:
            assert [c.get("/healthz").status_code for _ in range(5)] == [200] * 5


# ── security headers + CSP + no-store ─────────────────────────────────────────
class TestSecurityHeaders:
    def test_all_headers_present_on_html_and_api(self):
        with TestClient(_app()) as c:
            for path in ("/", "/privacy", "/healthz", "/api/jobs/none"):
                r = c.get(path)
                for h, v in SECURITY_HEADERS.items():
                    assert r.headers.get(h) == v, f"{path} missing {h}"

    def test_csp_exact_string(self):
        with TestClient(_app()) as c:
            csp = c.get("/").headers["content-security-policy"]
        assert csp == (
            "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
            "object-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        )

    def test_csp_still_forbids_inline_script(self):
        """The directive that does the work is the ABSENCE of script-src.

        With no script-src, scripts fall back to `default-src 'self'`, which
        permits `/app.js` and forbids an inline `<script>` body and every
        `on*=` handler. SEC-2 added three directives around it; this pin says
        in one line that none of them accidentally loosened the one that
        matters. `'unsafe-inline'` may appear exactly once, in style-src, and
        `'unsafe-eval'` never.
        """
        with TestClient(_app()) as c:
            csp = c.get("/").headers["content-security-policy"]
        assert "script-src" not in csp, (
            "script-src now exists; whatever it says, the inline-script "
            "guarantee is no longer 'it inherits default-src'"
        )
        assert csp.count("'unsafe-inline'") == 1 and "style-src 'self' 'unsafe-inline'" in csp
        assert "'unsafe-eval'" not in csp
        for directive in ("base-uri 'self'", "frame-ancestors 'none'", "object-src 'none'"):
            assert directive in csp, f"SEC-2 directive lost: {directive}"

    def test_the_frame_headers_agree(self):
        """Both are sent, and they must not contradict each other: a browser
        that honours only X-Frame-Options and one that honours only CSP must
        reach the same answer."""
        with TestClient(_app()) as c:
            headers = c.get("/").headers
        assert headers["x-frame-options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["content-security-policy"]

    def test_permissions_policy_denies_every_named_feature(self):
        with TestClient(_app()) as c:
            policy = c.get("/").headers["permissions-policy"]
        for feature in ("camera", "microphone", "geolocation", "payment", "usb",
                        "accelerometer", "gyroscope", "magnetometer"):
            assert f"{feature}=()" in policy, feature
        # An allowlist that is not empty would be the bug: `camera=(self)`
        # grants the feature to this document.
        assert "(self)" not in policy and "(*)" not in policy

    def test_no_store_only_on_sensitive_routes(self):
        with TestClient(_app()) as c:
            assert c.get("/healthz").headers.get("cache-control") == "no-store"
            assert c.get("/api/jobs/none").headers.get("cache-control") == "no-store"
            # marketing/static routes stay cacheable
            assert c.get("/").headers.get("cache-control") is None
            assert c.get("/style.css").headers.get("cache-control") is None
            assert c.get("/app.js").headers.get("cache-control") is None

    def test_no_store_prefixes_are_the_sensitive_set(self):
        """A strict tuple, not a membership check, so gaining a prefix is a
        decision somebody made rather than one that happened. Session DB-1
        added `/api/store`: the import endpoint answers with account state, the
        list is matched by `startswith` so a new /api route inherits nothing,
        and this test is where saying so is required."""
        assert NO_STORE_PREFIXES == ("/api/jobs", "/api/store", "/healthz")


# ── HSTS: opt-in, and only over TLS ──────────────────────────────────────────
class TestHsts:
    """SEC-2. Off unless a deployment says otherwise, because HSTS cannot be
    withdrawn: a browser that has seen it refuses plain HTTP to the host for a
    year. `deploy/Caddyfile` carries the same value commented out with the same
    warning; `HSTS_ENABLED` is now the single place the decision is made."""

    def _get(self, env, *, proto=None, monkeypatch=None):
        monkeypatch.delenv("HSTS_ENABLED", raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        headers = {"X-Forwarded-Proto": proto} if proto else {}
        # The app is built AFTER the environment is set: the opt-in is resolved
        # at startup, the way every other deployment decision here is.
        with TestClient(_app()) as c:
            return c.get("/", headers=headers)

    def test_absent_by_default(self, monkeypatch):
        response = self._get({}, proto="https", monkeypatch=monkeypatch)
        assert "strict-transport-security" not in response.headers, (
            "HSTS is being sent without a deployment opting in -- that is a "
            "year-long commitment made by accident"
        )

    def test_sent_when_enabled_over_tls(self, monkeypatch):
        response = self._get({"HSTS_ENABLED": "true"}, proto="https", monkeypatch=monkeypatch)
        assert response.headers["strict-transport-security"] == HSTS_HEADER

    def test_not_sent_over_plain_http_even_when_enabled(self, monkeypatch):
        """A browser ignores the header on a plaintext response, so sending it
        on http://localhost would only mislead the one person who reads headers
        by hand."""
        response = self._get({"HSTS_ENABLED": "true"}, monkeypatch=monkeypatch)
        assert "strict-transport-security" not in response.headers

    def test_only_true_enables_it(self, monkeypatch):
        """`RETAIN_TRACES`' precedent, and deliberately strict: `1`, `yes` and
        `TRUE` do not turn on a control that cannot be turned off."""
        for value in ("1", "yes", "on", ""):
            response = self._get(
                {"HSTS_ENABLED": value}, proto="https", monkeypatch=monkeypatch
            )
            assert "strict-transport-security" not in response.headers, value

    def test_an_untrusted_peer_cannot_forge_the_scheme(self):
        """`X-Forwarded-Proto` is honoured only from a trusted local proxy --
        `client_ip`'s rule, for the same reason: a header a caller controls must
        not decide anything on its own."""
        request = types.SimpleNamespace(
            client=types.SimpleNamespace(host="203.0.113.9"),
            headers={"x-forwarded-proto": "https"},
            url=types.SimpleNamespace(scheme="http"),
        )
        assert request_is_https(request) is False
        trusted = types.SimpleNamespace(
            client=types.SimpleNamespace(host="127.0.0.1"),
            headers={"x-forwarded-proto": "https"},
            url=types.SimpleNamespace(scheme="http"),
        )
        assert request_is_https(trusted) is True



# ── prod-schema closure + healthz ─────────────────────────────────────────────
class TestSchemaAndHealth:
    def test_docs_redoc_openapi_all_404(self):
        with TestClient(_app()) as c:
            assert c.get("/docs").status_code == 404
            assert c.get("/redoc").status_code == 404
            assert c.get("/openapi.json").status_code == 404

    def test_healthz_shape(self):
        with TestClient(_app()) as c:
            body = c.get("/healthz").json()
        assert body == {"status": "ok", "version": __version__, "queue_depth": 0}

    def test_prod_mode_logged(self, caplog):
        with caplog.at_level(logging.INFO, logger="vib_agent.webapp"):
            with TestClient(create_app(webapp_cfg=_webapp_cfg(), invite_codes={"demo-code": "e1"}, app_env="prod")):
                pass
        assert any("mode=prod" in r.message for r in caplog.records)


# ── disk guard ────────────────────────────────────────────────────────────────
class TestDiskGuard:
    def test_low_disk_returns_503(self, monkeypatch):
        monkeypatch.setattr(app_module.shutil, "disk_usage",
                            lambda _p: types.SimpleNamespace(total=10**9, used=10**9, free=1))
        with TestClient(_app()) as c:
            r = _post_job(c)
        assert r.status_code == 503 and "storage is temporarily full" in r.json()["detail"]

    def test_ample_disk_allows_job(self, monkeypatch):
        monkeypatch.setattr(app_module.shutil, "disk_usage",
                            lambda _p: types.SimpleNamespace(total=10**12, used=0, free=10**12))
        with TestClient(_app()) as c:
            assert _post_job(c).status_code == 202


# ── TTL sweeper timer (the loop, not a direct sweep_expired call) ─────────────
class TestSweeperTimer:
    def test_background_timer_purges_without_a_request(self):
        """A FINISHED job past its TTL is a genuine expiry: files gone, entry
        dropped, later polls 404. Unchanged by S7 -- pinned through the real
        lifespan loop rather than a direct sweep_expired() call."""
        app = create_app(webapp_cfg=_webapp_cfg(sweep_interval_s=0.05, job_ttl_minutes=60),
                         invite_codes={"demo-code": "e1"})
        with TestClient(app):  # lifespan starts the sweep loop
            reg = app.state.vib.registry
            job = reg.create(code_label="e1", file_size=1)
            job.state = "done"
            job.created_at -= 61 * 60  # past TTL
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and reg.get(job.id) is not None:
                time.sleep(0.05)
            assert reg.get(job.id) is None, "sweep loop did not purge the expired job"

    def test_the_timer_strands_a_non_terminal_job_instead_of_dropping_it(self):
        """S7 / DEP-4b, through the same loop. An expired job that is still
        `queued` is NOT an expiry -- it is a job that stopped without finishing.
        Dropping it made it byte-identical to a finished report that aged out,
        and the client's expired card then told the analyst their report had
        been kept for 60 minutes and deleted, of which every clause was false."""
        app = create_app(webapp_cfg=_webapp_cfg(sweep_interval_s=0.05, job_ttl_minutes=60),
                         invite_codes={"demo-code": "e1"})
        with TestClient(app) as client:
            reg = app.state.vib.registry
            job = reg.create(code_label="e1", file_size=1)
            job.created_at -= 61 * 60          # past TTL, still `queued`
            job_dir = job.job_dir
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and job.state == "queued":
                time.sleep(0.05)
            assert job.state == "error", "the sweep loop left a stranded job non-terminal"
            assert job.error_code == "timeout"
            # the entry SURVIVES, so the analyst gets the truth rather than a 404
            assert reg.get(job.id) is not None
            body = client.get(f"/api/jobs/{job.id}").json()
            assert body["state"] == "error" and body["failure_kind"] == "server_error"
            # and the sweep did not touch its files
            assert job_dir.exists(), "the sweeper deleted a live job's directory"


# ── log hygiene: no IPs in the app log ───────────────────────────────────────
class TestLogHygiene:
    def test_429_burst_logs_no_ip(self, caplog):
        with caplog.at_level(logging.INFO, logger="vib_agent.webapp"):
            with TestClient(_app(ip_requests_per_minute=1)) as c:
                for _ in range(4):
                    c.get("/privacy", headers={"X-Forwarded-For": "203.0.113.77"})
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "203.0.113.77" not in joined
        assert "x-forwarded-for" not in joined.lower()


# ── CSP inline-JS extraction ─────────────────────────────────────────────────
class TestJsExtraction:
    def test_index_has_no_inline_script_body(self):
        with TestClient(_app()) as c:
            html = c.get("/").text
        assert '<script src="/app.js"' in html
        assert "document.getElementById('upload-form')" not in html  # body moved out

    def test_app_js_served_and_reads_contact_meta(self):
        with TestClient(_app()) as c:
            html = c.get("/").text
            js = c.get("/app.js")
        assert 'name="contact-email" content="ops@example.test"' in html
        assert js.status_code == 200 and "javascript" in js.headers["content-type"]
        assert 'meta[name="contact-email"]' in js.text
        assert "{{contact_email}}" not in js.text  # placeholder never leaks into static JS

    def test_no_served_page_carries_inline_script(self):
        """SEC-2: the CSP claim, checked against every HTML page, not one.

        `default-src 'self'` forbids an inline `<script>` body and every `on*=`
        handler attribute. UX-1 added client-side routing to this page and
        Session E added the multi-file form, so the page most likely to acquire
        an inline handler is the one that changes most -- and a CSP violation
        does not fail a test, it silently stops working in a browser. Measured
        here rather than read.
        """
        # Session PAGES appended the four marketing pages. This tuple is
        # hard-coded, so a new page is NOT swept until it is named here --
        # and a CSP violation does not fail a test, it silently stops
        # working in a browser.
        pages = ("/", "/privacy", "/terms", "/validation", "/field-validation-results.md",
                 "/product", "/how-it-works", "/pricing", "/demo")
        with TestClient(_app()) as c:
            served = {path: c.get(path).text for path in pages}
        for path, html in served.items():
            assert html.strip(), f"{path} served nothing -- the pin would be vacuous"
            for tag in re.finditer(r"<script\b[^>]*>(.*?)</script>", html, re.S | re.I):
                assert not tag.group(1).strip(), (
                    f"{path} has an inline <script> body; CSP `default-src 'self'` "
                    "blocks it, so it would silently not run in a browser"
                )
            handler = re.search(r"<[^>]*\son[a-z]+\s*=\s*[\"'][^\"']*[\"']", html, re.I)
            assert handler is None, (
                f"{path} has an inline event handler ({handler.group(0)[:60]!r}); "
                "CSP blocks those too"
            )
            assert "javascript:" not in html.lower(), f"{path} has a javascript: URL"
