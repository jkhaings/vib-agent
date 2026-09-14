"""RUN v6-A production hardening — app-layer helpers (webapp only).

Pure: no pdm_core, no thresholds, no I/O. An in-memory per-IP sliding-window
rate limiter plus the security-header / CSP constants the middleware applies.
Every tunable is injected (config/env) so tests build a tiny window and simulate
distinct client IPs. NO IP is ever logged, anywhere: not here, not by the
middleware, not by uvicorn (started with --no-access-log) and not by Caddy
(deploy/Caddyfile sets no `log` directive). The app log stays label-only. This
limiter is the abuse mitigation, and it is stateless by design — addresses live
in memory only, inside the sliding window. Do not describe a "split" where the
proxy holds IPs; that split does not exist (see CLAUDE.md).
"""

from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from typing import NamedTuple

# Security headers applied to EVERY response (setdefault — never clobbers a
# header a handler set deliberately).
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    # Nothing served here uses a device capability, so every one of them is
    # switched off by name (SEC-2). An empty allowlist `()` denies the feature
    # to this document and to anything it embeds; a browser that does not know
    # a feature ignores that entry and honours the rest.
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    # default-src 'self' blocks inline <script> (JS lives in /app.js) and
    # cross-origin loads. style-src adds 'unsafe-inline' for the small set of
    # self-authored, esc()'d style="" attributes (no user markup ever reaches
    # them); img-src allows the data: URIs a report/masthead may embed.
    #
    # SEC-2 added the three directives that do NOT fall back to default-src, so
    # until now nothing constrained them at all:
    #   base-uri        a single injected <base> would silently repoint every
    #                   relative URL on the page, /app.js included
    #   frame-ancestors X-Frame-Options' modern twin. Both are sent: the legacy
    #                   header still covers clients that never learned CSP2
    #   object-src      plugin content. Reports open as top-level documents
    #                   (<a href>, never <embed>), so 'none' costs nothing
    #
    # form-action is deliberately ABSENT, and that is a decision rather than an
    # omission: `POST /auth/google` answers with a redirect to Google, browsers
    # have historically checked form-action against redirect targets, and the
    # directive would buy nothing here — no page renders user-authored markup,
    # so there is no injected form for it to stop. See docs/SECURITY.md §7.
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; frame-ancestors 'none'; "
        "object-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
    ),
}

#: Sent only when a deployment opts in AND the request arrived over TLS.
#: One year with subdomains — the exact value `deploy/Caddyfile` carries
#: commented out, so turning it on here and turning it on there cannot
#: disagree.
HSTS_HEADER = "max-age=31536000; includeSubDomains"

# Response paths that must never be cached (job status / report / health, and
# DB-1's store import). Matched with `startswith`, so a NEW /api route inherits
# nothing from this list -- it has to be named here, which is why it is a list
# and not a rule about "/api".
NO_STORE_PREFIXES: tuple[str, ...] = ("/api/jobs", "/api/store", "/healthz")

# Session AUTH-1's routes are NOT in that tuple, and that is deliberate rather
# than an oversight. The list is asserted by strict equality in
# tests/test_hardening.py precisely so a new route cannot join the sensitive set
# without somebody saying so; the auth handlers set `Cache-Control: no-store` on
# their own responses instead, which the middleware leaves alone (it only
# OVERWRITES the header for paths in the tuple above). Same guarantee, declared
# where the route is.

#: `/auth/*` requests per IP per 15 minutes. Not in `config/webapp.json`: that
#: file is limits-for-analysis and this is anti-abuse, and it follows the
#: IP_REQUESTS_PER_MINUTE precedent of being an env override over a constant.
AUTH_REQUESTS_PER_15MIN = 20

#: Sign-in links per ADDRESS per hour, keyed on a hash of it so no email is ever
#: held here in the clear. The per-IP window above does not cover this: one
#: mailbox must not be floodable from many addresses.
MAGIC_LINKS_PER_EMAIL_PER_HOUR = 5


