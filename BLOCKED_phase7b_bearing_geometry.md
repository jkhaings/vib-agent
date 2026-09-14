# BLOCKED (item-level) — Phase 7B: SKF 32222 J2 bearing geometry unobtainable

**Authority:** OVERNIGHT_RUNSHEET.md RULE 4 — "Unresolvable ambiguity: write
BLOCKED_<item>.md with the question, skip that item, continue. Never improvise
around a spec."
**Scope of this block:** ONE item — the geometry-dependent Layer 5 RCA
progression (spec work-item 3's "RCA PROGRESSION" hard assertion). **Everything
else in Phase 7B proceeded**, including the phase's stated primary purpose
(Layers 2/4 on the time dimension). This is not a task-level halt.

---

## The question I need answered

**What are the roller count (Z) and roller mean diameter (Dw) of the SKF
32222 J2 tapered roller bearing?**

With those two numbers I can complete the RCA progression immediately — the
config entry and adapter are already written and wired (`config/wind_turbine.json`
`bearing_key`, currently `null`; drop in a `SKF_32222_J2` entry in
`config/bearings.json` and re-run `scripts/run_wind_turbine.py`).

## Why I could not resolve it myself

The spec's fallback path is: *"Bearing fault frequencies: from repo/example docs
if stated; else compute from SKF 32222 J2 geometry via bearing_frequencies() with
a config entry marked _verify. Never guess silently."* That path presumes the
geometry is obtainable. It is not, here:

1. **The repo states nothing.** `data/wind_turbine/README.md` gives only the
   source (data-acoustics.com bearing-3), the CC BY-NC-SA license, and the
   sensor+tach merge note. No fs, no shaft speed, no bearing geometry.
2. **No file carries metadata.** I checked the variable signature of **all 50**
   `.mat` files: every one contains exactly `tach` and `vibration`. Nothing else.
   The only other repo files are `README.md` and `license.txt`.
3. **RULE 7 bars the lookup.** "Dataset downloads only (MFPT / mathworks GitHub /
   MAFAULDA hosts). Any other network need: BLOCKED note, skip." An SKF catalog
   lookup is exactly that other network need.

## Why I did not just fill in a plausible number

Because it would have been a doctored pass, which RULE 2 names as the only
failure state. Concretely:

- The designation `32222` *does* encode some geometry — `3`=tapered roller,
  `22`=dimension series, bore code `22` → **110 mm bore**, giving the standard
  d=110 / D=200 / T=56 mm envelope, hence pitch diameter Dpw ≈ (110+200)/2 ≈
  **155 mm**. Contact angle for the 322-series is ~14–16°. I am reasonably
  confident of those.
- But **Z and Dw are not encoded in the designation**, and I do not reliably
  know them.
- **BPFI = (Z/2)·(1 + (Dw/Dpw)·cos α)·f_shaft scales directly with Z.** A guess
  of Z=17 vs Z=20 moves BPFI by ~18% — six times the route profile's 3% match
  tolerance. So an invented Z does not produce a slightly-wrong BPFI; it produces
  a meaningless one, and the spec's HARD ASSERTION ("within the final 5 files,
  primary finding is inner-race family") would then be theater: it would pass or
  fail on my guess, not on the detector.

Fabricating the number and marking it `_verify: true` would not have fixed this.
`_verify` exists in this project for *"assumed constants not yet validated against
the user's ISO tables / bearing catalogs"* — i.e. a real catalog value awaiting
cross-check. It is not a license to invent a value I never had.

## What I delivered instead (all of it geometry-free)

The RCA block costs Phase 7B less than it might appear, because the phase's own
stated purpose is the time dimension:

> *"Phase 7B validates the TIME dimension — Layer 2 (Welford anomaly) and Layer 4
> (trend) — on real degradation for the first time."*

Layers 2 and 4 consume **overall RMS only** and need no bearing geometry at all.
So the timeline table (spec: "this table is the phase's core artifact"), the
Welford crossing analysis, and the trend assertions are all delivered and valid.

