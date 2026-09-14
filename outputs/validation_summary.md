VALIDATION SUMMARY — vib-agent
Last updated: 2026-07-23 (Blind Test A disposition)

Hand-authored and regeneration-safe. eval/runner.py rewrites cwru_results.md and
field_validation_results.md wholesale, so the consolidated status lives here instead.
Every number below is measured, and every miss is stated as a miss.

These numbers describe the DEPLOYED SERVICE. Uploaded data is analysed on the `route`
profile -- the webapp reads config/webapp.json `analysis_profile` and the CLI defaults
to `route` (`streaming` is the NCD sensor pipeline's separate, frozen profile). The
detector guards below -- the synchronous-collision guard, the amplitude floor, and the
two-condition imbalance gate -- are therefore active in what an analyst receives. A live
regeneration of 16 reports on 2026-07-22 (grading: outputs/regen_grading.md, 16/16 PASS)
confirmed it: the four reports that previously carried a suppressible false bearing call
now commit nothing and demote the candidate to the differential.


BEARING DIAGNOSIS -- CWRU (Case Western Reserve University)
-----------------------------------------------------------
  Committed inner/outer-race diagnosis correct:  20 / 24
  False bearing calls on healthy files:           0 / 4  (clean)

The 4 misses are one fault condition, not a scatter: all four load cases of OR014@6
(0.014 in outer race, 6 o'clock). This is a KNOWN LIMITATION, root-caused rather than
excused:

  - The defect tone is genuinely weak -- its BPFO peak stands at only 3.5-8.2x the
    spectrum mean, below the amplitude floor a committed call requires (12.73x), and
    below even a known false positive on another rig (6.14x). The true signal is
    quieter than the noise it must be separated from, so no amplitude, prominence, or
    floor threshold admits it without also admitting louder false positives.
  - Literature cross-reference (Smith, W.A. & Randall, R.B., "Rolling element bearing
    diagnostics using the Case Western Reserve University data: A benchmark study",
    Mechanical Systems and Signal Processing 64-65 (2015), 100-131): record #197
    (OR014@6_0) is rated Y2 -- "clearly diagnosable but showing non-classic
    characteristics". The literature considers that record solvable, so this is a
    genuine gap in this pipeline, NOT an excused undiagnosable case. Record #200
    (OR014@6_3) is rated N1 -- "not diagnosable for the specified fault, but with other
    identifiable problems, e.g. looseness".

  Full detail: cwru_results.md and cwru_results_notes.md.

A second KNOWN LIMITATION, on the oversized (0.028 in) defect class -- race
mislocalization, not a missed fault:

  - On an inner-race 0.028 in record (blind test A, 1797 rpm, 6205 bearing) the
    pipeline committed bearing_outer_race at HIGH confidence -- correct that a
    rolling-element bearing defect is present, wrong on the race. For a defect this
    large and late-stage the maintenance ACTION is identical either way (pull,
    inspect, replace the bearing at the same urgency), so this is a localization
    error inside a correct bearing call, not a missed fault and not a false alarm.
  - Two mechanisms: the 1x-shaft sidebands that flank the tone -- the rotating
    inner-race signature -- are currently read as CORROBORATION of whichever bearing
    fault was matched (here, outer), instead of as counter-evidence favoring an
    inner/rotating defect; and the true BPFI tone never entered adjudication at all,
    dropped by the top-N amplitude peak cap before any inner-race hypothesis could
    form (the same crowd-out as the OR014@6 weak-signal gap above, and the deferred
    B2 work).
  - Out of GRADED scope, not excused. The 0.028 in diameters are non-classic. Smith,
    W.A. & Randall, R.B., "Rolling element bearing diagnostics using the Case Western
    Reserve University data: A benchmark study", Mechanical Systems and Signal
    Processing 64-65 (2015), 100-131 grade the standard 0.007/0.014/0.021 in catalog;
    the 0.028 in records fall outside that graded set, and config/cwru.json marks
    0.028in+ "out of v1 scope (never independently verified; not authorized)". The
    eval case (blind_ir028_0) is filed RECORDED (no gate) -- it does not count toward
    the IR/OR pass bar. Refinement is queued POST-SEND, paired with B2.

  Full analysis: REVIEW_PACKET_blind_test_A.md.


BEARING DIAGNOSIS -- MFPT (Machinery Failure Prevention Technology)
-------------------------------------------------------------------
Rig files:
  Committed inner/outer-race diagnosis correct:  17 / 17
  False positives on healthy baselines:           0     (the last one cleared by the
                                                         amplitude floor)

Real-world field bearings (3 files), scored by a two-outcome rule -- a report passes
either by committing the correct fault, OR by honestly reporting uncertainty and naming
the measurement that would resolve it. A confident wrong answer and a silent clean bill
both fail.

  PASS 1 / 3.

  - intermediate-speed bearing -- PASS. Previously committed a ball-fault call at a
    frequency 3.7% off the embedded ball order (a confident wrong answer). The amplitude
    floor now demotes that peak to the differential, and the report states the
    uncertainty and recommends the enveloping capture that would settle it.
  - oil-pump bearing -- FAIL, and reported as such. Real outer/inner-race content exists
    in the spectrum but never enters the top-N peak selection, so the report is a clean
    bill of health on a known-faulted machine. The fix (window-local peak selection) was
    attempted and rejected: every variant that surfaces this evidence also puts phantom
    bearing findings on genuinely healthy machines.
  - planet bearing -- FAIL, same cause, same rejected fix.


WIND TURBINE -- 50-day run-to-failure timeline
-----------------------------------------------
  Trend layer (Layer 4):        MET -- the rising trend is detected and reported.
  Statistical baseline (L2):    NOT EVALUABLE -- the Welford baseline never armed
                                (weight peaked at 29.74 against a 30-reading minimum).
  Bearing fault ID (Layer 5):   BLOCKED -- see below.
  Chronological reports:        4 regenerated on the live path (RUN v5).

Bearing fault identification is blocked on a data gap, not a code gap: the roller count of the SKF
32222 J2 bearing is not obtainable from a citable source. SKF publishes the envelope
(110 x 200 x 56 mm) and contact angle (15.6 deg) but not the roller complement; the
primary literature is paywalled. Roller count drives BPFI by roughly 18% across
plausible values -- six times the frequency match tolerance -- so inventing it would
produce a confident meaningless answer. The item stays blocked and the reports say
"no significant findings" rather than guess.


IMBALANCE AND MISALIGNMENT -- MAFAULDA
---------------------------------------
Healthy files:
  Clean (no fault of any kind reported):  6 / 10   (was 1 / 10)

That improvement is the guard story. Three successive guards removed false bearing
calls without costing a single true positive:

  - Synchronous-collision guard: a bearing frequency that lands within 2% of an integer
    multiple of shaft speed is indistinguishable from ordinary shaft-order vibration, so
    it is reported as a differential candidate rather than a committed fault, together
    with the order-synchronous capture that would separate the two. The nearest genuine
    bearing fault sits 5.6% off an integer order, a 2.8x margin.
  - Amplitude floor: a matched peak must stand at least 12.73x the spectrum mean to be
    committed. Below that it is reported at differential grade with an enveloping
    capture recommended.
  - Two-condition imbalance gate: imbalance requires both radial-velocity dominance and
    a 1x line that leads the higher radial harmonics (the latter is what separates
    imbalance from misalignment).

  Imbalance detection rate:      0 / 21
  Misalignment detection rate:   9 / 30

  KNOWN LIMITATION -- imbalance requires phase verification. Committed imbalance on
  acceleration-only data with axial cross-talk requires phase verification; such cases
  report at differential rather than as a committed diagnosis. The gate constants above
  are derived from fixtures with known ground truth, and on this dataset they are more
  conservative than the imbalance signal present, so imbalance is not committed here.
  The 1x amplitude does rise monotonically with added mass on these files -- the signal
  is real; the gate deliberately will not commit to it from a single acceleration
  channel without phase.


SEVERITY REPORTING -- ISO 20816
--------------------------------
ISO 20816 severity is a velocity judgement. When a reading is acceleration-only, the
reports state severity as "unrated", carry an explicit Severity & Coverage boundary
naming what was and was not assessed, and recommend the broadband velocity measurement
that would establish it. They do NOT name a severity zone.

Verified on the live product path, not just in tests: of 16 reports regenerated on
2026-07-22, every acceleration-only report carried "unrated"/"not assessable" plus the
velocity follow-up, with ZERO ISO-zone claims (grading: outputs/regen_grading.md, 16/16
PASS). The consistency gate also now checks the published PROSE, not just the model's
structured echo block: a fault named as the diagnosis must be a finding the analysis
actually committed, at the tier the prose claims, and a confidence word next to a fault
must equal its computed confidence. A no-findings reading gets an explicit report shape
("Committed diagnosis: none -- parameters within normal range" plus what was screened),
so a clean machine is reported as a positive result rather than pressuring the draft to
invent one.

  RESOLVED (Session D): a healthy-baseline report that had asserted a committed fault the
  analysis did not produce -- the echo block was honest while the prose was not -- is now
  caught by the prose-level fault check, retried, and (if it recurs) degraded. Reproduced
  as a regression test against the fake client.
