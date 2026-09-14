> **Public-repository note.** This document describes the full private project. The webapp's
> commercial surface (accounts, billing, the database layer) and the deployment topology in §5
> are **not** part of this repository — see the root `README.md` for what is published. The
> suite count quoted below is a historical snapshot; the current figure is in the README.
> Everything about `pdm_core/`, the pipeline, the agent loop and the report path applies as
> written, and §7 — the deferred-work register — is the honest limitations list.

---

# vib-agent — System Design Record

**Status: current-state truthful.** Everything below describes what the code does at
`master` today. Where a thing is broken, partial, or deferred, it is written as such —
this document contains no roadmap fiction. Anything not yet built lives in §7 (the
deferred-work register) and nowhere else.

Last updated: 2026-07-29 (pre-launch audit). Test suite at time of writing:
**490 passed, 2 skipped, 1 xfailed**. The xfail is deliberate and load-bearing — see §7, D3.

---

## 1. The shape of the thing

One deterministic analytics core, two front doors into it.

```
        NCD sensor pipeline                    Analyst upload (web)
        (streaming provenance)                 (route provenance)
                 │                                      │
        onboard 3-peak-per-axis                 CSV · XLSX · UFF/UNV
        triplets, already reduced               WAV · MAT (4 families)
                 │                                      │
                 │                             adapters/uploads/*.py
                 │                             (sandboxed subprocess)
                 │                                      │
                 │                             webapp/assembly.py
                 │                             (≤3 files → 1 Case)
                 │                                      │
                 └──────────────┬───────────────────────┘
                                │
                          ┌─────▼─────┐
                          │   Case    │   models.py — the ONE input contract
                          └─────┬─────┘
                                │
        ╔═══════════════════════▼════════════════════════════════╗
        ║  pdm_core/  — pure, deterministic, no I/O, no LLM       ║
        ║                                                         ║
        ║  quality_gate → iso_classify → anomaly → trend          ║
        ║       → bearing_rca → synthesize → recommendations      ║
        ╚═══════════════════════╤════════════════════════════════╝
                                │
                       ┌────────▼────────┐
                       │ AnalysisResult  │   every number the product will
                       └────────┬────────┘   ever state, fixed at this point
                                │
                 ┌──────────────┴──────────────┐
                 │                             │
         --no-llm (CLI)              agent/loop.py (drafting)
         report straight from        LLM writes PROSE ONLY over
         AnalysisResult              the same AnalysisResult
                 │                             │
                 │                    consistency contract
                 │                    (prose re-checked vs numbers)
                 └──────────────┬──────────────┘
                                │
                         report/generate.py
                          markdown → PDF
```

The critical property: **the two paths are numerically identical.** `pipeline.run_analysis`
is the reference oracle; the agent path calls the same `pdm_core` functions through tool
schemas and must produce the same numbers. If they ever diverge, the agent path is wrong
by definition.

### 1.1 Two products, one core

| | **NCD / streaming** | **Upload / route** |
|---|---|---|
| Input | Onboard sensor, 3 peaks/axis triplets | Analyst's file export |
| Threshold profile | `streaming` — **frozen** | `route` — where calibration happens |
| Provenance | Known rig, known units, known scaling | Third-party, units *declared by the analyst* |
| Calibration status | Production-validated against the live Node-RED reference | Tuned against CWRU / MFPT / MAFAULDA / wind-turbine |
| Front door | CLI / `Case` JSON | `webapp/` |

These are genuinely different products sharing a core, not one product with a flag. The
`streaming` profile is validated against a live reference implementation and is never
touched by benchmark work; `route` is the one that moves. Conflating them is not a style
issue — it is the RUN v5 defect (§3.3).

### 1.2 The PeakSet seam

`PeakSet` is the boundary where "how the data was captured" stops mattering and "what the
machine is doing" starts. Everything upstream of it is provenance-specific (an NCD triplet
and a 1673-bin CSV spectrum reach it by completely different routes); everything downstream
is one set of detectors.

The NCD path arrives at a `PeakSet` directly — the sensor already did the peak-picking, so
its kind is `ncd_peaks` (`bearing_rca.py:104`). The upload path arrives with a full
`Spectrum` and `peaks_from_spectrum` reduces it, keeping the **top 3 peaks per axis by
descending amplitude** (`max_peaks_per_axis=3`). That reduction is where the product's two
largest known misses come from — a true-but-quiet fault tone loses its slot to a louder
coincidental peak and never becomes a hypothesis at all (§7, D1/D2).

