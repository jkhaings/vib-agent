# vib-agent

**A vibration-analysis agent for rotating machinery. Data file in, analyst-grade draft report out — and the LLM never does the math.**

Every number in every report is computed by pure, deterministic, unit-tested Python. The
language model gets a finished result object and writes prose over it, then the prose is
machine-checked back against the numbers. If the two disagree, the job fails rather than ships.

This is a working prototype — roughly 29k lines of Python — built solo over about two months. It is no longer maintained, and
it is published as an engineering portfolio piece. The validation section below reports what it
does badly as plainly as what it does well.

---

## The shape of it

One deterministic analytics core, two front doors.

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

The critical property: **the two paths are numerically identical.** `pipeline.run_analysis` is
the reference oracle; the agent path calls the same `pdm_core` functions through tool schemas
and must produce the same numbers. If they ever diverge, the agent path is wrong by definition.

---

## Quickstart

Needs Python 3.11+. Clone it — don't `pip install` it: `config/` and `knowledge/causes.yaml`
are resolved relative to the repo root, so the product wants a checkout, not a wheel.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[web,pdf]"

vib demo --no-llm --pdf        # seeded BPFO case → outputs/demo/
```

That runs the whole deterministic pipeline on a synthetic bearing-fault case and writes a
markdown report and a PDF. **No API key, no dataset download, no network.**

To use the drafting path (writes the prose with Claude):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
vib demo                       # same numbers, drafted narrative
vib analyze mycase.json        # your own Case JSON
```

To run the web app:

```bash
pip install -e ".[web,pdf]"
INVITE_CODES="localtest:local" CONTACT_EMAIL="you@example.com" \
  uvicorn vib_agent.webapp.app:app --port 8000 --workers 1
```

`--workers 1` matters: the job registry and rate limiters are in-process memory.
Upload `src/vib_agent/webapp/static/sample.csv` to see the full flow.

### Running the tests

```bash
pip install -e ".[web,pdf,dev]" "pypdf==6.16.1"
pytest
```

`pip install -e ".[dev]" && pytest` will **not** work — 60-odd test modules import
`fastapi.testclient` at module scope, so the `[web]` extra is required to collect them.
The suite needs no API key (a fake Anthropic client is injected; zero real calls) and no
datasets — the benchmark tests skip cleanly when `data/` is absent.

---

## What it actually does

**1. Quality gate first, always.** `pdm_core/quality_gate.py` runs before any diagnosis. A gate
failure means the only valid output is an insufficient-data report that names exactly what to
re-collect. There is no path from bad data to a confident answer.

**2. Six analysis layers, fixed order** (`pipeline.py`):

| Layer | Module | What it does |
|---|---|---|
| 1 | `iso_classify.py` | ISO 20816-3:2022 broadband velocity zone, 3-tier threshold resolution |
| 2 | `anomaly.py` | Welford time-weighted z-score against a rolling baseline |
| 3 | `anomaly.py` | Isolation Forest (declines without enough history — see limitations) |
| 4 | `trend.py` | OLS regression on daily means + zone-boundary projection |
| 5 | `bearing_rca.py` | Bearing fault frequencies, peak matching, seven detectors, committed differential + confidence rubric |
| 6 | `staging.py` | Damage-stage estimate from already-computed evidence, with an explicit honesty floor |

**3. A committed differential, not a list of maybes.** RCA emits `primary_findings` (the calls
the system commits to) plus `differential` (candidates the interaction rules suppressed, each
with the reason it was downgraded). Confidence is a computed ordinal — high/medium/low, no
fake percentages — from a config-weighted factor rubric.

**4. It says what to collect next.** Whenever the evidence can't resolve the differential, a
separate recommendations engine emits the specific follow-up measurement that would. Gate
failures emit through the same channel: bad data in, "here's what to re-capture" out.

**5. The LLM is fenced in.** `agent/loop.py` calls the pipeline exactly once; that result is
ground truth. The model gets a read-only drill-down tool budget and writes prose. Then
`agent/consistency.py` runs **eleven** checks over the draft — title line, echo block, zone
language, fault terms, confidence binding, numeric quotes, stage language, cause language,
baseline claims, measured channels. One retry with the diff appended; a second mismatch is a
hard failure, never a silent accept.

### Input formats

CSV and XLSX (spectrum or trend schema), UFF/UNV (dataset 58), WAV (scaled or unscaled), and
MATLAB `.mat` in four families — CWRU, MFPT, wind-turbine, MAFAULDA — disambiguated by peeking
at top-level variable names rather than by filename. Plus a `.txt`/`.dat`/`.asc` lane where a
model proposes a parse recipe from a bounded sample, a deterministic executor runs it, and a
**mandatory verification gate** tests the hypothesis before any diagnosis is allowed.