def is_status_request(method: str, path: str) -> bool:
    """A browser POLLING its own job: GET /api/jobs/{id}, not the report.

    These get their own, much larger per-IP budget. A client following its own
    protocol -- one poll every 2.5s for a job that legitimately takes a minute --
    must not be able to rate-limit ITSELF into failure and then be told it is
    abusing the service. Job POSTs keep their own tight bucket, which is where
    abuse actually costs us."""
    if method.upper() != "GET" or not path.startswith("/api/jobs/"):
        return False
    return not path.endswith("/report.pdf")

# request.client peers we trust to have set X-Forwarded-For (Caddy fronting
# uvicorn on loopback; "testclient" is Starlette's synthetic test peer).
_TRUSTED_PROXY_PEERS = frozenset({"127.0.0.1", "::1", "testclient"})


def client_ip(request) -> str:
    """The rate-limiting key: the direct peer, or the first X-Forwarded-For hop
    when the peer is a trusted local proxy. Behind Caddy on 127.0.0.1 this
    recovers the real client IP; a direct (untrusted) peer's XFF is ignored so a
    caller cannot spoof its way into a fresh bucket."""
    peer = request.client.host if request.client else "unknown"
    if peer in _TRUSTED_PROXY_PEERS:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip() or peer
    return peer


class IpRateLimiter:
    """Two per-IP sliding windows: a global request window and a job-POST
    window. Memory is bounded — empty deques are pruned and, past a hard IP
    cap, the least-recently-active IPs are dropped (the cap only bites under a
    distributed flood, which is Caddy/fail2ban's job anyway)."""

    def __init__(
        self, *, requests_per_minute: int, job_posts_per_hour: int,
        status_requests_per_minute: int = 300, max_ips: int = 50_000,
        auth_requests_per_15min: int = AUTH_REQUESTS_PER_15MIN,
        magic_links_per_hour: int = MAGIC_LINKS_PER_EMAIL_PER_HOUR,
    ) -> None:
        self.requests_per_minute = int(requests_per_minute)
        self.job_posts_per_hour = int(job_posts_per_hour)
        self.status_requests_per_minute = int(status_requests_per_minute)
        self.auth_requests_per_15min = int(auth_requests_per_15min)
        self.magic_links_per_hour = int(magic_links_per_hour)
        self._max_ips = max_ips
        self._req: dict[str, deque[float]] = defaultdict(deque)
        self._job: dict[str, deque[float]] = defaultdict(deque)
        self._status: dict[str, deque[float]] = defaultdict(deque)
        self._auth: dict[str, deque[float]] = defaultdict(deque)
        # Keyed on sha256(address), never the address (AUTH-1). This class has
        # no idea what it is counting, which is what makes it safe to keep the
        # counter in a module whose whole claim is that it stores no identities.
        self._magic: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    @staticmethod
    def _trim(dq: deque[float], now: float, window_s: float) -> None:
        cutoff = now - window_s
        while dq and dq[0] <= cutoff:
            dq.popleft()

    def _prune(self, table: dict[str, deque[float]]) -> None:
        if len(table) <= self._max_ips:
            return
        for ip in [ip for ip, dq in table.items() if not dq]:
            del table[ip]
        if len(table) > self._max_ips:  # still over: drop the oldest-active
            ordered = sorted(table.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)
            for ip, _ in ordered[: len(table) - self._max_ips]:
                del table[ip]

    def _admit(self, table: dict[str, deque[float]], ip: str, window_s: float, limit: int) -> bool:
        now = self._now()
        dq = table[ip]
        self._trim(dq, now, window_s)
        if len(dq) >= limit:  # at/over the cap — reject WITHOUT extending the window
            return False
        dq.append(now)
        self._prune(table)
        return True

    def check_request(self, ip: str) -> bool:
        """Record one request against the global window; True if within limit."""
        return self._admit(self._req, ip, 60.0, self.requests_per_minute)

    def check_status_request(self, ip: str) -> bool:
        """Record one job-status poll against its own, larger window."""
        return self._admit(self._status, ip, 60.0, self.status_requests_per_minute)

    def check_job_post(self, ip: str) -> bool:
        """Record one job POST against the hourly window; True if within limit."""
        return self._admit(self._job, ip, 3600.0, self.job_posts_per_hour)

    def check_auth_request(self, ip: str) -> bool:
        """Record one `/auth/*` request against its own 15-minute window.

        Its own bucket rather than the general one because the two are being
        defended against different things: the general window is about load,
        this one is about somebody working through a list of addresses or
        invite codes. A budget wide enough for ordinary browsing is far too
        wide for that."""
        return self._admit(self._auth, ip, 900.0, self.auth_requests_per_15min)

    def check_magic_link(self, address_hash: str) -> bool:
        """Record one sign-in link for one ADDRESS, however many IPs ask.

        The per-IP window cannot see this case: a mailbox is floodable from a
        botnet at one request per IP, and the person whose mailbox it is has
        done nothing wrong and cannot make it stop."""
        return self._admit(self._magic, address_hash, 3600.0, self.magic_links_per_hour)

    def retry_after(self, ip: str, *, kind: str = "request") -> int:
        """Whole seconds until this IP's oldest entry leaves the window -- what
        a 429 should put in Retry-After. A client that waits that long is not
        guessing, and does not have to back off blindly."""
        table, window = {
            "request": (self._req, 60.0),
            "status": (self._status, 60.0),
            "job": (self._job, 3600.0),
            "auth": (self._auth, 900.0),
        }[kind]
        dq = table.get(ip)
        if not dq:
            return 1
        remaining = window - (self._now() - dq[0])
        return max(1, math.ceil(remaining))