### 1.3 Spectrum-kind typing

Introduced in Session B (B1) to kill a category error: envelope-domain and
velocity/raw-domain spectra were being fed to the same detectors, so an artifact of
enveloping could present as imbalance. `Spectrum` now carries
`kind ∈ {ncd_peaks, raw_acceleration, velocity, envelope}`, and the detector registry
declares what each accepts:

- **imbalance / misalignment family / looseness / belt / blade-pass / resonance** —
  REQUIRE `raw_acceleration` or `velocity`. **Never** `envelope`.
- **bearing detectors** — accept `envelope` (preferred) and raw.

Where an adapter holds the raw signal (CWRU, MFPT, wind-turbine, MAFAULDA all do), it emits
**both** spectra and `run_rca` builds two contexts, routing each detector family to the
spectrum it is entitled to read.

**Current gap, stated plainly:** the tabular upload adapter hardcodes `kind="velocity"`
(`tabular.py:85`). An analyst who exports an *already-enveloped* spectrum from their
analyzer and uploads it as CSV has no form field to say so, and the file is typed as
velocity — the exact category error B1 was built to prevent, reintroduced at the upload
seam. See §7, D6.

### 1.4 Threshold-profile doctrine

**Never call `load_thresholds()` with no argument in a product path.** The bare call
resolves `active_profile`, which is a default, not a decision. Every entry point names its
profile explicitly:

- webapp → `config/webapp.json` `analysis_profile` (= `route`), read in `app.py`
- CLI → `--profile`, default `route`

This is doctrine because it was a shipped defect. RUN v5 found the entire product silently
analysing uploads on `streaming` because of one bare call — every route-calibrated guard
was inactive in the live service while the benchmark harness reported them working. Pinned
by `tests/test_webapp_e2e.py::TestAnalysisProfileWiring`.

---

## 2. The pipeline, stage by stage

Fixed order. No stage may be skipped, and no diagnosis exists before the gate passes.

**Quality gate** — `pdm_core/quality_gate.py`. Always first. Checks the reading is
physically usable at all: machine actually running, signal present, sample adequacy,
sane RPM. A gate **fail** makes the only valid downstream output an insufficient-data
report naming what is missing. This is not an error path; it is a product output with its
own template and its own recommendations.

**ISO classification (Layer 1)** — `pdm_core/iso_classify.py`. ISO 20816-3 zone from
broadband velocity, given machine group and support class. ISO severity is a *velocity*
judgement: when a reading is acceleration-only there is no honest zone, and the layer
returns `not_assessable` rather than a number. That distinction is enforced downstream
(§3.3).

**Anomaly (Layers 2 & 3)** — `pdm_core/anomaly.py`. Welford running z-score against a
per-machine baseline, plus IsolationForest. Layer 2 declines to fire until the baseline has
armed (a minimum reading count); an unarmed baseline reports as *not evaluable*, never as
"normal".

**Trend (Layer 4)** — `pdm_core/trend.py`. OLS regression over history with projection to
the next ISO zone boundary. Requires a history series; a single upload has none, so the
layer declines rather than extrapolating from one point.

**Assembly (multi-axis)** — `webapp/assembly.py`. Upload path only, and *between* parsing
and the core rather than inside it. The form takes up to 3 files each with a declared
direction; direction→axis is fixed (`axial→x`, `radial-h→y`, `radial-v→z`). Each file
parses in its **own** sandbox, then the trusted parent remaps each single-axis Case onto its
declared axis and merges. Cross-file integrity lives here, not in `pdm_core`: per-channel
quality gate (failing channels excluded; all-fail → insufficient-data) and cross-file speed
agreement (disagreement → warning; no channel agreeing with the stated RPM → fail-closed).
A single file with a *defaulted* direction is a strict identity pass-through, so its report
stays byte-identical to the pre-Session-E single-file path.

**Bearing RCA (Layer 5)** — `pdm_core/bearing_rca.py`. Computes BPFO/BPFI/BSF/FTF from
bearing geometry and shaft speed, matches spectral peaks within tolerance, and emits a
**committed differential**: `primary_findings` (the calls we commit to) plus `differential`
(candidates the interaction rules suppressed or downgraded, each carrying its adjudication
reason). Three guards stand between a match and a commitment:

- *synchronous-collision guard* — a bearing frequency within `epsilon_sync` (2%) of an
  integer shaft order is indistinguishable from ordinary shaft vibration; it may only be
  committed with non-synchronous corroboration.
- *amplitude floor* — a committed match must stand ≥ 12.73× the spectrum mean.
- *suppression floor* — only a **committed** bearing finding suppresses imbalance;
  differential-grade candidates suppress nothing.

**Confidence rubric** — same module, weights in `config/thresholds.json` `confidence`. A
computed **ordinal** (high/medium/low — no percentages in v1) summed from evidence factors,
each cited in the report. Confidence is derived, never authored.

**Synthesis** — `pdm_core/synthesize.py`. Cross-layer findings with severity anchored to
the ISO zone, and conservative recommendations. Never "run to failure".

**Recommendations** — `pdm_core/recommendations.py` + `config/next_measurements.json`. The
product pillar's second half: whenever evidence cannot resolve the differential, emit the
follow-up measurement that would. The **gate-fail path emits through this same channel** —
bad data produces "here is what to re-capture", not a shrug. So does the Session-E
cross-file fail-closed path (`worker.py::_FAIL_CLOSED_RECS`).

**Drafting pass** — `agent/loop.py`. A plain Messages-API tool-use loop over `pdm_core`
tools. The LLM orchestrates, synthesizes, and writes report language. **It never does
math.** Every numeric claim must come from a tool result. It relays confidence and
recommended measurements verbatim in substance — it may rephrase, never invent, upgrade, or
extend.

**Consistency contract** — enforced around the drafting pass. Checks the published **prose**,
not merely the model's structured echo block: a fault named as the diagnosis must be one the
analysis actually committed, at the tier the prose claims, and a confidence word next to a
fault must equal its computed confidence. Violation → retry → degrade. Session D added the
prose-level check after a healthy-baseline report asserted a committed fault the analysis
never produced — the echo block was honest while the narrative was not.

**Render** — `report/generate.py`. Jinja2 → markdown → PDF via two paths tried in order:
(1) `markdown` + `weasyprint`, (2) `pandoc` + `tectonic`. Neither available → markdown only
plus a notice; **never an error**. On this dev machine weasyprint is installed without its
native pango/cairo libs and fails with `OSError` partway through import, so the
pandoc/tectonic path is what actually runs locally.

**Purge** — `webapp/worker.py::_keep_only_report` + `jobs.py`. At completion the raw upload
and every intermediate are deleted, leaving only `report.pdf` (plus `trace.jsonl` if the
analyst ticked consent *and* `RETAIN_TRACES=true`). Deletion of intermediates is
deliberately **decoupled from the download** — the report is served idempotently within its
TTL. That decoupling is a bug fix, not a design flourish: see §3.4.

---

## 3. Trust architecture

This is the part of the system that exists because a plausible-sounding wrong answer is
worse than no answer. Each mechanism below traces to a specific incident.

### 3.1 The determinism boundary

**The LLM never computes.** All numeric analysis lives in pure, tested functions in
`pdm_core/` — no I/O, no globals, no LLM anywhere in that package — exposed to the agent as
tools. The model receives computed results and writes prose about them.

This is enforced structurally, not by instruction: the agent has no calculator, no code
execution, and no access to raw signal arrays. It cannot compute a fault frequency even if
prompted to. `pipeline.run_analysis` (the `--no-llm` path) is the reference oracle, and the
agent path must match it numerically because it calls the identical functions.

### 3.2 Validation record — including the misses

Every number measured; every miss stated as a miss. Full detail in
`outputs/validation_summary.md`, surfaced publicly at `/validation`.

