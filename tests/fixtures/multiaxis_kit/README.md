# Multi-Axis Test Set — 5 cases, 13 files

All files: velocity spectra (mm/s RMS on the form), CSV template, RPM 1780,
ISO Group 2, rigid. Bearing field BLANK except Case E (6205).
Slot mapping: _H -> Radial-horizontal, _V -> Radial-vertical, _A -> Axial.

## CASE A — the conjunction proof (run this one FIRST, in two steps)
Step 1: upload caseA_H.csv ALONE (direction: radial-horizontal).
        Record what the report says. (1x-dominant radial, no axial info
        -> expect NO committed imbalance; whatever it says, save it.)
Step 2: upload all three (H, V, A in their slots).
        EXPECT: imbalance explicitly NOT committed (radial/axial 1x
        ratio ~0.29, far below the 7.5 gate) and an axial-implicating
        misalignment-family finding (angular / bent-shaft / general —
        FAMILY level is the assertion, exact variant is informative).
        The delta between Step 1 and Step 2 IS the feature.

## CASE B — variant discrimination with real axial context
All three files. Radial 2x dominant, quiet axial.
EXPECT: misalignment-family finding (parallel or general), severity
RATED (velocity data), Zone C-ish. Axial channel listed as measured.

## CASE C — imbalance positive control (T07 geometry)
All three files. Radial 1x ~6.5-6.8, axial 1x ~0.4 (ratio ~16).
EXPECT: COMMITTED imbalance — the call single-channel uploads could
never make (the J3 gate needs the axial ratio). Severity rated,
Zone D-ish (strong 1x). This is the ceiling-raise demonstrated.

## CASE D — wrong-machine trap
Upload caseD_H.csv (H) + caseD_V.csv (V), stated RPM 1780.
V's content sits at 1480 RPM. EXPECT: cross-file speed-agreement
warning ("files may not be from the same machine/condition") or
fail-closed insufficient-data — NOT a quiet merged diagnosis.

## CASE E — partial upload with a dead channel
caseE_H.csv (H, bearing 6205) + caseE_A.csv (Axial — flat/dead).
EXPECT: axial channel FAILS the gate (flat spectrum) and is reported
per-channel in Data Quality; analysis proceeds on H alone -> committed
outer-race (BPFO family), severity rated; coverage states
"Axial: unreadable/failed", V: not measured.

## Scoring notes
- Family-level outcomes are the assertions; exact variants and exact
  zones are informative, not pass/fail (consistent with the MAFAULDA
  scoring philosophy).
- Any file REJECTED at upload: diff against the served templates —
  template drift is itself a finding.
- Keep this folder in-repo as multiaxis regression fixtures.