# ─────────────────────────────────────────────────────────────────────────────
# Session SEC-2 — the external-service registry, and the /privacy sentence
# generated from it.
#
# WHY THIS EXISTS. `static/privacy.html` used to carry the sentence "One
# external service is involved: the Anthropic API" as literal markup, outside
# the `<!--ACCOUNTS-->` markers, so it rendered on BOTH backends. It was true
# only because production has never flipped `STORE_BACKEND`; the day an
# operator sets `EMAIL_PROVIDER=resend` the page states something false about
# where personal data goes, and nothing in the suite could notice. That is the
# same failure `outputs/LLM_DATAFLOW.md` §0 had for a whole session while
# AUTH-1's two Google endpoints sat in the tree (EMAIL-1 F-2).
#
# So the sentence is no longer written; it is DERIVED from this table, which is
# the same inventory §0 documents. `tests/test_sec2_disclosure.py` asserts that
# every `https://` endpoint constant under `src/` is claimed by a row here — so
# a sixth egress point cannot be added without either declaring it to the
# analyst or turning a test red.
#
# The condition is read from the ENVIRONMENT, never from an injected object.
# The suite hands `create_app` a fake sender and a fake Google client; those
# make no network call and must not add a row, or the page would announce a
# third party that this deployment does not have.
#
# The prose lives here rather than in the template because the whole point is
# that it is generated. Keep it small, and keep every clause traceable to
# `outputs/LLM_DATAFLOW.md` §4A.

class ExternalService(NamedTuple):
    """One third party this deployment can reach.

    `hosts` is what the endpoint-constant pin matches against; `phrase` is how
    the sentence names it; `detail` is the paragraph shown only when the
    service is actually enabled ("" for Anthropic, whose paragraphs are part of
    the page's permanent text).
    """

    key: str
    hosts: frozenset[str]
    phrase: str
    detail: str


