# Contract — one machine, N measurement locations

**Status:** v1, Session INTAKE-2 (2026-09-11); section 2 extended by Session
INTAKEFIX-1 (2026-09-12) with `group_source`/`support_source`; section 3
extended by Session LIMITS-1c (2026-09-12) with `zone_basis`/`iso_zone_would_be`,
and section 2 given a request-side subsection. **Owner of the producer:**
`src/vib_agent/webapp/` (`app.py`, `jobs.py`, `locations.py`).
**Owner of the consumer:** REPORT-3.

This file is what REPORT-3 builds against. It describes the shape a finished job
carries when an analyst measured more than one point on one machine, and it is
written down here — rather than left to be read off `jobs.py` — because the
producer and the consumer are two sessions on two branches and the shape is the
only thing between them.

**Every field below exists in the tree today.** The example payloads are
*measured* from a live `TestClient` run, not composed by hand; if a field is
absent from an example, it is absent because that run did not produce it.

---

## 1 · Why the shape is what it is

A `Case` is one machine at one measurement point (`models.py`), and
`pipeline.run_analysis` takes exactly that. So N locations is N analyses, and
nothing under `pdm_core/` or in `assembly.py` knows this feature exists — the
intake hands each location its own form dict and runs the existing path once per
point (`webapp/locations.py::location_form_dict`).

What that leaves is a reporting question. INTAKE-2 did not answer it —
`report/` belonged to REPORT-3 that round — and **Session REPORTFIX-1 did**. So:

* **location 1 gets the document.** Its spectra, its figures and its evidence
  tables are what the document is built from, and a one-location job is
  byte-identical to the pre-INTAKE-2 product.
* **every other location's NUMBERS ride the job and the wire**, in the shape
  below.
* **the document is MACHINE-level where it speaks for the machine.** Page 1
  carries the worst measured point's zone *with that point named*, one
  conclusion, and the fault named once with every position it was seen at;
  Evidence runs per location in roster order. The one-line status and the
  Executive Summary speak for the worst point too, because a verdict taken from
  location 1 is a clean bill whenever location 1 is the healthy one — which on
  a real route it usually is.

That is a **selection**, never an aggregate: rule 2 below forbids totalling or
averaging across locations, and nothing here does. One point's own numbers are
chosen and the point is named beside them.

