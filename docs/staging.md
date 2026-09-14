# Damage staging — definitions, citations, and what the measurement cannot see

Session R1. Companion to `pdm_core/staging.py`. Every stage definition and every
threshold constant below cites a document in `references/INDEX.md` by index
number, document and section, per the References convention in `CLAUDE.md`.

---

## 1. The honesty floor, and why it is a citation rather than a hedge

**SKF #01** — *Bearing Damage and Failure Analysis*, PUB BU/I3 14219/3 EN, 3rd ed.,
section 2 "Inspection and troubleshooting", **Fig. 1 / Diagram 1, p. 8, explained
on p. 9** — plots bearing damage progression against *the detection technology
that can see each step*:

| SKF point | Damage state | Detected by (SKF's own labels) |
|---|---|---|
| 1 | Damage initiated — incipient abrasive wear | — |
| 2 | First spall | **SKF enveloped acceleration technology** |
| 3 | Spalling developed far enough to be detectable | **standard vibration monitoring** |
| 4 | Advanced spalling — high vibration and noise, operating temperature rise | "Listen and feel" |
| 5 | Severe damage — fatigue fracture of the inner ring | |
| 6 | Catastrophic failure with secondary damage to other components | |

vib-agent receives route-band velocity and envelope spectra. That is "standard
vibration monitoring". **By SKF's own diagram it is a point-3-and-later
instrument** — points 1 and 2 belong to enveloped acceleration.

This is the whole justification for the honesty floor in
`pdm_core/staging.py`:

> Stages 1–2 are invisible to route-band spectra: below stage-3 evidence,
> output `not_determinable` and route
> "early-stage detection requires HF/ultrasonic trending" to recommendations.

A report that assigns an early stage from route data claims a sensitivity the
measurement does not have. Absence of stage-3 evidence is therefore reported as
an absence of resolving power — never as "stage 1", never as "early damage",
and never as a clean bill.

## 2. Vocabulary, and the mapping onto SKF's six points

The four-stage vocabulary is the practitioner convention. The mapping is ours
and is stated here so it can be argued with:

| This module | SKF point(s) | Justification |
|---|---|---|
| *(unreachable)* stages 1–2 | 1–2 | Behind enveloped acceleration per Fig. 1 — `not_determinable` |
| `stage_3_early` | 3 | A discrete, matched defect frequency is the earliest thing standard vibration monitoring resolves |
| `stage_3_advanced` | 3 → 4 | Harmonic series and/or shaft-rate modulation: the defect is being loaded repeatedly, no longer a single clean impact |
| `stage_4_suspected` | 4–5 | "Advanced spalling causes high vibration and noise levels" — elevated ISO zone **plus** a corroborating spectral marker |
| `not_determinable` | — | Committed fault, but the stage-3 discriminators could not be evaluated |

**Why stage 4 is only ever `suspected`.** The classical stage-4 marker is that
discrete defect frequencies *lose* prominence into a rising broadband floor.
This module only ever sees tones that still matched — a spectrum that still
resolves a discrete tone cannot demonstrate that the discrete tone is
disappearing. The vocabulary encodes that limit rather than hiding it.

### Corroborating progressions in the library

Two independent sources, neither of which is a vibration-detectability model,
but both of which confirm the ordinal shape of the progression:

- **Timken #02** — *Bearing Damage Analysis with Lubrication Reference Guide*,
  order 5892, **pp. 10–11**: four progressive damage levels caused by inadequate
  lubrication — Level 1 discoloration from elevated temperature, Level 2
  micro-spalling/peeling from thin film, Level 3 heat damage from metal-to-metal
  contact, Level 4 advanced metal flow with cone rib deformation and cage
  expansion through total lockup. Anchors the advanced end.
- **Noria #08** — *Determining Fatigue Wear Using Wear Particle Analysis Tools*,
  **p. 19, Chart 1 "Bearing Fatigue Wear Severity Atlas"**: five severity levels,
  Level 1 initial microspalling to Level 5 significant deep-spalling. The
  oil-side analogue; useful as an independent staging axis when oil data exists.

### What the library does NOT contain

Stated so nobody goes looking for a citation that isn't there. **No document in
the library publishes numeric vibration criteria for the four-stage model** —
no "stage 3 = BPFO plus N harmonics with sidebands" table. Searched and empty:
NASA #43 (472 pp) and #44 (314 pp) contain no bearing-stage material at all
(only "spalling" in a concrete-tank context and "incipient" in a glossary);
FAG #03 covers damage *modes* and fatigue morphology, not staged detectability;
AGH #56 covers envelope-band selection with no staging content.

The closest missing candidate is **#47, DTIC AD-A291123 / DSTO-RR-0013**,
*A Review of Rolling Element Bearing Vibration: Detection, Diagnosis and
Prognosis* — bearing kinematics, defect frequencies, signal processing and
prognosis. It is **free, public, and still not downloaded** (`GAPS.md` §7).
It is the single most valuable outstanding item for this module.

**Consequence for the constants in §3:** they are engineering rules anchored on
the cited *qualitative* progression, not values transcribed from a published
table. They carry `_verify: true` accordingly, per the repo convention for
constants not yet validated against a source table.

## 3. Constants

Route profile only. The NCD `streaming` profile is production-validated and
frozen; it has no `staging` block, and every rule below falls back to its
documented default there, so the NCD path is unchanged.

| Constant | Value | Mechanical rule | Citation |
|---|---|---|---|
| `harmonics_advanced_min` | 3 | A defect frequency with ≥3 harmonic orders on a single axis is a harmonic *series*, not a single impact — the defect is being struck repeatedly per revolution. Per-axis, not pooled: three orders on one axis is a series; one order on each of three axes is not. | SKF #01 §2 Fig. 1 point 3→4 (progression from detectable spalling to advanced spalling) |
| `sidebands_advanced_min` | 2 | Shaft-rate sidebands mean the defect amplitude is modulated by shaft rotation — the defect is passing through the load zone. A pair (±1×) is the minimum that distinguishes modulation from a single incidental neighbour peak. | SKF #01 §2 Fig. 1 point 3→4 |
| `stage4_zones` | `["C", "D"]` | SKF point 4 is characterised by "high vibration and noise levels". ISO 20816-3 zones C/D are the repo's existing severity anchor for elevated overall vibration. Ties staging to the severity layer instead of inventing a second amplitude scale. | SKF #01 §2 p. 9 point 4; ISO zones per `config/iso_zones.json` |

Stage 4 additionally requires a corroborating spectral marker (floor
marginality **or** ≥`sidebands_advanced_min` sidebands) so that a rich harmonic
series on a quiet machine is not called stage 4 on zone alone.

## 4. Evidence this module may read

Already-computed evidence only. No FFT, no peak finding, no envelope, no
re-reading of a spectrum.

| Evidence | Source field |
|---|---|
| Committed bearing fault | `result.findings` ∩ `rca.primary_findings` |
| Matched vs expected tone | `FaultMatch.freq_hz` / `.expected_hz` |
| Harmonic orders | `FaultMatch.harmonics_by_axis` |
| Sideband presence and density | `FaultMatch.sidebands` |
| Peak-to-floor marginality | `amplitude_marginal` confidence factor |
| Dominance | `FaultMatch.loudest_axis` |
| Severity anchor | `result.iso.iso_zone` |

**Known limitation — peak-to-floor is a boolean here, not a ratio.**
`bearing_rca._amplitude_floor` computes the numeric ratio, but a match failing
the floor is demoted to the differential, and a match that passes carries only
the factor *name* `amplitude_marginal` with a prose detail stating no number.
Parsing a float back out of that sentence would break silently the first time
the wording changed. Making the ratio numeric requires changing
`bearing_rca.py`, which Session R1 froze — packeted, not patched.

**A committed fault only.** A differential candidate never receives a stage: we
did not commit to the fault, so staging it would smuggle a suppressed
hypothesis back into the report through a side door.

## 5. What a stage estimate is not

- **Not a remaining-life figure.** Stage boundaries are not sharp and no
  time-to-failure is implied. SKF's own diagram (#01 p. 8) labels the horizontal
  axis "Operating time" with the caption *"Depending on the background noise,
  the pre-warning time can vary"* — the interval between points is explicitly
  not fixed.
- **Not a replacement decision.** It describes the measurement.
- **Not comparable across measurement types.** A stage from an enveloped
  acceleration spectrum and a stage from a route velocity spectrum are not the
  same instrument reading the same thing.