| Dataset | Result | Honest reading |
|---|---|---|
| **CWRU** bearing dx | **20 / 24** committed race calls correct; **0 / 4** false calls on healthy | The 4 misses are one fault condition, not scatter: all four load cases of OR014@6. Root-caused, not excused — Smith & Randall rate record #197 "clearly diagnosable", so this is a genuine gap. |
| **CWRU** oversized (0.028″) | Race **mislocalized** (committed outer, truth inner), HIGH confidence | Action-correct, localization-wrong — same maintenance action either way. Out of *graded* scope (Smith & Randall grade 0.007/0.014/0.021″ only), filed RECORDED (no gate). |
| **MFPT** rig | **17 / 17** correct; **0** false positives on baselines | The last baseline FP was cleared by the amplitude floor. |
| **MFPT** real-world field | **1 / 3 PASS** | Scored two-outcome (commit correctly, *or* report uncertainty + name the resolving measurement). The 2 failures are clean bills of health on known-faulted machines — the worst failure mode this product has. Cause: top-N crowd-out. Fix attempted and **rejected** (§7, D1). |
| **Wind turbine** 50-day | Trend **MET**; L2 **not evaluable**; RCA **BLOCKED** | Blocked on a *data* gap, not code: SKF 32222 J2 roller count is not obtainable from a citable source, and it drives BPFI ~18% — six times the match tolerance. Inventing it would produce a confident meaningless answer, so it stays blocked. |
| **MAFAULDA** healthy | **6 / 10** clean (was 1/10) | The three guards removed false bearing calls at zero true-positive cost. |
| **MAFAULDA** imbalance | **0 / 21** | Deliberate. Committed imbalance on acceleration-only data with axial cross-talk requires phase verification; these report at differential instead. The 1× amplitude *does* rise monotonically with added mass — the signal is real, the gate will not commit without phase. |
| **MAFAULDA** misalignment | **9 / 30** | A real detection-rate gap, reported as such. |
| **Live regeneration** (2026-07-22) | **16 / 16 PASS** | Graded on the real product path, not in tests. |

The honest summary: **bearing diagnosis on rig-quality data is strong; real-world
field data and imbalance/misalignment are materially weaker, and the weakness is
concentrated in one mechanism** (top-N peak crowd-out) that has resisted a tune-safe fix.

### 3.3 Honesty mechanisms → the incident each traces to

| Mechanism | What it does | Traces to |
|---|---|---|
| **`not_assessable` severity** | Acceleration-only readings state severity "unrated" with an explicit Severity & Coverage boundary and a velocity follow-up. Zero ISO-zone claims. | **Phase 7B.** Phase 7 booked five `outcome=done` PDFs as a pass; Phase 7B *read* them and found "ISO Zone A / no action" on acceleration-only, known-faulted machines. Fixed at the source in Session A. |
| **Prose-level consistency check** | Validates the published narrative, not just the structured echo block: named fault must be committed, at the claimed tier, with the computed confidence word. | **Session D.** A healthy-baseline report asserted a committed fault the analysis never produced — echo block honest, prose not. |
| **Explicit no-findings report shape** | A clean reading gets "Committed diagnosis: none — parameters within normal range" plus what *was* screened. | Same incident. A blank report pressures the model to invent a finding; a positively-stated clean result does not. |
| **Degrade, never fail** | Spend cap exceeded / consistency hard-fail / API error → deterministic report with a **visible** note. `error` is reserved for unparseable uploads. | Phase 6 design. An analyst who uploaded a good file must never get nothing back because the LLM was unavailable. |
| **Gate-fail through the recommendations channel** | Bad data produces "here is what to re-capture", via the same engine as a normal differential. | Product pillar. |
| **Committed differential** | Suppressed candidates survive in `differential` with their adjudication reason, rather than vanishing. | Product pillar — the system must show its work on what it *rejected*. |
| **`xfail(strict=True)` trip-wires** | A known divergence is pinned as a strict expected-failure, so a future calibration that silently "fixes" it re-triggers review. | **Multi-axis kit, Case C.** See §7, D3. |
| **Explicit profile selection** | Every entry point names its threshold profile. | **RUN v5.** The whole product silently ran uploads on `streaming`. |
| **Part C standing rule** | `outcome=done` is not a grade. Every drafted PDF is read against known ground truth; every format goes through the **live browser** form. | **Phase 7B** (the severity defect) and the **CWRU nameless-upload bug** — eval-only filename logic reached through the upload dispatch, shipped because CWRU `.mat` was only ever tested via CLI. |

### 3.4 Anti-Goodhart discipline

Recorded here because it is load-bearing architecture, not culture. Across Sessions B, E,
Blind Test A, and the multi-axis kit, the repeated pattern is: **a change that would make
the number go up is reverted and packeted rather than kept.** B2 (window-local peak
selection) was implemented, measured, found to have no tune-safe constant, and reverted.
B5 was implemented, measured, reverted. Multi-axis Case C was left failing under a strict
xfail rather than loosened to pass. Case A1's measured behavior was recorded honestly as
the baseline the trio overturns, rather than re-asserted to match the brief.