#: In the order `outputs/LLM_DATAFLOW.md` §0 lists them.
EXTERNAL_SERVICES: tuple[ExternalService, ...] = (
    ExternalService(
        key="anthropic",
        hosts=frozenset({"api.anthropic.com"}),
        # Byte-for-byte the words this page has always used. The one-service
        # rendering below must reproduce the shipped sentence exactly, because
        # `browser` is what production runs and its bytes are pinned.
        phrase="the Anthropic API",
        detail="",
    ),
    ExternalService(
        key="resend",
        hosts=frozenset({"api.resend.com"}),
        phrase="Resend (which delivers your sign-in email)",
        detail=(
            "<p><b>What is sent when you ask for a sign-in link.</b> Your email address and "
            "the one-time link are sent to Resend, which delivers the message. Nothing else "
            "is — not your name, not a machine alias, not a reading, not your invite code, "
            "and not your IP address; the sender is handed the address and the link and has "
            "no field for anything else. The message is plain text, so it carries no "
            "tracking pixel and reading it is not a network event. The link is single-use "
            "and stops working 20 minutes after it is issued, and we keep only a hash of "
            "it, so neither we nor anyone holding a copy of our database can read a working "
            "link back out. Resend keeps its own record of what it delivered; what that "
            "record contains and how long they keep it is governed by their published "
            "terms, not by anything we can do from here.</p>"
        ),
    ),
    ExternalService(
        key="google",
        hosts=frozenset({
            "accounts.google.com", "oauth2.googleapis.com", "www.googleapis.com",
        }),
        phrase="Google (only if you use Sign in with Google)",
        detail=(
            "<p><b>What is sent when you use Sign in with Google.</b> Pressing the Google "
            "button sends your browser to Google to sign in. We then exchange the one-time "
            "code Google issues for your identity and fetch Google's public keys to check "
            "it. <b>We send Google no data of yours in that exchange</b> — the code was "
            "created for that sign-in seconds earlier, and your identity travels in the "
            "opposite direction. Google necessarily learns that you signed in here, which "
            "is inherent to choosing that button. Your machine details, your readings and "
            "anything you upload are never sent to Google, on this path or any other.</p>"
        ),
    ),
    ExternalService(
        key="stripe",
        hosts=frozenset({"api.stripe.com"}),
        phrase="Stripe (only if you buy credits)",
        detail=(
            "<p><b>What is sent when you buy credits.</b> Pressing a pack button asks Stripe "
            "to open a payment page and sends them four things: which pack you chose, the "
            "identifier of the price we have configured for it, the two addresses on this "
            "site to return you to, and an opaque identifier for your account \u2014 a random "
            "string that means nothing outside our database. <b>Your email address, your "
            "machine details, your readings and anything you upload are never sent to "
            "Stripe.</b> You then enter your card on Stripe\u2019s own page, in your own "
            "browser: those details go from you to them and never pass through this server, "
            "which is why we can say we do not hold them rather than promising not to look. "
            "Stripe tells us afterwards only that a particular payment page was paid for; we "
            "look up what that page was worth in our own records and add the credits. "
            "<b>Stripe keeps its own record of the payment</b> \u2014 including the card "
            "details and billing information you gave them \u2014 for as long as their own "
            "terms and the law require of a payment processor. That record is theirs, not "
            "ours: deleting your account here removes what we hold, and cannot remove "
            "theirs.</p>"
        ),
    ),
)

#: Indexed by COUNT, so the tuple has to be as long as the number of services
#: that can be enabled at once, plus one for zero. Four services exist; a fifth
#: would need a fifth word here, and `third_parties_sentence` falls back to the
#: digit rather than an IndexError if one is ever forgotten.
_COUNT_WORDS = ("No", "One", "Two", "Three", "Four")


