> **Public-repository note.** This document describes the full private project, including the
> deployment (`deploy/`), the account and billing routes, and the four-layer secret-scanning
> model whose CI half lived in the private repo's gate. Sections about those will not resolve
> against this tree. What does still apply: the upload sandbox, the response-header posture on
> the routes that remain, and the reasoning throughout.

---

# Security controls — secret scanning, dependency auditing, response headers

Added by session SEC-1, extended by SEC-2. Three questions this document
answers: *what stops a secret being committed*, *what to do the day something
fires*, and *what every response carries and why*.

§§1–6 are repository tooling and touch no application code. §7 is the webapp's
response-header surface, audited at SEC-2 and pinned route by route; it is the
only part of this document that describes something running in production.
`pdm_core/` and `config/` are outside all of it.

---

## 1 · The controls

| Layer | What it scans | When | Can it be bypassed? |
| --- | --- | --- | --- |
| `.gitignore` | file **names** | staging | only by `git add -f` |
| pre-commit hook | staged **content** | `git commit` | yes — `git commit -n`, or simply not installed |
| `security.yml` / `secrets` | **all** history, **all** refs | every push, every PR | no |
| `security.yml` / `deps` | dependency versions | every push, every PR | no |

The layers are not redundant. `.gitignore` stops a `.env` arriving by name but
says nothing about a key pasted into a `.py` file. The hook catches that, but
only on a machine where somebody ran the install command. CI catches both and
cannot be skipped by the person making the mistake — which is why it, not the
hook, is the control this repository actually relies on.

---

## 2 · Operator install — the exact commands

```bash
brew install gitleaks                              # Go binary; not on PyPI
PYTHONPATH=src ./.venv/bin/pip install -e ".[dev]" # brings pre-commit
./.venv/bin/pre-commit install                     # writes .git/hooks/pre-commit
```

Confirm it is live, and confirm it can actually **fail** — an unproven scanner
is not a control:

```bash
printf 'ANTHROPIC_API_KEY=sk-ant-api03-%s\n' "$(python3 -c "print('A1b2C3d4E5f6G7h8I9j0'*5)")" > canary.txt
git add canary.txt
./.venv/bin/pre-commit run gitleaks     # MUST fail, and must print REDACTED
git reset canary.txt && rm canary.txt
./.venv/bin/pre-commit run gitleaks     # MUST pass
```

That canary uses a key shape gitleaks' **default** ruleset misses, so a failure
also proves the repo's custom rule is loaded. See §5.

> Worktrees share the common `.git/hooks` directory, so installing once
> generally covers linked worktrees too. Verify it rather than assume it: run
> the canary above in the tree you care about.

### `-f` is required when a hook already exists

`pre-commit install` **without** `-f` runs in *migration mode*: it preserves any
existing `.git/hooks/pre-commit` as `pre-commit.legacy` and keeps running it
alongside the new one. If the hook being replaced is the thing you are replacing
*because it is wrong*, migration mode quietly keeps it in force. Use
`pre-commit install -f`, then confirm no `pre-commit.legacy` was left behind:

```bash
ls "$(git rev-parse --git-common-dir)/hooks/" | grep pre-commit
```

This repository had exactly that situation at SEC-1 — a hand-written
`grep -q "sk-ant-api"` hook, which was replaced. See `outputs/SESSION_SEC1.md` §4.

---

## 3 · Running a full-history scan by hand

The scan itself is safe. **The report is not** — a gitleaks report contains the
secrets it found, so it is written outside the repository and `--redact` is
always passed, which redacts the `Secret` and `Match` fields in the report file
itself rather than only on stdout.

```bash
OUT="$(mktemp -d)"
gitleaks git . \
  --no-banner --redact \
  --config=.gitleaks.toml \
  --log-opts="--all" \
  --report-format json --report-path "$OUT/history.json"
echo "EXIT=$?"
```

Two flags carry the whole claim:

* **`--log-opts="--all"`** — scan every ref, not just the current branch's
  ancestry. Without it, commits that live only on unmerged branches are never
  read. At SEC-1 that was 4 commits.
* **`--redact`** — never print the secret. When reporting a finding, quote the
  **file and commit only**. A prefix is not a safe redaction: it narrows the
  search space for anyone who later reads the report.