**The `app.py::_other_locations_note` stopgap is gone** (INTAKE-2's F-5). It
existed only because the numbers arrived a round before the document could
carry them, and it would now repeat — less well — what the document says. The
function is still defined and still tested; only the call site went.

---

## 2 · Machine-level fields

On `Job` (`webapp/jobs.py`) and on `GET /api/jobs/{id}`.

| field | type | on the wire when | example | meaning |
|---|---|---|---|---|
| `machine_type` | `str` | always (required at intake) | `"motor"` | One of `MACHINE_TYPES` in `adapters/uploads/common.py`: `motor`, `pump`, `fan`, `compressor`, `gearbox`, `blower`, `other`. Never invented — PARTC F-2 was the literal `"motor"` on every upload. Reaches `MachineMeta.type`, which `pdm_core/recommendations.py:179` reads. |
| `iso_group` | `"1" \| "2"` | always | `"2"` | The ISO 20816-3 group the severity was judged against. |
| `iso_support` | `"rigid" \| "flexible"` | with `iso_group` | `"rigid"` | The support class. `f"{iso_group}_{iso_support}"` is the key of the row in `config/iso_zones.json`. |
| `iso_assumed` | `bool` | with `iso_group` | `false` | **`true` means nobody told us.** Neither a rated power nor a mounting was stated for that half, so Group 2 rigid was assumed. A report may print "(assumed)" **only** when this is `true`, and must not otherwise. Present even when `false`: "we were told" is as much a fact as "we were not". |
| `group_source` | `"rated" \| "stated" \| "assumed"` | with `iso_group` | `"rated"` | HOW the group was decided. `rated` — derived from the rated power the analyst entered. `stated` — they picked the group themselves. `assumed` — nobody said, so Group 2 was applied. |
| `support_source` | `"rated" \| "stated" \| "assumed"` | with `iso_group` | `"stated"` | The same three answers for the support class. `rated` here means derived from the declared mounting. |
| `iso_note` | `str` | only when the rated power is below ISO 20816-3's 15 kW scope floor | `"rated power is below ISO 20816-3's 15 kW scope floor; …"` | The standard does not cover machines under 15 kW. The closest row is applied and this says so. Print it verbatim; do not paraphrase a standard's scope. |
| `locations` | `list[dict]` | only when the analyst measured more than one point | see §3 | One entry per location **other than the first**, in the order the form listed them. **Absent**, not `[]`, on a single-location job. |

**Provenance is finer-grained than `iso_assumed`, and Session INTAKEFIX-1 put it
on the wire** (INTAKE-2's F-4). `iso_assumed` is the `or` of the two sources
above, so it can answer *"was anything assumed"* and nothing else — while the
words the report actually prints are *"(group and support class assumed)"*, a
claim about **both** halves drawn from a flag that is true when **either** one
was. A machine whose group came from a 90 kW rating and whose mounting nobody
stated reads, today, as though the group were a guess too. `group_source` and
`support_source` are what let that sentence be true.

They do **not** widen rule 3 below. `iso_assumed` is still the only thing that
gates the word "assumed"; these say which half earned it, so a report may write
*"Group 2 (from the rated 90 kW), rigid support (assumed)"* instead of one
caveat covering a half it does not apply to.

### 2.1 · On the REQUEST — `MachineMeta.thresholds`

Every field above is a **response** field. This one is the opposite direction,
and it is documented here because it is what makes `zone_basis` in §3 ever say
anything but `"iso"`.

| field | type | posted as | example | meaning |
|---|---|---|---|---|
| `thresholds` | `MachineThresholds \| null` | three optional form fields, `limit_ab` / `limit_bc` / `limit_cd` | `{"ab": 5, "bc": 8, "cd": 12}` | The severity zone boundaries **this machine** is judged against, in mm/s RMS, when the analyst set them. All three or none. Machine-level, not per-location: one machine has one alarm policy, and a blank trio is the ISO table. |

Three rules a producer must keep:

1. **All three or none.** A partial trio is refused at the form boundary
   (`app.py::_thresholds_422`) with the sentence `MachineThresholds`' own
   validator wrote, and `thresholds_from_form` fails **closed** to ISO if one
   ever gets past. A machine judged against one stated boundary and two ISO ones
   is judged against nothing anybody chose.
2. **`iso_group` and `iso_support` stay required even with limits set.** They no
   longer decide the zone, but they are what lets `iso_zone_would_be` be
   answered at all — without them `_iso_fallback` returns `None` and the report
   loses the second opinion that proves a custom limit is not hiding a real
   problem.
3. **Nothing on this side sets `threshold_source`.** `pdm_core` stamps it
   (`iso_classify.py::resolve_thresholds`, tier 1). A second authority on which
   tier won is how the two come to disagree.

---

## 3 · A `locations[]` entry

| field | type | present when | example | meaning |
|---|---|---|---|---|
| `label` | `str` | always | `"Motor NDE"` | The point's name, as the analyst typed it. Unique within a machine (enforced at intake, including against location 1's). This is also the `location` half of the trend key `alias\|location`, so a report, a machine page and a stored reading all name the point with one string. |
| `status` | `"ok" \| "gate_fail" \| "unreadable" \| "error"` | always | `"ok"` | `ok` — analysed. `gate_fail` — the data-quality gate failed, so **no diagnosis was made**. `unreadable` — the file(s) could not be parsed. `error` — the analysis raised. Anything other than `ok` carries no numbers. |
| `channels` | `list[str]` | `status="ok"` | `["radial_h"]` | The directions measured at this point, from `assembly.Direction`: `radial_h`, `radial_v`, `axial`. |
| `rpm` | `float \| null` | always | `1780.0` | The speed this point was analysed at. **This can differ from the machine speed** — a gearbox output does not turn at its input's rate — and every order in the analysis derives from it, so a report naming a speed must name *this* one for *this* point. |
| `bearing` | `str \| null` | always | `"6309"` | A catalogue key, or the literal `"geometry as entered"` when the analyst typed the four numbers, or `null` when no bearing was given (in which case the bearing family was not assessed). |
| `iso_zone` | `"A" \| "B" \| "C" \| "D" \| null` | `status="ok"` and a zone was assessable | `"B"` | The severity zone for this point. **Read `zone_basis` before attributing it** — it is an ISO 20816-3 zone only when that says `"iso"`. |
| `zone_basis` | `"iso" \| "custom"` | with `iso_zone` | `"iso"` | Which authority produced the letter: a row of ISO 20816-3, or severity limits somebody set for this machine. `"custom"` covers both a machine-specific limit and a factory-wide default — a number a human chose either way. **Not** `readings.zone_source`, which is a different question (did we compute this reading or import it). |
| `iso_zone_would_be` | `"A" \| "B" \| "C" \| "D" \| null` | with `zone_basis == "custom"`, and only when ISO can answer for this machine | `"D"` | The letter ISO 20816-3 would have given this same `severity_rms_mms`. `null` on the `iso` basis (there the zone IS ISO's answer) and `null` when the machine has no ISO row to fall back on. Withheld, like the zone itself, on a reading `mark_not_assessable` refused. |
| `severity_rms_mms` | `float` | `status="ok"` and a zone was assessable | `2.6703552198162703` | The axis-max velocity RMS in **mm/s** the zone was decided from (`pdm_core/iso_classify.py`). Not rounded here; round at the point of display. |
| `committed_fault` | `str \| null` | `status="ok"` | `"imbalance"` | The committed diagnosis as the MODEL's own id (`Finding.fault`), not a display label — the string a query can group by. `null` means nothing was committed at this point. |
| `trend_point` | `dict \| null` | `status="ok"` and there is a trendable scalar | see below | HIST-1's machine-readable reading, built by the same `worker._trend_point` as location 1's. The browser files it under this point's own trend key. |
| `gate_reasons` | `list[str]` | `status="gate_fail"` | `["sample rate not stated"]` | Why the gate failed, one sentence each. |
| `message` | `str` | `status` is `unreadable` or `error` | `"could not be read"` | What went wrong, in words written for an analyst. |

`trend_point`, when present, is exactly HIST-1's shape:

```json
{
  "severity_rms_mms": 2.6703552198162703,
  "iso_zone": "B",
  "dominant_axis": "y",
  "captured_at": "2026-09-12T01:54:06.835581+00:00"
}
```

`dominant_axis` is `"x" | "y" | "z"` or `null`; the axis mapping is fixed
(axial→`x`, radial-h→`y`, radial-v→`z`). `captured_at` is an ISO-8601 UTC stamp,
and its first 10 characters are the *date* the D-24 replacement rule keys on.

---

## 4 · A measured payload

`GET /api/jobs/{id}` for a motor with two points — a 6206 at Motor DE and a 6309
at Motor NDE, rated 90 kW, one radial-horizontal file each. Copied from a live
run, fields unrelated to this contract elided:

```json
{
  "state": "degraded",
  "machine_type": "motor",
  "iso_group": "2",
  "iso_support": "rigid",
  "iso_assumed": false,
  "group_source": "rated",
  "support_source": "stated",
  "locations": [
    {
      "label": "Motor NDE",
      "status": "ok",
      "channels": ["radial_h"],
      "rpm": 1780.0,
      "bearing": "6309",
      "trend_point": {
        "severity_rms_mms": 2.6703552198162703,
        "iso_zone": "B",
        "dominant_axis": "y",
        "captured_at": "2026-09-12T01:54:06.835581+00:00"
      },
      "committed_fault": "imbalance",
      "iso_zone": "B",
      "severity_rms_mms": 2.6703552198162703,
      "zone_basis": "iso",
      "iso_zone_would_be": null
    }
  ]
}
```

**`zone_basis` is `"iso"` here and `iso_zone_would_be` is `null`, and that is
the common case.** This analyst set no machine-specific limits, so the ISO table
decided the zone and there is no second opinion to offer — on that basis the
letter IS ISO's answer, and a `iso_zone_would_be` repeating it would invite a
report to print the same zone twice. Re-measured from a live run for Session
LIMITS-1c; every other value in this payload is unchanged from INTAKEFIX-1's
measurement, which is itself worth knowing.

**Read that `committed_fault` carefully — it is the contract working.** Both
points were sent the *same spectrum*, whose strongest line sits at the 6206's
outer-race frequency. Location 1 commits a bearing fault; Motor NDE, carrying a
6309, commits `imbalance` instead. Two bearings, two answers, one spectrum. If
the per-location bearing were not reaching the analysis, both would agree —
`tests/test_intake2_locations.py::TestEachLocationGetsItsOwnBearingAndSpeed` is
that assertion.

**Read `group_source`/`support_source` beside `iso_assumed` here — that row is
the whole reason they exist.** The group came from the 90 kW rating and the
support class from the analyst's own select, so `iso_assumed` is correctly
`false`. Had they rated it and said nothing about the mounting, the flag would
be `true` and the report's *"(group and support class assumed)"* would be wrong
about the group. These two fields are what let a consumer caveat only the half
that was assumed.

A single-location job carries **no** `locations` key at all:

```json
{ "state": "degraded", "machine_type": "pump", "iso_group": "2",
  "iso_support": "rigid", "iso_assumed": true,
  "group_source": "assumed", "support_source": "assumed" }
```

**How that payload was produced, stated because it is not what it looks like.**
`iso_group` and `iso_support` are `Form(...)` — REQUIRED — and FastAPI reads an
empty form value for a required `str` as missing, so posting either one blank is
a 422 and **the shipped browser form cannot produce an assumed ISO class at
all** (measured, Session INTAKEFIX-1). The payload above comes from a post whose
group was a word the server does not recognise, which is the case
`resolve_iso_class`'s docstring already names — *"an older client, or a post
built by hand, simply has its rating honoured"*. So `assumed` is a real state of
this producer and not dead code, but it is not a state today's form can reach.
Recorded as a finding rather than changed: making the selects optional is an
intake decision, not a contract one.

---

## 4.1 · What page 1 reads, in each of the four states

A consumer of this contract that renders a health line has **four** states, not
two, and they are reproduced here verbatim from `outputs/SESSION_LIMITS1B.md`
§5, which is where they were ruled and measured. Copy them; do not re-derive
them. All four are from the seeded `bpfo` case — `severity_rms` 5.20 mm/s on
`y`, ISO group 2 on rigid support (boundaries 1.4 / 2.8 / 4.5, so **ISO says
Zone D**) — with a plant limit of 5.0 / 8.0 / 12.0 where one applies.

The branch between the first two is `iso_zone_would_be is None`; the three
numbers are the boundaries actually used, formatted `:g`.

### 5.1 · custom + ISO answers

```
**Health:** Zone B — acceptable per machine-specific limits: overall 5.20 mm/s RMS on the y-axis, above the 5 mm/s Zone A/B boundary. Judged against machine-specific limits (5 / 8 / 12 mm/s RMS) — ISO 20816-3 Group 2 rigid support would give Zone D.

_Machine-specific limits also govern the 1× severity gate for this machine._
```

```html
<p class="lead"><b>Health.</b> Zone B — acceptable per machine-specific limits: overall 5.20 mm/s RMS on the y-axis, above the 5 mm/s Zone A/B boundary. Judged against machine-specific limits (5 / 8 / 12 mm/s RMS) — ISO 20816-3 Group 2 rigid support would give Zone D.</p>
<p class="sheet-rule">Machine-specific limits also govern the 1× severity gate for this machine.</p>
```

### 5.2 · custom + no ISO row (machine has no `iso_group` / `iso_support`)

```
**Health:** Zone B — acceptable per machine-specific limits: overall 5.20 mm/s RMS on the y-axis, above the 5 mm/s Zone A/B boundary. Judged against machine-specific limits (5 / 8 / 12 mm/s RMS); ISO 20816-3 has no zone for this machine.

_Machine-specific limits also govern the 1× severity gate for this machine._
```

The gate sentence **is** present here. The ISO comparison is what is missing, not the plant limit.

### 5.3 · iso — unchanged, byte for byte

```
**Health:** ISO Zone D — unacceptable per ISO 20816-3: overall 5.20 mm/s RMS on the y-axis, above the 4.5 mm/s Zone C/D boundary (Group 2, rigid support).
```

No gate sentence, no `Judged against`, no `would give`. Pinned both ways.

### 5.4 · not assessable (`mark_not_assessable` has fired)

```
**Health:** ISO severity not assessable on this reading — overall vibration is not rated.
```

No zone, no ISO clause, no gate sentence — on **either** basis. `zone_basis` survives on the
reading (it is provenance, SESSION_LIMITS1.md §5.2) and changes nothing on the page.

---

## 5 · Rules a consumer must follow

1. **`status` gates the numbers.** Read `iso_zone`, `severity_rms_mms` or
   `committed_fault` only when `status == "ok"`. A `gate_fail` location has no
   diagnosis, and rendering a blank where a zone would go implies one was
   assessed.
2. **Never total or average across locations.** Each point has its own bearing,
   its own speed and its own zone; a machine-level "overall severity" computed
   from N points is a number nothing in `pdm_core/` produced, and the report law
   forbids a numeric claim without a tool-result source.
3. **`iso_assumed` gates the word "assumed"**, and nothing else does.
4. **`locations` is the OTHER points.** Location 1 is the document's own subject
   and is not repeated in the list. `len(locations) + 1` is the number of points
   measured.
5. **Order is the analyst's.** The list is in the order the form listed the
   locations; do not re-sort by severity. An analyst reading a report against
   their own route sheet is reading in route order.
6. **A label is free text.** Bounded at 60 characters, and it reaches the
   drafting prompt — escape it wherever it reaches markup.
7. **`zone_basis` gates the words "ISO" and "ISO 20816-3" beside a zone**, and
   nothing else does. On `"custom"` the letter is the plant's, the standard did
   not set the boundary that produced it, and naming ISO there is a false
   attribution — print `iso_zone_would_be` instead, which is what ISO *would*
   have said. This rule exists because the producer broke it: Session LIMITS-1b
   F-2 measured `report/generate.py::_sheet_health` building *"ISO Zone B —
   acceptable per ISO 20816-3"* from these scalars for a worst point whose
   boundaries an analyst had typed, on the same line as *"Judged against
   machine-specific limits"*. Closed by Session LIMITS-1c, which is why the two
   fields above are here.

---

## 6 · What is NOT here, and is owed

* **No per-location report.** One `report.pdf` per job. It is machine-level on
  page 1 and carries a section per point (§1); it is not N complete reports,
  and each point's evidence TABLE and figures need the live objects, which the
  wire does not carry — `webapp/` hands those to the renderer in-process.
* **No per-location PDF kept in the browser.** D-26 keeps the report with the
  reading for location 1 only, because there is one report.
* **No `locations` column on the `db` backend — but there IS a per-location row
  now.** A machine's identity there is already `(user_id, machine_alias,
  measurement_location)` — operator ruling R-1, Session INTAKE-2 — so N
  locations is N machine rows and needs no schema change beyond the three
  columns Alembic `0005` added. **Session INTAKEFIX-1 wrote them**: a job that
  reaches `done` or `degraded` records one `machines` row and one `readings` row
  per location whose `status` is `ok` and which produced a `trend_point`, each
  carrying the declared `machine_type`/`rated_kw`/`driven_rpm`. A `gate_fail`,
  `unreadable` or `error` location records nothing — rule 1 below, enforced
  rather than trusted.

  The `jobs` row stays **singular**: its `id` is the job id and its primary key,
  so one analysis is one row however many points it covered, and its
  `machine_id` is location 1's — the point the document was written about.
* ~~**`group_source` / `support_source`** exist on the producer and are not on the
  wire.~~ **Closed by Session INTAKEFIX-1** — both are on the wire and in §2.
* ~~**`zone_basis`** is on the producer's `Reading` and not on the wire, so a
  multi-location report whose worst point carries custom limits attributes its
  zone to ISO~~ (Session LIMITS-1b F-2). **Closed by Session LIMITS-1c** —
  `zone_basis` and `iso_zone_would_be` are on the wire, in §3, and §5 rule 7 is
  the rule they exist for.
* **No per-machine limits column on the `db` backend — but there is now.**
  Alembic `0006` adds `limit_ab` / `limit_bc` / `limit_cd` to `machines`, as
  bounded text like every other card column (`''` means "not provided"), written
  by the same `_machine_for` path as the rest of the declared card and therefore
  stamped on **every** measurement point of the job. They are metadata an
  analyst typed; none of them could hold a measurement.