Every adapter converts to mm/s RMS through one units module and states the conversion it
applied in the report. Units are always explicit form fields — never inferred.

---

## Sample output

Two reports in [`outputs/demo_package/`](outputs/demo_package/), chosen as a contrasting pair
because together they are the whole thesis:

| File | What it shows |
|---|---|
| [`bpfo_synthetic/report.pdf`](outputs/demo_package/bpfo_synthetic/report.pdf) | A velocity channel is present → commits **ISO Zone D** and an outer-race fault at high confidence |
| [`cwru_or021_6_0/report.pdf`](outputs/demo_package/cwru_or021_6_0/report.pdf) | Acceleration-only data → commits the **fault**, and explicitly **declines to rate severity**, because ISO 20816 severity is a velocity judgement |

That second behaviour is the point. Most of this problem is knowing what you are not entitled
to say.

Screenshots of the running web app are in [`docs/samples/`](docs/samples/) — real browser
captures of the intake form, the progress rail, a Zone-D result, and a validation error —
alongside four more rendered reports.

---

## Validation

174 files across four public datasets, driven through the deterministic pipeline on the same
threshold profile the web app uses. **No detector, threshold, or config value was changed for
this run — this is validation, not calibration.** Full method and per-file tables:
[`outputs/validation_summary.md`](outputs/validation_summary.md).

| Dataset | Result |
|---|---|
| **All four** | 174 files, 174 analysed, **0 errors** |
| **CWRU** (40 files) | **20/24** inner/outer-race diagnoses correct · **0** false bearing calls on healthy files |
| **MFPT rig** (20 files) | **17/17 (100%)** · **0/3** false positives — detectors calibrated on one rig, applied unchanged to another |
| **MFPT field machines** (3) | **1/3** |
| **MAFAULDA imbalance** (21) | **0/21** |
| **MAFAULDA misalignment** (30) | **9/30** |
| **MAFAULDA healthy** (10) | **6/10** reported clean |
| **Wind turbine** (50-day run-to-failure) | Rising trend detected (+0.011 g/day); bearing ID **blocked** |
| **Report rendering** (25-file sample) | 25/25 rendered · 25/25 with every evidence figure · 25/25 numeric-consistency |

### The misses, and why they are in this table

- **The 4 CWRU misses are one condition**, not scattered failures: every load case of the
  0.014" centred outer-race defect. Its tone stands at 3.5–8.2× the spectrum mean — below the
  amplitude floor, and below a known false positive on another rig. The literature rates it
  "clearly diagnosable", so this is a real gap in *this* pipeline, not an excused case.
- **Two of the three MFPT field machines got a clean bill of health while faulted.** That is
  the worst failure mode this product has, and it is reported as a failure. Root cause is
  top-N peak crowd-out; the obvious fix was attempted, measured, and **rejected** — every
  variant that surfaced the weak true tone also put phantom findings on healthy machines.
- **Imbalance is 0/21 by design.** Committing imbalance from a single acceleration channel
  without phase information is something this system will not do. The 1× amplitude does rise
  monotonically with added mass, so the signal is plainly there — the gate refuses to convert
  it into a committed diagnosis. The practical consequence is a 0% detection rate on that
  dataset, and that is how it should be read.
- **Four healthy MAFAULDA files get a low-confidence misalignment finding.** False positives
  on healthy machines. Not zero, and not hidden.

### The severity boundary

All four datasets are acceleration-only, and ISO 20816 severity is a velocity judgement.
**Not one of the 174 files receives an ISO severity zone.** Every report states severity as
*unrated*, carries a coverage boundary naming what was and was not assessed, and recommends the
broadband velocity capture that would establish it.

So this scorecard validates **fault identification, not severity classification**. No ISO zone
accuracy claim can be made from this corpus, and none is made.

---

## Engineering notes

- **3,533 tests** across 125 modules, plus 23 JavaScript harnesses for the front end.
- **The analytics core is pure** — no I/O, no globals, no LLM anywhere in `pdm_core/`, which
  is what makes the whole thing testable and the agent path checkable against it.
- **Every tunable lives in `config/*.json`** — ISO zone boundaries, match tolerances, bearing
  geometry, confidence weights. Nothing is hardcoded. Constants not yet validated against
  primary sources are marked `"_verify": true`.