The rule that produces this: **every tunable is chosen by a stated mechanical rule on a
tune set, documented with candidate values and margins, written to `profiles.route` with a
`_rationale`, and never revisited after the holdout runs.** One shot. Reverting beats
massaging.

---

## 4. The webapp surface

FastAPI, single process, `--workers 1` (the job registry and rate limiters are in-process
memory; extra workers would each get independent state).

**Request path for a job:**

```
POST /api/jobs (multipart, ≤3 files)
  │
  ├─ per-IP rate limit (middleware)   60 req/min · 10 job-POSTs/hr
  ├─ invite-code resolve              401 if unknown  → code LABEL only, never the code
  ├─ per-code + global daily caps     429
  ├─ queue depth / disk floor         503
  ├─ per-file validate                extension whitelist + 25 MiB cap + magic-byte sniff
  ├─ write to mkdtemp job dir         fixed names: upload{,_2,_3}.<ext>
  └─ 202 {job_id}  →  background task
                        │
                        ├─ parse_in_subprocess (PER FILE, own sandbox)
                        ├─ assembly.merge_channels
                        ├─ worker.process_job → pipeline → draft/degrade → PDF
                        └─ _keep_only_report  (raw upload + intermediates deleted)

GET /api/jobs/{id}          status + UI summaries (labelled strings only)
GET /api/jobs/{id}/report.pdf   idempotent within TTL
```

**Job identity and isolation.** Job IDs are `uuid.uuid4().hex`; each job gets its own
`mkdtemp()` directory. The URL's `job_id` is used *only* as a dict key — the filesystem path
comes from the stored `Job.job_dir`, never from user input. There is no path-construction
from a request parameter anywhere in the job flow.

**The parse sandbox.** Uploads are untrusted input, so parsing never happens inline.
`webapp/parsing.py` spawns `python -m vib_agent.webapp._parse_child` with a wall-clock
timeout (30 s) and an `RLIMIT_AS` memory cap (512 MB), passing paths as **argv elements
only — never through a shell**. The child prints exactly one `PARSE_ERROR: <safe message>`
line on any failure; the parent never sees a raw traceback from untrusted input. This is
also the mitigation for decompression bombs and malformed-binary crashes in scipy/openpyxl
(§5).

**Data lifecycle.** **Scoped to `STORE_BACKEND=browser`, which is the default and what
production runs:** nothing is persisted beyond a job, and there is no database — an in-memory
registry only. Raw uploads and intermediates are deleted at job completion; `report.pdf`
survives until the TTL sweep (60 min, clock re-anchored at *completion* so queue time doesn't
eat the window). The app log carries one metadata line per job — timestamp, code **label**,
kind, size, duration, token cost, outcome — and never filenames, file contents, machine
aliases, or client IPs. That line is rotated daily and kept **14 days**
(`deploy/setup_server.sh`).

*This paragraph predates DB-1 and was written when it was unconditional; RULED D-22 amended
common law #8 and made it backend-dependent.* On `STORE_BACKEND=db` there **is** a database
(`webapp/db/`, SQLAlchemy + Alembic), holding account, machine, reading and job **metadata**
— and still no measurement data: `tests/test_db1_schema.py` walks `Base.metadata` and refuses
any column that could hold a spectrum, an amplitude array or report bytes. The measurement
half of the sentence above holds on both backends; the "no database" half does not, and
`static/privacy.html` flips with the flag in the same commit as any column that changes it.

**No request-level IP logging exists, in the application or the proxy.** Uvicorn's access
log is disabled (`--no-access-log` in the unit — it would otherwise write real visitor IPs
into `app.log`, since Caddy sets `X-Forwarded-For`), and `deploy/Caddyfile` configures **no
`log` directive**, so Caddy writes no access log at all. Abuse mitigation is therefore
**stateless**: the per-IP sliding-window rate limiter in `hardening.py` holds addresses in
memory only and never logs them. This is deliberate — minimal-by-default. Short-retention
access logging is the documented **escalation** path if targeted abuse ever warrants it, to
be decided then and disclosed on `/privacy` at that time.

*Scope of that claim:* it covers application and request logs. System-layer security logs
(sshd/journald authentication records, `ufw`, `fail2ban`) do exist and are out of scope —
they are host administration, not visitor request logging.