`.gitignore` covers `gitleaks-*.json` so that a scan run inside the working
tree cannot produce a committable report. Prefer `mktemp -d` anyway.

### If a real secret is found

1. **Rotate the credential first.** It is compromised from the moment it was
   committed; a clean history does not un-leak it. Assume disclosure.
2. Record it as **file + commit**, nothing more.
3. Only then decide about history. Rewriting published history is a
   coordinated operation, not a cleanup — it invalidates every clone and every
   open branch. It is deliberately out of scope for an automated session.

---

## 4 · Dependency audit and accepted exceptions

Two audits run per push, and they answer different questions:

* **the resolved environment** — what a fresh `pip install -e ".[web,pdf,db,dev]"` gets;
* **`constraints.txt`** — the versions **production** resolves, since
  `deploy/deploy.sh` runs `pip install -c constraints.txt -e '.[web,pdf,dev]'`.

The second matters more. A pin does not move when PyPI does, so a vulnerable
pinned version is the likelier production exposure — and an environment-only
audit would never see it.

To accept a vulnerability, add its advisory ID to `.pip-audit-ignore` with the
five mandatory fields documented in that file's header (package, why it is
unreachable **here**, the fixing version and why it is not being taken, who
accepted it, and a review-by date). The workflow turns each line into one
`--ignore-vuln` argument.

The order of preference is: **upgrade the package; drop the dependency; only
then add an exception.** An entry in that file silences a real, published
vulnerability, and `pip-audit` accepts unknown IDs silently — so a typo there
produces an exception list that appears to work while ignoring nothing.
`tests/test_secrets_hygiene.py` pins the ID format for exactly that reason.

---

## 5 · Why there are custom gitleaks rules

`.gitleaks.toml` extends gitleaks' default ruleset with three rules, one per real
credential this product has. All three exist for the same measured reason: the
upstream ruleset does not reliably catch ours.

### The first credential: Anthropic

`anthropic-api-key-shape`. Measured against gitleaks 8.30.1's defaults:

| Shape | Default ruleset |
| --- | --- |
| `sk-ant-api03-` + 93 chars + `AA` (canonical 108-char key) | detected |
| `sk-ant-api03-` + 95 chars | **missed** |
| `sk-ant-` + 95 chars (shorter legacy prefix) | **missed** |

The upstream rule is anchored to one exact length and suffix. This product's
only real credential is an Anthropic key, so that gap is the one that matters
most here: a key a few characters longer than expected would pass every scan
with a confident green. The custom rule is shape-based — `sk-ant-` followed by
40+ key characters — so a length change cannot invalidate it.

It does not match the documentation placeholders in `RUNBOOK.md` and
`outputs/SESSION_V2WIRE.md`, which truncate with an ellipsis. Verified by a
full-history scan with the config in force returning zero findings.

### The second credential: Resend

`RESEND_API_KEY` arrived with EMAIL-1 (RULED D-23) and the same question was asked
again. Measured against gitleaks 8.30.1's defaults, at EMAIL-1 and re-measured at
SEC-2:

| Shape | Default ruleset |
| --- | --- |
| `re_<id>_<secret>` bare on its own line | **missed** |
| `RESEND_API_KEY=re_…`, low entropy | **missed** |
| `RESEND_API_KEY="re_…"` / `key = "re_…"`, high entropy | detected — by `generic-api-key`, on entropy **plus quoted-assignment context**, not on the key's shape |
| inside `curl -H "Authorization: Bearer re_…"` | detected — `curl-auth-header` |

There is **no Resend rule upstream at all**; every catch above is incidental, and
the bare form — the one that ends up in a commit message, a `.env` line or a
note-to-self file — is the one that gets through. `resend-api-key-shape` closes it.

Two details in that regex are load-bearing. The alphabet includes `_`, because a
real key reads `re_<id>_<secret>` and its first alphanumeric run is only nine
characters — an alphabet without the underscore never reaches `{20,}` and would
miss every real key. And the prefix is anchored with `\b`, or it matches ordinary
identifiers (`pre_computed_value_that_is_long` contains `re_` mid-word).