- **Two threshold profiles, always chosen explicitly.** `streaming` (frozen, sensor pipeline)
  and `route` (uploaded/third-party data, where calibration happens). A bare
  `load_thresholds()` once silently ran the entire shipped product on the wrong one — every
  route-calibrated guard inactive in the service while benchmarks reported them working. Every
  entry point now names its profile, and a test pins it.
- **Untrusted uploads parse in a sandboxed subprocess** — 30 s timeout, 512 MB address-space
  cap, argv only, never a shell.
- **Native renderers run in a child process.** weasyprint and matplotlib are reached only via
  `python -m vib_agent.report._render_child`. Three native SIGSEGV/SIGBUS faults are on record,
  one of them *with the serialisation lock held* — a lock can serialise renders but cannot
  serialise the garbage collection of the cairo/pango objects a finished render leaves behind.
  A child crash becomes a retryable error instead of taking the server down.
- **Degradation is designed, not incidental.** If the LLM path is unavailable — budget
  exceeded, consistency hard-fail, API error — a web job degrades to the deterministic report
  with a visible note. `error` is reserved for genuinely unreadable uploads.

---

## Known limitations

The honest register is [`docs/ARCHITECTURE.md` §7](docs/ARCHITECTURE.md), which carries each
open item with its root cause and the trigger that should un-defer it. The headline entry:

> **D1 — window-local peak selection. Attempted, measured, REJECTED.** Every variant that
> surfaces the true weak tone also puts phantom bearing findings on genuinely healthy machines.
> **There is no tune-safe prominence constant** — the true signal is quieter than coincidental
> noise-floor peaks on healthy rigs. Revisit needs a *discriminating* feature (harmonic-family
> coherence, sideband structure, envelope corroboration), not another constant sweep.

Also worth knowing:

- **`vib eval` is not implemented** as a bare command — it exits 1. Only `vib eval --suite cwru`
  and `--suite mfpt` work, and both need dataset-derived case files that are not committed
  (see `scripts/fetch_cwru.py`).
- **Isolation Forest (Layer 3) is a stub** that always reports `not_enough_history`.
- **Layer 2 declines on any single-file analysis** — there is no streaming baseline in a case file.
- **Wind-turbine bearing ID is blocked on a citable source.** The SKF 32222 J2 roller count is
  not published, and it moves the computed inner-race frequency by ~18% — six times the match
  tolerance. Rather than invent it, the agent wrote
  [`BLOCKED_phase7b_bearing_geometry.md`](BLOCKED_phase7b_bearing_geometry.md) and stopped.
  That file is kept in this repo deliberately.

---

## Layout

```
config/            every tunable constant, as JSON
knowledge/         causes.yaml — cause knowledge with per-entry source citations
src/vib_agent/
  pdm_core/        THE product core — pure, deterministic, no LLM
  pipeline.py      canonical fixed-order orchestrator (the reference oracle)
  models.py        Pydantic contracts for everything crossing a boundary
  agent/           tool schemas · Messages-API loop · 11 consistency checks
  adapters/        CWRU · MFPT · MAFAULDA · wind-turbine + uploads/
  report/          Jinja2 → markdown → PDF, charts, child-process renderer
  synth/           deterministic seeded synthetic data
  webapp/          FastAPI: upload → job → poll → PDF
  cli.py           typer: demo / analyze / eval
docs/              ARCHITECTURE.md (system design record) · SECURITY.md · staging.md
references/        the citation register the knowledge layer is built from
tests/             3,533 tests
```

A note on `references/`: it is a **citation register, not RAG**. No embeddings, no vector
store, no retrieval at runtime. Source PDFs are read by a human at authoring time and are never
committed — only `INDEX.md` and `GAPS.md` are. A cause entry with a plausible mechanism and no
index row is a guess wearing a citation's clothes, which is the exact failure that library
exists to prevent.

---

## About the development process

This was built with heavy AI-assisted development, and [`CLAUDE.md`](CLAUDE.md) is the
operating manual written for that collaboration — scope boundaries with dated amendments,
standing rules, and the specific bug each rule was bought with. It is included because it is a
more honest record of how the work actually happened than the commit log would be.

---

## License

**All rights reserved.** No license is granted to use, copy, modify, or distribute this code.
It is published for reading — as a portfolio and reference — not for reuse. (GitHub's Terms of
Service permit viewing and forking within GitHub; that is not a license to use the code.)

The public datasets referenced here are **not redistributed** and are not in this repository.
MAFAULDA (COPPE/UFRJ) and the wind-turbine dataset (Eric Bechhoefer, CC BY-NC-SA 4.0) are used
under their own terms; the reference PDFs behind `knowledge/causes.yaml` are commercially
licensed documents and are likewise not included.