---

## 5. Deployment topology

```
              Internet
                 │  :443 TLS (auto-HTTPS, Let's Encrypt)
         ┌───────▼────────┐
         │     Caddy      │   deploy/Caddyfile
         │                │   · request_body max_size 80MB
         │                │   · direct-IP :80 / unknown Host → bare 404
         │                │     (no-SNI HTTPS to the raw IP is refused at
         │                │      the handshake — `tls internal` has no name
         │                │      to sign for; app never served either way)
         │                │   · HSTS present but COMMENTED
         │                │   · CSP deliberately NOT set (app owns it)
         │                │   · NO access log (no `log` directive) — see §4
         └───────┬────────┘
                 │  127.0.0.1:8000
         ┌───────▼────────────────────────────────┐
         │  systemd: vibagent.service             │
         │  user vibagent (--system, nologin)     │
         │  uvicorn --workers 1                   │
         │                                        │
         │  sandbox:                              │
         │   NoNewPrivileges    ProtectSystem=strict
         │   ProtectHome=yes    PrivateTmp=yes    │
         │   MemoryMax=1500M    RestrictAddressFamilies
         │   ReadWritePaths=/var/log/vibagent ONLY│
         └───────┬────────────────────────────────┘
                 │
      ┌──────────┴───────────┬──────────────────┐
  /opt/vibagent/app     /etc/vibagent/env   /var/log/vibagent
  (checkout + .venv)    root:root 0600      app.log, logrotate 14d
                        ANTHROPIC_API_KEY
                        INVITE_CODES
                        CONTACT_EMAIL
                        RETAIN_TRACES
                        APP_ENV
```

**Why `/opt` and not `/home`:** `ProtectHome=yes` hides `/home` from the service. The app
cannot live under a directory the sandbox is hiding, so v6-A moved the checkout to `/opt`
to keep the sandbox honest rather than weakening it.

**Host hardening** (`deploy/setup_server.sh`, idempotent): `ufw` (22/80/443, default deny),
key-only ssh behind a loud confirm-first banner, unattended security upgrades, `fail2ban`
sshd jail, 1 GB swap, weasyprint native deps, logrotate.

**Deploy** (`deploy/deploy.sh`): fetch ref → install into venv → **run the full test suite
on the server** → restart → poll `/healthz`. Fails closed — if tests fail or health doesn't
come up, the old process keeps running and the rollback command is printed.

**Data at rest, by location:**

| Location | Contents | Lifetime |
|---|---|---|
| `PrivateTmp` job dir | raw upload, intermediates | deleted at job completion |
| `PrivateTmp` job dir | `report.pdf` (+ `trace.jsonl` if consented) | TTL 60 min from completion |
| `/var/log/vibagent/app.log` | job metadata, labels only | 14 days (logrotate) |
| Caddy access log | **does not exist** — no `log` directive is configured | — |
| request-level IPs | **nowhere.** Uvicorn access log disabled; rate limiting is in-memory only | — |
| `/etc/vibagent/env` | secrets | until rotated |
| anywhere else | *nothing* | — |

---

## 6. Scope boundaries

Standing prohibitions (`CLAUDE.md` is authoritative). No database. No auth beyond invite
codes. No RAG/vector stores/embeddings. No CMMS integrations, file watchers, or schedulers.
No Docker or orchestration. No dashboards, admin panels, or multi-file batch UI. No
speculative abstraction layers.

Three scope-guard amendments have lifted narrow parts of the original "no web UI / no
deployment config" rule: **Phase 6** (`webapp/`, `adapters/uploads/`, `RUNBOOK.md`),
**v6-A** (`deploy/`, `scripts/prod_smoke.py`, `webapp/hardening.py`). Every other boundary
holds *inside* those surfaces too. `pdm_core/` and `agent/` are untouched by all of them —
the webapp calls them exactly the way the CLI does and adds no analysis logic of its own.

---

## 7. Known limitations & deferred-work register

**This is the single place future sessions look.** Everything deferred is here, with its
status and the trigger that should un-defer it. Nothing here is scheduled; each entry
requires a tune-set + holdout pass before it can land.

### The detector-refinement cluster