I also ran a **geometry-independent** substitute for the RCA question — tracking
the dominant envelope families and their order relative to shaft rate across all
50 days, and checking for ±1× shaft sidebands (the structural inner-race
signature). That answers "does a bearing signature emerge, and where?"
descriptively, **without** fitting a fault frequency to the data. When you supply
Z and Dw, the observed orders can be compared to the true BPFI in one step.

Deliberately NOT done: deriving the "bearing geometry" backwards from the
final-day spectrum so that BPFI lands on whatever family is loudest. That is
circular — it would guarantee a pass and prove nothing.

## Status of the affected assertion

**Spec work-item 3, RCA PROGRESSION / "final 5 files inner-race" HARD ASSERTION:
reported BLOCKED — neither PASS nor FAIL.** Recording it as a FAIL would be as
dishonest as recording a pass; the detector was never given the input it needs.
See `outputs/wind_turbine_results.md` for the geometry-independent evidence that
bears on it.

## What unblocks it

One line from you, or a catalog page:

```json
"SKF_32222_J2": {
  "n_balls": <Z>, "ball_dia_mm": <Dw>, "pitch_dia_mm": 155.0,
  "contact_angle_deg": 14.0, "_verify": true,
  "_comment": "SKF 32222 J2 tapered roller, wind-turbine HS shaft (Phase 7B)."
}
```

then set `bearing_key` to `"SKF_32222_J2"` in `config/wind_turbine.json` and
re-run `scripts/run_wind_turbine.py`. Everything downstream is already wired.

---

## Session B UPDATE (2026-07-22) — citation search performed; STILL BLOCKED

Per the Session B spec's conditional side-task ("Resolve … ONLY with a citable
source (SKF published data for 32222 J2 or the Saidi/Bechhoefer literature). Cite
in the config _rationale. No citation found = stays blocked."), a bounded web
search was run this session (5 queries/fetches). Result:

- **Confirmed & citable (SKF):** envelope **110 × 200 × 56 mm**, **contact angle
  15.6°** (SKF 32222 J2 product listings, e.g. klium.com / 123bearing.com). This
  refines the earlier ~14–16° estimate but is NOT sufficient on its own.
- **Roller count Z — NOT confirmable.** Search-engine *aggregations* attribute
  "20 rolling elements" to this bearing in the Saidi/Bechhoefer context, but:
  (1) the primary papers (Saidi & Bechhoefer, *Wind turbine high-speed shaft
  bearing degradation analysis…*, and the *…via spectral kurtosis* follow-ups) are
  paywalled — ResearchGate returned HTTP 403, Semantic Scholar returned no
  extractable content; (2) the MathWorks "Wind Turbine High-Speed Bearing
  Prognosis" example (the dataset's canonical MATLAB home) states a **"20-tooth
  pinion gear"** and gives NO bearing geometry — a direct conflation risk for an
  unverified "20 rollers"; (3) that example uses a data-driven (spectral-kurtosis)
  method and never computes BPFO/BPFI, so it states no fault-frequency factors to
  borrow either.
- **Roller mean diameter Dw — NOT found anywhere.**

Since Z is the parameter that dominates BPFI (a Z=17 vs Z=20 swing moves BPFI ~18%,
6× the route 3% tolerance), and it is available only as an unverifiable secondary
aggregation with a known conflation hazard, filling it in would be the doctored
pass RULE 2 forbids. **The item stays BLOCKED.** `config/wind_turbine.json`
`bearing_key` remains `null`; the WT chronological RCA progression is NOT re-run;
the wind-turbine holdout stays at its cited baseline (RCA never reaches inner-race
medium confidence). Unblock requires a primary source stating Z (and ideally Dw)
for the SKF 32222 J2 — e.g. full text of the Saidi/Bechhoefer papers or an SKF
engineering datasheet with the internal roller complement.
