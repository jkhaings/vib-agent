> **Note for readers of the public repository.**
>
> This file is the operating manual I wrote for my AI collaborator over 566 commits. It is
> published unedited, as a record of how the work was actually done — which means it
> describes the **whole** private project, including parts that are deliberately not in this
> repository: the billing, accounts and database layers (`webapp/billing/`, `webapp/auth/`,
> `webapp/db/`, `webapp/mail/`, `webapp/marketing/`), the deployment (`deploy/`, `RUNBOOK.md`),
> the CI gate (`scripts/gate/gate.sh`, the per-branch scope files, the private dataset release),
> and the session ledger under `outputs/`. References to any of those will not resolve here.
>
> Read it for the reasoning, not as a map of this tree. The scope-boundary amendments in
> particular are a record of a prototype turning into a product and back again.

---

# vib-agent

Prototype AI agent for vibration analysis of rotating machinery. Data file in → analyst-grade draft report out. The LLM **never does math** — all numeric analysis lives in deterministic, tested Python functions (`pdm_core/`) exposed to the agent as tools. The LLM does orchestration, synthesis, and report language only.

## Product pillar

**Committed diagnosis with computed, evidence-cited confidence; every unresolved ambiguity ships with the measurement that resolves it. The system knows what it doesn't know and says what to collect next.**

Concretely: RCA emits a committed differential — `primary_findings` (the calls we commit to) plus `differential` (candidates the interaction rules suppressed/downgraded, each with an adjudication reason). Confidence is a computed ordinal (high/medium/low; no percentages in v1) from a config-weighted factor rubric (`config/thresholds.json` `confidence`). A separate recommendations engine (`pdm_core/recommendations.py` + `config/next_measurements.json`) emits follow-up measurements whenever evidence can't resolve the differential — and the **gate-fail path emits through the same channel** (bad data → what to re-capture). The agent (Phase 4) relays confidence and measurement recommendations **verbatim in substance** — may rephrase, never invent, upgrade, or extend.

## Hard scope boundaries — DO NOT BUILD

- No web UI, frontend, or dashboard
- No database (including SQLite) — all I/O is JSON/CSV files on disk
- No auth, users, or multi-tenancy
- No RAG, vector stores, or embeddings
- No CMMS/external integrations, file watchers, or schedulers
- No Docker or deployment config
- No speculative abstraction layers (plugin systems, provider interfaces)

Single-user local prototype. Resist adding any of the above.

**Scope-guard amendment (Phase 6):** the "no web UI" and "no deployment config"
prohibitions above are LIFTED, but **only** for `src/vib_agent/webapp/`,
`src/vib_agent/adapters/uploads/`, and `RUNBOOK.md`. Every other boundary above still
holds inside that surface too — no accounts/auth beyond invite codes, no database (an
in-memory job registry only), no dashboards, no multi-file batch UI, no admin panel.
`pdm_core/` and `agent/` are untouched by Phase 6; the webapp calls them exactly the
way the CLI does (`pipeline.run_analysis`, `agent.loop.run_agent_analysis`) and adds
no new analysis logic of its own.

**Scope-guard amendment (v6-A — production hardening):** the "no deployment config"
lift now also covers the `deploy/` directory (systemd unit, Caddyfile, `setup_server.sh`,
`deploy.sh`) and `scripts/prod_smoke.py`, plus app-layer hardening inside
`src/vib_agent/webapp/` (`hardening.py`: per-IP rate limits, security headers/CSP,
`/healthz`, disk guard). Still no Docker, no orchestration, no accounts/auth beyond
invite codes, no database. `pdm_core/`, `config/thresholds.json`, and the detectors are
untouched — hardening is webapp-surface only.

**Scope-guard amendment (HIST-1 — persistence foundation):** the "no database (including
SQLite)" and "no auth, users, or multi-tenancy" prohibitions above are LIFTED, but **only**
for `src/vib_agent/history/` — SQLite via the stdlib, holding tenant → site → machine →
measurement_point → survey → measurement rows plus spectrum blobs and pinned reports.
`tenant_id` is a data-partition key enforced by composite foreign keys, NOT auth: still no
accounts, no login, no sessions, no invite-code changes. Still no ORM or migration framework
(numbered SQL + `PRAGMA user_version` only — the "no speculative abstraction layers" rule
survives intact), no RAG, no schedulers, no Docker, no dashboards. **The layer is additive
and inert as of HIST-1**: nothing in `pdm_core/`, `pipeline`, `agent/`, `report/`, or
`webapp/` imports it — only tests do — and the webapp's in-memory job registry and its
"no database" docstrings remain true for the webapp surface. The existing upload → report
path is byte-identical with this package present (acceptance-checked at HIST-1 close).
Wiring ingestion into the product — and the report-footer + `/privacy` wording that MUST
change with it — is a future session's explicitly-approved step, never a side effect; the
approved wording and the deployment prerequisites (systemd `StateDirectory`, a backup story)
are recorded in `outputs/HIST1_PLAN.md`.