def enabled_external_services(
    env: Mapping[str, str], *, store_backend: str
) -> tuple[ExternalService, ...]:
    """The rows this deployment can actually reach, given its environment.

    The three conditional rows mirror the code that decides reachability, and
    are pinned against it: Resend follows `auth/email.py::require_sender` (which
    defaults to `log` when `EMAIL_PROVIDER` is unset), Google follows
    `auth/google.py::google_client_from_env` (all three variables, stripped,
    non-empty), and Stripe follows `billing/packs.py::billing_enabled` (exactly
    the string "true"). All three are additionally gated on the `db` backend,
    because on `browser` none of those packages is even imported.

    STRIPE IS READ FROM THE ENVIRONMENT HERE RATHER THAN BY CALLING
    `billing_enabled`, and that duplication is deliberate rather than lazy. This
    module is imported on EVERY backend, including the one production runs;
    importing the billing package to ask it a question would put a billing module
    in `sys.modules` on a deployment that charges for nothing, and
    `tests/test_bill1_inert.py` asserts that it does not. The two spellings are
    pinned equal to each other by a test instead, which is the same trade the
    Resend and Google rows already make.
    """
    enabled = [EXTERNAL_SERVICES[0]]  # Anthropic: rows 1-2 of §0, always
    if store_backend == "db":
        by_key = {service.key: service for service in EXTERNAL_SERVICES}
        if (env.get("EMAIL_PROVIDER") or "").strip().lower() == "resend":
            enabled.append(by_key["resend"])
        google = [
            (env.get(name) or "").strip()
            for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI")
        ]
        if all(google):
            enabled.append(by_key["google"])
        # Session BILL-1. Gated on the flag alone and NOT on the presence of
        # `STRIPE_SECRET_KEY`: `create_app` refuses to start when the flag is set
        # without its secrets, so "flag on" and "Stripe reachable" are the same
        # state by the time any page renders. Reading the key here as well would
        # be a second, weaker copy of a rule that is already enforced at startup.
        if (env.get("BILLING_ENABLED") or "").strip().lower() == "true":
            enabled.append(by_key["stripe"])
    return tuple(enabled)


def third_parties_sentence(services: Sequence[ExternalService]) -> str:
    """The bold lead sentence of `/privacy`'s "Third parties" section.

    Singular is not a special case bolted on: it is the shipped wording, and
    `tests/test_auth1_inert.py`'s byte-identity pin holds it to the character.
    """
    phrases = [service.phrase for service in services]
    if not phrases:
        # Unreachable through `enabled_external_services`, whose Anthropic row
        # is unconditional. Handled anyway because the alternative is an
        # IndexError raised while rendering the one page that must always
        # render -- a privacy page that 500s discloses nothing at all.
        return "No external service is involved."
    if len(phrases) == 1:
        return f"One external service is involved: {phrases[0]}."
    listed = ", ".join(phrases[:-1]) + f" and {phrases[-1]}"
    word = _COUNT_WORDS[len(phrases)] if len(phrases) < len(_COUNT_WORDS) else str(len(phrases))
    return f"{word} external services are involved: {listed}."


def third_party_details(services: Sequence[ExternalService]) -> str:
    """The per-service paragraphs, or "" when only Anthropic is reachable.

    Empty is what keeps `browser` byte-identical: `_fill_page` deletes the
    whole line an empty slot sits on, so the page a browser receives has no
    blank line where these paragraphs would go.
    """
    return "\n".join(service.detail for service in services if service.detail)


def request_is_https(request) -> bool:
    """Did the ANALYST's connection to us use TLS?

    Behind Caddy this process speaks plain HTTP on loopback, so
    `request.url.scheme` reads `http` in production and is the wrong answer.
    `X-Forwarded-Proto` is where the real one lives -- honoured only from a
    trusted local peer, on `client_ip`'s rule and for the same reason: a header
    the caller controls must not decide anything on its own.
    """
    peer = request.client.host if request.client else "unknown"
    if peer in _TRUSTED_PROXY_PEERS:
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        if forwarded:
            return forwarded.lower() == "https"
    return request.url.scheme == "https"


def hsts_enabled(env: Mapping[str, str]) -> bool:
    """Has this deployment opted in to HSTS? (`HSTS_ENABLED=true`, exactly.)

    OPT-IN, because HSTS is a one-way door: a browser that has seen the header
    refuses plain HTTP to this host for a year and there is no un-send.
    `deploy/Caddyfile` carries the same value commented out with the same
    warning -- enable it only after a stable week on a real certificate -- and
    defaulting it on here would quietly overrule that note from the other side
    of the proxy. This variable is now the single place the decision is made.

    Exactly `true`, on `RETAIN_TRACES`' precedent and deliberately strict: `1`
    and `yes` do not switch on a control that cannot be switched off.
    """
    return (env.get("HSTS_ENABLED") or "").strip().lower() == "true"