These seven are **one cluster**, not seven independent tickets. D1 and D2 share a single
root mechanism (top-N amplitude crowd-out in `peaks_from_spectrum`); D3 and D4 share the
imbalance dominance gate. Fixing them separately is how you get a Goodhart result.

| ID | Item | Status | What's actually wrong | Trigger to revisit |
|---|---|---|---|---|
| **D1** | **B2 — window-local peak selection** | **Attempted, measured, REJECTED.** `outputs/REVIEW_PACKET_session_b_B2.md` | Search each computed fault frequency's tolerance window directly instead of relying on global top-N. Every variant that surfaces the true weak tone (CWRU OR014@6 at rank 41/1673; MFPT oil-pump, planet) also puts phantom bearing findings on genuinely healthy machines. **There is no tune-safe prominence constant** — the true signal is quieter than coincidental noise-floor peaks on healthy rigs. | A *discriminating* feature beyond amplitude/prominence — e.g. harmonic-family coherence, sideband structure, or envelope-domain corroboration at the same frequency — that separates weak-true from loud-false without a bare threshold. Not another constant sweep. |
| **D2** | **Blind Test A — 1× sideband discriminator** | **Deferred, PAIRED WITH D1.** `REVIEW_PACKET_blind_test_A.md` | Two mechanisms. (a) `enrich_with_sidebands` is one-directional and identity-blind: ±1×-shaft sidebands are treated as *corroboration* of whatever bearing fault was matched (+1.0 weight), but physically they are the **rotating-defect (inner-race)** signature — around a BPFO match they are counter-evidence, and here they pushed a wrong outer-race call to HIGH. (b) The true BPFI never entered adjudication at all — same top-N crowd-out as D1. | Cannot land without D1 (there is no inner-race hypothesis to adjudicate until the tone survives peak selection). The sideband rule additionally needs gating against the CWRU/MFPT inner-vs-outer tune set — an outer-race fault with incidental structural 1× modulation must not be flipped to inner. |
| **D3** | **Absent-vs-measured-zero (imbalance gate)** | **Open — the higher-value fix.** `REVIEW_PACKET_multiaxis.md` | `axial_radial_ratio = v_axial / v_radial_max`, and a *missing* axial channel yields `v_axial = 0.0`, indistinguishable from a genuinely measured near-zero. `0.0` → infinite dominance, so the gate is **structurally unable** to withhold imbalance from a lone radial channel. A single radial file commits imbalance regardless of what an axial measurement would have shown. | Landing this flips multi-axis Case A1 to a withheld/differential imbalance ("needs an axial to confirm") — which matches the product's say-what-to-collect pillar. It will also break the strict xfail on Case C, **by design**, forcing this packet back to review. |
| **D4** | **Peak-aware dominance (imbalance gate)** | **Open — paired with D3.** `REVIEW_PACKET_multiaxis.md` | The gate compares full-spectrum **Parseval** velocities while the dominance was designed in terms of **1× peak** amplitude. A quiet axial channel carrying broadband content scores 2.5× its own 1× tone, so a designed 16.6× peak dominance collapses to 6.80× Parseval — under the 7.5 gate. Imbalance is refused and the misalignment family fires instead. | Same trigger as D3. Pinned by `tests/test_multiaxis.py` Case C as `xfail(strict=True)` — if it ever passes silently, the suite fails and forces review. |
| **D5** | **OR014@6 weak-signal gap** | **Known limitation, root-caused, not excused.** `outputs/validation_summary.md` | All four load cases. BPFO peak stands at only 3.5–8.2× the spectrum mean — below the 12.73× amplitude floor, and below even a known false positive on another rig (6.14×). Literature (Smith & Randall 2015, record #197) rates it Y2 "clearly diagnosable", so this is a real gap in *this* pipeline. | Subsumed by D1 — it is the same crowd-out mechanism. Do not attempt a floor adjustment: the margins prove no threshold admits it without admitting louder false positives. |
| **D6** | **Enveloped-spectrum intake** | **Open — gap at the upload seam.** | `tabular.py:85` hardcodes `kind="velocity"` on every uploaded CSV/XLSX spectrum. An analyst uploading an already-enveloped spectrum from their analyzer has no form field to declare it, so it is typed as velocity and routed to 1×-family detectors that must never see envelope data — reintroducing at the upload seam exactly the category error B1 removed from the core. | An analyst actually uploads an enveloped export (likely — it is a standard analyzer output). Fix is a form field + honest `kind` propagation, **not** sniffing. Low risk, contained to the adapter + form; does not touch `pdm_core`. |
| **D7** | **Render-timeout test seam** | **Open — test-infrastructure gap.** `REVIEW_PACKET_phase8.md` §4, `MORNING_SUMMARY.md` | Webapp e2e tests flake under full-suite load: `_poll_until_terminal` has a hardcoded **10 s** deadline while tectonic PDF rendering takes **30–60 s** under CPU contention. Every flaking job actually reaches `outcome=done` — the test gives up, the product doesn't. Pre-existing, verified not a regression (stash-tested against baseline), and deliberately **not touched** because raising a test deadline is the kind of edit that hides a real slowdown. Separately, the render-*unavailable* branch (no engine → markdown only) has no injectable seam, so it is exercised only by accident. | Any recurrence in CI, or before relying on the markdown-only fallback in production. Correct fix is an injectable clock/deadline plus a forced-unavailable seam — **not** simply raising the constant. Pure test-infrastructure work; no product behavior change. |

### Other standing limitations

| Item | Status | Note |
|---|---|---|
| **SKF 32222 J2 geometry** | **BLOCKED on a citable source.** `BLOCKED_phase7b_bearing_geometry.md` | Roller count unobtainable — SKF publishes envelope (110×200×56 mm) and contact angle (15.6°) but not the complement; primary literature paywalled. Drives BPFI ~18% across plausible values, six times the match tolerance. Searched twice (Session B Task 2) and **stays blocked**. Unblocks only with SKF published data or the Saidi/Bechhoefer literature, cited in the config `_rationale`. Wind-turbine Layer 5 stays dark until then. |
| **Imbalance needs phase** | **Deliberate, not a bug.** | Committed imbalance on acceleration-only data with axial cross-talk requires phase verification. MAFAULDA 0/21 is the gate working as designed, not failing. |
| **MAFAULDA misalignment 9/30** | Open detection-rate gap. | Reported honestly; no attempted fix on record. |
| **Layer 2 baseline arming** | Structural. | Welford needs 30 readings; the wind-turbine run peaked at weight 29.74. Reports as *not evaluable*, never as "normal". |
| **CSP `style-src 'unsafe-inline'`** | Accepted risk, documented. | 23 self-authored, `esc()`-escaped `style=""` attributes remain. No user markup reaches them. Removing it means refactoring all 23 (including runtime-injected ones) into classes. |
| **HSTS commented in Caddyfile** | Operator decision, pending. | Enable after a stable first week on the cert — hard to walk back. |

---

## 8. Where things live

```
config/                  iso_zones · bearings · thresholds · agent · webapp
                         next_measurements · cwru · mfpt · wind_turbine · mafaulda
src/vib_agent/
  pdm_core/              THE product core — pure, deterministic, no LLM
    quality_gate.py      always first
    iso_classify.py      Layer 1 — ISO 20816-3
    anomaly.py           Layers 2 & 3 — Welford z-score + IsolationForest
    trend.py             Layer 4 — OLS + zone-boundary projection
    bearing_rca.py       Layer 5 — fault freqs, matching, differential, confidence
    synthesize.py        cross-layer findings + conservative recommendations
    recommendations.py   follow-up measurement rules (unresolved differential OR gate-fail)
  pipeline.py            canonical fixed-order orchestrator — the reference oracle
  models.py              Pydantic contracts for everything crossing a boundary
  agent/                 tool schemas · Messages-API loop · system prompt
  adapters/
    cwru.py              CWRU .mat → Case (benchmark path)
    uploads/             CSV/XLSX · UFF · WAV · MAT(×4 families) · units · common
  report/                Jinja2 → markdown → PDF (two render paths, best-effort)
  webapp/                app · jobs · worker · assembly · parsing · _parse_child
                         security · spend · hardening · static/
  cli.py                 typer: demo / analyze / eval
eval/                    8 seeded cases + runner.py scorecard
tests/                   490 passed · 2 skipped · 1 xfailed (deliberate — §7 D3/D4)
outputs/                 generated reports + the machine-written results docs
deploy/                  vibagent.service · Caddyfile · setup_server.sh · deploy.sh
docs/                    ARCHITECTURE.md (this file) · LAUNCH_CHECKLIST.md
RUNBOOK.md               deployment narrative around deploy/
```