Measured before committing: the needle matches **0 of the 697 tracked files** — the
two SEC-2 test files included, because their canaries are assembled from pieces for
exactly this reason — and a full-history scan with the rule in force (`--log-opts=--all`, 243 commits, all
refs) returns **zero findings**.

Prove it can fail, the same way:

```bash
printf 're_%s_%s\n' "123456789" "AbCdEfGhIjKlMnOpQrStUvWx" > canary.txt
git add canary.txt
./.venv/bin/pre-commit run gitleaks     # MUST fail, and must print REDACTED
git reset canary.txt && rm canary.txt
```

`tests/test_secrets_hygiene.py` does the same thing on every run through
`gitleaks stdin` — including the paired control that clean content stays green,
without which "it went red" would only prove the scanner reddens on everything.

### The third credential: Stripe

Session BILL-1 (RULED D-25) added Stripe Checkout, so `STRIPE_SECRET_KEY` and
`STRIPE_WEBHOOK_SECRET` joined the other two in `/etc/vibagent/env`. BILL-1
measured the same question and found a split answer — gitleaks 8.30.1, four
canaries in a scratch directory, scanned with `--redact`:

| canary shape | rule that fired |
| --- | --- |
| `sk_live_…` | `stripe-access-token` (built in) |
| `sk_test_…` | `stripe-access-token` (built in) |
| **`whsec_…`** | **no rule matched** |
| `price_1…` | none — correctly, a Price ID is configuration, not a secret |

The secret **key** is covered by the defaults; the webhook **signing secret** is
not. That is the wrong half to be missing: holding the signing secret lets an
attacker forge a `checkout.session.completed` against `/api/billing/webhook` and
grant themselves credits without paying, whereas the secret key mostly lets them
spend the operator's own Stripe account.

BILL-1 pre-wrote the rule and left it for the next session with scope on the
file; Session LEGAL-1 added it, in `resend-api-key-shape`'s format. The alphabet
has no underscore, unlike the Resend rule: a Stripe endpoint secret is one
unbroken alphanumeric run, and adding `_` would match every `snake_case`
identifier that happens to start `whsec_`.

**One measured trap, recorded so nobody re-derives it from a green run.** The
first canary LEGAL-1 wrote was `whsec_` + `AbCdEfGhIjKlMnOpQrStUvWxYz012345`. It
matches the rule's regex — Python's `re` agrees — and gitleaks reported **no
leaks found, exit 0**: its default allowlist drops sequential runs like a walked
alphabet, so a canary built from one is silently discarded while the rule is
working perfectly. A canary must be high-entropy, and must be measured against
the binary rather than against `re`. That failure is why the binary-backed test
exists at all.

Prove it red by hand the same way as above:

```bash
printf 'whsec_%s\n' "8kQvN2mXpL4rT7wZ3yB6cD9fH1jK5nS0" > canary.txt
git add canary.txt
./.venv/bin/pre-commit run gitleaks     # MUST fail, and must print REDACTED
git reset canary.txt && rm canary.txt
```

Full-history rescan with the rule in force (`gitleaks git . --log-opts="--all"
--redact`, report written outside the repository), run twice — once on the
working tree and once after the commit that introduced the rule, because a rule
that reddens its own introducing commit is a rule the next person deletes rather
than investigates:

| | |
| --- | --- |
| Findings | **0** |
| Commits scanned | **293** before the commit, **294** after it (all refs, so sibling worktrees' branches are in the count) |
| Exit | 0, both times |
---

## 6 · Verifying CI

**The workflows cannot be verified before they are merged.** GitHub only runs a
workflow file that exists on the ref being pushed, so `security.yml` does
nothing until it reaches `master`. The first real evidence is the next push
after the merge.

On <https://github.com/jkhaings/vib-agent/actions>:

1. For the merge commit's SHA, confirm **two** runs appear: `CI` and `security`.
2. Open `security` and confirm **both** jobs are green:
   * `secrets` — the gitleaks step's log tail ends `EXIT=0`;
   * `deps` — **both** pip-audit steps end `EXIT=0` (environment *and*
     `constraints.txt`).
3. In `secrets`, expand the checkout step and confirm it fetched **full
   history**. A shallow clone scans one commit and reports a green all-clear.
4. Confirm the gitleaks install step printed the version you expect and that
   `sha256sum -c -` reported `OK`.

### Prove it can go red — once

A scanner that has never failed is unproven.

```bash
git switch -c sec-canary
printf 'ANTHROPIC_API_KEY=sk-ant-api03-%s\n' "$(python3 -c "print('A1b2C3d4E5f6G7h8I9j0'*5)")" > canary.txt
git add -f canary.txt && git commit -n -m "canary: prove the CI secret scan fails"
git push -u origin sec-canary
gh run list --workflow=security.yml --limit 5      # expect a FAILED run
git push origin --delete sec-canary && git switch - && git branch -D sec-canary
```

`git commit -n` is required: the local hook would otherwise block the canary
before it could test CI. That is the hook working correctly.

---

## 7 · Response headers — the audited surface (SEC-2)

Every response from the webapp carries these, set by one middleware
(`webapp/app.py::_harden`) from one table (`webapp/hardening.py::SECURITY_HEADERS`).
`setdefault` is used throughout, so a handler that sets a header deliberately is
never clobbered.

| Header | Value | What it is for |
| --- | --- | --- |
| `Content-Security-Policy` | `default-src 'self'; base-uri 'self'; frame-ancestors 'none'; object-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:` | see below |
| `X-Content-Type-Options` | `nosniff` | a `.csv` we serve is never re-interpreted as HTML |
| `X-Frame-Options` | `DENY` | clickjacking, for clients older than CSP 2 |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | a report URL never travels in a `Referer` to a third party |
| `Permissions-Policy` | `accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()` | nothing served here uses a device capability |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | **only** when `HSTS_ENABLED=true` **and** the request arrived over TLS |

### What the CSP actually forbids, and what it deliberately does not

**Inline script is blocked by the ABSENCE of `script-src`.** With no `script-src`,
scripts fall back to `default-src 'self'`: `/app.js` loads, an inline `<script>`
body does not, and neither does an `on…=` handler attribute. Re-verified at SEC-2
after UX-1's client-side routing — no served page has an inline script body, an
inline handler, or a `javascript:` URL, and that is now swept by a test rather
than read.

`'unsafe-inline'` appears exactly once, in `style-src`, for the small set of
self-authored `style=""` attributes. No user-supplied markup reaches any page.

Session PAGES added four pages and a second stylesheet and uses **neither**: the
marketing surface carries no inline `<style>` block and no `style=""` attribute
at all, and no script tag either — the demo's charts are server-rendered SVG,
with geometry in SVG attributes and colour in classes. `tests/test_pages_surface.py`
pins all three. That narrows the claim above rather than widening it.

Session UX-4 changed how two of those pages are BUILT without adding a route.
`GET /validation` and `GET /field-validation-results.md` served their source
files inside `<pre class="doc">`; they now render generated HTML through
`webapp/marketing/mdpage.py`. The route inventory, the cache policy and the
table below are all unchanged — but a page that was previously nothing but
escaped text is now a page with tags in it, so the guarantee is worth stating
rather than inheriting:

* the renderer **escapes first and marks up afterwards**, so no character from
  a source file can become part of a tag or an attribute;
* link targets are **allowlisted** to site-relative and `https://` — an
  allowlist has to anticipate no scheme, where a `javascript:` blocklist has to
  anticipate every one of them;
* it emits no `<script>`, no `on…=` handler and no `javascript:` URL, which
  `tests/test_hardening.py::test_no_served_page_carries_inline_script` already
  sweeps for both routes, and `tests/test_ux4_copy.py::TestTheRendererIsSafe`
  now drives adversarial input through it directly (a `<script>` in the prose,
  a handler attribute inside a table cell, four refused link schemes).

The documents it renders are repo-authored, not user input. That is a reason to
keep the property, not to rely on it: `outputs/*.md` is regenerated by
`eval/runner.py`, so the text on those pages is written by a program.

SEC-2 added the three directives that do **not** inherit from `default-src`, so
until then nothing constrained them at all: `base-uri` (one injected `<base>`
silently repoints every relative URL on the page, `/app.js` included),
`frame-ancestors` (`X-Frame-Options`' modern twin — both are sent), and
`object-src` (reports open as top-level documents via `<a href>`, never `<embed>`,
so `'none'` costs nothing).

**`form-action` is deliberately absent.** `POST /auth/google` answers with a
redirect to Google; browsers have historically checked `form-action` against
redirect targets, so the directive could break sign-in on the very backend this
work is clearing the way for. It would buy little here — no page renders
user-authored markup, so there is no injected form for it to stop.

### HSTS is opt-in, and that is the point

A browser that has seen HSTS refuses plain HTTP to the host **for a year**, and
there is no way to withdraw it. `deploy/Caddyfile` carries the same value
commented out with the same warning (enable only after a stable week on a real
certificate). Rather than have two places that could send it, the app now owns
the switch:

```bash
# /etc/vibagent/env — then: sudo systemctl restart vibagent
HSTS_ENABLED=true
```

It is sent only on requests that arrived over TLS (`X-Forwarded-Proto`, honoured
only from the trusted loopback proxy — an untrusted peer cannot forge it), so a
local `http://` run never sees it. Leave the Caddyfile line commented; a test
fails if both start sending it.

### Route → headers

Every route the app registers, with the cache policy its responses must carry.
The table is declared as data in `tests/test_sec2_headers.py` and asserted by
**strict equality against the routes FastAPI actually registered**, so a new
route cannot join without a decision being recorded here and there.

| Route | Backend | `Cache-Control` | Why |
| --- | --- | --- | --- |
| `GET /` | both | *(cacheable)* | public page |
| `GET /privacy` | both | *(cacheable)*, `no-store` when signed in | the signed-in rendering carries a CSRF token and a delete button |
| `GET /terms` | both | *(cacheable)* | public page — no account state, no token, identical bytes for every visitor (LEGAL-1) |
| `GET /validation`, `GET /field-validation-results.md` | both | *(cacheable)* | public pages |
| `GET /product`, `GET /how-it-works`, `GET /pricing`, `GET /demo` | both | *(cacheable)* | public marketing pages (PAGES) — no account state, no token, identical bytes for every visitor |
| `GET /marketing.css`, `GET /demo/{name}` | both | *(cacheable)* | static assets — the marketing stylesheet and the two committed demo spectra |
| `GET /style.css`, `/app.js`, `/fonts/{name}`, `/assets/{name}`, `/sample.csv`, `/example-spectrum.csv`, `/sample.xlsx`, `/sample-report.pdf` | both | *(cacheable)* | static assets |
| `GET /healthz` | both | `no-store` | liveness + queue depth |
| `POST /api/jobs` | both | `no-store` | creates a job |
| `GET /api/jobs/{id}`, `POST /api/jobs/{id}/confirm`, `GET /api/jobs/{id}/report.pdf` | both | `no-store` | the analyst's job and report |
| `POST /api/store/import` | both | `no-store` | account state; signed-in **and** `X-CSRF-Token`-checked since SEC-3 (404 on `browser`, where there is nothing to import into) |
| `GET /login` | `db` | `no-store` | carries a CSRF token |
| `POST /auth/magic`, `GET /auth/magic` | `db` | `no-store` | mints / spends a sign-in link |
| `POST /auth/google`, `GET /auth/callback` | `db` | `no-store` | opens / completes an OAuth flow |
| `POST /logout` | `db` | `no-store` | ends the session |
| `GET /me` | `db` | `no-store` | the account itself |
| `POST /auth/account/delete` | `db` | `no-store` | deletes the account |
| `GET /api/machines`, `GET /api/machines/{id}` | `db` | `no-store` | this account's machines |
| `POST /api/machines`, `PUT /api/machines/{id}`, `DELETE /api/machines/{id}`, `DELETE /api/machines/{id}/readings/{captured_at}` | `db` | `no-store` | writes to this account's machines (UX-2); every response sets its own `no-store` in `routes_machines.py` |
| `GET /billing` | `db` + `BILLING_ENABLED` | `no-store` | carries a CSRF token and this account's credit balance (BILL-1) |
| `POST /api/billing/checkout` | `db` + `BILLING_ENABLED` | `no-store` | opens a Stripe payment page against this account (BILL-1) |
| `POST /api/billing/webhook` | `db` + `BILLING_ENABLED` | `no-store` | grants credits; a cached 200 is a fulfilment replayable from a proxy (BILL-1) |

**The webhook is the one route in this table with no session and no CSRF token, and that is
not an exemption.** Stripe holds no cookie of ours and cannot be given one. The request is
authenticated by an HMAC-SHA256 signature over its **raw body**, computed with the standard
library (`hmac`, `hashlib`, `hmac.compare_digest`) against `STRIPE_WEBHOOK_SECRET`, and
refused if its timestamp is more than five minutes from ours — without that window a captured
request stays replayable forever, because a signature does not expire on its own. Three
consequences are deliberate and each is pinned in `tests/test_bill1_webhook.py`: the body is
verified **before** it is parsed, so no attacker-supplied JSON is decoded unauthenticated; the
body is capped at 256 KB, since verification needs the whole of it in memory; and every `v1`
digest in the header is checked, not only the first, so an endpoint-secret rotation does not
fail every event mid-rollover.

**A verified event is trusted about its origin and about nothing else.** The event names a
checkout; how many credits that checkout is worth is read from our own `checkout_sessions`
row, written before any money moved. A signature proves Stripe sent the message — it does not
prove the number inside it is the number we quoted.

The `/api/*` and `/healthz` rows reach `no-store` through the middleware's
`hardening.NO_STORE_PREFIXES`; every `db` row sets it on its own response
instead, which AUTH-1 chose so that the sensitive-prefix tuple could stay
strictly pinned. The test asserts the **outcome**, so a route that changes
mechanism stays green and one that loses the header does not.

**That gap is closed (SEC-3).** SEC-2 found that the auth router's promise held
for every response it *returned* and failed for every one it *raised*: bad CSRF
(403), the `/auth/*` rate limit (429) and the delete route's signed-out check
(401) are built by FastAPI's own exception handler and never passed through that
router's `_no_store`. It was pinned rather than described, so that the session
closing it would delete the measurement on purpose.

The fix is `auth/routes.py::NoStoreRoute`, the router's **route class** — not
three edited `raise` statements. The header is merged into
`HTTPException.headers`, which FastAPI's handler renders, so the exception stays
FastAPI's to format and `_rate_limit`'s `Retry-After` survives alongside it. The
guarantee is therefore a property of the router: a raise path added later
inherits it without anyone remembering to.
`tests/test_sec2_headers.py::TestTheRaisedErrorPathsCarryItToo` drives all five
raise paths, asserts `Retry-After` is not eaten, and — the pin that makes this a
statement about the router rather than about five handlers — mounts a route the
product does not have on a router built the same way and checks the header
arrives unasked.

### CSRF — where each state-changing route proves the request came from this site

There is no middleware CSRF check, deliberately: it would need a list of exempt
paths, and that list goes stale silently. Each route says so where it is.

| Route(s) | Proof | Carried as |
| --- | --- | --- |
| `POST /api/jobs`, `POST /api/store/import` | `app.py::_require_account` — session first, then double-submit | a form field / an `X-CSRF-Token` header |
| `POST/PUT/DELETE /api/machines…` (4 routes) | `routes_machines.py::_csrf_ok` | `X-CSRF-Token` header (JSON requests, no form) |
| `POST /auth/magic`, `/auth/google`, `/logout`, `/auth/account/delete` | `auth/routes.py::_require_csrf` | a form field |
| `POST /api/jobs/{id}/confirm` | **none** — authorised by possession of an unguessable job id, the same capability it relies on in `browser` mode where there are no accounts | — |

The cookie is `HttpOnly`, so the compared value is always the one the server
rendered into the page, never one read back out of the cookie by script. A
header is used wherever the body is JSON: a custom header cannot be set
cross-origin without a preflight this app grants nobody. The order is session →
CSRF → confirmation everywhere it applies, so a signed-out visitor is told to
sign in rather than that their form expired.

The last row is a decision, not an omission, and it is the one route in `app.py`
that `_require_account` does not cover — recorded here and as SEC-3 F-1 so that
the next reader meets the sentence rather than the silence.