**Scope-guard amendment (DB-1 — accounts foundation):** the "no database" and "no auth,
users, or multi-tenancy" prohibitions, **and the HIST-1 amendment's "no ORM or migration
framework" clause immediately above**, are LIFTED — but **only** for
`src/vib_agent/webapp/db/`: SQLAlchemy models and Alembic migrations, dialect-neutral
(SQLite for tests and local work, Postgres via `DATABASE_URL` in production), holding
`users`, `magic_links`, `credits_ledger`, `machines`, `readings` and `jobs`. **Metadata
only — no column anywhere holds a spectrum, an amplitude array or report bytes**, pinned by
inspecting `Base.metadata` rather than by convention. **The layer is inert while
`STORE_BACKEND=browser`**, which is the default and what production runs: `app.py` imports
the package inside `create_app` under the flag branch and nowhere else, no request reaches a
database, `POST /api/store/import` 404s, and `deploy/deploy.sh` installs `.[web,pdf,dev]` —
without the new `[db]` extra — so the deployed dependency set is unchanged. Still no
sessions, no cookies, no dashboards, no RAG, no schedulers, no Docker; still no Postgres
driver in the repo (dialect-neutrality is proven offline, so egress law #6 holds). This is
**not** `src/vib_agent/history/`, which is a different persistence layer from a different
session and stays inert and unimported here. Turning accounts on — and the `/privacy` and
retention wording that MUST change in the same release — is AUTH-1's explicitly-approved
step, never a side effect. **ROADMAP common law #8 ("no server-side persistence of any
kind") was deliberately NOT amended**; the conflict is recorded in `outputs/SESSION_DB1.md`
for a ruling before AUTH-1.

**Scope-guard amendment (AUTH-1 — accounts, behind the same flag):** the DB-1 lift above
is extended to `src/vib_agent/webapp/auth/` — sign-in by emailed one-time link or Google
(authlib, state + PKCE), a server-side session row, CSRF on every state-changing form, and
per-`/auth/*` rate limits. Two tables were added to the DB-1 schema (`sessions`,
`oauth_flows`, both keyed on a value that is not a credential) and one UNIQUE constraint
(`credits_ledger (user_id, ref)`), in Alembic revision `0002_auth1_accounts`. **Still
metadata only** — the no-bulk-data schema pin walks the new tables too. Invite codes stop
being a per-request gate on the `db` backend and become a **one-time credit grant per code
per account**; the row records the invite *label*, never the code, so `/privacy`'s "your
invite code is never stored" stays true. **Nothing debits the ledger**: AUTH-1 grants and
charges for nothing, `jobs` is still written by nothing, and the retention promises say
exactly that much. Google client id/secret/redirect URI are env-only and a grep pin across
the tracked tree enforces it; `authlib` is a new `[auth]` extra, imported inside
`create_app`'s flag branch and (for authlib itself) inside the two methods that use it, so
the deployed `.[web,pdf,dev]` install still loads neither extra. **Browser mode is
byte-identical** — `/` and `/privacy` render the same bytes, every auth route 404s, no auth
or db module enters `sys.modules` across a whole job, and `NO_STORE_PREFIXES` is unchanged
(the auth handlers set `Cache-Control: no-store` themselves rather than joining that tuple).
Still no accounts UI beyond a login page, no dashboards, no password, no session for the
`browser` backend, no Docker, no schedulers. **The same-commit rule was honoured** (ROADMAP
common law #8 as amended by D-22): the ledger rows, the `/privacy` Accounts section and the
Alembic revision that makes them true ship in one commit, and both flip *with the backend*
so production is unchanged until the operator flips the flag. Turning it on is an operator
errand list in `outputs/SESSION_AUTH1.md`, never a side effect.

**Scope-guard amendment (EMAIL-1 — the real email sender, RULED D-23):** the AUTH-1 lift is
extended to `src/vib_agent/webapp/mail/` — one module, `resend.py`, holding the product's
**third network egress point**: an HTTPS POST to `api.resend.com` that delivers the sign-in
link AUTH-1's `MagicLinkSender` seam was written for and deliberately shipped without.
**No new dependency** — the `httpx` already declared in `[web]` and pinned in
`constraints.txt`; `pyproject.toml`, `constraints.txt` and `deploy/deploy.sh` are byte-
unchanged, so ROADMAP egress law #6's "no new third-party dependency that can open a network
egress point" survives intact and the deployed install is untouched. The wire format is
**ported from `reference/flows.json`**, which has been mailing this operator's alerts through
Resend in production — but its failure log, which prints `JSON.stringify(msg.payload)` and so
writes the recipient's address into a log, is deliberately **not** ported: this module logs a
status code or an exception class name and never reads the response body. `RESEND_API_KEY` and
`EMAIL_FROM` are **env-only and both required** — either missing is a refusal to start, in
every environment — and a grep pin across every tracked file fails on a Resend-shaped key.
**No new `ERROR_TAXONOMY` row**: a sign-in send produces no job and no wire response (the page
is identical on success and failure by design, which is what stops `/auth/magic` being an
enumeration oracle), so the ratified S7 vocabulary is carried as class attributes on two
exception categories instead — wire law #5 satisfied without asking the operator to ratify a
code nothing can emit. Exactly **one line of `auth/` changed**: the provider branch in
`require_sender`, plus the `Supported:` enumeration inside it that would otherwise tell a
misconfigured operator the feature does not exist. Still no queue, no outbox, no retry loop, no
template engine, no second provider, no accounts UI. **The layer is inert while
`STORE_BACKEND=browser`**, which is the default and what production runs: `webapp/mail/` is
imported inside `require_sender` and nowhere else, a whole job loads no module from it, and
`/` and `/privacy` are byte-identical. Turning it on is an operator errand list in
`outputs/SESSION_EMAIL1.md`, never a side effect — and it is **blocked** on the `/privacy`
"Third parties" wording, which still names Anthropic as the only external service and renders
in both modes.

**Scope-guard amendment (ACCOUNT-2 — the two things the flag flip required):** the DB-1 /
AUTH-1 lift is unchanged in extent; this session spent it on the two debts those sessions
recorded rather than declined work. **F-4 is closed**: `readings.zone_source`
(`computed` | `imported`, NOT NULL, no default on either side) in Alembic revision
`0003_account2_zone_source`, stamped `imported` by the importer — the only code that has ever
constructed a `Reading`. **Nothing writes `computed`**, because there is still no
analysis-to-database path and `jobs` is still written by nothing; a pin asserts no module under
`src/` names `ZONE_SOURCE_COMPUTED`, so the session that adds that writer meets a failing test
rather than silently changing what `/privacy` means. **AUTH-1 F-4 is closed**:
`POST /auth/account/delete` (signed-in, CSRF, typed confirmation) and
`scripts/account_delete.py <email>` both call ONE cascade,
`webapp/auth/store.py::delete_account`, whose child-first order is declared as data and checked
against `Base.metadata` itself. The cascade lives in `auth/` because `webapp/db/` is pinned
against issuing any DELETE; the ledger-readers pin was **widened to walk `auth/` too** rather
than left to pass by accident. **The "no accounts UI beyond a login page" clause above is
narrowed by exactly one control**: a delete form rendered into `/privacy` for a signed-in
analyst only — no account page, no dashboard, and the anonymous rendering is unchanged. The
same-commit rule (D-22) was honoured again: the column, the revision, the retention-ledger
wording and the `/privacy` wording ship together, and *"the ISO zone we computed"* became
provenance-honest in all three places that describe account-held readings. Browser mode stays
byte-identical; every `/auth/*` path including the new one still 404s there. **Nothing debits
the ledger still** — deletion removes ledger rows with the account, which is a different
promise from spending them.

**Scope-guard amendment (JOB-DB — the first analysis-to-database path):** the DB-1 /
AUTH-1 lift is unchanged in extent; this session spent it on the writer ACCOUNT-2's F-6
said would arrive. **On `STORE_BACKEND=db`, a job that reaches `done` or `degraded` records
one `readings` row and one `jobs` row**, in `src/vib_agent/webapp/db/recorder.py`, inside the
terminal transition and before the state flips (PURGE-SYNC's rule: the instant a client can
read `done`, everything that word implies is already true). `gate_fail` and `error` record
nothing — no diagnosis was made. **`zone_source='computed'` now has a writer**, and the
ACCOUNT-2 pin that forbade one was re-pointed rather than deleted: exactly ONE module under
`src/` may name the constant, so a second writer still lands as a red test. **Still metadata
only** — the values written are `job.trend_point`, which is D-5's scalar and the same four
numbers the analyst's browser already holds; the no-bulk-data schema pin is untouched and no
column moved, so **there is no Alembic revision in this session** (head stays
`0003_account2_zone_source`). **D-24 is enforced in Python, not by a constraint**: same
machine + same UTC date + same axis + same provenance is a replacement, done by ORM attribute
assignment because `webapp/db/` is pinned against any call named `update`. An **imported**
reading is never replaced by a computed one — that would delete the only copy of a browser's
claim and relabel it as ours, F-4 running backwards.

Three files outside `webapp/db/ + worker.py + tests` were touched, each for a stated reason:
`jobs.py` gains `db_hook` (the `refund_hook` twin — a bare callable, so `worker.py` still
imports nothing from `webapp/db/` on any backend) and `committed_fault`; `app.py` hands over
the user id `_require_account` was already resolving and attaches the hook beside the refund
hook; and `static/{app.js,privacy.html,privacy_accounts.html}` carry the promises, which
**D-22 requires in the same commit as the writer**. The retention ledger's Job-metadata row
went live (13 live / 0 future on `db`), and `/privacy` stopped saying *"We do not keep a
record of individual analyses against your account."* **Nothing debits the ledger still** —
`jobs.ledger_id` is written NULL and is BILL-1's named seam. **Browser mode writes nothing**,
and that is a property of the object rather than a branch: `job.db_hook is None` there,
pinned alongside the `sys.modules` claim that a whole job loads no db module.

## Commands

```bash
source .venv/bin/activate        # project venv (Python 3.11+)
pytest                           # run all tests — must be green before advancing a phase
vib --help                       # CLI entry point
vib demo                         # seeded BPFO case through the full agent path
vib analyze case.json --no-llm   # deterministic pipeline, zero API calls
vib analyze case.json            # agent path (needs ANTHROPIC_API_KEY)
vib eval                         # scorecard over eval/cases/

pip install -e ".[web]"                              # Phase 6 webapp deps (FastAPI, uvicorn, openpyxl, pyuff)
uvicorn vib_agent.webapp.app:app --reload --port 8000 # run the webapp locally (needs ANTHROPIC_API_KEY, INVITE_CODES)
pytest tests/test_webapp_*.py                         # webapp suite alone (fake Anthropic client, zero real API calls)
```

## Environment

- **PDF rendering**: two paths, tried in order by `report/generate.py::_try_render_pdf`. (1) `markdown` + `weasyprint` Python packages (the `[pdf]` optional extra) — preferred when installed. (2) `pandoc` + `tectonic` (system tools, via `brew install pandoc tectonic`) — the fallback established on this machine, since neither `weasyprint` nor `pandoc` was present at Phase 3.5 close-out. `tectonic` is pandoc's `--pdf-engine`; plain `pandoc` alone cannot produce a PDF (needs a LaTeX engine). If neither path is available, `render_report(..., pdf=True)` writes markdown only and prints a notice — it never errors. **Corrected Session RENDER-SERIAL (measured, 2026-09-05): `weasyprint` 69.0 imports and renders on this dev machine, and the weasyprint path is what is live here** — the earlier note said the opposite (native pango/cairo libs unreachable, import dying with `OSError` partway through rather than a clean `ImportError`, pandoc/tectonic live locally). That was true when written and is why `_try_render_pdf` catches `OSError` as well as `ImportError`; keep both handlers. **Production also takes the weasyprint path**, and does not have the fallback at all: `deploy/setup_server.sh:49-50` apt-installs weasyprint's native deps (`libpango-1.0-0`, `libpangoft2-1.0-0`, `libgdk-pixbuf-2.0-0`, `libcairo2`) and says in its own comment that pandoc/tectonic is "the documented fallback, not installed here", while `deploy/deploy.sh:34` installs `.[web,pdf,dev]`. So on the droplet a weasyprint failure degrades to markdown-only, never to pandoc. This is what put `write_pdf` on a worker thread and made PROD_READINESS §7 F-1 reachable in production. `render_pdf(markdown_text, out_dir)` is the public wrapper the webapp uses to re-render a PDF after post-processing (units/degraded notes) without going through `render_report`'s own write-then-render sequencing.
- **The native renderers run in a CHILD PROCESS (Session RENDER-PROC).** weasyprint and matplotlib are reached only from `src/vib_agent/report/_render_child.py`, spawned by `report/render_proc.py` as `python -P -m vib_agent.report._render_child <op>` with the payload on stdin. **Nothing in the parent imports either library** — pinned by a full document render in a fresh interpreter. This exists because a lock could not close the class: three native faults are on record (GEOM-A `EXIT=139` in `Figure.add_axes`, DB-1 `EXIT=139` in `write_pdf` during a GC pass, EMAIL-1 F-6 `EXIT=138` SIGBUS in `FontConfiguration.__init__`), and **F-6 happened with `render_lock.py` in the frame list** — the lock was held. A lock serializes renders; it cannot serialize the garbage collection of the cairo/pango objects a finished render leaves behind, which runs on whatever thread allocates next. `report/render_lock.py` is **kept**, for two narrower jobs: one child in flight at a time (a memory bound, since systemd's `MemoryMax=1500M` covers the whole cgroup) and the `acquisitions() == 1` pins. A child crash (SIGSEGV/SIGBUS/timeout) raises `RenderChildCrashed`, which — because `webapp/worker.py` has no try/except around its render calls — reaches `app.py::_run_guarded` and becomes the already-ratified `internal_error` (`server_error`, retryable). **No new `ERROR_TAXONOMY` row**; wire law #5 is satisfied by mapping, as EMAIL-1 did. `subprocess` and not `multiprocessing`, deliberately: `multiprocessing`'s spawn re-imports the parent's `__main__` (uvicorn's own entrypoint), and on Linux/3.12 its **default** start method is `fork`. `render_charts` **can now raise** — its old "Never raises" promise still holds for a missing matplotlib (empty `ChartSet`), but a native crash is surfaced rather than hidden behind a figure-less report.
- **PDF renders ARE byte-deterministic on the weasyprint path — correcting an inherited claim.** `tests/test_report_charts.py`'s docstring said both engines embed document ids, and CHARTS-AGG measured two renders of "the same input" differing by 9 bytes. Measured again in Session RENDER-PROC: weasyprint 69.0 writes **no** `/ID` (only when `pdf_identifier` is set; default `None`) and **no** `/CreationDate` (only from a `dcterms.created` meta, which no template has). What varies is the embedded image XObject name, `md5(url)` of the chart PNG (`weasyprint/pdf/stream.py:217`, `images.py:324`) — and that url carries the absolute `out_dir`. **Two renders into the same directory are byte-identical**; two renders into different directories are not, which is what was actually being measured. The pandoc/tectonic fallback genuinely does stamp, so scope the claim per engine.
- **`ANTHROPIC_API_KEY`**: required for the agent path (`vib analyze case.json`, `vib demo`, and any webapp job that reaches the drafting stage); `--no-llm` never needs it, and gate-fail/spend-capped webapp jobs never call it either. Not committed anywhere; export it in your shell (or put it in `/etc/vibapp.env` for the deployed service — see `RUNBOOK.md`) before running agent commands.
- **Phase 6 webapp env vars** (never in config, never committed): `INVITE_CODES` (`code:label,code:label`, gates every job-creating request; only the label is ever logged), `CONTACT_EMAIL` (shown on the unsupported-format funnel message and privacy page), `RETAIN_TRACES` (`true`/`false`; even when true, a trace is only kept for jobs where the analyst also ticked the form's consent checkbox). Tunable limits (upload size, TTL, concurrency, rate limits, daily token budget) live in `config/webapp.json`.

## Conventions

- **Pure functions** in `pdm_core/` — no I/O, no globals, no LLM anywhere in that package.
- **Full type hints** everywhere; **Pydantic models** (`models.py`) for all structured data crossing boundaries (readings, spectra, findings, results).
- **All tunable constants live in `config/*.json`** — zone boundaries, thresholds, tolerances, bearing geometry, model settings. Never hardcode them. Code reads whatever the config contains.
- **Threshold-profile doctrine (Session D — one profile per data provenance, always chosen explicitly):**
  - **`streaming`** = the **NCD sensor pipeline**. Production-validated against the live Node-RED
    reference and **frozen**; benchmark/calibration work never modifies it. Use it only for
    NCD-shaped data (the onboard 3-peak-per-axis triplets).
  - **`route`** = **all third-party / uploaded / route-collected data** — CSV/XLSX/UFF/WAV/MAT
    uploads, CWRU, MFPT, MAFAULDA, wind-turbine. This is where detector calibration happens
    (match tolerance, the synchronous-collision guard, the amplitude floor, the imbalance gate).
  - **Never call `load_thresholds()` with no argument in a product path.** The bare call resolves
    `active_profile`, which is a *default*, not a decision. Every entry point names its profile:
    the webapp reads `config/webapp.json` `analysis_profile` (`app.py`), the CLI takes `--profile`
    (default `route`). RUN v5 found the whole shipped product silently analysing uploads on
    `streaming` because of that bare call — every route-calibrated guard was inactive in the
    service while the benchmarks reported them as working. Pinned by
    `tests/test_webapp_e2e.py::TestAnalysisProfileWiring`.
- Assumed constants (not yet validated against the user's ISO tables / bearing catalogs) are marked `"_verify": true` in config and `# VERIFY` in code.
- If a `reference/` directory appears containing Node-RED JavaScript, treat it as the **source of truth** for algorithm logic and constants — port faithfully.
- Quality gate always runs first; any gate **fail** means the only valid downstream output is an insufficient-data report naming what's missing.
- Every numeric claim in a report must come from a tool result. Recommendations are conservative and actionable — never "run to failure".
- Synthetic data generation is deterministic under fixed seeds.
- Anthropic SDK reads `ANTHROPIC_API_KEY` from env. Default model `claude-sonnet-4-6`, configurable in `config/agent.json`. `--no-llm` paths must work with no key present.
- Agent tool traces log to `outputs/<case>/trace.jsonl` (CLI path) or a job's temp dir (webapp path — deleted with the rest of the job unless retention was explicitly opted into).
- **No request-level IP logging — application or proxy (v6-A, found in production).** The app log carries only the per-job metadata line (label, kind, size, duration, tokens, outcome). Uvicorn's access log is disabled with `--no-access-log` in `deploy/vibagent.service`: it logs one line per request *including the client IP*, and since Caddy sets `X-Forwarded-For` (trusted from loopback) those are real visitor IPs, not `127.0.0.1` — with `StandardOutput=append:` that silently made `app.log` a 14-day IP store. `deploy/Caddyfile` sets no `log` directive, so there is no proxy access log either. Abuse mitigation is **stateless**: the per-IP limiter in `hardening.py` is in-memory and never logs addresses. Minimal-by-default; short-retention access logging is the **escalation** path if targeted abuse warrants it, decided then and disclosed on `/privacy` then. Scope the claim to application/request logs — system-layer sshd/ufw/fail2ban security logs exist and are out of scope. **Do not re-add an access log, and do not describe a "split" where the proxy holds IPs — that split does not exist.**
- **Webapp-specific (Phase 6):** units are always explicit form fields, never assumed — every upload adapter converts to mm/s RMS via `adapters/uploads/units.py` and states the conversion applied (or that none was needed) in the report. A webapp job never hard-fails when the LLM path is unavailable (spend budget exceeded, a consistency hard-fail, or an API error) — it degrades to the deterministic report with a visible note instead; `error` is reserved for unparseable/malformed uploads. Every parser runs in a sandboxed subprocess (timeout + memory cap) since uploads are untrusted input.
- **Multi-axis upload (Session E — `webapp/assembly.py`):** the form accepts up to **3 files, each with a declared direction** (radial-horizontal / radial-vertical / axial), merged into ONE `Case`. Direction → axis mapping is fixed (the MAFAULDA precedent): **axial→`x`, radial-h→`y`, radial-v→`z`, `axial_axis` stays `x`**. Each file parses in its **own sandbox** (per-file `parse_in_subprocess`); the trusted parent remaps each single-axis `"y"` Case onto its declared axis and merges spectra + per-axis velocities, so the existing 3-axis pdm_core logic (axial-ratio, misalignment-variant, imbalance radial-dominance) engages exactly as on the NCD path — **no pdm_core change; the assembly layer only feeds it.** A single file with a **defaulted** direction is a strict identity pass-through (`merge_channels` returns the parsed Case untouched) so its report stays **byte-identical** to the pre-Session-E single-file path — the "Channels measured" line and per-channel notes appear only when a direction was explicitly chosen or >1 file uploaded. Cross-file integrity is assembly-layer, not pdm_core: per-channel quality gate (failing channels excluded; all-fail → insufficient-data), and cross-file speed agreement (disagreement → warning; no channel agreeing with the stated RPM → fail-closed insufficient-data). Trend/MAFAULDA modes stay single-file (multi-file in those modes is a 400). Pinned by `tests/test_multiaxis.py` (byte-compat matrix, the conjunction proof — an axial-dominant 1× trio commits `angular_misalignment` where a lone radial channel commits `imbalance` and a lone axial commits neither, closing the latent axis-mislabel bug — plus 4xx, speed, partial-gate, e2e).

## References — the citation register for the knowledge layer

`references/` holds the reference library the cause-knowledge layer is built from: bearing damage
taxonomies (SKF, Timken, FAG), wear-particle and ferrography material, lab limit bulletins, ISO 4406
explainers, and public-domain government handbooks. `references/INDEX.md` is its catalogue;
`references/GAPS.md` records what is paid, gated, or still missing.

- **Citations resolve against `references/INDEX.md`.** A knowledge-layer citation is an INDEX number
  (`#43 Table 4-5`, `#16 WP 004 00`), not a bare URL and not a remembered fact. If it is not in INDEX,
  it is not a source yet — add it to INDEX first, with its page count measured from the file.
- **No `causes.yaml` entry ships without an operator-approved source** already indexed. A cause with a
  plausible-sounding mechanism and no INDEX row is a guess wearing a citation's clothes; that is the
  exact failure this library exists to prevent.
- **Reference PDFs are never committed.** `.gitignore` uses `references/*` with negations for the two
  catalogue files — `INDEX.md` and `GAPS.md` are versioned, everything else (PDFs, `fetch.sh`,
  `verify.sh`, `PAGECOUNTS.tsv`) is not. Unlicensed copies live outside the repo entirely, at
  `~/Desktop/vib-agent-refs/_unlicensed/`, and are never cited.
- **`references/` (plural) is not `reference/` (singular).** The singular directory is reserved above
  for Node-RED JavaScript treated as algorithmic source of truth — port from it faithfully. The plural
  one is bibliography: it informs *what we write in a report*, never what a detector computes.
- **This is a citation register, not RAG.** No embeddings, no vector store, no retrieval at runtime —
  consistent with the scope boundary above. The PDFs are read by a human or an agent at *authoring*
  time; nothing in `pdm_core/`, `agent/` or `webapp/` reads `references/` at all.
- **Page counts and editions in INDEX are measured, not estimated.** `references/verify.sh` re-checks
  magic bytes, re-counts pages and finds dangling symlinks; it downloads nothing. Note the caveats it
  surfaced: INDEX #03 is an earlier FAG edition (WL 82 102/**2 ED**) with an unrecorded source host,
  #54's identity is unconfirmed, and #56's open-access terms are unverified. Cite around those three
  until they are settled.

## Layout

```
config/                  # iso_zones, bearings, thresholds, agent, webapp — all tunables
src/vib_agent/
  pdm_core/              # deterministic analytics — THE product core
    quality_gate.py      #   data quality checks (always first)
    iso_classify.py      #   Layer 1: ISO 20816-3 zone classification
    anomaly.py           #   Layer 2: Welford z-score + Layer 3: IsolationForest
    trend.py             #   Layer 4: OLS trend regression + zone-boundary projection
    bearing_rca.py       #   Layer 5: fault freqs + peak matching → committed differential + confidence rubric
    synthesize.py        #   cross-layer Findings (severity-anchored) + conservative recommendations
    recommendations.py   #   follow-up measurement rules (unresolved differential OR gate-fail)
  pipeline.py            # canonical fixed-order orchestrator: Case → AnalysisResult (used by --no-llm and generator)
  models.py              # Pydantic: MachineMeta, Reading, Spectrum, PeakSet, FaultMatch, DifferentialCandidate, Finding, AnalysisResult, Case
  synth/generator.py     # synthetic spectra/histories with seeded faults; expected derived via pipeline
  agent/                 # tool schemas, plain Messages-API tool-use loop, system prompt
  adapters/
    cwru.py              #   CWRU .mat → Case (Phase 3.5 benchmark path)
    uploads/             #   Phase 6: CSV/XLSX/UFF/WAV → Case (units.py, tabular.py, uff.py, wav.py)
  report/                # AnalysisResult + Jinja2 template → markdown → PDF (weasyprint+markdown optional)
  webapp/                # Phase 6: FastAPI app — jobs.py, worker.py, security.py, spend.py, parsing.py + static/
  cli.py                 # typer: demo / analyze / eval
eval/cases/ + runner.py  # 8 seeded cases, scorecard (zone accuracy, fault P/R, gate correctness)
tests/
outputs/                 # generated reports land here
references/              # reference library — only INDEX.md + GAPS.md are committed; PDFs gitignored
RUNBOOK.md               # Phase 6 deployment (systemd + Caddy)
```

## Build phases (work in order; pytest green before advancing)

0. **Scaffold** — pyproject, skeleton, CLAUDE.md, pytest wired, `vib --help` works. ✅
1. **pdm_core** — five layers + quality gate as pure functions with unit tests. No LLM.
2. **Synthetic data** — generator with seeded faults; generated BPFO case flagged by bearing_rca in a test.
3. **Deterministic pipeline + report** — `vib analyze --no-llm`: gate → layers in fixed order → Jinja2 → markdown (PDF if weasyprint present). Zero API calls.
4. **Agent loop** — tool schemas over pdm_core; plain while-loop against Messages API; emits schema-validated `findings.json` + narrative report. Same numbers as `--no-llm` (same tools).
5. **Eval harness** — `vib eval` scorecard: ≥ 7/8 zone accuracy, correct fault ID on all single-fault cases. Exits non-zero below thresholds.
6. **Hosted webapp** — `webapp/`: invite-gated upload → `pipeline.run_analysis` → `agent.loop.run_agent_analysis` (gate-fail/spend-guard-exceeded/draft-failure all degrade to the deterministic PDF with a visible note, never a hard error) → PDF download, then process-and-delete. New formats beyond the CLI's Case JSON / CWRU `.mat`: CSV/XLSX (spectrum or trend schema), UFF/UNV (dataset 58), WAV (scaled or unscaled) — `adapters/uploads/`. See `RUNBOOK.md` for deployment.
7. *(optional)* GitHub Actions running pytest on push.

**Standing rule — Part C acceptance (every phase, no exceptions):** a live product-path ("Part C") pass means each drafted PDF was **read against the known ground truth** for that case, not that the job reached `outcome=done`. `outcome=done` only means the job didn't crash; it is not a grade. A PDF that asserts a clean bill / "ISO Zone A / no action" on a known-faulted machine is a **fail**, however green the job log looks. (Phase 7 booked five `outcome=done` PDFs as a pass; Phase 7B read them and found "ISO Zone A" on acceleration-only, known-faulted machines — the severity-truthfulness defect Session A fixed at the source.)

**Standing rule — every supported format must be exercised through the LIVE web form (Part C, every phase):** product-path verification must upload each supported format (CSV/XLSX spectrum + trend, UFF/UNV, WAV, and .mat in every family it accepts — CWRU, MFPT, wind-turbine, MAFAULDA) through the running webapp at least once. **A CLI/`vib analyze`/direct-adapter run does NOT count as product coverage** — the CLI keeps the real filename, but the webapp stores every upload as `upload.<ext>`, so filename-coupled adapter logic (fault class, load, expected block, timestamp, starter-set convention) only fails on the web path. The CWRU nameless-upload bug (eval-only `filename_to_expected` reached through the upload dispatch; same class as the Phase 7B wind-turbine fix) shipped precisely because CWRU `.mat` was only ever tested via the CLI. `tests/test_upload_nameless.py` now pins the class rule (every format parses a stem-`upload` file), but the standing rule is the human check: drive it through the browser. **Session E extends this: product-path verification must also include at least one real-browser *multi-axis* upload — three files with three directions (radial-H / radial-V / axial) through the live MEASUREMENTS section — since the multi-file form, per-slot direction selectors, and empty-slot handling (the JS deletes untouched `file_2`/`file_3` parts) only exercise on the browser path; a `TestClient` `files={...}` post does not reproduce a browser's empty-file-part behavior.**

**Standing rule — at least one real-browser session per release (Part C, every phase):** product-path verification must include a human session in an actual web browser — not a test client, not `curl`, not a headless screenshot pass (`TestClient`, `httpx`, and Playwright/Chrome-headless do NOT count as a browser). One live session per release: submit the upload form, view the drafted PDF inline in the browser's own PDF viewer, save it to disk, and refresh the report link. Browsers do things test clients never do — inline PDF viewers and download managers issue follow-up and ranged GETs on separate connections, so a report endpoint that behaved for a test client's single GET can still be broken for a human. The download-semantics bug (hotfix-1: `GET /report.pdf` purged the job, so a browser viewer's follow-up requests all 410'd) passed every `TestClient` test and only showed up in a real browser on the MFPT job. Reports are now served idempotently within their TTL; the human refresh-and-save check is what proves it.

**Standing rule — test gates capture pytest's exit code directly, never through a pipe:** a green `EXIT` obtained through `| tail`, `| tee`, `| grep`, or any other pipeline is **not evidence** — a shell pipeline reports the *last* command's status, so `pytest -q | tail -20` exits 0 while the suite is red. Capture it directly (`pytest -q > out.txt 2>&1; echo "EXIT=$?"`) and read the summary, or check for `FAILED` lines. This bit twice in one session: a full-suite run was reported green on a `| tail` exit code while 4 `test_webapp_e2e` tests were failing, and a killed background run reported exit 0 because `tail` saw EOF cleanly. `deploy/deploy.sh`'s gate does not pipe (and the script sets `pipefail`) — keep it that way; the failure mode is silent and that gate is the last thing between a broken commit and production. Corollary: **`-q` on the command line stacks with the `-q` already in `addopts`**, making `-qq`, which suppresses the `N passed` summary line entirely — so "no summary line" is not "no tests ran". **Session CI-1 extends the rule to CI:** every
capture in `.github/workflows/ci.yml` uses `set +e` (a step with no `shell:` key runs under
`bash -e {0}`, so an unguarded failing command kills the shell before the postmortem prints —
a red X with zero test output, which is what this workflow's first real run produced), writes
`echo "EXIT=$?" >> f` into the file, and keeps `grep -q "^EXIT=0$"` LAST so it is the step's
verdict. Never `|| true`, which substitutes true's status and writes `EXIT=0` on every
failure. And a **missing** `EXIT=` line is a failure in its own right, not a zero — an absent
line means the capture was truncated, i.e. the run was killed (`scripts/gate/ci_summary.py`
treats it that way).

**Standing rule — one full suite at a time on the box, and no test may hold it silently (Session SUITE-LOCK, law #9 made mechanical):** a full run (**≥ 2000 collected**, `tests/conftest.py` `FULL_RUN_MIN_TESTS`) takes a non-blocking `flock` on `/tmp/vib-agent-suite-<uid>.lock` and refuses in one sentence naming the other holder; **targeted runs — including every `pytest -k …` — never touch it**, and neither does `--collect-only`. `scripts/gate/gate.sh` takes the same lock on **fd 9** and hands the descriptor down through `VIB_SUITE_LOCK_FD` (verified by inode, not trusted as a flag), which is what stops the gate refusing the suite it runs itself; it also **refuses to start at all** if `ps` finds a `_render_child` or a `pytest` on the box (`GATE_BOX_CHECK=0` overrides — the gate's own pins need it, since they run the gate from inside pytest). **Use `ps`, never `pgrep`, for that check**: macOS `pgrep` excludes the caller *and all its ancestors*, so a `pgrep` version is green on this laptop and red on the droplet. Every test is separately bounded at **900 s** by `faulthandler.dump_traceback_later(exit=True)`, writing to `/tmp/vib-agent-wedge-<pid>.log` — a file, because `_exit(1)` under pytest's fd capture loses stderr, and the file is deleted at session end unless something actually dumped. **There is deliberately no graceful per-test timeout.** A SIGALRM that raises while the main thread is in `Thread.join(timeout=…)` — where every hang-prone test here waits — trips `Thread._wait_for_tstate_lock`'s bpo-45274 handler, which releases a *live* worker's tstate lock and marks it stopped: `is_alive()` then reports `False` for a thread that is still running and still holding `render_lock`, so `assert not t.is_alive()` passes on a stranded worker. Measured, and pinned in `tests/test_suite_lock.py::TestWhyThereIsNoGracefulBound`. A `_render_child` in state `UE` (macOS) / `D` (Linux) still means **reboot, not kill**.

**Standing rule — GitHub CI is the N/1, not the laptop (Session CI-1):** the authoritative
full-suite run is the `summary` job in `.github/workflows/ci.yml`, over the **merge commit**
a `pull_request` produces (`refs/pull/N/merge`, asserted to be a 2-parent merge whose first
parent is the PR base — GitHub gives no merge ref when the PR conflicts). Sessions still run
targeted tests only; the difference is that no full suite need ever run on the operator's box
again. CI reaches the ledger's own number because the four things that kept it reduced are
fixed: `[db,auth]` and pypdf are installed, gitleaks is on the runner, and `data/` (1.6 GB)
plus the `references/` PDFs arrive as checksum-verified GitHub release assets
(`scripts/gate/assets.sh`, tag `assets-v1`, manifest `scripts/gate/ASSETS.sha256`). Before
this session CI reported **2825 passed / 92 skipped** against the laptop's **3252 / 1**, and
**322 of the missing tests never even collected** — 18 whole test files
`importorskip("sqlalchemy")` at module scope, and a module-level skip books ONE skip and
removes the file. A green CI run was green over a suite 11% smaller and its summary line
could not say so.
- **`pytest --shard k/N`** (`tests/conftest.py`) runs bin *k* of a deterministic whole-file
  bin-pack; CI uses 4. Whole files, because module-scoped state is real here (the autouse git
  fixture in `test_secrets_hygiene.py`, the `render_lock` serialisation in the render_proc /
  render_serial / charts_threading trio). Measured per-file weights live in
  `scripts/gate/shard_weights.json` — generated by Session CI-2 from run `34369841287`'s four
  `--durations=0` logs (135 files, 1714.6 s) and **committed**. They change **balance only** —
  membership is a partition either way, and that is what `summary` asserts. Balancing by test
  COUNT was the wrong unit: it put shard 3 at 788 s against shard 1's 141 s. By seconds the
  worst bin is 576 s, and it is `tests/test_purge_order.py` **alone** — that one file is 34% of
  the suite's wall time, so it is the floor, and raising `env.SHARDS` buys nothing until it is
  split. Refresh with `scripts/gate/shard_weights.py --run <id> shard-*/pytest.out`.
  **A malformed `--shard` is a `UsageError`, never a silent full run** (`--shard=$SHARD` with the variable unset expands to `--shard=`,
  which used to run everything on one runner and report a plausible number for it).
- **The suite lock reads the PRE-shard count on purpose.** `--shard 1/4` over `tests/` is
  still a full run of the box's renderers, so four shards started in parallel on the
  operator's laptop are refused exactly as four full suites are. Without that, `--shard` is a
  hole straight through law #9.
- **`summary` is the required check, and it is red on things no exit code notices:** a shard
  that never uploaded its artifact, a shard that collected zero tests, a capture with no
  `EXIT=` line, shard counts that do not add back up to `meta`'s `--collect-only` total, a
  passed total below the floor `scripts/gate/ci_floor.py` reads from `scripts/gate/floor.json`,
  which only `scripts/gate/floor.py bump <run-id>` writes, or **a skip whose REASON is not on the
  platform's allowlist**
  (`scripts/gate/ci_floor.py::EXPECTED_SKIPS_BY_PLATFORM` — Linux 2, Darwin 4, each entry an
  equality in both directions). That last one is load-bearing: a dataset, a reference PDF, an
  optional extra or a binary that fails to reach a runner shows up there and nowhere else, and
  every other signal stays green. **It was a bare count of 1 until Session CI-2, and it was
  wrong** — Linux really skips 2, the second being CI-1's own `test_ci_assets.py:235`
  (`skipif(sys.platform != "darwin")`). A count also cannot say *which* thing went missing; the
  reason can, and an unlisted skip is now printed with its reason. Two further CI-2 fixes to
  the same instrument: `collected` counts **every** pytest outcome (an xfail is a test that
  ran — three of them made a green run read as a broken partition), and each shard report
  carries conftest's own `bin_size`, so "the split is a partition" and "every test in the bin
  reported an outcome" are checked separately instead of conflated. **`-rs` is now
  load-bearing**: a run that reports skips with no reason lines is red, not vacuously green.
- **`scripts/gate/gate.sh` is unchanged in behaviour** (32 pins green); Session CI-1 only
  factored its portable checks into `scripts/gate/lib/*.sh` so CI runs the same rules rather
  than a second copy. Its stage 3 FAILs on `skipped != EXPECT_SKIPPED` (4 on Darwin since
  D-27, 1 on non-Darwin — Session CI-3 moved it to 2, in the same commit as
  `tests/test_gate_sh.py:80`, and added `TestTheTwoInstrumentsAgreeAboutSkips` so the two
  instruments can no longer disagree silently.). For a corpus check without a
  local full suite, run gate.sh's stage-4 three-liner directly (`run_corpus.py --dataset all`
  + `diff_corpus.py`, RUNBOOK §12) — **not** `GATE_SUITE_CMD=true`, which works but always
  ends `VERDICT: FAIL` on the absent `N passed` line, and a routine that trains you to look
  past a FAIL is worse than no routine.
- **Postgres is reached for the first time.** `scripts/gate/pg_check.py` runs Alembic
  `0001..0004` up, `downgrade base` and up again against a `postgres:16` service container,
  then diffs the migrated schema against `Base.metadata`. No *test* touches Postgres — every
  db fixture is `sqlite:///{tmp_path}` — so a service container changes zero test outcomes;
  this is the check that did not exist. **psycopg is installed by the CI step and never
  declared in `pyproject.toml`**: `tests/test_db1_inert.py:268` reads the file rather than the
  environment, and ROADMAP egress law #6 keeps its letter — `constraints.txt` and
  `deploy/deploy.sh` are byte-unchanged.

**Laws #22–27 — verbatim from `outputs/HANDOFF_2026-09-11.md` §2.** Laws #1–21 are in the
handoffs of 09-06 §2, 09-08 §2 (#9–16) and 09-09 §2 (#17–21) and in the ROADMAP common law, and
all still bind; HANDOFF_2026-09-11 §2 amends #19 (Darwin counts N/4, Linux counts N/2).

22. **Tests read nothing that moves.** No test reads `outputs/*.md` (the ledger floor put a green
    suite into FAIL on a one-line append, `56bddfb`); no test reads the environment it happens to run
    in (colour, TTY, locale are pinned in conftest, not discovered per box — `891c3c9`, `9f1432f`).
    The floor is `scripts/gate/floor.json`, bumped by `floor.py bump <run-id>` from a green master
    run, never by prose. COMBINED ledger lines are record only.
23. **`scripts/gate/` is frozen.** Four sessions in a row fixed the ruler instead of the thing being
    measured. Nobody opens `scripts/gate/` or `.github/workflows/` for two rounds unless master's
    verdict is red for a reason that is provably inside them (read the table first — law #21).
24. **Master only receives what CI graded, byte for byte.** The round shape is: builder STOPs with
    master already merged in (law #18) → operator pushes the branch and opens the PR → CI green on
    that push → `git merge --ff-only <branch>` → push. No `--no-ff` merges into master, no ledger-only
    commits to master, no "master's run will grade it after". CI-3's head check in `meta` turns red on
    any master head that is not the head sha of a green branch run or the merge sha of a green PR run;
    it **detects, it cannot prevent** (branch protection needs GitHub Pro — RUNBOOK rewritten to say
    so). tidy2 landed ungraded on Sep 10 by skipping the first half of the block; that is the last time.
25. **Every branch declares its scope.** First commit on the branch is `scope/<branch>.txt` — the
    globs from the brief's Scope line. The `scope` job fails the PR, naming files, on any path outside
    them except `outputs/SESSION_*.md` and the scope file. A brief's Scope line and its scope file say
    the same thing; chat writes both.
26. **Briefs are committed before the session starts** — `outputs/BRIEFS/<SESSION>.md`, on the branch,
    as the second commit. ROUND5C could not read REPORT-2 against its acceptance without leaving the
    tree. The close-out is read against the file, not against a chat message.
27. **Hot files run serial.** `app.py`, `app.js`, `static/index.html`, `RUNBOOK.md`, anything under
    `scripts/gate/`. Two briefs in one round that both name one of these: the second waits for the
    first to land. "Don't touch each other's block" in parallel briefs (TIDY-1/GEOM-1) worked once by
    luck and hunk-number checks; it is not a rule.

**How a branch lands (laws #18, #24, #25, #26; the exact commands are
`outputs/HANDOFF_2026-09-11.md` §7 A).** The builder's first commit on the branch is
`scope/<branch>.txt` and its second is the brief as `outputs/BRIEFS/<SESSION>.md` — force-added
(`git add -f`), because `.gitignore`'s `outputs/*` excludes that directory; the builder works
inside the declared scope, merges master into the branch before STOP, and STOPs without pushing.
The operator then, in one block, merges master in again, pushes the branch, opens a draft PR
against master and reads the verdict off that PR's own CI run — `ci-summary`'s `VERDICT: PASS`
and every job `success`, `scope` included. Only then: `git merge --ff-only <branch>` on master,
and push. Never `--no-ff`, never a ledger-only or docs-only commit straight to master, never
"master's run will grade it after"; master's own push run re-grades the same sha, and `meta`'s
head check reads red if master's head is not a sha a green CI run executed — it detects, and on
this plan nothing prevents. Two branches in one round land one at a time, in STOP order, the
second merging the master that now contains the first. The round's DONE ledger lines are written
once, on a docs branch, quoting the run ids, and `scripts/gate/floor.py bump <run-id>` moves the
floor from the green master run in the same place. `/round` (§7 A′) is the local fallback only,
for when CI is unavailable.

## Agent procedure (enforced by system prompt)

Fixed order: quality gate → machine context → ISO classify → trend/anomaly (if history) → bearing RCA (if spectrum) → synthesize → recommend measurements. No diagnosis before the gate passes. Every finding carries severity (anchored to the ISO zone) and confidence (computed ordinal, with cited evidence factors) with a stated reason. Honest uncertainty; gate failure → insufficient-data report that still names exactly what to collect (via the same recommendations channel). The `--no-llm` pipeline (`pipeline.run_analysis`) is the reference oracle the agent must match numerically.
