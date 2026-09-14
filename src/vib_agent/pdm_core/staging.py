"""Layer 6 — bearing damage stage estimate, from already-computed evidence only.

HONESTY FLOOR
=============
Stages 1-2 are invisible to route-band spectra: below stage-3 evidence, output
not_determinable and route "early-stage detection requires HF/ultrasonic
trending" to recommendations.

That is not a hedge, it is what the bearing manufacturer's own literature says.
SKF, *Bearing Damage and Failure Analysis* (PUB BU/I3 14219/3 EN), section 2
"Inspection and troubleshooting", Fig. 1 / Diagram 1 (p. 8, explained p. 9),
plots damage progression against the detection technology that can see it:

    1. Damage initiated -- bearing exhibits incipient abrasive wear.
    2. First spall, detected by SKF ENVELOPED ACCELERATION TECHNOLOGY.
    3. Spalling has developed to an extent that the damage can be detected
       by STANDARD VIBRATION MONITORING.
    4. Advanced spalling causes high vibration and noise levels and an
       increase in operating temperature.
    5. Severe damage occurs: fatigue fracture of the bearing inner ring.
    6. Catastrophic failure occurs with secondary damage to other components.

A route-band velocity/envelope spectrum -- the only thing this product ever
receives -- is "standard vibration monitoring". By SKF's own diagram it is a
point-3-and-later instrument; points 1 and 2 belong to enveloped acceleration.
So a report that assigns an early stage from route data is claiming a
sensitivity the measurement does not have, and this module refuses to do it:
absence of stage-3 evidence is reported as `not_determinable`, never as
"stage 1", never as "early damage", and never as a clean bill.

See docs/staging.md for the four-stage <-> six-point mapping, the corroborating
Timken and Noria progressions, and -- importantly -- what the reference library
does NOT contain (no source in it publishes numeric four-stage vibration
criteria, so every constant here is an engineering rule marked `_verify: true`).

What this module may read
=========================
ALREADY-COMPUTED EVIDENCE ONLY. No signal processing happens here: no FFT, no
peak finding, no envelope, no re-reading of a spectrum. Every input is a field
another layer already put on the AnalysisResult:

  * committed bearing findings          result.findings / rca.primary_findings
  * matched vs expected tone            FaultMatch.freq_hz / .expected_hz
  * harmonic orders                     FaultMatch.harmonics_by_axis
  * sideband presence and density       FaultMatch.sidebands
  * peak-to-floor marginality           the `amplitude_marginal` confidence
                                        factor (see the limitation below)
  * dominance                           FaultMatch.loudest_axis
  * severity anchor                     result.iso.iso_zone

LIMITATION, stated rather than worked around: peak-to-floor is available here
only as a BOOLEAN. `bearing_rca._amplitude_floor` computes the numeric ratio,
but a match that fails the floor is demoted to the differential, and a match
that passes carries only the factor name `amplitude_marginal` with a prose
detail ("... peak sits near the noise floor") that states no number. Parsing a
float back out of that sentence would be a silent breakage waiting to happen,
so this module treats floor marginality as the flag it actually is. Making the
ratio numeric would mean changing bearing_rca.py, which Session R1 froze.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from vib_agent.models import AnalysisResult, FaultMatch

Stage = Literal[
    "stage_3_early", "stage_3_advanced", "stage_4_suspected", "not_determinable"
]

#: Rendered verbatim whenever a bearing fault is committed but the evidence does
#: not reach stage 3. The wording is fixed because both the report and the
#: drafted-report consistency check assert on it.
EARLY_STAGE_LIMITATION = "early-stage detection requires HF/ultrasonic trending"

#: Human-facing label for each stage. The report renders these; nothing else may
#: invent stage wording (see agent.consistency.check_stage_language).
STAGE_LABELS: dict[str, str] = {
    "stage_3_early": "Stage 3 (early)",
    "stage_3_advanced": "Stage 3 (advanced)",
    "stage_4_suspected": "Stage 4 (suspected)",
    "not_determinable": "Not determinable from this measurement",
}

_BEARING_PREFIX = "bearing_"
_CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class StageEstimate:
    """The staging verdict plus the evidence that produced it.

    `evidence` is the list the report prints. Every entry restates a value
    another layer computed -- this dataclass never introduces a number.
    """

    stage: Stage
    label: str
    fault: str | None
    evidence: list[str] = field(default_factory=list)
    limitation: str = ""

    @property
    def determinable(self) -> bool:
        return self.stage != "not_determinable"

    @property
    def is_bearing_stage(self) -> bool:
        """True when a bearing fault was committed at all -- i.e. when the
        report is entitled to render a stage block, determinable or not."""
        return self.fault is not None


def staging_config(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """The `staging` block of an already-resolved profile from config/staging.json.

    Callers pass the profile dict they loaded -- every entry point names its
    profile explicitly, per the threshold-profile doctrine in CLAUDE.md. A
    profile with no `staging` block (`streaming`, the frozen NCD path, which has
    none by design) yields an empty dict, and every rule below then reads its
    documented default. The NCD path is therefore unaffected.
    """
    if not cfg:
        return {}
    block = cfg.get("staging")
    return block if isinstance(block, dict) else {}


def _committed_bearing_matches(result: AnalysisResult) -> list[FaultMatch]:
    """Bearing FaultMatches that survived adjudication into a committed Finding.

    A match in `rca.primary_findings` is not automatically a committed fault --
    `synthesize()` is what turns a match into a Finding, and the report asserts
    Findings. Staging follows the report: a differential candidate never gets a
    stage, because we did not commit to the fault in the first place.
    """
    if result.rca is None:
        return []
    committed = {f.fault for f in result.findings}
    return [
        m
        for m in result.rca.primary_findings
        if m.fault.startswith(_BEARING_PREFIX) and m.fault in committed
    ]


def _harmonic_orders(match: FaultMatch) -> int:
    """Highest harmonic-order count on any SINGLE axis.

    Per-axis rather than pooled: three orders on one axis is a harmonic series,
    one order on each of three axes is not.
    """
    by_axis = match.harmonics_by_axis or {}
    if not by_axis:
        return 1 if match.harmonic_present else 0
    return max((len(labels) for labels in by_axis.values()), default=0)


def _sideband_count(match: FaultMatch) -> int:
    return len(match.sidebands or [])


def _floor_marginal(match: FaultMatch) -> bool:
    """True when the committed peak was flagged as sitting near the broadband
    floor. See the module docstring: this is a flag, not a ratio."""
    return any(f.name == "amplitude_marginal" for f in match.confidence_evidence)


def _unresolvable_tone(match: FaultMatch, orders: int, sidebands: int) -> bool:
    """True when the committed tone carries no structure AND did not stand clear
    of the broadband floor.

    This is the one case where a committed bearing fault still gets no stage: a
    lone peak, no harmonics, no sidebands, flagged as sitting in the noise. We
    matched it well enough to commit, but we cannot tell a real early defect
    from an incidental tone, and per the honesty floor that uncertainty is
    reported rather than rounded down to "early damage".
    """
    return orders <= 1 and sidebands == 0 and _floor_marginal(match)


def classify_bearing_stage(
    result: AnalysisResult, cfg: dict[str, Any] | None = None
) -> StageEstimate:
    """Estimate the damage stage of the committed bearing fault, if any.

    Pure: no I/O, no globals, no LLM, no signal processing. Reads `result` and
    the resolved staging profile `cfg`, returns a verdict.

    Returns `not_determinable` with `fault=None` when no bearing fault was
    committed -- the report then renders no stage block at all.
    """
    staging = staging_config(cfg)
    # Staging is a ROUTE-profile feature. The `streaming` (NCD) profile has no
    # staging block by design: it is production-validated and frozen, and its
    # 3-peak-per-axis triplets carry no broadband floor and no sideband
    # evidence, so the stage-3 discriminators are structurally unavailable.
    # Gating on the CONFIGURED PROFILE rather than on the shape of the data is
    # deliberate. The obvious data-shape test -- "did this match come from a
    # real spectrum?" -- cannot be made to work here: enrich_with_sidebands
    # leaves `sidebands` as None when it finds none, so a spectrum with no
    # sidebands is indistinguishable from an NCD triplet at this layer. Any
    # inference from absence would silently mis-stage one path or the other.
    # With no block, fault=None, so the report renders no stage section at all
    # and the NCD output is byte-identical to before Session R1.
    if not staging:
        return StageEstimate(
            stage="not_determinable",
            label=STAGE_LABELS["not_determinable"],
            fault=None,
        )

    matches = _committed_bearing_matches(result)
    if not matches:
        return StageEstimate(
            stage="not_determinable",
            label=STAGE_LABELS["not_determinable"],
            fault=None,
        )

    harmonics_advanced_min = int(staging.get("harmonics_advanced_min", 3))
    sidebands_advanced_min = int(staging.get("sidebands_advanced_min", 2))
    stage4_zones = tuple(staging.get("stage4_zones", ("C", "D")))

    # The strongest committed bearing fault carries the stage. Ranking by
    # confidence, then harmonic richness, then fault id keeps this deterministic
    # when two bearing faults are committed together (BPFO + BPFI on one machine).
    match = sorted(
        matches,
        key=lambda m: (_CONFIDENCE_ORDER.get(m.confidence, 3), -_harmonic_orders(m), m.fault),
    )[0]

    orders = _harmonic_orders(match)
    sidebands = _sideband_count(match)
    marginal = _floor_marginal(match)
    zone = result.iso.iso_zone if result.iso is not None else None

    evidence: list[str] = [
        f"Committed fault: {match.fault} at {match.freq_hz:.2f} Hz"
        if match.freq_hz is not None
        else f"Committed fault: {match.fault}",
        f"Harmonic orders on the strongest axis: {orders}",
        f"Shaft-rate sidebands detected: {sidebands}",
    ]
    # Only a REAL zone is evidence. On an acceleration-only reading the zone is
    # "not_assessable", and Session A's contract is that an unrated reading
    # carries zero ISO-zone language anywhere in the report -- naming it here
    # would reintroduce exactly the severity-truthfulness defect that fixed.
    # It is also simply not evidence: it is the absence of a severity judgement.
    if zone is not None and zone != "not_assessable":
        evidence.append(f"ISO zone at the time of measurement: {zone}")
    if marginal:
        evidence.append(
            "The matched peak was flagged as sitting near the broadband noise floor"
        )

    # ---- Stage 4 (suspected) ------------------------------------------------
    # SKF point 4: "Advanced spalling causes high vibration and noise levels."
    # The classical stage-4 marker is that discrete defect frequencies LOSE
    # prominence into a rising broadband floor. We only ever see tones that
    # still matched, so stage 4 can be suspected but never asserted -- which is
    # what the vocabulary already encodes. Requiring an elevated ISO zone AND a
    # corroborating spectral marker stops a merely-rich harmonic series on a
    # quiet machine from being called stage 4 on zone alone.
    if zone in stage4_zones and (marginal or sidebands >= sidebands_advanced_min):
        evidence.append(
            f"ISO zone {zone} with "
            + (
                "the tone no longer standing clear of the broadband floor"
                if marginal
                else f"{sidebands} sidebands indicating strong modulation"
            )
        )
        return StageEstimate(
            stage="stage_4_suspected",
            label=STAGE_LABELS["stage_4_suspected"],
            fault=match.fault,
            evidence=evidence,
            limitation=(
                "Stage 4 is reported as suspected, never confirmed: its defining marker is "
                "that discrete defect frequencies fade into a rising broadband floor, and a "
                "spectrum that still matches a discrete tone cannot demonstrate that. "
                "Confirm with a broadband/overall trend and an enveloped-acceleration reading."
            ),
        )

    # ---- Stage 3 (advanced) -------------------------------------------------
    # A harmonic series, or shaft-rate modulation of the defect tone, means the
    # defect is being loaded repeatedly and is no longer a single clean impact --
    # SKF point 3 well developed, heading for point 4.
    if orders >= harmonics_advanced_min or sidebands >= sidebands_advanced_min:
        return StageEstimate(
            stage="stage_3_advanced",
            label=STAGE_LABELS["stage_3_advanced"],
            fault=match.fault,
            evidence=evidence,
            limitation=(
                "Stage boundaries are not sharp and this estimate describes the measurement, "
                "not a remaining-life figure. No time-to-failure is implied."
            ),
        )

    # ---- Not determinable ---------------------------------------------------
    # A lone tone with no structure that also failed to stand clear of the
    # broadband floor. We committed to the fault, but we cannot separate an
    # early defect from an incidental peak -- reported as absent resolving
    # power, never as an early stage and never as a clean bill.
    if _unresolvable_tone(match, orders, sidebands):
        evidence.append(
            "No harmonic series, no sidebands, and the peak did not stand clear of the "
            "broadband floor, so the stage-3 discriminators could not be evaluated"
        )
        return StageEstimate(
            stage="not_determinable",
            label=STAGE_LABELS["not_determinable"],
            fault=match.fault,
            evidence=evidence,
            limitation=(
                f"Stages 1-2 are invisible to route-band spectra -- {EARLY_STAGE_LIMITATION} "
                "(enveloped acceleration / ultrasonic). This measurement cannot distinguish an "
                "early-stage defect from an incidental tone, so no stage is assigned."
            ),
        )

    # ---- Stage 3 (early) ----------------------------------------------------
    # A discrete, matched defect frequency IS the point-3 signature: it is the
    # earliest thing standard vibration monitoring can resolve. Everything that
    # reaches here has a committed tone that either carried structure or stood
    # clear of the floor.
    return StageEstimate(
        stage="stage_3_early",
        label=STAGE_LABELS["stage_3_early"],
        fault=match.fault,
        evidence=evidence,
        limitation=(
            "A discrete defect frequency is the earliest damage standard vibration "
            f"monitoring can resolve; {EARLY_STAGE_LIMITATION}, so anything earlier than "
            "this could not have been seen and its absence is not evidence of a healthy "
            "bearing."
        ),
    )
